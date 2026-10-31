#!/usr/bin/env bash
# Example 06: share testing provenance between two clones with `provgit sync`.
# Run from anywhere: bash examples/06_sync_between_clones/input.sh
# Everything is created in ./sync-demo (git-ignored).
set -euo pipefail
cd "$(dirname "$0")/../.."
source examples/_run.sh
rm -rf sync-demo && mkdir sync-demo
scripts/make_demo_repo.sh sync-demo/origin-src > /dev/null
git clone -q --bare sync-demo/origin-src sync-demo/remote.git          # the shared server
for c in alice bob; do
  git clone -q sync-demo/remote.git "sync-demo/$c"
  git -C "sync-demo/$c" branch -q UAT origin/UAT
  git -C "sync-demo/$c" branch -q DEV origin/DEV
  git -C "sync-demo/$c" config user.name "$c"
  git -C "sync-demo/$c" config user.email "$c@example.com"
done
run "provgit import-ci sync-demo/origin-src/ci_results.json -C sync-demo/alice   # CI results reach alice"
run "provgit sync --push -C sync-demo/alice                                       # alice publishes them"
run "provgit track PROD UAT DEV --prod PROD -C sync-demo/bob | grep -A2 Hotfix    # bob: no metadata yet"
run "provgit sync -C sync-demo/bob                                                # bob fetches + merges"
run "provgit track PROD UAT DEV --prod PROD -C sync-demo/bob | grep -A2 Hotfix    # bob: verified"
