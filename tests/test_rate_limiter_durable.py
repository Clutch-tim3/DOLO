"""
The global throttle survives an instance restart.

It used to open `procurement.db` with raw `sqlite3`. On Cloud Run `db_paths`
resolves that to `/tmp`, which is per-instance and wiped on cold start, so a new
instance began with an empty counter and the 30/min ceiling became 30/min *per
instance*. A burst large enough to trigger autoscaling therefore raised the
limit at exactly the moment it should have held — in front of a paid API on
someone else's key.

The durability itself cannot be proved from here: proving it needs a real
Postgres, and CLAUDE.md is explicit that the Postgres path has never served a
request against Cloud SQL. What these tests pin is the part that is provable
locally and that regressed before — that the counter is not held in process
memory, and that this module goes through `agent/db.py` rather than opening
SQLite itself.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from agent import rate_limiter

SOURCE = Path(__file__).resolve().parent.parent / "agent" / "rate_limiter.py"


@pytest.fixture(autouse=True)
def _clean_counter():
    """Empty the counter around each test so ordering cannot couple them."""
    from agent import db
    from agent.db_paths import PROCUREMENT_DB as DB_PATH

    rate_limiter.init_global_limiter()
    with db.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM global_rate_limit")
        conn.commit()
    yield
    with db.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM global_rate_limit")
        conn.commit()


def _simulate_instance_restart():
    """
    What a cold start does to this module: every scrap of in-process state
    goes, and the schema guard has to re-run against a fresh connection.
    """
    rate_limiter._initialised_pids.clear()


def test_the_limit_is_not_reset_by_a_restart():
    """The regression: a new instance used to start with an empty counter."""
    for _ in range(rate_limiter.GLOBAL_MAX_REQUESTS_PER_MINUTE):
        assert rate_limiter.check_global_rate_limit() is True

    assert rate_limiter.check_global_rate_limit() is False, "limit did not engage"

    _simulate_instance_restart()

    assert rate_limiter.check_global_rate_limit() is False, (
        "the limit reset when the instance restarted — the counter is not durable"
    )


def test_the_count_is_not_held_in_module_memory():
    """
    Nothing but the schema guard may live in this module between calls. A
    counter cached in a module-level variable would survive the test above by
    accident while still resetting on a real cold start.
    """
    for _ in range(5):
        rate_limiter.check_global_rate_limit()

    ints = {
        name: value for name, value in vars(rate_limiter).items()
        if isinstance(value, int) and not name.startswith("__")
        and name != "GLOBAL_MAX_REQUESTS_PER_MINUTE"
    }
    assert not ints, f"request state is being kept in module memory: {ints}"


def test_it_does_not_open_sqlite_itself():
    """
    Going through `agent/db.py` is what routes this to Cloud SQL in production.
    `import sqlite3` here is the bug, restored.
    """
    body = SOURCE.read_text(encoding="utf-8").split('"""', 2)[-1]
    assert "import sqlite3" not in body
    assert "sqlite3.connect" not in body
    assert "db.connect" in body


def test_schema_setup_does_not_run_at_import():
    """
    `init_global_limiter()` used to be called at module scope. With the Cloud
    SQL connector that builds background refresh threads before the ASGI bridge
    forks, which is what made every request 504 once already.
    """
    body = SOURCE.read_text(encoding="utf-8").split('"""', 2)[-1]
    module_level_calls = [
        line for line in body.splitlines()
        if line.strip() == "init_global_limiter()"
    ]
    assert not module_level_calls, "schema setup runs at import time"


def test_it_fails_closed_when_the_counter_is_unreachable(monkeypatch):
    """
    A throttle that waves requests through when its storage is down is not a
    throttle, and what it is guarding costs money per call.
    """
    def broken(*a, **kw):
        raise RuntimeError("database unreachable")

    monkeypatch.setattr(rate_limiter.db, "connect", broken)
    assert rate_limiter.check_global_rate_limit() is False
