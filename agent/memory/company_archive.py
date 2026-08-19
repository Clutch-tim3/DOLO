"""
Archived company documents, keyed by the company that owns them.

WHY THIS EXISTS
---------------
The archive was `company_archive.json`: a flat list holding every company's
records with no company_id anywhere in it. Anything built on it was
cross-tenant by construction — `get_archived_companies()` took no argument and
could not have filtered even if a caller wanted it to. Five routes read it,
including the compliance dashboard.

It had a second problem that is easy to miss behind the first. The file lived
at `DATA_DIR / "company_archive.json"`, and DATA_DIR is `/tmp/data` whenever
K_SERVICE is set. On Cloud Run that is per-instance and wiped on cold start, so
the archive was not only shared, it was disappearing — the same fault as the
nine sqlite call sites in app.py.

State goes through `agent/db.py` now: Postgres in production, SQLite locally.

WHAT IS DELIBERATELY NOT CARRIED OVER
-------------------------------------
The old reader scanned UPLOAD_FOLDER for PDFs that no record mentioned and
attached them to a company — by name match, and failing that:

    # Fallback: if only one company exists in the ledger, associate it there
    if not target_company and len(data) == 1:
        target_company = data[0]

UPLOAD_FOLDER is shared by every tenant. That fallback attaches a stranger's
uploaded document to whichever company happens to be the only one in the
ledger, and the name-match path above it searched across all companies too. A
file that is not associated with anything is recoverable; a file silently
attached to the wrong company is a disclosure. The scan is gone.

ROWS WITH NO OWNER
------------------
Records migrated from the JSON file carry company_id NULL, because the file
never recorded who they belonged to and inventing an owner is worse than
having none. They match no company and are returned to nobody.
`scripts/migrate_company_archive.py --assign-to <company_id>` is how a human
gives them one.
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Optional

from agent import db
from agent.db_paths import PROCUREMENT_DB as DB_PATH

#: Columns stored as their own field. `files` is a JSON array; the rest are
#: scalars the UI reads directly.
_SCALAR_COLUMNS = (
    "company_name",
    "registration_number",
    "supplier_number",
    "bbbee_level",
    "cipc_uploaded",
    "csd_uploaded",
    "cipc_count",
    "csd_count",
)

_BOOL_COLUMNS = ("cipc_uploaded", "csd_uploaded")

_schema_ready: set = set()


def _ensure_schema(conn) -> None:
    """Create the table once per process. Lazy, for the same reason as the
    rate limiter: building the Cloud SQL connector at import puts its
    background threads on the wrong side of the ASGI fork."""
    pid = os.getpid()
    if pid in _schema_ready:
        return

    conn.execute("""
        CREATE TABLE IF NOT EXISTS company_archive (
            archive_id          TEXT PRIMARY KEY,
            company_id          TEXT,
            company_name        TEXT,
            registration_number TEXT,
            supplier_number     TEXT,
            bbbee_level         INTEGER,
            cipc_uploaded       INTEGER DEFAULT 0,
            csd_uploaded        INTEGER DEFAULT 0,
            cipc_count          INTEGER DEFAULT 0,
            csd_count           INTEGER DEFAULT 0,
            files               TEXT
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_company_archive_company
        ON company_archive (company_id)
    """)
    conn.commit()
    _schema_ready.add(pid)


def _row_to_record(row) -> dict:
    """Back into the dict shape the routes and the frontend already expect."""
    record = {column: row[column] for column in _SCALAR_COLUMNS}

    for column in _BOOL_COLUMNS:
        record[column] = bool(record.get(column))

    try:
        record["files"] = json.loads(row["files"]) if row["files"] else []
    except (TypeError, ValueError):
        record["files"] = []

    return record


def get_archived_companies(company_id: str) -> list:
    """
    This company's archived companies.

    `company_id` is required and has no default. That absence is the fix: the
    previous signature took no argument, so every caller got everybody's
    records whether it wanted them or not.
    """
    if not company_id:
        raise ValueError("company_id is required; there is no unscoped read")

    with db.connect(DB_PATH) as conn:
        _ensure_schema(conn)
        cur = conn.execute(
            "SELECT * FROM company_archive WHERE company_id = ? ORDER BY company_name",
            (company_id,),
        )
        return [_row_to_record(r) for r in cur.fetchall()]


def save_archived_companies(companies: list, company_id: str) -> bool:
    """
    Replace this company's archive with `companies`.

    Scoped delete-then-insert: it mirrors what the JSON writer did (dump the
    whole list) without touching a row that belongs to anyone else. Rows with a
    NULL company_id are never removed here — they are not this company's to
    delete.
    """
    if not company_id:
        raise ValueError("company_id is required; there is no unscoped write")

    try:
        with db.connect(DB_PATH) as conn:
            _ensure_schema(conn)
            conn.execute("DELETE FROM company_archive WHERE company_id = ?", (company_id,))

            for record in companies:
                conn.execute(
                    """INSERT INTO company_archive
                       (archive_id, company_id, company_name, registration_number,
                        supplier_number, bbbee_level, cipc_uploaded, csd_uploaded,
                        cipc_count, csd_count, files)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        str(uuid.uuid4()),
                        company_id,
                        record.get("company_name"),
                        record.get("registration_number"),
                        record.get("supplier_number"),
                        record.get("bbbee_level"),
                        int(bool(record.get("cipc_uploaded"))),
                        int(bool(record.get("csd_uploaded"))),
                        record.get("cipc_count") or 0,
                        record.get("csd_count") or 0,
                        json.dumps(record.get("files") or []),
                    ),
                )
            conn.commit()
        return True
    except Exception as exc:
        print(f"Failed to save archive: {exc}")
        return False


def count_archived_companies(company_id: str) -> int:
    """How many companies this one has archived. Scoped like everything else —
    a global count across tenants is a business metric about other customers."""
    return len(get_archived_companies(company_id))


def unowned_records(conn=None) -> list:
    """
    Records migrated from the JSON file that nobody owns yet.

    Used by the migration script to report what is invisible and by tests to
    assert those rows are returned to no company.
    """
    def _read(c):
        _ensure_schema(c)
        cur = c.execute("SELECT * FROM company_archive WHERE company_id IS NULL")
        return [_row_to_record(r) for r in cur.fetchall()]

    if conn is not None:
        return _read(conn)
    with db.connect(DB_PATH) as conn:
        return _read(conn)
