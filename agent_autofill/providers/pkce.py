"""
PKCE (RFC 7636) for the authorization-code flow.

WHAT IT BUYS HERE
-----------------
The authorization code arrives as a query parameter on our callback URL, and
query strings end up in access logs — on Cloud Run the request line is logged
by the runtime before our code ever sees it. Our own handlers never log the
code, but we cannot stop the platform logging the URL that carried it.

PKCE makes that exposure inert. The code is bound at issue time to a secret
this server generated and never transmitted: the authorization request carries
only a SHA-256 hash of it, and the token exchange carries the secret. A code
lifted from a log line cannot be redeemed, because whoever lifted it does not
have the verifier.

It also closes the case where the redirect is intercepted before it reaches us
— a malicious app registered for the same custom scheme, a proxy, a shared
machine. The code alone stops being sufficient in all of them.

WHERE THE VERIFIER LIVES
------------------------
Beside the state record, server-side, for the ten minutes the flow is open. It
is not sent to the browser and never appears in a URL, which is the whole
point: the browser carries the challenge, we keep the verifier.

Stored in the clear rather than encrypted. It is worthless without the matching
code, worthless after the single-use state is consumed, and gone within ten
minutes — and adding a second secret to protect a ten-minute one mostly moves
the problem. The state digest beside it is hashed because that one IS a bearer
value.
"""

from __future__ import annotations

import base64
import hashlib
import secrets

#: RFC 7636 allows 43-128 characters. 64 random bytes urlsafe-encoded lands
#: comfortably inside that and well past guessable.
VERIFIER_BYTES = 64

#: "plain" is also permitted by the RFC and is worth nothing here — it puts the
#: verifier itself in the authorization URL, which is the exact place we are
#: trying to keep it out of. Only S256 is produced or accepted.
METHOD = "S256"


def new_verifier() -> str:
    """A fresh code_verifier. Never leaves this server."""
    return secrets.token_urlsafe(VERIFIER_BYTES)


def challenge_for(verifier: str) -> str:
    """
    The code_challenge to put in the authorization URL.

    BASE64URL(SHA256(verifier)), unpadded — the padding characters are not
    allowed in the parameter and providers reject a challenge that carries them.
    """
    digest = hashlib.sha256((verifier or "").encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def verify(verifier: str, challenge: str) -> bool:
    """
    Whether a verifier matches a challenge.

    The provider does this check, not us — this exists so tests can assert the
    pair we generated is actually a valid pair, rather than asserting that two
    strings we made up look plausible.
    """
    if not verifier or not challenge:
        return False
    return secrets.compare_digest(challenge_for(verifier), challenge)
