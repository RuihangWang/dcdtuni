"""Materialize one audited liquid sample as a separate OpenFOAM air case.

Usage after a complete v3 DAE batch/UI run::

  python -m backend.aircooling.runner.hybrid_air_case_cli \
    --project model.cfdproj --air source.cfdrun \
    --tri-surface Mesh/constant/triSurface --liquid-run log/run-ID \
    --sample-index 5 --output air-t100

The output is a derived run directory, never an editable project. The normal
``run_cfd --mesh-only`` and ``--solve-only`` entry points enforce STL, mesh and
actual patch-area checks for its hybrid manifest.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil

from backend.aircooling.runner.hybrid_air_heat_handoff import compile_hybrid_air_heat


def _read_json(path: Path) -> dict:
    if not path.is_file():
        raise ValueError(f"required hybrid input is missing: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"required hybrid input is not a JSON object: {path}")
    return value


def write_hybrid_air_case(
    project_file: str | Path,
    air_cfdrun_file: str | Path,
    tri_surface_dir: str | Path,
    liquid_run_dir: str | Path,
    sample_index: int,
    output_dir: str | Path,
) -> dict:
    """Write a new model/ + Mesh/ case only after all physics checks pass."""
    project_path = Path(project_file)
    air_path = Path(air_cfdrun_file)
    surface_path = Path(tri_surface_dir)
    liquid_path = Path(liquid_run_dir)
    output = Path(output_dir)
    if output.exists():
        raise ValueError(f"hybrid output already exists and will not be overwritten: {output}")
    project = _read_json(project_path)
    air = _read_json(air_path)
    original = _read_json(liquid_path / "input.liquidrun")
    executed = _read_json(liquid_path / "executed.liquidrun")
    result = _read_json(liquid_path / "result.json")
    receipt = _read_json(liquid_path / "executed-inputs.json")
    compiled, ledger = compile_hybrid_air_heat(
        project, air, surface_path, original_liquidrun=original,
        executed_liquidrun=executed, liquid_result=result,
        accepted_provenance=receipt, sample_index=sample_index)
    expected = {entry["path"] for entry in compiled["geometryExport"]["stlFiles"].values()}

    output.mkdir(parents=False, exist_ok=False)
    model_dir = output / "model"
    target_surfaces = output / "Mesh/constant/triSurface"
    model_dir.mkdir()
    target_surfaces.mkdir(parents=True)
    for name in sorted(expected):
        shutil.copy2(surface_path / name, target_surfaces / name)
    (model_dir / air_path.name).write_text(json.dumps(compiled, indent=2, allow_nan=False) + "\n",
                                           encoding="utf-8")
    (output / "heat-ledger.json").write_text(json.dumps(ledger, indent=2, allow_nan=False) + "\n",
                                               encoding="utf-8")
    return {"case_dir": str(output.resolve()), "air_run_id": compiled["runId"],
            "liquid_run_id": ledger["liquid_run_id"], "sample_index": sample_index,
            "time_s": ledger["time_s"], "air_residual_W": ledger["totals"]["air_residual_W"],
            "server_count": len(ledger["servers"]), "stl_file_count": len(expected)}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Compile one verified liquid sample to an isolated air case")
    parser.add_argument("--project", required=True)
    parser.add_argument("--air", required=True)
    parser.add_argument("--tri-surface", required=True)
    parser.add_argument("--liquid-run", required=True)
    parser.add_argument("--sample-index", required=True, type=int)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    receipt = write_hybrid_air_case(
        args.project, args.air, args.tri_surface, args.liquid_run,
        args.sample_index, args.output)
    print(json.dumps(receipt, sort_keys=True))


if __name__ == "__main__":
    main()
