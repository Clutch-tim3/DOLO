"""
The HTTP surface for connecting a Drive or Dropbox account.

The providers could already build a consent URL and exchange a code. Nothing
called either, so there was no way for a user to connect anything and the
`state` CSRF parameter both providers generate was never stored or checked.
This is the missing half.

    GET  /api/autofill/providers/{provider}/connect?company_id=...
         -> 302 to the provider's consent screen
    GET  /api/autofill/providers/{provider}/callback?code=...&state=...
         -> exchanges the code, stores encrypted tokens, redirects back
    GET  /api/autofill/providers/status?company_id=...
    POST /api/autofill/providers/{provider}/disconnect

WHAT THE CALLBACK TRUSTS
------------------------
Only the `state`. The company the account is attached to comes from the stored
record, never from the callback's own parameters — otherwise anyone who
completed a legitimate consent for their own account could replay the callback
with someone else's company_id and attach their Drive to that company.

The authorization code is never logged, never echoed in a redirect, and never
put in an error message. It is a single-use credential for the few seconds
before it is exchanged, and a code in a log line is a code in a log aggregator.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from agent_autofill.providers import oauth_state

logger = logging.getLogger("agent_autofill.providers.oauth")

router = APIRouter(prefix="/api/autofill/providers", tags=["agent-autofill-providers"])

#: Where the user is sent after the round trip, with a result flag. Kept on our
#: own site: an open redirect here would let a phishing page borrow our domain.
RETURN_PATH = "/workspace"

PROVIDERS = ("google_drive", "dropbox")


def _provider(name: str):
    """Instantiate by name. Imported lazily so a missing SDK is a 503, not an
    import-time failure that takes the whole app down."""
    if name == "google_drive":
        from agent_autofill.providers.google_drive_provider import GoogleDriveProvider

        return GoogleDriveProvider()
    if name == "dropbox":
        from agent_autofill.providers.dropbox_provider import DropboxProvider

        return DropboxProvider()
    raise HTTPException(status_code=404, detail="Unknown provider")


#: The public origin users reach the app on, e.g. "https://cairoai.web.app".
#: Set this in production. Deriving the origin from the request does NOT work
#: behind Firebase Hosting: TLS terminates at the edge and the rewrite forwards
#: to Cloud Run, so `request.base_url` reports the backend's own scheme and
#: host — "http://api-xxxx-uc.a.run.app". Google rejects that twice over, for
#: being http and for not matching the registered URI.
PUBLIC_BASE_URL_ENV = "PUBLIC_BASE_URL"


def _callback_url(request: Request, provider: str) -> str:
    """
    The redirect_uri. Must match the provider's console registration exactly.

    Configured origin first; the live request only as a local-development
    fallback. The scheme is forced to https for anything that is not localhost,
    because a proxied request arrives as http and an http redirect_uri is
    refused outright.
    """
    import os
    from urllib.parse import urlparse

    base = (os.environ.get(PUBLIC_BASE_URL_ENV) or "").strip().rstrip("/")

    if not base:
        # Honour the forwarding headers before falling back to what the socket
        # says, so a correctly-configured proxy still produces the right origin.
        forwarded_host = request.headers.get("x-forwarded-host")
        forwarded_proto = request.headers.get("x-forwarded-proto")
        parsed = urlparse(str(request.base_url))
        host = forwarded_host or parsed.netloc
        scheme = forwarded_proto or parsed.scheme
        if host.split(":")[0] not in ("localhost", "127.0.0.1"):
            scheme = "https"
        base = f"{scheme}://{host}"

    return f"{base}/api/autofill/providers/{provider}/callback"


def _back(ok: bool, message: str) -> RedirectResponse:
    """
    Send the user back to the app with a result.

    A fixed internal path with a query flag, never a URL from the request — a
    callback that redirects wherever it is told is an open redirect, and this
    one sits on a domain users are being asked to trust with Drive access.
    """
    return RedirectResponse(
        url=f"{RETURN_PATH}?{urlencode({'connected' if ok else 'connect_error': message})}",
        status_code=303,
    )


@router.get("/{provider}/connect")
async def start_connection(provider: str, request: Request,
                           company_id: str = Query(...)) -> RedirectResponse:
    """Begin consent. Records the state, then hands off to the provider."""
    if provider not in PROVIDERS:
        raise HTTPException(status_code=404, detail="Unknown provider")

    impl = _provider(provider)
    redirect_uri = _callback_url(request, provider)
    flow = oauth_state.issue(company_id, provider, redirect_uri)

    try:
        built = impl.build_authorization_url(
            company_id, redirect_uri,
            state=flow["state"],
            code_challenge=flow["code_challenge"],
            code_challenge_method=flow["code_challenge_method"],
        )
    except Exception as exc:
        # Almost always a missing client id/secret. Surfaced as 503 with the
        # provider's own wording, which names the environment variable.
        logger.warning("cannot build %s authorization url: %s", provider, exc)
        raise HTTPException(status_code=503, detail=str(exc))

    return RedirectResponse(url=built["url"], status_code=302)


@router.get("/{provider}/callback")
async def finish_connection(provider: str, request: Request,
                            code: str | None = Query(default=None),
                            state: str | None = Query(default=None),
                            error: str | None = Query(default=None)):
    """
    Complete consent.

    Every failure path returns the user to the app with a message rather than
    an HTTP error page: this URL is reached by a redirect from Google or
    Dropbox, and a raw 400 in the browser at that point is indistinguishable
    from the app being broken.
    """
    if provider not in PROVIDERS:
        raise HTTPException(status_code=404, detail="Unknown provider")

    if error:
        # The user pressed Cancel, or the provider refused. Their wording is
        # not ours to interpret; it is logged and summarised.
        logger.info("%s consent not granted: %s", provider, error)
        return _back(False, "Access was not granted.")

    try:
        flow = oauth_state.consume(state or "", provider)
    except oauth_state.OAuthStateError as exc:
        # Deliberately not distinguishing "unknown" from "expired" from "wrong
        # provider" to the caller. All three mean the same thing to a user, and
        # the difference is only useful to someone probing.
        logger.warning("rejected %s callback: %s", provider, exc)
        return _back(False, "That connection attempt is no longer valid. Try again.")

    if not code:
        return _back(False, "No authorization code was returned.")

    impl = _provider(provider)
    try:
        # company_id comes from the STORED flow. Never from the URL — see the
        # module docstring for what that would allow.
        result = impl.connect(flow["company_id"], code, flow["redirect_uri"],
                              code_verifier=flow["code_verifier"])
    except Exception as exc:
        # The code must not reach the log, and neither must anything derived
        # from it. exc is provider wording about scopes or client config.
        logger.warning("%s token exchange failed for company %s: %s",
                       provider, flow["company_id"], exc)
        return _back(False, "Could not complete the connection.")

    logger.info("%s connected for company %s", provider, flow["company_id"])
    return _back(True, str(result.get("account_label") or provider))


@router.get("/status")
async def connection_status(company_id: str = Query(...)) -> dict[str, Any]:
    """What this company has connected. Never includes a token."""
    from agent_autofill.providers.token_store import list_connections

    return {"company_id": company_id, "connections": list_connections(company_id)}


@router.post("/{provider}/disconnect")
async def disconnect(provider: str, company_id: str = Query(...)) -> dict[str, Any]:
    """
    Forget a connection.

    Deletes the stored tokens. It does NOT revoke consent at the provider —
    that is the user's to do in their Google or Dropbox account settings, and
    saying so is more honest than implying we removed access we cannot remove.
    """
    if provider not in PROVIDERS:
        raise HTTPException(status_code=404, detail="Unknown provider")

    from agent_autofill.providers.token_store import delete_token

    # Returns {"deleted": bool}, not a bare boolean — a truthiness check on the
    # dict itself would report success for a connection that never existed.
    removed = delete_token(company_id, provider).get("deleted", False)
    return {
        "status": "success",
        "provider": provider,
        "removed": removed,
        "message": (
            "Stored credentials deleted. To revoke this app's access entirely, "
            "remove it in your provider account's connected-apps settings."
        ),
    }
