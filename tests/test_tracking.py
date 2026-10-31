"""Tracking changes across branches and inheriting their test provenance."""

import json
import os
import subprocess
from pathlib import Path

import pytest

from provgit import provenance as prov
from provgit.gitutil import git
from provgit.matcher import change_of, is_partial, track

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def demo(tmp_path):
    """The slide scenario built by scripts/make_demo_repo.sh."""
    repo = tmp_path / "demo"
    subprocess.run(["bash", str(ROOT / "scripts" / "make_demo_repo.sh"), str(repo)],
                   check=True, capture_output=True)
    return str(repo)


def _sha(repo, rev):
    return git("rev-parse", rev, cwd=repo).strip()


def _find(changes, text):
    return next(c for c in changes if text in c.title)


def test_hotfix_is_one_change_on_prod_and_uat(demo):
    changes = track(["PROD", "UAT", "DEV"], cwd=demo)
    hotfix = _find(changes, "Hotfix #812")
    assert set(hotfix.commits) == {"PROD", "UAT"}
    assert hotfix.commits["PROD"] != hotfix.commits["UAT"]           # different SHAs
    kinds = {l.kind for l in hotfix.links}
    assert {"exact", "declared"} <= kinds                             # patch-id + -x trailer
    assert not is_partial(hotfix)


def test_without_metadata_status_is_unknown_never_tested(demo):
    changes = track(["PROD", "UAT", "DEV"], cwd=demo)
    st = prov.status_of(_find(changes, "Hotfix #812"), "PROD", cwd=demo)
    assert st.level == prov.UNKNOWN


def test_backport_inherits_prod_ci_result(demo):
    prov.import_ci(os.path.join(demo, "ci_results.json"), cwd=demo)
    changes = track(["PROD", "UAT", "DEV"], cwd=demo)

    uat_copy = _sha(demo, "UAT")                       # the backport commit itself has no record
    assert prov.read(uat_copy, cwd=demo) is None
    st = prov.status_of(change_of(uat_copy, changes), "PROD", cwd=demo)
    assert st.level == prov.PROD_VERIFIED
    assert st.evidence_sha == _sha(demo, "PROD")

    pr845 = prov.status_of(_find(changes, "PR #845"), "PROD", cwd=demo)
    assert pr845.level == prov.UNTESTED


def test_ci_failed_is_reported(demo):
    prov.record("DEV", "failed", cwd=demo)
    changes = track(["PROD", "UAT", "DEV"], cwd=demo)
    assert prov.status_of(_find(changes, "PR #845"), "PROD", cwd=demo).level == prov.CI_FAILED


def test_ci_passed_but_not_in_prod(demo):
    prov.record("DEV", "passed", cwd=demo)
    changes = track(["PROD", "UAT", "DEV"], cwd=demo)
    assert prov.status_of(_find(changes, "PR #845"), "PROD", cwd=demo).level == prov.CI_PASSED


def _repo(tmp_path):
    def run(*a, **kw):
        return git(*a, cwd=str(tmp_path), **kw)
    run("init", "-q", "-b", "main")
    run("config", "user.name", "t")
    run("config", "user.email", "t@t")
    return run


def test_backport_modified_during_conflict_is_linked_but_partial(tmp_path):
    run = _repo(tmp_path)
    f = tmp_path / "settings.py"
    f.write_text("TIMEOUT = 10\nRETRIES = 3\n")
    run("add", ".")
    run("commit", "-qm", "base")
    run("branch", "stable")

    f.write_text("TIMEOUT = 30\nRETRIES = 5\n")
    run("commit", "-qam", "Fix outage (#812)")
    src = run("rev-parse", "HEAD").strip()

    # stable diverged on RETRIES, so the cherry-pick conflicts and is resolved by hand
    run("checkout", "-q", "stable")
    f.write_text("TIMEOUT = 10\nRETRIES = 4\n")
    run("commit", "-qam", "Stable-only tweak")
    run("cherry-pick", "-x", src, check=False)
    f.write_text("TIMEOUT = 30\nRETRIES = 4\n")          # kept stable's RETRIES
    run("add", ".")
    run("-c", "core.editor=true", "cherry-pick", "--continue")

    changes = track(["main", "stable"], cwd=str(tmp_path))
    fix = _find(changes, "Fix outage")
    assert set(fix.commits) == {"main", "stable"}
    assert not any(l.kind == "exact" for l in fix.links)    # diffs differ...
    assert any(l.kind == "declared" for l in fix.links)     # ...but the trailer links them
    assert is_partial(fix)

    prov.record(src, "passed", cwd=str(tmp_path))
    st = prov.status_of(fix, "main", cwd=str(tmp_path))
    assert st.level == prov.PROD_VERIFIED and st.partial
    assert "modified during backport" in st.reason


