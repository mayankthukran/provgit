"""provgit merge: side selection, standard merge commit with trailers, and every safety rule."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from provgit import merger
from provgit.cli import main
from provgit.gitutil import git, run
from provgit.merger import MergeStopped, execute, prepare, resolve_text

ROOT = Path(__file__).resolve().parents[1]
posix_only = pytest.mark.skipif(sys.platform == "win32", reason="needs POSIX file names / symlinks")


# --- helpers ------------------------------------------------------------------

def _demo(tmp_path, with_ci=True):
    repo = str(tmp_path / "demo")
    subprocess.run(["bash", str(ROOT / "scripts" / "make_demo_repo.sh"), repo],
                   check=True, capture_output=True)
    if with_ci:
        main(["import-ci", f"{repo}/ci_results.json", "-C", repo])
    git("checkout", "-q", "UAT", cwd=repo)
    return repo


@pytest.fixture
def demo(tmp_path):
    return _demo(tmp_path)


def _init(tmp_path, branch="main"):
    repo = str(tmp_path / "r")
    git("init", "-q", "-b", branch, repo)
    git("config", "user.name", "t", cwd=repo)
    git("config", "user.email", "t@example.com", cwd=repo)
    return repo


def _commit(repo, files: dict, msg: str):
    for name, data in files.items():
        p = Path(repo, name)
        if data is None:
            p.unlink()
        elif isinstance(data, bytes):
            p.write_bytes(data)
        else:
            p.write_text(data)
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", msg, cwd=repo)
    return git("rev-parse", "HEAD", cwd=repo).strip()


def _two_branches(tmp_path, base: dict, ours: dict, theirs: dict):
    """main and feature diverge from `base`; HEAD is main."""
    repo = _init(tmp_path)
    _commit(repo, base, "base")
    git("branch", "feature", cwd=repo)
    _commit(repo, ours, "ours")
    git("checkout", "-q", "feature", cwd=repo)
    _commit(repo, theirs, "theirs")
    git("checkout", "-q", "main", cwd=repo)
    return repo


def _head(repo):
    return git("rev-parse", "HEAD", cwd=repo).strip()


def _merging(repo):
    return run("rev-parse", "-q", "--verify", "MERGE_HEAD", cwd=repo).returncode == 0


def _trailers(repo):
    msg = git("log", "-1", "--format=%B", cwd=repo)
    return git("interpret-trailers", "--parse", cwd=repo, input=msg)


def _unchanged(repo, head):
    assert _head(repo) == head
    assert not _merging(repo)
    assert git("status", "--porcelain", "--untracked-files=no", cwd=repo) == ""


def _merge(repo, *extra, source="DEV", into="UAT"):
    return main(["merge", source, "--into", into, "--prod", "PROD", *extra, "-C", repo])


# --- resolve_text -------------------------------------------------------------

CONFLICT = "top\n<<<<<<< ours\na1\n=======\nb1\n>>>>>>> theirs\nmid\n<<<<<<< ours\na2\n=======\nb2\n>>>>>>> theirs\nend\n"
EXPECTED = [(["a1"], ["b1"]), (["a2"], ["b2"])]


def test_resolve_text_picks_each_side():
    assert resolve_text(CONFLICT, ["A", "B"], EXPECTED) == "top\na1\nmid\nb2\nend\n"


def test_resolve_text_keeps_crlf_and_missing_final_newline():
    text = "x\r\n<<<<<<< o\r\na\r\n=======\r\nb\r\n>>>>>>> t\r\ny"
    assert resolve_text(text, ["B"], [(["a"], ["b"])]) == "x\r\nb\r\ny"


def test_resolve_text_count_mismatch_stops():
    with pytest.raises(MergeStopped):
        resolve_text(CONFLICT, ["A"], EXPECTED[:1])


def test_resolve_text_content_mismatch_stops():
    with pytest.raises(MergeStopped, match="differs"):
        resolve_text(CONFLICT, ["A", "A"], [(["a1"], ["XX"]), (["a2"], ["b2"])])


def test_resolve_text_ambiguous_separator_stops():
    text = "<<<<<<< o\nTitle\n=======\n=======\nb\n>>>>>>> t\n"
    with pytest.raises(MergeStopped, match="ambiguous"):
        resolve_text(text, ["A"], [(["Title"], ["=======", "b"])])


# --- the slide scenario -------------------------------------------------------

def test_prefer_tested_keeps_prod_hotfix(demo):
    before = _head(demo)
    assert _merge(demo, "--prefer-tested") == 0
    parents = git("rev-list", "--parents", "-1", "HEAD", cwd=demo).split()
    assert parents[1:] == [before, git("rev-parse", "DEV", cwd=demo).strip()]
    assert "TIMEOUT = 30  # stop prod outage" in Path(demo, "settings.py").read_text()
    assert "retries" in Path(demo, "app.py").read_text()          # non-conflicting DEV change merged
    assert git("status", "--porcelain", "--untracked-files=no", cwd=demo) == ""


def test_trailers_are_standard_git_trailers(demo):
    _merge(demo, "--prefer-tested")
    t = _trailers(demo)
    assert 'Provgit-Resolved: "settings.py":2 kept=A(UAT)' in t
    assert "PROD_VERIFIED" in t
    assert 'Provgit-Rejected: "settings.py":2 B(DEV)' in t
    assert "UNTESTED" in t
    assert "Provgit-Tested-Together: no" in t
    assert "Provgit-Untested-Incoming: " in t


def test_take_b(demo):
    assert _merge(demo, "--take", "B") == 0
    assert "TIMEOUT = 5  # shorter jobs" in Path(demo, "settings.py").read_text()
    assert "kept=B(DEV)" in _trailers(demo)


def test_prefer_tested_without_notes_stops(tmp_path, capsys):
    repo = _demo(tmp_path, with_ci=False)          # everything UNKNOWN
    head = _head(repo)
    assert _merge(repo, "--prefer-tested") == 1
    assert "neither side is tested" in capsys.readouterr().out
    _unchanged(repo, head)


def test_no_strategy_and_no_terminal_stops(demo, capsys, monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    head = _head(demo)
    assert _merge(demo) == 1
    assert "--take" in capsys.readouterr().out
    _unchanged(demo, head)


def test_user_conflictstyle_zdiff3_is_ignored(demo):
    git("config", "merge.conflictStyle", "zdiff3", cwd=demo)
    assert _merge(demo, "--prefer-tested") == 0
    assert "TIMEOUT = 30" in Path(demo, "settings.py").read_text()


# --- refusals: nothing may change ---------------------------------------------

def test_dirty_worktree_refused(demo, capsys):
    Path(demo, "settings.py").write_text("edited\n")
    head = _head(demo)
    assert _merge(demo, "--prefer-tested") == 1
    assert "uncommitted" in capsys.readouterr().err
    assert _head(demo) == head and not _merging(demo)


def test_not_on_into_branch_refused(demo, capsys):
    git("checkout", "-q", "PROD", cwd=demo)
    head = _head(demo)
    assert _merge(demo, "--prefer-tested") == 1
    assert "git checkout UAT" in capsys.readouterr().err
    _unchanged(demo, head)


def test_into_must_be_local_branch(demo):
    git("tag", "v1", cwd=demo)
    with pytest.raises(MergeStopped, match="not a local branch"):
        prepare("DEV", "v1", cwd=demo)


def test_branch_moved_after_report_stops(demo):
    plan = prepare("DEV", "UAT", prod="PROD", cwd=demo)
    plan.choices = ["A"]
    git("checkout", "-q", "DEV", cwd=demo)
    _commit(demo, {"new.txt": "x\n"}, "sneaky change after the report")
    git("checkout", "-q", "UAT", cwd=demo)
    head = _head(demo)
    with pytest.raises(MergeStopped, match="moved"):
        execute(plan)
    _unchanged(demo, head)


def test_ctrl_c_during_merge_aborts(demo, monkeypatch):
    plan = prepare("DEV", "UAT", prod="PROD", cwd=demo)
    plan.choices = ["A"]
    head = _head(demo)

    def boom(*a, **k):
        raise KeyboardInterrupt
    monkeypatch.setattr(merger, "_resolve_path", boom)
    with pytest.raises(KeyboardInterrupt):
        execute(plan)
    _unchanged(demo, head)


def test_already_up_to_date(tmp_path, capsys):
    repo = _init(tmp_path, "PROD")
    _commit(repo, {"a": "1\n"}, "one")
    git("branch", "old", cwd=repo)
    _commit(repo, {"a": "2\n"}, "two")
    head = _head(repo)
    assert main(["merge", "old", "--into", "PROD", "--take", "A", "-C", repo]) == 0
    assert "Already up to date" in capsys.readouterr().out
    assert _head(repo) == head


# --- other conflict kinds -----------------------------------------------------

def test_clean_merge_writes_trailers(tmp_path):
    repo = _two_branches(tmp_path, {"a": "1\n", "b": "1\n"}, {"a": "2\n"}, {"b": "2\n"})
    assert main(["merge", "feature", "--into", "main", "--prefer-tested", "-C", repo]) == 0
    t = _trailers(repo)
    assert "Provgit-Resolved" not in t
    assert "Provgit-Untested-Incoming: " in t and "Provgit-Tested-Together: no" in t


@pytest.mark.parametrize("pick, exists", [("A", True), ("B", False)])
def test_modify_delete(tmp_path, pick, exists):
    repo = _two_branches(tmp_path, {"f": "1\n"}, {"f": "2\n"}, {"f": None})
    assert main(["merge", "feature", "--into", "main", "--take", pick, "-C", repo]) == 0
    assert Path(repo, "f").exists() is exists
    assert "whole-file" in _trailers(repo)


def test_non_utf8_file(tmp_path):
    repo = _two_branches(tmp_path, {"f": b"caf\xe9\n"}, {"f": b"caf\xe9 A\n"}, {"f": b"caf\xe9 B\n"})
    assert main(["predict", "feature", "--into", "main", "-C", repo]) == 1     # no crash
    assert main(["merge", "feature", "--into", "main", "--take", "B", "-C", repo]) == 0
    assert Path(repo, "f").read_bytes() == b"caf\xe9 B\n"                   # bytes kept exactly


def test_crlf_line_endings_kept(tmp_path):
    repo = _two_branches(tmp_path, {"f": b"x\r\n1\r\ny\r\n"}, {"f": b"x\r\n2\r\ny\r\n"},
                         {"f": b"x\r\n3\r\ny\r\n"})
    assert main(["merge", "feature", "--into", "main", "--take", "A", "-C", repo]) == 0
    assert Path(repo, "f").read_bytes() == b"x\r\n2\r\ny\r\n"


def test_rst_heading_inside_conflict_stops(tmp_path, capsys):
    repo = _two_branches(tmp_path, {"doc.rst": "x\n"}, {"doc.rst": "Title\n=======\n"},
                         {"doc.rst": "Other\n"})
    head = _head(repo)
    assert main(["merge", "feature", "--into", "main", "--take", "A", "-C", repo]) == 1
    assert "ambiguous" in capsys.readouterr().err
    _unchanged(repo, head)


def test_rerere_resolution_is_not_overwritten(tmp_path):
    repo = _two_branches(tmp_path, {"f": "1\n"}, {"f": "2\n"}, {"f": "3\n"})
    assert main(["merge", "feature", "--into", "main", "--take", "B", "--rerere", "-C", repo]) == 0
    git("reset", "-q", "--hard", "HEAD^", cwd=repo)
    # Second time: git reuses the recorded resolution (B). provgit must keep it and say so.
    assert main(["merge", "feature", "--into", "main", "--take", "A", "--rerere", "-C", repo]) == 0
    assert Path(repo, "f").read_text() == "3\n"
    assert "rerere" in _trailers(repo)


# --- security -----------------------------------------------------------------

@posix_only
def test_newline_in_file_name_cannot_forge_a_trailer(tmp_path):
    evil = "x\nProvgit-Tested-Together: yes"
    repo = _two_branches(tmp_path, {evil: "1\n"}, {evil: "2\n"}, {evil: "3\n"})
    assert main(["merge", "feature", "--into", "main", "--take", "A", "-C", repo]) == 0
    keys = [line.split(":", 1)[0] for line in _trailers(repo).splitlines()]
    assert keys.count("Provgit-Tested-Together") == 1
    assert "Provgit-Tested-Together: no" in _trailers(repo).splitlines()


@posix_only
@posix_only
def test_report_does_not_print_control_characters(tmp_path, capsys):
    evil = "f\x1b[2Jcleared"
    repo = _two_branches(tmp_path, {evil: "1\n"}, {evil: "2\n"}, {evil: "3\n"})
    main(["predict", "feature", "--into", "main", "-C", repo])
    assert "\x1b" not in capsys.readouterr().out


@posix_only
def test_symlink_conflict_refused(tmp_path, capsys):
    repo = _init(tmp_path)
    os.symlink("one", Path(repo, "link"))
    _commit(repo, {}, "base")
    git("branch", "feature", cwd=repo)
    os.remove(Path(repo, "link")); os.symlink("two", Path(repo, "link"))
    _commit(repo, {}, "ours")
    git("checkout", "-q", "feature", cwd=repo)
    os.remove(Path(repo, "link")); os.symlink("three", Path(repo, "link"))
    _commit(repo, {}, "theirs")
    git("checkout", "-q", "main", cwd=repo)
    head = _head(repo)
    assert main(["merge", "feature", "--into", "main", "--take", "A", "-C", repo]) == 1
    assert "symlink" in capsys.readouterr().err
    _unchanged(repo, head)


@posix_only
def test_safe_path_refuses_writing_through_a_symlink(tmp_path):
    repo = tmp_path / "repo"; repo.mkdir()
    outside = tmp_path / "outside"; outside.mkdir()
    os.symlink(outside, repo / "dir")
    with pytest.raises(MergeStopped, match="symlink"):
        merger._safe_path(str(repo), "dir/settings.py")
    assert merger._safe_path(str(repo), "ok.py").endswith("ok.py")
