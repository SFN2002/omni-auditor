from __future__ import annotations

import collections.abc as cabc
import enum
import errno
import inspect
import os
import sys
import typing as t
from collections import abc
from collections import Counter
from contextlib import AbstractContextManager
from contextlib import contextmanager
from contextlib import ExitStack
from functools import update_wrapper
from gettext import gettext as _
from gettext import ngettext
from itertools import repeat
from types import TracebackType

from . import types
from ._utils import FLAG_NEEDS_VALUE
from ._utils import UNSET
from .exceptions import Abort
from .exceptions import BadParameter
from .exceptions import ClickException
from .exceptions import Exit
from .exceptions import MissingParameter
from .exceptions import NoArgsIsHelpError
from .exceptions import UsageError
from .formatting import HelpFormatter
from .formatting import join_options
from .globals import pop_context
from .globals import push_context
from .parser import _OptionParser
from .parser import _split_opt
from .termui import confirm
from .termui import prompt
from .termui import style
from .utils import _detect_program_name
from .utils import _expand_args
from .utils import echo
from .utils import make_default_short_help
from .utils import make_str
from .utils import PacifyFlushWrapper

if t.TYPE_CHECKING:
    from .shell_completion import CompletionItem

F = t.TypeVar("F", bound="t.Callable[..., t.Any]")
V = t.TypeVar("V")


def _complete_visible_commands(
    ctx: Context, incomplete: str
) -> cabc.Iterator[tuple[str, Command]]:
    """List all the subcommands of a group that start with the
    incomplete value and aren't hidden.

    :param ctx: Invocation context for the group.
    :param incomplete: Value being completed. May be empty.
    """
    multi = t.cast(Group, ctx.command)

    for name in multi.list_commands(ctx):
        if name.startswith(incomplete):
            command = multi.get_command(ctx, name)

            if command is not None and not command.hidden:
                yield name, command


def _check_nested_chain(
    base_command: Group, cmd_name: str, cmd: Command, register: bool = False
) -> None:
    if not base_command.chain or not isinstance(cmd, Group):
        return

    if register:
        message = (
            f"It is not possible to add the group {cmd_name!r} to another"
            f" group {base_command.name!r} that is in chain mode."
        )
    else:
        message = (
            f"Found the group {cmd_name!r} as subcommand to another group "
            f" {base_command.name!r} that is in chain mode. This is not supported."
        )

    raise RuntimeError(message)


def batch(iterable: cabc.Iterable[V], batch_size: int) -> list[tuple[V, ...]]:
    return list(zip(*repeat(iter(iterable), batch_size), strict=False))


@contextmanager
def augment_usage_errors(
    ctx: Context, param: Parameter | None = None
) -> cabc.Iterator[None]:
    """Context manager that attaches extra information to exceptions."""
    try:
        yield
    except BadParameter as e:
        if e.ctx is None:
            e.ctx = ctx
        if param is not None and e.param is None:
            e.param = param
        raise
    except UsageError as e:
        if e.ctx is None:
            e.ctx = ctx
        raise


def iter_params_for_processing(
    invocation_order: cabc.Sequence[Parameter],
    declaration_order: cabc.Sequence[Parameter],
) -> list[Parameter]:
    """Returns all declared parameters in the order they should be processed.

    The declared parameters are re-shuffled depending on the order in which
    they were invoked, as well as the eagerness of each parameters.

    The invocation order takes precedence over the declaration order. I.e. the
    order in which the user provided them to the CLI is respected.

    This behavior and its effect on callback evaluation is detailed at:
    https://click.palletsprojects.com/en/stable/advanced/#callback-evaluation-order
    """

    def sort_key(item: Parameter) -> tuple[bool, float]:
        try:
            idx: float = invocation_order.index(item)
        except ValueError:
            idx = float("inf")

        return not item.is_eager, idx

    return sorted(declaration_order, key=sort_key)


