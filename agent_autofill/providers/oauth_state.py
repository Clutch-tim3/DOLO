"""
The CSRF state for an OAuth authorization round trip.

Both providers already generate a `state` value and both docstrings say the
caller must store it against the user's session. There was no caller. Nothing
mounted the flow, so nothing stored anything, and the parameter that exists to
stop cross-site request forgery was decorative.

WHAT THE STATE ACTUALLY DEFENDS
-------------------------------
Two different attacks, and the second is the one people forget:

1. A forged callback. An attacker sends the victim to our callback URL with an
   authorization code of the attacker's choosing. Without state, we exchange it
   and attach the ATTACKER'S Drive account to the victim's company — so
   documents the victim later "connects" are read from, and written by, an
   account the attacker controls.

2. A swapped company. If the callback took `company_id` from its own URL, an
   attacker who completed a legitimate consent for their own Google account
   could replay the callback with a different company_id and attach their
   account to someone else's company. So the company is read from the STORED
   state, never from the callback's parameters. The callback URL carries no
   authority at all beyond proving the round trip is ours.

Records are single-use and short-lived. Consumption is a conditional DELETE
returning the row, so two concurrent callbacks with the same state cannot both
succeed — the same shape as the export gate's conditional UPDATE, for the same
reason: a check followed by an action has a window between them.

The value is stored as a SHA-256 digest rather than in the clear. It behaves as
a bearer token for the duration of the flow, and a database dump should not
hand someone a live one.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from agent import db
from agent_autofill.providers import pkce
from agent_autofill.providers.provider_db import provider_db_path

#: How long a user has to complete consent. Long enough to read a Google
#: consent screen and pick an account; short enough that an abandoned flow does
#: not leave a usable token lying around.
STATE_TTL_SECONDS = 10 * 60


class OAuthStateError(Exception):
    """The callback could not be matched to a flow we started."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _digest(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def init_state_db() -> None:
    with db.connect(provider_db_path()) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS provider_oauth_state (
                state_digest TEXT PRIMARY KEY,
                company_id   TEXT NOT NULL,
                provider     TEXT NOT NULL,
                redirect_uri TEXT NOT NULL,
                created_at   TIMESTAMP NOT NULL,
                expires_at   TIMESTAMP NOT NULL
            )
        """)
        # The PKCE verifier. Kept server-side for the life of the flow: the
        # browser carries only the challenge, which is a hash of this.
        if "code_verifier" not in db.table_columns(conn, "provider_oauth_state"):
            conn.execute(
                "ALTER TABLE provider_oauth_state ADD COLUMN code_verifier TEXT")


init_state_db()


def issue(company_id: str, provider: str, redirect_uri: str) -> dict:
    """
    Start a flow. Returns the state and the PKCE challenge for the URL.

    The caller keeps nothing: everything needed to finish the flow is recorded
    here, keyed by a value only the legitimate round trip can present. The
    verifier stays here too — that is what makes an intercepted authorization
    code useless.
    """
    if not company_id:
        raise OAuthStateError("A company_id is required to start a connection.")
    if not redirect_uri:
        raise OAuthStateError("A redirect_uri is required to start a connection.")

    value = secrets.token_urlsafe(32)
    verifier = pkce.new_verifier()
    now = _now()
    with db.connect(provider_db_path()) as conn:
        conn.execute(
            """INSERT INTO provider_oauth_state
               (state_digest, company_id, provider, redirect_uri, created_at,
                expires_at, code_verifier)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (_digest(value), company_id, provider, redirect_uri,
             now.isoformat(), (now + timedelta(seconds=STATE_TTL_SECONDS)).isoformat(),
             verifier),
        )
    return {
        "state": value,
        "code_challenge": pkce.challenge_for(verifier),
        "code_challenge_method": pkce.METHOD,
    }


def consume(state: str, provider: str) -> dict:
    """
    Finish a flow. Returns the company and redirect_uri that STARTED it.

    Single use: the row is deleted as part of the same statement that reads it,
    so a replayed callback finds nothing. Raises rather than returning None —
    every failure here means "do not exchange this code", and a caller that
    treated a falsy return as "carry on" would be exchanging it.
    """
    if not state:
        raise OAuthStateError("Missing state parameter.")

    digest = _digest(state)

    # Read and delete in one transaction, and let it COMMIT before any check
    # that can raise. Doing the validation inside the block looked equivalent
    # and was not: the connection rolls back on exception, so raising for a
    # wrong provider or an expired record silently undid the delete and left
    # the state usable again. A test caught it; inspection did not.
    with db.connect(provider_db_path()) as conn:
        row = conn.execute(
            """SELECT company_id, provider, redirect_uri, expires_at, code_verifier
                 FROM provider_oauth_state WHERE state_digest = ?""",
            (digest,),
        ).fetchone()
        if row is not None:
            conn.execute(
                "DELETE FROM provider_oauth_state WHERE state_digest = ?", (digest,))
            captured = {
                "company_id": row["company_id"],
                "provider": row["provider"],
                "redirect_uri": row["redirect_uri"],
                "expires_at": row["expires_at"],
                "code_verifier": row["code_verifier"],
            }
        else:
            captured = None

    # Burned either way by this point, so a mismatched attempt cannot be probed
    # and then retried against the right provider.
    if captured is None:
        raise OAuthStateError("Unknown or already-used state.")
    if captured["provider"] != provider:
        raise OAuthStateError("State does not belong to this provider.")
    if _parse(captured["expires_at"]) < _now():
        raise OAuthStateError("This connection attempt expired; start again.")

    return {
        "company_id": captured["company_id"],
        "provider": captured["provider"],
        "redirect_uri": captured["redirect_uri"],
        "code_verifier": captured["code_verifier"],
    }


def _parse(value) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def purge_expired() -> int:
    """Housekeeping. Expired rows are already unusable; this stops them piling up."""
    with db.connect(provider_db_path()) as conn:
        cur = conn.execute(
            "DELETE FROM provider_oauth_state WHERE expires_at < ?",
            (_now().isoformat(),),
        )
        return cur.rowcount or 0
