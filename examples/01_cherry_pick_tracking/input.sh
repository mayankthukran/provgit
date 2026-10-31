#!/usr/bin/env bash
# Input for example 01: a hotfix on PROD is cherry-picked (backported) to UAT.
# Run from an empty folder, then run the two provgit commands at the bottom.
set -e
git init -q -b PROD
printf 'TIMEOUT = 10\nRETRIES = 3\n' > settings.py
git add . && git commit -qm "Initial settings"
git branch UAT

printf 'TIMEOUT = 30\nRETRIES = 3\n' > settings.py
git commit -qam "Hotfix #812: raise timeout to stop prod outage"
HOTFIX=$(git rev-parse HEAD)

git checkout -q UAT
git cherry-pick -x "$HOTFIX" > /dev/null
BACKPORT=$(git rev-parse HEAD)

provgit show "$HOTFIX"
provgit show "$BACKPORT"
# Expected: different SHAs, same patch_id, and the backport links to the hotfix.
