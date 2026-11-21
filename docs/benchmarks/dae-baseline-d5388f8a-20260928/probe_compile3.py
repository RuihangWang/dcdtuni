"""Stage 0 probe round 3: solve_steady end-to-end with a TRULY compiled residual.

Fresh process; no torch.func calls before the solve. The dense Jacobian inside
solve_steady uses torch.autograd.functional.jacobian (functorch transform), so
those calls are expected to fall back to eager (round-2 G mechanism); the value
calls (initial residual, line-search candidates) should genuinely compile.
Gate: identical root, iterations, scaled residual vs eager; frames counter and
compile_times recorded as proof of real compilation.
"""
import dataclasses, json, pathlib, time
import torch
import torch._dynamo as dynamo
from backend.tests.test_liquid.test_equation_graph import adapter, exact_state
from backend.liquidcooling.solver.dae_newton import solve_steady

torch.set_default_dtype(torch.float64)
torch.set_num_threads(2)
eq = adapter(); theta = eq.parameters(); y_exact = exact_state(eq, theta)
p = eq.problem
cr = torch.compile(eq.residual, mode="default", dynamic=False)
pc = dataclasses.replace(p, residual=cr)
guess = eq.initial_guess(theta)

t0 = time.perf_counter(); y_e, rep_e = solve_steady(p, guess, theta); eager_s = time.perf_counter() - t0
t0 = time.perf_counter(); y_c, rep_c = solve_steady(pc, guess, theta); comp_s = time.perf_counter() - t0

out = {
    "probe": "stage0-round3-solve-compiled",
    "state_max_abs_diff": float((y_c - y_e).abs().max()),
    "vs_exact_max_abs_diff": float((y_c - y_exact).abs().max()),
    "iterations": [rep_e.iterations, rep_c.iterations],
    "scaled_residual": [rep_e.scaled_residual_inf, rep_c.scaled_residual_inf],
    "solve_seconds_eager": round(eager_s, 4),
    "solve_seconds_compiled_incl_first_compile": round(comp_s, 4),
    "frames": dict(dynamo.utils.counters["frames"]),
    "compile_times_csv_head": str(dynamo.utils.compile_times(repr="csv", aggregate=True))[:400],
}
pathlib.Path(__file__).resolve().parent.joinpath("results_round3.json").write_text(json.dumps(out, indent=2))
print(json.dumps(out, indent=2, default=str)[:900])
