#!/usr/bin/env bash
# Input for example 02: the PROD / UAT / DEV scenario from the Problem 1 slide.
set -e
scripts/make_demo_repo.sh demo-repo
provgit track PROD UAT DEV --prod PROD -C demo-repo          # before: no metadata in this clone
provgit import-ci demo-repo/ci_results.json -C demo-repo     # the hotfix passed CI on PROD
provgit track PROD UAT DEV --prod PROD -C demo-repo          # after
