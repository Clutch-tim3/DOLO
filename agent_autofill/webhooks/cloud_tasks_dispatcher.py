"""
Hand webhook work to Cloud Tasks instead of an in-process queue.

The in-process dispatcher cannot work on Cloud Functions. CPU is throttled once
the response returns, so a thread that was going to do the work after replying
to Google's webhook may simply never be scheduled — and nothing reports an
error, because from the runtime's point of view the request succeeded. That is
the worst failure shape available: silent, intermittent, and invisible in logs.

Cloud Tasks moves the work out of the process entirely. The webhook handler
enqueues a task and returns; Cloud Tasks then makes a fresh HTTP request back
into this service, which is a normal request with a normal CPU allocation.

WHAT THIS MODULE CANNOT DO FOR YOU
----------------------------------
The queue itself has to exist, and the service account needs
`cloudtasks.tasks.create` on it. Neither can be done from here, and neither can
be verified from a machine that is not the deployed function. `readiness()`
reports exactly which pieces are configured so the answer is checkable rather
than assumed — see `agent_autofill/providers/VERIFICATION.md` for the standing
rule about not claiming verification that did not happen.

AUTHENTICATION
--------------
The worker endpoint is a public URL, so it must not execute anything it is
handed. Each task carries an HMAC over its body and a timestamp; the worker
recomputes both and rejects on mismatch or on age. That is a shared secret
rather than OIDC — deliberately, because it needs no additional IAM wiring and
fails closed when unconfigured, whereas a half-configured OIDC check tends to
fail open.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from dataclasses import asdict
from typing import Any, Callable

from agent_autofill.webhooks.async_queue import WebhookTask

logger = logging.getLogger(__name__)

#: Full queue path: projects/<project>/locations/<region>/queues/<queue>
QUEUE_ENV = "WEBHOOK_TASKS_QUEUE"
#: Absolute https URL of the worker endpoint Cloud Tasks should call back.
WORKER_URL_ENV = "WEBHOOK_WORKER_URL"
#: Shared secret authenticating the callback. Set it like any other secret.
TASK_SECRET_ENV = "WEBHOOK_TASK_SECRET"

#: How old a signed task may be before the worker refuses it. Cloud Tasks
#: retries for far longer than this, so it is a replay bound, not a delivery
#: deadline — a task retried past it is dropped rather than replayed forever.
MAX_TASK_AGE_SECONDS = 15 * 60


class TaskAuthError(Exception):
    """The callback could not be authenticated. Never say why, to the caller."""


def _secret() -> bytes:
    raw = (os.environ.get(TASK_SECRET_ENV) or "").strip()
    if not raw:
        raise TaskAuthError(f"{TASK_SECRET_ENV} is not set")
    return raw.encode("utf-8")


def sign_body(body: bytes, timestamp: str) -> str:
    """MAC over the exact bytes sent, bound to a timestamp so it cannot replay."""
    mac = hmac.new(_secret(), timestamp.encode("ascii") + b"." + body, hashlib.sha256)
    return mac.hexdigest()


def verify_request(body: bytes, timestamp: str, signature: str,
                   now: float | None = None) -> None:
    """
    Raise `TaskAuthError` unless this is a task we enqueued, recently.

    Raises rather than returning a bool so a caller cannot accidentally treat
    the result as truthy and carry on.
    """
    if not timestamp or not signature:
        raise TaskAuthError("missing signature headers")
    try:
        age = (time.time() if now is None else now) - float(timestamp)
    except (TypeError, ValueError):
        raise TaskAuthError("unparseable timestamp")
    # Negative age means a clock skew or a forged future timestamp; both are
    # refused rather than tolerated, since tolerating one tolerates the other.
    if age < -60 or age > MAX_TASK_AGE_SECONDS:
        raise TaskAuthError("timestamp outside the accepted window")
    if not hmac.compare_digest(signature, sign_body(body, timestamp)):
        raise TaskAuthError("signature mismatch")


def task_to_body(task: WebhookTask) -> bytes:
    payload = asdict(task)
    payload["accounts"] = list(task.accounts)
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def body_to_task(body: bytes) -> WebhookTask:
    data = json.loads(body.decode("utf-8"))
    data["accounts"] = tuple(data.get("accounts") or ())
    allowed = {f for f in WebhookTask.__dataclass_fields__}
    return WebhookTask(**{k: v for k, v in data.items() if k in allowed})


def _default_transport(queue: str, url: str, body: bytes,
                       headers: dict[str, str]) -> None:
    """
    Create the task through the Cloud Tasks REST API.

    Uses google-api-python-client, which is already a dependency, rather than
    adding google-cloud-tasks purely for this. Imported inside the function so
    a machine without credentials can still import this module and run the
    signing tests.
    """
    import base64

    from googleapiclient.discovery import build

    client = build("cloudtasks", "v2", cache_discovery=False)
    client.projects().locations().queues().tasks().create(
        parent=queue,
        body={
            "task": {
                "httpRequest": {
                    "httpMethod": "POST",
                    "url": url,
                    "headers": {**headers, "Content-Type": "application/json"},
                    "body": base64.b64encode(body).decode("ascii"),
                }
            }
        },
    ).execute()


def readiness(env: dict[str, str] | None = None) -> dict[str, Any]:
    """Which pieces are configured. Reported per-piece so a gap is nameable."""
    env = os.environ if env is None else env
    pieces = {
        "queue": bool((env.get(QUEUE_ENV) or "").strip()),
        "worker_url": bool((env.get(WORKER_URL_ENV) or "").strip()),
        "task_secret": bool((env.get(TASK_SECRET_ENV) or "").strip()),
    }
    missing = sorted(k for k, v in pieces.items() if not v)
    return {
        "configured": not missing,
        "missing": missing,
        "detail": (
            "Cloud Tasks dispatch is configured."
            if not missing
            else "Not configured; set " + ", ".join(
                {"queue": QUEUE_ENV, "worker_url": WORKER_URL_ENV,
                 "task_secret": TASK_SECRET_ENV}[m] for m in missing
            )
        ),
    }


def make_dispatcher(transport: Callable[..., None] | None = None,
                    env: dict[str, str] | None = None) -> Callable[[WebhookTask], bool]:
    """
    Build a dispatcher for `async_queue.set_dispatcher`.

    Returns False rather than raising when enqueueing fails: the caller is a
    webhook handler, and failing its HTTP response would make the provider
    retry the whole delivery. A dropped task is logged loudly and the next
    delivery re-triggers the work.
    """
    env = os.environ if env is None else env
    send = transport or _default_transport

    def dispatch(task: WebhookTask) -> bool:
        state = readiness(env)
        if not state["configured"]:
            logger.error("webhook task dropped, %s", state["detail"])
            return False
        body = task_to_body(task)
        timestamp = f"{time.time():.0f}"
        try:
            send(
                env[QUEUE_ENV].strip(),
                env[WORKER_URL_ENV].strip(),
                body,
                {"X-CairoAI-Timestamp": timestamp,
                 "X-CairoAI-Signature": sign_body(body, timestamp)},
            )
        except Exception:
            logger.exception("failed to enqueue webhook task %s", task.as_log_fields())
            return False
        return True

    return dispatch


def install_if_configured(env: dict[str, str] | None = None) -> bool:
    """
    Switch the dispatcher over when the configuration is present.

    Called at import of the webhook routes. Deliberately does nothing when
    unconfigured, so local development keeps the in-process queue and
    `deployment_readiness()` keeps reporting ready=False rather than being
    quietly satisfied by a dispatcher that cannot reach a queue.
    """
    from agent_autofill.webhooks.async_queue import set_dispatcher

    env = os.environ if env is None else env
    if not readiness(env)["configured"]:
        return False
    set_dispatcher(make_dispatcher(env=env), in_process=False)
    logger.info("webhook dispatch handed to Cloud Tasks")
    return True
