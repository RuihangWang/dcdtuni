"""Headless modelling: a list of automation-contract steps -> a saved ``.cfdproj``.

``build_project(steps, base=None, out=...)`` runs the editor's own automation
contract (``window.__dcdtuni``, ``src/frontend/react/src/automation/contract.ts``)
in JSDOM through the headless compiler (operation ``build-project``,
``src/frontend/react/src/headless/projectBuilder.ts``). Only the verbs that
edit or read the document are open -- create, place, patch, rack servers, run
configuration, ``setProjectMeta`` and the store's document setters; the scene,
view, selection, server and history verbs are closed. A step is
``{"verb": name, "args": [...]}``; a string argument ``"$N"`` is what step N
returned (a create's uid, ``addPatch``'s patch id, ``addRackServerAtRange``'s
server uid). Every edit goes through ``projectStore.edit``, exactly as a click.

The output is the bytes the editor's save button writes. Right after the steps
the saved bytes go through the editor's air export (``exportAirCase``) as a
self-check: schema errors, pre-flight stops and export refusals are reported
in ``selfCheck`` (the file is still written; ``selfCheck.ok`` says whether an
air run of it would start). A failed step writes nothing and reports the step.
An existing ``out`` is never replaced unless ``overwrite=True``.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from agent.scripted_api import identity as ident
from agent.scripted_api.headless import HeadlessCompileError, compile_project

BUILD_FORMAT = "dcdtuni.api.build/v1"


def load_build_spec(path: Path | str) -> Dict[str, Any]:
    """``spec.json``: a list of steps, or ``{"steps": [...], "base": "Y.cfdproj"}`` (base relative to the file)."""
    from agent.scripted_api.rundir import read_json_source
    path = Path(path).resolve()
    _, spec = read_json_source(path)
    if isinstance(spec, list):
        spec = {"steps": spec}
    if not isinstance(spec, dict) or not isinstance(spec.get("steps"), list):
        raise ValueError(f"{path}: expected a list of steps or an object with a 'steps' list")
    unknown = sorted(set(spec) - {"steps", "base"})
    if unknown:
        raise ValueError(f"{path}: unknown key(s) {unknown}; a build spec has 'steps' and optional 'base'")
    if spec.get("base") is not None:
        spec["base"] = str((path.parent / spec["base"]).resolve())
    return spec


def build_project(steps: Sequence[Dict[str, Any]], *, out: Path | str, base: Path | str | None = None,
                  overwrite: bool = False, timeout: float = 600.0) -> Dict[str, Any]:
    """Run ``steps`` on ``base`` (a ``.cfdproj``) or a new project; write ``out``; return the build report.

    ``status`` is ``succeeded`` when every step ran and ``out`` was written
    (``failure = {stage, reason, step}`` otherwise: ``input`` for a bad
    request, ``build`` for a failed step). ``selfCheck`` is reported separately.
    """
    started_wall, started = ident.now_sgt(), time.perf_counter()
    out = Path(out).resolve()
    report: Dict[str, Any] = {"format": BUILD_FORMAT, "status": "failed", "failure": None, "out": str(out),
                              "base": None if base is None else str(Path(base).resolve()),
                              "startedAt": ident.iso(started_wall), "steps": [], "selfCheck": None}
    try:
        if not isinstance(steps, (list, tuple)):
            raise ValueError("steps must be a list of {verb, args} objects")
        if out.suffix != ".cfdproj":
            raise ValueError(f"out must be a .cfdproj path, got {out.name}")
        if out.exists() and not overwrite:
            raise ValueError(f"{out} exists; pass overwrite=True (--overwrite) to replace it")
        base_project: Optional[Dict[str, Any]] = None
        if base is not None:
            base_raw = Path(base).read_bytes()
            base_project = json.loads(base_raw.decode("utf-8"))
            report["baseSha256"] = ident.sha256_bytes(base_raw)
    except (OSError, ValueError) as exc:
        report["failure"] = {"stage": "input", "reason": f"{type(exc).__name__}: {exc}", "step": None}
        report["elapsedSeconds"] = time.perf_counter() - started
        return report
    stamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    try:
        reply = compile_project("build-project", base_project, str(out), timestamp=stamp, edits=list(steps),
                                timeout=timeout)
    except HeadlessCompileError as exc:
        report["failure"] = {"stage": "build", "reason": str(exc), "step": None}
        report["elapsedSeconds"] = time.perf_counter() - started
        return report
    report["steps"] = reply.get("steps") or []
    report["implementation"] = {k: v for k, v in (reply.get("implementation") or {}).items()
                                if k != "compilerStderrTail"}
    if not reply.get("ok"):
        failure = reply.get("failure") or {}
        report["failure"] = {"stage": "build", "step": failure.get("step"), "verb": failure.get("verb"),
                             "reason": f"step {failure.get('step')} ({failure.get('verb')}): {failure.get('reason')}"}
        report["elapsedSeconds"] = time.perf_counter() - started
        return report
    text = reply["cfdproj"]
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w" if overwrite else "x", encoding="utf-8") as stream:
        stream.write(text)
    report.update(status="succeeded", sha256=ident.sha256_bytes(text.encode("utf-8")), bytes=len(text.encode("utf-8")),
                  modelCount=reply.get("modelCount"), selfCheck=reply.get("selfCheck"))
    report["finishedAt"] = ident.iso(ident.now_sgt())
    report["elapsedSeconds"] = time.perf_counter() - started
    return report


def steps_summary(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    """One line per step for a CLI summary: index, verb and what it returned."""
    return [{"index": row.get("index"), "verb": row.get("verb"), "result": row.get("result")}
            for row in report.get("steps") or []]
