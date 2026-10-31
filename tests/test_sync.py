"""provgit sync and multi-record notes: two clones and a bare remote, plus the record rules."""

import json
import subprocess
from pathlib import Path

import pytest

from provgit import provenance as prov
from provgit import sync as syncmod
from provgit.cli import main
from provgit.gitutil import git, run
from provgit.matcher import change_of, track

ROOT = Path(__file__).resolve().parents[1]
ID = ["-c", "user.name=t", "-c", "user.email=t@example.com"]


# --- fixtures -----------------------------------------------------------------

def _clone(remote: str, path: Path, identity=True) -> str:
    repo = str(path)
    git("clone", "-q", remote, repo)
    for b in ("UAT", "DEV"):
        git("branch", b, f"origin/{b}", cwd=repo)
    if identity:
        git("config", "user.name", "t", cwd=repo)
        git("config", "user.email", "t@example.com", cwd=repo)
    return repo


@pytest.fixture
def world(tmp_path):
    demo = tmp_path / "demo"
    subprocess.run(["bash", str(ROOT / "scripts" / "make_demo_repo.sh"), str(demo)],
                   check=True, capture_output=True)
    remote = str(tmp_path / "remote.git")
    git("clone", "-q", "--bare", str(demo), remote)
    hotfix = json.loads((demo / "ci_results.json").read_text())
    c1 = _clone(remote, tmp_path / "c1")
    c2 = _clone(remote, tmp_path / "c2")
    return {"tmp": tmp_path, "remote": remote, "c1": c1, "c2": c2,
            "ci": str(demo / "ci_results.json"), "hotfix": next(iter(hotfix))}


def _level(repo, sha):
    ch = change_of(sha, track(["PROD", "UAT", "DEV"], cwd=repo))
    return prov.status_of(ch, "PROD", cwd=repo)


def _write_note(repo, sha, lines):
    git(*ID, "notes", "--ref=provenance", "add", "-f", "-F", "-", sha, cwd=repo,
        input="\n".join(lines) + "\n")


def _rec(ci, at=None, **extra):
    r = {"ci": ci, **extra}
    if at is not None:
        r["at"] = at
    return json.dumps(r, sort_keys=True)


def _remote_has_notes(world):
    return run("show-ref", "--verify", "--quiet", "refs/notes/provenance",
               cwd=world["remote"]).returncode == 0


def _sync(repo, *extra):
    return main(["sync", *extra, "-C", repo])


# --- sync between clones ------------------------------------------------------

def test_clone_unknown_until_sync(world, capsys):
    c1, c2, hotfix = world["c1"], world["c2"], world["hotfix"]
    main(["import-ci", world["ci"], "-C", c1])
    assert _sync(c1, "--push") == 0
    assert "pushed" in capsys.readouterr().out
    assert _level(c2, hotfix).level == prov.UNKNOWN
    assert _sync(c2) == 0
    assert "fetched 1 note(s)" in capsys.readouterr().out
    assert _level(c2, hotfix).level == prov.PROD_VERIFIED


def test_sync_without_push_does_not_publish(world, capsys):
    prov.record(world["hotfix"], "passed", cwd=world["c1"])
    assert _sync(world["c1"]) == 0
    assert "not pushed" in capsys.readouterr().out
    assert not _remote_has_notes(world)


def test_both_clones_records_kept(world):
    c1, c2 = world["c1"], world["c2"]
    x = git("rev-parse", "DEV", cwd=c1).strip()
    y = world["hotfix"]
    prov.record(x, "failed", cwd=c1)
    prov.record(y, "passed", cwd=c2)
    assert _sync(c1, "--push") == 0
    assert _sync(c2, "--push") == 0
    assert _sync(c1) == 0
    for repo in (c1, c2):
        assert prov.read(x, cwd=repo)["ci"] == "failed"
        assert prov.read(y, cwd=repo)["ci"] == "passed"


def test_newest_wins_across_clones_and_disagreement_shown(world):
    c1, c2, sha = world["c1"], world["c2"], world["hotfix"]
    _write_note(c1, sha, [_rec("failed", "2026-10-01T10:00:00.000000+00:00")])
    _write_note(c2, sha, [_rec("passed", "2026-10-01T11:00:00.000000+00:00")])
    _sync(c1, "--push")
    _sync(c2, "--push")
    _sync(c1)
    for repo in (c1, c2):
        assert len(prov.read_all(sha, cwd=repo)) == 2
        st = _level(repo, sha)
        assert st.level == prov.PROD_VERIFIED
        assert "CI results disagree (1 passed, 1 failed)" in st.reason


