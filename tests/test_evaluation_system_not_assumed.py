"""
Which PPPFA system applies is never assumed.

`get_evaluation_system(None)` returned '80/20'. That is an assertion about
which statute governs a bid, made with no information — and it is not neutral:
80/20 awards a level 1 bidder 20 preference points where 90/10 awards 10, so
the default also overstates the bidder's position by double whenever the guess
is wrong.

It was reachable rather than theoretical. `pdf_parser` filled an unparsed
tender value with `bid_price * 1.2`, so a bid just over R41.7m crossed the R50m
threshold on a guess and was told the wrong system applied.

Sibling of test_price_search_never_fabricates.py and
test_regional_router_never_fabricates.py.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from models.sa_scoring import (
    calculate_price_score,
    calculate_total_sa_score,
    get_bbbee_points,
    get_evaluation_system,
)

# Literals, not imported constants: this module must be able to run against the
# version that had the bug, or it proves nothing. Importing the new names made
# the whole file fail on collection with an ImportError, which looks like a
# caught regression and is not one.
SYSTEM_80_20, SYSTEM_90_10 = "80/20", "90/10"
PPPFA_THRESHOLD_ZAR = 50_000_000

PARSER = Path(__file__).resolve().parent.parent / "models" / "pdf_parser.py"


def test_an_unknown_tender_value_yields_no_system():
    assert get_evaluation_system(None) is None


@pytest.mark.parametrize("value,expected", [
    (1_000_000, SYSTEM_80_20),
    (PPPFA_THRESHOLD_ZAR - 1, SYSTEM_80_20),
    (PPPFA_THRESHOLD_ZAR, SYSTEM_90_10),
    (60_000_000, SYSTEM_90_10),
])
def test_a_known_tender_value_still_selects_correctly(value, expected):
    """The real rule must survive the removal of the default."""
    assert get_evaluation_system(value) == expected


def test_bbbee_points_are_withheld_when_the_system_is_unknown():
    """
    Level 1 is worth 20 points under one system and 10 under the other. Quoting
    either without knowing which applies is picking a number.
    """
    assert get_bbbee_points(1, None) is None
    assert get_bbbee_points(1, SYSTEM_80_20) == 20.0
    assert get_bbbee_points(1, SYSTEM_90_10) == 10.0


def test_an_unrecognised_system_does_not_silently_become_90_10():
    """
    The lookup was `if system == '80/20': ... else: 90/10`, so anything
    unexpected — a typo, a None — quietly halved every bidder's points.
    """
    assert get_bbbee_points(1, "80/20 ") is None
    assert get_bbbee_points(1, "eighty-twenty") is None
    assert calculate_price_score(100, 100, "not-a-system") is None


def test_the_whole_score_is_withheld_without_a_tender_value():
    res = calculate_total_sa_score(
        supplier_price=450000, lowest_competing_price=400000,
        bbbee_level=1, tender_value_zar=None,
    )
    assert res["evaluation_system"] is None
    assert res["bbbee_points"] is None
    assert res["price_score"] is None
    assert res["total_score"] is None
    assert res["competitive_position"] is None
    assert "not found" in res["evaluation_system_unavailable_reason"].lower()


def test_a_known_value_still_scores_normally():
    """The withholding must not swallow the case where everything is known."""
    res = calculate_total_sa_score(
        supplier_price=400000, lowest_competing_price=400000,
        bbbee_level=1, tender_value_zar=1_000_000,
    )
    assert res["evaluation_system"] == SYSTEM_80_20
    assert res["bbbee_points"] == 20.0
    assert res["price_score"] == 80.0
    assert res["total_score"] == 100.0
    assert res["competitive_position"] == "Strong"


def test_the_parser_no_longer_guesses_a_tender_value():
    body = PARSER.read_text(encoding="utf-8")
    code = [ln for ln in body.splitlines() if not ln.strip().startswith("#")]
    offenders = [ln.strip() for ln in code if "* 1.2" in ln]
    assert not offenders, f"tender_value is still guessed from bid_price: {offenders}"
