"""
Agent Autofill: the export gate and the review state carried in the file.

The premise mirrors the safety tests. A false positive costs a user one extra
click. A false negative hands someone a half-reviewed draft that says it was
reviewed, and that draft goes to an organ of state. So these tests do not check
that the happy path works — they check that the refusal cannot be talked out of,
using the routes a determined caller would actually take: calling the export
function directly, forging the stored state, acknowledging a field that was
never flagged, acknowledging one field repeatedly to reach the count, and
driving it through the agent's own tool registry with as_reviewed=true.

DATABASE ISOLATION
------------------
`AGENT_DB_DIR` and `AGENT_GENERATED_DIR` are set before any `agent.*` import,
following tests/test_agent_autofill_profile.py. db_paths and file_paths both
read their environment at import time, so the real agent_memory.db and the real
static/downloads are never touched.
"""

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# MUST happen before importing anything under agent/ or agent_autofill/.
_TEST_DB_DIR = Path(tempfile.mkdtemp(prefix="cairo-gate-test-db-"))
_TEST_GEN_DIR = Path(tempfile.mkdtemp(prefix="cairo-gate-test-gen-"))
os.environ["AGENT_DB_DIR"] = str(_TEST_DB_DIR)
os.environ["AGENT_GENERATED_DIR"] = str(_TEST_GEN_DIR)

from agent.db_paths import AGENT_MEMORY_DB                               # noqa: E402
from agent.tool_dispatch import execute_tool                             # noqa: E402
from agent_autofill.extraction.field_alias_dictionary import match_label  # noqa: E402
from agent_autofill.fill_engine.document_filler import fill_docx         # noqa: E402
from agent_autofill.integration import export_metadata as em             # noqa: E402
from agent_autofill.integration import review_gate as rg                 # noqa: E402

FORM = ROOT / "tests" / "fixtures" / "sa_forms_generated" / "mbd1_supplier_info.docx"

COMPANY = "gate_test_co"
OTHER_COMPANY = "gate_test_other_co"

PROFILE = {
    "company_name": "CairoAI Gate Test (Pty) Ltd",
    "registration_number": "2026/250499/07",
    "csd_number": "MAAA1234567",
    "bbbee_level": "Level 1 Contributor",
    "tax_reference_number": "9012345678",
    "vat_registration_number": "4480290011",
    "physical_address": "Centurion, Gauteng",
    "postal_address": "PO Box 1, Centurion",
    "standard_contact_person": "T. Molwantwa",
    "standard_phone": "+27 12 000 0000",
    "standard_email": "bids@cairoai.co.za",
}


@pytest.fixture
def review(tmp_path):
    """A real fill of the real MBD 1 fixture, with its flags still open."""
    draft = tmp_path / "draft.docx"
    result = fill_docx(FORM, draft, PROFILE, match_label)
    assert result.skipped, "fixture must produce at least one flagged field"
    opened = rg.open_review(COMPANY, result, company_name=PROFILE["company_name"])
    yield opened["review_id"], result, draft
    rg.delete_review(COMPANY, opened["review_id"])


def _outstanding(review_id):
    return rg.get_review(COMPANY, review_id)["outstanding_count"]


def _ack_all(review_id):
    for item in rg.get_review(COMPANY, review_id)["outstanding"]:
        rg.acknowledge_field(
            COMPANY, review_id, item["item_key"],
            f"I will complete {item['label']} by hand before submitting.",
        )
    _confirm_values(review_id)

def _confirm_values(review_id, company=None):
    """
    The other half of a human review: confirming the values that WERE filled.
    Acknowledging the flags alone no longer produces a reviewed export — a form
    with nothing flagged used to export as REVIEWED with no human involvement
    at all.
    """
    co = company or COMPANY
    shown = rg.filled_values(co, review_id)
    return rg.confirm_filled_values(
        co, review_id, [v["item_key"] for v in shown["values"]])



# --- the refusal -----------------------------------------------------------


def test_export_reviewed_refuses_while_any_flag_is_open(review):
    review_id, result, _ = review
    out = rg.export_reviewed(COMPANY, review_id)
    assert out["status"] == "error"
    assert out["outstanding_count"] == len(result.skipped)
    assert "export_path" not in out


