"""
Proofs for `agent_autofill/providers` and `agent_autofill/webhooks`.

WHAT THIS FILE CAN AND CANNOT PROVE
===================================
It cannot prove the providers work. Nothing here talks to Google or Dropbox:
there is no OAuth client, no test account, and completing a consent screen
needs credentials. Every network path is untested and is marked UNVERIFIED in
the module docstrings and in `agent_autofill/providers/VERIFICATION.md`.

What it does prove, with real assertions:

  1. Both webhook receivers reject malformed and forged requests -- missing
     signature, wrong signature, unknown channel, replayed/stale delivery,
     expired channel -- and Dropbox's HMAC is checked *before* the body is
     parsed, demonstrated by spying on `json.loads`.
  2. Token encryption round-trips, the ciphertext on disk is not the token,
     and no token reaches a log statement or a browser-servable path.
  3. The renewal cadence keeps a 24-hour channel alive across every possible
     creation phase, and a daily cadence provably does not.
  4. All of it runs with `google-api-python-client`, `google-auth-oauthlib`
     and `dropbox` absent, which is the state of the machine it was written
     on.

Run with `-s` to see the literal tables the report quotes.
"""

from __future__ import annotations

import ast
import hashlib
import hmac
import importlib
import json
import logging
import re
import sys
import time
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent_autofill.providers import channel_registry, provider_db, token_store
from agent_autofill.providers import dropbox_provider as dbx_mod
from agent_autofill.providers import google_drive_provider as gd_mod
from agent_autofill.providers.base_provider import (
    ProviderSDKMissing,
    safe_download_path,
)
from agent_autofill.providers.dropbox_provider import DropboxProvider
from agent_autofill.providers.google_drive_provider import GoogleDriveProvider
from agent_autofill.providers.token_store import OAuthToken
from agent_autofill.webhooks import async_queue
from agent_autofill.webhooks import channel_renewal_cron as cron

PROVIDERS_DIR = REPO_ROOT / "agent_autofill" / "providers"
WEBHOOKS_DIR = REPO_ROOT / "agent_autofill" / "webhooks"

