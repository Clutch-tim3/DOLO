"""
Pricing never invents a figure.

`search_price` used to return R85.50 marked HIGH_CONFIDENCE, sourced to a real
Makro URL, for any description containing "paper" — with no network call. That
number reached the line total, the subtotal, the VAT, and a PDF headed
"INVITATION FOR BID". HIGH_CONFIDENCE is not a flagged status, so it also
passed the finalisation gate with nobody confirming it.

The agent's system prompt has always said "Never fabricate a price."
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from agent.quotation.price_search import get_prices_for_items, search_price
from agent.quotation.quote_audit_log import FLAGGED_STATUSES


# The exact descriptions that used to produce invented figures.
@pytest.mark.parametrize("description", [
    "A4 paper 80gsm", "Box of pens", "PAPER", "ballpoint pen black",
    "Ampoule filling machine", "", "file", "anything at all",
])
def test_no_description_produces_a_price(description):
    out = search_price(description)
    assert out["price"] is None, f"invented a price for {description!r}"
    assert out["source_url"] is None, f"invented a source for {description!r}"
    assert out["retailer_name"] is None


@pytest.mark.parametrize("description", ["A4 paper", "pens", "widget"])
def test_every_item_is_flagged_for_a_human(description):
    """
    And flagged with a status the finalisation gate actually recognises —
    HIGH_CONFIDENCE was not in FLAGGED_STATUSES, which is how the old figures
    walked past it.
    """
    status = search_price(description)["price_status"]
    assert status in FLAGGED_STATUSES, f"{status} would not stop finalisation"


def test_the_price_does_not_vary_with_the_wording():
    """A number that changes with the description while nothing is being looked
    up is the behaviour this replaced."""
    a, b = search_price("A4 paper"), search_price("hydraulic press")
    assert a["price"] == b["price"] is None
    assert a["price_status"] == b["price_status"]


def test_totals_are_never_computed_from_an_invented_price():
    items = [{"description": "A4 paper", "quantity": 500},
             {"description": "Box of pens", "quantity": 10}]
    priced = get_prices_for_items(items)
    assert len(priced) == 2
    for p in priced:
        assert p["total"] is None, "a total was computed with no price behind it"
        assert p["price"] is None


def test_a_reason_is_given_rather_than_a_silent_blank():
    """The user should know no lookup happened, not assume one found nothing."""
    note = search_price("A4 paper").get("price_note", "")
    assert "no automatic price source" in note.lower()
