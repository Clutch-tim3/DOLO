"""
Hand-off between "a webhook arrived" and "work happened".

=============================================================================
WHY THE HANDLER MUST NOT DO THE WORK
=============================================================================
Extraction on a 145-page tender pack takes seconds to tens of seconds. A
Claude call takes longer. Neither belongs inside a webhook handler, for three
separate reasons:

1. **Providers retry slow handlers.** Google retries push notifications with
   exponential backoff when the endpoint is slow or errors, and repeatedly
   failing deliveries cause it to abandon the channel entirely. A handler that
   runs extraction inline converts one document upload into a pile of
   duplicate deliveries, each starting its own extraction, each slower than
   the last.

2. **The handler is unauthenticated by construction.** Anyone can POST to the
   URL. Verification rejects forgeries, but the endpoint still has to survive
   being hammered. Cheap work per request is the only defence that scales.

3. **Cloud Functions caps request time.** `functions.yaml` sets `timeout_sec`
   for the API function; a webhook that outlives it returns 504 to Google,
   which is indistinguishable from an outage and triggers the retry storm in
   (1).

So the handler does exactly: verify -> enqueue -> 200.

=============================================================================
THIS IMPLEMENTATION IS THE LOCAL ONE, AND THAT IS NOT A DETAIL
=============================================================================
`ThreadedTaskQueue` below runs work on daemon threads inside the same process.
That is correct for `uvicorn` locally and it is honest about being wrong for
production:

    On Cloud Functions gen2 / Cloud Run, CPU is throttled to near zero once
    the response has been returned, unless the instance has CPU always
    allocated or a minimum instance count. A background thread started in the
    handler may not run at all, may run minutes later, or may be killed when
    the instance is reclaimed. There is no error and no log line -- the work
    simply never happens.

The production dispatcher must therefore be an external queue: Cloud Tasks
targeting a second HTTP endpoint, or Pub/Sub. `set_dispatcher()` is the seam
for that. It is a deliberate one-function swap rather than a rewrite:

    from agent_autofill.webhooks import async_queue
    async_queue.set_dispatcher(push_to_cloud_tasks)

Until that is wired in, `deployment_readiness()` reports `ready=False` when it
detects a serverless environment with the in-process dispatcher still active.

=============================================================================
BOUNDED, NOT UNBOUNDED
=============================================================================
The queue has a fixed size and `submit()` never blocks. If it is full the task
is dropped and counted. That is deliberate: blocking the handler on a full
queue reintroduces exactly the slow-handler problem the queue exists to avoid,
and dropping is recoverable -- Drive's next notification, or the renewal
cycle's next pass, re-derives the change set from the stored cursor. Silently
growing an unbounded queue until the instance is OOM-killed is not.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

__all__ = [
    "WebhookTask",
    "ThreadedTaskQueue",
    "get_queue",
    "set_dispatcher",
    "submit",
    "deployment_readiness",
]

logger = logging.getLogger("agent_autofill.webhooks")

DEFAULT_MAX_QUEUE = 512
DEFAULT_WORKERS = 2


@dataclass(frozen=True)
class WebhookTask:
    """
    One unit of deferred work.

    Carries identifiers only -- no tokens, no request bodies, no signatures.
    A queue is a place things sit around in memory and get dumped into logs
    when something goes wrong, so nothing sensitive is put in one.
    """

    provider: str
    company_id: str | None
    reason: str
    channel_id: str | None = None
    accounts: tuple[str, ...] = ()
    resource_state: str | None = None
    enqueued_at: float = field(default_factory=time.time)

    def as_log_fields(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "company_id": self.company_id,
            "channel_id": self.channel_id,
            "resource_state": self.resource_state,
            "accounts": len(self.accounts),
        }


class ThreadedTaskQueue:
    """Bounded in-process queue with daemon workers. Local/dev dispatcher."""

    def __init__(
        self,
        handler: Callable[[WebhookTask], None] | None = None,
        max_queue: int = DEFAULT_MAX_QUEUE,
        workers: int = DEFAULT_WORKERS,
    ) -> None:
        self._queue: "queue.Queue[WebhookTask | None]" = queue.Queue(maxsize=max_queue)
        self._handler = handler
        self._workers: list[threading.Thread] = []
        self._worker_count = workers
        self._started = False
        self._lock = threading.Lock()
        self.submitted = 0
        self.dropped = 0
        self.processed = 0
        self.errored = 0

    def set_handler(self, handler: Callable[[WebhookTask], None]) -> None:
        self._handler = handler

    def _ensure_started(self) -> None:
        with self._lock:
            if self._started:
                return
            for i in range(self._worker_count):
                t = threading.Thread(
                    target=self._run, name=f"autofill-webhook-worker-{i}", daemon=True
                )
                t.start()
                self._workers.append(t)
            self._started = True

    def _run(self) -> None:
        while True:
            task = self._queue.get()
            try:
                if task is None:
                    return
                if self._handler is None:
                    logger.warning(
                        "webhook task discarded: no handler registered provider=%s",
                        task.provider,
                    )
                    continue
                self._handler(task)
                self.processed += 1
            except Exception:
                self.errored += 1
                # Never let a worker die on one bad task.
                logger.error("webhook task handler raised", exc_info=True)
            finally:
                self._queue.task_done()

    def submit(self, task: WebhookTask) -> bool:
        """
        Enqueue without blocking. Returns False if the task was dropped.

        `put_nowait` rather than `put`: the caller is an HTTP handler that has
        already committed to answering 200 quickly.
        """
        self._ensure_started()
        try:
            self._queue.put_nowait(task)
        except queue.Full:
            self.dropped += 1
            logger.error(
                "webhook queue full, task dropped provider=%s company_id=%s",
                task.provider,
                task.company_id,
            )
            return False
        self.submitted += 1
        return True

    def drain(self, timeout: float = 5.0) -> bool:
        """Block until the queue empties. Tests only -- never call from a handler."""
        deadline = time.time() + timeout
        while not self._queue.empty() and time.time() < deadline:
            time.sleep(0.01)
        self._queue.join()
        return self._queue.empty()

    def stats(self) -> dict[str, int]:
        return {
            "submitted": self.submitted,
            "dropped": self.dropped,
            "processed": self.processed,
            "errored": self.errored,
            "pending": self._queue.qsize(),
        }


_default_queue = ThreadedTaskQueue()
_dispatcher: Callable[[WebhookTask], bool] = _default_queue.submit
_dispatcher_is_in_process = True


def get_queue() -> ThreadedTaskQueue:
    return _default_queue


def set_dispatcher(dispatcher: Callable[[WebhookTask], bool], in_process: bool = False) -> None:
    """
    Replace the hand-off mechanism.

    Pass the Cloud Tasks / Pub-Sub publisher here at startup in production;
    leave `in_process=False` so `deployment_readiness()` stops complaining.
    """
    global _dispatcher, _dispatcher_is_in_process
    _dispatcher = dispatcher
    _dispatcher_is_in_process = in_process


def submit(task: WebhookTask) -> bool:
    """Hand a task to whatever dispatcher is configured."""
    return _dispatcher(task)


def deployment_readiness(env: dict[str, str] | None = None) -> dict[str, Any]:
    """
    Whether the current dispatcher can actually run work here.

    Exists so the honest answer is queryable rather than buried in a comment:
    on a serverless runtime with the in-process dispatcher still installed,
    enqueued work may never execute.
    """
    import os

    env = os.environ if env is None else env
    serverless = bool(env.get("K_SERVICE") or env.get("FUNCTION_TARGET"))
    ready = not (serverless and _dispatcher_is_in_process)
    return {
        "serverless": serverless,
        "in_process_dispatcher": _dispatcher_is_in_process,
        "ready": ready,
        "warning": (
            ""
            if ready
            else (
                "In-process background work is not reliable on Cloud Functions/Run: "
                "CPU is throttled after the response returns, so enqueued webhook "
                "tasks may never execute. Call set_dispatcher() with a Cloud Tasks "
                "or Pub/Sub publisher before deploying."
            )
        ),
    }
