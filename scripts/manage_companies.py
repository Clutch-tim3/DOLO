#!/usr/bin/env python3
"""
Create and manage customer companies.

Tiers used to be `MOCK_CLIENT_REGISTRY`, a dict of three ids in
agent/subscription.py. Putting a customer on a plan meant editing source and
deploying. This is the operation that replaces that.

    python scripts/manage_companies.py list
    python scripts/manage_companies.py create --company acme_trading \\
        --name "Acme Trading (Pty) Ltd" --tier pro
    python scripts/manage_companies.py set-tier --company acme_trading --tier enterprise
    python scripts/manage_companies.py suspend --company acme_trading
    python scripts/manage_companies.py activate --company acme_trading

A company must exist before a user can usefully be created against it:
`manage_users.py create --company <id>` will happily provision an account for
an unknown company, and that account silently resolves to the starter tier.
`create` here warns if the id looks unused.

Suspending is preferred over deleting for a real customer — it drops them to
the starter tier while keeping the record, so their data stays attributable.
`delete` exists for undoing a mistaken creation.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from agent.memory import company_registry  # noqa: E402


def _print_company(c: dict) -> None:
    flag = "" if c["status"] == company_registry.STATUS_ACTIVE else f"  [{c['status'].upper()}]"
    print(f"  {c['company_id']:<24} {c['tier']:<12} {c['display_name'] or ''}{flag}")


def cmd_list(args) -> int:
    companies = company_registry.list_companies()
    if not companies:
        print("no companies")
        return 0
    print(f"{'COMPANY_ID':<26}{'TIER':<12}NAME")
    for c in companies:
        _print_company(c)
    print(f"\n{len(companies)} company(ies)")
    return 0


def cmd_create(args) -> int:
    try:
        company = company_registry.create_company(
            args.company, display_name=args.name or args.company,
            tier=args.tier, created_by=args.by or "", notes=args.notes or "")
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print("created:")
    _print_company(company)
    print(f"\nNow provision a user for it:\n"
          f"  python scripts/manage_users.py create --username <email> --company {args.company}")
    return 0


def cmd_set_tier(args) -> int:
    try:
        company = company_registry.set_tier(args.company, args.tier)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print("updated:")
    _print_company(company)
    return 0


def _set_status(company_id: str, status: str) -> int:
    try:
        company = company_registry.set_status(company_id, status)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print("updated:")
    _print_company(company)
    if status == company_registry.STATUS_SUSPENDED:
        print("\nThey now resolve to the starter tier. The record and their data remain.")
    return 0


def cmd_suspend(args) -> int:
    return _set_status(args.company, company_registry.STATUS_SUSPENDED)


def cmd_activate(args) -> int:
    return _set_status(args.company, company_registry.STATUS_ACTIVE)


def cmd_delete(args) -> int:
    if not args.yes:
        print("Refusing without --yes. Prefer `suspend` for a real customer: it drops them\n"
              "to starter while keeping the record, so their data stays attributable.",
              file=sys.stderr)
        return 2
    if company_registry.delete_company(args.company):
        print(f"deleted {args.company}")
        print("Their profile, archive, packs and outcomes are untouched and now unreachable.")
        return 0
    print(f"no such company: {args.company}", file=sys.stderr)
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    sub.add_parser("list").set_defaults(func=cmd_list)

    p = sub.add_parser("create")
    p.add_argument("--company", required=True, help="company_id, e.g. acme_trading")
    p.add_argument("--name", help="display name")
    p.add_argument("--tier", default=company_registry.DEFAULT_TIER)
    p.add_argument("--by", help="who provisioned it")
    p.add_argument("--notes")
    p.set_defaults(func=cmd_create)

    p = sub.add_parser("set-tier")
    p.add_argument("--company", required=True)
    p.add_argument("--tier", required=True)
    p.set_defaults(func=cmd_set_tier)

    for name, fn in (("suspend", cmd_suspend), ("activate", cmd_activate)):
        p = sub.add_parser(name)
        p.add_argument("--company", required=True)
        p.set_defaults(func=fn)

    p = sub.add_parser("delete")
    p.add_argument("--company", required=True)
    p.add_argument("--yes", action="store_true", help="confirm removal")
    p.set_defaults(func=cmd_delete)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
