# engine/result.py
# Copyright (C) 2005-2026 the SQLAlchemy authors and contributors
# <see AUTHORS file>
#
# This module is part of SQLAlchemy and is released under
# the MIT License: https://www.opensource.org/licenses/mit-license.php

"""Define generic result set constructs."""

from __future__ import annotations

from enum import Enum
import functools
import itertools
import operator
import typing
from typing import Any
from typing import Callable
from typing import cast
from typing import Dict
from typing import Generic
from typing import Iterable
from typing import Iterator
from typing import List
from typing import Mapping
from typing import NoReturn
from typing import Optional
from typing import overload
from typing import Sequence
from typing import Set
from typing import Tuple
from typing import TYPE_CHECKING
from typing import TypeVar
from typing import Union

from .row import Row
from .row import RowMapping
from .. import exc
from .. import util
from ..sql.base import _generative
from ..sql.base import HasMemoized
from ..sql.base import InPlaceGenerative
from ..util import HasMemoized_ro_memoized_attribute
from ..util import NONE_SET
from ..util._has_cy import HAS_CYEXTENSION
from ..util.typing import Literal
from ..util.typing import Self

if typing.TYPE_CHECKING or not HAS_CYEXTENSION:
    from ._py_row import tuplegetter as tuplegetter
else:
    from sqlalchemy.cyextension.resultproxy import tuplegetter as tuplegetter

if typing.TYPE_CHECKING:
    from typing import Type

    from .. import inspection
    from ..sql import roles
    from ..sql._typing import _HasClauseElement
    from ..sql.elements import SQLCoreOperations
    from ..sql.type_api import _ResultProcessorType

_KeyType = Union[
    str,
    "SQLCoreOperations[Any]",
    "roles.TypedColumnsClauseRole[Any]",
    "roles.ColumnsClauseRole",
    "Type[Any]",
    "inspection.Inspectable[_HasClauseElement[Any]]",
]
_KeyIndexType = Union[_KeyType, int]

# is overridden in cursor using _CursorKeyMapRecType
_KeyMapRecType = Any

_KeyMapType = Mapping[_KeyType, _KeyMapRecType]


_RowData = Union[Row[Any], RowMapping, Any]
"""A generic form of "row" that accommodates for the different kinds of
"rows" that different result objects return, including row, row mapping, and
scalar values"""

_RawRowType = Tuple[Any, ...]
"""represents the kind of row we get from a DBAPI cursor"""

_R = TypeVar("_R", bound=_RowData)
_T = TypeVar("_T", bound=Any)
_TP = TypeVar("_TP", bound=Tuple[Any, ...])

_InterimRowType = Union[_R, _RawRowType]
"""a catchall "anything" kind of return type that can be applied
across all the result types

"""

_InterimSupportsScalarsRowType = Union[Row[Any], Any]

_ProcessorsType = Sequence[Optional["_ResultProcessorType[Any]"]]
_TupleGetterType = Callable[[Sequence[Any]], Sequence[Any]]
_UniqueFilterType = Callable[[Any], Any]
_UniqueFilterStateType = Tuple[Set[Any], Optional[_UniqueFilterType]]


