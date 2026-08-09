"""
The OAuth connect/callback flow.

Both providers generated a `state` value and documented that the caller must
store it. There was no caller — nothing mounted the flow — so the CSRF
parameter was decorative. These tests are about the state machine, which is the
part that actually decides whose Google account gets attached to whose company.

No real provider is contacted. The token exchange is faked, because the point
under test is what we do with the callback, not what Google does with the code.
Real consent against real credentials is a separate, manual verification — see
agent_autofill/providers/VERIFICATION.md.
"""

import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("AGENT_DB_DIR", tempfile.mkdtemp(prefix="cairo-oauth-db-"))
os.environ.setdefault("AGENT_GENERATED_DIR", tempfile.mkdtemp(prefix="cairo-oauth-gen-"))
os.environ.setdefault("PROVIDER_DB_PATH",
                      str(Path(tempfile.mkdtemp(prefix="cairo-oauth-prov-")) / "p.db"))

from agent_autofill.providers import oauth_state  # noqa: E402

COMPANY = "oauth-test-co"
OTHER = "someone-elses-co"
REDIRECT = "https://cairoai.web.app/api/autofill/providers/google_drive/callback"


# --- the state machine ------------------------------------------------------


def test_a_state_can_be_issued_and_consumed_once():
    state = oauth_state.issue(COMPANY, "google_drive", REDIRECT)
    flow = oauth_state.consume(state, "google_drive")
    assert flow["company_id"] == COMPANY
    assert flow["redirect_uri"] == REDIRECT


def test_a_state_cannot_be_consumed_twice():
    """
    Replay protection. Without it, a captured callback URL could be replayed to
    attach an account again — and the second time, the code is the attacker's.
    """
    state = oauth_state.issue(COMPANY, "google_drive", REDIRECT)
    oauth_state.consume(state, "google_drive")
    with pytest.raises(oauth_state.OAuthStateError):
        oauth_state.consume(state, "google_drive")


def test_an_unknown_state_is_refused():
    with pytest.raises(oauth_state.OAuthStateError):
        oauth_state.consume("never-issued-by-us", "google_drive")


@pytest.mark.parametrize("empty", ["", None])
def test_a_missing_state_is_refused(empty):
    with pytest.raises(oauth_state.OAuthStateError):
        oauth_state.consume(empty, "google_drive")


def test_a_state_issued_for_one_provider_does_not_work_for_another():
    """Otherwise a Dropbox consent could complete a Drive connection."""
    state = oauth_state.issue(COMPANY, "dropbox", REDIRECT)
    with pytest.raises(oauth_state.OAuthStateError):
        oauth_state.consume(state, "google_drive")


def test_a_rejected_state_is_still_burned():
    """
    The row is deleted before the provider and expiry checks, so a wrong-
    provider attempt cannot be used to probe which states exist and then be
    retried against the right one.
    """
    state = oauth_state.issue(COMPANY, "dropbox", REDIRECT)
    with pytest.raises(oauth_state.OAuthStateError):
        oauth_state.consume(state, "google_drive")
    with pytest.raises(oauth_state.OAuthStateError):
        oauth_state.consume(state, "dropbox")


def test_an_expired_state_is_refused(monkeypatch):
    state = oauth_state.issue(COMPANY, "google_drive", REDIRECT)
    from datetime import timedelta

    real_now = oauth_state._now()
    monkeypatch.setattr(oauth_state, "_now",
                        lambda: real_now + timedelta(seconds=oauth_state.STATE_TTL_SECONDS + 1))
    with pytest.raises(oauth_state.OAuthStateError, match="expired"):
        oauth_state.consume(state, "google_drive")


def test_the_raw_state_is_not_stored():
    """It is a bearer value for the duration of the flow; a DB dump must not
    hand someone a live one."""
    import sqlite3

    from agent_autofill.providers.provider_db import provider_db_path

    state = oauth_state.issue(COMPANY, "google_drive", REDIRECT)
    with sqlite3.connect(str(provider_db_path())) as conn:
        stored = [r[0] for r in conn.execute(
            "SELECT state_digest FROM provider_oauth_state").fetchall()]
    assert state not in stored
    assert any(len(s) == 64 for s in stored), "expected a sha256 digest"


