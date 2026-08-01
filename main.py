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

from firebase_admin import initialize_app
from firebase_functions import https_fn
from firebase_functions.params import SecretParam
from a2wsgi import ASGIMiddleware

# Initialize Firebase Admin app
initialize_app()

# Deployed functions do not receive the local .env (it is gitignored), so the
# API key is supplied as a Firebase secret. Declaring it here injects it into
# os.environ at runtime, which is where agent/claude_client.py reads it from.
#
# Set it once with:
#     firebase functions:secrets:set ANTHROPIC_API_KEY
ANTHROPIC_API_KEY = SecretParam("ANTHROPIC_API_KEY")

# Import the FastAPI app
from app import app as fastapi_app

# Convert ASGI app (FastAPI) to WSGI app
wsgi_app = ASGIMiddleware(fastapi_app)


# timeout_sec raised from 60: one agent turn can run several
# request -> execute tools -> request round trips, which does not reliably
# fit inside 60 seconds.
@https_fn.on_request(
    memory=1024,
    max_instances=20,
    timeout_sec=300,
    secrets=[ANTHROPIC_API_KEY],
)
def api(req: https_fn.Request) -> https_fn.Response:
    # buffered=True so the response is fully materialised before the functions
    # framework tears down the request context.
    return https_fn.Response.from_app(wsgi_app, req.environ, buffered=True)
