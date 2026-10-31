# provgit

**Merges that know what's tested.** A Git-integrated tool that shows, for every merge conflict, where each side came from and whether it was tested or is already running in production.

Built for Hacktoberfest 2026 Hack Day Sonipat, Problem 01: *Intelligent Git Conflict Resolution with Testing Provenance*.

## The problem

Teams keep long-lived branches (DEV → UAT → PROD). Hotfixes are made on PROD and backported down; new work is promoted up. When both touch the same lines, Git shows two blocks of text and nothing else. It does not say that one side passed CI and runs in production while the other was never tested.

## What provgit does

| Feature | Status |
|---|---|
| Link a change across branches by `git patch-id` (same diff, new SHA) | ✅ |
| Parse backport references: CPython `(cherry picked from commit …)` and Django `Backport of … from main.` | ✅ |
| Track each change across DEV/UAT/PROD (patch-id, backport trailers, PR numbers) | ✅ |
| Store and share testing provenance with git notes; inherit it across copies of a change | ✅ |
| Report UNKNOWN (never "tested") when a clone has no provenance metadata | ✅ |
| Commit trailers on the final merge commit | 🚧 |
| Predict conflicts with a dry-run merge (`git merge-tree --write-tree`) | ✅ |
| Show each conflict side's origin (`git blame`), test status and a recommendation | ✅ |
| Write a standard Git merge commit with provenance trailers | 🚧 |
| Plain-English conflict explanations from a local open-weight model (Ollama) | 🚧 |

## Install

Requires Python 3.10+ and Git 2.38+.

```bash
git clone <this-repo-url>
cd <repo>
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[test]"
provgit --version
```

### Optional: local model for explanations

```bash
# install Ollama from https://ollama.com/download, start it, then:
ollama pull qwen2.5-coder:14b      # or qwen2.5-coder:7b on 8 GB laptops
export PROVGIT_MODEL=qwen2.5-coder:14b
```

The core verdicts use deterministic Git logic, so provgit works without a model running.

## Usage

```bash
scripts/make_demo_repo.sh demo-repo                       # the PROD/UAT/DEV scenario from the slide

provgit track PROD UAT DEV --prod PROD -C demo-repo       # every change, its copies, its test status
provgit import-ci demo-repo/ci_results.json -C demo-repo  # load CI results into git notes
provgit record <sha> --ci passed --env prod               # record one CI result by hand
provgit status <sha> --branches PROD UAT DEV --prod PROD  # status of one commit, inherited from its copies
provgit predict DEV --into UAT --prod PROD -C demo-repo   # dry-run merge: each conflict, its origin, tested or not
provgit show <sha>                                        # patch-id + backport references of a commit
```

### Predicting conflicts

`provgit predict DEV --into UAT` merges in memory with `git merge-tree --write-tree`, so the working folder, index and branches do not change. For each conflict hunk it:

1. finds the commits that wrote each side with `git blame` on that side's branch;
2. maps each commit to its logical change, so a UAT backport inherits the CI result of the PROD hotfix it was copied from;
3. recommends a side: keep the tested one, or, when both passed CI separately, run CI on the combined result;
4. warns when a side is a backport whose code was edited during conflict resolution.

Even for a clean merge it lists the incoming changes that were never tested: *tested alone ≠ tested together*. It exits 1 when there are conflicts, so it can be used as a CI gate. `--json` gives machine-readable output.

### Trust levels

| Level | Meaning |
|---|---|
| 🟢 PROD_VERIFIED | CI passed on a copy of this change, and the change is in the production branch |
| 🔵 CI_PASSED | CI passed, not in production yet |
| 🟡 IN_PROD_NO_CI | In production, but no CI record |
| 🟠 CI_FAILED | CI failed |
| 🔴 UNTESTED | Provenance metadata exists, but nothing for this change |
| ⚪ UNKNOWN | This clone has no provenance metadata at all |

### How a change is followed across branches

1. **exact**: same `git patch-id` (the diff is identical, only the SHA changed).
2. **declared**: a backport trailer names the source commit: `(cherry picked from commit X)` (CPython, `git cherry-pick -x`) or `Backport of X from main.` (Django). This still links copies whose code was edited during conflict resolution; those are marked *partial*, because the edited lines were never tested.
3. **referenced**: the titles share a PR or issue number (`#812`, `GH-112400`).

### Where provenance is stored

CI results are JSON in the git notes ref `refs/notes/provenance`. Notes attach data to a commit without changing its SHA, but a plain `git clone` does not fetch them. Share them with:

```bash
git push origin refs/notes/provenance
git fetch origin refs/notes/provenance:refs/notes/provenance
```

See [`examples/`](examples/) for real inputs and outputs.

## Tests

```bash
pytest -v
```

Tests build temporary Git repositories and check real Git behaviour (cherry-picks keep the patch-id, `-x` trailers link commits). CI runs them on every pull request.

## License

MIT
