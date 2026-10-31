"""Follow each logical change across branches, even when cherry-picks give it new SHAs.

Commits are linked into one "change" by three kinds of evidence, strongest first:
  exact       same `git patch-id` (identical diff)
  declared    a backport trailer names the source commit
              (CPython "(cherry picked from commit X)", Django "Backport of X from main.")
  referenced  the commit titles share a PR / issue number (#812, GH-112400)
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from .gitutil import git, parse_backport_refs

_TITLE_NUM = re.compile(r"(?:GH-|#)(\d+)")
STRENGTH = {"exact": 3, "declared": 2, "referenced": 1}


@dataclass
class Commit:
    sha: str
    branch: str
    title: str
    message: str
    patch_id: str | None = None


@dataclass
class Link:
    a: str
    b: str
    kind: str            # exact | declared | referenced
    detail: str = ""


@dataclass
class Change:
    id: str                                   # short sha of the earliest commit
    title: str
    commits: dict[str, list[str]] = field(default_factory=dict)   # branch -> shas
    links: list[Link] = field(default_factory=list)

    def all_shas(self) -> list[str]:
        return [s for shas in self.commits.values() for s in shas]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "commits": self.commits,
            "links": [vars(l) for l in self.links],
        }


def title_numbers(title: str) -> set[str]:
    return set(_TITLE_NUM.findall(title))


def shallow_roots(cwd: str) -> list[str]:
    """Oldest commits of a shallow clone. They have no parent here, so `git log -p` would show
    them as adding every file in the repository; they must not be read as changes."""
    path = git("rev-parse", "--git-path", "shallow", cwd=cwd).strip()
    full = path if os.path.isabs(path) else os.path.join(cwd, path)
    try:
        with open(full) as fh:
            return [line.strip() for line in fh if line.strip()]
    except FileNotFoundError:
        return []


def _read_branch(branch: str, cwd: str, limit: int, exclude: list[str] | None = None) -> list[Commit]:
    """Read commit messages and patch-ids for one branch in two git calls."""
    skip = [f"^{s}" for s in shallow_roots(cwd)]
    raw = git("log", "--no-merges", f"-n{limit}", "--format=%H%x00%B%x01", branch, *skip, cwd=cwd)
    commits: dict[str, Commit] = {}
    order: list[str] = []
    for chunk in raw.split("\x01"):
        chunk = chunk.strip("\n")
        if not chunk:
            continue
        sha, _, msg = chunk.partition("\x00")
        msg = msg.strip()
        commits[sha] = Commit(sha, branch, msg.splitlines()[0] if msg else "", msg)
        order.append(sha)

    paths = ["--", ".", *(f":(exclude){p}" for p in exclude)] if exclude else []
    patches = git("log", "--no-merges", f"-n{limit}", "-p", "--format=commit %H", branch, *skip,
                  *paths, cwd=cwd)
    pid_out = git("patch-id", "--stable", cwd=cwd, input=patches)
    for line in pid_out.splitlines():
        pid, sha = line.split()
        if sha in commits:
            commits[sha].patch_id = pid
    return [commits[s] for s in order]


def _changed_lines(sha: str, cwd: str, exclude: list[str] | None = None) -> list[str]:
    """Only the added and removed lines of a commit, per file, without context or line numbers."""
    paths = ["--", ".", *(f":(exclude){p}" for p in exclude)] if exclude else []
    out = git("show", "--no-color", "--format=", "--unified=0", sha, *paths, cwd=cwd)
    keep = []
    for line in out.splitlines():
        if line.startswith(("+++ ", "--- ")):
            keep.append(line)                       # which file
        elif line[:1] in ("+", "-"):
            keep.append(line)
    return keep


class _UnionFind:
    def __init__(self):
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        self.parent[self.find(a)] = self.find(b)


def track(branches: list[str], cwd: str = ".", limit: int = 500,
          exclude: list[str] | None = None) -> list[Change]:
    """Group the commits of several branches into logical changes.

    `exclude`: path patterns left out of the patch-id (e.g. "*.po" for huge translation files).
    """
    by_sha: dict[str, Commit] = {}
    seen_on: dict[str, set[str]] = {}
    for br in branches:
        for c in _read_branch(br, cwd, limit, exclude):
            seen_on.setdefault(c.sha, set()).add(br)
            # A commit shared by several branches (common history) is kept once,
            # under the first branch listed.
            by_sha.setdefault(c.sha, c)

    uf = _UnionFind()
    links: list[Link] = []
    for sha in by_sha:
        uf.find(sha)

    def link(a: str, b: str, kind: str, detail: str = "") -> None:
        if a == b:
            return
        if uf.find(a) != uf.find(b) or kind in ("exact", "declared"):
            links.append(Link(a, b, kind, detail))
        uf.union(a, b)

    # exact: identical patch-id
    by_pid: dict[str, list[str]] = {}
    for c in by_sha.values():
        if c.patch_id:
            by_pid.setdefault(c.patch_id, []).append(c.sha)
    for shas in by_pid.values():
        for other in shas[1:]:
            link(shas[0], other, "exact", "same patch-id")

    # declared: backport trailers (accept abbreviated SHAs)
    for c in by_sha.values():
        for ref in parse_backport_refs(c.message):
            src = next((s for s in by_sha if s.startswith(ref.source_sha)), None)
            if src:
                if by_sha[src].patch_id == c.patch_id:
                    detail = ""
                elif _changed_lines(src, cwd, exclude) == _changed_lines(c.sha, cwd, exclude):
                    # Same added/removed lines; only the unchanged lines around them differ
                    # (the file on the other branch looks slightly different). Not partial.
                    detail = "; same changed lines, applied to different surrounding code"
                else:
                    detail = "; code was modified during backport"
                link(src, c.sha, "declared", f"{ref.style} trailer{detail}")

    # referenced: shared PR/issue number in titles, across different branches only
    by_num: dict[str, list[str]] = {}
    for c in by_sha.values():
        for n in title_numbers(c.title):
            by_num.setdefault(n, []).append(c.sha)
    # A ticket number is weak evidence: "Refs #N" is used by many unrelated commits. Link two
    # commits by number only while neither already has a partner on the other's branch, so
    # numbers can join a lonely backport to its source but never chain complete pairs
    # together (which would let one change inherit another change's CI result).
    group_branches: dict[str, set[str]] = {}
    for sha in by_sha:
        group_branches.setdefault(uf.find(sha), set()).update(seen_on[sha])

    for n, shas in by_num.items():
        for i, x in enumerate(shas):
            for y in shas[i + 1:]:
                bx, by = by_sha[x].branch, by_sha[y].branch
                rx, ry = uf.find(x), uf.find(y)
                if bx == by or rx == ry:
                    continue
                if by in group_branches[rx] or bx in group_branches[ry]:
                    continue
                link(x, y, "referenced", f"both titles mention #{n}")
                merged = group_branches.pop(rx) | group_branches.pop(ry)
                group_branches[uf.find(x)] = merged

    groups: dict[str, list[str]] = {}
    for sha in by_sha:
        groups.setdefault(uf.find(sha), []).append(sha)

    changes: list[Change] = []
    for members in groups.values():
        member_set = set(members)
        commits: dict[str, list[str]] = {}
        for sha in members:
            for br in sorted(seen_on[sha], key=branches.index):
                commits.setdefault(br, []).append(sha)
        first = members[-1]
        changes.append(Change(
            id=first[:7],
            title=by_sha[first].title,
            commits=commits,
            links=[l for l in links if l.a in member_set],
        ))
    return changes


def change_of(sha: str, changes: list[Change]) -> Change | None:
    for ch in changes:
        if any(s.startswith(sha) or sha.startswith(s) for s in ch.all_shas()):
            return ch
    return None


def is_partial(change: Change) -> bool:
    """True when the copies of this change do not share one diff (edited during backport)."""
    return any("modified" in l.detail for l in change.links) or (
        any(l.kind == "referenced" for l in change.links)
        and not any(l.kind == "exact" for l in change.links)
    )
