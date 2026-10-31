"""Command-line entry point."""

from __future__ import annotations

import argparse
import json
import sys
import textwrap

from . import __version__
from . import ai
from . import provenance as prov
from .gitutil import GitError, commit_message, git, parse_backport_refs, patch_id
from .matcher import Link, change_of, track
from .merger import A, B, MergeStopped, choose, execute, prepare, start_manual
from .merger import clean as safe
from .predictor import predict

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


def _track_ai(changes, args) -> dict[tuple[str, str], dict]:
    """Second opinion from the model on links whose code differs. Annotation only."""
    out: dict[tuple[str, str], dict] = {}
    todo = [l for ch in changes for l in ch.links if ai.needs_check(l)]
    if not todo:
        return out
    try:
        inf = ai.info()
    except ai.AIUnavailable as e:
        print(f"🤖 model not available: {safe(str(e))}. Run `provgit ai-check`.", file=sys.stderr)
        return out
    for n, link in enumerate(todo[:args.ai_limit], 1):
        print(f"🤖 checking link {n}/{min(len(todo), args.ai_limit)} "
              f"{link.a[:7]} ↔ {link.b[:7]} …", file=sys.stderr)
        try:
            out[(link.a, link.b)] = ai.same_change(link, args.repo, inf).to_dict()
        except ai.AIInvalid as e:
            out[(link.a, link.b)] = {"error": f"model answer rejected: {e}"}
        except ai.AIUnavailable as e:
            out[(link.a, link.b)] = {"error": str(e)}
            break
    if len(todo) > args.ai_limit:
        print(f"🤖 {len(todo) - args.ai_limit} more link(s) not checked (use --ai-limit)", file=sys.stderr)
    return out


def _print_link_ai(v: dict) -> None:
    if "error" in v:
        print(f"     🤖 {safe(v['error'])}")
    elif v["same_change"]:
        print(f"     🤖 {ai.label()}: same change, {v['confidence']} — \"{v['reason']}\"")
    else:
        print(f"     ⚠️  AI doubts this link ({v['confidence']}): \"{v['reason']}\"")
        print("        Test status is inherited through this link; check it by hand.")


def _cmd_track(args) -> int:
    changes = track(args.branches, cwd=args.repo, limit=args.limit)
    verdicts = _track_ai(changes, args) if args.ai else {}
    rows = []
    for ch in changes:
        st = prov.status_of(ch, args.prod, cwd=args.repo)
        row = {**ch.to_dict(), "status": st.to_dict()}
        for l in row["links"]:
            if (l["a"], l["b"]) in verdicts:
                l["ai"] = verdicts[(l["a"], l["b"])]
        rows.append(row)

    if args.json:
        print(json.dumps(rows, indent=2))
        return 0

    # Most interesting first: changes that exist on more than one branch.
    rows.sort(key=lambda r: -len(r["commits"]))
    for r in rows:
        st = r["status"]
        print(f"{ICON[st['level']]} {safe(r['title'])}")
        cells = []
        for br in args.branches:
            shas = r["commits"].get(br)
            cells.append(f"{br}={shas[0][:7] if shas else '—'}")
        print("   " + "  ".join(cells))
        print(f"   status: {st['level']} — {st['reason']}")
        for l in r["links"]:
            print(f"   linked {l['a'][:7]} ↔ {l['b'][:7]} [{l['kind']}] {l['detail']}")
            if "ai" in l:
                _print_link_ai(l["ai"])
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


def _cmd_predict(args) -> int:
    rep = predict(args.source, args.into, prod=args.prod, cwd=args.repo, limit=args.limit)
    if args.json:
        print(json.dumps(rep.to_dict(), indent=2))
        return 1 if rep.conflicts else 0
    _print_report(rep)
    return 1 if rep.conflicts else 0


