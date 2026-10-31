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
| `qwen2.5-coder:14b` (open-weight, Apache-2.0) | Locally via Ollama (`http://localhost:11434`) | 1. Explains each side of a merge conflict in plain English, using the diff, commit messages and test provenance. 2. Judges whether two diffs are the same logical change when a backport was modified during conflict resolution (fuzzy matching). 3. Drafts a combined resolution, clearly labelled "AI suggestion, untested". | Code-specialised model that reads diffs and Git conflicts well, open-weight, and runs offline on our laptops. |

The core verdicts (which commit a conflict side came from, and whether it was tested) are computed with deterministic Git logic (`git patch-id`, `git cherry`, `git merge-tree`, `git blame`), so the tool still works, and our CI tests still pass, when no model is running. The model adds explanation and fuzzy-match judgement on top.

No closed-model APIs are used in the product.

## AI coding assistants used during development
None
