"""
Central SQLite path resolution.

Every DB path used to be hardcoded relative to the repo root, e.g.

    DB_PATH = Path(__file__).parent.parent / "agent_memory.db"

That works locally and breaks on Firebase. The deployed function bundle is
**read-only**, and `*.db` is gitignored so no database is uploaded in the first
place. Each of company_store / subscription / quote_audit_log / cost_tracking /
rate_limiter calls its init_*() at import time, so on Cloud Functions the very
first import would try to CREATE TABLE inside a read-only directory and raise
`sqlite3.OperationalError: unable to open database file` — taking the whole
function down before any request is served.

On Cloud Functions / Cloud Run the only writable location is /tmp, so that is
where the databases go there.

    LIMITATION: /tmp is per-instance and ephemeral. Company memory, quote audit
    history and usage quotas will NOT persist across cold starts or scale-out.
    That is enough to make the deployed agent run and demo correctly; it is not
    durable storage. Moving these tables to Firestore or Cloud SQL is the real
    fix and is a separate piece of work.

Local behaviour is unchanged: the same files in the same places as before.
"""

import os
import shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _on_serverless() -> bool:
    # K_SERVICE is set by Cloud Run and Cloud Functions gen2; FUNCTION_TARGET by
    # the functions framework. app.py already keys its logging setup off K_SERVICE.
    return bool(os.environ.get("K_SERVICE") or os.environ.get("FUNCTION_TARGET"))


def _writable_dir() -> Path:
    override = os.environ.get("AGENT_DB_DIR")
    root = Path(override) if override else Path("/tmp") / "dolo-db"
    root.mkdir(parents=True, exist_ok=True)
    return root


def resolve(local_path: Path) -> Path:
    """
    Return the path to use for a SQLite file.

    Locally: `local_path`, untouched.
    On Cloud Functions: a writable copy under /tmp, seeded from `local_path` if
    that file happens to have been bundled.
    """
    if not (_on_serverless() or os.environ.get("AGENT_DB_DIR")):
        local_path.parent.mkdir(parents=True, exist_ok=True)
        return local_path

    target = _writable_dir() / local_path.name
    if not target.exists() and local_path.exists():
        try:
            shutil.copy2(local_path, target)
        except OSError:
            # Seed copy is best-effort; init_*() will create empty tables.
            pass
    return target


AGENT_MEMORY_DB = resolve(PROJECT_ROOT / "agent_memory.db")
PROCUREMENT_DB = resolve(PROJECT_ROOT / "data" / "procurement.db")
