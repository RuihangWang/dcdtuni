"""The agent skills (.claude/commands) and the API's user docs agree with the code they drive.

Every command a skill or doc shows has to be copy-runnable: bash blocks parse,
``python -m agent.scripted_api`` lines parse with the real CLI parser, launcher flags
exist, repository paths exist, and the facts that went stale once (launcher
ports, cookie location, convergence rule, case names, buildGeometryData being
async) are pinned to the code that defines them.
"""
from __future__ import annotations

import inspect
import json
import re
import shlex
import subprocess
from pathlib import Path
from typing import Iterator, List, Tuple

import pytest

from agent.scripted_api.__main__ import build_parser
from agent.scripted_api import contract_verbs as cv
from agent.scripted_api.identity import REPO

COMMANDS = REPO / ".claude" / "commands"
SKILLS = sorted(COMMANDS.glob("*.md"))
README = REPO / "README.md"
QUICK_START = REPO / "docs" / "user-guide" / "quick-start.md"
SCRIPTED_API = REPO / "docs" / "reference" / "scripted-api.md"
DOCS = SKILLS + [README, QUICK_START, SCRIPTED_API]
PAPER = REPO / "docs" / "manuscript" / "paper_experiment"
PAPER_SCRIPTS = [PAPER / "run_all_cases.py", PAPER / "batch_cfd_pipeline.py", PAPER / "score_case.py"]
IDS = lambda p: str(p.relative_to(REPO))  # noqa: E731

FENCE = re.compile(r"^(?P<indent>[ \t]*)```(?P<lang>[\w+-]*)[^\n]*\n(?P<body>.*?)^(?P=indent)```[ \t]*$",
                   re.M | re.S)


def _unquote(text: str) -> str:
    """Drop markdown blockquote markers so quoted code blocks are found too."""
    return "\n".join(re.sub(r"^>\s?", "", line) for line in text.splitlines())


def blocks(path: Path, langs: Tuple[str, ...] = ()) -> Iterator[Tuple[str, str]]:
    for match in FENCE.finditer(_unquote(path.read_text(encoding="utf-8"))):
        lang = match.group("lang").lower()
        if langs and lang not in langs:
            continue
        indent = match.group("indent")
        body = "\n".join(line[len(indent):] if line.startswith(indent) else line
                         for line in match.group("body").splitlines())
        yield lang, body


def bash_blocks(path: Path) -> List[str]:
    return [body for _, body in blocks(path, ("bash", "sh", "shell"))]


def logical_lines(script: str) -> List[str]:
    return script.replace("\\\n", " ").splitlines()


# ── every shell block parses ────────────────────────────────────────────────

@pytest.mark.parametrize("path", DOCS, ids=IDS)
def test_bash_blocks_are_valid_bash(path):
    for body in bash_blocks(path):
        done = subprocess.run(["bash", "-n"], input=body, capture_output=True, text=True)
        assert done.returncode == 0, f"{done.stderr}\n--- block ---\n{body}"


@pytest.mark.parametrize("path", DOCS, ids=IDS)
def test_python_blocks_compile(path):
    """A python block is a module, or an excerpt of a function body (``await`` / ``return`` at top level)."""
    for lang, body in blocks(path, ("python", "py")):
        try:
            compile(body, f"{path.name}:python", "exec")
        except SyntaxError as exc:
            if "outside" not in str(exc.msg):
                raise
            wrapped = "async def _excerpt():\n" + "\n".join("    " + line for line in body.splitlines())
            compile(wrapped, f"{path.name}:python-excerpt", "exec")


# ── python -m agent.scripted_api lines parse with the real CLI ─────────────────────

API_CALL = re.compile(r"python3?\s+-m\s+agent\.scripted_api(?P<module>\.contract_verbs)?\b(?P<rest>.*)")


def _argv(rest: str) -> List[str]:
    words = shlex.split(rest, comments=True)
    argv = []
    for word in words:
        if word in ("|", "||", "&&", ";") or word.startswith((">", "2>", "|")):
            break
        argv.append("8" if re.fullmatch(r"\$\{?\w+\}?|\$\(\(.*\)\)", word) else word)
    return argv


