"""Real React v4 Shinaide float64 CPU/GPU/compiled short-trajectory parity.

All outputs and caches must be inside dcdtuni. This is numerical verification,
not an automatic product CUDA/compile policy or a performance benchmark.
"""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import time
import traceback

import numpy as np
import torch

from torch._dynamo.utils import counters as dynamo_counters
from torch._inductor import metrics as inductor_metrics

from backend.liquidcooling.dae_experiment import DAEExperiment
from backend.liquidcooling.liquidrun_dae_schema import load_dae_input
from backend.liquidcooling.solver.compile_support import compiled_problem

ROOT = Path(__file__).resolve().parents[1]
GPU_UUID = "GPU-dd97ad37-4a36-19df-d789-0ee503ba4d93"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def gpu_snapshot():
    fields = "index,uuid,name,memory.total,memory.used,memory.free"
    raw = subprocess.check_output(["nvidia-smi", "--id=" + GPU_UUID,
        "--query-gpu=" + fields, "--format=csv,noheader,nounits"], text=True).strip()
    row = [item.strip() for item in raw.split(",")]
    return dict(raw=raw, index=int(row[0]), uuid=row[1], name=row[2],
        memory_total_MiB=int(row[3]), memory_used_MiB=int(row[4]),
        memory_free_MiB=int(row[5]))


def synchronize(device):
    if device.startswith("cuda"):
        torch.cuda.synchronize()


def compiler_report():
    return {"dynamo_counters": {name:{str(key):value for key,value in values.items()}
            for name,values in dynamo_counters.items()},
            "inductor_generated_kernel_count": inductor_metrics.generated_kernel_count}


def arm(raw, device, compiled, steps, output, label, newton_override):
    manifest = copy.deepcopy(raw)
    manifest["execution"].update(device=device, dtype="float64", max_threads=2)
    manifest["newton"].update(newton_override)
    t0 = time.perf_counter()
    definition = load_dae_input(manifest)
    eq = definition.equation_graph
    router = None
    compile_time = None
    if compiled:
        dynamo_counters.clear()
        inductor_metrics.reset()
        eq.problem, router = compiled_problem(eq.problem)
        guess = eq.initial_guess(definition.parameters(0))
        started = time.perf_counter()
        with torch.no_grad():
            _ = eq.problem.residual(guess.new_tensor(0.), guess,
                                    torch.zeros_like(guess), definition.parameters(0))
        synchronize(device)
        compile_time = time.perf_counter() - started
        print(label + " WARM " + json.dumps(router.routing_report()), flush=True)
    records, arrays = {}, {}
    for mode in ("original", "actuated"):
        started = time.perf_counter()
        experiment = DAEExperiment(definition)
        # Initialization uses the actual original sample 0, independent of alpha
        # and beta. Online commands then act on the first accepted step.
        alpha = torch.tensor(1.2, dtype=torch.float64, device=device, requires_grad=True)
        beta = torch.tensor(.9, dtype=torch.float64, device=device, requires_grad=True)
        if mode == "actuated":
            part = eq.parameter_layout["pump_rpm_ratio"]
            initial_rpm = definition.parameters(0)[part]
            controls = experiment.pump_rpm_control_edge_ids
            if controls != experiment.pump_edge_ids:
                raise ValueError("This parity trajectory requires curve-controlled pumps")
            experiment.set_inputs(heat_multiplier=alpha,
                pump_rpm_ratios={edge: initial_rpm[index] * beta
                                for index, edge in enumerate(controls)})
        states = [experiment.tensor_state.detach().cpu().numpy()]
        residency = [dict(state=str(experiment.tensor_state.device),
                          theta=str(experiment._theta.device),
                          dtype=str(experiment.tensor_state.dtype))]
        for index in range(steps):
            experiment.step()
            states.append(experiment.tensor_state.detach().cpu().numpy())
            residency.append(dict(state=str(experiment.tensor_state.device),
                                  theta=str(experiment._theta.device),
                                  dtype=str(experiment.tensor_state.dtype)))
            print(label + " " + mode + " ACCEPTED " + str(experiment.seq), flush=True)
        synchronize(device)
        forward_seconds = time.perf_counter() - started
        objective = experiment.tensor_series("solid_temperature")[:, -1].mean()
        values = dict(objective_K=float(objective.detach()), forward_seconds=forward_seconds,
            residency=residency, frames=len(states),
            frame_times_s=[frame["time_s"] for frame in experiment.frames],
            newton=[frame["newton"] for frame in experiment.frames],
            diagnostics=[frame["diagnostics"] for frame in experiment.frames],
            online_accepted=experiment.applied_inputs)
        if mode == "actuated":
            started = time.perf_counter()
            gradients = torch.autograd.grad(objective, (alpha, beta))
            synchronize(device)
            values.update(adjoint_seconds=time.perf_counter() - started,
                first_derivatives={name: float(value.detach())
                    for name, value in zip(("heat_multiplier", "pump_rpm_multiplier"), gradients)},
                gradient_residency=[str(value.device) for value in gradients])
        arrays[mode] = np.stack(states)
        records[mode] = values
        del experiment, states
    synchronize(device)
    records.update(device=device, dtype="float64", compile_requested=compiled,
        first_compile_seconds=compile_time, elapsed_seconds=time.perf_counter() - t0,
        routing=None if router is None else router.routing_report(),
        compiler=None if router is None else compiler_report(),
        newton=manifest["newton"],
        graph=dict(nodes=eq.graph.N_nodes, edges=eq.graph.N_edges,
            water_cells=eq.graph.N_cells, cold_plates=eq.graph.N_cp,
            heat_exchangers=eq.graph.N_hx, dae_unknowns=eq.size,
            thermal_states=int(eq.differential_mask.sum().detach().cpu())) ,
        state_layout={name:[sl.start, sl.stop] for name, sl in eq.state_layout.items()})
    path = output / (label + "-states.npz")
    np.savez_compressed(path, **arrays)
    records["raw_states"] = dict(path=str(path.relative_to(ROOT)), sha256=digest(path))
    del eq, definition, router
    if device.startswith("cuda"):
        torch.cuda.empty_cache()
    return records, arrays


