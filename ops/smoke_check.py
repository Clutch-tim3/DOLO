#!/usr/bin/env python3
"""
A9 — prove sign-in works through the origin real browsers actually use.

WHY THIS EXISTS

Every test and every deployment check hit the Cloud Run URL directly. Firebase
Hosting sits in front of that in production and forwards exactly one cookie —
`__session` — stripping all others. That is how sign-in was broken for every
real user while the suite stayed green: the cookie was named something else,
the Cloud Run URL never saw the difference, and nothing tested the path a
browser takes.

So this deliberately does NOT accept a Cloud Run URL by default. Checking the
origin that has never broken is what let the breakage through.

USAGE

    python ops/smoke_check.py --username Test --password '...'
    python ops/smoke_check.py --base-url https://cairoai.web.app --username ...
    python ops/smoke_check.py --base-url http://127.0.0.1:8000 --allow-non-hosting \\
        --username you@example.test --password '...'

Exit code 0 means sign-in works end to end through this origin. Non-zero means
it does not — wire it in as the LAST step of every deploy, because a deploy
that reports success while sign-in is broken is the exact failure being
guarded against.

WHAT IT CHECKS, IN ORDER

  1. The origin serves the app at all.
  2. An anonymous authenticated call is refused (401) — proves the gate is on.
  3. Login returns 200 and sets a cookie named `__session`, HttpOnly.
  4. That cookie, sent back through this same origin, authenticates a call.
  5. The identity returned is the account that signed in.

Step 4 is the one that matters. Steps 1-3 all passed while sign-in was broken.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from http.cookiejar import CookieJar

DEFAULT_BASE_URL = "https://cairoai.web.app"

#: Firebase Hosting forwards this cookie and strips every other. If it is ever
#: renamed, sign-in through the Hosting origin breaks while every test against
#: the Cloud Run URL keeps passing. See agent/auth.py.
SESSION_COOKIE = "__session"

#: The shape of a Cloud Run URL. Allowed only with --allow-non-hosting, because
#: checking it instead of the Hosting origin is how this was missed before.
CLOUD_RUN_HINTS = (".run.app", "run.googleapis.com")

TIMEOUT = 30


class SmokeFailure(Exception):
    pass


def _header(headers, name: str) -> str:
    """
    Case-insensitive header lookup, joining repeats.

    HTTP header names are case-insensitive and servers disagree: uvicorn sends
    `set-cookie`, other stacks send `Set-Cookie`. Reading a plain dict by exact
    name found nothing against uvicorn and would have reported "login did not
    set a __session cookie" for a login that had. A smoke check that fails for
    the wrong reason is worse than none, because the next person debugs the
    wrong thing.

    Repeats are joined because Set-Cookie legitimately appears more than once.
    """
    wanted = name.lower()
    values = [v for k, v in headers.items() if k.lower() == wanted]
    return ", ".join(values)


def _request(opener, url: str, method: str = "GET", body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with opener.open(req, timeout=TIMEOUT) as resp:
            return resp.status, resp.read().decode("utf-8", "replace"), resp.headers
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace"), exc.headers
    except urllib.error.URLError as exc:
        raise SmokeFailure(f"could not reach {url}: {exc.reason}") from exc


def run(base_url: str, username: str, password: str) -> None:
    base_url = base_url.rstrip("/")
    jar = CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    # 1. The origin serves something.
    status, _, _ = _request(opener, f"{base_url}/")
    if status >= 500:
        raise SmokeFailure(f"origin returned {status} for /")
    print(f"  [ok] origin responds ({status})")

    # 2. The gate is actually on. If this returns 200 anonymously, sign-in
    #    "working" below would prove nothing.
    status, _, _ = _request(opener, f"{base_url}/api/auth/me")
    if status != 401:
        raise SmokeFailure(
            f"anonymous /api/auth/me returned {status}, expected 401 — "
            "the authentication gate is not on, so this check cannot prove anything"
        )
    print("  [ok] anonymous call refused (401)")

    # 3. Login.
    status, body, headers = _request(
        opener, f"{base_url}/api/auth/login", "POST",
        {"username": username, "password": password},
    )
    if status != 200:
        raise SmokeFailure(f"login returned {status}: {body[:300]}")

    set_cookie = _header(headers, "Set-Cookie")
    if SESSION_COOKIE not in set_cookie:
        raise SmokeFailure(
            f"login did not set a {SESSION_COOKIE!r} cookie. Firebase Hosting "
            f"forwards only that name and strips the rest, so whatever it set "
            f"will not survive the trip: {set_cookie[:200]!r}"
        )
    if "httponly" not in set_cookie.lower():
        raise SmokeFailure("session cookie is not HttpOnly")
    print(f"  [ok] login set an HttpOnly {SESSION_COOKIE} cookie")

    # 4. THE CHECK THAT MATTERS. Everything above passed while sign-in was
    #    broken for every real user. This is the round trip that did not.
    if not any(c.name == SESSION_COOKIE for c in jar):
        raise SmokeFailure(
            f"no {SESSION_COOKIE} cookie was retained by the client after login"
        )

    status, body, _ = _request(opener, f"{base_url}/api/auth/me")
    if status != 200:
        raise SmokeFailure(
            f"authenticated /api/auth/me returned {status} through this origin. "
            "Sign-in is broken for real browsers even if it works against the "
            "Cloud Run URL directly."
        )
    print("  [ok] the session cookie authenticates a call through this origin")

    # 5. It is the right account.
    try:
        identity = json.loads(body)
    except ValueError as exc:
        raise SmokeFailure(f"/api/auth/me did not return JSON: {body[:200]!r}") from exc

    if not identity.get("company_id"):
        raise SmokeFailure(f"/api/auth/me returned no company_id: {identity}")
    print(f"  [ok] authenticated as company_id={identity['company_id']}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL,
                    help=f"origin to check (default: {DEFAULT_BASE_URL})")
    ap.add_argument("--username", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--allow-non-hosting", action="store_true",
                    help="permit a non-Hosting origin, e.g. localhost or Cloud Run")
    args = ap.parse_args()

    looks_like_cloud_run = any(h in args.base_url for h in CLOUD_RUN_HINTS)
    if looks_like_cloud_run and not args.allow_non_hosting:
        print(
            f"Refusing to check {args.base_url}.\n\n"
            "That is the Cloud Run URL. Firebase Hosting sits in front of it in "
            "production and strips every cookie except __session — checking the "
            "origin that has never broken is precisely how sign-in stayed broken "
            "for real users while the suite was green.\n\n"
            "Check https://cairoai.web.app, or pass --allow-non-hosting if you "
            "genuinely mean to test the backend directly.",
            file=sys.stderr,
        )
        return 2

    print(f"smoke check: {args.base_url}")
    try:
        run(args.base_url, args.username, args.password)
    except SmokeFailure as exc:
        print(f"\nFAILED: {exc}", file=sys.stderr)
        return 1

    print("\nPASS — sign-in works end to end through this origin.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
