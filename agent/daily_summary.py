import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
import json

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "procurement.db"

def generate_daily_summary():
    """Generates a daily summary report for Monitoring & Alerting."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    now = datetime.utcnow()
    one_day_ago = (now - timedelta(days=1)).isoformat()
    
    # 1. Total API Calls
    c.execute("SELECT COUNT(*) as total_calls FROM api_cost_tracking WHERE timestamp >= ?", (one_day_ago,))
    total_calls = c.fetchone()["total_calls"]
    
    # 2. Total Cost
    c.execute("SELECT SUM(cost_usd) as total_cost FROM api_cost_tracking WHERE timestamp >= ?", (one_day_ago,))
    total_cost_res = c.fetchone()
    total_cost = total_cost_res["total_cost"] if total_cost_res and total_cost_res["total_cost"] else 0.0
    
    # 3. Rate-limit events
    c.execute("SELECT COUNT(*) as rate_limits FROM api_cost_tracking WHERE timestamp >= ? AND is_rate_limited = 1", (one_day_ago,))
    rate_limits = c.fetchone()["rate_limits"]
    
    # 4. Total Errors (Non-Rate Limit)
    c.execute("SELECT COUNT(*) as errors FROM api_cost_tracking WHERE timestamp >= ? AND status LIKE 'error:%' AND is_rate_limited = 0", (one_day_ago,))
    errors = c.fetchone()["errors"]
    
    # 5. Global rate limits (Layer 3) triggered?
    c.execute("SELECT COUNT(*) as global_triggers FROM global_rate_limit WHERE timestamp >= ?", ((now - timedelta(days=1)).timestamp(),))
    try:
        global_triggers = c.fetchone()["global_triggers"]
    except Exception:
        global_triggers = 0
        
    conn.close()
    
    report = {
        "report_time_utc": now.isoformat(),
        "period": "Last 24 Hours",
        "total_api_calls": total_calls,
        "total_cost_usd": round(total_cost, 4),
        "anthropic_rate_limit_events": rate_limits,
        "global_circuit_breaker_triggers": global_triggers,
        "api_errors": errors
    }
    
    print("--- DAILY SUMMARY REPORT ---")
    print(json.dumps(report, indent=4))
    
    if total_cost > 50.0:
        print("[ALERT] Daily cost exceeded $50.00!")
    if rate_limits > 10:
        print("[ALERT] High number of Anthropic rate limits detected!")

if __name__ == "__main__":
    generate_daily_summary()
