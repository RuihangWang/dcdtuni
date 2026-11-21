"""Ask OpenFOAM for the *meshed* area of named heat patches.

``surfaceFieldValue`` uses mesh.magSf() for each patch. The query runs after
meshing and before any solve, with a separate temporary control dictionary;
it does not need a temperature field or modify the project's controlDict.
"""
from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import uuid

from .mesh_layout import mesh_directories


_FOAM_WORD = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_FLOAT = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"


def require_nonempty_mesh(case_dir: str | Path, *, prefer_partitions: bool = False) -> dict:
    """Require positive global cells on the authoritative solver mesh layout.

    Empty local MPI ranks are legal. A reconstructed root is ignored when a
    caller has explicitly finalized the solver's processor layout.
    """
    from .mesh_layout import processor_mesh_dirs
    from .polymesh_io import read_label_list

    case = Path(case_dir)
    meshes = processor_mesh_dirs(case) if prefer_partitions else mesh_directories(case)
    if not meshes:
        raise ValueError(f"OpenFOAM mesh layout is incomplete or inconsistent: {case}")
    counts = {}
    for mesh in meshes:
        with (mesh / "owner").open("rb") as handle:
            header = handle.read(4096).decode("utf-8", errors="replace")
        note = re.search(r'\bnote\s+"([^"]*)"', header)
        count = re.search(r'\bnCells:(\d+)\b', note[1]) if note else None
        if count:
            cells = int(count[1])
        else:
            # The owner note is optional; both label arrays support ASCII and
            # binary OpenFOAM files without that count header.
            labels = [read_label_list(mesh / "owner"), read_label_list(mesh / "neighbour")]
            if any(array.size and int(array.min()) < 0 for array in labels):
                raise ValueError(f"OpenFOAM mesh contains negative cell labels: {mesh}")
            cells = max((int(array.max()) + 1 for array in labels if array.size), default=0)
        counts[str(mesh.relative_to(case))] = cells
    total = sum(counts.values())
    if total <= 0:
        raise ValueError("OpenFOAM mesh is globally empty (0 cells); choose locationInMesh inside the fluid domain and regenerate the mesh")
    return {"global_cells": total, "cells_by_mesh": counts}


def checkmesh_quality_status(output: str) -> str:
    """Require checkMesh's explicit verdict; its exit code can still be zero."""
    failures = re.findall(r"Failed\s+(\d+)\s+mesh\s+checks?\.", output)
    if failures:
        # Concave cells are reported, never refused (the mesh gate's rule:
        # -allGeometry counts every cell with two coplanar faces, and the
        # face-tet check still gates the real ones).
        failed = int(failures[-1]) - (1 if re.search(r"Concave cells \(using face planes\) found", output) else 0)
        if failed > 0:
            raise ValueError(f"OpenFOAM checkMesh failed {failed} mesh checks")
        return "ok"
    if re.search(r"(?m)^Mesh OK\.\s*$", output):
        return "ok"
    raise ValueError("OpenFOAM checkMesh verdict is missing or unrecognized")


def _openfoam_environment(binary: str) -> dict[str, str]:
    environment = os.environ.copy()
    binary_path = Path(binary)
    if binary_path.is_absolute() and binary_path.parent.name == "bin":
        package_root = binary_path.parent.parent
        if (package_root / "etc").is_dir():
            environment.update({
                "WM_PROJECT_DIR": str(package_root),
                "FOAM_LIBBIN": str(package_root / "lib"),
                "FOAM_APPBIN": str(package_root / "bin"),
                "FOAM_ETC": str(package_root / "etc"),
                "FOAM_MPI": "sys-mpich",
                "FOAM_SIGFPE": "false",
                "PATH": str(package_root / "bin") + os.pathsep + environment.get("PATH", ""),
            })
    return environment


def _mesh_command(case: Path, binary: str, *options: str) -> list[str]:
    """Run against the same authoritative mesh used by the identity digest."""
    meshes = mesh_directories(case)
    if not meshes:
        raise ValueError(f"OpenFOAM mesh layout is incomplete or inconsistent: {case}")
    command = [binary, "-case", str(case)]
    if meshes[0] != case / "constant/polyMesh":
        command = ["mpirun", "-np", str(len(meshes)), *command, "-parallel"]
    return [*command, *options]


