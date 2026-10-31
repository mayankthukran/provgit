"""Share testing provenance (git notes in refs/notes/provenance) with a remote.

    provgit sync          fetch the remote notes into refs/notes/provenance-remote and merge
                          them with `cat_sort_uniq`: never conflicts, every clone's records kept
    provgit sync --push   the same, then push; if someone pushed meanwhile, fetch + merge and
                          push once more

Pushing is opt-in, so a `provgit record` made while trying things out is never published by
accident. Safety: only configured remotes are accepted and `--end-of-options` is passed to
git, so a remote name can never be read as a git option; an interrupted notes merge is
aborted first; the notes merge uses a fixed identity (it only writes notes history).
"""

from __future__ import annotations

import os

from .gitutil import git, run

LOCAL = "refs/notes/provenance"
REMOTE = "refs/notes/provenance-remote"
SYNC_ID = ["-c", "user.name=provgit-sync", "-c", "user.email=provgit-sync@localhost"]


class SyncError(Exception):
    pass


def _check_remote(name: str, cwd: str) -> None:
    if name not in git("remote", cwd=cwd).split():
        raise SyncError(f"'{name}' is not a configured remote (see `git remote -v`)")


def _has_ref(ref: str, cwd: str) -> bool:
    return run("show-ref", "--verify", "--quiet", ref, cwd=cwd).returncode == 0


def count(ref: str, cwd: str) -> int:
    """Number of notes under a notes ref (0 if it does not exist)."""
    if not _has_ref(ref, cwd):
        return 0
    return len(git("notes", f"--ref={ref}", "list", cwd=cwd).splitlines())


_MERGE_STATE = ("NOTES_MERGE_PARTIAL", "NOTES_MERGE_REF")


def _clear_stale_merge(cwd: str) -> bool:
    """Abort an interrupted notes merge. True if there was one; SyncError if it can't be cleared."""
    gitdir = git("rev-parse", "--absolute-git-dir", cwd=cwd).strip()
    left = lambda: [f for f in _MERGE_STATE if os.path.exists(os.path.join(gitdir, f))]
    if not left():
        return False
    run("notes", f"--ref={LOCAL}", "merge", "--abort", cwd=cwd)   # exit code is unreliable; re-check
    still = left()
    if still:
        raise SyncError(f"an interrupted notes merge could not be aborted ({', '.join(still)} in "
                        f"{gitdir}); run `git notes --ref={LOCAL} merge --abort` and remove them by hand")
    return True


def fetch(remote: str, cwd: str) -> int:
    """Fetch the remote's notes into REMOTE. Returns the number of notes, or -1 if it has none."""
    p = run("fetch", "--quiet", "--end-of-options", remote, f"+{LOCAL}:{REMOTE}", cwd=cwd)
    if p.returncode != 0:
        if "couldn't find remote ref" in p.stderr:
            return -1
        raise SyncError(f"fetch from '{remote}' failed: {p.stderr.strip()}")
    return count(REMOTE, cwd)


def merge(cwd: str) -> None:
    """Merge REMOTE into LOCAL (or adopt it when there are no local notes), then drop REMOTE."""
    if not _has_ref(LOCAL, cwd):
        git("update-ref", LOCAL, REMOTE, cwd=cwd)
    else:
        git(*SYNC_ID, "notes", f"--ref={LOCAL}", "merge", "-q", "-s", "cat_sort_uniq", REMOTE, cwd=cwd)
    git("update-ref", "-d", REMOTE, cwd=cwd)


def _push_once(remote: str, cwd: str) -> bool:
    """True when pushed (or already up to date), False when rejected because the remote moved."""
    p = run("push", "--porcelain", "--end-of-options", remote, f"{LOCAL}:{LOCAL}", cwd=cwd)
    line = next((l for l in p.stdout.splitlines() if f"{LOCAL}:{LOCAL}" in l), "")
    if line.startswith("!"):
        return False
    if p.returncode == 0 and line:
        return True
    raise SyncError(f"push to '{remote}' failed: {(p.stderr or p.stdout).strip()}")


def sync(remote: str = "origin", push: bool = False, cwd: str = ".") -> dict:
    _check_remote(remote, cwd)
    stale = _clear_stale_merge(cwd)
    before = count(LOCAL, cwd)
    fetched = fetch(remote, cwd)
    if fetched >= 0:
        merge(cwd)
    result = {
        "remote": remote,
        "stale_merge_cleared": stale,
        "remote_had_notes": fetched >= 0,
        "fetched": max(fetched, 0),
        "local_before": before,
        "local_after": count(LOCAL, cwd),
        "pushed": None,
    }
    if not push:
        return result
    if result["local_after"] == 0:
        result["pushed"] = "nothing to push"
    elif _push_once(remote, cwd):
        result["pushed"] = "ok"
    else:
        if fetch(remote, cwd) >= 0:
            merge(cwd)
        if not _push_once(remote, cwd):
            raise SyncError("push rejected twice: someone else is syncing right now; try again")
        result["pushed"] = "ok (after retry)"
        result["local_after"] = count(LOCAL, cwd)
    return result
