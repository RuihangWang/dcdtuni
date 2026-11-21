"""Stage 0 probe round 2: did inductor ACTUALLY compile? (fresh process)

Findings from round 1 raised a suspicion: torch.func.jvp wrapping a
torch.compile'd closure ran bit-exact but far too fast to have compiled
(0.13 s first call). Hypotheses:
  H1: under a functorch transform (jvp outer, compile inner), dynamo falls
      back to eager — and poisons the per-code-object cache for the rest of
      the process (explains C/D also being instant).
  H2: compile-outside-jvp (torch.compile(lambda z,v: torch.func.jvp(...)))
      DOES trace and compile, staying bit-exact.

Probe order matters: E (pure value path, no jvp anywhere) runs FIRST in this
fresh process; F (compile-outside-jvp) second; G (jvp-outside-compile, the
round-1 shape) LAST, to test the poisoning hypothesis explicitly.
Instrumentation: torch._dynamo.utils.compile_times + counters after each probe.
"""
import json
import pathlib
import time

import torch
import torch._dynamo as dynamo

RESULTS = {"probe": "stage0-compile-verification-round2", "date_sgt": "2026-09-28"}


def build():
    from backend.tests.test_liquid.test_equation_graph import adapter, exact_state
    eq = adapter()
    theta = eq.parameters()
    y = exact_state(eq, theta)
    t = torch.tensor(0.0, dtype=torch.float64)
    problem = eq.problem

    def scaled(value):
        return problem.residual(
            t, value * problem.state_scale, torch.zeros_like(value), theta
        ) / problem.residual_scale

    z = (y / problem.state_scale).detach()
    gen = torch.Generator().manual_seed(20260928)
    v = torch.randn(z.shape, dtype=z.dtype, generator=gen)
    return scaled, z, v


def dynamo_state(tag):
    times = dynamo.utils.compile_times(repr="csv", aggregate=True)
    return {tag + "_compile_times_csv": str(times)[:800],
            tag + "_frame_calls": dict(dynamo.utils.counters["frames"])}


def main():
    torch.set_default_dtype(torch.float64)
    torch.set_num_threads(2)
    scaled, z, v = build()
    out = {}

    # --- E: pure value path, compiled; NO jvp anywhere before this ---
    t0 = time.perf_counter()
    eager_ref = scaled(z)
    eager_grad = torch.autograd.grad(
        scaled(z.clone().requires_grad_(True)).square().sum(),
        z.clone().requires_grad_(True),
    ) if False else None  # grad parity done below properly
    cscaled = torch.compile(scaled, mode="default", dynamic=False)
    t1 = time.perf_counter()
    compiled_first = cscaled(z)
    first_s = time.perf_counter() - t1
    t1 = time.perf_counter()
    for _ in range(50):
        cscaled(z)
    warm_s = (time.perf_counter() - t1) / 50
    t1 = time.perf_counter()
    for _ in range(50):
        scaled(z)
    eager_warm_s = (time.perf_counter() - t1) / 50
    ze = z.clone().requires_grad_(True)
    ge = torch.autograd.grad(scaled(ze).square().sum(), ze)[0]
    zc = z.clone().requires_grad_(True)
    gc = torch.autograd.grad(cscaled(zc).square().sum(), zc)[0]
    out["E_value_path"] = {
        "value_max_abs_diff": float((compiled_first - eager_ref).abs().max()),
        "grad_max_abs_diff": float((gc - ge).abs().max()),
        "first_call_seconds": round(first_s, 3),
        "warm_compiled_seconds": round(warm_s, 6),
        "warm_eager_seconds": round(eager_warm_s, 6),
    }
    out.update(dynamo_state("E"))

    # --- F: compile OUTSIDE jvp (correct order for the GMRES matvec) ---
    def jvp_tangent(zz, vv):
        return torch.func.jvp(scaled, (zz,), (vv,))[1]

    e_tan = jvp_tangent(z, v)
    cjvp = torch.compile(jvp_tangent, mode="default", dynamic=False)
    t1 = time.perf_counter()
    c_tan_first = cjvp(z, v)
    f_first_s = time.perf_counter() - t1
    t1 = time.perf_counter()
    for _ in range(50):
        cjvp(z, v)
    f_warm_s = (time.perf_counter() - t1) / 50
    t1 = time.perf_counter()
    for _ in range(50):
        jvp_tangent(z, v)
    f_eager_s = (time.perf_counter() - t1) / 50
    out["F_compile_outside_jvp"] = {
        "tangent_max_abs_diff": float((c_tan_first - e_tan).abs().max()),
        "first_call_seconds": round(f_first_s, 3),
        "warm_compiled_seconds": round(f_warm_s, 6),
        "warm_eager_seconds": round(f_eager_s, 6),
    }
    out.update(dynamo_state("F"))

    # --- G: jvp OUTSIDE compile (round-1 shape) — poisoning test, runs LAST ---
    cscaled2 = torch.compile(scaled, mode="default", dynamic=False)
    t1 = time.perf_counter()
    g_primal, g_tangent = torch.func.jvp(cscaled2, (z,), (v,))
    g_first_s = time.perf_counter() - t1
    out["G_jvp_outside_compile"] = {
        "primal_max_abs_diff": float((g_primal - scaled(z)).abs().max()),
        "tangent_max_abs_diff": float((g_tangent - e_tan).abs().max()),
        "first_call_seconds": round(g_first_s, 3),
        "note": "if instant + bit-exact, dynamo fell back to eager under functorch",
    }
    out.update(dynamo_state("G"))

    RESULTS["probes"] = out
    p = pathlib.Path(__file__).resolve().parent / "results_round2.json"
    p.write_text(json.dumps(RESULTS, indent=2, default=str), encoding="utf-8")
    for k, val in out.items():
        print(f"[{k}] {json.dumps(val, default=str)[:400]}", flush=True)
    print("written:", p)


if __name__ == "__main__":
    main()
