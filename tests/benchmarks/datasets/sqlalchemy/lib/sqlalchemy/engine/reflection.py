# engine/reflection.py
# Copyright (C) 2005-2026 the SQLAlchemy authors and contributors
# <see AUTHORS file>
#
# This module is part of SQLAlchemy and is released under
# the MIT License: https://www.opensource.org/licenses/mit-license.php

"""Provides an abstraction for obtaining database schema information.

Usage Notes:

Here are some general conventions when accessing the low level inspector
methods such as get_table_names, get_columns, etc.

1. Inspector methods return lists of dicts in most cases for the following
   reasons:

   * They're both standard types that can be serialized.
   * Using a dict instead of a tuple allows easy expansion of attributes.
   * Using a list for the outer structure maintains order and is easy to work
     with (e.g. list comprehension [d['name'] for d in cols]).

2. Records that contain a name, such as the column name in a column record
   use the key 'name'. So for most return values, each record will have a
   'name' attribute..
"""
from __future__ import annotations

import contextlib
from dataclasses import dataclass
from enum import auto
from enum import Flag
from enum import unique
from typing import Any
from typing import Callable
from typing import Collection
from typing import Dict
from typing import Generator
from typing import Iterable
from typing import List
from typing import Optional
from typing import Sequence
from typing import Set
from typing import Tuple
from typing import TYPE_CHECKING
from typing import TypeVar
from typing import Union

from .base import Connection
from .base import Engine
from .. import exc
from .. import inspection
from .. import sql
from .. import util
from ..sql import operators
from ..sql import schema as sa_schema
from ..sql.cache_key import _ad_hoc_cache_key_from_args
from ..sql.elements import quoted_name
from ..sql.elements import TextClause
from ..sql.type_api import TypeEngine
from ..sql.visitors import InternalTraversal
from ..util import topological
from ..util.typing import final

if TYPE_CHECKING:
    from .interfaces import Dialect
    from .interfaces import ReflectedCheckConstraint
    from .interfaces import ReflectedColumn
    from .interfaces import ReflectedForeignKeyConstraint
    from .interfaces import ReflectedIndex
    from .interfaces import ReflectedPrimaryKeyConstraint
    from .interfaces import ReflectedTableComment
    from .interfaces import ReflectedUniqueConstraint
    from .interfaces import TableKey

_R = TypeVar("_R")


@util.decorator
def cache(
    fn: Callable[..., _R],
    self: Dialect,
    con: Connection,
    *args: Any,
    **kw: Any,
) -> _R:
    info_cache = kw.get("info_cache", None)
    if info_cache is None:
        return fn(self, con, *args, **kw)
    exclude = {"info_cache", "unreflectable"}
    key = (
        fn.__name__,
        tuple(
            (str(a), a.quote) if isinstance(a, quoted_name) else a
            for a in args
            if isinstance(a, str)
        ),
        tuple(
            (k, (str(v), v.quote) if isinstance(v, quoted_name) else v)
            for k, v in kw.items()
            if k not in exclude
        ),
    )
    ret: _R = info_cache.get(key)
    if ret is None:
        ret = fn(self, con, *args, **kw)
        info_cache[key] = ret
    return ret


def flexi_cache(
    *traverse_args: Tuple[str, InternalTraversal]
) -> Callable[[Callable[..., _R]], Callable[..., _R]]:
    @util.decorator
    def go(
        fn: Callable[..., _R],
        self: Dialect,
        con: Connection,
        *args: Any,
        **kw: Any,
    ) -> _R:
        info_cache = kw.get("info_cache", None)
        if info_cache is None:
            return fn(self, con, *args, **kw)
        key = _ad_hoc_cache_key_from_args((fn.__name__,), traverse_args, args)
        ret: _R = info_cache.get(key)
        if ret is None:
            ret = fn(self, con, *args, **kw)
            info_cache[key] = ret
        return ret

    return go


