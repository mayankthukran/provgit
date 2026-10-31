"""Testing provenance: stored in git notes, inherited across copies of the same change.

Storage: JSON in the notes ref `refs/notes/provenance`, attached to a commit without
changing its SHA. Notes are standard Git but are not fetched by a plain clone, so a
repo without the ref reports UNKNOWN instead of pretending anything was tested.

A note holds one JSON record per line, so records from several clones can be merged
(`provgit sync`, git notes merge -s cat_sort_uniq). For one commit the newest record counts:
timestamps are UTC with microseconds, `failed` wins an exact tie, and records dated more than
a day in the future are ignored. Notes from other clones are untrusted input: notes larger
than MAX_NOTE are skipped without being read, and invalid lines are ignored.

Trust model: the `env` field is informational only. Whether a change is in production comes
from branch ancestry (`git merge-base --is-ancestor`), never from a note.

Share with:  provgit sync [--push]
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .gitutil import git
from .matcher import Change, is_partial

NOTES_REF = "provenance"

PROD_VERIFIED = "PROD_VERIFIED"      # in the production branch and CI passed
CI_PASSED = "CI_PASSED"              # CI passed, not in production yet
IN_PROD_NO_CI = "IN_PROD_NO_CI"      # in production, but no CI record
CI_FAILED = "CI_FAILED"
UNTESTED = "UNTESTED"                # metadata exists, but nothing for this change
UNKNOWN = "UNKNOWN"                  # no provenance metadata in this clone at all

TRUSTED = {PROD_VERIFIED, CI_PASSED}


def has_metadata(cwd: str = ".") -> bool:
    out = git("show-ref", "--verify", f"refs/notes/{NOTES_REF}", cwd=cwd, check=False)
    return bool(out.strip())


MAX_NOTE = 64_000
FUTURE_SLACK = timedelta(days=1)
_OLDEST = datetime.min.replace(tzinfo=timezone.utc)
_KEYS = ("ci", "url", "env")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _when(rec: dict) -> datetime:
    """Parsed UTC time of a record; a missing or invalid time counts as the oldest."""
    try:
        t = datetime.fromisoformat(str(rec.get("at", "")).replace("Z", "+00:00"))
    except ValueError:
        return _OLDEST
    return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _valid(r) -> bool:
    return isinstance(r, dict) and r.get("ci") in ("passed", "failed")


def _note_size(sha: str, cwd: str) -> int | None:
    """Size of the note blob on `sha` without reading it; None when there is no note."""
    blob = git("notes", f"--ref={NOTES_REF}", "list", sha, cwd=cwd, check=False).strip()
    if not blob:
        return None
    return int(git("cat-file", "-s", blob, cwd=cwd).strip())


def read_all(sha: str, cwd: str = ".") -> list[dict]:
    """Every valid CI record on a commit (a synced note holds one JSON object per line)."""
    size = _note_size(sha, cwd)
    if size is None or size > MAX_NOTE:
        return []
    text = git("notes", f"--ref={NOTES_REF}", "show", sha, cwd=cwd, check=False)
    recs = []
    for line in text.splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if _valid(r):
            recs.append(r)
    if not recs:                      # a note written by hand: one JSON object over many lines
        try:
            r = json.loads(text)
            recs = [r] if _valid(r) else []
        except json.JSONDecodeError:
            pass
    limit = datetime.now(timezone.utc) + FUTURE_SLACK
    return [r for r in recs if _when(r) <= limit]


def read(sha: str, cwd: str = ".") -> dict | None:
    """The record that counts for a commit: the newest; on an exact tie, 'failed' wins."""
    recs = read_all(sha, cwd)
    if not recs:
        return None
    return max(recs, key=lambda r: (_when(r), r["ci"] == "failed"))


def record(sha: str, ci: str, cwd: str = ".", url: str | None = None, env: str | None = None) -> dict:
    """Add a CI result to a commit, keeping earlier records (including synced ones).

    If the newest record already says the same thing, nothing is added (re-imports stay clean).
    """
    if ci not in {"passed", "failed"}:
        raise ValueError("ci must be 'passed' or 'failed'")
    full = git("rev-parse", "--verify", f"{sha}^{{commit}}", cwd=cwd).strip()
    data = {"ci": ci, "at": _now()}
    if url:
        data["url"] = url
    if env:
        data["env"] = env
    newest = read(full, cwd)
    if newest and all(newest.get(k) == data.get(k) for k in _KEYS):
        return newest
    lines = [json.dumps(r, sort_keys=True) for r in read_all(full, cwd)]
    lines.append(json.dumps(data, sort_keys=True))
    git("notes", f"--ref={NOTES_REF}", "add", "-f", "-F", "-", full, cwd=cwd,
        input="\n".join(lines) + "\n")
    return data


def import_ci(path: str, cwd: str = ".") -> int:
    """Load CI results from a JSON file: {"<sha or ref>": {"ci": "passed", "url": "..."}}."""
    with open(path) as fh:
        data = json.load(fh)
    for ref, rec in data.items():
        record(ref, rec["ci"], cwd=cwd, url=rec.get("url"), env=rec.get("env"))
    return len(data)


def _is_ancestor(sha: str, branch: str, cwd: str) -> bool:
    """True when `sha` is part of `branch` (git merge-base --is-ancestor)."""
    import subprocess
    return subprocess.run(
        ["git", "merge-base", "--is-ancestor", sha, branch], cwd=cwd, capture_output=True
    ).returncode == 0


@dataclass
class Status:
    level: str
    reason: str
    evidence_sha: str | None = None
    partial: bool = False

    def to_dict(self) -> dict:
        return vars(self)


def status_of(change: Change, prod_branch: str | None, cwd: str = ".") -> Status:
    """Trust level of a whole change, inherited from every copy of it."""
    partial = is_partial(change)
    note = " (code was modified during backport; modified lines were not tested)" if partial else ""

    in_prod = bool(prod_branch) and any(_is_ancestor(s, prod_branch, cwd) for s in change.all_shas())

    if not has_metadata(cwd):
        level = UNKNOWN
        reason = "no provenance metadata in this clone (run `provgit sync`)"
        if in_prod:
            reason += f"; change is in {prod_branch}"
        return Status(level, reason, partial=partial)

    every = [r for s in change.all_shas() for r in read_all(s, cwd)]
    n_pass = sum(r["ci"] == "passed" for r in every)
    n_fail = len(every) - n_pass
    if n_pass and n_fail:
        note += f" ⚠️ CI results disagree ({n_pass} passed, {n_fail} failed); newest per commit is used"

    records = [(s, read(s, cwd)) for s in change.all_shas()]
    records = [(s, r) for s, r in records if r]
    passed = next((s for s, r in records if r.get("ci") == "passed"), None)
    failed = next((s for s, r in records if r.get("ci") == "failed"), None)

    if failed and not passed:
        return Status(CI_FAILED, f"CI failed on {failed[:7]}{note}", failed, partial)
    if passed and in_prod:
        return Status(PROD_VERIFIED, f"CI passed on {passed[:7]} and change is in {prod_branch}{note}", passed, partial)
    if passed:
        return Status(CI_PASSED, f"CI passed on {passed[:7]}{note}", passed, partial)
    if in_prod:
        return Status(IN_PROD_NO_CI, f"in {prod_branch}, but no CI record", None, partial)
    return Status(UNTESTED, "no CI record for any copy of this change", None, partial)
