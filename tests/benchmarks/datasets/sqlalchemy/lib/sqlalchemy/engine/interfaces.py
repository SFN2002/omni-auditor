# engine/interfaces.py
# Copyright (C) 2005-2026 the SQLAlchemy authors and contributors
# <see AUTHORS file>
#
# This module is part of SQLAlchemy and is released under
# the MIT License: https://www.opensource.org/licenses/mit-license.php

"""Define core interfaces used by the engine system."""

from __future__ import annotations

from enum import Enum
from typing import Any
from typing import Awaitable
from typing import Callable
from typing import ClassVar
from typing import Collection
from typing import Dict
from typing import Iterable
from typing import Iterator
from typing import List
from typing import Mapping
from typing import MutableMapping
from typing import Optional
from typing import Sequence
from typing import Set
from typing import Tuple
from typing import Type
from typing import TYPE_CHECKING
from typing import TypeVar
from typing import Union

from .. import util
from ..event import EventTarget
from ..pool import Pool
from ..pool import PoolProxiedConnection as PoolProxiedConnection
from ..sql.compiler import Compiled as Compiled
from ..sql.compiler import Compiled  # noqa
from ..sql.compiler import TypeCompiler as TypeCompiler
from ..sql.compiler import TypeCompiler  # noqa
from ..util import immutabledict
from ..util.concurrency import await_only
from ..util.typing import Literal
from ..util.typing import NotRequired
from ..util.typing import Protocol
from ..util.typing import TypedDict

if TYPE_CHECKING:
    from .base import Connection
    from .base import Engine
    from .cursor import CursorResult
    from .url import URL
    from ..connectors.asyncio import AsyncIODBAPIConnection
    from ..event import _ListenerFnType
    from ..event import dispatcher
    from ..exc import StatementError
    from ..sql import Executable
    from ..sql.compiler import _InsertManyValuesBatch
    from ..sql.compiler import DDLCompiler
    from ..sql.compiler import IdentifierPreparer
    from ..sql.compiler import InsertmanyvaluesSentinelOpts
    from ..sql.compiler import Linting
    from ..sql.compiler import SQLCompiler
    from ..sql.elements import BindParameter
    from ..sql.elements import ClauseElement
    from ..sql.schema import Column
    from ..sql.schema import DefaultGenerator
    from ..sql.schema import SchemaItem
    from ..sql.schema import Sequence as Sequence_SchemaItem
    from ..sql.sqltypes import Integer
    from ..sql.type_api import _TypeMemoDict
    from ..sql.type_api import TypeEngine
    from ..util.langhelpers import generic_fn_descriptor

ConnectArgsType = Tuple[Sequence[str], MutableMapping[str, Any]]

_T = TypeVar("_T", bound="Any")


class CacheStats(Enum):
    CACHE_HIT = 0
    CACHE_MISS = 1
    CACHING_DISABLED = 2
    NO_CACHE_KEY = 3
    NO_DIALECT_SUPPORT = 4


class ExecuteStyle(Enum):
    """indicates the :term:`DBAPI` cursor method that will be used to invoke
    a statement."""

    EXECUTE = 0
    """indicates cursor.execute() will be used"""

    EXECUTEMANY = 1
    """indicates cursor.executemany() will be used."""

    INSERTMANYVALUES = 2
    """indicates cursor.execute() will be used with an INSERT where the
    VALUES expression will be expanded to accommodate for multiple
    parameter sets

    .. seealso::

        :ref:`engine_insertmanyvalues`

    """


class DBAPIModule(Protocol):
    class Error(Exception):
        def __getattr__(self, key: str) -> Any: ...

    class OperationalError(Error):
        pass

    class InterfaceError(Error):
        pass

    class IntegrityError(Error):
        pass

    def __getattr__(self, key: str) -> Any: ...


class DBAPIConnection(Protocol):
    """protocol representing a :pep:`249` database connection.

    .. versionadded:: 2.0

    .. seealso::

        `Connection Objects <https://www.python.org/dev/peps/pep-0249/#connection-objects>`_
        - in :pep:`249`

    """  # noqa: E501

    def close(self) -> None: ...

    def commit(self) -> None: ...

    def cursor(self, *args: Any, **kwargs: Any) -> DBAPICursor: ...

    def rollback(self) -> None: ...

    def __getattr__(self, key: str) -> Any: ...

    def __setattr__(self, key: str, value: Any) -> None: ...


