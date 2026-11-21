#!/usr/bin/env python3
"""Which server functions ``templates_ispo/direct_session.py`` ports, and the hash of each as it was ported.

The ispo profile ships no server: its in-process session repeats what these functions do. When one of them
changes, the port has to be looked at again; ``test_ispo_profile.py`` compares the hashes recorded in
``ported_server_functions.json`` with the sources and fails on a difference. After re-porting::

    python tools/dctwin_export/ported_server.py --record
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Dict

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
RECORD = HERE / "ported_server_functions.json"

PORTED = {
    "src/server/server.py": [
        # publication of an uploaded batch
        "_sanitize_cfdrun_filename", "_check_mesh_execution_budget", "_prepare_cfdrun_payload",
        "_write_active_cfdrun", "_decode_stl_uploads", "_archived_stl_batch_dir", "_stage_original_air_inputs",
        "_publish_case_input_batch", "_publish_case_input_snapshot",
        # the run job and its cancellation
        "_read_cfdrun_body_key", "_request_case_dir", "handle_run_mesh", "_new_job_state",
        "_raise_if_job_cancelled", "_start_snapshot_input", "_join_snapshot_input", "_spawn_job_process",
        "_finish_job", "_join_job_cleanup", "handle_abort_job", "_descendant_pgids", "_terminate_job_tree",
        "_any_alive",
        # what a finished job reports
        "_capture_solve_run_dir", "_solve_job_assessment", "_summarize_check_mesh_result",
        "_summarize_solve_mesh_quality_rejection", "_summarize_mesh_run_quality", "handle_job_status",
        "handle_log_stream",
    ],
    "src/server/liquid_runtime_api.py": ["_error", "handle_get", "handle_post"],
}
# the launcher's environment for OpenFOAM, repeated by direct_session.run_environment
SHELL_FUNCTIONS = {"src/start_server.sh": ["sanitize_foam_env"]}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def current(repo: Path = REPO) -> Dict[str, Dict[str, str]]:
    """``{file: {function: sha256 of its source text}}`` for every ported function, from ``repo``."""
    found: Dict[str, Dict[str, str]] = {}
    for name, functions in PORTED.items():
        text = (repo / name).read_text(encoding="utf-8")
        nodes = {node.name: node for node in ast.parse(text).body if isinstance(node, ast.FunctionDef)}
        missing = [function for function in functions if function not in nodes]
        if missing:
            raise KeyError(f"{name} no longer defines {missing}")
        found[name] = {function: _sha(ast.get_source_segment(text, nodes[function])) for function in functions}
    for name, functions in SHELL_FUNCTIONS.items():
        text = (repo / name).read_text(encoding="utf-8")
        found[name] = {}
        for function in functions:
            block = re.search(rf"^{re.escape(function)}\(\) \{{\n.*?^\}}\n", text, re.MULTILINE | re.DOTALL)
            if block is None:
                raise KeyError(f"{name} no longer defines {function}")
            found[name][function] = _sha(block.group(0))
    return found


def recorded() -> Dict[str, Dict[str, str]]:
    return json.loads(RECORD.read_text(encoding="utf-8"))


def changed(repo: Path = REPO) -> list:
    """``file:function`` of every ported function whose source differs from the recorded hash."""
    now, then = current(repo), recorded()
    return sorted(f"{name}:{function}" for name in set(now) | set(then)
                  for function in set(now.get(name, {})) | set(then.get(name, {}))
                  if now.get(name, {}).get(function) != then.get(name, {}).get(function))


if __name__ == "__main__":
    if sys.argv[1:] == ["--record"]:
        RECORD.write_text(json.dumps(current(), indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"recorded {sum(len(v) for v in current().values())} functions in {RECORD}")
    else:
        print("\n".join(changed()) or "no ported function changed")
