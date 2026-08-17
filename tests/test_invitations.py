"""
An invitation creates an account without letting anyone claim a company.

There was no signup route, by design: without an email channel, "sign up" means
anyone can assert any company_id, and every account had to be created by an
operator running manage_users.py. Workable for ten pilot customers, impossible
for a public launch.

An invite closes that without needing email. The company is fixed when the
invite is minted and lives in the stored record; the recipient chooses only
their username and password. The property that makes this safe is an ABSENCE —
there is no company_id parameter on redemption — so it is asserted directly.
"""

import inspect
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient

from agent import auth
from agent.memory import company_registry
from app import app

client = TestClient(app)

PASSWORD = "invite-test-not-a-real-password"


@pytest.fixture
def company():
    cid = f"inv-{uuid.uuid4().hex[:10]}"
    company_registry.create_company(cid, display_name="Invite Co", tier="pro")
    yield cid
    company_registry.delete_company(cid)


def _new_username() -> str:
    return f"inv-{uuid.uuid4().hex[:10]}@example.test"


# --- the absence that makes it safe ------------------------------------------

def test_redemption_takes_no_company_id():
    """
    If a caller could supply one, this would be a signup route that lets anyone
    become any tenant. The absence IS the defence, so it is pinned here rather
    than left to be noticed in review.
    """
    params = inspect.signature(auth.redeem_invite).parameters
    assert "company_id" not in params
    assert set(params) == {"raw_token", "username", "password"}


def test_the_http_route_does_not_read_a_company_from_the_body():
    """
    Parsed, not grepped. The route legitimately RETURNS company_id in its
    response and names it in the docstring; what must never happen is READING
    one from the request. A string search cannot tell those apart.
    """
    import ast

    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "agent", "auth_routes.py")
    tree = ast.parse(open(path, encoding="utf-8").read())
    func = next(n for n in ast.walk(tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and n.name == "redeem_invite")

    # No company_id parameter on the route.
    assert "company_id" not in {a.arg for a in func.args.args}

    # And nothing pulls one out of the payload: neither payload["company_id"]
    # nor payload.get("company_id").
    for node in ast.walk(func):
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
            assert node.slice.value != "company_id", "company_id is read from the request"
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get" and node.args
                and isinstance(node.args[0], ast.Constant)):
            assert node.args[0].value != "company_id", "company_id is read from the request"


# --- the happy path -----------------------------------------------------------

def test_an_invite_creates_an_account_in_the_right_company(company):
    token = auth.create_invite(company, created_by="operator")
    username = _new_username()

    user = auth.redeem_invite(token, username, PASSWORD)

    assert user.company_id == company, "the account landed in the wrong company"
    assert user.username == username
    assert auth.authenticate(username, PASSWORD).company_id == company


def test_the_invite_says_which_company_before_it_is_spent(company):
    """The page has to name the company before someone commits to a password."""
    token = auth.create_invite(company)
    detail = auth.peek_invite(token)
    assert detail["company_id"] == company

    # Peeking must not consume it.
    assert auth.redeem_invite(token, _new_username(), PASSWORD).company_id == company


# --- single use ---------------------------------------------------------------

def test_an_invite_works_exactly_once(company):
    token = auth.create_invite(company)
    auth.redeem_invite(token, _new_username(), PASSWORD)

    with pytest.raises(auth.AuthError, match="not valid"):
        auth.redeem_invite(token, _new_username(), PASSWORD)

    assert auth.peek_invite(token) is None


def test_a_revoked_invite_cannot_be_redeemed(company):
    token = auth.create_invite(company)
    selector = auth.list_invites(company)[0]["selector"]
    assert auth.revoke_invite(selector) is True

    with pytest.raises(auth.AuthError, match="not valid"):
        auth.redeem_invite(token, _new_username(), PASSWORD)
    assert auth.revoke_invite(selector) is False