class DBAPIType(Protocol):
    """protocol representing a :pep:`249` database type.

    .. versionadded:: 2.0

    .. seealso::

        `Type Objects <https://www.python.org/dev/peps/pep-0249/#type-objects>`_
        - in :pep:`249`

    """  # noqa: E501


class DBAPICursor(Protocol):
    """protocol representing a :pep:`249` database cursor.

    .. versionadded:: 2.0

    .. seealso::

        `Cursor Objects <https://www.python.org/dev/peps/pep-0249/#cursor-objects>`_
        - in :pep:`249`

    """  # noqa: E501

    @property
    def description(
        self,
    ) -> _DBAPICursorDescription:
        """The description attribute of the Cursor.

        .. seealso::

            `cursor.description <https://www.python.org/dev/peps/pep-0249/#description>`_
            - in :pep:`249`


        """  # noqa: E501
        ...

    @property
    def rowcount(self) -> int: ...

    arraysize: int

    lastrowid: int

    def close(self) -> None: ...

    def execute(
        self,
        operation: Any,
        parameters: Optional[_DBAPISingleExecuteParams] = None,
    ) -> Any: ...

    def executemany(
        self,
        operation: Any,
        parameters: _DBAPIMultiExecuteParams,
    ) -> Any: ...

    def fetchone(self) -> Optional[Any]: ...

    def fetchmany(self, size: int = ...) -> Sequence[Any]: ...

    def fetchall(self) -> Sequence[Any]: ...

    def setinputsizes(self, sizes: Sequence[Any]) -> None: ...

    def setoutputsize(self, size: Any, column: Any) -> None: ...

    def callproc(
        self, procname: str, parameters: Sequence[Any] = ...
    ) -> Any: ...

    def nextset(self) -> Optional[bool]: ...

    def __getattr__(self, key: str) -> Any: ...


_CoreSingleExecuteParams = Mapping[str, Any]
_MutableCoreSingleExecuteParams = MutableMapping[str, Any]
_CoreMultiExecuteParams = Sequence[_CoreSingleExecuteParams]
_CoreAnyExecuteParams = Union[
    _CoreMultiExecuteParams, _CoreSingleExecuteParams
]

_DBAPISingleExecuteParams = Union[Sequence[Any], _CoreSingleExecuteParams]

_DBAPIMultiExecuteParams = Union[
    Sequence[Sequence[Any]], _CoreMultiExecuteParams
]
_DBAPIAnyExecuteParams = Union[
    _DBAPIMultiExecuteParams, _DBAPISingleExecuteParams
]
_DBAPICursorDescription = Sequence[
    Tuple[
        str,
        "DBAPIType",
        Optional[int],
        Optional[int],
        Optional[int],
        Optional[int],
        Optional[bool],
    ]
]

_AnySingleExecuteParams = _DBAPISingleExecuteParams
_AnyMultiExecuteParams = _DBAPIMultiExecuteParams
_AnyExecuteParams = _DBAPIAnyExecuteParams

CompiledCacheType = MutableMapping[Any, "Compiled"]
SchemaTranslateMapType = Mapping[Optional[str], Optional[str]]

_ImmutableExecuteOptions = immutabledict[str, Any]

_ParamStyle = Literal[
    "qmark", "numeric", "named", "format", "pyformat", "numeric_dollar"
]

_GenericSetInputSizesType = List[Tuple[str, Any, "TypeEngine[Any]"]]

IsolationLevel = Literal[
    "SERIALIZABLE",
    "REPEATABLE READ",
    "READ COMMITTED",
    "READ UNCOMMITTED",
    "AUTOCOMMIT",
]


class _CoreKnownExecutionOptions(TypedDict, total=False):
    compiled_cache: Optional[CompiledCacheType]
    logging_token: str
    isolation_level: IsolationLevel
    no_parameters: bool
    stream_results: bool
    max_row_buffer: int
    yield_per: int
    insertmanyvalues_page_size: int
    schema_translate_map: Optional[SchemaTranslateMapType]
    preserve_rowcount: bool


