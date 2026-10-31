"""Merge with testing provenance: pick a side per conflict, write a standard Git merge commit.

Flow:
  1. prepare()  safety checks, pin both branches to SHAs, run predict().
  2. choose()   pick a side per conflict (--take, --prefer-tested, or the CLI asks).
  3. execute()  real `git merge --no-ff --no-commit`, resolve each conflict with the chosen
                side, verify every block matches what predict() showed, commit with
                `Provgit-*` trailers. Any failure runs `git merge --abort`.

Safety rules:
  - refuses to run with uncommitted tracked changes, a merge in progress, or HEAD not on `into`;
  - the merge uses the pinned SHAs; if either branch moved since the report, it stops;
  - a conflict block that does not match the report, or is ambiguous (e.g. a "=======" line
    inside a side), stops the merge: provgit never writes a side the user did not see;
  - never writes through a symlink, never resolves symlink or submodule conflicts itself;
  - trailer values are sanitised, so a file name cannot forge a trailer.

Trailers are an audit record, not proof: provgit never reads them to decide trust.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from . import __version__
from . import provenance as prov
from .gitutil import GitError, git, run
from .predictor import Conflict, Report, Side, predict, split_lines


class MergeStopped(Exception):
    """provgit refused or stopped the merge. The repository is as it was before."""


A, B = "A", "B"


@dataclass
class Plan:
    source: str
    into: str
    source_sha: str
    into_sha: str
    root: str
    report: Report
    choices: list[str] = field(default_factory=list)       # "A" / "B", one per report.conflicts


@dataclass
class Result:
    status: str                  # committed | up-to-date | manual
    sha: str | None = None
    message: str = ""


# --- 1. prepare ---------------------------------------------------------------

def _ok(*args: str, cwd: str) -> bool:
    return run(*args, cwd=cwd).returncode == 0


def _sha(ref: str, cwd: str) -> str:
    proc = run("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", cwd=cwd)
    if proc.returncode != 0:
        raise MergeStopped(f"'{ref}' is not a commit or branch")
    return proc.stdout.strip()


def _check_worktree(into: str, cwd: str) -> None:
    if not _ok("show-ref", "--verify", "--quiet", f"refs/heads/{into}", cwd=cwd):
        raise MergeStopped(f"'{into}' is not a local branch")
    current = run("symbolic-ref", "--quiet", "--short", "HEAD", cwd=cwd).stdout.strip()
    if current != into:
        raise MergeStopped(f"you are on '{current or 'a detached HEAD'}'; run `git checkout {into}` first")
    if _ok("rev-parse", "-q", "--verify", "MERGE_HEAD", cwd=cwd):
        raise MergeStopped("a merge is already in progress; finish it or run `git merge --abort`")
    if git("status", "--porcelain", "--untracked-files=no", cwd=cwd).strip():
        raise MergeStopped("you have uncommitted changes; commit or stash them first")


def prepare(source: str, into: str, prod: str | None = None, cwd: str = ".",
            limit: int = 500) -> Plan:
    root = git("rev-parse", "--show-toplevel", cwd=cwd).strip()
    _check_worktree(into, root)
    into_sha, source_sha = _sha(into, root), _sha(source, root)
    report = predict(source, into, prod=prod, cwd=root, limit=limit)
    return Plan(source, into, source_sha, into_sha, root, report)


# --- 2. choose ----------------------------------------------------------------

def _auto_ok(side: Side) -> bool:
    """A side provgit may pick on its own: tested, and no backport edited during a conflict."""
    return side.trusted and not side.partial


def choose(c: Conflict, prefer_tested: bool, take: str | None) -> tuple[str | None, str]:
    """Return (side or None, reason). None means provgit will not decide for the user."""
    if take in (A, B):
        return take, f"--take {take}"
    if not prefer_tested:
        return None, "no strategy given"
    a_ok, b_ok = _auto_ok(c.a), _auto_ok(c.b)
    if a_ok and not b_ok:
        return A, f"Side A is {c.a.level}, Side B is {c.b.level}"
    if b_ok and not a_ok:
        return B, f"Side B is {c.b.level}, Side A is {c.a.level}"
    if c.a.partial or c.b.partial:
        return None, "a side contains a backport modified during conflict resolution (lines never tested)"
    if a_ok and b_ok:
        return None, "both sides passed CI separately, but never together"
    return None, f"neither side is tested (A: {c.a.level}, B: {c.b.level})"


# --- 3. execute ---------------------------------------------------------------

def _keep_lines(text: str) -> list[str]:
    """Split on "\\n" only, keeping line endings, so the file round-trips byte for byte."""
    parts = text.split("\n")
    lines = [p + "\n" for p in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


def _bare(line: str) -> str:
    line = line[:-1] if line.endswith("\n") else line
    return line[:-1] if line.endswith("\r") else line


def _norm(lines: list[str]) -> list[str]:
    """The same view of the lines that predict() had (CR/LF normalised by git())."""
    return split_lines("".join(lines).replace("\r\n", "\n").replace("\r", "\n"))


def resolve_text(text: str, choices: list[str],
                 expected: list[tuple[list[str], list[str]]]) -> str:
    """Replace conflict block i with side choices[i].

    Every block must match expected[i] = (side A lines, side B lines) from the report,
    and must be unambiguous. Otherwise raise MergeStopped.
    """
    lines = _keep_lines(text)
    out: list[str] = []
    i = k = 0
    while k < len(lines):
        s = _bare(lines[k])
        if s.startswith("<<<<<<<"):
            end = next((j for j in range(k + 1, len(lines)) if _bare(lines[j]).startswith(">>>>>>>")), None)
            if end is None:
                raise MergeStopped("unterminated conflict block")
            inner = [_bare(lines[j]) for j in range(k + 1, end)]
            seps = [k + 1 + n for n, t in enumerate(inner) if t == "======="]
            if len(seps) != 1 or any(t.startswith(("<<<<<<<", "|||||||")) for t in inner):
                raise MergeStopped("ambiguous conflict block (a side contains a line that looks like "
                                   "a conflict marker, e.g. an RST '=======' heading)")
            a, b = lines[k + 1:seps[0]], lines[seps[0] + 1:end]
            if i >= len(choices) or (_norm(a), _norm(b)) != (expected[i][0], expected[i][1]):
                raise MergeStopped("the conflict differs from the report")
            out += a if choices[i] == A else b
            i, k = i + 1, end + 1
        elif s.startswith(">>>>>>>"):
            raise MergeStopped("ambiguous conflict block (stray '>>>>>>>' line)")
        else:
            out.append(lines[k])
            k += 1
    if i != len(choices):
        raise MergeStopped("the conflict differs from the report")
    return "".join(out)


def _unmerged(root: str) -> dict[str, dict[int, str]]:
    """path -> {stage: mode} for every conflicted path (from the index, not from markers)."""
    out: dict[str, dict[int, str]] = {}
    for entry in git("ls-files", "-u", "-z", cwd=root).split("\0"):
        if not entry:
            continue
        info, _, path = entry.partition("\t")
        mode, _sha_, stage = info.split()
        out.setdefault(path, {})[int(stage)] = mode
    return out


def _safe_path(root: str, path: str) -> str:
    """Absolute path inside the repo; refuse symlinks anywhere on the way."""
    real_root = os.path.realpath(root)
    full = os.path.normpath(os.path.join(real_root, path))
    if not full.startswith(real_root + os.sep) or os.path.realpath(full) != full:
        raise MergeStopped(f"{path!r} is or goes through a symlink; resolve it by hand")
    return full


def _resolve_path(root: str, path: str, stages: dict[int, str], conflicts: list[Conflict],
                  choices: list[str], rerere: bool) -> str:
    """Resolve one conflicted path. Returns how it was resolved (for the trailers)."""
    if any(m in ("120000", "160000") for m in stages.values()):
        raise MergeStopped(f"{path!r} is a symlink or submodule conflict; resolve it by hand")
    full = _safe_path(root, path)
    text_conflict = 2 in stages and 3 in stages and any(c.a.lines or c.b.lines for c in conflicts)

    if text_conflict:
        with open(full, "rb") as fh:
            text = fh.read().decode("utf-8", "surrogateescape")
        if "<<<<<<<" not in text:
            if rerere:
                git("add", "--", path, cwd=root)
                return "rerere"
            raise MergeStopped(f"{path!r}: the conflict differs from the report")
        expected = [(c.a.lines, c.b.lines) for c in conflicts]
        new = resolve_text(text, choices, expected)
        with open(full, "wb") as fh:
            fh.write(new.encode("utf-8", "surrogateescape"))
        git("add", "--", path, cwd=root)
        return "text"

    # Whole-file conflict (modify/delete, binary, add/add without markers): one choice.
    if len(conflicts) != 1:
        raise MergeStopped(f"{path!r}: the conflict differs from the report")
    stage = 2 if choices[0] == A else 3
    if stage in stages:
        git("checkout", "--ours" if stage == 2 else "--theirs", "--", path, cwd=root)
        git("add", "--", path, cwd=root)
    else:
        git("rm", "-q", "--", path, cwd=root)
    return "whole-file"


_BIDI = set("‪‫‬‭‮⁦⁧⁨⁩")
_ZERO_WIDTH = set("​‌‍⁠﻿")


def clean(value: str, keep_tabs: bool = False) -> str:
    """One line of safe text: a value cannot start a new trailer or control the terminal.

    Replaces C0/C1 control characters (incl. ESC and the 8-bit CSI 0x9b) and bidi overrides
    ("Trojan Source") with '?', and drops zero-width characters.
    """
    out = []
    for ch in value:
        o = ord(ch)
        if ch == "\t" and keep_tabs:
            out.append(ch)
        elif o < 0x20 or o == 0x7f or 0x80 <= o <= 0x9f or ch in _BIDI:
            out.append("?")
        elif ch in _ZERO_WIDTH:
            continue
        else:
            out.append(ch)
    return "".join(out)


def _quote(path: str) -> str:
    return '"' + clean(path).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _side_desc(label: str, side: Side) -> str:
    """e.g. A(UAT) 536da8c PROD_VERIFIED"""
    shas = ",".join(o.sha[:7] for o in side.origins) or "unknown"
    return f"{label}({clean(side.branch)}) {shas} {side.level}"


def message(plan: Plan, how: dict[str, str]) -> str:
    rep, s7, i7 = plan.report, plan.source_sha[:7], plan.into_sha[:7]
    n = len(rep.conflicts)
    lines = [f"Merge {clean(plan.source)} ({s7}) into {clean(plan.into)}", ""]
    if n:
        lines.append(f"provgit resolved {n} conflict{'s' if n > 1 else ''} using testing provenance.")
    else:
        lines.append("Merged cleanly; provgit recorded the testing provenance of incoming changes.")
    lines += ["The merge result itself is untested: run CI before promoting.", ""]

    for c, pick in zip(rep.conflicts, plan.choices):
        kept, dropped = (c.a, c.b) if pick == A else (c.b, c.a)
        other = B if pick == A else A
        where = f"{_quote(c.path)}:{kept.start}" if (c.a.lines or c.b.lines) else f"{_quote(c.path)}:whole-file"
        if how.get(c.path) == "rerere":
            lines.append(f"Provgit-Resolved: {where} rerere (previous resolution reused; not checked by provgit)")
            continue
        lines.append(f"Provgit-Resolved: {where} kept={_side_desc(pick, kept)}")
        lines.append(f"Provgit-Rejected: {where} {_side_desc(other, dropped)}")

    untested = " ".join(o.sha[:7] for o in rep.untested_incoming) or "none"
    lines += [
        f"Provgit-Untested-Incoming: {untested}",
        "Provgit-Tested-Together: no",
        f"Provgit-Version: {__version__}",
    ]
    return "\n".join(lines) + "\n"


def _verify_pins(plan: Plan) -> None:
    r = plan.root
    _check_worktree(plan.into, r)
    if _sha("HEAD", r) != plan.into_sha or _sha(plan.into, r) != plan.into_sha:
        raise MergeStopped(f"'{plan.into}' moved since the report; run provgit again")
    if _sha(plan.source, r) != plan.source_sha:
        raise MergeStopped(f"'{plan.source}' moved since the report; run provgit again")


def _start_merge(plan: Plan, rerere: bool) -> None:
    if rerere:
        git("config", "rerere.enabled", "true", cwd=plan.root)
    proc = run("-c", "merge.conflictStyle=merge", "-c", f"rerere.enabled={str(rerere).lower()}",
               "merge", "--no-ff", "--no-commit", plan.source_sha, cwd=plan.root)
    if not _ok("rev-parse", "-q", "--verify", "MERGE_HEAD", cwd=plan.root):
        # Not a conflict: git refused (untracked files in the way, lock file, ...).
        raise GitError(f"git merge failed: {(proc.stderr or proc.stdout).strip()}")


def start_manual(plan: Plan) -> Result:
    """Start the merge and leave the conflicts in the working folder for the user."""
    _verify_pins(plan)
    _start_merge(plan, rerere=False)
    return Result("manual")


def execute(plan: Plan, rerere: bool = False) -> Result:
    """Run the real merge with the chosen sides and commit it. Aborts cleanly on any error."""
    rep = plan.report
    if len(plan.choices) != len(rep.conflicts) or any(c not in (A, B) for c in plan.choices):
        raise MergeStopped("a side must be chosen for every conflict")
    _verify_pins(plan)
    if _ok("merge-base", "--is-ancestor", plan.source_sha, plan.into_sha, cwd=plan.root):
        return Result("up-to-date")

    _start_merge(plan, rerere)
    try:
        unmerged = _unmerged(plan.root)
        reported = {c.path for c in rep.conflicts}
        if set(unmerged) != reported:
            raise MergeStopped("the conflicted files differ from the report")

        how: dict[str, str] = {}
        for path, stages in unmerged.items():
            idx = [i for i, c in enumerate(rep.conflicts) if c.path == path]
            how[path] = _resolve_path(plan.root, path, stages, [rep.conflicts[i] for i in idx],
                                      [plan.choices[i] for i in idx], rerere)

        if _unmerged(plan.root):
            raise MergeStopped("some files are still unresolved")

        msg = message(plan, how)
        git("commit", "-q", "--cleanup=whitespace", "-F", "-", cwd=plan.root, input=msg)
        return Result("committed", _sha("HEAD", plan.root), msg)
    except BaseException:
        # Includes Ctrl-C: never leave a half-finished merge behind.
        run("merge", "--abort", cwd=plan.root)
        raise
