#!/usr/bin/env bash
# Builds the exact scenario from the Problem 1 slide in a fresh repo:
#
#   PROD  o----------[hotfix #812]          CI passed, running in production
#                         | backport (cherry-pick -x)
#   UAT   o----------[hotfix #812']----?    promote DEV -> UAT conflicts on settings.py
#                                       ^
#   DEV   o----o---------[PR #845]------'   new change, CI never ran
#
# Usage: scripts/make_demo_repo.sh [target-dir]   (default: ./demo-repo)
set -euo pipefail

# Fixed identity and dates: the same SHAs on every machine, so example outputs are reproducible.
export GIT_AUTHOR_NAME=demo GIT_AUTHOR_EMAIL=demo@example.com
export GIT_COMMITTER_NAME=demo GIT_COMMITTER_EMAIL=demo@example.com
T=1790000000                                   # fixed start time; each commit is 60 s later
tick() { T=$((T + 60)); export GIT_AUTHOR_DATE="@$T +0000" GIT_COMMITTER_DATE="@$T +0000"; }

DIR="${1:-demo-repo}"
rm -rf "$DIR"
mkdir -p "$DIR"
cd "$DIR"

git init -q -b PROD
git config user.name "demo"
git config user.email "demo@example.com"

commit() { tick; git add -A && git commit -q -m "$1"; }

cat > settings.py << 'EOF'
# Service settings
TIMEOUT = 10
RETRIES = 3
LOG_LEVEL = "info"
EOF
echo "print('app v1')" > app.py
commit "Initial service settings"

git branch UAT
git branch DEV

# DEV: normal work, then PR #845 which changes the same TIMEOUT line (never tested)
git checkout -q DEV
echo "print('app v1 with retries')" > app.py
commit "PR #830: log retry attempts"
sed -i.bak 's/^TIMEOUT = 10$/TIMEOUT = 5  # shorter jobs/' settings.py && rm settings.py.bak
commit "PR #845: lower timeout for faster batch jobs"

# PROD: urgent hotfix on the same line, CI passed, deployed
git checkout -q PROD
sed -i.bak 's/^TIMEOUT = 10$/TIMEOUT = 30  # stop prod outage/' settings.py && rm settings.py.bak
commit "Hotfix #812: raise timeout to stop prod outage"
HOTFIX=$(git rev-parse HEAD)

# Backport the hotfix down to UAT (new SHA, -x adds the "cherry picked from" trailer)
git checkout -q UAT
tick
git cherry-pick -x "$HOTFIX" > /dev/null

# Testing provenance: only the PROD hotfix has a CI record.
cat > ci_results.json << EOF
{
  "$HOTFIX": {"ci": "passed", "env": "prod", "url": "https://ci.example.com/runs/812"}
}
EOF
git checkout -q PROD

echo "Demo repo ready in $DIR"
echo "Branches: PROD UAT DEV.  CI results to import: $DIR/ci_results.json"
