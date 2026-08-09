"""
Authentication: the identity every other control in this codebase pins to.

Before this existed, each company-aware route read its tenant from
`request.headers.get("X-Company-ID", "starter_corp")`. Anyone could be any
company, and an anonymous caller was always somebody. Tenant pinning in
tool_dispatch, the export gate's company checks, finalize_quote_flow's
ownership check and the OAuth state binding were all real — and all pinned to a
value nobody had proved.

These tests are about that value. They are grouped as:

  1. fail closed        — no credential, no tenant, on every protected route
  2. the header is dead — X-Company-ID confers nothing, ever
  3. isolation          — company A cannot act as company B, and a refusal
                          writes nothing
  4. credential storage — nothing is stored in the clear, comparisons are
                          constant time
  5. sessions           — revocation, tampering, disabled accounts
  6. device tokens      — the headless pairing flow
  7. tier gating        — resolved from the principal, not from a header
  8. leakage            — no credential reaches a log

NOTE ON IMPORTING `app`: this module imports it, which re-keys
AUTOFILL_STAMP_SECRET from .env.local (see CLAUDE.md). That is already true of
test_batch_endpoint.py and test_single_endpoint.py, and pytest imports every
test module during collection anyway, so this adds no new ordering hazard. The
stamp tests deliberately do not import it and still do not need to.
"""

import logging
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent import auth  # noqa: E402
from app import app  # noqa: E402

client = TestClient(app)


# --- fixtures ---------------------------------------------------------------
#
# Users get random names so a re-run does not collide with rows left by the
# last one, and so two companies in the same test are unmistakably distinct.

PASSWORD = "correct-horse-battery-staple"


def _make_user(company_id: str) -> auth.User:
    return auth.create_user(f"t-{uuid.uuid4().hex[:12]}@example.test", company_id, PASSWORD)


@pytest.fixture(scope="module")
def company_a():
    user = _make_user("pro_corp")
    return {"user": user, "token": auth.issue_session(user), "company": "pro_corp"}


@pytest.fixture(scope="module")
def company_b():
    user = _make_user("enterprise_corp")
    return {"user": user, "token": auth.issue_session(user), "company": "enterprise_corp"}


@pytest.fixture(scope="module")
def starter_user():
    user = _make_user("starter_corp")
    return {"user": user, "token": auth.issue_session(user), "company": "starter_corp"}


def bearer(actor) -> dict:
    """Authenticate as `actor` over the header carrier the desktop client uses."""
    return {"Authorization": f"Bearer {actor['token']}"}


# --- 1. fail closed ---------------------------------------------------------
#
# Every route that resolves a company. Miss one and that route is still wide
# open, which is why this is a table rather than a handful of hand-written
# cases: adding a company-aware route without adding it here is the failure
# mode, and a table at least makes the omission visible in one place.

PROTECTED = [
    ("GET", "/api/company-profile"),
    ("GET", "/api/vault-status"),
    ("GET", "/api/subscription-status"),
    ("POST", "/api/agent/chat"),
    ("POST", "/api/archive/upload-document"),
    ("POST", "/api/batch-sort"),
    ("POST", "/api/tender/submit"),
    ("POST", "/api/predict"),
    ("GET", "/api/questionnaire"),
    ("GET", "/api/questionnaire/definition"),
    ("GET", "/api/questionnaire/autofill-values"),
    ("POST", "/api/questionnaire/preview"),
    ("POST", "/api/questionnaire/save"),
    ("GET", "/api/autofill/providers/status"),
    ("GET", "/api/autofill/providers/google_drive/connect"),
    ("POST", "/api/autofill/providers/google_drive/disconnect"),
    ("GET", "/api/auth/me"),
    ("GET", "/api/auth/devices"),
    ("POST", "/api/auth/device/pairing-code"),
    # Not header call sites — these resolve no company_id at all, which is why
    # they were easy to miss. They write files, delete files, or spend money on
    # a paid external API, and they answered to anyone who could reach them.
    ("GET", "/api/companies"),
    ("POST", "/api/companies/upload"),
    ("DELETE", "/api/companies/ANY%20COMPANY"),
    ("POST", "/api/generate-quotation"),
    ("POST", "/api/estimate"),
]