class ResultMetaData:
    """Base for metadata about result rows."""

    __slots__ = ()

    _tuplefilter: Optional[_TupleGetterType] = None
    _translated_indexes: Optional[Sequence[int]] = None
    _unique_filters: Optional[Sequence[Callable[[Any], Any]]] = None
    _keymap: _KeyMapType
    _keys: Sequence[str]
    _processors: Optional[_ProcessorsType]
    _key_to_index: Mapping[_KeyType, int]

    @property
    def keys(self) -> RMKeyView:
        return RMKeyView(self)

    def _has_key(self, key: object) -> bool:
        raise NotImplementedError()

    def _for_freeze(self) -> ResultMetaData:
        raise NotImplementedError()

    @overload
    def _key_fallback(
        self, key: Any, err: Optional[Exception], raiseerr: Literal[True] = ...
    ) -> NoReturn: ...

    @overload
    def _key_fallback(
        self,
        key: Any,
        err: Optional[Exception],
        raiseerr: Literal[False] = ...,
    ) -> None: ...

    @overload
    def _key_fallback(
        self, key: Any, err: Optional[Exception], raiseerr: bool = ...
    ) -> Optional[NoReturn]: ...

    def _key_fallback(
        self, key: Any, err: Optional[Exception], raiseerr: bool = True
    ) -> Optional[NoReturn]:
        assert raiseerr
        raise KeyError(key) from err

    def _raise_for_ambiguous_column_name(
        self, rec: _KeyMapRecType
    ) -> NoReturn:
        raise NotImplementedError(
            "ambiguous column name logic is implemented for "
            "CursorResultMetaData"
        )

    def _index_for_key(
        self, key: _KeyIndexType, raiseerr: bool
    ) -> Optional[int]:
        raise NotImplementedError()

    def _indexes_for_keys(
        self, keys: Sequence[_KeyIndexType]
    ) -> Sequence[int]:
        raise NotImplementedError()

    def _metadata_for_keys(
        self, keys: Sequence[_KeyIndexType]
    ) -> Iterator[_KeyMapRecType]:
        raise NotImplementedError()

    def _reduce(self, keys: Sequence[_KeyIndexType]) -> ResultMetaData:
        raise NotImplementedError()

    def _getter(
        self, key: Any, raiseerr: bool = True
    ) -> Optional[Callable[[Row[Any]], Any]]:
        index = self._index_for_key(key, raiseerr)

        if index is not None:
            return operator.itemgetter(index)
        else:
            return None

    def _row_as_tuple_getter(
        self, keys: Sequence[_KeyIndexType]
    ) -> _TupleGetterType:
        indexes = self._indexes_for_keys(keys)
        return tuplegetter(*indexes)

    def _make_key_to_index(
        self, keymap: Mapping[_KeyType, Sequence[Any]], index: int
    ) -> Mapping[_KeyType, int]:
        return {
            key: rec[index]
            for key, rec in keymap.items()
            if rec[index] is not None
        }

    def _key_not_found(self, key: Any, attr_error: bool) -> NoReturn:
        if key in self._keymap:
            # the index must be none in this case
            self._raise_for_ambiguous_column_name(self._keymap[key])
        else:
            # unknown key
            if attr_error:
                try:
                    self._key_fallback(key, None)
                except KeyError as ke:
                    raise AttributeError(ke.args[0]) from ke
            else:
                self._key_fallback(key, None)

    @property
    def _effective_processors(self) -> Optional[_ProcessorsType]:
        if not self._processors or NONE_SET.issuperset(self._processors):
            return None
        else:
            return self._processors


class RMKeyView(typing.KeysView[Any]):
    __slots__ = ("_parent", "_keys")

    _parent: ResultMetaData
    _keys: Sequence[str]

    def __init__(self, parent: ResultMetaData):
        self._parent = parent
        self._keys = [k for k in parent._keys if k is not None]

    def __len__(self) -> int:
        return len(self._keys)

    def __repr__(self) -> str:
        return "{0.__class__.__name__}({0._keys!r})".format(self)

    def __iter__(self) -> Iterator[str]:
        return iter(self._keys)

    def __contains__(self, item: Any) -> bool:
        if isinstance(item, int):
            return False

        # note this also includes special key fallback behaviors
        # which also don't seem to be tested in test_resultset right now
        return self._parent._has_key(item)

    def __eq__(self, other: Any) -> bool:
        return list(other) == list(self)

    def __ne__(self, other: Any) -> bool:
        return list(other) != list(self)


