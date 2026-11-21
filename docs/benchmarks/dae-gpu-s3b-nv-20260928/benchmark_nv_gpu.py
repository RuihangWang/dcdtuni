"""S3b: NV mock 1604-state GPU parity (dense path, float64) — one-card episode.

No product change: load_dae_input already honors manifest execution.device
(topology compiled on host, then ALL tensors move together — the v3 design).
This benchmark overrides execution.device to 'cuda' IN MEMORY (fixture file
and its sha256 untouched; the override is recorded in the receipt).

Arms: CPU reference recomputed in-run (anchored to the golden heat gradient
0.19631667626512342 and the G1/S3a receipt values), then GPU; full-state
diff, gradient diff, residuals, per-stage timings, GPU identity + peak
memory. Compiled-GPU arm belongs to S4, not here. Caller pins
CUDA_VISIBLE_DEVICES to one physical card by UUID and confirms release.
Diagnostic script, not product code.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import time
import traceback

import torch

from backend.liquidcooling.graphcompiling.time_slice import time_slice
from backend.liquidcooling.liquidrun_dae_schema import load_dae_input
from backend.liquidcooling.solver.dae_newton import backward_euler_step, solve_steady
from backend.liquidcooling.solver.differentiable_hydraulic import hydraulic_step

GOLDEN_HEAT_GRADIENT = 0.19631667626512342


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run_arm(definition, label, report):
    equation = definition.equation_graph
    options = definition.newton_options
    theta0 = definition.parameters(0)
    guess = equation.initial_guess(theta0)
    with torch.no_grad():
        try:
            pressure, flow = hydraulic_step(time_slice(equation.graph, 0))
            guess[equation.state_layout["pressure_free"]] = pressure[~equation.graph.bc_mask]
            guess[equation.state_layout["flow"]] = flow
            report[label + "_initializer"] = "hydraulic_predictor"
        except RuntimeError:
            report[label + "_initializer"] = "raw_guess"
    t0 = time.perf_counter()
    initial, steady_report = solve_steady(
        equation.problem, guess, theta0, options=options,
        conserved=(equation.steady_neutral_storage_mask, guess),
    )
    report[label + "_steady_seconds"] = time.perf_counter() - t0
    report[label + "_steady_residual"] = steady_report.scaled_residual_inf
    report[label + "_steady_iterations"] = steady_report.iterations

    scale = torch.tensor(1.2, dtype=torch.float64,
                         device=initial.device, requires_grad=True)
    theta1 = definition.parameters(1, heat_multiplier=scale)
    t0 = time.perf_counter()
    final, step_report = backward_euler_step(
        equation.problem, initial, theta1,
        time=definition.dt, dt=definition.dt, options=options,
    )
    report[label + "_step_seconds"] = time.perf_counter() - t0
    report[label + "_step_residual"] = step_report.scaled_residual_inf

    values = equation.unpack(final, theta1)
    objective = values["solid_temperature"].mean() + values["primary_return_temperature"].mean()
    t0 = time.perf_counter()
    (gradient,) = torch.autograd.grad(objective, scale)
    report[label + "_adjoint_seconds"] = time.perf_counter() - t0
    report[label + "_heat_gradient"] = float(gradient)
    report[label + "_primary_return_K"] = values["primary_return_temperature"].detach().cpu().tolist()
    rate = torch.where(equation.differential_mask,
                       (final - initial) / definition.dt, torch.zeros_like(final))
    diagnostics = equation.diagnostics(final.new_tensor(definition.dt), final, rate, theta1)
    report[label + "_energy_balance_W"] = float(diagnostics["energy_balance_W"])
    return initial.detach(), final.detach()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    torch.set_num_threads(args.threads)
    torch.set_num_interop_threads(1)
    fixture = args.repo / "docs/benchmarks/fixtures/nv-pek02-mock-v3.liquidrun"
    report = {
        "status": "running", "threads": args.threads,
        "input_sha256": digest(fixture),
        "execution_device_override": "cuda (in-memory only; fixture untouched)",
        "golden_heat_gradient": GOLDEN_HEAT_GRADIENT,
        "gpu": {"name": torch.cuda.get_device_name(0),
                "capability": list(torch.cuda.get_device_capability(0))},
    }
    stage = "load"
    try:
        raw = json.loads(fixture.read_text())
        definition_cpu = load_dae_input(copy.deepcopy(raw))

        raw_gpu = copy.deepcopy(raw)
        raw_gpu["execution"]["device"] = "cuda"
        stage = "load_gpu"
        definition_gpu = load_dae_input(raw_gpu)
        report["states"] = definition_gpu.equation_graph.size

        stage = "cpu_arm"
        initial_c, final_c = run_arm(definition_cpu, "cpu", report)
        stage = "gpu_arm"
        torch.cuda.reset_peak_memory_stats()
        initial_g, final_g = run_arm(definition_gpu, "gpu", report)
        report["gpu"]["peak_memory_MiB"] = torch.cuda.max_memory_allocated() / 1048576

        stage = "parity"
        report["steady_state_max_abs_diff"] = float((initial_g.cpu() - initial_c).abs().max())
        report["be_state_max_abs_diff"] = float((final_g.cpu() - final_c).abs().max())
        report["heat_gradient_gpu_vs_cpu_abs_diff"] = abs(
            report["gpu_heat_gradient"] - report["cpu_heat_gradient"])
        report["heat_gradient_gpu_vs_golden_rel_diff"] = abs(
            report["gpu_heat_gradient"] - GOLDEN_HEAT_GRADIENT) / abs(GOLDEN_HEAT_GRADIENT)
        # 09-26 one-shot acceptance precedent: CPU/GPU scaled state diff 1.23e-13
        report["state_diff_gate_lt_1e_9"] = (
            report["steady_state_max_abs_diff"] < 1e-9
            and report["be_state_max_abs_diff"] < 1e-9)
        report["gradient_gate_lt_1e_9_rel"] = (
            report["heat_gradient_gpu_vs_golden_rel_diff"] < 1e-9)
        report["residual_gate"] = (
            report["gpu_steady_residual"] < 1e-8 and report["gpu_step_residual"] < 1e-8)
        report["status"] = "passed" if (
            report["state_diff_gate_lt_1e_9"]
            and report["gradient_gate_lt_1e_9_rel"]
            and report["residual_gate"]) else "gates_not_met"
    except Exception as exc:
        report.update(status="failed", failed_stage=stage, error_type=type(exc).__name__,
                      error=str(exc), traceback=traceback.format_exc()[-3000:])
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "traceback"}, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
