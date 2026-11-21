"""CPU HTTP smoke check against a separately launched repository server.

Use src/start_server.sh to start that server first. Credentials are read from
the repository development guide and are never written to the report.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import http.cookiejar
import json
import math
from pathlib import Path
import re
import subprocess
import time
import urllib.request


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(repo, base, output):
    cookies = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookies))

    def request(method, path, payload=None):
        data = None if payload is None else json.dumps(payload, allow_nan=False).encode()
        req = urllib.request.Request(base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        with opener.open(req, timeout=120) as response:
            return json.load(response)

    guide = (repo / ".claude/CLAUDE.md").read_text()
    username = re.search(r"\| Username \| `([^`]+)`", guide).group(1)
    password = re.search(r"\| Password \| `([^`]+)`", guide).group(1)
    request("POST", "/api/auth/login", {"username": username, "password": password})
    info = request("GET", "/api/server-info")
    assert info["allOk"] and info["caseDir"].startswith(str(repo / ".codex-tmp"))
    report = {
        "status": "running", "source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip(),
        "scope": "actual HTTP DAE runtime, two mock fixtures; no browser or GPU",
        "execution": {"device": "cpu", "dtype": "float64", "max_threads": 2},
        "cases": [],
    }
    output.parent.mkdir(parents=True, exist_ok=True)

    def write():
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n")

    try:
        for name in ("nv-pek02-mock-v3.liquidrun", "shinaide-dae-v3-mock.liquidrun"):
            source = repo / "docs/benchmarks/fixtures" / name
            raw = json.loads(source.read_text())
            raw["execution"]["max_threads"] = 2
            started = time.perf_counter()
            created = request("POST", "/api/liquid/runs", {"liquidrun": raw})
            path = "/api/liquid/runs/" + created["run_id"]
            initial = created["frame"]
            assert created["metadata"]["solver"] == "differentiable_newton_dae"
            assert created["metadata"]["execution"]["actual_threads"] == 2
            pending = request("POST", path + "/inputs", {"heat_multiplier": 1.2})
            assert pending["frame"] == initial
            first = request("POST", path + "/step", {})["frame"]
            assert first["seq"] == 1 and first["heat_multiplier"] == 1.2
            equilibrium = request("POST", path + "/equilibrium", {})
            assert equilibrium["kind"] == "equilibrium"
            assert request("GET", path)["frame"] == first
            for _ in range(raw["time"]["N_t"] - 2):
                request("POST", path + "/step", {})
            result = request("GET", path + "/result")
            assert result["status"] == "completed" and result["converged"]
            assert len(result["time_s"]) == raw["time"]["N_t"]
            fields = ("node_pressure_Pa", "node_temperature_K", "edge_flow_kg_s",
                      "chip_temp_K", "cell_temp_K", "edge_outlet_temp_K",
                      "primary_return_K", "secondary_supply_K")
            for field in fields:
                assert result[field] and all(math.isfinite(v) for row in result[field] for v in row)
            balance = max(abs(d["energy_balance_W"]) for d in result["diagnostics"])
            assert balance < 1e-4
            accepted = request("GET", path + "/executed-input")
            replay_created = request("POST", "/api/liquid/runs", {"liquidrun": accepted})
            replay_path = "/api/liquid/runs/" + replay_created["run_id"]
            for _ in range(accepted["time"]["N_t"] - 1):
                request("POST", replay_path + "/step", {})
            replay = request("GET", replay_path + "/result")
            assert all(replay[field] == result[field] for field in fields)
            steady_raw = copy.deepcopy(raw)
            steady_raw.update(analysis={"mode": "steady", "initialization": "steady"}, events=[])
            steady_raw["time"]["N_t"] = 1
            steady = request("POST", "/api/liquid/runs", {"liquidrun": steady_raw})
            assert steady["status"] == "completed" and steady["frame"]["time_s"] == 0
            assert steady["frame"]["primary_return_K"] == initial["primary_return_K"]
            report["cases"].append({
                "fixture": str(source.relative_to(repo)), "fixture_sha256": sha(source),
                "input_change": "execution.max_threads=2; online heat multiplier=1.2 at first step",
                "run_id": created["run_id"], "replay_run_id": replay_created["run_id"],
                "steady_run_id": steady["run_id"], "frames": len(result["time_s"]),
                "final_time_s": result["time_s"][-1], "converged": result["converged"],
                "max_scaled_residual": result["final_loss"], "max_energy_balance_W": balance,
                "primary_return_K_initial": initial["primary_return_K"],
                "primary_return_K_final": [row[-1] for row in result["primary_return_K"]],
                "independent_equilibrium_preserved_transient": True,
                "steady_matches_initial_primary_return": True,
                "accepted_replay_exact_fields": list(fields),
                "elapsed_seconds": time.perf_counter() - started,
            })
            write()
            print(json.dumps({"fixture": name, "status": "passed", "frames": len(result["time_s"])}), flush=True)
        report["status"] = "passed"
    except Exception as error:
        report.update(status="failed", error_type=type(error).__name__, error=str(error))
        raise
    finally:
        write()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--base", default="http://127.0.0.1:8384")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    check(args.repo.resolve(), args.base, args.output)
