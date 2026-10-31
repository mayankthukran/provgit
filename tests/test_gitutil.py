import subprocess

from provgit.gitutil import (
    git,
    parse_backport_refs,
    parse_change_refs,
    patch_id,
)

# Real-format commit messages (shapes copied from public CPython and Django history).
CPYTHON_MSG = """[3.12] gh-112345: Fix crash on long URLs (GH-112400) (#112410)

Long URLs no longer overflow the buffer.
(cherry picked from commit 4f1d2c3b9a8e7f6d5c4b3a291807f6e5d4c3b2a1)

Co-authored-by: Example Dev <dev@example.com>
"""

DJANGO_MSG = """[5.0.x] Fixed #35000 -- Corrected timeout default in settings.

Backport of 9a8b7c6d5e4f3a2b1c0d9e8f7a6b5c4d3e2f1a0b from main.
"""


def test_parses_cpython_cherry_pick_trailer():
    refs = parse_backport_refs(CPYTHON_MSG)
    assert len(refs) == 1
    assert refs[0].style == "cherry-pick"
    assert refs[0].source_sha == "4f1d2c3b9a8e7f6d5c4b3a291807f6e5d4c3b2a1"


def test_parses_django_backport_trailer():
    refs = parse_backport_refs(DJANGO_MSG)
    assert len(refs) == 1
    assert refs[0].style == "django"
    assert refs[0].source_sha == "9a8b7c6d5e4f3a2b1c0d9e8f7a6b5c4d3e2f1a0b"
    assert refs[0].from_branch == "main"


def test_plain_commit_has_no_backport_refs():
    assert parse_backport_refs("Add new feature\n\nNothing copied here.") == []


def test_change_refs_from_titles():
    assert parse_change_refs(CPYTHON_MSG)["pr"] == ["112400", "112410"]
    assert parse_change_refs(DJANGO_MSG)["ticket"] == ["35000"]


def _repo(tmp_path):
    def run(*args):
        return git(*args, cwd=str(tmp_path))
    run("init", "-q", "-b", "main")
    run("config", "user.name", "t")
    run("config", "user.email", "t@t")
    return run


def test_cherry_pick_keeps_patch_id_but_changes_sha(tmp_path):
    run = _repo(tmp_path)
    (tmp_path / "settings.py").write_text("TIMEOUT = 10\nRETRIES = 3\n")
    run("add", ".")
    run("commit", "-q", "-m", "base")
    run("branch", "UAT")

    (tmp_path / "settings.py").write_text("TIMEOUT = 30\nRETRIES = 3\n")
    run("commit", "-q", "-am", "Hotfix #812: raise timeout")
    hotfix = run("rev-parse", "HEAD").strip()

    run("checkout", "-q", "UAT")
    run("cherry-pick", "-x", hotfix)
    backport = run("rev-parse", "HEAD").strip()

    assert hotfix != backport                                   # new SHA...
    assert patch_id(hotfix, cwd=str(tmp_path)) == patch_id(backport, cwd=str(tmp_path))  # ...same change
    msg = run("log", "-1", "--format=%B", backport)
    assert parse_backport_refs(msg)[0].source_sha == hotfix     # -x trailer links them


def test_cli_version():
    out = subprocess.run(["provgit", "--version"], capture_output=True, text=True)
    assert out.returncode == 0
    assert "provgit" in out.stdout
