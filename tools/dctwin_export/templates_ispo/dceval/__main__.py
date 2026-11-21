"""``python -m dceval evaluate <cfdproj> --case-dir <dir> [--solver openfoam] [--gpus 0,1] [--processes 8]``;
``python -m dceval check``: is every exported file as its export wrote it (exit 1 when not).

stdout carries exactly one line of JSON: a summary (status, failure, the path of ``result.json``, cell count,
convergence verdict, rack inlet summary), or the whole result with ``--full``. Exit code: 0 when the status is
``succeeded``, 1 when the run failed, 2 for invalid arguments.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

from dceval import SOLVERS, STAGES, check, evaluate, summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m dceval", description="Data-centre design evaluator")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("evaluate", help="mesh and solve one .cfdproj, print one JSON line")
    run.add_argument("cfdproj", help="the project file")
    run.add_argument("--case-dir", default=None, help="working directory of this evaluation "
                                                      "(default: the project file's directory)")
    run.add_argument("--solver", choices=SOLVERS, default="gpuNewton",
                     help="air solver (default: gpuNewton, the GPU solver; openfoam: the CPU reference)")
    run.add_argument("--gpus", default=None, help="cards of the gpuNewton solver: nvidia-smi indices or GPU-... "
                                                  "UUIDs, comma separated (default: any card)")
    run.add_argument("--processes", type=int, default=8, help="MPI ranks / mesh partitions (default: 8)")
    run.add_argument("--stages", default=",".join(STAGES), help="mesh,solve (default), mesh, or solve")
    run.add_argument("--mesh-engine", default=None, help="override the project's mesh engine")
    run.add_argument("--end-time", type=float, default=None, help="override the solve's end time / iterations")
    run.add_argument("--run-settings", default=None, help="JSON object {meshConfig, simulationConfig} of overrides")
    run.add_argument("--mesh-timeout", type=float, default=1800.0, help="hard limit of the mesh job, seconds")
    run.add_argument("--solve-timeout", type=float, default=4 * 3600.0, help="hard limit of the solve job, seconds")
    run.add_argument("--liquid-timeout", type=float, default=3600.0, help="hard limit of the liquid solve, seconds")
    run.add_argument("--liquid-device", default="cpu", help="torch device of the liquid solve (default: cpu)")
    run.add_argument("--allow-serial-solve", action="store_true", help="accept a solve on one rank")
    run.add_argument("--require-check-mesh", action="store_true", help="fail the run when the final checkMesh fails")
    run.add_argument("--label", default=None, help="suffix of the run's result directory")
    run.add_argument("--full", action="store_true", help="print the whole result instead of the summary")
    commands.add_parser("check", help="compare the folder with its EXPORT.json, print one JSON line")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "check":
        report = check()
        print(json.dumps(report))
        return 0 if report["ok"] else 1
    try:
        settings = json.loads(args.run_settings) if args.run_settings else None
        if settings is not None and not isinstance(settings, dict):
            raise ValueError("--run-settings must be a JSON object")
        result = evaluate(args.cfdproj, args.case_dir, solver=args.solver, gpus=args.gpus, processes=args.processes,
                          stages=tuple(part.strip() for part in args.stages.split(",") if part.strip()),
                          mesh_engine=args.mesh_engine, end_time=args.end_time, run_settings=settings,
                          mesh_timeout=args.mesh_timeout, solve_timeout=args.solve_timeout,
                          liquid_timeout=args.liquid_timeout, liquid_device=args.liquid_device,
                          allow_serial_solve=args.allow_serial_solve, require_check_mesh=args.require_check_mesh,
                          label=args.label)
    except (ValueError, OSError) as exc:
        print(json.dumps({"status": "failed", "failure": {"stage": "input", "reason": str(exc)}}))
        return 2
    print(json.dumps(result if args.full else summary(result), ensure_ascii=False, default=str))
    return 0 if result.get("status") == "succeeded" else 1


if __name__ == "__main__":
    sys.exit(main())
