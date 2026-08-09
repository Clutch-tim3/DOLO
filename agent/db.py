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
from typing import Any

#: Postgres connection string. Absent means SQLite, which is the local default.
DATABASE_URL_ENV = "DATABASE_URL"


def database_url() -> str:
    return (os.environ.get(DATABASE_URL_ENV) or "").strip()


def is_postgres() -> bool:
    return bool(database_url())


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


def connect(sqlite_path=None) -> Connection:
    """
    Open a connection to application state.

    `sqlite_path` is used only when running on SQLite, so a caller can keep
    passing the path it already resolved through `agent/db_paths.py` and this
    quietly ignores it once `DATABASE_URL` is set.
    """
    if is_postgres():
        import psycopg

        return Connection(psycopg.connect(database_url()), postgres=True)

    raw = sqlite3.connect(str(sqlite_path))
    raw.row_factory = sqlite3.Row
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
