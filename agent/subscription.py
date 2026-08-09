import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any

from agent import db
from agent.db_paths import AGENT_MEMORY_DB as DB_PATH

TIER_CONFIG = {
  "starter": {
    "model_access": ["sailor"],
    "agent_enabled": False,
    "claude_api_enabled": False,
    "quotes_per_day": 0,
    "onboarding_advice_enabled": False,
    "calendar_agent_enabled": False,
    # Agent Autofill is off entirely on starter. The gate below refuses before
    # any classification call is made, so a starter company never costs an
    # Anthropic request.
    "agent_autofill_enabled": False,
    "connected_folders_limit": 0,
    "autofills_per_day": 0
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
    "company_memory_enabled": True,
    "agent_autofill_enabled": True,
    "connected_folders_limit": 1,
    "autofills_per_day": 3
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
    "company_memory_enabled": True,
    "agent_autofill_enabled": True,
    "connected_folders_limit": 5,
    "autofills_per_day": 25
  }
}

#: The action_type written to usage_logs for one completed autofill draft.
AUTOFILL_ACTION = "agent_autofill"

# Mock database mapping company_id header to a specific tier
MOCK_CLIENT_REGISTRY = {
    "starter_corp": "starter",
    "pro_corp": "pro",
    "enterprise_corp": "enterprise"
}

def init_subscription_db():
    with db.connect(DB_PATH) as conn:
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
    with db.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM usage_logs WHERE company_id = ? AND action_type = 'generate_quote' AND timestamp LIKE ?",
            (company_id, f"{today}%")
        )
        return cur.fetchone()[0]

def log_quote_generation(company_id: str):
    timestamp = datetime.now(timezone.utc).isoformat()
    with db.connect(DB_PATH) as conn:
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

# ---------------------------------------------------------------------------
# Agent Autofill quota
#
# Same shape as the quote quota above, and the same hard-won rule: the check
# runs BEFORE the work, the log runs AFTER it succeeds. Logging on entry meant
# a failed quote still burned a day's allowance; do not "simplify" this back
# into one call.
#
# The check is deliberately cheap and does no I/O beyond one COUNT, because it
# sits in front of an Anthropic request. Rejecting here is the difference
# between refusing a request and paying for one we then refuse.
# ---------------------------------------------------------------------------

def get_autofills_used_today(company_id: str) -> int:
    today = get_utc_today_str()
    with db.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM usage_logs WHERE company_id = ? AND action_type = ? AND timestamp LIKE ?",
            (company_id, AUTOFILL_ACTION, f"{today}%")
        )
        return cur.fetchone()[0]


def log_autofill_run(company_id: str):
    """
    Record one CONSUMED autofill.

    Call this only after a draft has actually been produced. A run that was
    refused, skipped by the classifier, or failed must not appear here.
    """
    timestamp = datetime.now(timezone.utc).isoformat()
    with db.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO usage_logs (company_id, action_type, timestamp) VALUES (?, ?, ?)",
            (company_id, AUTOFILL_ACTION, timestamp)
        )


def check_autofill_quota(company_id: str) -> dict:
    """
    Server-side gate for Agent Autofill. Returns {"allowed": bool, ...}.

    MUST be called before the classification request, not after. The whole
    point of the tier limit is to avoid spending on a request that would be
    rejected anyway.
    """
    config = get_config(company_id)

    if not config.get("agent_autofill_enabled"):
        tier_name = get_company_tier(company_id).capitalize()
        return {
            "allowed": False,
            "reason": (f"Agent Autofill is not included in the {tier_name} plan. "
                       f"Upgrade to Pro to connect a folder and draft tender documents."),
            "code": "tier_disabled",
            "tier": get_company_tier(company_id),
            "used": 0,
            "limit": 0,
        }

    limit = config.get("autofills_per_day", 0)
    used = get_autofills_used_today(company_id)

    if used >= limit:
        tier_name = get_company_tier(company_id).capitalize()
        next_tier = "Enterprise" if tier_name == "Pro" else "Pro"
        return {
            "allowed": False,
            "reason": (f"You've reached your daily Agent Autofill limit ({limit}/day on "
                       f"{tier_name}). Resets at midnight UTC. Upgrade to {next_tier} for more."),
            "code": "daily_limit_reached",
            "tier": get_company_tier(company_id),
            "used": used,
            "limit": limit,
        }

    return {
        "allowed": True,
        "tier": get_company_tier(company_id),
        "used": used,
        "limit": limit,
        "remaining": limit - used,
    }


def check_connected_folder_limit(company_id: str, current_folder_count: int) -> dict:
    """
    Gate for connecting one more source folder (Drive / Dropbox).

    `current_folder_count` is supplied by the caller that owns the folder
    table, so this module keeps no opinion about where folders are stored.
    """
    config = get_config(company_id)

    if not config.get("agent_autofill_enabled"):
        tier_name = get_company_tier(company_id).capitalize()
        return {
            "allowed": False,
            "reason": f"Agent Autofill is not included in the {tier_name} plan.",
            "code": "tier_disabled",
            "limit": 0,
        }

    limit = config.get("connected_folders_limit", 0)
    if current_folder_count >= limit:
        tier_name = get_company_tier(company_id).capitalize()
        return {
            "allowed": False,
            "reason": (f"{tier_name} allows {limit} connected folder"
                       f"{'' if limit == 1 else 's'}. Disconnect one first, or upgrade."),
            "code": "folder_limit_reached",
            "limit": limit,
        }

    return {"allowed": True, "limit": limit, "used": current_folder_count}


def get_subscription_status(company_id: str) -> dict:
    tier = get_company_tier(company_id)
    config = TIER_CONFIG[tier]

    used_quotes = get_quotes_used_today(company_id) if config["quotes_per_day"] > 0 else 0
    autofill_limit = config.get("autofills_per_day", 0)
    used_autofills = get_autofills_used_today(company_id) if autofill_limit > 0 else 0

    return {
        "company_id": company_id,
        "tier": tier,
        "features": config,
        "usage": {
            "quotes_used_today": used_quotes,
            "quotes_remaining_today": max(0, config["quotes_per_day"] - used_quotes) if config["quotes_per_day"] > 0 else 0,
            "autofills_used_today": used_autofills,
            "autofills_remaining_today": max(0, autofill_limit - used_autofills),
        }
    }
