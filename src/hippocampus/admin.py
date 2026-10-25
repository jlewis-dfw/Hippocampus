"""Admin CLI for access control, talking straight to Postgres (so the first admin needs no API token).

    HIPPO_DSN=... python -m hippocampus.admin create-client --name root --admin
    HIPPO_DSN=... python -m hippocampus.admin create-client --name private-kobold --read 'private-*' --write 'private-*'
    HIPPO_DSN=... python -m hippocampus.admin list-clients | rotate-client NAME | revoke-client NAME | set-visibility SLUG restricted

The token is printed once and never stored (only its SHA-256 is).
"""
from __future__ import annotations

import argparse
import os
import sys

from .auth import CLIENT_NAME_RE, hash_token, is_private_slug, new_token, validate_grants
from .store import connect_store


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="hippocampus.admin", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create-client", help="create a client and print its token once")
    c.add_argument("--name", required=True)
    c.add_argument("--admin", action="store_true")
    c.add_argument("--read", action="append", default=[], metavar="SLUG_OR_GLOB")
    c.add_argument("--write", action="append", default=[], metavar="SLUG_OR_GLOB")
    c.add_argument("--note")
    sub.add_parser("list-clients")
    ro = sub.add_parser("rotate-client", help="new token for an existing client; the old one stops working")
    ro.add_argument("name")
    r = sub.add_parser("revoke-client")
    r.add_argument("name")
    v = sub.add_parser("set-visibility")
    v.add_argument("slug")
    v.add_argument("visibility", choices=["shared", "restricted"])
    args = ap.parse_args(argv)

    dsn = os.environ.get("HIPPO_DSN")
    if not dsn:
        print("HIPPO_DSN is not set", file=sys.stderr)
        return 2
    with connect_store(dsn)() as store:
        if args.cmd == "create-client":
            if not CLIENT_NAME_RE.match(args.name):
                print(f"invalid client name {args.name!r}", file=sys.stderr)
                return 2
            try:
                read, write = validate_grants(args.read), validate_grants(args.write)
            except ValueError as exc:
                print(exc, file=sys.stderr)
                return 2
            token = new_token()
            if not store.create_client(args.name, hash_token(token), args.admin, read, write, args.note):
                print(f"client {args.name!r} already exists", file=sys.stderr)
                return 1
            print(f"client {args.name}: admin={args.admin} read={read} write={write}")
            print(f"token (shown once, store it in the vault): {token}")
        elif args.cmd == "list-clients":
            for cl in store.list_clients():
                state = "REVOKED" if cl["revoked"] else "active"
                print(f"{cl['name']:<24} {state:<8} admin={cl['admin']!s:<5} read={cl['read']} write={cl['write']} "
                      f"last_used={cl['last_used_at'] or '-'}")
        elif args.cmd == "rotate-client":
            token = new_token()
            if not store.rotate_client(args.name, hash_token(token)):
                print(f"no active client {args.name!r}", file=sys.stderr)
                return 1
            print(f"rotated {args.name}: the old token no longer works")
            print(f"token (shown once, store it in the vault): {token}")
        elif args.cmd == "revoke-client":
            if not store.revoke_client(args.name):
                print(f"no active client {args.name!r}", file=sys.stderr)
                return 1
            print(f"revoked {args.name}")
        elif args.cmd == "set-visibility":
            if args.visibility == "shared" and is_private_slug(args.slug):
                print("collections named private* must stay restricted", file=sys.stderr)
                return 2
            if not store.update_collection(args.slug, None, None, args.visibility):
                print(f"no collection {args.slug!r}", file=sys.stderr)
                return 1
            print(f"{args.slug} is now {args.visibility}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
