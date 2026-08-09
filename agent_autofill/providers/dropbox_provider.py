"""
Dropbox provider: app-folder OAuth, HMAC-verified webhooks, cursor-driven sync.

=============================================================================
UNVERIFIED -- NOTHING HERE HAS TOUCHED A LIVE DROPBOX ACCOUNT
=============================================================================
No Dropbox app is registered, no test account exists, and completing the OAuth
consent flow requires entering credentials, which was prohibited in the
environment this was written in. The consent flow, the webhook URL enablement
handshake, and `/files/list_folder/continue` have never been run.

What HAS been tested offline, with literal output: `X-Dropbox-Signature`
verification against crafted valid and invalid payloads, the challenge
response, unknown-account handling, replay suppression, and token encryption.
See `providers/VERIFICATION.md` for what a human must run.

=============================================================================
PERMISSION MODEL: APP FOLDER ONLY
=============================================================================
Dropbox's blast radius is decided by the app's **access type**, chosen once at
app creation in the App Console and immutable afterwards:

    App folder  -- the app gets a single dedicated folder inside the user's
                   Dropbox (`/Apps/<AppName>/`) and can see nothing outside it.
    Full Dropbox -- everything the user has.

This app MUST be created as "App folder". That is a console setting, not a
scope string: **no token inspection can confirm it**, and this module cannot
enforce it in code. It is therefore a manual verification step, item 4 in
VERIFICATION.md, and it is the single most important one -- getting it wrong
grants a procurement assistant read access to the user's entire Dropbox and
cannot be corrected without recreating the app.

On top of that, only two scopes are requested:

    files.metadata.read   list the folder, receive change notifications
    files.content.read    download a tender document

No write scope. This system produces drafts locally; it has no business
putting anything back into the user's Dropbox.

Under app-folder access every path is relative to the app folder root, so
`files_list_folder("")` means "the app folder", not "the whole Dropbox".

=============================================================================
WEBHOOKS ARE NOTIFY-ONLY -- THE BODY TELLS YOU ALMOST NOTHING
=============================================================================
A Dropbox webhook POST does not describe the change. It says, in effect,
"something moved for these accounts":

    {"list_folder": {"accounts": ["dbid:AAA..."]},
     "delta": {"users": [12345]}}

The actual change set comes from calling `/2/files/list_folder/continue` with
the cursor stored for that account. That has two consequences worth stating:

* Processing is **idempotent by construction**. Replaying a notification just
  re-runs `continue` against the same cursor and yields the same (possibly
  empty) result. The replay cache below is defence in depth, not correctness.
* The cursor is the checkpoint. Losing it means a full re-list, not data loss;
  advancing it before the work is done means silently skipping changes. It is
  therefore only saved after the entries have been handed off.

=============================================================================
SIGNATURE VERIFICATION HAPPENS BEFORE PARSING. ALWAYS.
=============================================================================
`X-Dropbox-Signature` is HMAC-SHA256 of the **raw request body** keyed with
the app secret, hex-encoded. It must be computed over the bytes exactly as
received -- not over a re-serialised dict, which will differ in key order and
whitespace and fail for every legitimate request.

`verify_webhook()` computes and compares the HMAC before `json.loads` is
reached. Until that comparison passes, the body is attacker-controlled input
and handing it to a parser is the entire class of bug this ordering exists to
prevent. Comparison uses `hmac.compare_digest`; `==` on an HMAC leaks the
prefix through timing and turns forgery into a few thousand requests.

=============================================================================
SDK DEPENDENCY: LAZY
=============================================================================
`dropbox` is in requirements.txt but imported inside functions only, so this
module imports and every verification path is testable without it. The OAuth
authorize URL and the token exchange use `urlencode`/`requests` rather than
`dropbox.DropboxOAuth2Flow`, because that helper wants to own CSRF state in a
session object this layer does not have; state is generated here and handed to
the caller to bind to its own session.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import time
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
    "DropboxProvider",
    "DROPBOX_SIGNATURE_HEADER",
]

logger = logging.getLogger("agent_autofill.providers")

PROVIDER_NAME = "dropbox"

SCOPES: tuple[str, ...] = ("files.metadata.read", "files.content.read")

# Scopes this package refuses to request. Written out so widening the grant
# requires deleting a stated refusal.
FORBIDDEN_SCOPES: dict[str, str] = {
    "files.content.write": "this system never writes back to the user's Dropbox",
    "files.metadata.write": "this system never modifies the user's Dropbox metadata",
    "sharing.write": "no share links are ever created on the user's behalf",
    "account_info.write": "no account modification, ever",
}

AUTH_ENDPOINT = "https://www.dropbox.com/oauth2/authorize"
TOKEN_ENDPOINT = "https://api.dropboxapi.com/oauth2/token"

DROPBOX_SIGNATURE_HEADER = "X-Dropbox-Signature"

# Hex SHA-256 is exactly 64 characters. Anything else is not a signature.
_SIGNATURE_LENGTH = 64

# Dropbox's own guidance: respond to the verification GET with the challenge
# echoed as text/plain and nosniff. Without nosniff a browser can be coaxed
# into interpreting an attacker-chosen challenge as HTML on our origin.
CHALLENGE_HEADERS = {
    "Content-Type": "text/plain",
    "X-Content-Type-Options": "nosniff",
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


class DropboxProvider(BaseCloudProvider):
    """Dropbox connection for one deployment. All state lives in SQLite."""

    name = PROVIDER_NAME
    SCOPES = SCOPES

    def __init__(self, app_key: str | None = None, app_secret: str | None = None) -> None:
        self._app_key = app_key or os.environ.get("DROPBOX_APP_KEY")
        # The webhook HMAC key. Never logged, never returned, never stored.
        self._app_secret = app_secret or os.environ.get("DROPBOX_APP_SECRET")

    # -----------------------------------------------------------------
    # Config
    # -----------------------------------------------------------------

    def _require_app(self) -> tuple[str, str]:
        if not self._app_key or not self._app_secret:
            raise ProviderConfigError(
                "DROPBOX_APP_KEY / DROPBOX_APP_SECRET are not set. Create an app "
                "in the Dropbox App Console with access type 'App folder' and "
                "bind both as secrets."
            )
        return self._app_key, self._app_secret

    @staticmethod
    def _require_sdk(module_path: str, package: str) -> Any:
        try:
            return __import__(module_path, fromlist=["_"])
        except ImportError as exc:
            raise ProviderSDKMissing(
                f"{module_path} is required for this call but is not installed. "
                f"Install it with: pip install {package}"
            ) from exc

    # -----------------------------------------------------------------
    # OAuth
    # -----------------------------------------------------------------

    def build_authorization_url(
        self, company_id: str, redirect_uri: str, state: str | None = None,
        code_challenge: str | None = None,
        code_challenge_method: str = "S256", **kw: Any
    ) -> dict[str, Any]:
        """
        The consent URL. Pure string construction -- no network, no SDK.

        `token_access_type=offline` is what makes Dropbox return a refresh
        token. Omit it and you get a short-lived access token only, and the
        connection dies roughly four hours later with no way to recover it
        without sending the user back through consent.
        """
        app_key, _ = self._require_app()
        state = state or secrets.token_urlsafe(24)

        params = {
            "client_id": app_key,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "token_access_type": "offline",
            "scope": " ".join(self.SCOPES),
            "state": state,
        }
        # PKCE — same reasoning as the Drive provider: it makes an intercepted
        # or logged authorization code useless on its own.
        if code_challenge:
            params["code_challenge"] = code_challenge
            params["code_challenge_method"] = code_challenge_method
        return {
            "url": f"{AUTH_ENDPOINT}?{urlencode(params)}",
            "state": state,
            "scopes": list(self.SCOPES),
            "access_type_note": (
                "The app must be registered with access type 'App folder'. This "
                "cannot be asserted from the token and is a manual check."
            ),
        }

    def connect(
        self, company_id: str, authorization_code: str, redirect_uri: str,
        code_verifier: str | None = None, **kw: Any
    ) -> dict[str, Any]:
        """
        Exchange the code for tokens and store them encrypted.

        Returns non-secret metadata only. Any granted scope in
        `FORBIDDEN_SCOPES` aborts the connection before storage -- a
        misconfigured console app that hands back a write scope is a
        configuration error, not something to accept and ignore.
        """
        app_key, app_secret = self._require_app()
        requests = self._require_sdk("requests", "requests")

        response = requests.post(
            TOKEN_ENDPOINT,
            data={
                "code": authorization_code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
                # Omitted entirely when absent rather than sent empty: Dropbox
                # rejects a blank code_verifier outright.
                **({"code_verifier": code_verifier} if code_verifier else {}),
            },
            auth=(app_key, app_secret),
            timeout=30,
        )
        if response.status_code != 200:
            # Deliberately does not echo the response body: a failed token
            # exchange can quote the submitted code back.
            raise ProviderAuthError(
                f"Dropbox token exchange failed with HTTP {response.status_code}"
            )
        payload = response.json()

        granted = tuple((payload.get("scope") or " ".join(self.SCOPES)).split())
        over_scoped = [s for s in granted if s in FORBIDDEN_SCOPES]
        if over_scoped:
            raise ProviderError(
                "refusing to store an over-scoped Dropbox grant: "
                + "; ".join(f"{s} ({FORBIDDEN_SCOPES[s]})" for s in over_scoped)
            )

        expires_in = payload.get("expires_in")
        token = OAuthToken(
            provider=self.name,
            access_token=payload.get("access_token", ""),
            refresh_token=payload.get("refresh_token"),
            expires_at=(time.time() + float(expires_in)) if expires_in else None,
            scopes=granted,
            account_label=payload.get("account_id"),
            extra={"account_id": payload.get("account_id")},
        )
        stored = token_store.save_token(company_id, token)

        account_id = payload.get("account_id")
        if account_id:
            # Bind account -> company now, so a webhook for this account can be
            # attributed. The cursor is filled in by `start_sync()`.
            channel_registry.save_cursor(account_id, company_id, cursor="")

        return {**stored, "provider": self.name, "scopes": list(granted), "account_id": account_id}

    def _client(self, company_id: str) -> Any:
        app_key, app_secret = self._require_app()
        dropbox_sdk = self._require_sdk("dropbox", "dropbox")
        try:
            token = token_store.load_token(company_id, self.name)
        except token_store.TokenNotFound as exc:
            raise ProviderAuthError(
                f"no Dropbox connection for company_id={company_id!r}"
            ) from exc

        return dropbox_sdk.Dropbox(
            oauth2_access_token=token.reveal_access_token() or None,
            oauth2_refresh_token=token.reveal_refresh_token(),
            oauth2_access_token_expiration=None,
            app_key=app_key,
            app_secret=app_secret,
        )

    # -----------------------------------------------------------------
    # "Webhooks"
    # -----------------------------------------------------------------
    # Dropbox has no per-connection channel: one webhook URI is configured in
    # the App Console for the whole app and fires for every connected account.
    # There is nothing to register per company and nothing that expires, so
    # `register_webhook` records the account->cursor binding instead, and
    # `renew_webhook` is a documented no-op. Both return a WebhookChannel so
    # the interface stays uniform for the caller.

    def register_webhook(
        self, company_id: str, callback_url: str | None = None, **kw: Any
    ) -> WebhookChannel:
        """
        Establish the sync checkpoint for this company's Dropbox account.

        There is no API call that creates a Dropbox webhook -- the URI is set
        once in the App Console and Dropbox verifies it with a GET challenge
        (see `challenge_response`). What this does instead is take the initial
        `list_folder` cursor, without which the first notification has nothing
        to continue from and the change set is unrecoverable.
        """
        account_id = kw.get("account_id")
        cursor = kw.get("cursor")
        if not cursor:
            cursor, account_id = self._initial_cursor(company_id, kw.get("path", ""))
        if not account_id:
            raise ProviderError("register_webhook needs an account_id for Dropbox")

        channel_registry.save_cursor(account_id, company_id, cursor)
        now = time.time()
        logger.info(
            "dropbox sync checkpoint established company_id=%s account_id=%s",
            company_id,
            account_id,
        )
        return WebhookChannel(
            channel_id=f"dropbox:{account_id}",
            provider=self.name,
            company_id=company_id,
            resource_id=account_id,
            resource_uri=None,
            watched_file_id=None,
            callback_url=callback_url or "",
            created_at=now,
            # Dropbox webhooks do not expire. Represented as +infinity so the
            # renewal cron's "expiring soon" query can never select it.
            expiration_at=float("inf"),
        )

    def renew_webhook(self, channel_id: str, **kw: Any) -> WebhookChannel:
        """
        No-op by design.

        Dropbox webhook registrations do not expire; only the OAuth access
        token does, and the SDK refreshes that from the stored refresh token.
        Raising here would make a shared renewal cron fail on Dropbox rows, so
        this returns the existing binding unchanged.
        """
        account_id = channel_id.split(":", 1)[-1]
        row = channel_registry.get_cursor_row(account_id)
        if row is None:
            raise ProviderError(f"unknown Dropbox account binding: {channel_id!r}")
        return WebhookChannel(
            channel_id=channel_id,
            provider=self.name,
            company_id=row["company_id"],
            resource_id=account_id,
            resource_uri=None,
            watched_file_id=None,
            callback_url="",
            created_at=row["updated_at"],
            expiration_at=float("inf"),
        )

    # -----------------------------------------------------------------
    # Webhook verification
    # -----------------------------------------------------------------

    def challenge_response(self, challenge: str | None) -> tuple[str, dict[str, str], int]:
        """
        Answer Dropbox's webhook-URI verification GET.

        Dropbox sends `?challenge=<value>` and expects it echoed verbatim.
        Returned as `(body, headers, status)` so the transport layer stays
        replaceable. The headers are not cosmetic: without
        `X-Content-Type-Options: nosniff` a browser may sniff an attacker-
        supplied challenge as HTML and execute it on this origin, and this
        endpoint is unauthenticated by definition.
        """
        if challenge is None:
            return ("missing challenge", dict(CHALLENGE_HEADERS), 400)
        return (str(challenge), dict(CHALLENGE_HEADERS), 200)

    def compute_signature(self, raw_body: bytes) -> str:
        """HMAC-SHA256 of the raw body, hex, keyed with the app secret."""
        _, app_secret = self._require_app()
        return hmac.new(app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()

    def verify_webhook(
        self, headers: dict[str, str], raw_body: bytes, now: float | None = None
    ) -> WebhookVerdict:
        """
        Authenticate an inbound Dropbox notification, then interpret it.

        Order is the point:

          1. `X-Dropbox-Signature` present and 64 hex chars   -> 403 / 400
          2. HMAC-SHA256(app_secret, RAW BODY) matches,
             compared with `hmac.compare_digest`               -> 403
          --- only now is the body treated as data at all ---
          3. body parses as JSON with a `list_folder.accounts` -> 400
          4. duplicate delivery inside the replay window       -> 200, ignored
          5. at least one account has a stored cursor          -> 200, ignored

        Steps 4 and 5 return 200 deliberately. A replay is something we have
        already handled and a notification for an account we do not manage is
        not an error on Dropbox's side; a non-2xx makes Dropbox retry, which
        turns both into repeated load for no benefit.
        """
        now = time.time() if now is None else now
        presented = self._header(headers, DROPBOX_SIGNATURE_HEADER)

        if not presented:
            return _reject(
                "missing_signature",
                403,
                detail=f"{DROPBOX_SIGNATURE_HEADER} header absent",
            )

        presented = presented.strip().lower()
        if len(presented) != _SIGNATURE_LENGTH or not all(
            c in "0123456789abcdef" for c in presented
        ):
            return _reject(
                "malformed_signature",
                400,
                detail=f"{DROPBOX_SIGNATURE_HEADER} is not 64 hex characters",
            )

        expected = self.compute_signature(raw_body or b"")
        if not hmac.compare_digest(presented, expected):
            return _reject(
                "signature_mismatch",
                403,
                detail="HMAC-SHA256 over the raw body did not match",
            )

        # ---- authenticated; the body may now be parsed -------------------
        try:
            payload = json.loads((raw_body or b"").decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return _reject("malformed_body", 400, detail="body is not valid UTF-8 JSON")

        if not isinstance(payload, dict):
            return _reject("malformed_body", 400, detail="body is not a JSON object")

        accounts = (payload.get("list_folder") or {}).get("accounts")
        if not isinstance(accounts, list) or not accounts:
            return _reject(
                "no_accounts_in_payload",
                400,
                detail="expected list_folder.accounts to be a non-empty array",
            )
        accounts = [str(a) for a in accounts]

        # Replay suppression keyed on a hash of the signature. The signature
        # itself is not stored -- it is an HMAC under the app secret, and a
        # database full of (body, HMAC) pairs is an offline attack on that key.
        delivery_digest = hashlib.sha256(presented.encode("ascii")).hexdigest()
        if channel_registry.seen_recently(delivery_digest, self.name, now=now):
            return _reject(
                "replayed_delivery",
                200,
                accounts=tuple(accounts),
                detail=(
                    "an identical signed body was already accepted within "
                    f"{channel_registry.REPLAY_WINDOW_SECONDS}s"
                ),
            )

        known: list[str] = []
        company_id: str | None = None
        for account_id in accounts:
            row = channel_registry.get_cursor_row(account_id)
            if row is not None:
                known.append(account_id)
                company_id = company_id or row["company_id"]

        if not known:
            return _reject(
                "unknown_account",
                200,
                accounts=tuple(accounts),
                detail="no stored cursor for any account in the notification",
            )

        return WebhookVerdict(
            accepted=True,
            reason="ok",
            http_status=200,
            process=True,
            provider=self.name,
            company_id=company_id,
            accounts=tuple(known),
        )

    # -----------------------------------------------------------------
    # Data
    # -----------------------------------------------------------------

    def _initial_cursor(self, company_id: str, path: str = "") -> tuple[str, str | None]:
        """
        Take the first cursor for the app folder.

        `path=""` is the app folder root under app-folder access. The listing
        itself is discarded: the cursor marks "everything up to now is known",
        which is what makes the first notification meaningful.
        """
        client = self._client(company_id)
        account_id = None
        try:
            account_id = client.users_get_current_account().account_id
        except Exception:  # pragma: no cover - network path
            logger.warning("could not read dropbox account id company_id=%s", company_id)
        result = client.files_list_folder(path, recursive=True)
        while result.has_more:
            result = client.files_list_folder_continue(result.cursor)
        return result.cursor, account_id

    def list_changed_files(
        self, company_id: str, account_id: str | None = None, **kw: Any
    ) -> list[ChangedFile]:
        """
        Drain `/files/list_folder/continue` from the stored cursor.

        This is the call the webhook exists to trigger -- the notification
        itself carries no change information.

        The cursor is advanced only after every page has been collected. If
        this raises halfway through, the stored cursor is unchanged and the
        next notification replays the same range: duplicated work, no missed
        document. The reverse ordering loses tender documents silently, which
        is the failure this system cannot have.
        """
        dropbox_files = self._require_sdk("dropbox.files", "dropbox")

        if account_id is None:
            raise ProviderError("list_changed_files needs an account_id for Dropbox")
        cursor = channel_registry.get_cursor(account_id)
        if not cursor:
            raise ProviderError(
                f"no stored cursor for account_id={account_id!r}; call "
                "register_webhook() to establish one before processing webhooks"
            )

        client = self._client(company_id)
        out: list[ChangedFile] = []
        result = client.files_list_folder_continue(cursor)
        while True:
            for entry in result.entries:
                if isinstance(entry, dropbox_files.FileMetadata):
                    out.append(
                        ChangedFile(
                            file_id=entry.id,
                            name=entry.name,
                            modified_time=getattr(entry, "server_modified", None)
                            and entry.server_modified.isoformat(),
                            size=entry.size,
                            path=entry.path_lower,
                            provider=self.name,
                        )
                    )
                elif isinstance(entry, dropbox_files.DeletedMetadata):
                    out.append(
                        ChangedFile(
                            file_id=entry.path_lower or entry.name,
                            name=entry.name,
                            path=entry.path_lower,
                            removed=True,
                            provider=self.name,
                        )
                    )
                # FolderMetadata is intentionally ignored: a folder is not a
                # tender document and creating one changes nothing to extract.
            if not result.has_more:
                break
            result = client.files_list_folder_continue(result.cursor)

        channel_registry.save_cursor(account_id, company_id, result.cursor)
        logger.info(
            "dropbox change list company_id=%s account_id=%s files=%d",
            company_id,
            account_id,
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

        `file_id` may be a Dropbox file id (`id:...`) or an app-folder-relative
        path; both are accepted by `files_get_metadata`/`files_download`.
        """
        client = self._client(company_id)
        meta = client.files_get_metadata(file_id)
        size = getattr(meta, "size", None)
        if size is not None and size > max_bytes:
            raise ProviderError(
                f"refusing to download {meta.name!r}: {size} bytes exceeds the "
                f"{max_bytes} byte limit"
            )

        target = safe_download_path(getattr(meta, "name", file_id), dest_dir)
        _, response = client.files_download(file_id)
        content = response.content
        if len(content) > max_bytes:
            raise ProviderError(
                f"aborting download of {target.name!r}: exceeded {max_bytes} bytes"
            )
        target.write_bytes(content)
        logger.info(
            "dropbox file downloaded company_id=%s bytes=%d", company_id, len(content)
        )
        return target
