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

ROOT_DIR = Path(__file__).resolve().parent.parent


@pytest.fixture
def pg(monkeypatch):
    """Pretend a Postgres is configured, without connecting to anything."""
    monkeypatch.delenv(db.CLOUD_SQL_INSTANCE_ENV, raising=False)
    monkeypatch.setenv(db.DATABASE_URL_ENV, "postgresql://u:p@h:5432/dbname")
    assert db.is_postgres()


@pytest.fixture
def lite(monkeypatch):
    monkeypatch.delenv(db.DATABASE_URL_ENV, raising=False)
    monkeypatch.delenv(db.CLOUD_SQL_INSTANCE_ENV, raising=False)
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

    def _fake_direct(url):
        conn = _FakePgConnection(log)
        connections.append(conn)
        return conn

    monkeypatch.setattr(db, "_connect_direct", _fake_direct)
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


# --- the three connection modes --------------------------------------------


def test_cloud_sql_instance_selects_the_connector(monkeypatch):
    monkeypatch.setenv(db.CLOUD_SQL_INSTANCE_ENV, "cairoai:us-central1:cairoai-db")
    assert db.use_cloud_sql_connector() and db.is_postgres()


def test_cloud_sql_wins_over_database_url(monkeypatch):
    """
    Both set is a misconfiguration, not a fallback. The connector is chosen
    rather than silently preferring whichever branch happened to come first.
    """
    monkeypatch.setenv(db.CLOUD_SQL_INSTANCE_ENV, "p:r:i")
    monkeypatch.setenv(db.DATABASE_URL_ENV, "postgresql://u:p@h/db")
    assert db.use_cloud_sql_connector()


def test_neither_set_means_sqlite(lite):
    assert not db.is_postgres() and not db.use_cloud_sql_connector()


def test_connector_is_asked_for_pg8000_with_a_password(monkeypatch):
    """psycopg 3 is not a driver the connector supports; pg8000 is."""
    monkeypatch.setenv(db.CLOUD_SQL_INSTANCE_ENV, "cairoai:us-central1:cairoai-db")
    monkeypatch.setenv(db.CLOUD_SQL_DB_ENV, "cairoai")
    monkeypatch.setenv(db.CLOUD_SQL_USER_ENV, "cairoai_app")
    monkeypatch.setenv(db.CLOUD_SQL_PASSWORD_ENV, "pw")
    monkeypatch.delenv(db.CLOUD_SQL_IAM_AUTH_ENV, raising=False)

    seen = {}

    class _FakeConnector:
        def connect(self, instance, driver, **kwargs):
            seen["instance"] = instance
            seen["driver"] = driver
            seen["kwargs"] = kwargs
            return _FakePgConnection([])

    monkeypatch.setattr(db, "_get_connector", lambda: _FakeConnector())
    db._connect_cloud_sql()

    assert seen["instance"] == "cairoai:us-central1:cairoai-db"
    assert seen["driver"] == "pg8000"
    assert seen["kwargs"]["user"] == "cairoai_app"
    assert seen["kwargs"]["password"] == "pw"
    assert "enable_iam_auth" not in seen["kwargs"]


def test_iam_auth_sends_no_password_at_all(monkeypatch):
    """
    The point of IAM auth is that no database password exists to leak. If a
    password were still sent alongside it, that property would be lost.
    """
    monkeypatch.setenv(db.CLOUD_SQL_INSTANCE_ENV, "p:r:i")
    monkeypatch.setenv(db.CLOUD_SQL_DB_ENV, "cairoai")
    monkeypatch.setenv(db.CLOUD_SQL_USER_ENV, "sa@project.iam")
    monkeypatch.setenv(db.CLOUD_SQL_PASSWORD_ENV, "should-be-ignored")
    monkeypatch.setenv(db.CLOUD_SQL_IAM_AUTH_ENV, "true")

    seen = {}

    class _FakeConnector:
        def connect(self, instance, driver, **kwargs):
            seen.update(kwargs)
            return _FakePgConnection([])

    monkeypatch.setattr(db, "_get_connector", lambda: _FakeConnector())
    db._connect_cloud_sql()

    assert seen["enable_iam_auth"] is True
    assert "password" not in seen


@pytest.mark.parametrize("value,expected", [
    ("true", True), ("TRUE", True), ("1", True), ("yes", True), ("on", True),
    ("false", False), ("0", False), ("", False), ("no", False),
])
def test_iam_auth_flag_parsing(monkeypatch, value, expected):
    monkeypatch.setenv(db.CLOUD_SQL_IAM_AUTH_ENV, value)
    assert db.iam_auth() is expected