def test_refusal_names_every_outstanding_field(review):
    review_id, result, _ = review
    out = rg.export_reviewed(COMPANY, review_id)
    for skipped in result.skipped:
        assert skipped.label in out["message"], f"refusal did not name {skipped.label!r}"


def test_refusal_writes_no_file(review, tmp_path):
    review_id, _, _ = review
    before = set(Path(os.environ["AGENT_GENERATED_DIR"]).glob("*"))
    rg.export_reviewed(COMPANY, review_id)
    assert set(Path(os.environ["AGENT_GENERATED_DIR"]).glob("*")) == before


def test_partial_acknowledgement_still_refuses(review):
    review_id, result, _ = review
    first = rg.get_review(COMPANY, review_id)["outstanding"][0]
    rg.acknowledge_field(COMPANY, review_id, first["item_key"],
                         "I have seen this field and will handle it myself.")
    if len(result.skipped) > 1:
        assert rg.export_reviewed(COMPANY, review_id)["status"] == "error"


def test_export_reviewed_takes_no_readiness_argument():
    """
    The verdict must come from stored state, never from the caller.

    The quotation gate this pattern follows computes has_flags from the
    `priced_items` the caller passes in, so a caller that passes a clean list
    finalises the quote regardless of what the database holds. This asserts the
    same hole was not reproduced here.
    """
    import inspect

    params = set(inspect.signature(rg.export_reviewed).parameters)
    assert params == {"company_id", "review_id"}


# --- acknowledgement cannot be done in bulk --------------------------------


@pytest.mark.parametrize("token", ["*", "all", "ALL", "everything", "yes", "confirm",
                                   "i agree", "each", "none"])
def test_blanket_acknowledgement_tokens_are_refused(review, token):
    review_id, _, _ = review
    with pytest.raises(rg.ReviewGateError):
        rg.acknowledge_field(COMPANY, review_id, token, "I have reviewed everything")
    assert _outstanding(review_id) > 0


@pytest.mark.parametrize("key", ["F01,F02", "F01 F02", "F01,F02,F03"])
def test_multi_key_acknowledgement_is_refused(review, key):
    review_id, _, _ = review
    with pytest.raises(rg.ReviewGateError):
        rg.acknowledge_field(COMPANY, review_id, key, "checked all of these")


@pytest.mark.parametrize("note", ["", "  ", "ok", "yes", "OK", "confirmed", "-"])
def test_generic_note_is_refused(review, note):
    review_id, _, _ = review
    with pytest.raises(rg.ReviewGateError):
        rg.acknowledge_field(COMPANY, review_id, "F01", note)
    assert _outstanding(review_id) > 0


def test_acknowledging_a_field_that_was_never_flagged_is_refused(review):
    review_id, _, _ = review
    with pytest.raises(rg.ReviewGateError):
        rg.acknowledge_field(COMPANY, review_id, "F99",
                             "this field does not exist in this review")


def test_acknowledging_the_same_field_repeatedly_does_not_advance_the_count(review):
    """The count is rows-with-a-timestamp, not a tally of successful calls."""
    review_id, result, _ = review
    if len(result.skipped) < 2:
        pytest.skip("needs at least two flagged fields")

    before = _outstanding(review_id)
    for n in range(5):
        rg.acknowledge_field(COMPANY, review_id, "F01",
                             f"repeat call number {n} against the same field")
    assert _outstanding(review_id) == before - 1
    assert rg.export_reviewed(COMPANY, review_id)["status"] == "error"


# --- tenant isolation ------------------------------------------------------


def test_another_tenant_cannot_acknowledge(review):
    review_id, _, _ = review
    with pytest.raises(rg.ReviewGateError):
        rg.acknowledge_field(OTHER_COMPANY, review_id, "F01",
                             "a different company reaching into this review")


def test_another_tenant_cannot_export(review):
    review_id, _, _ = review
    _ack_all(review_id)
    with pytest.raises(rg.ReviewGateError):
        rg.export_reviewed(OTHER_COMPANY, review_id)


def test_another_tenant_cannot_even_read(review):
    review_id, _, _ = review
    with pytest.raises(rg.ReviewGateError):
        rg.get_review(OTHER_COMPANY, review_id)


# --- forging the stored state ----------------------------------------------