_ExecuteOptions = immutabledict[str, Any]
CoreExecuteOptionsParameter = Union[
    _CoreKnownExecutionOptions, Mapping[str, Any]
]


class ReflectedIdentity(TypedDict):
    """represent the reflected IDENTITY structure of a column, corresponding
    to the :class:`_schema.Identity` construct.

    The :class:`.ReflectedIdentity` structure is part of the
    :class:`.ReflectedColumn` structure, which is returned by the
    :meth:`.Inspector.get_columns` method.

    """

    always: bool
    """type of identity column"""

    on_null: bool
    """indicates ON NULL"""

    start: int
    """starting index of the sequence"""

    increment: int
    """increment value of the sequence"""

    minvalue: int
    """the minimum value of the sequence."""

    maxvalue: int
    """the maximum value of the sequence."""

    nominvalue: bool
    """no minimum value of the sequence."""

    nomaxvalue: bool
    """no maximum value of the sequence."""

    cycle: bool
    """allows the sequence to wrap around when the maxvalue
    or minvalue has been reached."""

    cache: Optional[int]
    """number of future values in the
    sequence which are calculated in advance."""

    order: bool
    """if true, renders the ORDER keyword."""


class ReflectedComputed(TypedDict):
    """Represent the reflected elements of a computed column, corresponding
    to the :class:`_schema.Computed` construct.

    The :class:`.ReflectedComputed` structure is part of the
    :class:`.ReflectedColumn` structure, which is returned by the
    :meth:`.Inspector.get_columns` method.

    """

    sqltext: str
    """the expression used to generate this column returned
    as a string SQL expression"""

    persisted: NotRequired[bool]
    """indicates if the value is stored in the table or computed on demand"""


class ReflectedColumn(TypedDict):
    """Dictionary representing the reflected elements corresponding to
    a :class:`_schema.Column` object.

    The :class:`.ReflectedColumn` structure is returned by the
    :class:`.Inspector.get_columns` method.

    """

    name: str
    """column name"""

    type: TypeEngine[Any]
    """column type represented as a :class:`.TypeEngine` instance."""

    nullable: bool
    """boolean flag if the column is NULL or NOT NULL"""

    default: Optional[str]
    """column default expression as a SQL string"""

    autoincrement: NotRequired[bool]
    """database-dependent autoincrement flag.

    This flag indicates if the column has a database-side "autoincrement"
    flag of some kind.   Within SQLAlchemy, other kinds of columns may
    also act as an "autoincrement" column without necessarily having
    such a flag on them.

    See :paramref:`_schema.Column.autoincrement` for more background on
    "autoincrement".

    """

    comment: NotRequired[Optional[str]]
    """comment for the column, if present.
    Only some dialects return this key
    """

    computed: NotRequired[ReflectedComputed]
    """indicates that this column is computed by the database.
    Only some dialects return this key.

    .. versionadded:: 1.3.16 - added support for computed reflection.
    """

    identity: NotRequired[ReflectedIdentity]
    """indicates this column is an IDENTITY column.
    Only some dialects return this key.

    .. versionadded:: 1.4 - added support for identity column reflection.
    """

    dialect_options: NotRequired[Dict[str, Any]]
    """Additional dialect-specific options detected for this reflected
    object"""


class ReflectedConstraint(TypedDict):
    """Dictionary representing the reflected elements corresponding to
    :class:`.Constraint`

    A base class for all constraints
    """

    name: Optional[str]
    """constraint name"""

    comment: NotRequired[Optional[str]]
    """comment for the constraint, if present"""


class ReflectedCheckConstraint(ReflectedConstraint):
    """Dictionary representing the reflected elements corresponding to
    :class:`.CheckConstraint`.

    The :class:`.ReflectedCheckConstraint` structure is returned by the
    :meth:`.Inspector.get_check_constraints` method.

    """

    sqltext: str
    """the check constraint's SQL expression"""

    dialect_options: NotRequired[Dict[str, Any]]
    """Additional dialect-specific options detected for this check constraint

    .. versionadded:: 1.3.8
    """


