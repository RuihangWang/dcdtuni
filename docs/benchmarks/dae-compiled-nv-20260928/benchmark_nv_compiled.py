"""G1 gate: NV mock 1604-state fixture under compile routing rules v3.

Golden reference: heat_gradient 0.19631667626512342 (docs/benchmarks/
liquid-dae-matrix-free-2026-09-27/nv_dense_cpu{1,4}.json, dense default).
Gate: compiled-routed solve reproduces the golden gradient DIGIT-FOR-DIGIT,
roots/residuals identical, routing counters recorded, compile seconds separate
from steady-state timing. CPU only (GPU episodes are S3/S4). Diagnostic
script, not product code.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time
import traceback

import torch

from backend.liquidcooling.graphcompiling.time_slice import time_slice
from backend.liquidcooling.liquidrun_dae_schema import load_dae_input
from backend.liquidcooling.solver.compile_support import compiled_problem
from backend.liquidcooling.solver.dae_newton import backward_euler_step, solve_steady
from backend.liquidcooling.solver.differentiable_hydraulic import hydraulic_step

GOLDEN_HEAT_GRADIENT = 0.19631667626512342


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    torch.set_num_threads(args.threads)
    torch.set_num_interop_threads(1)
    fixture = args.repo / "docs/benchmarks/fixtures/nv-pek02-mock-v3.liquidrun"
    sources = [
        args.repo / "src/backend/liquidcooling/solver/dae_newton.py",
        args.repo / "src/backend/liquidcooling/solver/compile_support.py",
        args.repo / "src/backend/liquidcooling/solver/equation_graph.py",
    ]
    report = {
        "status": "running", "threads": args.threads,
        "input_sha256": digest(fixture),
        "source_sha256": {str(s.name): digest(s) for s in sources},
        "state_device": "cpu", "dtype": "float64", "stage_seconds": {},
        "golden_heat_gradient": GOLDEN_HEAT_GRADIENT,
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

        problem, router = compiled_problem(equation.problem)

        stage = "steady"
        start = time.perf_counter()
        initial, steady_report = solve_steady(
            problem, guess, theta0, options=options,
            conserved=(equation.steady_neutral_storage_mask, guess),
        )
        report["stage_seconds"][stage] = time.perf_counter() - start
        report["steady_residual"] = steady_report.scaled_residual_inf
        report["steady_iterations"] = steady_report.iterations

        scale = torch.tensor(1.2, dtype=torch.float64, requires_grad=True)
        theta1 = definition.parameters(1, heat_multiplier=scale)
        stage = "step"
        start = time.perf_counter()
        final, step_report = backward_euler_step(
            problem, initial, theta1,
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

        rate = torch.where(equation.differential_mask, (final - initial) / definition.dt, torch.zeros_like(final))
        diagnostics = equation.diagnostics(final.new_tensor(definition.dt), final, rate, theta1)
        heat_gradient = float(gradient)
        report.update(
            status="passed",
            heat_gradient=heat_gradient,
            heat_gradient_matches_golden_digit_for_digit=(heat_gradient == GOLDEN_HEAT_GRADIENT),
            heat_gradient_abs_diff=abs(heat_gradient - GOLDEN_HEAT_GRADIENT),
            energy_balance_W=float(diagnostics["energy_balance_W"]),
            primary_return_K=values["primary_return_temperature"].detach().tolist(),
            objective_K=float(objective.detach()),
            routing=router.routing_report(),
        )
    except Exception as exc:
        report.update(status="failed", failed_stage=stage, error_type=type(exc).__name__,
                      error=str(exc), traceback=traceback.format_exc()[-3000:])
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "traceback"}, indent=2))
    return 0 if report["status"] == "passed" and report.get("heat_gradient_matches_golden_digit_for_digit") else 1


if __name__ == "__main__":
    raise SystemExit(main())
