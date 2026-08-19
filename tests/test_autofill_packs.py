"""
The Autofill Vault: a pack of documents, one review, one export gate.

What these tests are actually about — the pack is a WRAPPER, and a wrapper is
exactly the shape of thing that quietly loosens the rule it wraps. So the
assertions are less about "does the endpoint return 200" and more about:

  1. Grouping does not relax anything. A signature is still flagged and still
     unfilled; a declaration form still fills nothing; the pack cannot reach
     `reviewed` while one field in one document is unacknowledged or one
     pre-filled value in one document is unconfirmed.
  2. The tier gate is not bypassed by the new door. A Starter company's submit
     is refused with ZERO invocations of the Anthropic client — counted, not
     asserted from a status code, because "refused after paying" and "refused
     before paying" look identical from outside.
  3. A pack is never left in `processing`. Every failure path is checked for a
     terminal status and a specific reason.
  4. Tenancy. Company B cannot read, submit, acknowledge, confirm or export
     company A's pack, and gets 404 rather than 403 — a 403 would confirm the
     id is real to someone holding only an id.

NOTE ON IMPORTING `app`: this module imports it, which re-keys
AUTOFILL_STAMP_SECRET from .env.local (CLAUDE.md). test_auth.py,
test_batch_endpoint.py and test_single_endpoint.py already do, and pytest
imports every test module at collection, so this adds no new ordering hazard —
every signature in this file is made and verified after that point.

No test here makes a real API call: the classifier's client is replaced with a
recorder in a fixture.
"""

from __future__ import annotations

import sys
import uuid
import zipfile
from pathlib import Path

import pytest

from conftest import clear_company, set_company_tier
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent import auth, claude_client, db, subscription  # noqa: E402
from agent.db_paths import AGENT_MEMORY_DB  # noqa: E402
from agent.memory.company_store import update_company_profile  # noqa: E402
from agent_autofill.classification import is_tender_document as cls  # noqa: E402
from agent_autofill.integration import pack_store  # noqa: E402
from agent_autofill.integration.export_metadata import (  # noqa: E402
    STATUS_REVIEWED,
    read_review_state,
)
from app import app  # noqa: E402

client = TestClient(app)

FIXTURES = Path(__file__).parent / "fixtures"
MBD1 = FIXTURES / "sa_forms_generated" / "mbd1_supplier_info.docx"   # has a SIGNATURE row
SBD4 = FIXTURES / "sa_forms" / "REVISED SBD 4 -Annexure A.docx"      # a declaration form
ALFRED_DUMA = FIXTURES / "alfred_duma.pdf"   # 1-page summary; eligibility proof ONLY

CO_A = "pack_test_alpha"
CO_B = "pack_test_beta"
CO_STARTER = "pack_test_starter"

PROFILE = {
    "company_name": "MOLWANTWA TRADING (PTY) LTD",
    "registration_number": "2019/123456/07",
    "csd_number": "MAAA0123456",
    "tax_reference_number": "9012345678",
    "vat_registration_number": "4123456789",
    "physical_address": "12 Church Street, Ladysmith, 3370",
    "postal_address": "PO Box 91, Ladysmith, 3370",
    "standard_contact_person": "T Molwantwa",
    "standard_phone": "036 123 4567",
    "standard_cell": "082 123 4567",
    "standard_email": "bids@example.test",
    "bbbee_level": "Level 1 Contributor",
    "authorized_signatory_capacity": "Director",
}


class RecordingClaude:
    """
    Stands in for the Anthropic client and counts every invocation.

    The count is the point. A test that only reads the HTTP response cannot tell
    a refusal that happened before the request from one that happened after it,
    and the whole reason the tier check runs first is to not pay for the second.
    """

    def __init__(self):
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "content": ('{"is_tender": true, "confidence": 0.95, '
                        '"document_type": "SBD form", "reason": "test"}'),
            "tool_calls": [], "stop_reason": "end_turn", "blocks": [],
        }

    @property
    def count(self) -> int:
        return len(self.calls)


def _purge_usage_logs():
    with db.connect(AGENT_MEMORY_DB) as conn:
        conn.execute(
            "DELETE FROM usage_logs WHERE action_type = ? AND company_id IN (?, ?, ?)",
            (subscription.AUTOFILL_ACTION, CO_A, CO_B, CO_STARTER),
        )


