"""Project tools on ``agent.scripted_api``: edit, query, identity and validation of a ``.cfdproj``.

- :func:`edit_project` compiles semantic edits (:mod:`agent.tools.edits`) to
  build steps and runs them once with :func:`agent.scripted_api.build_project` -- the
  editor's own automation contract in JSDOM; the saved bytes are the save
  button's.
- :func:`query_project` (transitional) runs read-back verbs (``getProject``,
  ``allModels``, ``rackServers`` ...) through ``build_project`` into a
  throw-away scratch file and returns each step's result; the scratch
  directory is removed. ``agent.scripted_api`` has no read-only query entry yet.
- :func:`project_identity` (transitional) hashes the file bytes and the
  canonical JSON with ``agent.scripted_api.identity`` -- the same ``canonical_sha256``
  a run records as ``inputs.effectiveProjectSha256`` when it applies no run
  settings. ``agent.scripted_api`` has no public project-identity function yet (the
  hybrid ``contentHash`` stays private to the runner).
- :func:`validate_project` (transitional) is the headless air export
  (:func:`agent.scripted_api.export_air_case`): ``ok`` when the project loads and its
  manifest passes the cfdrun schema; pre-flight stops and seed collisions are
  reported, not fatal. ``agent.scripted_api`` has no document-only validation yet.

- :func:`export_liquid` is :func:`agent.scripted_api.export_liquidrun`: the
  ``.liquidrun`` the liquid panel would submit for the saved project.
- :func:`export_air` writes the headless air export (``air.cfdrun`` and its
  STL batch, each file checked against the manifest's ``contentHash``) into a
  fresh directory: the geometry an LF model reads.

Scratch files go under :data:`SCRATCH_ROOT` (``<repo>/_tmp/agent-scratch``).
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Union

import agent.scripted_api as api
from agent.scripted_api import identity as api_identity
from agent.scripted_api.air import export_blockers
from agent.scripted_api.headless import HeadlessCompileError

from agent.tools.edits import Edit, ProjectView, compile_edits, needs_materialized

PathLike = Union[str, os.PathLike]
REPO_ROOT = Path(__file__).resolve().parents[3]
SCRATCH_ROOT = REPO_ROOT / "_tmp" / "agent-scratch"


def _scratch_dir(prefix: str) -> Path:
    root = Path(SCRATCH_ROOT)
    root.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=prefix, dir=root))


def _read_json(path: PathLike) -> Dict[str, Any]:
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"{path} is not a .cfdproj object")
    return document


def query_project(cfdproj: PathLike, steps: Sequence[Mapping[str, Any]], *, timeout: float = 600.0) -> List[Any]:
    """Read-only: the return value of every step, run on ``cfdproj`` in the headless editor; writes nothing.

    Transitional (``build_project`` into a scratch file that is deleted); raises
    ``RuntimeError`` with the build's reason when a step fails.
    """
    scratch = _scratch_dir("query-")
    try:
        report = api.build_project([dict(s) for s in steps], out=scratch / "query.cfdproj",
                                   base=str(Path(cfdproj).resolve()), timeout=timeout)
        if report.get("status") != "succeeded":
            failure = report.get("failure") or {}
            raise RuntimeError(f"query failed at {failure.get('stage')}: {failure.get('reason')}")
        return [row.get("result") for row in report.get("steps") or []]
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def materialized_project(cfdproj: PathLike) -> Dict[str, Any]:
    """The project as the editor holds it at t = 0 (v5 tracks materialised), via ``getProject``."""
    (project,) = query_project(cfdproj, [{"verb": "getProject"}])
    if not isinstance(project, dict):
        raise RuntimeError("getProject returned no project")
    return project


def project_view(base: Optional[PathLike], *, build_verbs: Optional[Sequence[str]] = None) -> ProjectView:
    """What an edit may read about ``base`` (lazy: the materialised project is fetched only on demand)."""
    if base is None:
        return ProjectView(build_verbs=build_verbs)
    return ProjectView(document=_read_json(base), materialize=lambda: materialized_project(base),
                       build_verbs=build_verbs)


def edit_project(base: Optional[PathLike], edits: Sequence[Edit], *, out: PathLike, overwrite: bool = False,
                 build_verbs: Optional[Sequence[str]] = None, timeout: float = 600.0) -> Dict[str, Any]:
    """Apply ``edits`` to ``base`` (``None``: a new project) and save ``out``; the ``dcdtuni.api.build/v1`` report.

    The steps are compiled before anything runs, so an edit that cannot be
    expressed (``ContractVerbMissing``, a bad id) builds nothing. The report
    gains ``edits`` (each edit as a dict) and ``compiledSteps``.
    """
    view = project_view(base, build_verbs=build_verbs)
    if needs_materialized(edits):
        view.materialized  # noqa: B018 - fetched once, before the build
    steps = compile_edits(list(edits), view)
    report = api.build_project(steps, out=out, base=None if base is None else str(Path(base).resolve()),
                               overwrite=overwrite, timeout=timeout)
    report["edits"] = [edit.to_dict() for edit in edits]
    report["compiledSteps"] = steps
    return report


def project_identity(cfdproj: PathLike) -> Dict[str, Any]:
    """``{path, sha256, canonicalSha256, schemaVersion, uid, name}`` (hashes as ``agent.scripted_api`` computes them)."""
    path = Path(cfdproj).resolve()
    raw = path.read_bytes()
    document = json.loads(raw.decode("utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"{path} is not a .cfdproj object")
    return {"path": str(path), "sha256": api_identity.sha256_bytes(raw),
            "canonicalSha256": api_identity.canonical_sha256(document),
            "schemaVersion": document.get("schemaVersion"), "uid": document.get("uid"), "name": document.get("name")}


def validate_project(cfdproj: Union[PathLike, Mapping[str, Any]], *,
                     run_settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """``{ok, errors, preflightStops, seedCollisions, blockers, via}`` from the headless air export.

    A project object is written to a scratch ``.cfdproj`` first (removed after).
    """
    scratch = None
    if isinstance(cfdproj, Mapping):
        scratch = _scratch_dir("validate-")
        path = scratch / f"{uuid.uuid4().hex[:8]}.cfdproj"
        path.write_text(json.dumps(cfdproj, allow_nan=False), encoding="utf-8")
    else:
        path = Path(cfdproj).resolve()
    report: Dict[str, Any] = {"via": "agent.scripted_api.export_air_case", "ok": False, "errors": [],
                              "preflightStops": [], "seedCollisions": [], "blockers": []}
    try:
        export = api.export_air_case(path, run_settings=run_settings)
    except (HeadlessCompileError, ValueError) as exc:
        report["errors"] = [{"stage": "load/export", "message": str(exc)}]
        return report
    finally:
        if scratch is not None:
            shutil.rmtree(scratch, ignore_errors=True)
    errors = list(export.get("manifestErrors") or [])
    report.update(ok=not errors, errors=errors, preflightStops=list(export.get("preflightStops") or []),
                  seedCollisions=list((export.get("preflight") or {}).get("seedCollisions") or []),
                  blockers=export_blockers(export), hybrid=export.get("hybrid"),
                  cfdrunFilename=export.get("cfdrunFilename"))
    return report


AIR_EXPORT_FORMAT = "agent.tools.air-export/v1"


def export_air(cfdproj: PathLike, *, out_dir: PathLike, timestamp: Optional[str] = None,
               run_settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Write the editor's air export of ``cfdproj`` into a fresh ``out_dir``; its identity.

    :func:`agent.scripted_api.export_air_case` (the editor's own export, headless, no
    server): ``out_dir/air.cfdrun`` holds the manifest text exactly as exported
    and every STL file its ``geometryExport.stlFiles`` declares lands beside it.
    Nothing is written unless the export's STL batch is exactly the declared one
    and each file's SHA-256 equals its ``contentHash``; ``out_dir`` must not exist
    or be empty. ``timestamp`` (ISO 8601) fixes the manifest's ``exportedAt`` so
    a re-export is byte-identical.

    Returns ``{format, via, cfdproj, outDir, cfdrun, cfdrunFilename, cfdrunSha256,
    projectContentHash, stlSha256, manifestErrors, preflightStops, hybrid,
    timestamp}``; ``cfdproj`` is :func:`project_identity` of the source.
    """
    out = Path(out_dir)
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise FileExistsError(f"{out} exists and is not an empty directory")
    source = project_identity(cfdproj)
    export = api.export_air_case(source["path"], run_settings=run_settings, timestamp=timestamp)
    text = export["cfdrunJson"]
    manifest = json.loads(text)
    files = {name: content.encode("utf-8") for name, content in (export.get("stlFiles") or {}).items()}
    declared = {row["path"]: row["contentHash"] for row in manifest["geometryExport"]["stlFiles"].values()}
    hashes = {name: api_identity.sha256_bytes(data) for name, data in sorted(files.items())}
    if set(hashes) != set(declared):
        raise ValueError(f"exported STL files {sorted(hashes)} differ from the manifest's {sorted(declared)}")
    changed = sorted(name for name, digest in hashes.items() if declared[name] != digest)
    if changed:
        raise ValueError(f"exported STL bytes are not the ones the manifest binds: {changed}")
    if any(Path(name).name != name or name.startswith(".") for name in files):
        raise ValueError("an exported STL name is not a plain file name")
    out.mkdir(parents=True, exist_ok=True)
    (out / "air.cfdrun").write_bytes(text.encode("utf-8"))
    for name, data in files.items():
        (out / name).write_bytes(data)
    return {"format": AIR_EXPORT_FORMAT, "via": "agent.scripted_api.export_air_case", "cfdproj": source,
            "outDir": str(out.resolve()), "cfdrun": str((out / "air.cfdrun").resolve()),
            "cfdrunFilename": export.get("cfdrunFilename"),
            "cfdrunSha256": api_identity.sha256_bytes(text.encode("utf-8")),
            "projectContentHash": manifest["sourceProject"]["contentHash"], "stlSha256": hashes,
            "manifestErrors": list(export.get("manifestErrors") or []),
            "preflightStops": list(export.get("preflightStops") or []), "hybrid": export.get("hybrid"),
            "timestamp": manifest["sourceProject"].get("exportedAt")}


def export_liquid(cfdproj: PathLike, *, mode: Optional[str] = None, duration_s: Optional[float] = None,
                  solver_type: Optional[str] = None) -> Dict[str, Any]:
    """``{liquidrun, controllers, simulationMode, implementation}`` -- :func:`agent.scripted_api.export_liquidrun`.

    ``mode`` (``steady``/``transient``) and ``duration_s`` go through the
    editor's own liquid-mode and timeline edits; nothing is written.
    """
    return api.export_liquidrun(cfdproj, mode=mode, duration_s=duration_s, solver_type=solver_type)
