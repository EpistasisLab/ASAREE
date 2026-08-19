"""Command-line entry point for ASAREE's schema — a deploy step, not app startup.

    python -m asaree.migrations upgrade
    python -m asaree.migrations current
    python -m asaree.migrations stamp    # adopt an existing schema
    python -m asaree.migrations downgrade --revision base

Run Motoro's own chain first (``python -m motoro.migrations
upgrade``) — this one does not know or care about that one, but product tables
routinely reference a core row by opaque UUID, so core's schema should exist
first as a matter of sequencing the deploy, not a coded dependency.
"""

from __future__ import annotations

import argparse
import asyncio
import sys


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m asaree.migrations")
    ap.add_argument("command", choices=["upgrade", "downgrade", "current", "stamp"])
    ap.add_argument("--url", default=None, help="Database URL. Defaults to product_database_url.")
    ap.add_argument("--revision", default=None, help="Target revision. Defaults: head, or -1 for downgrade.")
    args = ap.parse_args(argv)

    from asaree.migrations import current_revision, downgrade, stamp, upgrade

    url = args.url

    if args.command == "current":
        rev = asyncio.run(current_revision(url))
        print(rev or "not migrated")
        return 0

    if args.command == "upgrade":
        upgrade(url, args.revision or "head")
    elif args.command == "downgrade":
        upgrade_target = args.revision or "-1"
        downgrade(url, upgrade_target)
    elif args.command == "stamp":
        stamp(url, args.revision or "head")

    rev = asyncio.run(current_revision(url))
    print(f"{args.command} -> {rev or 'not migrated'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
