"""
The narration the agent reads out while a pack processes.

The rule under test is not "does it render a sentence" — it is that no sentence
describes work the system does not do. The obvious temptation was a line like
"searching suppliers for prices"; there is no price lookup, every line item is
flagged for a human, and saying otherwise would be the same class of claim as
the "Files by Google API" that does not exist.
"""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("AGENT_DB_DIR", tempfile.mkdtemp(prefix="cairo-ev-db-"))
os.environ.setdefault("AGENT_GENERATED_DIR", tempfile.mkdtemp(prefix="cairo-ev-gen-"))

import pytest

from agent_autofill.integration import pack_events as ev


def test_events_come_back_in_order_with_increasing_seq():
    pack = "pack-order"
    ev.emit(pack, "pack_started", {"file_count": 3})
    ev.emit(pack, "file_opened", {"filename": "mbd1.docx"})
    ev.emit(pack, "review_ready", {"flag_count": 12})
    seqs = [e["seq"] for e in ev.events_since(pack)]
    assert seqs == sorted(seqs) and len(seqs) == 3


def test_polling_with_a_cursor_returns_only_what_is_new():
    """A reconnecting browser resumes mid-narration, it does not replay."""
    pack = "pack-cursor"
    ev.emit(pack, "pack_started", {"file_count": 1})
    first = ev.events_since(pack)
    ev.emit(pack, "review_ready", {"flag_count": 4})
    later = ev.events_since(pack, after_seq=first[-1]["seq"])
    assert len(later) == 1 and later[0]["stage"] == "review_ready"


def test_messages_are_rendered_from_the_detail():
    pack = "pack-render"
    ev.emit(pack, "filled", {"filename": "mbd1.docx", "filled_count": 12,
                             "flagged_count": 6})
    msg = ev.events_since(pack)[-1]["message"]
    assert "mbd1.docx" in msg and "12" in msg and "6" in msg


def test_a_missing_template_field_does_not_lose_the_event():
    """Narration must never be able to break the work it describes."""
    pack = "pack-missing"
    ev.emit(pack, "filled", {"filename": "only.docx"})     # counts absent
    events = ev.events_since(pack)
    assert len(events) == 1
    assert events[0]["message"]


def test_an_unknown_stage_still_records():
    pack = "pack-unknown"
    ev.emit(pack, "some_future_stage", {})
    assert ev.events_since(pack)[0]["message"] == "some future stage"


def test_emit_never_raises_even_on_a_broken_write(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("database gone")
    monkeypatch.setattr(ev.db, "connect", boom)
    ev.emit("pack-broken", "pack_started", {"file_count": 1})   # must not raise


def test_usage_counts_model_calls_explicitly_not_by_inference():
    """
    An earlier version inferred the call count from "did this row carry
    tokens", which reported zero the moment tokens stopped being available.
    The count is now its own column and is passed in deliberately.
    """
    pack = "pack-usage"
    ev.emit(pack, "file_opened", {"filename": "a.docx"})                 # free
    ev.emit(pack, "classified", {"filename": "a.docx", "verdict": "tender",
                                 "confidence": "0.94"}, model_calls=1)
    ev.emit(pack, "classified", {"filename": "b.docx", "verdict": "tender",
                                 "confidence": "0.91"}, model_calls=1)
    t = ev.token_totals(pack)
    assert t["model_calls"] == 2, "only the classification calls should count"


def test_usage_is_explicit_that_token_counts_are_not_available():
    """
    ClassificationResult carries confidence and document_type but not tokens;
    claude_client records them and never returns them. Reporting zero without
    saying so would read as "this was free".
    """
    t = ev.token_totals("pack-none")
    assert t["tokens_in"] == 0 and t["tokens_out"] == 0
    assert t["tokens_available"] is False


# --- the honesty constraint ------------------------------------------------


def test_no_template_claims_a_web_search():
    """
    price_search makes no network call and never has. A narration line implying
    otherwise would be a fabrication in the user's face, on the same screen
    that produces a government bid document.
    """
    joined = " ".join(ev.STAGE_TEMPLATES.values()).lower()
    for phrase in ("search the web", "searching the web", "web search",
                   "searching suppliers", "looking up prices", "found a price",
                   "best price", "comparing retailers"):
        assert phrase not in joined, f"narration claims: {phrase!r}"


def test_the_pricing_line_says_a_human_must_supply_the_figure():
    msg = ev.STAGE_TEMPLATES["pricing_skipped"].lower()
    assert "no prices" in msg
    assert "you" in msg


def test_no_template_calls_anything_complete_or_submission_ready():
    """Drafts only, in the narration as much as in the document."""
    joined = " ".join(ev.STAGE_TEMPLATES.values()).lower()
    for phrase in ("ready to submit", "submission-ready", "complete and ready",
                   "signed", "signature applied"):
        assert phrase not in joined, f"narration implies finality: {phrase!r}"


def test_every_template_placeholder_is_fillable():
    """A stage whose sentence cannot render is a stage that reports nothing."""
    import re
    for stage, template in ev.STAGE_TEMPLATES.items():
        fields = re.findall(r"\{(\w+)\}", template)
        detail = {f: "x" for f in fields}
        out = template.format(**detail)
        assert out and "{" not in out, stage
