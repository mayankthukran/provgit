"""Predict merge conflicts before merging, and say where each side came from and whether it was tested.

Steps:
  1. Dry-run merge with `git merge-tree --write-tree` (Git 2.38+). Nothing in the working
     folder, the index or any branch changes.
  2. Read each conflicted file from the result tree and split it into conflict hunks,
     keeping track of each side's line numbers in its own branch.
  3. `git blame` those lines on each branch to find the commits that wrote them.
  4. Map each commit to its logical change (matcher.track) and its trust level
     (provenance.status_of), which is inherited across cherry-picks and backports.
  5. Recommend a side, and warn that changes tested alone were never tested together.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import provenance as prov
from .gitutil import git
from .matcher import Change, change_of, track

# Weakest first. A side made of several changes is only as trusted as its weakest one.
RANK = {
    prov.UNKNOWN: 0,
    prov.CI_FAILED: 1,
    prov.UNTESTED: 2,
    prov.IN_PROD_NO_CI: 3,
    prov.CI_PASSED: 4,
    prov.PROD_VERIFIED: 5,
}

_BLAME_HEADER = re.compile(r"^([0-9a-f]{40}) \d+ \d+")


@dataclass
class Origin:
    sha: str
    title: str
    change_id: str
    level: str
    reason: str
    partial: bool
    copies: dict[str, list[str]]          # branch -> shas of the same change

    def to_dict(self) -> dict:
        return vars(self)


@dataclass
class Side:
    branch: str
    lines: list[str]
    start: int                            # 1-based line in this branch's version of the file
    origins: list[Origin] = field(default_factory=list)

    @property
    def level(self) -> str:
        if not self.origins:
            return prov.UNKNOWN
        return min((o.level for o in self.origins), key=RANK.__getitem__)

    @property
    def partial(self) -> bool:
        return any(o.partial for o in self.origins)

    @property
    def trusted(self) -> bool:
        return self.level in prov.TRUSTED

    def to_dict(self) -> dict:
        return {
            "branch": self.branch,
            "lines": self.lines,
            "start": self.start,
            "level": self.level,
            "partial": self.partial,
            "origins": [o.to_dict() for o in self.origins],
        }


@dataclass
class Conflict:
    path: str
    a: Side                               # the branch being merged into
    b: Side                               # the branch being merged in
    recommendation: str = ""

    def to_dict(self) -> dict:
        return {"path": self.path, "a": self.a.to_dict(), "b": self.b.to_dict(),
                "recommendation": self.recommendation}


@dataclass
class Report:
    source: str
    into: str
    conflicts: list[Conflict]
    untested_incoming: list[Origin]       # changes coming in that are not CI_PASSED / PROD_VERIFIED

    @property
    def clean(self) -> bool:
        return not self.conflicts

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "into": self.into,
            "clean": self.clean,
            "conflicts": [c.to_dict() for c in self.conflicts],
            "untested_incoming": [o.to_dict() for o in self.untested_incoming],
        }


# --- 1. dry-run merge ---------------------------------------------------------

def dry_run_merge(into: str, source: str, cwd: str = ".") -> tuple[str, list[str]]:
    """Return (result tree id, conflicted paths) without touching the working folder."""
    out = git("-c", "merge.conflictStyle=merge", "merge-tree", "--write-tree",
              "--name-only", "-z", into, source, cwd=cwd, check=False)
    if not out:
        # merge-tree printed nothing: a real error (bad branch name, no merge base, old git).
        git("merge-tree", "--write-tree", into, source, cwd=cwd)   # raises GitError with stderr
    parts = out.split("\0")
    tree, paths = parts[0], []
    for p in parts[1:]:
        if p == "":
            break
        if p not in paths:
            paths.append(p)
    return tree, paths


# --- 2. conflict hunks --------------------------------------------------------

def split_lines(text: str) -> list[str]:
    """Split on "\n" only, like git does. str.splitlines() also splits on form feeds
    and Unicode separators, which would shift line numbers for git blame."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def parse_hunks(text: str) -> list[tuple[list[str], int, list[str], int]]:
    """Split a file with conflict markers into (a_lines, a_start, b_lines, b_start) hunks.

    Line numbers are 1-based and refer to each side's own version of the file:
    common lines count for both sides, a side's block counts only for that side.
    A diff3 base block (|||||||) is skipped.
    """
    hunks = []
    a_line = b_line = 0
    state = None
    a: list[str] = []
    b: list[str] = []
    a_start = b_start = 0
    for line in split_lines(text):
        if state is None and line.startswith("<<<<<<<"):
            state, a, b = "a", [], []
            a_start, b_start = a_line + 1, b_line + 1
        elif state in ("a", "base") and line.startswith("|||||||"):
            state = "base"
        elif state in ("a", "base") and line == "=======":
            state = "b"
        elif state == "b" and line.startswith(">>>>>>>"):
            hunks.append((a, a_start, b, b_start))
            state = None
        elif state == "a":
            a.append(line)
            a_line += 1
        elif state == "b":
            b.append(line)
            b_line += 1
        elif state == "base":
            pass
        else:
            a_line += 1
            b_line += 1
    return hunks


