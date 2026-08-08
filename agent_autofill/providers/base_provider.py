"""
The cloud-storage provider interface.

=============================================================================
UNVERIFIED
=============================================================================
No implementation of this interface has been exercised against a live Google
or Dropbox account. Doing so requires completing an OAuth consent flow, which
requires entering account credentials -- prohibited in the environment this was
built in, and there is no test account or configured OAuth client. Everything
here is code review plus offline tests of the parts that do not need a network:
signature verification, channel validation, renewal arithmetic, encryption
round-trip. See `agent_autofill/providers/VERIFICATION.md` for the checklist a
human must run before this is trusted.

=============================================================================
THE SHAPE OF THE CONTRACT
=============================================================================
Five operations, plus webhook verification:

    connect            complete OAuth, persist an encrypted token
    register_webhook   ask the provider to notify us about a resource
    renew_webhook      keep that notification alive before it lapses
    list_changed_files enumerate what changed since we last looked
    download_file      fetch one file's bytes to a non-servable location
    verify_webhook     decide whether an inbound request is genuine

`verify_webhook` is on the interface rather than left to each receiver because
both providers get it structurally wrong in the same way if it is not: the
temptation is to parse the body first and check authenticity afterwards.
Returning a `WebhookVerdict` (rather than raising) makes the decision a value
that can be logged, asserted on, and unit-tested without an HTTP layer.

=============================================================================
TWO RULES THAT ARE NOT NEGOTIABLE
=============================================================================
1. **Verify before you parse.** For Dropbox that means the HMAC is computed
   over the raw body before `json.loads` ever runs. An unauthenticated body is
   attacker-controlled input; handing it to a parser first is the whole bug.

2. **Acknowledge fast, process asynchronously.** A webhook handler must do
   verification, enqueue, respond. No extraction, no fill, no Claude call, no
   file download inside the handler. Google retries with exponential backoff
   if you are slow or fail, so a slow handler turns one notification into a
   storm; Dropbox expects a prompt 200 as well. `webhooks/async_queue.py`
   carries this out and documents why in-process background work is not
   sufficient on Cloud Functions.
"""

from __future__ import annotations

import logging
import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_autofill.providers import provider_db

__all__ = [
    "ProviderError",
    "ProviderSDKMissing",
    "ProviderConfigError",
    "ProviderAuthError",
    "WebhookVerdict",
    "ChangedFile",
    "WebhookChannel",
    "BaseCloudProvider",
    "MAX_DOWNLOAD_BYTES",
    "safe_download_path",
]

logger = logging.getLogger("agent_autofill.providers")

# A tender pack can be large (the 145pp BID_DOCUMENT fixture is a real
# example), but an unbounded download from a third party into an ephemeral
# /tmp with a few hundred MB of space is a denial-of-service on ourselves.
MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024  # 64 MB


class ProviderError(Exception):
    """Base for anything this package raises to a caller."""


class ProviderSDKMissing(ProviderError):
    """
    A provider SDK is required for this call but is not installed.

    Raised at call time, never at import time -- see the module docstrings of
    the concrete providers for why every SDK import in this package is lazy.
    """


class ProviderConfigError(ProviderError):
    """A required client id / client secret / callback URL is not configured."""


class ProviderAuthError(ProviderError):
    """The stored credentials are missing, expired beyond refresh, or rejected."""


@dataclass(frozen=True)
class WebhookVerdict:
    """
    The result of inspecting an inbound webhook request.

    `accepted` is about authenticity: did this come from the provider, for a
    channel we registered, and is it fresh? `process` is about work: a Google
    `sync` handshake is perfectly authentic and means nothing needs doing.

    `http_status` is the status the receiver should return. It is not always
    an error code when `accepted` is False, and that is deliberate:

        401/403  bad or missing signature/token -- tell the caller no.
        404      unknown channel -- the provider should stop sending. Google
                 treats repeated non-2xx as a reason to abandon the channel,
                 which is exactly what we want for a channel we do not know.
        400      malformed or missing required headers.
        410      the channel we do know has already expired.
        200      replayed or stale delivery. We return OK precisely so the
                 provider does NOT retry a message we have already handled.
                 The rejection is real -- `process` is False and nothing is
                 enqueued -- but arguing about it over HTTP helps no one.
    """

    accepted: bool
    reason: str
    http_status: int
    process: bool = False
    provider: str = ""
    channel_id: str | None = None
    company_id: str | None = None
    resource_state: str | None = None
    accounts: tuple[str, ...] = ()
    detail: str = ""

    def as_log_fields(self) -> dict[str, Any]:
        """Everything here is non-secret and safe to log."""
        return {
            "provider": self.provider,
            "accepted": self.accepted,
            "reason": self.reason,
            "status": self.http_status,
            "process": self.process,
            "channel_id": self.channel_id,
            "company_id": self.company_id,
            "resource_state": self.resource_state,
        }


