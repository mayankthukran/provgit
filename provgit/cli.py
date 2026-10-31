"""Command-line entry point."""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from . import provenance as prov
from .gitutil import GitError, commit_message, parse_backport_refs, patch_id
from .matcher import change_of, track

ICON = {
    prov.PROD_VERIFIED: "🟢",
    prov.CI_PASSED: "🔵",
    prov.IN_PROD_NO_CI: "🟡",
    prov.CI_FAILED: "🟠",
    prov.UNTESTED: "🔴",
    prov.UNKNOWN: "⚪",
}


def _add_repo(p: argparse.ArgumentParser) -> None:
    p.add_argument("-C", dest="repo", default=".", help="path to the git repository")


def _cmd_show(args) -> int:
    msg = commit_message(args.sha, cwd=args.repo)
    print(json.dumps({
        "sha": args.sha,
        "patch_id": patch_id(args.sha, cwd=args.repo),
        "backport_of": [vars(r) for r in parse_backport_refs(msg)],
    }, indent=2))
    return 0


def _cmd_track(args) -> int:
    changes = track(args.branches, cwd=args.repo, limit=args.limit)
    rows = []
    for ch in changes:
        st = prov.status_of(ch, args.prod, cwd=args.repo)
        rows.append({**ch.to_dict(), "status": st.to_dict()})

    if args.json:
        print(json.dumps(rows, indent=2))
        return 0

    # Most interesting first: changes that exist on more than one branch.
    rows.sort(key=lambda r: -len(r["commits"]))
    for r in rows:
        st = r["status"]
        print(f"{ICON[st['level']]} {r['title']}")
        cells = []
        for br in args.branches:
            shas = r["commits"].get(br)
            cells.append(f"{br}={shas[0][:7] if shas else '—'}")
        print("   " + "  ".join(cells))
        print(f"   status: {st['level']} — {st['reason']}")
        for l in r["links"]:
            print(f"   linked {l['a'][:7]} ↔ {l['b'][:7]} [{l['kind']}] {l['detail']}")
        print()
    return 0


def _cmd_record(args) -> int:
    data = prov.record(args.sha, args.ci, cwd=args.repo, url=args.url, env=args.env)
    print(f"recorded {args.sha}: {json.dumps(data)}")
    return 0


def _cmd_import_ci(args) -> int:
    n = prov.import_ci(args.file, cwd=args.repo)
    print(f"imported {n} CI result(s) into refs/notes/{prov.NOTES_REF}")
    return 0


def _cmd_status(args) -> int:
    changes = track(args.branches, cwd=args.repo, limit=args.limit)
    ch = change_of(args.sha, changes)
    if not ch:
        print(f"{args.sha} is not on any of: {', '.join(args.branches)}")
        return 1
    st = prov.status_of(ch, args.prod, cwd=args.repo)
    print(json.dumps({"change": ch.to_dict(), "status": st.to_dict()}, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="provgit", description="Git conflict resolution with testing provenance.")
    parser.add_argument("--version", action="version", version=f"provgit {__version__}")
    sub = parser.add_subparsers(dest="cmd")

    p = sub.add_parser("show", help="patch-id and backport references of one commit")
    p.add_argument("sha")
    _add_repo(p)
    p.set_defaults(func=_cmd_show)

    p = sub.add_parser("track", help="follow each change across branches, with its test status")
    p.add_argument("branches", nargs="+")
    p.add_argument("--prod", help="name of the production branch (e.g. PROD)")
    p.add_argument("--limit", type=int, default=500, help="commits read per branch")
    p.add_argument("--json", action="store_true")
    _add_repo(p)
    p.set_defaults(func=_cmd_track)

    p = sub.add_parser("record", help="record a CI result for a commit (git notes)")
    p.add_argument("sha")
    p.add_argument("--ci", required=True, choices=["passed", "failed"])
    p.add_argument("--url")
    p.add_argument("--env")
    _add_repo(p)
    p.set_defaults(func=_cmd_record)

    p = sub.add_parser("import-ci", help="import CI results from a JSON file")
    p.add_argument("file")
    _add_repo(p)
    p.set_defaults(func=_cmd_import_ci)

    p = sub.add_parser("status", help="test status of one commit, inherited across its copies")
    p.add_argument("sha")
    p.add_argument("--branches", nargs="+", required=True)
    p.add_argument("--prod")
    p.add_argument("--limit", type=int, default=500)
    _add_repo(p)
    p.set_defaults(func=_cmd_status)

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except GitError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
