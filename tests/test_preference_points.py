"""
Claiming preference points: arithmetic from documents, or nothing at all.

`SBD_COMPLIANCE.md` P0-2 — "Donington Vale is Level 1, so under 80/20 the claim
is 20 points. That is arithmetic from a documented fact, and leaving it blank
costs points for no reason."

And the constraint that governs the whole thing, repeated in every brief:

    "Never claim a goal the certificate does not support — that is a
     misrepresentation to an organ of state."

So the claim needs two facts and will not proceed on one. A Level 1 bidder
claims 20 points under 80/20 and 10 under 90/10; guessing the system either
doubles or halves the claim on a live bid.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from agent_autofill.fill_engine.preference_points import (
    ABSENT,
    AMBIGUOUS,
    detect_preference_system,
    goal_claims,
    points_claim,
)
from models.sa_scoring import SYSTEM_80_20, SYSTEM_90_10


# --- reading the system off the form -------------------------------------------

def test_the_standard_declaration_is_read():
    text = ("a) The applicable preference point system for this tender is the "
            "80/20 preference point system.")
    system, evidence = detect_preference_system(text)
    assert system == SYSTEM_80_20
    assert "80/20" in evidence, "the user must be able to check where it came from"


def test_the_rfq_wording_is_read():
    """From the owner's iThemba LABS RFQ, page 1."""
    text = ("Where quotations/proposals are R 2 000.00 or more, the preferential "
            "Procurement System Applicable is 80/20")
    assert detect_preference_system(text)[0] == SYSTEM_80_20


def test_ninety_ten_is_read_too():
    text = ("The applicable preference point system for this tender is the "
            "90/10 preference point system.")
    assert detect_preference_system(text)[0] == SYSTEM_90_10


def test_boilerplate_explaining_the_choice_is_not_a_choice():
    """
    THE trap, and it is in every pack the owner has. SBD 6.1 explains both
    systems before anyone picks one. Matching the first number found would
    produce a confident wrong answer on every document.
    """
    text = (
        "In cases where organs of state intend to use Regulation 3(2), which "
        "states that, if it is unclear whether the 80/20 or 90/10 preference "
        "point system applies, an organ of state must stipulate...\n"
        "the 80/20 system for requirements with a Rand value of up to R50 000 000\n"
        "the 90/10 system for requirements with a Rand value above R50 000 000\n"
    )
    system, evidence = detect_preference_system(text)
    assert system is None
    assert evidence == ABSENT


def test_a_form_stating_both_is_ambiguous_not_a_coin_toss():
    """
    The owner's Johannesburg Water RFQ carries BOTH a) 90/10 and b) 80/20 — a
    template where the buyer was meant to delete one and did not. There is no
    right answer to pick, so it asks.
    """
    text = (
        "a) The applicable preference point system for this tender is the "
        "90/10 preference point system.\n"
        "b) The applicable preference point system for this tender is the "
        "80/20 preference point system.\n"
    )
    system, evidence = detect_preference_system(text)
    assert system is None
    assert evidence == AMBIGUOUS


def test_silence_is_silence():
    assert detect_preference_system("")[0] is None
    assert detect_preference_system("A tender for the supply of drums.")[1] == ABSENT


# --- the claim ------------------------------------------------------------------

@pytest.mark.parametrize("level,expected", [
    (1, 20.0), (2, 18.0), (3, 14.0), (4, 12.0),
    (5, 8.0), (6, 6.0), (7, 4.0), (8, 2.0),
])
def test_the_eighty_twenty_table_is_the_regulated_one(level, expected):
    assert points_claim(level, SYSTEM_80_20) == expected


@pytest.mark.parametrize("level,expected", [
    (1, 10.0), (2, 9.0), (3, 6.0), (4, 5.0),
    (5, 4.0), (6, 3.0), (7, 2.0), (8, 1.0),
])
def test_the_ninety_ten_table_is_the_regulated_one(level, expected):
    assert points_claim(level, SYSTEM_90_10) == expected


def test_donington_vale_claims_twenty():
    """The worked example in the brief."""
    assert points_claim(1, SYSTEM_80_20) == 20.0


def test_no_system_means_no_claim():
    """
    The reason the whole module exists. A Level 1 bidder claims 20 under 80/20
    and 10 under 90/10 — a wrong guess is out by a factor of two on a bid.
    """
    assert points_claim(1, None) is None
    assert points_claim(1, ABSENT) is None
    assert points_claim(1, "80-20") is None


def test_no_level_means_no_claim():
    for level in (None, "", "unknown", "Level One"):
        assert points_claim(level, SYSTEM_80_20) is None


@pytest.mark.parametrize("level", [0, 9, 10, -1])
def test_a_level_off_the_scale_claims_nothing(level):
    """
    The scale is 1 to 8. The profile has held 9 — not a valid level — while the
    certificate proving Level 1 sat unread in the Vault. A claim built from that
    would be a number on a government form that nothing supports.
    """
    assert points_claim(level, SYSTEM_80_20) is None


# --- the specific goals ---------------------------------------------------------

def test_a_documented_goal_is_claimable():
    claims = {c["column"]: c for c in goal_claims({"owned_51pc_black": True})}
    assert claims["owned_51pc_black"]["qualifies"] is True
    assert claims["owned_51pc_black"]["source"] == "B-BBEE certificate"


def test_a_goal_the_certificate_denies_is_not_claimed():
    """
    His certificate reads "0% BLACK FEMALE OWNERSHIP". His own hand-completed
    SBD 6.1 leaves that row blank. False is an answer, and the answer is no.
    """
    claims = {c["column"]: c for c in goal_claims({"owned_51pc_black_women": False})}
    assert claims["owned_51pc_black_women"]["qualifies"] is False


def test_nothing_on_file_is_not_a_no():
    """
    Three states, not two. An unset flag is a question, not a denial — treating
    it as False silently forfeits points the company may qualify for.
    """
    claims = {c["column"]: c for c in goal_claims({})}
    assert all(c["qualifies"] is None for c in claims.values())
    assert all(c["source"] is None for c in claims.values())


def test_the_goals_carry_no_points():
    """
    The points for each goal are printed on the form by the organ of state, per
    tender. Storing a number here would mean claiming one this tender never
    allocated.
    """
    for claim in goal_claims({"owned_51pc_black": True}):
        assert "points" not in claim


def test_nothing_here_invents_a_flag():
    """
    Every one of the four comes from a document or from the user. There is no
    path that derives one from another — 100% black ownership says nothing
    about youth, women or disability.
    """
    claims = {c["column"]: c["qualifies"]
              for c in goal_claims({"owned_51pc_black": True})}
    assert claims["owned_51pc_black"] is True
    assert claims["owned_51pc_black_women"] is None
    assert claims["owned_51pc_black_youth"] is None
    assert claims["owned_51pc_black_disability"] is None
