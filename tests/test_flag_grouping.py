"""
Fifteen identical refusals are one decision, not fifteen.

The owner's 28-page pack produced 646 outstanding items across SEVEN distinct
reasons — 34 of them the identical pricing refusal, each demanding its own
note. Presenting one decision as thirty-four guarantees the person stops
reading, and a review nobody reads is not a review.

WHAT IS NOT BEING RELAXED

The per-field acknowledgement exists so nobody rubber-stamps a page of real
decisions. Every field still gets its own row, timestamp, actor and MAC — the
audit trail is byte-for-byte what it was. What changes is how many times a
person types the same sentence.

A blanket acknowledge-everything is still refused, and most of this file is
about that line: "these 34 are all pricing" is one decision about one policy,
whereas "acknowledge all 646" spans signatures, missing data and fields nobody
could read.
"""

import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from agent_autofill.integration import review_gate
from agent_autofill.integration.review_gate import (
    ReviewGateError,
    _reason_key,
    acknowledge_group,
    outstanding_groups,
)

PRICING = "Pricing must come from the quotation system, which keeps its own audit trail."
SIGNATURE = "Requires your signature. Agent Autofill never signs anything on your behalf."
NO_DATA = "Nothing on file for this field yet — add it in your company profile."
NOTE = "Checked: these come from the quotation, which I will attach separately."


# --- which reasons group ------------------------------------------------------

@pytest.mark.parametrize("reason,expected", [
    (PRICING, "pricing"),
    (SIGNATURE, "signature"),
    ("Not on the auto-fill whitelist — left for you to complete.", "not_whitelisted"),
    ("Could not read what this field is for, so it was left for you.", "unreadable"),
    ("I could not tell what this field is asking for.", "unrecognised"),
])
def test_a_structural_refusal_is_grouped(reason, expected):
    """The same decision for the same cause, whatever field it lands on."""
    assert _reason_key(reason) == expected


def test_a_missing_value_is_never_grouped():
    """
    "Nothing on file" is a DIFFERENT missing value each time. One note cannot
    describe supplying eight different facts, and these become questions the
    agent asks rather than flags at all.
    """
    assert _reason_key(NO_DATA) is None


def test_an_unknown_reason_stays_individual():
    """Anything not recognised is treated as field-specific — the safe side."""
    assert _reason_key("Something nobody has seen before") is None
    assert _reason_key("") is None
    assert _reason_key(None) is None


# --- the line that does not move ----------------------------------------------

@pytest.mark.parametrize("token", ["all", "ALL", "everything", "*", "any", "yes"])
def test_there_is_no_acknowledge_everything(token):
    """
    Grouping identical refusals is not a blanket acknowledgement, and the
    difference has to be enforced rather than described.
    """
    with pytest.raises(ReviewGateError, match="acknowledge|group"):
        acknowledge_group("co", "rev", token, NOTE)


def test_a_reason_that_is_not_structural_is_refused():
    with pytest.raises(ReviewGateError, match="not a group"):
        acknowledge_group("co", "rev", "no_data", NOTE)


def test_the_group_path_cannot_invent_its_own_note_rules():
    """
    acknowledge_group delegates to acknowledge_field, so the minimum-note rule
    and the blanket-token refusal live in exactly one place. A second copy
    would drift from the original the moment either changed.
    """
    import inspect

    source = inspect.getsource(review_gate.acknowledge_group)
    assert "acknowledge_field(" in source, (
        "the group path writes acknowledgements itself instead of going through "
        "the single-field function that validates and signs them"
    )
    assert "UPDATE autofill_review_item" not in source


def test_grouping_does_not_touch_the_per_field_gate():
    """
    acknowledge_field is unchanged: still one field per call, still refusing
    blanket tokens and thin notes.
    """
    import inspect

    source = inspect.getsource(review_gate.acknowledge_field)
    assert "BLANKET_TOKENS" in source
    assert "MIN_NOTE_CHARS" in source


# --- the summary a person reads -----------------------------------------------

def test_a_group_reports_its_count_and_pages(monkeypatch):
    """
    "Pricing — 34 fields across pages 1, 7, 8" is the line that makes the list
    usable. A bare list of 34 rows is what people stop reading.
    """
    rows = [
        {"item_key": f"F{i:02d}", "label": f"Total {i}", "location": f"page {1 + i % 3}",
         "category": "blocked", "reason": PRICING}
        for i in range(34)
    ] + [
        {"item_key": "F90", "label": "Signature", "location": "page 9",
         "category": "blocked", "reason": SIGNATURE},
        {"item_key": "F91", "label": "VAT number", "location": "page 2",
         "category": "no_data", "reason": NO_DATA},
    ]
    monkeypatch.setattr(review_gate, "_load_review", lambda c, r: None)
    monkeypatch.setattr(review_gate, "_outstanding_rows", lambda r: rows)

    result = outstanding_groups("co", "rev")

    pricing = next(g for g in result["groups"] if g["reason_key"] == "pricing")
    assert pricing["count"] == 34
    assert pricing["pages"] == ["page 1", "page 2", "page 3"]
    assert "34 field(s)" in pricing["summary"]

    # The missing value is NOT swept into a group.
    assert [r["item_key"] for r in result["individual"]] == ["F91"]


def test_the_number_of_decisions_is_what_shrinks(monkeypatch):
    """
    36 outstanding items become 3 decisions: two policies and one real
    question. That is the whole point.
    """
    rows = [{"item_key": f"F{i:02d}", "label": "x", "location": "page 1",
             "category": "blocked", "reason": PRICING} for i in range(34)]
    rows += [{"item_key": "F90", "label": "Sign", "location": "page 9",
              "category": "blocked", "reason": SIGNATURE},
             {"item_key": "F91", "label": "VAT", "location": "page 2",
              "category": "no_data", "reason": NO_DATA}]
    monkeypatch.setattr(review_gate, "_load_review", lambda c, r: None)
    monkeypatch.setattr(review_gate, "_outstanding_rows", lambda r: rows)

    result = outstanding_groups("co", "rev")
    assert result["outstanding_total"] == 36
    assert result["decisions_required"] == 3


def test_groups_are_ordered_by_how_much_they_cost_the_reader(monkeypatch):
    rows = [{"item_key": f"P{i}", "label": "x", "location": "page 1",
             "category": "blocked", "reason": PRICING} for i in range(10)]
    rows += [{"item_key": f"S{i}", "label": "x", "location": "page 9",
              "category": "blocked", "reason": SIGNATURE} for i in range(3)]
    monkeypatch.setattr(review_gate, "_load_review", lambda c, r: None)
    monkeypatch.setattr(review_gate, "_outstanding_rows", lambda r: rows)

    groups = outstanding_groups("co", "rev")["groups"]
    assert [g["reason_key"] for g in groups] == ["pricing", "signature"]