@unique
class ObjectKind(Flag):
    """Enumerator that indicates which kind of object to return when calling
    the ``get_multi`` methods.

    This is a Flag enum, so custom combinations can be passed. For example,
    to reflect tables and plain views ``ObjectKind.TABLE | ObjectKind.VIEW``
    may be used.

    .. note::
      Not all dialect may support all kind of object. If a dialect does
      not support a particular object an empty dict is returned.
      In case a dialect supports an object, but the requested method
      is not applicable for the specified kind the default value
      will be returned for each reflected object. For example reflecting
      check constraints of view return a dict with all the views with
      empty lists as values.
    """

    TABLE = auto()
    "Reflect table objects"
    VIEW = auto()
    "Reflect plain view objects"
    MATERIALIZED_VIEW = auto()
    "Reflect materialized view object"

    ANY_VIEW = VIEW | MATERIALIZED_VIEW
    "Reflect any kind of view objects"
    ANY = TABLE | VIEW | MATERIALIZED_VIEW
    "Reflect all type of objects"


@unique
class ObjectScope(Flag):
    """Enumerator that indicates which scope to use when calling
    the ``get_multi`` methods.
    """

    DEFAULT = auto()
    "Include default scope"
    TEMPORARY = auto()
    "Include only temp scope"
    ANY = DEFAULT | TEMPORARY
    "Include both default and temp scope"