@pytest.fixture
def tiers(monkeypatch):
    # Enterprise rather than Pro for the two working companies: Pro allows 3
    # autofills a day and several of these tests submit multi-file packs, so a
    # Pro limit would make them fail for a reason none of them is about.
    set_company_tier(CO_A, "enterprise")
    set_company_tier(CO_B, "enterprise")
    set_company_tier(CO_STARTER, "starter")
    _purge_usage_logs()
    yield
    _purge_usage_logs()
    # Companies are rows now, not dict entries a monkeypatch reverts. A test
    # that leaves one behind changes the tier of that id for every later run.
    for company_id in (CO_A, CO_B, CO_STARTER):
        clear_company(company_id)


@pytest.fixture
def claude(monkeypatch):
    recorder = RecordingClaude()
    monkeypatch.setattr(claude_client, "call_claude_with_tracking", recorder)
    # The global limiter is shared with the rest of the app; leaving it in would
    # make a full run flaky for reasons unrelated to packs.
    monkeypatch.setattr(cls.rate_limiter, "check_global_rate_limit", lambda: True)
    return recorder


@pytest.fixture
def uploads_dir(monkeypatch, tmp_path):
    """Pack uploads land under tmp_path, not in the repo."""
    monkeypatch.setenv("AGENT_UPLOAD_DIR", str(tmp_path / "uploads"))
    return tmp_path


@pytest.fixture
def generated_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_GENERATED_DIR", str(tmp_path / "generated"))
    return tmp_path / "generated"


def _actor(company_id: str) -> dict:
    user = auth.create_user(f"pack-{uuid.uuid4().hex[:10]}@example.test", company_id,
                            "pack-tests-not-a-real-password")
    return {"Authorization": f"Bearer {auth.issue_session(user)}"}


@pytest.fixture(scope="module")
def actor_a():
    update_company_profile(CO_A, dict(PROFILE), confirmed=True)
    return _actor(CO_A)


@pytest.fixture(scope="module")
def actor_b():
    update_company_profile(CO_B, dict(PROFILE), confirmed=True)
    return _actor(CO_B)


@pytest.fixture(scope="module")
def actor_starter():
    update_company_profile(CO_STARTER, dict(PROFILE), confirmed=True)
    return _actor(CO_STARTER)


# --- helpers ----------------------------------------------------------------


