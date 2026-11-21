"""The platform does not import the agent layer: ``backend.aidesign`` is retired."""
from __future__ import annotations

import ast
import importlib.util
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DYNAMIC = re.compile(r'(?:import_module|run_module)\(\s*["\'](agent(?:\.\w+)*)["\']')


def imported_modules(path: Path) -> set[str]:
    source = path.read_text(encoding="utf-8", errors="replace")
    names = set(DYNAMIC.findall(source))
    for node in ast.walk(ast.parse(source, filename=str(path))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.add(node.module)
    return names


def python_files(roots) -> list[Path]:
    """Tracked and new (not ignored) .py files; skips ignored trees such as kit-cae."""
    listed = subprocess.run(["git", "ls-files", "-z", "--cached", "--others",
                             "--exclude-standard", "--", *roots],
                            cwd=REPO, check=True, capture_output=True).stdout
    return [REPO / p for p in sorted(set(listed.decode().split("\0")) - {""})
            if p.endswith(".py") and (REPO / p).is_file()]


def offenders(roots, wanted) -> list[str]:
    found = []
    for path in python_files(roots):
        hits = sorted(n for n in imported_modules(path) if wanted(n))
        if hits:
            found.append(f"{path.relative_to(REPO)}: {', '.join(hits)}")
    return found


def test_backend_never_imports_agent():
    # The platform code never imports the agent layer. The suite under src/backend/tests also tests the scripted API
    # and the design modules, which live in src/agent since 2026-11-16 (agent.scripted_api, agent.dcdesign,
    # agent.plant), so it may import them; it is test code, not platform code.
    found = offenders(["src/backend"], lambda n: n == "agent" or n.startswith("agent."))
    assert [f for f in found if not f.startswith("src/backend/tests/")] == []


def test_backend_aidesign_is_gone():
    assert not (REPO / "src/backend/aidesign").exists()
    assert importlib.util.find_spec("backend.aidesign") is None


def test_maintained_code_uses_agent_harness_not_backend_aidesign():
    roots = ["src/backend", "src/server", "src/agent", "tools"]
    assert offenders(roots, lambda n: n.startswith("backend.aidesign")) == []
