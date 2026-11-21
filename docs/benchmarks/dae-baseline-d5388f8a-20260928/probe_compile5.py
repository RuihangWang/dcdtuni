"""Stage 0 probe round 5a: routed residual WITHOUT parameter backprop.

Probe-4 crash root-caused from the stack: it fires in _ImplicitRoot.backward's
NESTED grad call (:282 autograd.grad(r,values) where r came from a compiled
forward at :264) while the outer autograd engine runs. Rule set for S1:
  (1) under functorch transform -> eager
  (2) inside _ImplicitRoot.backward -> eager (product-code hook, NOT tested here)
  (3) otherwise -> compiled (Newton value calls build graphs that are never
      backpropped through: norms/merit only)
This probe validates rules (1)+(3) alone: solve_steady + backward_euler_step
with requires_grad-free parameters (so no outer backward is ever triggered).
Gate: roots/iterations/residuals identical to eager; n_compiled > 0.
"""
import dataclasses, json, pathlib, time
import torch
import torch._dynamo as dynamo
from torch._C import _functorch as _fc
from backend.tests.test_liquid.test_equation_graph import adapter, exact_state
from backend.liquidcooling.solver.dae_newton import solve_steady, backward_euler_step

torch.set_default_dtype(torch.float64)
torch.set_num_threads(2)

def under_transform():
    try:
        return _fc.maybe_current_level() is not None
    except Exception:
        return False

class RoutedResidual:
    def __init__(self, fn):
        self._eager = fn
        self._compiled = torch.compile(fn, mode="default", dynamic=False)
        self.n_compiled = 0
        self.n_eager = 0
    def __call__(self, *args):
        if under_transform():
            self.n_eager += 1
            return self._eager(*args)
        self.n_compiled += 1
        return self._compiled(*args)

eq = adapter(); p = eq.problem
y_exact = exact_state(eq, p and eq.parameters())
theta = eq.parameters()          # NO requires_grad -> no outer backward
rr = RoutedResidual(eq.residual)
pc = dataclasses.replace(p, residual=rr)
guess = eq.initial_guess(theta)

t0 = time.perf_counter()
y_e, rep_e = solve_steady(p, guess, theta)
eager_s = time.perf_counter() - t0
t0 = time.perf_counter()
y_c, rep_c = solve_steady(pc, guess, theta)
comp_s = time.perf_counter() - t0

prev = y_e.detach()
dt = torch.tensor(0.05, dtype=torch.float64)
ys_e, r_e = backward_euler_step(p, prev, theta, time=0.05, dt=dt)
ys_c, r_c = backward_euler_step(pc, prev, theta, time=0.05, dt=dt)

out = {
    "probe": "stage0-round5a-routed-no-backprop",
    "steady_state_max_abs_diff": float((y_c - y_e).abs().max()),
    "steady_vs_exact_max_abs_diff": float((y_c - y_exact).abs().max()),
    "steady_iterations": [rep_e.iterations, rep_c.iterations],
    "steady_scaled_residual": [rep_e.scaled_residual_inf, rep_c.scaled_residual_inf],
    "be_state_max_abs_diff": float((ys_c - ys_e).abs().max()),
    "be_iterations": [r_e.iterations, r_c.iterations],
    "routing": {"n_compiled_calls": rr.n_compiled, "n_eager_transform_calls": rr.n_eager},
    "solve_seconds_eager": round(eager_s, 4),
    "solve_seconds_compiled_incl_first_compile": round(comp_s, 4),
    "frames": dict(dynamo.utils.counters["frames"]),
}
pathlib.Path(__file__).resolve().parent.joinpath("results_round5a.json").write_text(json.dumps(out, indent=2))
print(json.dumps(out, indent=2, default=str)[:1200])
