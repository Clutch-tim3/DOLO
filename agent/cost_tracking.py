import sqlite3
import hashlib
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "procurement.db"

def _get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn

def init_cost_tracking_table():
    conn = _get_db()
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS api_cost_tracking (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hashed_company_id TEXT,
            endpoint TEXT,
            tokens_in INTEGER,
            tokens_out INTEGER,
            cost_usd REAL,
            latency_ms REAL,
            status TEXT,
            is_rate_limited BOOLEAN,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

def hash_company_id(company_id: str) -> str:
    """Anonymizes the company ID for logging."""
    return hashlib.sha256(company_id.encode('utf-8')).hexdigest()[:12]

def log_api_call(
    company_id: str,
    endpoint: str,
    tokens_in: int,
    tokens_out: int,
    cost_usd: float,
    latency_ms: float,
    status: str,
    is_rate_limited: bool = False
):
    """
    Logs every API call for auditing. 
    Logs are strictly metadata and anonymized. Full prompts/responses are NOT logged here.
    """
    conn = _get_db()
    c = conn.cursor()
    c.execute(
        '''
        INSERT INTO api_cost_tracking 
        (hashed_company_id, endpoint, tokens_in, tokens_out, cost_usd, latency_ms, status, is_rate_limited, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            hash_company_id(company_id),
            endpoint,
            tokens_in,
            tokens_out,
            cost_usd,
            latency_ms,
            status,
            is_rate_limited,
            datetime.utcnow().isoformat()
        )
    )
    conn.commit()
    conn.close()

# Initialize on import
init_cost_tracking_table()