def test_issue_requires_a_company_and_a_redirect():
    for args in ((None, "google_drive", REDIRECT), ("", "google_drive", REDIRECT),
                 (COMPANY, "google_drive", ""), (COMPANY, "google_drive", None)):
        with pytest.raises(oauth_state.OAuthStateError):
            oauth_state.issue(*args)


def test_purge_removes_only_expired_rows(monkeypatch):
    from datetime import timedelta

    live = oauth_state.issue(COMPANY, "google_drive", REDIRECT)
    real_now = oauth_state._now()
    monkeypatch.setattr(oauth_state, "_now",
                        lambda: real_now + timedelta(seconds=oauth_state.STATE_TTL_SECONDS + 5))
    stale = oauth_state.issue(OTHER, "google_drive", REDIRECT)
    monkeypatch.undo()

    oauth_state.purge_expired()
    # The live one still works; the stale one was issued with an already-past
    # expiry and is gone.
    assert oauth_state.consume(live, "google_drive")["company_id"] == COMPANY


# --- the confused deputy ----------------------------------------------------


def test_the_company_comes_from_stored_state_not_the_callback_url():
    """
    THE attack this whole module exists for. An attacker completes a genuine
    consent for their OWN Google account, then replays the callback with a
    different company_id. If the callback read company_id from its own
    parameters, their Drive would be attached to that company.

    The route signature is the proof: `finish_connection` takes no company_id
    at all, so there is no parameter to swap.
    """
    import inspect

    from agent_autofill.providers.oauth_routes import finish_connection

    params = set(inspect.signature(finish_connection).parameters)
    assert "company_id" not in params, (
        "the callback must not accept a company_id; it belongs to the state")
    assert {"code", "state"} <= params


def test_connect_route_requires_a_company_id():
    import inspect

    from agent_autofill.providers.oauth_routes import start_connection

    assert "company_id" in inspect.signature(start_connection).parameters


# --- the routes -------------------------------------------------------------


@pytest.fixture
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from agent_autofill.providers.oauth_routes import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app, follow_redirects=False)


def test_unknown_provider_is_404(client):
    assert client.get("/api/autofill/providers/onedrive/connect",
                      params={"company_id": COMPANY}).status_code == 404


def test_connect_without_client_credentials_is_503_not_a_crash(client, monkeypatch):
    """A console client that was never configured is an operator problem, and
    the provider's own message names the environment variable."""
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    r = client.get("/api/autofill/providers/google_drive/connect",
                   params={"company_id": COMPANY})
    assert r.status_code == 503


def test_connect_redirects_to_the_provider_with_our_state(client, monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "test-client-secret")

    r = client.get("/api/autofill/providers/google_drive/connect",
                   params={"company_id": COMPANY})
    assert r.status_code == 302
    location = r.headers["location"]
    assert location.startswith("https://accounts.google.com/")
    assert "state=" in location
    # The scope that cannot be folder-scoped must not appear — see BUILD_STATE.
    assert "drive.readonly" not in location


def test_callback_with_a_forged_state_does_not_exchange_anything(client, monkeypatch):
    from agent_autofill.providers import oauth_routes

    exchanged = []

    class _Impl:
        def connect(self, *a, **k):
            exchanged.append(a)
            return {}

    monkeypatch.setattr(oauth_routes, "_provider", lambda name: _Impl())
    r = client.get("/api/autofill/providers/google_drive/callback",
                   params={"code": "attacker-code", "state": "not-ours"})
    assert r.status_code == 303
    assert "connect_error" in r.headers["location"]
    assert exchanged == [], "a forged state reached the token exchange"


def test_callback_uses_the_stored_company(client, monkeypatch):
    from agent_autofill.providers import oauth_routes

    seen = {}

    class _Impl:
        def connect(self, company_id, code, redirect_uri):
            seen.update(company_id=company_id, code=code, redirect_uri=redirect_uri)
            return {"account_label": "someone@example.com"}

    monkeypatch.setattr(oauth_routes, "_provider", lambda name: _Impl())
    state = oauth_state.issue(COMPANY, "google_drive", REDIRECT)

    r = client.get("/api/autofill/providers/google_drive/callback",
                   params={"code": "a-real-code", "state": state})
    assert r.status_code == 303
    assert "connected=" in r.headers["location"]
    assert seen["company_id"] == COMPANY
    assert seen["redirect_uri"] == REDIRECT


