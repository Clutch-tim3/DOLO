"""
Inbound webhook receivers and the Drive channel renewal cron.

=============================================================================
UNVERIFIED
=============================================================================
No real notification from Google or Dropbox has ever reached this code. The
verification logic, the renewal arithmetic and the fast-acknowledge path are
tested offline against crafted requests; the live round-trip is not, and
cannot be here -- it needs an OAuth consent flow and a publicly reachable
HTTPS callback. See `agent_autofill/providers/VERIFICATION.md`.

=============================================================================
LAYOUT
=============================================================================
    routes.py                 FastAPI router. Verify -> enqueue -> 200.
                              Not mounted in app.py; mounting is a decision
                              for whoever owns that file.
    async_queue.py            The hand-off. Local threads now; Cloud Tasks
                              before this is deployed, and it says so.
    channel_renewal_cron.py   Why 6 hours and not 24, with the arithmetic.

`routes.py` imports FastAPI, so it is not imported here -- the rest of the
package must stay usable in a plain script and in tests without pulling a web
framework into the import graph.
"""

__all__ = ["async_queue", "channel_renewal_cron"]
