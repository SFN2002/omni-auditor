"""Composing task work-flows.

.. seealso:

    You should import these from :mod:`celery` and not this module.
"""

import itertools
import operator
import warnings
from abc import ABCMeta, abstractmethod
from collections import deque
from collections.abc import MutableSequence
from copy import deepcopy
from functools import partial as _partial
from functools import reduce
from operator import itemgetter
from types import GeneratorType

from kombu.utils.functional import fxrange, reprcall
from kombu.utils.objects import cached_property
from kombu.utils.uuid import uuid
from vine import barrier

from celery._state import current_app
from celery.exceptions import CPendingDeprecationWarning
from celery.result import GroupResult, allow_join_result
from celery.utils import abstract
from celery.utils.collections import ChainMap
from celery.utils.functional import _regen
from celery.utils.functional import chunks as _chunks
from celery.utils.functional import is_list, maybe_list, regen, seq_concat_item, seq_concat_seq
from celery.utils.objects import getitem_property
from celery.utils.text import remove_repeating_from_task, truncate

__all__ = (
    'Signature', 'chain', 'xmap', 'xstarmap', 'chunks',
    'group', 'chord', 'signature', 'maybe_signature',
)


def maybe_unroll_group(group):
    """Unroll group with only one member.
    This allows treating a group of a single task as if it
    was a single task without pre-knowledge."""
    # Issue #1656
    try:
        size = len(group.tasks)
    except TypeError:
        try:
            size = group.tasks.__length_hint__()
        except (AttributeError, TypeError):
            return group
        else:
            return list(group.tasks)[0] if size == 1 else group
    else:
        return group.tasks[0] if size == 1 else group


def task_name_from(task):
    return getattr(task, 'name', task)


def _stamp_regen_task(task, visitor, append_stamps, **headers):
    """When stamping a sequence of tasks created by a generator,
    we use this function to stamp each task in the generator
    without exhausting it."""

    task.stamp(visitor, append_stamps, **headers)
    return task


def _merge_dictionaries(d1, d2, aggregate_duplicates=True):
    """Merge two dictionaries recursively into the first one.

    Example:
    >>> d1 = {'dict': {'a': 1}, 'list': [1, 2], 'tuple': (1, 2)}
    >>> d2 = {'dict': {'b': 2}, 'list': [3, 4], 'set': {'a', 'b'}}
    >>> _merge_dictionaries(d1, d2)

    d1 will be modified to: {
        'dict': {'a': 1, 'b': 2},
        'list': [1, 2, 3, 4],
        'tuple': (1, 2),
        'set': {'a', 'b'}
    }

    Arguments:
        d1 (dict): Dictionary to merge into.
        d2 (dict): Dictionary to merge from.
        aggregate_duplicates (bool):
            If True, aggregate duplicated items (by key) into a list of all values in d1 in the same key.
            If False, duplicate keys will be taken from d2 and override the value in d1.
    """
    if not d2:
        return

    for key, value in d1.items():
        if key in d2:
            if isinstance(value, dict):
                _merge_dictionaries(d1[key], d2[key])
            else:
                if isinstance(value, (int, float, str)):
                    d1[key] = [value] if aggregate_duplicates else value
                if isinstance(d2[key], list) and isinstance(d1[key], list):
                    d1[key].extend(d2[key])
                elif aggregate_duplicates:
                    if d1[key] is None:
                        d1[key] = []
                    else:
                        d1[key] = list(d1[key])
                    d1[key].append(d2[key])
    for key, value in d2.items():
        if key not in d1:
            d1[key] = value


