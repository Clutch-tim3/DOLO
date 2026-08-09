"""
Firebase Functions entry point.

firebase.json rewrites /api/** to the `api` function, so every backend request
on the deployed site arrives here.

NOTE: this file previously built `wsgi_app` and then never used it — `api`
returned the literal string "Hello from Firebase Functions!" for every request.
That meant the FastAPI app was never invoked in production: /api/agent/chat
returned that string instead of an agent reply, no matter what the agent code
did. `Response.from_app` below is the missing wiring.
"""

import logging
import os
import threading

from firebase_admin import initialize_app
from firebase_functions import https_fn
from a2wsgi import ASGIMiddleware

logger = logging.getLogger("dolo.entrypoint")

# Initialize Firebase Admin app
initialize_app()

# Deployed functions do not receive the local .env (it is gitignored), so the
# API key is supplied as a Firebase secret, bound below and injected into
# os.environ at runtime, which is where agent/claude_client.py reads it from.
#
# Set it once with:
#     firebase functions:secrets:set ANTHROPIC_API_KEY --data-file <path>
#
# Named as a plain string rather than a SecretParam: SecretParam additionally
# emits the name under the manifest's `params:` block, which the CLI resolves
# as a deploy-time parameter. The string form declares only the runtime
# binding, which is all this needs.
ANTHROPIC_API_KEY = "ANTHROPIC_API_KEY"

# Signs Agent Autofill's review acknowledgements and export stamps, so a forged
# database row or a hand-built stamp cannot pass as a reviewed bid document.
# Agent Autofill fails closed without it: acknowledging a field and exporting a
# reviewed draft both raise rather than producing an unverifiable artefact.
# Create it the same way, with any high-entropy value:
#     firebase functions:secrets:set AUTOFILL_STAMP_SECRET --data-file <path>
AUTOFILL_STAMP_SECRET = "AUTOFILL_STAMP_SECRET"

# The Cloud SQL database password. Application state lives on Cloud SQL in
# production; without it agent/db.py falls back to SQLite on /tmp, which is
# per-instance and ephemeral — a cold start silently discards every profile,
# vault document, review and quote.
#
# The instance name, database and user are plain configuration and are set as
# ordinary env vars (CLOUD_SQL_INSTANCE / _DB / _USER); only the password is a
# secret. With CLOUD_SQL_IAM_AUTH=true there is no password at all and this
# binding is unused — the runtime service account authenticates itself, which
# is the better arrangement where it is set up.
CLOUD_SQL_PASSWORD = "CLOUD_SQL_PASSWORD"

# Signs the Cloud Tasks callback that runs deferred webhook work, so the public
# worker endpoint cannot be driven by anyone else.
WEBHOOK_TASK_SECRET = "WEBHOOK_TASK_SECRET"

# OAuth client secrets for the Drive and Dropbox connections. The matching
# CLIENT_ID / APP_KEY are NOT secrets — they travel in the authorization URL
# the browser follows — so they are set as ordinary env vars, and only these
# two go in Secret Manager.
GOOGLE_OAUTH_CLIENT_SECRET = "GOOGLE_OAUTH_CLIENT_SECRET"
DROPBOX_APP_SECRET = "DROPBOX_APP_SECRET"

# Import the FastAPI app
from app import app as fastapi_app

# --- ASGI -> WSGI bridge -----------------------------------------------------
#
# a2wsgi's ASGIMiddleware spins up an event loop in a background daemon thread
# in its constructor, and every request then blocks on
# `asyncio.run_coroutine_threadsafe(...).result()` against that loop.
#
# Building it at import time is what hung the deployed function: the runtime
# imports this module in one process and serves requests from forked workers.
# Threads do not survive fork, so the worker inherited a loop object with
# nothing running it and every request blocked forever on `.result()` until the
# platform request timeout killed it — a clean startup, no application logs,
# and a 504 on every call.
#
# Building it lazily and keying it to the current PID means the loop thread is
# always created in the process that actually serves the request, and a forked
# child rebuilds its own rather than inheriting a dead one.
_bridge = None
_bridge_pid = None
_bridge_lock = threading.Lock()


def _get_bridge():
    global _bridge, _bridge_pid
    pid = os.getpid()
    if _bridge is None or _bridge_pid != pid:
        with _bridge_lock:
            # Re-check inside the lock: the runtime serves requests on several
            # threads, so more than one can arrive here before the first builds.
            if _bridge is None or _bridge_pid != pid:
                logger.info("building ASGI->WSGI bridge in pid %s", pid)
                _bridge = ASGIMiddleware(fastapi_app)
                _bridge_pid = pid
    return _bridge


# timeout_sec raised from 60: one agent turn can run several
# request -> execute tools -> request round trips, which does not reliably
# fit inside 60 seconds.
@https_fn.on_request(
    memory=1024,
    max_instances=20,
    timeout_sec=300,
    secrets=[ANTHROPIC_API_KEY, AUTOFILL_STAMP_SECRET, CLOUD_SQL_PASSWORD,
             WEBHOOK_TASK_SECRET, GOOGLE_OAUTH_CLIENT_SECRET,
             DROPBOX_APP_SECRET],
)
def api(req: https_fn.Request) -> https_fn.Response:
    # Answered without touching the bridge, so it stays reachable even if the
    # ASGI path is wedged. If this responds and real routes time out, the fault
    # is in the bridge; if this times out too, it is the runtime or the
    # function config. That split is what identified the import-time event loop
    # above as the cause of the 504s.
    if req.path.rstrip("/").endswith("/__health"):
        # Deliberately says only whether the key is configured. An earlier
        # version of this reported its length and a hash prefix, which was
        # useful while diagnosing the binding but is not something a public
        # endpoint should hand out.
        configured = bool(os.environ.get("ANTHROPIC_API_KEY"))
        # Agent Autofill fails closed without this, so a missing binding means
        # exports stop working with no other outward symptom until a user tries
        # one. Reported here for the same reason as the key: a deploy can
        # succeed while a secret silently fails to bind, and the only way to
        # know is to ask the running instance. Boolean only — same rule.
        stamp_secret = bool((os.environ.get("AUTOFILL_STAMP_SECRET") or "").strip())
        # Absent DATABASE_URL is the dangerous one: nothing fails, the app
        # quietly falls back to SQLite on /tmp, and users lose everything on the
        # next cold start while the site looks healthy. It has to be visible
        # from outside or nobody finds out until data is already gone.
        durable = bool((os.environ.get("CLOUD_SQL_INSTANCE") or "").strip()
                       or (os.environ.get("DATABASE_URL") or "").strip())
        return https_fn.Response(
            f"ok api_key_configured={str(configured).lower()} "
            f"stamp_secret_configured={str(stamp_secret).lower()} "
            f"durable_state={str(durable).lower()}",
            # The stamp secret does not gate the whole API — chat, prediction
            # and the vault all work without it — so its absence is reported
            # rather than turned into a 503 for every caller.
            status=200 if configured else 503,
            mimetype="text/plain",
        )

    # buffered=True so the response is fully materialised before the functions
    # framework tears down the request context.
    return https_fn.Response.from_app(_get_bridge(), req.environ, buffered=True)