class ReflectedUniqueConstraint(ReflectedConstraint):
    """Dictionary representing the reflected elements corresponding to
    :class:`.UniqueConstraint`.

    The :class:`.ReflectedUniqueConstraint` structure is returned by the
    :meth:`.Inspector.get_unique_constraints` method.

    """

    column_names: List[str]
    """column names which comprise the unique constraint"""

    duplicates_index: NotRequired[Optional[str]]
    "Indicates if this unique constraint duplicates an index with this name"

    dialect_options: NotRequired[Dict[str, Any]]
    """Additional dialect-specific options detected for this unique
    constraint"""


class ReflectedPrimaryKeyConstraint(ReflectedConstraint):
    """Dictionary representing the reflected elements corresponding to
    :class:`.PrimaryKeyConstraint`.

    The :class:`.ReflectedPrimaryKeyConstraint` structure is returned by the
    :meth:`.Inspector.get_pk_constraint` method.

    """

    constrained_columns: List[str]
    """column names which comprise the primary key"""

    dialect_options: NotRequired[Dict[str, Any]]
    """Additional dialect-specific options detected for this primary key"""


class ReflectedForeignKeyConstraint(ReflectedConstraint):
    """Dictionary representing the reflected elements corresponding to
    :class:`.ForeignKeyConstraint`.

    The :class:`.ReflectedForeignKeyConstraint` structure is returned by
    the :meth:`.Inspector.get_foreign_keys` method.

    """

    constrained_columns: List[str]
    """local column names which comprise the foreign key"""

    referred_schema: Optional[str]
    """schema name of the table being referred"""

    referred_table: str
    """name of the table being referred"""

    referred_columns: List[str]
    """referred column names that correspond to ``constrained_columns``"""

    options: NotRequired[Dict[str, Any]]
    """Additional options detected for this foreign key constraint"""


class ReflectedIndex(TypedDict):
    """Dictionary representing the reflected elements corresponding to
    :class:`.Index`.

    The :class:`.ReflectedIndex` structure is returned by the
    :meth:`.Inspector.get_indexes` method.

    """

    name: Optional[str]
    """index name"""

    column_names: List[Optional[str]]
    """column names which the index references.
    An element of this list is ``None`` if it's an expression and is
    returned in the ``expressions`` list.
    """

    expressions: NotRequired[List[str]]
    """Expressions that compose the index. This list, when present, contains
    both plain column names (that are also in ``column_names``) and
    expressions (that are ``None`` in ``column_names``).
    """

    unique: bool
    """whether or not the index has a unique flag"""

    duplicates_constraint: NotRequired[Optional[str]]
    "Indicates if this index mirrors a constraint with this name"

    include_columns: NotRequired[List[str]]
    """columns to include in the INCLUDE clause for supporting databases.

    .. deprecated:: 2.0

        Legacy value, will be replaced with
        ``index_dict["dialect_options"]["<dialect name>_include"]``

    """

    column_sorting: NotRequired[Dict[str, Tuple[str]]]
    """optional dict mapping column names or expressions to tuple of sort
    keywords, which may include ``asc``, ``desc``, ``nulls_first``,
    ``nulls_last``.

    .. versionadded:: 1.3.5
    """

    dialect_options: NotRequired[Dict[str, Any]]
    """Additional dialect-specific options detected for this index"""


class ReflectedTableComment(TypedDict):
    """Dictionary representing the reflected comment corresponding to
    the :attr:`_schema.Table.comment` attribute.

    The :class:`.ReflectedTableComment` structure is returned by the
    :meth:`.Inspector.get_table_comment` method.

    """

    text: Optional[str]
    """text of the comment"""