class StampingVisitor(metaclass=ABCMeta):
    """Stamping API.  A class that provides a stamping API possibility for
    canvas primitives. If you want to implement stamping behavior for
    a canvas primitive override method that represents it.
    """

    def on_group_start(self, group, **headers) -> dict:
        """Method that is called on group stamping start.

         Arguments:
             group (group): Group that is stamped.
             headers (Dict): Partial headers that could be merged with existing headers.
         Returns:
             Dict: headers to update.
         """
        return {}

    def on_group_end(self, group, **headers) -> None:
        """Method that is called on group stamping end.

         Arguments:
             group (group): Group that is stamped.
             headers (Dict): Partial headers that could be merged with existing headers.
         """
        pass

    def on_chain_start(self, chain, **headers) -> dict:
        """Method that is called on chain stamping start.

         Arguments:
             chain (chain): Chain that is stamped.
             headers (Dict): Partial headers that could be merged with existing headers.
         Returns:
             Dict: headers to update.
         """
        return {}

    def on_chain_end(self, chain, **headers) -> None:
        """Method that is called on chain stamping end.

         Arguments:
             chain (chain): Chain that is stamped.
             headers (Dict): Partial headers that could be merged with existing headers.
         """
        pass

    @abstractmethod
    def on_signature(self, sig, **headers) -> dict:
        """Method that is called on signature stamping.

         Arguments:
             sig (Signature): Signature that is stamped.
             headers (Dict): Partial headers that could be merged with existing headers.
         Returns:
             Dict: headers to update.
         """

    def on_chord_header_start(self, sig, **header) -> dict:
        """Method that is called on сhord header stamping start.

         Arguments:
             sig (chord): chord that is stamped.
             headers (Dict): Partial headers that could be merged with existing headers.
         Returns:
             Dict: headers to update.
         """
        if not isinstance(sig.tasks, group):
            sig.tasks = group(sig.tasks)
        return self.on_group_start(sig.tasks, **header)

    def on_chord_header_end(self, sig, **header) -> None:
        """Method that is called on сhord header stamping end.

           Arguments:
               sig (chord): chord that is stamped.
               headers (Dict): Partial headers that could be merged with existing headers.
        """
        self.on_group_end(sig.tasks, **header)

    def on_chord_body(self, sig, **header) -> dict:
        """Method that is called on chord body stamping.

         Arguments:
             sig (chord): chord that is stamped.
             headers (Dict): Partial headers that could be merged with existing headers.
         Returns:
             Dict: headers to update.
        """
        return {}

    def on_callback(self, callback, **header) -> dict:
        """Method that is called on callback stamping.

         Arguments:
             callback (Signature): callback that is stamped.
             headers (Dict): Partial headers that could be merged with existing headers.
         Returns:
             Dict: headers to update.
         """
        return {}

    def on_errback(self, errback, **header) -> dict:
        """Method that is called on errback stamping.

         Arguments:
             errback (Signature): errback that is stamped.
             headers (Dict): Partial headers that could be merged with existing headers.
         Returns:
             Dict: headers to update.
         """
        return {}


