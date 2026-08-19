"""
Customers are records, not a dict in the source.

Tiers came from `MOCK_CLIENT_REGISTRY` in agent/subscription.py — a Python dict
holding `starter_corp`, `pro_corp` and `enterprise_corp`. Anyone else silently
resolved to starter. There was no customer record, no company creation, and no
way to put a customer on a plan without editing source and deploying.

The name was accurate. A registry called MOCK is not a customer list.
"""

import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from agent import subscription
from agent.memory import company_registry


@pytest.fixture
def company_id():
    cid = f"reg-{uuid.uuid4().hex[:10]}"
    yield cid
    company_registry.delete_company(cid)


# --- the dict is gone ---------------------------------------------------------

def test_the_mock_registry_no_longer_decides_anything():
    assert not hasattr(subscription, "MOCK_CLIENT_REGISTRY"), (
        "the hardcoded customer dict is still present"
    )


def test_the_three_original_companies_kept_their_tiers():
    """
    They were dict entries. If the move had dropped them, every existing tenant
    would have silently fallen to starter on the deploy that introduced the
    table — enterprise_corp included, which is the only real one in production.
    """
    assert subscription.get_company_tier("starter_corp") == "starter"
    assert subscription.get_company_tier("pro_corp") == "pro"
    assert subscription.get_company_tier("enterprise_corp") == "enterprise"


# --- a customer can be created without a deploy -------------------------------

def test_a_company_can_be_created_and_gets_its_tier(company_id):
    company_registry.create_company(company_id, display_name="Acme Trading",
                                    tier="pro", created_by="operator@example.test")

    record = company_registry.get_company(company_id)
    assert record["display_name"] == "Acme Trading"
    assert record["tier"] == "pro"
    assert record["status"] == "active"
    assert record["created_by"] == "operator@example.test"
    assert record["created_at"]

    # And the whole feature-gating system sees it, because every quota and
    # feature check funnels through get_company_tier.
    assert subscription.get_company_tier(company_id) == "pro"
    assert subscription.get_config(company_id)["agent_enabled"] is True


def test_a_company_can_be_moved_between_plans(company_id):
    company_registry.create_company(company_id, tier="starter")
    assert subscription.get_config(company_id)["agent_enabled"] is False

    company_registry.set_tier(company_id, "enterprise")
    assert subscription.get_company_tier(company_id) == "enterprise"
    assert subscription.get_config(company_id)["autofills_per_day"] == 25


def test_creating_the_same_company_twice_is_refused(company_id):
    """
    Creating and changing a customer are different intentions. Silently
    overwriting is how someone ends up on a plan nobody chose.
    """
    company_registry.create_company(company_id, tier="pro")
    with pytest.raises(ValueError, match="already exists"):
        company_registry.create_company(company_id, tier="enterprise")

    assert company_registry.get_company(company_id)["tier"] == "pro"


def test_an_unknown_tier_is_refused(company_id):
    """A typo must not create a customer on a plan that does not exist."""
    with pytest.raises(ValueError, match="unknown tier"):
        company_registry.create_company(company_id, tier="platinum")
    assert company_registry.get_company(company_id) is None


def test_the_valid_tiers_come_from_TIER_CONFIG():
    """Duplicating the list is how the two drift apart."""
    assert company_registry.valid_tiers() == set(subscription.TIER_CONFIG)


# --- failing toward starter ---------------------------------------------------

def test_an_unknown_company_resolves_to_starter():
    """
    Same direction the dict failed in, and the safe one: starter has the agent
    off and no quota, so an unrecognised company costs nothing and reaches
    nothing.
    """
    unknown = f"never-created-{uuid.uuid4().hex[:8]}"
    assert subscription.get_company_tier(unknown) == "starter"
    assert subscription.get_config(unknown)["agent_enabled"] is False
    assert subscription.get_config(unknown)["agent_autofill_enabled"] is False


def test_a_suspended_company_drops_to_starter(company_id):
    company_registry.create_company(company_id, tier="enterprise")
    assert subscription.get_company_tier(company_id) == "enterprise"

    company_registry.set_status(company_id, company_registry.STATUS_SUSPENDED)

    assert subscription.get_company_tier(company_id) == "starter"
    assert subscription.get_config(company_id)["agent_enabled"] is False
    # The record survives, so the suspension is distinguishable from a company
    # that simply chose the free tier.
    assert company_registry.get_company(company_id)["status"] == "suspended"
    assert company_registry.get_company(company_id)["tier"] == "enterprise"


def test_reactivating_restores_the_plan(company_id):
    company_registry.create_company(company_id, tier="pro")
    company_registry.set_status(company_id, company_registry.STATUS_SUSPENDED)
    company_registry.set_status(company_id, company_registry.STATUS_ACTIVE)
    assert subscription.get_company_tier(company_id) == "pro"


# --- listing and removal ------------------------------------------------------

def test_companies_can_be_listed(company_id):
    company_registry.create_company(company_id, tier="pro")
    ids = [c["company_id"] for c in company_registry.list_companies()]
    assert company_id in ids
    assert "enterprise_corp" in ids


def test_deleting_a_company_removes_only_the_record(company_id):
    company_registry.create_company(company_id, tier="pro")
    assert company_registry.delete_company(company_id) is True
    assert company_registry.get_company(company_id) is None
    assert company_registry.delete_company(company_id) is False
    # And it falls back to starter rather than erroring.
    assert subscription.get_company_tier(company_id) == "starter"


# --- the pre-fork trap --------------------------------------------------------

def test_schema_setup_does_not_run_at_import():
    """
    subscription.py called init_subscription_db() at module scope. With the
    Cloud SQL connector that builds background refresh threads before the ASGI
    bridge forks, which is what made every request 504 once already.
    """
    import ast
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "agent" / "subscription.py"
              ).read_text(encoding="utf-8")
    module_level = [
        node.value.func.id
        for node in ast.parse(source).body
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
    ]
    assert "init_subscription_db" not in module_level
