# engine/base.py
# Copyright (C) 2005-2026 the SQLAlchemy authors and contributors
# <see AUTHORS file>
#
# This module is part of SQLAlchemy and is released under
# the MIT License: https://www.opensource.org/licenses/mit-license.php
"""Defines :class:`_engine.Connection` and :class:`_engine.Engine`."""
from __future__ import annotations

import contextlib
import sys
import typing
from typing import Any
from typing import Callable
from typing import cast
from typing import Iterable
from typing import Iterator
from typing import List
from typing import Mapping
from typing import NoReturn
from typing import Optional
from typing import overload
from typing import Tuple
from typing import Type
from typing import TypeVar
from typing import Union

from .interfaces import BindTyping
from .interfaces import ConnectionEventsTarget
from .interfaces import DBAPICursor
from .interfaces import ExceptionContext
from .interfaces import ExecuteStyle
from .interfaces import ExecutionContext
from .interfaces import IsolationLevel
from .util import _distill_params_20
from .util import _distill_raw_params
from .util import TransactionalContext
from .. import exc
from .. import inspection
from .. import log
from .. import util
from ..sql import compiler
from ..sql import util as sql_util

if typing.TYPE_CHECKING:
    from . import CursorResult
    from . import ScalarResult
    from .interfaces import _AnyExecuteParams
    from .interfaces import _AnyMultiExecuteParams
    from .interfaces import _CoreAnyExecuteParams
    from .interfaces import _CoreMultiExecuteParams
    from .interfaces import _CoreSingleExecuteParams
    from .interfaces import _DBAPIAnyExecuteParams
    from .interfaces import _DBAPISingleExecuteParams
    from .interfaces import _ExecuteOptions
    from .interfaces import CompiledCacheType
    from .interfaces import CoreExecuteOptionsParameter
    from .interfaces import Dialect
    from .interfaces import SchemaTranslateMapType
    from .reflection import Inspector  # noqa
    from .url import URL
    from ..event import dispatcher
    from ..log import _EchoFlagType
    from ..pool import _ConnectionFairy
    from ..pool import Pool
    from ..pool import PoolProxiedConnection
    from ..sql import Executable
    from ..sql._typing import _InfoType
    from ..sql.compiler import Compiled
    from ..sql.ddl import ExecutableDDLElement
    from ..sql.ddl import InvokeDDLBase
    from ..sql.functions import FunctionElement
    from ..sql.schema import DefaultGenerator
    from ..sql.schema import HasSchemaAttr
    from ..sql.schema import SchemaVisitable
    from ..sql.selectable import TypedReturnsRows


_T = TypeVar("_T", bound=Any)
_EMPTY_EXECUTION_OPTS: _ExecuteOptions = util.EMPTY_DICT
NO_OPTIONS: Mapping[str, Any] = util.EMPTY_DICT


