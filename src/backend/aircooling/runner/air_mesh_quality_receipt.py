"""Bind a strict OpenFOAM mesh-quality verdict to exact air inputs.

The receipt records what this process checked. Consumers must recheck the
hashes and must not treat a copied or handwritten JSON file as proof that
``checkMesh`` ran; an accepted renderer source additionally needs verified
patch lineage and a result-run receipt.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

from .cfdrun_snapshot import CfdrunSnapshot
from .mesh_layout import mesh_directories
from .openfoam_patch_area import _openfoam_environment, mesh_identity_sha256, require_nonempty_mesh


_FAILED = re.compile(r"Failed\s+(\d+)\s+mesh\s+checks?\.", re.IGNORECASE)
_MESH_OK = re.compile(r"(?m)^Mesh OK\.\s*$")
_CONCAVE = re.compile(r"Concave cells \(using face planes\) found")
_SMALL_DET = re.compile(r"Cells with small determinant \(< [\d.e-]+\) found")
_FORMAT = "air-mesh-quality/v1"
_RECEIPT_NAME = "mesh-quality-receipt.json"
#: The checkMesh output a receipt was made from (stdout, stderr, command, mesh identity). A receipt is bound to the
#: .cfdrun as well as to the mesh, so a solve with other solver settings (an end time, a relaxation) found it stale and
#: ran checkMesh again on the same mesh: 98 s on Alibaba-hongchou. With this record the same mesh's output binds to
#: the new input instead (rebind_air_mesh_quality); the verdict is checkMesh's on exactly these mesh bytes.
_OUTPUT_NAME = "mesh-quality-checkmesh.json"

#: third_parties/dcCartMesh build output (Allwmake / deploy.sh)
_REPO_PLATFORMS = Path(__file__).resolve().parents[1] / "third_parties" / "dcCartMesh" / "platforms"
#: names of the checker this module accepts: OpenFOAM's and the repo build
CHECKMESH_NAMES = ("checkMesh", "dcCheckMesh")


def resolve_checkmesh_binary() -> str:
    """The repo-built ``dcCheckMesh`` when it exists, else ``checkMesh``.

    dcCheckMesh is OpenFOAM v2412's checkMesh with the parallel patch
    topology synchronised once for all patches instead of once per patch
    (third_parties/dcCartMesh/applications/utilities/mesh/manipulation/
    dcCheckMesh). Same checks, same report, same sets; a data hall with
    thousands of server-slot patches is checked in a fraction of the time
    (CMI-Guichou, 10 M cells, 7700 patches, 8 ranks: about 68 s -> 25 s).
    """
    if _REPO_PLATFORMS.is_dir():
        for platform_dir in sorted(_REPO_PLATFORMS.iterdir()):
            candidate = platform_dir / "bin" / "dcCheckMesh"
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
    return "checkMesh"


def _cfdrun_bytes(mesh: Path, cfdrun_path: str | Path, *,
                  input_snapshot: CfdrunSnapshot | None = None) -> bytes:
    source = Path(cfdrun_path).resolve()
    case = mesh.parent.resolve()
    if input_snapshot is not None:
        if not isinstance(input_snapshot, CfdrunSnapshot):
            raise TypeError('input_snapshot must be a CfdrunSnapshot')
        input_snapshot.require_context(case, cfdrun_path)
        if source.suffix != '.cfdrun':
            raise ValueError('.cfdrun must name the selected logical artifact')
        return input_snapshot.read_bytes()
    if not source.is_file() or source.suffix != ".cfdrun" or not source.is_relative_to(case):
        raise ValueError(".cfdrun must be an existing file inside the selected case")
    return source.read_bytes()


def _mesh_mode(mesh: Path) -> tuple[str, int]:
    roots = mesh_directories(mesh)
    if not roots:
        raise ValueError("No complete OpenFOAM polyMesh for quality audit")
    if roots[0] == mesh / "constant" / "polyMesh":
        return "serial", 1
    return "partitioned", len(roots)


def _verdict(output: str) -> tuple[str, int]:
    failures = [int(value) for value in _FAILED.findall(output)]
    if "FOAM FATAL" in output or "FOAM ERROR" in output:
        raise ValueError("checkMesh reported an OpenFOAM fatal error")
    if failures and any(value > 0 for value in failures):
        # Concave cells are reported, never refused -- the mesh gate's rule
        # (STLGenRunner._remove_disconnected_cells): -allGeometry counts every
        # cell with two coplanar faces, and the face-tet check still gates
        # the real ones.
        failed = max(failures) - (1 if _CONCAVE.search(output) else 0)
        # EXPERIMENT (2026-10-26, opt-in): the cell determinant only matters
        # to least-squares gradients; the air solver uses Gauss linear. Under
        # DCDTUNI_MESH_GATE_GAUSS_DET=1 it is reported, not refused, so a
        # coarser rack region can be solved and compared (base604 study).
        if os.environ.get("DCDTUNI_MESH_GATE_GAUSS_DET") == "1" and _SMALL_DET.search(output):
            failed -= 1
        if failed > 0:
            return "rejected", failed
        return "accepted", 0
    if _MESH_OK.search(output):
        return "accepted", 0
    raise ValueError("checkMesh verdict is missing or unrecognized")


def record_air_mesh_quality(
    mesh_dir: str | Path,
    cfdrun_path: str | Path,
    *,
    checkmesh_binary: str = "checkMesh",
    mpirun_binary: str = "mpirun",
    timeout_s: int = 300,
    input_snapshot: CfdrunSnapshot | None = None,
) -> dict:
    """Run strict checkMesh and atomically record accepted or explicit failure.

    A failed quality verdict still yields a bound *rejected* receipt, useful
    for diagnostic mesh edges. Missing/incomplete output, command failure or a
    mesh mutation yields no new receipt and cannot authorize any scalar field.
    """
    mesh = Path(mesh_dir).resolve()
    if mesh.name != "Mesh" or not mesh.is_dir():
        raise ValueError("mesh_dir must be the selected case's Mesh directory")
    if not isinstance(timeout_s, int) or isinstance(timeout_s, bool) or timeout_s <= 0:
        raise ValueError("timeout_s must be a positive integer")
    cfdrun = _cfdrun_bytes(mesh, cfdrun_path, input_snapshot=input_snapshot)
    mode, ranks = _mesh_mode(mesh)
    before = mesh_identity_sha256(mesh)
    destination = mesh.parent / _RECEIPT_NAME
    # A fresh audit supersedes an earlier accepted verdict. If this command
    # crashes or yields incomplete output, the previous receipt must not stay
    # available as though the new check had passed.
    destination.unlink(missing_ok=True)
    require_nonempty_mesh(mesh)
    check = [checkmesh_binary, "-case", str(mesh), "-constant", "-allTopology", "-allGeometry"]
    command = ([mpirun_binary, "-np", str(ranks), *check, "-parallel"]
               if mode == "partitioned" else check)
    try:
        process = subprocess.run(
            command, text=True, capture_output=True, check=False, timeout=timeout_s,
            env=_openfoam_environment(checkmesh_binary),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"checkMesh could not complete: {exc}") from exc
    if process.returncode != 0:
        raise ValueError("checkMesh process failed")
    if mesh_identity_sha256(mesh) != before:
        raise ValueError("mesh changed during checkMesh")
    return record_prechecked_air_mesh_quality(
        mesh, cfdrun_path, command=command, workdir=mesh,
        mesh_sha256_before=before,
        cfdrun_sha256_before=hashlib.sha256(cfdrun).hexdigest(),
        returncode=process.returncode, stdout=process.stdout, stderr=process.stderr,
        input_snapshot=input_snapshot,
    )


def record_prechecked_air_mesh_quality(
    mesh_dir: str | Path, cfdrun_path: str | Path, *,
    command: list[str] | tuple[str, ...], workdir: str | Path,
    mesh_sha256_before: str, cfdrun_sha256_before: str,
    returncode: int, stdout: str, stderr: str,
    input_snapshot: CfdrunSnapshot | None = None,
) -> dict:
    """Bind an already completed strict invocation, without running it twice.

    Only the exact full-scope checker commands used by this module or the
    runner are accepted. The caller supplies the identities captured before
    that invocation; both are revalidated now. This is an in-process result
    handoff, not an authorization to trust a saved log or renderer assertion.
    """
    mesh = Path(mesh_dir).resolve()
    if mesh.name != "Mesh" or not mesh.is_dir() or Path(workdir).resolve() != mesh:
        raise ValueError("prechecked quality must use the selected Mesh working directory")
    mode, ranks = _mesh_mode(mesh)
    tokens = list(command)
    if mode == "partitioned":
        if len(tokens) < 5 or Path(tokens[0]).name != "mpirun" or tokens[1:3] != ["-np", str(ranks)]:
            raise ValueError("prechecked quality MPI command does not match the mesh layout")
        tokens = tokens[3:]
        if not tokens or tokens[-1] != "-parallel":
            raise ValueError("prechecked quality requires parallel checkMesh")
        tokens = tokens[:-1]
    if not tokens or Path(tokens[0]).name not in CHECKMESH_NAMES:
        raise ValueError("prechecked quality requires checkMesh")
    options = tokens[1:]
    if options not in (["-allTopology", "-allGeometry"],
                       ["-case", str(mesh), "-constant", "-allTopology", "-allGeometry"]):
        raise ValueError("prechecked quality requires the exact full topology/geometry scope")
    destination = mesh.parent / _RECEIPT_NAME
    destination.unlink(missing_ok=True)
    require_nonempty_mesh(mesh)
    if type(returncode) is not int or returncode != 0:
        raise ValueError("checkMesh process failed")
    if not isinstance(stdout, str) or not isinstance(stderr, str):
        raise ValueError("checkMesh output must be captured text")
    cfdrun = _cfdrun_bytes(mesh, cfdrun_path, input_snapshot=input_snapshot)
    if mesh_identity_sha256(mesh) != mesh_sha256_before:
        raise ValueError("mesh changed during checkMesh")
    if hashlib.sha256(cfdrun).hexdigest() != cfdrun_sha256_before:
        raise ValueError(".cfdrun input changed during checkMesh")
    status, failed_checks = _verdict(stdout + "\n" + stderr)
    receipt = {
        "format": _FORMAT, "status": status, "failedChecks": failed_checks,
        "meshSha256": mesh_sha256_before, "cfdrunSha256": cfdrun_sha256_before,
        "mode": mode, "ranks": ranks,
        "checkmeshStdoutSha256": hashlib.sha256(stdout.encode()).hexdigest(),
        "checkmeshStderrSha256": hashlib.sha256(stderr.encode()).hexdigest(),
    }
    temporary = destination.with_name(f"{destination.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(receipt, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    output = mesh.parent / _OUTPUT_NAME
    temporary = output.with_name(f"{output.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps({"format": "air-mesh-quality-output/v1", "meshSha256": mesh_sha256_before,
                                         "command": list(command), "stdout": stdout, "stderr": stderr}) + "\n",
                             encoding="utf-8")
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return receipt


def rebind_air_mesh_quality(mesh_dir: str | Path, cfdrun_path: str | Path, *,
                            input_snapshot: CfdrunSnapshot | None = None) -> dict | None:
    """The receipt of this mesh made again for the current .cfdrun from the checkMesh output it was made from, or
    None (no such output, or not of these mesh bytes: run checkMesh). The output must be of the mesh as it is now and
    hash to what the receipt recorded; record_prechecked_air_mesh_quality then checks the mesh and input again."""
    mesh = Path(mesh_dir).resolve()
    try:
        receipt = json.loads((mesh.parent / _RECEIPT_NAME).read_text(encoding="utf-8"))
        saved = json.loads((mesh.parent / _OUTPUT_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(receipt, dict) or not isinstance(saved, dict) or saved.get("format") != "air-mesh-quality-output/v1":
        return None
    stdout, stderr = saved.get("stdout"), saved.get("stderr")
    if not isinstance(stdout, str) or not isinstance(stderr, str):
        return None
    current = mesh_identity_sha256(mesh)
    if not (saved.get("meshSha256") == receipt.get("meshSha256") == current
            and hashlib.sha256(stdout.encode()).hexdigest() == receipt.get("checkmeshStdoutSha256")
            and hashlib.sha256(stderr.encode()).hexdigest() == receipt.get("checkmeshStderrSha256")):
        return None
    cfdrun = _cfdrun_bytes(mesh, cfdrun_path, input_snapshot=input_snapshot)
    try:
        return record_prechecked_air_mesh_quality(
            mesh, cfdrun_path, command=saved.get("command") or [], workdir=mesh, mesh_sha256_before=current,
            cfdrun_sha256_before=hashlib.sha256(cfdrun).hexdigest(), returncode=0, stdout=stdout, stderr=stderr,
            input_snapshot=input_snapshot)
    except ValueError:
        return None


def verify_air_mesh_quality_receipt(
    receipt_path: str | Path,
    mesh_dir: str | Path,
    cfdrun_path: str | Path,
    *,
    require_accepted: bool = False,
    input_snapshot: CfdrunSnapshot | None = None,
) -> dict:
    """Check the saved verdict's shape and exact current mesh/input identity."""
    mesh = Path(mesh_dir).resolve()
    receipt_file = Path(receipt_path).resolve()
    if receipt_file != mesh.parent / _RECEIPT_NAME or not receipt_file.is_file():
        raise ValueError("mesh quality receipt is missing from the selected case")
    try:
        receipt = json.loads(receipt_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("mesh quality receipt is invalid") from exc
    if not isinstance(receipt, dict) or set(receipt) != {
        "format", "status", "failedChecks", "meshSha256", "cfdrunSha256",
        "mode", "ranks", "checkmeshStdoutSha256", "checkmeshStderrSha256",
    } or receipt["format"] != _FORMAT:
        raise ValueError("mesh quality receipt format is invalid")
    mode, ranks = _mesh_mode(mesh)
    require_nonempty_mesh(mesh)
    if receipt["mode"] != mode or receipt["ranks"] != ranks:
        raise ValueError("mesh quality receipt partition layout changed")
    if receipt["status"] not in ("accepted", "rejected") or type(receipt["failedChecks"]) is not int:
        raise ValueError("mesh quality receipt verdict is invalid")
    if (receipt["status"] == "accepted") != (receipt["failedChecks"] == 0):
        raise ValueError("mesh quality receipt verdict is inconsistent")
    for key in ("meshSha256", "cfdrunSha256", "checkmeshStdoutSha256", "checkmeshStderrSha256"):
        if not isinstance(receipt[key], str) or not re.fullmatch(r"[0-9a-f]{64}", receipt[key]):
            raise ValueError(f"mesh quality receipt {key} is invalid")
    if receipt["meshSha256"] != mesh_identity_sha256(mesh):
        raise ValueError("mesh quality receipt mesh identity does not match")
    if receipt["cfdrunSha256"] != hashlib.sha256(_cfdrun_bytes(mesh, cfdrun_path, input_snapshot=input_snapshot)).hexdigest():
        raise ValueError("mesh quality receipt .cfdrun input identity does not match")
    if require_accepted and receipt["status"] != "accepted":
        raise ValueError("mesh quality receipt is rejected, not accepted")
    return receipt
