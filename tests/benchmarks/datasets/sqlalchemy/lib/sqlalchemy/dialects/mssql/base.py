# dialects/mssql/base.py
# Copyright (C) 2005-2026 the SQLAlchemy authors and contributors
# <see AUTHORS file>
#
# This module is part of SQLAlchemy and is released under
# the MIT License: https://www.opensource.org/licenses/mit-license.php
# mypy: ignore-errors

"""
.. dialect:: mssql
    :name: Microsoft SQL Server
    :normal_support: 2012+
    :best_effort: 2005+

.. _mssql_external_dialects:

External Dialects
-----------------

In addition to the above DBAPI layers with native SQLAlchemy support, there
are third-party dialects for other DBAPI layers that are compatible
with SQL Server. See the "External Dialects" list on the
:ref:`dialect_toplevel` page.

.. _mssql_identity:

Auto Increment Behavior / IDENTITY Columns
------------------------------------------

SQL Server provides so-called "auto incrementing" behavior using the
``IDENTITY`` construct, which can be placed on any single integer column in a
table. SQLAlchemy considers ``IDENTITY`` within its default "autoincrement"
behavior for an integer primary key column, described at
:paramref:`_schema.Column.autoincrement`.  This means that by default,
the first integer primary key column in a :class:`_schema.Table` will be
considered to be the identity column - unless it is associated with a
:class:`.Sequence` - and will generate DDL as such::

    from sqlalchemy import Table, MetaData, Column, Integer

    m = MetaData()
    t = Table(
        "t",
        m,
        Column("id", Integer, primary_key=True),
        Column("x", Integer),
    )
    m.create_all(engine)

The above example will generate DDL as:

.. sourcecode:: sql

    CREATE TABLE t (
        id INTEGER NOT NULL IDENTITY,
        x INTEGER NULL,
        PRIMARY KEY (id)
    )

For the case where this default generation of ``IDENTITY`` is not desired,
specify ``False`` for the :paramref:`_schema.Column.autoincrement` flag,
on the first integer primary key column::

    m = MetaData()
    t = Table(
        "t",
        m,
        Column("id", Integer, primary_key=True, autoincrement=False),
        Column("x", Integer),
    )
    m.create_all(engine)

To add the ``IDENTITY`` keyword to a non-primary key column, specify
``True`` for the :paramref:`_schema.Column.autoincrement` flag on the desired
:class:`_schema.Column` object, and ensure that
:paramref:`_schema.Column.autoincrement`
is set to ``False`` on any integer primary key column::

    m = MetaData()
    t = Table(
        "t",
        m,
        Column("id", Integer, primary_key=True, autoincrement=False),
        Column("x", Integer, autoincrement=True),
    )
    m.create_all(engine)

.. versionchanged::  1.4   Added :class:`_schema.Identity` construct
   in a :class:`_schema.Column` to specify the start and increment
   parameters of an IDENTITY. These replace
   the use of the :class:`.Sequence` object in order to specify these values.

.. deprecated:: 1.4

   The ``mssql_identity_start`` and ``mssql_identity_increment`` parameters
   to :class:`_schema.Column` are deprecated and should we replaced by
   an :class:`_schema.Identity` object. Specifying both ways of configuring
   an IDENTITY will result in a compile error.
   These options are also no longer returned as part of the
   ``dialect_options`` key in :meth:`_reflection.Inspector.get_columns`.
   Use the information in the ``identity`` key instead.

.. deprecated:: 1.3

   The use of :class:`.Sequence` to specify IDENTITY characteristics is
   deprecated and will be removed in a future release.   Please use
   the :class:`_schema.Identity` object parameters
   :paramref:`_schema.Identity.start` and
   :paramref:`_schema.Identity.increment`.

.. versionchanged::  1.4   Removed the ability to use a :class:`.Sequence`
   object to modify IDENTITY characteristics. :class:`.Sequence` objects
   now only manipulate true T-SQL SEQUENCE types.

.. note::

    There can only be one IDENTITY column on the table.  When using
    ``autoincrement=True`` to enable the IDENTITY keyword, SQLAlchemy does not
    guard against multiple columns specifying the option simultaneously.  The
    SQL Server database will instead reject the ``CREATE TABLE`` statement.

.. note::

    An INSERT statement which attempts to provide a value for a column that is
    marked with IDENTITY will be rejected by SQL Server.   In order for the
    value to be accepted, a session-level option "SET IDENTITY_INSERT" must be
    enabled.   The SQLAlchemy SQL Server dialect will perform this operation
    automatically when using a core :class:`_expression.Insert`
    construct; if the
    execution specifies a value for the IDENTITY column, the "IDENTITY_INSERT"
    option will be enabled for the span of that statement's invocation.However,
    this scenario is not high performing and should not be relied upon for
    normal use.   If a table doesn't actually require IDENTITY behavior in its
    integer primary key column, the keyword should be disabled when creating
    the table by ensuring that ``autoincrement=False`` is set.

Controlling "Start" and "Increment"
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Specific control over the "start" and "increment" values for
the ``IDENTITY`` generator are provided using the
:paramref:`_schema.Identity.start` and :paramref:`_schema.Identity.increment`
parameters passed to the :class:`_schema.Identity` object::

    from sqlalchemy import Table, Integer, Column, Identity

    test = Table(
        "test",
        metadata,
        Column(
            "id", Integer, primary_key=True, Identity(start=100, increment=10)
        ),
        Column("name", String(20)),
    )

The CREATE TABLE for the above :class:`_schema.Table` object would be:

.. sourcecode:: sql

   CREATE TABLE test (
     id INTEGER NOT NULL IDENTITY(100,10) PRIMARY KEY,
     name VARCHAR(20) NULL,
   )

.. note::

   The :class:`_schema.Identity` object supports many other parameter in
   addition to ``start`` and ``increment``. These are not supported by
   SQL Server and will be ignored when generating the CREATE TABLE ddl.

.. versionchanged:: 1.3.19  The :class:`_schema.Identity` object is
   now used to affect the
   ``IDENTITY`` generator for a :class:`_schema.Column` under  SQL Server.
   Previously, the :class:`.Sequence` object was used.  As SQL Server now
   supports real sequences as a separate construct, :class:`.Sequence` will be
   functional in the normal way starting from SQLAlchemy version 1.4.


Using IDENTITY with Non-Integer numeric types
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

SQL Server also allows ``IDENTITY`` to be used with ``NUMERIC`` columns.  To
implement this pattern smoothly in SQLAlchemy, the primary datatype of the
column should remain as ``Integer``, however the underlying implementation
type deployed to the SQL Server database can be specified as ``Numeric`` using
:meth:`.TypeEngine.with_variant`::

    from sqlalchemy import Column
    from sqlalchemy import Integer
    from sqlalchemy import Numeric
    from sqlalchemy import String
    from sqlalchemy.ext.declarative import declarative_base

    Base = declarative_base()


    class TestTable(Base):
        __tablename__ = "test"
        id = Column(
            Integer().with_variant(Numeric(10, 0), "mssql"),
            primary_key=True,
            autoincrement=True,
        )
        name = Column(String)

In the above example, ``Integer().with_variant()`` provides clear usage
information that accurately describes the intent of the code. The general
restriction that ``autoincrement`` only applies to ``Integer`` is established
at the metadata level and not at the per-dialect level.

When using the above pattern, the primary key identifier that comes back from
the insertion of a row, which is also the value that would be assigned to an
ORM object such as ``TestTable`` above, will be an instance of ``Decimal()``
and not ``int`` when using SQL Server. The numeric return type of the
:class:`_types.Numeric` type can be changed to return floats by passing False
to :paramref:`_types.Numeric.asdecimal`. To normalize the return type of the
above ``Numeric(10, 0)`` to return Python ints (which also support "long"
integer values in Python 3), use :class:`_types.TypeDecorator` as follows::

    from sqlalchemy import TypeDecorator


    class NumericAsInteger(TypeDecorator):
        "normalize floating point return values into ints"

        impl = Numeric(10, 0, asdecimal=False)
        cache_ok = True

        def process_result_value(self, value, dialect):
            if value is not None:
                value = int(value)
            return value


    class TestTable(Base):
        __tablename__ = "test"
        id = Column(
            Integer().with_variant(NumericAsInteger, "mssql"),
            primary_key=True,
            autoincrement=True,
        )
        name = Column(String)

.. _mssql_insert_behavior:

INSERT behavior
^^^^^^^^^^^^^^^^

Handling of the ``IDENTITY`` column at INSERT time involves two key
techniques. The most common is being able to fetch the "last inserted value"
for a given ``IDENTITY`` column, a process which SQLAlchemy performs
implicitly in many cases, most importantly within the ORM.

The process for fetching this value has several variants:

* In the vast majority of cases, RETURNING is used in conjunction with INSERT
  statements on SQL Server in order to get newly generated primary key values:

  .. sourcecode:: sql

    INSERT INTO t (x) OUTPUT inserted.id VALUES (?)

  As of SQLAlchemy 2.0, the :ref:`engine_insertmanyvalues` feature is also
  used by default to optimize many-row INSERT statements; for SQL Server
  the feature takes place for both RETURNING and-non RETURNING
  INSERT statements.

  .. versionchanged:: 2.0.10 The :ref:`engine_insertmanyvalues` feature for
     SQL Server was temporarily disabled for SQLAlchemy version 2.0.9 due to
     issues with row ordering. As of 2.0.10 the feature is re-enabled, with
     special case handling for the unit of work's requirement for RETURNING to
     be ordered.

* When RETURNING is not available or has been disabled via
  ``implicit_returning=False``, either the ``scope_identity()`` function or
  the ``@@identity`` variable is used; behavior varies by backend:

  * when using PyODBC, the phrase ``; select scope_identity()`` will be
    appended to the end of the INSERT statement; a second result set will be
    fetched in order to receive the value.  Given a table as::

        t = Table(
            "t",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("x", Integer),
            implicit_returning=False,
        )

    an INSERT will look like:

    .. sourcecode:: sql

        INSERT INTO t (x) VALUES (?); select scope_identity()

  * Other dialects such as pymssql will call upon
    ``SELECT scope_identity() AS lastrowid`` subsequent to an INSERT
    statement. If the flag ``use_scope_identity=False`` is passed to
    :func:`_sa.create_engine`,
    the statement ``SELECT @@identity AS lastrowid``
    is used instead.

A table that contains an ``IDENTITY`` column will prohibit an INSERT statement
that refers to the identity column explicitly.  The SQLAlchemy dialect will
detect when an INSERT construct, created using a core
:func:`_expression.insert`
construct (not a plain string SQL), refers to the identity column, and
in this case will emit ``SET IDENTITY_INSERT ON`` prior to the insert
statement proceeding, and ``SET IDENTITY_INSERT OFF`` subsequent to the
execution.  Given this example::

    m = MetaData()
    t = Table(
        "t", m, Column("id", Integer, primary_key=True), Column("x", Integer)
    )
    m.create_all(engine)

    with engine.begin() as conn:
        conn.execute(t.insert(), {"id": 1, "x": 1}, {"id": 2, "x": 2})

The above column will be created with IDENTITY, however the INSERT statement
we emit is specifying explicit values.  In the echo output we can see
how SQLAlchemy handles this:

.. sourcecode:: sql

    CREATE TABLE t (
        id INTEGER NOT NULL IDENTITY(1,1),
        x INTEGER NULL,
        PRIMARY KEY (id)
    )

    COMMIT
    SET IDENTITY_INSERT t ON
    INSERT INTO t (id, x) VALUES (?, ?)
    ((1, 1), (2, 2))
    SET IDENTITY_INSERT t OFF
    COMMIT



This is an auxiliary use case suitable for testing and bulk insert scenarios.

SEQUENCE support
----------------

The :class:`.Sequence` object creates "real" sequences, i.e.,
``CREATE SEQUENCE``:

.. sourcecode:: pycon+sql

    >>> from sqlalchemy import Sequence
    >>> from sqlalchemy.schema import CreateSequence
    >>> from sqlalchemy.dialects import mssql
    >>> print(
    ...     CreateSequence(Sequence("my_seq", start=1)).compile(
    ...         dialect=mssql.dialect()
    ...     )
    ... )
    {printsql}CREATE SEQUENCE my_seq START WITH 1

For integer primary key generation, SQL Server's ``IDENTITY`` construct should
generally be preferred vs. sequence.

.. tip::

    The default start value for T-SQL is ``-2**63`` instead of 1 as
    in most other SQL databases. Users should explicitly set the
    :paramref:`.Sequence.start` to 1 if that's the expected default::

        seq = Sequence("my_sequence", start=1)

.. versionadded:: 1.4 added SQL Server support for :class:`.Sequence`

.. versionchanged:: 2.0 The SQL Server dialect will no longer implicitly
   render "START WITH 1" for ``CREATE SEQUENCE``, which was the behavior
   first implemented in version 1.4.

MAX on VARCHAR / NVARCHAR
-------------------------

SQL Server supports the special string "MAX" within the
:class:`_types.VARCHAR` and :class:`_types.NVARCHAR` datatypes,
to indicate "maximum length possible".   The dialect currently handles this as
a length of "None" in the base type, rather than supplying a
dialect-specific version of these types, so that a base type
specified such as ``VARCHAR(None)`` can assume "unlengthed" behavior on
more than one backend without using dialect-specific types.

To build a SQL Server VARCHAR or NVARCHAR with MAX length, use None::

    my_table = Table(
        "my_table",
        metadata,
        Column("my_data", VARCHAR(None)),
        Column("my_n_data", NVARCHAR(None)),
    )

Collation Support
-----------------

Character collations are supported by the base string types,
specified by the string argument "collation"::

    from sqlalchemy import VARCHAR

    Column("login", VARCHAR(32, collation="Latin1_General_CI_AS"))

When such a column is associated with a :class:`_schema.Table`, the
CREATE TABLE statement for this column will yield:

.. sourcecode:: sql

    login VARCHAR(32) COLLATE Latin1_General_CI_AS NULL

LIMIT/OFFSET Support
--------------------

MSSQL has added support for LIMIT / OFFSET as of SQL Server 2012, via the
"OFFSET n ROWS" and "FETCH NEXT n ROWS" clauses.  SQLAlchemy supports these
syntaxes automatically if SQL Server 2012 or greater is detected.

.. versionchanged:: 1.4 support added for SQL Server "OFFSET n ROWS" and
   "FETCH NEXT n ROWS" syntax.

For statements that specify only LIMIT and no OFFSET, all versions of SQL
Server support the TOP keyword.   This syntax is used for all SQL Server
versions when no OFFSET clause is present.  A statement such as::

    select(some_table).limit(5)

will render similarly to:

.. sourcecode:: sql

    SELECT TOP 5 col1, col2.. FROM table

For versions of SQL Server prior to SQL Server 2012, a statement that uses
LIMIT and OFFSET, or just OFFSET alone, will be rendered using the
``ROW_NUMBER()`` window function.   A statement such as::

    select(some_table).order_by(some_table.c.col3).limit(5).offset(10)

will render similarly to:

.. sourcecode:: sql

    SELECT anon_1.col1, anon_1.col2 FROM (SELECT col1, col2,
    ROW_NUMBER() OVER (ORDER BY col3) AS
    mssql_rn FROM table WHERE t.x = :x_1) AS
    anon_1 WHERE mssql_rn > :param_1 AND mssql_rn <= :param_2 + :param_1

Note that when using LIMIT and/or OFFSET, whether using the older
or newer SQL Server syntaxes, the statement must have an ORDER BY as well,
else a :class:`.CompileError` is raised.

.. _mssql_comment_support:

DDL Comment Support
--------------------

Comment support, which includes DDL rendering for attributes such as
:paramref:`_schema.Table.comment` and :paramref:`_schema.Column.comment`, as
well as the ability to reflect these comments, is supported assuming a
supported version of SQL Server is in use. If a non-supported version such as
Azure Synapse is detected at first-connect time (based on the presence
of the ``fn_listextendedproperty`` SQL function), comment support including
rendering and table-comment reflection is disabled, as both features rely upon
SQL Server stored procedures and functions that are not available on all
backend types.

To force comment support to be on or off, bypassing autodetection, set the
parameter ``supports_comments`` within :func:`_sa.create_engine`::

    e = create_engine("mssql+pyodbc://u:p@dsn", supports_comments=False)

.. versionadded:: 2.0 Added support for table and column comments for
   the SQL Server dialect, including DDL generation and reflection.

.. _mssql_isolation_level:

Transaction Isolation Level
---------------------------

All SQL Server dialects support setting of transaction isolation level
both via a dialect-specific parameter
:paramref:`_sa.create_engine.isolation_level`
accepted by :func:`_sa.create_engine`,
as well as the :paramref:`.Connection.execution_options.isolation_level`
argument as passed to
:meth:`_engine.Connection.execution_options`.
This feature works by issuing the
command ``SET TRANSACTION ISOLATION LEVEL <level>`` for
each new connection.

To set isolation level using :func:`_sa.create_engine`::

    engine = create_engine(
        "mssql+pyodbc://scott:tiger@ms_2008", isolation_level="REPEATABLE READ"
    )

To set using per-connection execution options::

    connection = engine.connect()
    connection = connection.execution_options(isolation_level="READ COMMITTED")

Valid values for ``isolation_level`` include:

* ``AUTOCOMMIT`` - pyodbc / pymssql-specific
* ``READ COMMITTED``
* ``READ UNCOMMITTED``
* ``REPEATABLE READ``
* ``SERIALIZABLE``
* ``SNAPSHOT`` - specific to SQL Server

There are also more options for isolation level configurations, such as
"sub-engine" objects linked to a main :class:`_engine.Engine` which each apply
different isolation level settings.  See the discussion at
:ref:`dbapi_autocommit` for background.

.. seealso::

    :ref:`dbapi_autocommit`

.. _mssql_reset_on_return:

Temporary Table / Resource Reset for Connection Pooling
-------------------------------------------------------

The :class:`.QueuePool` connection pool implementation used
by the SQLAlchemy :class:`.Engine` object includes
:ref:`reset on return <pool_reset_on_return>` behavior that will invoke
the DBAPI ``.rollback()`` method when connections are returned to the pool.
While this rollback will clear out the immediate state used by the previous
transaction, it does not cover a wider range of session-level state, including
temporary tables as well as other server state such as prepared statement
handles and statement caches.   An undocumented SQL Server procedure known
as ``sp_reset_connection`` is known to be a workaround for this issue which
will reset most of the session state that builds up on a connection, including
temporary tables.

To install ``sp_reset_connection`` as the means of performing reset-on-return,
the :meth:`.PoolEvents.reset` event hook may be used, as demonstrated in the
example below. The :paramref:`_sa.create_engine.pool_reset_on_return` parameter
is set to ``None`` so that the custom scheme can replace the default behavior
completely.   The custom hook implementation calls ``.rollback()`` in any case,
as it's usually important that the DBAPI's own tracking of commit/rollback
will remain consistent with the state of the transaction::

    from sqlalchemy import create_engine
    from sqlalchemy import event

    mssql_engine = create_engine(
        "mssql+pyodbc://scott:tiger^5HHH@mssql2017:1433/test?driver=ODBC+Driver+17+for+SQL+Server",
        # disable default reset-on-return scheme
        pool_reset_on_return=None,
    )


    @event.listens_for(mssql_engine, "reset")
    def _reset_mssql(dbapi_connection, connection_record, reset_state):
        if not reset_state.terminate_only:
            dbapi_connection.execute("{call sys.sp_reset_connection}")

        # so that the DBAPI itself knows that the connection has been
        # reset
        dbapi_connection.rollback()

.. versionchanged:: 2.0.0b3  Added additional state arguments to
   the :meth:`.PoolEvents.reset` event and additionally ensured the event
   is invoked for all "reset" occurrences, so that it's appropriate
   as a place for custom "reset" handlers.   Previous schemes which
   use the :meth:`.PoolEvents.checkin` handler remain usable as well.

.. seealso::

    :ref:`pool_reset_on_return` - in the :ref:`pooling_toplevel` documentation

Nullability
-----------
MSSQL has support for three levels of column nullability. The default
nullability allows nulls and is explicit in the CREATE TABLE
construct:

.. sourcecode:: sql

    name VARCHAR(20) NULL

If ``nullable=None`` is specified then no specification is made. In
other words the database's configured default is used. This will
render:

.. sourcecode:: sql

    name VARCHAR(20)

If ``nullable`` is ``True`` or ``False`` then the column will be
``NULL`` or ``NOT NULL`` respectively.

Date / Time Handling
--------------------
DATE and TIME are supported.   Bind parameters are converted
to datetime.datetime() objects as required by most MSSQL drivers,
and results are processed from strings if needed.
The DATE and TIME types are not available for MSSQL 2005 and
previous - if a server version below 2008 is detected, DDL
for these types will be issued as DATETIME.

.. _mssql_large_type_deprecation:

Large Text/Binary Type Deprecation
----------------------------------

Per
`SQL Server 2012/2014 Documentation <https://technet.microsoft.com/en-us/library/ms187993.aspx>`_,
the ``NTEXT``, ``TEXT`` and ``IMAGE`` datatypes are to be removed from SQL
Server in a future release.   SQLAlchemy normally relates these types to the
:class:`.UnicodeText`, :class:`_expression.TextClause` and
:class:`.LargeBinary` datatypes.

In order to accommodate this change, a new flag ``deprecate_large_types``
is added to the dialect, which will be automatically set based on detection
of the server version in use, if not otherwise set by the user.  The
behavior of this flag is as follows:

* When this flag is ``True``, the :class:`.UnicodeText`,
  :class:`_expression.TextClause` and
  :class:`.LargeBinary` datatypes, when used to render DDL, will render the
  types ``NVARCHAR(max)``, ``VARCHAR(max)``, and ``VARBINARY(max)``,
  respectively.  This is a new behavior as of the addition of this flag.

* When this flag is ``False``, the :class:`.UnicodeText`,
  :class:`_expression.TextClause` and
  :class:`.LargeBinary` datatypes, when used to render DDL, will render the
  types ``NTEXT``, ``TEXT``, and ``IMAGE``,
  respectively.  This is the long-standing behavior of these types.

* The flag begins with the value ``None``, before a database connection is
  established.   If the dialect is used to render DDL without the flag being
  set, it is interpreted the same as ``False``.

* On first connection, the dialect detects if SQL Server version 2012 or
  greater is in use; if the flag is still at ``None``, it sets it to ``True``
  or ``False`` based on whether 2012 or greater is detected.

* The flag can be set to either ``True`` or ``False`` when the dialect
  is created, typically via :func:`_sa.create_engine`::

        eng = create_engine(
            "mssql+pymssql://user:pass@host/db", deprecate_large_types=True
        )

* Complete control over whether the "old" or "new" types are rendered is
  available in all SQLAlchemy versions by using the UPPERCASE type objects
  instead: :class:`_types.NVARCHAR`, :class:`_types.VARCHAR`,
  :class:`_types.VARBINARY`, :class:`_types.TEXT`, :class:`_mssql.NTEXT`,
  :class:`_mssql.IMAGE`
  will always remain fixed and always output exactly that
  type.

.. _multipart_schema_names:

Multipart Schema Names
----------------------

SQL Server schemas sometimes require multiple parts to their "schema"
qualifier, that is, including the database name and owner name as separate
tokens, such as ``mydatabase.dbo.some_table``. These multipart names can be set
at once using the :paramref:`_schema.Table.schema` argument of
:class:`_schema.Table`::

    Table(
        "some_table",
        metadata,
        Column("q", String(50)),
        schema="mydatabase.dbo",
    )

When performing operations such as table or component reflection, a schema
argument that contains a dot will be split into separate
"database" and "owner"  components in order to correctly query the SQL
Server information schema tables, as these two values are stored separately.
Additionally, when rendering the schema name for DDL or SQL, the two
components will be quoted separately for case sensitive names and other
special characters.   Given an argument as below::

    Table(
        "some_table",
        metadata,
        Column("q", String(50)),
        schema="MyDataBase.dbo",
    )

The above schema would be rendered as ``[MyDataBase].dbo``, and also in
reflection, would be reflected using "dbo" as the owner and "MyDataBase"
as the database name.

To control how the schema name is broken into database / owner,
specify brackets (which in SQL Server are quoting characters) in the name.
Below, the "owner" will be considered as ``MyDataBase.dbo`` and the
"database" will be None::

    Table(
        "some_table",
        metadata,
        Column("q", String(50)),
        schema="[MyDataBase.dbo]",
    )

To individually specify both database and owner name with special characters
or embedded dots, use two sets of brackets::

    Table(
        "some_table",
        metadata,
        Column("q", String(50)),
        schema="[MyDataBase.Period].[MyOwner.Dot]",
    )

.. versionchanged:: 1.2 the SQL Server dialect now treats brackets as
   identifier delimiters splitting the schema into separate database
   and owner tokens, to allow dots within either name itself.

.. _legacy_schema_rendering:

Legacy Schema Mode
------------------

Very old versions of the MSSQL dialect introduced the behavior such that a
schema-qualified table would be auto-aliased when used in a
SELECT statement; given a table::

    account_table = Table(
        "account",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("info", String(100)),
        schema="customer_schema",
    )

this legacy mode of rendering would assume that "customer_schema.account"
would not be accepted by all parts of the SQL statement, as illustrated
below:

.. sourcecode:: pycon+sql

    >>> eng = create_engine("mssql+pymssql://mydsn", legacy_schema_aliasing=True)
    >>> print(account_table.select().compile(eng))
    {printsql}SELECT account_1.id, account_1.info
    FROM customer_schema.account AS account_1

This mode of behavior is now off by default, as it appears to have served
no purpose; however in the case that legacy applications rely upon it,
it is available using the ``legacy_schema_aliasing`` argument to
:func:`_sa.create_engine` as illustrated above.

.. deprecated:: 1.4

   The ``legacy_schema_aliasing`` flag is now
   deprecated and will be removed in a future release.

.. _mssql_indexes:

Clustered Index Support
-----------------------

The MSSQL dialect supports clustered indexes (and primary keys) via the
``mssql_clustered`` option.  This option is available to :class:`.Index`,
:class:`.UniqueConstraint`. and :class:`.PrimaryKeyConstraint`.
For indexes this option can be combined with the ``mssql_columnstore`` one
to create a clustered columnstore index.

To generate a clustered index::

    Index("my_index", table.c.x, mssql_clustered=True)

which renders the index as ``CREATE CLUSTERED INDEX my_index ON table (x)``.

To generate a clustered primary key use::

    Table(
        "my_table",
        metadata,
        Column("x", ...),
        Column("y", ...),
        PrimaryKeyConstraint("x", "y", mssql_clustered=True),
    )

which will render the table, for example, as:

.. sourcecode:: sql

  CREATE TABLE my_table (
    x INTEGER NOT NULL,
    y INTEGER NOT NULL,
    PRIMARY KEY CLUSTERED (x, y)
  )

Similarly, we can generate a clustered unique constraint using::

    Table(
        "my_table",
        metadata,
        Column("x", ...),
        Column("y", ...),
        PrimaryKeyConstraint("x"),
        UniqueConstraint("y", mssql_clustered=True),
    )

To explicitly request a non-clustered primary key (for example, when
a separate clustered index is desired), use::

    Table(
        "my_table",
        metadata,
        Column("x", ...),
        Column("y", ...),
        PrimaryKeyConstraint("x", "y", mssql_clustered=False),
    )

which will render the table, for example, as:

.. sourcecode:: sql

  CREATE TABLE my_table (
    x INTEGER NOT NULL,
    y INTEGER NOT NULL,
    PRIMARY KEY NONCLUSTERED (x, y)
  )

Columnstore Index Support
-------------------------

The MSSQL dialect supports columnstore indexes via the ``mssql_columnstore``
option.  This option is available to :class:`.Index`. It be combined with
the ``mssql_clustered`` option to create a clustered columnstore index.

To generate a columnstore index::

    Index("my_index", table.c.x, mssql_columnstore=True)

which renders the index as ``CREATE COLUMNSTORE INDEX my_index ON table (x)``.

To generate a clustered columnstore index provide no columns::

    idx = Index("my_index", mssql_clustered=True, mssql_columnstore=True)
    # required to associate the index with the table
    table.append_constraint(idx)

the above renders the index as
``CREATE CLUSTERED COLUMNSTORE INDEX my_index ON table``.

.. versionadded:: 2.0.18

MSSQL-Specific Index Options
-----------------------------

In addition to clustering, the MSSQL dialect supports other special options
for :class:`.Index`.

INCLUDE
^^^^^^^

The ``mssql_include`` option renders INCLUDE(colname) for the given string
names::

    Index("my_index", table.c.x, mssql_include=["y"])

would render the index as ``CREATE INDEX my_index ON table (x) INCLUDE (y)``

.. _mssql_index_where:

Filtered Indexes
^^^^^^^^^^^^^^^^

The ``mssql_where`` option renders WHERE(condition) for the given string
names::

    Index("my_index", table.c.x, mssql_where=table.c.x > 10)

would render the index as ``CREATE INDEX my_index ON table (x) WHERE x > 10``.

.. versionadded:: 1.3.4

Index ordering
^^^^^^^^^^^^^^

Index ordering is available via functional expressions, such as::

    Index("my_index", table.c.x.desc())

would render the index as ``CREATE INDEX my_index ON table (x DESC)``

.. seealso::

    :ref:`schema_indexes_functional`

Compatibility Levels
--------------------
MSSQL supports the notion of setting compatibility levels at the
database level. This allows, for instance, to run a database that
is compatible with SQL2000 while running on a SQL2005 database
server. ``server_version_info`` will always return the database
server version information (in this case SQL2005) and not the
compatibility level information. Because of this, if running under
a backwards compatibility mode SQLAlchemy may attempt to use T-SQL
statements that are unable to be parsed by the database server.

.. _mssql_triggers:

Triggers
--------

SQLAlchemy by default uses OUTPUT INSERTED to get at newly
generated primary key values via IDENTITY columns or other
server side defaults.   MS-SQL does not
allow the usage of OUTPUT INSERTED on tables that have triggers.
To disable the usage of OUTPUT INSERTED on a per-table basis,
specify ``implicit_returning=False`` for each :class:`_schema.Table`
which has triggers::

    Table(
        "mytable",
        metadata,
        Column("id", Integer, primary_key=True),
        # ...,
        implicit_returning=False,
    )

Declarative form::

    class MyClass(Base):
        # ...
        __table_args__ = {"implicit_returning": False}

.. _mssql_rowcount_versioning:

Rowcount Support / ORM Versioning
---------------------------------

The SQL Server drivers may have limited ability to return the number
of rows updated from an UPDATE or DELETE statement.

As of this writing, the PyODBC driver is not able to return a rowcount when
OUTPUT INSERTED is used.    Previous versions of SQLAlchemy therefore had
limitations for features such as the "ORM Versioning" feature that relies upon
accurate rowcounts in order to match version numbers with matched rows.

SQLAlchemy 2.0 now retrieves the "rowcount" manually for these particular use
cases based on counting the rows that arrived back within RETURNING; so while
the driver still has this limitation, the ORM Versioning feature is no longer
impacted by it. As of SQLAlchemy 2.0.5, ORM versioning has been fully
re-enabled for the pyodbc driver.

.. versionchanged:: 2.0.5  ORM versioning support is restored for the pyodbc
   driver.  Previously, a warning would be emitted during ORM flush that
   versioning was not supported.


Enabling Snapshot Isolation
---------------------------

SQL Server has a default transaction
isolation mode that locks entire tables, and causes even mildly concurrent
applications to have long held locks and frequent deadlocks.
Enabling snapshot isolation for the database as a whole is recommended
for modern levels of concurrency support.  This is accomplished via the
following ALTER DATABASE commands executed at the SQL prompt:

.. sourcecode:: sql

    ALTER DATABASE MyDatabase SET ALLOW_SNAPSHOT_ISOLATION ON

    ALTER DATABASE MyDatabase SET READ_COMMITTED_SNAPSHOT ON

Background on SQL Server snapshot isolation is available at
https://msdn.microsoft.com/en-us/library/ms175095.aspx.

"""  # noqa

from __future__ import annotations

import codecs
import datetime
import operator
import re
from typing import Any
from typing import overload
from typing import TYPE_CHECKING
from uuid import UUID as _python_UUID

from . import information_schema as ischema
from .json import JSON
from .json import JSONIndexType
from .json import JSONPathType
from ... import exc
from ... import Identity
from ... import schema as sa_schema
from ... import Sequence
from ... import sql