class SimpleResultMetaData(ResultMetaData):
    """result metadata for in-memory collections."""

    __slots__ = (
        "_keys",
        "_keymap",
        "_processors",
        "_tuplefilter",
        "_translated_indexes",
        "_unique_filters",
        "_key_to_index",
    )

    _keys: Sequence[str]

    def __init__(
        self,
        keys: Sequence[str],
        extra: Optional[Sequence[Any]] = None,
        _processors: Optional[_ProcessorsType] = None,
        _tuplefilter: Optional[_TupleGetterType] = None,
        _translated_indexes: Optional[Sequence[int]] = None,
        _unique_filters: Optional[Sequence[Callable[[Any], Any]]] = None,
    ):
        self._keys = list(keys)
        self._tuplefilter = _tuplefilter
        self._translated_indexes = _translated_indexes
        self._unique_filters = _unique_filters
        if extra:
            recs_names = [
                (
                    (name,) + (extras if extras else ()),
                    (index, name, extras),
                )
                for index, (name, extras) in enumerate(zip(self._keys, extra))
            ]
        else:
            recs_names = [
                ((name,), (index, name, ()))
                for index, name in enumerate(self._keys)
            ]

        self._keymap = {key: rec for keys, rec in recs_names for key in keys}

        self._processors = _processors

        self._key_to_index = self._make_key_to_index(self._keymap, 0)

    def _has_key(self, key: object) -> bool:
        return key in self._keymap

    def _for_freeze(self) -> ResultMetaData:
        unique_filters = self._unique_filters
        if unique_filters and self._tuplefilter:
            unique_filters = self._tuplefilter(unique_filters)

        # TODO: are we freezing the result with or without uniqueness
        # applied?
        return SimpleResultMetaData(
            self._keys,
            extra=[self._keymap[key][2] for key in self._keys],
            _unique_filters=unique_filters,
        )

    def __getstate__(self) -> Dict[str, Any]:
        return {
            "_keys": self._keys,
            "_translated_indexes": self._translated_indexes,
        }

    def __setstate__(self, state: Dict[str, Any]) -> None:
        if state["_translated_indexes"]:
            _translated_indexes = state["_translated_indexes"]
            _tuplefilter = tuplegetter(*_translated_indexes)
        else:
            _translated_indexes = _tuplefilter = None
        self.__init__(  # type: ignore
            state["_keys"],
            _translated_indexes=_translated_indexes,
            _tuplefilter=_tuplefilter,
        )

    def _index_for_key(self, key: Any, raiseerr: bool = True) -> int:
        if int in key.__class__.__mro__:
            key = self._keys[key]
        try:
            rec = self._keymap[key]
        except KeyError as ke:
            rec = self._key_fallback(key, ke, raiseerr)

        return rec[0]  # type: ignore[no-any-return]

    def _indexes_for_keys(self, keys: Sequence[Any]) -> Sequence[int]:
        return [self._keymap[key][0] for key in keys]

    def _metadata_for_keys(
        self, keys: Sequence[Any]
    ) -> Iterator[_KeyMapRecType]:
        for key in keys:
            if int in key.__class__.__mro__:
                key = self._keys[key]

            try:
                rec = self._keymap[key]
            except KeyError as ke:
                rec = self._key_fallback(key, ke, True)

            yield rec

    def _reduce(self, keys: Sequence[Any]) -> ResultMetaData:
        try:
            metadata_for_keys = [
                self._keymap[
                    self._keys[key] if int in key.__class__.__mro__ else key
                ]
                for key in keys
            ]
        except KeyError as ke:
            self._key_fallback(ke.args[0], ke, True)

        indexes: Sequence[int]
        new_keys: Sequence[str]
        extra: Sequence[Any]
        indexes, new_keys, extra = zip(*metadata_for_keys)

        if self._translated_indexes:
            indexes = [self._translated_indexes[idx] for idx in indexes]

        tup = tuplegetter(*indexes)

        new_metadata = SimpleResultMetaData(
            new_keys,
            extra=extra,
            _tuplefilter=tup,
            _translated_indexes=indexes,
            _processors=self._processors,
            _unique_filters=self._unique_filters,
        )

        return new_metadata


def result_tuple(
    fields: Sequence[str], extra: Optional[Any] = None
) -> Callable[[Iterable[Any]], Row[Any]]:
    parent = SimpleResultMetaData(fields, extra)
    return functools.partial(
        Row, parent, parent._effective_processors, parent._key_to_index
    )


# a symbol that indicates to internal Result methods that
# "no row is returned".  We can't use None for those cases where a scalar
# filter is applied to rows.
class _NoRow(Enum):
    _NO_ROW = 0


_NO_ROW = _NoRow._NO_ROW


