"""
The deploy smoke check itself works.

A9 exists because every test and every deployment check hit the Cloud Run URL
directly, while Firebase Hosting — which forwards only `__session` and strips
every other cookie — sits in front of it in production. Sign-in was broken for
every real user while the suite stayed green.

A smoke check has the same failure mode one level up: one that cannot fail, or
that fails for the wrong reason, is worse than none because the next person
debugs the wrong thing. Writing this caught exactly that — `dict(resp.headers)`
is case-sensitive and uvicorn sends `set-cookie`, so the check reported "login
did not set a __session cookie" for a login that had.
"""

import email.message
import importlib.util
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

SMOKE = Path(__file__).resolve().parent.parent / "ops" / "smoke_check.py"

_spec = importlib.util.spec_from_file_location("smoke_check", SMOKE)
smoke_check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(smoke_check)


def _headers(pairs):
    msg = email.message.Message()
    for k, v in pairs:
        msg[k] = v
    return msg


# --- the bug this check was written to catch ---------------------------------

def test_it_knows_which_cookie_name_hosting_forwards():
    """
    Firebase Hosting forwards `__session` and strips the rest. A check that
    does not know the name cannot catch the rename that broke sign-in.
    """
    assert smoke_check.SESSION_COOKIE == "__session"

    from agent import auth
    assert smoke_check.SESSION_COOKIE == auth.SESSION_COOKIE, (
        "the smoke check and the app disagree about the session cookie name"
    )


def test_it_defaults_to_the_hosting_origin_not_cloud_run():
    """Checking the origin that never broke is how the breakage got through."""
    assert "web.app" in smoke_check.DEFAULT_BASE_URL
    assert not any(h in smoke_check.DEFAULT_BASE_URL for h in smoke_check.CLOUD_RUN_HINTS)


# --- header handling, which silently broke the check -------------------------

@pytest.mark.parametrize("name", ["Set-Cookie", "set-cookie", "SET-COOKIE"])
def test_headers_are_read_case_insensitively(name):
    """
    HTTP header names are case-insensitive and servers disagree. Reading a
    plain dict by exact name found nothing against uvicorn.
    """
    headers = _headers([(name, "__session=abc; HttpOnly")])
    assert "__session" in smoke_check._header(headers, "Set-Cookie")


def test_repeated_headers_are_joined_not_dropped():
    """Set-Cookie legitimately appears more than once."""
    headers = _headers([("set-cookie", "a=1"), ("set-cookie", "__session=xyz; HttpOnly")])
    joined = smoke_check._header(headers, "Set-Cookie")
    assert "a=1" in joined and "__session=xyz" in joined


def test_a_missing_header_is_empty_not_an_error():
    assert smoke_check._header(_headers([("Content-Type", "application/json")]),
                               "Set-Cookie") == ""


# --- the guard ----------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "https://api-tja32zbdja-uc.a.run.app",
    "https://something.run.app",
])
def test_a_cloud_run_url_is_recognised(url):
    assert any(hint in url for hint in smoke_check.CLOUD_RUN_HINTS)


def test_the_hosting_origin_is_not_mistaken_for_cloud_run():
    assert not any(h in "https://cairoai.web.app" for h in smoke_check.CLOUD_RUN_HINTS)


# --- failure is loud ----------------------------------------------------------

def test_failures_raise_rather_than_returning_quietly():
    """
    A smoke check that returns success on an unreachable origin is the failure
    it was written to prevent.
    """
    class DeadOpener:
        def open(self, *a, **kw):
            import urllib.error
            raise urllib.error.URLError("connection refused")

    with pytest.raises(smoke_check.SmokeFailure):
        smoke_check._request(DeadOpener(), "http://127.0.0.1:9/")


def test_an_http_error_status_is_returned_not_raised():
    """
    A 401 is a result the check reasons about — it asserts the gate is on —
    not a transport failure. Raising on it would make step 2 impossible.
    """
    import urllib.error

    class ErrorOpener:
        def open(self, *a, **kw):
            raise urllib.error.HTTPError(
                "http://x/", 401, "Unauthorized", _headers([]), None)

    status, _, _ = smoke_check._request(ErrorOpener(), "http://x/")
    assert status == 401
