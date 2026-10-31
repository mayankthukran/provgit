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

DIR="${1:-demo-repo}"
rm -rf "$DIR"
mkdir -p "$DIR"
cd "$DIR"

git init -q -b PROD
git config user.name "demo"
git config user.email "demo@example.com"

commit() { git add -A && git commit -q -m "$1"; }

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
git cherry-pick -x "$HOTFIX" > /dev/null

# Testing provenance: only the PROD hotfix has a CI record.
cat > ci_results.json << EOF
{
  "$HOTFIX": {"ci": "passed", "env": "prod", "url": "https://ci.example.com/runs/812"}
}
EOF
git checkout -q PROD

echo "Demo repo ready in $(pwd)"
echo "Branches: PROD UAT DEV.  CI results to import: $(pwd)/ci_results.json"
