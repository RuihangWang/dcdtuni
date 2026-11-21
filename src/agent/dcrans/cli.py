"""CLI subcommands for dcRANS forward solve, inversion, and patch-preservation audit."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable, Dict


def _dump(obj: Any) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


def _cmd_audit(args: argparse.Namespace) -> int:
    from agent.dcrans.service import verify_dcrans_patches
    res = verify_dcrans_patches(
        Path(args.case_dir),
        agglomerate_box_m=args.box_m,
        check_agglomeration=not args.boundary_only,
        run_acceptance=not args.no_acceptance,
        output_root=args.out_root,
        python_exe=args.python_exe,
    )
    _dump(res)
    return 0 if res.get("status") == "succeeded" else 1


def _cmd_solve(args: argparse.Namespace) -> int:
    from agent.dcrans.service import solve_dcrans
    theta = json.loads(args.theta) if args.theta else None
    res = solve_dcrans(
        args.source,
        level=args.level,
        mode=args.mode,
        theta=theta,
        duration_s=args.duration,
        dt_s=args.dt_s,
        agglomerate_box_m=args.box_m,
        case_dir=args.case_dir,
        output_root=args.out_root,
        gpu=args.gpu,
        python_exe=args.python_exe,
    )
    _dump(res)
    return 0 if res.get("status") == "succeeded" else 1


def _cmd_invert(args: argparse.Namespace) -> int:
    from agent.dcrans.service import invert_dcrans
    res = invert_dcrans(
        args.source,
        Path(args.observations),
        level=args.level,
        mode=args.mode,
        free_indices=json.loads(args.free_indices) if args.free_indices else None,
        dt_s=args.dt_s,
        max_iters=args.max_iters,
        fd_check=args.fd_check,
        output_root=args.out_root,
        gpu=args.gpu,
        python_exe=args.python_exe,
    )
    _dump(res)
    return 0 if res.get("status") == "succeeded" else 1


def register(subparsers: argparse._SubParsersAction) -> Dict[str, Callable[[argparse.Namespace], int]]:
    au = subparsers.add_parser("dcrans-audit", help="audit OpenFOAM/agglomerated boundary patch preservation")
    au.add_argument("case_dir")
    au.add_argument("--box-m", type=float, default=0.4)
    au.add_argument("--boundary-only", action="store_true")
    au.add_argument("--no-acceptance", action="store_true")
    au.add_argument("--out-root")
    au.add_argument("--python-exe")

    sv = subparsers.add_parser("dcrans-solve", help="run unified JFNK steady/transient dcRANS solve")
    sv.add_argument("source", help=".cfdproj or case directory")
    sv.add_argument("--level", choices=("L0", "L1"), default="L0")
    sv.add_argument("--mode", choices=("steady", "transient"), default="steady")
    sv.add_argument("--theta", help="JSON array of calibration parameters")
    sv.add_argument("--duration", type=float)
    sv.add_argument("--dt-s", type=float, default=300.0)
    sv.add_argument("--box-m", type=float, default=0.4)
    sv.add_argument("--case-dir")
    sv.add_argument("--out-root")
    sv.add_argument("--gpu")
    sv.add_argument("--python-exe")

    iv = subparsers.add_parser("dcrans-invert", help="run unified JFGNK steady/transient dcRANS calibration")
    iv.add_argument("source", help=".cfdproj or case directory")
    iv.add_argument("--observations", required=True, help="JSON file of target observations")
    iv.add_argument("--level", choices=("L0", "L1"), default="L0")
    iv.add_argument("--mode", choices=("steady", "transient"), default="steady")
    iv.add_argument("--free-indices", help="JSON list of free parameter indices")
    iv.add_argument("--dt-s", type=float, default=300.0)
    iv.add_argument("--max-iters", type=int, default=10)
    iv.add_argument("--fd-check", action="store_true")
    iv.add_argument("--out-root")
    iv.add_argument("--gpu")
    iv.add_argument("--python-exe")

    return {"dcrans-audit": _cmd_audit, "dcrans-solve": _cmd_solve, "dcrans-invert": _cmd_invert}
