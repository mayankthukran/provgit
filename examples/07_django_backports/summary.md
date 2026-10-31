# Django main vs stable/6.1.x: `provgit track` on real backports

Run: `provgit track main stable/6.1.x --limit 3000 --json` · 3.09 s, 93 MB peak memory, Apple M3 8 GB

| Measure | Count |
|---|---|
| Declared backports on stable/6.1.x (`Backport of …` trailer, in the commits read) | 237 |
| … linked to their main commit | **237 (100%)** |
| … not linked: source commit outside this shallow clone | 0 |
| … not linked: other reason | 0 |
| Links by identical patch-id (exact copy) | 225 |
| **Partial: backport code edited during conflict resolution** (edited lines never tested on main) | **10** |
| Same changed lines, different surrounding code (not partial) | 6 |
| Weak groups (joined only by a ticket number) | 0, largest has 0 commits |
| Commits shared by both branches (history before the branch point, same SHA) | 77 |
| Largest change (commits) — more than 2 would mean separate fixes were chained | 2 |

## Checked by hand

Each pair was opened with `git show` and compared by hand. **Result: 8 of 8 correct.** No ticket-number-only links were needed on this branch: every backport carries a `Backport of …` trailer.

| Kind | stable/6.1.x commit | main commit | Title | Same fix? |
|---|---|---|---|---|
| exact | [8485c6ac38](https://github.com/django/django/commit/8485c6ac386c6d41cf46001851c19360275c7cc9) | [817df1c4d6](https://github.com/django/django/commit/817df1c4d6d38f416460d7d9354d7deab9d35139) | [6.1.x] Fixed #37463 -- Fixed attrs mutation in legend_tag(). | ✅ same fix, identical diff |
| exact | [317287d6cd](https://github.com/django/django/commit/317287d6cdf7ed463dccb4f08083329f0c69e77a) | [c61620099c](https://github.com/django/django/commit/c61620099cfa1874c12771a4637e405f59c287a8) | [6.1.x] Fixed #37349 -- Documented that JSONField doesn't guarantee pr | ✅ same fix, identical diff |
| partial | [6feba4e108](https://github.com/django/django/commit/6feba4e108a79ba1316083a90261bda53c285f96) | [c6ebf51871](https://github.com/django/django/commit/c6ebf51871fa149e88f39ea44aaf700590366607) | [6.1.x] Fixed #36664 -- Doc'd Python 3.15 compatibility. | ✅ same fix; stable version edited (different CI workflow files) |
| partial | [4e77ef1e69](https://github.com/django/django/commit/4e77ef1e69c94780006b82795aa7db101996c3af) | [ebcb13b327](https://github.com/django/django/commit/ebcb13b327301f28cbc6cd5e4988a719f00575aa) | [6.1.x] Fixed CVE-2026-87890 -- Blocked bytes in spatial lookups. | ✅ same CVE fix; stable test edited (`City`/`point__contained` vs `State`/`poly__intersects`) |
| partial | [1fc6109d42](https://github.com/django/django/commit/1fc6109d4260c68fef6dd0b7f7b453c3f9f2d470) | [026b005f3d](https://github.com/django/django/commit/026b005f3dc43a98557ce5a546b5e4938d06fed1) | [6.1.x] Refs #37293 -- Updated deprecation docs for calendar-based war | ✅ same fix; stable docs edited (import line differs) |
| partial | [b6cb9c6452](https://github.com/django/django/commit/b6cb9c645212a260d96f0ef23a02142ef56cdaed) | [0d0e7f1503](https://github.com/django/django/commit/0d0e7f15038360c158b6d22c679b47b21557fa1f) | [6.1.x] Refs #37293 -- Aliased RemovedInDjango70Warning to RemovedInDj | ✅ same fix; stable import written differently |
| same lines | [9f52f9af0e](https://github.com/django/django/commit/9f52f9af0e841c14e1ee52534ff1d67cadd099fa) | [2fff3e9f49](https://github.com/django/django/commit/2fff3e9f493a9c85fb8a2294358c6751575ee4bf) | [6.1.x] Fixed #37431 -- Added support for serializing frozendicts in m | ✅ same fix; identical changed lines, different surrounding code |
| same lines | [8590dfdb41](https://github.com/django/django/commit/8590dfdb4132ab56a9a26c13094e01f2d2a74ee6) | [50eef95591](https://github.com/django/django/commit/50eef95591b1e5d8c47b2e8c96066cb0c516753a) | [6.1.x] Fixed #37291 -- Prevented tuple lookups from hanging on F() ex | ✅ same fix; identical changed lines, different surrounding code |

## What this run fixed in provgit

Running on real Django history found two bugs, both fixed in this PR:

1. **Ticket numbers chained separate fixes together.** Several `Refs #35514` backports became one 11-commit "change", so a CI pass on one would have counted for the others. Ticket numbers now only link commits that have no stronger partner (largest change is now 2 commits).
2. **"Partial" false positives.** 6 of 16 backports flagged as edited had identical changed lines; only the unchanged lines around them differed. They are now reported as "same changed lines, different surrounding code" and not as partial.

Known limitation: ticket-number links can chain unrelated `Refs #N` commits; they are reported separately and never counted as partial backports.

Data: Django (BSD-3-Clause), https://github.com/django/django
