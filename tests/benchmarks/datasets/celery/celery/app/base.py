"""Actual App instance implementation."""
import functools
import importlib
import inspect
import os
import sys
import threading
import typing
import warnings
from collections import UserDict, defaultdict, deque
from datetime import datetime
from datetime import timezone as datetime_timezone
from operator import attrgetter

from click.exceptions import Exit
from dateutil.parser import isoparse
from kombu import Exchange, pools
from kombu.clocks import LamportClock
from kombu.common import oid_from
from kombu.transport.native_delayed_delivery import calculate_routing_key
from kombu.utils.compat import register_after_fork
from kombu.utils.objects import cached_property
from kombu.utils.uuid import uuid
from vine import starpromise

from celery import platforms, signals
from celery._state import (_announce_app_finalized, _deregister_app, _register_app, _set_current_app, _task_stack,
                           connect_on_app_finalize, get_current_app, get_current_worker_task, set_default_app)
from celery.exceptions import AlwaysEagerIgnored, ImproperlyConfigured
from celery.loaders import get_loader_cls
from celery.local import PromiseProxy, maybe_evaluate
from celery.utils import abstract
from celery.utils.collections import AttributeDictMixin
from celery.utils.dispatch import Signal
from celery.utils.functional import first, head_from_fun, maybe_list
from celery.utils.imports import gen_task_name, instantiate, symbol_by_name
from celery.utils.log import get_logger
from celery.utils.objects import FallbackContext, mro_lookup
from celery.utils.time import maybe_make_aware, timezone, to_utc

from ..utils.annotations import annotation_is_class, annotation_issubclass, get_optional_arg
from ..utils.quorum_queues import detect_quorum_queues
# Load all builtin tasks
from . import backends, builtins  # noqa
from .annotations import prepare as prepare_annotations
from .autoretry import add_autoretry_behaviour
from .defaults import DEFAULT_SECURITY_DIGEST, find_deprecated_settings
from .registry import TaskRegistry
from .utils import (AppPickler, Settings, _new_key_to_old, _old_key_to_new, _unpickle_app, _unpickle_app_v2, appstr,
                    bugreport, detect_settings)

if typing.TYPE_CHECKING:  # pragma: no cover  # codecov does not capture this
    # flake8 marks the BaseModel import as unused, because the actual typehint is quoted.
    from pydantic import BaseModel  # noqa: F401

__all__ = ('Celery',)

logger = get_logger(__name__)

if sys.version_info >= (3, 14):
    import annotationlib

    def _get_annotations(fun):
        # In Python 3.14+, annotations are deferred by default (PEP 649).
        # Accessing fun.__annotations__ (or inspect.get_annotations without a
        # format) evaluates them and may raise NameError for types only
        # available under TYPE_CHECKING. To preserve previous behavior, first
        # try to return evaluated annotations; if that fails with NameError,
        # fall back to returning stringified annotations instead.
        try:
            return inspect.get_annotations(fun)
        except NameError:
            return inspect.get_annotations(fun, format=annotationlib.Format.STRING)
else:
    def _get_annotations(fun):
        return fun.__annotations__

BUILTIN_FIXUPS = {
    'celery.fixups.django:fixup',
}
USING_EXECV = os.environ.get('FORKED_BY_MULTIPROCESSING')

ERR_ENVVAR_NOT_SET = """
The environment variable {0!r} is not set,
and as such the configuration could not be loaded.

Please set this variable and make sure it points to
a valid configuration module.

Example:
    {0}="proj.celeryconfig"
"""


def app_has_custom(app, attr):
    """Return true if app has customized method `attr`.

    Note:
        This is used for optimizations in cases where we know
        how the default behavior works, but need to account
        for someone using inheritance to override a method/property.
    """
    return mro_lookup(app.__class__, attr, stop={Celery, object},
                      monkey_patched=[__name__])


def _unpickle_appattr(reverse_name, args):
    """Unpickle app."""
    # Given an attribute name and a list of args, gets
    # the attribute from the current app and calls it.
    return get_current_app()._rgetattr(reverse_name)(*args)


