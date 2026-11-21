import json
import subprocess
import sys
from pathlib import Path
HERE = Path(__file__).resolve().parents[1]


def test_cli_generates_two_cases(tmp_path):
    # build a tiny 2-case plan from the real generator
    subprocess.run([sys.executable, str(HERE / "generate_sampling_plan.py"),
                    "--seed", "42", "--output-dir", str(tmp_path)],
                   check=True, cwd=str(HERE))
    r = subprocess.run(
        [sys.executable, str(HERE / "generate_cfdproj.py"),
         "--plan", str(tmp_path / "meta" / "sampling_plan.json"),
         "--output-dir", str(tmp_path), "--cases", "case_0000,case_0499"],
        capture_output=True, text=True, cwd=str(HERE))
    assert r.returncode == 0, r.stderr
    p = json.loads((tmp_path / "case_0000" / "GTC_dataset_0000.cfdproj").read_text())
    assert p["meta"]["casePath"].endswith("case_0000")
    assert p["schemaVersion"] == 4
    assert (tmp_path / "case_0499" / "GTC_dataset_0499.cfdproj").exists()
