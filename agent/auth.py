"""
Who is making this request.

WHY THIS EXISTS
---------------
Until now there was no authentication anywhere in this application. Every
company-aware route resolved its tenant like this:

    company_id = request.headers.get("X-Company-ID", "starter_corp")

A header any client can set, with a default for the ones that do not bother.
Proven against the live site with no credentials at all: sending
`X-Company-ID: enterprise_corp` returned that company's profile with HTTP 200.

Everything built on top of that value is real work — tenant pinning in
`agent/tool_dispatch.py`, the export gate's company checks, the quotation
finalize ownership check, the OAuth state binding. All of it pins to an
identity that was never established. This module establishes it. It is the
first authentication in this codebase; there was nothing here to reuse and
nothing here is a wrapper over something older.

WHY A SESSION TABLE RATHER THAN FIREBASE AUTH
---------------------------------------------
Firebase Auth was evaluated honestly and rejected, for four reasons that are
about this application rather than about Firebase:

1. **The desktop client needs a long-lived, revocable, company-scoped device
   credential.** Firebase Auth has no such object. ID tokens live an hour and
   refresh tokens are held and rotated by a client SDK that a headless process
   would have to emulate; revoking one device means revoking refresh tokens for
   the whole user. A device-token table has to be built either way.

2. **A Firebase UID is not a company.** The mapping from principal to
   `company_id` — the value every gate in this codebase pins to — is ours, and
   needs a table of ours. Having built that table, the session row beside it is
   three more columns.

3. **The test suite and local development would need an emulator.**
   `verify_id_token` reaches Google for signing certificates and wants real
   project credentials. `app.py` runs standalone locally (`initialize_app()`
   lives in `main.py`, which the local server never imports). Auth that behaves
   differently in tests than in production is auth that is not really tested.

4. **State is already solved here.** `agent/db.py` gives SQLite locally and
   Cloud SQL in production from one call, which is exactly what a session store
   needs, and it survives the instance restart that `/tmp` does not.

What Firebase Auth would have given us and this does not: password reset and
email verification flows, MFA, and social login. Reset and verification both
need email, which this project does not have (see the note on provisioning
below), so they were not available to us in either design. MFA is a real loss
and is recorded as such.

HOW A CREDENTIAL IS STORED
--------------------------
Nothing here stores a credential in the clear.

* Passwords: PBKDF2-HMAC-SHA256, per-user random salt, iteration count stored
  alongside so it can be raised later without invalidating existing users.
* Tokens (session, device, pairing): issued as `<prefix><selector>.<verifier>`.
  The selector is stored in the clear and is only an index; the verifier is
  stored as a SHA-256 digest. Lookup is by selector, and the verifier is then
  compared with `hmac.compare_digest`. The split is what makes the comparison
  genuinely constant time: a bare `WHERE token_hash = ?` leaks timing through
  the index, and a table scan comparing every row does not scale.

A database dump therefore yields no usable session, no usable device token and
no password.

PROVISIONING
------------
There is deliberately no self-service signup route. Signup without email
verification lets anyone create an account naming any `company_id`, which is
the same hole this module exists to close, wearing a different hat. Accounts
are created by an operator with `scripts/manage_users.py`. That is a real
limitation and it is written down rather than worked around.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, Request

from agent import db
from agent.db_paths import AGENT_MEMORY_DB as DB_PATH

# --- tunables ---------------------------------------------------------------

#: OWASP's 2023 floor for PBKDF2-HMAC-SHA256. Stored per-row, so raising this
#: later re-hashes users as they log in rather than locking them out.
PBKDF2_ITERATIONS = 600_000
_SALT_BYTES = 16

#: The browser session cookie. HttpOnly so script cannot read it, SameSite=Lax
#: so a cross-site POST does not carry it, Secure on anything that is not
#: localhost (where Secure would make the browser drop it entirely).
#:
#: THE NAME IS NOT A CHOICE. Firebase Hosting strips every cookie except
#: `__session` from requests it forwards to a function — it is what makes the
#: CDN able to cache anything at all. Any other name is set in the browser
#: perfectly happily and then silently dropped on the way back in.
#:
#: This was `cairoai_session`, and the result was a sign-in that appeared to
#: work and then asked again, forever: POST /api/auth/login returned 200 and a
#: Set-Cookie the browser stored, the very next GET /api/auth/me arrived at the
#: function with no cookie at all, whoami() returned null, and the overlay came
#: straight back. Through the site it failed every time; against the Cloud Run
#: URL directly — which is how it was tested — it passed every time, because
#: that path has no Hosting in front of it.
#:
#: Do not "tidy" this back to a branded name.
SESSION_COOKIE = "__session"

#: Absolute lifetime of a browser session, extended on use (see `_touch`).
SESSION_TTL_SECONDS = 12 * 60 * 60
#: Only rewrite the expiry when less than this much of the window remains, so a
#: busy tab does not write a row on every request.
SESSION_REFRESH_AFTER_SECONDS = 60 * 60

#: A pairing code is read off a screen and typed into a terminal. Short-lived
#: on purpose: it is a bearer credential for as long as it exists.
PAIRING_TTL_SECONDS = 10 * 60

#: Device tokens are long-lived by design — a headless client cannot complete an
#: interactive login. `None` means "until revoked".
DEVICE_TOKEN_TTL_SECONDS: Optional[int] = None

#: Brute-force brake. Counted per username over a rolling window; a locked
#: account still answers with the same message as a wrong password, so the
#: lockout is not an oracle for which usernames exist.
LOGIN_MAX_FAILURES = 10
LOGIN_FAILURE_WINDOW_SECONDS = 15 * 60

_SESSION_PREFIX = "cases_"
_DEVICE_PREFIX = "cadev_"
_PAIRING_PREFIX = "capair_"


class AuthError(Exception):
    """A credential was presented and is not good."""


# --- schema -----------------------------------------------------------------


def init_auth_db() -> None:
    with db.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS auth_users (
                user_id       TEXT PRIMARY KEY,
                username      TEXT NOT NULL UNIQUE,
                company_id    TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                created_at    TEXT NOT NULL,
                disabled      INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS auth_sessions (
                selector      TEXT PRIMARY KEY,
                verifier_hash TEXT NOT NULL,
                user_id       TEXT NOT NULL,
                company_id    TEXT NOT NULL,
                created_at    TEXT NOT NULL,
                expires_at    TEXT NOT NULL,
                last_seen_at  TEXT,
                revoked_at    TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS auth_device_tokens (
                selector      TEXT PRIMARY KEY,
                verifier_hash TEXT NOT NULL,
                company_id    TEXT NOT NULL,
                user_id       TEXT NOT NULL,
                label         TEXT NOT NULL,
                created_at    TEXT NOT NULL,
                expires_at    TEXT,
                last_used_at  TEXT,
                revoked_at    TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS auth_pairing_codes (
                selector      TEXT PRIMARY KEY,
                verifier_hash TEXT NOT NULL,
                company_id    TEXT NOT NULL,
                user_id       TEXT NOT NULL,
                label         TEXT NOT NULL,
                created_at    TEXT NOT NULL,
                expires_at    TEXT NOT NULL,
                consumed_at   TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS auth_login_failures (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                username   TEXT NOT NULL,
                occurred_at TEXT NOT NULL
            )
        """)