@abstract.CallableSignature.register
class Signature(dict):
    """Task Signature.

    Class that wraps the arguments and execution options
    for a single task invocation.

    Used as the parts in a :class:`group` and other constructs,
    or to pass tasks around as callbacks while being compatible
    with serializers with a strict type subset.

    Signatures can also be created from tasks:

    - Using the ``.signature()`` method that has the same signature
      as ``Task.apply_async``:

        .. code-block:: pycon

            >>> add.signature(args=(1,), kwargs={'kw': 2}, options={})

    - or the ``.s()`` shortcut that works for star arguments:

        .. code-block:: pycon

            >>> add.s(1, kw=2)

    - the ``.s()`` shortcut does not allow you to specify execution options
      but there's a chaining `.set` method that returns the signature:

        .. code-block:: pycon

            >>> add.s(2, 2).set(countdown=10).set(expires=30).delay()

    Note:
        You should use :func:`~celery.signature` to create new signatures.
        The ``Signature`` class is the type returned by that function and
        should be used for ``isinstance`` checks for signatures.

    See Also:
        :ref:`guide-canvas` for the complete guide.

    Arguments:
        task (Union[Type[celery.app.task.Task], str]): Either a task
            class/instance, or the name of a task.
        args (Tuple): Positional arguments to apply.
        kwargs (Dict): Keyword arguments to apply.
        options (Dict): Additional options to :meth:`Task.apply_async`.

    Note:
        If the first argument is a :class:`dict`, the other
        arguments will be ignored and the values in the dict will be used
        instead::

            >>> s = signature('tasks.add', args=(2, 2))
            >>> signature(s)
            {'task': 'tasks.add', args=(2, 2), kwargs={}, options={}}
    """

    TYPES = {}
    _app = _type = None
    # The following fields must not be changed during freezing/merging because
    # to do so would disrupt completion of parent tasks
    _IMMUTABLE_OPTIONS = {"group_id", "stamped_headers"}

    @classmethod
    def register_type(cls, name=None):
        """Register a new type of signature.
        Used as a class decorator, for example:
        >>> @Signature.register_type()
        >>> class mysig(Signature):
        >>>     pass
        """
        def _inner(subclass):
            cls.TYPES[name or subclass.__name__] = subclass
            return subclass

        return _inner

    @classmethod
    def from_dict(cls, d, app=None):
        """Create a new signature from a dict.
        Subclasses can override this method to customize how are
        they created from a dict.
        """
        typ = d.get('subtask_type')
        if typ:
            target_cls = cls.TYPES[typ]
            if target_cls is not cls:
                return target_cls.from_dict(d, app=app)
        return Signature(d, app=app)

    def __init__(self, task=None, args=None, kwargs=None, options=None,
                 type=None, subtask_type=None, immutable=False,
                 app=None, **ex):
        self._app = app

        if isinstance(task, dict):
            super().__init__(task)  # works like dict(d)
        else:
            # Also supports using task class/instance instead of string name.
            try:
                task_name = task.name
            except AttributeError:
                task_name = task
            else:
                self._type = task

            super().__init__(
                task=task_name, args=tuple(args or ()),
                kwargs=kwargs or {},
                options=dict(options or {}, **ex),
                subtask_type=subtask_type,
                immutable=immutable,
            )

    def __call__(self, *partial_args, **partial_kwargs):
        """Call the task directly (in the current process)."""
        args, kwargs, _ = self._merge(partial_args, partial_kwargs, None)
        return self.type(*args, **kwargs)

    def delay(self, *partial_args, **partial_kwargs):
        """Shortcut to :meth:`apply_async` using star arguments."""
        return self.apply_async(partial_args, partial_kwargs)

    def apply(self, args=None, kwargs=None, **options):
        """Call task locally.

        Same as :meth:`apply_async` but executed the task inline instead
        of sending a task message.
        """
        args = args if args else ()
        kwargs = kwargs if kwargs else {}
        # Extra options set to None are dismissed
        options = {k: v for k, v in options.items() if v is not None}
        # For callbacks: extra args are prepended to the stored args.
        args, kwargs, options = self._merge(args, kwargs, options)
        return self.type.apply(args, kwargs, **options)

    def apply_async(self, args=None, kwargs=None, route_name=None, **options):
        """Apply this task asynchronously.

        Arguments:
            args (Tuple): Partial args to be prepended to the existing args.
            kwargs (Dict): Partial kwargs to be merged with existing kwargs.
            options (Dict): Partial options to be merged
                with existing options.

        Returns:
            ~@AsyncResult: promise of future evaluation.

        See also:
            :meth:`~@Task.apply_async` and the :ref:`guide-calling` guide.
        """
        args = args if args else ()
        kwargs = kwargs if kwargs else {}
        # Extra options set to None are dismissed
        options = {k: v for k, v in options.items() if v is not None}
        try:
            _apply = self._apply_async
        except IndexError:  # pragma: no cover
            # no tasks for chain, etc to find type
            return
        # For callbacks: extra args are prepended to the stored args.
        if args or kwargs or options:
            args, kwargs, options = self._merge(args, kwargs, options)
        else:
            args, kwargs, options = self.args, self.kwargs, self.options
        # pylint: disable=too-many-function-args
        #   Works on this, as it's a property
        return _apply(args, kwargs, **options)

    def _merge(self, args=None, kwargs=None, options=None, force=False):
        """Merge partial args/kwargs/options with existing ones.

        If the signature is immutable and ``force`` is False, the existing
        args/kwargs will be returned as-is and only the options will be merged.

        Stamped headers are considered immutable and will not be merged regardless.

        Arguments:
            args (Tuple): Partial args to be prepended to the existing args.
            kwargs (Dict): Partial kwargs to be merged with existing kwargs.
            options (Dict): Partial options to be merged with existing options.
            force (bool): If True, the args/kwargs will be merged even if the signature is
                immutable. The stamped headers are not affected by this option and will not
                be merged regardless.

        Returns:
            Tuple: (args, kwargs, options)
        """
        args = args if args else ()
        kwargs = kwargs if kwargs else {}
        if options is not None:
            # We build a new options dictionary where values in `options`
            # override values in `self.options` except for keys which are
            # noted as being immutable (unrelated to signature immutability)
            # implying that allowing their value to change would stall tasks
            immutable_options = self._IMMUTABLE_OPTIONS
            if "stamped_headers" in self.options:
                immutable_options = self._IMMUTABLE_OPTIONS.union(set(self.options.get("stamped_headers", [])))
            # merge self.options with options without overriding stamped headers from self.options
            new_options = {**self.options, **{
                k: v for k, v in options.items()
                if k not in immutable_options or k not in self.options
            }}
        else:
            new_options = self.options
        if self.immutable and not force:
            return (self.args, self.kwargs, new_options)
        return (tuple(args) + tuple(self.args) if args else self.args,
                dict(self.kwargs, **kwargs) if kwargs else self.kwargs,
                new_options)

    def clone(self, args=None, kwargs=None, **opts):
        """Create a copy of this signature.

        Arguments:
            args (Tuple): Partial args to be prepended to the existing args.
            kwargs (Dict): Partial kwargs to be merged with existing kwargs.
            options (Dict): Partial options to be merged with
                existing options.
        """
        args = args if args else ()
        kwargs = kwargs if kwargs else {}
        # need to deepcopy options so origins links etc. is not modified.
        if args or kwargs or opts:
            args, kwargs, opts = self._merge(args, kwargs, opts)
        else:
            args, kwargs, opts = self.args, self.kwargs, self.options
        signature = Signature.from_dict({'task': self.task,
                                         'args': tuple(args),
                                         'kwargs': kwargs,
                                         'options': deepcopy(opts),
                                         'subtask_type': self.subtask_type,
                                         'immutable': self.immutable},
                                        app=self._app)
        signature._type = self._type
        return signature

    partial = clone

    def freeze(self, _id=None, group_id=None, chord=None,
               root_id=None, parent_id=None, group_index=None):
        """Finalize the signature by adding a concrete task id.

        The task won't be called and you shouldn't call the signature
        twice after freezing it as that'll result in two task messages
        using the same task id.

        The arguments are used to override the signature's headers during
        freezing.

        Arguments:
            _id (str): Task id to use if it didn't already have one.
                New UUID is generated if not provided.
            group_id (str): Group id to use if it didn't already have one.
            chord (Signature): Chord body when freezing a chord header.
            root_id (str): Root id to use.
            parent_id (str): Parent id to use.
            group_index (int): Group index to use.

        Returns:
            ~@AsyncResult: promise of future evaluation.
        """
        # pylint: disable=redefined-outer-name
        #   XXX chord is also a class in outer scope.
        opts = self.options
        try:
            # if there is already an id for this task, return it
            tid = opts['task_id']
        except KeyError:
            # otherwise, use the _id sent to this function, falling back on a generated UUID
            tid = opts['task_id'] = _id or uuid()
        if root_id:
            opts['root_id'] = root_id
        if parent_id:
            opts['parent_id'] = parent_id
        if 'reply_to' not in opts:
            # fall back on unique ID for this thread in the app
            opts['reply_to'] = self.app.thread_oid
        if group_id and "group_id" not in opts:
            opts['group_id'] = group_id
        if chord:
            opts['chord'] = chord
        if group_index is not None:
            opts['group_index'] = group_index
        # pylint: disable=too-many-function-args
        #   Works on this, as it's a property.
        return self.AsyncResult(tid)

    _freeze = freeze

    def replace(self, args=None, kwargs=None, options=None):
        """Replace the args, kwargs or options set for this signature.

        These are only replaced if the argument for the section is
        not :const:`None`.
        """
        signature = self.clone()
        if args is not None:
            signature.args = args
        if kwargs is not None:
            signature.kwargs = kwargs
        if options is not None:
            signature.options = options
        return signature

    def set(self, immutable=None, **options):
        """Set arbitrary execution options (same as ``.options.update(…)``).

        Returns:
            Signature: This is a chaining method call
                (i.e., it will return ``self``).
        """
        if immutable is not None:
            self.set_immutable(immutable)
        self.options.update(options)
        return self

    def set_immutable(self, immutable):
        self.immutable = immutable

    def _stamp_headers(self, visitor_headers=None, append_stamps=False, self_headers=True, **headers):
        """Collect all stamps from visitor, headers and self,
        and return an idempotent dictionary of stamps.

        .. versionadded:: 5.3

        Arguments:
            visitor_headers (Dict): Stamps from a visitor method.
            append_stamps (bool):
                If True, duplicated stamps will be appended to a list.
                If False, duplicated stamps will be replaced by the last stamp.
            self_headers (bool):
                If True, stamps from self.options will be added.
                If False, stamps from self.options will be ignored.
            headers (Dict): Stamps that should be added to headers.

        Returns:
            Dict: Merged stamps.
        """
        # Use append_stamps=False to prioritize visitor_headers over headers in case of duplicated stamps.
        # This will lose duplicated headers from the headers argument, but that is the best effort solution
        # to avoid implicitly casting the duplicated stamp into a list of both stamps from headers and
        # visitor_headers of the same key.
        # Example:
        #   headers = {"foo": "bar1"}
        #   visitor_headers = {"foo": "bar2"}
        #   _merge_dictionaries(headers, visitor_headers, aggregate_duplicates=True)
        #   headers["foo"] == ["bar1", "bar2"] -> The stamp is now a list
        #   _merge_dictionaries(headers, visitor_headers, aggregate_duplicates=False)
        #   headers["foo"] == "bar2" -> "bar1" is lost, but the stamp is according to the visitor

        headers = headers.copy()

        if "stamped_headers" not in headers:
            headers["stamped_headers"] = list(headers.keys())

        # Merge headers with visitor headers
        if visitor_headers is not None:
            visitor_headers = visitor_headers or {}
            if "stamped_headers" not in visitor_headers:
                visitor_headers["stamped_headers"] = list(visitor_headers.keys())

            # Sync from visitor
            _merge_dictionaries(headers, visitor_headers, aggregate_duplicates=append_stamps)
            headers["stamped_headers"] = list(set(headers["stamped_headers"]))

        # Merge headers with self.options
        if self_headers:
            stamped_headers = set(headers.get("stamped_headers", []))
            stamped_headers.update(self.options.get("stamped_headers", []))
            headers["stamped_headers"] = list(stamped_headers)
            # Only merge stamps that are in stamped_headers from self.options
            redacted_options = {k: v for k, v in self.options.items() if k in headers["stamped_headers"]}

            # Sync from self.options
            _merge_dictionaries(headers, redacted_options, aggregate_duplicates=append_stamps)
            headers["stamped_headers"] = list(set(headers["stamped_headers"]))

        return headers

    def stamp(self, visitor=None, append_stamps=False, **headers):
        """Stamp this signature with additional custom headers.
        Using a visitor will pass on responsibility for the stamping
        to the visitor.

        .. versionadded:: 5.3

        Arguments:
            visitor (StampingVisitor): Visitor API object.
            append_stamps (bool):
                If True, duplicated stamps will be appended to a list.
                If False, duplicated stamps will be replaced by the last stamp.
            headers (Dict): Stamps that should be added to headers.
        """
        self.stamp_links(visitor, append_stamps, **headers)
        headers = headers.copy()
        visitor_headers = None
        if visitor is not None:
            visitor_headers = visitor.on_signature(self, **headers) or {}
        headers = self._stamp_headers(visitor_headers, append_stamps, **headers)
        return self.set(**headers)

    def stamp_links(self, visitor, append_stamps=False, **headers):
        """Stamp this signature links (callbacks and errbacks).
        Using a visitor will pass on responsibility for the stamping
        to the visitor.

        Arguments:
            visitor (StampingVisitor): Visitor API object.
            append_stamps (bool):
                If True, duplicated stamps will be appended to a list.
                If False, duplicated stamps will be replaced by the last stamp.
            headers (Dict): Stamps that should be added to headers.
        """
        non_visitor_headers = headers.copy()

        # When we are stamping links, we want to avoid adding stamps from the linked signature itself
        # so we turn off self_headers to stamp the link only with the visitor and the headers.
        # If it's enabled, the link copies the stamps of the linked signature, and we don't want that.
        self_headers = False

        # Stamp all of the callbacks of this signature
        headers = deepcopy(non_visitor_headers)
        for link in maybe_list(self.options.get('link')) or []:
            link = maybe_signature(link, app=self.app)
            visitor_headers = None
            if visitor is not None:
                visitor_headers = visitor.on_callback(link, **headers) or {}
            headers = self._stamp_headers(
                visitor_headers=visitor_headers,
                append_stamps=append_stamps,
                self_headers=self_headers,
                **headers
            )
            link.stamp(visitor, append_stamps, **headers)

        # Stamp all of the errbacks of this signature
        headers = deepcopy(non_visitor_headers)
        for link in maybe_list(self.options.get('link_error')) or []:
            link = maybe_signature(link, app=self.app)
            visitor_headers = None
            if visitor is not None:
                visitor_headers = visitor.on_errback(link, **headers) or {}
            headers = self._stamp_headers(
                visitor_headers=visitor_headers,
                append_stamps=append_stamps,
                self_headers=self_headers,
                **headers
            )
            link.stamp(visitor, append_stamps, **headers)

    def _with_list_option(self, key):
        """Gets the value at the given self.options[key] as a list.

        If the value is not a list, it will be converted to one and saved in self.options.
        If the key does not exist, an empty list will be set and returned instead.

        Arguments:
            key (str): The key to get the value for.

        Returns:
            List: The value at the given key as a list or an empty list if the key does not exist.
        """
        items = self.options.setdefault(key, [])
        if not isinstance(items, MutableSequence):
            items = self.options[key] = [items]
        return items

    def append_to_list_option(self, key, value):
        """Appends the given value to the list at the given key in self.options."""
        items = self._with_list_option(key)
        if value not in items:
            items.append(value)
        return value

    def extend_list_option(self, key, value):
        """Extends the list at the given key in self.options with the given value.

        If the value is not a list, it will be converted to one.
        """
        items = self._with_list_option(key)
        items.extend(maybe_list(value))

    def link(self, callback):
        """Add callback task to be applied if this task succeeds.

        Returns:
            Signature: the argument passed, for chaining
                or use with :func:`~functools.reduce`.
        """
        return self.append_to_list_option('link', callback)

    def link_error(self, errback):
        """Add callback task to be applied on error in task execution.

        Returns:
            Signature: the argument passed, for chaining
                or use with :func:`~functools.reduce`.
        """
        return self.append_to_list_option('link_error', errback)

    def on_error(self, errback):
        """Version of :meth:`link_error` that supports chaining.

        on_error chains the original signature, not the errback so::

            >>> add.s(2, 2).on_error(errback.s()).delay()

        calls the ``add`` task, not the ``errback`` task, but the
        reverse is true for :meth:`link_error`.
        """
        self.link_error(errback)
        return self

    def flatten_links(self):
        """Return a recursive list of dependencies.

        "unchain" if you will, but with links intact.
        """
        return list(itertools.chain.from_iterable(itertools.chain(
            [[self]],
            (link.flatten_links()
             for link in maybe_list(self.options.get('link')) or [])
        )))

    def __or__(self, other):
        """Chaining operator.

        Example:
            >>> add.s(2, 2) | add.s(4) | add.s(8)

        Returns:
            chain: Constructs a :class:`~celery.canvas.chain` of the given signatures.
        """
        if isinstance(other, _chain):
            # task | chain -> chain
            return _chain(seq_concat_seq(
                (self,), other.unchain_tasks()), app=self._app)
        elif isinstance(other, group):
            # unroll group with one member
            other = maybe_unroll_group(other)
            # task | group() -> chain
            return _chain(self, other, app=self.app)
        elif isinstance(other, Signature):
            # task | task -> chain
            return _chain(self, other, app=self._app)
        return NotImplemented

    def __ior__(self, other):
        # Python 3.9 introduces | as the merge operator for dicts.
        # We override the in-place version of that operator
        # so that canvases continue to work as they did before.
        return self.__or__(other)

    def election(self):
        type = self.type
        app = type.app
        tid = self.options.get('task_id') or uuid()

        with app.producer_or_acquire(None) as producer:
            props = type.backend.on_task_call(producer, tid)
            app.control.election(tid, 'task',
                                 self.clone(task_id=tid, **props),
                                 connection=producer.connection)
            return type.AsyncResult(tid)

    def reprcall(self, *args, **kwargs):
        """Return a string representation of the signature.

        Merges the given arguments with the signature's arguments
        only for the purpose of generating the string representation.
        The signature itself is not modified.

        Example:
            >>> add.s(2, 2).reprcall()
            'add(2, 2)'
        """
        args, kwargs, _ = self._merge(args, kwargs, {}, force=True)
        return reprcall(self['task'], args, kwargs)

    def __deepcopy__(self, memo):
        memo[id(self)] = self
        return dict(self)  # TODO: Potential bug of being a shallow copy

    def __invert__(self):
        return self.apply_async().get()

    def __reduce__(self):
        # for serialization, the task type is lazily loaded,
        # and not stored in the dict itself.
        return signature, (dict(self),)

    def __json__(self):
        return dict(self)

    def __repr__(self):
        return self.reprcall()

    def items(self):
        for k, v in super().items():
            yield k.decode() if isinstance(k, bytes) else k, v

    @property
    def name(self):
        # for duck typing compatibility with Task.name
        return self.task

    @cached_property
    def type(self):
        return self._type or self.app.tasks[self['task']]

    @cached_property
    def app(self):
        return self._app or current_app

    @cached_property
    def AsyncResult(self):
        try:
            return self.type.AsyncResult
        except KeyError:  # task not registered
            return self.app.AsyncResult

    @cached_property
    def _apply_async(self):
        try:
            return self.type.apply_async
        except KeyError:
            return _partial(self.app.send_task, self['task'])

    id = getitem_property('options.task_id', 'Task UUID')
    parent_id = getitem_property('options.parent_id', 'Task parent UUID.')
    root_id = getitem_property('options.root_id', 'Task root UUID.')
    task = getitem_property('task', 'Name of task.')
    args = getitem_property('args', 'Positional arguments to task.')
    kwargs = getitem_property('kwargs', 'Keyword arguments to task.')
    options = getitem_property('options', 'Task execution options.')
    subtask_type = getitem_property('subtask_type', 'Type of signature')
    immutable = getitem_property(
        'immutable', 'Flag set if no longer accepts new arguments')


