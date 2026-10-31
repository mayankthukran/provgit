#!/usr/bin/env bash
# Example 04: resolve the DEV -> UAT conflict with testing provenance and write a standard
# Git merge commit with Provgit-* trailers. Run from anywhere:
#   bash examples/04_merge_with_trailers/input.sh
set -euo pipefail
cd "$(dirname "$0")/../.."
source examples/_run.sh
scripts/make_demo_repo.sh demo-repo > /dev/null
provgit import-ci demo-repo/ci_results.json -C demo-repo > /dev/null
# Fixed identity and time, so the merge commit has the same SHA on every machine.
export GIT_AUTHOR_NAME=demo GIT_AUTHOR_EMAIL=demo@example.com
export GIT_COMMITTER_NAME=demo GIT_COMMITTER_EMAIL=demo@example.com
export GIT_AUTHOR_DATE="@1790010000 +0000" GIT_COMMITTER_DATE="@1790010000 +0000"
git -C demo-repo checkout -q UAT                 # merge needs the target branch checked out
run "provgit merge DEV --into UAT --prod PROD --prefer-tested -C demo-repo"
run "git -C demo-repo log --graph --oneline -4             # a real merge commit with two parents"
run "git -C demo-repo log -1 --format='%(trailers)'        # standard trailers, readable by any git"
run "cat demo-repo/settings.py                             # the PROD_VERIFIED line was kept"
