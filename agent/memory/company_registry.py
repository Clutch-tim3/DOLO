"""
Customer records: which companies exist, what tier they are on, and who set
them up.

WHY THIS EXISTS
---------------
Tiers came from `MOCK_CLIENT_REGISTRY` in `agent/subscription.py` — a Python
dict holding `starter_corp`, `pro_corp` and `enterprise_corp`. Anyone else
silently resolved to the starter tier. There was no customer record, no company
creation, and no way to put a customer on a plan without editing source and
deploying.

The name said what it was. A registry called MOCK is not a customer list.

WHAT A COMPANY IS NOW
---------------------
A row: an id, a display name, a tier, a status, when it was created and by
whom. Creating a customer is an operation, not a code change. Tier lookup goes
through `tier_for`, which every quota and feature gate already funnels into via
`subscription.get_company_tier`.

FAILING TOWARD STARTER
----------------------
An unknown company resolves to `starter`, and so does a suspended one. That is
the same direction the dict failed in and it is the safe one: starter has the
agent off, Agent Autofill off and no quota, so an unrecognised company costs
nothing and reaches nothing. Refusing outright would turn a missing row into an
outage; silently granting `pro` would turn one into a bill.

`status` is exposed separately so a caller that wants to distinguish "suspended"
from "on the free tier" can, rather than inferring it from the tier.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from agent import db
from agent.db_paths import PROCUREMENT_DB as DB_PATH

#: The tier an unknown or suspended company gets. See the module docstring.
DEFAULT_TIER = "starter"

STATUS_ACTIVE = "active"
STATUS_SUSPENDED = "suspended"

#: Seeded on first use so the three companies that existed as dict entries keep
#: working. Without this every existing tenant would silently drop to starter on
#: the deploy that introduced the table — enterprise_corp included, which is the
#: only real one in production.
_SEED = {
    "starter_corp": "starter",
    "pro_corp": "pro",
    "enterprise_corp": "enterprise",
}

_schema_ready: set = set()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ensure_schema(conn) -> None:
    """
    Create the table and seed the pre-existing companies, once per process.

    Lazy and PID-keyed for the same reason as the rate limiter and the archive:
    building the Cloud SQL connector at import puts its background refresh
    threads on the wrong side of the ASGI fork.
    """
    pid = os.getpid()
    if pid in _schema_ready:
        return

    conn.execute("""
        CREATE TABLE IF NOT EXISTS companies (
            company_id   TEXT PRIMARY KEY,
            display_name TEXT,
            tier         TEXT NOT NULL DEFAULT 'starter',
            status       TEXT NOT NULL DEFAULT 'active',
            created_at   TEXT NOT NULL,
            created_by   TEXT,
            notes        TEXT
        )
    """)

    # Idempotent: only inserts an id that is not already there, so it never
    # overwrites a tier someone has since changed on purpose.
    existing = {r["company_id"] for r in
                conn.execute("SELECT company_id FROM companies").fetchall()}
    for company_id, tier in _SEED.items():
        if company_id not in existing:
            conn.execute(
                "INSERT INTO companies (company_id, display_name, tier, status,"
                " created_at, created_by, notes) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (company_id, company_id, tier, STATUS_ACTIVE, _now(), "seed",
                 "Seeded from MOCK_CLIENT_REGISTRY when the companies table was introduced."),
            )

    conn.commit()
    _schema_ready.add(pid)


def _row_to_company(row) -> dict:
    return {
        "company_id": row["company_id"],
        "display_name": row["display_name"],
        "tier": row["tier"],
        "status": row["status"],
        "created_at": row["created_at"],
        "created_by": row["created_by"],
        "notes": row["notes"],
    }


def get_company(company_id: str) -> dict | None:
    """The customer record, or None if there is no such company."""
    if not company_id:
        return None
    with db.connect(DB_PATH) as conn:
        _ensure_schema(conn)
        row = conn.execute(
            "SELECT * FROM companies WHERE company_id = ?", (company_id,)
        ).fetchone()
        return _row_to_company(row) if row else None


def tier_for(company_id: str) -> str:
    """
    The tier this company is on.

    Unknown company, or suspended company, resolves to starter. See the module
    docstring for why that is the safe direction rather than an error.
    """
    company = get_company(company_id)
    if company is None:
        return DEFAULT_TIER
    if company["status"] != STATUS_ACTIVE:
        return DEFAULT_TIER
    return company["tier"] or DEFAULT_TIER


def list_companies() -> list:
    with db.connect(DB_PATH) as conn:
        _ensure_schema(conn)
        rows = conn.execute(
            "SELECT * FROM companies ORDER BY created_at, company_id").fetchall()
        return [_row_to_company(r) for r in rows]


def create_company(company_id: str, display_name: str = "", tier: str = DEFAULT_TIER,
                   created_by: str = "", notes: str = "") -> dict:
    """
    Bring a customer into existence.

    Refuses an id that already exists rather than updating it: creating and
    changing a customer are different intentions, and silently overwriting a
    tier is how someone ends up on a plan nobody chose. Use `set_tier`.
    """
    company_id = (company_id or "").strip()
    if not company_id:
        raise ValueError("company_id is required")

    valid = valid_tiers()
    if tier not in valid:
        raise ValueError(f"unknown tier {tier!r}; expected one of {sorted(valid)}")

    with db.connect(DB_PATH) as conn:
        _ensure_schema(conn)
        exists = conn.execute(
            "SELECT 1 FROM companies WHERE company_id = ?", (company_id,)).fetchone()
        if exists:
            raise ValueError(f"company {company_id!r} already exists")

        conn.execute(
            "INSERT INTO companies (company_id, display_name, tier, status,"
            " created_at, created_by, notes) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (company_id, display_name or company_id, tier, STATUS_ACTIVE,
             _now(), created_by or "", notes or ""),
        )
        conn.commit()

    return get_company(company_id)


def set_tier(company_id: str, tier: str) -> dict:
    """Move a customer to another plan."""
    valid = valid_tiers()
    if tier not in valid:
        raise ValueError(f"unknown tier {tier!r}; expected one of {sorted(valid)}")

    with db.connect(DB_PATH) as conn:
        _ensure_schema(conn)
        conn.execute("UPDATE companies SET tier = ? WHERE company_id = ?",
                     (tier, company_id))
        conn.commit()

    company = get_company(company_id)
    if company is None:
        raise ValueError(f"no such company {company_id!r}")
    return company


def set_status(company_id: str, status: str) -> dict:
    if status not in (STATUS_ACTIVE, STATUS_SUSPENDED):
        raise ValueError(f"unknown status {status!r}")

    with db.connect(DB_PATH) as conn:
        _ensure_schema(conn)
        conn.execute("UPDATE companies SET status = ? WHERE company_id = ?",
                     (status, company_id))
        conn.commit()

    company = get_company(company_id)
    if company is None:
        raise ValueError(f"no such company {company_id!r}")
    return company


def delete_company(company_id: str) -> bool:
    """
    Remove a customer record entirely. True if a row went.

    Prefer `set_status(company_id, STATUS_SUSPENDED)` for a real customer:
    suspending drops them to the starter tier while leaving the record, so
    their data stays attributable. This is for undoing a mistaken creation,
    and for tests, which need the row gone rather than lingering between runs.

    It removes the company record only. Anything owned by that company —
    profile, archive, packs, tracked outcomes — is untouched and becomes
    unreachable rather than deleted, the same way rows with no owner behave
    elsewhere.
    """
    with db.connect(DB_PATH) as conn:
        _ensure_schema(conn)
        existed = conn.execute(
            "SELECT 1 FROM companies WHERE company_id = ?", (company_id,)).fetchone()
        conn.execute("DELETE FROM companies WHERE company_id = ?", (company_id,))
        conn.commit()
    return bool(existed)


def valid_tiers() -> set:
    """
    The tiers that exist, read from TIER_CONFIG rather than duplicated.

    Imported at call time: subscription imports this module, so a module-level
    import here would close the loop.
    """
    from agent.subscription import TIER_CONFIG
    return set(TIER_CONFIG)
