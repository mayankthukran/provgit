#!/usr/bin/env bash
# Input for example 06: share testing provenance between two clones with `provgit sync`.
# Run from the repo root. Everything is created in ./sync-demo (git-ignored).
set -euo pipefail
rm -rf sync-demo && mkdir sync-demo
scripts/make_demo_repo.sh sync-demo/origin-src > /dev/null
git clone -q --bare sync-demo/origin-src sync-demo/remote.git          # the shared server
for c in alice bob; do
  git clone -q sync-demo/remote.git sync-demo/$c
  git -C sync-demo/$c branch -q UAT origin/UAT && git -C sync-demo/$c branch -q DEV origin/DEV
  git -C sync-demo/$c config user.name "$c" && git -C sync-demo/$c config user.email "$c@example.com"
done
provgit import-ci sync-demo/origin-src/ci_results.json -C sync-demo/alice      # CI results land on alice's clone
provgit sync --push -C sync-demo/alice                                          # alice publishes them
provgit track PROD UAT DEV --prod PROD -C sync-demo/bob | grep -A2 Hotfix            # bob: no metadata yet
provgit sync -C sync-demo/bob                                                   # bob fetches + merges
provgit track PROD UAT DEV --prod PROD -C sync-demo/bob | grep -A2 Hotfix            # bob: verified