def test_django_style_backport_without_x_trailer(tmp_path):
    run = _repo(tmp_path)
    (tmp_path / "a.py").write_text("x = 1\n")
    run("add", ".")
    run("commit", "-qm", "base")
    run("branch", "stable/5.0.x")
    (tmp_path / "a.py").write_text("x = 2\n")
    run("commit", "-qam", "Fixed #35000 -- Corrected x.")
    src = run("rev-parse", "HEAD").strip()

    run("checkout", "-q", "stable/5.0.x")
    (tmp_path / "a.py").write_text("x = 2\n")
    run("commit", "-qam", f"[5.0.x] Fixed #35000 -- Corrected x.\n\nBackport of {src[:10]} from main.")

    changes = track(["main", "stable/5.0.x"], cwd=str(tmp_path))
    fix = _find(changes, "Corrected x")
    assert set(fix.commits) == {"main", "stable/5.0.x"}
    assert any(l.kind == "declared" and "django" in l.detail for l in fix.links)


def test_cli_track_json(demo):
    out = subprocess.run(["provgit", "track", "PROD", "UAT", "DEV", "--prod", "PROD", "--json", "-C", demo],
                         capture_output=True, text=True, check=True)
    rows = json.loads(out.stdout)
    hotfix = next(r for r in rows if "Hotfix #812" in r["title"])
    assert hotfix["status"]["level"] == prov.UNKNOWN


def test_ticket_numbers_do_not_chain_complete_pairs(tmp_path):
    """Two separate backports that both say "Refs #5" must stay two changes (seen on Django)."""
    repo = str(tmp_path / "chain")
    git("init", "-q", "-b", "main", repo)
    git("config", "user.name", "t", cwd=repo)
    git("config", "user.email", "t@example.com", cwd=repo)

    def commit(name, text, msg):
        Path(repo, name).write_text(text)
        git("add", "-A", cwd=repo)
        git("commit", "-q", "-m", msg, cwd=repo)
        return git("rev-parse", "HEAD", cwd=repo).strip()

    commit("base", "0\n", "base")
    git("branch", "stable", cwd=repo)
    a = commit("a", "a\n", "Refs #5 -- Fix A")
    b = commit("b", "b\n", "Refs #5 -- Fix B")
    git("checkout", "-q", "stable", cwd=repo)
    git("cherry-pick", "-x", a, cwd=repo)
    git("cherry-pick", "-x", b, cwd=repo)

    changes = track(["main", "stable"], cwd=repo)
    for sha in (a, b):
        ch = change_of(sha, changes)
        assert len(ch.all_shas()) == 2, ch.all_shas()
        assert not any(l.kind == "referenced" for l in ch.links)
    prov.record(a, "passed", cwd=repo)
    assert prov.status_of(change_of(b, changes), None, cwd=repo).level == prov.UNTESTED


def test_backport_with_same_lines_but_different_context_is_not_partial(tmp_path):
    """Seen on Django: the stable file differs around the fix, so patch-ids differ, but the
    added/removed lines are identical. That is not an edited (partial) backport."""
    repo = str(tmp_path / "ctx")
    git("init", "-q", "-b", "main", repo)
    git("config", "user.name", "t", cwd=repo)
    git("config", "user.email", "t@example.com", cwd=repo)

    def commit(text, msg):
        Path(repo, "f.py").write_text(text)
        git("add", "-A", cwd=repo)
        git("commit", "-q", "-m", msg, cwd=repo)
        return git("rev-parse", "HEAD", cwd=repo).strip()

    commit("a\nb\nc\nTIMEOUT = 10\nd\n", "base")
    git("branch", "stable", cwd=repo)
    commit("a\nb\nc changed on main\nTIMEOUT = 10\nd\n", "unrelated main change")
    fix = commit("a\nb\nc changed on main\nTIMEOUT = 30\nd\n", "Fix timeout")
    git("checkout", "-q", "stable", cwd=repo)
    commit("a\nb\nc\nTIMEOUT = 30\nd\n", f"[stable] Fix timeout\n\nBackport of {fix} from main.")

    ch = change_of(fix, track(["main", "stable"], cwd=repo))
    link = next(l for l in ch.links if l.kind == "declared")
    assert "same changed lines" in link.detail
    assert not is_partial(ch)


def test_output_piped_into_head_does_not_crash(tmp_path):
    """`provgit track ... | head` must not print a BrokenPipeError traceback (seen on Django)."""
    import sys
    repo = str(tmp_path / "big")
    git("init", "-q", "-b", "main", repo)
    stream = []
    for i in range(1500):          # ~200 KB of track output: more than a pipe buffer holds
        data = f"{i}\n"
        stream += [f"commit refs/heads/main", f"committer t <t@example.com> {1700000000 + i} +0000",
                   f"data {len(f'change {i}')}", f"change {i}",
                   f"M 100644 inline f{i % 50}.txt", f"data {len(data)}", data.rstrip("\n"), ""]
    subprocess.run(["git", "fast-import", "--quiet"], cwd=repo, check=True,
                   input="\n".join(stream) + "\n", text=True)
    code = "import sys; from provgit.cli import main; sys.exit(main(sys.argv[1:]))"
    cmd = (f'"{sys.executable}" -c "{code}" track main --limit 2000 -C "{repo}" | head -1; '
           'exit ${PIPESTATUS[0]}')
    p = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True)
    assert "Traceback" not in p.stderr and "BrokenPipeError" not in p.stderr, p.stderr[-500:]
    assert p.returncode == 0