@pytest.mark.parametrize("path", DOCS, ids=IDS)
def test_backend_api_commands_parse(path):
    seen = 0
    for body in bash_blocks(path):
        for line in logical_lines(body):
            found = API_CALL.search(line)
            if not found:
                continue
            argv = _argv(found.group("rest"))
            seen += 1
            if found.group("module"):
                parser = cv.build_parser()
            else:
                parser = build_parser()
            try:
                parser.parse_args(argv)
            except SystemExit as exc:
                if exc.code not in (0, None):
                    pytest.fail(f"{path.name}: `{line.strip()}` does not parse: argv={argv}")
    if path.name in ("dc-run-cfd.md", "scripted-api.md", "README.md"):
        assert seen, "expected runnable agent.scripted_api examples"


# ── launcher invocations use flags the launcher (or server.py) knows ────────

def _launcher_flags() -> set:
    text = (REPO / "src" / "start_server.sh").read_text(encoding="utf-8")
    loop = text[text.index("while (($# > 0)); do"):text.index("done", text.index("while (($# > 0)); do"))]
    flags = set(re.findall(r"(--[a-z][a-z-]*)", loop)) | {"-h"}
    server = (REPO / "src" / "server" / "server.py").read_text(encoding="utf-8")
    flags |= set(re.findall(r"add_argument\(\s*'(--[a-z][a-z-]*)'", server))
    return flags


@pytest.mark.parametrize("path", DOCS, ids=IDS)
def test_launcher_invocations_use_known_flags(path):
    known = _launcher_flags()
    for body in bash_blocks(path):
        for line in logical_lines(body):
            at = line.find("start_server.sh")
            if at < 0 or line.lstrip().startswith("#"):
                continue
            words = _argv(line[at + len("start_server.sh"):])
            for i, word in enumerate(words):
                if word.startswith("-"):
                    flag = word.split("=", 1)[0]
                    assert flag in known, f"{path.name}: unknown launcher flag {flag} in `{line.strip()}`"
                    if flag == "--app":
                        value = word.split("=", 1)[1] if "=" in word else words[i + 1]
                        assert value in ("react", "html", "omniverse", "api"), line


# ── repository paths named in skills exist ──────────────────────────────────

GENERATED = ("src/frontend/react/dist", "src/frontend/react/node_modules", "src/server/auth-local", "_tmp/",
             ".codex-tmp/", "src/backend/kit-cae")
PATH_TOKEN = re.compile(r"`((?:src|docs|tools|case|\.claude)/[\w./-]+)`")


@pytest.mark.parametrize("path", [p for p in DOCS if not p.name.startswith("sync-")], ids=IDS)
def test_repository_paths_exist(path):
    missing = []
    for token in PATH_TOKEN.findall(path.read_text(encoding="utf-8")):
        token = token.rstrip(".")
        if token.startswith(GENERATED) or re.search(r"[A-Z]{3,}|\*", token):
            continue  # generated at run time, or a placeholder
        if not (REPO / token).exists():
            missing.append(token)
    assert not missing, missing


# ── DOM ids the skills drive exist in the editor ────────────────────────────

REACT_SRC = REPO / "src" / "frontend" / "react" / "src"
DOM_ID = re.compile(r"getElementById\(\s*['\"]([\w-]+)['\"]\s*\)|\b(?:canvas|dialog|button|div|input)#([\w-]+)")


def dom_ids(text: str) -> set:
    return {a or b for a, b in DOM_ID.findall(text)}


def _react_source() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in REACT_SRC.rglob("*.ts*"))


def _id_in_source(element_id: str, source: str) -> bool:
    """A literal id, or one built from a template (``tab-btn-${tab.id}`` with the tab id ``'postprocess'``)."""
    if element_id in source:
        return True
    prefix, _, suffix = element_id.rpartition("-")
    return bool(prefix) and f"{prefix}-${{" in source and f"'{suffix}'" in source


@pytest.mark.parametrize("path", SKILLS, ids=IDS)
def test_dom_ids_a_skill_drives_exist_in_the_editor(path):
    source = _react_source()
    missing = sorted(i for i in dom_ids(path.read_text(encoding="utf-8")) if not _id_in_source(i, source))
    assert not missing, missing


