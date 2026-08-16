"""
The company profile endpoint returns this company, not a fixed one.

/api/company-profile was hardcoded, and said so:

    # In a real app this would query the DB. We'll return mock data
    return {"name": "CairoAI", "registration": "2026/250499/07",
            "location": "Centurion, GP",
            "stats": {"pit_total_wins": 3, "pit_win_rate_overall": "21%",
                      "bbbee_level": "Lvl 1", "pit_is_incumbent": "2 buyers"}}

Every user saw the same company name, the same registration number and the
same invented track record regardless of who they were, and the whole
workspace sidebar reads this endpoint.
"""

import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient

from agent.memory import company_store
from app import app

client = TestClient(app)

APP_SOURCE = (Path(__file__).resolve().parent.parent / "app.py").read_text(encoding="utf-8")

#: The literals the endpoint used to return for everybody.
MOCK_VALUES = ("2026/250499/07", "Centurion, GP", "2 buyers", "21%")


def _headers_for(company_id: str) -> dict:
    from agent import auth
    user = auth.create_user(
        f"cp-{uuid.uuid4().hex[:10]}@example.test", company_id,
        "test-suite-not-a-real-password")
    return {"Authorization": f"Bearer {auth.issue_session(user)}"}


def test_the_hardcoded_values_are_gone_from_the_source():
    """
    A literal was the bug, so the guard is against literals — but parsed, not
    grepped. The docstring quotes the old values to explain them, and a string
    search cannot tell that apart from returning them.
    """
    import ast

    tree = ast.parse(APP_SOURCE)
    func = next(
        n for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name == "api_company_profile"
    )

    body = func.body[1:] if ast.get_docstring(func) else func.body
    strings = {
        node.value for stmt in body for node in ast.walk(stmt)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }

    for literal in MOCK_VALUES:
        assert literal not in strings, f"{literal!r} is still returned"
    assert "CairoAI" not in strings, "the placeholder company name is still returned"


def test_two_companies_do_not_get_the_same_profile():
    """The failure was that everybody got one company's details."""
    a, b = f"cp-a-{uuid.uuid4().hex[:8]}", f"cp-b-{uuid.uuid4().hex[:8]}"

    company_store.update_company_profile(
        a, {"company_name": "ALPHA ENGINEERING", "registration_number": "2019/111111/07",
            "province": "Gauteng", "bbbee_level": 1}, confirmed=True)
    company_store.update_company_profile(
        b, {"company_name": "BETA LOGISTICS", "registration_number": "2021/222222/07",
            "province": "Western Cape", "bbbee_level": 4}, confirmed=True)

    pa = client.get("/api/company-profile", headers=_headers_for(a)).json()
    pb = client.get("/api/company-profile", headers=_headers_for(b)).json()

    assert pa["name"] == "ALPHA ENGINEERING"
    assert pb["name"] == "BETA LOGISTICS"
    assert pa["registration"] != pb["registration"]
    assert pa["stats"]["bbbee_level"] == "Lvl 1"
    assert pb["stats"]["bbbee_level"] == "Lvl 4"


def test_an_empty_profile_reports_empty_rather_than_inventing_one():
    """
    A company with nothing stored used to see "CairoAI, Centurion, GP". It
    should see nothing, flagged, so the UI can prompt instead of rendering
    somebody else's details.
    """
    fresh = f"cp-empty-{uuid.uuid4().hex[:8]}"
    body = client.get("/api/company-profile", headers=_headers_for(fresh)).json()

    assert body["profile_empty"] is True
    assert body["name"] is None
    assert body["registration"] is None
    assert body["location"] is None
    for literal in MOCK_VALUES:
        assert literal not in str(body)


def test_the_track_record_starts_empty_and_follows_real_outcomes():
    """
    3 wins at 21% was invented. The count now follows what this company
    actually recorded through /api/track-outcome.
    """
    company_id = f"cp-rec-{uuid.uuid4().hex[:8]}"
    headers = _headers_for(company_id)

    before = client.get("/api/company-profile", headers=headers).json()["stats"]
    assert before["tracked_outcomes"] == 0
    assert before["pit_total_wins"] is None, "a win count with no bids behind it"
    assert before["pit_win_rate_overall"] is None, "a win rate over zero decided bids"

    for outcome in ("won", "won", "lost"):
        client.post("/api/track-outcome", headers=headers, json={
            "prediction_id": str(uuid.uuid4()),
            "tender_identifier": "CP-T",
            "actual_outcome": outcome,
        })

    after = client.get("/api/company-profile", headers=headers).json()["stats"]
    assert after["tracked_outcomes"] == 3
    assert after["decided_outcomes"] == 3
    assert after["pit_total_wins"] == 2
    assert after["pit_win_rate_overall"] == "67%"


def test_incumbency_is_not_reported_rather_than_guessed():
    """
    "2 buyers" needs the buying entity per bid. tracked_outcomes has no buyer
    column, so there is nothing to count and nothing is claimed.
    """
    body = client.get("/api/company-profile",
                      headers=_headers_for(f"cp-inc-{uuid.uuid4().hex[:8]}")).json()
    assert body["stats"]["pit_is_incumbent"] is None


def test_the_tier_is_read_not_inferred_from_a_feature_flag():
    """It was `"pro" if config.get("agent_enabled") else "starter"`, which
    cannot ever say enterprise."""
    body = client.get("/api/company-profile",
                      headers=_headers_for("enterprise_corp")).json()
    assert body["tier"] in ("starter", "pro", "enterprise")


def test_it_still_refuses_anonymous_callers():
    assert client.get("/api/company-profile").status_code == 401
