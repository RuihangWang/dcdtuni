"""Capture the real GPU torch.compile failure traceback (no router)."""
import copy, json, traceback
import torch
from backend.liquidcooling.liquidrun_dae_schema import load_dae_input

raw = json.loads(open("docs/benchmarks/fixtures/nv-pek02-mock-v3.liquidrun").read())
raw["execution"]["device"] = "cuda"
d = load_dae_input(raw)
eq = d.equation_graph
cr = torch.compile(eq.residual, mode="default", dynamic=False)
theta = d.parameters(0)
y = eq.initial_guess(theta)
t = torch.tensor(0.0, dtype=torch.float64, device=y.device)
with torch.no_grad():
    try:
        r = cr(t, y, torch.zeros_like(y), theta)
        torch.cuda.synchronize()
        print("COMPILED GPU CALL OK, residual max:", float(r.abs().max()))
    except Exception:
        traceback.print_exc()
