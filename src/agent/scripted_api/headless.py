"""Python side of the headless project compiler.

``src/frontend/react/src/headless/headless_compiler.mjs`` bundles the React app's own
export modules and runs them in a JSDOM window: the ``.cfdrun`` manifest and
grouped STL bytes it returns are the ones the editor would POST to
``/api/run-mesh`` for the same saved project, the ``.liquidrun`` is the one
the liquid panel would submit, and the hybrid project is the ``hybridHeat.project``
of the editor's hybrid solve request. ``build-project`` runs the automation
contract's document verbs (``projectBuilder.ts``) and returns the saved bytes;
``contract-verbs`` lists the live contract's keys and ``BUILD_VERBS`` (what
``agent.scripted_api.contract_verbs`` is checked against).
Python never re-implements any of it.

Node is located in this order: ``$DCDTUNI_NODE``; ``node`` on PATH;
``~/.local/opt/node-*/bin/node`` (newest). esbuild/jsdom/three come from
``$DCDTUNI_HEADLESS_NODE_MODULES`` when set, else
``src/frontend/react/node_modules``.
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional

from agent.scripted_api.identity import REPO

COMPILER = REPO / "src" / "frontend" / "react" / "src" / "headless" / "headless_compiler.mjs"


class HeadlessCompileError(RuntimeError):
    """The frontend export refused the project (the editor would show this message)."""


def find_node() -> str:
    explicit = os.environ.get("DCDTUNI_NODE")
    if explicit:
        if not (os.path.isfile(explicit) and os.access(explicit, os.X_OK)):
            raise FileNotFoundError(f"DCDTUNI_NODE={explicit!r} is not an executable file")
        return explicit
    on_path = shutil.which("node")
    if on_path:
        return on_path
    candidates = sorted(glob.glob(os.path.expanduser("~/.local/opt/node-v*/bin/node")))
    if candidates:
        return candidates[-1]
    raise FileNotFoundError("Node.js not found: set DCDTUNI_NODE, put node on PATH, or install it under ~/.local/opt")


def node_modules_dir() -> Path:
    explicit = os.environ.get("DCDTUNI_HEADLESS_NODE_MODULES")
    return Path(explicit).expanduser().resolve() if explicit else REPO / "src" / "frontend" / "react" / "node_modules"


OPERATIONS = ("air-case", "liquidrun", "hybrid-project", "build-project", "contract-verbs")


def compile_project(operation: str, project: Optional[Dict[str, Any]], project_path: Optional[str], *,
                    timestamp: Optional[str] = None, options: Optional[Dict[str, Any]] = None,
                    edits: Optional[list] = None, timeout: float = 600.0) -> Dict[str, Any]:
    """Run one headless compile. Returns the compiler's JSON reply.

    ``options`` (air-case, liquidrun, hybrid-project): ``solverType`` and
    ``liquidMode`` (``steady``/``transient``, through the editor's
    ``setSimulationMode``) and ``durationS`` (the timeline strip's duration
    edit), applied to the loaded project before the compile, so every
    operation of one run sees the same document. ``build-project`` takes
    ``edits`` (the step list) and ``project`` as the base document or ``None``.

    Raises :class:`HeadlessCompileError` with the export's own message when the
    frontend refuses the project, and ``RuntimeError`` for an environment fault.
    """
    if operation not in OPERATIONS:
        raise ValueError(f"unknown headless operation {operation!r}")
    modules = node_modules_dir()
    for package in ("esbuild", "jsdom", "three"):
        if not (modules / package / "package.json").is_file():
            raise RuntimeError(
                f"headless compiler needs {package} in {modules}; run npm install in src/frontend/react "
                f"or point DCDTUNI_HEADLESS_NODE_MODULES at an installed node_modules")
    request = {"operation": operation, "project": project, "projectPath": project_path}
    if timestamp is not None:
        request["timestamp"] = timestamp
    if options:
        request["options"] = options
    if edits is not None:
        request["edits"] = edits
    env = {**os.environ, "DCDTUNI_HEADLESS_NODE_MODULES": str(modules)}
    started = time.perf_counter()
    done = subprocess.run([find_node(), str(COMPILER)], input=json.dumps(request), capture_output=True,
                          text=True, timeout=timeout, env=env, cwd=str(REPO))
    try:
        reply = json.loads(done.stdout) if done.stdout.strip() else {}
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"headless compiler wrote non-JSON output (rc={done.returncode}): {exc}; "
                           f"stderr tail: {done.stderr[-2000:]}") from exc
    if done.returncode != 0 or "error" in reply:
        message = reply.get("error") or f"headless compiler exited {done.returncode}"
        error = HeadlessCompileError(message)
        error.stderr = done.stderr  # type: ignore[attr-defined]
        raise error
    reply.setdefault("implementation", {})["wallSeconds"] = time.perf_counter() - started
    reply["implementation"]["compilerStderrTail"] = done.stderr[-4000:]
    return reply
