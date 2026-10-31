#!/usr/bin/env bash
# Input for example 05: plain-English conflict explanation from a local open-weight model.
# Needs Ollama running. Run from the repo root.
ollama pull qwen2.5-coder:7b              # or qwen2.5-coder:14b on machines with 16 GB+ RAM
ollama run qwen2.5-coder:7b "hi" > /dev/null   # warm up the model
export PROVGIT_MODEL=qwen2.5-coder:7b
provgit ai-check
rm -rf demo-repo && scripts/make_demo_repo.sh demo-repo > /dev/null
provgit import-ci demo-repo/ci_results.json -C demo-repo
time provgit explain DEV --into UAT --prod PROD -C demo-repo
