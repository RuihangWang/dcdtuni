"""Thin CLI over :mod:`agent.scripted_api`. Run from ``src/`` (or with ``src`` on ``PYTHONPATH``).

    python -m agent.scripted_api air ../case/SingleRack/SingleRack.cfdproj --solve-processes 8 --max-processes 8
    python -m agent.scripted_api air X.cfdproj --mesh-only --mesh-engine snappy
    python -m agent.scripted_api air X.cfdproj --solve-only --solve-processes 4 --end-time 1500
    python -m agent.scripted_api liquid ../case/Shinaide_Liquid/Shinaide_liquid.cfdproj --fd-check 3
    python -m agent.scripted_api liquid X.cfdproj --mode transient --duration 20 --pump-rpm m_1_pump=0.8
    python -m agent.scripted_api liquid run.liquidrun --out-root ../_tmp/api-runs --device cpu
    python -m agent.scripted_api hybrid ../case/Shinaide_Liquid/Shinaide_liquid.cfdproj --solve-processes 4 --max-processes 8
    python -m agent.scripted_api build spec.json --out ../_tmp/Room/Room.cfdproj [--base Y.cfdproj]
    python -m agent.scripted_api export-air X.cfdproj --out-root ../_tmp/exports [--case-layout]
    python -m agent.scripted_api export-liquid X.cfdproj --out-root ../_tmp/exports
    python -m agent.scripted_api batch batch.json --out-root ../_tmp/batches --max-parallel 2 --max-processes 8
    python -m agent.scripted_api identity

stdout carries one JSON document (a summary; ``--full`` prints the whole
result). The exit code is 0 when ``status`` is ``succeeded``, 1 otherwise.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional


def _run_settings(args) -> Optional[Dict[str, Any]]:
    from agent.scripted_api.air import run_settings_from_options
    settings = None
    if args.run_settings:
        try:
            settings = json.loads(args.run_settings)
        except json.JSONDecodeError as exc:
            raise ValueError(f"--run-settings is not valid JSON: {exc}") from exc
        if not isinstance(settings, dict):
            raise ValueError(f"--run-settings must be a JSON object, got {type(settings).__name__}")
    return run_settings_from_options(settings,
                                     mesh_engine=args.mesh_engine, mesh_processes=args.mesh_processes,
                                     solve_processes=args.solve_processes, end_time=args.end_time)


def _air_summary(result: Dict[str, Any]) -> Dict[str, Any]:
    fields = (result.get("metrics") or {}).get("fields") or {}
    stages = result.get("stages") or {}
    return {
        "status": result.get("status"), "failure": result.get("failure"), "runDir": result.get("runDir"),
        "resultJson": (result.get("artifacts") or {}).get("resultJson"), "elapsedSeconds": result.get("elapsedSeconds"),
        "stages": (result.get("inputs") or {}).get("stages"),
        "cells": (stages.get("mesh") or {}).get("cells"), "solveRunDir": (stages.get("solve") or {}).get("runDir"),
        "convergence": ((result.get("metrics") or {}).get("convergence") or {}).get("verdict"),
        "convergenceReason": ((result.get("metrics") or {}).get("convergence") or {}).get("reason"),
        "rackInlet": (fields.get("rackInlet") or {}).get("summary"),
        "energyBalance": fields.get("energyBalance"),
    }


def _flows(edges) -> Dict[str, Any]:
    return {row["id"]: {"massFlowKgS": row["massFlowKgS"], "direction": row["direction"]} for row in edges or []}


def _liquid_summary(result: Dict[str, Any]) -> Dict[str, Any]:
    metrics = result.get("metrics") or {}
    if metrics.get("mode") == "transient":
        return {
            "status": result.get("status"), "failure": result.get("failure"), "runDir": result.get("runDir"),
            "resultJson": (result.get("artifacts") or {}).get("resultJson"),
            "liquidRunDir": (result.get("artifacts") or {}).get("liquidRunDir"),
            "elapsedSeconds": result.get("elapsedSeconds"), "mode": "transient", "samples": metrics.get("samples"),
            "final": metrics.get("final"), "appliedInputs": metrics.get("appliedInputs"),
            "maxChipTemperatureK": metrics.get("maxChipTemperatureK"),
            "peakChipTemperatureK": metrics.get("peakChipTemperatureK"),
            "maxAbsEnergyBalanceW": metrics.get("maxAbsEnergyBalanceW"), "flows": _flows(metrics.get("edges")),
        }
    inner = metrics.get("metrics") or {}
    return {
        "status": result.get("status"), "failure": result.get("failure"), "runDir": result.get("runDir"),
        "resultJson": (result.get("artifacts") or {}).get("resultJson"), "elapsedSeconds": result.get("elapsedSeconds"),
        "mode": "steady", "inputs": metrics.get("inputs"), "flows": _flows(inner.get("edges")),
        "objectives": metrics.get("objectives"), "energyBalanceW": inner.get("energyBalanceW"),
        "finiteDifferenceCheck": [{k: row.get(k) for k in ("objective", "parameter", "component", "adjoint",
                                                            "finiteDifference", "relativeError")}
                                  for row in metrics.get("finiteDifferenceCheck") or []],
    }


def _hybrid_summary(result: Dict[str, Any]) -> Dict[str, Any]:
    summary = _air_summary(result)
    liquid = (result.get("stages") or {}).get("liquid") or {}
    ledger = (result.get("metrics") or {}).get("heatLedger") or {}
    summary.update(kind=result.get("kind"),
                   liquid={key: liquid.get(key) for key in ("runId", "status", "samples", "sampleIndex", "timeS",
                                                            "runDir")},
                   heatLedger={"path": (result.get("artifacts") or {}).get("heatLedger"),
                               "totals": ledger.get("totals"), "servers": len(ledger.get("servers") or [])})
    return summary


def _commands(pairs, flag: str) -> Optional[Dict[str, float]]:
    """``["id=value", ...]`` -> ``{id: value}``."""
    if not pairs:
        return None
    out: Dict[str, float] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key:
            raise ValueError(f"{flag} takes EDGE_ID=VALUE, got {pair!r}")
        try:
            out[key] = float(value)
        except ValueError as exc:
            raise ValueError(f"{flag} {pair!r}: {value!r} is not a number") from exc
    return out


def _liquid_inputs(args) -> Optional[Dict[str, Dict[str, float]]]:
    inputs = {field: _commands(getattr(args, dest), flag)
              for field, dest, flag in (("pump_rpm_ratios", "pump_rpm", "--pump-rpm"),
                                        ("valve_openings", "valve_opening", "--valve-opening"),
                                        ("primary_inlet_K", "primary_inlet_k", "--primary-inlet-k"))}
    inputs = {field: value for field, value in inputs.items() if value}
    return inputs or None


def _engine_choices():
    from agent.scripted_api.air import mesh_engines
    return mesh_engines()


def _add_run_setting_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--mesh-engine", choices=_engine_choices(),
                        help="meshConfig.meshEngine (the engines the cfdrun schema admits)")
    parser.add_argument("--mesh-processes", type=int, help="mesh MPI ranks (parallelMesh when > 1)")
    parser.add_argument("--solve-processes", type=int, help="solve MPI ranks (parallelSolve when > 1)")
    parser.add_argument("--end-time", type=float, help="simulationConfig.endTime (steady: iterations)")
    parser.add_argument("--run-settings", help="JSON object merged into project.runDefaults")
    parser.add_argument("--solver-type", choices=("steady", "transient"),
                        help="air steady/transient, applied through the editor's setSimulationMode "
                             "(transient.airMode, solverType and the transient stop time change together)")


def _add_air_flags(parser: argparse.ArgumentParser) -> None:
    """Run settings and run controls shared by ``air`` and ``hybrid``."""
    _add_run_setting_flags(parser)
    parser.add_argument("--max-processes", type=int, help="refuse to start if any stage asks for more MPI ranks")
    parser.add_argument("--solver-engine", choices=("openfoam", "gpuNewton"), default=None,
                        help="solver of the steady air solve: openfoam (default) or gpuNewton, this project's GPU "
                             "Newton-Krylov solver (one GPU; DCDTUNI_GPU_SOLVER_GPU names the cards it may use, "
                             "default any idle card)")
    parser.add_argument("--allow-serial-solve", action="store_true",
                        help="run a one-rank solve; by default a solve with 1 MPI rank is refused at the input stage "
                             "(CLAUDE.md debugging rule 7: never solve serially)")
    parser.add_argument("--mesh-timeout", type=float, default=1800.0,
                        help="seconds: the API's own hard limit on the mesh job, which is aborted past it (default "
                             "1800). The runner's mesh budget (meshConfig.meshTimeLimitSeconds) is only a soft "
                             "target and never stops a mesh")
    parser.add_argument("--solve-timeout", type=float, default=4 * 3600.0,
                        help="seconds: hard limit on the solve job, aborted past it (default 14400)")
    parser.add_argument("--require-check-mesh", action="store_true",
                        help="stop after the mesh stage when the final checkMesh fails (default: report it and "
                             "go on -- mesh quality is reported, never required)")
    parser.add_argument("--allow-failed-checkmesh", action="store_true",
                        help="accepted for old scripts; solving past a failed checkMesh is now the default")
    parser.add_argument("--accept-seed-collisions", action="store_true",
                        help="continue past the editor's seed-collision question (default: refuse)")
    parser.add_argument("--app", choices=("api", "react", "html"), default="api",
                        help="the private server's --app (default api: /api only, no editor build needed); "
                             "react/html serve an editor too, for debugging")
    parser.add_argument("--label")
    parser.add_argument("--full", action="store_true", help="print the whole result instead of a summary")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m agent.scripted_api", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    air = sub.add_parser("air", help="export, mesh and/or solve one .cfdproj through a private server "
                                     "(a hybrid project's solve takes the hybrid path)")
    air.add_argument("cfdproj")
    air.add_argument("--case-dir", help="case directory (default: the .cfdproj's folder)")
    stages = air.add_mutually_exclusive_group()
    stages.add_argument("--mesh-only", action="store_true", help="export and mesh; no solve")
    stages.add_argument("--solve-only", action="store_true",
                        help="solve the case's existing mesh: refused (failure stage 'input') unless its "
                             "mesh-quality-receipt is accepted and it was built from the STL batch this project "
                             "exports now")
    _add_air_flags(air)
    air.add_argument("--ai-init", metavar="MODEL",
                     help="start the steady solve from this SCANO checkpoint's prediction on the same mesh "
                          "(the editor's AI 推理初始化) instead of uniform fields")
    air.add_argument("--sample-index", type=int,
                     help="hybrid project only: the accepted liquid sample whose heat goes to the air solve "
                          "(default: the latest)")

    hybrid = sub.add_parser("hybrid", help="liquid session, mesh and solve with its heat (hybridHeat), one server")
    hybrid.add_argument("cfdproj")
    hybrid.add_argument("--case-dir", help="case directory (default: the .cfdproj's folder)")
    hybrid.add_argument("--solve-only", action="store_true",
                        help="reuse the case's accepted mesh (as air --solve-only); the liquid session still runs")
    hybrid.add_argument("--sample-index", type=int,
                        help="the accepted liquid sample whose heat goes to the air solve (default: the latest, "
                             "as the editor sends)")
    hybrid.add_argument("--liquid-timeout", type=float, default=3600.0,
                        help="seconds: the liquid session is cancelled past it (default 3600)")
    hybrid.add_argument("--liquid-device", default="cpu",
                        help="torch device of the liquid session (default cpu; 'project' keeps the saved one)")
    hybrid.add_argument("--liquid-threads", type=int, help="torch threads of the liquid session")
    _add_air_flags(hybrid)



    liquid = sub.add_parser("liquid", help="DAE steady state (network, metrics, gradients) or transient march")
    liquid.add_argument("source", help=".cfdproj (compiled headlessly) or .liquidrun")
    liquid.add_argument("--out-root", help="result root (required for a .liquidrun)")
    liquid.add_argument("--mode", choices=("steady", "transient"), default="steady",
                        help="steady: the session's equilibrium query plus gradients; transient: the product batch "
                             "march (runtime_batch.run_dae_manifest). A .cfdproj is compiled in this mode through "
                             "the editor's mode switch")
    liquid.add_argument("--duration", type=float, dest="duration_s",
                        help=".cfdproj only: the timeline length (s) a transient run marches over")
    for flag, dest, what in (("--pump-rpm", "pump_rpm", "pump speed ratio"),
                             ("--valve-opening", "valve_opening", "valve opening (0, 1]"),
                             ("--primary-inlet-k", "primary_inlet_k", "CDU primary inlet temperature (K)")):
        liquid.add_argument(flag, action="append", dest=dest, metavar="EDGE_ID=VALUE",
                            help=f"{what}; repeatable. One command through DAESimulation.set_inputs (the server's "
                                 "inputs endpoint): a transient run applies it from sample 1, a steady answer is "
                                 "the equilibrium with it pending")
    liquid.add_argument("--objective", action="append", dest="objectives")
    liquid.add_argument("--gradient-parameter", action="append", dest="gradient_parameters")
    liquid.add_argument("--heat-multiplier", type=float, default=1.0)
    liquid.add_argument("--device", default="cpu", help="torch device (default cpu)")
    liquid.add_argument("--threads", type=int)
    liquid.add_argument("--fd-check", type=int, default=0)
    liquid.add_argument("--fd-step", type=float, default=1e-4, help="relative finite-difference step")
    liquid.add_argument("--label")
    liquid.add_argument("--full", action="store_true")

    for name in ("export-air", "export-liquid"):
        export = sub.add_parser(name, help="headless frontend export only")
        export.add_argument("cfdproj")
        export.add_argument("--out-root", required=True)
        if name == "export-air":
            _add_run_setting_flags(export)
            export.add_argument("--case-layout", action="store_true",
                                help="write a case: model/<name>.cfdrun and Mesh/constant/triSurface/*.stl "
                                     "(default: <name>.cfdrun and triSurface/ side by side)")

    batch = sub.add_parser("batch", help="run several air, hybrid or liquid items; each air/hybrid item through "
                                         "its own private server")
    batch.add_argument("spec", help="batch.json: a list of items, or {\"items\": [...], \"maxParallel\": N, "
                                    "\"maxProcesses\": M}; relative paths resolve against the file's folder")
    batch.add_argument("--out-root", required=True, help="a new dated batch directory is created under it")
    batch.add_argument("--max-parallel", type=int, help="jobs at once (default: the spec's, else 1)")
    batch.add_argument("--max-processes", type=int,
                       help="MPI ranks (liquid items: torch threads) for the whole batch; each job gets "
                            "max_processes // max_parallel")
    batch.add_argument("--label")
    batch.add_argument("--full", action="store_true", help="print batch-result.json instead of a summary")

    build = sub.add_parser("build", help="headless modelling: automation-contract steps -> a saved .cfdproj, "
                                         "then the editor's air export as a self-check")
    build.add_argument("spec", help="spec.json: a list of {\"verb\": ..., \"args\": [...]} steps, or {\"steps\": "
                                    "[...], \"base\": \"Y.cfdproj\"} (base relative to the file); \"$N\" in args "
                                    "is what step N returned")
    build.add_argument("--out", required=True, help="the .cfdproj to write (refused if it exists, unless --overwrite)")
    build.add_argument("--base", help="start from this .cfdproj instead of a new project (overrides the spec's)")
    build.add_argument("--overwrite", action="store_true", help="replace --out if it exists")
    build.add_argument("--full", action="store_true", help="print the whole report (every step's result)")

    sub.add_parser("identity", help="print the code/tool identity this API would record")
    return parser


def _export_air(args) -> int:
    from agent.scripted_api.air import export_air_case, export_blockers
    from agent.scripted_api.rundir import create_run_dir, write_json, write_text
    reply = export_air_case(args.cfdproj, run_settings=_run_settings(args), solver_type=args.solver_type)
    out = create_run_dir(args.out_root, "air-export", Path(args.cfdproj).stem)
    if args.case_layout:
        (out / "model").mkdir()
        manifest_path = out / "model" / reply["cfdrunFilename"]
        surfaces = out / "Mesh" / "constant" / "triSurface"
    else:
        manifest_path = out / reply["cfdrunFilename"]
        surfaces = out / "triSurface"
    write_text(manifest_path, reply["cfdrunJson"])
    surfaces.mkdir(parents=True)
    for name, text in reply["stlFiles"].items():
        write_text(surfaces / name, text)
    write_json(out / "implementation.json", reply.get("implementation", {}))
    blockers = export_blockers(reply)
    summary = {"outDir": str(out), "layout": "case" if args.case_layout else "flat", "cfdrun": str(manifest_path),
               "stlDir": str(surfaces), "stlFiles": sorted(reply["stlFiles"]),
               "manifestErrors": reply.get("manifestErrors"), "preflightStops": reply.get("preflightStops"),
               "seedCollisions": (reply.get("preflight") or {}).get("seedCollisions") or [],
               "hybrid": reply.get("hybrid"), "simulationMode": reply.get("simulationMode"),
               "blockers": blockers}
    print(json.dumps(summary, indent=2, default=str))
    return 0 if not blockers else 1


def _refused(kind: str, stage: str, exc: Exception) -> int:
    """A run or export the API refused before it began: still one JSON document on stdout (scripted-api.md).

    ``stage`` is ``input`` (a missing or unreadable source, a bad option) or,
    for ``export-air`` / ``export-liquid``, ``export`` (the editor's export refused the project).
    """
    reply = {"kind": kind, "status": "failed", "failure": {"stage": stage, "reason": str(exc)}, "runDir": None}
    if kind.startswith("export-"):
        reply["outDir"] = None
    print(json.dumps(reply, indent=2))
    return 1


def main(argv=None) -> int:
    from agent.scripted_api.headless import HeadlessCompileError
    parser = build_parser()
    args = parser.parse_args(argv)
    refusable = args.command in ("air", "hybrid", "liquid", "export-air", "export-liquid")
    try:
        return _dispatch(parser, args)
    except HeadlessCompileError as exc:
        # air, hybrid and liquid record their own export refusals in result.json
        if args.command in ("export-air", "export-liquid"):
            return _refused(args.command, "export", exc)
        raise
    except (ValueError, OSError) as exc:
        if refusable:
            return _refused(args.command, "input", exc)
        raise


def _dispatch(parser, args) -> int:

    if args.command == "air":
        from agent.scripted_api import air as air_api
        stages = ("mesh",) if args.mesh_only else ("solve",) if args.solve_only else ("mesh", "solve")
        result = air_api.run_air_case(args.cfdproj, case_dir=args.case_dir, run_settings=_run_settings(args),
                                      stages=stages, solver_type=args.solver_type,
                                      require_check_mesh=args.require_check_mesh,
                                      accept_seed_collisions=args.accept_seed_collisions,
                                      allow_serial_solve=args.allow_serial_solve,
                                      max_processes=args.max_processes, mesh_timeout=args.mesh_timeout,
                                      solve_timeout=args.solve_timeout, label=args.label, app=args.app,
                                      sample_index=args.sample_index, ai_init=args.ai_init,
                                      solver_engine=args.solver_engine)
        summary = _hybrid_summary(result) if result.get("kind") == "hybrid" else _air_summary(result)
        print(json.dumps(result if args.full else summary, indent=2, default=str))
        return 0 if result["status"] == "succeeded" else 1
    if args.command == "hybrid":
        from agent.scripted_api import air as air_api
        result = air_api.run_hybrid_case(args.cfdproj, case_dir=args.case_dir, run_settings=_run_settings(args),
                                         stages=("solve",) if args.solve_only else ("mesh", "solve"),
                                         solver_type=args.solver_type, sample_index=args.sample_index,
                                         liquid_timeout=args.liquid_timeout,
                                         liquid_device=None if args.liquid_device == "project" else args.liquid_device,
                                         liquid_threads=args.liquid_threads,
                                         require_check_mesh=args.require_check_mesh,
                                         accept_seed_collisions=args.accept_seed_collisions,
                                         allow_serial_solve=args.allow_serial_solve,
                                         max_processes=args.max_processes, mesh_timeout=args.mesh_timeout,
                                         solve_timeout=args.solve_timeout, label=args.label, app=args.app,
                                         solver_engine=args.solver_engine)
        print(json.dumps(result if args.full else _hybrid_summary(result), indent=2, default=str))
        return 0 if result["status"] == "succeeded" else 1
    if args.command == "build":
        from agent.scripted_api import build as build_api
        try:
            spec = build_api.load_build_spec(args.spec)
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
        base = args.base or spec.get("base")
        report = build_api.build_project(spec["steps"], out=args.out, base=base, overwrite=args.overwrite)
        if args.full:
            print(json.dumps(report, indent=2, default=str))
        else:
            check = report.get("selfCheck") or {}
            print(json.dumps({key: report.get(key) for key in ("status", "failure", "out", "sha256", "modelCount",
                                                               "elapsedSeconds")}
                             | {"steps": build_api.steps_summary(report),
                                "selfCheck": {key: check.get(key) for key in (
                                    "ok", "error", "manifestErrors", "preflightStops", "seedCollisions",
                                    "stlFileCount", "boundaryRoles", "cfdrunFilename")}},
                             indent=2, default=str))
        return 0 if report["status"] == "succeeded" and (report.get("selfCheck") or {}).get("ok") else 1
    if args.command == "liquid":
        from agent.scripted_api.liquid import DEFAULT_GRADIENT_PARAMETERS, run_liquid_case
        try:
            inputs = _liquid_inputs(args)
        except ValueError as exc:
            parser.error(str(exc))
        result = run_liquid_case(args.source, output_root=args.out_root, label=args.label, mode=args.mode,
                                 inputs=inputs, duration_s=args.duration_s, objectives=args.objectives,
                                 gradient_parameters=args.gradient_parameters or DEFAULT_GRADIENT_PARAMETERS,
                                 heat_multiplier=args.heat_multiplier, device=args.device, threads=args.threads,
                                 fd_check=args.fd_check, fd_relative_step=args.fd_step)
        print(json.dumps(result if args.full else _liquid_summary(result), indent=2, default=str))
        return 0 if result["status"] == "succeeded" else 1
    if args.command == "export-air":
        return _export_air(args)
    if args.command == "export-liquid":
        from agent.scripted_api.liquid import export_liquidrun
        from agent.scripted_api.rundir import create_run_dir, write_json
        reply = export_liquidrun(args.cfdproj)
        out = create_run_dir(args.out_root, "liquid-export", Path(args.cfdproj).stem)
        write_json(out / "export.liquidrun", reply["liquidrun"])
        write_json(out / "implementation.json", reply.get("implementation", {}))
        print(json.dumps({"outDir": str(out), "liquidrun": str(out / "export.liquidrun"),
                          "version": reply["liquidrun"].get("schemaVersion") or reply["liquidrun"].get("version")},
                         indent=2))
        return 0
    if args.command == "batch":
        from agent.scripted_api.batch import load_batch_spec, run_batch
        try:
            spec = load_batch_spec(args.spec)
            max_parallel = args.max_parallel or spec.get("maxParallel") or 1
            max_processes = args.max_processes or spec.get("maxProcesses")
            if not max_processes:
                raise ValueError("batch needs --max-processes (or maxProcesses in the spec)")
            result = run_batch(spec["items"], out_root=args.out_root, max_parallel=max_parallel,
                               max_processes=max_processes, label=args.label)
        except ValueError as exc:  # refused before the batch began: still one JSON document
            print(json.dumps({"kind": "batch", "status": "failed", "failure": {"stage": "input", "reason": str(exc)},
                              "batchDir": None, "runDir": None}, indent=2))
            return 1
        summary = {key: result.get(key) for key in ("status", "batchDir", "resultJson", "maxParallel",
                                                    "maxProcesses", "processesPerJob", "elapsedSeconds")}
        summary["items"] = [{key: row.get(key) for key in ("index", "kind", "label", "status", "failure",
                                                           "resultJson")}
                            for row in result.get("items", [])]
        print(json.dumps(result if args.full else summary, indent=2, default=str))
        return 0 if result["status"] == "succeeded" else 1
    if args.command == "identity":
        from agent.scripted_api import identity as ident
        from agent.scripted_api.headless import find_node, node_modules_dir
        try:
            node = ident.binary_identity(find_node())
        except FileNotFoundError as exc:
            node = {"error": str(exc)}
        print(json.dumps({"repository": ident.repository_identity(), "host": ident.host_identity(),
                          "python": ident.python_identity(), "node": node,
                          "headlessNodeModules": str(node_modules_dir())}, indent=2))
        return 0
    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    sys.exit(main())
