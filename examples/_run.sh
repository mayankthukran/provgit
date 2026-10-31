# Sourced by examples/*/input.sh.
#
# `run "cmd"` prints the command like a terminal session, runs it, and shows a non-zero exit
# code instead of stopping (predict, explain and merge exit 1 when there are conflicts).
# Setup steps are written normally, outside `run`, so a failing setup step still stops the
# script (the examples use `set -euo pipefail`).
#
# Quoting rules:
#   - always use double quotes: run "git log -1 --format='%(trailers)'"
#   - write $ as \$ when it must reach eval unexpanded
#   - loops, clones and other setup never go through `run`
run() {
  printf '$ %s\n' "$1"
  local rc=0
  eval "$1" || rc=$?
  if [ "$rc" -ne 0 ]; then echo "(exit code $rc)"; fi
  echo
  return 0
}
