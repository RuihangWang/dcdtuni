"""S3 receipt: Shinaide_Liquid with refined pipes, sparse vs dense Newton/adjoint.

Steady: the equilibrium plus implicit-adjoint gradients (the steady initial
guess is already converged, so this arm times the convergence check and the
adjoint). Transient: a 20 % heat step marched with backward Euler, where every
step's Newton iterates.

Each run is a subprocess, so its peak RSS is its own. Run from the repository
root (writes results.json beside this script):

    PYTHONPATH=src python docs/benchmarks/liquid-dae-sparse-2026-10-22/bench.py
"""
from __future__ import annotations

import json
import os
import resource
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
PROJECT = REPO / "case/Shinaide_Liquid/Shinaide_liquid.cfdproj"
THREADS = 4
PARAMETERS = ("heat_W", "pump_rpm_ratio", "valve_opening")
# (pipe cells, solver); dense past 4000 unknowns is a research comparison past the admission gate.
RUNS = [(2, "dense"), (2, "sparse"), (30, "dense"), (30, "sparse"), (60, "sparse"), (100, "sparse")]
TRANSIENT_RUNS = [(10, "dense"), (10, "sparse"), (60, "sparse")]
TRANSIENT_DURATION_S = 60.0


def refined(mode: str, cells: int, solver: str, **export) -> dict:
    import backend.api as api
    from backend.liquidcooling import liquidrun_dae_schema as schema

    liquidrun = api.export_liquidrun(str(PROJECT), mode=mode, **export)["liquidrun"]
    profiles = liquidrun["parameter_pack"].get("profiles", {}).values()
    for item in [*liquidrun["edges"], *profiles]:
        if item.get("type") == "pipe" and "n_cells" in item.get("params", {}):
            item["params"]["n_cells"] = cells
    liquidrun["newton"]["linear_solver"] = solver
    schema.DENSE_UNKNOWNS_LIMIT = 10 ** 9
    return liquidrun


def transient(cells: int, solver: str) -> dict:
    import tempfile
    import backend.api as api
    from backend.liquidcooling.dae_runtime import admit_dae_runtime_input

    liquidrun = refined("transient", cells, solver, duration_s=TRANSIENT_DURATION_S)
    unknowns = admit_dae_runtime_input(json.loads(json.dumps(liquidrun))).equation_graph.size
    with tempfile.TemporaryDirectory(dir=REPO / "_tmp") as run_dir:
        start = time.perf_counter()
        out = api.solve_liquid_transient(liquidrun, run_dir, heat_multiplier=1.2, device="cpu", threads=THREADS)
        seconds = time.perf_counter() - start
        result = json.loads((Path(out["liquidRunDir"]) / "result.json").read_text())
    return {
        "arm": "transient", "cells": cells, "solver": solver, "unknowns": unknowns, "samples": out["samples"],
        "dtS": liquidrun["time"]["dt_s"], "newtonIterations": result["epochs"],
        "seconds": round(seconds, 2), "peakRssMB": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024,
        "maxAbsEnergyBalanceW": out["maxAbsEnergyBalanceW"], "heatInputW": out["heatInputW"],
        "chipTemperatureK": out["chipTemperatureK"], "peakChipTemperatureK": out["peakChipTemperatureK"],
        "chipSeriesK": result["chip_temp_K"],
    }


def one(cells: int, solver: str) -> dict:
    import backend.api as api

    liquidrun = refined("steady", cells, solver)
    start = time.perf_counter()
    out = api.solve_liquid_steady(liquidrun, gradient_parameters=PARAMETERS, device="cpu", threads=THREADS)
    seconds = time.perf_counter() - start
    metrics = out["metrics"]
    unknowns = out["graph"]["unknowns"]
    return {
        "arm": "steady", "cells": cells, "solver": solver, "unknowns": unknowns,
        "newton": out.get("newton"),
        "omittedSolverPicks": "sparse" if unknowns > 4000 else "dense",
        "seconds": round(seconds, 2),
        "peakRssMB": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024,
        "energyBalanceW": metrics["energyBalanceW"], "heatInputW": metrics["heatInputW"],
        "maxChipTemperatureK": metrics["maxChipTemperatureK"],
        "pressurePa": {n["id"]: n["pressurePa"] for n in metrics["nodes"]},
        "massFlowKgS": {e["id"]: e["massFlowKgS"] for e in metrics["edges"]},
        "gradients": out["gradients"],
    }