def _print_report(rep) -> None:
    if rep.clean:
        print(f"✅ No conflicts: {rep.source} → {rep.into} merges cleanly.")
    else:
        n = len(rep.conflicts)
        print(f"⚠️  {n} conflict{'s' if n > 1 else ''}: {rep.source} → {rep.into}")
    for c in rep.conflicts:
        print()
        if c.a.lines or c.b.lines:
            print(f"{safe(c.path)}  (Side A line {c.a.start}, Side B line {c.b.start})")
        else:
            print(f"{safe(c.path)}  (whole-file conflict)")
        for label, s in (("A", c.a), ("B", c.b)):
            print(f"  Side {label} ({s.branch}):")
            for line in s.lines or ["(no lines: deleted or whole file)"]:
                print(f"     | {safe(line)}")
            for o in s.origins:
                copies = "  ".join(f"{br}={','.join(x[:7] for x in shas)}"
                                   for br, shas in o.copies.items())
                print(f"     from {o.sha[:7]} \"{safe(o.title)}\"   [{copies}]")
                print(f"     {ICON[o.level]} {o.level} — {o.reason}")
        print(f"  → {c.recommendation}")

    if rep.untested_incoming:
        print()
        print(f"⚠️  Tested alone ≠ tested together. {len(rep.untested_incoming)} incoming "
              f"change(s) from {rep.source} are not tested:")
        for o in rep.untested_incoming:
            print(f"   {ICON[o.level]} {o.sha[:7]} {safe(o.title)} — {o.level}")
    elif rep.clean:
        print("   Every incoming change passed CI on its own, but not together: "
              "run CI on the merge result.")


def _box(label: str, text: str) -> None:
    wrapped = textwrap.wrap(text, width=88, initial_indent=f"{label}: ",
                            subsequent_indent="  ") or [f"{label}:"]
    for line in wrapped:
        print(f"  │ {line}")


def _print_explanation(e: "ai.Explanation") -> None:
    print(f"  ┌ 🤖 {ai.label()} · explains only, does not change the verdict above")
    _box("Side A", e.side_a)
    _box("Side B", e.side_b)
    _box("Why they conflict", e.why_conflict)
    _box("Risk", e.risk)
    if e.can_combine:
        print("  │ Draft combined resolution (not tested):")
        for line in e.draft:
            print(f"  │    | {line}")
        _box("Why", e.rationale)
    else:
        print("  │ No combined resolution proposed.")
    if e.note:
        print(f"  │ ⚠️  {e.note}")
    m = e.meta
    print(f"  └ {m.get('model')} {str(m.get('digest', ''))[:19]} · Ollama {m.get('ollama_version')} "
          f"· {m.get('seconds')}s")


def _cmd_explain(args) -> int:
    rep = predict(args.source, args.into, prod=args.prod, cwd=args.repo, limit=args.limit)
    code = 1 if rep.conflicts else 0
    root = git("rev-parse", "--show-toplevel", cwd=args.repo).strip()   # conflict paths are root-relative
    if not args.json:
        _print_report(rep)

    results: list[dict | None] = [None] * len(rep.conflicts)
    todo = [i for i, c in enumerate(rep.conflicts) if c.a.lines or c.b.lines]
    skipped_whole = len(rep.conflicts) - len(todo)
    over = todo[args.max_conflicts:]
    todo = todo[:args.max_conflicts]

    unavailable = None
    inf = None
    if todo:
        try:
            inf = ai.info()
        except ai.AIUnavailable as e:
            unavailable = str(e)

    if not args.json and rep.conflicts:
        print()
        print("🤖 AI explanations")
    for n, i in enumerate(todo, 1):
        c = rep.conflicts[i]
        if unavailable:
            results[i] = {"error": unavailable}
            continue
        print(f"🤖 explaining {n}/{len(todo)} {safe(c.path)} …", file=sys.stderr)
        try:
            e = ai.explain_conflict(c, root, inf)
            results[i] = e.to_dict()
            print(f"🤖 done in {e.meta.get('seconds')}s", file=sys.stderr)
        except ai.AIInvalid as err:
            results[i] = {"error": f"model answer rejected: {err}"}
        except ai.AIUnavailable as err:
            unavailable = str(err)
            results[i] = {"error": unavailable}
        if not args.json:
            print()
            print(f"Conflict {n}: {safe(c.path)} (Side A line {c.a.start}, Side B line {c.b.start})")
            if "error" in results[i]:
                print(f"  🤖 {safe(results[i]['error'])}")
            else:
                _print_explanation(e)

    if args.json:
        data = rep.to_dict()
        for i, r in enumerate(results):
            if r is not None:
                data["conflicts"][i]["ai"] = r
        print(json.dumps(data, indent=2))
        return code

    if unavailable:
        print()
        print(f"🤖 model not available: {safe(unavailable)}")
        print("   Showing provgit's verdict only. Run `provgit ai-check`.")
    if skipped_whole:
        print(f"🤖 {skipped_whole} whole-file conflict(s) not explained (no lines to explain).")
    if over:
        print(f"🤖 {len(over)} more conflict(s) not explained (use --max-conflicts).")
    return code


def _cmd_ai_check(args) -> int:
    rows = ai.check()
    for what, ok, detail in rows:
        print(f"{what:<8} {'✅' if ok else '❌'} {safe(detail)}")
    return 0 if rows and all(ok for _, ok, _ in rows) else 1


