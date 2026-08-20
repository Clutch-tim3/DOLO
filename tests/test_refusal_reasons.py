"""
Saying why a field was left, accurately.

Every refusal that was not a hard block came back as one category and one
sentence: "I could not tell what this field is asking for." On a real RFQ that
fired 41 times, and 26 of those were the system working exactly as intended —
SBD 4 declaration fields, price cells, and commercial terms for that bid.

Describing a correct decision as confusion is worse than a bad refusal,
because it teaches the user to distrust the good ones. These tests hold the
descriptions to what actually happened.
"""

from __future__ import annotations

import pytest

from agent_autofill.extraction.field_alias_dictionary import match_label
from agent_autofill.fill_engine.refusal_reasons import (
    CORRECT_BY_DESIGN,
    REASONS,
    classify_unfilled,
)


def _classify(label):
    return classify_unfilled(label, match_label(label))[0]


# --- the SBD 4 declaration -------------------------------------------------


@pytest.mark.parametrize("label", [
    "Name of State institution",
    "SURNAME AND NAME",
    "2.2.1 If so, furnish particulars",
    "Identity Number",
    "Are you in the service of the state",
    "Name of shareholder",
])
def test_declaration_fields_are_named_as_declarations(label):
    """
    A sworn declaration is not something the system failed to understand.

    SBD 4 is the declaration of interest. A wrong answer there is not a typo,
    it is a false declaration to an organ of state — so the refusal is the
    whole point, and it must read like a decision rather than a shrug.
    """
    assert _classify(label) == "declaration"
    assert "sworn declaration" in REASONS["declaration"]
    assert "will not answer it for you" in REASONS["declaration"]


# --- pricing ---------------------------------------------------------------


@pytest.mark.parametrize("label", ["R", "r", "R:", "R ."])
def test_a_bare_currency_cell_is_named_as_pricing(label):
    """Eight of the owner's flags were a rand column, not questions."""
    assert _classify(label) == "pricing_cell"


def test_a_number_that_merely_starts_with_r_is_not_a_currency_cell():
    """`_CURRENCY_ONLY` must not swallow real labels beginning with R."""
    assert _classify("Registration Number") != "pricing_cell"
    assert _classify("Reference") != "pricing_cell"


# --- commercial terms for this bid -----------------------------------------


@pytest.mark.parametrize("label", [
    "Lead Time for delivery",
    "Packaging Charge",
    "Delivery Charge to iThemba LABS",
    "Warranty period",
    "Validity of offer",
])
def test_tender_terms_are_named_as_questions_about_this_bid(label):
    """
    These are real questions with real answers — the answers just are not
    facts about the company, so no profile could hold them. "I could not tell
    what this is asking" is wrong twice over: it is legible, and it is not
    the system's to answer.
    """
    assert _classify(label) == "tender_terms"
    assert "nothing on file" in REASONS["tender_terms"]


# --- too general to guess --------------------------------------------------


@pytest.mark.parametrize("label", ["ADDRESS", "NAME", "Full Name", "AMOUNT"])
def test_a_label_too_general_to_resolve_asks_rather_than_shrugs(label):
    """
    Postal and physical are different answers and only one is right. The
    system knows the value and cannot tell which field wants it, which is a
    question, not a failure.
    """
    assert _classify(label) == "ambiguous_label"
    assert "Tell me which value it wants" in REASONS["ambiguous_label"]


# --- a genuine miss is still a genuine miss --------------------------------


def test_an_unrecognised_label_is_still_reported_honestly():
    """The category must not become a way of never admitting a miss."""
    assert _classify("Zorblatt coefficient") == "unmatched"
    assert REASONS["unmatched"] == "I could not tell what this field is asking for."


def test_declaration_wins_over_every_other_reading():
    """
    Order matters where being wrong is worst. "Director's Delivery Address"
    contains a commercial marker, but it is a declaration field first.
    """
    assert _classify("Director details") == "declaration"


# --- the gate must not move ------------------------------------------------


def test_the_new_categories_do_not_start_blocking_exports():
    """
    Every one of these was `unmatched`, which is advisory. Renaming a thing
    must not quietly change what blocks an export — that would turn 37 notes
    on the owner's pack into 37 mandatory acknowledgements as a side effect of
    better wording.
    """
    from agent_autofill.integration.review_gate import ADVISORY_CATEGORIES

    for category in REASONS:
        assert category in ADVISORY_CATEGORIES, (
            f"{category} would newly block an export")


def test_correct_by_design_never_includes_a_genuine_miss():
    assert "unmatched" not in CORRECT_BY_DESIGN
    assert "ambiguous_label" not in CORRECT_BY_DESIGN
    assert CORRECT_BY_DESIGN <= set(REASONS)
