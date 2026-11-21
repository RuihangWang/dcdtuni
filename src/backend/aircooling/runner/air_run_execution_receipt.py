"""Publish a native wind-result identity after a successful solver command.

The ``accepted`` status has a narrow meaning: the runner's solver command
returned without error and the selected positive-time mesh and T/U files
passed the content identity checks. It is not a mesh-quality, convergence or
physical-validity decision.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any

from .cfdrun_snapshot import CfdrunSnapshot

from .air_run_result_receipt import (
    _inside,
    _execution_manifest_path,
    _manifest_info,
    build_air_run_result_receipt,
    verify_air_run_result_receipt,
)


FORMAT = "air-run-execution-receipt/v1"
FILENAME = "air-run-execution-receipt.json"


def preflight_air_run_execution_source(
    *, case_dir: str | Path, cfdrun_path: str | Path,
    input_snapshot: CfdrunSnapshot | None = None,
) -> None:
    """Reject an untraceable execution manifest before starting OpenFOAM."""
    case = Path(case_dir).resolve(strict=True)
    manifest = _execution_manifest_path(case, Path(cfdrun_path), input_snapshot=input_snapshot)
    try:
        _manifest_info(manifest, input_snapshot=input_snapshot, case_dir=case)
    except ValueError as exc:
        raise ValueError(f"{exc}; re-export .cfdrun from the saved .cfdproj") from exc


def _latest_complete_time(run_dir: Path) -> float:
    from .postprocess import _openfoam_result_layout

    _, times = _openfoam_result_layout(run_dir)
    positive = [time for time in times if math.isfinite(time) and time > 0]
    if not positive:
        raise ValueError("air run has no positive field-bearing result time")
    selected = positive[-1]

    # OpenFOAM may leave a newer, partially written frame on one rank. The
    # reader's common-time intersection would otherwise fall back to an older
    # frame and make the just-finished run look complete.
    from .mesh_layout import processor_mesh_dirs

    roots = [run_dir]
    partitions = processor_mesh_dirs(run_dir)
    roots.extend(mesh.parent.parent for mesh in partitions)
    for root in roots:
        for child in root.iterdir():
            if not child.is_dir():
                continue
            try:
                value = float(child.name)
            except ValueError:
                continue
            if math.isfinite(value) and value > selected:
                raise ValueError(
                    f"incomplete newer result time {child.name} after selected {selected}"
                )
    return selected


def verify_air_run_execution_receipt(
    receipt: dict[str, Any], *, case_dir: str | Path,
    cfdrun_path: str | Path, run_dir: str | Path,
    input_snapshot: CfdrunSnapshot | None = None,
) -> dict[str, Any]:
    """Recheck the current native frame and the exact execution envelope."""
    if not isinstance(receipt, dict) or set(receipt) != {
        "format", "status", "solverCommand", "result",
    }:
        raise ValueError("air run execution receipt has unexpected fields")
    if receipt["format"] != FORMAT or receipt["status"] != "accepted":
        raise ValueError("air run execution receipt is not accepted v1")
    if not isinstance(receipt["solverCommand"], str) or not receipt["solverCommand"].strip():
        raise ValueError("air run execution receipt has no solver command")
    if not isinstance(receipt["result"], dict):
        raise ValueError("air run execution receipt result must be an object")
    selected = _latest_complete_time(Path(run_dir))
    if receipt["result"].get("timeValue") != selected:
        raise ValueError("air run execution receipt does not select the latest complete time")
    verify_air_run_result_receipt(
        receipt["result"], case_dir=case_dir,
        cfdrun_path=cfdrun_path, run_dir=run_dir, input_snapshot=input_snapshot,
    )
    return receipt


def publish_air_run_execution_receipt(
    *, case_dir: str | Path, cfdrun_path: str | Path,
    input_snapshot: CfdrunSnapshot | None = None,
    run_dir: str | Path, solver_command: str,
) -> dict[str, Any]:
    """Atomically publish one verified result after the caller's solver success.

    The caller must invoke this only after the solver and any reconstruction or
    boundary-field rewrite have returned successfully. A later consumer still
    needs to call the verifier because on-disk result bytes can change.
    """
    run = Path(run_dir)
    selected = _latest_complete_time(run)
    result = build_air_run_result_receipt(
        case_dir=case_dir, cfdrun_path=cfdrun_path,
        run_dir=run, time_value=selected, input_snapshot=input_snapshot,
    )
    receipt = {
        "format": FORMAT, "status": "accepted",
        "solverCommand": solver_command, "result": result,
    }
    verify_air_run_execution_receipt(
        receipt, case_dir=case_dir, cfdrun_path=cfdrun_path, run_dir=run,
        input_snapshot=input_snapshot,
    )
    target = run / FILENAME
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"air run execution receipt already exists: {target}")
    payload = (json.dumps(receipt, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
    fd, temporary = tempfile.mkstemp(prefix=f".{FILENAME}.", dir=run)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, target)
        try:
            persisted = json.loads(target.read_bytes())
            verify_air_run_execution_receipt(
                persisted, case_dir=case_dir,
                cfdrun_path=cfdrun_path, run_dir=run, input_snapshot=input_snapshot,
            )
        except Exception:
            target.unlink(missing_ok=True)
            raise
    finally:
        Path(temporary).unlink(missing_ok=True)
    return receipt