def test_identical_records_deduplicated_by_merge(world):
    c1, c2, sha = world["c1"], world["c2"], world["hotfix"]
    line = _rec("passed", "2026-10-01T10:00:00.000000+00:00", url="https://ci/1")
    _write_note(c1, sha, [line])
    _write_note(c2, sha, [line])
    _sync(c1, "--push")
    _sync(c2)
    assert len(prov.read_all(sha, cwd=world["c2"])) == 1


def test_record_keeps_synced_lines(world):
    c1, c2, sha = world["c1"], world["c2"], world["hotfix"]
    prov.record(sha, "passed", cwd=c1, url="https://ci/from-c1")
    _sync(c1, "--push")
    _sync(c2)
    prov.record(sha, "failed", cwd=c2, url="https://ci/from-c2")
    urls = {r.get("url") for r in prov.read_all(sha, cwd=c2)}
    assert urls == {"https://ci/from-c1", "https://ci/from-c2"}


def test_remote_without_notes(world, capsys):
    assert _sync(world["c1"]) == 0
    assert "has no provenance notes yet" in capsys.readouterr().out


def test_push_with_no_local_notes(world, capsys):
    assert _sync(world["c1"], "--push") == 0
    assert "nothing to push" in capsys.readouterr().out


def test_push_retry_after_race(world, monkeypatch):
    prov.record(world["hotfix"], "passed", cwd=world["c1"])
    real = syncmod._push_once
    calls = []

    def flaky(remote, cwd):
        calls.append(1)
        return False if len(calls) == 1 else real(remote, cwd)
    monkeypatch.setattr(syncmod, "_push_once", flaky)
    r = syncmod.sync("origin", push=True, cwd=world["c1"])
    assert r["pushed"] == "ok (after retry)" and len(calls) == 2
    assert _remote_has_notes(world)


def test_push_rejected_twice_is_a_clear_error(world, monkeypatch, capsys):
    prov.record(world["hotfix"], "passed", cwd=world["c1"])
    monkeypatch.setattr(syncmod, "_push_once", lambda remote, cwd: False)
    assert _sync(world["c1"], "--push") == 1
    assert "rejected twice" in capsys.readouterr().err


def test_stale_notes_merge_cleared(world, capsys):
    c1 = world["c1"]
    gitdir = git("rev-parse", "--absolute-git-dir", cwd=c1).strip()
    Path(gitdir, "NOTES_MERGE_REF").write_text("ref: refs/notes/provenance\n")   # what git leaves
    assert _sync(c1) == 0
    assert "interrupted notes merge was aborted" in capsys.readouterr().out
    assert not Path(gitdir, "NOTES_MERGE_REF").exists()


def test_broken_merge_state_is_a_clear_error(world, capsys):
    c1 = world["c1"]
    gitdir = git("rev-parse", "--absolute-git-dir", cwd=c1).strip()
    Path(gitdir, "NOTES_MERGE_REF").write_text("garbage\n")
    assert _sync(c1) == 1
    assert "could not be aborted" in capsys.readouterr().err


def test_bad_remote_name_refused(world, capsys):
    assert main(["sync", "--remote=--upload-pack=touch pwned", "-C", world["c1"]]) == 1
    assert "not a configured remote" in capsys.readouterr().err
    assert not list(world["tmp"].rglob("pwned"))


def test_sync_works_without_git_identity(tmp_path, world, monkeypatch):
    c1 = world["c1"]
    c3 = _clone(world["remote"], tmp_path / "c3", identity=False)
    prov.record(world["hotfix"], "passed", cwd=c1)
    _sync(c1, "--push")
    x = git("rev-parse", "DEV", cwd=c3).strip()
    _write_note(c3, x, [_rec("failed", "2026-10-01T10:00:00.000000+00:00")])   # forces a real merge
    home = tmp_path / "empty-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for var in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL", "EMAIL"):
        monkeypatch.delenv(var, raising=False)
    assert _sync(c3) == 0
    assert prov.read(world["hotfix"], cwd=c3)["ci"] == "passed"
    assert prov.read(x, cwd=c3)["ci"] == "failed"


# --- record rules -------------------------------------------------------------

