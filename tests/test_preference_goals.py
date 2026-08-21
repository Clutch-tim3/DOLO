"""
The specific-goals table: claim what a document proves, ask about the rest.

A point claimed here is a claim made to an organ of state. Every brief in this
repo says the same thing about it:

    "Never claim a preference point the company does not qualify for. Every one
     of those four flags comes from a document or from the user, never from
     inference."

WHAT READING THE REAL FORMS CHANGED

The brief asked for "the specific-goals table filled from the four
`owned_51pc_*` columns". Both of the owner's real tables make that impossible as
stated, because every tender writes its own goals:

    his SBD 6.1     black / black women / black disability / black youth /
                    rural or township / EME or QSE
    his 145pg pack  Youth / Local Production and Content / Locality /
                    Historically Disadvantaged Individuals / Women / Disability

Different goals, different points, and tiered — ">51%" scores 5 where "10-50%"
scores 2.5.

So a row is claimed only where a stored flag is strictly NARROWER than the goal.
Everything else is a question carrying the tender's own numbers.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from agent_autofill.fill_engine.preference_goals import (
    goal_rows,
    propose_claims,
)

PACK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "data", "archive", "temp_tender_BID_DOCUMENT_06FY27_.pdf")

ALL_TRUE = {
    "owned_51pc_black": True, "owned_51pc_black_women": True,
    "owned_51pc_black_youth": True, "owned_51pc_black_disability": True,
}


def _rows(*pairs):
    return [{"goal": g, "allocated": a, "row_index": i}
            for i, (g, a) in enumerate(pairs)]


def _by_goal(proposals):
    return {p["goal"]: p for p in proposals}


# --- what may be claimed --------------------------------------------------------

@pytest.mark.parametrize("goal,column", [
    (">51% Women Ownership 10-50% Women Ownership", "owned_51pc_black_women"),
    (">51% Youth Ownership 10-50% Youth Ownership", "owned_51pc_black_youth"),
    (">51% Disability Ownership", "owned_51pc_black_disability"),
])
def test_a_narrower_flag_proves_a_broader_goal(goal, column):
    """
    51% black-women-owned IS 51% women-owned. The implication holds in that
    direction with no judgement, which is what makes it safe to claim.
    """
    p = propose_claims(_rows((goal, "3 1.5 0")), {column: True})[0]
    assert p["action"] == "claim"
    assert p["from_column"] == column
    assert p["because"], "the user must be told which document supports it"


def test_the_claim_uses_the_tenders_own_number():
    """
    Points come from the row the organ of state filled in, never from a table
    stored here — a stored one would claim points this tender never offered.
    """
    p = propose_claims(_rows((">51% Women Ownership", "7 3.5 0")),
                       {"owned_51pc_black_women": True})[0]
    assert p["points"] == "7"


def test_the_top_tier_is_claimed():
    """
    The flag means 51% or more, which is the top band. Claiming the 10-50% tier
    would forfeit a point the certificate supports.
    """
    p = propose_claims(_rows((">51% Youth Ownership 10-50% Youth", "5 2.5 0")),
                       {"owned_51pc_black_youth": True})[0]
    assert p["points"] == "5"


# --- what may NOT be claimed ----------------------------------------------------

def test_historically_disadvantaged_is_never_inferred_from_black_ownership():
    """
    THE one that matters. HDI is a defined term — disenfranchised before the
    1994 Constitution, by race, gender or disability. B-BBEE black ownership
    overlaps it and is not it. Claiming three points on that reasoning is the
    misrepresentation every brief forbids.
    """
    p = propose_claims(
        _rows((">51% Historically Disadvantaged Individuals Ownership", "3 1.5 0")),
        ALL_TRUE)[0]
    assert p["action"] == "ask"
    assert p["points"] is None


@pytest.mark.parametrize("goal", [
    "Local Production and Content",
    "Locality (Enterprises located in the Eastern Cape Province)",
    "Sub-contracting to an EME",
    "Job creation",
])
def test_a_goal_that_is_not_about_ownership_is_never_claimed(goal):
    """No ownership flag can speak to where work is done or who is employed."""
    assert propose_claims(_rows((goal, "4")), ALL_TRUE)[0]["action"] == "ask"


def test_a_false_flag_asks_rather_than_declining():
    """
    A False flag is not evidence against the broader goal: a company 51% owned
    by white women is women-owned while `owned_51pc_black_women` is False.
    Turning it into a silent no forfeits points the company may be owed.
    """
    p = propose_claims(_rows((">51% Women Ownership", "3 1.5 0")),
                       {"owned_51pc_black_women": False})[0]
    assert p["action"] == "ask"


def test_an_absent_flag_asks():
    p = propose_claims(_rows((">51% Women Ownership", "3 1.5 0")), {})[0]
    assert p["action"] == "ask"


def test_no_proposal_ever_carries_a_signature_or_a_price():
    proposals = propose_claims(
        _rows((">51% Women Ownership", "3"), ("Local Content", "4")), ALL_TRUE)
    for p in proposals:
        assert set(p) <= {
            "goal", "action", "points", "allocated", "from_column",
            "because", "question", "row_index",
        }


def test_every_question_carries_the_tenders_numbers():
    """A question the user can answer in one line, not a field name."""
    p = propose_claims(_rows(("Local Production and Content", "4")), {})[0]
    assert "4" in p["question"]
    assert "Local Production and Content" in p["question"]


# --- against the real document --------------------------------------------------

@pytest.mark.skipif(not os.path.exists(PACK), reason="the 145-page pack is not present")
def test_the_real_goals_table_is_found_and_read():
    """
    Page 53 of the owner's pack. The extractor returned ZERO blanks for this
    table, which is why the points claim had nowhere to land.
    """
    import pdfplumber
    from agent_autofill.fill_engine.preference_goals import find_goals_table

    with pdfplumber.open(PACK) as pdf:
        found = find_goals_table(pdf.pages[52])
        assert found is not None, "the goals table was not recognised"
        rows = goal_rows(found)

    goals = [r["goal"] for r in rows]
    assert len(rows) == 6, f"expected 6 goal rows, got {len(rows)}: {goals}"
    assert not any("specific goals allocated" in g.lower() for g in goals), \
        "the header row leaked in as a goal"
    assert not any(g.strip().lower() == "total" for g in goals)


@pytest.mark.skipif(not os.path.exists(PACK), reason="the 145-page pack is not present")
def test_on_the_real_table_only_youth_is_claimed_for_donington_vale():
    """
    His certificate: 100% black, 100% black youth, 0% black female, 0% black
    disability. On this tender that proves exactly one row.
    """
    import pdfplumber
    from agent_autofill.fill_engine.preference_goals import find_goals_table

    profile = {"owned_51pc_black": True, "owned_51pc_black_youth": True,
               "owned_51pc_black_women": False,
               "owned_51pc_black_disability": False}

    with pdfplumber.open(PACK) as pdf:
        proposals = propose_claims(goal_rows(find_goals_table(pdf.pages[52])),
                                   profile)

    claimed = [p for p in proposals if p["action"] == "claim"]
    assert len(claimed) == 1, [p["goal"] for p in claimed]
    assert "Youth" in claimed[0]["goal"]
    assert claimed[0]["points"] == "5"