def test_dom_id_check_catches_the_chart_canvas_that_went_away():
    old = "var cv=document.getElementById('lsm-chart-canvas'); var t=document.getElementById('tab-btn-postprocess')"
    assert dom_ids(old) == {"lsm-chart-canvas", "tab-btn-postprocess"}
    source = _react_source()
    assert not _id_in_source("lsm-chart-canvas", source) and _id_in_source("tab-btn-postprocess", source)


# ── facts that went stale once ──────────────────────────────────────────────

@pytest.mark.parametrize("path", DOCS, ids=IDS)
def test_buildGeometryData_is_always_awaited(path):
    text = path.read_text(encoding="utf-8")
    assert "rebuilds synchronously" not in text
    for _, body in blocks(path):
        for match in re.finditer(r"(\bawait\s+)?[\w.$]*\bbuildGeometryData\s*\(", body):
            assert match.group(1), f"{path.name}: buildGeometryData() is async; await it: {body[:200]}"


@pytest.mark.parametrize("path", DOCS + PAPER_SCRIPTS, ids=IDS)
def test_cookie_jar_lives_in_the_repo_tmp(path):
    assert "/tmp/stlgen_cookie" not in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("path", DOCS, ids=IDS)
def test_launcher_port_semantics(path):
    """The launcher refuses a port another user holds and frees its own; it never moves to another pair."""
    text = path.read_text(encoding="utf-8")
    for stale in (r"different pair", r"may land on", r"it moves", r"auto-?scans?", r"scans upward",
                  r"next free port", r"\b8005\b"):
        assert not re.search(stale, text, re.I), f"{path.name}: stale launcher claim /{stale}/"


def test_launcher_default_port_is_what_the_docs_say():
    text = (REPO / "src" / "start_server.sh").read_text(encoding="utf-8")
    assert re.search(r"(?m)^PORT=8000$", text) and re.search(r"(?m)^APP=react\b", text)
    for path in (README, QUICK_START, COMMANDS / "browser-verify.md"):
        assert "8000" in path.read_text(encoding="utf-8"), path.name


@pytest.mark.parametrize("name", ["dc-builder.md", "dc-orchestrate.md", "dc-dataset-reverse.md"])
def test_pipeline_skills_never_restart_a_server_in_use(name):
    """Re-running the launcher on a port your server holds frees it -- killing that server and its job."""
    for body in bash_blocks(COMMANDS / name):
        if re.search(r"^\s*[^#\n]*start_server\.sh", body, re.M):
            assert "ss -tln" in body, f"{name}: start the launcher only after checking the port is free:\n{body}"


@pytest.mark.parametrize("path", SKILLS, ids=IDS)
def test_a_background_launcher_start_waits_for_the_server(path):
    """The next command logs in; started in the background, the server is not listening yet."""
    for body in bash_blocks(path):
        if re.search(r"^[^#\n]*nohup[^\n]*start_server\.sh[^\n]*&\s*$", body.replace("\\\n", " "), re.M):
            assert re.search(r"for i in \$\(seq [^)]*\); do curl -s[^\n]*/api/auth/check[^\n]*&& break; sleep",
                             body), f"{path.name}: wait for /api/auth/check after starting the launcher:\n{body}"


def test_orchestrate_health_check_is_authenticated():
    text = (COMMANDS / "dc-orchestrate.md").read_text(encoding="utf-8")
    assert "/api/auth/check" in text
    for body in bash_blocks(COMMANDS / "dc-orchestrate.md"):
        for line in logical_lines(body):
            if "/api/server-info" in line and "curl" in line:
                assert " -b " in line, line


def test_dc_analyze_states_the_return_air_rule():
    from backend.aircooling.runner import convergence
    tol = inspect.signature(convergence.temperature_series_verdict).parameters["plateau_tol"].default
    text = (COMMANDS / "dc-analyze.md").read_text(encoding="utf-8")
    assert f"{tol:g} K" in text
    assert "diagnostic" in text
    assert "residuals fall smoothly AND" not in text


def test_dc_run_cfd_covers_every_air_mode():
    text = (COMMANDS / "dc-run-cfd.md").read_text(encoding="utf-8")
    for needle in ("--mesh-only", "--solve-only", "--mesh-engine", "hybrid", "--allow-serial-solve", "batch",
                   "--solver-type"):
        assert needle in text, needle
    # meshgen was retired on 2026-10-26; the skill must not offer it as an engine.
    assert "--mesh-engine meshgen" not in text


