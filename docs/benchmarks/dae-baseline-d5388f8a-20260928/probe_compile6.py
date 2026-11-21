"""Stage 0 probe round 5c: routing rules v3 (the S1 architecture).

Rules:
  (1) under functorch transform            -> eager
  (2) grad enabled AND any input requires grad (a graph WILL be built) -> eager
      (AOT-compiled backward graphs must never exist where vmap/jacobian or
       nested backward could later execute them — probe 3/4/5a crash class)
  (3) otherwise (no_grad or grad-free inputs: inference, rollout value
      re-evaluation, diagnostics)          -> compiled
Expected in-solve behaviour with UNCHANGED product code: everything routes
eager (Newton value calls run under enable_grad with requires_grad states),
so solve/BE must pass bit-identical with n_compiled_in_solve == 0. The
compiled speedup surface arrives with the S1 product change (explicit
no_grad around Newton merit/norm value calls). Separately verified here:
rule-3 compiled calls (no_grad) remain bit-exact and genuinely compiled.
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

class RoutedResidualV3:
    def __init__(self, fn):
        self._eager = fn
        self._compiled = torch.compile(fn, mode="default", dynamic=False)
        self.n_compiled = 0
        self.n_eager_transform = 0
        self.n_eager_grad = 0
    def __call__(self, *args):
        if under_transform():
            self.n_eager_transform += 1
            return self._eager(*args)
        if torch.is_grad_enabled() and any(
            isinstance(a, torch.Tensor) and a.requires_grad for a in args
        ):
            self.n_eager_grad += 1
            return self._eager(*args)
        self.n_compiled += 1
        return self._compiled(*args)

eq = adapter(); p = eq.problem
theta = eq.parameters()
rr = RoutedResidualV3(eq.residual)
pc = dataclasses.replace(p, residual=rr)
guess = eq.initial_guess(theta)
y_exact = exact_state(eq, theta)

# --- part 1: unchanged-product-code solve must be bit-identical, zero compiled in-solve
y_e, rep_e = solve_steady(p, guess, theta)
y_c, rep_c = solve_steady(pc, guess, theta)
prev = y_e.detach()
dt = torch.tensor(0.05, dtype=torch.float64)
ys_e, r_e = backward_euler_step(p, prev, theta, time=0.05, dt=dt)
ys_c, r_c = backward_euler_step(pc, prev, theta, time=0.05, dt=dt)
routing_after_solve = dict(n_compiled=rr.n_compiled, n_eager_transform=rr.n_eager_transform, n_eager_grad=rr.n_eager_grad)

# --- part 2: grad-enabled solve WITH parameters requiring grad (the probe-4 crash scenario) must now pass
theta_g = eq.parameters().clone().requires_grad_(True)
y_g, rep_g = solve_steady(pc, eq.initial_guess(theta_g.detach()), theta_g)
g = torch.autograd.grad(y_g.square().sum(), theta_g)[0]
theta_ge = eq.parameters().clone().requires_grad_(True)
y_ge, _ = solve_steady(p, eq.initial_guess(theta_ge.detach()), theta_ge)
g_e = torch.autograd.grad(y_ge.square().sum(), theta_ge)[0]

# --- part 3: rule-3 compiled surface (no_grad value call) genuinely compiled + bit-exact
z = (y_e / p.state_scale).detach()
t0 = torch.tensor(0.0, dtype=torch.float64)
with torch.no_grad():
    r_eager = eq.residual(t0, z * p.state_scale, torch.zeros_like(z), theta) / p.residual_scale
    t1 = time.perf_counter()
    r_comp = rr(t0, z, torch.zeros_like(z), theta)   # routes compiled (no_grad)
    first_s = time.perf_counter() - t1
    t1 = time.perf_counter()
    for _ in range(50):
        rr(t0, z, torch.zeros_like(z), theta)
    warm_s = (time.perf_counter() - t1) / 50

out = {
    "probe": "stage0-round5c-routing-v3",
    "steady_state_max_abs_diff": float((y_c - y_e).abs().max()),
    "steady_vs_exact_max_abs_diff": float((y_c - y_exact).abs().max()),
    "steady_iterations": [rep_e.iterations, rep_c.iterations],
    "be_state_max_abs_diff": float((ys_c - ys_e).abs().max()),
    "grad_param_solve_passed": True,
    "param_grad_max_abs_diff_vs_eager": float((g - g_e).abs().max()),
    "routing_after_plain_solves": routing_after_solve,
    "routing_final": dict(n_compiled=rr.n_compiled, n_eager_transform=rr.n_eager_transform, n_eager_grad=rr.n_eager_grad),
    "no_grad_value_max_abs_diff": float((r_comp - r_eager).abs().max()),
    "no_grad_compiled_first_seconds": round(first_s, 3),
    "no_grad_compiled_warm_seconds": round(warm_s, 6),
    "frames": dict(dynamo.utils.counters["frames"]),
}
pathlib.Path(__file__).resolve().parent.joinpath("results_round5c.json").write_text(json.dumps(out, indent=2))
print(json.dumps(out, indent=2, default=str)[:1400])
