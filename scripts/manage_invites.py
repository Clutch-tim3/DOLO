#!/usr/bin/env python3
"""
Issue and audit single-use invitations.

This is how someone gets an account without you running manage_users.py for
them, and without an email provider: you generate a link and send it however
you like — WhatsApp, email, a signed PDF, read it down the phone.

    python scripts/manage_invites.py create --company acme_trading
    python scripts/manage_invites.py create --company acme_trading \\
        --username buyer@acme.co.za --days 3
    python scripts/manage_invites.py list
    python scripts/manage_invites.py list --company acme_trading
    python scripts/manage_invites.py revoke --selector <selector>

WHY THIS IS NOT A SIGNUP FORM

The company is fixed when the invite is minted and travels in the stored
record. The recipient chooses only their username and password — they cannot
name a company, so they cannot claim one. A signup route that accepted a
company_id would let anyone become any tenant, which is the hole agent/auth.py
was written to close.

THE LINK IS THE CREDENTIAL

It is printed once and cannot be recovered from the database — only the
selector and a digest of the verifier are stored. If you lose it, revoke and
issue another. Treat it like a password: do not paste it into a ticket.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from agent import auth  # noqa: E402


def _base_url() -> str:
    return (os.environ.get("PUBLIC_BASE_URL") or "https://cairoai.web.app").rstrip("/")


def cmd_create(args) -> int:
    try:
        token = auth.create_invite(
            args.company,
            username=args.username or "",
            created_by=args.by or "",
            ttl_seconds=args.days * 24 * 3600,
        )
    except auth.AuthError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"invitation for {args.company}"
          + (f", locked to {args.username}" if args.username else "")
          + f", valid {args.days} day(s)\n")
    print(f"  {_base_url()}/invite?token={token}\n")
    print("Send that link to the person joining. It works once.")
    print("It is not stored and cannot be shown again — revoke and reissue if lost.")
    return 0


def cmd_list(args) -> int:
    invites = auth.list_invites(args.company or "")
    if not invites:
        print("no invitations")
        return 0
    print(f"{'SELECTOR':<20}{'COMPANY':<22}{'STATE':<10}{'EXPIRES':<22}FOR")
    for i in invites:
        print(f"  {i['selector']:<18}{i['company_id']:<22}{i['state']:<10}"
              f"{(i['expires_at'] or '')[:19]:<22}{i['username'] or 'anyone'}")
    open_count = sum(1 for i in invites if i["state"] == "open")
    print(f"\n{len(invites)} invitation(s), {open_count} still open")
    return 0


def cmd_revoke(args) -> int:
    if auth.revoke_invite(args.selector):
        print(f"revoked {args.selector}")
        return 0
    print("no open invitation with that selector "
          "(it may already be used, expired or revoked)", file=sys.stderr)
    return 1


def cmd_reset(args) -> int:
    """
    A reset link, rather than an operator-chosen password.

    `manage_users.py set-password` requires the operator to invent the
    credential and then transmit it: they know it, and it travels. A one-shot
    link means neither.
    """
    try:
        token = auth.create_password_reset(
            args.username, created_by=args.by or "",
            ttl_seconds=args.hours * 3600)
    except auth.AuthError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"password reset for {args.username}, valid {args.hours} hour(s)")
    print()
    print(f"  {_base_url()}/reset?token={token}")
    print()
    print("Send that link to them. It works once, and signs out every existing")
    print("session and paired device when used.")
    print("It is not stored and cannot be shown again.")
    return 0


def cmd_list_resets(args) -> int:
    resets = auth.list_password_resets(args.username or "")
    if not resets:
        print("no reset links")
        return 0
    print(f"{'SELECTOR':<20}{'ACCOUNT':<34}{'STATE':<10}EXPIRES")
    for r in resets:
        print(f"  {r['selector']:<18}{r['username']:<34}{r['state']:<10}"
              f"{(r['expires_at'] or '')[:19]}")
    open_count = sum(1 for r in resets if r["state"] == "open")
    print(f"\n{len(resets)} link(s), {open_count} still open")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("create")
    p.add_argument("--company", required=True)
    p.add_argument("--username", help="lock the invite to one email address")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--by", help="who issued it")
    p.set_defaults(func=cmd_create)

    p = sub.add_parser("list")
    p.add_argument("--company")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("revoke")
    p.add_argument("--selector", required=True)
    p.set_defaults(func=cmd_revoke)

    p = sub.add_parser("reset")
    p.add_argument("--username", required=True)
    p.add_argument("--hours", type=int, default=24)
    p.add_argument("--by", help="who issued it")
    p.set_defaults(func=cmd_reset)

    p = sub.add_parser("resets")
    p.add_argument("--username")
    p.set_defaults(func=cmd_list_resets)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
