"""
Storage location and schema for cloud-provider connections.

=============================================================================
WHY THIS MODULE EXISTS AT ALL
=============================================================================
Everything a cloud provider connection needs to persist -- OAuth tokens,
webhook channel registrations, Dropbox delta cursors -- is either a credential
or something that lets an attacker impersonate a webhook. None of it may ever
land somewhere a browser can fetch.

The repo makes that mistake easy to commit. `app.py` mounts::

    app.mount("/static", StaticFiles(directory="static"), name="static")

and `firebase_public/` is what Firebase Hosting serves. Anything written under
either directory is a URL. `agent/file_paths.py` resolves generated files to
`static/downloads` locally -- correct for a quotation PDF the user is meant to
click, catastrophic for a refresh token.

So this module does two things and nothing else:

1. Resolves the provider database and the provider download directory to
   locations that are NOT servable, and
2. calls `assert_not_browser_servable()` on the result, so that a future edit
   that points these at `static/` raises at import instead of silently
   publishing credentials.

The guard is deliberately dumb -- a path-component check, not a clever one. It
cannot be satisfied by a path that merely *looks* safe.

=============================================================================
DURABILITY LIMITATION (same as agent/db_paths.py)
=============================================================================
On Cloud Functions the only writable location is /tmp, which is per-instance
and ephemeral. A provider connection stored there does NOT survive a cold start
or scale-out: the user's Drive would appear disconnected at random. Provider
connections are the one kind of state in this codebase that genuinely cannot
live in /tmp in production -- they must move to Firestore or Secret Manager
before this ships. That is called out again in `token_store.py` and in the
UNVERIFIED list.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

__all__ = [
    "PROJECT_ROOT",
    "SERVABLE_DIR_NAMES",
    "UnsafeStorageLocation",
    "assert_not_browser_servable",
    "provider_db_path",
    "provider_download_dir",
    "connect",
    "session",
    "ensure_schema",
]

# agent_autofill/providers/provider_db.py -> providers -> agent_autofill -> repo
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Directory names that end up addressable over HTTP. `static` is the FastAPI
# StaticFiles mount; `firebase_public` is the Hosting document root.
SERVABLE_DIR_NAMES = ("static", "firebase_public")


class UnsafeStorageLocation(RuntimeError):
    """Raised when credential storage would land under a browser-servable path."""


def _on_serverless() -> bool:
    return bool(os.environ.get("K_SERVICE") or os.environ.get("FUNCTION_TARGET"))


def assert_not_browser_servable(path: Path | str) -> Path:
    """
    Return `path` resolved, or raise if any component is a servable directory.

    Checked on every resolution rather than once at import, because the
    environment overrides below are read at call time.
    """
    resolved = Path(path).resolve()
    lowered = {part.lower() for part in resolved.parts}
    for name in SERVABLE_DIR_NAMES:
        if name in lowered:
            raise UnsafeStorageLocation(
                f"refusing to store provider credentials at {resolved} -- "
                f"'{name}/' is served to browsers"
            )
    return resolved


def provider_db_path() -> Path:
    """Filesystem path of the provider SQLite database."""
    override = os.environ.get("AGENT_AUTOFILL_PROVIDER_DB")
    if override:
        path = Path(override)
    elif _on_serverless():
        path = Path("/tmp") / "dolo-db" / "agent_autofill_providers.db"
    else:
        # Repo root. `*.db` is gitignored, so this is never committed.
        path = PROJECT_ROOT / "agent_autofill_providers.db"

    path.parent.mkdir(parents=True, exist_ok=True)
    return assert_not_browser_servable(path)


def provider_download_dir() -> Path:
    """
    Where files pulled from Drive/Dropbox are written.

    Explicitly NOT `agent.file_paths.generated_dir()`. That resolves to
    `static/downloads` locally, which is a public URL prefix -- fine for a
    quotation the user clicks, wrong for a tender document fetched out of a
    private Drive folder.
    """
    override = os.environ.get("AGENT_AUTOFILL_DOWNLOAD_DIR")
    if override:
        root = Path(override)
    elif _on_serverless():
        root = Path("/tmp") / "dolo-provider-downloads"
    else:
        root = PROJECT_ROOT / "data" / "provider_downloads"

    root.mkdir(parents=True, exist_ok=True)
    return assert_not_browser_servable(root)


# =============================================================================
# Schema
# =============================================================================
# One place, so there is no second opinion about what a column means.
#
# Note what is NOT stored in plaintext:
#   provider_tokens.token_ciphertext  -- Fernet blob, see token_store.py
#   webhook_channels.channel_token_sha256 -- a hash, never the token itself.
#     The channel token is generated at registration, handed to Google once,
#     and only its digest is kept. Verification is a digest comparison, so the
#     plaintext is never needed again and a database read yields nothing an
#     attacker can replay into the webhook endpoint.

_SCHEMA = """
CREATE TABLE IF NOT EXISTS provider_tokens (
    company_id       TEXT NOT NULL,
    provider         TEXT NOT NULL,
    token_ciphertext BLOB NOT NULL,
    scopes           TEXT NOT NULL DEFAULT '',
    account_label    TEXT,
    created_at       REAL NOT NULL,
    updated_at       REAL NOT NULL,
    PRIMARY KEY (company_id, provider)
);

CREATE TABLE IF NOT EXISTS webhook_channels (
    channel_id            TEXT PRIMARY KEY,
    provider              TEXT NOT NULL,
    company_id            TEXT NOT NULL,
    resource_id           TEXT,
    resource_uri          TEXT,
    watched_file_id       TEXT,
    channel_token_sha256  TEXT NOT NULL,
    callback_url          TEXT NOT NULL,
    created_at            REAL NOT NULL,
    expiration_at         REAL NOT NULL,
    last_message_number   INTEGER NOT NULL DEFAULT 0,
    status                TEXT NOT NULL DEFAULT 'active',
    superseded_by         TEXT
);

CREATE INDEX IF NOT EXISTS idx_channels_expiry
    ON webhook_channels (status, expiration_at);

CREATE TABLE IF NOT EXISTS dropbox_cursors (
    account_id  TEXT PRIMARY KEY,
    company_id  TEXT NOT NULL,
    cursor      TEXT NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS webhook_replay_cache (
    digest    TEXT PRIMARY KEY,
    provider  TEXT NOT NULL,
    seen_at   REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_replay_seen_at
    ON webhook_replay_cache (seen_at);
"""


def connect() -> sqlite3.Connection:
    """Open the provider database with the schema guaranteed to exist."""
    conn = sqlite3.connect(str(provider_db_path()))
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    return conn


@contextmanager
def session() -> Iterator[sqlite3.Connection]:
    """
    A connection that is actually closed afterwards.

    `with sqlite3.connect(...) as conn` commits or rolls back but does NOT
    close -- the handle survives until garbage collection. On Windows that
    keeps a lock on the database file, which is how a test suite ends up
    unable to delete its own temporary directory. Every caller in this package
    uses this rather than `connect()` directly, and commits explicitly.
    """
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    conn.commit()