def verify_openfoam_mesh_quality(
    case_dir: str | Path, *, checkmesh_binary: str = "checkMesh",
    timeout_s: int = 300,
) -> dict:
    """Run strict geometry/topology checks and fail closed before air solve."""
    case = Path(case_dir).resolve()
    before_hash = mesh_identity_sha256(case)
    require_nonempty_mesh(case)
    try:
        process = subprocess.run(
            _mesh_command(case, checkmesh_binary,
                          "-constant", "-allTopology", "-allGeometry"),
            text=True, capture_output=True, timeout=timeout_s, check=False,
            env=_openfoam_environment(checkmesh_binary))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"OpenFOAM checkMesh could not complete: {exc}") from exc
    if process.returncode != 0:
        raise ValueError("OpenFOAM checkMesh process failed: " + process.stderr[-800:])
    checkmesh_quality_status(process.stdout + "\n" + process.stderr)
    if mesh_identity_sha256(case) != before_hash:
        raise ValueError("OpenFOAM mesh changed during checkMesh")
    return {"status": "ok", "mesh_sha256": before_hash,
            "checkmesh_stdout_sha256": hashlib.sha256(process.stdout.encode()).hexdigest(),
            "checkmesh_stderr_sha256": hashlib.sha256(process.stderr.encode()).hexdigest()}


def parse_surface_field_value_area(path: str | Path, expected_patch: str) -> dict:
    """Parse one v2412 surfaceFieldValue.dat, rejecting stale/wrong patches."""
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    region = [re.fullmatch(r"#\s*Region type\s*:\s*patch\s+(.+?)\s*", line)
              for line in lines]
    region = [match.group(1) for match in region if match]
    faces = [re.fullmatch(r"#\s*Faces\s*:\s*(\d+)\s*", line)
             for line in lines]
    faces = [int(match.group(1)) for match in faces if match]
    areas = [re.fullmatch(rf"#\s*Area\s*:\s*({_FLOAT})\s*", line)
             for line in lines]
    areas = [float(match.group(1)) for match in areas if match]
    data = [line.split() for line in lines if line.strip() and not line.lstrip().startswith("#")]
    if region != [expected_patch]:
        raise ValueError(f"surfaceFieldValue Region type/patch does not match {expected_patch!r}")
    if len(faces) != 1 or faces[0] <= 0:
        raise ValueError(f"surfaceFieldValue Faces must be positive for {expected_patch}")
    if len(areas) != 1 or len(data) != 1 or len(data[0]) != 2:
        raise ValueError(f"surfaceFieldValue Area output is incomplete for {expected_patch}")
    try:
        time_value, table_area = (float(value) for value in data[0])
    except ValueError as exc:
        raise ValueError(f"surfaceFieldValue Area row is not numeric for {expected_patch}") from exc
    if (not math.isfinite(time_value) or not math.isclose(time_value, 0., abs_tol=1e-12) or
            not math.isfinite(areas[0]) or not math.isfinite(table_area) or
            areas[0] <= 0 or table_area <= 0 or
            not math.isclose(areas[0], table_area, rel_tol=1e-10, abs_tol=1e-12)):
        raise ValueError(f"surfaceFieldValue Area header and time-zero row disagree for {expected_patch}")
    return {"area_m2": table_area, "face_count": faces[0]}


def mesh_identity_sha256(case_dir: str | Path) -> str:
    """Hash the exact serial or partitioned OpenFOAM mesh topology.

    Keep the established serial digest stable for existing receipts. A mesh
    written only under processor directories has a domain-separated digest
    over every rank's five topology files in numeric rank order.
    """
    case = Path(case_dir)
    names = ("points", "faces", "owner", "neighbour", "boundary")
    serial = case / "constant/polyMesh"
    if serial.is_dir():
        roots = [("", serial)]
    else:
        candidates = sorted(
            p for p in case.glob("processor*") if p.is_dir()
        )
        if not candidates:
            raise ValueError(f"OpenFOAM mesh is missing {serial / 'points'}")
        ranks = {}
        for path in candidates:
            match = re.fullmatch(r"processor(\d+)", path.name)
            if match is None:
                raise ValueError(f"unsupported OpenFOAM partition directory {path}")
            rank = int(match.group(1))
            if path.name != f"processor{rank}" or rank in ranks:
                raise ValueError(f"noncanonical or ambiguous OpenFOAM partition directory {path}")
            ranks[rank] = path
        missing = [rank for rank in range(len(ranks)) if rank not in ranks]
        if missing:
            raise ValueError(
                "partitioned OpenFOAM mesh requires contiguous ranks; "
                f"missing processor{missing[0]}"
            )
        roots = [(f"processor{rank}/", ranks[rank] / "constant/polyMesh")
                 for rank in range(len(ranks))]
    files = []
    for prefix, root in roots:
        for name in names:
            path = root / name
            if not path.is_file():
                raise ValueError(f"OpenFOAM mesh is missing {path}")
            files.append((prefix, name, path))
    # One job hashes the same mesh many times (receipts, gates, the solve's own checks). The digest of an
    # unchanged file set is reused; a file counts as unchanged only if its inode, size, modification and
    # change times (nanoseconds) are the same before and after the read that produced the digest. A digest is
    # stored only when every file last changed at least _SETTLED_NS before the read: file times advance in
    # coarse ticks, so a same-size rewrite within one tick of the read would leave the key as it was.
    key = _identity_key(files)
    known = _IDENTITY_MEMO.get(key)
    if known is not None:
        return known
    started = time.time_ns()
    digest = hashlib.sha256()
    if roots[0][0]:
        digest.update(b"partitioned-polyMesh-v1\0")
    for prefix, name, path in files:
        digest.update(f"{prefix}{name}".encode() + b"\0")
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    value = digest.hexdigest()
    settled = all(max(entry[4], entry[5]) <= started - _SETTLED_NS for entry in key)
    if settled and _identity_key(files) == key:
        if len(_IDENTITY_MEMO) > 64:
            _IDENTITY_MEMO.clear()
        _IDENTITY_MEMO[key] = value
    return value