APP_SECRET = "dropbox-app-secret-for-tests-only"
CHANNEL_TOKEN = "channel-token-for-tests-0123456789abcdef"


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def provider_env(tmp_path, monkeypatch):
    """Point every piece of provider state at a throwaway directory."""
    monkeypatch.setenv("AGENT_AUTOFILL_PROVIDER_DB", str(tmp_path / "providers.db"))
    monkeypatch.setenv("AGENT_AUTOFILL_DOWNLOAD_DIR", str(tmp_path / "downloads"))
    monkeypatch.setenv("AGENT_AUTOFILL_TOKEN_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("AGENT_AUTOFILL_TOKEN_KEY_PREVIOUS", raising=False)
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.delenv("FUNCTION_TARGET", raising=False)
    yield


@pytest.fixture
def drive():
    return GoogleDriveProvider(
        client_id="test-client-id",
        client_secret="test-client-secret",
        default_callback_url="https://cairoai.web.app/api/autofill/webhooks/google-drive",
    )


@pytest.fixture
def dropbox():
    return DropboxProvider(app_key="test-app-key", app_secret=APP_SECRET)


def make_channel(
    channel_id="cai-test-channel-0001",
    company_id="co-test",
    token=CHANNEL_TOKEN,
    resource_id="resource-abc",
    ttl=86_400,
    now=None,
    last_message_number=0,
):
    now = time.time() if now is None else now
    ch = channel_registry.register_channel(
        channel_id=channel_id,
        provider="google_drive",
        company_id=company_id,
        channel_token=token,
        callback_url="https://cairoai.web.app/api/autofill/webhooks/google-drive",
        expiration_at=now + ttl,
        resource_id=resource_id,
        resource_uri="https://www.googleapis.com/drive/v3/files/folder-1",
        watched_file_id="folder-1",
        created_at=now,
    )
    if last_message_number:
        channel_registry.record_message_number(channel_id, last_message_number)
    return ch


# The positional is `cid`, not `channel_id`, so that a caller can still pass
# `channel_id=None` as an override to DROP the header. Naming it `channel_id`
# made that call a TypeError instead of the "missing channel ID" case it reads as.
def goog_headers(cid, **overrides):
    headers = {
        "X-Goog-Channel-ID": cid,
        "X-Goog-Channel-Token": CHANNEL_TOKEN,
        "X-Goog-Resource-ID": "resource-abc",
        "X-Goog-Resource-State": "update",
        "X-Goog-Message-Number": "7",
        "Content-Type": "application/json",
    }
    for key, value in overrides.items():
        header = "X-Goog-" + key.replace("_", "-").title().replace("Id", "ID")
        if value is None:
            headers.pop(header, None)
        else:
            headers[header] = value
    return headers


def dropbox_body(accounts=("dbid:AAAA-test-account",)):
    return json.dumps(
        {"list_folder": {"accounts": list(accounts)}, "delta": {"users": [12345]}}
    ).encode("utf-8")


def sign(body: bytes, secret: str = APP_SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def row(case, verdict):
    return (
        f"  {case:<34} accepted={str(verdict.accepted):<5} "
        f"status={verdict.http_status:<4} process={str(verdict.process):<5} "
        f"reason={verdict.reason}"
    )


# =============================================================================
# 1. The SDKs really are absent, and that does not stop anything here
# =============================================================================


def test_provider_sdks_are_not_installed():
    """Baseline for every other claim in this file about importability."""
    missing = []
    for name in ("googleapiclient", "google_auth_oauthlib", "dropbox"):
        try:
            importlib.import_module(name)
        except ImportError:
            missing.append(name)
    print("\n[SDK STATE] not installed:", missing)
    assert missing == ["googleapiclient", "google_auth_oauthlib", "dropbox"], (
        "This suite is meant to demonstrate that the package works without the "
        f"provider SDKs. Installed now: {set(['googleapiclient','google_auth_oauthlib','dropbox']) - set(missing)}"
    )


def test_no_provider_module_imports_an_sdk_at_module_level():
    """
    Every SDK import must be inside a function.

    A single top-level `import dropbox` would take the whole package down on a
    machine without it -- including the verification logic, which needs no SDK
    at all.
    """
    sdk_roots = {"googleapiclient", "google_auth_oauthlib", "dropbox", "google"}
    offenders = []
    for path in sorted(list(PROVIDERS_DIR.glob("*.py")) + list(WEBHOOKS_DIR.glob("*.py"))):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:  # top level only
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in sdk_roots:
                        offenders.append(f"{path.name}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.split(".")[0] in sdk_roots:
                    offenders.append(f"{path.name}: from {node.module} import ...")
    print("\n[LAZY IMPORTS] top-level SDK imports found:", offenders or "none")
    assert offenders == []


def test_sdk_missing_raises_at_call_time_not_import_time(drive, dropbox):
    with pytest.raises(ProviderSDKMissing) as exc:
        drive._require_sdk("googleapiclient.discovery", "google-api-python-client")
    print("\n[SDK MISSING]", exc.value)
    with pytest.raises(ProviderSDKMissing):
        dropbox._require_sdk("dropbox.files", "dropbox")


def test_authorization_urls_build_without_any_sdk(drive, dropbox):
    """The one part of OAuth with no network dependency stays testable."""
    g = drive.build_authorization_url("co-test", "https://cairoai.web.app/oauth/google")
    d = dropbox.build_authorization_url("co-test", "https://cairoai.web.app/oauth/dropbox")
    print("\n[AUTH URL] drive  :", g["url"][:120] + "...")
    print("[AUTH URL] dropbox:", d["url"][:120] + "...")

    assert "drive.file" in g["url"]
    assert "drive.readonly" not in g["url"]
    assert "access_type=offline" in g["url"]
    assert "prompt=consent" in g["url"]
    assert g["state"] and len(g["state"]) >= 20

    assert "token_access_type=offline" in d["url"]
    assert "files.metadata.read" in d["url"]
    assert "files.content.write" not in d["url"]


# =============================================================================
# 2. Scope discipline
# =============================================================================


def test_drive_scope_is_drive_file_only():
    """
    BUILD_STATE.md correction, re-asserted as a test.

    `drive.readonly` reads to the user as "See and download all your Google
    Drive files" and cannot be restricted to one folder. If someone widens
    this, the test says why before the consent screen does.
    """
    assert gd_mod.SCOPES == ("https://www.googleapis.com/auth/drive.file",)
    assert "https://www.googleapis.com/auth/drive.readonly" in gd_mod.FORBIDDEN_SCOPES
    assert "https://www.googleapis.com/auth/drive" in gd_mod.FORBIDDEN_SCOPES
    print("\n[SCOPE] drive  :", gd_mod.SCOPES)
    print("[SCOPE] refused:", list(gd_mod.FORBIDDEN_SCOPES))


def test_dropbox_scopes_are_read_only():
    assert dbx_mod.SCOPES == ("files.metadata.read", "files.content.read")
    for scope in dbx_mod.SCOPES:
        assert not scope.endswith(".write")
    assert "files.content.write" in dbx_mod.FORBIDDEN_SCOPES
    print("\n[SCOPE] dropbox:", dbx_mod.SCOPES)


def test_drive_channel_ttl_is_one_day_not_seven():
    assert gd_mod.DRIVE_CHANNEL_MAX_TTL_SECONDS == 86_400
    assert gd_mod.DRIVE_CHANNEL_DEFAULT_TTL_SECONDS == 3_600
    # Over-asking is clamped locally so the number we store is the number we
    # asked for, rather than a week that Drive silently shortened to a day.
    assert GoogleDriveProvider.clamp_ttl(7 * 86_400) == 86_400
    assert GoogleDriveProvider.clamp_ttl(0) == 3_600
    print("\n[TTL] max=86400 default=3600 clamp(7d)=", GoogleDriveProvider.clamp_ttl(7 * 86_400))


# =============================================================================
# 3. Google Drive webhook rejection matrix
# =============================================================================


def test_google_webhook_rejection_matrix(drive):
    print("\n=== GOOGLE DRIVE WEBHOOK -- REJECTION MATRIX ===")
    results = {}

    # -- missing required headers -------------------------------------
    make_channel("cai-a")
    v = drive.verify_webhook({}, b"")
    print(row("no headers at all", v))
    results["no_headers"] = v
    assert (v.accepted, v.http_status, v.reason) == (False, 400, "missing_required_headers")

    v = drive.verify_webhook(goog_headers("cai-a", channel_id=None), b"")
    print(row("missing X-Goog-Channel-ID", v))
    assert (v.accepted, v.http_status, v.reason) == (False, 400, "missing_required_headers")

    v = drive.verify_webhook(goog_headers("cai-a", resource_state=None), b"")
    print(row("missing X-Goog-Resource-State", v))
    assert (v.accepted, v.http_status, v.reason) == (False, 400, "missing_required_headers")

    # -- unknown channel ----------------------------------------------
    v = drive.verify_webhook(goog_headers("cai-does-not-exist"), b"")
    print(row("unknown channel id", v))
    results["unknown_channel"] = v
    assert (v.accepted, v.http_status, v.reason) == (False, 404, "unknown_channel")

    # -- channel token (the only authenticity signal Drive gives us) ---
    make_channel("cai-b")
    v = drive.verify_webhook(goog_headers("cai-b", channel_token=None), b"")
    print(row("missing X-Goog-Channel-Token", v))
    results["missing_token"] = v
    assert (v.accepted, v.http_status, v.reason) == (False, 403, "channel_token_mismatch")

    v = drive.verify_webhook(goog_headers("cai-b", channel_token="wrong-token"), b"")
    print(row("wrong X-Goog-Channel-Token", v))
    results["wrong_token"] = v
    assert (v.accepted, v.http_status, v.reason) == (False, 403, "channel_token_mismatch")

    # A near-miss: correct token with one character changed.
    v = drive.verify_webhook(
        goog_headers("cai-b", channel_token=CHANNEL_TOKEN[:-1] + "X"), b""
    )
    print(row("channel token off by one char", v))
    assert (v.accepted, v.http_status, v.reason) == (False, 403, "channel_token_mismatch")

    # -- resource id mismatch -----------------------------------------
    v = drive.verify_webhook(goog_headers("cai-b", resource_id="someone-elses-resource"), b"")
    print(row("resource id mismatch", v))
    assert (v.accepted, v.http_status, v.reason) == (False, 403, "resource_id_mismatch")

    # -- nonsense resource state --------------------------------------
    v = drive.verify_webhook(goog_headers("cai-b", resource_state="exfiltrate"), b"")
    print(row("invalid resource state", v))
    assert (v.accepted, v.http_status, v.reason) == (False, 400, "invalid_resource_state")

    # -- message number ------------------------------------------------
    v = drive.verify_webhook(goog_headers("cai-b", message_number="not-a-number"), b"")
    print(row("non-numeric message number", v))
    assert (v.accepted, v.http_status, v.reason) == (False, 400, "invalid_message_number")

    v = drive.verify_webhook(goog_headers("cai-b", message_number=None), b"")
    print(row("missing message number", v))
    assert (v.accepted, v.http_status, v.reason) == (False, 400, "missing_message_number")

    # -- expired channel ------------------------------------------------
    now = time.time()
    make_channel("cai-expired", ttl=-10, now=now)
    v = drive.verify_webhook(goog_headers("cai-expired"), b"", now=now)
    print(row("channel already expired", v))
    results["expired"] = v
    assert (v.accepted, v.http_status, v.reason) == (False, 410, "channel_expired")

    # A second delivery on the same channel now reads as 'not active', which
    # is a different operational story from 'never existed'.
    v = drive.verify_webhook(goog_headers("cai-expired"), b"", now=now)
    print(row("stopped/expired channel, retry", v))
    assert (v.accepted, v.http_status, v.reason) == (False, 410, "channel_not_active")

    print("=== END MATRIX ===")
    assert all(not v.accepted and not v.process for v in results.values())


def test_google_webhook_replay_and_stale_message_numbers(drive):
    print("\n=== GOOGLE DRIVE WEBHOOK -- REPLAY / STALE ===")
    make_channel("cai-replay")

    first = drive.verify_webhook(goog_headers("cai-replay", message_number="7"), b"")
    print(row("message #7 (first delivery)", first))
    assert (first.accepted, first.process, first.reason) == (True, True, "ok")

    replay = drive.verify_webhook(goog_headers("cai-replay", message_number="7"), b"")
    print(row("message #7 replayed verbatim", replay))
    assert (replay.accepted, replay.process, replay.reason) == (
        False,
        False,
        "stale_or_replayed_message",
    )
    # 200 on purpose: an error status makes Drive retry the duplicate we just
    # refused. The rejection is that nothing is enqueued.
    assert replay.http_status == 200

    stale = drive.verify_webhook(goog_headers("cai-replay", message_number="3"), b"")
    print(row("message #3 (older than #7)", stale))
    assert (stale.accepted, stale.process, stale.reason) == (
        False,
        False,
        "stale_or_replayed_message",
    )

    nxt = drive.verify_webhook(goog_headers("cai-replay", message_number="8"), b"")
    print(row("message #8 (genuinely new)", nxt))
    assert (nxt.accepted, nxt.process) == (True, True)
    print("=== END REPLAY ===")


def test_google_sync_handshake_is_authentic_but_does_no_work(drive):
    make_channel("cai-sync")
    v = drive.verify_webhook(goog_headers("cai-sync", resource_state="sync"), b"")
    print("\n[SYNC]", row("sync handshake", v).strip())
    assert v.accepted is True
    assert v.process is False
    assert v.reason == "sync_handshake"


def test_google_verification_ignores_the_body_entirely(drive):
    """
    Drive puts everything meaningful in headers, so the body never influences
    the decision. Proven by handing it a hostile one.
    """
    make_channel("cai-body")
    hostile = b'{"__proto__": {"admin": true}, "x": ' + b"9" * 10000 + b"}"
    v = drive.verify_webhook(goog_headers("cai-body", message_number="2"), hostile)
    print("\n[BODY IGNORED]", row("10KB hostile body, valid headers", v).strip())
    assert v.accepted and v.process


# =============================================================================
# 4. Dropbox webhook rejection matrix
# =============================================================================


def register_dropbox_account(account_id="dbid:AAAA-test-account", company_id="co-test"):
    channel_registry.save_cursor(account_id, company_id, "cursor-abc123")


def test_dropbox_webhook_rejection_matrix(dropbox):
    print("\n=== DROPBOX WEBHOOK -- REJECTION MATRIX ===")
    register_dropbox_account()
    body = dropbox_body()
    good = sign(body)

    v = dropbox.verify_webhook({}, body)
    print(row("no X-Dropbox-Signature header", v))
    assert (v.accepted, v.http_status, v.reason) == (False, 403, "missing_signature")

    v = dropbox.verify_webhook({"X-Dropbox-Signature": ""}, body)
    print(row("empty signature header", v))
    assert (v.accepted, v.http_status, v.reason) == (False, 403, "missing_signature")

    v = dropbox.verify_webhook({"X-Dropbox-Signature": "not-hex-at-all"}, body)
    print(row("signature is not 64 hex chars", v))
    assert (v.accepted, v.http_status, v.reason) == (False, 400, "malformed_signature")

    v = dropbox.verify_webhook({"X-Dropbox-Signature": "0" * 64}, body)
    print(row("well-formed but wrong signature", v))
    assert (v.accepted, v.http_status, v.reason) == (False, 403, "signature_mismatch")

    v = dropbox.verify_webhook(
        {"X-Dropbox-Signature": sign(body, "the-wrong-app-secret")}, body
    )
    print(row("signed with the wrong secret", v))
    assert (v.accepted, v.http_status, v.reason) == (False, 403, "signature_mismatch")

    # Valid signature for a DIFFERENT body: the classic "capture one delivery,
    # swap the payload" attack.
    other = dropbox_body(accounts=("dbid:attacker",))
    v = dropbox.verify_webhook({"X-Dropbox-Signature": good}, other)
    print(row("valid sig, tampered body", v))
    assert (v.accepted, v.http_status, v.reason) == (False, 403, "signature_mismatch")

    # Body mutated by a single trailing byte.
    v = dropbox.verify_webhook({"X-Dropbox-Signature": sign(body)}, body + b" ")
    print(row("body altered by one byte", v))
    assert (v.accepted, v.http_status, v.reason) == (False, 403, "signature_mismatch")

    # Correctly signed but not JSON.
    junk = b"\x00\x01not json at all"
    v = dropbox.verify_webhook({"X-Dropbox-Signature": sign(junk)}, junk)
    print(row("correctly signed, not JSON", v))
    assert (v.accepted, v.http_status, v.reason) == (False, 400, "malformed_body")

    # Correctly signed JSON with no accounts.
    empty = json.dumps({"list_folder": {"accounts": []}}).encode()
    v = dropbox.verify_webhook({"X-Dropbox-Signature": sign(empty)}, empty)
    print(row("correctly signed, no accounts", v))
    assert (v.accepted, v.http_status, v.reason) == (False, 400, "no_accounts_in_payload")

    # Correctly signed, for an account we have never seen. The Dropbox
    # equivalent of an unknown channel id.
    stranger = dropbox_body(accounts=("dbid:never-connected-to-us",))
    v = dropbox.verify_webhook({"X-Dropbox-Signature": sign(stranger)}, stranger)
    print(row("unknown account (unknown 'channel')", v))
    assert (v.accepted, v.reason) == (False, "unknown_account")
    assert v.http_status == 200  # legitimate notification, just not ours

    print("=== END MATRIX ===")


def test_dropbox_replay_is_suppressed(dropbox):
    print("\n=== DROPBOX WEBHOOK -- REPLAY ===")
    register_dropbox_account()
    body = dropbox_body()
    headers = {"X-Dropbox-Signature": sign(body)}

    first = dropbox.verify_webhook(headers, body)
    print(row("first delivery", first))
    assert (first.accepted, first.process, first.reason) == (True, True, "ok")

    replay = dropbox.verify_webhook(headers, body)
    print(row("byte-identical replay", replay))
    assert (replay.accepted, replay.process, replay.reason) == (
        False,
        False,
        "replayed_delivery",
    )
    assert replay.http_status == 200

    # Outside the replay window the same delivery is fresh again.
    later = dropbox.verify_webhook(
        headers, body, now=time.time() + channel_registry.REPLAY_WINDOW_SECONDS + 60
    )
    print(row("same delivery, window expired", later))
    assert later.accepted and later.process
    print("=== END REPLAY ===")


def test_dropbox_signature_is_verified_before_the_body_is_parsed(dropbox, monkeypatch):
    """
    The ordering proof.

    A spy on `json.loads` records whether the parser ran. With a bad
    signature it must never be reached -- an unauthenticated body is
    attacker-controlled input and handing it to a parser first is the bug this
    ordering exists to prevent.
    """
    register_dropbox_account()
    calls: list[int] = []
    real_loads = json.loads

    def spy(*args, **kwargs):
        calls.append(1)
        return real_loads(*args, **kwargs)

    monkeypatch.setattr(dbx_mod.json, "loads", spy)

    body = dropbox_body()
    bad = dropbox.verify_webhook({"X-Dropbox-Signature": "0" * 64}, body)
    print("\n[ORDERING] bad signature -> reason=%s, json.loads calls=%d"
          % (bad.reason, len(calls)))
    assert bad.reason == "signature_mismatch"
    assert calls == [], "json.loads ran on an unauthenticated body"

    good = dropbox.verify_webhook({"X-Dropbox-Signature": sign(body)}, body)
    print("[ORDERING] good signature -> reason=%s, json.loads calls=%d"
          % (good.reason, len(calls)))
    assert good.accepted
    assert len(calls) == 1


def test_dropbox_challenge_response(dropbox):
    body, headers, status = dropbox.challenge_response("abc123challenge")
    print("\n[CHALLENGE]", status, headers, repr(body))
    assert (body, status) == ("abc123challenge", 200)
    assert headers["Content-Type"] == "text/plain"
    # Without nosniff a browser can sniff an attacker-chosen challenge as HTML
    # on this origin, and the endpoint is unauthenticated by definition.
    assert headers["X-Content-Type-Options"] == "nosniff"

    body, headers, status = dropbox.challenge_response(None)
    assert status == 400


def test_dropbox_hmac_uses_constant_time_comparison():
    source = (PROVIDERS_DIR / "dropbox_provider.py").read_text(encoding="utf-8")
    assert "hmac.compare_digest" in source
    assert not re.search(r"presented\s*==\s*expected", source)
    print("\n[TIMING] dropbox signature comparison uses hmac.compare_digest")


# =============================================================================
# 5. Token encryption
# =============================================================================


def test_token_encryption_round_trip():
    print("\n=== TOKEN ENCRYPTION ===")
    token = OAuthToken(
        provider="google_drive",
        access_token="ya29.a0AfB_FAKE_ACCESS_TOKEN_FOR_TESTS_0123456789",
        refresh_token="1//0gFAKE_REFRESH_TOKEN_FOR_TESTS_abcdefghij",
        expires_at=time.time() + 3600,
        scopes=("https://www.googleapis.com/auth/drive.file",),
    )
    meta = token_store.save_token("co-test", token)
    print("  save_token returned:", meta)
    assert meta["encrypted_at_rest"] is True

    loaded = token_store.load_token("co-test", "google_drive")
    assert loaded.reveal_access_token() == token.access_token
    assert loaded.reveal_refresh_token() == token.refresh_token
    assert loaded.scopes == token.scopes
    print("  round-trip: access and refresh tokens recovered intact")

    raw = provider_db.provider_db_path().read_bytes()
    assert token.access_token.encode() not in raw
    assert token.refresh_token.encode() not in raw
    assert b"gAAAAA" in raw  # Fernet's version byte, base64-encoded
    print(f"  on-disk db ({len(raw)} bytes): plaintext tokens absent, Fernet blob present")
    print("=== END ENCRYPTION ===")


def test_token_repr_and_public_dict_never_leak():
    token = OAuthToken(
        provider="dropbox",
        access_token="sl.FAKE_DROPBOX_ACCESS_TOKEN_0123456789abcdefghij",
        refresh_token="FAKE_DROPBOX_REFRESH_0123456789",
        scopes=("files.metadata.read",),
    )
    printed = f"{token!r} {token!s} {token}"
    print("\n[REPR]", repr(token))
    assert token.access_token not in printed
    assert token.refresh_token not in printed
    assert token_store.REDACTED in printed

    public = token.to_public_dict()
    print("[PUBLIC DICT]", public)
    assert public["access_token"] == token_store.REDACTED
    assert public["refresh_token"] == token_store.REDACTED
    assert token.access_token not in json.dumps(public)

    token_store.save_token("co-test", token)
    listed = token_store.list_connections("co-test")
    print("[LIST CONNECTIONS]", listed)
    assert token.access_token not in json.dumps(listed)


def test_missing_key_refuses_rather_than_inventing_one(monkeypatch):
    monkeypatch.delenv("AGENT_AUTOFILL_TOKEN_KEY", raising=False)
    with pytest.raises(token_store.TokenEncryptionKeyMissing) as exc:
        token_store.save_token("co-test", OAuthToken(provider="dropbox", access_token="x"))
    print("\n[NO KEY]", str(exc.value)[:90], "...")


def test_wrong_key_fails_closed(monkeypatch):
    token_store.save_token(
        "co-test", OAuthToken(provider="dropbox", access_token="sl.secret-value-here")
    )
    monkeypatch.setenv("AGENT_AUTOFILL_TOKEN_KEY", Fernet.generate_key().decode())
    with pytest.raises(token_store.TokenDecryptionFailed) as exc:
        token_store.load_token("co-test", "dropbox")
    print("\n[WRONG KEY]", exc.value)
    assert "secret-value-here" not in str(exc.value)


def test_key_rotation(monkeypatch):
    old_key = Fernet.generate_key().decode()
    monkeypatch.setenv("AGENT_AUTOFILL_TOKEN_KEY", old_key)
    token_store.save_token(
        "co-test", OAuthToken(provider="dropbox", access_token="sl.rotate-me")
    )

    new_key = Fernet.generate_key().decode()
    monkeypatch.setenv("AGENT_AUTOFILL_TOKEN_KEY", new_key)
    monkeypatch.setenv("AGENT_AUTOFILL_TOKEN_KEY_PREVIOUS", old_key)
    result = token_store.reencrypt_all()
    print("\n[ROTATION]", result)
    assert result == {"rotated": 1, "failed": 0}

    monkeypatch.delenv("AGENT_AUTOFILL_TOKEN_KEY_PREVIOUS")
    assert token_store.load_token("co-test", "dropbox").reveal_access_token() == "sl.rotate-me"


def test_log_redaction_filter_scrubs_token_shapes(caplog):
    """
    Second line of defence, for secrets that arrive inside strings this
    package did not build -- an SDK error that quotes the request back, say.
    """
    logger = logging.getLogger("agent_autofill.providers")
    token_store.install_redaction_filter(logger)
    samples = [
        "refresh failed for ya29.a0AfB_LEAKED_ACCESS_TOKEN_abcdefghij",
        "stored 1//0gLEAKED_REFRESH_TOKEN_abcdefghij for company",
        "Authorization: Bearer sl.LEAKED_DROPBOX_TOKEN_abcdefghijklmnop",
    ]
    print("\n=== LOG REDACTION ===")
    for sample in samples:
        record = logging.LogRecord("agent_autofill.providers", logging.INFO,
                                   __file__, 0, sample, None, None)
        for f in logger.filters:
            f.filter(record)
        print(f"  in : {sample}")
        print(f"  out: {record.getMessage()}")
        assert "LEAKED" not in record.getMessage()
        assert token_store.REDACTED in record.getMessage()
    print("=== END REDACTION ===")


# =============================================================================
# 6. No token in any log statement or any browser-servable path
# =============================================================================

SENSITIVE_IDENTIFIERS = {
    "access_token",
    "refresh_token",
    "token",
    "channel_token",
    "app_secret",
    "client_secret",
    "secret",
    "app_key",
    "ciphertext",
    "token_ciphertext",
    "raw_body",
    "body",
    "signature",
    "presented",
    "expected",
    "credentials",
    "creds",
    "payload",
    "key",
}

LOG_METHODS = {"debug", "info", "warning", "warn", "error", "exception", "critical", "log"}


def _dotted(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


def _sensitive_refs(call: ast.Call):
    """Maximal dotted names inside a call whose final component is sensitive."""
    found: list[str] = []

    def visit(node):
        if isinstance(node, (ast.Name, ast.Attribute)):
            dotted = _dotted(node)
            if dotted is not None:
                if dotted.split(".")[-1] in SENSITIVE_IDENTIFIERS:
                    found.append(dotted)
                return
        for child in ast.iter_child_nodes(node):
            visit(child)

    for arg in list(call.args) + [kw.value for kw in call.keywords]:
        visit(arg)
    return found


def test_no_sensitive_value_is_passed_to_a_logger():
    print("\n=== LOG-CALL AST SCAN ===")
    scanned = 0
    log_calls = 0
    offenders = []
    for path in sorted(list(PROVIDERS_DIR.glob("*.py")) + list(WEBHOOKS_DIR.glob("*.py"))):
        scanned += 1
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in LOG_METHODS:
                continue
            base = _dotted(node.func.value) or ""
            if not (base.endswith("logger") or base.endswith("log") or base.endswith("logging")):
                continue
            log_calls += 1
            refs = _sensitive_refs(node)
            if refs:
                offenders.append(f"{path.name}:{node.lineno} -> {refs}")
    print(f"  files scanned : {scanned}")
    print(f"  log calls found: {log_calls}")
    print(f"  offenders      : {offenders or 'none'}")
    print("=== END SCAN ===")
    assert log_calls > 0, "scan found no log calls -- the check would be vacuous"
    assert offenders == []


def test_no_token_material_under_static_or_firebase_public():
    print("\n=== SERVABLE-PATH SCAN ===")
    patterns = {
        "google_access_token": re.compile(r"ya29\.[A-Za-z0-9_\-\.]{10,}"),
        "google_refresh_token": re.compile(r"1//[A-Za-z0-9_\-]{10,}"),
        "dropbox_token": re.compile(r"\bsl\.[A-Za-z0-9_\-]{20,}"),
        "fernet_ciphertext": re.compile(r"\bgAAAAA[A-Za-z0-9_\-=]{10,}"),
        "bearer_header": re.compile(r"(?i)\bbearer\s+[A-Za-z0-9_\-\.=]{10,}"),
        "jwt": re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),
        "literal_access_token": re.compile(r"access_token"),
        "literal_refresh_token": re.compile(r"refresh_token"),
        "provider_db_filename": re.compile(r"agent_autofill_providers\.db"),
        "token_key_env": re.compile(r"AGENT_AUTOFILL_TOKEN_KEY"),
        "token_table": re.compile(r"provider_tokens"),
    }
    hits = []
    total = 0
    for directory in ("static", "firebase_public"):
        root = REPO_ROOT / directory
        assert root.is_dir(), f"{directory}/ is missing -- scan would be vacuous"
        count = 0
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            count += 1
            text = path.read_text(encoding="utf-8", errors="ignore")
            for name, pattern in patterns.items():
                if pattern.search(text):
                    hits.append(f"{path.relative_to(REPO_ROOT)} [{name}]")
        total += count
        print(f"  {directory}/: {count} files scanned")
    print(f"  total files    : {total}")
    print(f"  hits           : {hits or 'none'}")
    print("=== END SCAN ===")
    assert total > 0
    assert hits == []


def test_provider_storage_refuses_a_servable_location(monkeypatch, tmp_path):
    """The guard is the thing that keeps the scan above true in future."""
    print("\n=== STORAGE LOCATION GUARD ===")
    real = provider_db.provider_db_path()
    print("  resolved db path:", real)
    assert "static" not in {p.lower() for p in real.parts}
    assert "firebase_public" not in {p.lower() for p in real.parts}

    for bad in (
        REPO_ROOT / "static" / "downloads" / "providers.db",
        REPO_ROOT / "firebase_public" / "providers.db",
    ):
        monkeypatch.setenv("AGENT_AUTOFILL_PROVIDER_DB", str(bad))
        with pytest.raises(provider_db.UnsafeStorageLocation) as exc:
            provider_db.provider_db_path()
        print(f"  refused: {bad.relative_to(REPO_ROOT)} -> {exc.value}")

    monkeypatch.setenv("AGENT_AUTOFILL_DOWNLOAD_DIR", str(REPO_ROOT / "static" / "downloads"))
    with pytest.raises(provider_db.UnsafeStorageLocation):
        provider_db.provider_download_dir()
    print("  refused: static/downloads as a provider download dir")
    print("=== END GUARD ===")


def test_downloaded_filenames_cannot_escape(tmp_path):
    dest = tmp_path / "dl"
    for hostile in ("../../etc/passwd", "..\\..\\windows\\system32\\x.dll",
                    "/absolute/evil.pdf", "MBD 1 — supplier info.pdf"):
        resolved = safe_download_path(hostile, dest)
        assert resolved.parent == dest.resolve()
        print(f"\n[DOWNLOAD NAME] {hostile!r} -> {resolved.name!r}")
    for empty in ("", "...", "/"):
        with pytest.raises(ValueError):
            safe_download_path(empty, dest)


# =============================================================================
# 7. Renewal arithmetic
# =============================================================================

TTL = 86_400
SIX_HOURS = 21_600
TWELVE_HOURS = 43_200
ONE_DAY = 86_400


def test_configured_cadence_is_sound():
    v = cron.assess_cadence()
    print("\n=== CONFIGURED CADENCE (6h interval / 12h threshold / 24h TTL) ===")
    for key, value in v.items():
        print(f"  {key:<34} {value}")
    print("=== END ===")
    assert v["sufficient"] is True
    assert v["guaranteed_attempts"] == 2
    assert v["survivable_consecutive_failures"] == 1
    assert v["guaranteed_overlap_seconds"] == SIX_HOURS
    assert v["threshold_is_degenerate"] is False


def test_daily_cadence_is_provably_insufficient():
    v = cron.assess_cadence(ttl=TTL, interval=ONE_DAY, threshold=ONE_DAY)
    print("\n=== DAILY CADENCE (24h interval / 24h threshold / 24h TTL) ===")
    for key, value in v.items():
        print(f"  {key:<34} {value}")
    print("=== END ===")
    assert v["sufficient"] is False
    assert v["guaranteed_attempts"] == 1
    assert v["survivable_consecutive_failures"] == 0
    assert v["guaranteed_overlap_seconds"] == 0
    assert v["threshold_is_degenerate"] is True
    with pytest.raises(ValueError):
        cron.assert_cadence_is_sound(ttl=TTL, interval=ONE_DAY, threshold=ONE_DAY)


def test_cadence_comparison_table():
    print("\n=== CADENCE COMPARISON (Drive TTL = 86,400s) ===")
    print(f"  {'interval':>9} {'threshold':>10} {'attempts':>9} {'survivable':>11} "
          f"{'overlap':>9} {'degenerate':>11} {'ok':>6}")
    rows = [
        (ONE_DAY, ONE_DAY),
        (TWELVE_HOURS, TWELVE_HOURS),
        (TWELVE_HOURS, ONE_DAY),
        (SIX_HOURS, TWELVE_HOURS),
        (SIX_HOURS, SIX_HOURS),
        (3_600, TWELVE_HOURS),
    ]
    for interval, threshold in rows:
        v = cron.assess_cadence(ttl=TTL, interval=interval, threshold=threshold)
        print(f"  {interval:>9} {threshold:>10} {v['guaranteed_attempts']:>9} "
              f"{v['survivable_consecutive_failures']:>11} "
              f"{int(v['guaranteed_overlap_seconds']):>9} "
              f"{str(v['threshold_is_degenerate']):>11} {str(v['sufficient']):>6}")
    print("=== END TABLE ===")
    assert cron.assess_cadence(ttl=TTL, interval=SIX_HOURS, threshold=TWELVE_HOURS)["sufficient"]
    assert not cron.assess_cadence(ttl=TTL, interval=ONE_DAY, threshold=ONE_DAY)["sufficient"]
    assert not cron.assess_cadence(ttl=TTL, interval=SIX_HOURS, threshold=SIX_HOURS)["sufficient"]


def test_six_hourly_cron_renews_every_channel_before_expiry_at_every_phase():
    """
    A channel is created at an arbitrary moment relative to the cron schedule.
    Sweep every phase and check the invariant holds for all of them, rather
    than for the one convenient case.
    """
    print("\n=== PHASE SWEEP -- 6h cron, 12h threshold, 24h TTL ===")
    overlaps = []
    for phase in range(0, SIX_HOURS, 600):
        sim = cron.simulate_renewal(
            created_at=float(phase),
            ttl_seconds=TTL,
            interval_seconds=SIX_HOURS,
            threshold_seconds=TWELVE_HOURS,
            cron_epoch=0.0,
        )
        assert sim["renewed_before_expiry"], f"phase {phase} lost the channel"
        assert sim["renewed_at"] < sim["expiry"]
        assert sim["overlap_seconds"] >= SIX_HOURS, (
            f"phase {phase}: overlap {sim['overlap_seconds']}s below the 6h floor"
        )
        overlaps.append(sim["overlap_seconds"])
    print(f"  phases tested        : {len(overlaps)} (every 600s across a 6h period)")
    print(f"  renewed before expiry: {len(overlaps)}/{len(overlaps)}")
    print(f"  overlap min/max      : {int(min(overlaps))}s / {int(max(overlaps))}s")
    print(f"  6h floor             : {SIX_HOURS}s")
    print("=== END SWEEP ===")
    assert min(overlaps) >= SIX_HOURS


def test_six_hourly_cron_survives_one_missed_run_but_daily_does_not():
    print("\n=== ONE MISSED RUN ===")
    six_ok = 0
    daily_ok = 0
    phases = list(range(0, ONE_DAY, 1800))
    for phase in phases:
        s6 = cron.simulate_renewal(
            created_at=float(phase), ttl_seconds=TTL,
            interval_seconds=SIX_HOURS, threshold_seconds=TWELVE_HOURS,
            cron_epoch=0.0, failing_runs=1,
        )
        s24 = cron.simulate_renewal(
            created_at=float(phase), ttl_seconds=TTL,
            interval_seconds=ONE_DAY, threshold_seconds=ONE_DAY,
            cron_epoch=0.0, failing_runs=1,
        )
        six_ok += bool(s6["renewed_before_expiry"])
        daily_ok += bool(s24["renewed_before_expiry"])
    print(f"  phases tested                 : {len(phases)}")
    print(f"  6h  cron, 1 failed run -> kept: {six_ok}/{len(phases)}")
    print(f"  24h cron, 1 failed run -> kept: {daily_ok}/{len(phases)}")
    print("=== END ===")
    assert six_ok == len(phases)
    assert daily_ok == 0, "a daily cron must not survive a single missed run"


def test_daily_cron_hands_over_with_almost_no_overlap():
    print("\n=== DAILY CADENCE OVERLAP ===")
    worst = None
    for phase in range(0, ONE_DAY, 600):
        sim = cron.simulate_renewal(
            created_at=float(phase), ttl_seconds=TTL,
            interval_seconds=ONE_DAY, threshold_seconds=ONE_DAY, cron_epoch=0.0,
        )
        if sim["renewed_before_expiry"]:
            if worst is None or sim["overlap_seconds"] < worst[1]:
                worst = (phase, sim["overlap_seconds"])
    print(f"  worst-case phase     : {worst[0]}s after the cron tick")
    print(f"  overlap at that phase: {int(worst[1])}s")
    print(f"  6h cadence floor     : {SIX_HOURS}s")
    print("=== END ===")
    assert worst[1] < 3600, "daily cadence should have a near-zero worst-case overlap"


def test_renewal_cycle_selects_only_channels_inside_the_threshold():
    print("\n=== RENEWAL CYCLE SELECTION ===")
    now = 1_000_000.0
    make_channel("cai-fresh", ttl=TTL, now=now)                    # 24h left
    make_channel("cai-half", ttl=TWELVE_HOURS, now=now)            # 12h left
    make_channel("cai-soon", ttl=3_600, now=now)                   # 1h left
    make_channel("cai-dead", ttl=-600, now=now)                    # expired

    due = channel_registry.channels_due_for_renewal(
        now=now, threshold_seconds=TWELVE_HOURS, provider="google_drive"
    )
    ids = sorted(c.channel_id for c in due)
    for c in sorted(due, key=lambda c: c.expiration_at):
        print(f"  due: {c.channel_id:<12} remaining={int(c.seconds_remaining(now)):>7}s")
    print("  not due: cai-fresh (24h remaining, outside the 12h threshold)")
    assert ids == ["cai-dead", "cai-half", "cai-soon"]

    renewed: list[str] = []

    class FakeProvider:
        def renew_webhook(self, channel_id, now=None):
            renewed.append(channel_id)
            new_id = channel_id + "-new"
            return channel_registry.register_channel(
                channel_id=new_id, provider="google_drive", company_id="co-test",
                channel_token="fresh-token", callback_url="https://x/y",
                expiration_at=(now or 0) + TTL, resource_id="resource-abc",
                watched_file_id="folder-1", created_at=now or 0,
            )

    report = cron.run_renewal_cycle(provider_factory=lambda _: FakeProvider(), now=now)
    print(f"  report: due={report['due']} renewed={report['renewed']} "
          f"failed={report['failed']} already_expired={report['already_expired']}")
    print("=== END ===")
    assert report["due"] == 3
    assert report["renewed"] == 3
    assert report["already_expired"] == 1
    assert sorted(renewed) == ["cai-dead", "cai-half", "cai-soon"]


def test_one_failing_channel_does_not_abort_the_cycle():
    now = 1_000_000.0
    make_channel("cai-ok", ttl=3_600, now=now)
    make_channel("cai-broken", ttl=3_600, now=now)

    class FlakyProvider:
        def renew_webhook(self, channel_id, now=None):
            if channel_id == "cai-broken":
                raise RuntimeError("token revoked by the user")
            return channel_registry.register_channel(
                channel_id=channel_id + "-new", provider="google_drive",
                company_id="co-test", channel_token="fresh", callback_url="https://x/y",
                expiration_at=(now or 0) + TTL, created_at=now or 0,
            )

    report = cron.run_renewal_cycle(provider_factory=lambda _: FlakyProvider(), now=now)
    print(f"\n[PARTIAL FAILURE] due={report['due']} renewed={report['renewed']} "
          f"failed={report['failed']}")
    for entry in report["details"]:
        print("  ", {k: v for k, v in entry.items() if k != "new_expiration_at"})
    assert report["renewed"] == 1
    assert report["failed"] == 1


def test_dropbox_channels_are_never_selected_for_renewal():
    """Dropbox registrations live in the App Console and do not expire."""
    channel_registry.register_channel(
        channel_id="dropbox:dbid:x", provider="dropbox", company_id="co-test",
        channel_token="n/a", callback_url="", expiration_at=float("inf"),
        created_at=time.time(),
    )
    due = channel_registry.channels_due_for_renewal(
        now=time.time(), threshold_seconds=TWELVE_HOURS
    )
    print("\n[DROPBOX RENEWAL] channels due:", [c.channel_id for c in due])
    assert due == []


# =============================================================================
# 8. Fast acknowledge, asynchronous processing
# =============================================================================


def test_receivers_do_no_extraction_or_model_work():
    """
    The router must not be able to run extraction inline, so it must not
    import anything that could.
    """
    tree = ast.parse((WEBHOOKS_DIR / "routes.py").read_text(encoding="utf-8"))
    forbidden = ("agent_autofill.extraction", "agent_autofill.fill_engine",
                 "agent.main_agent", "agent.claude_client", "anthropic")
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    offenders = [m for m in imported if any(m.startswith(f) for f in forbidden)]
    print("\n[FAST ACK] routes.py imports:", sorted(set(imported)))
    print("[FAST ACK] forbidden imports :", offenders or "none")
    assert offenders == []


def test_queue_hands_work_off_and_never_blocks_the_handler():
    from agent_autofill.webhooks.async_queue import ThreadedTaskQueue, WebhookTask

    seen: list[str] = []
    q = ThreadedTaskQueue(handler=lambda t: seen.append(t.provider), max_queue=4, workers=1)
    for i in range(4):
        assert q.submit(WebhookTask(provider="google_drive", company_id="co", reason="ok"))
    q.drain(timeout=5)
    print("\n[QUEUE]", q.stats())
    assert len(seen) == 4

    # A full queue drops rather than blocking the HTTP handler.
    blocked = ThreadedTaskQueue(handler=lambda t: time.sleep(5), max_queue=1, workers=0)
    blocked._started = True  # no workers, so nothing drains it
    assert blocked.submit(WebhookTask(provider="dropbox", company_id="co", reason="ok"))
    assert not blocked.submit(WebhookTask(provider="dropbox", company_id="co", reason="ok"))
    print("[QUEUE] full-queue behaviour:", blocked.stats())
    assert blocked.dropped == 1


def test_webhook_task_carries_no_secrets():
    from agent_autofill.webhooks.async_queue import WebhookTask

    task = WebhookTask(provider="google_drive", company_id="co", reason="ok",
                       channel_id="cai-1", resource_state="update")
    fields = set(task.__dataclass_fields__)
    print("\n[TASK FIELDS]", sorted(fields))
    assert not (fields & SENSITIVE_IDENTIFIERS)


def test_in_process_dispatcher_is_reported_as_unsafe_on_serverless():
    ready = async_queue.deployment_readiness(env={})
    print("\n[READINESS local]", ready)
    assert ready["ready"] is True

    serverless = async_queue.deployment_readiness(env={"K_SERVICE": "api"})
    print("[READINESS cloud]", serverless["ready"], "--", serverless["warning"][:80], "...")
    assert serverless["ready"] is False
    assert "Cloud Tasks" in serverless["warning"]


# =============================================================================
# 9. Interface conformance
# =============================================================================


def test_both_providers_implement_the_full_interface():
    from agent_autofill.providers.base_provider import BaseCloudProvider

    required = [
        "build_authorization_url", "connect", "register_webhook",
        "renew_webhook", "verify_webhook", "list_changed_files", "download_file",
    ]
    for cls in (GoogleDriveProvider, DropboxProvider):
        assert issubclass(cls, BaseCloudProvider)
        for method in required:
            assert callable(getattr(cls, method)), f"{cls.__name__}.{method}"
            assert getattr(cls, method) is not getattr(BaseCloudProvider, method), (
                f"{cls.__name__}.{method} is still the abstract stub"
            )
    print("\n[INTERFACE] both providers implement:", required)


def test_every_module_declares_unverified():
    """
    The honesty check.

    This work cannot be verified here, and the code has to say so where
    someone will read it. Failing this test is the correct outcome if the
    marker is ever quietly removed without the checklist having been run.
    """
    required = [
        PROVIDERS_DIR / "__init__.py",
        PROVIDERS_DIR / "base_provider.py",
        PROVIDERS_DIR / "google_drive_provider.py",
        PROVIDERS_DIR / "dropbox_provider.py",
        WEBHOOKS_DIR / "__init__.py",
        WEBHOOKS_DIR / "channel_renewal_cron.py",
    ]
    for path in required:
        text = path.read_text(encoding="utf-8")
        doc = ast.get_docstring(ast.parse(text)) or ""
        assert "UNVERIFIED" in doc, f"{path.name} does not declare UNVERIFIED"
    assert (PROVIDERS_DIR / "VERIFICATION.md").is_file()
    print("\n[UNVERIFIED] declared in:", [p.name for p in required])
