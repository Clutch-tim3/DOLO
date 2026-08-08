"""
Google Drive provider: OAuth, `files.watch` channels, and webhook verification.

=============================================================================
UNVERIFIED -- NOTHING HERE HAS TOUCHED A LIVE GOOGLE ACCOUNT
=============================================================================
No OAuth client is configured, no test Google account exists, and completing a
consent screen requires entering credentials, which was prohibited in the
environment this was written in. So:

  * the consent flow has never been run,
  * `files.watch` has never been called,
  * no real notification has ever arrived at the receiver,
  * channel renewal has never been observed end to end.

What HAS been tested offline, with real assertions and literal output:
verification of crafted notifications (missing headers, wrong channel token,
unknown channel, stale message number, expired channel), the renewal
arithmetic, and token encryption. Everything that requires the network is
structurally reviewed only. `providers/VERIFICATION.md` lists exactly what a
human has to do to close the gap. Do not describe this module as working until
that checklist has been run.

=============================================================================
SCOPE: `drive.file`, NOT `drive.readonly` -- DO NOT "FIX" THIS
=============================================================================
`https://www.googleapis.com/auth/drive.readonly` presents the user with
"See and download all your Google Drive files". It is not folder-scopeable:
there is no parameter, no filter, and no consent-time restriction that limits
it to one folder. Granting it to a procurement assistant means granting read
access to the user's entire Drive -- payroll, contracts, personal documents --
in exchange for watching one tender folder.

`https://www.googleapis.com/auth/drive.file` grants access only to files the
user explicitly opens with the Google Picker, plus files this app creates. The
user picks the tender folder; we see that and nothing else. Consent reads
"See, edit, create, and delete only the specific Google Drive files you use
with this app."

The cost is that a Picker interaction is mandatory -- there is no way to
enumerate a folder the user has not picked, and no server-side substitute.
That cost is the feature. This was checked against Google's live documentation
and is recorded in `agent_autofill/BUILD_STATE.md` as a correction to the
original spec. If a later change makes folder discovery "easier" by widening
this scope, it is not an optimisation.

=============================================================================
CHANNEL LIFETIME: 86,400s MAXIMUM
=============================================================================
`files.watch` accepts an `expiration` in the future; Drive caps it at **one
day** (86,400s) and defaults to **one hour** if omitted. It is not ~7 days --
that figure belongs to other Google APIs and, taken at face value, produces a
renewal schedule that lets every channel die. See
`webhooks/channel_renewal_cron.py` for the arithmetic.

A channel also cannot be extended in place. There is no "renew" call. Renewal
means: create a new channel, confirm it, then `channels.stop` the old one. The
order matters -- stopping first opens a gap in which changes are missed
silently, and a missed tender document is the failure mode that matters here.

=============================================================================
SDK DEPENDENCY: LAZY
=============================================================================
`google-api-python-client` and `google-auth-oauthlib` are added to
requirements.txt but every import of them in this module is inside a function.
The module therefore imports, and all verification logic is testable, on a
machine where neither is installed -- which is the machine this was built on.
A call that genuinely needs the network raises `ProviderSDKMissing` with an
install line, rather than the whole package failing to import.

The authorization URL is built with `urllib.parse.urlencode` rather than the
SDK, so the one piece of the OAuth flow that has no network dependency stays
testable too.
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from agent_autofill.providers import channel_registry, token_store
from agent_autofill.providers.base_provider import (
    MAX_DOWNLOAD_BYTES,
    BaseCloudProvider,
    ChangedFile,
    ProviderAuthError,
    ProviderConfigError,
    ProviderError,
    ProviderSDKMissing,
    WebhookChannel,
    WebhookVerdict,
    safe_download_path,
)
from agent_autofill.providers.token_store import OAuthToken

__all__ = [
    "SCOPES",
    "DRIVE_CHANNEL_MAX_TTL_SECONDS",
    "DRIVE_CHANNEL_DEFAULT_TTL_SECONDS",
    "VALID_RESOURCE_STATES",
    "GoogleDriveProvider",
]

logger = logging.getLogger("agent_autofill.providers")

PROVIDER_NAME = "google_drive"

# ---------------------------------------------------------------------------
# The scope decision. See the module docstring. Do not widen.
# ---------------------------------------------------------------------------
SCOPES: tuple[str, ...] = ("https://www.googleapis.com/auth/drive.file",)

# Scopes this package refuses to request, with the reason, so that a future
# edit has to delete an explicit refusal rather than change a string.
FORBIDDEN_SCOPES: dict[str, str] = {
    "https://www.googleapis.com/auth/drive": "full read/write over the entire Drive",
    "https://www.googleapis.com/auth/drive.readonly": (
        "grants 'See and download all your Google Drive files'; cannot be "
        "restricted to a folder -- use drive.file + Google Picker instead"
    ),
    "https://www.googleapis.com/auth/drive.metadata.readonly": (
        "metadata for every file in the Drive, still not folder-scopeable"
    ),
}

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"

# Drive caps channel life at one day and defaults to one hour.
DRIVE_CHANNEL_MAX_TTL_SECONDS = 86_400
DRIVE_CHANNEL_DEFAULT_TTL_SECONDS = 3_600

# Values Drive may send in X-Goog-Resource-State.
VALID_RESOURCE_STATES = frozenset(
    {"sync", "add", "remove", "update", "trash", "untrash", "change"}
)

# `sync` is the handshake Drive sends immediately after watch() succeeds. It is
# authentic and means nothing changed.
NO_WORK_STATES = frozenset({"sync"})

_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{5,128}$")

_FILE_FIELDS = "id, name, mimeType, modifiedTime, size, trashed, parents"

# Google Workspace native documents have no bytes to download; they must be
# exported. Mapping only the two that matter for tender packs.
_EXPORT_MIME = {
    "application/vnd.google-apps.document": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".docx",
    ),
    "application/vnd.google-apps.spreadsheet": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xlsx",
    ),
    "application/vnd.google-apps.presentation": ("application/pdf", ".pdf"),
}


def _reject(reason: str, status: int, **kw: Any) -> WebhookVerdict:
    return WebhookVerdict(
        accepted=False,
        reason=reason,
        http_status=status,
        process=False,
        provider=PROVIDER_NAME,
        **kw,
    )


class GoogleDriveProvider(BaseCloudProvider):
    """Drive connection for one deployment. Stateless; all state is in SQLite."""

    name = PROVIDER_NAME
    SCOPES = SCOPES

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        default_callback_url: str | None = None,
    ) -> None:
        self._client_id = client_id or os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
        # Held only to pass to the SDK. Never logged, never returned.
        self._client_secret = client_secret or os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
        self._default_callback_url = default_callback_url or os.environ.get(
            "GOOGLE_DRIVE_WEBHOOK_URL"
        )

    # -----------------------------------------------------------------
    # Config
    # -----------------------------------------------------------------

    def _require_client(self) -> tuple[str, str]:
        if not self._client_id or not self._client_secret:
            raise ProviderConfigError(
                "GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET are not set. "
                "Create an OAuth client of type 'Web application' in the Google "
                "Cloud console and bind both as secrets."
            )
        return self._client_id, self._client_secret

    @staticmethod
    def _require_sdk(module_path: str, package: str) -> Any:
        try:
            module = __import__(module_path, fromlist=["_"])
        except ImportError as exc:
            raise ProviderSDKMissing(
                f"{module_path} is required for this call but is not installed. "
                f"Install it with: pip install {package}"
            ) from exc
        return module

    # -----------------------------------------------------------------
    # OAuth
    # -----------------------------------------------------------------

    def build_authorization_url(
        self, company_id: str, redirect_uri: str, state: str | None = None, **kw: Any
    ) -> dict[str, Any]:
        """
        The consent URL. Pure string construction -- no network, no SDK.

        `access_type=offline` + `prompt=consent` is what produces a refresh
        token. Without both, Google returns a refresh token only on the very
        first consent for that user/client pair, so a user who reconnects
        after revoking gets an access token that dies in an hour and a
        connection that appears to work for exactly 3,600 seconds.

        `state` is a CSRF token. The caller must store it against the user's
        session and compare it on the callback; this returns it rather than
        managing session state it cannot see.
        """
        client_id, _ = self._require_client()
        state = state or secrets.token_urlsafe(24)

        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(self.SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
            "state": state,
        }
        return {
            "url": f"{AUTH_ENDPOINT}?{urlencode(params)}",
            "state": state,
            "scopes": list(self.SCOPES),
            "consent_text": (
                "See, edit, create, and delete only the specific Google Drive "
                "files you use with this app."
            ),
        }

    def connect(
        self, company_id: str, authorization_code: str, redirect_uri: str, **kw: Any
    ) -> dict[str, Any]:
        """
        Exchange the code for tokens and store them encrypted.

        Returns non-secret metadata only. The granted scopes are checked
        against `FORBIDDEN_SCOPES` before anything is stored: if a mis-
        configured console client hands back `drive.readonly`, we refuse the
        connection rather than quietly accept read access to the whole Drive.
        """
        client_id, client_secret = self._require_client()
        flow_module = self._require_sdk("google_auth_oauthlib.flow", "google-auth-oauthlib")

        flow = flow_module.Flow.from_client_config(
            {
                "web": {
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "auth_uri": AUTH_ENDPOINT,
                    "token_uri": TOKEN_ENDPOINT,
                }
            },
            scopes=list(self.SCOPES),
            redirect_uri=redirect_uri,
        )
        flow.fetch_token(code=authorization_code)
        creds = flow.credentials

        granted = tuple(creds.scopes or self.SCOPES)
        over_scoped = [s for s in granted if s in FORBIDDEN_SCOPES]
        if over_scoped:
            raise ProviderError(
                "refusing to store an over-scoped Drive grant: "
                + "; ".join(f"{s} ({FORBIDDEN_SCOPES[s]})" for s in over_scoped)
            )

        token = OAuthToken(
            provider=self.name,
            access_token=creds.token or "",
            refresh_token=creds.refresh_token,
            expires_at=creds.expiry.replace(tzinfo=timezone.utc).timestamp()
            if getattr(creds, "expiry", None)
            else None,
            scopes=granted,
            account_label=kw.get("account_label"),
        )
        stored = token_store.save_token(company_id, token)
        return {**stored, "provider": self.name, "scopes": list(granted)}

    # -----------------------------------------------------------------
    # Credentials / service
    # -----------------------------------------------------------------

    def _credentials(self, company_id: str) -> Any:
        client_id, client_secret = self._require_client()
        creds_module = self._require_sdk(
            "google.oauth2.credentials", "google-api-python-client"
        )
        try:
            token = token_store.load_token(company_id, self.name)
        except token_store.TokenNotFound as exc:
            raise ProviderAuthError(
                f"no Google Drive connection for company_id={company_id!r}"
            ) from exc

        expiry = None
        if token.expires_at:
            expiry = datetime.fromtimestamp(token.expires_at, tz=timezone.utc).replace(
                tzinfo=None
            )

        return creds_module.Credentials(
            token=token.reveal_access_token() or None,
            refresh_token=token.reveal_refresh_token(),
            token_uri=TOKEN_ENDPOINT,
            client_id=client_id,
            client_secret=client_secret,
            scopes=list(token.scopes or self.SCOPES),
            expiry=expiry,
        )

    def _service(self, company_id: str) -> Any:
        discovery = self._require_sdk("googleapiclient.discovery", "google-api-python-client")
        creds = self._credentials(company_id)
        # cache_discovery=False: the default file cache writes into the
        # package directory, which is read-only on Cloud Functions.
        service = discovery.build("drive", "v3", credentials=creds, cache_discovery=False)
        self._persist_if_refreshed(company_id, creds)
        return service

    def _persist_if_refreshed(self, company_id: str, creds: Any) -> None:
        """
        Write back a token the SDK refreshed underneath us.

        Without this, every request pays a refresh round-trip and the stored
        access token is permanently stale.
        """
        try:
            stored = token_store.load_token(company_id, self.name)
        except token_store.TokenNotFound:
            return
        new_access = getattr(creds, "token", None)
        if not new_access or new_access == stored.reveal_access_token():
            return
        expiry = getattr(creds, "expiry", None)
        refreshed = OAuthToken(
            provider=self.name,
            access_token=new_access,
            refresh_token=getattr(creds, "refresh_token", None) or stored.reveal_refresh_token(),
            expires_at=expiry.replace(tzinfo=timezone.utc).timestamp() if expiry else None,
            scopes=stored.scopes,
            account_label=stored.account_label,
            extra=stored.extra,
        )
        token_store.save_token(company_id, refreshed)

    # -----------------------------------------------------------------
    # Channels
    # -----------------------------------------------------------------

    @staticmethod
    def clamp_ttl(requested_seconds: float) -> int:
        """
        Clamp a requested channel lifetime to what Drive will actually grant.

        Asking for more than 86,400s does not fail loudly -- Drive returns a
        shorter expiration than requested and the caller, if it trusts its own
        request, schedules renewal for a channel that is already dead. Clamping
        locally means the number we store is the number we asked for.
        """
        if requested_seconds <= 0:
            return DRIVE_CHANNEL_DEFAULT_TTL_SECONDS
        return int(min(requested_seconds, DRIVE_CHANNEL_MAX_TTL_SECONDS))

    def register_webhook(
        self,
        company_id: str,
        callback_url: str | None = None,
        file_id: str | None = None,
        ttl_seconds: int = DRIVE_CHANNEL_MAX_TTL_SECONDS,
        now: float | None = None,
        **kw: Any,
    ) -> WebhookChannel:
        """
        Register a `files.watch` channel on the folder the user picked.

        `file_id` is a folder id obtained from the Google Picker. Under
        `drive.file` there is no way to obtain it server-side; that is the
        scope working as intended.

        The channel token is a fresh 256-bit secret. Drive echoes it back in
        `X-Goog-Channel-Token` on every notification, which is the only
        authenticity signal Drive provides -- notifications are not signed.
        Only its SHA-256 is stored.
        """
        callback_url = callback_url or self._default_callback_url
        if not callback_url:
            raise ProviderConfigError(
                "no callback URL: pass callback_url or set GOOGLE_DRIVE_WEBHOOK_URL. "
                "It must be HTTPS with a valid certificate on a domain verified "
                "for this Cloud project."
            )
        if not callback_url.lower().startswith("https://"):
            raise ProviderConfigError(
                f"Drive push notifications require an HTTPS callback; got {callback_url!r}"
            )
        if not file_id or not _ID_RE.match(file_id):
            raise ProviderError(
                "file_id must be a Drive folder/file id from the Google Picker; "
                f"got {file_id!r}"
            )

        now = time.time() if now is None else now
        ttl = self.clamp_ttl(ttl_seconds)
        channel_id = "cai-" + secrets.token_urlsafe(24)
        channel_token = channel_registry.new_channel_secret()
        expiration_ms = int((now + ttl) * 1000)

        service = self._service(company_id)
        response = (
            service.files()
            .watch(
                fileId=file_id,
                supportsAllDrives=True,
                body={
                    "id": channel_id,
                    "type": "web_hook",
                    "address": callback_url,
                    "token": channel_token,
                    "expiration": expiration_ms,
                },
            )
            .execute()
        )

        # Trust Drive's expiration over ours -- it is authoritative and may be
        # shorter than requested.
        granted_ms = response.get("expiration")
        expiration_at = float(granted_ms) / 1000.0 if granted_ms else now + ttl

        channel = channel_registry.register_channel(
            channel_id=channel_id,
            provider=self.name,
            company_id=company_id,
            channel_token=channel_token,
            callback_url=callback_url,
            expiration_at=expiration_at,
            resource_id=response.get("resourceId"),
            resource_uri=response.get("resourceUri"),
            watched_file_id=file_id,
            created_at=now,
        )
        # channel_token goes out of scope here and is never persisted raw.
        return channel

    def renew_webhook(self, channel_id: str, now: float | None = None, **kw: Any) -> WebhookChannel:
        """
        Replace a channel that is approaching expiry.

        Drive has no extend/renew operation. This creates a replacement first
        and only then stops the old channel, so the two overlap and no change
        falls between them. If `channels.stop` fails the replacement is still
        live -- a duplicate notification is harmless (processing is
        cursor/checkpoint driven), a gap is not.
        """
        now = time.time() if now is None else now
        old = channel_registry.get_channel(channel_id)
        if old is None:
            raise ProviderError(f"unknown channel_id={channel_id!r}")
        if not old.watched_file_id:
            raise ProviderError(
                f"channel_id={channel_id!r} has no watched_file_id; cannot rebuild it"
            )

        new = self.register_webhook(
            company_id=old.company_id,
            callback_url=old.callback_url,
            file_id=old.watched_file_id,
            ttl_seconds=DRIVE_CHANNEL_MAX_TTL_SECONDS,
            now=now,
        )
        channel_registry.mark_superseded(old.channel_id, new.channel_id)

        try:
            self.stop_channel(old.company_id, old.channel_id, old.resource_id)
        except Exception:
            # Deliberately swallowed and logged. The replacement is live; a
            # lingering old channel costs duplicate deliveries until it expires
            # on its own, which is at most 24 hours and harmless.
            logger.warning(
                "channels.stop failed for old channel_id=%s (replacement %s is live)",
                old.channel_id,
                new.channel_id,
                exc_info=True,
            )
        return new

    def stop_channel(self, company_id: str, channel_id: str, resource_id: str | None) -> None:
        if not resource_id:
            return
        service = self._service(company_id)
        service.channels().stop(body={"id": channel_id, "resourceId": resource_id}).execute()

    # -----------------------------------------------------------------
    # Webhook verification
    # -----------------------------------------------------------------

    def verify_webhook(
        self, headers: dict[str, str], raw_body: bytes = b"", now: float | None = None
    ) -> WebhookVerdict:
        """
        Decide whether an inbound Drive notification is genuine.

        Drive does NOT sign push notifications. There is no HMAC and no
        certificate pinning available to the receiver. Authenticity rests
        entirely on the channel token we chose at registration being echoed
        back, so the token check is not optional hardening -- it is the whole
        authentication mechanism. A receiver that checks only
        `X-Goog-Channel-ID` is accepting anything from anyone who can guess or
        observe a channel id.

        Checks, in this order:

          1. required headers present               -> 400
          2. channel id is one we registered        -> 404
          3. channel token matches the stored hash  -> 403   (constant time)
          4. resource id matches the registration   -> 403
          5. resource state is a value Drive sends  -> 400
          6. channel has not expired / been stopped -> 410
          7. message number is newer than the last  -> 200, not processed
          8. `sync` handshake                       -> 200, not processed

        No body parsing happens at any point. Drive notification bodies are
        empty or trivial; everything meaningful is in the headers, and the
        body is attacker-controlled until step 3 passes.
        """
        now = time.time() if now is None else now
        h = self._header

        channel_id = h(headers, "X-Goog-Channel-ID")
        resource_state = h(headers, "X-Goog-Resource-State")
        resource_id = h(headers, "X-Goog-Resource-ID")
        presented_token = h(headers, "X-Goog-Channel-Token")
        message_number_raw = h(headers, "X-Goog-Message-Number")

        if not channel_id or not resource_state:
            return _reject("missing_required_headers", 400,
                           detail="X-Goog-Channel-ID and X-Goog-Resource-State are required")

        channel = channel_registry.get_channel(channel_id)
        if channel is None or channel.provider != self.name:
            # 404 so Drive abandons a channel we have no record of. Channel ids
            # are 192-bit random strings, so this leaks nothing enumerable.
            return _reject("unknown_channel", 404, channel_id=channel_id)

        stored_digest = channel_registry.get_channel_token_digest(channel_id)
        if not channel_registry.token_matches(presented_token, stored_digest):
            return _reject(
                "channel_token_mismatch",
                403,
                channel_id=channel_id,
                company_id=channel.company_id,
                detail="X-Goog-Channel-Token missing or does not match registration",
            )

        if channel.resource_id and resource_id and resource_id != channel.resource_id:
            return _reject(
                "resource_id_mismatch",
                403,
                channel_id=channel_id,
                company_id=channel.company_id,
            )

        if resource_state not in VALID_RESOURCE_STATES:
            return _reject(
                "invalid_resource_state",
                400,
                channel_id=channel_id,
                company_id=channel.company_id,
                resource_state=resource_state,
            )

        if channel.status not in ("active", "superseded"):
            return _reject(
                "channel_not_active",
                410,
                channel_id=channel_id,
                company_id=channel.company_id,
                detail=f"channel status is {channel.status!r}",
            )

        if channel.expiration_at <= now:
            channel_registry.mark_expired(channel_id)
            return _reject(
                "channel_expired",
                410,
                channel_id=channel_id,
                company_id=channel.company_id,
                detail="notification arrived after the channel's expiration",
            )

        try:
            message_number = int(message_number_raw) if message_number_raw is not None else None
        except (TypeError, ValueError):
            return _reject(
                "invalid_message_number",
                400,
                channel_id=channel_id,
                company_id=channel.company_id,
            )

        if message_number is None:
            return _reject(
                "missing_message_number",
                400,
                channel_id=channel_id,
                company_id=channel.company_id,
            )

        if message_number <= channel.last_message_number:
            # 200: we have already handled this message. Returning an error
            # would make Drive retry the duplicate we just refused.
            return _reject(
                "stale_or_replayed_message",
                200,
                channel_id=channel_id,
                company_id=channel.company_id,
                resource_state=resource_state,
                detail=(
                    f"message number {message_number} is not newer than the last "
                    f"accepted ({channel.last_message_number})"
                ),
            )

        channel_registry.record_message_number(channel_id, message_number)

        if resource_state in NO_WORK_STATES:
            return WebhookVerdict(
                accepted=True,
                reason="sync_handshake",
                http_status=200,
                process=False,
                provider=self.name,
                channel_id=channel_id,
                company_id=channel.company_id,
                resource_state=resource_state,
            )

        return WebhookVerdict(
            accepted=True,
            reason="ok",
            http_status=200,
            process=True,
            provider=self.name,
            channel_id=channel_id,
            company_id=channel.company_id,
            resource_state=resource_state,
        )

    # -----------------------------------------------------------------
    # Data
    # -----------------------------------------------------------------

    def list_changed_files(
        self,
        company_id: str,
        folder_id: str | None = None,
        since: float | str | None = None,
        page_size: int = 100,
        **kw: Any,
    ) -> list[ChangedFile]:
        """
        Files in the picked folder modified since `since`.

        Under `drive.file` this returns only files the user granted through
        the Picker, which is the point: a query that would otherwise walk the
        whole Drive simply cannot.

        `folder_id` is interpolated into the Drive query string, so it is
        validated against `_ID_RE` first -- a folder id containing a quote
        would otherwise let a caller rewrite the query.
        """
        if folder_id is not None and not _ID_RE.match(folder_id):
            raise ProviderError(f"invalid folder_id: {folder_id!r}")

        clauses = ["trashed = false"]
        if folder_id:
            clauses.append(f"'{folder_id}' in parents")
        if since is not None:
            if isinstance(since, (int, float)):
                stamp = datetime.fromtimestamp(float(since), tz=timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                )
            else:
                stamp = str(since)
            if "'" in stamp:
                raise ProviderError(f"invalid `since` timestamp: {since!r}")
            clauses.append(f"modifiedTime > '{stamp}'")

        service = self._service(company_id)
        out: list[ChangedFile] = []
        page_token = None
        while True:
            response = (
                service.files()
                .list(
                    q=" and ".join(clauses),
                    spaces="drive",
                    fields=f"nextPageToken, files({_FILE_FIELDS})",
                    pageSize=min(int(page_size), 1000),
                    pageToken=page_token,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                )
                .execute()
            )
            for item in response.get("files", []):
                out.append(
                    ChangedFile(
                        file_id=item["id"],
                        name=item.get("name", item["id"]),
                        mime_type=item.get("mimeType"),
                        modified_time=item.get("modifiedTime"),
                        size=int(item["size"]) if item.get("size") else None,
                        removed=bool(item.get("trashed")),
                        provider=self.name,
                    )
                )
            page_token = response.get("nextPageToken")
            if not page_token:
                break

        logger.info(
            "drive change list company_id=%s folder_id=%s files=%d",
            company_id,
            folder_id,
            len(out),
        )
        return out

    def download_file(
        self,
        company_id: str,
        file_id: str,
        dest_dir: Path | None = None,
        max_bytes: int = MAX_DOWNLOAD_BYTES,
        **kw: Any,
    ) -> Path:
        """
        Download one file to a non-servable directory.

        Three guards, all of which have a concrete failure behind them:

        * `safe_download_path` sanitises the provider-supplied filename and
          re-asserts that the destination is not under `static/` or
          `firebase_public/`. A tender document pulled from a private Drive
          folder must not become a public URL.
        * `max_bytes` is checked against the declared size before the transfer
          and against the actual bytes during it, because the declared size is
          a third party's claim.
        * Google Workspace native files have no bytes; `files.get_media` fails
          on them. They are exported instead, to the format in `_EXPORT_MIME`.
        """
        io_module = self._require_sdk("io", "python")  # stdlib; keeps one code path
        http_module = self._require_sdk("googleapiclient.http", "google-api-python-client")

        service = self._service(company_id)
        meta = (
            service.files()
            .get(fileId=file_id, fields=_FILE_FIELDS, supportsAllDrives=True)
            .execute()
        )
        name = meta.get("name") or file_id
        mime = meta.get("mimeType") or ""
        declared = int(meta["size"]) if meta.get("size") else None

        if declared is not None and declared > max_bytes:
            raise ProviderError(
                f"refusing to download {name!r}: {declared} bytes exceeds the "
                f"{max_bytes} byte limit"
            )

        if mime.startswith("application/vnd.google-apps."):
            export = _EXPORT_MIME.get(mime)
            if export is None:
                raise ProviderError(
                    f"{name!r} is a Google Workspace file of type {mime!r} with no "
                    "export mapping; it cannot be downloaded"
                )
            export_mime, suffix = export
            if not name.lower().endswith(suffix):
                name += suffix
            request = service.files().export_media(fileId=file_id, mimeType=export_mime)
        else:
            request = service.files().get_media(fileId=file_id, supportsAllDrives=True)

        target = safe_download_path(name, dest_dir)
        buffer = io_module.BytesIO()
        downloader = http_module.MediaIoBaseDownload(buffer, request, chunksize=1024 * 1024)
        done = False
        while not done:
            _, done = downloader.next_chunk()
            if buffer.tell() > max_bytes:
                raise ProviderError(
                    f"aborting download of {name!r}: exceeded {max_bytes} bytes mid-transfer"
                )

        target.write_bytes(buffer.getvalue())
        logger.info(
            "drive file downloaded company_id=%s file_id=%s bytes=%d",
            company_id,
            file_id,
            target.stat().st_size,
        )
        return target