def _prepare_chain_from_options(options, tasks, use_link):
    # When we publish groups we reuse the same options dictionary for all of
    # the tasks in the group. See:
    # https://github.com/celery/celery/blob/fb37cb0b8/celery/canvas.py#L1022.
    # Issue #5354 reported that the following type of canvases
    # causes a Celery worker to hang:
    # group(
    #   add.s(1, 1),
    #   add.s(1, 1)
    # ) | tsum.s() | add.s(1) | group(add.s(1), add.s(1))
    # The resolution of #5354 in PR #5681 was to only set the `chain` key
    # in the options dictionary if it is not present.
    # Otherwise we extend the existing list of tasks in the chain with the new
    # tasks: options['chain'].extend(chain_).
    # Before PR #5681 we overrode the `chain` key in each iteration
    # of the loop which applies all the tasks in the group:
    # options['chain'] = tasks if not use_link else None
    # This caused Celery to execute chains correctly in most cases since
    # in each iteration the `chain` key would reset itself to a new value
    # and the side effect of mutating the key did not propagate
    # to the next task in the group.
    # Since we now mutated the `chain` key, a *list* which is passed
    # by *reference*, the next task in the group will extend the list
    # of tasks in the chain instead of setting a new one from the chain_
    # variable above.
    # This causes Celery to execute a chain, even though there might not be
    # one to begin with. Alternatively, it causes Celery to execute more tasks
    # that were previously present in the previous task in the group.
    # The solution is to be careful and never mutate the options dictionary
    # to begin with.
    # Here is an example of a canvas which triggers this issue:
    # add.s(5, 6) | group((add.s(1) | add.s(2), add.s(3))).
    # The expected result is [14, 14]. However, when we extend the `chain`
    # key the `add.s(3)` task erroneously has `add.s(2)` in its chain since
    # it was previously applied to `add.s(1)`.
    # Without being careful not to mutate the options dictionary, the result
    # in this case is [16, 14].
    # To avoid deep-copying the entire options dictionary every single time we
    # run a chain we use a ChainMap and ensure that we never mutate
    # the original `chain` key, hence we use list_a + list_b to create a new
    # list.
    if use_link:
        return ChainMap({'chain': None}, options)
    elif 'chain' not in options:
        return ChainMap({'chain': tasks}, options)
    elif tasks is not None:
        # chain option may already be set, resulting in
        # "multiple values for keyword argument 'chain'" error.
        # Issue #3379.
        # If a chain already exists, we need to extend it with the next
        # tasks in the chain.
        # Issue #5354.
        # WARNING: Be careful not to mutate `options['chain']`.
        return ChainMap({'chain': options['chain'] + tasks},
                        options)


