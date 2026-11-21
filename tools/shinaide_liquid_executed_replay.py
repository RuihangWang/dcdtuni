#!/usr/bin/env python3
"""Fresh full-horizon solve of a browser's materialized accepted-input manifest.

This does not restore a checkpoint or replay its states. The checkpoint is only
read as an independently inspectable accepted-command ledger for boundary audits.
Original evidence files are never changed. Run with CPU float64 and one thread.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np
import torch

from backend.liquidcooling.dae_runtime import DAESimulation, admit_dae_runtime_input

PHYSICAL_FIELDS = (
    "time_s", "node_pressure_Pa", "node_temperature_K", "edge_flow_kg_s",
    "chip_temp_K", "edge_outlet_temp_K", "primary_return_K",
    "secondary_supply_K", "cell_temp_K", "water_outlet_temp_K",
)
CONTROL_FIELDS = ("pump_rpm_ratios", "valve_openings", "primary_inlet_K")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def sample(value, index):
    return value[index] if isinstance(value, list) else value


def numeric_comparison(original, recomputed):
    a = np.asarray(original, dtype=np.float64)
    b = np.asarray(recomputed, dtype=np.float64)
    if a.shape != b.shape:
        return {"shape_original": list(a.shape), "shape_recomputed": list(b.shape),
                "same_shape": False, "exact": False}
    delta = np.abs(a - b)
    flat = int(np.argmax(delta)) if delta.size else 0
    return {"shape": list(a.shape), "same_shape": True,
            "finite": bool(np.isfinite(a).all() and np.isfinite(b).all()),
            "exact": bool(np.array_equal(a, b)),
            "max_abs_difference": float(delta.max()) if delta.size else 0.0,
            "max_difference_index": [int(x) for x in np.unravel_index(flat, delta.shape)] if delta.size else [],
            "nonidentical_values": int(np.count_nonzero(a != b))}


def tree_comparison(original, recomputed):
    """Compare nested physical diagnostics, retaining each nonidentical leaf."""
    mismatches = []

    def visit(a, b, path):
        if isinstance(a, dict) and isinstance(b, dict):
            if set(a) != set(b):
                mismatches.append({"path": path, "key_difference": sorted(set(a) ^ set(b))})
            for key in a.keys() & b.keys():
                visit(a[key], b[key], path + "/" + key)
        elif isinstance(a, list) and isinstance(b, list):
            if len(a) != len(b):
                mismatches.append({"path": path, "lengths": [len(a), len(b)]})
            else:
                for i, (x, y) in enumerate(zip(a, b)):
                    visit(x, y, path + "/" + str(i))
        elif a != b:
            entry = {"path": path, "original": a, "recomputed": b}
            if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                entry["absolute_difference"] = abs(a - b)
            mismatches.append(entry)

    visit(original, recomputed, "")
    numeric = [x["absolute_difference"] for x in mismatches if "absolute_difference" in x]
    return {"exact": not mismatches, "nonidentical_leaves": len(mismatches),
            "max_numeric_abs_difference": max(numeric, default=0.0),
            "first_mismatches": mismatches[:20]}


def boundary_audit(original, executed, checkpoint, definition):
    """Independently inspect JSON schedules and all compiled parameter samples."""
    count = executed["time"]["N_t"]
    original_edges = {e["id"]: e for e in original["edges"]}
    executed_edges = {e["id"]: e for e in executed["edges"]}
    traces = {key: checkpoint[key] for key in CONTROL_FIELDS}
    failures = []
    examined = 0
    for edge_id, edge in original_edges.items():
        if edge["type"] != "cold_plate":
            continue
        for name in ("Q_W", "dT_set_K"):
            if name not in edge["params"]:
                continue
            for i in range(count):
                expected = sample(edge["params"][name], i) * checkpoint["heat_multipliers"][i]
                actual = sample(executed_edges[edge_id]["params"][name], i)
                examined += 1
                if expected != actual:
                    failures.append({"edge_id": edge_id, "field": name, "seq": i,
                                     "expected": expected, "actual": actual})
    for field, param_name in (("pump_rpm_ratios", "rpm_ratio"), ("valve_openings", "opening")):
        for i, trace in enumerate(traces[field]):
            for edge_id, expected in trace.items():
                actual = sample(executed_edges[edge_id]["params"][param_name], i)
                examined += 1
                if actual != expected:
                    failures.append({"edge_id": edge_id, "field": param_name, "seq": i,
                                     "expected": expected, "actual": actual})
    circuits = {c["edge_id"]: c for c in executed["primary_circuits"]}
    for i, trace in enumerate(traces["primary_inlet_K"]):
        for edge_id, expected in trace.items():
            actual = sample(circuits[edge_id]["inlet_K"], i)
            examined += 1
            if actual != expected:
                failures.append({"edge_id": edge_id, "field": "inlet_K", "seq": i,
                                 "expected": expected, "actual": actual})

    # Independently overlay the ledger onto original compiled parameters without
    # using set_inputs(), _theta_at(), or build_accepted_manifest().
    original_definition = admit_dae_runtime_input(original)
    graph = original_definition.equation_graph
    layout = graph.parameter_layout
    g = graph.graph
    groups = {
        "pump_rpm_ratios": ("pump_rpm_ratio", [g.edge_str_ids[j] for j in g.pump_indices.edge_ids.tolist()]),
        "valve_openings": ("valve_opening", [g.edge_str_ids[j] for j in g.valve_indices.edge_ids.tolist()]),
        "primary_inlet_K": ("primary_inlet_K", [g.edge_str_ids[j] for j in g.hx_indices.edge_ids.tolist()]),
    }
    parameter_checks = []
    field_max = {field: 0.0 for field in layout}
    for i in range(count):
        expected = original_definition.parameters(i, heat_multiplier=checkpoint["heat_multipliers"][i]).clone()
        for trace_name, (field, ids) in groups.items():
            part = layout[field]
            for local, edge_id in enumerate(ids):
                expected[part.start + local] = checkpoint[trace_name][i][edge_id]
        actual = definition.parameters(i)
        comparison = numeric_comparison(expected.detach().cpu().numpy(), actual.detach().cpu().numpy())
        parameter_checks.append({"seq": i, **comparison})
        for field, part in layout.items():
            if actual[part].numel():
                field_max[field] = max(field_max[field], float(torch.max(torch.abs(actual[part] - expected[part]))))
    events = []
    for event in checkpoint["applied_inputs"]:
        i = event["seq"]
        item = {"seq": i, "time_s": event["time_s"],
                "time_matches_sample": event["time_s"] == i * executed["time"]["dt_s"]}
        for key in ("heat_multiplier", *CONTROL_FIELDS):
            if key not in event:
                continue
            trace = checkpoint["heat_multipliers"] if key == "heat_multiplier" else checkpoint[key]
            item[key] = {"previous": trace[i - 1], "accepted": trace[i], "command": event[key]}
            item[key]["command_matches_accepted"] = (
                trace[i] == event[key] if key == "heat_multiplier"
                else all(trace[i][edge_id] == value for edge_id, value in event[key].items()))
        events.append(item)
    return {
        "json_materialization_values_examined": examined,
        "json_materialization_exact": not failures,
        "json_materialization_failures": failures[:20],
        "compiled_parameter_samples": parameter_checks,
        "compiled_parameter_field_max_abs_difference": field_max,
        "compiled_parameters_exact": all(x["exact"] for x in parameter_checks),
        "accepted_events": events,
        "scope": "All 61 source-schedule samples, independent accepted-ledger overlay; sample i applies to the solve ending at time i*dt.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=pathlib.Path, required=True)
    parser.add_argument("--prefix", default="executed-recompute")
    args = parser.parse_args()
    evidence = args.evidence.resolve()
    evidence.relative_to(ROOT)
    if "/" in args.prefix or "\\" in args.prefix:
        raise ValueError("prefix must be a filename stem")
    receipt_path = evidence / (args.prefix + "-receipt.json")
    result_path = evidence / (args.prefix + "-result.json")
    for path in (receipt_path, result_path):
        if path.exists():
            raise FileExistsError(path)
    input_names = ("input.liquidrun", "executed.liquidrun", "checkpoint.json", "result.json", "executed-inputs.json")
    identities = {name: sha(evidence / name) for name in input_names}
    originals = {name: json.loads((evidence / name).read_text()) for name in input_names}
    original = originals["input.liquidrun"]
    executed = originals["executed.liquidrun"]
    checkpoint = originals["checkpoint.json"]
    expected = originals["result.json"]
    required_execution = {"backend": "pytorch", "device": "cpu", "dtype": "float64", "max_threads": 1}
    if executed["execution"] != required_execution:
        raise ValueError("This acceptance runner requires an unchanged CPU/float64/one-thread manifest")
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "":
        raise ValueError("Set CUDA_VISIBLE_DEVICES= to isolate this CPU run")
    torch.set_num_threads(1)
    torch.set_num_interop_threads(1)
    source_paths = sorted((ROOT / "src/backend/liquidcooling").rglob("*.py"))
    source_hashes = {str(p.relative_to(ROOT)): sha(p) for p in source_paths}
    source_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    start = time.perf_counter()
    print(json.dumps({"action": "fresh_DAESimulation", "input_sha256": identities["executed.liquidrun"],
                      "source_head": source_head, "execution": required_execution}), flush=True)
    simulation = DAESimulation(executed, run_id="executed-manifest-fresh-recompute")
    initialized = time.perf_counter()
    boundary = boundary_audit(original, executed, checkpoint, simulation.definition)
    while not simulation.done:
        frame = simulation.step()
        if frame["seq"] % 10 == 0 or simulation.done:
            print(json.dumps({"seq": frame["seq"], "time_s": frame["time_s"],
                              "newton": frame["newton"], "elapsed_s": time.perf_counter() - start}), flush=True)
    recomputed = simulation.result()
    solved = time.perf_counter()
    comparisons = {field: numeric_comparison(expected[field], recomputed[field]) for field in PHYSICAL_FIELDS}
    nested = {field: tree_comparison(expected[field], recomputed[field]) for field in
              (*CONTROL_FIELDS, "controls", "cp_details", "diagnostics", "rack_summaries")}
    identities_after = {name: sha(evidence / name) for name in input_names}
    source_hashes_after = {str(p.relative_to(ROOT)): sha(p) for p in source_paths}
    all_exact = all(x["exact"] for x in comparisons.values()) and all(x["exact"] for x in nested.values())
    max_thermal = max(v["max_abs_difference"] for k, v in comparisons.items() if k.endswith("_K"))
    max_pressure = comparisons["node_pressure_Pa"]["max_abs_difference"]
    max_flow = comparisons["edge_flow_kg_s"]["max_abs_difference"]
    equivalent = max_thermal <= 1e-8 and max_pressure <= 1e-6 and max_flow <= 1e-12
    receipt = {
        "format": "dcdtuni-fresh-executed-manifest-recompute/v1",
        "method": "Fresh DAESimulation(executed.liquidrun), initializer plus every actual Newton DAE step; no checkpoint restoration, no imported states, no queued online commands.",
        "input_sha256": identities,
        "original_inputs_unchanged": identities == identities_after,
        "source_git_head": source_head,
        "source_sha256": source_hashes,
        "source_unchanged_during_run": source_hashes == source_hashes_after,
        "runner_sha256": sha(pathlib.Path(__file__)),
        "execution": {**required_execution, "actual_threads": torch.get_num_threads(),
                      "actual_interop_threads": torch.get_num_interop_threads(),
                      "cpu_affinity": sorted(os.sched_getaffinity(0)), "CUDA_VISIBLE_DEVICES": os.environ["CUDA_VISIBLE_DEVICES"],
                      "torch_version": torch.__version__, "python_version": sys.version},
        "horizon": {"frame_count": len(recomputed["time_s"]), "time_first_s": recomputed["time_s"][0],
                    "time_last_s": recomputed["time_s"][-1], "dt_s": simulation.definition.dt,
                    "steps_recomputed": simulation.experiment.seq},
        "timing": {"initialization_s": initialized - start, "initialization_plus_steps_and_audit_s": solved - start},
        "original_config_hash": expected["config_hash"], "executed_config_hash": recomputed["config_hash"],
        "physical_fields": comparisons, "nested_physical_and_controls": nested,
        "boundary_audit": boundary,
        "physical_and_control_fields_exact": all_exact,
        "physics_within_fixed_comparison_limits": equivalent,
        "comparison_limits": {"temperature_K": 1e-8, "pressure_Pa": 1e-6, "flow_kg_s": 1e-12,
                              "purpose": "Report exact equality separately; these limits do not claim discretization or real-equipment accuracy."},
        "representation_differences": {
            "heat_multipliers": {"original": expected["heat_multipliers"], "recomputed": recomputed["heat_multipliers"],
                                 "explanation": "Accepted heat multipliers are folded into each cold-plate Q_W/dT_set_K schedule in executed.liquidrun; fresh runtime's additional multiplier is one."},
            "applied_inputs": {"original": expected["applied_inputs"], "recomputed": recomputed["applied_inputs"],
                               "explanation": "The materialized manifest already contains schedules. A fresh solve queues no runtime command and therefore has no applied online-command log."},
            "identity_and_provenance": "run_id, config_hash and parameter_provenance.manifest_sha256 identify the fresh materialized input and intentionally differ from the original interactive session.",
        },
        "original_solver": {"final_loss": expected["final_loss"], "epochs": expected["epochs"]},
        "recomputed_solver": {"final_loss": recomputed["final_loss"], "epochs": recomputed["epochs"],
                              "completed": recomputed["completed"]},
        "output_result": result_path.name,
        "gates": {
            "full_horizon": len(recomputed["time_s"]) == len(expected["time_s"]) == executed["time"]["N_t"] and simulation.done,
            "unchanged_original_evidence": identities == identities_after,
            "stable_solver_source": source_hashes == source_hashes_after,
            "materialization_exact": boundary["json_materialization_exact"] and boundary["compiled_parameters_exact"],
            "accepted_event_times": all(x["time_matches_sample"] and all(v["command_matches_accepted"] for k, v in x.items() if k in ("heat_multiplier", *CONTROL_FIELDS)) for x in boundary["accepted_events"]),
            "physical_equivalence": equivalent,
        },
        "limits": ["Numerical reproducibility of this one real browser run only; neither MMS nor device calibration.", "Original dt=10 s and Newton tolerances are retained; no time-accuracy claim follows from replay equality."],
    }
    dump(result_path, recomputed)
    receipt["output_result_sha256"] = sha(result_path)
    dump(receipt_path, receipt)
    print(json.dumps({"receipt": str(receipt_path.relative_to(ROOT)), "gates": receipt["gates"],
                      "exact": all_exact, "max_temperature_difference_K": max_thermal,
                      "max_pressure_difference_Pa": max_pressure, "max_flow_difference_kg_s": max_flow,
                      "elapsed_s": solved - start}), flush=True)
    return 0 if all(receipt["gates"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