def _ask(n: int, c) -> str:
    """Ask the user for one conflict. Returns A, B, 'm' (manual) or 'q' (quit)."""
    prompt = (f"Conflict {n} ({safe(c.path)}): keep [A] {c.a.branch} {ICON[c.a.level]} / "
              f"[B] {c.b.branch} {ICON[c.b.level]} / [m]anual / [q]uit? ")
    while True:
        try:
            ans = input(prompt).strip().lower()
        except EOFError:
            return "q"
        if ans in ("a", "b"):
            return ans.upper()
        if ans in ("m", "q"):
            return ans


def _cmd_merge(args) -> int:
    try:
        plan = prepare(args.source, args.into, prod=args.prod, cwd=args.repo, limit=args.limit)
        _print_report(plan.report)
        print()

        interactive = sys.stdin.isatty()
        for n, c in enumerate(plan.report.conflicts, 1):
            pick, reason = choose(c, args.prefer_tested, args.take)
            if pick is None and interactive:
                print(f"provgit won't choose for conflict {n}: {reason}.")
                pick = _ask(n, c)
                if pick == "q":
                    print("Stopped. Nothing was changed.")
                    return 1
                if pick == "m":
                    start_manual(plan)
                    print(f"Merge started with conflicts left in the files. Resolve them, "
                          f"`git add` them, then `git commit` (or `git merge --abort`).")
                    return 1
            elif pick is None:
                print(f"provgit won't choose for conflict {n} ({safe(c.path)}): {reason}.")
                print("Nothing was changed. Use --take A|B, or run in a terminal to choose.")
                return 1
            else:
                print(f"Conflict {n} ({safe(c.path)}): keeping Side {pick} — {reason}.")
            plan.choices.append(pick)

        res = execute(plan, rerere=args.rerere)
    except MergeStopped as e:
        print(f"stopped: {e}. Nothing was changed.", file=sys.stderr)
        return 1

    if res.status == "up-to-date":
        print(f"Already up to date: {args.into} contains {args.source}.")
        return 0
    print(f"✅ Merge commit {res.sha[:7]} written on {args.into}:")
    print()
    for line in res.message.splitlines():
        print(f"   {line}")
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
    p.add_argument("--ai", action="store_true",
                   help="ask the local model whether edited/PR-linked commits are the same change (note only)")
    p.add_argument("--ai-limit", type=int, default=20, help="most links to check with --ai")
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

    p = sub.add_parser("predict", help="dry-run a merge: conflicts, where each side came from, was it tested")
    p.add_argument("source", help="branch to merge in (e.g. DEV)")
    p.add_argument("--into", required=True, help="branch to merge into (e.g. UAT)")
    p.add_argument("--prod", help="name of the production branch (e.g. PROD)")
    p.add_argument("--limit", type=int, default=500)
    p.add_argument("--json", action="store_true")
    _add_repo(p)
    p.set_defaults(func=_cmd_predict)

    p = sub.add_parser("explain", help="predict + plain-English explanation of each conflict "
                                        "from a local model (Ollama); never changes the verdict")
    p.add_argument("source", help="branch to merge in (e.g. DEV)")
    p.add_argument("--into", required=True, help="branch to merge into (e.g. UAT)")
    p.add_argument("--prod", help="name of the production branch (e.g. PROD)")
    p.add_argument("--max-conflicts", type=int, default=5, help="most conflicts to explain")
    p.add_argument("--limit", type=int, default=500)
    p.add_argument("--json", action="store_true")
    _add_repo(p)
    p.set_defaults(func=_cmd_explain)

    p = sub.add_parser("ai-check", help="is the local model (Ollama) ready?")
    p.set_defaults(func=_cmd_ai_check)

    p = sub.add_parser("merge", help="merge with provenance: pick a side per conflict, "
                                      "standard merge commit with trailers")
    p.add_argument("source", help="branch to merge in (e.g. DEV)")
    p.add_argument("--into", required=True, help="branch to merge into; must be checked out")
    p.add_argument("--prod", help="name of the production branch (e.g. PROD)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--prefer-tested", action="store_true",
                   help="keep the tested side when exactly one side is tested")
    g.add_argument("--take", choices=[A, B], help="keep this side for every conflict")
    p.add_argument("--rerere", action="store_true", help="let git remember and reuse the resolution")
    p.add_argument("--limit", type=int, default=500)
    _add_repo(p)
    p.set_defaults(func=_cmd_merge)

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
