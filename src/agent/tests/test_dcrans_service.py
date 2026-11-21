"""Contract and functional tests for unified L0/L1 steady & BDF1 transient dcRANS services."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import agent.api as api
from agent.api import (
    CostLedger,
    Fidelity,
    evaluate,
    evaluate_dcrans_l0,
    evaluate_dcrans_l1,
    invert_dcrans,
    read_result,
    solve_dcrans,
    verify_dcrans_patches,
)

REPO = Path(__file__).resolve().parents[3]
SRC = REPO / "src"
SINGLE_RACK = REPO / "case" / "SingleRack" / "SingleRack.cfdproj"


def _write_two_cell_hex_polymesh(case_dir: Path) -> None:
    """Create a valid 2-cell OpenFOAM polyMesh with 6 named boundary patches."""
    pm = case_dir / "constant" / "polyMesh"
    pm.mkdir(parents=True, exist_ok=True)
    header = (
        "FoamFile\n{\n    version     2.0;\n    format      ascii;\n"
        "    class       %(cls)s;\n    object      %(obj)s;\n}\n"
    )
    points = [
        (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (2.0, 0.0, 0.0),
        (0.0, 1.0, 0.0), (1.0, 1.0, 0.0), (2.0, 1.0, 0.0),
        (0.0, 0.0, 1.0), (1.0, 0.0, 1.0), (2.0, 0.0, 1.0),
        (0.0, 1.0, 1.0), (1.0, 1.0, 1.0), (2.0, 1.0, 1.0),
    ]
    # Internal face 0: between cell 0 and cell 1 at x=1 (pts 1, 4, 10, 7)
    # Boundary faces grouped cleanly by patch:
    # inlet_x0 (1), outlet_x2 (1), south_y0 (2), north_y1 (2), bottom_z0 (2), top_z1 (2)
    faces = [
        (1, 4, 10, 7),    # 0 internal
        (0, 6, 9, 3),     # 1 inlet_x0 (cell 0)
        (2, 5, 11, 8),    # 2 outlet_x2 (cell 1)
        (0, 1, 7, 6),     # 3 south_y0 (cell 0)
        (1, 2, 8, 7),     # 4 south_y0 (cell 1)
        (3, 9, 10, 4),    # 5 north_y1 (cell 0)
        (4, 10, 11, 5),   # 6 north_y1 (cell 1)
        (0, 3, 4, 1),     # 7 bottom_z0 (cell 0)
        (1, 4, 5, 2),     # 8 bottom_z0 (cell 1)
        (6, 7, 10, 9),    # 9 top_z1 (cell 0)
        (7, 8, 11, 10),   # 10 top_z1 (cell 1)
    ]
    owner = [0, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1]
    neighbour = [1]

    (pm / "points").write_text(
        header % {"cls": "vectorField", "obj": "points"}
        + f"\n{len(points)}\n(\n" + "\n".join(f"({x} {y} {z})" for x, y, z in points) + "\n)\n",
        encoding="utf-8",
    )
    (pm / "faces").write_text(
        header % {"cls": "faceList", "obj": "faces"}
        + f"\n{len(faces)}\n(\n" + "\n".join("4(" + " ".join(map(str, f)) + ")" for f in faces) + "\n)\n",
        encoding="utf-8",
    )
    (pm / "owner").write_text(
        header % {"cls": "labelList", "obj": "owner"}
        + f"\n{len(owner)}\n(\n" + "\n".join(map(str, owner)) + "\n)\n",
        encoding="utf-8",
    )
    (pm / "neighbour").write_text(
        header % {"cls": "labelList", "obj": "neighbour"}
        + f"\n{len(neighbour)}\n(\n" + "\n".join(map(str, neighbour)) + "\n)\n",
        encoding="utf-8",
    )
    patches = [
        ("inlet_x0", "patch", 1, 1),
        ("outlet_x2", "patch", 1, 2),
        ("south_y0", "wall", 2, 3),
        ("north_y1", "wall", 2, 5),
        ("bottom_z0", "wall", 2, 7),
        ("top_z1", "wall", 2, 9),
    ]
    b_entries = "\n".join(
        f"    {nm}\n    {{\n        type            {tp};\n        nFaces          {nf};\n        startFace       {sf};\n    }}"
        for nm, tp, nf, sf in patches
    )
    (pm / "boundary").write_text(
        header % {"cls": "polyBoundaryMesh", "obj": "boundary"} + f"\n{len(patches)}\n(\n{b_entries}\n)\n",
        encoding="utf-8",
    )


def test_verify_dcrans_patches_pass_and_dropped_failure(tmp_path):
    case = tmp_path / "hex2"
    _write_two_cell_hex_polymesh(case)
    ok = verify_dcrans_patches(
        case,
        agglomerate_box_m=2.0,
        expected_patches=["inlet_x0", "outlet_x2", "south_y0", "north_y1", "bottom_z0", "top_z1"],
        check_agglomeration=True,
        run_acceptance=False,
        output_root=tmp_path / "runs",
    )
    assert ok["status"] == "succeeded"
    assert ok["dropped_patches"] == []
    agg = ok["metrics"]["agglomeration"]
    assert agg["ok"] is True
    assert agg["n_patches"] == 6
    assert agg["fine_cells"] == 2
    assert agg["coarse_cells"] == 1

    bad = verify_dcrans_patches(
        case,
        expected_patches=["inlet_x0", "missing_baffle_patch"],
        check_agglomeration=False,
        run_acceptance=False,
        output_root=tmp_path / "runs",
    )
    assert bad["status"] == "failed"
    assert "missing_baffle_patch" in bad["dropped_patches"]


def _l0_case():
    """A prepared L0 case directory (dcCart coarsenLevel L0) of Base201 in this checkout, or None."""
    for run in sorted((REPO / "case" / "Base201_L0" / "log").glob("*_Base201_L0"), reverse=True):
        if (run / "processor0" / "0" / "T").is_file() and (run / "system" / "controlDict").is_file():
            return run
    return None


def _needs_l0_gpu():
    import importlib.util
    if _l0_case() is None:
        pytest.skip("no prepared Base201_L0 run in this checkout")
    if importlib.util.find_spec("torch") is None:
        pytest.skip("torch is not installed")
    import torch
    if not torch.cuda.is_available():
        pytest.skip("the zonal network's Warp kernels need CUDA")


def test_l0_steady_and_transient_solve_and_evaluate(tmp_path):
    _needs_l0_gpu()
    case = _l0_case()
    res_s = solve_dcrans(case, level="L0", mode="steady", output_root=tmp_path / "runs")
    assert res_s["status"] == "succeeded", res_s.get("failure")
    assert res_s["metrics"]["converged"] is True
    assert res_s["metrics"]["model"] == "zonal network of the L0 mesh"
    assert any(k.startswith("return_T:") for k in res_s["metrics"]["observations"])
    rv = read_result(res_s["runDir"])
    assert rv.succeeded and rv.kind == "dcrans"

    ev = evaluate_dcrans_l0(case, mode="transient", n_steps=4, dt_s=120.0, output_root=tmp_path / "runs")
    assert ev.succeeded
    assert ev.metrics["n_steps"] == 3 and len(ev.metrics["trajectory_observations"]) == 4
    # constant inputs: the steady start is the equilibrium, the window stays there
    first, last = ev.metrics["trajectory_observations"][0], ev.metrics["trajectory_observations"][-1]
    assert max(abs(first[k] - last[k]) for k in first) < 1e-6

    ledger = CostLedger()
    rec = evaluate(case, Fidelity.DCRANS_L0, ledger=ledger, output_root=tmp_path / "runs")
    assert rec.status == "succeeded" and rec.validity == "valid"
    assert len(ledger.attempts) == 1


def test_l0_refuses_a_project_that_is_not_l0(tmp_path):
    with pytest.raises(ValueError, match="not an L0 project"):
        solve_dcrans(SINGLE_RACK, level="L0", mode="steady", output_root=tmp_path / "runs")


def test_l0_steady_and_transient_invert_with_fd_contract(tmp_path):
    _needs_l0_gpu()
    case = _l0_case()
    # parameters: P_scale, dTsrv, Q100, bT, lsBlank (+ ltau in transient)
    truth = [1.35, 12.0, 1.0, 0.8, -1.5]
    prior = [1.5, 12.0, 1.0, 0.0, -1.5]
    fwd_s = solve_dcrans(case, level="L0", mode="steady", theta=truth, output_root=tmp_path / "runs")
    assert fwd_s["status"] == "succeeded", fwd_s.get("failure")
    inv_s = invert_dcrans(case, fwd_s["metrics"]["observations"], level="L0", mode="steady", free_indices=[0, 3],
                          prior=prior, prior_sigma=[100.0] * 5, obs_sigma=0.05, max_iters=15, tol=1e-10,
                          fd_check=True, output_root=tmp_path / "runs")
    assert inv_s["status"] == "succeeded", inv_s.get("failure")
    assert inv_s["post_rmse"] < 1e-4
    assert abs(inv_s["theta_post"][0] - 1.35) < 1e-3 and abs(inv_s["theta_post"][3] - 0.8) < 1e-3
    assert inv_s["contract"]["tangent_vs_difference"] < 1e-3

    # transient: a steady start, then the units slow to 85 %: the response carries the thermal capacity.  Staged
    # as in use: the heat scale is the steady posterior, the window fits the blanking and the capacity.
    import sys as _sys
    _sys.path.insert(0, str(REPO / "src" / "backend" / "aircooling" / "gpu_solver"))
    from Parse_mesh import dctwin_bc
    names = list(dctwin_bc.acu_patches(case))
    slow = {"acu_speed": {n: 85.0 for n in names}}
    snaps = [{"t_s": 0.0}, {"t_s": 120.0, **slow}, {"t_s": 240.0, **slow}, {"t_s": 360.0, **slow}]
    truth_t = truth + [0.3]
    fwd_t = solve_dcrans(case, level="L0", mode="transient", theta=truth_t, snapshots=snaps, dt_s=120.0,
                         output_root=tmp_path / "runs")
    assert fwd_t["status"] == "succeeded", fwd_t.get("failure")
    inv_t = invert_dcrans(case, fwd_t["metrics"]["trajectory_observations"], level="L0", mode="transient",
                          snapshots=snaps, dt_s=120.0, free_indices=[3, 5], prior=[inv_s["theta_post"][0]] + prior[1:] + [0.0],
                          prior_sigma=[100.0] * 6, obs_sigma=0.05, max_iters=15, tol=1e-10, fd_check=True,
                          output_root=tmp_path / "runs")
    assert inv_t["status"] == "succeeded", inv_t.get("failure")
    assert inv_t["post_rmse"] < 1e-4
    assert abs(inv_t["theta_post"][3] - 0.8) < 1e-3 and abs(inv_t["theta_post"][5] - 0.3) < 1e-2
    assert inv_t["contract"]["tangent_vs_difference"] < 1e-3


def test_evaluate_dcrans_l1_guard_and_cli_help(tmp_path):
    with pytest.raises(FileNotFoundError):
        evaluate_dcrans_l1(tmp_path / "does_not_exist.cfdproj", output_root=tmp_path / "runs")
    for sub in ("dcrans-audit", "dcrans-solve", "dcrans-invert"):
        done = subprocess.run([sys.executable, "-m", "agent", sub, "--help"], cwd=SRC, capture_output=True, text=True, check=True)
        assert sub in done.stdout or "usage:" in done.stdout.lower()