def compare(reference, observed, ref_records, actual_records):
    result = {}
    for mode in ("original", "actuated"):
        absolute = np.abs(observed[mode] - reference[mode])
        components = {}
        for name, (begin, end) in ref_records["state_layout"].items():
            components[name] = float(absolute[:, begin:end].max()) if end > begin else 0.
        result[mode] = dict(max_all_state_absolute_difference=float(absolute.max()),
            max_absolute_difference_by_state=components,
            objective_absolute_difference_K=abs(actual_records[mode]["objective_K"]
                                               - ref_records[mode]["objective_K"]))
        if mode == "actuated":
            directions = {}
            for name, ref in ref_records[mode]["first_derivatives"].items():
                value = actual_records[mode]["first_derivatives"][name]
                directions[name] = dict(reference=ref, observed=value,
                    absolute_difference=abs(value-ref),
                    relative_difference=abs(value-ref)/max(abs(value),abs(ref),1e-12))
            result[mode]["first_derivatives"] = directions
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fixture", type=Path, required=True)
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--steps", type=int, default=3)
    ap.add_argument("--newton-atol", type=float)
    ap.add_argument("--newton-rtol", type=float)
    args = ap.parse_args()
    fixture, output = args.fixture.resolve(), args.output_dir.resolve()
    if not fixture.is_relative_to(ROOT) or not output.is_relative_to(ROOT):
        ap.error("fixture/output must stay in dcdtuni")
    if os.environ.get("CUDA_VISIBLE_DEVICES") != GPU_UUID:
        ap.error("CUDA_VISIBLE_DEVICES must be the explicitly authorized GPU0 UUID")
    for key in ("TMPDIR", "XDG_CACHE_HOME", "TORCHINDUCTOR_CACHE_DIR", "TRITON_CACHE_DIR"):
        path = os.environ.get(key)
        if (not path or not Path(path).is_absolute() or not Path(path).is_dir()
                or not Path(path).resolve().is_relative_to(ROOT)):
            ap.error(key + " must be inside dcdtuni")
    if not set(os.sched_getaffinity(0)).issubset(set(range(24,28))):
        ap.error("CPU affinity must be within 24-27")
    output.mkdir(parents=True, exist_ok=True)
    torch.set_num_threads(2)
    torch.set_num_interop_threads(1)
    preflight = gpu_snapshot()
    if preflight["uuid"] != GPU_UUID or preflight["memory_free_MiB"] < 6000:
        ap.error("GPU0 identity or free-memory preflight failed")
    if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
        ap.error("Exactly the authorized CUDA card must be visible")
    raw = json.loads(fixture.read_text())
    if raw.get("schemaVersion") != 4 or not 1 <= args.steps < raw["time"]["N_t"]:
        ap.error("Requires real v4 input and a valid short step count")
    source_files = ["tools/shinaide_liquid_cuda_verification.py",
        "src/backend/liquidcooling/dae_experiment.py",
        "src/backend/liquidcooling/liquidrun_dae_schema.py",
        "src/backend/liquidcooling/solver/equation_graph.py",
        "src/backend/liquidcooling/solver/dae_newton.py",
        "src/backend/liquidcooling/solver/compile_support.py"]
    newton_override = {}
    for name,value in (("atol",args.newton_atol),("rtol",args.newton_rtol)):
        if value is not None:
            if not math.isfinite(value) or (value <= 0 if name == "atol" else value < 0):
                ap.error("Newton tolerance override must be finite and admissible")
            newton_override[name] = value
    started = time.perf_counter()
    report = dict(newton_override=newton_override, original_newton=raw["newton"], status="running", date="2026-10-05", input=str(fixture.relative_to(ROOT)),
        input_sha256=digest(fixture),
        git_head=subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip(),
        source_sha256={name:digest(ROOT/name) for name in source_files},
        torch_version=torch.__version__, cuda_runtime=torch.version.cuda,
        execution=dict(cpu_affinity=sorted(os.sched_getaffinity(0)), cpu_threads=2,
            cuda_visible_devices=os.environ["CUDA_VISIBLE_DEVICES"],
            torch_visible_card=torch.cuda.get_device_name(0), torch_visible_count=1,
            cache_paths={key:os.environ[key] for key in ("TMPDIR","XDG_CACHE_HOME",
                "TORCHINDUCTOR_CACHE_DIR","TRITON_CACHE_DIR")},
            library_path=os.environ.get("LIBRARY_PATH")),
        gpu_preflight=preflight,
        scope=dict(original_duration_s=(raw["time"]["N_t"]-1)*raw["time"]["dt_s"],
            tested_duration_s=args.steps*raw["time"]["dt_s"], dt_s=raw["time"]["dt_s"],
            accepted_steps=args.steps, initial="original steady initialization",
            original="unmodified product schedule at samples 0..steps; execution device only",
            actuated="heat 1.2 and all curve-pump RPM times 0.9 queued after original initialization",
            objective="mean of all compiled cold-plate solid temperatures at final accepted short step",
            derivatives="first-order heat and pump scalars; fixed topology/flow/valve regime",
            compiled="opt-in routed residual wrapper; transforms and autograd intentionally eager",
            exclusions="not complete 600 s GPU acceptance, higher derivatives, GPU speedup, or browser CUDA"))
    arrays = {}
    try:
        for label, device, compiled in (("cpu-eager","cpu",False),
                ("gpu-eager","cuda:0",False),("gpu-compiled","cuda:0",True)):
            print("START " + label, flush=True)
            report[label], arrays[label] = arm(raw,device,compiled,args.steps,output,label,newton_override)
            (output/"receipt.json").write_text(json.dumps(report,indent=2)+"\n")
            print("DONE " + label + " " + json.dumps(report[label]["actuated"]["first_derivatives"]),flush=True)
        report["comparisons"] = {
            "gpu_eager_vs_cpu_eager":compare(arrays["cpu-eager"],arrays["gpu-eager"],
                report["cpu-eager"],report["gpu-eager"]),
            "gpu_compiled_vs_gpu_eager":compare(arrays["gpu-eager"],arrays["gpu-compiled"],
                report["gpu-eager"],report["gpu-compiled"])}
        gates = {}
        for name, comparison in report["comparisons"].items():
            gates[name+"_all_states"] = all(value["max_all_state_absolute_difference"] < 1e-8
                for value in comparison.values())
            gates[name+"_gradients"] = all(value["relative_difference"] < 1e-8
                for value in comparison["actuated"]["first_derivatives"].values())
        route = report["gpu-compiled"]["routing"]
        compiler = report["gpu-compiled"]["compiler"]
        backend_counts = compiler["dynamo_counters"].get("inductor",{})
        gates.update(compiled_value_calls=route["n_compiled"]>0,
            backend_graphs=compiler["dynamo_counters"].get("stats",{}).get("unique_graphs",0)>0,
            backend_inductor_cache_or_kernels=(compiler["inductor_generated_kernel_count"]>0
                or backend_counts.get("fxgraph_cache_hit",0)>0
                or backend_counts.get("fxgraph_cache_miss",0)>0),
            no_unplanned_fallback=route["n_eager_fallback"]==0 and not route["broken_latch"],
            intentional_gradient_routing=route["n_eager_grad"]>0 or route["n_eager_transform"]>0,
            finite_nonzero_gradients=all(math.isfinite(value) and abs(value)>1e-8
                for label in ("cpu-eager","gpu-eager","gpu-compiled")
                for value in report[label]["actuated"]["first_derivatives"].values()),
            accepted_residuals=all(frame["scaled_residual_inf"] < 1e-8
                for label in ("cpu-eager","gpu-eager","gpu-compiled")
                for mode in ("original","actuated") for frame in report[label][mode]["newton"]),
            state_and_theta_residency=all(item["dtype"]=="torch.float64"
                and item["state"]==report[label]["device"] and item["theta"]==report[label]["device"]
                for label in ("cpu-eager","gpu-eager","gpu-compiled")
                for mode in ("original","actuated") for item in report[label][mode]["residency"]))
        report["gates"]=gates
        report["status"]="passed" if all(gates.values()) else "gates_not_met"
    except Exception as error:
        report.update(status="failed", error_type=type(error).__name__, error=str(error),
            traceback=traceback.format_exc())
    finally:
        torch.cuda.empty_cache()
        report.update(elapsed_seconds=time.perf_counter()-started,
            gpu_postflight=gpu_snapshot())
        (output/"receipt.json").write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps({key:report.get(key) for key in
        ("status","gates","comparisons","error","elapsed_seconds","gpu_preflight","gpu_postflight")},
        indent=2),flush=True)
    return 0 if report["status"]=="passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