@Signature.register_type(name='chain')
class _chain(Signature):
    tasks = getitem_property('kwargs.tasks', 'Tasks in chain.')

    @classmethod
    def from_dict(cls, d, app=None):
        tasks = d['kwargs']['tasks']
        if tasks:
            if isinstance(tasks, tuple):  # aaaargh
                tasks = d['kwargs']['tasks'] = list(tasks)
            tasks = [maybe_signature(task, app=app) for task in tasks]
        return cls(tasks, app=app, **d['options'])

    def __init__(self, *tasks, **options):
        tasks = (regen(tasks[0]) if len(tasks) == 1 and is_list(tasks[0])
                 else tasks)
        super().__init__('celery.chain', (), {'tasks': tasks}, **options
                         )
        self._use_link = options.pop('use_link', None)
        self.subtask_type = 'chain'
        self._frozen = None

    def __call__(self, *args, **kwargs):
        if self.tasks:
            return self.apply_async(args, kwargs)

    def __or__(self, other):
        if isinstance(other, group):
            # unroll group with one member
            other = maybe_unroll_group(other)
            if not isinstance(other, group):
                return self.__or__(other)
            # chain | group() -> chain
            tasks = self.unchain_tasks()
            if not tasks:
                # If the chain is empty, return the group
                return other
            if isinstance(tasks[-1], chord):
                # CHAIN [last item is chord] | GROUP -> chain with chord body.
                tasks[-1].body = tasks[-1].body | other
                return type(self)(tasks, app=self.app)
            # use type(self) for _chain subclasses
            return type(self)(seq_concat_item(
                tasks, other), app=self._app)
        elif isinstance(other, _chain):
            # chain | chain -> chain
            return reduce(operator.or_, other.unchain_tasks(), self)
        elif isinstance(other, Signature):
            if self.tasks and isinstance(self.tasks[-1], group):
                # CHAIN [last item is group] | TASK -> chord
                sig = self.clone()
                sig.tasks[-1] = chord(
                    sig.tasks[-1], other, app=self._app)
                # In the scenario where the second-to-last item in a chain is a chord,
                # it leads to a situation where two consecutive chords are formed.
                # In such cases, a further upgrade can be considered.
                # This would involve chaining the body of the second-to-last chord with the last chord."
                if len(sig.tasks) > 1 and isinstance(sig.tasks[-2], chord):
                    sig.tasks[-2].body = sig.tasks[-2].body | sig.tasks[-1]
                    sig.tasks = sig.tasks[:-1]
                return sig
            elif self.tasks and isinstance(self.tasks[-1], chord) and not isinstance(other, chord):
                # CHAIN [last item is chord] | TASK -> chain with chord body.
                sig = self.clone()
                sig.tasks[-1].body = sig.tasks[-1].body | other
                return sig
            else:
                # chain | task/chord -> chain
                # use type(self) for _chain subclasses
                return type(self)(seq_concat_item(