class BindTyping(Enum):
    """Define different methods of passing typing information for
    bound parameters in a statement to the database driver.

    .. versionadded:: 2.0

    """

    NONE = 1
    """No steps are taken to pass typing information to the database driver.

    This is the default behavior for databases such as SQLite, MySQL / MariaDB,
    SQL Server.

    """

    SETINPUTSIZES = 2
    """Use the pep-249 setinputsizes method.

    This is only implemented for DBAPIs that support this method and for which
    the SQLAlchemy dialect has the appropriate infrastructure for that dialect
    set up.  Current dialects include python-oracledb, cx_Oracle as well as
    optional support for SQL Server using pyodbc.

    When using setinputsizes, dialects also have a means of only using the
    method for certain datatypes using include/exclude lists.

    When SETINPUTSIZES is used, the :meth:`.Dialect.do_set_input_sizes` method
    is called for each statement executed which has bound parameters.

    """

    RENDER_CASTS = 3
    """Render casts or other directives in the SQL string.

    This method is used for all PostgreSQL dialects, including asyncpg,
    pg8000, psycopg, psycopg2.   Dialects which implement this can choose
    which kinds of datatypes are explicitly cast in SQL statements and which
    aren't.

    When RENDER_CASTS is used, the compiler will invoke the
    :meth:`.SQLCompiler.render_bind_cast` method for the rendered
    string representation of each :class:`.BindParameter` object whose
    dialect-level type sets the :attr:`.TypeEngine.render_bind_cast` attribute.

    The :meth:`.SQLCompiler.render_bind_cast` is also used to render casts
    for one form of "insertmanyvalues" query, when both
    :attr:`.InsertmanyvaluesSentinelOpts.USE_INSERT_FROM_SELECT` and
    :attr:`.InsertmanyvaluesSentinelOpts.RENDER_SELECT_COL_CASTS` are set,
    where the casts are applied to the intermediary columns e.g.
    "INSERT INTO t (a, b, c) SELECT p0::TYP, p1::TYP, p2::TYP "
    "FROM (VALUES (?, ?), (?, ?), ...)".

    .. versionadded:: 2.0.10 - :meth:`.SQLCompiler.render_bind_cast` is now
       used within some elements of the "insertmanyvalues" implementation.


    """


VersionInfoType = Tuple[Union[int, str], ...]
TableKey = Tuple[Optional[str], str]