def test_same_time_tie_failed_wins(world):
    t = "2026-10-01T10:00:00.000000+00:00"
    _write_note(world["c1"], world["hotfix"], [_rec("passed", t), _rec("failed", t)])
    assert prov.read(world["hotfix"], cwd=world["c1"])["ci"] == "failed"


def test_z_and_offset_times_compare_correctly(world):
    _write_note(world["c1"], world["hotfix"], [_rec("failed", "2026-10-01T10:00:00Z"),
                                                _rec("passed", "2026-10-01T12:30:00+02:00")])
    # 12:30+02:00 is 10:30 UTC, later than 10:00Z: passed wins although it sorts first as text.
    assert prov.read(world["hotfix"], cwd=world["c1"])["ci"] == "passed"


def test_future_record_ignored(world):
    _write_note(world["c1"], world["hotfix"], [_rec("passed", "2026-10-01T10:00:00Z"),
                                                _rec("failed", "2099-01-01T00:00:00Z")])
    assert [r["ci"] for r in prov.read_all(world["hotfix"], cwd=world["c1"])] == ["passed"]


def test_missing_or_bad_at_is_oldest(world):
    _write_note(world["c1"], world["hotfix"], [_rec("failed"), _rec("failed", "yesterday"),
                                                _rec("passed", "2026-10-01T10:00:00Z")])
    assert prov.read(world["hotfix"], cwd=world["c1"])["ci"] == "passed"


def test_reimport_adds_no_duplicate(world):
    c1 = world["c1"]
    main(["import-ci", world["ci"], "-C", c1])
    main(["import-ci", world["ci"], "-C", c1])
    assert len(prov.read_all(world["hotfix"], cwd=c1)) == 1


def test_record_after_failure_appends(world):
    c1 = world["c1"]
    prov.record(world["hotfix"], "failed", cwd=c1)
    prov.record(world["hotfix"], "passed", cwd=c1)
    assert len(prov.read_all(world["hotfix"], cwd=c1)) == 2
    assert prov.read(world["hotfix"], cwd=c1)["ci"] == "passed"


def test_hand_written_multiline_json_note(world):
    _write_note(world["c1"], world["hotfix"], ["{", '  "ci": "passed",', '  "url": "https://ci/9"', "}"])
    assert prov.read(world["hotfix"], cwd=world["c1"])["url"] == "https://ci/9"


def test_old_single_line_notes_still_work(world):
    _write_note(world["c1"], world["hotfix"], ['{"ci": "passed", "env": "prod"}'])
    assert prov.read(world["hotfix"], cwd=world["c1"])["ci"] == "passed"


def test_junk_lines_ignored(world):
    _write_note(world["c1"], world["hotfix"], ["not json", '{"ci": "maybe"}', "[1, 2]",
                                                _rec("passed", "2026-10-01T10:00:00Z")])
    assert len(prov.read_all(world["hotfix"], cwd=world["c1"])) == 1


def test_huge_note_skipped_without_reading(world, monkeypatch):
    _write_note(world["c1"], world["hotfix"], [_rec("passed", "2026-10-01T10:00:00Z", pad="x" * 100_000)])
    shown = []
    real_git = prov.git

    def spy(*args, **kw):
        if "show" in args:
            shown.append(args)
        return real_git(*args, **kw)
    monkeypatch.setattr(prov, "git", spy)
    assert prov.read_all(world["hotfix"], cwd=world["c1"]) == []
    assert shown == []


def test_env_field_never_makes_a_change_in_prod(world):
    dev = git("rev-parse", "DEV", cwd=world["c1"]).strip()
    prov.record(dev, "passed", cwd=world["c1"], env="prod")
    assert _level(world["c1"], dev).level == prov.CI_PASSED     # not PROD_VERIFIED


# --- shallow clones -----------------------------------------------------------

def test_shallow_root_not_read(world, tmp_path):
    shallow = str(tmp_path / "shallow")
    git("clone", "-q", "--depth", "1", "--no-single-branch", f"file://{world['remote']}", shallow)
    for b in ("UAT", "DEV"):
        git("branch", b, f"origin/{b}", cwd=shallow)
    roots = set(git("rev-parse", "--git-path", "shallow", cwd=shallow) and
                Path(shallow, ".git", "shallow").read_text().split())
    assert roots
    changes = track(["PROD", "UAT", "DEV"], cwd=shallow)
    read = {s for ch in changes for s in ch.all_shas()}
    assert not (roots & read)