def _after_fork_cleanup_app(app):
    # This is used with multiprocessing.register_after_fork,
    # so need to be at module level.
    try:
        app._after_fork()
    except Exception as exc:  # pylint: disable=broad-except
        logger.info('after forker raised exception: %r', exc, exc_info=1)


def pydantic_wrapper(
    app: "Celery",
    task_fun: typing.Callable[..., typing.Any],
    task_name: str,
    strict: bool = True,
    context: typing.Optional[typing.Dict[str, typing.Any]] = None,
    dump_kwargs: typing.Optional[typing.Dict[str, typing.Any]] = None
):
    """Wrapper to validate arguments and serialize return values using Pydantic."""
    try:
        pydantic = importlib.import_module('pydantic')
    except ModuleNotFoundError as ex:
        raise ImproperlyConfigured('You need to install pydantic to use pydantic model serialization.') from ex

    BaseModel: typing.Type['BaseModel'] = pydantic.BaseModel  # noqa: F811  # only defined when type checking

    if context is None:
        context = {}
    if dump_kwargs is None:
        dump_kwargs = {}
    dump_kwargs.setdefault('mode', 'json')

    # If a file uses `from __future__ import annotations`, all annotations will
    # be strings. `typing.get_type_hints()` can turn these back into real
    # types, but can also sometimes fail due to circular imports. Try that
    # first, and fall back to annotations from `inspect.signature()`.
    task_signature = inspect.signature(task_fun)

    try:
        type_hints = typing.get_type_hints(task_fun)
    except (NameError, AttributeError, TypeError):
        # Fall back to raw annotations from inspect if get_type_hints fails
        type_hints = None

    @functools.wraps(task_fun)
    def wrapper(*task_args, **task_kwargs):
        # Validate task parameters if type hinted as BaseModel
        bound_args = task_signature.bind(*task_args, **task_kwargs)
        for arg_name, arg_value in bound_args.arguments.items():
            if type_hints and arg_name in type_hints:
                arg_annotation = type_hints[arg_name]
            else:
                arg_annotation = task_signature.parameters[arg_name].annotation

            optional_arg = get_optional_arg(arg_annotation)
            if optional_arg is not None and arg_value is not None:
                arg_annotation = optional_arg

            if annotation_issubclass(arg_annotation, BaseModel):
                bound_args.arguments[arg_name] = arg_annotation.model_validate(
                    arg_value,
                    strict=strict,
                    context={**context, 'celery_app': app, 'celery_task_name': task_name},
                )

        # Call the task with (potentially) converted arguments
        returned_value = task_fun(*bound_args.args, **bound_args.kwargs)

        # Dump Pydantic model if the returned value is an instance of pydantic.BaseModel *and* its
        # class matches the typehint
        if type_hints and 'return' in type_hints:
            return_annotation = type_hints['return']
        else:
            return_annotation = task_signature.return_annotation

        optional_return_annotation = get_optional_arg(return_annotation)
        if optional_return_annotation is not None:
            return_annotation = optional_return_annotation

        if (
            annotation_is_class(return_annotation)
            and isinstance(returned_value, BaseModel)
            and isinstance(returned_value, return_annotation)
        ):
            return returned_value.model_dump(**dump_kwargs)

        return returned_value

    return wrapper


class PendingConfiguration(UserDict, AttributeDictMixin):
    # `app.conf` will be of this type before being explicitly configured,
    # meaning the app can keep any configuration set directly
    # on `app.conf` before the `app.config_from_object` call.
    #
    # accessing any key will finalize the configuration,
    # replacing `app.conf` with a concrete settings object.

    callback = None
    _data = None

    def __init__(self, conf, callback):
        object.__setattr__(self, '_data', conf)
        object.__setattr__(self, 'callback', callback)

    def __setitem__(self, key, value):
        self._data[key] = value

    def clear(self):
        self._data.clear()

    def update(self, *args, **kwargs):
        self._data.update(*args, **kwargs)

    def setdefault(self, *args, **kwargs):
        return self._data.setdefault(*args, **kwargs)

    def __contains__(self, key):
        # XXX will not show finalized configuration
        # setdefault will cause `key in d` to happen,
        # so for setdefault to be lazy, so does contains.
        return key in self._data

    def __len__(self):
        return len(self.data)

    def __repr__(self):
        return repr(self.data)

    @cached_property
    def data(self):
        return self.callback()


