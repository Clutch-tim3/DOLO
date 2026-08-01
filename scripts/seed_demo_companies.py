"""
Seed company profiles for the tier IDs the app actually uses.

Why this exists
---------------
Two separate ID namespaces had drifted apart:

  * agent/subscription.py  MOCK_CLIENT_REGISTRY -> starter_corp / pro_corp / enterprise_corp
  * agent_memory.db        company_profile      -> c123 / test_co_123

The frontend sends X-Company-ID: pro_corp, so every company-aware tool looked
up a company_id that had no row. The agent then correctly answered "nothing on
file" (its hard constraint working as designed) and the quote flow bailed at
`if not profile`. This seeds the tier IDs so the two namespaces line up.

Idempotent — safe to re-run. Existing rows are updated, not duplicated.

Usage:
    python scripts/seed_demo_companies.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.memory.company_store import get_company_profile, update_company_profile

# Real CairoAI details, matching what /api/company-profile already reports.
CAIROAI = {
    "company_name": "CairoAI",
    "registration_number": "2026/250499/07",
    "bbbee_level": "Level 1 Contributor",
    "province": "Gauteng",
    "registered_municipality": "City of Tshwane",
    "industry": "ICT & Professional Services",
}

COMPANIES = {
    "pro_corp": CAIROAI,
    "enterprise_corp": {**CAIROAI, "company_name": "CairoAI (Enterprise)"},
    "starter_corp": {**CAIROAI, "company_name": "CairoAI (Starter)"},
}


def main() -> None:
    for company_id, fields in COMPANIES.items():
        existed = bool(get_company_profile(company_id))
        update_company_profile(company_id, fields)
        profile = get_company_profile(company_id)
        verb = "updated" if existed else "created"
        print(f"  {verb:8} {company_id:16} -> {profile.get('company_name')}")

    print("\nVerification:")
    for company_id in COMPANIES:
        p = get_company_profile(company_id)
        ok = bool(p) and p.get("company_name")
        print(f"  {company_id:16} {'OK' if ok else 'MISSING'}  bbbee={p.get('bbbee_level')}")


if __name__ == "__main__":
    main()
