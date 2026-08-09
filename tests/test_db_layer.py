"""
The SQLite/Postgres compatibility layer.

Application state used to live in a SQLite file that db_paths resolves to /tmp
on Cloud Functions, where it is per-instance and ephemeral — so a cold start
silently discarded every profile, vault document, review and quote. This layer
is what lets the same raw SQL run against Cloud SQL instead.

HONEST SCOPE: there is no Postgres server on the machine these tests run on.
The translation and row-mapping behaviour is tested directly, and the driver
interaction through a fake DB-API module. That is not the same as having run
against Postgres, and nothing here should be read as claiming it.
"""

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import db


@pytest.fixture
def pg(monkeypatch):
    """Pretend DATABASE_URL is set, without connecting to anything."""
    monkeypatch.setenv(db.DATABASE_URL_ENV, "postgresql://u:p@h/dbname")
    assert db.is_postgres()


@pytest.fixture
def lite(monkeypatch):
    monkeypatch.delenv(db.DATABASE_URL_ENV, raising=False)
    assert not db.is_postgres()


# --- translation ------------------------------------------------------------


def test_sqlite_sql_is_untouched(lite):
    sql = "SELECT * FROM t WHERE a = ? AND b = ?"
    assert db.translate(sql) == sql


def test_placeholders_become_percent_s(pg):
    assert db.translate("SELECT * FROM t WHERE a = ? AND b = ?") == (
        "SELECT * FROM t WHERE a = %s AND b = %s")


def test_autoincrement_becomes_bigserial(pg):
    out = db.translate("CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, x TEXT)")
    assert "BIGSERIAL PRIMARY KEY" in out
    assert "AUTOINCREMENT" not in out.upper()


def test_insert_or_ignore_becomes_on_conflict(pg):
    out = db.translate("INSERT OR IGNORE INTO t (a) VALUES (?)")
    assert out.startswith("INSERT INTO t")
    assert out.rstrip().endswith("ON CONFLICT DO NOTHING")
    assert "%s" in out


def test_insert_or_ignore_that_already_has_a_conflict_clause_is_not_doubled(pg):
    out = db.translate("INSERT OR IGNORE INTO t (a) VALUES (?) ON CONFLICT (a) DO NOTHING")
    assert out.upper().count("ON CONFLICT") == 1


def test_insert_or_replace_is_left_alone_to_fail_loudly(pg):
    """
    Guessing a conflict target would produce a statement that runs and means
    something different. Better it errors on Postgres and gets written properly.
    """
    assert "INSERT OR REPLACE" in db.translate("INSERT OR REPLACE INTO t (a) VALUES (?)")


def test_datetime_type_becomes_timestamp(pg):
    assert "TIMESTAMP" in db.translate("CREATE TABLE t (at DATETIME)")


def test_a_question_mark_inside_a_string_literal_is_still_rewritten(pg):
    """
    Known limitation, pinned rather than hidden: the placeholder rewrite is
    textual, so a literal '?' inside a quoted string would also be replaced.
    No statement in this codebase contains one. If one is ever added, this test
    is the thing that explains the resulting confusion.
    """
    assert db.translate("SELECT 'why?' FROM t") == "SELECT 'why%s' FROM t"


# --- rows -------------------------------------------------------------------


def test_row_supports_name_and_index_like_sqlite3_row():
    row = db._Row(("a-value", 42), ["name", "count"])
    assert row["name"] == "a-value"
    assert row[0] == "a-value"
    assert row["count"] == 42
    assert row.keys() == ["name", "count"]
    assert list(row) == ["a-value", 42]
    assert len(row) == 2


def test_row_get_returns_default_for_missing_column():
    row = db._Row(("x",), ["only"])
    assert row.get("nope") is None
    assert row.get("nope", "fallback") == "fallback"


# --- the SQLite path, for real ---------------------------------------------