def gradient_difference(a: dict, b: dict) -> float:
    """Largest |a - b| over every entry, relative to the largest entry of its (objective, parameter) block."""
    worst = 0.0
    for objective, by_parameter in a.items():
        for parameter, block in by_parameter.items():
            scale = max(abs(v) for v in block.values())
            other = b[objective][parameter]
            if scale < 1e-12:  # round-off only: both blocks must be zero
                assert max(abs(v) for v in other.values()) < 1e-12, (objective, parameter)
                continue
            worst = max(worst, *(abs(v - other[k]) / scale for k, v in block.items()))
    return worst


def main() -> None:
    if len(sys.argv) == 4:
        run = (transient if sys.argv[1] == "transient" else one)(int(sys.argv[2]), sys.argv[3])
        print(json.dumps(run))
        return
    env = {**os.environ, "OMP_NUM_THREADS": str(THREADS), "MKL_NUM_THREADS": str(THREADS)}
    load_before = os.getloadavg()
    results = []
    plan = [("steady", c, s) for c, s in RUNS] + [("transient", c, s) for c, s in TRANSIENT_RUNS]
    for arm, cells, solver in plan:
        done = subprocess.run([sys.executable, __file__, arm, str(cells), solver], env=env, cwd=REPO,
                              capture_output=True, text=True, check=True)
        results.append(json.loads(done.stdout.strip().splitlines()[-1]))
        print({k: results[-1][k] for k in ("arm", "cells", "solver", "unknowns", "seconds", "peakRssMB")}, flush=True)
    rows = []
    for run in [r for r in results if r["arm"] == "transient"]:
        row = {k: run[k] for k in ("arm", "cells", "solver", "unknowns", "samples", "dtS", "newtonIterations",
                                   "seconds", "peakRssMB", "maxAbsEnergyBalanceW", "heatInputW", "peakChipTemperatureK")}
        dense = next((d for d in results if d["arm"] == "transient" and d["cells"] == run["cells"]
                      and d["solver"] == "dense"), None)
        if run["solver"] == "sparse" and dense is not None:
            row["vsDense"] = {"maxChipSeriesDifferenceK": max(
                abs(a - b) for sa, sb in zip(dense["chipSeriesK"], run["chipSeriesK"]) for a, b in zip(sa, sb))}
        rows.append(row)
    for run in [r for r in results if r["arm"] == "steady"]:
        row = {k: run[k] for k in ("arm", "cells", "solver", "unknowns", "omittedSolverPicks", "newton", "seconds",
                                   "peakRssMB", "energyBalanceW", "heatInputW", "maxChipTemperatureK")}
        dense = next((d for d in results if d["arm"] == "steady" and d["cells"] == run["cells"]
                      and d["solver"] == "dense"), None)
        if run["solver"] == "sparse" and dense is not None:
            row["vsDense"] = {
                "maxPressureDifferencePa": max(abs(v - run["pressurePa"][k]) for k, v in dense["pressurePa"].items()),
                "maxMassFlowDifferenceKgS": max(abs(v - run["massFlowKgS"][k]) for k, v in dense["massFlowKgS"].items()),
                "maxGradientDifferenceRelative": gradient_difference(dense["gradients"], run["gradients"]),
            }
        rows.append(row)
    import platform
    import torch
    summary = {"project": str(PROJECT.relative_to(REPO)), "device": "cpu", "threads": THREADS,
               "gradientParameters": list(PARAMETERS), "torch": torch.__version__,
               "host": platform.node(), "cpuCount": os.cpu_count(),
               # a shared host: the 1-minute load average before and after the runs
               "loadAverage": {"before": round(load_before[0], 1), "after": round(os.getloadavg()[0], 1)},
               "runs": rows}
    (HERE / "results.json").write_text(json.dumps(summary, indent=2) + "\n")


if __name__ == "__main__":
    main()
