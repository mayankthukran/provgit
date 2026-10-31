"""Thin, tested wrappers around the git command line."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass


class GitError(RuntimeError):
    pass


def run(*args: str, cwd: str = ".", input: str | None = None) -> subprocess.CompletedProcess:
    """Run a git command and return the CompletedProcess (stdout/stderr as text).

    Output is decoded as UTF-8 with surrogateescape, so files in other encodings
    (Latin-1, binary) never crash provgit and round-trip byte for byte.
    """
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        input=input,
        capture_output=True,
        encoding="utf-8",
        errors="surrogateescape",
    )


def git(*args: str, cwd: str = ".", input: str | None = None, check: bool = True) -> str:
    """Run a git command and return stdout as text. Raises GitError on failure if check."""
    proc = run(*args, cwd=cwd, input=input)
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def patch_id(sha: str, cwd: str = ".") -> str | None:
    """Stable patch-id of a commit: same diff gives the same id on any branch."""
    diff = git("show", sha, cwd=cwd)
    out = git("patch-id", "--stable", cwd=cwd, input=diff).strip()
    return out.split()[0] if out else None


def commit_message(sha: str, cwd: str = ".") -> str:
    return git("log", "-1", "--format=%B", sha, cwd=cwd)


def commits_on(branch: str, cwd: str = ".", limit: int | None = None) -> list[str]:
    args = ["rev-list", "--no-merges", branch]
    if limit:
        args.insert(1, f"--max-count={limit}")
    return git(*args, cwd=cwd).split()


# --- Backport references in commit messages ---------------------------------
# CPython (cherry-pick -x / miss-islington):  "(cherry picked from commit <sha>)"
# Django:                                      "Backport of <sha> from main."
_CHERRY = re.compile(r"\(cherry picked from commit ([0-9a-f]{7,40})\)")
_DJANGO = re.compile(r"Backport of ([0-9a-f]{7,40}) from ([\w./-]+?)\.?\s*$", re.MULTILINE)
_GH_PR = re.compile(r"\((?:GH-|#)(\d+)\)")
_DJANGO_TICKET = re.compile(r"(?:Fixed|Refs) #(\d+)")


@dataclass
class BackportRef:
    source_sha: str
    style: str               # "cherry-pick" or "django"
    from_branch: str | None = None


def parse_backport_refs(message: str) -> list[BackportRef]:
    """Find every 'this commit was copied from X' reference in a commit message."""
    refs = [BackportRef(m.group(1), "cherry-pick") for m in _CHERRY.finditer(message)]
    refs += [BackportRef(m.group(1), "django", m.group(2)) for m in _DJANGO.finditer(message)]
    return refs


def parse_change_refs(message: str) -> dict[str, list[str]]:
    """PR / ticket numbers mentioned in a commit title, used as a weaker match signal."""
    title = message.splitlines()[0] if message else ""
    return {
        "pr": _GH_PR.findall(title),
        "ticket": _DJANGO_TICKET.findall(title),
    }