def test_dc_builder_raised_floor_is_not_copied_in_with_models_at_floor_level():
    """A sheet at y = 0.4 in the same block as models at y = 0 cuts each one (layout_check: interpenetrate)."""
    found = []
    for _, body in blocks(COMMANDS / "dc-builder.md", ("python", "py")):
        if "Raised_Floor" not in body:
            continue
        found.append(body)
        heights = [float(y) for y in re.findall(r'"location":\s*\{[^}]*"y":\s*([-\d.]+)', body)]
        assert heights and all(y == heights[0] for y in heights), body
    assert found
    text = (COMMANDS / "dc-builder.md").read_text(encoding="utf-8")
    assert "interpenetrate" in text and "Raised_Floor" in text


def test_dataset_reverse_cases_exist_and_nothing_commits():
    text = (COMMANDS / "dc-dataset-reverse.md").read_text(encoding="utf-8")
    listed = re.search(r"\*\*Include these cfdproj files:\*\*\s*\n(.+)", text).group(1)
    names = [name.strip() for name in listed.split(",")]
    missing = [name for name in names if not list((REPO / "case").glob(f"*/{name}.cfdproj"))]
    assert not missing, missing
    assert "git commit" not in text and "git add" not in text


@pytest.mark.parametrize("name", ["sync-dcdtuni-dctwin.md", "sync-dctwin-dcdtuni.md"])
def test_sync_skills_cover_the_product_frontend_and_real_cases(name):
    text = (COMMANDS / name).read_text(encoding="utf-8")
    assert "src/frontend/react/" in text and "src/frontend/shared/" in text
    case_block = re.search(r"Case directories.*?```\n(.*?)```", text, re.S).group(1)
    dirs = case_block.split()
    missing = [d for d in dirs if not (REPO / "case" / d).is_dir()]
    assert dirs and not missing, missing
    always = re.search(r"\*\*Always sync\*\*(.*?)\n\*\*", text, re.S).group(1)
    ours = re.findall(r"`(src/(?:frontend|server|backend)/[\w./-]*|src/start_server\.sh)`", always)
    gone = [p for p in ours if not (REPO / p).exists()]
    assert ours and not gone, gone  # every dcdtuni-side path of the always-sync list exists here
    assert "src/frontend/react/" in always and "src/frontend/shared/" in always


def test_readme_documents_the_current_product():
    text = README.read_text(encoding="utf-8")
    assert re.search(r"(?m)^## Scripted / headless API", text)
    assert "docs/reference/scripted-api.md" in text
    assert "src/server/auth-local/admin_credentials" in text
    assert "dcCart" in text
    assert "ODIL liquid solver" not in text
    assert not re.search(r"\b\d{3,} tests\b", text), "test counts drift; do not hard-code them"


def test_quick_start_logs_in_with_the_local_credentials():
    text = QUICK_START.read_text(encoding="utf-8")
    assert "src/server/auth-local/admin_credentials" in text
    assert "dcCart" in text


def test_node_requirement_matches_package_json():
    engines = json.loads((REPO / "src" / "frontend" / "react" / "package.json").read_text())["engines"]["node"]
    for path in (README, SCRIPTED_API):
        assert engines in path.read_text(encoding="utf-8"), f"{path.name} should state engines.node {engines}"


def test_scripted_api_has_no_untested_dated_limitation():
    assert "967992b8" not in SCRIPTED_API.read_text(encoding="utf-8")


def test_scripted_api_signatures_match_the_code():
    """The Python stubs in scripted-api.md carry the real parameter names, kinds and defaults."""
    import ast

    import agent.scripted_api as api

    stubs = [body for _, body in blocks(SCRIPTED_API, ("python",)) if "def run_air_case" in body]
    assert len(stubs) == 1
    checked = 0
    for node in ast.parse(stubs[0]).body:
        if not isinstance(node, ast.FunctionDef):
            continue
        real = inspect.signature(getattr(api, node.name)).parameters
        args = node.args
        documented = [(a.arg, "positional") for a in args.args] + [(a.arg, "keyword") for a in args.kwonlyargs]
        actual = [(name, "keyword" if p.kind is p.KEYWORD_ONLY else "positional") for name, p in real.items()]
        assert documented == actual, node.name
        positional_defaults = dict(zip([a.arg for a in args.args][len(args.args) - len(args.defaults):],
                                       args.defaults))
        keyword_defaults = {a.arg: d for a, d in zip(args.kwonlyargs, args.kw_defaults) if d is not None}
        for name, expr in {**positional_defaults, **keyword_defaults}.items():
            value = getattr(api, expr.id) if isinstance(expr, ast.Name) else ast.literal_eval(expr)
            assert real[name].default == value, (node.name, name)
        for name, param in real.items():
            if name not in positional_defaults and name not in keyword_defaults:
                assert param.default is inspect.Parameter.empty, (node.name, name, "undocumented default")
        checked += 1
    assert checked == 9


