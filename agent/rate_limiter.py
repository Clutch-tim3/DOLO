"""
The global throttle in front of the Anthropic API.

WHY THIS MOVED OFF SQLITE
-------------------------
The counter used to live in `procurement.db` via raw `sqlite3`. On Cloud Run
`db_paths` resolves that to `/tmp`, which is per-instance and wiped on cold
start — so every new instance began with an empty table and the limit reset.
Under any scale-out the effective ceiling was 30 requests per minute *per
instance*, not globally, and a burst that triggered autoscaling raised the
limit exactly when it should have held.

That counter is the only thing standing between an abusive caller and an
unbounded bill on someone else's API key, so "it resets in the user's favour"
is the wrong direction: the user it favours is not the one paying.

State goes through `agent/db.py` like the rest of the application, so it is
Postgres in production and SQLite locally, unchanged, and it survives an
instance restart.

INITIALISATION IS LAZY, DELIBERATELY
------------------------------------
This module used to call `init_global_limiter()` at import. With the Cloud SQL
connector that builds the connector — and its background refresh threads — at
import time, before the ASGI bridge forks. Inheriting one across a fork is what
made every request 504 once already, which is why `db.py` keys the connector to
the PID. Schema setup happens on first use instead, and is itself PID-keyed so
a forked child re-runs it against its own connection.
"""

import logging
import os
import time

from agent import db
from agent.db_paths import PROCUREMENT_DB as DB_PATH

log = logging.getLogger("agent.rate_limiter")

# Global limit: max 30 requests per minute
GLOBAL_MAX_REQUESTS_PER_MINUTE = 30

#: PIDs whose schema has been ensured. Same guard as the connector in db.py:
#: a forked child must not assume the parent's setup applies to its connection.
_initialised_pids: set[int] = set()


def _ensure_schema(conn) -> None:
    """Create the counter table if it is not there yet, once per process."""
    pid = os.getpid()
    if pid in _initialised_pids:
        return

    # AUTOINCREMENT and the ? placeholders are translated by db.connect() for
    # Postgres; the SQL is kept as written so this reads the same on both.
    conn.execute('''
        CREATE TABLE IF NOT EXISTS global_rate_limit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL
        )
    ''')
    conn.commit()
    _initialised_pids.add(pid)


def init_global_limiter() -> None:
    """
    Ensure the counter table exists.

    Kept as a public function because callers and tests refer to it, but it is
    no longer invoked at import — see the module docstring.
    """
    with db.connect(DB_PATH) as conn:
        _ensure_schema(conn)


def check_global_rate_limit() -> bool:
    """
    Whether this request is within the global API limit (Layer 3).

    Returns True if allowed, False if rate limited. Fails **closed**: if the
    counter cannot be read, the request is refused rather than waved through. A
    throttle that disables itself when its storage is unreachable is not a
    throttle, and the thing on the other side of it costs money per call.
    """
    now = time.time()
    one_minute_ago = now - 60.0

    try:
        with db.connect(DB_PATH) as conn:
            _ensure_schema(conn)

            # Drop entries that have aged out of the window.
            conn.execute(
                "DELETE FROM global_rate_limit WHERE timestamp < ?",
                (one_minute_ago,),
            )

            cur = conn.execute("SELECT COUNT(*) FROM global_rate_limit")
            count = cur.fetchone()[0]

            if count >= GLOBAL_MAX_REQUESTS_PER_MINUTE:
                conn.commit()  # keep the cleanup above
                return False

            conn.execute(
                "INSERT INTO global_rate_limit (timestamp) VALUES (?)",
                (now,),
            )
            conn.commit()
            return True
    except Exception:
        # Logged, not swallowed quietly: refusing every request is a loud
        # symptom and whoever is paged needs to see why.
        log.exception("rate limiter could not reach its counter; refusing the request")
        return False