_IDENTITY_MEMO: dict = {}
_SETTLED_NS = 2_000_000_000   # a file younger than this at the read is hashed again on every call


def _identity_key(files) -> tuple:
    out = []
    for prefix, name, path in files:
        st = path.stat()
        out.append((str(path.resolve()), st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns))
    return tuple(out)


def _area_control_dict(names: list[str], prefix: str) -> str:
    items = []
    for index, patch in enumerate(names, 1):
        function = f"{prefix}_{index:03d}"
        items.append(f"""    {function}
    {{
        type surfaceFieldValue;
        libs (fieldFunctionObjects);
        regionType patch;
        name {patch};
        operation sum;
        fields ();
        writeArea true;
        writeFields false;
        writeControl timeStep;
        writeInterval 1;
    }}""")
    return """FoamFile
{
    version 2.0;
    format ascii;
    class dictionary;
    object controlDict;
}
application buoyantBoussinesqSimpleFoam;
startFrom latestTime;
startTime 0;
stopAt endTime;
endTime 1000;
deltaT 1;
writeControl timeStep;
writeInterval 1;
purgeWrite 0;
writeFormat ascii;
writePrecision 15;
writeCompression off;
timeFormat general;
timePrecision 15;
runTimeModifiable true;
functions
{
""" + "\n".join(items) + "\n}\n"


def measure_openfoam_patch_areas(
    case_dir: str | Path,
    patch_names: set[str],
    *,
    postprocess_binary: str = "postProcess",
    timeout_s: int = 180,
) -> dict:
    """Return all positive patch areas and a mesh-content identity receipt.

    The caller is responsible for the OpenFOAM environment and for rejecting
    a mesh that failed ``checkMesh``. This only verifies patch membership and
    geometric area; it does not certify mesh quality or solver convergence.
    """
    case = Path(case_dir).resolve()
    if not case.is_dir():
        raise ValueError(f"OpenFOAM case does not exist: {case}")
    if (not isinstance(patch_names, set) or not patch_names or
            any(not isinstance(name, str) or not _FOAM_WORD.fullmatch(name)
                for name in patch_names)):
        raise ValueError("patch_names must be a nonempty set of OpenFOAM patch words")
    if not isinstance(timeout_s, int) or isinstance(timeout_s, bool) or timeout_s <= 0:
        raise ValueError("timeout_s must be a positive integer")
    before_hash = mesh_identity_sha256(case)
    names = sorted(patch_names)
    prefix = "hybridArea" + uuid.uuid4().hex[:12]
    with tempfile.TemporaryDirectory(prefix="dcdtuni-hybrid-area-") as scratch:
        control = Path(scratch) / "controlDict"
        control.write_text(_area_control_dict(names, prefix), encoding="utf-8")
        try:
            process = subprocess.run(
                _mesh_command(case, postprocess_binary,
                              "-time", "0", "-constant", "-noZero",
                              "-dict", str(control)), text=True, capture_output=True,
                timeout=timeout_s, check=False, env=_openfoam_environment(postprocess_binary))
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValueError(f"OpenFOAM patch-area query could not complete: {exc}") from exc
    if process.returncode != 0:
        raise ValueError("OpenFOAM patch-area query failed: " + process.stderr[-800:])
    if mesh_identity_sha256(case) != before_hash:
        raise ValueError("OpenFOAM mesh changed during patch-area query")
    values = {}
    for index, patch in enumerate(names, 1):
        function = f"{prefix}_{index:03d}"
        outputs = list((case / "postProcessing" / function).glob("*/surfaceFieldValue.dat"))
        if len(outputs) != 1:
            raise ValueError(f"mesh patch {patch} is missing a unique surfaceFieldValue area output")
        values[patch] = parse_surface_field_value_area(outputs[0], patch)
    return {"format": "openfoam-patch-area/v1", "mesh_sha256": before_hash,
            "patches": values, "postprocess_stdout_sha256": hashlib.sha256(process.stdout.encode()).hexdigest(),
            "postprocess_stderr_sha256": hashlib.sha256(process.stderr.encode()).hexdigest()}
