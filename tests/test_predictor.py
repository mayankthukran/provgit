"""Conflict prediction: dry-run merge, origin of each side, trust level, recommendation."""

import json
import subprocess
from pathlib import Path

import pytest

from provgit import provenance as prov
from provgit.cli import main
from provgit.gitutil import git
from provgit.predictor import parse_hunks, predict, recommend, Side, Origin

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def demo(tmp_path):
    repo = tmp_path / "demo"
    subprocess.run(["bash", str(ROOT / "scripts" / "make_demo_repo.sh"), str(repo)],
                   check=True, capture_output=True)
    return str(repo)


def _init(tmp_path):
    repo = str(tmp_path / "r")
    git("init", "-q", "-b", "PROD", repo)
    git("config", "user.name", "t", cwd=repo)
    git("config", "user.email", "t@example.com", cwd=repo)
    return repo


def _commit(repo, files: dict, msg: str):
    for name, text in files.items():
        Path(repo, name).write_text(text)
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", msg, cwd=repo)
    return git("rev-parse", "HEAD", cwd=repo).strip()


# --- parsing conflict markers -------------------------------------------------

def test_parse_hunks_tracks_line_numbers_per_side():
    text = "\n".join([
        "a", "b",
        "<<<<<<< UAT", "x1", "x2", "=======", "y1", ">>>>>>> DEV",
        "c",
        "<<<<<<< UAT", "x3", "=======", "y2", "y3", ">>>>>>> DEV",
    ])
    hunks = parse_hunks(text)
    assert hunks == [
        (["x1", "x2"], 3, ["y1"], 3),
        (["x3"], 6, ["y2", "y3"], 5),     # side A: a b x1 x2 c x3 / side B: a b y1 c y2
    ]


def test_parse_hunks_skips_diff3_base_block():
    text = "<<<<<<< A\nours\n||||||| base\nold\n=======\ntheirs\n>>>>>>> B\n"
    assert parse_hunks(text) == [(["ours"], 1, ["theirs"], 1)]


# --- the slide scenario -------------------------------------------------------

def test_slide_conflict_shows_origin_and_trust(demo):
    prov.import_ci(str(Path(demo) / "ci_results.json"), cwd=demo)
    rep = predict("DEV", "UAT", prod="PROD", cwd=demo)

    assert len(rep.conflicts) == 1
    c = rep.conflicts[0]
    assert c.path == "settings.py"
    assert c.a.lines == ["TIMEOUT = 30  # stop prod outage"]
    assert c.b.lines == ["TIMEOUT = 5  # shorter jobs"]

    # Side A is the UAT backport, which inherits the PROD hotfix's CI result.
    assert "Hotfix #812" in c.a.origins[0].title
    assert c.a.origins[0].sha == git("rev-parse", "UAT", cwd=demo).strip()
    assert set(c.a.origins[0].copies) == {"PROD", "UAT"}
    assert c.a.level == prov.PROD_VERIFIED

    assert "PR #845" in c.b.origins[0].title
    assert c.b.level == prov.UNTESTED
    assert c.recommendation.startswith("Keep Side A (UAT)")

    # Tested alone != tested together: both DEV changes come in untested.
    titles = [o.title for o in rep.untested_incoming]
    assert any("PR #845" in t for t in titles) and any("PR #830" in t for t in titles)


def test_without_notes_both_sides_are_unknown(demo):
    c = predict("DEV", "UAT", prod="PROD", cwd=demo).conflicts[0]
    assert c.a.level == prov.UNKNOWN and c.b.level == prov.UNKNOWN
    assert "Neither side is tested" in c.recommendation


def test_predict_changes_nothing_in_the_repo(demo):
    before = (git("rev-parse", "HEAD", cwd=demo), git("status", "--porcelain", cwd=demo),
              git("for-each-ref", cwd=demo))
    predict("DEV", "UAT", prod="PROD", cwd=demo)
    after = (git("rev-parse", "HEAD", cwd=demo), git("status", "--porcelain", cwd=demo),
             git("for-each-ref", cwd=demo))
    assert before == after
    assert not (Path(demo) / ".git" / "MERGE_HEAD").exists()


# --- CLI ------------------------------------------------------------------------

