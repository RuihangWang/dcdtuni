"""Native RED: a declared source sheet cannot acquire a new tangent footprint."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest


_GENERATE = Path(__file__).parent / "native" / "dccart_source_support" / "generate_case.py"
_SOURCE_META = '''
dcdtuniSourceSurfaces
{
    wall.stl
    {
        wall
        {
            sourceObjectUid "fixture/wall";
            sourceFaceUid "fixture/wall/source-sheet";
            representation zeroThicknessSheet;
        }
    }
    floor.stl
    {
        floor
        {
            sourceObjectUid "fixture/floor";
            sourceFaceUid "fixture/floor/source-sheet";
            representation zeroThicknessSheet;
        }
    }
}
'''


def _run_case(tmp_path: Path, mesher: str, *, control: bool) -> subprocess.CompletedProcess[str]:
    name = "control" if control else "near-grid"
    case = tmp_path / name
    args = [sys.executable, str(_GENERATE), str(case)]
    if control:
        args.append("--control")
    subprocess.run(args, check=True)
    dictionary = case / "system" / "snappyHexMeshDict"
    dictionary.write_text(dictionary.read_text() + _SOURCE_META)
    env = os.environ.copy()
    env["CUDA_VISIBLE_DEVICES"] = ""
    return subprocess.run(
        [mesher, "-overwrite"], cwd=case, env=env,
        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        timeout=90, check=False,
    )


def test_uid_source_sheet_rejects_tangent_weld_while_far_grid_remains_valid(tmp_path: Path):
    mesher = os.environ.get("DCDT_DCCART_MESHER")
    if not mesher:
        pytest.skip("set DCDT_DCCART_MESHER to the source-matched native binary")
    control = _run_case(tmp_path, mesher, control=True)
    assert control.returncode == 0, control.stdout[-3000:]
    near = _run_case(tmp_path, mesher, control=False)
    assert near.returncode != 0, near.stdout[-3000:]
    assert "source support tangent displacement" in near.stdout
    assert "fixture/floor/source-sheet" in near.stdout or "fixture/wall/source-sheet" in near.stdout


def test_uid_sheet_allows_pure_normal_grid_alignment(tmp_path: Path):
    mesher = os.environ.get("DCDT_DCCART_MESHER")
    if not mesher:
        pytest.skip("set DCDT_DCCART_MESHER to the source-matched native binary")
    case = tmp_path / "normal-only"
    subprocess.run([sys.executable, str(_GENERATE), str(case), "--control"], check=True)
    wall = case / "constant" / "triSurface" / "wall.stl"
    old = wall.read_text()
    changed = old.replace("vertex 0.100000000 ", "vertex 0.124000000 ")
    assert changed != old
    wall.write_text(changed)
    dictionary = case / "system" / "snappyHexMeshDict"
    dictionary.write_text(dictionary.read_text() + _SOURCE_META)
    env = os.environ.copy()
    env["CUDA_VISIBLE_DEVICES"] = ""
    env["DCCART_WELD_DEBUG"] = "1"
    run = subprocess.run(
        [mesher, "-overwrite"], cwd=case, env=env,
        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        timeout=90, check=False,
    )
    assert "weld G1: axis 0" in run.stdout
    assert run.returncode == 0, run.stdout[-3000:]
