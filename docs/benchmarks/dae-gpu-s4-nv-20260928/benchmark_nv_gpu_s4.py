"""S4: NV 1604-state GPU+compile combination episode + sync anatomy.

Arms (all on one pinned card, float64, dense default unless stated):
  A. GPU eager        — recompute of the S3b anchor (golden heat gradient)
  B. GPU compiled     — compiled_problem routing rules v3 on GPU; parity vs A;
                        first-compile seconds reported separately (never
                        folded into steady-state timing)
  C. GPU pgmres       — matrix-free forward (host-Givens, one batched
                        transfer per iteration) + dense adjoint; quantifies
                        the transfer-cost question left open by S3b
  D. sync anatomy     — torch.profiler around arm A steady: kernel-time sum
                        vs wall time = launch/sync overhead, the A3 evidence

Gates: B vs A state diff < 1e-9 and heat-gradient rel diff vs golden < 1e-9;
C converges with residual < 1e-8 and gradient rel diff < 1e-9; all timings
recorded, NO speedup claimed unless the desync roadmap earns it later.
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
from dataclasses import replace

import torch

from backend.liquidcooling.graphcompiling.time_slice import time_slice
from backend.liquidcooling.liquidrun_dae_schema import load_dae_input
from backend.liquidcooling.solver.compile_support import compiled_problem
from backend.liquidcooling.solver.dae_newton import backward_euler_step, solve_steady
from backend.liquidcooling.solver.differentiable_hydraulic import hydraulic_step

GOLDEN = 0.19631667626512342


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def solve_chain(definition, problem, label, report, profile=False):
    equation = definition.equation_graph
    options = definition.newton_options
    if label == "pgmres":
        options = replace(options, linear_solver="pgmres", pgmres_restart=30,
                          pgmres_max_cycles=20, poly_degree=8,
                          linear_atol=1e-10, linear_rtol=1e-8)
    theta0 = definition.parameters(0)
    guess = equation.initial_guess(theta0)
    with torch.no_grad():
        try:
            pressure, flow = hydraulic_step(time_slice(equation.graph, 0))
            guess[equation.state_layout["pressure_free"]] = pressure[~equation.graph.bc_mask]
            guess[equation.state_layout["flow"]] = flow
        except RuntimeError:
            pass
    ctx = torch.profiler.profile(
        activities=[torch.profiler.ProfilerActivity.CPU,
                    torch.profiler.ProfilerActivity.CUDA],
    ) if profile else None
    t0 = time.perf_counter()
    if ctx is not None:
        with ctx:
            initial, steady_report = solve_steady(
                problem, guess, theta0, options=options,
                conserved=(equation.steady_neutral_storage_mask, guess))
    else:
        initial, steady_report = solve_steady(
            problem, guess, theta0, options=options,
            conserved=(equation.steady_neutral_storage_mask, guess))
    steady_s = time.perf_counter() - t0
    if ctx is not None:
        ka = ctx.key_averages()
        kernel_sum = sum(e.self_device_time_total for e in ka) / 1e6
        report["profile_steady"] = {
            "wall_seconds": steady_s,
            "cuda_kernel_time_sum_seconds": kernel_sum,
            "launch_sync_overhead_seconds": steady_s - kernel_sum,
            "overhead_fraction": (steady_s - kernel_sum) / steady_s if steady_s else None,
            "top_ops": [(e.key[:70], round(e.self_device_time_total / 1e3, 2), e.count)
                        for e in sorted(ka, key=lambda x: -x.self_device_time_total)[:8]],
        }
    scale = torch.tensor(1.2, dtype=torch.float64, device=initial.device, requires_grad=True)
    theta1 = definition.parameters(1, heat_multiplier=scale)
    t0 = time.perf_counter()
    final, step_report = backward_euler_step(
        problem, initial, theta1, time=definition.dt, dt=definition.dt, options=options)
    step_s = time.perf_counter() - t0
    values = equation.unpack(final, theta1)
    objective = values["solid_temperature"].mean() + values["primary_return_temperature"].mean()
    t0 = time.perf_counter()
    (gradient,) = torch.autograd.grad(objective, scale)
    adj_s = time.perf_counter() - t0
    report[label] = {
        "steady_seconds": steady_s, "steady_residual": steady_report.scaled_residual_inf,
        "steady_iterations": steady_report.iterations,
        "step_seconds": step_s, "step_residual": step_report.scaled_residual_inf,
        "adjoint_seconds": adj_s, "heat_gradient": float(gradient),
        "heat_gradient_rel_diff_vs_golden": abs(float(gradient) - GOLDEN) / abs(GOLDEN),
    }
    return initial.detach(), final.detach()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--skip-pgmres", action="store_true")
    args = parser.parse_args()
    torch.set_num_threads(args.threads)
    torch.set_num_interop_threads(1)
    fixture = args.repo / "docs/benchmarks/fixtures/nv-pek02-mock-v3.liquidrun"
    report = {"status": "running", "input_sha256": digest(fixture),
              "golden_heat_gradient": GOLDEN,
              "gpu": {"name": torch.cuda.get_device_name(0)}}
    stage = "load"
    try:
        raw = json.loads(fixture.read_text())
        raw_gpu = copy.deepcopy(raw); raw_gpu["execution"]["device"] = "cuda"

        stage = "A_gpu_eager"
        definition = load_dae_input(copy.deepcopy(raw_gpu))
        initial_a, final_a = solve_chain(definition, definition.equation_graph.problem,
                                         "A_gpu_eager", report, profile=True)

        stage = "B_gpu_compiled"
        definition_b = load_dae_input(copy.deepcopy(raw_gpu))
        t_compile0 = time.perf_counter()
        problem_b, router = compiled_problem(definition_b.equation_graph.problem)
        # warm the compiled kernels once, timed separately from the solve chain
        with torch.no_grad():
            y_w = definition_b.equation_graph.initial_guess(definition_b.parameters(0))
            _ = problem_b.residual(torch.tensor(0.0, dtype=torch.float64, device=y_w.device),
                                   y_w, torch.zeros_like(y_w), definition_b.parameters(0))
        torch.cuda.synchronize()
        report["B_first_compile_seconds"] = time.perf_counter() - t_compile0
        initial_b, final_b = solve_chain(definition_b, problem_b, "B_gpu_compiled", report)
        report["B_routing"] = router.routing_report()
        report["B_vs_A_steady_diff"] = float((initial_b - initial_a).abs().max())
        report["B_vs_A_be_diff"] = float((final_b - final_a).abs().max())

        if not args.skip_pgmres:
            stage = "C_gpu_pgmres"
            definition_c = load_dae_input(copy.deepcopy(raw_gpu))
            initial_c, final_c = solve_chain(definition_c, definition_c.equation_graph.problem,
                                             "C_gpu_pgmres", report)
            report["C_vs_A_steady_diff"] = float((initial_c - initial_a).abs().max())

        report["gates"] = {
            "B_state_diff_lt_1e_9": report["B_vs_A_steady_diff"] < 1e-9 and report["B_vs_A_be_diff"] < 1e-9,
            "B_gradient_rel_lt_1e_9": report["B_gpu_compiled"]["heat_gradient_rel_diff_vs_golden"] < 1e-9,
            "B_no_fallback": report["B_routing"]["n_eager_fallback"] == 0,
            "A_gradient_rel_lt_1e_9": report["A_gpu_eager"]["heat_gradient_rel_diff_vs_golden"] < 1e-9,
        }
        if not args.skip_pgmres:
            report["gates"]["C_converged"] = (report["C_gpu_pgmres"]["steady_residual"] < 1e-8
                                              and report["C_gpu_pgmres"]["step_residual"] < 1e-8)
            report["gates"]["C_gradient_rel_lt_1e_9"] = report["C_gpu_pgmres"]["heat_gradient_rel_diff_vs_golden"] < 1e-9
        report["status"] = "passed" if all(report["gates"].values()) else "gates_not_met"
    except Exception as exc:
        report.update(status="failed", failed_stage=stage, error_type=type(exc).__name__,
                      error=str(exc), traceback=traceback.format_exc()[-3000:])
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "traceback"}, indent=2, default=str)[:3500])
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
