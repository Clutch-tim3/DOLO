"""
The record of which notification channels we actually registered.

=============================================================================
WHY A REGISTRY IS THE SECURITY BOUNDARY
=============================================================================
A webhook endpoint is a URL on the public internet that anybody can POST to.
Google's notification carries no signature -- authenticity rests entirely on
two things the receiver must check against local state:

    * the channel ID is one WE created, and
    * the `X-Goog-Channel-Token` matches the opaque secret WE supplied when we
      created it.

Without a registry there is nothing to check against, and "validate the
webhook" degenerates into "confirm the header exists", which any attacker can
satisfy. So the registry is not bookkeeping; it is the thing that makes
verification possible at all.

Three deliberate choices:

1. **The channel token is stored as a SHA-256 digest, never in plaintext.**
   It is generated at registration, sent to Google once, and thereafter only
   compared. Storing the digest means a database read gives an attacker
   nothing they can replay. Comparison uses `hmac.compare_digest`.

2. **`last_message_number` is persisted per channel.** Google sends
   `X-Goog-Message-Number`, which increases monotonically within a channel.
   Keeping the high-water mark turns replay detection into an integer
   comparison. Without it, a captured notification can be re-sent forever.

3. **Expired channels are kept, not deleted**, with `status='expired'`. A
   deleted channel is indistinguishable from a channel that never existed, so
   a late delivery on a channel that lapsed an hour ago would be reported as
   "unknown channel" -- which looks like an attack and hides an operational
   problem (the renewal cron stopped running). They are different failures and
   should read differently in the logs.

Dropbox has no channel concept: notifications are per-account and carry an
HMAC over the body instead. Its state here is the delta cursor plus a shared
replay cache.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time
from typing import Any, Iterable

from agent_autofill.providers import provider_db
from agent_autofill.providers.base_provider import WebhookChannel

__all__ = [
    "new_channel_secret",
    "hash_channel_token",
    "token_matches",
    "register_channel",
    "get_channel",
    "list_active_channels",
    "channels_due_for_renewal",
    "record_message_number",
    "mark_expired",
    "mark_superseded",
    "delete_channel",
    "get_cursor",
    "save_cursor",
    "delete_cursor",
    "seen_recently",
    "prune_replay_cache",
    "REPLAY_WINDOW_SECONDS",
]

logger = logging.getLogger("agent_autofill.providers")

# How long a delivery digest is remembered for duplicate suppression. Long
# enough to cover a provider's retry window, short enough that the table stays
# small. Dropbox retries a failed delivery for a matter of minutes.
REPLAY_WINDOW_SECONDS = 15 * 60


# =============================================================================
# Channel tokens
# =============================================================================


def new_channel_secret() -> str:
    """
    A fresh per-channel secret.

    `secrets.token_urlsafe(32)` is 256 bits of CSPRNG output. Not `uuid4()` --
    a UUID is an identifier, has 122 bits, and is often logged; this value is
    a bearer credential that authenticates every notification on the channel.
    """
    return secrets.token_urlsafe(32)


def hash_channel_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_matches(presented: str | None, stored_digest: str | None) -> bool:
    """
    Constant-time comparison of a presented channel token against its digest.

    Both sides are hashed first so the comparison is over fixed-length hex,
    and `compare_digest` keeps it timing-safe. A plain `==` on the raw token
    leaks its prefix through timing to anyone willing to send enough requests.
    """
    if not presented or not stored_digest:
        return False
    return hmac.compare_digest(hash_channel_token(presented), stored_digest)


# =============================================================================
# Channels
# =============================================================================


def _row_to_channel(row: Any) -> WebhookChannel:
    return WebhookChannel(
        channel_id=row["channel_id"],
        provider=row["provider"],
        company_id=row["company_id"],
        resource_id=row["resource_id"],
        resource_uri=row["resource_uri"],
        watched_file_id=row["watched_file_id"],
        callback_url=row["callback_url"],
        created_at=row["created_at"],
        expiration_at=row["expiration_at"],
        last_message_number=row["last_message_number"],
        status=row["status"],
        superseded_by=row["superseded_by"],
    )


def register_channel(
    *,
    channel_id: str,
    provider: str,
    company_id: str,
    channel_token: str,
    callback_url: str,
    expiration_at: float,
    resource_id: str | None = None,
    resource_uri: str | None = None,
    watched_file_id: str | None = None,
    created_at: float | None = None,
) -> WebhookChannel:
    """Persist a channel. `channel_token` is hashed here and not stored raw."""
    created_at = time.time() if created_at is None else created_at
    digest = hash_channel_token(channel_token)

    with provider_db.session() as conn:
        conn.execute(
            """
            INSERT INTO webhook_channels
                (channel_id, provider, company_id, resource_id, resource_uri,
                 watched_file_id, channel_token_sha256, callback_url,
                 created_at, expiration_at, last_message_number, status,
                 superseded_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'active', NULL)
            ON CONFLICT(channel_id) DO UPDATE SET
                resource_id          = excluded.resource_id,
                resource_uri         = excluded.resource_uri,
                watched_file_id      = excluded.watched_file_id,
                channel_token_sha256 = excluded.channel_token_sha256,
                callback_url         = excluded.callback_url,
                expiration_at        = excluded.expiration_at,
                status               = 'active'
            """,
            (
                channel_id,
                provider,
                company_id,
                resource_id,
                resource_uri,
                watched_file_id,
                digest,
                callback_url,
                created_at,
                expiration_at,
            ),
        )
        conn.commit()

    logger.info(
        "webhook channel registered provider=%s company_id=%s channel_id=%s "
        "expires_in=%.0fs",
        provider,
        company_id,
        channel_id,
        expiration_at - created_at,
    )
    return WebhookChannel(
        channel_id=channel_id,
        provider=provider,
        company_id=company_id,
        resource_id=resource_id,
        resource_uri=resource_uri,
        watched_file_id=watched_file_id,
        callback_url=callback_url,
        created_at=created_at,
        expiration_at=expiration_at,
    )


def get_channel(channel_id: str) -> WebhookChannel | None:
    with provider_db.session() as conn:
        row = conn.execute(
            "SELECT * FROM webhook_channels WHERE channel_id = ?", (channel_id,)
        ).fetchone()
    return _row_to_channel(row) if row else None


def get_channel_token_digest(channel_id: str) -> str | None:
    with provider_db.session() as conn:
        row = conn.execute(
            "SELECT channel_token_sha256 FROM webhook_channels WHERE channel_id = ?",
            (channel_id,),
        ).fetchone()
    return row["channel_token_sha256"] if row else None


def list_active_channels(provider: str | None = None) -> list[WebhookChannel]:
    sql = "SELECT * FROM webhook_channels WHERE status = 'active'"
    params: tuple[Any, ...] = ()
    if provider:
        sql += " AND provider = ?"
        params = (provider,)
    sql += " ORDER BY expiration_at ASC"
    with provider_db.session() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_channel(r) for r in rows]


def channels_due_for_renewal(
    now: float, threshold_seconds: float, provider: str | None = None
) -> list[WebhookChannel]:
    """
    Active channels whose remaining life is at or below `threshold_seconds`.

    Already-expired channels are included: they still need a replacement, and
    reporting them separately is the renewal cron's job, not the query's.
    """
    cutoff = now + threshold_seconds
    sql = (
        "SELECT * FROM webhook_channels "
        "WHERE status = 'active' AND expiration_at <= ?"
    )
    params: list[Any] = [cutoff]
    if provider:
        sql += " AND provider = ?"
        params.append(provider)
    sql += " ORDER BY expiration_at ASC"
    with provider_db.session() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    return [_row_to_channel(r) for r in rows]


def record_message_number(channel_id: str, message_number: int) -> None:
    """
    Advance the per-channel high-water mark.

    Guarded by `> last_message_number` in SQL so two concurrent deliveries
    cannot move it backwards.
    """
    with provider_db.session() as conn:
        conn.execute(
            "UPDATE webhook_channels SET last_message_number = ? "
            "WHERE channel_id = ? AND last_message_number < ?",
            (message_number, channel_id, message_number),
        )
        conn.commit()


def mark_expired(channel_id: str) -> None:
    with provider_db.session() as conn:
        conn.execute(
            "UPDATE webhook_channels SET status = 'expired' WHERE channel_id = ?",
            (channel_id,),
        )
        conn.commit()


def mark_superseded(old_channel_id: str, new_channel_id: str) -> None:
    """
    Retire a channel that has been replaced.

    Called only AFTER the replacement is confirmed live -- see
    `webhooks/channel_renewal_cron.py`. Retiring first would open a gap.
    """
    with provider_db.session() as conn:
        conn.execute(
            "UPDATE webhook_channels SET status = 'superseded', superseded_by = ? "
            "WHERE channel_id = ?",
            (new_channel_id, old_channel_id),
        )
        conn.commit()
    logger.info(
        "webhook channel superseded old_channel_id=%s new_channel_id=%s",
        old_channel_id,
        new_channel_id,
    )


def delete_channel(channel_id: str) -> bool:
    with provider_db.session() as conn:
        cur = conn.execute(
            "DELETE FROM webhook_channels WHERE channel_id = ?", (channel_id,)
        )
        conn.commit()
    return cur.rowcount > 0


# =============================================================================
# Dropbox cursors
# =============================================================================
# The cursor is an opaque pagination token, not a credential -- it grants no
# access on its own. Stored in plaintext so that a lost encryption key costs a
# full re-sync rather than data loss.


def get_cursor(account_id: str) -> str | None:
    with provider_db.session() as conn:
        row = conn.execute(
            "SELECT cursor FROM dropbox_cursors WHERE account_id = ?", (account_id,)
        ).fetchone()
    return row["cursor"] if row else None


def get_cursor_row(account_id: str) -> dict[str, Any] | None:
    with provider_db.session() as conn:
        row = conn.execute(
            "SELECT * FROM dropbox_cursors WHERE account_id = ?", (account_id,)
        ).fetchone()
    return dict(row) if row else None


def save_cursor(account_id: str, company_id: str, cursor: str) -> None:
    with provider_db.session() as conn:
        conn.execute(
            """
            INSERT INTO dropbox_cursors (account_id, company_id, cursor, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(account_id) DO UPDATE SET
                company_id = excluded.company_id,
                cursor     = excluded.cursor,
                updated_at = excluded.updated_at
            """,
            (account_id, company_id, cursor, time.time()),
        )
        conn.commit()


def delete_cursor(account_id: str) -> bool:
    with provider_db.session() as conn:
        cur = conn.execute(
            "DELETE FROM dropbox_cursors WHERE account_id = ?", (account_id,)
        )
        conn.commit()
    return cur.rowcount > 0


# =============================================================================
# Replay cache
# =============================================================================


def seen_recently(
    digest: str, provider: str, now: float | None = None, window: float = REPLAY_WINDOW_SECONDS
) -> bool:
    """
    True if this exact delivery was already accepted inside the window.

    Records the digest as a side effect when it is new, so the check and the
    insert are one atomic step -- `INSERT OR IGNORE` then inspect `rowcount`.
    A separate SELECT-then-INSERT would let two concurrent copies of the same
    replayed request both pass.

    For Dropbox this is genuine replay defence. For Google it is belt and
    braces behind `X-Goog-Message-Number`; a legitimate duplicate is harmless
    anyway because processing is cursor-driven and idempotent.
    """
    now = time.time() if now is None else now
    prune_replay_cache(now=now, window=window)
    with provider_db.session() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO webhook_replay_cache (digest, provider, seen_at) "
            "VALUES (?, ?, ?)",
            (digest, provider, now),
        )
        conn.commit()
        return cur.rowcount == 0


def prune_replay_cache(now: float | None = None, window: float = REPLAY_WINDOW_SECONDS) -> int:
    now = time.time() if now is None else now
    with provider_db.session() as conn:
        cur = conn.execute(
            "DELETE FROM webhook_replay_cache WHERE seen_at < ?", (now - window,)
        )
        conn.commit()
    return cur.rowcount


def summarise(channels: Iterable[WebhookChannel], now: float) -> list[dict[str, Any]]:
    """Non-secret view of a channel list, for the cron's report."""
    return [
        {**c.to_public_dict(), "seconds_remaining": round(c.seconds_remaining(now))}
        for c in channels
    ]
