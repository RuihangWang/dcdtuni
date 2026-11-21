#!/usr/bin/env python3
"""
Run OpenFOAM CFD simulation for data center thermal analysis.

Usage:
    python -m backend.aircooling.runner.run_cfd --from-cfdrun <path/to/<basename>.cfdrun>
    python -m backend.aircooling.runner.run_cfd --from-cfdrun <path> --mesh-only
    python -m backend.aircooling.runner.run_cfd --from-cfdrun <path> --solve-only
    python -m backend.aircooling.runner.run_cfd --from-cfdrun <path> --first-iteration-dir <dir>   (SCANO input)

The normal browser job supplies an owned .cfdrun JSON snapshot on binary stdin.
`--from-cfdrun` identifies its case/model namespace; the replay artifact is optional
input metadata. Legacy file entry remains available without --cfdrun-stdin.
"""
import argparse
import signal
import sys
from pathlib import Path

from backend.aircooling.runner.mesh_budget import MeshBudget, mesh_deadline_watchdog

from loguru import logger

# Setup logging
logger.remove()
logger.add(sys.stderr, level="INFO", format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}")


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Run OpenFOAM CFD simulation for data center",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--from-cfdrun",
        type=str,
        required=True,
        metavar="CFDRUN_PATH",
        help="Case/model .cfdrun identity (file input unless --cfdrun-stdin). "
             "Uses browser-exported STL files in Mesh/constant/triSurface/ directly.",
    )

    parser.add_argument(
        "--mesh-only",
        action="store_true",
        help="Generate mesh only (skip solver)",
    )

    parser.add_argument(
        "--patch-update",
        metavar="PLAN_JSON",
        default=None,
        help="With --mesh-only: keep the existing mesh and reassign its boundary faces to the uploaded STL "
             "solids (the server's mesh_change plan)",
    )

    parser.add_argument(
        "--solve-only",
        action="store_true",
        help="Run solver only (requires existing mesh)",
    )

    parser.add_argument(
        "--first-iteration-dir",
        type=str,
        metavar="DIR",
        help="Write the first iteration of an AI solve into DIR (the model's boundary input; requires an "
             "existing mesh, solve-only semantics, and --first-iteration-recipe)",
    )

    parser.add_argument(
        "--initial-fields",
        type=str,
        metavar="NPZ",
        help="Solve from these internal fields (U, p_rgh, T per cell of the serial mesh) instead of the uniform "
             "initial field (the AI-initialised CFD); boundary conditions unchanged",
    )

    parser.add_argument(
        "--solver-engine",
        choices=("openfoam", "gpuNewton"),
        default="openfoam",
        help="Solver of the steady air solve: openfoam (default) or gpuNewton, the external GPU Newton-Krylov "
             "solver (runner/gpu_newton.py; needs DCDTUNI_GPU_SOLVER_ROOT)",
    )

    parser.add_argument(
        "--first-iteration-recipe",
        type=str,
        metavar="JSON",
        help="The model card's first-iteration recipe (format dcdtuni.first-iteration/v1)",
    )

    parser.add_argument(
        "--expected-cfdrun-sha256",
        type=str,
        metavar="SHA256",
        help="Expected uploaded .cfdrun bytes; checked under the case lock before work starts",
    )

    parser.add_argument('--expected-stl-batch-sha256', type=str, metavar='SHA256',
                        help='Physical STL batch captured with the owned stdin input')
    parser.add_argument('--cfdrun-stdin', action='store_true',
                        help='Read exact input bytes from binary stdin; --from-cfdrun is logical identity')
    parser.add_argument("--mesh-started-monotonic", type=float,
                        help="Same-host mesh admission origin; execution metadata only")
    parser.add_argument("--mesh-deadline-monotonic", type=float,
                        help="Earliest same-host deadline, paired with the admission origin")
    return parser.parse_args()


def _install_sigterm_handler(runner_cls) -> None:
    """Stop this runner once, including signals queued during interpreter exit."""
    stopping = False

    def _on_sigterm(signum, _frame):
        nonlocal stopping
        if stopping:
            return
        stopping = True
        # The supervisor can signal our session and the launcher separately.
        # Ignore later deliveries before logging, child cleanup or Python's
        # atexit/threading shutdown; a queued Python callback is fenced above.
        # The supervisor's SIGKILL escalation remains available if cleanup hangs.
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        active = runner_cls.active_step_process()
        if active is not None:
            logger.warning("SIGTERM received: terminating the running mesh/solve step")
            runner_cls._terminate_process_group(active)
        sys.exit(128 + signum)

    signal.signal(signal.SIGTERM, _on_sigterm)


