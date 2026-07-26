import sqlite3
import json
import uuid
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent.parent.parent / "agent_memory.db"

def init_db():
    schema_path = Path(__file__).parent / "schema.sql"
    with sqlite3.connect(DB_PATH) as conn:
        with open(schema_path, "r") as f:
            conn.executescript(f.read())

def get_company_profile(company_id: str) -> dict:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM company_profile WHERE company_id = ?", (company_id,))
        row = cur.fetchone()
        return dict(row) if row else {}

def update_company_profile(company_id: str, fields: dict) -> dict:
    if not fields:
        return {"status": "error", "message": "No fields provided"}
        
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("SELECT company_id FROM company_profile WHERE company_id = ?", (company_id,))
        if not cur.fetchone():
            cur.execute(
                "INSERT INTO company_profile (company_id, created_at, updated_at) VALUES (?, ?, ?)",
                (company_id, datetime.now(), datetime.now())
            )
            
        updates = []
        values = []
        allowed_keys = ['company_name', 'registration_number', 'csd_number', 'bbbee_level', 'province', 'registered_municipality', 'industry', 'logo_file_path']
        for k, v in fields.items():
            if k in allowed_keys:
                updates.append(f"{k} = ?")
                values.append(v)
                
        if updates:
            updates.append("updated_at = ?")
            values.append(datetime.now())
            values.append(company_id)
            
            query = f"UPDATE company_profile SET {', '.join(updates)} WHERE company_id = ?"
            cur.execute(query, values)
            
        conn.commit()
    return {"status": "success", "updated_fields": list(fields.keys())}

def get_company_documents(company_id: str) -> list:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM company_documents WHERE company_id = ?", (company_id,))
        rows = cur.fetchall()
        
        docs = []
        for r in rows:
            d = dict(r)
            if d.get("parsed_fields"):
                d["parsed_fields"] = json.loads(d["parsed_fields"])
            docs.append(d)
        return docs

def add_company_document(company_id: str, document_type: str, file_path: str, parsed_fields: dict = None, expiry_date: str = None) -> str:
    doc_id = str(uuid.uuid4())
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        parsed_json = json.dumps(parsed_fields) if parsed_fields else None
        cur.execute(
            """INSERT INTO company_documents (id, company_id, document_type, file_path, expiry_date, parsed_fields, uploaded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (doc_id, company_id, document_type, file_path, expiry_date, parsed_json, datetime.now())
        )
        conn.commit()
    return doc_id

def log_conversation(company_id: str, user_message: str, agent_response: str, tool_calls_made: list = None):
    log_id = str(uuid.uuid4())
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        tools_json = json.dumps(tool_calls_made) if tool_calls_made else None
        cur.execute(
            """INSERT INTO conversation_log (id, company_id, user_message, agent_response, tool_calls_made, timestamp)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (log_id, company_id, user_message, agent_response, tools_json, datetime.now())
        )
        conn.commit()
        
def search_conversation_history(company_id: str, query: str = None, limit: int = 5) -> list:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        if query:
            cur.execute("""
                SELECT * FROM conversation_log 
                WHERE company_id = ? AND (user_message LIKE ? OR agent_response LIKE ?) 
                ORDER BY timestamp DESC LIMIT ?
            """, (company_id, f"%{query}%", f"%{query}%", limit))
        else:
            cur.execute("SELECT * FROM conversation_log WHERE company_id = ? ORDER BY timestamp DESC LIMIT ?", (company_id, limit))
            
        rows = cur.fetchall()
        logs = []
        for r in rows:
            d = dict(r)
            if d.get("tool_calls_made"):
                d["tool_calls_made"] = json.loads(d["tool_calls_made"])
            logs.append(d)
        return logs

# Initialize schema
init_db()
