# HACKATHON.md

## Team name
Brain Not Found

## Team members
- Rohit Kumar
- Anurag Pandey
- Mayank Yadav
- Sarthak Mishra

## Problem statement
**Problem 01 — Intelligent Git Conflict Resolution with Testing Provenance**
(Slide title: "Merges that know what's tested")

We are building **provgit**, a Git-integrated command-line tool that:
- follows each change across long-lived branches (DEV, UAT, PROD), even after cherry-picks and backports change its commit SHA;
- records and transfers testing provenance (CI result, deployed-to-PROD) using standard Git (git notes and commit trailers);
- predicts merge conflicts before a merge, using a dry-run merge;
- shows, for every conflict, where each side came from and whether it was tested or running in production;
- produces a normal, standard Git merge commit with provenance trailers.

## AI models used in the product

| Model | How we run it | What it does in the product | Why we chose it |
|---|---|---|---|
| `qwen2.5-coder:14b` (default) and `qwen2.5-coder:7b` (for 8 GB laptops); both open-weight, Apache-2.0 | Locally via Ollama, from `provgit/ai.py`; choose with `PROVGIT_MODEL`. On our 8 GB Apple M3 the 14b model did not answer within 300 s, so our recorded runs use 7b (`examples/05_ai_explain`) | 1. `provgit explain`: for each predicted conflict, explains both sides in plain English, why they conflict and the risk of each choice, using the conflicting lines, the commit messages and each commit's diff of that file. 2. `provgit explain`: drafts a combined resolution, clearly labelled "AI draft, not tested". It is shown, never applied automatically. 3. `provgit track --ai`: for links where the two commits' code differs (a backport edited during conflict resolution, or two commits linked only by a PR number), judges whether they are the same logical change, and warns when it doubts the link. | Code-specialised model that reads diffs and Git conflicts well, open-weight, and runs offline on our laptops, so source code never leaves the machine. |

The core verdicts (which commit a conflict side came from, and whether it was tested) are computed with deterministic Git logic (`git patch-id`, `git merge-tree`, `git blame`, git notes), so the tool still works, and our CI tests still pass, when no model is running. The model only adds explanations, drafts and notes on top: it can never change a trust level, a link or a recommendation.

How the model is used safely: only a local Ollama is used unless explicitly allowed; proxy settings are ignored; repository text is passed to the model as untrusted data; answers are validated, size-capped and cleaned before display; the model's text cannot show trust levels, so it cannot imitate provgit's verdict.

No closed-model APIs are used in the product.

## AI coding assistants used during development
- Claude: used to speed up writing the code, tests and documentation within the hackathon time limit. Every change was reviewed and tested by the team.