# --- 3. who wrote the lines ---------------------------------------------------

def blame(branch: str, path: str, start: int, count: int, cwd: str = ".") -> list[str]:
    """Commits that last changed lines start..start+count-1 of `path` on `branch`, oldest line first."""
    out = git("blame", "--porcelain", "-L", f"{start},+{count}", branch, "--", path, cwd=cwd)
    shas: list[str] = []
    for line in out.splitlines():
        m = _BLAME_HEADER.match(line)
        if m and m.group(1) not in shas:
            shas.append(m.group(1))
    return shas


def last_touch(branch: str, base: str, path: str, cwd: str = ".") -> list[str]:
    """For a side with no lines (deleted, or a whole-file conflict): the last commit since the
    merge base that touched the file on this branch."""
    out = git("log", "-1", "--format=%H", f"{base}..{branch}", "--", path, cwd=cwd).strip()
    return [out] if out else []


# --- 4. trust -----------------------------------------------------------------

class _Trust:
    """Track all branches once, then answer 'which change and how trusted' per commit."""

    def __init__(self, branches: list[str], prod: str | None, cwd: str, limit: int):
        self.prod, self.cwd = prod, cwd
        self.changes = track(branches, cwd=cwd, limit=limit)
        self._cache: dict[str, prov.Status] = {}

    def origin(self, sha: str) -> Origin:
        ch = change_of(sha, self.changes)
        if ch is None:   # older than --limit: judge the commit on its own
            title = git("log", "-1", "--format=%s", sha, cwd=self.cwd).strip()
            ch = Change(id=sha[:7], title=title, commits={"?": [sha]})
        if ch.id not in self._cache:
            self._cache[ch.id] = prov.status_of(ch, self.prod, cwd=self.cwd)
        st = self._cache[ch.id]
        title = git("log", "-1", "--format=%s", sha, cwd=self.cwd).strip()
        return Origin(sha, title, ch.id, st.level, st.reason, st.partial, ch.commits)


# --- 5. recommendation --------------------------------------------------------

def recommend(a: Side, b: Side) -> str:
    if a.trusted and not b.trusted:
        rec = (f"Keep Side A ({a.branch}): it is {a.level}. Side B ({b.branch}) is {b.level}; "
               f"test it before taking it.")
    elif b.trusted and not a.trusted:
        rec = (f"Keep Side B ({b.branch}): it is {b.level}. Side A ({a.branch}) is {a.level}; "
               f"test it before keeping it.")
    elif a.trusted and b.trusted:
        rec = ("Both sides passed CI separately, but never together. "
               "Combine them and run CI on the merge result before promoting it.")
    else:
        rec = (f"Neither side is tested (A: {a.level}, B: {b.level}). "
               "Run CI on both before choosing.")
    if a.partial or b.partial:
        which = " and ".join(s for s, side in (("A", a), ("B", b)) if side.partial)
        rec += (f" Warning: side {which} contains a backport whose code was modified during "
                "conflict resolution; the modified lines were never tested.")
    return rec


# --- the whole prediction -----------------------------------------------------

def predict(source: str, into: str, prod: str | None = None, cwd: str = ".",
            limit: int = 500) -> Report:
    """Dry-run merging `source` into `into` and explain every conflict."""
    # Paths from merge-tree are relative to the repo root; run everything from there.
    cwd = git("rev-parse", "--show-toplevel", cwd=cwd).strip()
    tree, paths = dry_run_merge(into, source, cwd=cwd)
    base = git("merge-base", into, source, cwd=cwd).strip()

    branches = [b for b in (prod, into, source) if b]
    trust = _Trust(list(dict.fromkeys(branches)), prod, cwd, limit)

    def side(branch: str, lines: list[str], start: int, path: str) -> Side:
        shas = blame(branch, path, start, len(lines), cwd) if lines else []
        if not shas:
            shas = last_touch(branch, base, path, cwd)
        return Side(branch, lines, start, [trust.origin(s) for s in shas])

    conflicts: list[Conflict] = []
    for path in paths:
        text = git("cat-file", "-p", f"{tree}:{path}", cwd=cwd, check=False)
        hunks = parse_hunks(text)
        if not hunks:   # modify/delete, rename or binary conflict: no markers to split
            hunks = [([], 0, [], 0)]
        for a_lines, a_start, b_lines, b_start in hunks:
            a = side(into, a_lines, a_start, path)
            b = side(source, b_lines, b_start, path)
            conflicts.append(Conflict(path, a, b, recommend(a, b)))

    # Everything that comes in with this merge and is not trusted, conflicting or not.
    incoming: list[Origin] = []
    seen: set[str] = set()
    for sha in git("rev-list", "--no-merges", "--reverse", f"{into}..{source}", cwd=cwd).split():
        o = trust.origin(sha)
        if o.change_id not in seen and o.level not in prov.TRUSTED:
            seen.add(o.change_id)
            incoming.append(o)

    return Report(source, into, conflicts, incoming)