def test_forging_the_status_column_alone_does_not_produce_an_export(review):
    """
    The status flip is a conditional UPDATE whose WHERE clause re-checks the
    items, so a forged status does not satisfy it and no file is written.
    """
    review_id, _, _ = review
    with sqlite3.connect(str(AGENT_MEMORY_DB)) as conn:
        conn.execute("UPDATE autofill_review SET status='REVIEWED' WHERE review_id=?",
                     (review_id,))

    out = rg.export_reviewed(COMPANY, review_id)
    assert out["status"] == "error"
    assert not out.get("export_path")


# --- the stamp object refuses to describe an unfinished document -----------


def test_cannot_construct_a_reviewed_stamp_with_open_flags():
    with pytest.raises(em.ReviewStateError):
        em.ReviewStamp(review_id="x", status=em.STATUS_REVIEWED,
                       flags_total=4, flags_open=4)


@pytest.mark.parametrize("status", [
    "APPROVED", "FINAL", "READY TO SUBMIT", "SUBMITTED", "reviewed draft", "",
])
def test_no_status_stronger_than_reviewed_draft_can_be_written(status):
    with pytest.raises(em.ReviewStateError):
        em.ReviewStamp(review_id="x", status=status, flags_total=0, flags_open=0)


def test_open_flags_cannot_exceed_total():
    with pytest.raises(em.ReviewStateError):
        em.ReviewStamp(review_id="x", status=em.STATUS_UNREVIEWED,
                       flags_total=2, flags_open=5)


# --- through the agent's own tool registry ---------------------------------


def test_tool_registry_export_with_as_reviewed_true_is_refused(review):
    review_id, _, _ = review
    result, is_error = execute_tool(
        "autofill_export_document",
        {"company_id": "some-other-tenant", "review_id": review_id, "as_reviewed": True},
        COMPANY,
    )
    assert not is_error, "the gate must return a readable refusal, not a crash"
    assert result["status"] == "error"
    assert result["outstanding_count"] > 0


def test_tool_registry_pins_the_tenant(review):
    """company_id in the tool input is model text and is overwritten."""
    review_id, _, _ = review
    result, _ = execute_tool(
        "autofill_review_status",
        {"company_id": OTHER_COMPANY, "review_id": review_id},
        COMPANY,
    )
    assert result["status"] == "success"


# --- the happy path, and what it does and does not claim -------------------


def test_export_succeeds_once_every_field_is_acknowledged_individually(review):
    review_id, result, _ = review
    _ack_all(review_id)
    out = rg.export_reviewed(COMPANY, review_id)
    assert out["status"] == "success"
    assert out["acknowledged_count"] == len(result.skipped)
    assert Path(out["export_path"]).exists()


def test_reviewed_export_never_claims_to_be_a_submission(review):
    review_id, _, _ = review
    _ack_all(review_id)
    out = rg.export_reviewed(COMPANY, review_id)
    state = em.read_review_state(out["export_path"])
    blob = f"{state['comments']} {state['banner_text']} {state['subject']}".lower()
    assert "not a submission" in blob or "not yet a submission" in blob
    for word in ("approved", "submission-ready", "ready to submit", "final version"):
        assert word not in blob, f"export claims {word!r}"


# --- the state is in the FILE, read back from the saved file ---------------


def test_draft_file_carries_unreviewed_state(review):
    review_id, result, draft = review
    state = em.read_review_state(draft)
    assert state["stamped"] is True
    assert state["content_status"] == em.STATUS_UNREVIEWED
    assert state["banner_present"] is True
    assert state["review_id"] == review_id
    assert state["flags_total"] == len(result.skipped)


def test_reviewed_export_file_carries_reviewed_state(review):
    review_id, result, _ = review
    _ack_all(review_id)
    out = rg.export_reviewed(COMPANY, review_id)

    state = em.read_review_state(out["export_path"])
    assert state["content_status"] == em.STATUS_REVIEWED
    assert state["flags_open"] == 0
    assert state["acknowledged"] == len(result.skipped)
    assert state["review_id"] == review_id


def test_the_two_files_are_distinguishable_without_the_database(review):
    """
    The whole point: a half-reviewed draft forwarded by email must not look
    like a finished one to someone who has no access to our database.
    """
    review_id, _, draft = review
    unreviewed = em.read_review_state(draft)
    _ack_all(review_id)
    reviewed = em.read_review_state(rg.export_reviewed(COMPANY, review_id)["export_path"])

    assert unreviewed["content_status"] != reviewed["content_status"]
    assert unreviewed["banner_text"] != reviewed["banner_text"]
    assert "DO NOT SUBMIT" in unreviewed["banner_text"]