class Dialect(EventTarget):
    """Define the behavior of a specific database and DB-API combination.

    Any aspect of metadata definition, SQL query generation,
    execution, result-set handling, or anything else which varies
    between databases is defined under the general category of the
    Dialect.  The Dialect acts as a factory for other
    database-specific object implementations including
    ExecutionContext, Compiled, DefaultGenerator, and TypeEngine.

    .. note:: Third party dialects should not subclass :class:`.Dialect`
       directly.  Instead, subclass :class:`.default.DefaultDialect` or
       descendant class.

    """

    CACHE_HIT = CacheStats.CACHE_HIT
    CACHE_MISS = CacheStats.CACHE_MISS
    CACHING_DISABLED = CacheStats.CACHING_DISABLED
    NO_CACHE_KEY = CacheStats.NO_CACHE_KEY
    NO_DIALECT_SUPPORT = CacheStats.NO_DIALECT_SUPPORT

    dispatch: dispatcher[Dialect]

    name: str
    """identifying name for the dialect from a DBAPI-neutral point of view
      (i.e. 'sqlite')
    """

    driver: str
    """identifying name for the dialect's DBAPI"""

    dialect_description: str

    dbapi: Optional[DBAPIModule]
    """A reference to the DBAPI module object itself.

    SQLAlchemy dialects import DBAPI modules using the classmethod
    :meth:`.Dialect.import_dbapi`. The rationale is so that any dialect
    module can be imported and used to generate SQL statements without the
    need for the actual DBAPI driver to be installed.  Only when an
    :class:`.Engine` is constructed using :func:`.create_engine` does the
    DBAPI get imported; at that point, the creation process will assign
    the DBAPI module to this attribute.

    Dialects should therefore implement :meth:`.Dialect.import_dbapi`
    which will import the necessary module and return it, and then refer
    to ``self.dbapi`` in dialect code in order to refer to the DBAPI module
    contents.

    .. versionchanged:: The :attr:`.Dialect.dbapi` attribute is exclusively
       used as the per-:class:`.Dialect`-instance reference to the DBAPI
       module.   The previous not-fully-documented ``.Dialect.dbapi()``
       classmethod is deprecated and replaced by :meth:`.Dialect.import_dbapi`.

    """

    @util.non_memoized_property
    def loaded_dbapi(self) -> DBAPIModule:
        """same as .dbapi, but is never None; will raise an error if no
        DBAPI was set up.

        .. versionadded:: 2.0

        """
        raise NotImplementedError()

    positional: bool
    """True if the paramstyle for this Dialect is positional."""

    paramstyle: str
    """the paramstyle to be used (some DB-APIs support multiple
      paramstyles).
    """

    compiler_linting: Linting

    statement_compiler: Type[SQLCompiler]
    """a :class:`.Compiled` class used to compile SQL statements"""

    ddl_compiler: Type[DDLCompiler]
    """a :class:`.Compiled` class used to compile DDL statements"""

    type_compiler_cls: ClassVar[Type[TypeCompiler]]
    """a :class:`.Compiled` class used to compile SQL type objects

    .. versionadded:: 2.0

    """

    type_compiler_instance: TypeCompiler
    """instance of a :class:`.Compiled` class used to compile SQL type
    objects

    .. versionadded:: 2.0

    """

    type_compiler: Any
    """legacy; this is a TypeCompiler class at the class level, a
    TypeCompiler instance at the instance level.

    Refer to type_compiler_instance instead.

    """

    preparer: Type[IdentifierPreparer]
    """a :class:`.IdentifierPreparer` class used to
    quote identifiers.
    """

    identifier_preparer: IdentifierPreparer
    """This element will refer to an instance of :class:`.IdentifierPreparer`
    once a :class:`.DefaultDialect` has been constructed.

    """

    server_version_info: Optional[Tuple[Any, ...]]
    """a tuple containing a version number for the DB backend in use.

    This value is only available for supporting dialects, and is
    typically populated during the initial connection to the database.
    """

    default_schema_name: Optional[str]
    """the name of the default schema.  This value is only available for
    supporting dialects, and is typically populated during the
    initial connection to the database.

    """

    # NOTE: this does not take into effect engine-level isolation level.
    # not clear if this should be changed, seems like it should
    default_isolation_level: Optional[IsolationLevel]
    """the isolation that is implicitly present on new connections"""

    skip_autocommit_rollback: bool
    """Whether or not the :paramref:`.create_engine.skip_autocommit_rollback`
    parameter was set.

    .. versionadded:: 2.0.43

    """

    # create_engine()  -> isolation_level  currently goes here
    _on_connect_isolation_level: Optional[IsolationLevel]

    execution_ctx_cls: Type[ExecutionContext]
    """a :class:`.ExecutionContext` class used to handle statement execution"""

    execute_sequence_format: Union[
        Type[Tuple[Any, ...]], Type[Tuple[List[Any]]]
    ]
    """either the 'tuple' or 'list' type, depending on what cursor.execute()
    accepts for the second argument (they vary)."""

    supports_alter: bool
    """``True`` if the database supports ``ALTER TABLE`` - used only for
    generating foreign key constraints in certain circumstances
    """

    max_identifier_length: int
    """The maximum length of identifier names."""
    max_index_name_length: Optional[int]
    """The maximum length of index names if different from
    ``max_identifier_length``."""
    max_constraint_name_length: Optional[int]
    """The maximum length of constraint names if different from
    ``max_identifier_length``."""

    supports_server_side_cursors: Union[generic_fn_descriptor[bool], bool]
    """indicates if the dialect supports server side cursors"""

    server_side_cursors: bool
    """deprecated; indicates if the dialect should attempt to use server
    side cursors by default"""

    supports_sane_rowcount: bool
    """Indicate whether the dialect properly implements rowcount for
      ``UPDATE`` and ``DELETE`` statements.
    """

    supports_sane_multi_rowcount: bool
    """Indicate whether the dialect properly implements rowcount for
      ``UPDATE`` and ``DELETE`` statements when executed via
      executemany.
    """

    supports_empty_insert: bool
    """dialect supports INSERT () VALUES (), i.e. a plain INSERT with no
    columns in it.

    This is not usually supported; an "empty" insert is typically
    suited using either "INSERT..DEFAULT VALUES" or
    "INSERT ... (col) VALUES (DEFAULT)".

    """

    supports_default_values: bool
    """dialect supports INSERT... DEFAULT VALUES syntax"""

    supports_default_metavalue: bool
    """dialect supports INSERT...(col) VALUES (DEFAULT) syntax.

    Most databases support this in some way, e.g. SQLite supports it using
    ``VALUES (NULL)``.    MS SQL Server supports the syntax also however
    is the only included dialect where we have this disabled, as
    MSSQL does not support the field for the IDENTITY column, which is
    usually where we like to make use of the feature.

    """

    default_metavalue_token: str = "DEFAULT"
    """for INSERT... VALUES (DEFAULT) syntax, the token to put in the
    parenthesis.

    E.g. for SQLite this is the keyword "NULL".

    """

    supports_multivalues_insert: bool
    """Target database supports INSERT...VALUES with multiple value
    sets, i.e. INSERT INTO table (cols) VALUES (...), (...), (...), ...

    """

    insert_executemany_returning: bool
    """dialect / driver / database supports some means of providing
    INSERT...RETURNING support when dialect.do_executemany() is used.

    """

    insert_executemany_returning_sort_by_parameter_order: bool
    """dialect / driver / database supports some means of providing
    INSERT...RETURNING support when dialect.do_executemany() is used
    along with the :paramref:`_dml.Insert.returning.sort_by_parameter_order`
    parameter being set.

    """

    update_executemany_returning: bool
    """dialect supports UPDATE..RETURNING with executemany."""

    delete_executemany_returning: bool
    """dialect supports DELETE..RETURNING with executemany."""

    use_insertmanyvalues: bool
    """if True, indicates "insertmanyvalues" functionality should be used
    to allow for ``insert_executemany_returning`` behavior, if possible.

    In practice, setting this to True means:

    if ``supports_multivalues_insert``, ``insert_returning`` and
    ``use_insertmanyvalues`` are all True, the SQL compiler will produce
    an INSERT that will be interpreted by the :class:`.DefaultDialect`
    as an :attr:`.ExecuteStyle.INSERTMANYVALUES` execution that allows
    for INSERT of many rows with RETURNING by rewriting a single-row
    INSERT statement to have multiple VALUES clauses, also executing
    the statement multiple times for a series of batches when large numbers
    of rows are given.

    The parameter is False for the default dialect, and is set to True for
    SQLAlchemy internal dialects SQLite, MySQL/MariaDB, PostgreSQL, SQL Server.
    It remains at False for Oracle Database, which provides native "executemany
    with RETURNING" support and also does not support
    ``supports_multivalues_insert``.  For MySQL/MariaDB, those MySQL dialects
    that don't support RETURNING will not report
    ``insert_executemany_returning`` as True.

    .. versionadded:: 2.0

    .. seealso::

        :ref:`engine_insertmanyvalues`

    """

    use_insertmanyvalues_wo_returning: bool
    """if True, and use_insertmanyvalues is also True, INSERT statements
    that don't include RETURNING will also use "insertmanyvalues".

    .. versionadded:: 2.0

    .. seealso::

        :ref:`engine_insertmanyvalues`

    """

    insertmanyvalues_implicit_sentinel: InsertmanyvaluesSentinelOpts
    """Options indicating the database supports a form of bulk INSERT where
    the autoincrement integer primary key can be reliably used as an ordering
    for INSERTed rows.

    .. versionadded:: 2.0.10

    .. seealso::

        :ref:`engine_insertmanyvalues_returning_order`

    """

    insertmanyvalues_page_size: int
    """Number of rows to render into an individual INSERT..VALUES() statement
    for :attr:`.ExecuteStyle.INSERTMANYVALUES` executions.

    The default dialect defaults this to 1000.

    .. versionadded:: 2.0

    .. seealso::

        :paramref:`_engine.Connection.execution_options.insertmanyvalues_page_size` -
        execution option available on :class:`_engine.Connection`, statements

    """  # noqa: E501

    insertmanyvalues_max_parameters: int
    """Alternate to insertmanyvalues_page_size, will additionally limit
    page size based on number of parameters total in the statement.


    """

    preexecute_autoincrement_sequences: bool
    """True if 'implicit' primary key functions must be executed separately
      in order to get their value, if RETURNING is not used.

      This is currently oriented towards PostgreSQL when the
      ``implicit_returning=False`` parameter is used on a :class:`.Table`
      object.

    """

    insert_returning: bool
    """if the dialect supports RETURNING with INSERT

    .. versionadded:: 2.0

    """

    update_returning: bool
    """if the dialect supports RETURNING with UPDATE

    .. versionadded:: 2.0

    """

    update_returning_multifrom: bool
    """if the dialect supports RETURNING with UPDATE..FROM

    .. versionadded:: 2.0

    """

