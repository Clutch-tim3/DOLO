"""
Who owns each file in the generated directory.

`/api/generated/<filename>` serves everything this application produces —
quotation PDFs, accreditation roadmaps, and Agent Autofill's reviewed bid
documents. It verified that an autofill export's stamp was genuine and never
checked that the person asking for it was entitled to it. A filename was
sufficient authority. That is an IDOR on a reviewed bid document: company
registration numbers, tax reference, CSD number, B-BBEE level, director names
and ID numbers, and whatever the tender itself contains.

The stamp check answers "is this document what it claims to be". It cannot
answer "is this yours", because the document does not know who is asking.

Filenames carry a uuid4 fragment, which makes guessing impractical — but a
filename travels: it is in a chat transcript, a browser history, a shared
screenshot, a support ticket, and an access log. Unguessable is not the same as
private, and the difference matters most for exactly these documents.

So ownership is recorded when a file is created and checked when it is served.

FAIL CLOSED. A file with no recorded owner is refused rather than served. The
alternative — serve anything unregistered — would mean any generator that
forgot to register produced world-readable bid documents, and the failure would
be silent. This way a missed registration is an obvious broken download during
development rather than a quiet leak in production.
"""

from __future__ import annotations

import logging
from datetime import datetime

from agent import db
from agent.db_paths import AGENT_MEMORY_DB as DB_PATH

logger = logging.getLogger("agent.generated_files")


def init_generated_files_db() -> None:
    with db.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS generated_file_owner (
                filename   TEXT PRIMARY KEY,
                company_id TEXT NOT NULL,
                kind       TEXT,
                created_at TIMESTAMP
            )
        """)


init_generated_files_db()


def register(filename: str, company_id: str, kind: str = "", conn=None) -> None:
    """
    Record who a generated file belongs to.

    Called at creation, beside the write. Deliberately not raising when
    `company_id` is missing: a generator that cannot name an owner should still
    fail visibly at download time rather than crash mid-generation and lose the
    user's work. The absent row is what refuses the download.

    `conn` MUST be passed when the caller already holds an open connection to
    this database. Opening a second one inside an outer write transaction
    deadlocks against it — SQLite blocks the inner writer until the busy
    timeout expires and then raises "database is locked". That is not
    theoretical: it broke every reviewed export in `export_reviewed`, which
    holds its transaction open across the stamp so the status change and the
    file cannot disagree.
    """
    name = (filename or "").strip()
    if not name:
        return
    if not company_id:
        logger.warning(
            "generated file %s has no owner; it will not be servable", name)
        return

    sql = ("INSERT OR IGNORE INTO generated_file_owner"
           " (filename, company_id, kind, created_at) VALUES (?, ?, ?, ?)")
    params = (name, company_id, kind or "",
              datetime.now().isoformat(timespec="seconds"))

    if conn is not None:
        conn.execute(sql, params)
        _mirror(name)
        return
    with db.connect(DB_PATH) as own:
        own.execute(sql, params)
    _mirror(name)


def _mirror(name: str) -> None:
    """
    Copy the file into durable storage, if there is any.

    Registration is the right moment: it is the point at which a generated file
    becomes something a user can be handed a link to, and every generator
    already calls it beside the write.

    Without this the ownership row outlives the document. That is not a
    hypothetical — a filled SBD 1 in production downloaded fine seconds after it
    was made and returned "File not found or expired" ten minutes later, with
    its row still sitting in Cloud SQL, because `/tmp` belongs to one instance.

    Failure is logged and swallowed. The alternative is a generator that
    completes the work and then throws away the result over a storage error,
    and the file is still on this instance either way.
    """
    try:
        from agent.file_paths import safe_generated_path
        from agent import object_store

        if not object_store.enabled():
            return
        object_store.upload(safe_generated_path(name), name)
    except Exception:  # noqa: BLE001 - mirroring must never fail a generation
        logger.exception("could not mirror %s to durable storage", name)


def owner_of(filename: str) -> str | None:
    """The company a file belongs to, or None if nothing recorded."""
    name = (filename or "").strip()
    if not name:
        return None
    with db.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT company_id FROM generated_file_owner WHERE filename = ?",
            (name,),
        ).fetchone()
    return row["company_id"] if row else None


def belongs_to(filename: str, company_id: str) -> bool:
    """
    Whether `company_id` may download `filename`.

    False for an unregistered file, which is the fail-closed half — see the
    module docstring for why that is the safer default.
    """
    if not company_id:
        return False
    owner = owner_of(filename)
    return owner is not None and owner == company_id
