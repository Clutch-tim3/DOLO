import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "procurement.db"

# Global limit: max 30 requests per minute
GLOBAL_MAX_REQUESTS_PER_MINUTE = 30

def _get_db():
    conn = sqlite3.connect(str(DB_PATH))
    return conn

def init_global_limiter():
    conn = _get_db()
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS global_rate_limit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL
        )
    ''')
    conn.commit()
    conn.close()

def check_global_rate_limit() -> bool:
    """
    Checks if the global API limit (Layer 3) has been exceeded.
    Returns True if allowed, False if rate limited.
    """
    now = time.time()
    one_minute_ago = now - 60.0
    
    conn = _get_db()
    c = conn.cursor()
    
    # Clean up old entries
    c.execute("DELETE FROM global_rate_limit WHERE timestamp < ?", (one_minute_ago,))
    conn.commit()
    
    # Check current count
    c.execute("SELECT COUNT(*) FROM global_rate_limit")
    count = c.fetchone()[0]
    
    if count >= GLOBAL_MAX_REQUESTS_PER_MINUTE:
        conn.close()
        return False
        
    # Log this request
    c.execute("INSERT INTO global_rate_limit (timestamp) VALUES (?)", (now,))
    conn.commit()
    conn.close()
    
    return True

init_global_limiter()
