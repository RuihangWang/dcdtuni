"""``python -m agent {identity,edit,evaluate,optimize}`` -- thin commands over :mod:`agent.tools`.

::

    python -m agent identity
    python -m agent edit case/X/X.cfdproj --edits edits.json --out _tmp/X2/X2.cfdproj
    python -m agent evaluate air_mesh _tmp/X2/X2.cfdproj --case-dir _tmp/X2 --mesh-processes 2 --max-processes 2
    python -m agent evaluate liquid_steady case/Y/Y.cfdproj --fd-check 1 --log-root _tmp/agent-runs

``edits.json`` is a list of ``{"op": "<Edit class>", ...fields}`` (see
:mod:`agent.tools.edits`). ``evaluate`` prints ``{"record": ..., "resultJson": ...}``
and exits 0 when the platform reported ``succeeded``, 1 otherwise; with
``--log-root`` the call is also booked in a new run of that store.
``optimize`` is wired once a scenario is registered (the scenarios step); it
exits 2 until then.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

FIDELITY_NAMES = ("admission", "lf", "liquid_steady", "liquid_transient", "air_mesh", "air_full", "hybrid", "dcrans_l0", "dcrans_l1")


def _print(value: Any) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False, default=str))


def _options(args: argparse.Namespace) -> Dict[str, Any]:
    fidelity = args.fidelity
    options: Dict[str, Any] = {}
    if fidelity in ("air_mesh", "air_full", "hybrid"):
        if args.case_dir is None:
            raise SystemExit(f"evaluate {fidelity}: --case-dir is required (each candidate owns its case directory)")
        options["case_dir"] = args.case_dir
        options["max_processes"] = args.max_processes
        options["mesh_processes"] = args.mesh_processes
        if fidelity != "air_mesh":
            options["solve_processes"] = args.solve_processes
            if args.end_time is not None:
                options["end_time"] = args.end_time
        if args.mesh_engine and fidelity != "hybrid":
            options["mesh_engine"] = args.mesh_engine
    elif fidelity in ("liquid_steady", "liquid_transient"):
        options["fd_check"] = args.fd_check
        if args.duration is not None:
            options["duration_s"] = args.duration
        if args.threads is not None:
            options["threads"] = args.threads
    elif fidelity in ("dcrans_l0", "dcrans_l1"):
        if args.case_dir is not None:
            options["case_dir"] = args.case_dir
        if args.duration is not None:
            options["duration_s"] = args.duration
    if args.label and fidelity != "admission":
        options["label"] = args.label
    return options


def _evaluate(args: argparse.Namespace) -> int:
    from agent.tools.fidelity import evaluate
    if args.fidelity == "lf":
        print("evaluate lf: the LF models arrive with the scenarios; call agent.api.evaluate(..., lf=...) from Python",
              file=sys.stderr)
        return 2
    logger = None
    if args.log_root:
        from agent.records import RunLogger, RunStore
        logger = RunLogger(RunStore(args.log_root, create=True), args.log_label or f"cli-{args.fidelity}",
                           config={"argv": sys.argv[1:], "fidelity": args.fidelity, "candidate": args.candidate},
                           identity={"model": "not used", "data": str(Path(args.candidate).resolve()), "seed": None,
                                     "purpose": f"python -m agent evaluate {args.fidelity}"})
    status = "failed"
    try:
        record = evaluate(Path(args.candidate), args.fidelity, logger=logger, **_options(args))
        status = record.status
        out = {"record": record.as_record(), "resultJson": record.result_identity.get("path")}
        if args.print_result or record.result_identity.get("path") is None:
            out["result"] = getattr(record.result, "raw", dict(record.result))
        _print(out)
    finally:
        if logger is not None:
            logger.finish("completed" if status == "succeeded" else "failed", result_status=status)
    return 0 if status == "succeeded" else 1


def _edit(args: argparse.Namespace) -> int:
    from agent.tools.edits import edit_from_dict
    from agent.tools.project import edit_project
    rows = json.loads(Path(args.edits).read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise SystemExit("--edits must hold a JSON list of {op, ...} objects")
    base = None if args.base == "-" else args.base
    report = edit_project(base, [edit_from_dict(row) for row in rows], out=args.out, overwrite=args.overwrite)
    _print(report)
    return 0 if report.get("status") == "succeeded" else 1


def _identity(args: argparse.Namespace) -> int:
    from agent.tools.evaluate import identity
    _print(identity())
    return 0


def _optimize(args: argparse.Namespace) -> int:
    print("optimize: no scenario is registered yet -- the optimizers (agent.optim.optimizers) are wired to "
          "a scenario's oracle in the scenarios step; call agent.api.optimize(oracle, space, budget, seed, logger) "
          "from Python meanwhile", file=sys.stderr)
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m agent", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("identity", help="code identity a run would record")
    edit = sub.add_parser("edit", help="apply semantic edits through the automation contract")
    edit.add_argument("base", help="base .cfdproj, or - for a new project")
    edit.add_argument("--edits", required=True, help="JSON list of {op, ...} edits")
    edit.add_argument("--out", required=True)
    edit.add_argument("--overwrite", action="store_true")
    ev = sub.add_parser("evaluate", help="evaluate one candidate at one fidelity")
    ev.add_argument("fidelity", choices=FIDELITY_NAMES)
    ev.add_argument("candidate", help=".cfdproj (or .liquidrun for liquid)")
    ev.add_argument("--case-dir")
    ev.add_argument("--mesh-processes", type=int, default=2)
    ev.add_argument("--solve-processes", type=int, default=2)
    ev.add_argument("--max-processes", type=int, default=4)
    ev.add_argument("--mesh-engine")
    ev.add_argument("--end-time", type=float)
    ev.add_argument("--fd-check", type=int, default=0)
    ev.add_argument("--duration", type=float)
    ev.add_argument("--threads", type=int)
    ev.add_argument("--label")
    ev.add_argument("--log-root", help="book the call in a new run of this RunStore root")
    ev.add_argument("--log-label")
    ev.add_argument("--print-result", action="store_true", help="also print the raw result.json")
    opt = sub.add_parser("optimize", help="optimize a registered scenario (wired in the scenarios step)")
    opt.add_argument("--scenario")
    from agent.dcrans.cli import register as register_dcrans
    register_dcrans(sub)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    from agent.dcrans.cli import _cmd_audit, _cmd_invert, _cmd_solve
    args = build_parser().parse_args(argv)
    handlers = {
        "identity": _identity,
        "edit": _edit,
        "evaluate": _evaluate,
        "optimize": _optimize,
        "dcrans-audit": _cmd_audit,
        "dcrans-solve": _cmd_solve,
        "dcrans-invert": _cmd_invert,
    }
    return handlers[args.command](args)
