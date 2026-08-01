import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any

from agent.db_paths import AGENT_MEMORY_DB as DB_PATH

TIER_CONFIG = {
  "starter": {
    "model_access": ["sailor"],
    "agent_enabled": False,
    "claude_api_enabled": False,
    "quotes_per_day": 0,
    "onboarding_advice_enabled": False,
    "calendar_agent_enabled": False
  },
  "pro": {
    "model_access": ["sailor", "conquest"],
    "agent_enabled": True,
    "claude_api_enabled": True,
    "quotes_per_day": 5,
    "onboarding_advice_enabled": True,
    "onboarding_advice_refresh_limit_per_month": 1,
    "calendar_agent_enabled": True,
    "navigation_help_enabled": True,
    "company_memory_enabled": True
  },
  "enterprise": {
    "model_access": ["sailor", "conquest"],  # Monica excluded until explicitly promoted
    "agent_enabled": True,
    "claude_api_enabled": True,
    "quotes_per_day": 50,
    "onboarding_advice_enabled": True,
    "onboarding_advice_refresh_limit_per_month": None,
    "calendar_agent_enabled": True,
    "navigation_help_enabled": True,
    "company_memory_enabled": True
  }
}

# Mock database mapping company_id header to a specific tier
MOCK_CLIENT_REGISTRY = {
    "starter_corp": "starter",
    "pro_corp": "pro",
    "enterprise_corp": "enterprise"
}

def init_subscription_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS usage_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id TEXT,
                action_type TEXT,
                timestamp TEXT
            )
        """)
        conn.commit()

init_subscription_db()

def get_company_tier(company_id: str) -> str:
    return MOCK_CLIENT_REGISTRY.get(company_id, "starter")

def get_config(company_id: str) -> Dict[str, Any]:
    tier = get_company_tier(company_id)
    return TIER_CONFIG[tier]

def get_utc_today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")
    
def get_utc_month_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")

def get_quotes_used_today(company_id: str) -> int:
    today = get_utc_today_str()
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM usage_logs WHERE company_id = ? AND action_type = 'generate_quote' AND timestamp LIKE ?",
            (company_id, f"{today}%")
        )
        return cur.fetchone()[0]

def log_quote_generation(company_id: str):
    timestamp = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO usage_logs (company_id, action_type, timestamp) VALUES (?, ?, ?)",
            (company_id, "generate_quote", timestamp)
        )

def check_quote_quota(company_id: str) -> dict:
    config = get_config(company_id)
    if not config["claude_api_enabled"] or not config["agent_enabled"]:
        return {"allowed": False, "reason": "Agent features are not enabled on your current plan."}
        
    limit = config["quotes_per_day"]
    used = get_quotes_used_today(company_id)
    
    if used >= limit:
        tier_name = get_company_tier(company_id).capitalize()
        next_tier = "Enterprise" if tier_name == "Pro" else "Pro"
        reset_time = "midnight UTC"
        return {
            "allowed": False, 
            "reason": f"You've reached your daily quote generation limit ({limit}/day on {tier_name}). Resets at {reset_time}. Upgrade to {next_tier} for more."
        }
        
    return {"allowed": True, "used": used, "limit": limit}

def get_subscription_status(company_id: str) -> dict:
    tier = get_company_tier(company_id)
    config = TIER_CONFIG[tier]
    
    used_quotes = get_quotes_used_today(company_id) if config["quotes_per_day"] > 0 else 0
    
    return {
        "company_id": company_id,
        "tier": tier,
        "features": config,
        "usage": {
            "quotes_used_today": used_quotes,
            "quotes_remaining_today": max(0, config["quotes_per_day"] - used_quotes) if config["quotes_per_day"] > 0 else 0
        }
    }