def _execute(args, mesh_budget, active_runner_class) -> None:
    from backend.aircooling.runner.stlgen_runner import STLGenRunner
    active_runner_class[0] = STLGenRunner
    if mesh_budget is not None:
        mesh_budget.check("runner import")

    cfdrun_path = Path(args.from_cfdrun).resolve()
    if not args.cfdrun_stdin and not cfdrun_path.exists():
        logger.error(f"cfdrun file not found: {cfdrun_path}")
        sys.exit(1)

    # <basename>.cfdrun lives in <case>/model/ — case_dir is the parent
    case_dir = cfdrun_path.parent
    if case_dir.name == "model":
        case_dir = case_dir.parent

    logger.info(f"STLGen pipeline: {cfdrun_path}")
    logger.info(f"Case directory: {case_dir}")

    # An abort from the UI is a SIGTERM. Without a handler Python dies at
    # once and the mpirun step it was waiting on — in its own session, so the
    # server's killpg does not reach it — runs on as an orphan until it
    # finishes. Terminate the active step's group first, then leave with 143.
    _install_sigterm_handler(STLGenRunner)

    try:
        input_snapshot = None
        if args.cfdrun_stdin:
            if args.expected_stl_batch_sha256 is None:
                raise ValueError('stdin cfdrun requires an expected STL batch SHA-256')
            from backend.aircooling.runner.cfdrun_transport import read_cfdrun_stdin
            input_snapshot = read_cfdrun_stdin(
                sys.stdin.buffer, case_dir=case_dir, logical_path=cfdrun_path,
                expected_sha256=args.expected_cfdrun_sha256,
                expected_stl_batch_sha256=args.expected_stl_batch_sha256,
                mesh_budget=mesh_budget)
            logger.info(f"Owned input snapshot: {len(input_snapshot.payload)} bytes, "
                        f"SHA-256 {input_snapshot.sha256}, STL batch {input_snapshot.stl_batch_sha256}")
        runner = STLGenRunner(
            case_dir, cfdrun_path,
            expected_cfdrun_sha256=args.expected_cfdrun_sha256,
            mesh_budget=mesh_budget,
            **({'input_snapshot': input_snapshot} if input_snapshot is not None else {}),
        )
        if getattr(args, "initial_fields", None):
            runner.initial_fields = Path(args.initial_fields)
        runner.solver_engine = getattr(args, "solver_engine", None) or "openfoam"
        first_iteration_dir = getattr(args, "first_iteration_dir", None)
        if first_iteration_dir:
            recipe_path = getattr(args, "first_iteration_recipe", None)
            if not recipe_path:
                raise ValueError("--first-iteration-dir needs --first-iteration-recipe")
            from backend.aircooling.runner.stlgen_runner import load_first_iteration_recipe
            runner.run_first_iteration(Path(first_iteration_dir), load_first_iteration_recipe(Path(recipe_path)))
        elif args.mesh_only:
            plan = getattr(args, "patch_update", None)
            runner.run_mesh_only(**({"patch_update": Path(plan)} if plan else {}))
        elif args.solve_only:
            runner.run_solve_only()
        else:
            runner.run_simulation()
    except Exception as e:
        logger.error(f"STLGen runner failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def main() -> None:
    """Count CLI parsing, imports, stdin and preparation in the same mesh budget."""
    local_budget = MeshBudget()
    args = parse_args()
    first_iteration = getattr(args, "first_iteration_dir", None)
    if sum(bool(x) for x in (args.mesh_only, args.solve_only, first_iteration)) > 1:
        raise ValueError("mesh-only, solve-only and first-iteration-dir are mutually exclusive")
    if getattr(args, "patch_update", None) and not args.mesh_only:
        raise ValueError("--patch-update goes with --mesh-only")
    if args.solve_only or first_iteration:
        if args.mesh_started_monotonic is not None or args.mesh_deadline_monotonic is not None:
            raise ValueError("solve-only / first-iteration does not accept a mesh deadline")
        mesh_budget = None
    elif args.mesh_started_monotonic is not None or args.mesh_deadline_monotonic is not None:
        mesh_budget = MeshBudget(started=args.mesh_started_monotonic,
                                 deadline=args.mesh_deadline_monotonic)
        mesh_budget.deadline = min(mesh_budget.deadline, local_budget.deadline)
    else:
        mesh_budget = local_budget
    active_runner_class = [None]
    def cleanup():
        cls = active_runner_class[0]
        if cls is not None:
            active = cls.active_step_process()
            if active is not None:
                cls._terminate_process_group(active)
    with mesh_deadline_watchdog(mesh_budget, cleanup=cleanup):
        _execute(args, mesh_budget, active_runner_class)


if __name__ == "__main__":
    main()