def test_sqlite_round_trip_through_the_wrapper(lite, tmp_path):
    path = tmp_path / "t.db"
    with db.connect(path) as conn:
        conn.execute("CREATE TABLE company (id TEXT PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO company (id, name) VALUES (?, ?)", ("c1", "CairoAI"))

    with db.connect(path) as conn:
        row = conn.execute("SELECT id, name FROM company WHERE id = ?", ("c1",)).fetchone()
    assert row["name"] == "CairoAI" and row[0] == "c1"


def test_context_manager_rolls_back_on_error(lite, tmp_path):
    path = tmp_path / "t.db"
    with db.connect(path) as conn:
        conn.execute("CREATE TABLE t (x TEXT)")
    with pytest.raises(RuntimeError):
        with db.connect(path) as conn:
            conn.execute("INSERT INTO t (x) VALUES (?)", ("dropped",))
            raise RuntimeError("boom")
    with db.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 0


def test_connection_is_closed_on_exit(lite, tmp_path):
    """
    sqlite3's own context manager leaves the connection open. On a serverless
    runtime that is how a Cloud SQL instance's connection limit gets exhausted,
    so this wrapper closes.
    """
    path = tmp_path / "t.db"
    conn = db.connect(path)
    with conn:
        conn.execute("CREATE TABLE t (x TEXT)")
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")


def test_table_columns_on_sqlite(lite, tmp_path):
    path = tmp_path / "t.db"
    with db.connect(path) as conn:
        conn.execute("CREATE TABLE t (a TEXT, b INTEGER)")
        assert db.table_columns(conn, "t") == {"a", "b"}


# --- the Postgres path, through a fake driver ------------------------------


class _FakePgCursor:
    def __init__(self, log):
        self.log = log
        self.description = [("id",), ("name",)]
        self._rows = [("c1", "CairoAI")]

    def execute(self, sql, params=()):
        self.log.append((sql, params))

    def executemany(self, sql, seq):
        self.log.append((sql, list(seq)))

    def fetchone(self):
        return self._rows[0]

    def fetchall(self):
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)


class _FakePgConnection:
    def __init__(self, log):
        self.log = log
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self):
        return _FakePgCursor(self.log)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


@pytest.fixture
def fake_pg(pg, monkeypatch):
    import types

    log = []
    connections = []

    module = types.ModuleType("psycopg")
    def _connect(url):
        conn = _FakePgConnection(log)
        connections.append(conn)
        return conn
    module.connect = _connect
    monkeypatch.setitem(sys.modules, "psycopg", module)
    return log, connections


def test_postgres_statements_are_translated_before_reaching_the_driver(fake_pg):
    log, _ = fake_pg
    with db.connect() as conn:
        conn.execute("SELECT * FROM company WHERE id = ?", ("c1",))
    assert log[0][0] == "SELECT * FROM company WHERE id = %s"
    assert log[0][1] == ("c1",)


def test_postgres_rows_come_back_addressable_by_name(fake_pg):
    with db.connect() as conn:
        row = conn.execute("SELECT id, name FROM company").fetchone()
    assert row["name"] == "CairoAI"
    assert row[0] == "c1"


def test_postgres_commits_on_success_and_closes(fake_pg):
    _, connections = fake_pg
    with db.connect() as conn:
        conn.execute("SELECT 1")
    assert connections[0].committed and connections[0].closed


def test_postgres_rolls_back_on_error_and_still_closes(fake_pg):
    _, connections = fake_pg
    with pytest.raises(RuntimeError):
        with db.connect() as conn:
            conn.execute("SELECT 1")
            raise RuntimeError("boom")
    assert connections[0].rolled_back and connections[0].closed
    assert not connections[0].committed


def test_table_columns_uses_information_schema_on_postgres(fake_pg):
    log, _ = fake_pg
    with db.connect() as conn:
        db.table_columns(conn, "autofill_review")
    sql, params = log[0]
    assert "information_schema.columns" in sql
    assert params == ("autofill_review",)
    assert "PRAGMA" not in sql.upper()
