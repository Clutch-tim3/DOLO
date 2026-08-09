"""
HTTP surface for the two webhook receivers and the renewal cron.

=============================================================================
NOT MOUNTED
=============================================================================
This router is deliberately not registered in `app.py` or `main.py`. Another
part of the build owns those files, and mounting a public unauthenticated
endpoint is a decision the user should make explicitly rather than discover.

To wire it in:

    from agent_autofill.webhooks.routes import router as autofill_webhooks
    app.include_router(autofill_webhooks)

and remember the CLAUDE.md trap: the Firebase CLI reads `functions.yaml`
instead of running discovery, so re-run `python scripts/gen_functions_yaml.py`
after any change to the function's options, or the route ships without the
timeout and secrets it needs.

=============================================================================
WHAT EACH HANDLER IS ALLOWED TO DO
=============================================================================
Verify. Enqueue. Return. Nothing else.

There is no import of `agent_autofill.extraction`, `agent_autofill.fill_engine`
or `agent.main_agent` anywhere in this module, and a test asserts that by
walking the module's AST. That is not stylistic: the moment extraction runs
inline, the handler outlives the provider's patience, deliveries are retried,
and each retry starts another extraction.

The raw body is read with `await request.body()` and passed to the verifier as
bytes. It is never `await request.json()`-ed first -- Dropbox's HMAC is
computed over the exact bytes received, and re-serialising a parsed dict
produces different bytes and a signature mismatch on every legitimate request.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response

from agent_autofill.providers.dropbox_provider import DropboxProvider
from agent_autofill.providers.google_drive_provider import GoogleDriveProvider
from agent_autofill.webhooks import async_queue
from agent_autofill.webhooks.async_queue import WebhookTask

__all__ = ["router", "GOOGLE_WEBHOOK_PATH", "DROPBOX_WEBHOOK_PATH",
           "TASK_WORKER_PATH", "RENEWAL_PATH", "set_task_handler",
           "get_task_handler"]

logger = logging.getLogger("agent_autofill.webhooks")

GOOGLE_WEBHOOK_PATH = "/api/autofill/webhooks/google-drive"
DROPBOX_WEBHOOK_PATH = "/api/autofill/webhooks/dropbox"
RENEWAL_PATH = "/api/autofill/webhooks/renew"
#: Where Cloud Tasks delivers work back to. Not a provider-facing URL.
TASK_WORKER_PATH = "/api/autofill/webhooks/task"

router = APIRouter(tags=["agent-autofill-webhooks"])

#: What actually processes a task once it comes back through Cloud Tasks. Held
#: here rather than imported so the routes module keeps its deliberate freedom
#: from agent_autofill.extraction / fill_engine (see the module docstring), and
#: so tests can install a recorder.
_task_handler = None


def set_task_handler(handler) -> None:
    global _task_handler
    _task_handler = handler


def get_task_handler():
    return _task_handler


# Switch dispatch to Cloud Tasks when the configuration is present. A no-op
# locally, which is intended: development keeps the in-process queue and
# deployment_readiness() keeps saying ready=False rather than being satisfied
# by a dispatcher with nowhere to send anything.
try:
    from agent_autofill.webhooks.cloud_tasks_dispatcher import install_if_configured

    install_if_configured()
except Exception:
    logger.exception("Cloud Tasks dispatcher unavailable; keeping in-process queue")


def _headers_of(request: Request) -> dict[str, str]:
    return {k: v for k, v in request.headers.items()}


@router.post(GOOGLE_WEBHOOK_PATH)
async def google_drive_webhook(request: Request) -> Response:
    """
    Receive a Drive push notification.

    Drive notifications are not signed; authenticity comes from the channel
    token we chose at registration being echoed back. All of that lives in
    `GoogleDriveProvider.verify_webhook`.
    """
    raw_body = await request.body()
    provider = GoogleDriveProvider()
    verdict = provider.verify_webhook(_headers_of(request), raw_body)

    if verdict.process:
        async_queue.submit(
            WebhookTask(
                provider=verdict.provider,
                company_id=verdict.company_id,
                reason=verdict.reason,
                channel_id=verdict.channel_id,
                resource_state=verdict.resource_state,
            )
        )

    _log(verdict)
    return Response(status_code=verdict.http_status)


@router.get(DROPBOX_WEBHOOK_PATH)
async def dropbox_webhook_challenge(request: Request) -> Response:
    """
    Answer Dropbox's URI-verification GET.

    Echoes `?challenge=` as text/plain with `nosniff`. Unauthenticated by
    definition -- Dropbox has no credential to present at this point -- which
    is exactly why the content type is pinned.
    """
    provider = DropboxProvider()
    body, headers, status = provider.challenge_response(
        request.query_params.get("challenge")
    )
    return Response(content=body, status_code=status, headers=headers)


@router.post(DROPBOX_WEBHOOK_PATH)
async def dropbox_webhook(request: Request) -> Response:
    """
    Receive a Dropbox change notification.

    `await request.body()` gives the exact bytes Dropbox signed. Parsing must
    not happen before verification; the provider enforces that ordering.
    """
    raw_body = await request.body()
    provider = DropboxProvider()
    verdict = provider.verify_webhook(_headers_of(request), raw_body)

    if verdict.process:
        async_queue.submit(
            WebhookTask(
                provider=verdict.provider,
                company_id=verdict.company_id,
                reason=verdict.reason,
                accounts=verdict.accounts,
            )
        )

    _log(verdict)
    return Response(status_code=verdict.http_status)


async def _require_signed_request(request: Request) -> bytes:
    """
    Reject anything that is not a request we signed ourselves.

    Both endpoints below are public URLs that cause real work to happen, so
    neither may act on an unauthenticated caller. Returns the body so the
    caller does not read the stream twice — a second `await request.body()`
    after this would be empty.
    """
    from agent_autofill.webhooks.cloud_tasks_dispatcher import (
        TaskAuthError,
        verify_request,
    )

    body = await request.body()
    try:
        verify_request(
            body,
            request.headers.get("X-CairoAI-Timestamp", ""),
            request.headers.get("X-CairoAI-Signature", ""),
        )
    except TaskAuthError as exc:
        # Logged with the reason, answered without it: a caller probing for
        # which part of the signature it got wrong learns nothing.
        logger.warning("rejected unsigned call to %s: %s", request.url.path, exc)
        raise HTTPException(status_code=403, detail="Forbidden")
    return body


@router.post(TASK_WORKER_PATH)
async def run_webhook_task(request: Request) -> dict[str, Any]:
    """
    Execute one webhook task. Called by Cloud Tasks, not by a provider.

    This is the other half of the fix for in-process background work: the
    handler enqueues and returns, and the work happens here, inside a request
    that has a real CPU allocation rather than a throttled one.
    """
    from agent_autofill.webhooks.cloud_tasks_dispatcher import body_to_task

    body = await _require_signed_request(request)
    try:
        task = body_to_task(body)
    except Exception:
        # A malformed body from a correctly signed caller is our bug. 400 so
        # Cloud Tasks stops retrying it rather than looping forever.
        logger.exception("signed task body could not be decoded")
        raise HTTPException(status_code=400, detail="Bad task payload")

    handler = get_task_handler()
    if handler is None:
        logger.error("no webhook task handler registered; task dropped %s",
                     task.as_log_fields())
        raise HTTPException(status_code=503, detail="No handler registered")

    handler(task)
    logger.info("webhook task executed %s", task.as_log_fields())
    return {"status": "ok"}


@router.post(RENEWAL_PATH)
async def run_renewal(request: Request) -> dict[str, Any]:
    """
    Trigger the channel renewal cycle.

    An open renewal endpoint lets an anonymous caller churn every registered
    channel and burn the project's Drive quota, so it now requires the same
    signature the task worker does. Cloud Scheduler can sign it with the shared
    secret; there is no unauthenticated path.

    The cron is imported inside the function so the module does not carry its
    import-time cadence assertion into every request path.
    """
    await _require_signed_request(request)

    from agent_autofill.webhooks.channel_renewal_cron import run_renewal_cycle

    return run_renewal_cycle()


def _log(verdict: Any) -> None:
    fields = verdict.as_log_fields()
    if verdict.accepted:
        logger.info("webhook accepted %s", fields)
    else:
        logger.warning("webhook rejected %s", fields)