def test_an_expired_invite_is_refused(company):
    token = auth.create_invite(company, ttl_seconds=-1)
    assert auth.peek_invite(token) is None
    with pytest.raises(auth.AuthError, match="not valid"):
        auth.redeem_invite(token, _new_username(), PASSWORD)


# --- forgery ------------------------------------------------------------------

def test_a_made_up_token_is_refused(company):
    for bogus in ("cainv_aaaa.bbbb", "not-a-token", "", "cainv_", "cainv_x.y"):
        assert auth.peek_invite(bogus) is None
        with pytest.raises(auth.AuthError):
            auth.redeem_invite(bogus, _new_username(), PASSWORD)


def test_the_right_selector_with_a_wrong_verifier_is_refused(company):
    """
    The selector indexes the row; the verifier is what proves possession. If
    only the selector were checked, knowing it would be enough.
    """
    auth.create_invite(company)
    selector = auth.list_invites(company)[0]["selector"]

    forged = f"cainv_{selector}.{'z' * 43}"
    assert auth.peek_invite(forged) is None
    with pytest.raises(auth.AuthError, match="not valid"):
        auth.redeem_invite(forged, _new_username(), PASSWORD)


def test_the_raw_token_is_not_recoverable_from_the_database(company):
    """Only the selector and a digest are stored, as with sessions."""
    token = auth.create_invite(company)
    stored = repr(auth.list_invites(company))
    verifier = token.split(".", 1)[1]
    assert verifier not in stored
    assert token not in stored


# --- an invite locked to one address ------------------------------------------

def test_an_invite_can_be_locked_to_one_email(company):
    intended = _new_username()
    token = auth.create_invite(company, username=intended)

    with pytest.raises(auth.AuthError, match="different email"):
        auth.redeem_invite(token, _new_username(), PASSWORD)

    # And it is still unspent for the person it was meant for.
    assert auth.redeem_invite(token, intended, PASSWORD).username == intended


# --- guards -------------------------------------------------------------------

def test_an_invite_for_a_company_that_does_not_exist_is_refused():
    """
    Otherwise the account is created and silently resolves to starter, which
    looks like a broken product rather than a missing step.
    """
    with pytest.raises(auth.AuthError, match="No company"):
        auth.create_invite(f"never-created-{uuid.uuid4().hex[:8]}")


def test_a_short_password_is_refused(company):
    token = auth.create_invite(company)
    with pytest.raises(auth.AuthError, match="12 characters"):
        auth.redeem_invite(token, _new_username(), "short")
    # And the invite survives, so a typo does not burn it.
    assert auth.peek_invite(token) is not None


def test_an_existing_username_cannot_be_taken_over(company):
    existing = _new_username()
    auth.create_user(existing, "pro_corp", PASSWORD)

    token = auth.create_invite(company)
    with pytest.raises(auth.AuthError, match="already exists"):
        auth.redeem_invite(token, existing, PASSWORD)

    assert auth.authenticate(existing, PASSWORD).company_id == "pro_corp"


# --- over HTTP ----------------------------------------------------------------

def test_the_http_flow_signs_the_new_user_in(company):
    token = auth.create_invite(company)
    username = _new_username()

    seen = client.get(f"/api/auth/invite/{token}")
    assert seen.status_code == 200
    assert seen.json()["company_id"] == company

    created = client.post(f"/api/auth/invite/{token}/redeem",
                          json={"username": username, "password": PASSWORD})
    assert created.status_code == 200, created.text
    assert created.json()["company_id"] == company
    assert auth.SESSION_COOKIE in created.headers.get("set-cookie", "")
    client.cookies.clear()


def test_an_unusable_token_is_a_404_with_one_message():
    """Distinguishing unknown from expired from spent is an oracle."""
    r = client.get("/api/auth/invite/cainv_nope.nope")
    assert r.status_code == 404
    assert "not valid" in r.json()["detail"]