@pytest.mark.parametrize("method,path", PROTECTED)
def test_an_anonymous_request_is_refused(method, path):
    """
    401, not 200, and not a default tenant.

    The old `"starter_corp"` default is the bug being fixed; replacing it with
    any other default would be the same bug. So the assertion is on the status,
    not on which company came back.
    """
    response = client.request(method, path, json={"message": "hello"},
                              follow_redirects=False)
    assert response.status_code == 401, (
        f"{method} {path} answered {response.status_code} without a credential")


@pytest.mark.parametrize("method,path", PROTECTED)
def test_a_bad_credential_is_refused(method, path):
    """A well-formed but wrong token is worth no more than no token at all."""
    response = client.request(
        method, path, json={"message": "hello"}, follow_redirects=False,
        headers={"Authorization": "Bearer cases_AAAAAAAAAAAAAAAA.BBBBBBBBBBBBBBBB"})
    assert response.status_code == 401


# --- 2. the header is dead --------------------------------------------------


def test_the_old_header_alone_authenticates_nothing():
    """
    The exact request that worked against the live site:

        curl -H "X-Company-ID: enterprise_corp" .../api/company-profile  -> 200

    It must now be a 401. This is the regression pin for the whole finding.
    """
    for company in ("starter_corp", "pro_corp", "enterprise_corp"):
        response = client.get("/api/company-profile",
                              headers={"X-Company-ID": company})
        assert response.status_code == 401, f"header alone still served {company}"


def test_the_header_cannot_override_an_authenticated_identity(company_a):
    """
    Authenticated as pro_corp, claiming enterprise_corp in the header.

    The response must describe pro_corp. Not a 403 — the header is not a
    request to be refused, it is an input that no longer exists.
    """
    response = client.get("/api/subscription-status",
                          headers={**bearer(company_a),
                                   "X-Company-ID": "enterprise_corp"})
    assert response.status_code == 200
    assert response.json()["company_id"] == "pro_corp"


# --- 3. isolation -----------------------------------------------------------


def test_acting_as_another_company_is_refused_and_writes_nothing(company_a, company_b):
    """
    Company A starts an OAuth connection naming company B.

    Two assertions, and the second is the one that matters: the refusal happens
    before `oauth_state.issue`, so there is no half-started flow left in the
    table for someone to finish later.
    """
    from agent import db
    from agent_autofill.providers.provider_db import provider_db_path

    def state_rows() -> int:
        with db.connect(provider_db_path()) as conn:
            return int(conn.execute(
                "SELECT COUNT(*) FROM provider_oauth_state").fetchone()[0])

    before = state_rows()
    response = client.get("/api/autofill/providers/google_drive/connect",
                          params={"company_id": company_b["company"]},
                          headers=bearer(company_a), follow_redirects=False)
    assert response.status_code == 403
    assert state_rows() == before, "a refused cross-tenant request wrote a state row"


def test_reading_another_companys_connections_is_refused(company_a, company_b):
    response = client.get("/api/autofill/providers/status",
                          params={"company_id": company_b["company"]},
                          headers=bearer(company_a))
    assert response.status_code == 403


def test_disconnecting_another_companys_provider_is_refused(company_a, company_b):
    response = client.post("/api/autofill/providers/google_drive/disconnect",
                           params={"company_id": company_b["company"]},
                           headers=bearer(company_a))
    assert response.status_code == 403


def test_revoking_another_companys_device_is_refused_and_the_device_still_works(
        company_a, company_b):
    """
    A cross-tenant write attempt, proven not to have taken effect by using the
    credential afterwards rather than by reading a row back.
    """
    issued = auth.issue_device_token(company_b["company"], company_b["user"].user_id,
                                     "b-device")
    device_headers = {"Authorization": f"Bearer {issued['device_token']}"}

    assert client.get("/api/auth/me", headers=device_headers).status_code == 200

    refused = client.post(f"/api/auth/devices/{issued['device_id']}/revoke",
                          headers=bearer(company_a))
    assert refused.status_code == 404

    still_working = client.get("/api/auth/me", headers=device_headers)
    assert still_working.status_code == 200
    assert still_working.json()["company_id"] == company_b["company"]