class ParameterSource(enum.IntEnum):
    """This is an :class:`~enum.IntEnum` that indicates the source of a
    parameter's value.

    Use :meth:`click.Context.get_parameter_source` to get the
    source for a parameter by name.

    Members are ordered from most explicit to least explicit source.
    This allows comparison to check if a value was explicitly provided:

    .. code-block:: python

        source = ctx.get_parameter_source("port")
        if source < click.ParameterSource.DEFAULT_MAP:
            ...  # value was explicitly set

    .. versionchanged:: 8.3.3
        Use :class:`~enum.IntEnum` and reorder members from most to
        least explicit. Supports comparison operators.

    .. versionchanged:: 8.0
        Use :class:`~enum.Enum` and drop the ``validate`` method.

    .. versionchanged:: 8.0
        Added the ``PROMPT`` value.
    """

    PROMPT = enum.auto()
    """Used a prompt to confirm a default or provide a value."""
    COMMANDLINE = enum.auto()
    """The value was provided by the command line args."""
    ENVIRONMENT = enum.auto()
    """The value was provided with an environment variable."""
    DEFAULT_MAP = enum.auto()
    """Used a default provided by :attr:`Context.default_map`."""
    DEFAULT = enum.auto()
    """Used the default specified by the parameter."""


class Context:
    """The context is a special internal object that holds state relevant
    for the script execution at every single level.  It's normally invisible
    to commands unless they opt-in to getting access to it.

    The context is useful as it can pass internal objects around and can
    control special execution features such as reading data from
    environment variables.

    A context can be used as context manager in which case it will call
    :meth:`close` on teardown.

    :param command: the command class for this context.
    :param parent: the parent context.
    :param info_name: the info name for this invocation.  Generally this
                      is the most descriptive name for the script or
                      command.  For the toplevel script it is usually
                      the name of the script, for commands below it it's
                      the name of the script.
    :param obj: an arbitrary object of user data.
    :param auto_envvar_prefix: the prefix to use for automatic environment
                               variables.  If this is `None` then reading
                               from environment variables is disabled.  This
                               does not affect manually set environment
                               variables which are always read.
    :param default_map: a dictionary (like object) with default values
                        for parameters.
    :param terminal_width: the width of the terminal.  The default is
                           inherit from parent context.  If no context
                           defines the terminal width then auto
                           detection will be applied.
    :param max_content_width: the maximum width for content rendered by
                              Click (this currently only affects help
                              pages).  This defaults to 80 characters if
                              not overridden.  In other words: even if the
                              terminal is larger than that, Click will not
                              format things wider than 80 characters by
                              default.  In addition to that, formatters might
                              add some safety mapping on the right.
    :param resilient_parsing: if this flag is enabled then Click will
                              parse without any interactivity or callback
                              invocation.  Default values will also be
                              ignored.  This is useful for implementing
                              things such as completion support.
    :param allow_extra_args: if this is set to `True` then extra arguments
                             at the end will not raise an error and will be
                             kept on the context.  The default is to inherit
                             from the command.
    :param allow_interspersed_args: if this is set to `False` then options
                                    and arguments cannot be mixed.  The
                                    default is to inherit from the command.
    :param ignore_unknown_options: instructs click to ignore options it does
                                   not know and keeps them for later
                                   processing.
    :param help_option_names: optionally a list of strings that define how
                              the default help parameter is named.  The
                              default is ``['--help']``.
    :param token_normalize_func: an optional function that is used to
                                 normalize tokens (options, choices,
                                 etc.).  This for instance can be used to
                                 implement case insensitive behavior.
    :param color: controls if the terminal supports ANSI colors or not.  The
                  default is autodetection.  This is only needed if ANSI
                  codes are used in texts that Click prints which is by
                  default not the case.  This for instance would affect
                  help output.
    :param show_default: Show the default value for commands. If this
        value is not set, it defaults to the value from the parent
        context. ``Command.show_default`` overrides this default for the
        specific command.

    .. versionchanged:: 8.2
        The ``protected_args`` attribute is deprecated and will be removed in
        Click 9.0. ``args`` will contain remaining unparsed tokens.

    .. versionchanged:: 8.1
        The ``show_default`` parameter is overridden by
        ``Command.show_default``, instead of the other way around.

    .. versionchanged:: 8.0
        The ``show_default`` parameter defaults to the value from the
        parent context.

    .. versionchanged:: 7.1
       Added the ``show_default`` parameter.

    .. versionchanged:: 4.0
        Added the ``color``, ``ignore_unknown_options``, and
        ``max_content_width`` parameters.

    .. versionchanged:: 3.0
        Added the ``allow_extra_args`` and ``allow_interspersed_args``
        parameters.

    .. versionchanged:: 2.0
        Added the ``resilient_parsing``, ``help_option_names``, and
        ``token_normalize_func`` parameters.
    """

    #: The formatter class to create with :meth:`make_formatter`.
    #:
    #: .. versionadded:: 8.0
    formatter_class: type[HelpFormatter] = HelpFormatter

    def __init__(
        self,
        command: Command,
        parent: Context | None = None,
        info_name: str | None = None,
        obj: t.Any | None = None,
        auto_envvar_prefix: str | None = None,
        default_map: cabc.MutableMapping[str, t.Any] | None = None,
        terminal_width: int | None = None,
        max_content_width: int | None = None,
        resilient_parsing: bool = False,
        allow_extra_args: bool | None = None,
        allow_interspersed_args: bool | None = None,
        ignore_unknown_options: bool | None = None,
        help_option_names: list[str] | None = None,
        token_normalize_func: t.Callable[[str], str] | None = None,
        color: bool | None = None,
        show_default: bool | None = None,
    ) -> None:
        #: the parent context or `None` if none exists.
        self.parent = parent
        #: the :class:`Command` for this context.
        self.command = command
        #: the descriptive information name
        self.info_name = info_name
        #: Map of parameter names to their parsed values. Parameters
        #: with ``expose_value=False`` are not stored.
        self.params: dict[str, t.Any] = {}
        #: the leftover arguments.
        self.args: list[str] = []
        #: protected arguments.  These are arguments that are prepended
        #: to `args` when certain parsing scenarios are encountered but
        #: must be never propagated to another arguments.  This is used
        #: to implement nested parsing.
        self._protected_args: list[str] = []
        #: the collected prefixes of the command's options.
        self._opt_prefixes: set[str] = set(parent._opt_prefixes) if parent else set()

        if obj is None and parent is not None:
            obj = parent.obj

        #: the user object stored.
        self.obj: t.Any = obj
        self._meta: dict[str, t.Any] = getattr(parent, "meta", {})

        #: A dictionary (-like object) with defaults for parameters.
        if (
            default_map is None
            and info_name is not None
            and parent is not None
            and parent.default_map is not None
        ):
            default_map = parent.default_map.get(info_name)

        self.default_map: cabc.MutableMapping[str, t.Any] | None = default_map

        #: This flag indicates if a subcommand is going to be executed. A
        #: group callback can use this information to figure out if it's
        #: being executed directly or because the execution flow passes
        #: onwards to a subcommand. By default it's None, but it can be
        #: the name of the subcommand to execute.
        #:
        #: If chaining is enabled this will be set to ``'*'`` in case
        #: any commands are executed.  It is however not possible to
        #: figure out which ones.  If you require this knowledge you
        #: should use a :func:`result_callback`.
        self.invoked_subcommand: str | None = None

        if terminal_width is None and parent is not None:
            terminal_width = parent.terminal_width

        #: The width of the terminal (None is autodetection).
        self.terminal_width: int | None = terminal_width

        if max_content_width is None and parent is not None:
            max_content_width = parent.max_content_width

        #: The maximum width of formatted content (None implies a sensible
        #: default which is 80 for most things).
        self.max_content_width: int | None = max_content_width

        if allow_extra_args is None:
            allow_extra_args = command.allow_extra_args

        #: Indicates if the context allows extra args or if it should
        #: fail on parsing.
        #:
        #: .. versionadded:: 3.0
        self.allow_extra_args = allow_extra_args

        if allow_interspersed_args is None:
            allow_interspersed_args = command.allow_interspersed_args

        #: Indicates if the context allows mixing of arguments and
        #: options or not.
        #:
        #: .. versionadded:: 3.0
        self.allow_interspersed_args: bool = allow_interspersed_args

        if ignore_unknown_options is None:
            ignore_unknown_options = command.ignore_unknown_options

        #: Instructs click to ignore options that a command does not
        #: understand and will store it on the context for later
        #: processing.  This is primarily useful for situations where you
        #: want to call into external programs.  Generally this pattern is
        #: strongly discouraged because it's not possibly to losslessly
        #: forward all arguments.
        #:
        #: .. versionadded:: 4.0
        self.ignore_unknown_options: bool = ignore_unknown_options

        if help_option_names is None:
            if parent is not None:
                help_option_names = parent.help_option_names
            else:
                help_option_names = ["--help"]

        #: The names for the help options.
        self.help_option_names: list[str] = help_option_names

        if token_normalize_func is None and parent is not None:
            token_normalize_func = parent.token_normalize_func

        #: An optional normalization function for tokens.  This is
        #: options, choices, commands etc.
        self.token_normalize_func: t.Callable[[str], str] | None = token_normalize_func

        #: Indicates if resilient parsing is enabled.  In that case Click
        #: will do its best to not cause any failures and default values
        #: will be ignored. Useful for completion.
        self.resilient_parsing: bool = resilient_parsing

        # If there is no envvar prefix yet, but the parent has one and
        # the command on this level has a name, we can expand the envvar
        # prefix automatically.
        if auto_envvar_prefix is None:
            if (
                parent is not None
                and parent.auto_envvar_prefix is not None
                and self.info_name is not None
            ):
                auto_envvar_prefix = (
                    f"{parent.auto_envvar_prefix}_{self.info_name.upper()}"
                )
        else:
            auto_envvar_prefix = auto_envvar_prefix.upper()

        if auto_envvar_prefix is not None:
            auto_envvar_prefix = auto_envvar_prefix.replace("-", "_")

        self.auto_envvar_prefix: str | None = auto_envvar_prefix

        if color is None and parent is not None:
            color = parent.color

        #: Controls if styling output is wanted or not.
        self.color: bool | None = color

        if show_default is None and parent is not None:
            show_default = parent.show_default

        #: Show option default values when formatting help text.
        self.show_default: bool | None = show_default

        self._close_callbacks: list[t.Callable[[], t.Any]] = []
        self._depth = 0
        self._parameter_source: dict[str, ParameterSource] = {}
        self._exit_stack = ExitStack()

    @property
    def protected_args(self) -> list[str]:
        import warnings

        warnings.warn(
            "'protected_args' is deprecated and will be removed in Click 9.0."
            " 'args' will contain remaining unparsed tokens.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self._protected_args

    def to_info_dict(self) -> dict[str, t.Any]:
        """Gather information that could be useful for a tool generating
        user-facing documentation. This traverses the entire CLI
        structure.

        .. code-block:: python

            with Context(cli) as ctx:
                info = ctx.to_info_dict()

        .. versionadded:: 8.0
        """
        return {
            "command": self.command.to_info_dict(self),
            "info_name": self.info_name,
            "allow_extra_args": self.allow_extra_args,
            "allow_interspersed_args": self.allow_interspersed_args,
            "ignore_unknown_options": self.ignore_unknown_options,
            "auto_envvar_prefix": self.auto_envvar_prefix,
        }

    def __enter__(self) -> Context:
        self._depth += 1
        push_context(self)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        tb: TracebackType | None,
    ) -> bool | None:
        self._depth -= 1
        exit_result: bool | None = None
        if self._depth == 0:
            exit_result = self._close_with_exception_info(exc_type, exc_value, tb)
        pop_context()

        return exit_result

    @contextmanager
    def scope(self, cleanup: bool = True) -> cabc.Iterator[Context]:
        """This helper method can be used with the context object to promote
        it to the current thread local (see :func:`get_current_context`).
        The default behavior of this is to invoke the cleanup functions which
        can be disabled by setting `cleanup` to `False`.  The cleanup
        functions are typically used for things such as closing file handles.

        If the cleanup is intended the context object can also be directly
        used as a context manager.

        Example usage::

            with ctx.scope():
                assert get_current_context() is ctx

        This is equivalent::

            with ctx:
                assert get_current_context() is ctx

        .. versionadded:: 5.0

        :param cleanup: controls if the cleanup functions should be run or
                        not.  The default is to run these functions.  In
                        some situations the context only wants to be
                        temporarily pushed in which case this can be disabled.
                        Nested pushes automatically defer the cleanup.
        """
        if not cleanup:
            self._depth += 1
        try:
            with self as rv:
                yield rv
        finally:
            if not cleanup:
                self._depth -= 1

    @property
    def meta(self) -> dict[str, t.Any]:
        """This is a dictionary which is shared with all the contexts
        that are nested.  It exists so that click utilities can store some
        state here if they need to.  It is however the responsibility of
        that code to manage this dictionary well.

        The keys are supposed to be unique dotted strings.  For instance
        module paths are a good choice for it.  What is stored in there is
        irrelevant for the operation of click.  However what is important is
        that code that places data here adheres to the general semantics of
        the system.

        Example usage::

            LANG_KEY = f'{__name__}.lang'

            def set_language(value):
                ctx = get_current_context()
                ctx.meta[LANG_KEY] = value

            def get_language():
                return get_current_context().meta.get(LANG_KEY, 'en_US')

        .. versionadded:: 5.0
        """
        return self._meta

    def make_formatter(self) -> HelpFormatter:
        """Creates the :class:`~click.HelpFormatter` for the help and
        usage output.

        To quickly customize the formatter class used without overriding
        this method, set the :attr:`formatter_class` attribute.

        .. versionchanged:: 8.0
            Added the :attr:`formatter_class` attribute.
        """
        return self.formatter_class(
            width=self.terminal_width, max_width=self.max_content_width
        )

    def with_resource(self, context_manager: AbstractContextManager[V]) -> V:
        """Register a resource as if it were used in a ``with``
        statement. The resource will be cleaned up when the context is
        popped.

        Uses :meth:`contextlib.ExitStack.enter_context`. It calls the
        resource's ``__enter__()`` method and returns the result. When
        the context is popped, it closes the stack, which calls the
        resource's ``__exit__()`` method.

        To register a cleanup function for something that isn't a
        context manager, use :meth:`call_on_close`. Or use something
        from :mod:`contextlib` to turn it into a context manager first.

        .. code-block:: python

            @click.group()
            @click.option("--name")
            @click.pass_context
            def cli(ctx):
                ctx.obj = ctx.with_resource(connect_db(name))

        :param context_manager: The context manager to enter.
        :return: Whatever ``context_manager.__enter__()`` returns.

        .. versionadded:: 8.0
        """
        return self._exit_stack.enter_context(context_manager)

    def call_on_close(self, f: t.Callable[..., t.Any]) -> t.Callable[..., t.Any]:
        """Register a function to be called when the context tears down.

        This can be used to close resources opened during the script
        execution. Resources that support Python's context manager
        protocol which would be used in a ``with`` statement should be
        registered with :meth:`with_resource` instead.

        :param f: The function to execute on teardown.
        """
        return self._exit_stack.callback(f)

    def close(self) -> None:
        """Invoke all close callbacks registered with
        :meth:`call_on_close`, and exit all context managers entered
        with :meth:`with_resource`.
        """
        self._close_with_exception_info(None, None, None)

    def _close_with_exception_info(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        tb: TracebackType | None,
    ) -> bool | None:
        """Unwind the exit stack by calling its :meth:`__exit__` providing the exception
        information to allow for exception handling by the various resources registered
        using :meth;`with_resource`

        :return: Whatever ``exit_stack.__exit__()`` returns.
        """
        exit_result = self._exit_stack.__exit__(exc_type, exc_value, tb)
        # In case the context is reused, create a new exit stack.
        self._exit_stack = ExitStack()

        return exit_result

    @property
    def command_path(self) -> str:
        """The computed command path.  This is used for the ``usage``
        information on the help page.  It's automatically created by
        combining the info names of the chain of contexts to the root.
        """
        rv = ""
        if self.info_name is not None:
            rv = self.info_name
        if self.parent is not None:
            parent_command_path = [self.parent.command_path]

            if isinstance(self.parent.command, Command):
                for param in self.parent.command.get_params(self):
                    parent_command_path.extend(param.get_usage_pieces(self))

            rv = f"{' '.join(parent_command_path)} {rv}"
        return rv.lstrip()

    def find_root(self) -> Context:
        """Finds the outermost context."""
        node = self
        while node.parent is not None:
            node = node.parent
        return node

    def find_object(self, object_type: type[V]) -> V | None:
        """Finds the closest object of a given type."""
        node: Context | None = self

        while node is not None:
            if isinstance(node.obj, object_type):
                return node.obj

            node = node.parent

        return None

    def ensure_object(self, object_type: type[V]) -> V:
        """Like :meth:`find_object` but sets the innermost object to a
        new instance of `object_type` if it does not exist.
        """
        rv = self.find_object(object_type)
        if rv is None:
            self.obj = rv = object_type()
        return rv

    def _default_map_has(self, name: str | None) -> bool:
        """Check if :attr:`default_map` contains a real value for ``name``.

        Returns ``False`` when the key is absent, the map is ``None``,
        ``name`` is ``None``, or the stored value is the internal
        :data:`UNSET` sentinel.
        """
        return (
            name is not None
            and self.default_map is not None
            and name in self.default_map
            and self.default_map[name] is not UNSET
        )

    @t.overload
    def lookup_default(
        self, name: str, call: t.Literal[True] = True
    ) -> t.Any | None: ...

    @t.overload
    def lookup_default(
        self, name: str, call: t.Literal[False] = ...
    ) -> t.Any | t.Callable[[], t.Any] | None: ...

    def lookup_default(self, name: str, call: bool = True) -> t.Any | None:
        """Get the default for a parameter from :attr:`default_map`.

        :param name: Name of the parameter.
        :param call: If the default is a callable, call it. Disable to
            return the callable instead.

        .. versionchanged:: 8.0
            Added the ``call`` parameter.
        """
        if not self._default_map_has(name):
            return None

        # Assert to make the type checker happy.
        assert self.default_map is not None
        value = self.default_map[name]

        if call and callable(value):
            return value()

        return value

    def fail(self, message: str) -> t.NoReturn:
        """Aborts the execution of the program with a specific error
        message.

        :param message: the error message to fail with.
        """
        raise UsageError(message, self)

    def abort(self) -> t.NoReturn:
        """Aborts the script."""
        raise Abort()

    def exit(self, code: int = 0) -> t.NoReturn:
        """Exits the application with a given exit code.

        .. versionchanged:: 8.2
            Callbacks and context managers registered with :meth:`call_on_close`
            and :meth:`with_resource` are closed before exiting.
        """
        self.close()
        raise Exit(code)

    def get_usage(self) -> str:
        """Helper method to get formatted usage string for the current
        context and command.
        """
        return self.command.get_usage(self)

    def get_help(self) -> str:
        """Helper method to get formatted help page for the current
        context and command.
        """
        return self.command.get_help(self)

    def _make_sub_context(self, command: Command) -> Context:
        """Create a new context of the same type as this context, but
        for a new command.

        :meta private:
        """
        return type(self)(command, info_name=command.name, parent=self)

    @t.overload
    def invoke(
        self, callback: t.Callable[..., V], /, *args: t.Any, **kwargs: t.Any
    ) -> V: ...

    @t.overload
    def invoke(self, callback: Command, /, *args: t.Any, **kwargs: t.Any) -> t.Any: ...

    def invoke(
        self, callback: Command | t.Callable[..., V], /, *args: t.Any, **kwargs: t.Any
    ) -> t.Any | V:
        """Invokes a command callback in exactly the way it expects.  There
        are two ways to invoke this method:

        1.  the first argument can be a callback and all other arguments and
            keyword arguments are forwarded directly to the function.
        2.  the first argument is a click command object.  In that case all
            arguments are forwarded as well but proper click parameters
            (options and click arguments) must be keyword arguments and Click
            will fill in defaults.

        .. versionchanged:: 8.0
            All ``kwargs`` are tracked in :attr:`params` so they will be
            passed if :meth:`forward` is called at multiple levels.

        .. versionchanged:: 3.2
            A new context is created, and missing arguments use default values.
        """
        if isinstance(callback, Command):
            other_cmd = callback

            if other_cmd.callback is None:
                raise TypeError(
                    "The given command does not have a callback that can be invoked."
                )
            else:
                callback = t.cast("t.Callable[..., V]", other_cmd.callback)

            ctx = self._make_sub_context(other_cmd)

            for param in other_cmd.params:
                if param.name not in kwargs and param.expose_value:
                    default_value = param.get_default(ctx)
                    # We explicitly hide the :attr:`UNSET` value to the user, as we
                    # choose to make it an implementation detail. And because ``invoke``
                    # has been designed as part of Click public API, we return ``None``
                    # instead. Refs:
                    # https://github.com/pallets/click/issues/3066
                    # https://github.com/pallets/click/issues/3065
                    # https://github.com/pallets/click/pull/3068
                    if default_value is UNSET:
                        default_value = None
                    kwargs[param.name] = param.type_cast_value(  # type: ignore
                        ctx, default_value
                    )

            # Track all kwargs as params, so that forward() will pass
            # them on in subsequent calls.
            ctx.params.update(kwargs)
        else:
            ctx = self

        with augment_usage_errors(self):
            with ctx:
                return callback(*args, **kwargs)

    def forward(self, cmd: Command, /, *args: t.Any, **kwargs: t.Any) -> t.Any:
        """Similar to :meth:`invoke` but fills in default keyword
        arguments from the current context if the other command expects
        it.  This cannot invoke callbacks directly, only other commands.

        .. versionchanged:: 8.0
            All ``kwargs`` are tracked in :attr:`params` so they will be
            passed if ``forward`` is called at multiple levels.
        """
        # Can only forward to other commands, not direct callbacks.
        if not isinstance(cmd, Command):
            raise TypeError("Callback is not a command.")

        for param in self.params:
            if param not in kwargs:
                kwargs[param] = self.params[param]

        return self.invoke(cmd, *args, **kwargs)

    def set_parameter_source(self, name: str, source: ParameterSource) -> None:
        """Set the source of a parameter. This indicates the location
        from which the value of the parameter was obtained.

        :param name: The name of the parameter.
        :param source: A member of :class:`~click.core.ParameterSource`.
        """
        self._parameter_source[name] = source

    def get_parameter_source(self, name: str) -> ParameterSource | None:
        """Get the source of a parameter. This indicates the location
        from which the value of the parameter was obtained.

        This can be useful for determining when a user specified a value
        on the command line that is the same as the default value. It
        will be :attr:`~click.core.ParameterSource.DEFAULT` only if the
        value was actually taken from the default.

        :param name: The name of the parameter.
        :rtype: ParameterSource

        .. versionchanged:: 8.0
            Returns ``None`` if the parameter was not provided from any
            source.
        """
        return self._parameter_source.get(name)