class Connection(ConnectionEventsTarget, inspection.Inspectable["Inspector"]):
    """Provides high-level functionality for a wrapped DB-API connection.

    The :class:`_engine.Connection` object is procured by calling the
    :meth:`_engine.Engine.connect` method of the :class:`_engine.Engine`
    object, and provides services for execution of SQL statements as well
    as transaction control.

    The Connection object is **not** thread-safe. While a Connection can be
    shared among threads using properly synchronized access, it is still
    possible that the underlying DBAPI connection may not support shared
    access between threads. Check the DBAPI documentation for details.

    The Connection object represents a single DBAPI connection checked out
    from the connection pool. In this state, the connection pool has no
    affect upon the connection, including its expiration or timeout state.
    For the connection pool to properly manage connections, connections
    should be returned to the connection pool (i.e. ``connection.close()``)
    whenever the connection is not in use.

    .. index::
      single: thread safety; Connection

    """

    dialect: Dialect
    dispatch: dispatcher[ConnectionEventsTarget]

    _sqla_logger_namespace = "sqlalchemy.engine.Connection"

    # used by sqlalchemy.engine.util.TransactionalContext
    _trans_context_manager: Optional[TransactionalContext] = None

    # legacy as of 2.0, should be eventually deprecated and
    # removed.  was used in the "pre_ping" recipe that's been in the docs
    # a long time
    should_close_with_result = False

    _dbapi_connection: Optional[PoolProxiedConnection]

    _execution_options: _ExecuteOptions

    _transaction: Optional[RootTransaction]
    _nested_transaction: Optional[NestedTransaction]

    def __init__(
        self,
        engine: Engine,
        connection: Optional[PoolProxiedConnection] = None,
        _has_events: Optional[bool] = None,
        _allow_revalidate: bool = True,
        _allow_autobegin: bool = True,
    ):
        """Construct a new Connection."""
        self.engine = engine
        self.dialect = dialect = engine.dialect

        if connection is None:
            try:
                self._dbapi_connection = engine.raw_connection()
            except dialect.loaded_dbapi.Error as err:
                Connection._handle_dbapi_exception_noconnection(
                    err, dialect, engine
                )
                raise
        else:
            self._dbapi_connection = connection

        self._transaction = self._nested_transaction = None
        self.__savepoint_seq = 0
        self.__in_begin = False

        self.__can_reconnect = _allow_revalidate
        self._allow_autobegin = _allow_autobegin
        self._echo = self.engine._should_log_info()

        if _has_events is None:
            # if _has_events is sent explicitly as False,
            # then don't join the dispatch of the engine; we don't
            # want to handle any of the engine's events in that case.
            self.dispatch = self.dispatch._join(engine.dispatch)
        self._has_events = _has_events or (
            _has_events is None and engine._has_events
        )

        self._execution_options = engine._execution_options

        if self._has_events or self.engine._has_events:
            self.dispatch.engine_connect(self)

    # this can be assigned differently via
    # characteristics.LoggingTokenCharacteristic
    _message_formatter: Any = None

    def _log_info(self, message: str, *arg: Any, **kw: Any) -> None:
        fmt = self._message_formatter

        if fmt:
            message = fmt(message)

        if log.STACKLEVEL:
            kw["stacklevel"] = 1 + log.STACKLEVEL_OFFSET

        self.engine.logger.info(message, *arg, **kw)

    def _log_debug(self, message: str, *arg: Any, **kw: Any) -> None:
        fmt = self._message_formatter

        if fmt:
            message = fmt(message)

        if log.STACKLEVEL:
            kw["stacklevel"] = 1 + log.STACKLEVEL_OFFSET

        self.engine.logger.debug(message, *arg, **kw)

    @property
    def _schema_translate_map(self) -> Optional[SchemaTranslateMapType]:
        schema_translate_map: Optional[SchemaTranslateMapType] = (
            self._execution_options.get("schema_translate_map", None)
        )

        return schema_translate_map

    def schema_for_object(self, obj: HasSchemaAttr) -> Optional[str]:
        """Return the schema name for the given schema item taking into
        account current schema translate map.

        """

        name = obj.schema
        schema_translate_map: Optional[SchemaTranslateMapType] = (
            self._execution_options.get("schema_translate_map", None)
        )

        if (
            schema_translate_map
            and name in schema_translate_map
            and obj._use_schema_map
        ):
            return schema_translate_map[name]
        else:
            return name

    def __enter__(self) -> Connection:
        return self

    def __exit__(self, type_: Any, value: Any, traceback: Any) -> None:
        self.close()

    @overload
    def execution_options(
        self,
        *,
        compiled_cache: Optional[CompiledCacheType] = ...,
        logging_token: str = ...,
        isolation_level: IsolationLevel = ...,
        no_parameters: bool = False,
        stream_results: bool = False,
        max_row_buffer: int = ...,
        yield_per: int = ...,
        insertmanyvalues_page_size: int = ...,
        schema_translate_map: Optional[SchemaTranslateMapType] = ...,
        preserve_rowcount: bool = False,
        **opt: Any,
    ) -> Connection: ...

    @overload
    def execution_options(self, **opt: Any) -> Connection: ...

    def execution_options(self, **opt: Any) -> Connection:
        r"""Set non-SQL options for the connection which take effect
        during execution.

        This method modifies this :class:`_engine.Connection` **in-place**;
        the return value is the same :class:`_engine.Connection` object
        upon which the method is called.   Note that this is in contrast
        to the behavior of the ``execution_options`` methods on other
        objects such as :meth:`_engine.Engine.execution_options` and
        :meth:`_sql.Executable.execution_options`.  The rationale is that many
        such execution options necessarily modify the state of the base
        DBAPI connection in any case so there is no feasible means of
        keeping the effect of such an option localized to a "sub" connection.

        .. versionchanged:: 2.0  The :meth:`_engine.Connection.execution_options`
           method, in contrast to other objects with this method, modifies
           the connection in-place without creating copy of it.

        As discussed elsewhere, the :meth:`_engine.Connection.execution_options`
        method accepts any arbitrary parameters including user defined names.
        All parameters given are consumable in a number of ways including
        by using the :meth:`_engine.Connection.get_execution_options` method.
        See the examples at :meth:`_sql.Executable.execution_options`
        and :meth:`_engine.Engine.execution_options`.

        The keywords that are currently recognized by SQLAlchemy itself
        include all those listed under :meth:`.Executable.execution_options`,
        as well as others that are specific to :class:`_engine.Connection`.

        :param compiled_cache: Available on: :class:`_engine.Connection`,
          :class:`_engine.Engine`.

          A dictionary where :class:`.Compiled` objects
          will be cached when the :class:`_engine.Connection`
          compiles a clause
          expression into a :class:`.Compiled` object.  This dictionary will
          supersede the statement cache that may be configured on the
          :class:`_engine.Engine` itself.   If set to None, caching
          is disabled, even if the engine has a configured cache size.

          Note that the ORM makes use of its own "compiled" caches for
          some operations, including flush operations.  The caching
          used by the ORM internally supersedes a cache dictionary
          specified here.

        :param logging_token: Available on: :class:`_engine.Connection`,
          :class:`_engine.Engine`, :class:`_sql.Executable`.

          Adds the specified string token surrounded by brackets in log
          messages logged by the connection, i.e. the logging that's enabled
          either via the :paramref:`_sa.create_engine.echo` flag or via the
          ``logging.getLogger("sqlalchemy.engine")`` logger. This allows a
          per-connection or per-sub-engine token to be available which is
          useful for debugging concurrent connection scenarios.

          .. versionadded:: 1.4.0b2

          .. seealso::

            :ref:`dbengine_logging_tokens` - usage example

            :paramref:`_sa.create_engine.logging_name` - adds a name to the
            name used by the Python logger object itself.

        :param isolation_level: Available on: :class:`_engine.Connection`,
          :class:`_engine.Engine`.

          Set the transaction isolation level for the lifespan of this
          :class:`_engine.Connection` object.
          Valid values include those string
          values accepted by the :paramref:`_sa.create_engine.isolation_level`
          parameter passed to :func:`_sa.create_engine`.  These levels are
          semi-database specific; see individual dialect documentation for
          valid levels.

          The isolation level option applies the isolation level by emitting
          statements on the DBAPI connection, and **necessarily affects the
          original Connection object overall**. The isolation level will remain
          at the given setting until explicitly changed, or when the DBAPI
          connection itself is :term:`released` to the connection pool, i.e. the
          :meth:`_engine.Connection.close` method is called, at which time an
          event handler will emit additional statements on the DBAPI connection
          in order to revert the isolation level change.

          .. note:: The ``isolation_level`` execution option may only be
             established before the :meth:`_engine.Connection.begin` method is
             called, as well as before any SQL statements are emitted which
             would otherwise trigger "autobegin", or directly after a call to
             :meth:`_engine.Connection.commit` or
             :meth:`_engine.Connection.rollback`. A database cannot change the
             isolation level on a transaction in progress.

          .. note:: The ``isolation_level`` execution option is implicitly
             reset if the :class:`_engine.Connection` is invalidated, e.g. via
             the :meth:`_engine.Connection.invalidate` method, or if a
             disconnection error occurs. The new connection produced after the
             invalidation will **not** have the selected isolation level
             re-applied to it automatically.

          .. seealso::

                :ref:`dbapi_autocommit`

                :meth:`_engine.Connection.get_isolation_level`
                - view current actual level

        :param no_parameters: Available on: :class:`_engine.Connection`,
          :class:`_sql.Executable`.

          When ``True``, if the final parameter
          list or dictionary is totally empty, will invoke the
          statement on the cursor as ``cursor.execute(statement)``,
          not passing the parameter collection at all.
          Some DBAPIs such as psycopg2 and mysql-python consider
          percent signs as significant only when parameters are
          present; this option allows code to generate SQL
          containing percent signs (and possibly other characters)
          that is neutral regarding whether it's executed by the DBAPI
          or piped into a script that's later invoked by
          command line tools.

        :param stream_results: Available on: :class:`_engine.Connection`,
          :class:`_sql.Executable`.

          Indicate to the dialect that results should be "streamed" and not
          pre-buffered, if possible.  For backends such as PostgreSQL, MySQL
          and MariaDB, this indicates the use of a "server side cursor" as
          opposed to a client side cursor.  Other backends such as that of
          Oracle Database may already use server side cursors by default.

          The usage of
          :paramref:`_engine.Connection.execution_options.stream_results` is
          usually combined with setting a fixed number of rows to to be fetched
          in batches, to allow for efficient iteration of database rows while
          at the same time not loading all result rows into memory at once;
          this can be configured on a :class:`_engine.Result` object using the
          :meth:`_engine.Result.yield_per` method, after execution has
          returned a new :class:`_engine.Result`.   If
          :meth:`_engine.Result.yield_per` is not used,
          the :paramref:`_engine.Connection.execution_options.stream_results`
          mode of operation will instead use a dynamically sized buffer
          which buffers sets of rows at a time, growing on each batch
          based on a fixed growth size up until a limit which may
          be configured using the
          :paramref:`_engine.Connection.execution_options.max_row_buffer`
          parameter.

          When using the ORM to fetch ORM mapped objects from a result,
          :meth:`_engine.Result.yield_per` should always be used with
          :paramref:`_engine.Connection.execution_options.stream_results`,
          so that the ORM does not fetch all rows into new ORM objects at once.

          For typical use, the
          :paramref:`_engine.Connection.execution_options.yield_per` execution
          option should be preferred, which sets up both
          :paramref:`_engine.Connection.execution_options.stream_results` and
          :meth:`_engine.Result.yield_per` at once. This option is supported
          both at a core level by :class:`_engine.Connection` as well as by the
          ORM :class:`_engine.Session`; the latter is described at
          :ref:`orm_queryguide_yield_per`.

          .. seealso::

            :ref:`engine_stream_results` - background on
            :paramref:`_engine.Connection.execution_options.stream_results`

            :paramref:`_engine.Connection.execution_options.max_row_buffer`

            :paramref:`_engine.Connection.execution_options.yield_per`

            :ref:`orm_queryguide_yield_per` - in the :ref:`queryguide_toplevel`
            describing the ORM version of ``yield_per``

        :param max_row_buffer: Available on: :class:`_engine.Connection`,
          :class:`_sql.Executable`.  Sets a maximum
          buffer size to use when the
          :paramref:`_engine.Connection.execution_options.stream_results`
          execution option is used on a backend that supports server side
          cursors.  The default value if not specified is 1000.

          .. seealso::

            :paramref:`_engine.Connection.execution_options.stream_results`

            :ref:`engine_stream_results`


        :param yield_per: Available on: :class:`_engine.Connection`,
          :class:`_sql.Executable`.  Integer value applied which will
          set the :paramref:`_engine.Connection.execution_options.stream_results`
          execution option and invoke :meth:`_engine.Result.yield_per`
          automatically at once.  Allows equivalent functionality as
          is present when using this parameter with the ORM.

          .. versionadded:: 1.4.40

          .. seealso::

            :ref:`engine_stream_results` - background and examples
            on using server side cursors with Core.

            :ref:`orm_queryguide_yield_per` - in the :ref:`queryguide_toplevel`
            describing the ORM version of ``yield_per``

        :param insertmanyvalues_page_size: Available on: :class:`_engine.Connection`,
            :class:`_engine.Engine`. Number of rows to format into an
            INSERT statement when the statement uses "insertmanyvalues" mode,
            which is a paged form of bulk insert that is used for many backends
            when using :term:`executemany` execution typically in conjunction
            with RETURNING. Defaults to 1000. May also be modified on a
            per-engine basis using the
            :paramref:`_sa.create_engine.insertmanyvalues_page_size` parameter.

            .. versionadded:: 2.0

            .. seealso::

                :ref:`engine_insertmanyvalues`

        :param schema_translate_map: Available on: :class:`_engine.Connection`,
          :class:`_engine.Engine`, :class:`_sql.Executable`.

          A dictionary mapping schema names to schema names, that will be
          applied to the :paramref:`_schema.Table.schema` element of each
          :class:`_schema.Table`
          encountered when SQL or DDL expression elements
          are compiled into strings; the resulting schema name will be
          converted based on presence in the map of the original name.

          .. seealso::

            :ref:`schema_translating`

        :param preserve_rowcount: Boolean; when True, the ``cursor.rowcount``
          attribute will be unconditionally memoized within the result and
          made available via the :attr:`.CursorResult.rowcount` attribute.
          Normally, this attribute is only preserved for UPDATE and DELETE
          statements.  Using this option, the DBAPIs rowcount value can
          be accessed for other kinds of statements such as INSERT and SELECT,
          to the degree that the DBAPI supports these statements.  See
          :attr:`.CursorResult.rowcount` for notes regarding the behavior
          of this attribute.

          .. versionadded:: 2.0.28

        .. seealso::

            :meth:`_engine.Engine.execution_options`

            :meth:`.Executable.execution_options`

            :meth:`_engine.Connection.get_execution_options`

            :ref:`orm_queryguide_execution_options` - documentation on all
            ORM-specific execution options

        """  # noqa
        if self._has_events or self.engine._has_events:
            self.dispatch.set_connection_execution_options(self, opt)
        self._execution_options = self._execution_options.union(opt)
        self.dialect.set_connection_execution_options(self, opt)
        return self

    def get_execution_options(self) -> _ExecuteOptions:
        """Get the non-SQL options which will take effect during execution.

        .. versionadded:: 1.3

        .. seealso::

            :meth:`_engine.Connection.execution_options`
        """
        return self._execution_options

    @property
    def _still_open_and_dbapi_connection_is_valid(self) -> bool:
        pool_proxied_connection = self._dbapi_connection
        return (
            pool_proxied_connection is not None
            and pool_proxied_connection.is_valid
        )

    @property
    def closed(self) -> bool:
        """Return True if this connection is closed."""

        return self._dbapi_connection is None and not self.__can_reconnect

    @property
    def invalidated(self) -> bool:
        """Return True if this connection was invalidated.

        This does not indicate whether or not the connection was
        invalidated at the pool level, however

        """

        # prior to 1.4, "invalid" was stored as a state independent of
        # "closed", meaning an invalidated connection could be "closed",
        # the _dbapi_connection would be None and closed=True, yet the
        # "invalid" flag would stay True.  This meant that there were
        # three separate states (open/valid, closed/valid, closed/invalid)
        # when there is really no reason for that; a connection that's
        # "closed" does not need to be "invalid".  So the state is now
        # represented by the two facts alone.

        pool_proxied_connection = self._dbapi_connection
        return pool_proxied_connection is None and self.__can_reconnect

    @property
    def connection(self) -> PoolProxiedConnection:
        """The underlying DB-API connection managed by this Connection.

        This is a SQLAlchemy connection-pool proxied connection
        which then has the attribute
        :attr:`_pool._ConnectionFairy.dbapi_connection` that refers to the
        actual driver connection.

        .. seealso::


            :ref:`dbapi_connections`

        """

        if self._dbapi_connection is None:
            try:
                return self._revalidate_connection()
            except (exc.PendingRollbackError, exc.ResourceClosedError):
                raise
            except BaseException as e:
                self._handle_dbapi_exception(e, None, None, None, None)
        else:
            return self._dbapi_connection

    def get_isolation_level(self) -> IsolationLevel:
        """Return the current **actual** isolation level that's present on
        the database within the scope of this connection.

        This attribute will perform a live SQL operation against the database
        in order to procure the current isolation level, so the value returned
        is the actual level on the underlying DBAPI connection regardless of
        how this state was set. This will be one of the four actual isolation
        modes ``READ UNCOMMITTED``, ``READ COMMITTED``, ``REPEATABLE READ``,
        ``SERIALIZABLE``. It will **not** include the ``AUTOCOMMIT`` isolation
        level setting. Third party dialects may also feature additional
        isolation level settings.

        .. note::  This method **will not report** on the ``AUTOCOMMIT``
          isolation level, which is a separate :term:`dbapi` setting that's
          independent of **actual** isolation level.  When ``AUTOCOMMIT`` is
          in use, the database connection still has a "traditional" isolation
          mode in effect, that is typically one of the four values
          ``READ UNCOMMITTED``, ``READ COMMITTED``, ``REPEATABLE READ``,
          ``SERIALIZABLE``.

        Compare to the :attr:`_engine.Connection.default_isolation_level`
        accessor which returns the isolation level that is present on the
        database at initial connection time.

        .. seealso::

            :attr:`_engine.Connection.default_isolation_level`
            - view default level

            :paramref:`_sa.create_engine.isolation_level`
            - set per :class:`_engine.Engine` isolation level

            :paramref:`.Connection.execution_options.isolation_level`
            - set per :class:`_engine.Connection` isolation level

        """
        dbapi_connection = self.connection.dbapi_connection
        assert dbapi_connection is not None
        try:
            return self.dialect.get_isolation_level(dbapi_connection)
        except BaseException as e:
            self._handle_dbapi_exception(e, None, None, None, None)

    @property
    def default_isolation_level(self) -> Optional[IsolationLevel]:
        """The initial-connection time isolation level associated with the
        :class:`_engine.Dialect` in use.

        This value is independent of the
        :paramref:`.Connection.execution_options.isolation_level` and
        :paramref:`.Engine.execution_options.isolation_level` execution
        options, and is determined by the :class:`_engine.Dialect` when the
        first connection is created, by performing a SQL query against the
        database for the current isolation level before any additional commands
        have been emitted.

        Calling this accessor does not invoke any new SQL queries.

        .. seealso::

            :meth:`_engine.Connection.get_isolation_level`
            - view current actual isolation level

            :paramref:`_sa.create_engine.isolation_level`
            - set per :class:`_engine.Engine` isolation level

            :paramref:`.Connection.execution_options.isolation_level`
            - set per :class:`_engine.Connection` isolation level

        """
        return self.dialect.default_isolation_level

    def _invalid_transaction(self) -> NoReturn:
        raise exc.PendingRollbackError(
            "Can't reconnect until invalid %stransaction is rolled "
            "back.  Please rollback() fully before proceeding"
            % ("savepoint " if self._nested_transaction is not None else ""),
            code="8s2b",
        )

    def _revalidate_connection(self) -> PoolProxiedConnection:
        if self.__can_reconnect and self.invalidated:
            if self._transaction is not None:
                self._invalid_transaction()
            self._dbapi_connection = self.engine.raw_connection()
            return self._dbapi_connection
        raise exc.ResourceClosedError("This Connection is closed")

    @property
    def info(self) -> _InfoType:
        """Info dictionary associated with the underlying DBAPI connection
        referred to by this :class:`_engine.Connection`, allowing user-defined
        data to be associated with the connection.

        The data here will follow along with the DBAPI connection including
        after it is returned to the connection pool and used again
        in subsequent instances of :class:`_engine.Connection`.

        """

        return self.connection.info

    def invalidate(self, exception: Optional[BaseException] = None) -> None:
        """Invalidate the underlying DBAPI connection associated with
        this :class:`_engine.Connection`.

        An attempt will be made to close the underlying DBAPI connection
        immediately; however if this operation fails, the error is logged
        but not raised.  The connection is then discarded whether or not
        close() succeeded.

        Upon the next use (where "use" typically means using the
        :meth:`_engine.Connection.execute` method or similar),
        this :class:`_engine.Connection` will attempt to
        procure a new DBAPI connection using the services of the
        :class:`_pool.Pool` as a source of connectivity (e.g.
        a "reconnection").

        If a transaction was in progress (e.g. the
        :meth:`_engine.Connection.begin` method has been called) when
        :meth:`_engine.Connection.invalidate` method is called, at the DBAPI
        level all state associated with this transaction is lost, as
        the DBAPI connection is closed.  The :class:`_engine.Connection`
        will not allow a reconnection to proceed until the
        :class:`.Transaction` object is ended, by calling the
        :meth:`.Transaction.rollback` method; until that point, any attempt at
        continuing to use the :class:`_engine.Connection` will raise an
        :class:`~sqlalchemy.exc.InvalidRequestError`.
        This is to prevent applications from accidentally
        continuing an ongoing transactional operations despite the
        fact that the transaction has been lost due to an
        invalidation.

        The :meth:`_engine.Connection.invalidate` method,
        just like auto-invalidation,
        will at the connection pool level invoke the
        :meth:`_events.PoolEvents.invalidate` event.

        :param exception: an optional ``Exception`` instance that's the
         reason for the invalidation.  is passed along to event handlers
         and logging functions.

        .. seealso::

            :ref:`pool_connection_invalidation`

        """

        if self.invalidated:
            return

        if self.closed:
            raise exc.ResourceClosedError("This Connection is closed")

        if self._still_open_and_dbapi_connection_is_valid:
            pool_proxied_connection = self._dbapi_connection
            assert pool_proxied_connection is not None
            pool_proxied_connection.invalidate(exception)

        self._dbapi_connection = None

    def detach(self) -> None:
        """Detach the underlying DB-API connection from its connection pool.

        E.g.::

            with engine.connect() as conn:
                conn.detach()
                conn.execute(text("SET search_path TO schema1, schema2"))

                # work with connection

            # connection is fully closed (since we used "with:", can
            # also call .close())

        This :class:`_engine.Connection` instance will remain usable.
        When closed
        (or exited from a context manager context as above),
        the DB-API connection will be literally closed and not
        returned to its originating pool.

        This method can be used to insulate the rest of an application
        from a modified state on a connection (such as a transaction
        isolation level or similar).

        """

        if self.closed:
            raise exc.ResourceClosedError("This Connection is closed")

        pool_proxied_connection = self._dbapi_connection
        if pool_proxied_connection is None:
            raise exc.InvalidRequestError(
                "Can't detach an invalidated Connection"
            )
        pool_proxied_connection.detach()

    def _autobegin(self) -> None:
        if self._allow_autobegin and not self.__in_begin:
            self.begin()

    def begin(self) -> RootTransaction:
        """Begin a transaction prior to autobegin occurring.

        E.g.::

            with engine.connect() as conn:
                with conn.begin() as trans:
                    conn.execute(table.insert(), {"username": "sandy"})

        The returned object is an instance of :class:`_engine.RootTransaction`.
        This object represents the "scope" of the transaction,
        which completes when either the :meth:`_engine.Transaction.rollback`
        or :meth:`_engine.Transaction.commit` method is called; the object
        also works as a context manager as illustrated above.

        The :meth:`_engine.Connection.begin` method begins a
        transaction that normally will be begun in any case when the connection
        is first used to execute a statement.  The reason this method might be
        used would be to invoke the :meth:`_events.ConnectionEvents.begin`
        event at a specific time, or to organize code within the scope of a
        connection checkout in terms of context managed blocks, such as::

            with engine.connect() as conn:
                with conn.begin():
                    conn.execute(...)
                    conn.execute(...)

                with conn.begin():
                    conn.execute(...)
                    conn.execute(...)

        The above code is not  fundamentally any different in its behavior than
        the following code  which does not use
        :meth:`_engine.Connection.begin`; the below style is known
        as "commit as you go" style::

            with engine.connect() as conn:
                conn.execute(...)
                conn.execute(...)
                conn.commit()

                conn.execute(...)
                conn.execute(...)
                conn.commit()

        From a database point of view, the :meth:`_engine.Connection.begin`
        method does not emit any SQL or change the state of the underlying
        DBAPI connection in any way; the Python DBAPI does not have any
        concept of explicit transaction begin.

        .. seealso::

            :ref:`tutorial_working_with_transactions` - in the
            :ref:`unified_tutorial`

            :meth:`_engine.Connection.begin_nested` - use a SAVEPOINT

            :meth:`_engine.Connection.begin_twophase` -
            use a two phase /XID transaction

            :meth:`_engine.Engine.begin` - context manager available from
            :class:`_engine.Engine`

        """
        if self._transaction is None:
            self._transaction = RootTransaction(self)
            return self._transaction
        else:
            raise exc.InvalidRequestError(
                "This connection has already initialized a SQLAlchemy "
                "Transaction() object via begin() or autobegin; can't "
                "call begin() here unless rollback() or commit() "
                "is called first."
            )

    def begin_nested(self) -> NestedTransaction:
        """Begin a nested transaction (i.e. SAVEPOINT) and return a transaction
        handle that controls the scope of the SAVEPOINT.

        E.g.::

            with engine.begin() as connection:
                with connection.begin_nested():
                    connection.execute(table.insert(), {"username": "sandy"})

        The returned object is an instance of
        :class:`_engine.NestedTransaction`, which includes transactional
        methods :meth:`_engine.NestedTransaction.commit` and
        :meth:`_engine.NestedTransaction.rollback`; for a nested transaction,
        these methods correspond to the operations "RELEASE SAVEPOINT <name>"
        and "ROLLBACK TO SAVEPOINT <name>". The name of the savepoint is local
        to the :class:`_engine.NestedTransaction` object and is generated
        automatically. Like any other :class:`_engine.Transaction`, the
        :class:`_engine.NestedTransaction` may be used as a context manager as
        illustrated above which will "release" or "rollback" corresponding to
        if the operation within the block were successful or raised an
        exception.

        Nested transactions require SAVEPOINT support in the underlying
        database, else the behavior is undefined. SAVEPOINT is commonly used to
        run operations within a transaction that may fail, while continuing the
        outer transaction. E.g.::

            from sqlalchemy import exc

            with engine.begin() as connection:
                trans = connection.begin_nested()
                try:
                    connection.execute(table.insert(), {"username": "sandy"})
                    trans.commit()
                except exc.IntegrityError:  # catch for duplicate username
                    trans.rollback()  # rollback to savepoint

                # outer transaction continues
                connection.execute(...)

        If :meth:`_engine.Connection.begin_nested` is called without first
        calling :meth:`_engine.Connection.begin` or
        :meth:`_engine.Engine.begin`, the :class:`_engine.Connection` object
        will "autobegin" the outer transaction first. This outer transaction
        may be committed using "commit-as-you-go" style, e.g.::

            with engine.connect() as connection:  # begin() wasn't called

                with connection.begin_nested():  # will auto-"begin()" first
                    connection.execute(...)
                # savepoint is released

                connection.execute(...)

                # explicitly commit outer transaction
                connection.commit()

                # can continue working with connection here

        .. versionchanged:: 2.0

            :meth:`_engine.Connection.begin_nested` will now participate
            in the connection "autobegin" behavior that is new as of
            2.0 / "future" style connections in 1.4.

        .. seealso::

            :meth:`_engine.Connection.begin`

            :ref:`session_begin_nested` - ORM support for SAVEPOINT

        """
        if self._transaction is None:
            self._autobegin()

        return NestedTransaction(self)

    def begin_twophase(self, xid: Optional[Any] = None) -> TwoPhaseTransaction:
        """Begin a two-phase or XA transaction and return a transaction
        handle.

        The returned object is an instance of :class:`.TwoPhaseTransaction`,
        which in addition to the methods provided by
        :class:`.Transaction`, also provides a
        :meth:`~.TwoPhaseTransaction.prepare` method.

        :param xid: the two phase transaction id.  If not supplied, a
          random id will be generated.

        .. seealso::

            :meth:`_engine.Connection.begin`

            :meth:`_engine.Connection.begin_twophase`

        """

        if self._transaction is not None:
            raise exc.InvalidRequestError(
                "Cannot start a two phase transaction when a transaction "
                "is already in progress."
            )
        if xid is None:
            xid = self.engine.dialect.create_xid()
        return TwoPhaseTransaction(self, xid)

    def commit(self) -> None:
        """Commit the transaction that is currently in progress.

        This method commits the current transaction if one has been started.
        If no transaction was started, the method has no effect, assuming
        the connection is in a non-invalidated state.

        A transaction is begun on a :class:`_engine.Connection` automatically
        whenever a statement is first executed, or when the
        :meth:`_engine.Connection.begin` method is called.

        .. note:: The :meth:`_engine.Connection.commit` method only acts upon
          the primary database transaction that is linked to the
          :class:`_engine.Connection` object.  It does not operate upon a
          SAVEPOINT that would have been invoked from the
          :meth:`_engine.Connection.begin_nested` method; for control of a
          SAVEPOINT, call :meth:`_engine.NestedTransaction.commit` on the
          :class:`_engine.NestedTransaction` that is returned by the
          :meth:`_engine.Connection.begin_nested` method itself.


        """
        if self._transaction:
            self._transaction.commit()

    def rollback(self) -> None:
        """Roll back the transaction that is currently in progress.

