"""
The company archive belongs to somebody.

It was `company_archive.json`: a flat list holding every company's archived
documents with no company_id anywhere in it. `get_archived_companies()` took no
argument, so the five routes reading it — including the compliance dashboard —
could not have filtered even if they wanted to.

It had a second fault behind the first. The file lived at
`DATA_DIR / "company_archive.json"`, and DATA_DIR is `/tmp/data` when K_SERVICE
is set, so on Cloud Run the archive was not only shared but wiped on every cold
start.
"""

import json
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from agent import db
from agent.db_paths import PROCUREMENT_DB as DB_PATH
from agent.memory import company_archive

APP_SOURCE = (Path(__file__).resolve().parent.parent / "app.py").read_text(encoding="utf-8")


def _record(name, files=None):
    return {
        "company_name": name,
        "registration_number": "2020/123456/07",
        "supplier_number": "MAAA1234567",
        "bbbee_level": 2,
        "cipc_uploaded": True,
        "csd_uploaded": False,
        "cipc_count": 1,
        "csd_count": 0,
        "files": files if files is not None else [f"{name}.pdf"],
    }


@pytest.fixture
def two_companies():
    alice, bob = f"alice-{uuid.uuid4().hex[:8]}", f"bob-{uuid.uuid4().hex[:8]}"
    company_archive.save_archived_companies([_record("ALICE HOLDINGS")], alice)
    company_archive.save_archived_companies([_record("BOB TRADING")], bob)
    yield alice, bob
    company_archive.save_archived_companies([], alice)
    company_archive.save_archived_companies([], bob)


def test_each_company_sees_only_its_own(two_companies):
    alice, bob = two_companies

    alice_names = [r["company_name"] for r in company_archive.get_archived_companies(alice)]
    bob_names = [r["company_name"] for r in company_archive.get_archived_companies(bob)]

    assert alice_names == ["ALICE HOLDINGS"]
    assert bob_names == ["BOB TRADING"]
    assert "BOB TRADING" not in alice_names, "another company's archive is readable"


def test_a_write_does_not_disturb_another_company(two_companies):
    """save_ replaces the caller's records. It used to dump the whole list."""
    alice, bob = two_companies

    company_archive.save_archived_companies([_record("ALICE REPLACED")], alice)

    assert [r["company_name"] for r in company_archive.get_archived_companies(bob)] == ["BOB TRADING"]
    assert [r["company_name"] for r in company_archive.get_archived_companies(alice)] == ["ALICE REPLACED"]


def test_an_unscoped_read_is_impossible():
    """
    The old signature took no argument. There is no way to spell that now —
    the absence of a default is the fix, exactly as with require_company_id.
    """
    with pytest.raises(TypeError):
        company_archive.get_archived_companies()
    for empty in (None, ""):
        with pytest.raises(ValueError):
            company_archive.get_archived_companies(empty)
        with pytest.raises(ValueError):
            company_archive.save_archived_companies([], empty)


def test_records_with_no_owner_are_returned_to_nobody():
    """
    Rows migrated from the JSON file carry NULL, because the file never said
    who they belonged to. Invisible is the safe direction.
    """
    orphan = f"ORPHAN {uuid.uuid4().hex[:8]}"
    with db.connect(DB_PATH) as conn:
        company_archive._ensure_schema(conn)
        conn.execute(
            "INSERT INTO company_archive (archive_id, company_id, company_name, files) "
            "VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), None, orphan, json.dumps([])),
        )
        conn.commit()

    somebody = f"someone-{uuid.uuid4().hex[:8]}"
    names = [r["company_name"] for r in company_archive.get_archived_companies(somebody)]
    assert orphan not in names

    assert orphan in [r["company_name"] for r in company_archive.unowned_records()], (
        "the migration script could not report this record as needing an owner"
    )


def test_the_files_list_survives_a_round_trip():
    """`files` is a JSON column; the routes and the UI read it as a list."""
    company_id = f"rt-{uuid.uuid4().hex[:8]}"
    files = ["COR14.3.pdf", "tax_clearance.pdf", "bbbee_cert.pdf"]
    company_archive.save_archived_companies([_record("ROUND TRIP", files)], company_id)

    got = company_archive.get_archived_companies(company_id)[0]
    assert got["files"] == files
    assert got["cipc_uploaded"] is True
    assert got["csd_uploaded"] is False
    assert got["bbbee_level"] == 2

    company_archive.save_archived_companies([], company_id)


# --- the shape of the old bug, pinned in app.py --------------------------------

def test_app_never_calls_the_archive_unscoped():
    """
    Parsed rather than grepped: the docstrings in app.py quote the old
    signature to explain what was wrong with it, and a string search counts
    that prose as a call.
    """
    import ast

    tree = ast.parse(APP_SOURCE)
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id not in ("get_archived_companies", "save_archived_companies"):
            continue
        # get_ takes company_id; save_ takes companies AND company_id.
        required = 1 if node.func.id == "get_archived_companies" else 2
        if len(node.args) < required:
            offenders.append((node.lineno, node.func.id, len(node.args)))

    assert not offenders, f"unscoped archive call at {offenders}"


def test_the_archive_is_not_read_from_a_json_file_any_more():
    """
    DATA_DIR is /tmp/data on Cloud Run, so the JSON file was ephemeral as well
    as shared. Reading it back would restore both faults at once.
    """
    assert "json.load(f)" not in APP_SOURCE or "ARCHIVE_JSON_PATH" not in APP_SOURCE.split("def get_archived_companies")[1][:800]


def test_the_cross_tenant_disk_scan_is_gone():
    """
    The reader walked the shared UPLOAD_FOLDER and attached unassociated PDFs
    to a company by name match across all companies, falling back to "if only
    one company exists, associate it there" — a stranger's upload landing on
    whoever happened to be alone in the ledger.
    """
    assert "if not target_company and len(data) == 1" not in APP_SOURCE
    assert "files_on_disk" not in APP_SOURCE