def test_a_companys_device_list_shows_only_its_own(company_a, company_b):
    issued = auth.issue_device_token(company_b["company"], company_b["user"].user_id,
                                     "b-only")
    listing = client.get("/api/auth/devices", headers=bearer(company_a)).json()
    assert issued["device_id"] not in [d["device_id"] for d in listing["devices"]]


# --- 4. credential storage --------------------------------------------------


def test_a_password_is_not_stored_in_the_clear():
    from agent import db
    from agent.db_paths import AGENT_MEMORY_DB

    username = f"t-{uuid.uuid4().hex[:12]}@example.test"
    auth.create_user(username, "pro_corp", PASSWORD)
    with db.connect(AGENT_MEMORY_DB) as conn:
        stored = conn.execute(
            "SELECT password_hash FROM auth_users WHERE username = ?",
            (username,)).fetchone()["password_hash"]

    assert PASSWORD not in stored
    assert stored.startswith("pbkdf2_sha256$")
    assert auth.verify_password(PASSWORD, stored)
    assert not auth.verify_password(PASSWORD + "x", stored)


def test_the_same_password_hashes_differently_each_time():
    """Per-user salt. Two identical passwords must not produce the same row."""
    assert auth.hash_password(PASSWORD) != auth.hash_password(PASSWORD)


def test_a_session_token_is_not_stored_in_the_clear(company_a):
    from agent import db
    from agent.db_paths import AGENT_MEMORY_DB

    raw = company_a["token"]
    selector = raw[len("cases_"):].split(".")[0]
    verifier = raw.split(".", 1)[1]
    with db.connect(AGENT_MEMORY_DB) as conn:
        row = conn.execute(
            "SELECT verifier_hash FROM auth_sessions WHERE selector = ?",
            (selector,)).fetchone()

    assert row is not None
    assert verifier not in row["verifier_hash"]
    assert raw not in row["verifier_hash"]


def test_a_valid_selector_with_a_wrong_verifier_is_refused(company_a):
    """
    The half of the split-token design that does the work.

    The selector is public — it is the index. Presenting a real one with the
    wrong verifier must fail, and it fails on a `compare_digest` rather than on
    a database lookup, which is what keeps the comparison constant time.
    """
    selector = company_a["token"][len("cases_"):].split(".")[0]
    forged = f"cases_{selector}.{'A' * 43}"
    response = client.get("/api/auth/me",
                          headers={"Authorization": f"Bearer {forged}"})
    assert response.status_code == 401


# --- 5. sessions ------------------------------------------------------------


def test_login_sets_an_httponly_session_cookie():
    user = _make_user("pro_corp")
    response = client.post("/api/auth/login",
                           json={"username": user.username, "password": PASSWORD})
    assert response.status_code == 200
    assert response.json()["company_id"] == "pro_corp"

    cookie_header = response.headers.get("set-cookie", "")
    assert auth.SESSION_COOKIE in cookie_header
    assert "HttpOnly" in cookie_header
    assert "SameSite=lax" in cookie_header or "samesite=lax" in cookie_header.lower()
    client.cookies.clear()


def test_a_wrong_password_is_refused_and_says_nothing_useful():
    user = _make_user("pro_corp")
    response = client.post("/api/auth/login",
                           json={"username": user.username, "password": "wrong-password"})
    assert response.status_code == 401
    unknown = client.post("/api/auth/login",
                          json={"username": "nobody@example.test",
                                "password": "wrong-password"})
    assert unknown.status_code == 401
    # Identical wording: telling "no such user" apart from "wrong password" is a
    # free account-enumeration oracle.
    assert response.json()["detail"] == unknown.json()["detail"]


def test_a_revoked_session_stops_working():
    user = _make_user("pro_corp")
    token = auth.issue_session(user)
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/auth/me", headers=headers).status_code == 200
    assert auth.revoke_session(token) is True
    assert client.get("/api/auth/me", headers=headers).status_code == 401


def test_logout_revokes_the_session_it_was_given():
    user = _make_user("pro_corp")
    token = auth.issue_session(user)
    headers = {"Authorization": f"Bearer {token}"}
    client.cookies.set(auth.SESSION_COOKIE, token)
    assert client.post("/api/auth/logout").status_code == 200
    client.cookies.clear()
    assert client.get("/api/auth/me", headers=headers).status_code == 401


