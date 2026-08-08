"""
Encrypted-at-rest storage for provider OAuth tokens.

=============================================================================
THE RULE
=============================================================================
A refresh token for a user's Google Drive or Dropbox is a long-lived key to
their documents. Three things follow, and all three are enforced here rather
than left to caller discipline:

1. **Encrypted at rest.** The token record is serialised to JSON and sealed
   with Fernet (AES-128-CBC + HMAC-SHA256, authenticated) before it touches
   SQLite. The database column is a BLOB of ciphertext. A stolen `.db` file is
   inert without the key, which lives in the environment (Secret Manager in
   production), never in the database and never in the repo.

2. **Never logged, not even partially.** `OAuthToken` carries the secrets in
   `repr=False` fields and its `__repr__`/`__str__` return a redacted form, so
   an f-string or a `%s` in a log call cannot leak one by accident.
   `install_redaction_filter()` adds a second, independent net: a logging
   filter that scrubs known token shapes out of any record on the
   `agent_autofill` logger regardless of who emitted it. `redact()` returns a
   constant -- no prefix, no suffix, no length, because "just the first six
   characters" is how these end up in a bug report.

3. **Never browser-reachable.** The database path comes from
   `provider_db.provider_db_path()`, which raises if it would resolve under
   `static/` or `firebase_public/`. See that module for why.

=============================================================================
KEY MANAGEMENT
=============================================================================
    AGENT_AUTOFILL_TOKEN_KEY           required -- urlsafe-base64 32-byte key
    AGENT_AUTOFILL_TOKEN_KEY_PREVIOUS  optional -- accepted for decrypt only

There is no default key and no key generated on the fly. A module that
silently invents a key encrypts against a value that changes on the next cold
start, which looks like working encryption and is not. Missing key raises
`TokenEncryptionKeyMissing`.

Generate one with::

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

and set it as a Firebase secret::

    firebase functions:secrets:set AGENT_AUTOFILL_TOKEN_KEY --project cairoai --data-file <path>

Use `--data-file` with a real file; CLAUDE.md records that piping to
`--data-file -` fails on Windows PowerShell.

To rotate: put the new key in AGENT_AUTOFILL_TOKEN_KEY, move the old one to
AGENT_AUTOFILL_TOKEN_KEY_PREVIOUS, then call `reencrypt_all()` and drop the
old key.

=============================================================================
LIMITATION -- READ BEFORE SHIPPING
=============================================================================
On Cloud Functions this database lives in /tmp, which is per-instance and
ephemeral. Provider connections would silently disappear on cold start. Unlike
quota counters or generated PDFs, that is not an acceptable degradation: the
user reconnects their Drive at random intervals forever. This must move to
Firestore (ciphertext column, same Fernet key) before the feature ships.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from agent_autofill.providers import provider_db

__all__ = [
    "REDACTED",
    "redact",
    "OAuthToken",
    "TokenEncryptionKeyMissing",
    "TokenDecryptionFailed",
    "TokenNotFound",
    "save_token",
    "load_token",
    "delete_token",
    "list_connections",
    "reencrypt_all",
    "install_redaction_filter",
    "scrub",
]

logger = logging.getLogger("agent_autofill.providers")

REDACTED = "***REDACTED***"

KEY_ENV = "AGENT_AUTOFILL_TOKEN_KEY"
PREVIOUS_KEY_ENV = "AGENT_AUTOFILL_TOKEN_KEY_PREVIOUS"


class TokenEncryptionKeyMissing(RuntimeError):
    """No Fernet key in the environment. Refuse rather than invent one."""


class TokenDecryptionFailed(RuntimeError):
    """Ciphertext did not authenticate under any configured key."""


class TokenNotFound(KeyError):
    """No stored connection for this (company_id, provider)."""


def redact(_value: Any = None) -> str:
    """
    The only representation of a secret this package ever produces.

    Takes an argument so it can be dropped in place of a value, and ignores it
    so there is no code path where any part of the secret reaches the output.
    """
    return REDACTED


# =============================================================================
# Log scrubbing
# =============================================================================
# Second line of defence. The first is that nothing in this package passes a
# secret to a logger; `tests/test_agent_autofill_providers.py` proves that by
# walking the AST of every module here. This filter catches the case the AST
# check cannot: a secret that arrives inside a string built somewhere else,
# e.g. an SDK error message that quotes the request body back at you.

_TOKEN_PATTERNS = (
    re.compile(r"ya29\.[A-Za-z0-9_\-\.]{10,}"),          # Google access token
    re.compile(r"1//[A-Za-z0-9_\-]{10,}"),                # Google refresh token
    re.compile(r"\bsl\.[A-Za-z0-9_\-]{20,}"),             # Dropbox short-lived
    re.compile(r"\bgAAAAA[A-Za-z0-9_\-=]{10,}"),          # Fernet ciphertext
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9_\-\.=]{10,}"),  # Authorization header
    re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),  # JWT
)


def scrub(text: str) -> str:
    """Replace anything shaped like a provider token with the redaction marker."""
    for pattern in _TOKEN_PATTERNS:
        text = pattern.sub(REDACTED, text)
    return text


class _RedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = scrub(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: (scrub(v) if isinstance(v, str) else v)
                    for k, v in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    scrub(a) if isinstance(a, str) else a for a in record.args
                )
        return True


_filter_singleton = _RedactionFilter()


def install_redaction_filter(target: logging.Logger | None = None) -> logging.Logger:
    """Attach the scrubbing filter to `target` (default: the package logger)."""
    target = target or logger
    if not any(isinstance(f, _RedactionFilter) for f in target.filters):
        target.addFilter(_filter_singleton)
    return target


install_redaction_filter()


# =============================================================================
# The token record
# =============================================================================


@dataclass
class OAuthToken:
    """
    One provider connection's credentials.

    `access_token` and `refresh_token` are `repr=False`, so the dataclass-
    generated repr cannot print them; `__repr__` is overridden anyway in case
    the field list changes. `to_public_dict()` is what any caller that wants a
    dict should use -- it contains no secret at all. Getting the real value is
    an explicit, greppable `.reveal_access_token()`.
    """

    provider: str
    access_token: str = field(repr=False, default="")
    refresh_token: str | None = field(repr=False, default=None)
    expires_at: float | None = None
    scopes: tuple[str, ...] = ()
    account_label: str | None = None
    extra: dict[str, Any] = field(default_factory=dict, repr=False)

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return (
            f"OAuthToken(provider={self.provider!r}, access_token={REDACTED}, "
            f"refresh_token={REDACTED}, expires_at={self.expires_at!r}, "
            f"scopes={self.scopes!r})"
        )

    __str__ = __repr__

    def reveal_access_token(self) -> str:
        """Explicit accessor. Greppable, so an audit can find every use."""
        return self.access_token

    def reveal_refresh_token(self) -> str | None:
        return self.refresh_token

    def is_expired(self, skew_seconds: int = 60, now: float | None = None) -> bool:
        if self.expires_at is None:
            return False
        now = time.time() if now is None else now
        return now >= (self.expires_at - skew_seconds)

    def to_public_dict(self) -> dict[str, Any]:
        """Safe for an API response, a log line, or a UI. No secrets."""
        return {
            "provider": self.provider,
            "scopes": list(self.scopes),
            "account_label": self.account_label,
            "expires_at": self.expires_at,
            "has_refresh_token": self.refresh_token is not None,
            "access_token": REDACTED,
            "refresh_token": REDACTED,
        }

    def _to_sealed_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "scopes": list(self.scopes),
            "account_label": self.account_label,
            "extra": self.extra,
        }

    @classmethod
    def _from_sealed_dict(cls, data: dict[str, Any]) -> "OAuthToken":
        return cls(
            provider=data["provider"],
            access_token=data.get("access_token", ""),
            refresh_token=data.get("refresh_token"),
            expires_at=data.get("expires_at"),
            scopes=tuple(data.get("scopes") or ()),
            account_label=data.get("account_label"),
            extra=data.get("extra") or {},
        )


# =============================================================================
# Encryption
# =============================================================================


def _fernet() -> MultiFernet:
    primary = os.environ.get(KEY_ENV)
    if not primary:
        raise TokenEncryptionKeyMissing(
            f"{KEY_ENV} is not set. Provider tokens are encrypted at rest and "
            "this module will not fall back to an ephemeral or hardcoded key. "
            "Generate one with Fernet.generate_key() and bind it as a secret."
        )

    keys = [Fernet(primary.encode() if isinstance(primary, str) else primary)]
    previous = os.environ.get(PREVIOUS_KEY_ENV)
    if previous:
        keys.append(Fernet(previous.encode() if isinstance(previous, str) else previous))
    return MultiFernet(keys)


def _seal(token: OAuthToken) -> bytes:
    payload = json.dumps(token._to_sealed_dict(), separators=(",", ":")).encode("utf-8")
    return _fernet().encrypt(payload)


def _unseal(blob: bytes) -> OAuthToken:
    try:
        payload = _fernet().decrypt(bytes(blob))
    except InvalidToken as exc:
        # Deliberately does not include the ciphertext or the key.
        raise TokenDecryptionFailed(
            "stored provider token did not authenticate under any configured "
            "key -- the key was rotated without re-encrypting, or the row was "
            "tampered with"
        ) from exc
    return OAuthToken._from_sealed_dict(json.loads(payload.decode("utf-8")))


# =============================================================================
# CRUD
# =============================================================================


def save_token(company_id: str, token: OAuthToken) -> dict[str, Any]:
    """Encrypt and upsert one connection. Returns non-secret metadata only."""
    now = time.time()
    ciphertext = _seal(token)
    scopes = " ".join(token.scopes)

    with provider_db.session() as conn:
        conn.execute(
            """
            INSERT INTO provider_tokens
                (company_id, provider, token_ciphertext, scopes, account_label,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(company_id, provider) DO UPDATE SET
                token_ciphertext = excluded.token_ciphertext,
                scopes           = excluded.scopes,
                account_label    = excluded.account_label,
                updated_at       = excluded.updated_at
            """,
            (
                company_id,
                token.provider,
                ciphertext,
                scopes,
                token.account_label,
                now,
                now,
            ),
        )
        conn.commit()

    logger.info(
        "provider connection stored company_id=%s provider=%s scopes=%s",
        company_id,
        token.provider,
        scopes,
    )
    return {
        "company_id": company_id,
        "provider": token.provider,
        "scopes": list(token.scopes),
        "encrypted_at_rest": True,
        "updated_at": now,
    }


def load_token(company_id: str, provider: str) -> OAuthToken:
    with provider_db.session() as conn:
        row = conn.execute(
            "SELECT token_ciphertext FROM provider_tokens "
            "WHERE company_id = ? AND provider = ?",
            (company_id, provider),
        ).fetchone()

    if row is None:
        raise TokenNotFound(f"no {provider} connection for company_id={company_id!r}")
    return _unseal(row["token_ciphertext"])


def delete_token(company_id: str, provider: str) -> dict[str, Any]:
    with provider_db.session() as conn:
        cur = conn.execute(
            "DELETE FROM provider_tokens WHERE company_id = ? AND provider = ?",
            (company_id, provider),
        )
        conn.commit()
    logger.info(
        "provider connection removed company_id=%s provider=%s", company_id, provider
    )
    return {"deleted": cur.rowcount > 0}


def list_connections(company_id: str) -> list[dict[str, Any]]:
    """Non-secret connection metadata. Safe to return from an API endpoint."""
    with provider_db.session() as conn:
        rows = conn.execute(
            "SELECT provider, scopes, account_label, created_at, updated_at "
            "FROM provider_tokens WHERE company_id = ? ORDER BY provider",
            (company_id,),
        ).fetchall()

    return [
        {
            "provider": r["provider"],
            "scopes": r["scopes"].split() if r["scopes"] else [],
            "account_label": r["account_label"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
            "access_token": REDACTED,
            "refresh_token": REDACTED,
        }
        for r in rows
    ]


def reencrypt_all() -> dict[str, int]:
    """
    Re-seal every stored token under the current primary key.

    Run after rotating AGENT_AUTOFILL_TOKEN_KEY while the old key is still in
    AGENT_AUTOFILL_TOKEN_KEY_PREVIOUS; then remove the previous key.
    """
    rotated = 0
    failed = 0
    with provider_db.session() as conn:
        rows = conn.execute(
            "SELECT company_id, provider, token_ciphertext FROM provider_tokens"
        ).fetchall()
        for row in rows:
            try:
                reopened = _unseal(row["token_ciphertext"])
            except TokenDecryptionFailed:
                failed += 1
                continue
            conn.execute(
                "UPDATE provider_tokens SET token_ciphertext = ?, updated_at = ? "
                "WHERE company_id = ? AND provider = ?",
                (_seal(reopened), time.time(), row["company_id"], row["provider"]),
            )
            rotated += 1
        conn.commit()

    logger.info("provider token re-encryption complete rotated=%s failed=%s", rotated, failed)
    return {"rotated": rotated, "failed": failed}


def scopes_of(tokens: Iterable[OAuthToken]) -> set[str]:
    out: set[str] = set()
    for t in tokens:
        out.update(t.scopes)
    return out