def test_cli_exits_1_on_conflict(demo, capsys):
    assert main(["predict", "DEV", "--into", "UAT", "--prod", "PROD", "-C", demo]) == 1
    out = capsys.readouterr().out
    assert "1 conflict" in out and "settings.py" in out and "Tested alone" in out


def test_cli_json(demo, capsys):
    prov.import_ci(str(Path(demo) / "ci_results.json"), cwd=demo)
    assert main(["predict", "DEV", "--into", "UAT", "--prod", "PROD", "--json", "-C", demo]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["clean"] is False
    assert data["conflicts"][0]["a"]["level"] == prov.PROD_VERIFIED
    assert data["conflicts"][0]["b"]["level"] == prov.UNTESTED


def test_clean_merge_exits_0_but_warns_untested(tmp_path, capsys):
    repo = _init(tmp_path)
    _commit(repo, {"a.py": "x = 1\n"}, "Initial")
    git("branch", "DEV", cwd=repo)
    git("checkout", "-q", "DEV", cwd=repo)
    _commit(repo, {"b.py": "y = 2\n"}, "PR #900: add b")
    assert main(["predict", "DEV", "--into", "PROD", "--prod", "PROD", "-C", repo]) == 0
    out = capsys.readouterr().out
    assert "merges cleanly" in out and "PR #900" in out and "Tested alone" in out


def test_unknown_branch_is_a_git_error(demo):
    assert main(["predict", "NOPE", "--into", "UAT", "-C", demo]) == 2


# --- other conflict shapes ------------------------------------------------------

def test_modify_delete_conflict_still_has_origins(tmp_path):
    repo = _init(tmp_path)
    _commit(repo, {"cfg.py": "A = 1\n"}, "Initial")
    git("branch", "DEV", cwd=repo)
    edit = _commit(repo, {"cfg.py": "A = 2\n"}, "Hotfix #1: change A")
    git("checkout", "-q", "DEV", cwd=repo)
    git("rm", "-q", "cfg.py", cwd=repo)
    git("commit", "-q", "-m", "PR #2: remove cfg", cwd=repo)
    removal = git("rev-parse", "HEAD", cwd=repo).strip()

    rep = predict("DEV", "PROD", prod="PROD", cwd=repo)
    assert len(rep.conflicts) == 1
    c = rep.conflicts[0]
    assert c.a.origins[0].sha == edit
    assert c.b.origins[0].sha == removal


def test_partial_backport_side_gets_a_warning(tmp_path):
    """A backport edited during conflict resolution: linked, but its edited lines are untested."""
    repo = _init(tmp_path)
    _commit(repo, {"s.py": "T = 10\nR = 3\n"}, "Initial")
    git("branch", "UAT", cwd=repo)
    git("branch", "DEV", cwd=repo)
    fix = _commit(repo, {"s.py": "T = 30\nR = 3\n"}, "Hotfix #812: raise T")
    git("checkout", "-q", "UAT", cwd=repo)
    _commit(repo, {"s.py": "T = 25\nR = 3\n"},
            f"Hotfix #812: raise T (backport)\n\n(cherry picked from commit {fix})")
    git("checkout", "-q", "DEV", cwd=repo)
    _commit(repo, {"s.py": "T = 5\nR = 3\n"}, "PR #845: lower T")
    prov.record(fix, "passed", cwd=repo)

    c = predict("DEV", "UAT", prod="PROD", cwd=repo).conflicts[0]
    assert c.a.partial
    assert c.a.level == prov.PROD_VERIFIED
    assert "modified during conflict resolution" in c.recommendation


def _side(branch, level):
    return Side(branch, ["x"], 1, [Origin("0" * 40, "t", "0000000", level, "", False, {})])


def test_recommendation_rules():
    assert recommend(_side("UAT", prov.CI_PASSED), _side("DEV", prov.UNTESTED)).startswith("Keep Side A")
    assert recommend(_side("UAT", prov.UNTESTED), _side("DEV", prov.PROD_VERIFIED)).startswith("Keep Side B")
    assert "never together" in recommend(_side("UAT", prov.CI_PASSED), _side("DEV", prov.CI_PASSED))
    assert "Neither side" in recommend(_side("UAT", prov.CI_FAILED), _side("DEV", prov.UNKNOWN))