def test_disabling_an_account_kills_its_live_sessions():
    """A disabled account that stays signed in for another twelve hours is not
    disabled."""
    user = _make_user("pro_corp")
    token = auth.issue_session(user)
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/auth/me", headers=headers).status_code == 200
    auth.set_disabled(user.username, True)
    assert client.get("/api/auth/me", headers=headers).status_code == 401


def test_an_expired_session_is_refused():
    from datetime import datetime, timedelta, timezone

    from agent import db
    from agent.db_paths import AGENT_MEMORY_DB

    user = _make_user("pro_corp")
    token = auth.issue_session(user)
    selector = token[len("cases_"):].split(".")[0]
    with db.connect(AGENT_MEMORY_DB) as conn:
        conn.execute(
            "UPDATE auth_sessions SET expires_at = ? WHERE selector = ?",
            ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(), selector))

    assert client.get("/api/auth/me",
                      headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_repeated_failures_lock_an_account_out():
    user = _make_user("pro_corp")
    for _ in range(auth.LOGIN_MAX_FAILURES):
        client.post("/api/auth/login",
                    json={"username": user.username, "password": "wrong-password"})
    # Even the correct password is now refused, until the window rolls off.
    response = client.post("/api/auth/login",
                           json={"username": user.username, "password": PASSWORD})
    assert response.status_code == 401


# --- 6. device tokens -------------------------------------------------------


def test_a_pairing_code_becomes_a_working_device_token(company_a):
    minted = client.post("/api/auth/device/pairing-code",
                         json={"label": "Reception desktop"},
                         headers=bearer(company_a))
    assert minted.status_code == 200
    code = minted.json()["pairing_code"]

    paired = client.post("/api/auth/device/pair", json={"code": code})
    assert paired.status_code == 200
    body = paired.json()
    assert body["company_id"] == company_a["company"]

    who = client.get("/api/auth/me",
                     headers={"Authorization": f"Bearer {body['device_token']}"})
    assert who.status_code == 200
    assert who.json()["auth_kind"] == "device"
    assert who.json()["company_id"] == company_a["company"]


def test_a_pairing_code_works_exactly_once(company_a):
    code = client.post("/api/auth/device/pairing-code",
                       json={"label": "once"}, headers=bearer(company_a)
                       ).json()["pairing_code"]
    assert client.post("/api/auth/device/pair", json={"code": code}).status_code == 200
    assert client.post("/api/auth/device/pair", json={"code": code}).status_code == 401


def test_an_expired_pairing_code_is_refused(company_a):
    from datetime import datetime, timedelta, timezone

    from agent import db
    from agent.db_paths import AGENT_MEMORY_DB

    code = client.post("/api/auth/device/pairing-code",
                       json={"label": "stale"}, headers=bearer(company_a)
                       ).json()["pairing_code"]
    selector = code[len("capair_"):].split(".")[0]
    with db.connect(AGENT_MEMORY_DB) as conn:
        conn.execute(
            "UPDATE auth_pairing_codes SET expires_at = ? WHERE selector = ?",
            ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(), selector))

    assert client.post("/api/auth/device/pair", json={"code": code}).status_code == 401


def test_a_device_token_cannot_mint_another_pairing_code(company_a):
    """
    Otherwise a leaked device credential is self-renewing and revoking it
    achieves nothing — the revoked device just pairs itself again.
    """
    issued = auth.issue_device_token(company_a["company"], company_a["user"].user_id,
                                     "self-renewing")
    response = client.post("/api/auth/device/pairing-code", json={"label": "nope"},
                           headers={"Authorization": f"Bearer {issued['device_token']}"})
    assert response.status_code == 403


def test_a_revoked_device_token_stops_working(company_a):
    issued = auth.issue_device_token(company_a["company"], company_a["user"].user_id,
                                     "revoke-me")
    headers = {"Authorization": f"Bearer {issued['device_token']}"}
    assert client.get("/api/auth/me", headers=headers).status_code == 200

    revoked = client.post(f"/api/auth/devices/{issued['device_id']}/revoke",
                          headers=bearer(company_a))
    assert revoked.status_code == 200
    assert client.get("/api/auth/me", headers=headers).status_code == 401


