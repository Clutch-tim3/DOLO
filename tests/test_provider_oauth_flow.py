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


def _state(company_id, provider, redirect_uri):
    """issue() returns the state plus the PKCE challenge; tests mostly want the
    state. The challenge half has its own tests below."""
    return oauth_state.issue(company_id, provider, redirect_uri)["state"]



# --- the state machine ------------------------------------------------------


def test_a_state_can_be_issued_and_consumed_once():
    state = _state(COMPANY, "google_drive", REDIRECT)
    flow = oauth_state.consume(state, "google_drive")
    assert flow["company_id"] == COMPANY
    assert flow["redirect_uri"] == REDIRECT


def test_a_state_cannot_be_consumed_twice():
    """
    Replay protection. Without it, a captured callback URL could be replayed to
    attach an account again — and the second time, the code is the attacker's.
    """
    state = _state(COMPANY, "google_drive", REDIRECT)
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
    state = _state(COMPANY, "dropbox", REDIRECT)
    with pytest.raises(oauth_state.OAuthStateError):
        oauth_state.consume(state, "google_drive")


def test_a_rejected_state_is_still_burned():
    """
    The row is deleted before the provider and expiry checks, so a wrong-
    provider attempt cannot be used to probe which states exist and then be
    retried against the right one.
    """
    state = _state(COMPANY, "dropbox", REDIRECT)
    with pytest.raises(oauth_state.OAuthStateError):
        oauth_state.consume(state, "google_drive")
    with pytest.raises(oauth_state.OAuthStateError):
        oauth_state.consume(state, "dropbox")


def test_an_expired_state_is_refused(monkeypatch):
    state = _state(COMPANY, "google_drive", REDIRECT)
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

    state = _state(COMPANY, "google_drive", REDIRECT)
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

    live = _state(COMPANY, "google_drive", REDIRECT)
    real_now = oauth_state._now()
    monkeypatch.setattr(oauth_state, "_now",
                        lambda: real_now + timedelta(seconds=oauth_state.STATE_TTL_SECONDS + 5))
    stale = _state(OTHER, "google_drive", REDIRECT)
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
        def connect(self, company_id, code, redirect_uri, code_verifier=None):
            seen.update(company_id=company_id, code=code, redirect_uri=redirect_uri,
                        code_verifier=code_verifier)
            return {"account_label": "someone@example.com"}

    monkeypatch.setattr(oauth_routes, "_provider", lambda name: _Impl())
    state = _state(COMPANY, "google_drive", REDIRECT)

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
    state = _state(COMPANY, "google_drive", REDIRECT)

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
    state = _state(COMPANY, "google_drive", REDIRECT)

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
    state = _state(COMPANY, "google_drive", REDIRECT)
    r = client.get("/api/autofill/providers/google_drive/callback",
                   params={"code": "c", "state": state,
                           "next": "https://evil.example.com"})
    assert r.headers["location"].startswith(oauth_routes.RETURN_PATH + "?")
    assert "evil.example.com" not in r.headers["location"]


# --- PKCE -------------------------------------------------------------------


def test_issue_returns_a_challenge_that_matches_a_verifier_it_kept():
    from agent_autofill.providers import pkce

    flow = oauth_state.issue(COMPANY, "google_drive", REDIRECT)
    assert flow["code_challenge_method"] == "S256"
    consumed = oauth_state.consume(flow["state"], "google_drive")
    assert pkce.verify(consumed["code_verifier"], flow["code_challenge"])


def test_the_verifier_never_appears_in_the_authorization_url(client, monkeypatch):
    """
    The entire point. The browser carries the challenge; the verifier stays
    here. If it leaked into the URL, PKCE would defend nothing — the URL is the
    thing that ends up in access logs.
    """
    import sqlite3

    from agent_autofill.providers.provider_db import provider_db_path

    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "test-client-secret")

    r = client.get("/api/autofill/providers/google_drive/connect",
                   params={"company_id": COMPANY})
    location = r.headers["location"]
    assert "code_challenge=" in location
    assert "code_challenge_method=S256" in location

    with sqlite3.connect(str(provider_db_path())) as conn:
        verifiers = [row[0] for row in conn.execute(
            "SELECT code_verifier FROM provider_oauth_state").fetchall() if row[0]]
    assert verifiers, "no verifier was stored"
    for v in verifiers:
        assert v not in location, "the verifier leaked into the authorization URL"


def test_the_verifier_reaches_the_token_exchange(client, monkeypatch):
    from agent_autofill.providers import oauth_routes, pkce

    seen = {}

    class _Impl:
        def connect(self, company_id, code, redirect_uri, code_verifier=None):
            seen["verifier"] = code_verifier
            return {"account_label": "x"}

    monkeypatch.setattr(oauth_routes, "_provider", lambda name: _Impl())
    flow = oauth_state.issue(COMPANY, "google_drive", REDIRECT)
    client.get("/api/autofill/providers/google_drive/callback",
               params={"code": "c", "state": flow["state"]})

    assert seen["verifier"], "the exchange went out without a verifier"
    assert pkce.verify(seen["verifier"], flow["code_challenge"])


def test_challenge_is_unpadded_base64url():
    """Padding characters are not allowed in the parameter; providers reject
    a challenge carrying them."""
    from agent_autofill.providers import pkce

    for _ in range(20):
        challenge = pkce.challenge_for(pkce.new_verifier())
        assert "=" not in challenge
        assert "+" not in challenge and "/" not in challenge
        assert len(challenge) == 43


def test_each_flow_gets_a_fresh_verifier():
    from agent_autofill.providers import pkce

    seen = set()
    for _ in range(10):
        seen.add(pkce.new_verifier())
    assert len(seen) == 10


def test_verify_rejects_a_mismatched_pair():
    from agent_autofill.providers import pkce

    a, b = pkce.new_verifier(), pkce.new_verifier()
    assert pkce.verify(a, pkce.challenge_for(a))
    assert not pkce.verify(b, pkce.challenge_for(a))
    assert not pkce.verify("", pkce.challenge_for(a))
    assert not pkce.verify(a, "")


def test_only_s256_is_produced():
    """`plain` puts the verifier in the URL — the exact place it must not be."""
    from agent_autofill.providers import pkce

    assert pkce.METHOD == "S256"


def test_both_providers_accept_the_pkce_arguments():
    """
    A signature check, because a provider that silently ignored code_challenge
    would produce a working flow with no PKCE at all — and nothing else here
    would notice.
    """
    import inspect

    from agent_autofill.providers.dropbox_provider import DropboxProvider
    from agent_autofill.providers.google_drive_provider import GoogleDriveProvider

    for impl in (GoogleDriveProvider, DropboxProvider):
        auth = inspect.signature(impl.build_authorization_url).parameters
        assert "code_challenge" in auth, impl.__name__
        assert "code_challenge_method" in auth, impl.__name__
        assert "code_verifier" in inspect.signature(impl.connect).parameters, impl.__name__
