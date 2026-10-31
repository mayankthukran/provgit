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
| Store and share testing provenance with git notes and commit trailers | 🚧 |
| Predict conflicts with a dry-run merge (`git merge-tree --write-tree`) | 🚧 |
| Show each conflict side's origin and test status | 🚧 |
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
provgit show <sha> -C path/to/repo     # patch-id + backport references of a commit
```

See [`examples/`](examples/) for real inputs and outputs.

## Tests

```bash
pytest -v
```

Tests build temporary Git repositories and check real Git behaviour (cherry-picks keep the patch-id, `-x` trailers link commits). CI runs them on every pull request.

## License

MIT
