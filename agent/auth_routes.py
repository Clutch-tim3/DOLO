"""
The HTTP surface for signing in, signing out, and pairing a device.

Mounted from app.py. Kept out of app.py itself so the authentication surface is
one file you can read end to end — it is the thing every other route now
depends on, and it should not be spread through 1,800 lines of prediction code.

WHAT IS NOT HERE, AND WHY
-------------------------
* **No signup.** An open registration route lets anyone create an account
  naming any `company_id`, which is precisely the hole this work closes. With
  no email channel there is nothing to verify an applicant against, so accounts
  are provisioned by an operator (`scripts/manage_users.py`).
* **No password reset.** Same reason: a reset needs a channel to send the
  challenge down, and there is none. An operator resets a password.
* **No credential in a URL.** The password and the pairing code both travel in
  a request body. The runtime logs request lines before any handler runs, so
  anything in a query string is in the access log by the time we see it — the
  same reasoning that put PKCE on the OAuth flow.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from agent import auth
from agent.subscription import get_company_tier, get_config

logger = logging.getLogger("cairoai.auth")

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _identity(principal: auth.Principal) -> dict:
    """
    What the client is told about itself.

    The tier comes from the server's own lookup against the authenticated
    company. The page used to decide this for itself from localStorage, which
    is why a locked feature could be unlocked from the console.
    """
    config = get_config(principal.company_id)
    return {
        "company_id": principal.company_id,
        "username": principal.username,
        "auth_kind": principal.kind,
        "tier": get_company_tier(principal.company_id),
        "features": {
            "agent_enabled": bool(config.get("agent_enabled")),
            "agent_autofill_enabled": bool(config.get("agent_autofill_enabled")),
            "model_access": list(config.get("model_access", [])),
        },
    }


@router.post("/login")
async def login(request: Request, payload: dict = Body(...)):
    """
    Exchange a username and password for a session cookie.

    The failure is one message with one status for every cause — unknown user,
    wrong password, disabled account, too many attempts. Telling them apart
    hands an attacker an account-enumeration oracle for free.
    """
    username = str(payload.get("username") or "")
    password = str(payload.get("password") or "")

    try:
        user = auth.authenticate(username, password)
    except auth.AuthError:
        # Logged without the password, and without confirming whether the
        # username exists. The username itself is recorded because a burst of
        # failures against one account is the signal an operator needs.
        logger.warning("failed login for %r", username[:64])
        raise HTTPException(status_code=401, detail="Incorrect username or password.")

    token = auth.issue_session(user)
    response = JSONResponse(_identity(auth.Principal(
        company_id=user.company_id, user_id=user.user_id,
        kind="session", username=user.username)))
    auth.set_session_cookie(response, request, token)
    logger.info("login for %s (company %s)", user.username, user.company_id)
    return response


@router.post("/logout")
async def logout(request: Request):
    """
    End the session. Idempotent: signing out twice, or without a session, is a
    200 — a client trying to end a session it may not have should not have to
    handle an error to do it.
    """
    cookie = request.cookies.get(auth.SESSION_COOKIE)
    if cookie:
        auth.revoke_session(cookie)
    response = JSONResponse({"status": "signed_out"})
    auth.clear_session_cookie(response, request)
    return response


@router.get("/me")
async def me(principal: auth.Principal = Depends(auth.require_principal)):
    """Who the caller is. 401 when nobody — this is what the UI gates on."""
    return _identity(principal)


# --- device pairing ---------------------------------------------------------
#
# For the headless desktop client that will be built next. This half is the
# credential: issuance, exchange, verification and revocation. The client
# itself is not built here.
#
# The flow needs no email and no SMS, which is the constraint that ruled out
# every "we send you a code" design:
#
#   1. A signed-in human asks the web UI for a pairing code.
#   2. They type it into the desktop client once.
#   3. The client POSTs it here and receives a long-lived device token scoped
#      to that one company.
#   4. The code is consumed. The token is revocable from the same UI.


@router.post("/device/pairing-code")
async def create_pairing_code(
    principal: auth.Principal = Depends(auth.require_principal),
    payload: dict = Body(default={}),
):
    """
    Mint a pairing code for the authenticated company.

    A device token cannot mint another one. Otherwise a single leaked device
    credential would be self-renewing and revoking it would achieve nothing —
    the revoked device could simply pair itself again.
    """
    if principal.kind != "session":
        raise HTTPException(
            status_code=403,
            detail="Pair a device from a signed-in browser session.",
        )
    label = str((payload or {}).get("label") or "Desktop client")
    result = auth.create_pairing_code(principal.company_id, principal.user_id, label)
    # The code is in the body, never in a redirect or a log line.
    return result


@router.post("/device/pair")
async def pair_device(payload: dict = Body(...)):
    """
    Swap a pairing code for a device token.

    Deliberately unauthenticated: the code IS the credential, and the client
    presenting it has nothing else yet. It is single-use, ten minutes old at
    most, and carries its own company — the caller supplies no company_id, so
    there is nothing here to point at another tenant.
    """
    code = str((payload or {}).get("code") or "")
    try:
        issued = auth.redeem_pairing_code(code)
    except auth.AuthError as exc:
        # The code never appears in the message or the log.
        logger.warning("device pairing refused: %s", exc)
        raise HTTPException(status_code=401, detail=str(exc))

    logger.info("device paired for company %s (device %s)",
                issued["company_id"], issued["device_id"])
    return issued


@router.get("/devices")
async def devices(principal: auth.Principal = Depends(auth.require_principal)):
    """Paired devices for the authenticated company. Never includes a token."""
    return {"company_id": principal.company_id,
            "devices": auth.list_device_tokens(principal.company_id)}


@router.post("/devices/{device_id}/revoke")
async def revoke_device(device_id: str,
                        principal: auth.Principal = Depends(auth.require_principal)):
    """
    Revoke one device. Scoped to the authenticated company by the UPDATE's own
    WHERE clause, so a guessed device id belonging to another tenant matches
    nothing.
    """
    removed = auth.revoke_device_token(principal.company_id, device_id)
    if not removed:
        raise HTTPException(status_code=404, detail="No such device.")
    return {"status": "revoked", "device_id": device_id}
