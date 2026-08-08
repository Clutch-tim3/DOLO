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


# --- found by the adversarial pass, after the gate was built ---------------


def test_omitted_company_id_does_not_skip_the_ownership_check():
    """
    The guard read `company_id is not None`, so an omitted company_id skipped
    ownership entirely and finalised another company's quote. Not
    model-reachable — execute_tool injects the session value — but a direct
    caller could hit it, so absence is refused rather than waved through.
    """
    from agent.main_agent import finalize_quote_flow

    owner = f"victim-{uuid.uuid4().hex[:8]}"
    quote_id = log_draft_quote(owner, "TENDER_TEST", LAUNDERED_ITEMS)
    try:
        result = finalize_quote_flow(quote_id, None, company_id=None)
        assert _stored(quote_id)[0] == "DRAFT", "omitted company_id finalised it"
        assert "successfully" not in str(result).lower()
    finally:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("DELETE FROM quote_audit_log WHERE quote_id = ?", (quote_id,))


@pytest.mark.parametrize("bad", [float("inf"), float("-inf"), float("nan")])
def test_non_finite_prices_are_refused(flagged_quote, bad):
    """
    inf and nan both survive float() and both slip past `price < 0` — nan
    because every comparison with it is False. Either reaches json.dumps,
    which emits `Infinity` / `NaN`: Python reads those back, the JSON spec
    does not allow them, and any other consumer of the audit row chokes.
    """
    from agent.quotation.quote_audit_log import resolve_quote_item

    company_id, quote_id = flagged_quote
    out = resolve_quote_item(quote_id, company_id, 0, bad)
    assert out["status"] == "error"
    assert "finite" in out["message"].lower()
    assert "MANUAL_REVIEW_REQUIRED" in [
        i.get("price_status") for i in json.loads(_stored(quote_id)[1])
    ]


def test_the_gate_is_not_a_dead_end(flagged_quote):
    """
    The gate decides from stored state, so something must be able to WRITE a
    confirmed price into stored state. Nothing could: resolve_quote_item was
    in neither the tool registry nor any route, which made a flagged quote
    impossible to finalise by any path. A refusal with no way forward is a
    broken feature, not a safe one.
    """
    from agent.tool_dispatch import TOOL_REGISTRY, execute_tool
    from agent.main_agent import quotation_tools

    assert "resolve_quote_item" in TOOL_REGISTRY
    assert "resolve_quote_item" in [t["name"] for t in quotation_tools]

    company_id, quote_id = flagged_quote
    refused, _ = execute_tool("finalize_quotation", {"quote_id": quote_id}, company_id)
    assert "unresolved" in str(refused).lower()

    resolved, is_err = execute_tool(
        "resolve_quote_item",
        {"quote_id": quote_id, "item_index": 0, "price": 87500.0},
        company_id,
    )
    assert is_err is False and resolved["remaining_flags"] == 0

    done, _ = execute_tool("finalize_quotation", {"quote_id": quote_id}, company_id)
    assert "successfully" in str(done).lower()
    assert _stored(quote_id)[0] == "FINAL"


def test_resolve_is_tenant_pinned_through_the_registry(flagged_quote):
    """The model supplies quote_id; company_id is injected, never taken."""
    from agent.tool_dispatch import execute_tool

    _, quote_id = flagged_quote
    out, _ = execute_tool(
        "resolve_quote_item",
        {"quote_id": quote_id, "item_index": 0, "price": 1.0,
         "company_id": "attacker-supplied"},
        "attacker-session",
    )
    assert out["status"] == "error"
    assert "MANUAL_REVIEW_REQUIRED" in [
        i.get("price_status") for i in json.loads(_stored(quote_id)[1])
    ]
