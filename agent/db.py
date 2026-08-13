"""
One connection function for application state, SQLite or Postgres.

WHY THIS EXISTS
---------------
Every runtime table — company profiles, the compliance vault, autofill reviews,
quote audit history, subscription quotas — lived in a SQLite file that
`agent/db_paths.py` resolves to `/tmp` on Cloud Functions. `/tmp` is
per-instance and ephemeral, so on the deployed site a cold start silently threw
away everything a user had entered. It demoed fine, which is what made it
dangerous: the failure looks like "the vault forgot my documents", not like an
outage.

Local development stays on SQLite. Set `DATABASE_URL` and this switches to
Postgres, which is what Cloud SQL provides.

THE POINT OF THE WRAPPER
------------------------
The modules that hold state are written in raw SQL with `?` placeholders and
`sqlite3` row objects. Rewriting them all into an ORM would be a far larger
change with far more places to get a WHERE clause subtly wrong — and several of
those WHERE clauses are the security gates written earlier in this build. So
the connection is wrapped instead: placeholders and a few dialect differences
are translated on the way through, and rows come back as mappings that support
both `row["col"]` and `row[0]`. A calling module changes one line.

VERIFICATION STATUS
-------------------
The SQLite path is exercised by the whole existing test suite. The Postgres
path is covered by translation tests and a fake DB-API driver, but **has not
been run against a real Postgres server from this machine** — there isn't one
here. Treat "works on Cloud SQL" as unverified until it has served a request
there. See `agent_autofill/providers/VERIFICATION.md` for why that distinction
is kept explicit in this project.
"""

from __future__ import annotations

import os
import re
import sqlite3
import threading
from typing import Any

#: Postgres connection string, for a directly reachable server — a local
#: Postgres, or a host that is not Cloud SQL. Absent means SQLite.
DATABASE_URL_ENV = "DATABASE_URL"

#: Cloud SQL instance connection name, "project:region:instance". When set, the
#: Cloud SQL Python Connector is used instead of a direct connection. That
#: avoids attaching the instance to the Cloud Run service with
#: `--add-cloudsql-instances`, which `firebase deploy` may not preserve when it
#: rewrites the service config — a setting that silently disappears on a later
#: deploy is worse than one that was never there.
CLOUD_SQL_INSTANCE_ENV = "CLOUD_SQL_INSTANCE"
CLOUD_SQL_DB_ENV = "CLOUD_SQL_DB"
CLOUD_SQL_USER_ENV = "CLOUD_SQL_USER"
CLOUD_SQL_PASSWORD_ENV = "CLOUD_SQL_PASSWORD"
#: "true" to authenticate as the runtime service account instead of with a
#: password. Preferred where it is set up: there is then no database password
#: in Secret Manager, in an env var, or anywhere else to leak.
CLOUD_SQL_IAM_AUTH_ENV = "CLOUD_SQL_IAM_AUTH"


def database_url() -> str:
    return (os.environ.get(DATABASE_URL_ENV) or "").strip()


def cloud_sql_instance() -> str:
    return (os.environ.get(CLOUD_SQL_INSTANCE_ENV) or "").strip()


def use_cloud_sql_connector() -> bool:
    return bool(cloud_sql_instance())


def iam_auth() -> bool:
    return (os.environ.get(CLOUD_SQL_IAM_AUTH_ENV) or "").strip().lower() in (
        "1", "true", "yes", "on")


def is_postgres() -> bool:
    return bool(database_url()) or use_cloud_sql_connector()


# --- the Cloud SQL connector ------------------------------------------------
#
# The Connector keeps background threads refreshing the instance's ephemeral
# certificates. Building it at import time is the same mistake that hung this
# function once already: the runtime imports the module in one process and
# serves from forked workers, threads do not survive fork, and the inherited
# object is dead. See the ASGI bridge comment in main.py. So it is built lazily
# and keyed to the PID that will actually use it.
_connector = None
_connector_pid = None
_connector_lock = threading.Lock()


def _get_connector():
    global _connector, _connector_pid
    pid = os.getpid()
    if _connector is None or _connector_pid != pid:
        with _connector_lock:
            if _connector is None or _connector_pid != pid:
                from google.cloud.sql.connector import Connector

                _connector = Connector()
                _connector_pid = pid
    return _connector


def _connect_cloud_sql():
    """
    Open a connection through the Cloud SQL Python Connector.

    Uses pg8000: it is pure Python, so the deploy needs no build toolchain, and
    it is one of the drivers the connector actually supports — psycopg 3 is not.
    """
    kwargs = {
        "db": (os.environ.get(CLOUD_SQL_DB_ENV) or "").strip(),
        "user": (os.environ.get(CLOUD_SQL_USER_ENV) or "").strip(),
    }
    if iam_auth():
        kwargs["enable_iam_auth"] = True
    else:
        kwargs["password"] = os.environ.get(CLOUD_SQL_PASSWORD_ENV) or ""
    return _get_connector().connect(cloud_sql_instance(), "pg8000", **kwargs)