class Celery:
    """Celery application.

    Arguments:
        main (str): Name of the main module if running as `__main__`.
            This is used as the prefix for auto-generated task names.

    Keyword Arguments:
        broker (str): URL of the default broker used.
        backend (Union[str, Type[celery.backends.base.Backend]]):
            The result store backend class, or the name of the backend
            class to use.

            Default is the value of the :setting:`result_backend` setting.
        autofinalize (bool): If set to False a :exc:`RuntimeError`
            will be raised if the task registry or tasks are used before
            the app is finalized.
        set_as_current (bool):  Make this the global current app.
        include (List[str]): List of modules every worker should import.

        amqp (Union[str, Type[AMQP]]): AMQP object or class name.
        events (Union[str, Type[celery.app.events.Events]]): Events object or
            class name.
        log (Union[str, Type[Logging]]): Log object or class name.
        control (Union[str, Type[celery.app.control.Control]]): Control object
            or class name.
        tasks (Union[str, Type[TaskRegistry]]): A task registry, or the name of
            a registry class.
        fixups (List[str]): List of fix-up plug-ins (e.g., see
            :mod:`celery.fixups.django`).
        config_source (Union[str, class]): Take configuration from a class,
            or object.  Attributes may include any settings described in
            the documentation.
        task_cls (Union[str, Type[celery.app.task.Task]]): base task class to
            use. See :ref:`this section <custom-task-cls-app-wide>` for usage.
    """

    #: This is deprecated, use :meth:`reduce_keys` instead
    Pickler = AppPickler

    SYSTEM = platforms.SYSTEM
    IS_macOS, IS_WINDOWS = platforms.IS_macOS, platforms.IS_WINDOWS

    #: Name of the `__main__` module.  Required for standalone scripts.
    #:
    #: If set this will be used instead of `__main__` when automatically
    #: generating task names.
    main = None

    #: Custom options for command-line programs.
    #: See :ref:`extending-commandoptions`
    user_options = None

    #: Custom bootsteps to extend and modify the worker.
    #: See :ref:`extending-bootsteps`.
    steps = None

    builtin_fixups = BUILTIN_FIXUPS

    amqp_cls = 'celery.app.amqp:AMQP'
    backend_cls = None
    events_cls = 'celery.app.events:Events'
    loader_cls = None
    log_cls = 'celery.app.log:Logging'
    control_cls = 'celery.app.control:Control'
    task_cls = 'celery.app.task:Task'
    registry_cls = 'celery.app.registry:TaskRegistry'

    #: Thread local storage.
    _local = None
    _fixups = None
    _pool = None
    _conf = None
    _after_fork_registered = False

    #: Signal sent when app is loading configuration.
    on_configure = None

    #: Signal sent after app has prepared the configuration.
    on_after_configure = None

    #: Signal sent after the app has been finalized (i.e., all pending
    #: task decorators have been evaluated, built-in tasks loaded, and
    #: every currently registered task has been bound to the app).  This is
    #: the earliest point at which the task registry is initialized/stable
    #: and safe to inspect for tasks currently registered with this app.
    on_after_finalize = None

    #: Signal sent by every new process after fork.
    on_after_fork = None

    def __init__(self, main=None, loader=None, backend=None,
                 amqp=None, events=None, log=None, control=None,
                 set_as_current=True, tasks=None, broker=None, include=None,
                 changes=None, config_source=None, fixups=None, task_cls=None,
                 autofinalize=True, namespace=None, strict_typing=True,
                 **kwargs):

        self._local = threading.local()
        self._backend_cache = None

        self.clock = LamportClock()
        self.main = main
        self.amqp_cls = amqp or self.amqp_cls
        self.events_cls = events or self.events_cls
        self.loader_cls = loader or self._get_default_loader()
        self.log_cls = log or self.log_cls
        self.control_cls = control or self.control_cls
        self._custom_task_cls_used = (
            # Custom task class provided as argument
            bool(task_cls)
            # subclass of Celery with a task_cls attribute
            or self.__class__ is not Celery and hasattr(self.__class__, 'task_cls')
        )
        self.task_cls = task_cls or self.task_cls
        self.set_as_current = set_as_current
        self.registry_cls = symbol_by_name(self.registry_cls)
        self.user_options = defaultdict(set)
        self.steps = defaultdict(set)
        self.autofinalize = autofinalize
        self.namespace = namespace
        self.strict_typing = strict_typing

        self.configured = False
        self._config_source = config_source
        self._pending_defaults = deque()
        self._pending_periodic_tasks = deque()

        self.finalized = False
        self._finalize_mutex = threading.RLock()
        self._pending = deque()
        self._tasks = tasks
        if not isinstance(self._tasks, TaskRegistry):
            self._tasks = self.registry_cls(self._tasks or {})

        # If the class defines a custom __reduce_args__ we need to use
        # the old way of pickling apps: pickling a list of
        # args instead of the new way that pickles a dict of keywords.
        self._using_v1_reduce = app_has_custom(self, '__reduce_args__')

        # these options are moved to the config to
        # simplify pickling of the app object.
        self._preconf = changes or {}
        self._preconf_set_by_auto = set()
        self.__autoset('broker_url', broker)
        self.__autoset('result_backend', backend)
        self.__autoset('include', include)

        for key, value in kwargs.items():
            self.__autoset(key, value)

        self._conf = Settings(
            PendingConfiguration(
                self._preconf, self._finalize_pending_conf),
            prefix=self.namespace,
            keys=(_old_key_to_new, _new_key_to_old),
        )

        # - Apply fix-ups.
        self.fixups = set(self.builtin_fixups) if fixups is None else fixups
        # ...store fixup instances in _fixups to keep weakrefs alive.
        self._fixups = [symbol_by_name(fixup)(self) for fixup in self.fixups]

        if self.set_as_current:
            self.set_current()

        # Signals
        if self.on_configure is None:
            # used to be a method pre 4.0
            self.on_configure = Signal(name='app.on_configure')
        self.on_after_configure = Signal(
            name='app.on_after_configure',
            providing_args={'source'},
        )
        self.on_after_finalize = Signal(name='app.on_after_finalize')
        self.on_after_fork = Signal(name='app.on_after_fork')

        # Boolean signalling, whether fast_trace_task are enabled.
        # this attribute is set in celery.worker.trace and checked by celery.worker.request
        self.use_fast_trace_task = False

        self.on_init()
        _register_app(self)

    def _get_default_loader(self):
        # the --loader command-line argument sets the environment variable.
        return (
            os.environ.get('CELERY_LOADER') or
            self.loader_cls or
            'celery.loaders.app:AppLoader'
        )

    def on_init(self):
        """Optional callback called at init."""

    def __autoset(self, key, value):
        if value is not None:
            self._preconf[key] = value
            self._preconf_set_by_auto.add(key)

    def set_current(self):
        """Make this the current app for this thread."""
        _set_current_app(self)

    def set_default(self):
        """Make this the default app for all threads."""
        set_default_app(self)

    def _ensure_after_fork(self):
        if not self._after_fork_registered:
            self._after_fork_registered = True
            if register_after_fork is not None:
                register_after_fork(self, _after_fork_cleanup_app)

    def close(self):
        """Clean up after the application.

        Only necessary for dynamically created apps, and you should
        probably use the :keyword:`with` statement instead.

        Example:
            >>> with Celery(set_as_current=False) as app:
            ...     with app.connection_for_write() as conn:
            ...         pass
        """
        self._pool = None
        _deregister_app(self)

    def start(self, argv=None):
        """Run :program:`celery` using `argv`.

        Uses :data:`sys.argv` if `argv` is not specified.
        """
        from celery.bin.celery import celery

        celery.params[0].default = self

        if argv is None:
            argv = sys.argv

        try:
            celery.main(args=argv, standalone_mode=False)
        except Exit as e:
            return e.exit_code
        finally:
            celery.params[0].default = None

    def worker_main(self, argv=None):
        """Run :program:`celery worker` using `argv`.

        Uses :data:`sys.argv` if `argv` is not specified.
        """
        if argv is None:
            argv = sys.argv

        if 'worker' not in argv:
            raise ValueError(
                "The worker sub-command must be specified in argv.\n"
                "Use app.start() to programmatically start other commands."
            )

        self.start(argv=argv)

    def task(self, *args, **opts):
        """Decorator to create a task class out of any callable.

        See :ref:`Task options<task-options>` for a list of the
        arguments that can be passed to this decorator.

        Examples:
            .. code-block:: python

                @app.task
                def refresh_feed(url):
                    store_feed(feedparser.parse(url))

            with setting extra options:

            .. code-block:: python

                @app.task(exchange='feeds')
                def refresh_feed(url):
                    return store_feed(feedparser.parse(url))

        Note:
            App Binding: For custom apps the task decorator will return
            a proxy object, so that the act of creating the task is not
            performed until the task is used or the task registry is accessed.

            If you're depending on binding to be deferred, then you must
            not access any attributes on the returned object until the
            application is fully set up (finalized).
        """
        if USING_EXECV and opts.get('lazy', True):
            # When using execv the task in the original module will point to a
            # different app, so doing things like 'add.request' will point to
            # a different task instance.  This makes sure it will always use
            # the task instance from the current app.
            # Really need a better solution for this :(
            from . import shared_task
            return shared_task(*args, lazy=False, **opts)

        def inner_create_task_cls(shared=True, filter=None, lazy=True, **opts):
            _filt = filter

            def _create_task_cls(fun):
                if shared:
                    def cons(app):
                        return app._task_from_fun(fun, **opts)

                    cons.__name__ = fun.__name__
                    connect_on_app_finalize(cons)
                if not lazy or self.finalized:
                    ret = self._task_from_fun(fun, **opts)
                else:
                    # return a proxy object that evaluates on first use
                    ret = PromiseProxy(self._task_from_fun, (fun,), opts,
                                       __doc__=fun.__doc__)
                    self._pending.append(ret)
                if _filt:
                    return _filt(ret)
                return ret

            return _create_task_cls

        if len(args) == 1:
            if callable(args[0]):
                return inner_create_task_cls(**opts)(*args)
            raise TypeError('argument 1 to @task() must be a callable')
        if args:
            raise TypeError(
                '@task() takes exactly 1 argument ({} given)'.format(
                    sum([len(args), len(opts)])))
        return inner_create_task_cls(**opts)

    def type_checker(self, fun, bound=False):
        return staticmethod(head_from_fun(fun, bound=bound))

    def _task_from_fun(
        self,
        fun,
        name=None,
        base=None,
        bind=False,
        pydantic: bool = False,
        pydantic_strict: bool = False,
        pydantic_context: typing.Optional[typing.Dict[str, typing.Any]] = None,
        pydantic_dump_kwargs: typing.Optional[typing.Dict[str, typing.Any]] = None,
        **options,
    ):
        if not self.finalized and not self.autofinalize:
            raise RuntimeError('Contract breach: app not finalized')
        name = name or self.gen_task_name(fun.__name__, fun.__module__)
        base = base or self.Task

        if name not in self._tasks:
            if pydantic is True:
                fun = pydantic_wrapper(self, fun, name, pydantic_strict, pydantic_context, pydantic_dump_kwargs)

            run = fun if bind else staticmethod(fun)
            task = type(fun.__name__, (base,), dict({
                'app': self,
                'name': name,
                'run': run,
                '_decorated': True,
                '__doc__': fun.__doc__,
                '__module__': fun.__module__,
                '__annotations__': _get_annotations(fun),
                '__header__': self.type_checker(fun, bound=bind),
                '__wrapped__': run}, **options))()
            # for some reason __qualname__ cannot be set in type()
            # so we have to set it here.
            try:
                task.__qualname__ = fun.__qualname__
            except AttributeError:
                pass
            self._tasks[task.name] = task
            task.bind(self)  # connects task to this app
            add_autoretry_behaviour(task, **options)
        else:
            task = self._tasks[name]
        return task

    def register_task(self, task, **options):
        """Utility for registering a task-based class.

        Note:
            This is here for compatibility with old Celery 1.0
            style task classes, you should not need to use this for
            new projects.
        """
        task = inspect.isclass(task) and task() or task
        if not task.name:
            task_cls = type(task)
            task.name = self.gen_task_name(
                task_cls.__name__, task_cls.__module__)
        add_autoretry_behaviour(task, **options)
        self.tasks[task.name] = task
        task._app = self
        task.bind(self)
        return task

    def gen_task_name(self, name, module):
        return gen_task_name(self, name, module)

    def finalize(self, auto=False):
        """Finalize the app.

        This loads built-in tasks, evaluates pending task decorators,
        reads configuration, etc.
        """
        with self._finalize_mutex:
            if not self.finalized:
                if auto and not self.autofinalize:
                    raise RuntimeError('Contract breach: app not finalized')
                self.finalized = True
                _announce_app_finalized(self)

                pending = self._pending
                while pending:
                    maybe_evaluate(pending.popleft())

                for task in self._tasks.values():
                    task.bind(self)

                self.on_after_finalize.send(sender=self)

    def add_defaults(self, fun):
        """Add default configuration from dict ``d``.

        If the argument is a callable function then it will be regarded
        as a promise, and it won't be loaded until the configuration is
        actually needed.

        This method can be compared to:

        .. code-block:: pycon

            >>> celery.conf.update(d)

        with a difference that 1) no copy will be made and 2) the dict will
        not be transferred when the worker spawns child processes, so
        it's important that the same configuration happens at import time
        when pickle restores the object on the other side.
        """
        if not callable(fun):
            d, fun = fun, lambda: d
        if self.configured:
            return self._conf.add_defaults(fun())
        self._pending_defaults.append(fun)

    def config_from_object(self, obj,
                           silent=False, force=False, namespace=None):
        """Read configuration from object.

        Object is either an actual object or the name of a module to import.

        Example:
            >>> celery.config_from_object('myapp.celeryconfig')

            >>> from myapp import celeryconfig
            >>> celery.config_from_object(celeryconfig)

        Arguments:
            silent (bool): If true then import errors will be ignored.
            force (bool): Force reading configuration immediately.
                By default the configuration will be read only when required.
        """
        self._config_source = obj
        self.namespace = namespace or self.namespace
        if force or self.configured:
            self._conf = None
            if self.loader.config_from_object(obj, silent=silent):
                return self.conf

    def config_from_envvar(self, variable_name, silent=False, force=False):
        """Read configuration from environment variable.

        The value of the environment variable must be the name
        of a module to import.

        Example:
            >>> os.environ['CELERY_CONFIG_MODULE'] = 'myapp.celeryconfig'
            >>> celery.config_from_envvar('CELERY_CONFIG_MODULE')
        """
        module_name = os.environ.get(variable_name)
        if not module_name:
            if silent:
                return False
            raise ImproperlyConfigured(
                ERR_ENVVAR_NOT_SET.strip().format(variable_name))
        return self.config_from_object(module_name, silent=silent, force=force)

    def config_from_cmdline(self, argv, namespace='celery'):
        self._conf.update(
            self.loader.cmdline_config_parser(argv, namespace)
        )

    def setup_security(self, allowed_serializers=None, key=None, key_password=None, cert=None,
                       store=None, digest=DEFAULT_SECURITY_DIGEST,
                       serializer='json'):
        """Setup the message-signing serializer.

        This will affect all application instances (a global operation).

        Disables untrusted serializers and if configured to use the ``auth``
        serializer will register the ``auth`` serializer with the provided
        settings into the Kombu serializer registry.

        Arguments:
            allowed_serializers (Set[str]): List of serializer names, or
                content_types that should be exempt from being disabled.
            key (str): Name of private key file to use.
                Defaults to the :setting:`security_key` setting.
            key_password (bytes): Password to decrypt the private key.
                Defaults to the :setting:`security_key_password` setting.
            cert (str): Name of certificate file to use.
                Defaults to the :setting:`security_certificate` setting.
            store (str): Directory containing certificates.
                Defaults to the :setting:`security_cert_store` setting.
            digest (str): Digest algorithm used when signing messages.
                Default is ``sha256``.
            serializer (str): Serializer used to encode messages after
                they've been signed.  See :setting:`task_serializer` for
                the serializers supported.  Default is ``json``.
        """
        from celery.security import setup_security
        return setup_security(allowed_serializers, key, key_password, cert,
                              store, digest, serializer, app=self)

    def autodiscover_tasks(self, packages=None,
                           related_name='tasks', force=False):
        """Auto-discover task modules.

        Searches a list of packages for a "tasks.py" module (or use
        related_name argument).

        If the name is empty, this will be delegated to fix-ups (e.g., Django).

        For example if you have a directory layout like this:

        .. code-block:: text

            foo/__init__.py
               tasks.py
               models.py

            bar/__init__.py
                tasks.py
                models.py

            baz/__init__.py
                models.py

        Then calling ``app.autodiscover_tasks(['foo', 'bar', 'baz'])`` will
        result in the modules ``foo.tasks`` and ``bar.tasks`` being imported.

        Arguments:
            packages (List[str]): List of packages to search.
                This argument may also be a callable, in which case the
                value returned is used (for lazy evaluation).
            related_name (Optional[str]): The name of the module to find.  Defaults
                to "tasks": meaning "look for 'module.tasks' for every
                module in ``packages``.".  If ``None`` will only try to import
                the package, i.e. "look for 'module'".
            force (bool): By default this call is lazy so that the actual
                auto-discovery won't happen until an application imports
                the default modules.  Forcing will cause the auto-discovery
                to happen immediately.
        """
        if force:
            return self._autodiscover_tasks(packages, related_name)
        signals.import_modules.connect(starpromise(
            self._autodiscover_tasks, packages, related_name,
        ), weak=False, sender=self)

    def _autodiscover_tasks(self, packages, related_name, **kwargs):
        if packages:
            return self._autodiscover_tasks_from_names(packages, related_name)
        return self._autodiscover_tasks_from_fixups(related_name)

    def _autodiscover_tasks_from_names(self, packages, related_name):
        # packages argument can be lazy
        return self.loader.autodiscover_tasks(
            packages() if callable(packages) else packages, related_name,
        )

    def _autodiscover_tasks_from_fixups(self, related_name):
        return self._autodiscover_tasks_from_names([
            pkg for fixup in self._fixups
            if hasattr(fixup, 'autodiscover_tasks')
            for pkg in fixup.autodiscover_tasks()
        ], related_name=related_name)

    def send_task(self, name, args=None, kwargs=None, countdown=None,
                  eta=None, task_id=None, producer=None, connection=None,
                  router=None, result_cls=None, expires=None,
                  publisher=None, link=None, link_error=None,
                  add_to_parent=True, group_id=None, group_index=None,
                  retries=0, chord=None,
                  reply_to=None, time_limit=None, soft_time_limit=None,
                  root_id=None, parent_id=None, route_name=None,
                  shadow=None, chain=None, task_type=None, replaced_task_nesting=0, **options):
        """Send task by name.

        Supports the same arguments as :meth:`@-Task.apply_async`.

        Arguments:
            name (str): Name of task to call (e.g., `"tasks.add"`).
            result_cls (AsyncResult): Specify custom result class.
        """
        parent = have_parent = None
        amqp = self.amqp
        task_id = task_id or uuid()
        producer = producer or publisher  # XXX compat
        router = router or amqp.router
        conf = self.conf
        if conf.task_always_eager:  # pragma: no cover
            warnings.warn(AlwaysEagerIgnored(
                'task_always_eager has no effect on send_task',
            ), stacklevel=2)

        ignore_result = options.pop('ignore_result', False)
        options = router.route(
            options, route_name or name, args, kwargs, task_type)

        driver_type = self.producer_pool.connections.connection.transport.driver_type

        if (eta or countdown) and detect_quorum_queues(self, driver_type)[0]:

            queue = options.get("queue")
            exchange_type = queue.exchange.type if queue else options["exchange_type"]
            routing_key = queue.routing_key if queue else options["routing_key"]
            exchange_name = queue.exchange.name if queue else options["exchange"]

            if exchange_type != 'direct':
                if eta:
                    if isinstance(eta, str):
                        eta = isoparse(eta)
                    countdown = (maybe_make_aware(eta) - self.now()).total_seconds()

                if countdown:
                    if countdown > 0:
                        routing_key = calculate_routing_key(int(countdown), routing_key)
                        exchange = Exchange(
                            'celery_delayed_27',
                            type='topic',
                        )
                        options.pop("queue", None)
                        options['routing_key'] = routing_key
                        options['exchange'] = exchange

            else:
                logger.warning(
                    'Direct exchanges are not supported with native delayed delivery.\n'
                    f'{exchange_name} is a direct exchange but should be a topic exchange or '
                    'a fanout exchange in order for native delayed delivery to work properly.\n'
                    'If quorum queues are used, this task may block the worker process until the ETA arrives.'
                )

        if expires is not None:
            if isinstance(expires, datetime):
                expires_s = (maybe_make_aware(
                    expires) - self.now()).total_seconds()
            elif isinstance(expires, str):
                expires_s = (maybe_make_aware(
                    isoparse(expires)) - self.now()).total_seconds()
            else:
                expires_s = expires

            if expires_s < 0:
                logger.warning(
                    f"{task_id} has an expiration date in the past ({-expires_s}s ago).\n"
                    "We assume this is intended and so we have set the "
                    "expiration date to 0 instead.\n"
                    "According to RabbitMQ's documentation:\n"
                    "\"Setting the TTL to 0 causes messages to be expired upon "
                    "reaching a queue unless they can be delivered to a "
                    "consumer immediately.\"\n"
                    "If this was unintended, please check the code which "
                    "published this task."
                )
                expires_s = 0

            options["expiration"] = expires_s

        if not root_id or not parent_id:
            parent = self.current_worker_task
            if parent:
                if not root_id:
                    root_id = parent.request.root_id or parent.request.id
                if not parent_id:
                    parent_id = parent.request.id

                if conf.task_inherit_parent_priority:
                    options.setdefault('priority',
                                       parent.request.delivery_info.get('priority'))

        # alias for 'task_as_v2'
        message = amqp.create_task_message(
            task_id, name, args, kwargs, countdown, eta, group_id, group_index,
            expires, retries, chord,
            maybe_list(link), maybe_list(link_error),
            reply_to or self.thread_oid, time_limit, soft_time_limit,
            self.conf.task_send_sent_event,
            root_id, parent_id, shadow, chain,
            ignore_result=ignore_result,
            replaced_task_nesting=replaced_task_nesting, **options
        )

        stamped_headers = options.pop('stamped_headers', [])
        for stamp in stamped_headers:
            options.pop(stamp)

        if connection:
            producer = amqp.Producer(connection, auto_declare=False)

        with self.producer_or_acquire(producer) as P:
            with P.connection._reraise_as_library_errors():
                if not ignore_result:
                    self.backend.on_task_call(P, task_id)
                amqp.send_task_message(P, name, message, **options)
        result = (result_cls or self.AsyncResult)(task_id)
        # We avoid using the constructor since a custom result class
        # can be used, in which case the constructor may still use
        # the old signature.
        result.ignored = ignore_result

        if add_to_parent:
            if not have_parent:
                parent, have_parent = self.current_worker_task, True
            if parent:
                parent.add_trail(result)
        return result

    def connection_for_read(self, url=None, **kwargs):
        """Establish connection used for consuming.

        See Also:
            :meth:`connection` for supported arguments.
        """
        return self._connection(url or self.conf.broker_read_url, **kwargs)

    def connection_for_write(self, url=None, **kwargs):
        """Establish connection used for producing.

        See Also:
            :meth:`connection` for supported arguments.
        """
        return self._connection(url or self.conf.broker_write_url, **kwargs)

    def connection(self, hostname=None, userid=None, password=None,
                   virtual_host=None, port=None, ssl=None,