# ── run length, early stop and short examples (2026-10-22) ─────────────────

def test_dc_analyze_states_the_run_length_minimum():
    from backend.aircooling.runner import convergence
    text = (COMMANDS / "dc-analyze.md").read_text(encoding="utf-8")
    assert f"{convergence.MIN_WINDOW_SAMPLES} iterations" in text
    assert f"{convergence.minimum_run_iterations()} iterations" in text
    assert "too-short" in text and "verdictReason" in text
    assert "at least 5" not in text


@pytest.mark.parametrize("path", [COMMANDS / "dc-analyze.md", SCRIPTED_API], ids=IDS)
def test_the_early_stop_is_documented_as_a_wall_clock_poll(path):
    from backend.aircooling.runner.stlgen_runner import STLGenRunner
    text = " ".join(path.read_text(encoding="utf-8").split())
    assert f"every {STLGenRunner._STEADY_STOP_POLL_SECONDS:g} s" in text
    assert "wall clock" in text and "DCDTUNI_RUNTIME_MODIFIABLE=false" in text


_END_TIME = re.compile(r'(?:--end-time\s+|"endTime":\s*)(\d+)')


@pytest.mark.parametrize("path", DOCS + PAPER_SCRIPTS[:2], ids=IDS)
def test_a_short_end_time_example_says_its_verdict_is_inconclusive(path):
    """An example too short for the return-air window says it only checks the pipeline."""
    from backend.aircooling.runner import convergence
    minimum = convergence.minimum_run_iterations()
    text = path.read_text(encoding="utf-8")
    for match in _END_TIME.finditer(text):
        if int(match.group(1)) >= minimum:
            continue
        following = text[match.end():match.end() + 1500]
        assert "INCONCLUSIVE" in following, f"{path.name}: {match.group(0)} with no INCONCLUSIVE note after it"


def test_scripted_api_result_table_matches_the_server_stage():
    text = SCRIPTED_API.read_text(encoding="utf-8")
    rows = {match.group(1): match.group(2) for match in re.finditer(r"^\| `([^`]+)`[^|]*\| (.*) \|$", text, re.M)}
    assert "startupSeconds" in rows["stages.server"] and "start time" not in rows["stages.server"]
    seconds = rows["stages.*.seconds"]
    assert "`server`" not in seconds
    for stage in ("export", "meshReuse", "hybridExport", "liquid", "mesh", "solve", "metrics"):
        assert f"`{stage}`" in seconds, stage
    convergence_row = rows["metrics.convergence"]
    assert "reason" in convergence_row and "minimumIterations" in convergence_row


def test_scripted_api_names_the_run_directory_by_command_not_kind():
    text = " ".join(SCRIPTED_API.read_text(encoding="utf-8").split())
    assert "__air_" in text and "`result.kind`" in text and "hybrid project" in text


def test_dc_analyze_reads_each_return_as_the_verdict_does():
    """A too-short run judges no single return either (convergence.temperature_series_verdict)."""
    text = " ".join((COMMANDS / "dc-analyze.md").read_text(encoding="utf-8").split())
    assert "`passed` is `null`" in text and "`TOO_SHORT`" in text
    assert "keyed by the return patch name" in text and '"return:<patch>"' in text


def test_the_saved_end_time_is_the_new_project_default_in_dc_run_cfd():
    text = " ".join((COMMANDS / "dc-run-cfd.md").read_text(encoding="utf-8").split())
    assert "default is 500" not in text
    assert "a new project saves 1500 iterations" in text
