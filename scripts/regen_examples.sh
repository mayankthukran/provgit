#!/usr/bin/env bash
# Regenerate the outputs of the examples that need no network and no model (01-04, 06).
# Run after any code change during the freeze, then commit the changed output.txt files.
# Safety: stops if an example commits to, or changes tracked files of, the provgit repo.
set -euo pipefail
cd "$(dirname "$0")/.."
command -v provgit > /dev/null || { echo "provgit is not on PATH (activate the venv)"; exit 1; }

EXAMPLES="01_cherry_pick_tracking 02_track_slide_scenario 03_predict_conflict 04_merge_with_trailers 06_sync_between_clones"
tracked_changes() { git status --porcelain --untracked-files=no | grep -vE '^ M examples/[^/]+/output\.txt$' || true; }
head0=$(git rev-parse HEAD)
changes0=$(tracked_changes)

for d in $EXAMPLES; do
  bash "examples/$d/input.sh" > "examples/$d/output.txt" 2>&1
  if [ "$(git rev-parse HEAD)" != "$head0" ]; then
    echo "STOP: example $d made a commit in the provgit repo"; exit 1
  fi
  if [ "$(tracked_changes)" != "$changes0" ]; then
    echo "STOP: example $d changed tracked files in the provgit repo"; exit 1
  fi
  echo "regenerated examples/$d/output.txt"
done

rm -rf demo-repo sync-demo ex01-demo
if grep -rnE '/Users/|/home/|/private/|/var/folders/' examples/; then
  echo "STOP: absolute path in examples/"; exit 1
fi
echo "examples regenerated and clean"