# --- dialect translation ----------------------------------------------------

#: Statements SQLite accepts and Postgres does not. Ordered; applied in turn.
_REWRITES: list[tuple[re.Pattern, str]] = [
    # Placeholders. Postgres uses %s. Done first so later patterns see stable text.
    (re.compile(r"\?"), "%s"),
    # Auto-incrementing keys.
    (re.compile(r"\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b", re.I), "BIGSERIAL PRIMARY KEY"),
    (re.compile(r"\bAUTOINCREMENT\b", re.I), ""),
    # Upserts. SQLite's INSERT OR IGNORE has a direct Postgres equivalent, but
    # the conflict target has to be supplied by the caller, so only the
    # no-target form is translated and INSERT OR REPLACE is deliberately left
    # to fail loudly rather than be guessed at.
    (re.compile(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", re.I), "INSERT INTO"),
    # SQLite is untyped enough to accept these; Postgres wants real types.
    (re.compile(r"\bDATETIME\b", re.I), "TIMESTAMP"),
]

#: `INSERT OR IGNORE` loses its "or ignore" in the rewrite above, so the
#: conflict clause is appended instead. Kept separate because it must go at the
#: end of the statement, not at the match site.
_INSERT_OR_IGNORE = re.compile(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", re.I)


def translate(sql: str) -> str:
    """
    Rewrite one statement for Postgres. Identity when running on SQLite.

    Deliberately narrow. It handles the constructs this codebase actually uses;
    anything else is left alone so it fails visibly on Postgres rather than
    being silently mangled into something that runs and means something else.
    """
    if not is_postgres():
        return sql
    needs_conflict = bool(_INSERT_OR_IGNORE.search(sql))
    out = sql
    for pattern, replacement in _REWRITES:
        out = pattern.sub(replacement, out)
    if needs_conflict and "ON CONFLICT" not in out.upper():
        out = out.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    return out


def _split_sql(script: str) -> list[str]:
    """
    Split a SQL script into statements on semicolons that actually terminate one.

    Not a naive `script.split(";")`. `agent/memory/schema.sql` has four
    semicolons and THREE of them sit inside string literals — default values
    and comments — so splitting naively produces fragments that are not valid
    SQL and a schema that half-applies. Quoted strings (single and double, with
    SQL's doubled-quote escaping) and `--` line comments are skipped over.
    """
    statements: list[str] = []
    current: list[str] = []
    quote: str | None = None
    in_line_comment = False
    i = 0
    while i < len(script):
        ch = script[i]
        nxt = script[i + 1] if i + 1 < len(script) else ""

        if in_line_comment:
            current.append(ch)
            if ch == "\n":
                in_line_comment = False
        elif quote:
            current.append(ch)
            if ch == quote:
                # A doubled quote is an escaped quote, not the end of the string.
                if nxt == quote:
                    current.append(nxt)
                    i += 1
                else:
                    quote = None
        elif ch == "-" and nxt == "-":
            in_line_comment = True
            current.append(ch)
        elif ch in ("'", '"'):
            quote = ch
            current.append(ch)
        elif ch == ";":
            statements.append("".join(current))
            current = []
        else:
            current.append(ch)
        i += 1

    statements.append("".join(current))
    return [s for s in (st.strip() for st in statements) if s]


class _Cursor:
    """Cursor that translates SQL on the way in and yields mappings on the way out."""

    def __init__(self, cursor, postgres: bool):
        self._cursor = cursor
        self._postgres = postgres

    def execute(self, sql: str, params: Any = ()):
        self._cursor.execute(translate(sql), params)
        return self

    def executemany(self, sql: str, seq):
        self._cursor.executemany(translate(sql), seq)
        return self

    def fetchone(self):
        return self._wrap(self._cursor.fetchone())

    def fetchall(self):
        return [self._wrap(r) for r in self._cursor.fetchall()]

    def __iter__(self):
        for row in self._cursor:
            yield self._wrap(row)

    def _wrap(self, row):
        if row is None:
            return None
        if self._postgres and not isinstance(row, _Row):
            return _Row(row, [d[0] for d in (self._cursor.description or [])])
        return row

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class _Row:
    """
    A row addressable by name or index, matching `sqlite3.Row` closely enough
    that calling code does not care which database it is talking to.
    """

    __slots__ = ("_values", "_columns")

    def __init__(self, values, columns):
        self._values = tuple(values)
        self._columns = list(columns)

    def __getitem__(self, key):
        if isinstance(key, str):
            return self._values[self._columns.index(key)]
        return self._values[key]

    def get(self, key, default=None):
        try:
            return self[key]
        except (ValueError, IndexError):
            return default

    def keys(self):
        return list(self._columns)

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def __eq__(self, other):
        return tuple(self._values) == tuple(other)

    def __repr__(self):
        return f"Row({dict(zip(self._columns, self._values))})"


class Connection:
    """
    Thin wrapper so `conn.execute(...)` works the way the SQLite code expects.

    Postgres' DB-API has no connection-level `execute`, and its context manager
    means "transaction" rather than "close" — the opposite of what
    `with sqlite3.connect(...)` does in this codebase, where it is used to mean
    commit-and-release. The wrapper normalises both so the call sites do not
    have to change their shape.
    """

    def __init__(self, raw, postgres: bool):
        self._raw = raw
        self._postgres = postgres

    def execute(self, sql: str, params: Any = ()):
        cursor = _Cursor(self._raw.cursor(), self._postgres)
        return cursor.execute(sql, params)

    def executemany(self, sql: str, seq):
        cursor = _Cursor(self._raw.cursor(), self._postgres)
        return cursor.executemany(sql, seq)

    def executescript(self, script: str):
        """
        Run a multi-statement SQL script.

        `executescript` is a SQLite extension — no Postgres driver has it, and
        `__getattr__` delegation therefore raised AttributeError against a real
        server. Two callers depend on it (`company_store` loading schema.sql,
        `provider_db` loading its inline schema), so on Postgres both failed at
        import and no company or provider table was ever created. Nothing caught
        it locally because SQLite has the method.

        Statements are split here and run one at a time through `execute`, so
        each still passes through `translate`.
        """
        for statement in _split_sql(script):
            self.execute(statement)
        return self

    def cursor(self):
        return _Cursor(self._raw.cursor(), self._postgres)

    def commit(self):
        self._raw.commit()

    def rollback(self):
        self._raw.rollback()

    def close(self):
        self._raw.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        # Commit on success, roll back on failure, then release. sqlite3's own
        # context manager does not close the connection; on a serverless
        # runtime leaking connections is how you exhaust a Cloud SQL instance's
        # connection limit, so this one does.
        try:
            if exc_type is None:
                self._raw.commit()
            else:
                self._raw.rollback()
        finally:
            self._raw.close()
        return False

    def __getattr__(self, name):
        return getattr(self._raw, name)


def _connect_direct(url: str):
    """
    Connect to a Postgres reachable by host and port.

    pg8000 takes keyword arguments rather than a URL, so the URL is parsed here
    — keeping DATABASE_URL as the configuration format, which is what everything
    else in this ecosystem expects.
    """
    from urllib.parse import unquote, urlparse

    import pg8000.dbapi

    parts = urlparse(url)
    return pg8000.dbapi.connect(
        user=unquote(parts.username or ""),
        password=unquote(parts.password or ""),
        host=parts.hostname or "localhost",
        port=parts.port or 5432,
        database=(parts.path or "/").lstrip("/"),
    )


def connect(sqlite_path=None) -> Connection:
    """
    Open a connection to application state.

    `sqlite_path` is used only when running on SQLite, so a caller can keep
    passing the path it already resolved through `agent/db_paths.py` and this
    quietly ignores it once `DATABASE_URL` is set.
    """
    if use_cloud_sql_connector():
        return Connection(_connect_cloud_sql(), postgres=True)

    if database_url():
        # A directly reachable Postgres: local development against one, or a
        # host that is not Cloud SQL. Same driver, so dialect behaviour is
        # identical either way.
        return Connection(_connect_direct(database_url()), postgres=True)

    # timeout is the busy handler: how long a writer waits for another writer's
    # lock instead of raising "database is locked" immediately. The default is
    # 5 seconds, which was enough while one or two modules wrote here and is not
    # now that sessions, review state, quote audit and file ownership all do.
    raw = sqlite3.connect(str(sqlite_path), timeout=30.0)
    raw.row_factory = sqlite3.Row
    try:
        # WAL lets readers proceed while a write is in flight, which is most of
        # the contention. Set per-connection because it is a property of the
        # database file, so the first connection to set it is enough — the rest
        # are cheap no-ops. Wrapped because a read-only or unusual filesystem
        # can refuse the journal change, and that must not stop the app.
        raw.execute("PRAGMA journal_mode=WAL")
        raw.execute("PRAGMA synchronous=NORMAL")
    except sqlite3.DatabaseError:
        pass
    return Connection(raw, postgres=False)


def table_columns(conn: Connection, table: str) -> set[str]:
    """
    Column names for a table, on either backend.

    Replaces `PRAGMA table_info(...)`, which the additive migrations use to
    decide whether a column needs adding and which Postgres does not have.
    """
    if is_postgres():
        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
            (table,),
        ).fetchall()
        return {r[0] for r in rows}
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