init_auth_db()


# --- small helpers ----------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat()


def _parse(value) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _digest(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


# --- passwords --------------------------------------------------------------


def hash_password(password: str, iterations: int = PBKDF2_ITERATIONS) -> str:
    """
    `pbkdf2_sha256$<iterations>$<salt>$<derived>`.

    The iteration count travels with the hash so it can be raised without
    invalidating anyone: `verify_password` reads whatever a row was made with.
    """
    if not password:
        raise AuthError("A password is required.")
    salt = secrets.token_bytes(_SALT_BYTES)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${_b64(salt)}${_b64(derived)}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time comparison against a stored hash. False on anything odd."""
    try:
        algorithm, iterations, salt_b64, expected_b64 = (stored or "").split("$")
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    try:
        iterations_int = int(iterations)
        salt = base64.urlsafe_b64decode(salt_b64 + "=" * (-len(salt_b64) % 4))
        expected = base64.urlsafe_b64decode(expected_b64 + "=" * (-len(expected_b64) % 4))
    except (ValueError, TypeError):
        return False
    derived = hashlib.pbkdf2_hmac(
        "sha256", (password or "").encode("utf-8"), salt, iterations_int)
    return hmac.compare_digest(derived, expected)


# --- tokens -----------------------------------------------------------------


def _mint(prefix: str) -> tuple[str, str, str]:
    """Return (raw token, selector, verifier hash). The raw form exists once."""
    selector = _b64(secrets.token_bytes(12))
    verifier = _b64(secrets.token_bytes(32))
    return f"{prefix}{selector}.{verifier}", selector, _digest(verifier)


def _split(raw: str, prefix: str) -> Optional[tuple[str, str]]:
    """(selector, verifier) from a presented token, or None if it is malformed."""
    if not raw or not raw.startswith(prefix):
        return None
    body = raw[len(prefix):]
    selector, _, verifier = body.partition(".")
    if not selector or not verifier:
        return None
    return selector, verifier


# --- users ------------------------------------------------------------------


@dataclass(frozen=True)
class User:
    user_id: str
    username: str
    company_id: str
    disabled: bool


def _row_to_user(row) -> User:
    return User(
        user_id=row["user_id"],
        username=row["username"],
        company_id=row["company_id"],
        disabled=bool(row["disabled"]),
    )


def create_user(username: str, company_id: str, password: str) -> User:
    """
    Provision an account. Operator-only — see the module docstring for why there
    is no signup route.
    """
    username = (username or "").strip().lower()
    company_id = (company_id or "").strip()
    if not username:
        raise AuthError("A username is required.")
    if not company_id:
        raise AuthError("A company_id is required.")
    if len(password or "") < 12:
        # Long rather than ornate: this is the whole credential, and there is no
        # reset channel, so it wants entropy rather than a character-class dance.
        raise AuthError("Password must be at least 12 characters.")

    user_id = uuid.uuid4().hex
    with db.connect(DB_PATH) as conn:
        existing = conn.execute(
            "SELECT user_id FROM auth_users WHERE username = ?", (username,)).fetchone()
        if existing:
            raise AuthError("That username already exists.")
        conn.execute(
            """INSERT INTO auth_users
               (user_id, username, company_id, password_hash, created_at, disabled)
               VALUES (?, ?, ?, ?, ?, 0)""",
            (user_id, username, company_id, hash_password(password), _iso(_now())),
        )
    return User(user_id=user_id, username=username, company_id=company_id, disabled=False)


def get_user(username: str) -> Optional[User]:
    with db.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT user_id, username, company_id, disabled FROM auth_users WHERE username = ?",
            ((username or "").strip().lower(),),
        ).fetchone()
    return _row_to_user(row) if row else None


def set_password(username: str, password: str) -> None:
    if len(password or "") < 12:
        raise AuthError("Password must be at least 12 characters.")
    with db.connect(DB_PATH) as conn:
        cur = conn.execute(
            "UPDATE auth_users SET password_hash = ? WHERE username = ?",
            (hash_password(password), (username or "").strip().lower()),
        )
        if not cur.rowcount:
            raise AuthError("No such user.")


def set_disabled(username: str, disabled: bool) -> None:
    with db.connect(DB_PATH) as conn:
        cur = conn.execute(
            "UPDATE auth_users SET disabled = ? WHERE username = ?",
            (1 if disabled else 0, (username or "").strip().lower()),
        )
        if not cur.rowcount:
            raise AuthError("No such user.")
        # A disabled account must not keep serving from a session issued before
        # it was disabled.
        conn.execute(
            """UPDATE auth_sessions SET revoked_at = ?
                WHERE user_id = (SELECT user_id FROM auth_users WHERE username = ?)
                  AND revoked_at IS NULL""",
            (_iso(_now()), (username or "").strip().lower()),
        )


def list_users() -> list[dict]:
    with db.connect(DB_PATH) as conn:
        rows = conn.execute(
            """SELECT username, company_id, created_at, disabled
                 FROM auth_users ORDER BY username"""
        ).fetchall()
    return [
        {
            "username": r["username"],
            "company_id": r["company_id"],
            "created_at": r["created_at"],
            "disabled": bool(r["disabled"]),
        }
        for r in rows
    ]


# --- login ------------------------------------------------------------------


def _recent_failures(conn, username: str) -> int:
    cutoff = _iso(_now() - timedelta(seconds=LOGIN_FAILURE_WINDOW_SECONDS))
    row = conn.execute(
        "SELECT COUNT(*) FROM auth_login_failures WHERE username = ? AND occurred_at > ?",
        (username, cutoff),
    ).fetchone()
    return int(row[0] or 0)


def _record_failure(conn, username: str) -> None:
    conn.execute(
        "INSERT INTO auth_login_failures (username, occurred_at) VALUES (?, ?)",
        (username, _iso(_now())),
    )


def authenticate(username: str, password: str) -> User:
    """
    Check a username and password. Raises `AuthError` on every failure path with
    one message, because "no such user" and "wrong password" told apart is a
    free account-enumeration oracle.
    """
    username = (username or "").strip().lower()

    with db.connect(DB_PATH) as conn:
        locked = _recent_failures(conn, username) >= LOGIN_MAX_FAILURES
        row = conn.execute(
            "SELECT user_id, username, company_id, password_hash, disabled "
            "FROM auth_users WHERE username = ?",
            (username,),
        ).fetchone()
        user = _row_to_user(row) if row is not None else None
        # A missing user still pays for a hash, so the response time does not
        # tell an attacker which usernames exist.
        stored = row["password_hash"] if row is not None else (
            "pbkdf2_sha256$600000$AAAAAAAAAAAAAAAAAAAAAA$"
            "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")

    # Outside the connection deliberately. PBKDF2 at this iteration count takes
    # a noticeable fraction of a second, and holding a Cloud SQL connection open
    # across it wastes the scarcer resource.
    ok = verify_password(password or "", stored)

    if locked or user is None or not ok or user.disabled:
        # A SEPARATE connection, because this one has to commit. Recording the
        # failure on the connection above and then raising would roll the insert
        # back — `Connection.__exit__` rolls back on exception — and the lockout
        # would count to one forever. This is the same shape as the bug
        # oauth_state.consume documents; it was reproduced here by a test that
        # tried ten wrong passwords and was then let in.
        with db.connect(DB_PATH) as failures:
            _record_failure(failures, username)
        raise AuthError("Incorrect username or password.")

    with db.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM auth_login_failures WHERE username = ?", (username,))
    return user


# --- sessions ---------------------------------------------------------------


def issue_session(user: User) -> str:
    """
    Start a browser session. Returns the raw token, which exists exactly here
    and in the cookie — it is never written to a log or re-derivable from the
    row, which stores only the selector and the verifier's digest.
    """
    raw, selector, verifier_hash = _mint(_SESSION_PREFIX)
    now = _now()
    with db.connect(DB_PATH) as conn:
        conn.execute(
            """INSERT INTO auth_sessions
               (selector, verifier_hash, user_id, company_id, created_at,
                expires_at, last_seen_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (selector, verifier_hash, user.user_id, user.company_id, _iso(now),
             _iso(now + timedelta(seconds=SESSION_TTL_SECONDS)), _iso(now)),
        )
    return raw


def revoke_session(raw_token: str) -> bool:
    parts = _split(raw_token or "", _SESSION_PREFIX)
    if not parts:
        return False
    with db.connect(DB_PATH) as conn:
        cur = conn.execute(
            "UPDATE auth_sessions SET revoked_at = ? WHERE selector = ? AND revoked_at IS NULL",
            (_iso(_now()), parts[0]),
        )
        return bool(cur.rowcount)


def _touch_session(conn, selector: str, expires_at: datetime, now: datetime) -> None:
    """Slide the window forward, but only when it is worth a write."""
    if (expires_at - now).total_seconds() > SESSION_TTL_SECONDS - SESSION_REFRESH_AFTER_SECONDS:
        return
    conn.execute(
        "UPDATE auth_sessions SET last_seen_at = ?, expires_at = ? WHERE selector = ?",
        (_iso(now), _iso(now + timedelta(seconds=SESSION_TTL_SECONDS)), selector),
    )


# --- device tokens ----------------------------------------------------------


def create_pairing_code(company_id: str, user_id: str, label: str) -> dict:
    """
    Issue a short-lived code that a headless client swaps for a device token.

    This is the whole pairing design, and it is shaped by two constraints: there
    is no email or SMS to send anything through, and the desktop client has no
    browser to complete a consent screen in. So the authenticated web session —
    the one place a human has already proved who they are — mints a code, the
    human carries it to the terminal, and the client exchanges it once.

    The code is a bearer credential for its ten minutes, so it is stored the
    same way as everything else here: selector in the clear, verifier hashed.
    """
    raw, selector, verifier_hash = _mint(_PAIRING_PREFIX)
    now = _now()
    with db.connect(DB_PATH) as conn:
        conn.execute(
            """INSERT INTO auth_pairing_codes
               (selector, verifier_hash, company_id, user_id, label, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (selector, verifier_hash, company_id, user_id, (label or "device").strip()[:80],
             _iso(now), _iso(now + timedelta(seconds=PAIRING_TTL_SECONDS))),
        )
    return {"pairing_code": raw, "expires_in": PAIRING_TTL_SECONDS}


def redeem_pairing_code(code: str) -> dict:
    """
    Exchange a pairing code for a device token, once.

    The row is marked consumed inside the same statement that claims it, so two
    clients racing the same code cannot both get a token — the same conditional
    -write shape the OAuth state and the export gate use, for the same reason:
    a check followed by an action has a window between them.

    The company is taken from the stored row. A caller supplies no company at
    all, which is what stops a redeemed code from being pointed at someone else.
    """
    parts = _split(code or "", _PAIRING_PREFIX)
    if not parts:
        raise AuthError("That pairing code is not valid.")
    selector, verifier = parts
    now = _now()

    with db.connect(DB_PATH) as conn:
        row = conn.execute(
            """SELECT verifier_hash, company_id, user_id, label, expires_at, consumed_at
                 FROM auth_pairing_codes WHERE selector = ?""",
            (selector,),
        ).fetchone()
        if row is None:
            raise AuthError("That pairing code is not valid.")

        # Claim it before validating anything else, and let the transaction
        # commit. Raising inside the block would roll the claim back and leave
        # the code usable — the exact bug oauth_state.consume documents.
        claimed = conn.execute(
            "UPDATE auth_pairing_codes SET consumed_at = ? WHERE selector = ? AND consumed_at IS NULL",
            (_iso(now), selector),
        ).rowcount
        captured = {
            "verifier_hash": row["verifier_hash"],
            "company_id": row["company_id"],
            "user_id": row["user_id"],
            "label": row["label"],
            "expires_at": row["expires_at"],
            "claimed": bool(claimed),
        }

    if not captured["claimed"]:
        raise AuthError("That pairing code is not valid.")
    if not hmac.compare_digest(captured["verifier_hash"], _digest(verifier)):
        raise AuthError("That pairing code is not valid.")
    if _parse(captured["expires_at"]) < now:
        raise AuthError("That pairing code is not valid.")

    return issue_device_token(
        captured["company_id"], captured["user_id"], captured["label"])


def issue_device_token(company_id: str, user_id: str, label: str) -> dict:
    raw, selector, verifier_hash = _mint(_DEVICE_PREFIX)
    now = _now()
    expires_at = (_iso(now + timedelta(seconds=DEVICE_TOKEN_TTL_SECONDS))
                  if DEVICE_TOKEN_TTL_SECONDS else None)
    with db.connect(DB_PATH) as conn:
        conn.execute(
            """INSERT INTO auth_device_tokens
               (selector, verifier_hash, company_id, user_id, label, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (selector, verifier_hash, company_id, user_id,
             (label or "device").strip()[:80], _iso(now), expires_at),
        )
    return {
        "device_token": raw,
        # The selector is the handle a revocation call uses. It is not a
        # credential on its own — the verifier is the half that authenticates.
        "device_id": selector,
        "company_id": company_id,
        "label": label,
    }


def list_device_tokens(company_id: str) -> list[dict]:
    with db.connect(DB_PATH) as conn:
        rows = conn.execute(
            """SELECT selector, label, created_at, last_used_at, revoked_at, expires_at
                 FROM auth_device_tokens WHERE company_id = ? ORDER BY created_at DESC""",
            (company_id,),
        ).fetchall()
    return [
        {
            "device_id": r["selector"],
            "label": r["label"],
            "created_at": r["created_at"],
            "last_used_at": r["last_used_at"],
            "revoked": r["revoked_at"] is not None,
        }
        for r in rows
    ]


def revoke_device_token(company_id: str, device_id: str) -> bool:
    """
    Revoke by selector, scoped to the company that owns it.

    `AND company_id = ?` is the whole point of this function: without it, one
    company could revoke another's device by guessing a selector.
    """
    with db.connect(DB_PATH) as conn:
        cur = conn.execute(
            """UPDATE auth_device_tokens SET revoked_at = ?
                WHERE selector = ? AND company_id = ? AND revoked_at IS NULL""",
            (_iso(_now()), device_id, company_id),
        )
        return bool(cur.rowcount)


# --- resolving a request to a principal -------------------------------------


@dataclass(frozen=True)
class Principal:
    """Who a verified request belongs to. `company_id` is the authority."""

    company_id: str
    user_id: str
    kind: str             # "session" | "device"
    username: str = ""
    device_id: str = ""


def _principal_from_session(raw: str) -> Optional[Principal]:
    parts = _split(raw, _SESSION_PREFIX)
    if not parts:
        return None
    selector, verifier = parts
    now = _now()
    with db.connect(DB_PATH) as conn:
        row = conn.execute(
            """SELECT s.verifier_hash, s.user_id, s.company_id, s.expires_at,
                      s.revoked_at, u.username, u.disabled
                 FROM auth_sessions s JOIN auth_users u ON u.user_id = s.user_id
                WHERE s.selector = ?""",
            (selector,),
        ).fetchone()
        if row is None:
            return None
        if not hmac.compare_digest(row["verifier_hash"], _digest(verifier)):
            return None
        if row["revoked_at"] is not None or bool(row["disabled"]):
            return None
        expires_at = _parse(row["expires_at"])
        if expires_at < now:
            return None
        _touch_session(conn, selector, expires_at, now)
        return Principal(
            company_id=row["company_id"],
            user_id=row["user_id"],
            kind="session",
            username=row["username"],
        )


def _principal_from_device(raw: str) -> Optional[Principal]:
    parts = _split(raw, _DEVICE_PREFIX)
    if not parts:
        return None
    selector, verifier = parts
    now = _now()
    with db.connect(DB_PATH) as conn:
        row = conn.execute(
            """SELECT verifier_hash, company_id, user_id, expires_at, revoked_at
                 FROM auth_device_tokens WHERE selector = ?""",
            (selector,),
        ).fetchone()
        if row is None:
            return None
        if not hmac.compare_digest(row["verifier_hash"], _digest(verifier)):
            return None
        if row["revoked_at"] is not None:
            return None
        if row["expires_at"] and _parse(row["expires_at"]) < now:
            return None
        conn.execute(
            "UPDATE auth_device_tokens SET last_used_at = ? WHERE selector = ?",
            (_iso(now), selector),
        )
        return Principal(
            company_id=row["company_id"],
            user_id=row["user_id"],
            kind="device",
            device_id=selector,
        )


def principal_for_request(request: Request) -> Optional[Principal]:
    """
    Resolve a request to a principal, or None. Never falls back to a default.

    Two carriers, deliberately: an HttpOnly cookie for the browser, so no script
    on the page can read the session; and `Authorization: Bearer` for the
    headless client, which has no cookie jar and no browser. The token's own
    prefix says which table to look in, so a session token presented as a device
    token simply does not resolve.
    """
    header = (request.headers.get("authorization") or "").strip()
    if header[:7].lower() == "bearer ":
        token = header[7:].strip()
        if token.startswith(_DEVICE_PREFIX):
            return _principal_from_device(token)
        if token.startswith(_SESSION_PREFIX):
            return _principal_from_session(token)
        return None

    cookie = request.cookies.get(SESSION_COOKIE)
    if cookie:
        return _principal_from_session(cookie)
    return None


# --- FastAPI dependencies ---------------------------------------------------

_UNAUTHENTICATED = HTTPException(
    status_code=401,
    detail="Sign in to continue.",
    # Tells a non-browser client how to authenticate without naming a scheme a
    # browser would answer with its own basic-auth dialog.
    headers={"WWW-Authenticate": "Bearer"},
)


async def require_principal(request: Request) -> Principal:
    """
    The gate. Every company-aware route depends on this, directly or through
    `require_company_id`.

    There is no default tenant and no fallback header. That absence is the fix:
    the previous `request.headers.get("X-Company-ID", "starter_corp")` meant an
    anonymous request was always *somebody*.
    """
    principal = principal_for_request(request)
    if principal is None:
        raise _UNAUTHENTICATED
    return principal


async def require_company_id(
    principal: Principal = Depends(require_principal),
) -> str:
    """The authenticated tenant, for routes that only need the id."""
    return principal.company_id


def assert_company(principal: Principal, claimed: Optional[str]) -> str:
    """
    Refuse a request that names a company other than the authenticated one.

    Routes that still accept a `company_id` parameter — because a client sends
    it, or because a test pins its presence — pass it through here. Silently
    ignoring a mismatched value would be safe but dishonest: a client asking to
    act as another tenant should be told no, not quietly served its own data.
    """
    if claimed and claimed != principal.company_id:
        raise HTTPException(
            status_code=403,
            detail="That company is not yours.",
        )
    return principal.company_id


# A `require_feature(flag)` dependency was written here and then deleted. Tier
# gating already happens where it belongs: the routes that need it call
# `get_config(company_id)` from agent/subscription.py, and that company_id is
# now the authenticated one, which is the whole change. A second, parallel gate
# with no callers would have been the same shape as `verify_export` — documented
# as the authority, wired to nothing. This project has one of those already.


# --- cookie plumbing --------------------------------------------------------


def cookie_is_secure(request: Request) -> bool:
    """
    Whether to mark the session cookie Secure.

    Always, except on localhost — a Secure cookie over plain http is dropped by
    the browser, which would make local development look like a broken login.
    The forwarded header is honoured first because TLS terminates at Firebase
    Hosting's edge and the request arrives at Cloud Run as http.
    """
    host = (request.headers.get("x-forwarded-host") or request.url.hostname or "").split(":")[0]
    if host in ("localhost", "127.0.0.1", "::1", "testserver"):
        return False
    return True


def set_session_cookie(response, request: Request, raw_token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE,
        value=raw_token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=cookie_is_secure(request),
        path="/",
    )


def clear_session_cookie(response, request: Request) -> None:
    response.delete_cookie(
        key=SESSION_COOKIE,
        httponly=True,
        samesite="lax",
        secure=cookie_is_secure(request),
        path="/",
    )


def purge_expired() -> int:
    """Housekeeping. Expired rows are already unusable; this stops them piling up."""
    now = _iso(_now())
    removed = 0
    with db.connect(DB_PATH) as conn:
        removed += conn.execute(
            "DELETE FROM auth_sessions WHERE expires_at < ?", (now,)).rowcount or 0
        removed += conn.execute(
            "DELETE FROM auth_pairing_codes WHERE expires_at < ?", (now,)).rowcount or 0
        removed += conn.execute(
            "DELETE FROM auth_login_failures WHERE occurred_at < ?",
            (_iso(_now() - timedelta(seconds=LOGIN_FAILURE_WINDOW_SECONDS)),),
        ).rowcount or 0
    return removed
