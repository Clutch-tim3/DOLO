"""
Tests for the classification gate, the tier limits, and the orchestrator.

Two properties matter more than the rest and are asserted directly rather than
inferred from an HTTP response:

  * The quota check runs BEFORE the Anthropic request. A test that only looks
    at the final result cannot tell "refused early" from "refused after paying",
    so every test here counts invocations on a recording client instead.
  * The orchestrator stops at the human confirmation gate. It never marks a
    draft reviewed or exportable, on any path.

No test in this file makes a real API call; the client is replaced with a
recorder in a fixture.
"""

import os
import sqlite3
import sys
from pathlib import Path

import pytest

from conftest import clear_company, set_company_tier

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import claude_client, subscription
from agent.db_paths import AGENT_MEMORY_DB
from agent_autofill import main_autofill_orchestrator as orch
from agent_autofill.classification import is_tender_document as cls

FIXTURES = Path(__file__).parent / "fixtures"
DOCX_FORM = FIXTURES / "sa_forms_generated" / "mbd1_supplier_info.docx"
LEGACY_DOC = FIXTURES / "sa_forms" / "SCM-Bid documents SBD 3.1.docx"
REAL_DOCX = FIXTURES / "sa_forms" / "REVISED SBD 4 -Annexure A.docx"

PRO = "autofill_test_pro"
STARTER = "autofill_test_starter"
ENTERPRISE = "autofill_test_enterprise"


class RecordingClaude:
    """Stands in for the Anthropic client and counts every invocation."""

    def __init__(self, verdict='{"is_tender": true, "confidence": 0.95, '
                               '"document_type": "SBD form", "reason": "test"}'):
        self.calls = []
        self.verdict = verdict

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return {"content": self.verdict, "tool_calls": [], "stop_reason": "end_turn", "blocks": []}

    @property
    def count(self) -> int:
        return len(self.calls)


def _purge_usage_logs():
    with sqlite3.connect(AGENT_MEMORY_DB) as conn:
        conn.execute(
            "DELETE FROM usage_logs WHERE action_type = ? AND company_id IN (?, ?, ?)",
            (subscription.AUTOFILL_ACTION, PRO, STARTER, ENTERPRISE),
        )
        conn.commit()


@pytest.fixture
def tiers(monkeypatch):
    set_company_tier(PRO, "pro")
    set_company_tier(STARTER, "starter")
    set_company_tier(ENTERPRISE, "enterprise")
    _purge_usage_logs()
    yield
    _purge_usage_logs()
    # See the note in test_autofill_packs: these are rows, so they must go.
    for company_id in (PRO, STARTER, ENTERPRISE):
        clear_company(company_id)


@pytest.fixture
def claude(monkeypatch):
    recorder = RecordingClaude()
    monkeypatch.setattr(claude_client, "call_claude_with_tracking", recorder)
    # The global limiter is shared with the rest of the app; 30/min would make
    # a full test run flaky for reasons unrelated to autofill.
    monkeypatch.setattr(cls.rate_limiter, "check_global_rate_limit", lambda: True)
    return recorder


# --- tier configuration ----------------------------------------------------

@pytest.mark.parametrize("tier,enabled,folders,per_day", [
    ("starter", False, 0, 0),
    ("pro", True, 1, 3),
    ("enterprise", True, 5, 25),
])
def test_tier_config_matches_the_spec(tier, enabled, folders, per_day):
    config = subscription.TIER_CONFIG[tier]
    assert config["agent_autofill_enabled"] is enabled
    assert config["connected_folders_limit"] == folders
    assert config["autofills_per_day"] == per_day


def test_starter_is_refused_by_the_quota_check_itself(tiers):
    verdict = subscription.check_autofill_quota(STARTER)
    assert verdict["allowed"] is False
    assert verdict["code"] == "tier_disabled"


def test_connected_folder_limit_is_enforced_per_tier(tiers):
    assert subscription.check_connected_folder_limit(PRO, 0)["allowed"] is True
    assert subscription.check_connected_folder_limit(PRO, 1)["allowed"] is False
    assert subscription.check_connected_folder_limit(ENTERPRISE, 4)["allowed"] is True
    assert subscription.check_connected_folder_limit(ENTERPRISE, 5)["allowed"] is False
    assert subscription.check_connected_folder_limit(STARTER, 0)["allowed"] is False


