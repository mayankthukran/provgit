#!/usr/bin/env python3
"""Summarise `provgit track --json` for a main branch and a stable (backport) branch.

Counting rules (kept honest on purpose):
  - The denominator is the declared backports ("Backport of X" / "cherry picked from commit X")
    among the same --limit commits of the stable branch that `track` read.
  - "Linked" = the backport's change also holds a different commit on the main branch.
  - "Partial" counts only declared links whose code was modified during the backport,
    never groups joined only by ticket numbers.
  - Groups joined only by ticket numbers ("weak") are reported separately: ticket numbers
    such as "Refs #36001" can chain unrelated commits together.

Usage:
  python scripts/summarize_track.py track.json -C django-src --main main --stable stable/6.1.x \
      --limit 3000 [--note "2.95 s, 93 MB peak"] [--examples 2,3,3]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from provgit.gitutil import parse_backport_refs  # noqa: E402

GITHUB = "https://github.com/django/django/commit/"


def git(*args: str, cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          encoding="utf-8", errors="surrogateescape")


def stable_backports(cwd: str, branch: str, limit: int) -> dict[str, list[str]]:
    """sha -> source shas named in its backport trailer, for the first `limit` commits."""
    shallow = Path(cwd, ".git", "shallow")
    skip = [f"^{s}" for s in shallow.read_text().split()] if shallow.exists() else []
    out = git("log", "--no-merges", f"-n{limit}", "--format=%H%x00%B%x01", branch, *skip, cwd=cwd).stdout
    result = {}
    for chunk in out.split("\x01"):
        sha, _, msg = chunk.strip("\n").partition("\x00")
        refs = parse_backport_refs(msg)
        if sha and refs:
            result[sha] = [r.source_sha for r in refs]
    return result


def exists(sha: str, cwd: str) -> bool:
    return git("cat-file", "-e", f"{sha}^{{commit}}", cwd=cwd).returncode == 0


def link_row(kind: str, l: dict, ch: dict, stable: str, cwd: str) -> str:
    """One table row; columns ordered by which branch each commit is really on."""
    on_stable = set(ch["commits"].get(stable, []))
    s_sha, m_sha = (l["b"], l["a"]) if l["b"] in on_stable else (l["a"], l["b"])
    title = git("log", "-1", "--format=%s", s_sha, cwd=cwd).stdout.strip().replace("|", "/")
    return (f"| {kind} | [{s_sha[:10]}]({GITHUB}{s_sha}) | [{m_sha[:10]}]({GITHUB}{m_sha}) | "
            f"{title[:70]} | TODO |")


def main() -> int:  # noqa: C901
    ap = argparse.ArgumentParser()
    ap.add_argument("track_json")
    ap.add_argument("-C", dest="repo", required=True)
    ap.add_argument("--main", required=True)
    ap.add_argument("--stable", required=True)
    ap.add_argument("--limit", type=int, required=True)
    ap.add_argument("--note", default="")
    ap.add_argument("--examples", default="2,3,2,3", help="how many exact,partial,context,weak examples")
    args = ap.parse_args()

    changes = json.loads(Path(args.track_json).read_text())
    change_of = {s: ch for ch in changes for shas in ch["commits"].values() for s in shas}
    declared = stable_backports(args.repo, args.stable, args.limit)

    linked, outside, other = [], [], []
    for sha, sources in declared.items():
        ch = change_of.get(sha)
        on_main = [s for s in (ch or {}).get("commits", {}).get(args.main, []) if s != sha]
        if on_main:
            linked.append(sha)
        elif not any(exists(src, args.repo) for src in sources):
            outside.append(sha)
        else:
            other.append(sha)

    links = [(ch, l) for ch in changes for l in ch["links"]]
    exact = [(ch, l) for ch, l in links if l["kind"] == "exact"]
    partial = [(ch, l) for ch, l in links if l["kind"] == "declared" and "modified" in l["detail"]]
    context = [(ch, l) for ch, l in links if l["kind"] == "declared" and "same changed lines" in l["detail"]]
    weak_groups = [ch for ch in changes
                   if ch["links"] and all(l["kind"] == "referenced" for l in ch["links"])]
    size = lambda ch: len({s for v in ch["commits"].values() for s in v})
    largest_weak = max((size(ch) for ch in weak_groups), default=0)
    largest = max((size(ch) for ch in changes), default=0)
    common = sum(1 for ch in changes
                 if len(ch["commits"]) > 1 and not ch["links"])   # same SHA on both: shared history

    n_ex, n_part, n_ctx, n_weak = (int(x) for x in args.examples.split(","))
    d = len(declared)
    pct = f"{100 * len(linked) / d:.0f}%" if d else "n/a"

    print(f"# Django {args.main} vs {args.stable}: `provgit track` on real backports\n")
    print(f"Run: `provgit track {args.main} {args.stable} --limit {args.limit} --json`"
          + (f" · {args.note}" if args.note else "") + "\n")
    print("| Measure | Count |")
    print("|---|---|")
    print(f"| Declared backports on {args.stable} (`Backport of …` trailer, in the commits read) | {d} |")
    print(f"| … linked to their {args.main} commit | **{len(linked)} ({pct})** |")
    print(f"| … not linked: source commit outside this shallow clone | {len(outside)} |")
    print(f"| … not linked: other reason | {len(other)} |")
    print(f"| Links by identical patch-id (exact copy) | {len(exact)} |")
    print(f"| **Partial: backport code edited during conflict resolution** (edited lines never tested on {args.main}) | **{len(partial)}** |")
    print(f"| Same changed lines, different surrounding code (not partial) | {len(context)} |")
    print(f"| Weak groups (joined only by a ticket number) | {len(weak_groups)}, largest has {largest_weak} commits |")
    print(f"| Commits shared by both branches (history before the branch point, same SHA) | {common} |")
    print(f"| Largest change (commits) — more than 2 would mean separate fixes were chained | {largest} |")
    print()
    print("## Checked by hand\n")
    print(f"| Kind | {args.stable} commit | {args.main} commit | Title | Same fix? |")
    print("|---|---|---|---|---|")
    for ch, l in exact[:n_ex]:
        print(link_row("exact", l, ch, args.stable, args.repo))
    for ch, l in partial[:n_part]:
        print(link_row("partial", l, ch, args.stable, args.repo))
    for ch, l in context[:n_ctx]:
        print(link_row("same lines", l, ch, args.stable, args.repo))
    for ch in weak_groups[:n_weak]:
        print(link_row("weak", ch["links"][0], ch, args.stable, args.repo))
    print()
    print("Known limitation: ticket-number links can chain unrelated `Refs #N` commits; they are "
          "reported separately and never counted as partial backports.\n")
    print("Data: Django (BSD-3-Clause), https://github.com/django/django")
    if other:
        print("\n<!-- not linked, other reason: " + " ".join(s[:10] for s in other) + " -->")
    return 0


if __name__ == "__main__":
    sys.exit(main())