def test_connector_is_built_lazily_and_keyed_to_the_pid(monkeypatch):
    """
    The connector holds background threads refreshing the instance's ephemeral
    certificates, and threads do not survive fork. An object built at import
    time and inherited by a forked worker is dead — that is precisely what made
    every deployed request 504 once already (see the ASGI bridge in main.py).
    So it is built lazily, and rebuilt when the PID it was built for changes.
    """
    import types

    built = []

    class _FakeConnector:
        def __init__(self):
            built.append("built")

    module = types.ModuleType("google.cloud.sql.connector")
    module.Connector = _FakeConnector
    for name in ("google", "google.cloud", "google.cloud.sql"):
        monkeypatch.setitem(sys.modules, name,
                            sys.modules.get(name) or types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "google.cloud.sql.connector", module)

    monkeypatch.setattr(db, "_connector", None)
    monkeypatch.setattr(db, "_connector_pid", None)

    first = db._get_connector()
    assert len(built) == 1, "not built on first use"

    # Same process: reused, not rebuilt.
    assert db._get_connector() is first
    assert len(built) == 1, "rebuilt unnecessarily within one process"

    # Simulate the fork: the recorded PID no longer matches this process.
    monkeypatch.setattr(db, "_connector_pid", db._connector_pid + 1)
    second = db._get_connector()
    assert len(built) == 2, "stale connector inherited across a fork"
    assert second is not first


def test_direct_url_is_parsed_into_pg8000_arguments(monkeypatch):
    seen = {}

    class _FakeDbapi:
        @staticmethod
        def connect(**kwargs):
            seen.update(kwargs)
            return _FakePgConnection([])

    import types
    module = types.ModuleType("pg8000")
    module.dbapi = _FakeDbapi
    monkeypatch.setitem(sys.modules, "pg8000", module)
    monkeypatch.setitem(sys.modules, "pg8000.dbapi", _FakeDbapi)

    db._connect_direct("postgresql://someuser:s%40cret@dbhost:6543/cairoai")
    assert seen["user"] == "someuser"
    assert seen["password"] == "s@cret"   # percent-decoded
    assert seen["host"] == "dbhost"
    assert seen["port"] == 6543
    assert seen["database"] == "cairoai"


# --- executescript ---------------------------------------------------------
#
# Found against a real Cloud SQL instance, not locally: `executescript` is a
# SQLite extension. No Postgres driver has it, so `__getattr__` delegation
# raised AttributeError and BOTH callers — company_store loading schema.sql and
# provider_db loading its inline schema — failed at import. On Postgres that
# meant no company_profile, company_documents, conversation_log, or provider
# table was ever created. SQLite has the method, so nothing caught it here.


def test_split_sql_ignores_semicolons_inside_string_literals():
    """
    agent/memory/schema.sql has four semicolons and THREE are inside string
    literals (default values, comments). A naive split produces fragments that
    are not valid SQL and a schema that half-applies.
    """
    script = """
        CREATE TABLE a (x TEXT DEFAULT 'has; semicolon');
        CREATE TABLE b (y TEXT DEFAULT 'another; one');
    """
    parts = db._split_sql(script)
    assert len(parts) == 2, parts
    assert "has; semicolon" in parts[0]
    assert "another; one" in parts[1]


def test_split_sql_handles_doubled_quote_escapes():
    parts = db._split_sql("INSERT INTO t VALUES ('it''s; fine'); SELECT 1")
    assert len(parts) == 2, parts
    assert "it''s; fine" in parts[0]


def test_split_sql_ignores_semicolons_in_line_comments():
    parts = db._split_sql("-- a comment; with a semicolon\nSELECT 1; SELECT 2")
    assert len(parts) == 2, parts


def test_split_sql_drops_empty_trailing_statements():
    assert db._split_sql("SELECT 1;  ;\n\n") == ["SELECT 1"]


def test_the_real_schema_file_splits_into_whole_statements():
    """The actual file, not a synthetic one."""
    schema = (ROOT_DIR / "agent" / "memory" / "schema.sql").read_text(encoding="utf-8")
    parts = db._split_sql(schema)
    creates = [p for p in parts if "CREATE TABLE" in p.upper()]
    assert len(creates) >= 2
    for p in creates:
        assert p.count("(") == p.count(")"), "statement split mid-parentheses"


def test_executescript_runs_every_statement_on_sqlite(lite, tmp_path):
    path = tmp_path / "s.db"
    with db.connect(path) as conn:
        conn.executescript(
            "CREATE TABLE one (a TEXT DEFAULT 'x; y');"
            "CREATE TABLE two (b TEXT);"
        )
    with db.connect(path) as conn:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert {"one", "two"} <= names


def test_executescript_exists_on_the_wrapper_not_just_the_driver():
    """
    The bug was relying on __getattr__ to find it on the raw connection. It has
    to be on the wrapper, or Postgres has no implementation to fall through to.
    """
    assert "executescript" in vars(db.Connection)
