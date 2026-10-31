#!/usr/bin/env bash
# Input for example 07: provgit track on real Django history (main vs stable/6.1.x).
# Run from the folder that contains the provgit repo. Downloads ~51 MB.
set -euo pipefail
git ls-remote --heads https://github.com/django/django.git 'refs/heads/stable/*'   # newest: stable/6.1.x
git clone -q --single-branch --branch main --shallow-since="9 months ago" \
    https://github.com/django/django.git django-src
cd django-src
git fetch --quiet --shallow-since="6 months ago" origin stable/6.1.x:stable/6.1.x
git log stable/6.1.x --since='6 months ago' --grep='Backport of' --oneline | wc -l   # 237 on 2026-11-01
git log -p --no-merges -n 3000 main $(sed 's/^/^/' .git/shallow) | wc -c              # ~5 MB: no --exclude needed
/usr/bin/time -l provgit track main stable/6.1.x --limit 3000 --json -C . > ../django-track.json
provgit track main stable/6.1.x --limit 3000 -C . | head -120 > ../provgit/examples/07_django_backports/output.txt
cd ..
python provgit/scripts/summarize_track.py django-track.json -C django-src --main main \
    --stable stable/6.1.x --limit 3000 --examples 2,4,2,3 > provgit/examples/07_django_backports/summary.md
# then each "TODO" in the hand-check table was replaced after checking the pair with `git show`