@inspection._self_inspects
class Inspector(inspection.Inspectable["Inspector"]):
    """Performs database schema inspection.

    The Inspector acts as a proxy to the reflection methods of the
    :class:`~sqlalchemy.engine.interfaces.Dialect`, providing a
    consistent interface as well as caching support for previously
    fetched metadata.

    A :class:`_reflection.Inspector` object is usually created via the
    :func:`_sa.inspect` function, which may be passed an
    :class:`_engine.Engine`
    or a :class:`_engine.Connection`::

        from sqlalchemy import inspect, create_engine

        engine = create_engine("...")
        insp = inspect(engine)

    Where above, the :class:`~sqlalchemy.engine.interfaces.Dialect` associated
    with the engine may opt to return an :class:`_reflection.Inspector`
    subclass that
    provides additional methods specific to the dialect's target database.

    """

    bind: Union[Engine, Connection]
    engine: Engine
    _op_context_requires_connect: bool
    dialect: Dialect
    info_cache: Dict[Any, Any]

    @util.deprecated(
        "1.4",
        "The __init__() method on :class:`_reflection.Inspector` "
        "is deprecated and "
        "will be removed in a future release.  Please use the "
        ":func:`.sqlalchemy.inspect` "
        "function on an :class:`_engine.Engine` or "
        ":class:`_engine.Connection` "
        "in order to "
        "acquire an :class:`_reflection.Inspector`.",
    )
    def __init__(self, bind: Union[Engine, Connection]):
        """Initialize a new :class:`_reflection.Inspector`.

        :param bind: a :class:`~sqlalchemy.engine.Connection`,
          which is typically an instance of
          :class:`~sqlalchemy.engine.Engine` or
          :class:`~sqlalchemy.engine.Connection`.

        For a dialect-specific instance of :class:`_reflection.Inspector`, see
        :meth:`_reflection.Inspector.from_engine`

        """
        self._init_legacy(bind)

    @classmethod
    def _construct(
        cls, init: Callable[..., Any], bind: Union[Engine, Connection]
    ) -> Inspector:
        if hasattr(bind.dialect, "inspector"):
            cls = bind.dialect.inspector

        self = cls.__new__(cls)
        init(self, bind)
        return self

    def _init_legacy(self, bind: Union[Engine, Connection]) -> None:
        if hasattr(bind, "exec_driver_sql"):
            self._init_connection(bind)  # type: ignore[arg-type]
        else:
            self._init_engine(bind)

    def _init_engine(self, engine: Engine) -> None:
        self.bind = self.engine = engine
        engine.connect().close()
        self._op_context_requires_connect = True
        self.dialect = self.engine.dialect
        self.info_cache = {}

    def _init_connection(self, connection: Connection) -> None:
        self.bind = connection
        self.engine = connection.engine
        self._op_context_requires_connect = False
        self.dialect = self.engine.dialect
        self.info_cache = {}

    def clear_cache(self) -> None:
        """reset the cache for this :class:`.Inspector`.

        Inspection methods that have data cached will emit SQL queries
        when next called to get new data.

        .. versionadded:: 2.0

        """
        self.info_cache.clear()

    @classmethod
    @util.deprecated(
        "1.4",
        "The from_engine() method on :class:`_reflection.Inspector` "
        "is deprecated and "
        "will be removed in a future release.  Please use the "
        ":func:`.sqlalchemy.inspect` "
        "function on an :class:`_engine.Engine` or "
        ":class:`_engine.Connection` "
        "in order to "
        "acquire an :class:`_reflection.Inspector`.",
    )
    def from_engine(cls, bind: Engine) -> Inspector:
        """Construct a new dialect-specific Inspector object from the given
        engine or connection.

        :param bind: a :class:`~sqlalchemy.engine.Connection`
         or :class:`~sqlalchemy.engine.Engine`.

        This method differs from direct a direct constructor call of
        :class:`_reflection.Inspector` in that the
        :class:`~sqlalchemy.engine.interfaces.Dialect` is given a chance to
        provide a dialect-specific :class:`_reflection.Inspector` instance,
        which may
        provide additional methods.

        See the example at :class:`_reflection.Inspector`.

        """
        return cls._construct(cls._init_legacy, bind)

    @inspection._inspects(Engine)
    def _engine_insp(bind: Engine) -> Inspector:  # type: ignore[misc]
        return Inspector._construct(Inspector._init_engine, bind)

    @inspection._inspects(Connection)
    def _connection_insp(bind: Connection) -> Inspector:  # type: ignore[misc]
        return Inspector._construct(Inspector._init_connection, bind)

    @contextlib.contextmanager
    def _operation_context(self) -> Generator[Connection, None, None]:
        """Return a context that optimizes for multiple operations on a single
        transaction.

        This essentially allows connect()/close() to be called if we detected
        that we're against an :class:`_engine.Engine` and not a
        :class:`_engine.Connection`.

        """
        conn: Connection
        if self._op_context_requires_connect:
            conn = self.bind.connect()  # type: ignore[union-attr]
        else:
            conn = self.bind  # type: ignore[assignment]
        try:
            yield conn
        finally:
            if self._op_context_requires_connect:
                conn.close()

    @contextlib.contextmanager
    def _inspection_context(self) -> Generator[Inspector, None, None]:
        """Return an :class:`_reflection.Inspector`
        from this one that will run all
        operations on a single connection.

        """

        with self._operation_context() as conn:
            sub_insp = self._construct(self.__class__._init_connection, conn)
            sub_insp.info_cache = self.info_cache
            yield sub_insp

    @property
    def default_schema_name(self) -> Optional[str]:
        """Return the default schema name presented by the dialect
        for the current engine's database user.

        E.g. this is typically ``public`` for PostgreSQL and ``dbo``
        for SQL Server.

        """
        return self.dialect.default_schema_name

    def get_schema_names(self, **kw: Any) -> List[str]:
        r"""Return all schema names.

        :param \**kw: Additional keyword argument to pass to the dialect
         specific implementation. See the documentation of the dialect
         in use for more information.
        """

        with self._operation_context() as conn:
            return self.dialect.get_schema_names(
                conn, info_cache=self.info_cache, **kw
            )

    def get_table_names(
        self, schema: Optional[str] = None, **kw: Any
    ) -> List[str]:
        r"""Return all table names within a particular schema.

        The names are expected to be real tables only, not views.
        Views are instead returned using the
        :meth:`_reflection.Inspector.get_view_names` and/or
        :meth:`_reflection.Inspector.get_materialized_view_names`
        methods.

        :param schema: Schema name. If ``schema`` is left at ``None``, the
         database's default schema is
         used, else the named schema is searched.  If the database does not
         support named schemas, behavior is undefined if ``schema`` is not
         passed as ``None``.  For special quoting, use :class:`.quoted_name`.
        :param \**kw: Additional keyword argument to pass to the dialect
         specific implementation. See the documentation of the dialect
         in use for more information.

        .. seealso::

            :meth:`_reflection.Inspector.get_sorted_table_and_fkc_names`

            :attr:`_schema.MetaData.sorted_tables`

        """

        with self._operation_context() as conn:
            return self.dialect.get_table_names(
                conn, schema, info_cache=self.info_cache, **kw
            )

    def has_table(
        self, table_name: str, schema: Optional[str] = None, **kw: Any
    ) -> bool:
        r"""Return True if the backend has a table, view, or temporary
        table of the given name.

        :param table_name: name of the table to check
        :param schema: schema name to query, if not the default schema.
        :param \**kw: Additional keyword argument to pass to the dialect
         specific implementation. See the documentation of the dialect
         in use for more information.

        .. versionadded:: 1.4 - the :meth:`.Inspector.has_table` method
           replaces the :meth:`_engine.Engine.has_table` method.

        .. versionchanged:: 2.0:: :meth:`.Inspector.has_table` now formally
           supports checking for additional table-like objects:

           * any type of views (plain or materialized)
           * temporary tables of any kind

           Previously, these two checks were not formally specified and
           different dialects would vary in their behavior.   The dialect
           testing suite now includes tests for all of these object types
           and should be supported by all SQLAlchemy-included dialects.
           Support among third party dialects may be lagging, however.

        """
        with self._operation_context() as conn:
            return self.dialect.has_table(
                conn, table_name, schema, info_cache=self.info_cache, **kw
            )

    def has_sequence(
        self, sequence_name: str, schema: Optional[str] = None, **kw: Any
    ) -> bool:
        r"""Return True if the backend has a sequence with the given name.

        :param sequence_name: name of the sequence to check
        :param schema: schema name to query, if not the default schema.
        :param \**kw: Additional keyword argument to pass to the dialect
         specific implementation. See the documentation of the dialect
         in use for more information.

        .. versionadded:: 1.4

        """
        with self._operation_context() as conn:
            return self.dialect.has_sequence(
                conn, sequence_name, schema, info_cache=self.info_cache, **kw
            )

    def has_index(
        self,
        table_name: str,
        index_name: str,
        schema: Optional[str] = None,
        **kw: Any,
    ) -> bool:
        r"""Check the existence of a particular index name in the database.

        :param table_name: the name of the table the index belongs to
        :param index_name: the name of the index to check
        :param schema: schema name to query, if not the default schema.
        :param \**kw: Additional keyword argument to pass to the dialect
         specific implementation. See the documentation of the dialect
         in use for more information.

        .. versionadded:: 2.0

        """
        with self._operation_context() as conn:
            return self.dialect.has_index(
                conn,
                table_name,
                index_name,
                schema,
                info_cache=self.info_cache,
                **kw,
            )

    def has_schema(self, schema_name: str, **kw: Any) -> bool:
        r"""Return True if the backend has a schema with the given name.

        :param schema_name: name of the schema to check
        :param \**kw: Additional keyword argument to pass to the dialect
         specific implementation. See the documentation of the dialect
         in use for more information.

        .. versionadded:: 2.0

        """
        with self._operation_context() as conn:
            return self.dialect.has_schema(
                conn, schema_name, info_cache=self.info_cache, **kw
            )

    def get_sorted_table_and_fkc_names(
        self,
        schema: Optional[str] = None,
        **kw: Any,
    ) -> List[Tuple[Optional[str], List[Tuple[str, Optional[str]]]]]:
        r"""Return dependency-sorted table and foreign key constraint names in
        referred to within a particular schema.

        This will yield 2-tuples of
        ``(tablename, [(tname, fkname), (tname, fkname), ...])``
        consisting of table names in CREATE order grouped with the foreign key
        constraint names that are not detected as belonging to a cycle.
        The final element
        will be ``(None, [(tname, fkname), (tname, fkname), ..])``
        which will consist of remaining
        foreign key constraint names that would require a separate CREATE
        step after-the-fact, based on dependencies between tables.

        :param schema: schema name to query, if not the default schema.
        :param \**kw: Additional keyword argument to pass to the dialect
         specific implementation. See the documentation of the dialect
         in use for more information.

        .. seealso::

            :meth:`_reflection.Inspector.get_table_names`

            :func:`.sort_tables_and_constraints` - similar method which works
            with an already-given :class:`_schema.MetaData`.

        """

        return [
            (
                table_key[1] if table_key else None,
                [(tname, fks) for (_, tname), fks in fk_collection],
            )
            for (
                table_key,
                fk_collection,
            ) in self.sort_tables_on_foreign_key_dependency(
                consider_schemas=(schema,)
            )
        ]

    def sort_tables_on_foreign_key_dependency(
        self,
        consider_schemas: Collection[Optional[str]] = (None,),
        **kw: Any,
    ) -> List[
        Tuple[
            Optional[Tuple[Optional[str], str]],
            List[Tuple[Tuple[Optional[str], str], Optional[str]]],
        ]
    ]:
        r"""Return dependency-sorted table and foreign key constraint names
        referred to within multiple schemas.

        This method may be compared to
        :meth:`.Inspector.get_sorted_table_and_fkc_names`, which
        works on one schema at a time; here, the method is a generalization
        that will consider multiple schemas at once including that it will
        resolve for cross-schema foreign keys.

        .. versionadded:: 2.0

        """
        SchemaTab = Tuple[Optional[str], str]

        tuples: Set[Tuple[SchemaTab, SchemaTab]] = set()
        remaining_fkcs: Set[Tuple[SchemaTab, Optional[str]]] = set()
        fknames_for_table: Dict[SchemaTab, Set[Optional[str]]] = {}
        tnames: List[SchemaTab] = []

        for schname in consider_schemas:
            schema_fkeys = self.get_multi_foreign_keys(schname, **kw)
            tnames.extend(schema_fkeys)
            for (_, tname), fkeys in schema_fkeys.items():
                fknames_for_table[(schname, tname)] = {
                    fk["name"] for fk in fkeys
                }
                for fkey in fkeys:
                    if (
                        tname != fkey["referred_table"]
                        or schname != fkey["referred_schema"]
                    ):
                        tuples.add(
                            (
                                (
                                    fkey["referred_schema"],
                                    fkey["referred_table"],
                                ),
                                (schname, tname),
                            )
                        )
        try:
            candidate_sort = list(topological.sort(tuples, tnames))
        except exc.CircularDependencyError as err:
            edge: Tuple[SchemaTab, SchemaTab]
            for edge in err.edges:
                tuples.remove(edge)
                remaining_fkcs.update(
                    (edge[1], fkc) for fkc in fknames_for_table[edge[1]]
                )

            candidate_sort = list(topological.sort(tuples, tnames))
        ret: List[
            Tuple[Optional[SchemaTab], List[Tuple[SchemaTab, Optional[str]]]]
        ]
        ret = [
            (
                (schname, tname),
                [
                    ((schname, tname), fk)
                    for fk in fknames_for_table[(schname, tname)].difference(
                        name for _, name in remaining_fkcs
                    )
                ],
            )
            for (schname, tname) in candidate_sort
        ]
        return ret + [(None, list(remaining_fkcs))]

    def get_temp_table_names(self, **kw: Any) -> List[str]:
        r"""Return a list of temporary table names for the current bind.

        This method is unsupported by most dialects; currently
        only Oracle Database, PostgreSQL and SQLite implements it.

        :param \**kw: Additional keyword argument to pass to the dialect
         specific implementation. See the documentation of the dialect
         in use for more information.

        """

        with self._operation_context() as conn:
            return self.dialect.get_temp_table_names(
                conn, info_cache=self.info_cache, **kw
            )

    def get_temp_view_names(self, **kw: Any) -> List[str]:
        r"""Return a list of temporary view names for the current bind.

        This method is unsupported by most dialects; currently
        only PostgreSQL and SQLite implements it.

        :param \**kw: Additional keyword argument to pass to the dialect
         specific implementation. See the documentation of the dialect
         in use for more information.

        """
        with self._operation_context() as conn:
            return self.dialect.get_temp_view_names(
                conn, info_cache=self.info_cache, **kw
            )

    def get_table_options(
        self, table_name: str, schema: Optional[str] = None, **kw: Any
    ) -> Dict[str, Any]:
        r"""Return a dictionary of options specified when the table of the
        given name was created.

        This currently includes some options that apply to MySQL and Oracle
        Database tables.

        :param table_name: string name of the table.  For special quoting,
         use :class:`.quoted_name`.

        :param schema: string schema name; if omitted, uses the default schema
         of the database connection.  For special quoting,
         use :class:`.quoted_name`.

        :param \**kw: Additional keyword argument to pass to the dialect
         specific implementation. See the documentation of the dialect
         in use for more information.

        :return: a dict with the table options. The returned keys depend on the
         dialect in use. Each one is prefixed with the dialect name.

        .. seealso:: :meth:`Inspector.get_multi_table_options`

        """
        with self._operation_context() as conn:
            return self.dialect.get_table_options(
                conn, table_name, schema, info_cache=self.info_cache, **kw
            )

    def get_multi_table_options(
        self,
        schema: Optional[str] = None,
        filter_names: Optional[Sequence[str]] = None,
        kind: ObjectKind = ObjectKind.TABLE,
        scope: ObjectScope = ObjectScope.DEFAULT,
        **kw: Any,
    ) -> Dict[TableKey, Dict[str, Any]]:
        r"""Return a dictionary of options specified when the tables in the
        given schema were created.

        The tables can be filtered by passing the names to use to
        ``filter_names``.

        This currently includes some options that apply to MySQL and Oracle
        tables.

        :param schema: string schema name; if omitted, uses the default schema
         of the database connection.  For special quoting,
         use :class:`.quoted_name`.

        :param filter_names: optionally return information only for the
         objects listed here.

        :param kind: a :class:`.ObjectKind` that specifies the type of objects
         to reflect. Defaults to ``ObjectKind.TABLE``.

        :param scope: a :class:`.ObjectScope` that specifies if options of
         default, temporary or any tables should be reflected.
         Defaults to ``ObjectScope.DEFAULT``.

        :param \**kw: Additional keyword argument to pass to the dialect
         specific implementation. See the documentation of the dialect
         in use for more information.

        :return: a dictionary where the keys are two-tuple schema,table-name
         and the values are dictionaries with the table options.
         The returned keys in each dict depend on the
         dialect in use. Each one is prefixed with the dialect name.
         The schema is ``None`` if no schema is provided.

        .. versionadded:: 2.0

        .. seealso:: :meth:`Inspector.get_table_options`
        """
        with self._operation_context() as conn:
            res = self.dialect.get_multi_table_options(
                conn,
                schema=schema,
                filter_names=filter_names,
                kind=kind,
                scope=scope,
                info_cache=self.info_cache,
                **kw,
            )
            return dict(res)

    def get_view_names(
        self, schema: Optional[str] = None, **kw: Any
    ) -> List[str]:
        r"""Return all non-materialized view names in `schema`.

        :param schema: Optional, retrieve names from a non-default schema.
         For special quoting, use :class:`.quoted_name`.
        :param \**kw: Additional keyword argument to pass to the dialect
         specific implementation. See the documentation of the dialect
         in use for more information.


        .. versionchanged:: 2.0  For those dialects that previously included
           the names of materialized views in this list (currently PostgreSQL),
           this method no longer returns the names of materialized views.
           the :meth:`.Inspector.get_materialized_view_names` method should
           be used instead.

        .. seealso::

            :meth:`.Inspector.get_materialized_view_names`

        """

        with self._operation_context() as conn:
            return self.dialect.get_view_names(
                conn, schema, info_cache=self.info_cache, **kw
            )

    def get_materialized_view_names(
        self, schema: Optional[str] = None, **kw: Any
    ) -> List[str]:
        r"""Return all materialized view names in `schema`.

        :param schema: Optional, retrieve names from a non-default schema.
         For special quoting, use :class:`.quoted_name`.
        :param \**kw: Additional keyword argument to pass to the dialect
         specific implementation. See the documentation of the dialect
         in use for more information.

        .. versionadded:: 2.0

        .. seealso::

            :meth:`.Inspector.get_view_names`

        """

        with self._operation_context() as conn:
            return self.dialect.get_materialized_view_names(
                conn, schema, info_cache=self.info_cache, **kw
            )

    def get_sequence_names(
        self, schema: Optional[str] = None, **kw: Any
    ) -> List[str]:
        r"""Return all sequence names in `schema`.

        :param schema: Optional, retrieve names from a non-default schema.
         For special quoting, use :class:`.quoted_name`.
        :param \**kw: Additional keyword argument to pass to the dialect
         specific implementation. See the documentation of the dialect
         in use for more information.

        """

        with self._operation_context() as conn:
            return self.dialect.get_sequence_names(
                conn, schema, info_cache=self.info_cache, **kw
            )

    def get_view_definition(
        self, view_name: str, schema: Optional[str] = None, **kw: Any
    ) -> str:
        r"""Return definition for the plain or materialized view called
        ``view_name``.

        :param view_name: Name of the view.
        :param schema: Optional, retrieve names from a non-default schema.
         For special quoting, use :class:`.quoted_name`.
        :param \**kw: Additional keyword argument to pass to the dialect
         specific implementation. See the documentation of the dialect
         in use for more information.

        """

        with self._operation_context() as conn:
            return self.dialect.get_view_definition(
                conn, view_name, schema, info_cache=self.info_cache, **kw
            )

    def get_columns(
        self, table_name: str, schema: Optional[str] = None, **kw: Any
    ) -> List[ReflectedColumn]:
        r"""Return information about columns in ``table_name``.

        Given a string ``table_name`` and an optional string ``schema``,
        return column information as a list of :class:`.ReflectedColumn`.

        :param table_name: string name of the table.  For special quoting,
         use :class:`.quoted_name`.

        :param schema: string schema name; if omitted, uses the default schema
         of the database connection.  For special quoting,
         use :class:`.quoted_name`.

        :param \**kw: Additional keyword argument to pass to the dialect
         specific implementation. See the documentation of the dialect
         in use for more information.

        :return: list of dictionaries, each representing the definition of
         a database column.

        .. seealso:: :meth:`Inspector.get_multi_columns`.

        """

        with self._operation_context() as conn:
            col_defs = self.dialect.get_columns(
                conn, table_name, schema, info_cache=self.info_cache, **kw
            )
        if col_defs:
            self._instantiate_types([col_defs])
        return col_defs

    def _instantiate_types(
        self, data: Iterable[List[ReflectedColumn]]
    ) -> None:
        # make this easy and only return instances for coltype
        for col_defs in data:
            for col_def in col_defs:
                coltype = col_def["type"]
                if not isinstance(coltype, TypeEngine):
                    col_def["type"] = coltype()

    def get_multi_columns(
        self,
        schema: Optional[str] = None,
        filter_names: Optional[Sequence[str]] = None,
        kind: ObjectKind = ObjectKind.TABLE,
        scope: ObjectScope = ObjectScope.DEFAULT,
        **kw: Any,
    ) -> Dict[TableKey, List[ReflectedColumn]]:
        r"""Return information about columns in all objects in the given
        schema.

        The objects can be filtered by passing the names to use to
        ``filter_names``.

        For each table the value is a list of :class:`.ReflectedColumn`.

        :param schema: string schema name; if omitted, uses the default schema
         of the database connection.  For special quoting,
         use :class:`.quoted_name`.

        :param filter_names: optionally return information only for the
         objects listed here.

        :param kind: a :class:`.ObjectKind` that specifies the type of objects
         to reflect. Defaults to ``ObjectKind.TABLE``.

        :param scope: a :class:`.ObjectScope` that specifies if columns of
         default, temporary or any tables should be reflected.
         Defaults to ``ObjectScope.DEFAULT``.

        :param \**kw: Additional keyword argument to pass to the dialect
         specific implementation. See the documentation of the dialect
         in use for more information.

        :return: a dictionary where the keys are two-tuple schema,table-name
         and the values are list of dictionaries, each representing the
         definition of a database column.
         The schema is ``None`` if no schema is provided.

        .. versionadded:: 2.0

        .. seealso:: :meth:`Inspector.get_columns`
        """

        with self._operation_context() as conn:
            table_col_defs = dict(
                self.dialect.get_multi_columns(
                    conn,
                    schema=schema,
                    filter_names=filter_names,
                    kind=kind,
                    scope=scope,
                    info_cache=self.info_cache,
                    **kw,
                )
            )
        self._instantiate_types(table_col_defs.values())
        return table_col_defs

    def get_pk_constraint(
        self, table_name: str, schema: Optional[str] = None, **kw: Any
    ) -> ReflectedPrimaryKeyConstraint:
        r"""Return information about primary key constraint in ``table_name``.

        Given a string ``table_name``, and an optional string `schema`, return
        primary key information as a :class:`.ReflectedPrimaryKeyConstraint`.

        :param table_name: string name of the table.  For special quoting,
         use :class:`.quoted_name`.

        :param schema: string schema name; if omitted, uses the default schema
         of the database connection.  For special quoting,
         use :class:`.quoted_name`.

        :param \**kw: Additional keyword argument to pass to the dialect
         specific implementation. See the documentation of the dialect
         in use for more information.

        :return: a dictionary representing the definition of
         a primary key constraint.

        .. seealso:: :meth:`Inspector.get_multi_pk_constraint`
        """
        with self._operation_context() as conn:
            return self.dialect.get_pk_constraint(
                conn, table_name, schema, info_cache=self.info_cache, **kw
            )

    def get_multi_pk_constraint(
        self,
        schema: Optional[str] = None,
        filter_names: Optional[Sequence[str]] = None,
        kind: ObjectKind = ObjectKind.TABLE,
        scope: ObjectScope = ObjectScope.DEFAULT,
        **kw: Any,
    ) -> Dict[TableKey, ReflectedPrimaryKeyConstraint]:
        r"""Return information about primary key constraints in
        all tables in the given schema.

        The tables can be filtered by passing the names to use to
        ``filter_names``.

        For each table the value is a :class:`.ReflectedPrimaryKeyConstraint`.

        :param schema: string schema name; if omitted, uses the default schema
         of the database connection.  For special quoting,
         use :class:`.quoted_name`.

        :param filter_names: optionally return information only for the
         objects listed here.

        :param kind: a :class:`.ObjectKind` that specifies the type of objects
         to reflect. Defaults to ``ObjectKind.TABLE``.

        :param scope: a :class:`.ObjectScope` that specifies if primary keys of
