# dialects/sqlite/base.py
# Copyright (C) 2005-2026 the SQLAlchemy authors and contributors
# <see AUTHORS file>
#
# This module is part of SQLAlchemy and is released under
# the MIT License: https://www.opensource.org/licenses/mit-license.php
# mypy: ignore-errors


r'''
.. dialect:: sqlite
    :name: SQLite
    :normal_support: 3.12+
    :best_effort: 3.7.16+

.. _sqlite_datetime:

Date and Time Types
-------------------

SQLite does not have built-in DATE, TIME, or DATETIME types, and pysqlite does
not provide out of the box functionality for translating values between Python
`datetime` objects and a SQLite-supported format. SQLAlchemy's own
:class:`~sqlalchemy.types.DateTime` and related types provide date formatting
and parsing functionality when SQLite is used. The implementation classes are
:class:`_sqlite.DATETIME`, :class:`_sqlite.DATE` and :class:`_sqlite.TIME`.
These types represent dates and times as ISO formatted strings, which also
nicely support ordering. There's no reliance on typical "libc" internals for
these functions so historical dates are fully supported.

Ensuring Text affinity
^^^^^^^^^^^^^^^^^^^^^^

The DDL rendered for these types is the standard ``DATE``, ``TIME``
and ``DATETIME`` indicators.    However, custom storage formats can also be
applied to these types.   When the
storage format is detected as containing no alpha characters, the DDL for
these types is rendered as ``DATE_CHAR``, ``TIME_CHAR``, and ``DATETIME_CHAR``,
so that the column continues to have textual affinity.

.. seealso::

    `Type Affinity <https://www.sqlite.org/datatype3.html#affinity>`_ -
    in the SQLite documentation

.. _sqlite_autoincrement:

SQLite Auto Incrementing Behavior
----------------------------------

Background on SQLite's autoincrement is at: https://sqlite.org/autoinc.html

Key concepts:

* SQLite has an implicit "auto increment" feature that takes place for any
  non-composite primary-key column that is specifically created using
  "INTEGER PRIMARY KEY" for the type + primary key.

* SQLite also has an explicit "AUTOINCREMENT" keyword, that is **not**
  equivalent to the implicit autoincrement feature; this keyword is not
  recommended for general use.  SQLAlchemy does not render this keyword
  unless a special SQLite-specific directive is used (see below).  However,
  it still requires that the column's type is named "INTEGER".

Using the AUTOINCREMENT Keyword
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

To specifically render the AUTOINCREMENT keyword on the primary key column
when rendering DDL, add the flag ``sqlite_autoincrement=True`` to the Table
construct::

    Table(
        "sometable",
        metadata,
        Column("id", Integer, primary_key=True),
        sqlite_autoincrement=True,
    )

Allowing autoincrement behavior SQLAlchemy types other than Integer/INTEGER
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

SQLite's typing model is based on naming conventions.  Among other things, this
means that any type name which contains the substring ``"INT"`` will be
determined to be of "integer affinity".  A type named ``"BIGINT"``,
``"SPECIAL_INT"`` or even ``"XYZINTQPR"``, will be considered by SQLite to be
of "integer" affinity.  However, **the SQLite autoincrement feature, whether
implicitly or explicitly enabled, requires that the name of the column's type
is exactly the string "INTEGER"**.  Therefore, if an application uses a type
like :class:`.BigInteger` for a primary key, on SQLite this type will need to
be rendered as the name ``"INTEGER"`` when emitting the initial ``CREATE
TABLE`` statement in order for the autoincrement behavior to be available.

One approach to achieve this is to use :class:`.Integer` on SQLite
only using :meth:`.TypeEngine.with_variant`::

    table = Table(
        "my_table",
        metadata,
        Column(
            "id",
            BigInteger().with_variant(Integer, "sqlite"),
            primary_key=True,
        ),
    )

Another is to use a subclass of :class:`.BigInteger` that overrides its DDL
name to be ``INTEGER`` when compiled against SQLite::

    from sqlalchemy import BigInteger
    from sqlalchemy.ext.compiler import compiles


    class SLBigInteger(BigInteger):
        pass


    @compiles(SLBigInteger, "sqlite")
    def bi_c(element, compiler, **kw):
        return "INTEGER"


    @compiles(SLBigInteger)
    def bi_c(element, compiler, **kw):
        return compiler.visit_BIGINT(element, **kw)


    table = Table(
        "my_table", metadata, Column("id", SLBigInteger(), primary_key=True)
    )

.. seealso::

    :meth:`.TypeEngine.with_variant`

    :ref:`sqlalchemy.ext.compiler_toplevel`

    `Datatypes In SQLite Version 3 <https://sqlite.org/datatype3.html>`_

.. _sqlite_transactions:

Transactions with SQLite and the sqlite3 driver
-----------------------------------------------

As a file-based database, SQLite's approach to transactions differs from
traditional databases in many ways.  Additionally, the ``sqlite3`` driver
standard with Python (as well as the async version ``aiosqlite`` which builds
on top of it) has several quirks, workarounds, and API features in the
area of transaction control, all of which generally need to be addressed when
constructing a SQLAlchemy application that uses SQLite.

Legacy Transaction Mode with the sqlite3 driver
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

The most important aspect of transaction handling with the sqlite3 driver is
that it defaults (which will continue through Python 3.15 before being
removed in Python 3.16) to legacy transactional behavior which does
not strictly follow :pep:`249`.  The way in which the driver diverges from the
PEP is that it does not "begin" a transaction automatically as dictated by
:pep:`249` except in the case of DML statements, e.g. INSERT, UPDATE, and
DELETE.   Normally, :pep:`249` dictates that a BEGIN must be emitted upon
the first SQL statement of any kind, so that all subsequent operations will
be established within a transaction until ``connection.commit()`` has been
called.   The ``sqlite3`` driver, in an effort to be easier to use in
highly concurrent environments, skips this step for DQL (e.g. SELECT) statements,
and also skips it for DDL (e.g. CREATE TABLE etc.) statements for more legacy
reasons.  Statements such as SAVEPOINT are also skipped.

In modern versions of the ``sqlite3`` driver as of Python 3.12, this legacy
mode of operation is referred to as
`"legacy transaction control" <https://docs.python.org/3/library/sqlite3.html#sqlite3-transaction-control-isolation-level>`_, and is in
effect by default due to the ``Connection.autocommit`` parameter being set to
the constant ``sqlite3.LEGACY_TRANSACTION_CONTROL``.  Prior to Python 3.12,
the ``Connection.autocommit`` attribute did not exist.

The implications of legacy transaction mode include:

* **Incorrect support for transactional DDL** - statements like CREATE TABLE, ALTER TABLE,
  CREATE INDEX etc. will not automatically BEGIN a transaction if one were not
  started already, leading to the changes by each statement being
  "autocommitted" immediately unless BEGIN were otherwise emitted first.   Very
  old (pre Python 3.6) versions of SQLite would also force a COMMIT for these
  operations even if a transaction were present, however this is no longer the
  case.
* **SERIALIZABLE behavior not fully functional** - SQLite's transaction isolation
  behavior is normally consistent with SERIALIZABLE isolation, as it is a file-
  based system that locks the database file entirely for write operations,
  preventing COMMIT until all reader transactions (and associated file locks)
  have completed.  However, sqlite3's legacy transaction mode fails to emit BEGIN for SELECT
  statements, which causes these SELECT statements to no longer be "repeatable",
  failing one of the consistency guarantees of SERIALIZABLE.
* **Incorrect behavior for SAVEPOINT** - as the SAVEPOINT statement does not
  imply a BEGIN, a new SAVEPOINT emitted before a BEGIN will function on its
  own but fails to participate in the enclosing transaction, meaning a ROLLBACK
  of the transaction will not rollback elements that were part of a released
  savepoint.

Legacy transaction mode first existed in order to facilitate working around
SQLite's file locks.  Because SQLite relies upon whole-file locks, it is easy to
get "database is locked" errors, particularly when newer features like "write
ahead logging" are disabled.   This is a key reason why ``sqlite3``'s legacy
transaction mode is still the default mode of operation; disabling it will
produce behavior that is more susceptible to locked database errors.  However
note that **legacy transaction mode will no longer be the default** in a future
Python version (3.16 as of this writing).

.. _sqlite_enabling_transactions:

Enabling Non-Legacy SQLite Transactional Modes with the sqlite3 or aiosqlite driver
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Current SQLAlchemy support allows either for setting the
``.Connection.autocommit`` attribute, most directly by using a
:func:`._sa.create_engine` parameter, or if on an older version of Python where
the attribute is not available, using event hooks to control the behavior of
BEGIN.

* **Enabling modern sqlite3 transaction control via the autocommit connect parameter** (Python 3.12 and above)

  To use SQLite in the mode described at `Transaction control via the autocommit attribute <https://docs.python.org/3/library/sqlite3.html#transaction-control-via-the-autocommit-attribute>`_,
  the most straightforward approach is to set the attribute to its recommended value
  of ``False`` at the connect level using :paramref:`_sa.create_engine.connect_args``::

    from sqlalchemy import create_engine

    engine = create_engine(
        "sqlite:///myfile.db", connect_args={"autocommit": False}
    )

  This parameter is also passed through when using the aiosqlite driver::

    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(
        "sqlite+aiosqlite:///myfile.db", connect_args={"autocommit": False}
    )

  The parameter can also be set at the attribute level using the :meth:`.PoolEvents.connect`
  event hook, however this will only work for sqlite3, as aiosqlite does not yet expose this
  attribute on its ``Connection`` object::

    from sqlalchemy import create_engine, event

    engine = create_engine("sqlite:///myfile.db")


    @event.listens_for(engine, "connect")
    def do_connect(dbapi_connection, connection_record):
        # enable autocommit=False mode
        dbapi_connection.autocommit = False

* **Using SQLAlchemy to emit BEGIN in lieu of SQLite's transaction control** (all Python versions, sqlite3 and aiosqlite)

  For older versions of ``sqlite3`` or for cross-compatiblity with older and
  newer versions, SQLAlchemy can also take over the job of transaction control.
  This is achieved by using the :meth:`.ConnectionEvents.begin` hook
  to emit the "BEGIN" command directly, while also disabling SQLite's control
  of this command using the :meth:`.PoolEvents.connect` event hook to set the
  ``Connection.isolation_level`` attribute to ``None``::


    from sqlalchemy import create_engine, event

    engine = create_engine("sqlite:///myfile.db")


    @event.listens_for(engine, "connect")
    def do_connect(dbapi_connection, connection_record):
        # disable sqlite3's emitting of the BEGIN statement entirely.
        dbapi_connection.isolation_level = None


    @event.listens_for(engine, "begin")
    def do_begin(conn):
        # emit our own BEGIN.   sqlite3 still emits COMMIT/ROLLBACK correctly
        conn.exec_driver_sql("BEGIN")

  When using the asyncio variant ``aiosqlite``, refer to ``engine.sync_engine``
  as in the example below::

    from sqlalchemy import create_engine, event
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine("sqlite+aiosqlite:///myfile.db")


    @event.listens_for(engine.sync_engine, "connect")
    def do_connect(dbapi_connection, connection_record):
        # disable aiosqlite's emitting of the BEGIN statement entirely.
        dbapi_connection.isolation_level = None


    @event.listens_for(engine.sync_engine, "begin")
    def do_begin(conn):
        # emit our own BEGIN.  aiosqlite still emits COMMIT/ROLLBACK correctly
        conn.exec_driver_sql("BEGIN")

.. _sqlite_isolation_level:

Using SQLAlchemy's Driver Level AUTOCOMMIT Feature with SQLite
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

SQLAlchemy has a comprehensive database isolation feature with optional
autocommit support that is introduced in the section :ref:`dbapi_autocommit`.

For the ``sqlite3`` and ``aiosqlite`` drivers, SQLAlchemy only includes
built-in support for "AUTOCOMMIT".    Note that this mode is currently incompatible
with the non-legacy isolation mode hooks documented in the previous
section at :ref:`sqlite_enabling_transactions`.

To use the ``sqlite3`` driver with SQLAlchemy driver-level autocommit,
create an engine setting the :paramref:`_sa.create_engine.isolation_level`
parameter to "AUTOCOMMIT"::

    eng = create_engine("sqlite:///myfile.db", isolation_level="AUTOCOMMIT")

When using the above mode, any event hooks that set the sqlite3 ``Connection.autocommit``
parameter away from its default of ``sqlite3.LEGACY_TRANSACTION_CONTROL``
as well as hooks that emit ``BEGIN`` should be disabled.

Additional Reading for SQLite / sqlite3 transaction control
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Links with important information on SQLite, the sqlite3 driver,
as well as long historical conversations on how things got to their current state:

* `Isolation in SQLite <https://www.sqlite.org/isolation.html>`_ - on the SQLite website
* `Transaction control <https://docs.python.org/3/library/sqlite3.html#transaction-control>`_ - describes the sqlite3 autocommit attribute as well
  as the legacy isolation_level attribute.
* `sqlite3 SELECT does not BEGIN a transaction, but should according to spec <https://github.com/python/cpython/issues/54133>`_ - imported Python standard library issue on github
* `sqlite3 module breaks transactions and potentially corrupts data <https://github.com/python/cpython/issues/54949>`_ - imported Python standard library issue on github


INSERT/UPDATE/DELETE...RETURNING
---------------------------------

The SQLite dialect supports SQLite 3.35's  ``INSERT|UPDATE|DELETE..RETURNING``
syntax.   ``INSERT..RETURNING`` may be used
automatically in some cases in order to fetch newly generated identifiers in
place of the traditional approach of using ``cursor.lastrowid``, however
``cursor.lastrowid`` is currently still preferred for simple single-statement
cases for its better performance.

To specify an explicit ``RETURNING`` clause, use the
:meth:`._UpdateBase.returning` method on a per-statement basis::

    # INSERT..RETURNING
    result = connection.execute(
        table.insert().values(name="foo").returning(table.c.col1, table.c.col2)
    )
    print(result.all())

    # UPDATE..RETURNING
    result = connection.execute(
        table.update()
        .where(table.c.name == "foo")
        .values(name="bar")
        .returning(table.c.col1, table.c.col2)
    )
    print(result.all())

    # DELETE..RETURNING
    result = connection.execute(
        table.delete()
        .where(table.c.name == "foo")
        .returning(table.c.col1, table.c.col2)
    )
    print(result.all())

.. versionadded:: 2.0  Added support for SQLite RETURNING


.. _sqlite_foreign_keys:

Foreign Key Support
-------------------

SQLite supports FOREIGN KEY syntax when emitting CREATE statements for tables,
however by default these constraints have no effect on the operation of the
table.

Constraint checking on SQLite has three prerequisites:

* At least version 3.6.19 of SQLite must be in use
* The SQLite library must be compiled *without* the SQLITE_OMIT_FOREIGN_KEY
  or SQLITE_OMIT_TRIGGER symbols enabled.
* The ``PRAGMA foreign_keys = ON`` statement must be emitted on all
  connections before use -- including the initial call to
  :meth:`sqlalchemy.schema.MetaData.create_all`.

SQLAlchemy allows for the ``PRAGMA`` statement to be emitted automatically for
new connections through the usage of events::

    from sqlalchemy.engine import Engine
    from sqlalchemy import event


    @event.listens_for(Engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        # the sqlite3 driver will not set PRAGMA foreign_keys
        # if autocommit=False; set to True temporarily
        ac = dbapi_connection.autocommit
        dbapi_connection.autocommit = True

        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

        # restore previous autocommit setting
        dbapi_connection.autocommit = ac

.. warning::

    When SQLite foreign keys are enabled, it is **not possible**
    to emit CREATE or DROP statements for tables that contain
    mutually-dependent foreign key constraints;
    to emit the DDL for these tables requires that ALTER TABLE be used to
    create or drop these constraints separately, for which SQLite has
    no support.

.. seealso::

    `SQLite Foreign Key Support <https://www.sqlite.org/foreignkeys.html>`_
    - on the SQLite web site.

    :ref:`event_toplevel` - SQLAlchemy event API.

    :ref:`use_alter` - more information on SQLAlchemy's facilities for handling
     mutually-dependent foreign key constraints.

.. _sqlite_on_conflict_ddl:

ON CONFLICT support for constraints
-----------------------------------

.. seealso:: This section describes the :term:`DDL` version of "ON CONFLICT" for
   SQLite, which occurs within a CREATE TABLE statement.  For "ON CONFLICT" as
   applied to an INSERT statement, see :ref:`sqlite_on_conflict_insert`.

SQLite supports a non-standard DDL clause known as ON CONFLICT which can be applied
to primary key, unique, check, and not null constraints.   In DDL, it is
rendered either within the "CONSTRAINT" clause or within the column definition
itself depending on the location of the target constraint.    To render this
clause within DDL, the extension parameter ``sqlite_on_conflict`` can be
specified with a string conflict resolution algorithm within the
:class:`.PrimaryKeyConstraint`, :class:`.UniqueConstraint`,
:class:`.CheckConstraint` objects.  Within the :class:`_schema.Column` object,
there
are individual parameters ``sqlite_on_conflict_not_null``,
``sqlite_on_conflict_primary_key``, ``sqlite_on_conflict_unique`` which each
correspond to the three types of relevant constraint types that can be
indicated from a :class:`_schema.Column` object.

.. seealso::

    `ON CONFLICT <https://www.sqlite.org/lang_conflict.html>`_ - in the SQLite
    documentation

.. versionadded:: 1.3


The ``sqlite_on_conflict`` parameters accept a  string argument which is just
the resolution name to be chosen, which on SQLite can be one of ROLLBACK,
ABORT, FAIL, IGNORE, and REPLACE.   For example, to add a UNIQUE constraint
that specifies the IGNORE algorithm::

    some_table = Table(
        "some_table",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("data", Integer),
        UniqueConstraint("id", "data", sqlite_on_conflict="IGNORE"),
    )

The above renders CREATE TABLE DDL as:

.. sourcecode:: sql

    CREATE TABLE some_table (
        id INTEGER NOT NULL,
        data INTEGER,
        PRIMARY KEY (id),
        UNIQUE (id, data) ON CONFLICT IGNORE
    )


When using the :paramref:`_schema.Column.unique`
flag to add a UNIQUE constraint
to a single column, the ``sqlite_on_conflict_unique`` parameter can
be added to the :class:`_schema.Column` as well, which will be added to the
UNIQUE constraint in the DDL::

    some_table = Table(
        "some_table",
        metadata,
        Column("id", Integer, primary_key=True),
        Column(
            "data", Integer, unique=True, sqlite_on_conflict_unique="IGNORE"
        ),
    )

rendering:

.. sourcecode:: sql

    CREATE TABLE some_table (
        id INTEGER NOT NULL,
        data INTEGER,
        PRIMARY KEY (id),
        UNIQUE (data) ON CONFLICT IGNORE
    )

To apply the FAIL algorithm for a NOT NULL constraint,
``sqlite_on_conflict_not_null`` is used::

    some_table = Table(
        "some_table",
        metadata,
        Column("id", Integer, primary_key=True),
        Column(
            "data", Integer, nullable=False, sqlite_on_conflict_not_null="FAIL"
        ),
    )

this renders the column inline ON CONFLICT phrase:

.. sourcecode:: sql

    CREATE TABLE some_table (
        id INTEGER NOT NULL,
        data INTEGER NOT NULL ON CONFLICT FAIL,
        PRIMARY KEY (id)
    )


Similarly, for an inline primary key, use ``sqlite_on_conflict_primary_key``::

    some_table = Table(
        "some_table",
        metadata,
        Column(
            "id",
            Integer,
            primary_key=True,
            sqlite_on_conflict_primary_key="FAIL",
        ),
    )

SQLAlchemy renders the PRIMARY KEY constraint separately, so the conflict
resolution algorithm is applied to the constraint itself:

.. sourcecode:: sql

    CREATE TABLE some_table (
        id INTEGER NOT NULL,
        PRIMARY KEY (id) ON CONFLICT FAIL
    )

.. _sqlite_on_conflict_insert:

INSERT...ON CONFLICT (Upsert)
-----------------------------

.. seealso:: This section describes the :term:`DML` version of "ON CONFLICT" for
   SQLite, which occurs within an INSERT statement.  For "ON CONFLICT" as
   applied to a CREATE TABLE statement, see :ref:`sqlite_on_conflict_ddl`.

From version 3.24.0 onwards, SQLite supports "upserts" (update or insert)
of rows into a table via the ``ON CONFLICT`` clause of the ``INSERT``
statement. A candidate row will only be inserted if that row does not violate
any unique or primary key constraints. In the case of a unique constraint violation, a
secondary action can occur which can be either "DO UPDATE", indicating that
the data in the target row should be updated, or "DO NOTHING", which indicates
to silently skip this row.

Conflicts are determined using columns that are part of existing unique
constraints and indexes.  These constraints are identified by stating the
columns and conditions that comprise the indexes.

SQLAlchemy provides ``ON CONFLICT`` support via the SQLite-specific
:func:`_sqlite.insert()` function, which provides
the generative methods :meth:`_sqlite.Insert.on_conflict_do_update`
and :meth:`_sqlite.Insert.on_conflict_do_nothing`:

.. sourcecode:: pycon+sql

    >>> from sqlalchemy.dialects.sqlite import insert

    >>> insert_stmt = insert(my_table).values(
    ...     id="some_existing_id", data="inserted value"
    ... )

    >>> do_update_stmt = insert_stmt.on_conflict_do_update(
    ...     index_elements=["id"], set_=dict(data="updated value")
    ... )

    >>> print(do_update_stmt)
    {printsql}INSERT INTO my_table (id, data) VALUES (?, ?)
    ON CONFLICT (id) DO UPDATE SET data = ?{stop}

    >>> do_nothing_stmt = insert_stmt.on_conflict_do_nothing(index_elements=["id"])

    >>> print(do_nothing_stmt)
    {printsql}INSERT INTO my_table (id, data) VALUES (?, ?)
    ON CONFLICT (id) DO NOTHING

.. versionadded:: 1.4

.. seealso::

    `Upsert
    <https://sqlite.org/lang_UPSERT.html>`_
    - in the SQLite documentation.


Specifying the Target
^^^^^^^^^^^^^^^^^^^^^

Both methods supply the "target" of the conflict using column inference:

* The :paramref:`_sqlite.Insert.on_conflict_do_update.index_elements` argument
  specifies a sequence containing string column names, :class:`_schema.Column`
  objects, and/or SQL expression elements, which would identify a unique index
  or unique constraint.

* When using :paramref:`_sqlite.Insert.on_conflict_do_update.index_elements`
  to infer an index, a partial index can be inferred by also specifying the
  :paramref:`_sqlite.Insert.on_conflict_do_update.index_where` parameter:

  .. sourcecode:: pycon+sql

        >>> stmt = insert(my_table).values(user_email="a@b.com", data="inserted data")

        >>> do_update_stmt = stmt.on_conflict_do_update(
        ...     index_elements=[my_table.c.user_email],
        ...     index_where=my_table.c.user_email.like("%@gmail.com"),
        ...     set_=dict(data=stmt.excluded.data),
        ... )

        >>> print(do_update_stmt)
        {printsql}INSERT INTO my_table (data, user_email) VALUES (?, ?)
        ON CONFLICT (user_email)
        WHERE user_email LIKE '%@gmail.com'
        DO UPDATE SET data = excluded.data

The SET Clause
^^^^^^^^^^^^^^^

``ON CONFLICT...DO UPDATE`` is used to perform an update of the already
existing row, using any combination of new values as well as values
from the proposed insertion. These values are specified using the
:paramref:`_sqlite.Insert.on_conflict_do_update.set_` parameter.  This
parameter accepts a dictionary which consists of direct values
for UPDATE:

.. sourcecode:: pycon+sql

    >>> stmt = insert(my_table).values(id="some_id", data="inserted value")

    >>> do_update_stmt = stmt.on_conflict_do_update(
    ...     index_elements=["id"], set_=dict(data="updated value")
    ... )

    >>> print(do_update_stmt)
    {printsql}INSERT INTO my_table (id, data) VALUES (?, ?)
    ON CONFLICT (id) DO UPDATE SET data = ?

.. warning::

    The :meth:`_sqlite.Insert.on_conflict_do_update` method does **not** take
    into account Python-side default UPDATE values or generation functions,
    e.g. those specified using :paramref:`_schema.Column.onupdate`. These
    values will not be exercised for an ON CONFLICT style of UPDATE, unless
    they are manually specified in the
    :paramref:`_sqlite.Insert.on_conflict_do_update.set_` dictionary.

Updating using the Excluded INSERT Values
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

In order to refer to the proposed insertion row, the special alias
:attr:`~.sqlite.Insert.excluded` is available as an attribute on
the :class:`_sqlite.Insert` object; this object creates an "excluded." prefix
on a column, that informs the DO UPDATE to update the row with the value that
would have been inserted had the constraint not failed:

.. sourcecode:: pycon+sql

    >>> stmt = insert(my_table).values(
    ...     id="some_id", data="inserted value", author="jlh"
    ... )

    >>> do_update_stmt = stmt.on_conflict_do_update(
    ...     index_elements=["id"],
    ...     set_=dict(data="updated value", author=stmt.excluded.author),
    ... )

    >>> print(do_update_stmt)
    {printsql}INSERT INTO my_table (id, data, author) VALUES (?, ?, ?)
    ON CONFLICT (id) DO UPDATE SET data = ?, author = excluded.author

Additional WHERE Criteria
^^^^^^^^^^^^^^^^^^^^^^^^^

The :meth:`_sqlite.Insert.on_conflict_do_update` method also accepts
a WHERE clause using the :paramref:`_sqlite.Insert.on_conflict_do_update.where`
parameter, which will limit those rows which receive an UPDATE:

.. sourcecode:: pycon+sql

    >>> stmt = insert(my_table).values(
    ...     id="some_id", data="inserted value", author="jlh"
    ... )

    >>> on_update_stmt = stmt.on_conflict_do_update(
    ...     index_elements=["id"],
    ...     set_=dict(data="updated value", author=stmt.excluded.author),
    ...     where=(my_table.c.status == 2),
    ... )
    >>> print(on_update_stmt)
    {printsql}INSERT INTO my_table (id, data, author) VALUES (?, ?, ?)
    ON CONFLICT (id) DO UPDATE SET data = ?, author = excluded.author
    WHERE my_table.status = ?


Skipping Rows with DO NOTHING
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

``ON CONFLICT`` may be used to skip inserting a row entirely
if any conflict with a unique constraint occurs; below this is illustrated
using the :meth:`_sqlite.Insert.on_conflict_do_nothing` method:

.. sourcecode:: pycon+sql

    >>> stmt = insert(my_table).values(id="some_id", data="inserted value")
    >>> stmt = stmt.on_conflict_do_nothing(index_elements=["id"])
    >>> print(stmt)
    {printsql}INSERT INTO my_table (id, data) VALUES (?, ?) ON CONFLICT (id) DO NOTHING


If ``DO NOTHING`` is used without specifying any columns or constraint,
it has the effect of skipping the INSERT for any unique violation which
occurs:

.. sourcecode:: pycon+sql

    >>> stmt = insert(my_table).values(id="some_id", data="inserted value")
    >>> stmt = stmt.on_conflict_do_nothing()
    >>> print(stmt)
    {printsql}INSERT INTO my_table (id, data) VALUES (?, ?) ON CONFLICT DO NOTHING

.. _sqlite_type_reflection:

Type Reflection
---------------

SQLite types are unlike those of most other database backends, in that
the string name of the type usually does not correspond to a "type" in a
one-to-one fashion.  Instead, SQLite links per-column typing behavior
to one of five so-called "type affinities" based on a string matching
pattern for the type.

SQLAlchemy's reflection process, when inspecting types, uses a simple
lookup table to link the keywords returned to provided SQLAlchemy types.
This lookup table is present within the SQLite dialect as it is for all
other dialects.  However, the SQLite dialect has a different "fallback"
routine for when a particular type name is not located in the lookup map;
it instead implements the SQLite "type affinity" scheme located at
https://www.sqlite.org/datatype3.html section 2.1.

The provided typemap will make direct associations from an exact string
name match for the following types:

:class:`_types.BIGINT`, :class:`_types.BLOB`,
:class:`_types.BOOLEAN`, :class:`_types.BOOLEAN`,
:class:`_types.CHAR`, :class:`_types.DATE`,
:class:`_types.DATETIME`, :class:`_types.FLOAT`,
:class:`_types.DECIMAL`, :class:`_types.FLOAT`,
:class:`_types.INTEGER`, :class:`_types.INTEGER`,
:class:`_types.NUMERIC`, :class:`_types.REAL`,
:class:`_types.SMALLINT`, :class:`_types.TEXT`,
:class:`_types.TIME`, :class:`_types.TIMESTAMP`,
:class:`_types.VARCHAR`, :class:`_types.NVARCHAR`,
:class:`_types.NCHAR`

When a type name does not match one of the above types, the "type affinity"
lookup is used instead:

* :class:`_types.INTEGER` is returned if the type name includes the
  string ``INT``
* :class:`_types.TEXT` is returned if the type name includes the
  string ``CHAR``, ``CLOB`` or ``TEXT``
* :class:`_types.NullType` is returned if the type name includes the
  string ``BLOB``
* :class:`_types.REAL` is returned if the type name includes the string
  ``REAL``, ``FLOA`` or ``DOUB``.
* Otherwise, the :class:`_types.NUMERIC` type is used.

.. _sqlite_partial_index:

Partial Indexes
---------------

A partial index, e.g. one which uses a WHERE clause, can be specified
with the DDL system using the argument ``sqlite_where``::

    tbl = Table("testtbl", m, Column("data", Integer))
    idx = Index(
        "test_idx1",
        tbl.c.data,
        sqlite_where=and_(tbl.c.data > 5, tbl.c.data < 10),
    )

The index will be rendered at create time as:

.. sourcecode:: sql

    CREATE INDEX test_idx1 ON testtbl (data)
    WHERE data > 5 AND data < 10

.. _sqlite_dotted_column_names:

Dotted Column Names
-------------------

Using table or column names that explicitly have periods in them is
**not recommended**.   While this is generally a bad idea for relational
databases in general, as the dot is a syntactically significant character,
the SQLite driver up until version **3.10.0** of SQLite has a bug which
requires that SQLAlchemy filter out these dots in result sets.

The bug, entirely outside of SQLAlchemy, can be illustrated thusly::

    import sqlite3

    assert sqlite3.sqlite_version_info < (
        3,
        10,
        0,
    ), "bug is fixed in this version"

    conn = sqlite3.connect(":memory:")
    cursor = conn.cursor()

    cursor.execute("create table x (a integer, b integer)")
    cursor.execute("insert into x (a, b) values (1, 1)")
    cursor.execute("insert into x (a, b) values (2, 2)")

    cursor.execute("select x.a, x.b from x")
    assert [c[0] for c in cursor.description] == ["a", "b"]

    cursor.execute(
        """
        select x.a, x.b from x where a=1
        union
        select x.a, x.b from x where a=2
        """
    )
    assert [c[0] for c in cursor.description] == ["a", "b"], [
        c[0] for c in cursor.description
    ]

The second assertion fails:

.. sourcecode:: text

    Traceback (most recent call last):
      File "test.py", line 19, in <module>
        [c[0] for c in cursor.description]
    AssertionError: ['x.a', 'x.b']

Where above, the driver incorrectly reports the names of the columns
including the name of the table, which is entirely inconsistent vs.
when the UNION is not present.

SQLAlchemy relies upon column names being predictable in how they match
to the original statement, so the SQLAlchemy dialect has no choice but
to filter these out::


    from sqlalchemy import create_engine

    eng = create_engine("sqlite://")
    conn = eng.connect()

    conn.exec_driver_sql("create table x (a integer, b integer)")
    conn.exec_driver_sql("insert into x (a, b) values (1, 1)")
    conn.exec_driver_sql("insert into x (a, b) values (2, 2)")

    result = conn.exec_driver_sql("select x.a, x.b from x")
    assert result.keys() == ["a", "b"]

    result = conn.exec_driver_sql(
        """
        select x.a, x.b from x where a=1
        union
        select x.a, x.b from x where a=2
        """
    )
    assert result.keys() == ["a", "b"]

Note that above, even though SQLAlchemy filters out the dots, *both
names are still addressable*::

    >>> row = result.first()
    >>> row["a"]
    1
    >>> row["x.a"]
    1
    >>> row["b"]
    1
    >>> row["x.b"]
    1

Therefore, the workaround applied by SQLAlchemy only impacts
:meth:`_engine.CursorResult.keys` and :meth:`.Row.keys()` in the public API. In
the very specific case where an application is forced to use column names that
contain dots, and the functionality of :meth:`_engine.CursorResult.keys` and
:meth:`.Row.keys()` is required to return these dotted names unmodified,
the ``sqlite_raw_colnames`` execution option may be provided, either on a
per-:class:`_engine.Connection` basis::

    result = conn.execution_options(sqlite_raw_colnames=True).exec_driver_sql(
        """
        select x.a, x.b from x where a=1
        union
        select x.a, x.b from x where a=2
        """
    )
    assert result.keys() == ["x.a", "x.b"]

or on a per-:class:`_engine.Engine` basis::

    engine = create_engine(
        "sqlite://", execution_options={"sqlite_raw_colnames": True}
    )

When using the per-:class:`_engine.Engine` execution option, note that
**Core and ORM queries that use UNION may not function properly**.

SQLite-specific table options
-----------------------------

One option for CREATE TABLE is supported directly by the SQLite
dialect in conjunction with the :class:`_schema.Table` construct:

* ``WITHOUT ROWID``::

    Table("some_table", metadata, ..., sqlite_with_rowid=False)

*
  ``STRICT``::

    Table("some_table", metadata, ..., sqlite_strict=True)

  .. versionadded:: 2.0.37

.. seealso::

    `SQLite CREATE TABLE options
    <https://www.sqlite.org/lang_createtable.html>`_

.. _sqlite_include_internal:

Reflecting internal schema tables
----------------------------------

Reflection methods that return lists of tables will omit so-called
"SQLite internal schema object" names, which are considered by SQLite
as any object name that is prefixed with ``sqlite_``.  An example of
such an object is the ``sqlite_sequence`` table that's generated when
the ``AUTOINCREMENT`` column parameter is used.   In order to return
these objects, the parameter ``sqlite_include_internal=True`` may be
passed to methods such as :meth:`_schema.MetaData.reflect` or
:meth:`.Inspector.get_table_names`.

.. versionadded:: 2.0  Added the ``sqlite_include_internal=True`` parameter.
   Previously, these tables were not ignored by SQLAlchemy reflection
   methods.

.. note::

    The ``sqlite_include_internal`` parameter does not refer to the
    "system" tables that are present in schemas such as ``sqlite_master``.

.. seealso::

    `SQLite Internal Schema Objects <https://www.sqlite.org/fileformat2.html#intschema>`_ - in the SQLite
    documentation.

'''  # noqa
from __future__ import annotations

import datetime
import numbers
import re
from typing import Any
from typing import Callable
from typing import Optional
from typing import TYPE_CHECKING

from .json import JSON