def _upload(headers, pack_id, *paths):
    files = [("files", (p.name, p.read_bytes(),
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"))
             for p in paths]
    return client.post(f"/api/autofill-packs/{pack_id}/files", files=files, headers=headers)


def _new_pack(headers, name="Test pack", *paths):
    created = client.post("/api/autofill-packs", json={"pack_name": name}, headers=headers)
    assert created.status_code == 200, created.text
    pack_id = created.json()["pack_id"]
    if paths:
        assert _upload(headers, pack_id, *paths).status_code == 200
    return pack_id


def _clear_everything(headers, pack_id):
    """Acknowledge every flag and confirm every value, the long way round."""
    detail = client.get(f"/api/autofill-packs/{pack_id}", headers=headers).json()
    for item in detail["outstanding"]:
        r = client.post(
            f"/api/autofill-packs/{pack_id}/acknowledge",
            json={"review_id": item["review_id"], "item_key": item["item_key"],
                  "note": f"Checked {item['label'][:30]}; I will complete it by hand."},
            headers=headers)
        assert r.status_code == 200, r.text
    for f in detail["files"]:
        if not f["review"]:
            continue
        keys = [v["item_key"] for v in f["review"]["filled_values"]]
        if not keys:
            continue
        r = client.post(
            f"/api/autofill-packs/{pack_id}/confirm-values",
            json={"review_id": f["review_id"], "confirmed_keys": keys},
            headers=headers)
        assert r.status_code == 200, r.text


# --- 1. authentication ------------------------------------------------------


PACK_ROUTES = [
    ("POST", "/api/autofill-packs"),
    ("PATCH", "/api/autofill-packs/any"),
    ("POST", "/api/autofill-packs/any/files"),
    ("DELETE", "/api/autofill-packs/any/files/any"),
    ("POST", "/api/autofill-packs/any/submit"),
    ("GET", "/api/autofill-packs/any/status"),
    ("GET", "/api/autofill-packs"),
    ("GET", "/api/autofill-packs/any"),
    ("POST", "/api/autofill-packs/any/acknowledge"),
    ("POST", "/api/autofill-packs/any/confirm-values"),
    ("POST", "/api/autofill-packs/any/export"),
]


@pytest.mark.parametrize("method,path", PACK_ROUTES)
def test_every_pack_route_refuses_an_anonymous_caller(method, path):
    """
    401 with no credential, on every one of them.

    There is no default tenant in this codebase and this feature must not
    introduce one — an anonymous caller being 'somebody' is the exact hole
    agent/auth.py was written to close.
    """
    assert client.request(method, path, json={}).status_code == 401


@pytest.mark.parametrize("method,path", PACK_ROUTES)
def test_x_company_id_header_confers_nothing(method, path):
    """The dead header stays dead. Setting it does not authenticate."""
    r = client.request(method, path, json={}, headers={"X-Company-ID": CO_A})
    assert r.status_code == 401


# --- 2. creating a pack and uploading several files at once -----------------


def test_multiple_files_in_one_request_land_under_one_pack(actor_a, uploads_dir, tiers):
    pack_id = _new_pack(actor_a, "Multi-upload")
    response = _upload(actor_a, pack_id, MBD1, SBD4, ALFRED_DUMA)
    assert response.status_code == 200, response.text

    body = response.json()
    assert len(body["files"]) == 3
    assert {f["original_filename"] for f in body["files"]} == {
        MBD1.name, SBD4.name, ALFRED_DUMA.name}
    # file_type comes from magic bytes, never the extension.
    kinds = {f["original_filename"]: f["file_type"] for f in body["files"]}
    assert kinds[ALFRED_DUMA.name] == "pdf"
    assert kinds[MBD1.name] == "docx"

    detail = client.get(f"/api/autofill-packs/{pack_id}", headers=actor_a).json()
    assert detail["file_count"] == 3
    assert {f["file_id"] for f in detail["files"]} == {f["file_id"] for f in body["files"]}

    listed = client.get("/api/autofill-packs", headers=actor_a).json()
    row = next(p for p in listed if p["pack_id"] == pack_id)
    assert row["file_count"] == 3 and row["status"] == "draft"


def test_pack_can_be_renamed_and_a_file_removed(actor_a, uploads_dir, tiers):
    pack_id = _new_pack(actor_a, "Before", MBD1, SBD4)
    assert client.patch(f"/api/autofill-packs/{pack_id}",
                        json={"pack_name": "After"}, headers=actor_a).status_code == 200

    detail = client.get(f"/api/autofill-packs/{pack_id}", headers=actor_a).json()
    assert detail["pack_name"] == "After"

    victim = detail["files"][0]["file_id"]
    assert client.delete(f"/api/autofill-packs/{pack_id}/files/{victim}",
                         headers=actor_a).status_code == 200
    after = client.get(f"/api/autofill-packs/{pack_id}", headers=actor_a).json()
    assert after["file_count"] == 1
    assert victim not in {f["file_id"] for f in after["files"]}


def test_submitting_an_empty_pack_is_refused(actor_a, uploads_dir, tiers, claude):
    pack_id = _new_pack(actor_a, "Empty")
    response = client.post(f"/api/autofill-packs/{pack_id}/submit", headers=actor_a)
    assert response.status_code == 400
    assert claude.count == 0


# --- 3. submit, and the state machine ---------------------------------------


def test_submit_moves_the_pack_from_processing_to_needs_review(
        actor_a, uploads_dir, generated_dir, tiers, claude):
    """
    The transition, observed at each step rather than inferred from the end.

    `submit_pack` and `process_pack` are called directly here so the
    intermediate `processing` row is visible; the HTTP route runs exactly the
    same two calls, with the second on a BackgroundTask.
    """
    pack_id = _new_pack(actor_a, "State machine", MBD1)

    assert pack_store.pack_status(CO_A, pack_id)["status"] == "draft"
    assert pack_store.submit_pack(CO_A, pack_id) == {"status": "processing"}
    mid = pack_store.pack_status(CO_A, pack_id)
    assert mid["status"] == "processing"
    assert mid["files_done"] == 0 and mid["files_total"] == 1

    pack_store.process_pack(CO_A, pack_id)
    done = pack_store.pack_status(CO_A, pack_id)
    assert done["status"] == "needs_review"
    assert done["files_done"] == 1 and done["files_total"] == 1
    assert done["error_reason"] is None


def test_submit_over_http_reaches_needs_review_without_intervention(
        actor_a, uploads_dir, generated_dir, tiers, claude):
    pack_id = _new_pack(actor_a, "Over HTTP", MBD1, SBD4)
    submitted = client.post(f"/api/autofill-packs/{pack_id}/submit", headers=actor_a)
    assert submitted.status_code == 200
    assert submitted.json() == {"status": "processing"}

    # TestClient drains BackgroundTasks before returning, so by the time the
    # next request is made the worker has run — with nothing else touching it.
    status = client.get(f"/api/autofill-packs/{pack_id}/status", headers=actor_a).json()
    assert status["status"] == "needs_review"
    assert status["files_done"] == status["files_total"] == 2
    assert status["error_reason"] is None


def test_a_pack_is_never_left_processing_when_the_work_explodes(
        actor_a, uploads_dir, generated_dir, tiers, claude, monkeypatch):
    """
    The worker's terminal states are written in a `finally`.

    A background task that raises would otherwise leave the pack in
    `processing` forever, with a spinner the user can never clear.
    """
    pack_id = _new_pack(actor_a, "Exploding", MBD1)
    pack_store.submit_pack(CO_A, pack_id)

    import agent_autofill.main_autofill_orchestrator as orch

    def boom(*_args, **_kwargs):
        raise RuntimeError("disk fell off")

    monkeypatch.setattr(orch, "run_autofill_batch", boom)
    pack_store.process_pack(CO_A, pack_id)

    status = pack_store.pack_status(CO_A, pack_id)
    assert status["status"] == "error"
    assert "disk fell off" in status["error_reason"]


def test_a_pack_whose_files_vanished_reports_that_specifically(
        actor_a, uploads_dir, generated_dir, tiers, claude):
    """
    /tmp is per-instance. Files uploaded to one instance and submitted against
    another are simply gone, and 'no draft was produced' would send the user
    looking in the wrong place.
    """
    pack_id = _new_pack(actor_a, "Vanished", MBD1)
    for f in pack_store._pack_files(pack_id):
        Path(f["storage_path"]).unlink()

    pack_store.submit_pack(CO_A, pack_id)
    pack_store.process_pack(CO_A, pack_id)

    status = pack_store.pack_status(CO_A, pack_id)
    assert status["status"] == "error"
    assert "could be found on disk" in status["error_reason"]
    assert claude.count == 0, "a file that is not there must not be classified"


def test_files_cannot_be_added_after_submission(
        actor_a, uploads_dir, generated_dir, tiers, claude):
    """
    A document added after the review opened would be in the pack, absent from
    the review, and invisible to the export gate.
    """
    pack_id = _new_pack(actor_a, "Sealed", MBD1)
    client.post(f"/api/autofill-packs/{pack_id}/submit", headers=actor_a)
    assert _upload(actor_a, pack_id, SBD4).status_code == 409


# --- 4. the aggregated pack review ------------------------------------------


def test_pack_review_flags_a_signature_and_a_declaration_across_documents(
        actor_a, uploads_dir, generated_dir, tiers, claude):
    """
    THE rule, at pack level: a signature is flagged and left empty, and every
    cell of a declaration form is flagged, in ONE aggregated view.

    Fixtures are real SA forms — MBD 1's supplier block (built from the real
    labels, because MBD 1 itself is an OLE2 .doc that cannot be written) and the
    genuine REVISED SBD 4 declaration annexure.
    """
    pack_id = _new_pack(actor_a, "Signature and declaration", MBD1, SBD4)
    client.post(f"/api/autofill-packs/{pack_id}/submit", headers=actor_a)

    detail = client.get(f"/api/autofill-packs/{pack_id}", headers=actor_a).json()
    assert detail["status"] == "needs_review"
    by_kind = detail["flags"]["by_kind"]

    # Signature: flagged, and never among the values that were written.
    assert by_kind.get("signature", 0) >= 1
    signature_flags = [o for o in detail["outstanding"] if o["flag_kind"] == "signature"]
    assert signature_flags, "the signature row must be outstanding, not filled"
    written_labels = {
        v["label"].strip().lower()
        for f in detail["files"] if f["review"]
        for v in f["review"]["filled_values"]
    }
    for flag in signature_flags:
        assert flag["label"].strip().lower() not in written_labels

    # Declaration: SBD 4 fills nothing at all.
    assert by_kind.get("declaration_of_interest", 0) >= 1
    sbd4 = next(f for f in detail["files"] if f["original_filename"] == SBD4.name)
    assert sbd4["review"]["filled_count"] == 0
    assert sbd4["review"]["flagged_count"] == len(sbd4["review"]["items"]) > 0
    assert all(i["flag_kind"] == "declaration_of_interest" for i in sbd4["review"]["items"])

    # And the aggregation is the sum of the parts, not a separate opinion.
    assert detail["flags"]["total"] == sum(
        f["review"]["flagged_count"] for f in detail["files"] if f["review"])
    assert detail["flags"]["outstanding"] == detail["flags"]["total"]
    assert detail["flags"]["acknowledged"] == 0
    assert detail["exportable"] is False


def test_pack_detail_carries_an_eligibility_verdict(
        actor_a, uploads_dir, generated_dir, tiers, claude):
    """
    The eligibility pipeline runs on the pack's primary tender document, reusing
    `tender_assessment.assess_tender`.

    alfred_duma.pdf is used here and ONLY here: it is a 1-page, 81-word tender
    summary with no MBD forms, so extraction correctly finds nothing in it. It
    is the eligibility/DISQUALIFIED fixture. This has misled two agents already.
    """
    pack_id = _new_pack(actor_a, "Eligibility", MBD1, ALFRED_DUMA)
    client.post(f"/api/autofill-packs/{pack_id}/submit", headers=actor_a)

    detail = client.get(f"/api/autofill-packs/{pack_id}", headers=actor_a).json()
    assessment = detail["assessment"]
    assert assessment is not None
    # The PDF is chosen over the .docx form: the win-probability model reads
    # tender PDFs, and a returnable form carries no tender terms.
    assert assessment["document"] == ALFRED_DUMA.name
    assert "eligibility" in assessment
    assert assessment["disqualified"] is True
    assert assessment["recommendation"] == "DISQUALIFIED"
    assert assessment["hard_failures"]


def test_a_pdf_only_pack_errors_rather_than_pretending_to_have_a_review(
        actor_a, uploads_dir, generated_dir, tiers, claude):
    """
    The fill engine has no PDF writer. A pack of nothing but PDFs is analysed,
    not drafted — so `needs_review` would be a lie and the status is `error`
    with the reason spelled out.
    """
    pack_id = _new_pack(actor_a, "PDF only", ALFRED_DUMA)
    client.post(f"/api/autofill-packs/{pack_id}/submit", headers=actor_a)

    status = client.get(f"/api/autofill-packs/{pack_id}/status", headers=actor_a).json()
    assert status["status"] == "error"
    assert "No draft could be produced" in status["error_reason"]


# --- 5. the export gate -----------------------------------------------------


def test_export_before_acknowledging_is_blocked_and_names_what_is_outstanding(
        actor_a, uploads_dir, generated_dir, tiers, claude):
    pack_id = _new_pack(actor_a, "Premature export", MBD1, SBD4)
    client.post(f"/api/autofill-packs/{pack_id}/submit", headers=actor_a)

    response = client.post(f"/api/autofill-packs/{pack_id}/export", headers=actor_a)
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["pack_status"] == "needs_review"
    assert detail["outstanding_flags"] > 0
    assert detail["outstanding"], "the refusal must name the fields, not just count them"
    assert {"item_key", "label", "review_id", "flag_kind"} <= set(detail["outstanding"][0])
    assert any(o["flag_kind"] == "signature" for o in detail["outstanding"])


def test_acknowledging_everything_but_the_values_still_blocks_the_export(
        actor_a, uploads_dir, generated_dir, tiers, claude):
    """
    The gap this project found and closed: the gate reviewed the GAPS, not the
    FILLS. Acknowledging every flag is not the same as a person having seen the
    values that were written, and the pack must not smuggle that back in.
    """
    pack_id = _new_pack(actor_a, "Flags only", MBD1)
    client.post(f"/api/autofill-packs/{pack_id}/submit", headers=actor_a)

    detail = client.get(f"/api/autofill-packs/{pack_id}", headers=actor_a).json()
    for item in detail["outstanding"]:
        client.post(f"/api/autofill-packs/{pack_id}/acknowledge",
                    json={"review_id": item["review_id"], "item_key": item["item_key"],
                          "note": "Seen; I will complete this by hand."},
                    headers=actor_a)

    after = client.get(f"/api/autofill-packs/{pack_id}", headers=actor_a).json()
    assert after["flags"]["outstanding"] == 0
    assert after["values"]["unconfirmed"] > 0
    assert after["status"] == "needs_review"

    response = client.post(f"/api/autofill-packs/{pack_id}/export", headers=actor_a)
    assert response.status_code == 409
    assert response.json()["detail"]["unconfirmed_values"] > 0


def test_a_blanket_acknowledgement_is_still_refused_through_a_pack(
        actor_a, uploads_dir, generated_dir, tiers, claude):
    """One field per call, with a real note. The wrapper does not add a bulk door."""
    pack_id = _new_pack(actor_a, "Blanket", MBD1)
    client.post(f"/api/autofill-packs/{pack_id}/submit", headers=actor_a)
    detail = client.get(f"/api/autofill-packs/{pack_id}", headers=actor_a).json()
    review_id = detail["outstanding"][0]["review_id"]

    assert client.post(f"/api/autofill-packs/{pack_id}/acknowledge",
                       json={"review_id": review_id, "item_key": "all",
                             "note": "everything is fine"},
                       headers=actor_a).status_code == 400
    assert client.post(f"/api/autofill-packs/{pack_id}/acknowledge",
                       json={"review_id": review_id,
                             "item_key": detail["outstanding"][0]["item_key"],
                             "note": "ok"},
                       headers=actor_a).status_code == 400


def test_a_partial_value_confirmation_is_refused(
        actor_a, uploads_dir, generated_dir, tiers, claude):
    pack_id = _new_pack(actor_a, "Partial confirm", MBD1)
    client.post(f"/api/autofill-packs/{pack_id}/submit", headers=actor_a)
    detail = client.get(f"/api/autofill-packs/{pack_id}", headers=actor_a).json()
    f = next(f for f in detail["files"] if f["review"])
    keys = [v["item_key"] for v in f["review"]["filled_values"]]

    response = client.post(f"/api/autofill-packs/{pack_id}/confirm-values",
                           json={"review_id": f["review_id"], "confirmed_keys": keys[:-1]},
                           headers=actor_a)
    assert response.status_code == 200
    assert response.json()["status"] == "error"
    assert response.json()["missing"] == [keys[-1]]


def test_full_review_then_export_produces_reviewed_documents(
        actor_a, uploads_dir, generated_dir, tiers, claude):
    """
    The happy path, end to end, and the only one that reaches `reviewed`.

    Both documents are exported and both carry a REVIEWED stamp that verifies
    against its own review record. Several documents come back as one zip,
    because the endpoint promises one download_url and returning the first of
    two would silently lose one.
    """
    pack_id = _new_pack(actor_a, "Complete", MBD1, SBD4)
    client.post(f"/api/autofill-packs/{pack_id}/submit", headers=actor_a)
    _clear_everything(actor_a, pack_id)

    detail = client.get(f"/api/autofill-packs/{pack_id}", headers=actor_a).json()
    assert detail["status"] == "reviewed"
    assert detail["flags"]["outstanding"] == 0
    assert detail["values"]["unconfirmed"] == 0
    assert detail["exportable"] is True
    # The status endpoint agrees; there is one source of truth.
    assert client.get(f"/api/autofill-packs/{pack_id}/status",
                      headers=actor_a).json()["status"] == "reviewed"

    response = client.post(f"/api/autofill-packs/{pack_id}/export", headers=actor_a)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["download_url"].startswith("/api/generated/")
    assert len(body["documents"]) == 2

    for document in body["documents"]:
        state = read_review_state(document["export_path"])
        assert state["content_status"] == STATUS_REVIEWED  # "REVIEWED DRAFT"

    bundle = Path(pack_store.generated_dir()) / Path(body["download_url"]).name
    assert bundle.exists() and zipfile.is_zipfile(bundle)
    with zipfile.ZipFile(bundle) as archive:
        assert len(archive.namelist()) == 2

    # And it is servable: /api/generated fails closed on an unregistered file.
    served = client.get(body["download_url"], headers=actor_a)
    assert served.status_code == 200


def test_a_single_document_pack_exports_the_document_itself(
        actor_a, uploads_dir, generated_dir, tiers, claude):
    pack_id = _new_pack(actor_a, "One document", MBD1)
    client.post(f"/api/autofill-packs/{pack_id}/submit", headers=actor_a)
    _clear_everything(actor_a, pack_id)

    body = client.post(f"/api/autofill-packs/{pack_id}/export", headers=actor_a).json()
    assert body["download_url"].endswith(".docx")
    assert len(body["documents"]) == 1
    state = read_review_state(body["documents"][0]["export_path"])
    assert state["content_status"] == STATUS_REVIEWED  # "REVIEWED DRAFT"

    served = client.get(body["download_url"], headers=actor_a)
    assert served.status_code == 200, "an unregistered generated file is refused"


# --- 6. the tier gate -------------------------------------------------------


def test_a_starter_company_is_refused_and_no_claude_call_is_made(
        actor_starter, uploads_dir, generated_dir, tiers, claude):
    """
    Counted, not asserted. The point of checking the tier first is to avoid
    paying for a request that would be refused anyway, and a status code cannot
    tell those two apart.
    """
    pack_id = _new_pack(actor_starter, "Starter", MBD1, SBD4)
    assert claude.count == 0, "uploading must cost nothing"

    response = client.post(f"/api/autofill-packs/{pack_id}/submit", headers=actor_starter)
    assert response.status_code == 403
    assert "Starter" in response.json()["detail"]
    assert claude.count == 0, "a refused tier must not reach the classifier"

    status = client.get(f"/api/autofill-packs/{pack_id}/status",
                        headers=actor_starter).json()
    assert status["status"] == "draft"


def test_the_orchestrator_still_refuses_starter_if_submit_is_bypassed(
        actor_starter, uploads_dir, generated_dir, tiers, claude):
    """
    The endpoint's check is a courtesy. `run_autofill_batch` is the authority,
    so the worker is driven directly here with the endpoint gate skipped.
    """
    pack_id = _new_pack(actor_starter, "Bypassed", MBD1)
    with db.connect(AGENT_MEMORY_DB) as conn:
        conn.execute("UPDATE autofill_packs SET status = 'processing' WHERE pack_id = ?",
                     (pack_id,))

    pack_store.process_pack(CO_STARTER, pack_id)

    status = pack_store.pack_status(CO_STARTER, pack_id)
    assert status["status"] == "error"
    assert "Starter" in status["error_reason"]
    assert claude.count == 0


# --- 7. tenancy -------------------------------------------------------------


def test_company_b_cannot_read_submit_acknowledge_confirm_or_export_company_as_pack(
        actor_a, actor_b, uploads_dir, generated_dir, tiers, claude):
    """
    404 everywhere, not 403. A 403 confirms the id is real to someone who only
    has the id — and pack ids travel in URLs, logs and screenshots.
    """
    pack_id = _new_pack(actor_a, "A's pack", MBD1)
    client.post(f"/api/autofill-packs/{pack_id}/submit", headers=actor_a)
    detail = client.get(f"/api/autofill-packs/{pack_id}", headers=actor_a).json()
    review_id = detail["outstanding"][0]["review_id"]
    item_key = detail["outstanding"][0]["item_key"]
    value_keys = [v["item_key"]
                  for f in detail["files"] if f["review"]
                  for v in f["review"]["filled_values"]]

    assert client.get(f"/api/autofill-packs/{pack_id}", headers=actor_b).status_code == 404
    assert client.get(f"/api/autofill-packs/{pack_id}/status",
                      headers=actor_b).status_code == 404
    assert client.post(f"/api/autofill-packs/{pack_id}/submit",
                       headers=actor_b).status_code == 404
    assert client.patch(f"/api/autofill-packs/{pack_id}",
                        json={"pack_name": "mine now"}, headers=actor_b).status_code == 404
    assert _upload(actor_b, pack_id, SBD4).status_code == 404
    assert client.post(f"/api/autofill-packs/{pack_id}/acknowledge",
                       json={"review_id": review_id, "item_key": item_key,
                             "note": "I am acknowledging someone else's field."},
                       headers=actor_b).status_code == 404
    assert client.post(f"/api/autofill-packs/{pack_id}/confirm-values",
                       json={"review_id": review_id, "confirmed_keys": value_keys},
                       headers=actor_b).status_code == 404
    assert client.post(f"/api/autofill-packs/{pack_id}/export",
                       headers=actor_b).status_code == 404

    # And none of it changed anything.
    after = client.get(f"/api/autofill-packs/{pack_id}", headers=actor_a).json()
    assert after["pack_name"] == "A's pack"
    assert after["file_count"] == 1
    assert after["flags"]["acknowledged"] == 0
    assert after["values"]["unconfirmed"] > 0


def test_company_bs_pack_list_never_shows_company_as_packs(
        actor_a, actor_b, uploads_dir, tiers):
    pack_id = _new_pack(actor_a, "Private", MBD1)
    assert pack_id not in {p["pack_id"]
                           for p in client.get("/api/autofill-packs",
                                               headers=actor_b).json()}


def test_a_review_from_another_pack_cannot_be_acknowledged_through_this_one(
        actor_a, uploads_dir, generated_dir, tiers, claude):
    """
    Same tenant, different pack. Without this check a pack could reach
    'everything acknowledged' on the strength of work done in a different pack.
    """
    first = _new_pack(actor_a, "First", MBD1)
    client.post(f"/api/autofill-packs/{first}/submit", headers=actor_a)
    second = _new_pack(actor_a, "Second", MBD1)
    client.post(f"/api/autofill-packs/{second}/submit", headers=actor_a)

    borrowed = client.get(f"/api/autofill-packs/{first}",
                          headers=actor_a).json()["outstanding"][0]
    response = client.post(
        f"/api/autofill-packs/{second}/acknowledge",
        json={"review_id": borrowed["review_id"], "item_key": borrowed["item_key"],
              "note": "Acknowledging a field that belongs to another pack."},
        headers=actor_a)
    assert response.status_code == 404


# --- 8. the status is derived, never asserted by a caller -------------------


def test_there_is_no_request_that_sets_a_pack_to_reviewed(
        actor_a, uploads_dir, generated_dir, tiers, claude):
    """
    A pack's status is recomputed from its reviews on every read. A client that
    PATCHes a status, or names one in any body, changes nothing.
    """
    pack_id = _new_pack(actor_a, "Forgery attempt", MBD1)
    client.post(f"/api/autofill-packs/{pack_id}/submit", headers=actor_a)

    for body in ({"status": "reviewed"}, {"pack_name": "x", "status": "reviewed"},
                 {"pack_name": "x", "exportable": True}):
        client.patch(f"/api/autofill-packs/{pack_id}", json=body, headers=actor_a)

    detail = client.get(f"/api/autofill-packs/{pack_id}", headers=actor_a).json()
    assert detail["status"] == "needs_review"
    assert detail["exportable"] is False
    assert client.post(f"/api/autofill-packs/{pack_id}/export",
                       headers=actor_a).status_code == 409


def test_editing_an_acknowledgement_in_the_database_drops_the_pack_back(
        actor_a, uploads_dir, generated_dir, tiers, claude):
    """
    Acknowledgements are MAC'd. A row written by hand has no valid signature, so
    the pack does not count it — the same refusal `export_reviewed` makes,
    reached through the pack's own aggregation.
    """
    pack_id = _new_pack(actor_a, "Forged ack", MBD1)
    client.post(f"/api/autofill-packs/{pack_id}/submit", headers=actor_a)
    _clear_everything(actor_a, pack_id)
    assert client.get(f"/api/autofill-packs/{pack_id}",
                      headers=actor_a).json()["status"] == "reviewed"

    review_id = client.get(f"/api/autofill-packs/{pack_id}",
                           headers=actor_a).json()["files"][0]["review_id"]
    with db.connect(AGENT_MEMORY_DB) as conn:
        conn.execute(
            "UPDATE autofill_review_item SET acknowledged_note = ? WHERE review_id = ?",
            ("rewritten after the fact", review_id))

    response = client.post(f"/api/autofill-packs/{pack_id}/export", headers=actor_a)
    assert response.status_code == 409
    assert response.json()["detail"].get("tamper_detected") is True
