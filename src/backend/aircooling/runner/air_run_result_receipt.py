"""Content identity for one native OpenFOAM wind-result time.

This is a read-only snapshot verifier. It does not judge mesh quality,
convergence, physical validity, or whether the solver really advanced from
the supplied .cfdrun. The run integration must make those claims separately.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .cfdrun_snapshot import CfdrunSnapshot


FORMAT = "air-run-result-receipt/v1"
_MESH_NAMES = ("points", "faces", "owner", "neighbour", "boundary")
_FIELD_SPECS = {
    "T": ("volScalarField", [0, 0, 0, 1, 0, 0, 0]),
    "U": ("volVectorField", [0, 1, -1, 0, 0, 0, 0]),
}
_SHA256 = re.compile(r"[0-9a-fA-F]{64}\Z")
_COMMENTS = re.compile(r"/\*.*?\*/|//[^\n]*", re.DOTALL)
_FOAM_DICT = re.compile(r"\bFoamFile\s*\{([^{}]*)\}", re.DOTALL)
_DIMENSIONS = re.compile(r"\bdimensions\s*\[([^\]]*)\]\s*;")


def _inside(root: Path, path: Path, *, label: str) -> Path:
    """Resolve a path while rejecting aliases that could cross rank/case identity."""
    original = Path(path).absolute()
    resolved = original.resolve(strict=True)
    if not resolved.is_relative_to(root):
        raise ValueError(f"{label} must be within the selected case")
    # A machine may spell an ancestor of the case through a normal alias
    # (/var -> /private/var on macOS). Reject links *below* the case anchor,
    # which could otherwise make processor1 silently read processor0.
    anchor = next(
        (parent for parent in (original, *original.parents)
         if parent.resolve(strict=True) == root), None,
    )
    if anchor is None or anchor.is_symlink():
        raise ValueError(f"{label} must not be linked by a symlink inside the case")
    component = original
    while component != anchor:
        if component.is_symlink():
            raise ValueError(f"{label} must not be linked by a symlink inside the case")
        component = component.parent
    return resolved


def _hash_file(path: Path, *, aggregate: Any = None) -> tuple[str, bytes]:
    """Hash stable bytes, retaining only a bounded prefix for header parsing."""
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"OpenFOAM file is missing or linked: {path}")
    before = path.stat()
    if before.st_size <= 0:
        raise ValueError(f"OpenFOAM file is empty: {path}")
    digest = hashlib.sha256()
    head = bytearray()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            if aggregate is not None:
                aggregate.update(chunk)
            if len(head) < 128 * 1024:
                head.extend(chunk[:128 * 1024 - len(head)])
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (
        after.st_size, after.st_mtime_ns, after.st_ino
    ):
        raise ValueError(f"OpenFOAM file changed during hashing: {path}")
    return digest.hexdigest(), bytes(head)


def _field_header(path: Path, name: str, raw_head: bytes) -> tuple[str, list[int]]:
    if path.suffix == ".gz":
        # Consume the stream to verify its CRC as well as its header. It stays
        # bounded in memory even when the field contains millions of cells.
        prefix = bytearray()
        try:
            with gzip.open(path, "rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    if len(prefix) < 128 * 1024:
                        prefix.extend(chunk[:128 * 1024 - len(prefix)])
        except (OSError, EOFError) as exc:
            raise ValueError(f"field {name} gzip stream is invalid: {path}") from exc
        raw_head = bytes(prefix)
    text = _COMMENTS.sub("", raw_head.decode("latin-1"))
    header = _FOAM_DICT.search(text)
    if header is None:
        raise ValueError(f"field {name} has no FoamFile header: {path}")
    header_text = header.group(1)
    expected_class, expected_dimensions = _FIELD_SPECS[name]
    field_class = re.search(r"\bclass\s+([A-Za-z][A-Za-z0-9_]*)\s*;", header_text)
    if field_class is None or field_class.group(1) != expected_class:
        raise ValueError(f"field {name} class is not {expected_class}: {path}")
    object_name = re.search(r"\bobject\s+([A-Za-z][A-Za-z0-9_]*)\s*;", header_text)
    if object_name is None or object_name.group(1) != name:
        raise ValueError(f"field {name} object does not match its name: {path}")
    dimensional = _DIMENSIONS.search(text[header.end():])
    if dimensional is None:
        raise ValueError(f"field {name} dimensions are missing: {path}")
    try:
        values = [float(value) for value in dimensional.group(1).split()]
    except ValueError as exc:
        raise ValueError(f"field {name} dimensions are malformed: {path}") from exc
    if len(values) != 7 or not all(math.isfinite(value) for value in values) or values != expected_dimensions:
        raise ValueError(f"field {name} dimensions are not {expected_dimensions}: {path}")
    if re.search(r"\binternalField\b", text[dimensional.end():]) is None:
        raise ValueError(f"field {name} internalField is missing: {path}")
    return expected_class, expected_dimensions.copy()


def _one_time_dir(root: Path, time_value: float) -> Path:
    matches = []
    for candidate in root.iterdir():
        if not candidate.is_dir():
            continue
        try:
            candidate_value = float(candidate.name)
        except ValueError:
            continue
        if math.isfinite(candidate_value) and candidate_value == time_value:
            matches.append(candidate)
    if len(matches) != 1:
        detail = "ambiguous numeric time" if matches else "timeValue is missing"
        raise ValueError(f"{detail} {time_value} in {root}")
    return matches[0]


def _one_field_path(time_dir: Path, name: str) -> Path:
    matches = [path for path in (time_dir / name, time_dir / f"{name}.gz") if path.is_file()]
    if len(matches) != 1:
        detail = "ambiguous field" if matches else f"field {name} is missing"
        raise ValueError(f"{detail} at {time_dir}")
    return matches[0]


def _execution_manifest_path(case: Path, path: Path, *,
                             input_snapshot: CfdrunSnapshot | None = None) -> Path:
    """Bind logical RAM input without requiring its optional replay artifact."""
    if input_snapshot is None:
        source = _inside(case, path, label="executed .cfdrun")
        if source.suffix != '.cfdrun' or not source.is_file():
            raise ValueError('executed .cfdrun path must name a file within the selected case')
        return source
    if not isinstance(input_snapshot, CfdrunSnapshot):
        raise TypeError('input_snapshot must be a CfdrunSnapshot')
    input_snapshot.require_context(case, path)
    original = Path(path).absolute()
    anchor = next((parent for parent in (original, *original.parents)
                   if parent.resolve() == case), None)
    if anchor is None or anchor.is_symlink():
        raise ValueError('executed .cfdrun must not be linked by a symlink inside the case')
    component = original
    while component != anchor:
        if component.is_symlink():
            raise ValueError('executed .cfdrun must not be linked by a symlink inside the case')
        component = component.parent
    if input_snapshot.logical_path.suffix != '.cfdrun':
        raise ValueError('executed .cfdrun must name the selected logical artifact')
    return input_snapshot.logical_path


def _manifest_info(path: Path, *, input_snapshot: CfdrunSnapshot | None = None,
                   case_dir: Path | None = None) -> tuple[str, str, str]:
    if input_snapshot is not None:
        if case_dir is None:
            raise ValueError('snapshot manifest metadata requires its case context')
        _execution_manifest_path(case_dir, path, input_snapshot=input_snapshot)
        data = input_snapshot.read_bytes()
    else:
        before = path.stat()
        data = path.read_bytes()
        after = path.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (
            after.st_size, after.st_mtime_ns, after.st_ino
        ):
            raise ValueError("executed .cfdrun changed while building the receipt")
    try:
        manifest = json.loads(data)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("executed .cfdrun is not JSON") from exc
    run_id = manifest.get("runId") if isinstance(manifest, dict) else None
    source = manifest.get("sourceProject") if isinstance(manifest, dict) else None
    project_hash = source.get("contentHash") if isinstance(source, dict) else None
    if (not isinstance(run_id, str) or not run_id.strip() or
            not isinstance(project_hash, str) or not _SHA256.fullmatch(project_hash)):
        raise ValueError("executed .cfdrun needs runId and sourceProject.contentHash")
    return hashlib.sha256(data).hexdigest(), run_id, project_hash.lower()


def build_air_run_result_receipt(
    *, case_dir: str | Path, cfdrun_path: str | Path, run_dir: str | Path,
    time_value: int | float,
    input_snapshot: CfdrunSnapshot | None = None,
) -> dict[str, Any]:
    """Describe exactly one readable native T/U frame without writing files.

    The layout is selected by the existing OpenFOAM postprocessor for this
    numerical time. A reconstructed root and a partitioned mesh may coexist;
    only the selected layout is included, with every rank required.
    """
    if (isinstance(time_value, bool) or not isinstance(time_value, (int, float)) or
            not math.isfinite(time_value) or time_value <= 0):
        raise ValueError("timeValue must be a finite, positive number")
    time_value = float(time_value)
    case = Path(case_dir).resolve(strict=True)
    if not case.is_dir():
        raise ValueError("case_dir must be a directory")
    cfdrun = _execution_manifest_path(case, Path(cfdrun_path), input_snapshot=input_snapshot)
    run = _inside(case, Path(run_dir), label="run directory")
    if not run.is_dir() or run.parent != case / "log":
        raise ValueError("run directory must be one child of the selected case log directory")
    cfdrun_sha, run_id, project_hash = _manifest_info(cfdrun, input_snapshot=input_snapshot, case_dir=case)

    # This is intentionally the same layout decision made by native PP.
    # Import lazily: no VTK or OpenFOAM process is started by this module.
    from .postprocess import _openfoam_result_layout

    try:
        meshes, times = _openfoam_result_layout(run, time_value)
    except (FileNotFoundError, ValueError) as exc:
        raise ValueError(f"timeValue {time_value} has no complete OpenFOAM layout") from exc
    if time_value not in times:
        raise ValueError(f"timeValue {time_value} has no field-bearing common result")
    serial = meshes == [run / "constant" / "polyMesh"]
    layout = "serial" if serial else "partitioned"
    aggregate = hashlib.sha256()
    if not serial:
        aggregate.update(b"partitioned-polyMesh-v1\0")
    mesh_paths = []
    field_paths = []
    for rank, mesh in enumerate(meshes):
        base = run if serial else run / f"processor{rank}"
        if mesh != base / "constant" / "polyMesh":
            raise ValueError("selected OpenFOAM mesh layout has unexpected rank ordering")
        prefix = "" if serial else f"processor{rank}/"
        for name in _MESH_NAMES:
            mesh_paths.append((f"{prefix}{name}", _inside(run, mesh / name, label="mesh file")))
        time_dir = _one_time_dir(base, time_value)
        _inside(run, time_dir, label="time directory")
        for name in _FIELD_SPECS:
            field_paths.append((name, _inside(run, _one_field_path(time_dir, name), label=f"field {name}")))
    # The mesh files feed one running digest, in order, here; the field files
    # hash on threads meanwhile (hashlib releases the GIL).
    mesh_files = []
    field_files = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        field_hashes = [pool.submit(_hash_file, path) for _, path in field_paths]
        for label, path in mesh_paths:
            aggregate.update(label.encode() + b"\0")
            sha, _ = _hash_file(path, aggregate=aggregate)
            mesh_files.append({"path": path.relative_to(run).as_posix(), "sha256": sha})
        for (name, path), hashed in zip(field_paths, field_hashes):
            sha, head = hashed.result()
            foam_class, dimensions = _field_header(path, name, head)
            field_files.append({
                "field": name, "path": path.relative_to(run).as_posix(),
                "sha256": sha, "foamClass": foam_class, "dimensions": dimensions,
            })
    return {
        "format": FORMAT,
        "caseName": case.name,
        "runRelativePath": run.relative_to(case).as_posix(),
        "cfdrunRelativePath": cfdrun.relative_to(case).as_posix(),
        "cfdrunSha256": cfdrun_sha,
        "runId": run_id,
        "projectContentHash": project_hash,
        "timeValue": time_value,
        "meshLayout": layout,
        "meshSha256": aggregate.hexdigest(),
        "meshFiles": mesh_files,
        "fieldFiles": field_files,
    }


def verify_air_run_result_receipt(
    receipt: dict[str, Any], *, case_dir: str | Path,
    cfdrun_path: str | Path, run_dir: str | Path,
    input_snapshot: CfdrunSnapshot | None = None,
) -> dict[str, Any]:
    """Recompute from current bytes and reject every identity mismatch.

    The caller must supply the currently selected case, executed manifest and
    run directory; receipt paths alone never choose files for verification.
    """
    if not isinstance(receipt, dict):
        raise ValueError("receipt must be an object")
    expected = build_air_run_result_receipt(
        case_dir=case_dir, cfdrun_path=cfdrun_path, run_dir=run_dir,
        time_value=receipt.get("timeValue"), input_snapshot=input_snapshot,
    )
    if receipt != expected:
        raise ValueError("receipt identity mismatch against current case bytes")
    return expected
