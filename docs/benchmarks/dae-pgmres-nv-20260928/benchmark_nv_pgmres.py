"""G2 gate: NV mock 1604-state fixture under 'pgmres' (matrix-free, preconditioned).

The gap this attacks (docs/benchmarks/liquid-dae-matrix-free-2026-09-27/):
unpreconditioned GMRES stagnated at 2.76077 (64 it) / 0.887391 (128 it) against
a 1.3673e-6 target on this fixture; the dense default stayed the product path.

Gate:
  * steady + BE step CONVERGE matrix-free (scaled residuals < 1e-8);
  * heat gradient matches the dense golden 0.19631667626512342 to < 1e-9
    relative (roots are unique; bitwise equality is not expected across
    different linear solvers);
  * matvec accounting recorded per solve (budget discipline);
  * never builds a full Jacobian (asserted via monkeypatch-free design:
    the matrix-free path uses torch.func.jvp only — the pytest contract
    test enforces the ban; here we record timings only).
CPU-only float64; GPU episodes are S3/S4. Diagnostic script, not product code.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time
import traceback

import torch
from dataclasses import replace

from backend.liquidcooling.graphcompiling.time_slice import time_slice
from backend.liquidcooling.liquidrun_dae_schema import load_dae_input
from backend.liquidcooling.solver import dae_newton
from backend.liquidcooling.solver.dae_newton import backward_euler_step, solve_steady
from backend.liquidcooling.solver.differentiable_hydraulic import hydraulic_step

GOLDEN_HEAT_GRADIENT = 0.19631667626512342


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--restart", type=int, default=30)
    parser.add_argument("--cycles", type=int, default=8)
    parser.add_argument("--degree", type=int, default=8)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    torch.set_num_threads(args.threads)
    torch.set_num_interop_threads(1)
    fixture = args.repo / "docs/benchmarks/fixtures/nv-pek02-mock-v3.liquidrun"
    sources = [args.repo / "src/backend/liquidcooling/solver/dae_newton.py",
               args.repo / "src/backend/liquidcooling/solver/equation_graph.py"]
    report = {
        "status": "running", "threads": args.threads,
        "pgmres": {"restart": args.restart, "cycles": args.cycles, "degree": args.degree},
        "input_sha256": digest(fixture),
        "source_sha256": {str(s.name): digest(s) for s in sources},
        "state_device": "cpu", "dtype": "float64", "stage_seconds": {},
        "golden_heat_gradient": GOLDEN_HEAT_GRADIENT,
    }
    # instrument _pgmres to collect info dicts from inside the solver
    infos = []
    original = dae_newton._pgmres
    def spy(matvec, rhs, options):
        x, info = original(matvec, rhs, options)
        infos.append(info)
        return x, info
    dae_newton._pgmres = spy
    stage = "load"
    try:
        raw = json.loads(fixture.read_text())
        definition = load_dae_input(raw)
        equation = definition.equation_graph
        report["states"] = equation.size
        options = replace(definition.newton_options,
                          linear_solver="pgmres",
                          pgmres_restart=args.restart,
                          pgmres_max_cycles=args.cycles,
                          poly_degree=args.degree,
                          linear_atol=1e-10, linear_rtol=1e-8)
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
        report["steady_iterations"] = steady_report.iterations
        report["steady_linear_solves"] = len(infos)
        report["steady_matvecs_total"] = sum(i["matvecs"] for i in infos)

        scale = torch.tensor(1.2, dtype=torch.float64, requires_grad=True)
        theta1 = definition.parameters(1, heat_multiplier=scale)
        infos_step = []
        infos.clear(); infos.extend(infos_step)
        stage = "step"
        start = time.perf_counter()
        final, step_report = backward_euler_step(
            equation.problem, initial, theta1,
            time=definition.dt, dt=definition.dt, options=options,
        )
        report["stage_seconds"][stage] = time.perf_counter() - start
        report["step_residual"] = step_report.scaled_residual_inf
        report["step_matvecs_total"] = sum(i["matvecs"] for i in infos)

        values = equation.unpack(final, theta1)
        objective = values["solid_temperature"].mean() + values["primary_return_temperature"].mean()
        infos.clear()
        stage = "adjoint"
        start = time.perf_counter()
        (gradient,) = torch.autograd.grad(objective, scale)
        report["stage_seconds"][stage] = time.perf_counter() - start
        report["adjoint_matvecs_total"] = sum(i["matvecs"] for i in infos)

        rate = torch.where(equation.differential_mask, (final - initial) / definition.dt, torch.zeros_like(final))
        diagnostics = equation.diagnostics(final.new_tensor(definition.dt), final, rate, theta1)
        heat = float(gradient)
        report.update(
            status="passed", heat_gradient=heat,
            heat_gradient_rel_diff_vs_golden=abs(heat - GOLDEN_HEAT_GRADIENT) / abs(GOLDEN_HEAT_GRADIENT),
            heat_gradient_gate_lt_1e_9=abs(heat - GOLDEN_HEAT_GRADIENT) / abs(GOLDEN_HEAT_GRADIENT) < 1e-9,
            energy_balance_W=float(diagnostics["energy_balance_W"]),
            last_info=infos[-1] if infos else None,
        )
    except Exception as exc:
        report.update(status="failed", failed_stage=stage, error_type=type(exc).__name__,
                      error=str(exc), traceback=traceback.format_exc()[-3000:])
    finally:
        dae_newton._pgmres = original
    args.output.write_text(json.dumps(report, indent=2, default=str) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "traceback"}, indent=2, default=str))
    ok = (report["status"] == "passed"
          and report.get("steady_residual", 1) < 1e-8
          and report.get("step_residual", 1) < 1e-8
          and report.get("heat_gradient_gate_lt_1e_9"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
