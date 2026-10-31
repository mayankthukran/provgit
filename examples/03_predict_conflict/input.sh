#!/usr/bin/env bash
# Example 03: predict the DEV -> UAT conflict before merging, before and after loading CI
# results. Nothing in demo-repo changes: predict is a dry-run merge (git merge-tree).
# Run from anywhere: bash examples/03_predict_conflict/input.sh
set -euo pipefail
cd "$(dirname "$0")/../.."
source examples/_run.sh
scripts/make_demo_repo.sh demo-repo > /dev/null
run "provgit predict DEV --into UAT --prod PROD -C demo-repo     # before: no metadata"
run "provgit import-ci demo-repo/ci_results.json -C demo-repo"
run "provgit predict DEV --into UAT --prod PROD -C demo-repo     # after"
