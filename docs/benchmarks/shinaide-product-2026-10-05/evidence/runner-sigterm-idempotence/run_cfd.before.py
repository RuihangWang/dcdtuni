#!/usr/bin/env python3
"""
Run OpenFOAM CFD simulation for data center thermal analysis.

Usage:
    python -m backend.aircooling.runner.run_cfd --from-cfdrun <path/to/<basename>.cfdrun>
    python -m backend.aircooling.runner.run_cfd --from-cfdrun <path> --mesh-only
    python -m backend.aircooling.runner.run_cfd --from-cfdrun <path> --solve-only

The .cfdrun manifest is a JSON file produced by the STL UI via
`/api/browser/export-cfdrun`; it sits at `<case>/model/<basename>.cfdrun`.
"""
import argparse
import sys
from pathlib import Path

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
        help="Path to <basename>.cfdrun from frontend. "
             "Uses browser-exported STL files in Mesh/constant/triSurface/ directly.",
    )

    parser.add_argument(
        "--mesh-only",
        action="store_true",
        help="Generate mesh only (skip solver)",
    )

    parser.add_argument(
        "--solve-only",
        action="store_true",
        help="Run solver only (requires existing mesh)",
    )

    parser.add_argument(
        "--expected-cfdrun-sha256",
        type=str,
        metavar="SHA256",
        help="Expected uploaded .cfdrun bytes; checked under the case lock before work starts",
    )

    return parser.parse_args()


def main() -> None:
    """Main entry point."""
    args = parse_args()

    from backend.aircooling.runner.stlgen_runner import STLGenRunner

    cfdrun_path = Path(args.from_cfdrun).resolve()
    if not cfdrun_path.exists():
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
    import signal

    def _on_sigterm(signum, _frame):
        active = STLGenRunner.active_step_process()
        if active is not None:
            logger.warning("SIGTERM received: terminating the running mesh/solve step")
            STLGenRunner._terminate_process_group(active)
        sys.exit(128 + signum)

    signal.signal(signal.SIGTERM, _on_sigterm)

    try:
        runner = STLGenRunner(
            case_dir, cfdrun_path,
            expected_cfdrun_sha256=args.expected_cfdrun_sha256,
        )
        if args.mesh_only:
            runner.run_mesh_only()
        elif args.solve_only:
            runner.run_solve_only()
        else:
            runner.run_simulation()
    except Exception as e:
        logger.error(f"STLGen runner failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
