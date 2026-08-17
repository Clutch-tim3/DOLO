"""
A password can be reset without an email channel, and without an operator
knowing the new one.

There was no reset at all: `manage_users.py set-password` had the operator
choose the credential and then transmit it somehow. That is worse twice over —
the operator knows it, and it travels.

A reset link is the invitation pattern pointed at an existing account. The
operator sends a one-shot link and never learns what the person picks.

THE SAFETY PROPERTY IS AGAIN AN ABSENCE

`redeem_password_reset(raw_token, password)` takes no username. The account
comes from the stored record. A reset form that accepted a username would let
anyone change anyone's password.
"""

import inspect
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient

from agent import auth
from app import app

client = TestClient(app)

OLD_PASSWORD = "the-original-password"
NEW_PASSWORD = "a-brand-new-password"


@pytest.fixture
def user():
    username = f"rst-{uuid.uuid4().hex[:10]}@example.test"
    auth.create_user(username, "pro_corp", OLD_PASSWORD)
    yield username


# --- the absence --------------------------------------------------------------

def test_redemption_takes_no_username():
    params = inspect.signature(auth.redeem_password_reset).parameters
    assert "username" not in params
    assert set(params) == {"raw_token", "password"}


def test_the_route_does_not_read_a_username_from_the_body():
    """Parsed, not grepped: the route legitimately RETURNS a username."""
    import ast

    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "agent", "auth_routes.py")
    tree = ast.parse(open(path, encoding="utf-8").read())
    func = next(n for n in ast.walk(tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and n.name == "redeem_password_reset")

    assert "username" not in {a.arg for a in func.args.args}
    for node in ast.walk(func):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get" and node.args
                and isinstance(node.args[0], ast.Constant)):
            assert node.args[0].value != "username", "username is read from the request"


# --- the happy path -----------------------------------------------------------

def test_a_reset_changes_the_password(user):
    token = auth.create_password_reset(user, created_by="operator")

    auth.redeem_password_reset(token, NEW_PASSWORD)

    assert auth.authenticate(user, NEW_PASSWORD).username == user
    with pytest.raises(auth.AuthError):
        auth.authenticate(user, OLD_PASSWORD)


def test_the_link_says_whose_account_it_is_without_spending_it(user):
    token = auth.create_password_reset(user)
    assert auth.peek_password_reset(token)["username"] == user
    # Still usable after peeking.
    auth.redeem_password_reset(token, NEW_PASSWORD)


# --- the property that matters most -------------------------------------------

def test_a_reset_signs_out_every_existing_session(user):
    """
    A reset usually means the credential is suspected lost. Leaving live
    sessions running would let whoever prompted the reset carry on regardless,
    which is the opposite of what the person asking for it wants.
    """
    live = auth.issue_session(auth.authenticate(user, OLD_PASSWORD))
    assert auth._principal_from_session(live) is not None

    token = auth.create_password_reset(user)
    auth.redeem_password_reset(token, NEW_PASSWORD)

    assert auth._principal_from_session(live) is None, (
        "a session issued before the reset still works"
    )


# --- single use ---------------------------------------------------------------

def test_a_reset_link_works_exactly_once(user):
    token = auth.create_password_reset(user)
    auth.redeem_password_reset(token, NEW_PASSWORD)

    with pytest.raises(auth.AuthError, match="not valid"):
        auth.redeem_password_reset(token, "another-new-password")

    # The first new password still stands.
    assert auth.authenticate(user, NEW_PASSWORD).username == user


def test_a_revoked_link_cannot_be_used(user):
    token = auth.create_password_reset(user)
    selector = auth.list_password_resets(user)[0]["selector"]
    assert auth.revoke_password_reset(selector) is True

    with pytest.raises(auth.AuthError, match="not valid"):
        auth.redeem_password_reset(token, NEW_PASSWORD)
    assert auth.authenticate(user, OLD_PASSWORD).username == user


def test_an_expired_link_is_refused(user):
    token = auth.create_password_reset(user, ttl_seconds=-1)
    assert auth.peek_password_reset(token) is None
    with pytest.raises(auth.AuthError, match="not valid"):
        auth.redeem_password_reset(token, NEW_PASSWORD)


# --- forgery ------------------------------------------------------------------

def test_a_made_up_token_is_refused():
    for bogus in ("carst_aaaa.bbbb", "not-a-token", "", "carst_x.y"):
        assert auth.peek_password_reset(bogus) is None
        with pytest.raises(auth.AuthError):
            auth.redeem_password_reset(bogus, NEW_PASSWORD)


def test_the_right_selector_with_a_wrong_verifier_is_refused(user):
    auth.create_password_reset(user)
    selector = auth.list_password_resets(user)[0]["selector"]

    forged = f"carst_{selector}.{'z' * 43}"
    assert auth.peek_password_reset(forged) is None
    with pytest.raises(auth.AuthError, match="not valid"):
        auth.redeem_password_reset(forged, NEW_PASSWORD)
    assert auth.authenticate(user, OLD_PASSWORD).username == user


def test_an_invite_token_cannot_be_used_as_a_reset():
    """The prefixes are what keep the two token families apart."""
    from agent.memory import company_registry
    cid = f"rst-co-{uuid.uuid4().hex[:8]}"
    company_registry.create_company(cid, tier="pro")
    try:
        invite = auth.create_invite(cid)
        assert auth.peek_password_reset(invite) is None
        with pytest.raises(auth.AuthError):
            auth.redeem_password_reset(invite, NEW_PASSWORD)
    finally:
        company_registry.delete_company(cid)


# --- guards -------------------------------------------------------------------

def test_a_reset_for_an_unknown_account_is_refused():
    """Quietly succeeding would hide the typo until the recipient complains."""
    with pytest.raises(auth.AuthError, match="No account"):
        auth.create_password_reset(f"nobody-{uuid.uuid4().hex[:8]}@example.test")


def test_a_short_password_is_refused_and_the_link_survives(user):
    token = auth.create_password_reset(user)
    with pytest.raises(auth.AuthError, match="12 characters"):
        auth.redeem_password_reset(token, "short")
    assert auth.peek_password_reset(token) is not None
    assert auth.authenticate(user, OLD_PASSWORD).username == user


# --- over HTTP ----------------------------------------------------------------

def test_the_http_flow_sets_the_password_and_signs_in(user):
    token = auth.create_password_reset(user)

    seen = client.get(f"/api/auth/reset/{token}")
    assert seen.status_code == 200
    assert seen.json()["username"] == user

    done = client.post(f"/api/auth/reset/{token}/redeem", json={"password": NEW_PASSWORD})
    assert done.status_code == 200, done.text
    assert done.json()["company_id"] == "pro_corp"
    assert auth.SESSION_COOKIE in done.headers.get("set-cookie", "")
    client.cookies.clear()

    assert auth.authenticate(user, NEW_PASSWORD).username == user


def test_an_unusable_token_is_a_404_with_one_message():
    r = client.get("/api/auth/reset/carst_nope.nope")
    assert r.status_code == 404
    assert "not valid" in r.json()["detail"]