class ResultInternal(InPlaceGenerative, Generic[_R]):
    __slots__ = ()

    _real_result: Optional[Result[Any]] = None
    _generate_rows: bool = True
    _row_logging_fn: Optional[Callable[[Any], Any]]

    _unique_filter_state: Optional[_UniqueFilterStateType] = None
    _post_creational_filter: Optional[Callable[[Any], Any]] = None
    _is_cursor = False

    _metadata: ResultMetaData

    _source_supports_scalars: bool

    def _fetchiter_impl(self) -> Iterator[_InterimRowType[Row[Any]]]:
        raise NotImplementedError()

    def _fetchone_impl(
        self, hard_close: bool = False
    ) -> Optional[_InterimRowType[Row[Any]]]:
        raise NotImplementedError()

    def _fetchmany_impl(
        self, size: Optional[int] = None
    ) -> List[_InterimRowType[Row[Any]]]:
        raise NotImplementedError()

    def _fetchall_impl(self) -> List[_InterimRowType[Row[Any]]]:
        raise NotImplementedError()

    def _soft_close(self, hard: bool = False) -> None:
        raise NotImplementedError()

    @HasMemoized_ro_memoized_attribute
    def _row_getter(self) -> Optional[Callable[..., _R]]:
        real_result: Result[Any] = (
            self._real_result
            if self._real_result
            else cast("Result[Any]", self)
        )

        if real_result._source_supports_scalars:
            if not self._generate_rows:
                return None
            else:
                _proc = Row

                def process_row(
                    metadata: ResultMetaData,
                    processors: Optional[_ProcessorsType],
                    key_to_index: Mapping[_KeyType, int],
                    scalar_obj: Any,
                ) -> Row[Any]:
                    return _proc(
                        metadata, processors, key_to_index, (scalar_obj,)
                    )

        else:
            process_row = Row  # type: ignore

        metadata = self._metadata

        key_to_index = metadata._key_to_index
        processors = metadata._effective_processors
        tf = metadata._tuplefilter

        if tf and not real_result._source_supports_scalars:
            if processors:
                processors = tf(processors)

            _make_row_orig: Callable[..., _R] = functools.partial(  # type: ignore  # noqa E501
                process_row, metadata, processors, key_to_index
            )

            fixed_tf = tf

            def make_row(row: _InterimRowType[Row[Any]]) -> _R:
                return _make_row_orig(fixed_tf(row))

        else:
            make_row = functools.partial(  # type: ignore
                process_row, metadata, processors, key_to_index
            )

        if real_result._row_logging_fn:
            _log_row = real_result._row_logging_fn
            _make_row = make_row

            def make_row(row: _InterimRowType[Row[Any]]) -> _R:
                return _log_row(_make_row(row))  # type: ignore

        return make_row

    @HasMemoized_ro_memoized_attribute
    def _iterator_getter(self) -> Callable[..., Iterator[_R]]:
        make_row = self._row_getter

        post_creational_filter = self._post_creational_filter

        if self._unique_filter_state:
            uniques, strategy = self._unique_strategy

            def iterrows(self: Result[Any]) -> Iterator[_R]:
                for raw_row in self._fetchiter_impl():
                    obj: _InterimRowType[Any] = (
                        make_row(raw_row) if make_row else raw_row
                    )
                    hashed = strategy(obj) if strategy else obj
                    if hashed in uniques:
                        continue
                    uniques.add(hashed)
                    if post_creational_filter:
                        obj = post_creational_filter(obj)
                    yield obj  # type: ignore

        else:

            def iterrows(self: Result[Any]) -> Iterator[_R]:
                for raw_row in self._fetchiter_impl():
                    row: _InterimRowType[Any] = (
                        make_row(raw_row) if make_row else raw_row
                    )
                    if post_creational_filter:
                        row = post_creational_filter(row)
                    yield row  # type: ignore

        return iterrows

    def _raw_all_rows(self) -> List[_R]:
        make_row = self._row_getter
        assert make_row is not None
        rows = self._fetchall_impl()
        return [make_row(row) for row in rows]

    def _allrows(self) -> List[_R]:
        post_creational_filter = self._post_creational_filter

        make_row = self._row_getter

        rows = self._fetchall_impl()
        made_rows: List[_InterimRowType[_R]]
        if make_row:
            made_rows = [make_row(row) for row in rows]
        else:
            made_rows = rows  # type: ignore

        interim_rows: List[_R]

        if self._unique_filter_state:
            uniques, strategy = self._unique_strategy

            interim_rows = [
                made_row  # type: ignore
                for made_row, sig_row in [
                    (
                        made_row,
                        strategy(made_row) if strategy else made_row,
                    )
                    for made_row in made_rows
                ]
                if sig_row not in uniques and not uniques.add(sig_row)  # type: ignore # noqa: E501
            ]
        else:
            interim_rows = made_rows  # type: ignore

        if post_creational_filter:
            interim_rows = [
                post_creational_filter(row) for row in interim_rows
            ]
        return interim_rows

    @HasMemoized_ro_memoized_attribute
    def _onerow_getter(
        self,
    ) -> Callable[..., Union[Literal[_NoRow._NO_ROW], _R]]:
        make_row = self._row_getter

        post_creational_filter = self._post_creational_filter

        if self._unique_filter_state:
            uniques, strategy = self._unique_strategy

            def onerow(self: Result[Any]) -> Union[_NoRow, _R]:
                _onerow = self._fetchone_impl
                while True:
                    row = _onerow()
                    if row is None:
                        return _NO_ROW
                    else:
                        obj: _InterimRowType[Any] = (
                            make_row(row) if make_row else row
                        )
                        hashed = strategy(obj) if strategy else obj
                        if hashed in uniques:
                            continue
                        else:
                            uniques.add(hashed)
                        if post_creational_filter:
                            obj = post_creational_filter(obj)
                        return obj  # type: ignore

        else:

            def onerow(self: Result[Any]) -> Union[_NoRow, _R]:
                row = self._fetchone_impl()
                if row is None:
                    return _NO_ROW
                else:
                    interim_row: _InterimRowType[Any] = (
                        make_row(row) if make_row else row
                    )
                    if post_creational_filter:
                        interim_row = post_creational_filter(interim_row)
                    return interim_row  # type: ignore

        return onerow

    @HasMemoized_ro_memoized_attribute
    def _manyrow_getter(self) -> Callable[..., List[_R]]:
        make_row = self._row_getter

        post_creational_filter = self._post_creational_filter

        if self._unique_filter_state:
            uniques, strategy = self._unique_strategy

            def filterrows(
                make_row: Optional[Callable[..., _R]],
                rows: List[Any],
                strategy: Optional[Callable[[List[Any]], Any]],
                uniques: Set[Any],
            ) -> List[_R]:
                if make_row:
                    rows = [make_row(row) for row in rows]

                if strategy:
                    made_rows = (
                        (made_row, strategy(made_row)) for made_row in rows
                    )
                else:
                    made_rows = ((made_row, made_row) for made_row in rows)
                return [
                    made_row
                    for made_row, sig_row in made_rows
                    if sig_row not in uniques and not uniques.add(sig_row)  # type: ignore  # noqa: E501
                ]

            def manyrows(
                self: ResultInternal[_R], num: Optional[int]
            ) -> List[_R]:
                collect: List[_R] = []

                _manyrows = self._fetchmany_impl

                if num is None:
                    # if None is passed, we don't know the default
                    # manyrows number, DBAPI has this as cursor.arraysize
                    # different DBAPIs / fetch strategies may be different.
                    # do a fetch to find what the number is.  if there are
                    # only fewer rows left, then it doesn't matter.
                    real_result = (
                        self._real_result
                        if self._real_result
                        else cast("Result[Any]", self)
                    )
                    if real_result._yield_per:
                        num_required = num = real_result._yield_per
                    else:
                        rows = _manyrows(num)
                        num = len(rows)
                        assert make_row is not None
                        collect.extend(
                            filterrows(make_row, rows, strategy, uniques)
                        )
                        num_required = num - len(collect)
                else:
                    num_required = num

                assert num is not None

                while num_required:
                    rows = _manyrows(num_required)
                    if not rows:
                        break

                    collect.extend(
                        filterrows(make_row, rows, strategy, uniques)
                    )
                    num_required = num - len(collect)

                if post_creational_filter:
                    collect = [post_creational_filter(row) for row in collect]
                return collect

        else:

            def manyrows(
                self: ResultInternal[_R], num: Optional[int]
            ) -> List[_R]:
                if num is None:
                    real_result = (
                        self._real_result
                        if self._real_result
                        else cast("Result[Any]", self)
                    )
                    num = real_result._yield_per

                rows: List[_InterimRowType[Any]] = self._fetchmany_impl(num)
                if make_row:
                    rows = [make_row(row) for row in rows]
                if post_creational_filter:
                    rows = [post_creational_filter(row) for row in rows]
                return rows  # type: ignore

        return manyrows

    @overload
    def _only_one_row(
        self: ResultInternal[Row[Any]],
        raise_for_second_row: bool,
        raise_for_none: bool,
        scalar: Literal[True],
    ) -> Any: ...

    @overload
    def _only_one_row(
        self,
        raise_for_second_row: bool,
        raise_for_none: Literal[True],
        scalar: bool,
    ) -> _R: ...

    @overload
    def _only_one_row(
        self,
        raise_for_second_row: bool,
        raise_for_none: bool,
        scalar: bool,
    ) -> Optional[_R]: ...

    def _only_one_row(
        self,
        raise_for_second_row: bool,
        raise_for_none: bool,
        scalar: bool,
    ) -> Optional[_R]:
        onerow = self._fetchone_impl

        row: Optional[_InterimRowType[Any]] = onerow(hard_close=True)
        if row is None:
            if raise_for_none:
                raise exc.NoResultFound(
                    "No row was found when one was required"
                )
            else:
                return None

        if scalar and self._source_supports_scalars:
            self._generate_rows = False
            make_row = None
        else:
            make_row = self._row_getter

        try:
            row = make_row(row) if make_row else row
        except:
            self._soft_close(hard=True)
            raise

        if raise_for_second_row:
            if self._unique_filter_state:
                # for no second row but uniqueness, need to essentially
                # consume the entire result :(
                uniques, strategy = self._unique_strategy

                existing_row_hash = strategy(row) if strategy else row

                while True:
                    next_row: Any = onerow(hard_close=True)
                    if next_row is None:
                        next_row = _NO_ROW
                        break

                    try:
                        next_row = make_row(next_row) if make_row else next_row

                        if strategy:
                            assert next_row is not _NO_ROW
                            if existing_row_hash == strategy(next_row):
                                continue
                        elif row == next_row:
                            continue
                        # here, we have a row and it's different
                        break
                    except:
                        self._soft_close(hard=True)
                        raise
            else:
                next_row = onerow(hard_close=True)
                if next_row is None:
                    next_row = _NO_ROW

            if next_row is not _NO_ROW:
                self._soft_close(hard=True)
                raise exc.MultipleResultsFound(
                    "Multiple rows were found when exactly one was required"
                    if raise_for_none
                    else "Multiple rows were found when one or none "
                    "was required"
                )
        else:
            # if we checked for second row then that would have
            # closed us :)
            self._soft_close(hard=True)

        if not scalar:
            post_creational_filter = self._post_creational_filter
            if post_creational_filter:
                row = post_creational_filter(row)

        if scalar and make_row:
            return row[0]  # type: ignore
        else:
            return row  # type: ignore

    def _iter_impl(self) -> Iterator[_R]:
        return self._iterator_getter(self)

    def _next_impl(self) -> _R:
        row = self._onerow_getter(self)
        if row is _NO_ROW:
            raise StopIteration()
        else:
            return row

    @_generative
    def _column_slices(self, indexes: Sequence[_KeyIndexType]) -> Self:
        real_result = (
            self._real_result
            if self._real_result
            else cast("Result[Any]", self)
        )

        if not real_result._source_supports_scalars or len(indexes) != 1:
            self._metadata = self._metadata._reduce(indexes)

        assert self._generate_rows

        return self

    @HasMemoized.memoized_attribute
    def _unique_strategy(self) -> _UniqueFilterStateType:
        assert self._unique_filter_state is not None
        uniques, strategy = self._unique_filter_state

        real_result = (
            self._real_result
            if self._real_result is not None
            else cast("Result[Any]", self)
        )

        if not strategy and self._metadata._unique_filters:
            if (
                real_result._source_supports_scalars
                and not self._generate_rows
            ):
                strategy = self._metadata._unique_filters[0]
            else:
                filters = self._metadata._unique_filters
                if self._metadata._tuplefilter:
                    filters = self._metadata._tuplefilter(filters)

                strategy = operator.methodcaller("_filter_on_values", filters)
        return uniques, strategy