def test_quota_is_consumed_only_by_the_log_call(tiers):
    """check_ must not have the side effect that log_ has."""
    assert subscription.get_autofills_used_today(PRO) == 0
    for _ in range(3):
        assert subscription.check_autofill_quota(PRO)["allowed"] is True
    assert subscription.get_autofills_used_today(PRO) == 0, "checking burned quota"
    subscription.log_autofill_run(PRO)
    assert subscription.get_autofills_used_today(PRO) == 1


# --- the gate runs before the request --------------------------------------

def test_starter_never_reaches_the_api(tiers, claude, tmp_path):
    run = orch.run_autofill(STARTER, DOCX_FORM, output_dir=tmp_path)
    assert run.status == orch.STATUS_REFUSED_TIER
    assert claude.count == 0, "a starter company paid for a classification call"
    assert run.claude_calls == 0
    assert run.quota_consumed is False
    assert run.output_document is None


def test_fourth_pro_autofill_is_refused_before_any_request(tiers, claude, tmp_path):
    issued = []
    for _ in range(4):
        before = claude.count
        run = orch.run_autofill(PRO, DOCX_FORM, output_dir=tmp_path)
        issued.append((run.status, claude.count - before, run.quota_consumed))

    assert [s for s, _, _ in issued[:3]] == [orch.STATUS_AWAITING_REVIEW] * 3
    assert [n for _, n, _ in issued[:3]] == [1, 1, 1]
    assert all(consumed for _, _, consumed in issued[:3])

    status, requests_on_fourth, consumed = issued[3]
    assert status == orch.STATUS_REFUSED_QUOTA
    assert requests_on_fourth == 0, "the fourth attempt was refused only AFTER spending"
    assert consumed is False
    assert claude.count == 3


def test_a_refused_run_does_not_burn_the_quota(tiers, claude, tmp_path):
    for _ in range(4):
        orch.run_autofill(PRO, DOCX_FORM, output_dir=tmp_path)
    assert subscription.get_autofills_used_today(PRO) == 3


def test_enterprise_gets_the_larger_allowance(tiers, claude, tmp_path):
    quota = subscription.check_autofill_quota(ENTERPRISE)
    assert quota["allowed"] is True and quota["limit"] == 25


# --- classification --------------------------------------------------------

def test_threshold_is_seven_tenths():
    assert cls.CONFIDENCE_THRESHOLD == 0.7


def test_classifier_model_is_not_the_retired_haiku():
    assert cls.CLASSIFIER_MODEL == "claude-haiku-4-5"
    assert "20241022" not in cls.CLASSIFIER_MODEL


def test_the_call_pins_haiku_and_bounds_its_output(tiers, claude, tmp_path):
    orch.run_autofill(PRO, DOCX_FORM, output_dir=tmp_path)
    assert claude.calls[0]["model"] == "claude-haiku-4-5"
    assert claude.calls[0]["max_tokens"] == cls.MAX_TOKENS
    assert claude.calls[0]["company_id"] == PRO


@pytest.mark.parametrize("confidence,expected", [
    (0.69, False), (0.70, True), (0.95, True), (0.0, False),
])
def test_threshold_boundary(confidence, expected):
    result = cls.ClassificationResult(path="x.pdf", is_tender=True, confidence=confidence)
    assert result.proceed is expected


def test_a_tender_verdict_below_threshold_does_not_proceed(tiers, claude, tmp_path):
    claude.verdict = '{"is_tender": true, "confidence": 0.55, "reason": "unsure"}'
    run = orch.run_autofill(PRO, DOCX_FORM, output_dir=tmp_path)
    assert run.status == orch.STATUS_SKIPPED_NOT_TENDER
    assert run.output_document is None
    assert run.quota_consumed is False, "a skipped file charged the user"


def test_an_unparseable_verdict_fails_closed(tiers, claude, tmp_path):
    claude.verdict = "I think it's probably a tender, yes."
    run = orch.run_autofill(PRO, DOCX_FORM, output_dir=tmp_path)
    assert run.status == orch.STATUS_ERROR
    assert run.output_document is None
    assert run.quota_consumed is False


@pytest.mark.parametrize("name", ["scan.jpg", "notes.txt", "sheet.xlsx", "archive.zip"])
def test_non_document_types_are_refused_before_reading(tmp_path, name):
    path = tmp_path / name
    path.write_bytes(b"whatever")
    ok, why = cls.is_classifiable(path)
    assert ok is False and "PDF and DOCX" in why


