import uuid
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from agent.db_paths import AGENT_MEMORY_DB as DB_PATH

def init_audit_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS quote_audit_log (
                quote_id TEXT PRIMARY KEY,
                company_id TEXT,
                tender_id TEXT,
                line_items TEXT, -- JSON
                generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                finalized_at TIMESTAMP,
                status TEXT
            )
        """)

init_audit_db()

def log_draft_quote(company_id: str, tender_id: str, priced_items: list) -> str:
    quote_id = str(uuid.uuid4())
    items_json = json.dumps(priced_items)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO quote_audit_log (quote_id, company_id, tender_id, line_items, status) VALUES (?, ?, ?, ?, ?)",
            (quote_id, company_id, tender_id, items_json, "DRAFT")
        )
    return quote_id

# The statuses that mean "a human still has to price this". Kept here rather
# than in quote_builder so the gate and the document agree on one definition.
FLAGGED_STATUSES = ("MANUAL_REVIEW_REQUIRED", "LOW_CONFIDENCE")


def get_quote_record(quote_id: str) -> dict | None:
    """
    The recorded state of a quote — the only authority on whether it may be
    finalized. Callers pass item lists around, and those lists are
    attacker-influenceable (finalize_quotation is in TOOL_REGISTRY); what was
    written at draft time is not.
    """
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT quote_id, company_id, tender_id, line_items, status, finalized_at"
            " FROM quote_audit_log WHERE quote_id = ?",
            (quote_id,),
        ).fetchone()
    if not row:
        return None
    try:
        items = json.loads(row[3]) if row[3] else []
    except (TypeError, ValueError):
        # Unparseable stored items must not read as "no flags".
        items = None
    return {
        "quote_id": row[0],
        "company_id": row[1],
        "tender_id": row[2],
        "line_items": items,
        "status": row[4],
        "finalized_at": row[5],
    }


def flagged_items(items) -> list:
    """Indexes and descriptions of everything still awaiting a human price."""
    if items is None:
        # Corrupt stored state fails closed: treat it as entirely unresolved.
        return [{"index": -1, "description": "stored line items unreadable"}]
    out = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            out.append({"index": i, "description": "malformed line item"})
        elif item.get("price_status") in FLAGGED_STATUSES:
            out.append({"index": i,
                        "description": str(item.get("description", "(no description)")),
                        "price_status": item.get("price_status")})
    return out


def resolve_quote_item(quote_id: str, company_id: str, item_index: int,
                       price: float, resolved_by: str = "user") -> dict:
    """
    The sanctioned way to clear a flag: write the human's price into stored
    state. Finalisation reads stored state, so this is the only thing that can
    move a flagged quote forward — supplying a cleaner list to
    finalize_quote_flow does nothing.
    """
    record = get_quote_record(quote_id)
    if not record:
        return {"status": "error", "message": "Quote not found."}
    if record["company_id"] != company_id:
        return {"status": "error", "message": "Quote not found."}
    if record["status"] == "FINAL":
        return {"status": "error", "message": "Quote is already final."}
    items = record["line_items"]
    if items is None or not (0 <= item_index < len(items)):
        return {"status": "error", "message": "No such line item."}
    try:
        price = float(price)
    except (TypeError, ValueError):
        return {"status": "error", "message": "Price must be a number."}
    if price < 0:
        return {"status": "error", "message": "Price cannot be negative."}

    item = items[item_index]
    qty = item.get("quantity") or 1
    item["price"] = price
    item["total"] = price * qty
    item["price_status"] = "RESOLVED_BY_USER"
    item["resolved_by"] = resolved_by
    item["resolved_at"] = datetime.now().isoformat()

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE quote_audit_log SET line_items = ?"
            " WHERE quote_id = ? AND company_id = ? AND status = 'DRAFT'",
            (json.dumps(items), quote_id, company_id),
        )
    return {"status": "ok", "resolved": item.get("description"),
            "remaining_flags": len(flagged_items(items))}


def finalize_quote(quote_id: str, confirmed_items: list, company_id: str = None) -> bool:
    """
    Marks a quote final. The WHERE clause carries the guard rather than
    trusting that the caller checked first: a quote already FINAL, or belonging
    to another company, matches no row and nothing is written.
    """
    items_json = json.dumps(confirmed_items)
    with sqlite3.connect(DB_PATH) as conn:
        if company_id is None:
            cur = conn.execute(
                "UPDATE quote_audit_log SET line_items = ?, finalized_at = ?,"
                " status = 'FINAL' WHERE quote_id = ? AND status = 'DRAFT'",
                (items_json, datetime.now(), quote_id),
            )
        else:
            cur = conn.execute(
                "UPDATE quote_audit_log SET line_items = ?, finalized_at = ?,"
                " status = 'FINAL'"
                " WHERE quote_id = ? AND company_id = ? AND status = 'DRAFT'",
                (items_json, datetime.now(), quote_id, company_id),
            )
        return cur.rowcount == 1
