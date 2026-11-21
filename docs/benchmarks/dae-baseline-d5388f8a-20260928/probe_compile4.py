"""Stage 0 probe round 4: routed-residual prototype (the S1 mechanism).

Round-3 finding: calling a torch.compile'd residual under functorch transforms
(vmap via jacobian(vectorize=True), jvp) hard-crashes inductor backward kernels
("Cannot access data pointer of Tensor that doesn't have storage", torch 2.5.1).

Mechanism under test: RoutedResidual — compiled on the plain-tensor value path,
eager whenever a functorch level is active. Routing is counted, never silent.
Gates: solve_steady + backward_euler_step roots bit-identical vs eager;
parameter gradients through _ImplicitRoot bit-identical; compiled path actually
used (n_compiled > 0, frames ok > 0).
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
rr = RoutedResidual(eq.residual)
pc = dataclasses.replace(p, residual=rr)

theta_e = eq.parameters().clone().requires_grad_(True)
theta_c = eq.parameters().clone().requires_grad_(True)
guess = eq.initial_guess(theta_e.detach())

t0 = time.perf_counter()
y_e, rep_e = solve_steady(p, guess, theta_e)
eager_s = time.perf_counter() - t0
t0 = time.perf_counter()
y_c, rep_c = solve_steady(pc, guess, theta_c)
comp_s = time.perf_counter() - t0

g_e = torch.autograd.grad(y_e.square().sum(), theta_e)[0]
g_c = torch.autograd.grad(y_c.square().sum(), theta_c)[0]

# one backward-Euler step parity on the routed problem
prev = y_e.detach()
dt = torch.tensor(0.05, dtype=torch.float64)
ys_e, r_e = backward_euler_step(p, prev, theta_e.detach(), time=0.05, dt=dt)
ys_c, r_c = backward_euler_step(pc, prev, theta_c.detach(), time=0.05, dt=dt)

out = {
    "probe": "stage0-round4-routed-residual",
    "steady_state_max_abs_diff": float((y_c - y_e).abs().max()),
    "steady_iterations": [rep_e.iterations, rep_c.iterations],
    "steady_scaled_residual": [rep_e.scaled_residual_inf, rep_c.scaled_residual_inf],
    "param_grad_max_abs_diff": float((g_c - g_e).abs().max()),
    "be_step_state_max_abs_diff": float((ys_c - ys_e).abs().max()),
    "be_iterations": [r_e.iterations, r_c.iterations],
    "routing": {"n_compiled_calls": rr.n_compiled, "n_eager_transform_calls": rr.n_eager},
    "solve_seconds_eager": round(eager_s, 4),
    "solve_seconds_compiled_incl_first_compile": round(comp_s, 4),
    "frames": dict(dynamo.utils.counters["frames"]),
}
pathlib.Path(__file__).resolve().parent.joinpath("results_round4.json").write_text(json.dumps(out, indent=2))
print(json.dumps(out, indent=2, default=str)[:1200])
