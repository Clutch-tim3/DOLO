"""
Being unsure is a reason to ask, not a reason to leave a blank.

The owner, more than once, and finally:

    "the address line was not filled i keep telling you the agent needs to ask
     me if its not certain, postal address or physical address ill answer all
     that needs to be answered"

A blank labelled only "ADDRESS" was refused — correctly. Postal and physical
are different answers and writing the wrong one onto a government bid is worse
than leaving it. But the refusal was where it ended, and he got an empty line on
a submitted form instead of a three-second question.

TWO SEPARATE THINGS WERE BLOCKING THE QUESTION, and both are covered here:

  1. `missing_profile_fields` only built questions for "Nothing on file"
     refusals. An ambiguous label is not that — we may hold BOTH values — so it
     produced no question.
  2. `_outstanding_rows` filters `advisory = 0`, and `ambiguous_label` is
     advisory. Even once (1) was fixed, the row never reached the builder.

`advisory` answers "must a person tick this before export?" — for an ambiguous
label, no. The question builder answers "is this worth asking about?" — yes.
One flag was being made to answer both.
"""

import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from agent_autofill.integration.missing_fields import missing_profile_fields
from agent_autofill.integration import autofill_tools
from agent_autofill.extraction import learned_labels

BOTH_ADDRESSES = {"physical_address": "12 Main Road, Faure, 7131",
                  "postal_address": "PO Box 722, Somerset West, 7129"}


def _row(label, category, reason, location="page 3"):
    return {"item_key": f"{label}-{location}", "label": label,
            "category": category, "reason": reason, "location": location}


@pytest.fixture
def company_id():
    cid = f"ask-{uuid.uuid4().hex[:10]}"
    yield cid
    for lesson in learned_labels.lessons(cid):
        learned_labels.forget(cid, lesson["example_label"] or lesson["normalised"])


# --- the question gets built ---------------------------------------------------

def test_a_bare_address_becomes_a_question():
    """THE bug. It produced nothing at all."""
    rows = [_row("ADDRESS", "ambiguous_label",
                 "The label is too general to answer safely.")]

    questions = missing_profile_fields(rows, BOTH_ADDRESSES)

    assert len(questions) == 1, "an ambiguous label must be asked about"
    q = questions[0]
    assert q["kind"] == "which_one"
    assert "physical" in q["prompt"] and "postal" in q["prompt"]


def test_it_is_asked_even_though_both_values_are_on_file():
    """
    The old filter skipped any field the profile already had a value for, which
    is right for "supply" and exactly wrong here. Nothing is missing except
    knowing which one the form wants.
    """
    assert missing_profile_fields(
        [_row("ADDRESS", "ambiguous_label", "too general")], BOTH_ADDRESSES)


def test_one_question_however_many_blanks():
    """"ADDRESS" on four pages is one question, not four."""
    rows = [_row("ADDRESS", "ambiguous_label", "too general", f"page {n}")
            for n in (3, 7, 12, 19)]

    questions = missing_profile_fields(rows, BOTH_ADDRESSES)

    assert len(questions) == 1
    assert questions[0]["count"] == 4
    assert len(questions[0]["locations"]) == 4, "but it says where all four are"


def test_a_label_that_is_not_ambiguous_is_left_alone():
    """"PHYSICAL ADDRESS" already fills. Asking about it would be noise."""
    assert missing_profile_fields(
        [_row("PHYSICAL ADDRESS", "ambiguous_label", "too general")],
        BOTH_ADDRESSES) == []


def test_a_signature_is_still_never_asked_about():
    """Not everything unfilled is a question. This one is never ours."""
    rows = [_row("SIGNATURE OF BIDDER", "blocked", "Requires your signature")]
    assert missing_profile_fields(rows, BOTH_ADDRESSES) == []


def test_a_price_cell_is_still_never_asked_about():
    rows = [_row("R", "pricing_cell", "This is a price cell.")]
    assert missing_profile_fields(rows, BOTH_ADDRESSES) == []


def test_the_two_kinds_are_distinguishable():
    """
    The agent must not treat them the same: one supplies a value, the other
    picks between values already held.
    """
    rows = [
        _row("ADDRESS", "ambiguous_label", "too general"),
        _row("CELL NUMBER", "no_data", "Nothing on file for this field yet"),
    ]
    kinds = {q["field"]: q["kind"] for q in missing_profile_fields(rows, {})}
    assert kinds["physical_address"] == "which_one"
    assert kinds["standard_cell"] == "supply"


# --- the row reaches the builder ----------------------------------------------

def test_askable_rows_include_advisory_ambiguous_labels():
    """
    Blocker (2). `_outstanding_rows` filters advisory=0 and would never show
    this row, so fixing the builder alone changed nothing in production.
    """
    import inspect
    from agent_autofill.integration import review_gate

    sql = inspect.getsource(review_gate._askable_rows)
    assert "advisory = 0 OR category = ?" in sql
    assert "ambiguous_label" in sql


def test_the_question_tool_uses_the_askable_rows():
    """The tool must not quietly go back to the narrower query."""
    import inspect

    src = inspect.getsource(autofill_tools._autofill_missing_details)
    assert "_askable_rows" in src
    assert "_outstanding_rows(" not in src


def test_an_ambiguous_label_still_does_not_block_an_export():
    """
    The reason it was advisory in the first place, and that must not change.
    Asking a question is not the same as demanding an acknowledgement.
    """
    from agent_autofill.integration.review_gate import ADVISORY_CATEGORIES
    assert "ambiguous_label" in ADVISORY_CATEGORIES


# --- the answer is recorded ---------------------------------------------------

def test_the_users_answer_is_remembered(company_id):
    """
    Without this the same question is asked on every pack forever. There was
    nowhere to put the answer at all — `update_company_profile` is wrong, since
    the value is already on file.
    """
    out = autofill_tools._autofill_resolve_label(
        company_id, "ADDRESS", "physical_address")

    assert out["status"] == "success"
    lesson = learned_labels.lookup(company_id, "ADDRESS")
    assert lesson["canonical_field"] == "physical_address"
    assert lesson["taught_by"] == "user"


def test_an_invented_field_is_refused(company_id):
    out = autofill_tools._autofill_resolve_label(
        company_id, "ADDRESS", "home_address_of_director")

    assert out["status"] == "error"
    assert learned_labels.lookup(company_id, "ADDRESS") is None


def test_the_answer_cannot_unblock_a_signature(company_id):
    """
    A lesson may be recorded for any fillable field — including a wrong one —
    and it still cannot cause a signature to be filled, because `is_blocked`
    runs on the LABEL in the fill engine before any lesson is consulted.
    """
    from agent_autofill.fill_engine.never_fill_fields import is_blocked

    out = autofill_tools._autofill_resolve_label(
        company_id, "SIGNATURE OF BIDDER", "company_name")
    assert out["status"] == "success", "recording it is allowed"

    assert is_blocked("SIGNATURE OF BIDDER").blocked, "and it is still refused"


def test_the_tool_is_registered():
    assert "autofill_resolve_label" in autofill_tools.AUTOFILL_TOOL_HANDLERS
    names = {t["name"] for t in autofill_tools.autofill_tools}
    assert "autofill_resolve_label" in names, "the agent cannot call what it cannot see"


def test_the_agent_is_told_to_ask_rather_than_guess():
    from pathlib import Path
    import agent.main_agent as ma

    prompt = Path(ma.__file__).read_text(encoding="utf-8")
    assert "which_one" in prompt
    assert "autofill_resolve_label" in prompt
    assert "Being unsure is a reason to ask" in prompt
