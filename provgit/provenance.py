"""Testing provenance: stored in git notes, inherited across copies of the same change.

Storage: JSON in the notes ref `refs/notes/provenance`, attached to a commit without
changing its SHA. Notes are standard Git but are not fetched by a plain clone, so a
repo without the ref reports UNKNOWN instead of pretending anything was tested.

Share with:  git push origin refs/notes/provenance
             git fetch origin refs/notes/provenance:refs/notes/provenance
"""

from __future__ import annotations

import json
from dataclasses import dataclass

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


def record(sha: str, ci: str, cwd: str = ".", url: str | None = None, env: str | None = None) -> dict:
    """Attach a CI result to a commit (overwrites an earlier record)."""
    if ci not in {"passed", "failed"}:
        raise ValueError("ci must be 'passed' or 'failed'")
    full = git("rev-parse", sha, cwd=cwd).strip()
    data = {"ci": ci}
    if url:
        data["url"] = url
    if env:
        data["env"] = env
    git("notes", f"--ref={NOTES_REF}", "add", "-f", "-m", json.dumps(data), full, cwd=cwd)
    return data


def read(sha: str, cwd: str = ".") -> dict | None:
    out = git("notes", f"--ref={NOTES_REF}", "show", sha, cwd=cwd, check=False).strip()
    if not out:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


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
        reason = "no provenance metadata in this clone (fetch refs/notes/provenance)"
        if in_prod:
            reason += f"; change is in {prod_branch}"
        return Status(level, reason, partial=partial)

    records = [(s, read(s, cwd)) for s in change.all_shas()]
    records = [(s, r) for s, r in records if r]
    passed = next((s for s, r in records if r.get("ci") == "passed"), None)
    failed = next((s for s, r in records if r.get("ci") == "failed"), None)

    if failed and not passed:
        return Status(CI_FAILED, f"CI failed on {failed[:7]}", failed, partial)
    if passed and in_prod:
        return Status(PROD_VERIFIED, f"CI passed on {passed[:7]} and change is in {prod_branch}{note}", passed, partial)
    if passed:
        return Status(CI_PASSED, f"CI passed on {passed[:7]}{note}", passed, partial)
    if in_prod:
        return Status(IN_PROD_NO_CI, f"in {prod_branch}, but no CI record", None, partial)
    return Status(UNTESTED, "no CI record for any copy of this change", None, partial)