def test_a_doc_renamed_to_docx_is_caught_by_magic_bytes():
    ok, why = cls.is_classifiable(LEGACY_DOC)
    assert ok is False
    assert "legacy Word .doc" in why and "Save As" in why


def test_classification_of_an_unsupported_type_costs_nothing(tiers, claude, tmp_path):
    junk = tmp_path / "photo.jpg"
    junk.write_bytes(b"\xff\xd8\xff\xe0junk")
    result = cls.classify_document(junk, PRO)
    assert result.status == "unsupported_type"
    assert result.api_called is False
    assert claude.count == 0


def test_a_pdf_with_no_text_layer_is_reported_not_guessed(tiers, claude, tmp_path):
    blank = tmp_path / "scanned.pdf"
    blank.write_bytes(b"%PDF-1.4\n% not really a pdf\n")
    result = cls.classify_document(blank, PRO)
    assert result.status in ("unreadable", "unsupported_type")
    assert result.proceed is False
    assert claude.count == 0


def test_extract_text_head_is_bounded():
    head = cls.extract_text_head(REAL_DOCX)
    assert 0 < len(head) <= cls.HEAD_CHARS


# --- orchestrator behaviour ------------------------------------------------

def test_legacy_doc_is_explained_never_silently_dropped(tiers, claude, tmp_path):
    run = orch.run_autofill(PRO, LEGACY_DOC, output_dir=tmp_path)
    assert run.status == orch.STATUS_LEGACY_DOC
    assert ".docx" in run.message and "Save As" in run.message
    assert run.output_document is None
    assert claude.count == 0, "an unwritable file cost an API call"
    assert run.quota_consumed is False


def test_a_draft_is_written_to_a_copy_and_the_source_is_untouched(tiers, claude, tmp_path):
    original = DOCX_FORM.read_bytes()
    run = orch.run_autofill(PRO, DOCX_FORM, output_dir=tmp_path)
    assert run.status == orch.STATUS_AWAITING_REVIEW
    assert Path(run.output_document).exists()
    assert Path(run.output_document) != DOCX_FORM
    assert DOCX_FORM.read_bytes() == original


def test_the_review_summary_is_rendered_and_says_review_is_required(tiers, claude, tmp_path):
    run = orch.run_autofill(PRO, DOCX_FORM, output_dir=tmp_path)
    html = Path(run.review_summary_path).read_text(encoding="utf-8")
    assert "review required before use" in html
    assert "No signature has been applied" in html


def test_the_run_always_stops_at_the_confirmation_gate(tiers, claude, tmp_path):
    for company, source in [
        (PRO, DOCX_FORM), (PRO, LEGACY_DOC), (STARTER, DOCX_FORM),
        (PRO, tmp_path / "missing.docx"),
    ]:
        run = orch.run_autofill(company, source, output_dir=tmp_path)
        assert run.requires_human_confirmation is True
        assert run.reviewed is False
        assert run.exportable is False
        assert run.to_dict()["exportable"] is False


def test_the_orchestrator_contains_no_way_to_mark_something_reviewed():
    """A grep-level guard: Subagent 6 owns the export gate, not this module."""
    import re
    source = Path(orch.__file__).read_text(encoding="utf-8")
    bad = re.findall(r"^\s*\S*\.?(?:reviewed|exportable)\s*=\s*(?!False)\S+", source, re.M)
    assert bad == [], f"orchestrator sets a review flag: {bad}"


def test_a_missing_file_is_an_error_not_a_crash(tiers, claude, tmp_path):
    run = orch.run_autofill(PRO, tmp_path / "nope.docx", output_dir=tmp_path)
    assert run.status == orch.STATUS_ERROR
    assert claude.count == 0


def test_company_id_is_mandatory():
    with pytest.raises(ValueError):
        orch.run_autofill("", DOCX_FORM)


def test_batch_stops_the_moment_the_quota_runs_out(tiers, claude, tmp_path):
    runs = orch.run_autofill_batch(PRO, [DOCX_FORM] * 10, output_dir=tmp_path)
    assert len(runs) == 4, "the batch kept paying for classifications after the limit"
    assert runs[-1].status == orch.STATUS_REFUSED_QUOTA
    assert claude.count == 3


def test_generated_names_survive_the_download_path_validator(tiers, claude, tmp_path):
    from agent import file_paths
    run = orch.run_autofill(PRO, REAL_DOCX, output_dir=tmp_path)
    for name in (Path(run.output_document).name, Path(run.review_summary_path).name):
        file_paths.safe_generated_path(name)  # raises if the name is unsafe
