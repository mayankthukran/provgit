#!/usr/bin/env bash
# Example 01: a hotfix on PROD is cherry-picked (backported) to UAT; provgit shows that both
# commits have the same patch-id and that the backport names its source.
# Run from anywhere: bash examples/01_cherry_pick_tracking/input.sh
# It works in its own folder (ex01-demo, git-ignored) and never touches the provgit repo.
set -euo pipefail
cd "$(dirname "$0")/../.."
source examples/_run.sh
export GIT_AUTHOR_NAME=demo GIT_AUTHOR_EMAIL=demo@example.com
export GIT_COMMITTER_NAME=demo GIT_COMMITTER_EMAIL=demo@example.com
at() { export GIT_AUTHOR_DATE="@$1 +0000" GIT_COMMITTER_DATE="@$1 +0000"; }

rm -rf ex01-demo && mkdir ex01-demo && cd ex01-demo
git init -q -b PROD
at 1790000060
printf 'TIMEOUT = 10\nRETRIES = 3\n' > settings.py && git add . && git commit -qm "Initial settings"
git branch UAT
at 1790000120
printf 'TIMEOUT = 30\nRETRIES = 3\n' > settings.py && git commit -qam "Hotfix #812: raise timeout to stop prod outage"
HOTFIX=$(git rev-parse HEAD)
git checkout -q UAT
at 1790000180
git cherry-pick -x "$HOTFIX" > /dev/null
BACKPORT=$(git rev-parse HEAD)

run "provgit show $HOTFIX      # the hotfix on PROD"
run "provgit show $BACKPORT      # the backport on UAT: new SHA, same patch_id, names its source"
