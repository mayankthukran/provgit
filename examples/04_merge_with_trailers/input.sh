#!/usr/bin/env bash
# Input for example 04: resolve the DEV -> UAT conflict from the Problem 1 slide with
# testing provenance, and write a standard Git merge commit with Provgit-* trailers.
# Run from the repo root.
scripts/make_demo_repo.sh demo-repo > /dev/null
provgit import-ci demo-repo/ci_results.json -C demo-repo
git -C demo-repo checkout -q UAT                     # merge needs the target branch checked out
provgit merge DEV --into UAT --prod PROD --prefer-tested -C demo-repo; echo "exit code: $?"
git -C demo-repo log --graph --oneline -4            # a real merge commit with two parents
git -C demo-repo log -1 --format='%(trailers)'       # standard trailers, readable by any git
cat demo-repo/settings.py                            # the PROD_VERIFIED line was kept