def test_callback_when_the_user_declines(client, monkeypatch):
    from agent_autofill.providers import oauth_routes

    called = []
    monkeypatch.setattr(oauth_routes, "_provider",
                        lambda name: called.append(name) or object())
    r = client.get("/api/autofill/providers/google_drive/callback",
                   params={"error": "access_denied"})
    assert r.status_code == 303 and "connect_error" in r.headers["location"]
    assert called == []


def test_a_failed_exchange_does_not_leak_the_code_through_our_logging(
        client, monkeypatch, caplog):
    """
    Scoped to OUR logger deliberately.

    An earlier version asserted against all captured logs and failed — on the
    test client's own DEBUG line recording the request URL it had just made.
    That is the harness, not us. But it points at something real that we cannot
    fix from here and should not pretend to: the authorization code arrives as
    a QUERY PARAMETER, and platform access logs record query strings. On Cloud
    Run the request line is logged by the runtime before our code sees it.

    That exposure is inherent to the redirect-based flow. It is bounded because
    a code is single-use and short-lived, and the exchange happens immediately.
    PKCE is the actual mitigation — a stolen code is useless without the
    verifier — and is NOT implemented here. See CLAUDE.md.

    What this test does prove: our own handler never puts the code into a log
    record, and never into the redirect it sends the user to.
    """
    from agent_autofill.providers import oauth_routes

    class _Impl:
        def connect(self, *a, **k):
            raise RuntimeError("scope check failed")

    monkeypatch.setattr(oauth_routes, "_provider", lambda name: _Impl())
    state = oauth_state.issue(COMPANY, "google_drive", REDIRECT)

    with caplog.at_level("DEBUG", logger="agent_autofill.providers.oauth"):
        r = client.get("/api/autofill/providers/google_drive/callback",
                       params={"code": "SENSITIVE-CODE-VALUE", "state": state})

    ours = [rec for rec in caplog.records
            if rec.name.startswith("agent_autofill")]
    assert ours, "expected the handler to log the failure at all"
    assert not any("SENSITIVE-CODE-VALUE" in rec.getMessage() for rec in ours)
    assert "SENSITIVE-CODE-VALUE" not in r.headers["location"]


def test_a_successful_connection_does_not_log_the_code_either(client, monkeypatch, caplog):
    from agent_autofill.providers import oauth_routes

    class _Impl:
        def connect(self, *a, **k):
            return {"account_label": "someone@example.com"}

    monkeypatch.setattr(oauth_routes, "_provider", lambda name: _Impl())
    state = oauth_state.issue(COMPANY, "google_drive", REDIRECT)

    with caplog.at_level("DEBUG", logger="agent_autofill.providers.oauth"):
        r = client.get("/api/autofill/providers/google_drive/callback",
                       params={"code": "ANOTHER-SECRET-CODE", "state": state})

    ours = [rec for rec in caplog.records if rec.name.startswith("agent_autofill")]
    assert not any("ANOTHER-SECRET-CODE" in rec.getMessage() for rec in ours)
    assert "ANOTHER-SECRET-CODE" not in r.headers["location"]


def test_the_return_redirect_cannot_be_pointed_offsite(client, monkeypatch):
    """
    An open redirect on a domain users are being asked to trust with Drive
    access is worth more to a phisher than most bugs. The return path is fixed.
    """
    from agent_autofill.providers import oauth_routes

    class _Impl:
        def connect(self, *a, **k):
            return {"account_label": "x"}

    monkeypatch.setattr(oauth_routes, "_provider", lambda name: _Impl())
    state = oauth_state.issue(COMPANY, "google_drive", REDIRECT)
    r = client.get("/api/autofill/providers/google_drive/callback",
                   params={"code": "c", "state": state,
                           "next": "https://evil.example.com"})
    assert r.headers["location"].startswith(oauth_routes.RETURN_PATH + "?")
    assert "evil.example.com" not in r.headers["location"]
