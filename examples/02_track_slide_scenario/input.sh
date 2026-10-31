#!/usr/bin/env bash
# Example 02: follow every change across PROD / UAT / DEV (the Problem 1 slide), before and
# after loading CI results. Run from anywhere: bash examples/02_track_slide_scenario/input.sh
set -euo pipefail
cd "$(dirname "$0")/../.."
source examples/_run.sh
run "scripts/make_demo_repo.sh demo-repo"
run "provgit track PROD UAT DEV --prod PROD -C demo-repo        # before: no metadata in this clone"
run "provgit import-ci demo-repo/ci_results.json -C demo-repo   # the hotfix passed CI on PROD"
run "provgit track PROD UAT DEV --prod PROD -C demo-repo        # after"
