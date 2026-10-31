#!/usr/bin/env bash
# Input for example 03: predict the DEV -> UAT conflict from the Problem 1 slide,
# before and after loading CI results. Run from the repo root.
# Nothing in demo-repo changes: predict uses a dry-run merge (git merge-tree).
scripts/make_demo_repo.sh demo-repo > /dev/null
provgit predict DEV --into UAT --prod PROD -C demo-repo; echo "exit code: $?"   # no metadata yet
provgit import-ci demo-repo/ci_results.json -C demo-repo
provgit predict DEV --into UAT --prod PROD -C demo-repo; echo "exit code: $?"   # with CI results
