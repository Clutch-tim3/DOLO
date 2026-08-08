"""
The quotation finalisation gate.

`finalize_quotation` is in TOOL_REGISTRY, so the model can call it and its
arguments are attacker-influenceable in the same way any tool input is. The
rule being defended is "a quote with unresolved flagged items cannot be
finalised". If that rule is evaluated against the caller's own list rather than
against what was recorded at draft time, the caller simply supplies a clean
list and walks past it.

These tests drive the real SQLite audit log, not a mock, because the whole
point is that stored state is the authority.
"""

import json
import os
import sqlite3
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.db_paths import AGENT_MEMORY_DB as DB_PATH
from agent.quotation.quote_audit_log import log_draft_quote

FLAGGED_ITEMS = [
    {"description": "Ampoule filling machine", "quantity": 1,
     "price": None, "total": None, "price_status": "MANUAL_REVIEW_REQUIRED"},
    {"description": "5-year maintenance", "quantity": 1,
     "price": 120000.0, "total": 120000.0, "price_status": "OK"},
]

# The same list with the flag scrubbed. This is what an attacker passes.
LAUNDERED_ITEMS = [
    {"description": "Ampoule filling machine", "quantity": 1,
     "price": 1.0, "total": 1.0, "price_status": "OK"},
    {"description": "5-year maintenance", "quantity": 1,
     "price": 120000.0, "total": 120000.0, "price_status": "OK"},
]


@pytest.fixture
def flagged_quote():
    """A quote recorded at draft time with one unresolved flagged item."""
    company_id = f"test-co-{uuid.uuid4().hex[:8]}"
    quote_id = log_draft_quote(company_id, "TENDER_TEST", FLAGGED_ITEMS)
    yield company_id, quote_id
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM quote_audit_log WHERE quote_id = ?", (quote_id,))


def _stored(quote_id):
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT status, line_items FROM quote_audit_log WHERE quote_id = ?",
            (quote_id,),
        ).fetchone()
    return row


def test_laundered_item_list_cannot_finalize_a_flagged_quote(flagged_quote):
    """
    THE BYPASS. Stored state says MANUAL_REVIEW_REQUIRED. The caller passes a
    list where that flag has been changed to OK. Finalisation must be refused,
    and the stored row must still say DRAFT afterwards.
    """
    from agent.main_agent import finalize_quote_flow

    company_id, quote_id = flagged_quote
    result = finalize_quote_flow(quote_id, LAUNDERED_ITEMS, company_id=company_id)

    status, _ = _stored(quote_id)
    assert status == "DRAFT", (
        f"BYPASS: quote finalised via a laundered item list (status={status})"
    )
    assert "successfully" not in str(result).lower()


def test_stored_flag_survives_the_laundered_list(flagged_quote):
    """The caller's list must not overwrite what was recorded at draft time."""
    from agent.main_agent import finalize_quote_flow

    company_id, quote_id = flagged_quote
    finalize_quote_flow(quote_id, LAUNDERED_ITEMS, company_id=company_id)

    _, line_items = _stored(quote_id)
    statuses = [i.get("price_status") for i in json.loads(line_items)]
    assert "MANUAL_REVIEW_REQUIRED" in statuses, (
        "BYPASS: the caller's list overwrote the recorded flag"
    )


def test_another_companys_quote_cannot_be_finalized(flagged_quote):
    """
    quote_id is a UUID, but guessing is not the threat model — the model holds
    ids from its own context. Finalisation must check ownership.
    """
    from agent.main_agent import finalize_quote_flow

    _, quote_id = flagged_quote
    result = finalize_quote_flow(quote_id, LAUNDERED_ITEMS, company_id="attacker-co")

    status, _ = _stored(quote_id)
    assert status == "DRAFT", "CROSS-TENANT: another company finalised this quote"
    assert "successfully" not in str(result).lower()


def test_a_genuinely_clean_quote_still_finalizes():
    """The gate must not become a wall — resolved quotes still go through."""
    from agent.main_agent import finalize_quote_flow

    company_id = f"test-co-{uuid.uuid4().hex[:8]}"
    quote_id = log_draft_quote(company_id, "TENDER_TEST", LAUNDERED_ITEMS)
    try:
        result = finalize_quote_flow(quote_id, LAUNDERED_ITEMS, company_id=company_id)
        status, _ = _stored(quote_id)
        assert status == "FINAL", f"clean quote was refused: {result}"
    finally:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("DELETE FROM quote_audit_log WHERE quote_id = ?", (quote_id,))


def test_unknown_quote_id_is_refused():
    from agent.main_agent import finalize_quote_flow

    result = finalize_quote_flow(str(uuid.uuid4()), LAUNDERED_ITEMS, company_id="x")
    assert "successfully" not in str(result).lower()
