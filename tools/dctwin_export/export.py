#!/usr/bin/env python3
"""Export the dcdtuni design evaluator into a dctwin checkout (whitelist only).

What goes out: the scripted API (``backend.api``), the air-cooling runner and its OpenFOAM templates and solver
sources, the liquid-cooling DAE, the ISPO design modules (``dcdesign``, ``plant``, ``zonal``), the API-only part of
the server, and the project compiler as two prebuilt, minified browser bundles run in JSDOM. The cut-cell mesher
goes out as its built binary and library only.

What stays here: the React editor sources and every other frontend, the Omniverse display, SCANO inference
(``backend/aircooling/scano``, ``scano-Infra``, ``scano_infra``, ``scano_ai.py``), the mesher sources, the release-stamp check, the
agent layer, cases, docs and notes -- and the GPU air solver with everything that names it (owner 2026-11-11:
confidential, never to dctwin): ``backend/aircooling/gpu_solver``, its adapter ``runner/gpu_newton.py`` and the
``solverEngine`` hooks in the runner, the scripted API and the server. The leak scan refuses an export in which
any of these is left, so a revision that carries the solver is exportable only once the hooks are cut by a
transform here; until then the export fails and writes nothing.

Two profiles (``--profile``):

``dctwin`` (default)
    the export described above, into a dctwin git checkout.
``ispo``
    the owner's own evaluator folder inside the ISPO repository (owner 2026-11-11): the same whitelist plus the GPU
    air solver (``backend/aircooling/gpu_solver``, its adapter and hooks), written into a plain directory. It has
    no server (owner 2026-11-12): ``src/server``, the launcher and the server's client are left out, and the
    scripted API runs its jobs in process through ``backend/api/direct_session.py``, shipped from
    ``templates_ispo`` with the front door ``src/dceval`` (``ISPO_TEMPLATES``). Files a previous export wrote
    there (``EXPORT.json`` ``paths``) are replaced; every other file of that directory is left alone, and the
    paths ISPO maintains itself (``ISPO_OWN``: the renderer) are never written. The design modules (``dcdesign``,
    ``plant``, ``zonal``) are developed here and go out with every export (owner 2026-11-12: ISPO takes this
    repository's design and content through this profile only). ``EXPORT.json`` lists the SHA-256 of every
    file it wrote; ``python -m dceval check`` in the folder reports a file changed by hand.

Usage (from the dcdtuni main checkout, with the dcdtuni Python)::

    python tools/dctwin_export/export.py --source _tmp/wt-ispo-export --dest ~/mycode/dctwin
    python tools/dctwin_export/export.py --profile ispo --source _tmp/wt-ispo-export \
        --dest ~/mycode/ISPO-AgentForDesign/dc_evaluator

``--source`` is a clean checkout of the revision to export (it may be a worktree). ``--dest`` is a dctwin git
checkout; every tracked file there is replaced by the export (untracked files are left alone) and nothing is
committed. The run fails, writing nothing, when an expected text in a transformed file is missing (upstream
changed: update this tool) or when the leak scan finds an excluded path or a forbidden token in the result.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from strip_python import compile_patterns, strip_source  # noqa: E402

MAIN = HERE.parents[1]  # the dcdtuni checkout with node_modules and the built mesher (--main; default: this one)
PROFILE = "dctwin"
TEMPLATES = HERE / "templates"

INCLUDE = [
    "src/backend/__init__.py",
    "src/backend/api/",
    "src/backend/aircooling/__init__.py",
    "src/backend/aircooling/runner/",
    "src/backend/aircooling/utils/",
    "src/backend/aircooling/zonal.py",
    "src/backend/aircooling/third_parties/__init__.py",
    "src/backend/aircooling/third_parties/foam/",
    "src/backend/liquidcooling/",
    "src/backend/dcdesign/",
    "src/backend/plant/",
    "src/server/__init__.py",
    "src/server/server.py",
    "src/server/auth_manager.py",
    "src/server/security.py",
    "src/server/liquid_result_files.py",
    "src/server/liquid_run_catalog.py",
    "src/server/liquid_runtime_api.py",
]
SERVER_PREFIX = "src/server/"
SERVER_CLIENT = "src/backend/api/server_session.py"   # the scripted API's client of the private server

# Template files of the profile: name under TEMPLATES -> path in the export.
TEMPLATE_TARGETS = {"README.md": "README.md", "setup_env.sh": "setup_env.sh", "pyproject.toml": "pyproject.toml",
                    "gitignore": ".gitignore", "start_server.sh": "src/start_server.sh"}
# The ispo profile has no launcher; its session module, front door and their test are exporter-owned files here
# (they are staged like every other file, so EXPORT.json lists them and the next export replaces them).
ISPO_TEMPLATES = {"README.md": "README.md", "setup_env.sh": "setup_env.sh", "pyproject.toml": "pyproject.toml",
                  "gitignore": ".gitignore",
                  "direct_session.py": "src/backend/api/direct_session.py",
                  "dceval/__init__.py": "src/dceval/__init__.py",
                  "dceval/__main__.py": "src/dceval/__main__.py",
                  "test_direct_session.py": "src/backend/tests/test_direct_session.py"}

GPU_SOLVER_EXCLUDE = [
    r"^src/backend/aircooling/runner/gpu_newton\.py$",  # the GPU air solver's adapter (confidential, stays here)
]
EXCLUDE = [
    r"/__pycache__/",
    *GPU_SOLVER_EXCLUDE,
    r"^src/backend/api/contract_verbs\.py$",          # parses the editor's TypeScript; shipped as a table instead
    r"^src/backend/aircooling/runner/_debug_dump_registry\.py$",
]

# Projects shipped under their anonymised paths: the design families' base hall (backend.dcdesign.families).
PROJECTS = {
    "case/Shinaide_Liquid/Shinaide_liquid.cfdproj": "case/case_08_midsize_A_liquid/case_08_midsize_A_liquid.cfdproj",
}

# Tests that exercise only exported code (checked by running them on the export).
TESTS = [
    "src/backend/tests/__init__.py",
    "src/backend/tests/conftest.py",
    "src/backend/tests/test_perforated.py",
    "src/backend/tests/test_mesher_cyclic_porous_baffle.py",
    "src/backend/tests/test_dcdesign.py",
    "src/backend/tests/test_dcdesign_l0.py",
    "src/backend/tests/test_plant_energy.py",
    "src/backend/tests/test_zonal_network.py",
]

# Code developed in dctwin itself (the differentiable OpenFOAM path for ISPO): an export never deletes or overwrites it.
DCTWIN_OWN = ("src/backend/adjoint/", "dafoam/", "docs/adjoint/", "tests_adjoint/")


def dctwin_own(name: str) -> bool:
    """A path the destination maintains itself (dctwin's adjoint code; for ``ispo``, the paths of ISPO_OWN)."""
    return name.startswith(ISPO_OWN if PROFILE == "ispo" else DCTWIN_OWN)


# Paths that must never appear in the export, whatever INCLUDE says.
FORBIDDEN_PATHS = [
    r"^src/frontend/", r"omniverse", r"kit-cae", r"scano", r"^src/backend/aircooling/ai/", r"^src/backend/aircooling/scano/",
    r"dcCartMesh/(src|applications)/", r"release", r"RELEASE_STAMP", r"(^|/)CLAUDE(\.local)?\.md$",
    r"(^|/)AGENTS\.md$", r"^BHUMANS/", r"^case/(?!case_08_midsize_A_liquid/case_08_midsize_A_liquid\.cfdproj$)", r"^src/agent/", r"(^|/)ov_[^/]*\.py$", r"canonical_",
    r"render_scene", r"pp_ai_worker",
]
GPU_SOLVER_PATHS = [r"gpu_solver", r"gpu_newton", r"vendor_gpu_solver", r"GpuSolverStatus", r"solverEngine"]
FORBIDDEN_PATHS += GPU_SOLVER_PATHS

# Original case names (exported text uses the anonymised names), machine details, and modules that stay here.
CASE_NAMES = {
    "Alibaba_hangchou": "case_14_large_C", "Alibaba-sige": "case_13_large_B", "CMI-Guichou": "case_15_xlarge",
    "CMI_China": "case_07_compact", "GTC_2025": "case_12_large_A", "Base_coldAisleIso": "case_03_cold_aisle",
    "Base_leakage": "case_02_leakage", "Base2-9": "case_11_dense_B", "Base604": "case_10_dense_A",
    "DMAL_Testbed-patched": "case_01_testbed", "DMAL_Testbed": "case_01_testbed", "DAML-Testbed": "case_01_testbed",
    "Shinaide_Liquid": "case_08_midsize_A_liquid", "Shinaide_liquid": "case_08_midsize_A_liquid",
    "Shinaide": "case_08_midsize_A", "Alibaba-hongchou": "case_14_large_C", "CMI-China": "case_07_compact",
    "paintToDC1": "case_00_sketch", "Polygon_room": "case_04_polygon",
    "Tri_angle": "case_05_triangle", "STDCT": "case_09_standard",
}
FORBIDDEN_TOKENS = [
    r"(?i)alibaba", r"(?i)hangchou", r"(?i)hongchou", r"(?i)guichou", r"(?i)shinaide", r"(?i)base604", r"\bGTC\b", r"\bDAML\b", r"\bDMAL\b",
    r"paintToDC", r"\bSTDCT\b", r"CMI[_-]", r"/mnt/lv", r"litianyu", r"\b4090[12]\b", r"\bPro ?6000\b",
    r"release_check", r"release_stamp", r"owner-tree", r"scano_ai", r"kit-cae", r"(?i)omniverse",
    r"backend\.aircooling\.ai\b", r"backend\.aircooling\.scano\b", r"scano-Infra", r"zhaoshule", r"BHUMANS",
    r"(?i)polygon_room", r"(?i)tri_angle", r"(?i)base_leakage", r"(?i)coldaisleiso", r"(?i)base2-9",
    r"(?i)gtc_2025", r"(?i)dmal_testbed", r"(?i)daml-testbed", r"(?i)painttodc", r"(?i)\bstdct\b",
    r"(?i)dcdtuni",
]
# the GPU air solver (confidential): its code, adapter, switches and names
GPU_SOLVER_TOKENS = [
    r"gpu_newton", r"gpuNewton", r"gpu_solver", r"(?i)gpu[-_ ]solver", r"GPU_SOLVER", r"dc_foam_solve",
    r"solver[-_]engine", r"solverEngine", r"(?i)newton[-–]krylov", r"\bLPCNS\b", r"LearnedPreCondition", r"\bJFNK\b", r"\bJFGNK\b", r"(?i)\bdc_?rans\b",
]
FORBIDDEN_TOKENS += GPU_SOLVER_TOKENS

# The ispo profile: paths the ISPO repository maintains itself inside the evaluator folder (never written here).
# The design modules are not among them: they are developed in this repository and exported (owner 2026-11-12).
ISPO_OWN = ("src/backend/render/", "src/backend/tests/test_render", "OWNED.md")
GPU_SOLVER_DIR = "src/backend/aircooling/gpu_solver/"


def configure(profile: str, main: Path | None = None) -> None:
    """Select the profile: what is included, what the leak scan forbids, which templates are used."""
    global PROFILE, TEMPLATES, MAIN
    PROFILE = profile
    if main is not None:
        MAIN = main
    if profile == "ispo":
        TEMPLATES = HERE / "templates_ispo"
        INCLUDE.append(GPU_SOLVER_DIR)
        # no server: its modules, launcher and client stay here; the in-process session comes from the templates
        INCLUDE[:] = [inc for inc in INCLUDE if not inc.startswith(SERVER_PREFIX)]
        EXCLUDE.append("^" + re.escape(SERVER_CLIENT) + "$")
        FORBIDDEN_PATHS.extend(["^" + re.escape(SERVER_PREFIX), "^" + re.escape(SERVER_CLIENT) + "$",
                                r"(^|/)start_server\.sh$"])
        TESTS.append("src/backend/tests/test_dcdesign_hf.py")   # HF scoring with the plant model
        TEMPLATE_TARGETS.clear()
        TEMPLATE_TARGETS.update(ISPO_TEMPLATES)
        for rules, lifted in ((EXCLUDE, GPU_SOLVER_EXCLUDE), (FORBIDDEN_PATHS, GPU_SOLVER_PATHS),
                              (FORBIDDEN_TOKENS, GPU_SOLVER_TOKENS)):
            rules[:] = [rule for rule in rules if rule not in lifted]
    elif profile != "dctwin":
        raise ExportError(f"unknown profile {profile!r}")


class ExportError(RuntimeError):
    pass


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


def replace_once(text: str, old: str, new: str, where: str) -> str:
    count = text.count(old)
    if count != 1:
        raise ExportError(f"{where}: expected exactly one occurrence of {old[:80]!r}, found {count}")
    return text.replace(old, new)


# The scripted API and the design modules live in src/agent since 2026-11-16; the export keeps their former paths
# and module names (backend.api, backend.dcdesign, backend.plant), which is what dctwin and the ISPO folder import.
MOVED = (("src/backend/api/", "src/agent/scripted_api/"),
         ("src/backend/dcdesign/", "src/agent/dcdesign/"),
         ("src/backend/plant/", "src/agent/plant/"))


def exported_name(name: str) -> str:
    """The path a checkout file has in the export (its former path for the modules MOVED to src/agent)."""
    for old_p, new_p in MOVED:
        if name.startswith(new_p):
            return old_p + name[len(new_p):]
    return name


def checkout_name(name: str) -> str:
    """The inverse of :func:`exported_name`: where an exported file is in this checkout."""
    for old_p, new_p in MOVED:
        if name.startswith(old_p):
            return new_p + name[len(old_p):]
    return name


def exported_modules(text: str) -> str:
    """Python source with the module names of the export (agent.scripted_api -> backend.api, and so on)."""
    for old_p, new_p in MOVED:
        old_m, new_m = old_p[4:-1].replace("/", "."), new_p[4:-1].replace("/", ".")
        text = re.sub(rf"\b{re.escape(new_m)}\b", old_m, text)
    return text


def tracked_files(source: Path) -> List[str]:
    files = [exported_name(n) for n in git(source, "ls-files", "-z").split("\0")]
    exclude = [re.compile(p) for p in EXCLUDE]
    out = []
    for name in files:
        if not name:
            continue
        if not any(name == inc or (inc.endswith("/") and name.startswith(inc)) for inc in INCLUDE + TESTS):
            continue
        if any(p.search(name) for p in exclude):
            continue
        out.append(name)
    return sorted(out)


# ── transforms ──────────────────────────────────────────────────────────────

SERVER_DEFS = re.compile(
    r"PostProcessAIWorkerManager|_get_pp_ai_worker|handle_(pp|mesh_preview)_render_(source|grant)|handle_pp_ai_\w+"
    r"|handle_render_scene_export|handle_ov_\w+|_ov_\w+|handle_ai_scano_\w+|_scano_\w+|_start_scano_worker"
    r"|_scene_handoff_module|_local_tcp_listening")
SERVER_BRANCHES = compile_patterns(
    r"""['"]/api/(ov[/-]|ai/|pp/ai/|pp/render-|mesh-preview/render-|render-scene/)""", r"args\.scano", r"^ai_init$")
SERVER_STATEMENTS = compile_patterns(
    r"release_check", r"--scano", r"DCDTUNI_SCANO", r"args\.scano", r"_PP_AI_WORKER", r"_SCENE_HANDOFF\b",
    r"^_OV_\w+\s*=", r"--ai-gpu")
SERVER_DROP_LINES = [
    re.compile(r"^# SCANO AI solve"),
    re.compile(r"# The signed release stamp, before anything else"),
]


def transform_server(text: str) -> str:
    # The API-only server serves no page: its static root is a small directory holding the server's own strings.
    text = replace_once(text, 'SHARED_DIR = Path(__file__).resolve().parent.parent / "frontend" / "shared"\n',
                        'SHARED_DIR = Path(__file__).resolve().parent / "static"\n', "server.py")
    text = replace_once(
        text, '    ai_init = data.get("aiInit") if solve_only and not mesh_only else None\n',
        '    if data.get("aiInit"):\n'
        '        _send_json(handler, 400, {"code": "ai_init_unavailable",\n'
        '                                  "error": "AI initialisation is not part of this distribution"})\n'
        '        return\n', "server.py")
    text = replace_once(
        text, "        # Left running on purpose. It is shared with the Omniverse demo's\n"
              "        # head-verify scripts, costs nothing idle, and this repo's rules allow\n",
        "        # Left running on purpose. It costs nothing idle, and this repo's rules allow\n", "server.py")
    out, report = strip_source(text, defs=SERVER_DEFS, branch_tests=SERVER_BRANCHES, statements=SERVER_STATEMENTS,
                               drop_lines=SERVER_DROP_LINES)
    if len(report["defs"]) < 25 or len(report["branches"]) < 20:
        raise ExportError(f"server.py: the strip removed less than expected ({len(report['defs'])} definitions, "
                          f"{len(report['branches'])} branches); has the server changed?")
    return out


def transform_air_session(text: str) -> str:
    """The ispo profile: the scripted air run takes its session from the in-process module (air.SESSION_MODULE)."""
    return replace_once(text, 'SESSION_MODULE = "backend.api.server_session"\n',
                        'SESSION_MODULE = "backend.api.direct_session"\n', "backend/api/air.py")


FOAM_DIR = "src/backend/aircooling/third_parties/foam"


def _replace_block(text: str, start: str, end: str, new: str, where: str) -> str:
    a = text.find(start)
    b = text.find(end, a + len(start)) if a >= 0 else -1
    if a < 0 or b < 0 or text.count(start) != 1:
        raise ExportError(f"{where}: block {start[:40]!r} .. {end[:40]!r} not found exactly once")
    return text[:a] + new + text[b:]


def transform_foam_env(text: str) -> str:
    """The solver build takes the active conda environment with OpenFOAM, else ${DCTWIN_ENV:-dctwin}."""
    text = _replace_block(
        text, '__dc_repo="$(cd', 'if [ -z "${WM_OPTIONS:-}" ]; then',
        '# The conda environment with OpenFOAM: the active one when it carries the OpenFOAM headers,\n'
        '# else ${DCTWIN_ENV:-dctwin} (setup_env.sh).\n'
        'if [ -z "${CONDA_PREFIX:-}" ] || ! ls -d "${CONDA_PREFIX}"/include/OpenFOAM-* >/dev/null 2>&1; then\n'
        '    __conda_base="$(conda info --base 2>/dev/null)"\n'
        '    . "$__conda_base/etc/profile.d/conda.sh"\n'
        '    conda activate "${DCTWIN_ENV:-dctwin}"\n'
        'fi\n\n', f"{FOAM_DIR}/env.sh")
    return text.replace("Activate 'dcdtuni' first", "Activate the dctwin environment first").replace(
        "conda install -n dcdtuni", "conda install -n dctwin").replace("dcdtuni", "dctwin")


def transform_foam_deploy(text: str) -> str:
    text = _replace_block(
        text, 'command -v conda >/dev/null 2>&1 || die "conda not found on PATH."', 'ls -d "$PREFIX"/include/OpenFOAM-*',
        'PREFIX="${CONDA_PREFIX:-}"\n'
        'if [[ -z "$PREFIX" ]] || ! ls -d "$PREFIX"/include/OpenFOAM-* >/dev/null 2>&1; then\n'
        '    command -v conda >/dev/null 2>&1 || die "activate the dctwin conda environment first (setup_env.sh)"\n'
        '    PREFIX="$(conda env list | awk -v e="${DCTWIN_ENV:-dctwin}" \'$1==e{print $NF}\')"\n'
        'fi\n'
        '[ -x "$PREFIX/bin/python" ] || die "dctwin environment not found; run setup_env.sh"\n'
        'ok "conda prefix: $PREFIX"\n\n', f"{FOAM_DIR}/deploy.sh")
    return text.replace("dcdtuni", "dctwin")


def transform_headless_py(text: str) -> str:
    where = "backend/api/headless.py"
    text = replace_once(
        text, 'COMPILER = REPO / "src" / "frontend" / "react" / "src" / "headless" / "headless_compiler.mjs"',
        'COMPILER = REPO / "src" / "headless" / "headless_compiler.mjs"', where)
    text = replace_once(
        text, 'else REPO / "src" / "frontend" / "react" / "node_modules"',
        'else REPO / "src" / "headless" / "node_modules"', where)
    text = replace_once(text, 'for package in ("esbuild", "jsdom", "three"):', 'for package in ("jsdom",):', where)
    text = replace_once(text, "run npm install in src/frontend/react ", "run npm install in src/headless ", where)
    head, sep, body = text.partition('"""\nfrom __future__')
    if not sep:
        raise ExportError(f"{where}: module docstring not found")
    doc = ('"""Python side of the headless project compiler.\n\n'
           "``src/headless/headless_compiler.mjs`` runs the editor's export and modelling code (shipped prebuilt\n"
           "as ``compiler.min.js`` and ``builder.min.js``) in a JSDOM window: the ``.cfdrun`` manifest and grouped\n"
           "STL bytes it returns are the ones the editor would send to ``/api/run-mesh`` for the same saved project;\n"
           "``liquidrun`` and ``hybrid-project`` likewise; ``build-project`` applies automation-contract steps and\n"
           "returns the saved bytes.\n\n"
           "Node is located in this order: ``$DCDTUNI_NODE``; ``node`` on PATH; ``~/.local/opt/node-*/bin/node``.\n"
           "jsdom comes from ``$DCDTUNI_HEADLESS_NODE_MODULES`` when set, else ``src/headless/node_modules``.\n")
    return doc + sep + body


def transform_headless_mjs(text: str, bundles: Dict[str, str]) -> str:
    where = "headless_compiler.mjs"
    start = text.index("import fs from 'node:fs'")
    text = ("#!/usr/bin/env node\n"
            "/**\n"
            " * Headless .cfdproj compiler (no browser, no WebGL, no network, no server).\n"
            " *\n"
            " * Reads one JSON request on stdin and writes one JSON reply on stdout (operations air-case,\n"
            " * liquidrun, hybrid-project, build-project, contract-verbs; see backend/api/headless.py). The editor\n"
            " * code is the prebuilt bundle next to this file, evaluated inside a JSDOM window.\n"
            " */\n") + text[start:]
    text = replace_once(text, "path.dirname(fileURLToPath(import.meta.url)), '../../../../..')",
                        "path.dirname(fileURLToPath(import.meta.url)), '../..')", where)
    text = replace_once(text, "const reactDir = path.join(repo, 'src/frontend/react')",
                        "const reactDir = path.join(repo, 'src/headless')", where)
    text = replace_once(text, "const esbuild = require('esbuild')\n", "", where)
    for entry, bundle_name in (("projectCompiler.ts", "compiler.min.js"), ("projectBuilder.ts", "builder.min.js")):
        if f"'src/headless/{entry}'" not in text:
            raise ExportError(f"{where}: entry {entry} not found")
        text = text.replace(f"'src/headless/{entry}'", f"'{bundle_name}'")
    a = text.index("async function bundle(entry) {")
    b = text.index("\n}\n", a) + 3
    text = text[:a] + (
        "// The bundles are built from the editor sources by the exporter (minified, no source map).\n"
        "function bundle(name) {\n"
        "  const file = path.join(reactDir, name)\n"
        "  const code = fs.readFileSync(file, 'utf8')\n"
        "  return { code, sources: { [path.relative(repo, file)]: sha256(code) } }\n"
        "}\n") + text[b:]
    text = replace_once(text, "const { code, sources } = await bundle(path.join(reactDir, ENTRIES[operation]))",
                        "const { code, sources } = bundle(ENTRIES[operation])", where)
    text = replace_once(text, "packageVersions(['esbuild', 'jsdom', 'three', 'three-mesh-bvh', 'ajv'])",
                        "packageVersions(['jsdom'])", where)
    for name, digest in bundles.items():
        text += f"// {name} sha256 {digest}\n"
    if "esbuild" in text or "frontend/react" in text:
        raise ExportError(f"{where}: references to the source build remain")
    return text


def build_bundles(source: Path, node: str, out_dir: Path) -> Dict[str, str]:
    """Minified IIFE bundles of the two headless entries, built from the source checkout."""
    react = source / "src" / "frontend" / "react"
    modules = MAIN / "src" / "frontend" / "react" / "node_modules"
    script = HERE / "prebuild.mjs"
    done = subprocess.run([node, str(script), str(react), str(modules), str(out_dir)], capture_output=True, text=True)
    if done.returncode:
        raise ExportError(f"bundle build failed:\n{done.stderr[-4000:]}")
    digests = {}
    for name in ("compiler.min.js", "builder.min.js"):
        data = rename_tool_bytes((out_dir / name).read_bytes())
        (out_dir / name).write_bytes(data)
        if b"sourceMappingURL" in data:
            raise ExportError(f"{name} carries a source map")
        digests[name] = sha256(data)
    return digests


def contract_verbs_table(source: Path, python: str) -> str:
    done = subprocess.run([python, "-m", "agent.scripted_api.contract_verbs", "--markdown"], cwd=source / "src",
                          env={**os.environ, "PYTHONPATH": str(source / "src")}, capture_output=True, text=True)
    if done.returncode:
        raise ExportError(f"contract-verbs table failed: {done.stderr[-2000:]}")
    return done.stdout


# Bare short names, replaced as whole words only.
CASE_WORDS = {"DAML": "case_01_testbed", "DMAL": "case_01_testbed", "GTC": "case_12_large_A"}

# Other wording fixes in comments and docstrings: (pattern, replacement).
TEXT_SUBS = [
    (re.compile(r"\bPro ?6000 reference\b"), "reference implementation"),
    # Any spelling of the original case names left after the exact replacements above (e.g. lower case in ids).
    (re.compile(r"(?i)shinaide"), "case08"),
    (re.compile(r"(?i)base604"), "case10"),
    (re.compile(r"(?i)alibaba[-_ ]?(hangchou|hongchou)"), "case14"),
    (re.compile(r"(?i)alibaba[-_ ]?sige"), "case13"),
    (re.compile(r"(?:aircooling/scano/)?(?:scano[-_]infra/)?checkpoints/<dataset>/first_iteration\.json", re.IGNORECASE), "the model card's first_iteration.json"),
]


# The source repository's name is not shipped: identifiers, environment variables, format ids and the mesher's
# dictionary keywords take a neutral name of the same length (same length, so the built mesher can be patched in
# place and still reads the dictionaries the runner writes).
TOOL_NAME = {"dcdtuni": "dcmodel", "DCDTUNI": "DCMODEL", "Dcdtuni": "Dcmodel"}
TOOL_NAME_RE = re.compile(r"(?i)dcdtuni")


def rename_tool(text: str) -> str:
    return TOOL_NAME_RE.sub(lambda m: TOOL_NAME.get(m.group(0), "dcmodel"), text)


def rename_tool_bytes(data: bytes) -> bytes:
    for old, new in TOOL_NAME.items():
        data = data.replace(old.encode(), new.encode())
    return data


def anonymise(text: str) -> str:
    for original in sorted(CASE_NAMES, key=len, reverse=True):
        text = re.sub(re.escape(original), CASE_NAMES[original], text, flags=re.IGNORECASE)
    for word, anon in CASE_WORDS.items():
        text = re.sub(rf"\b{word}\b", anon, text)
    for pattern, replacement in TEXT_SUBS:
        text = pattern.sub(replacement, text)
    return text


AI_BOUNDARY_IMPORT = "from backend.aircooling.scano.foam_mesh import parse_boundary"


def move_parse_boundary(source: Path, stage: Path) -> None:
    """The runner imports one generic boundary-file parser from the AI package: ship it in polymesh_io instead."""
    import ast
    text = (source / "src/backend/aircooling/scano/foam_mesh.py").read_text()
    node = next((n for n in ast.parse(text).body if isinstance(n, ast.FunctionDef) and n.name == "parse_boundary"),
                None)
    if node is None:
        raise ExportError("foam_mesh.parse_boundary not found")
    io = stage / "src/backend/aircooling/runner/polymesh_io.py"
    io_text = io.read_text()
    for needed in ("import re\n", "from pathlib import Path\n", "from typing import Any, Dict, List, Optional\n"):
        if needed not in io_text:
            raise ExportError(f"polymesh_io.py no longer has {needed.strip()!r}")
    io_text = io_text.replace("from typing import Any, Dict, List, Optional\n",
                              "from typing import Any, Dict, List, Optional, Tuple\n", 1)
    io.write_text(io_text.rstrip("\n") + "\n\n\n" + ast.get_source_segment(text, node) + "\n")
    hits = 0
    for path in (stage / "src").rglob("*.py"):
        body = path.read_text()
        if AI_BOUNDARY_IMPORT in body:
            hits += body.count(AI_BOUNDARY_IMPORT)
            path.write_text(body.replace(AI_BOUNDARY_IMPORT,
                                         "from backend.aircooling.runner.polymesh_io import parse_boundary"))
    if not hits:
        raise ExportError("no import of foam_mesh.parse_boundary found; drop move_parse_boundary")


def scrub_binary(path: Path, rpath: str) -> None:
    """A built mesher names this machine twice: its rpath, and the source paths OpenFOAM's error macros compile
    in. The rpath becomes relative (the runner puts the environment's libraries on LD_LIBRARY_PATH); the home
    prefix of the source paths is overwritten with a string of the same length, so nothing else moves."""
    patchelf = shutil.which("patchelf") or str(Path(sys.executable).parent / "patchelf")
    subprocess.run([patchelf, "--set-rpath", rpath, str(path)], check=True)
    home = (str(Path.home()) + "/").encode()
    if len(home) < 12:
        raise ExportError(f"home prefix {home!r} too short to overwrite in place")
    replacement = b"/opt/build/" + b"w" * (len(home) - 12) + b"/"
    data = rename_tool_bytes(path.read_bytes().replace(home, replacement))
    # original case names in message strings: same-length placeholders
    for name in sorted(list(CASE_NAMES) + list(CASE_WORDS), key=len, reverse=True):
        bound = rb"\b" if name in CASE_WORDS else b""
        pattern = re.compile(bound + re.escape(name.encode()) + bound, re.IGNORECASE)
        placeholder = (b"case" + b"_" * max(len(name) - 4, 0))[:len(name)]
        data = pattern.sub(placeholder, data)
    path.write_bytes(data)


def leak_scan(root: Path, files: List[str]) -> List[str]:
    problems = []
    paths = [re.compile(p) for p in FORBIDDEN_PATHS]
    tokens = [re.compile(p) for p in [*FORBIDDEN_TOKENS, r"scano_infra"]]
    for name in files:
        if any(p.search(name) for p in paths):
            problems.append(f"path: {name}")
        data = (root / name).read_bytes()
        if b"\0" in data[:8192]:
            continue
        text = data.decode("utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            for p in tokens:
                if p.search(line):
                    problems.append(f"{name}:{i}: {p.pattern}: {line.strip()[:120]}")
    return problems


# ── main ────────────────────────────────────────────────────────────────────

def export(source: Path, dest: Path, node: str, python: str, *, dry_run: bool) -> dict:
    if git(source, "status", "--porcelain", "--untracked-files=no").strip():
        raise ExportError(f"{source} has uncommitted changes; export a committed revision")
    commit = git(source, "rev-parse", "HEAD").strip()
    stage = Path(tempfile.mkdtemp(prefix="dctwin-export-", dir=MAIN / "_tmp"))
    files = tracked_files(source)
    for name in files:
        target = stage / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / checkout_name(name), target)
        if name.endswith(".py"):
            target.write_text(exported_modules(target.read_text(encoding="utf-8")), encoding="utf-8")

    for original, anonymised in PROJECTS.items():
        (stage / anonymised).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / original, stage / anonymised)

    with_server = "src/server/server.py" in files
    if with_server:
        server = stage / "src/server/server.py"
        server.write_text(transform_server(server.read_text()))
    else:
        air = stage / "src/backend/api/air.py"
        air.write_text(transform_air_session(air.read_text()))
    move_parse_boundary(source, stage)
    for name, transform in (("env.sh", transform_foam_env), ("deploy.sh", transform_foam_deploy)):
        path = stage / FOAM_DIR / name
        text = transform(path.read_text())
        if PROFILE == "ispo":
            # the evaluator folder's environment is "dceval" (templates_ispo/setup_env.sh, DCEVAL_ENV)
            text = text.replace("DCTWIN_ENV", "DCEVAL_ENV").replace("dctwin", "dceval")
        path.write_text(text)
    for locale in ("en-US", "zh-CN") if with_server else ():
        table = json.loads((source / f"src/frontend/shared/i18n/{locale}.json").read_text())
        server_keys = {k: v for k, v in table.items() if k.startswith("server.")}
        if not server_keys:
            raise ExportError(f"no server.* strings in {locale}.json")
        target = stage / f"src/server/static/i18n/{locale}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(server_keys, ensure_ascii=False, indent=2) + "\n")
    headless_py = stage / "src/backend/api/headless.py"
    headless_py.write_text(transform_headless_py(headless_py.read_text()))

    headless_dir = stage / "src/headless"
    headless_dir.mkdir(parents=True)
    bundles = build_bundles(source, node, headless_dir)
    mjs = (source / "src/frontend/react/src/headless/headless_compiler.mjs").read_text()
    (headless_dir / "headless_compiler.mjs").write_text(transform_headless_mjs(mjs, bundles))
    jsdom_version = json.loads((MAIN / "src/frontend/react/node_modules/jsdom/package.json").read_text())["version"]
    (headless_dir / "package.json").write_text(json.dumps(
        {"name": "dctwin-headless", "private": True, "description": "Runtime of the headless project compiler",
         "dependencies": {"jsdom": jsdom_version}}, indent=2) + "\n")
    (stage / "docs").mkdir(exist_ok=True)
    (stage / "docs/contract-verbs.md").write_text(
        "# Modelling verbs\n\nThe steps `python -m backend.api build` accepts (`{\"verb\", \"args\"}`; `\"$N\"` is the "
        "value step N returned). Generated from the editor's automation contract by the exporter.\n\n"
        + "".join(line for line in contract_verbs_table(source, python).splitlines(keepends=True)
                  if not re.search(r"\bov[A-Z]\w*|setOv|[Oo]mniverse|scano|SCANO", line)))

    # The cut-cell mesher: built binary and library only, from a source tree identical to the exported revision.
    mesher_src = "src/backend/aircooling/third_parties/dcCartMesh"
    if git(MAIN, "status", "--porcelain", "--", mesher_src).strip():
        raise ExportError(f"{MAIN / mesher_src} has uncommitted changes; the built mesher would not match")
    if git(MAIN, "rev-parse", f"HEAD:{mesher_src}/src") != git(source, "rev-parse", f"HEAD:{mesher_src}/src") or \
            git(MAIN, "rev-parse", f"HEAD:{mesher_src}/applications") != \
            git(source, "rev-parse", f"HEAD:{mesher_src}/applications"):
        raise ExportError("the mesher sources of the exported revision differ from the built ones in the main checkout")
    last_change = int(git(MAIN, "log", "-1", "--format=%ct", "--", f"{mesher_src}/src", f"{mesher_src}/applications"))
    platforms = MAIN / mesher_src / "platforms"
    built = []
    binaries = []
    for kind, name in (("bin", "dcCartMesh"), ("lib", "libdcCartMesh.so")):
        found = sorted(platforms.glob(f"*/{kind}/{name}"))
        if len(found) != 1:
            raise ExportError(f"expected one built {name} under {platforms}, found {len(found)}")
        if found[0].stat().st_mtime < last_change:
            raise ExportError(f"{found[0]} is older than the last mesher source change; rebuild it")
        rel = found[0].relative_to(MAIN)
        (stage / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(found[0], stage / rel)
        scrub_binary(stage / rel, "$ORIGIN/../lib" if kind == "bin" else "$ORIGIN")
        built.append({"path": str(rel), "sha256": sha256(found[0].read_bytes()),
                      "shipped_sha256": sha256((stage / rel).read_bytes())})
        binaries.append(str(rel))
    (stage / mesher_src / "README.md").write_text(
        "# Cut-cell mesher (binary)\n\n`platforms/<tag>/bin/dcCartMesh` and `lib/libdcCartMesh.so` are built against\n"
        "conda-forge `openfoam=2412` with `mpich` (the environment `setup_env.sh` creates). The runner finds them\n"
        "here and puts the library directory on `LD_LIBRARY_PATH`. Sources are not distributed.\n")

    for name, placed in TEMPLATE_TARGETS.items():
        target = stage / placed
        if PROFILE == "ispo" and target.exists():
            raise ExportError(f"template {name} would overwrite the exported {placed}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(TEMPLATES / name, target)

    all_files = sorted(str(p.relative_to(stage)) for p in stage.rglob("*") if p.is_file())
    clash = [name for name in all_files if dctwin_own(name)]
    if clash:
        raise ExportError(f"the export would write paths the destination owns: {clash[:5]}")
    for name in all_files:
        path = stage / name
        data = path.read_bytes()
        if b"\0" in data[:8192] or name.endswith(".min.js"):
            continue
        text = data.decode("utf-8")
        new = rename_tool(anonymise(text))
        if new != text:
            path.write_text(new)

    problems = leak_scan(stage, [f for f in all_files if not f.endswith(".min.js")])
    # The bundles: no host paths, user names or case names.
    bundle_tokens = re.compile(r"(?i)/mnt/|/home/|litianyu|alibaba|hangchou|hongchou|guichou|shinaide|base604|\bDAML\b|\bDMAL\b|"
                               r"paintToDC|STDCT|CMI[_-]|release_stamp|kit-cae|dcdtuni")
    for name in bundles:
        for m in bundle_tokens.finditer((headless_dir / name).read_text()):
            problems.append(f"src/headless/{name}: {m.group(0)}")
    for name in binaries:
        for text in re.findall(rb"[\x20-\x7e]{4,}", (stage / name).read_bytes()):
            line = text.decode("ascii")
            for p in FORBIDDEN_TOKENS + [r"/home/"]:
                if re.search(p, line):
                    problems.append(f"{name}: {p}: {line[:120]}")
    manifest = {
        "format": "dctwin.export/v1",
        "profile": PROFILE,
        "exported_at": dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(timespec="seconds"),
        "source_commit": commit,
        "bundles": bundles,
        "mesher": built,
        "files": len(all_files),
    }
    if PROFILE == "ispo":
        vendored = json.loads((source / GPU_SOLVER_DIR / "VENDORED.json").read_text())
        manifest["gpu_solver"] = {"commit": vendored["source"]["commit"], "files": len(vendored["files"])}
        manifest["paths"] = all_files            # what the next export of this folder replaces
        # what `python -m dceval check` compares the folder with: a hand edit of an exported file is reported
        manifest["sha256"] = {name: sha256((stage / name).read_bytes()) for name in all_files}
    (stage / "EXPORT.json").write_text(json.dumps(manifest, indent=2) + "\n")
    if problems:
        report = stage / "LEAK_REPORT.txt"
        report.write_text("\n".join(problems) + "\n")
        raise ExportError(f"leak scan found {len(problems)} problem(s); see {report} (staging kept at {stage})")
    if dry_run:
        print(f"dry run: staged at {stage}")
        return manifest

    if PROFILE == "ispo":
        # A plain directory: replace what the previous export wrote there, leave every other file alone.
        previous = dest / "EXPORT.json"
        old_paths = json.loads(previous.read_text()).get("paths", []) if previous.is_file() else []
        if dest.exists() and not previous.is_file() and any(dest.iterdir()):
            raise ExportError(f"{dest} is not empty and has no EXPORT.json: not a folder this tool wrote")
        for name in old_paths:
            if not dctwin_own(name):
                (dest / name).unlink(missing_ok=True)
        for name in all_files + ["EXPORT.json"]:
            target = dest / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(stage / name, target)
        for path in sorted(dest.rglob("*"), reverse=True):
            if path.is_dir() and "node_modules" not in path.parts and not any(path.iterdir()):
                path.rmdir()
        shutil.rmtree(stage)
        return manifest

    # Replace the tracked tree of the dctwin checkout with the staged export, except dctwin's own code.
    for name in git(dest, "ls-files", "-z").split("\0"):
        if name and not dctwin_own(name):
            (dest / name).unlink(missing_ok=True)
    for path in sorted(dest.rglob("*"), reverse=True):
        if path.is_dir() and ".git" not in path.parts and not any(path.iterdir()):
            path.rmdir()
    for name in all_files + ["EXPORT.json"]:
        target = dest / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(stage / name, target)
    shutil.rmtree(stage)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--source", required=True, type=Path, help="clean checkout of the revision to export")
    parser.add_argument("--dest", required=True, type=Path,
                        help="dctwin git checkout to write into (profile ispo: the evaluator folder)")
    parser.add_argument("--profile", choices=("dctwin", "ispo"), default="dctwin",
                        help="dctwin: without the GPU air solver; ispo: the owner's evaluator folder, with it "
                             "and without a server")
    parser.add_argument("--main", type=Path, default=None,
                        help="the checkout with node_modules and the built mesher (default: this tool's checkout)")
    parser.add_argument("--node", default=shutil.which("node"), help="Node.js used to build the bundles")
    parser.add_argument("--python", default=sys.executable, help="Python with the backend's dependencies")
    parser.add_argument("--dry-run", action="store_true", help="stage and scan only")
    args = parser.parse_args()
    try:
        configure(args.profile, args.main.expanduser().resolve() if args.main else None)
        manifest = export(args.source.resolve(), args.dest.expanduser().resolve(), args.node, args.python,
                          dry_run=args.dry_run)
    except ExportError as exc:
        print(f"export failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