def test_a_device_token_is_scoped_to_one_company(company_a, company_b):
    issued = auth.issue_device_token(company_a["company"], company_a["user"].user_id,
                                     "scoped")
    headers = {"Authorization": f"Bearer {issued['device_token']}"}
    response = client.get("/api/subscription-status",
                          headers={**headers, "X-Company-ID": company_b["company"]})
    assert response.status_code == 200
    assert response.json()["company_id"] == company_a["company"]


def test_a_pairing_code_is_not_stored_in_the_clear(company_a):
    from agent import db
    from agent.db_paths import AGENT_MEMORY_DB

    code = client.post("/api/auth/device/pairing-code",
                       json={"label": "hashed"}, headers=bearer(company_a)
                       ).json()["pairing_code"]
    selector, verifier = code[len("capair_"):].split(".", 1)
    with db.connect(AGENT_MEMORY_DB) as conn:
        row = conn.execute(
            "SELECT verifier_hash FROM auth_pairing_codes WHERE selector = ?",
            (selector,)).fetchone()
    assert verifier not in row["verifier_hash"]


# --- 7. tier gating from the authenticated identity -------------------------


def test_a_starter_company_cannot_buy_pro_features_with_a_header(starter_user):
    """
    The whole point of tier gating from identity.

    A starter account sending `X-Company-ID: pro_corp` used to reach Claude,
    which is our Anthropic spend as well as our product boundary.
    """
    response = client.post("/api/agent/chat", json={"message": "hello"},
                           headers={**bearer(starter_user),
                                    "X-Company-ID": "pro_corp"})
    assert response.status_code == 403
    assert "Pro feature" in response.json()["detail"]


def test_a_starter_company_cannot_reach_a_model_it_does_not_have(starter_user):
    response = client.post(
        "/api/tender/submit",
        data={"model_version": "conquest", "supplier_name": "X"},
        headers={**bearer(starter_user), "X-Company-ID": "enterprise_corp"})
    assert response.status_code == 403
    assert "not available on your current plan" in response.json()["detail"]


def test_the_tier_reported_to_the_client_comes_from_the_server(starter_user):
    body = client.get("/api/auth/me", headers=bearer(starter_user)).json()
    assert body["tier"] == "starter"
    assert body["features"]["agent_enabled"] is False


# --- 8. leakage -------------------------------------------------------------


def test_a_failed_login_does_not_log_the_password(caplog):
    user = _make_user("pro_corp")
    secret = "hunter2-hunter2-hunter2"
    with caplog.at_level(logging.DEBUG):
        client.post("/api/auth/login",
                    json={"username": user.username, "password": secret})
    assert secret not in caplog.text


def test_a_successful_login_does_not_log_the_session_token(caplog):
    user = _make_user("pro_corp")
    with caplog.at_level(logging.DEBUG):
        response = client.post("/api/auth/login",
                               json={"username": user.username, "password": PASSWORD})
    token = client.cookies.get(auth.SESSION_COOKIE) or ""
    client.cookies.clear()
    assert response.status_code == 200
    assert token
    assert token not in caplog.text


def test_pairing_does_not_log_the_code_or_the_token(company_a, caplog):
    with caplog.at_level(logging.DEBUG):
        code = client.post("/api/auth/device/pairing-code", json={"label": "quiet"},
                           headers=bearer(company_a)).json()["pairing_code"]
        token = client.post("/api/auth/device/pair",
                            json={"code": code}).json()["device_token"]
    assert code not in caplog.text
    assert token not in caplog.text


def test_no_credential_travels_in_a_url(company_a):
    """
    Access logs record the request line before any handler runs, so anything in
    a query string is in the log by the time we could redact it. The pairing
    code and the password both go in a body; this pins that they are not
    accepted from the query string as a convenience.
    """
    code = client.post("/api/auth/device/pairing-code", json={"label": "url"},
                       headers=bearer(company_a)).json()["pairing_code"]
    response = client.post(f"/api/auth/device/pair?code={code}")
    assert response.status_code in (401, 422)
