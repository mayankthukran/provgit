"""The committed example outputs must match what the examples print today.

If this fails after a code change: run scripts/regen_examples.sh and commit the outputs.
Examples run in a copy under tmp_path, so the real repo is never touched.
Examples 05 (needs a model) and 07 (needs the network) are recorded runs and not checked here.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ["01_cherry_pick_tracking", "02_track_slide_scenario", "03_predict_conflict",
            "04_merge_with_trailers", "06_sync_between_clones"]


def _norm(text: str) -> list[str]:
    # git log --graph pads lines with trailing spaces; ignore trailing whitespace only.
    return [line.rstrip() for line in text.strip().splitlines()]


@pytest.mark.parametrize("name", EXAMPLES)
def test_example_output_is_current(name, tmp_path):
    work = tmp_path / "repo"
    shutil.copytree(ROOT / "scripts", work / "scripts")
    shutil.copytree(ROOT / "examples", work / "examples")
    p = subprocess.run(["bash", f"examples/{name}/input.sh"], cwd=work,
                       capture_output=True, text=True, timeout=120)
    got = _norm(p.stdout + p.stderr)
    want = _norm((ROOT / "examples" / name / "output.txt").read_text())
    hint = "run scripts/regen_examples.sh and commit the outputs"
    for i, (g, w) in enumerate(zip(got, want), 1):
        assert g == w, f"{name} line {i} differs:\n  got:  {g}\n  want: {w}\n{hint}"
    assert len(got) == len(want), f"{name}: {len(got)} lines now vs {len(want)} committed; {hint}"
