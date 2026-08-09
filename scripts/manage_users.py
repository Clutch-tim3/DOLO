"""
Provision the accounts that `agent/auth.py` authenticates.

There is no signup route and there is not going to be one until there is an
email channel to verify an applicant against. Without one, "sign up" means
"anyone may create an account claiming any company_id", which is the hole the
authentication work exists to close.

    python scripts/manage_users.py create  --username ops@acme.co.za --company pro_corp
    python scripts/manage_users.py list
    python scripts/manage_users.py set-password --username ops@acme.co.za
    python scripts/manage_users.py disable --username ops@acme.co.za
    python scripts/manage_users.py enable  --username ops@acme.co.za

The password is read from a prompt that does not echo, or from stdin with
`--password-stdin`. There is deliberately no `--password` flag: an argument is
visible in the process list and in shell history, which is the same class of
mistake as putting a credential in a URL.

`--company` must be one of the ids agent/subscription.py knows about, because
that is what resolves a tier. An unknown id is accepted with a warning rather
than refused — a real customer registry will outgrow MOCK_CLIENT_REGISTRY, and
falling back to the starter tier is the safe direction to be wrong in.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent import auth  # noqa: E402
from agent.subscription import MOCK_CLIENT_REGISTRY  # noqa: E402


def _read_password(from_stdin: bool, confirm: bool = True) -> str:
    if from_stdin:
        # One line, newline stripped. Trailing whitespace is NOT stripped —
        # a password may legitimately end in a space and silently changing it
        # produces a credential the user cannot reproduce.
        return sys.stdin.readline().rstrip("\n").rstrip("\r")
    first = getpass.getpass("Password (min 12 chars): ")
    if confirm and first != getpass.getpass("Repeat password: "):
        raise SystemExit("Passwords do not match.")
    return first


def _warn_unknown_company(company_id: str) -> None:
    if company_id not in MOCK_CLIENT_REGISTRY:
        print(f"note: '{company_id}' is not in MOCK_CLIENT_REGISTRY "
              f"(agent/subscription.py), so it resolves to the starter tier.")


def cmd_create(args) -> int:
    _warn_unknown_company(args.company)
    password = _read_password(args.password_stdin)
    try:
        user = auth.create_user(args.username, args.company, password)
    except auth.AuthError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"created {user.username} -> company {user.company_id}")
    return 0


def cmd_list(_args) -> int:
    users = auth.list_users()
    if not users:
        print("no users")
        return 0
    width = max(len(u["username"]) for u in users)
    for u in users:
        state = "disabled" if u["disabled"] else "active"
        print(f"{u['username']:<{width}}  {u['company_id']:<16} {state}")
    return 0


def cmd_set_password(args) -> int:
    password = _read_password(args.password_stdin)
    try:
        auth.set_password(args.username, password)
    except auth.AuthError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"password updated for {args.username}")
    return 0


def cmd_disable(args) -> int:
    try:
        auth.set_disabled(args.username, True)
    except auth.AuthError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    # Disabling also revokes live sessions; say so, because "disabled" that
    # leaves someone signed in for another twelve hours is not disabled.
    print(f"{args.username} disabled and existing sessions revoked")
    return 0


def cmd_enable(args) -> int:
    try:
        auth.set_disabled(args.username, False)
    except auth.AuthError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"{args.username} enabled")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    sub = parser.add_subparsers(dest="command", required=True)

    def add_password_flag(p):
        p.add_argument("--password-stdin", action="store_true",
                       help="read the password from stdin instead of prompting")

    p = sub.add_parser("create")
    p.add_argument("--username", required=True)
    p.add_argument("--company", required=True)
    add_password_flag(p)
    p.set_defaults(func=cmd_create)

    p = sub.add_parser("list")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("set-password")
    p.add_argument("--username", required=True)
    add_password_flag(p)
    p.set_defaults(func=cmd_set_password)

    p = sub.add_parser("disable")
    p.add_argument("--username", required=True)
    p.set_defaults(func=cmd_disable)

    p = sub.add_parser("enable")
    p.add_argument("--username", required=True)
    p.set_defaults(func=cmd_enable)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
