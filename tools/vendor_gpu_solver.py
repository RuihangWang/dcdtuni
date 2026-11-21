#!/usr/bin/env python3
"""Copy the GPU air solver from its development checkout into ``src/backend/aircooling/gpu_solver``.

The solver (a Newton-Krylov finite-volume solver for the steady buoyant air case, run by
``backend.aircooling.runner.gpu_newton``) is developed in its own repository; this project carries the files a
data-centre solve loads, copied verbatim. The copy is refreshed with

    python tools/vendor_gpu_solver.py --from <solver checkout>                    # the files of VENDORED.json
    python tools/vendor_gpu_solver.py --from <solver checkout> --trace a.json ... # a new file list

Which files: the modules a solve actually imports, recorded by the solver checkout's module trace (``--trace``,
JSON lists of paths under its ``src/``), plus ``--extra`` paths, closed over the imports those files make at module
level. Imports made inside functions that point outside the list are left out on purpose (options of the solver's
general entry that the data-centre recipe does not use) and are listed under ``notVendored`` in ``VENDORED.json``;
a solve that reaches one of them fails with an ``ImportError`` naming the module.

``VENDORED.json`` records the source commit, whether that tree was modified, and the SHA-256 of every file;
``--check`` compares the copy against it (a hand edit of a vendored file shows up there). Standard library only.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, Iterable, List, Set

REPO = Path(__file__).resolve().parents[1]
DEST = REPO / "src" / "backend" / "aircooling" / "gpu_solver"
MANIFEST = "VENDORED.json"
# files next to the modules that the solver reads at run time
DATA_FILES = ("Parse_mesh/fvconfig.toml",)
# loaded lazily from the solver core (not by an option of the general entry): carried even when no traced solve
# reached them
CORE_EXTRAS = (
    "Diagnostics/__init__.py", "Diagnostics/field_smoothness.py",
    "FVsolver/applications/buoyantBoussinesqpicoext_warp_cht.py",
    "FVsolver/applications/buoyantBoussinesqpicoext_warp_transient.py",
    "Parse_mesh/utils/utilities.py",
    "Parallel/__init__.py", "Parallel/newton.py", "Parallel/part.py",
    "Pipeline/CoupledNewton/block_amg_warp.py", "Pipeline/CoupledNewton/block_kernels.py",
    "Pipeline/CoupledNewton/block_kernels_warp.py", "Pipeline/CoupledNewton/block_pattern.py",
    "Pipeline/CoupledNewton/dc_adjoint.py", "Pipeline/CoupledNewton/dc_rans_framework.py",
    "Pipeline/CoupledNewton/hall_problem.py", "Pipeline/CoupledNewton/patch_binding.py",
    "Pipeline/CoupledNewton/steady_framework.py",
    "Post_process/dc_qoi.py",
    "SparseSolver/block_ilu.py", "SparseSolver/gcro.py", "SparseSolver/gmres_warp.py",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def resolve(src: Path, module: str) -> List[str]:
    """The files of ``src`` a dotted module name stands for (the module file and/or its package ``__init__``)."""
    base = src.joinpath(*module.split("."))
    found = []
    if base.with_suffix(".py").is_file():
        found.append(base.with_suffix(".py").relative_to(src).as_posix())
    if (base / "__init__.py").is_file():
        found.append((base / "__init__.py").relative_to(src).as_posix())
    return found


def imports_of(src: Path, rel: str):
    """``(target file, line, at module level)`` for every import in ``rel`` that resolves inside ``src``."""
    tree = ast.parse((src / rel).read_text(encoding="utf-8"), rel)
    package = Path(rel).parent.as_posix().replace("/", ".")
    package = "" if package == "." else package
    top = {id(node) for node in tree.body}
    # a module-level ``try: import x`` / ``if ...: import x`` counts as module level
    for node in tree.body:
        if isinstance(node, (ast.Try, ast.If, ast.With)):
            top.update(id(child) for child in ast.walk(node) if isinstance(child, (ast.Import, ast.ImportFrom)))
    for node in ast.walk(tree):
        names: List[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = package.split(".") if package else []
                parts = parts[:len(parts) - (node.level - 1)]
                base = ".".join([*parts, base]) if base else ".".join(parts)
            names = [base] + [f"{base}.{alias.name}" for alias in node.names]
        for name in names:
            parts = name.split(".")
            for depth in range(1, len(parts) + 1):               # the packages on the way are imported as well
                for hit in resolve(src, ".".join(parts[:depth])):
                    yield hit, node.lineno, id(node) in top


def close(src: Path, seeds: Iterable[str]):
    """``seeds`` closed over module-level imports; and what the result imports lazily from outside itself."""
    keep: Set[str] = set()
    todo = sorted(set(seeds))
    for rel in todo:
        if not (src / rel).is_file():
            raise SystemExit(f"{src / rel}: listed for vendoring, not in the solver checkout")
    left_out: Dict[str, Set[str]] = {}
    while todo:
        rel = todo.pop()
        if rel in keep:
            continue
        keep.add(rel)
        if not rel.endswith(".py"):
            continue
        for hit, line, at_module_level in imports_of(src, rel):
            if hit in keep:
                continue
            if at_module_level:
                todo.append(hit)
            else:
                left_out.setdefault(hit, set()).add(f"{rel}:{line}")
    return sorted(keep), {name: sorted(refs) for name, refs in sorted(left_out.items()) if name not in keep}


def git(checkout: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(checkout), *args], capture_output=True, text=True, check=True).stdout.strip()


def check() -> int:
    manifest = json.loads((DEST / MANIFEST).read_text(encoding="utf-8"))
    listed = manifest["files"]
    bad = [rel for rel, digest in listed.items() if not (DEST / rel).is_file() or sha256(DEST / rel) != digest]
    present = {p.relative_to(DEST).as_posix() for p in DEST.rglob("*")
               if p.is_file() and "__pycache__" not in p.parts and p.name not in (MANIFEST, "README.md")}
    stray = sorted(present - set(listed))
    for rel in bad:
        print(f"differs from {MANIFEST}: {rel}")
    for rel in stray:
        print(f"not in {MANIFEST}: {rel}")
    print(f"{len(listed)} files of solver commit {manifest['source']['commit'][:12]}: "
          + ("as vendored" if not bad and not stray else f"{len(bad)} changed, {len(stray)} stray"))
    return 1 if bad or stray else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--from", dest="source", help="the solver checkout (holds src/entries/dc_foam_solve.py)")
    ap.add_argument("--trace", nargs="*", default=[], help="module traces of solves (JSON lists of paths under src/); "
                                                           "without them the list of VENDORED.json is refreshed")
    ap.add_argument("--extra", nargs="*", default=[], help="further paths under src/ to carry")
    ap.add_argument("--check", action="store_true", help="compare the vendored copy against VENDORED.json")
    args = ap.parse_args(argv)
    if args.check:
        return check()
    if not args.source:
        ap.error("--from is required")
    checkout = Path(args.source).resolve()
    src = checkout / "src"
    if not (src / "entries" / "dc_foam_solve.py").is_file():
        raise SystemExit(f"{checkout}: no src/entries/dc_foam_solve.py")
    seeds: Set[str] = {"entries/dc_foam_solve.py", *args.extra}
    if args.trace:
        for trace in args.trace:
            seeds.update(json.loads(Path(trace).read_text(encoding="utf-8")))
    else:
        seeds.update(rel for rel in json.loads((DEST / MANIFEST).read_text(encoding="utf-8"))["files"]
                     if rel.endswith(".py"))
    seeds.update(CORE_EXTRAS)
    seeds.update(DATA_FILES)
    files, left_out = close(src, seeds)

    status = git(checkout, "status", "--porcelain", "--untracked-files=no", "--", "src")
    source = {"repository": "LearnedPreConditionNewtonSolver", "commit": git(checkout, "rev-parse", "HEAD"),
              "branch": git(checkout, "rev-parse", "--abbrev-ref", "HEAD"), "modified": bool(status),
              "subtree": "src"}
    if DEST.exists():
        for child in DEST.iterdir():
            if child.name == "README.md":
                continue
            shutil.rmtree(child) if child.is_dir() else child.unlink()
    digests = {}
    for rel in files:
        target = DEST / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src / rel, target)
        digests[rel] = sha256(target)
    manifest = {"format": "dcdtuni.gpu-solver-vendored/v1", "source": source,
                "vendoredAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "files": digests, "notVendored": left_out}
    (DEST / MANIFEST).write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    lines = sum((DEST / rel).read_text(encoding="utf-8", errors="replace").count("\n") for rel in files)
    print(f"{len(files)} files ({lines} lines) of {source['repository']} {source['commit'][:12]}"
          f"{' (modified tree)' if source['modified'] else ''} -> {DEST.relative_to(REPO)}; "
          f"{len(left_out)} modules referenced lazily and left out")
    return 0


if __name__ == "__main__":
    sys.exit(main())
