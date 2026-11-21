"""CPU-only source-matched NV mock dense/GMRES probe; diagnostic, not product code."""
from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import resource
import time
import traceback

import torch

from backend.liquidcooling.graphcompiling.time_slice import time_slice
from backend.liquidcooling.liquidrun_dae_schema import load_dae_input
from backend.liquidcooling.solver.dae_newton import backward_euler_step, solve_steady
from backend.liquidcooling.solver.differentiable_hydraulic import hydraulic_step


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--threads", type=int, choices=(1, 4), required=True)
    parser.add_argument("--solver", choices=("dense", "gmres"), required=True)
    parser.add_argument("--linear-iters", type=int, default=64)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    torch.set_num_threads(args.threads)
    torch.set_num_interop_threads(1)
    fixture = args.repo / "docs/benchmarks/fixtures/nv-pek02-mock-v3.liquidrun"
    source = args.repo / "src/backend/liquidcooling/solver/dae_newton.py"
    report = {
        "status": "running", "solver": args.solver, "threads": args.threads,
        "linear_iters": args.linear_iters, "input_sha256": digest(fixture),
        "solver_sha256": digest(source), "state_device": "cpu", "dtype": "float64",
        "stage_seconds": {},
    }
    stage = "load"
    try:
        raw = json.loads(fixture.read_text())
        start = time.perf_counter()
        definition = load_dae_input(raw)
        equation = definition.equation_graph
        report["stage_seconds"][stage] = time.perf_counter() - start
        report["states"] = equation.size
        options = definition.newton_options
        if args.solver == "gmres":
            options = replace(
                options, linear_solver="gmres",
                linear_max_iterations=args.linear_iters,
                linear_atol=1e-10, linear_rtol=1e-8,
            )
        theta0 = definition.parameters(0)
        guess = equation.initial_guess(theta0)
        with torch.no_grad():
            try:
                pressure, flow = hydraulic_step(time_slice(equation.graph, 0))
                guess[equation.state_layout["pressure_free"]] = pressure[~equation.graph.bc_mask]
                guess[equation.state_layout["flow"]] = flow
                report["initializer"] = "hydraulic_predictor"
            except RuntimeError:
                report["initializer"] = "raw_guess"
        stage = "steady"
        start = time.perf_counter()
        initial, steady_report = solve_steady(
            equation.problem, guess, theta0, options=options,
            conserved=(equation.steady_neutral_storage_mask, guess),
        )
        report["stage_seconds"][stage] = time.perf_counter() - start
        report["steady_residual"] = steady_report.scaled_residual_inf
        scale = torch.tensor(1.2, dtype=torch.float64, requires_grad=True)
        theta1 = definition.parameters(1, heat_multiplier=scale)
        stage = "step"
        start = time.perf_counter()
        final, step_report = backward_euler_step(
            equation.problem, initial, theta1,
            time=definition.dt, dt=definition.dt, options=options,
        )
        report["stage_seconds"][stage] = time.perf_counter() - start
        report["step_residual"] = step_report.scaled_residual_inf
        values = equation.unpack(final, theta1)
        objective = values["solid_temperature"].mean() + values["primary_return_temperature"].mean()
        stage = "adjoint"
        start = time.perf_counter()
        (gradient,) = torch.autograd.grad(objective, scale)
        report["stage_seconds"][stage] = time.perf_counter() - start
        rate = torch.where(equation.differential_mask, (final-initial)/definition.dt, torch.zeros_like(final))
        diagnostics = equation.diagnostics(final.new_tensor(definition.dt), final, rate, theta1)
        report.update(
            status="passed", heat_gradient=float(gradient),
            energy_balance_W=float(diagnostics["energy_balance_W"]),
            primary_return_K=values["primary_return_temperature"].detach().tolist(),
            objective_K=float(objective.detach()),
            peak_rss_MiB=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,
        )
    except Exception as exc:
        report.update(status="failed", failed_stage=stage, error_type=type(exc).__name__,
                      error=str(exc), traceback=traceback.format_exc()[-3000:])
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "traceback"}))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
