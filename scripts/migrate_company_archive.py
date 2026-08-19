#!/usr/bin/env python3
"""
A2 — move company_archive.json into the company_archive table.

The JSON file never recorded who a record belonged to, so this cannot work out
an owner for the rows it imports. It refuses to guess: imported records get
company_id NULL, which means they are returned to nobody until a human assigns
them with --assign-to.

That is the safe direction. A record with no owner is invisible; a record given
the wrong owner is a disclosure, and with one company in the ledger the obvious
guess ("it must be the only company") is exactly the reasoning that made the
old disk-scan fallback cross-tenant.

USAGE

    python scripts/migrate_company_archive.py                    # dry run
    python scripts/migrate_company_archive.py --confirm          # import, unowned
    python scripts/migrate_company_archive.py --confirm --assign-to enterprise_corp
    python scripts/migrate_company_archive.py --list-unowned     # what is invisible

The import is idempotent on company_name: re-running it will not duplicate a
record that is already in the table.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from agent import db  # noqa: E402
from agent.db_paths import PROCUREMENT_DB as DB_PATH  # noqa: E402
from agent.memory import company_archive  # noqa: E402


def _candidate_json_paths() -> list:
    """Where the file may be. On Cloud Run it was /tmp/data and is very likely
    already gone — that loss is the other half of what A2 fixes."""
    return [
        PROJECT_ROOT / "data" / "company_archive.json",
        Path("/tmp/data/company_archive.json"),
    ]


def load_json_records() -> tuple:
    for path in _candidate_json_paths():
        try:
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8")), path
        except (OSError, ValueError) as exc:
            print(f"  could not read {path}: {exc}")
    return [], None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--confirm", action="store_true", help="actually write rows")
    ap.add_argument("--assign-to", metavar="COMPANY_ID",
                    help="owner for imported records. Without it they are imported unowned "
                         "and returned to nobody.")
    ap.add_argument("--list-unowned", action="store_true",
                    help="list records already in the table that have no owner, and exit")
    args = ap.parse_args()

    if args.list_unowned:
        orphans = company_archive.unowned_records()
        print(f"records with no owner: {len(orphans)}")
        for r in orphans:
            print(f"  {r['company_name']}  ({len(r['files'])} files)")
        if orphans:
            print("\nAssign them with --assign-to <company_id>.")
        return 0

    records, source = load_json_records()
    print(f"source   : {source or 'no company_archive.json found'}")
    print(f"records  : {len(records)}")
    print(f"owner    : {args.assign_to or 'NULL (invisible until assigned)'}")
    print(f"mode     : {'WRITE' if args.confirm else 'dry run (nothing will be written)'}")
    print()

    if not records:
        print("Nothing to migrate.")
        if source is None:
            print("On Cloud Run this file lived in /tmp and is wiped on cold start,")
            print("so its absence may mean the archive was already lost rather than empty.")
        return 0

    with db.connect(DB_PATH) as conn:
        company_archive._ensure_schema(conn)

        cur = conn.execute("SELECT company_name FROM company_archive")
        existing = {r["company_name"] for r in cur.fetchall()}

        to_import = [r for r in records if r.get("company_name") not in existing]
        skipped = len(records) - len(to_import)

        for r in to_import:
            print(f"  + {r.get('company_name')}  ({len(r.get('files') or [])} files)")
        if skipped:
            print(f"  ({skipped} already present, skipped)")

        if not args.confirm:
            print("\nDry run. Re-run with --confirm to apply.")
            return 0

        for r in to_import:
            conn.execute(
                """INSERT INTO company_archive
                   (archive_id, company_id, company_name, registration_number,
                    supplier_number, bbbee_level, cipc_uploaded, csd_uploaded,
                    cipc_count, csd_count, files)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid.uuid4()),
                    args.assign_to,                       # None unless told otherwise
                    r.get("company_name"),
                    r.get("registration_number"),
                    r.get("supplier_number"),
                    r.get("bbbee_level"),
                    int(bool(r.get("cipc_uploaded"))),
                    int(bool(r.get("csd_uploaded"))),
                    r.get("cipc_count") or 0,
                    r.get("csd_count") or 0,
                    json.dumps(r.get("files") or []),
                ),
            )
        conn.commit()

    print(f"\nimported {len(to_import)} records"
          + (f" owned by {args.assign_to}" if args.assign_to else " with no owner"))
    if not args.assign_to:
        print("They are returned to nobody. Assign with --assign-to <company_id>.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
