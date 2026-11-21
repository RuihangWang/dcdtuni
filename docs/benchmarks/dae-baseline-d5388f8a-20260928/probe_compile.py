"""Stage 0 compile-feasibility probes (CPU-only; no product source changes).

Plan: OneDrive Research/DCTwin/LIQUID_DAE_UPGRADE_PLAN_2026-09-28.md, Stage 0
items (2)(3) + assumption A5 (dynamo zero-break residual tracing, torch.func.jvp
x torch.compile on 2.5.1 forward path).

Run (conda env dcdtuni, from repo root):
    PYTHONPATH=src OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 taskset -c 0-3 \
        python .codex-tmp/stage0_probes/probe_compile.py

Writes results.json next to this file. Failures are recorded, not raised:
a probe that errors is itself a finding (compile-readiness gap).
"""
import dataclasses
import json
import pathlib
import platform
import sys
import time
import traceback

import torch

RESULTS = {
    "probe": "stage0-compile-feasibility",
    "date_sgt": "2026-09-28",
    "env": {
        "python": sys.version.split()[0],
        "torch": torch.__version__,
        "cuda_build": torch.version.cuda,
        "platform": platform.platform(),
        "default_dtype": "float64 (set below)",
    },
}


def build():
    """adapter() = EquationGraph(small_graph(), primary()): CP + two-fluid HX loop."""
    from backend.tests.test_liquid.test_equation_graph import adapter, exact_state
    eq = adapter()
    theta = eq.parameters()
    y = exact_state(eq, theta)
    t = torch.tensor(0.0, dtype=torch.float64)
    return eq, theta, y, t


def probe_a_explain():
    """torch._dynamo.explain on the raw EquationGraph.residual closure."""
    import torch._dynamo as dynamo
    eq, theta, y, t = build()
    ydot = torch.zeros_like(y)
    explained = dynamo.explain(eq.residual)(t, y, ydot, theta)
    reasons = getattr(explained, "graph_break_reasons", []) or []
    return {
        "graph_count": int(getattr(explained, "graph_count", -1)),
        "graph_break_count": int(getattr(explained, "graph_break_count", -1)),
        "op_count": int(getattr(explained, "op_count", -1)),
        "break_reasons": [str(r) for r in reasons][:12],
        "out_guards": len(getattr(explained, "out_guards", []) or []),
    }


def _scaled_factory(eq, theta, t):
    problem = eq.problem

    def scaled(value):
        return problem.residual(
            t, value * problem.state_scale, torch.zeros_like(value), theta
        ) / problem.residual_scale

    return problem, scaled


def probe_b_jvp_compile():
    """torch.func.jvp through torch.compile(mode='default') — the _gmres matvec mechanism."""
    eq, theta, y, t = build()
    problem, scaled = _scaled_factory(eq, theta, t)
    z = (y / problem.state_scale).detach()
    gen = torch.Generator().manual_seed(20260928)
    v = torch.randn(z.shape, dtype=z.dtype, generator=gen)

    e_primal, e_tangent = torch.func.jvp(scaled, (z,), (v,))

    cscaled = torch.compile(scaled, mode="default", dynamic=False)
    t0 = time.perf_counter()
    c_primal, c_tangent = torch.func.jvp(cscaled, (z,), (v,))
    first_s = time.perf_counter() - t0
    t0 = time.perf_counter()
    reps = 20
    for _ in range(reps):
        torch.func.jvp(cscaled, (z,), (v,))
    warm_s = (time.perf_counter() - t0) / reps
    t0 = time.perf_counter()
    for _ in range(reps):
        torch.func.jvp(scaled, (z,), (v,))
    eager_warm_s = (time.perf_counter() - t0) / reps
    return {
        "jvp_primal_max_abs_diff": float((c_primal - e_primal).abs().max()),
        "jvp_tangent_max_abs_diff": float((c_tangent - e_tangent).abs().max()),
        "first_call_seconds": round(first_s, 2),
        "warm_compiled_seconds": round(warm_s, 6),
        "warm_eager_seconds": round(eager_warm_s, 6),
    }


def probe_c_grad_parity():
    """autograd backward parity: compiled vs eager scaled residual (float64, CPU)."""
    eq, theta, y, t = build()
    problem, scaled = _scaled_factory(eq, theta, t)
    cscaled = torch.compile(scaled, mode="default", dynamic=False)
    z0 = (y / problem.state_scale).detach()

    ze = z0.clone().requires_grad_(True)
    re = scaled(ze)
    ge = torch.autograd.grad(re.square().sum(), ze)[0]

    zc = z0.clone().requires_grad_(True)
    rc = cscaled(zc)
    gc = torch.autograd.grad(rc.square().sum(), zc)[0]
    return {
        "value_max_abs_diff": float((rc.detach() - re.detach()).abs().max()),
        "grad_max_abs_diff": float((gc - ge).abs().max()),
        "grad_rel_l2": float((gc - ge).norm() / (ge.norm() + 1e-300)),
    }


def probe_d_solve_compiled():
    """End-to-end: solve_steady with a compiled residual closure vs eager (dense path)."""
    from backend.liquidcooling.solver.dae_newton import solve_steady
    eq, theta, y_exact, t = build()
    problem = eq.problem
    compiled_residual = torch.compile(eq.residual, mode="default", dynamic=False)
    pc = dataclasses.replace(problem, residual=compiled_residual)

    guess = eq.initial_guess(theta)
    t0 = time.perf_counter()
    y_e, rep_e = solve_steady(problem, guess, theta)
    eager_s = time.perf_counter() - t0
    t0 = time.perf_counter()
    y_c, rep_c = solve_steady(pc, guess, theta)
    compiled_s = time.perf_counter() - t0
    return {
        "state_max_abs_diff": float((y_c - y_e).abs().max()),
        "vs_exact_state_max_abs_diff": float((y_c - y_exact).abs().max()),
        "iterations_eager": rep_e.iterations,
        "iterations_compiled": rep_c.iterations,
        "scaled_residual_eager": rep_e.scaled_residual_inf,
        "scaled_residual_compiled": rep_c.scaled_residual_inf,
        "solve_seconds_eager": round(eager_s, 4),
        "solve_seconds_compiled_incl_first_compile": round(compiled_s, 4),
    }


def main():
    torch.set_default_dtype(torch.float64)
    torch.set_num_threads(2)
    probes = {}
    for name, fn in [
        ("A_dynamo_explain", probe_a_explain),
        ("B_jvp_x_compile", probe_b_jvp_compile),
        ("C_grad_parity", probe_c_grad_parity),
        ("D_solve_steady_compiled", probe_d_solve_compiled),
    ]:
        t0 = time.perf_counter()
        try:
            probes[name] = fn()
        except Exception as exc:  # a failing probe is a finding, not a crash
            probes[name] = {
                "error": repr(exc),
                "traceback_tail": traceback.format_exc()[-1500:],
            }
        probes[name]["_seconds"] = round(time.perf_counter() - t0, 2)
        print(f"[{name}] " + json.dumps(probes[name], default=str)[:500], flush=True)
    RESULTS["probes"] = probes
    out = pathlib.Path(__file__).resolve().parent / "results.json"
    out.write_text(json.dumps(RESULTS, indent=2, default=str), encoding="utf-8")
    print("written:", out)


if __name__ == "__main__":
    main()