class Command:
    """Commands are the basic building block of command line interfaces in
    Click.  A basic command handles command line parsing and might dispatch
    more parsing to commands nested below it.

    :param name: the name of the command to use unless a group overrides it.
    :param context_settings: an optional dictionary with defaults that are
                             passed to the context object.
    :param callback: the callback to invoke.  This is optional.
    :param params: the parameters to register with this command.  This can
                   be either :class:`Option` or :class:`Argument` objects.
    :param help: the help string to use for this command.
    :param epilog: like the help string but it's printed at the end of the
                   help page after everything else.
    :param short_help: the short help to use for this command.  This is
                       shown on the command listing of the parent command.
    :param add_help_option: by default each command registers a ``--help``
                            option.  This can be disabled by this parameter.
    :param no_args_is_help: this controls what happens if no arguments are
                            provided.  This option is disabled by default.
                            If enabled this will add ``--help`` as argument
                            if no arguments are passed
    :param hidden: hide this command from help outputs.
    :param deprecated: If ``True`` or non-empty string, issues a message
                        indicating that the command is deprecated and highlights
                        its deprecation in --help. The message can be customized
                        by using a string as the value.

    .. versionchanged:: 8.2
        This is the base class for all commands, not ``BaseCommand``.
        ``deprecated`` can be set to a string as well to customize the
        deprecation message.

    .. versionchanged:: 8.1
        ``help``, ``epilog``, and ``short_help`` are stored unprocessed,
        all formatting is done when outputting help text, not at init,
        and is done even if not using the ``@command`` decorator.

    .. versionchanged:: 8.0
        Added a ``repr`` showing the command name.

    .. versionchanged:: 7.1
        Added the ``no_args_is_help`` parameter.

    .. versionchanged:: 2.0
        Added the ``context_settings`` parameter.
    """

    #: The context class to create with :meth:`make_context`.
    #:
    #: .. versionadded:: 8.0
    context_class: type[Context] = Context

    #: the default for the :attr:`Context.allow_extra_args` flag.
    allow_extra_args = False

    #: the default for the :attr:`Context.allow_interspersed_args` flag.
    allow_interspersed_args = True

    #: the default for the :attr:`Context.ignore_unknown_options` flag.
    ignore_unknown_options = False

    def __init__(
        self,
        name: str | None,
        context_settings: cabc.MutableMapping[str, t.Any] | None = None,
        callback: t.Callable[..., t.Any] | None = None,
        params: list[Parameter] | None = None,
        help: str | None = None,
        epilog: str | None = None,
        short_help: str | None = None,
        options_metavar: str | None = "[OPTIONS]",
        add_help_option: bool = True,
        no_args_is_help: bool = False,
        hidden: bool = False,
        deprecated: bool | str = False,
    ) -> None:
        #: the name the command thinks it has.  Upon registering a command
        #: on a :class:`Group` the group will default the command name
        #: with this information.  You should instead use the
        #: :class:`Context`\'s :attr:`~Context.info_name` attribute.
        self.name = name

        if context_settings is None:
            context_settings = {}

        #: an optional dictionary with defaults passed to the context.
        self.context_settings: cabc.MutableMapping[str, t.Any] = context_settings

        #: the callback to execute when the command fires.  This might be
        #: `None` in which case nothing happens.
        self.callback = callback
        #: the list of parameters for this command in the order they
        #: should show up in the help page and execute.  Eager parameters
        #: will automatically be handled before non eager ones.
        self.params: list[Parameter] = params or []
        self.help = help
        self.epilog = epilog
        self.options_metavar = options_metavar
