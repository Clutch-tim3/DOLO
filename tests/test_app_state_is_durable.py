"""
app.py does not open SQLite behind agent/db.py's back.

Nine call sites in app.py opened `sqlite3.connect(DB_PATH)` directly. DB_PATH
resolves to `/tmp/data/procurement.db` whenever `K_SERVICE` is set, and /tmp on
Cloud Run is per-instance and wiped on cold start — so tracked outcomes,
calendar events and predictions were written to a disk that disappears. It
demoed perfectly, which is what made it dangerous: the symptom is "my tracked
outcomes are gone", not an outage.

This is the same migration already done for auth, packs and reviews. What is
pinned here is that it stays done — a tenth `sqlite3.connect` added later would
reintroduce the loss silently, because everything still passes locally where
SQLite *is* the durable store.
"""

import ast
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

APP = Path(__file__).resolve().parent.parent / "app.py"
SOURCE = APP.read_text(encoding="utf-8")


def test_no_direct_sqlite_connections_remain():
    offenders = [
        (n, line.strip())
        for n, line in enumerate(SOURCE.splitlines(), 1)
        if "sqlite3.connect" in line and not line.strip().startswith("#")
    ]
    assert not offenders, f"app.py opens SQLite directly at {offenders}"


def test_state_goes_through_the_db_layer():
    assert "_state_db.connect(DB_PATH)" in SOURCE, "app.py no longer routes state through agent/db.py"


def test_schema_setup_does_not_run_at_import():
    """
    `init_db()` used to be called at module scope. With the Cloud SQL connector
    that builds background refresh threads before the ASGI bridge forks, and
    inheriting one across a fork is what made every request 504 once already.
    """
    tree = ast.parse(SOURCE)
    module_level_calls = [
        node.value.func.id
        for node in tree.body
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id in ("init_db", "init_global_limiter")
    ]
    assert not module_level_calls, f"schema setup runs at import: {module_level_calls}"


def test_every_state_block_ensures_its_schema():
    """
    Because setup is lazy, each connection has to make sure the tables are
    there. A `with _state_db.connect(...)` that skips `_ensure_schema` works on
    a warm instance and fails on the first request to a cold one.
    """
    lines = SOURCE.splitlines()
    missing = []
    for n, line in enumerate(lines):
        if "_state_db.connect(DB_PATH) as conn" not in line:
            continue
        nxt = lines[n + 1] if n + 1 < len(lines) else ""
        if "_ensure_schema(conn)" not in nxt and "_init_db_schema(conn)" not in nxt:
            missing.append(n + 1)
    assert not missing, f"connection opened without ensuring schema at lines {missing}"


def test_the_shim_is_not_handed_a_closed_connection():
    """
    Unlike sqlite3, agent/db.py's `with` block closes the connection. A
    `conn.close()` left inside one shuts it under the caller.
    """
    inside = []
    depth = None
    for n, line in enumerate(SOURCE.splitlines(), 1):
        if "_state_db.connect(DB_PATH) as conn" in line:
            depth = len(line) - len(line.lstrip())
            continue
        if depth is None:
            continue
        stripped = line.strip()
        if stripped and (len(line) - len(line.lstrip())) <= depth:
            depth = None
            continue
        if stripped == "conn.close()":
            inside.append(n)
    assert not inside, f"conn.close() inside a managed block at lines {inside}"