def test_unreviewed_banner_is_not_misread_as_reviewed(review):
    """
    Regression: "UNREVIEWED DRAFT" CONTAINS the substring "REVIEWED DRAFT".

    read_review_state() tested for the reviewed status first, so every
    unreviewed draft came back with banner_status "REVIEWED DRAFT" and
    channels_agree False — the one check meant to catch a forged file instead
    reported the safe files as suspect and the unsafe status as present.
    """
    _, _, draft = review
    state = em.read_review_state(draft)
    assert state["banner_status"] == em.STATUS_UNREVIEWED
    assert state["channels_agree"] is True


def test_untouched_form_reads_as_unstamped():
    state = em.read_review_state(FORM)
    assert state["stamped"] is False
    assert state["banner_present"] is False
    assert state["review_id"] is None


def test_restamping_does_not_stack_banners(review):
    """An export is a copy of an already-stamped draft; it gets one banner."""
    review_id, _, _ = review
    _ack_all(review_id)
    out = rg.export_reviewed(COMPANY, review_id)

    import docx

    banners = [p for p in docx.Document(out["export_path"]).paragraphs
               if (p.text or "").strip().startswith(em.BANNER_SENTINEL)]
    assert len(banners) == 1


def test_core_properties_stay_inside_the_255_char_opc_limit(review):
    """python-docx raises rather than truncating, so every property is clipped."""
    review_id, _, draft = review
    state = em.read_review_state(draft)
    for key in ("content_status", "category", "subject", "keywords", "comments"):
        assert len(state[key]) <= em.MAX_PROP_CHARS, f"{key} exceeds the OPC limit"


# --- advisory blanks -------------------------------------------------------
#
# A real 145-page PDF tender produced 651 items to confirm, 370 of them blanks
# whose labels could not be identified. Those were never filled and never could
# have been. A gate demanding 651 confirmations is not stricter than one
# demanding 281 — it is one people learn to click through, including past the
# 25 signatures.


def test_unmatched_blanks_do_not_require_acknowledgement(review):
    """They are recorded, and marked in the document, but not counted."""
    from agent_autofill.integration.review_gate import ADVISORY_CATEGORIES

    assert "unmatched" in ADVISORY_CATEGORIES

    review_id, result, _ = review
    state = rg.get_review(COMPANY, review_id)
    counted = {i["item_key"] for i in state["outstanding"]}
    for item in state["items"]:
        if item.get("advisory"):
            assert item["item_key"] not in counted, (
                f"{item['item_key']} is advisory but counted as outstanding")


def test_advisory_items_are_still_recorded(review):
    """Dropped from the tally, not from the record."""
    review_id, _, _ = review
    state = rg.get_review(COMPANY, review_id)
    assert "advisory_count" in state
    assert isinstance(state.get("advisory"), list)


def test_signatures_are_never_advisory():
    """
    The whole point of narrowing the list is that the things which matter stay
    on it. A signature must never be quietly reclassified as advisory.
    """
    from agent_autofill.integration.review_gate import ADVISORY_CATEGORIES

    for must_stay in ("blocked", "no_data", "low_confidence"):
        assert must_stay not in ADVISORY_CATEGORIES


def test_the_export_gate_still_refuses_while_a_real_flag_is_open(review):
    """Narrowing the count must not narrow the gate."""
    review_id, _, _ = review
    state = rg.get_review(COMPANY, review_id)
    if not state["outstanding"]:
        pytest.skip("fixture produced no non-advisory flags")
    out = rg.export_reviewed(COMPANY, review_id)
    assert out["status"] == "error"


def test_advisory_alone_does_not_hold_the_gate(review):
    """
    A document whose ONLY remaining items are advisory must be exportable once
    its real flags and values are done — otherwise the change achieved nothing.
    """
    review_id, _, _ = review
    _ack_all(review_id)
    state = rg.get_review(COMPANY, review_id)
    assert state["outstanding_count"] == 0
    out = rg.export_reviewed(COMPANY, review_id)
    assert out["status"] == "success", out.get("message")
