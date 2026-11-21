import json
import subprocess
import sys
from pathlib import Path
HERE = Path(__file__).resolve().parents[1]


def test_cli_writes_plan(tmp_path):
    out = tmp_path / "out"
    r = subprocess.run(
        [sys.executable, str(HERE / "generate_sampling_plan.py"),
         "--seed", "42", "--output-dir", str(out)],
        capture_output=True, text=True, cwd=str(HERE))
    assert r.returncode == 0, r.stderr
    plan = json.loads((out / "meta" / "sampling_plan.json").read_text())
    assert len(plan) == 500
    assert (out / "meta" / "server_types.json").exists()