class _WithKeys:
    __slots__ = ()

    _metadata: ResultMetaData

    # used mainly to share documentation on the keys method.
    def keys(self) -> RMKeyView:
        """Return an iterable view which yields the string keys that would
        be represented by each :class:`_engine.Row`.

        The keys can represent the labels of the columns returned by a core
        statement or the names of the orm classes returned by an orm
        execution.

        The view also can be tested for key containment using the Python
        ``in`` operator, which will test both for the string keys represented
        in the view, as well as for alternate keys such as column objects.

        .. versionchanged:: 1.4 a key view object is returned rather than a
           plain list.


        """
        return self._metadata.keys


class Result(_WithKeys, ResultInternal[Row[_TP]]):
    """Represent a set of database results.

    .. versionadded:: 1.4  The :class:`_engine.Result` object provides a
       completely updated usage model and calling facade for SQLAlchemy
       Core and SQLAlchemy ORM.   In Core, it forms the basis of the
       :class:`_engine.CursorResult` object which replaces the previous
       :class:`_engine.ResultProxy` interface.   When using the ORM, a
       higher level object called :class:`_engine.ChunkedIteratorResult`
       is normally used.

    .. note:: In SQLAlchemy 1.4 and above, this object is
       used for ORM results returned by :meth:`_orm.Session.execute`, which can
       yield instances of ORM mapped objects either individually or within
       tuple-like rows. Note that the :class:`_engine.Result` object does not
       deduplicate instances or rows automatically as is the case with the
       legacy :class:`_orm.Query` object. For in-Python de-duplication of
       instances or rows, use the :meth:`_engine.Result.unique` modifier
       method.

    .. seealso::

        :ref:`tutorial_fetching_rows` - in the :doc:`/tutorial/index`

    """

    __slots__ = ("_metadata", "__dict__")

    _row_logging_fn: Optional[Callable[[Row[Any]], Row[Any]]] = None

    _source_supports_scalars: bool = False

    _yield_per: Optional[int] = None

    _attributes: util.immutabledict[Any, Any] = util.immutabledict()

    def __init__(self, cursor_metadata: ResultMetaData):
        self._metadata = cursor_metadata

    def __enter__(self) -> Self:
        return self

    def __exit__(self, type_: Any, value: Any, traceback: Any) -> None:
        self.close()

    def close(self) -> None:
        """Hard close this :class:`_engine.Result`.

        The behavior of this method is implementation specific, and is
        not implemented by default.    The method should generally end
        the resources in use by the result object and also cause any
        subsequent iteration or row fetching to raise
        :class:`.ResourceClosedError`.

        .. versionadded:: 1.4.27 - ``.close()`` was previously not generally
           available for all :class:`_engine.Result` classes, instead only
           being available on the :class:`_engine.CursorResult` returned for
           Core statement executions. As most other result objects, namely the
           ones used by the ORM, are proxying a :class:`_engine.CursorResult`
           in any case, this allows the underlying cursor result to be closed
           from the outside facade for the case when the ORM query is using
           the ``yield_per`` execution option where it does not immediately
           exhaust and autoclose the database cursor.

        """
        self._soft_close(hard=True)

    @property
    def _soft_closed(self) -> bool:
        raise NotImplementedError()

    @property
    def closed(self) -> bool:
        """Return ``True`` if this :class:`_engine.Result` was **hard closed**
        by explicitly calling the :meth:`close` method.

        The attribute is **not** True if the :class:`_engine.Result` was only
        **soft closed**; a "soft close" is the style of close that takes place
