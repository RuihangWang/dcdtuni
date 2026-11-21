"""Round 5d: re-run part-3 of 5c with CORRECT physical-state arguments.

5c part-3 fed scaled z where physical y belongs (probe bug, not numerics):
diff 149798 ~ pressure scale 1.5e5 Pa. Here both sides get identical
(t, y_physical, ydot, theta); expect bit-exact + real compiled warm speedup.
"""
import json, pathlib, time
import torch
import torch._dynamo as dynamo
from torch._C import _functorch as _fc
from backend.tests.test_liquid.test_equation_graph import adapter, exact_state

torch.set_default_dtype(torch.float64); torch.set_num_threads(2)

def under_transform():
    try: return _fc.maybe_current_level() is not None
    except Exception: return False

class RoutedResidualV3:
    def __init__(self, fn):
        self._eager = fn
        self._compiled = torch.compile(fn, mode="default", dynamic=False)
        self.n_compiled = 0; self.n_eager_transform = 0; self.n_eager_grad = 0
    def __call__(self, *args):
        if under_transform():
            self.n_eager_transform += 1; return self._eager(*args)
        if torch.is_grad_enabled() and any(isinstance(a, torch.Tensor) and a.requires_grad for a in args):
            self.n_eager_grad += 1; return self._eager(*args)
        self.n_compiled += 1; return self._compiled(*args)

eq = adapter(); p = eq.problem; theta = eq.parameters()
y = exact_state(eq, theta)
t0 = torch.tensor(0.0, dtype=torch.float64)
ydot0 = torch.zeros_like(y)
rr = RoutedResidualV3(eq.residual)

with torch.no_grad():
    r_eager = eq.residual(t0, y, ydot0, theta) / p.residual_scale
    r_comp = rr(t0, y, ydot0, theta) / p.residual_scale
    t1 = time.perf_counter(); rr(t0, y, ydot0, theta); first_s = time.perf_counter() - t1
    t1 = time.perf_counter()
    for _ in range(100): rr(t0, y, ydot0, theta)
    warm_c = (time.perf_counter() - t1) / 100
    t1 = time.perf_counter()
    for _ in range(100): eq.residual(t0, y, ydot0, theta)
    warm_e = (time.perf_counter() - t1) / 100

out = {
    "probe": "stage0-round5d-no-grad-value-parity-fixed",
    "value_max_abs_diff": float((r_comp - r_eager).abs().max()),
    "compiled_first_seconds": round(first_s, 3),
    "warm_compiled_seconds": round(warm_c, 7),
    "warm_eager_seconds": round(warm_e, 7),
    "speedup_warm": round(warm_e / max(warm_c, 1e-12), 1),
    "routing": dict(n_compiled=rr.n_compiled, n_eager_transform=rr.n_eager_transform, n_eager_grad=rr.n_eager_grad),
    "frames": dict(dynamo.utils.counters["frames"]),
}
pathlib.Path(__file__).resolve().parent.joinpath("results_round5d.json").write_text(json.dumps(out, indent=2))
print(json.dumps(out, indent=2, default=str))
