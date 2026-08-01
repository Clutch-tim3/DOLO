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

def finalize_quote(quote_id: str, confirmed_items: list) -> bool:
    items_json = json.dumps(confirmed_items)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE quote_audit_log SET line_items = ?, finalized_at = ?, status = 'FINAL' WHERE quote_id = ?",
            (items_json, datetime.now(), quote_id)
        )
    return True
