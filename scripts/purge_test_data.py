#!/usr/bin/env python3
"""
A10 — remove test packs and placeholder profile values before launch.

WHY THIS IS A SCRIPT AND NOT A COMMIT

It deletes production rows and the files behind them. Cloud SQL has no backups
(LAUNCH_PLAN B1: backupConfiguration.enabled = False), so until that is turned
on there is no restore path and this is unrecoverable. It is deliberately not
run by anything automatically, and it will not act without --confirm.

Recommended order:
    1. Enable Cloud SQL backups (B1).
    2. Run this with no flags. It changes nothing and prints what it would do.
    3. Run it with --confirm.

USAGE

    python scripts/purge_test_data.py                      # dry run, default
    python scripts/purge_test_data.py --company enterprise_corp
    python scripts/purge_test_data.py --confirm            # actually delete

WHAT IT TOUCHES

  autofill_packs / autofill_pack_files / autofill_pack_event
      Packs belonging to the named company, and the stored files behind them.

  company_profile
      Placeholder values only, and only ones matching PLACEHOLDERS below. A
      real value that happens to sit in the same column is left alone —
      matching on the specific known placeholder rather than blanking the
      field is what keeps this from destroying real data.

It never deletes a user account, and it never touches a company other than the
one named.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from agent import db  # noqa: E402
from agent.db_paths import PROCUREMENT_DB as DB_PATH  # noqa: E402

#: The account whose test data is being removed. LAUNCH_PLAN A10 names
#: enterprise_corp as the Test account holding six packs.
DEFAULT_COMPANY = "enterprise_corp"

#: Exact placeholder values, per LAUNCH_PLAN A10. Matched literally: anything
#: else in these columns is a real value and is not touched.
PLACEHOLDERS = {
    "registration_number": "4999999999",
    "tax_clearance_pin": "TCS-TESTPIN",
    "tax_reference_number": "4999999999",
}


def _table_exists(conn, table: str) -> bool:
    """table_columns returns an empty set for a table that is not there."""
    return bool(db.table_columns(conn, table))


def find_packs(conn, company_id: str) -> list:
    if not _table_exists(conn, "autofill_packs"):
        return []
    cur = conn.execute(
        "SELECT pack_id, pack_name, status, created_at FROM autofill_packs WHERE company_id = ?",
        (company_id,),
    )
    return [dict(r) for r in cur.fetchall()]


def find_pack_files(conn, pack_ids: list) -> list:
    if not pack_ids or not _table_exists(conn, "autofill_pack_files"):
        return []
    marks = ",".join("?" for _ in pack_ids)
    cur = conn.execute(
        f"SELECT file_id, pack_id, original_filename, storage_path "
        f"FROM autofill_pack_files WHERE pack_id IN ({marks})",
        tuple(pack_ids),
    )
    return [dict(r) for r in cur.fetchall()]


def find_placeholders(conn, company_id: str) -> dict:
    if not _table_exists(conn, "company_profile"):
        return {}
    columns = db.table_columns(conn, "company_profile")
    present = {c: v for c, v in PLACEHOLDERS.items() if c in columns}
    if not present:
        return {}

    cur = conn.execute(
        f"SELECT {', '.join(present)} FROM company_profile WHERE company_id = ?",
        (company_id,),
    )
    row = cur.fetchone()
    if row is None:
        return {}
    return {col: expected for col, expected in present.items() if row[col] == expected}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--company", default=DEFAULT_COMPANY,
                    help=f"company_id to purge (default: {DEFAULT_COMPANY})")
    ap.add_argument("--confirm", action="store_true",
                    help="actually delete. Without this nothing is written.")
    args = ap.parse_args()

    print(f"database : {'Cloud SQL' if db.is_postgres() else DB_PATH}")
    print(f"company  : {args.company}")
    print(f"mode     : {'DELETE' if args.confirm else 'dry run (nothing will be written)'}")
    print()

    with db.connect(DB_PATH) as conn:
        packs = find_packs(conn, args.company)
        pack_ids = [p["pack_id"] for p in packs]
        files = find_pack_files(conn, pack_ids)
        placeholders = find_placeholders(conn, args.company)

        print(f"packs to remove: {len(packs)}")
        for p in packs:
            print(f"  {p['pack_id']}  {p.get('status'):<10}  {p.get('pack_name')}")

        print(f"\nstored files to remove: {len(files)}")
        on_disk = 0
        for f in files:
            path = f.get("storage_path")
            exists = bool(path) and Path(path).exists()
            on_disk += exists
            print(f"  {f.get('original_filename')}  [{'on disk' if exists else 'missing'}]")

        print(f"\nplaceholder profile values to clear: {len(placeholders)}")
        for col, value in placeholders.items():
            print(f"  {col} = {value!r}")

        if not (packs or files or placeholders):
            print("\nNothing to do.")
            return 0

        if not args.confirm:
            print("\nDry run. Re-run with --confirm to apply.")
            print("Enable Cloud SQL backups first (LAUNCH_PLAN B1) — there is no restore path today.")
            return 0

        # Files first: a pack row with no file is recoverable information, a
        # file with no pack row is an orphan nothing points at.
        removed_files = 0
        for f in files:
            path = f.get("storage_path")
            if path and Path(path).exists():
                try:
                    Path(path).unlink()
                    removed_files += 1
                except OSError as exc:
                    print(f"  could not remove {path}: {exc}")

        if pack_ids:
            marks = ",".join("?" for _ in pack_ids)
            conn.execute(f"DELETE FROM autofill_pack_files WHERE pack_id IN ({marks})", tuple(pack_ids))
            if _table_exists(conn, "autofill_pack_event"):
                conn.execute(f"DELETE FROM autofill_pack_event WHERE pack_id IN ({marks})", tuple(pack_ids))
            conn.execute("DELETE FROM autofill_packs WHERE company_id = ?", (args.company,))

        for col, value in placeholders.items():
            # Scoped by the placeholder value as well as the company, so a
            # concurrent edit that replaced it with something real survives.
            conn.execute(
                f"UPDATE company_profile SET {col} = NULL WHERE company_id = ? AND {col} = ?",
                (args.company, value),
            )

        conn.commit()

        print(f"\nremoved {len(packs)} packs, {len(files)} file rows, "
              f"{removed_files} files from disk, {len(placeholders)} placeholder values")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
