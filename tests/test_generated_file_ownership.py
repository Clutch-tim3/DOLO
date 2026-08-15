"""
Who may download a generated file.

`/api/generated/<filename>` serves quotation PDFs, accreditation roadmaps and
Agent Autofill's reviewed bid documents. It verified that an autofill export's
stamp was genuine — added earlier the same day — and never checked that the
caller owned it. A filename was sufficient authority.

The stamp answers "is this document what it claims to be". It cannot answer
"is this yours", because the file does not know who is asking. Those are
different questions and both have to be asked.

The uuid4 in each filename makes guessing impractical, which is why this is an
IDOR rather than an enumeration bug — but filenames travel. Chat transcripts,
browser history, screenshots, support tickets, access logs. Unguessable is not
private, and these documents carry registration and tax numbers, director ID
numbers, and the bid itself.
"""

import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("AGENT_DB_DIR", tempfile.mkdtemp(prefix="cairo-own-db-"))
os.environ.setdefault("AGENT_GENERATED_DIR", tempfile.mkdtemp(prefix="cairo-own-gen-"))

from agent import auth, generated_files                                   # noqa: E402
from agent.file_paths import generated_dir                                # noqa: E402


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app import app

    return TestClient(app)


def _headers_for(company_id):
    import uuid

    user = auth.create_user(f"own-{uuid.uuid4().hex[:10]}@example.test",
                            company_id, "not-a-real-password")
    return {"Authorization": f"Bearer {auth.issue_session(user)}"}


@pytest.fixture
def owned_file():
    """A generated file belonging to pro_corp."""
    import uuid

    name = f"quote_{uuid.uuid4().hex[:8]}.pdf"
    (generated_dir() / name).write_bytes(b"%PDF-1.4 pretend bid document")
    generated_files.register(name, "pro_corp", "quotation")
    yield name
    (generated_dir() / name).unlink(missing_ok=True)


# --- the IDOR --------------------------------------------------------------


def test_another_company_cannot_download_it(client, owned_file):
    """THE FINDING. A filename used to be the whole authorisation."""
    r = client.get(f"/api/generated/{owned_file}", headers=_headers_for("enterprise_corp"))
    assert r.status_code == 404, "IDOR: another tenant downloaded the file"
    assert b"PDF" not in r.content


def test_anonymous_cannot_download_it(client, owned_file):
    assert client.get(f"/api/generated/{owned_file}").status_code == 401


def test_the_owner_can_download_it(client, owned_file):
    """The gate must not become a wall."""
    r = client.get(f"/api/generated/{owned_file}", headers=_headers_for("pro_corp"))
    assert r.status_code == 200
    assert r.content.startswith(b"%PDF")


def test_refusal_is_indistinguishable_from_absence(client, owned_file):
    """
    A 403 would confirm the file exists to someone holding only a filename.
    Both answers are 404 so the response says nothing either way.
    """
    theirs = client.get(f"/api/generated/{owned_file}",
                        headers=_headers_for("enterprise_corp"))
    missing = client.get("/api/generated/quote_deadbeef.pdf",
                         headers=_headers_for("enterprise_corp"))
    assert theirs.status_code == missing.status_code == 404
    assert theirs.json() == missing.json()


# --- fail closed -----------------------------------------------------------


def test_an_unregistered_file_is_refused_even_to_a_real_user(client):
    """
    A generator that forgets to register produces an unowned file. Serving it
    would make it world-readable to every logged-in tenant, and the failure
    would be silent. Refusing makes it an obvious broken download instead.
    """
    import uuid

    name = f"orphan_{uuid.uuid4().hex[:8]}.pdf"
    (generated_dir() / name).write_bytes(b"%PDF-1.4 nobody owns this")
    try:
        r = client.get(f"/api/generated/{name}", headers=_headers_for("pro_corp"))
        assert r.status_code == 404
    finally:
        (generated_dir() / name).unlink(missing_ok=True)


def test_registration_with_no_company_records_nothing(caplog):
    """Rather than recording a file owned by the empty string."""
    generated_files.register("no_owner.pdf", "", "quotation")
    assert generated_files.owner_of("no_owner.pdf") is None
    assert generated_files.belongs_to("no_owner.pdf", "") is False


def test_belongs_to_is_false_for_unknown_files():
    assert generated_files.belongs_to("never_generated.pdf", "pro_corp") is False


def test_first_registration_wins(owned_file):
    """
    A second register() for the same filename must not silently transfer
    ownership — filenames carry a uuid4, so a collision means something is
    wrong, and reassigning would be the wrong resolution.
    """
    generated_files.register(owned_file, "enterprise_corp", "quotation")
    assert generated_files.owner_of(owned_file) == "pro_corp"


# --- the generators actually register --------------------------------------


def test_every_generator_records_an_owner():
    """
    Ownership is recorded beside each write. If a generator stops registering,
    its output becomes undownloadable — so this asserts the call is present
    rather than waiting for a user to find a broken link.
    """
    import re

    sources = {
        "agent/quotation/quote_builder.py": 1,
        "agent/onboarding/accreditation_report.py": 1,
        "agent_autofill/integration/review_gate.py": 2,
    }
    for path, expected in sources.items():
        text = (ROOT / path).read_text(encoding="utf-8")
        calls = len(re.findall(r"register_generated\(", text))
        assert calls >= expected, f"{path}: {calls} registrations, expected {expected}"


# --- durable storage, and who may cause a fetch from it --------------------


def test_a_missing_local_file_is_restored_for_its_owner(client, monkeypatch):
    """
    `/tmp` is per-instance, so the instance answering a download is often not
    the one that produced the file. A real filled SBD 1 downloaded fine seconds
    after it was made and 404'd ten minutes later with its ownership row still
    in the database.
    """
    import uuid

    from agent import object_store

    name = f"quote_{uuid.uuid4().hex[:8]}.pdf"
    generated_files.register(name, "pro_corp", "quotation")
    # Registered but NOT on this instance's disk — the cold-instance case.
    assert not (generated_dir() / name).exists()

    def restore(filename, local_path):
        Path(local_path).write_bytes(b"%PDF-1.4 restored from the bucket")
        return True

    monkeypatch.setattr(object_store, "ensure_local", restore)

    r = client.get(f"/api/generated/{name}", headers=_headers_for("pro_corp"))
    assert r.status_code == 200
    assert b"restored from the bucket" in r.content
    (generated_dir() / name).unlink(missing_ok=True)


def test_a_non_owner_cannot_cause_a_fetch_from_storage(client, monkeypatch):
    """
    Ownership is checked BEFORE the restore, and this is why.

    The route used to check existence first, which was harmless while files
    only ever sat on local disk. Once a miss can pull an object out of a
    bucket, that order would let anyone holding a filename cause a fetch —
    turning a 404 into a way to move someone else's document onto a machine
    and into its logs and metrics.
    """
    import uuid

    from agent import object_store

    name = f"quote_{uuid.uuid4().hex[:8]}.pdf"
    generated_files.register(name, "pro_corp", "quotation")

    attempts = []

    def record(filename, local_path):
        attempts.append(filename)
        return False

    monkeypatch.setattr(object_store, "ensure_local", record)

    r = client.get(f"/api/generated/{name}", headers=_headers_for("enterprise_corp"))
    assert r.status_code == 404
    assert attempts == [], (
        f"storage was consulted for a caller who does not own the file: {attempts}")
