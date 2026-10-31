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
| Predict conflicts with a dry-run merge (`git merge-tree --write-tree`) | ✅ |
| Show each conflict side's origin (`git blame`), test status and a recommendation | ✅ |
| Write a standard Git merge commit with provenance trailers (`provgit merge`) | ✅ |
| Plain-English conflict explanations from a local open-weight model (`provgit explain`, Ollama) | ✅ |
| Share provenance between clones (`provgit sync`); several records per commit, newest wins | ✅ |
| Tested on real Django backport history (237 of 237 backports linked) | ✅ |

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
git -C demo-repo checkout UAT
provgit merge DEV --into UAT --prod PROD --prefer-tested -C demo-repo   # real merge commit with provenance trailers
provgit explain DEV --into UAT --prod PROD -C demo-repo   # predict + plain-English explanation from a local model
provgit ai-check                                          # is the local model ready?
provgit sync [--push]                                     # share CI results (git notes) with the team
provgit show <sha>                                        # patch-id + backport references of a commit
```

### Predicting conflicts

`provgit predict DEV --into UAT` merges in memory with `git merge-tree --write-tree`, so the working folder, index and branches do not change. For each conflict hunk it:

1. finds the commits that wrote each side with `git blame` on that side's branch;
2. maps each commit to its logical change, so a UAT backport inherits the CI result of the PROD hotfix it was copied from;
3. recommends a side: keep the tested one, or, when both passed CI separately, run CI on the combined result;
4. warns when a side is a backport whose code was edited during conflict resolution.

Even for a clean merge it lists the incoming changes that were never tested: *tested alone ≠ tested together*. It exits 1 when there are conflicts, so it can be used as a CI gate. `--json` gives machine-readable output.

### Merging with provenance

`provgit merge DEV --into UAT` shows the same report, then picks a side for each conflict and writes a **normal Git merge commit** (`git merge --no-ff`, two parents):

- `--prefer-tested` keeps the tested side when exactly one side is tested. If both or neither are tested, or a side is a backport edited during a conflict, provgit stops instead of guessing.
- `--take A` / `--take B` keeps one side for every conflict.
- Without a flag, in a terminal, it asks for each conflict (`[A] / [B] / [m]anual / [q]uit`).
- `--rerere` lets Git remember the resolution and reuse it next time.

The commit message records the decision as standard Git trailers, readable with `git log --format='%(trailers)'` or `git interpret-trailers --parse`:

```
Merge DEV (051a3c9) into UAT

provgit resolved 1 conflict using testing provenance.
The merge result itself is untested: run CI before promoting.

Provgit-Resolved: "settings.py":2 kept=A(UAT) 536da8c PROD_VERIFIED
Provgit-Rejected: "settings.py":2 B(DEV) 051a3c9 UNTESTED
Provgit-Untested-Incoming: b77b736 051a3c9
Provgit-Tested-Together: no
Provgit-Version: 0.1.0
```

Trailers are part of the commit, so they survive any clone, even one without the git notes.

**Safety.** provgit merge never leaves a half-finished merge: on any error, or Ctrl-C, it runs `git merge --abort`. It refuses to start with uncommitted changes, a merge in progress, or when the target branch is not checked out. Both branches are pinned to their SHAs when the report is made; if either moves before the merge, it stops. Every conflict block must match the report exactly, so provgit never writes a side you did not see; ambiguous blocks (for example a reStructuredText `=======` heading inside a conflict) are left to you. It never writes through symlinks, and leaves symlink and submodule conflicts to you. File names are sanitised, so a file name cannot forge a trailer or send escape codes to your terminal.

**Trailers are an audit record, not proof.** Anyone can type a trailer into a commit message, so provgit never reads trailers to decide whether something was tested. Trust comes only from CI results in `refs/notes/provenance`; in a real deployment, only the CI bot should be allowed to push that ref.

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
3. **referenced**: the titles share a PR or issue number (`#812`, `GH-112400`). This is weak evidence, so it only links commits that have no stronger partner on the other branch: ticket numbers can never chain separate fixes together (which would let one change inherit another change's CI result).

When a declared backport's patch-id differs, provgit compares only the added and removed lines. If they are identical and only the surrounding code differs, the link is "same changed lines, different surrounding code" and is **not** marked partial.

### Where provenance is stored

CI results are JSON in the git notes ref `refs/notes/provenance`. Notes attach data to a commit without changing its SHA, but a plain `git clone` does not fetch them, so a fresh clone reports ⚪ UNKNOWN. Share them with:

```bash
provgit sync            # fetch the team's records and merge them into yours
provgit sync --push     # ...and publish yours (opt-in)
```

`sync` fetches into `refs/notes/provenance-remote` and merges with `git notes merge -s cat_sort_uniq`, which never conflicts: every clone's records are kept, one JSON record per line. For each commit the **newest record counts** (UTC timestamps with microseconds); `failed` wins an exact tie, records dated more than a day in the future are ignored, and the status says when results disagree. `provgit record` adds a record instead of overwriting, so synced records are never lost, and re-importing the same CI results adds nothing.

**Trust model.** Anyone who can push `refs/notes/provenance` can write CI results, just as anyone who can push code can change it. In a real setup only the CI bot pushes notes and people run `provgit sync` without `--push`; branch protection rules apply to branches, so restrict who can push to the repository. The `env` field is informational only: whether a change is in production always comes from branch history (`git merge-base --is-ancestor`), never from a note. Notes larger than 64 KB and invalid lines are ignored.

### Explaining conflicts with a local model

`provgit explain` prints the normal `predict` report first, then asks a local open-weight model (`qwen2.5-coder` via Ollama) to explain each conflict in plain English and draft a combined resolution, labelled *AI draft, not tested*. How the model is used safely:

- the model can never change a trust level, link or recommendation; trust comes only from CI records;
- only a local Ollama is used unless `PROVGIT_ALLOW_REMOTE_LLM=1`; proxy settings are ignored and redirects refused, so code does not leave the machine by accident;
- repository text is passed as untrusted data inside per-call random markers; answers are schema-checked, size-capped and cleaned of control characters, and cannot print trust levels;
- drafts are only shown, never applied; the model digest and Ollama version are recorded with every answer.

Without Ollama, `explain` shows the verdict and says the model is unavailable. Real output, including a mistake the model made, is in [`examples/05_ai_explain`](examples/05_ai_explain/).

## Real data: Django

`provgit track main stable/6.1.x` on a shallow clone of Django (2026-11-01, 3 s, 93 MB):

| | |
|---|---|
| Declared backports on `stable/6.1.x` | 237 |
| Linked to their `main` commit | **237 (100%)** |
| Identical copies (same patch-id) | 225 |
| Backports whose code was edited (lines never tested on `main`) | **10** |
| Same changed lines, different surrounding code | 6 |

8 of 8 links checked by hand were correct. Running on real history also found and fixed two bugs (ticket numbers chaining separate fixes, and false "partial" results). Details and commit links: [`examples/07_django_backports`](examples/07_django_backports/summary.md).

See [`examples/`](examples/) for real inputs and outputs.

## Tests

```bash
pytest -v
```

Tests build temporary Git repositories and check real Git behaviour (cherry-picks keep the patch-id, `-x` trailers link commits). CI runs them on every pull request.

## License

MIT
