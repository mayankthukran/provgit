"""Command-line entry point. Commands are added PR by PR."""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .gitutil import commit_message, parse_backport_refs, patch_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="provgit",
        description="Git conflict resolution with testing provenance.",
    )
    parser.add_argument("--version", action="version", version=f"provgit {__version__}")
    sub = parser.add_subparsers(dest="cmd")

    show = sub.add_parser("show", help="Show patch-id and backport references of a commit")
    show.add_argument("sha")
    show.add_argument("-C", dest="repo", default=".", help="path to the git repository")

    args = parser.parse_args(argv)

    if args.cmd == "show":
        msg = commit_message(args.sha, cwd=args.repo)
        result = {
            "sha": args.sha,
            "patch_id": patch_id(args.sha, cwd=args.repo),
            "backport_of": [vars(r) for r in parse_backport_refs(msg)],
        }
        print(json.dumps(result, indent=2))
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