@dataclass(frozen=True)
class ChangedFile:
    """One file the provider says changed. Metadata only -- no bytes."""

    file_id: str
    name: str
    mime_type: str | None = None
    modified_time: str | None = None
    size: int | None = None
    path: str | None = None
    removed: bool = False
    provider: str = ""


@dataclass(frozen=True)
class WebhookChannel:
    """A registered notification channel, as this package records it."""

    channel_id: str
    provider: str
    company_id: str
    resource_id: str | None
    resource_uri: str | None
    watched_file_id: str | None
    callback_url: str
    created_at: float
    expiration_at: float
    last_message_number: int = 0
    status: str = "active"
    superseded_by: str | None = None

    def seconds_remaining(self, now: float) -> float:
        return self.expiration_at - now

    def to_public_dict(self) -> dict[str, Any]:
        """No channel token, no digest -- nothing an attacker could forge with."""
        return {
            "channel_id": self.channel_id,
            "provider": self.provider,
            "company_id": self.company_id,
            "expiration_at": self.expiration_at,
            "status": self.status,
        }


_SAFE_NAME = re.compile(r"[^A-Za-z0-9._\-]+")


def safe_download_path(filename: str, dest_dir: Path | None = None) -> Path:
    """
    Resolve a provider-supplied filename to a path we are willing to write.

    The name comes from a third-party API and is therefore untrusted: it may
    contain separators, `..`, NUL, or be empty. Modelled on
    `agent/file_paths.safe_generated_path`, with one addition that matters
    here -- the destination is re-checked against the servable-directory guard,
    so a caller cannot pass `dest_dir=Path("static/downloads")` and publish a
    private tender document.
    """
    root = provider_db.assert_not_browser_servable(
        dest_dir if dest_dir is not None else provider_db.provider_download_dir()
    )
    root.mkdir(parents=True, exist_ok=True)

    base = os.path.basename(str(filename).replace("\\", "/")).strip()
    base = _SAFE_NAME.sub("_", base).lstrip(".")
    if not base or base in (".", ".."):
        raise ValueError(f"unsafe provider filename: {filename!r}")
    base = base[:180]

    target = (root / base).resolve()
    if target.parent != root:
        raise ValueError(f"provider filename escapes download dir: {filename!r}")
    return target


class BaseCloudProvider(ABC):
    """
    Abstract cloud-storage provider.

    Subclasses set `name` and `SCOPES`. `SCOPES` is part of the interface
    rather than an implementation detail because the minimum-consent decision
    is the security decision -- see `google_drive_provider.SCOPES` for the one
    that has already been got wrong once.
    """

    name: str = ""
    SCOPES: tuple[str, ...] = ()

    # ---- OAuth ----------------------------------------------------------

    @abstractmethod
    def build_authorization_url(self, company_id: str, redirect_uri: str, **kw: Any) -> dict[str, Any]:
        """Return {'url', 'state'} for the consent redirect. No network call."""

    @abstractmethod
    def connect(self, company_id: str, authorization_code: str, redirect_uri: str, **kw: Any) -> dict[str, Any]:
        """
        Exchange an authorization code and persist an encrypted token.

        MUST return non-secret metadata only. A connect() that returns the
        access token to its caller has defeated `token_store`.
        """

    # ---- Notifications --------------------------------------------------

    @abstractmethod
    def register_webhook(self, company_id: str, callback_url: str, **kw: Any) -> WebhookChannel:
        """Ask the provider to notify `callback_url` about a resource."""

    @abstractmethod
    def renew_webhook(self, channel_id: str, **kw: Any) -> WebhookChannel:
        """Keep notifications alive past the current channel's expiry."""

    @abstractmethod
    def verify_webhook(self, headers: dict[str, str], raw_body: bytes, now: float | None = None) -> WebhookVerdict:
        """
        Decide whether an inbound request is genuine.

        MUST authenticate before interpreting `raw_body`.
        MUST NOT perform extraction, download, or any provider API call.
        """

    # ---- Data -----------------------------------------------------------

    @abstractmethod
    def list_changed_files(self, company_id: str, **kw: Any) -> list[ChangedFile]:
        """Enumerate what changed since the last checkpoint."""

    @abstractmethod
    def download_file(self, company_id: str, file_id: str, dest_dir: Path | None = None, **kw: Any) -> Path:
        """Fetch one file to a non-servable location and return its path."""

    # ---- Shared helpers -------------------------------------------------

    @staticmethod
    def _header(headers: dict[str, str], name: str) -> str | None:
        """
        Case-insensitive header lookup.

        HTTP header names are case-insensitive and different servers normalise
        differently: Starlette lowercases, WSGI upper-cases with `HTTP_`
        prefixes, and Google's own docs write `X-Goog-Channel-ID`. Matching on
        the exact casing is a bug that only shows up in production.
        """
        if not headers:
            return None
        wanted = name.lower()
        for key, value in headers.items():
            if str(key).lower() == wanted:
                return value
        return None

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"<{type(self).__name__} name={self.name!r} scopes={list(self.SCOPES)!r}>"
