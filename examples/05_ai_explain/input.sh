#!/usr/bin/env bash
# Example 05: plain-English conflict explanation from a local open-weight model (Ollama).
# Needs Ollama with the model pulled:  ollama pull qwen2.5-coder:7b   (14b on 16 GB+ machines)
# Run from anywhere: PROVGIT_MODEL=qwen2.5-coder:7b bash examples/05_ai_explain/input.sh
set -euo pipefail
cd "$(dirname "$0")/../.."
source examples/_run.sh
scripts/make_demo_repo.sh demo-repo > /dev/null
provgit import-ci demo-repo/ci_results.json -C demo-repo > /dev/null
run "provgit ai-check"
run "provgit explain DEV --into UAT --prod PROD -C demo-repo"
