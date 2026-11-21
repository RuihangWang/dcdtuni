"""S4: why the coarse final-time HX-secondary order gate of the full-ring MMS stays RED.

Reads only the stored inputs of ../liquid-full-mms-asymptotic-20261007 (SHA-256
checked against its ROOT-archive-copy-map.json) and the current backend's
graph compiler. Writes results.json beside this script. From the repository root:

    PYTHONPATH=src OMP_NUM_THREADS=2 python docs/benchmarks/liquid-mms-hx-order-cause-2026-10-22/analysis.py

1. The independent backward-Euler error recurrence (the same formula as
   actual-v4/*/independent_asymptotic_recurrence.py), with the manufactured
   flow m*(t) = C[a0 + a1 sin(wt + phase)] and storage temperatures evaluated in
   closed form so that any dt can be run, reproduces the product's stored
   errors at all eight levels.
2. Final-time HX-secondary orders down to dt = 1/256 s, split by the storage
   family whose truncation error causes them.
3. Neighbouring manufactured problems (every storage capacity x k, or the
   forcing frequency x s; x*(t) and m*(t) are kept and the source adapts):
   coarse-level orders, the Richardson ratio r = e2/e1 of the final-time
   error e(dt) = e1 dt + e2 dt^2, and where the leading coefficient e1(t)
   changes sign.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import sys
import time
from pathlib import Path

import numpy as np
import torch
from scipy.sparse import coo_matrix, diags
from scipy.sparse.linalg import splu

HERE = Path(__file__).resolve().parent
MMS = HERE.parent / "liquid-full-mms-asymptotic-20261007"
ARCHIVE = {e["path"]: e for e in json.loads((MMS / "ROOT-archive-copy-map.json").read_text())["entries"]}
END_S = 8.0
LEVELS = {"2p0": 2.0, "1p0": 1.0, "0p5": .5, "0p25": .25, "0p125": .125, "0p0625": .0625,
          "0p03125": .03125, "0p015625": .015625}
STORED = {tag: (f"original-coarse/archives/results/newton-vs-manufactured-{tag}.npz.gz" if tag not in
                ("2p0", "0p03125", "0p015625") else
                "original-coarse/results/newton-vs-manufactured-2p0.npz" if tag == "2p0" else
                f"actual-v4/grid-{tag}/results/newton-vs-manufactured-{tag}.npz.gz") for tag in LEVELS}
FAMILIES = ["water", "solid", "hx_secondary", "hx_primary"]


def stored_bytes(relative: str) -> bytes:
    """A stored file, checked against the copy map (archive-gzipped entries also against their original)."""
    data = (MMS / relative).read_bytes()
    entry = ARCHIVE[relative]
    assert hashlib.sha256(data).hexdigest() == entry["sha256"], relative
    if entry["encoding"] == "gzip":
        data = gzip.decompress(data)
        assert hashlib.sha256(data).hexdigest() == entry["originalSHA256"], relative
    return gzip.decompress(data) if entry["originalPath"].endswith(".gz") else data


torch.set_num_threads(2)
from backend.liquidcooling.dae_runtime import admit_dae_runtime_input  # noqa: E402

RAW = json.loads(stored_bytes("actual-v4/grid-0p03125/results/manufactured-dt-0p03125.liquidrun.gz"))
DEF = admit_dae_runtime_input(RAW)
EQ = DEF.equation_graph
G = EQ.graph
N, NW, NC, NH = G.N_nodes, G.N_cells, G.N_cp, G.N_hx
IDS = {k: v.detach().numpy() for k, v in EQ._ids.items()}
FR, TO, BCT = EQ._from.numpy(), EQ._to.numpy(), EQ._bc_T_mask.numpy()
CELL_EDGES, FIRST, LAST = EQ._cells.numpy(), EQ._first.numpy(), EQ._last.numpy()
PREVIOUS, FOLLOWING = EQ._previous.numpy(), EQ._next.numpy()
PIPE_FIRST, PIPE_LAST = EQ._pipe_first.numpy(), EQ._pipe_last.numpy()
LAYOUT = {"node": slice(0, N), "water": slice(N, N + NW), "solid": slice(N + NW, N + NW + NC),
          "hx_secondary": slice(N + NW + NC, N + NW + NC + NH),
          "hx_primary": slice(N + NW + NC + NH, N + NW + NC + 2 * NH)}
SIZE = N + NW + NC + 2 * NH
SEC = N + NW + NC
CYCLE = np.load(io.BytesIO(stored_bytes("actual-v4/grid-0p03125/results/frozen-original-cycle.npz")))
OMEGA0 = 2 * np.pi / 32


def parameters(index: int) -> dict:
    theta = DEF.parameters(index)
    return {k: theta[s].detach().numpy() for k, s in EQ.parameter_layout.items() if not k.startswith("mms_")}


P = parameters(0)


def matrix(m: np.ndarray, capacity_scale: float = 1.0):
    """Algebraic operator D and storage capacities C of the thermal error recurrence at flow m."""
    p = P
    cp = p["fluid_cp"].item(); rho = p["fluid_rho"].item()
    up = np.where(m >= 0, FR, TO); down = np.where(m >= 0, TO, FR); w = np.abs(m)
    incoming = np.bincount(down, weights=w, minlength=N)
    eff = -np.expm1(-p["cp_UA"] / (w[IDS["cp"]] * cp))
    cp_local = {int(e): i for i, e in enumerate(IDS["cp"])}
    hx_local = {int(e): i for i, e in enumerate(IDS["hx"])}
    pipe_local = {int(e): i for i, e in enumerate(IDS["pipe"])}
    row, col, val = [], [], []

    def add(a, b, v):
        row.append(a); col.append(b); val.append(v)

    capacity = np.zeros(SIZE)
    capacity[LAYOUT["water"]] = rho * cp * p["cell_volume"]
    capacity[LAYOUT["solid"]] = p["cp_Csolid"]
    capacity[LAYOUT["hx_secondary"]] = rho * cp * p["secondary_volume"]
    capacity[LAYOUT["hx_primary"]] = p["primary_rho"] * p["primary_cp"] * p["primary_volume"]
    for n in range(N):
        add(n, n, 1. if BCT[n] else incoming[n] * cp)
    for e in range(G.N_edges):
        u, downstream = int(up[e]), int(down[e]); q = w[e] * cp
        if BCT[downstream]:
            continue
        if e in pipe_local:
            loc = pipe_local[e]
            add(downstream, N + int(PIPE_LAST[loc] if m[e] >= 0 else PIPE_FIRST[loc]), -q)
        elif e in cp_local:
            loc = cp_local[e]; add(downstream, u, -q * (1 - eff[loc])); add(downstream, N + NW + loc, -q * eff[loc])
        elif e in hx_local:
            add(downstream, N + NW + NC + hx_local[e], -q)
        else:
            add(downstream, u, -q)
    for c, e in enumerate(CELL_EDGES):
        r = N + c; q = w[e] * cp; add(r, r, q)
        if m[e] >= 0:
            other = int(FR[e]) if FIRST[c] else N + int(PREVIOUS[c])
        else:
            other = int(TO[e]) if LAST[c] else N + int(FOLLOWING[c])
        add(r, other, -q)
    for i, e in enumerate(IDS["cp"]):
        r = N + NW + i; q = w[e] * cp * eff[i]; add(r, r, q); add(r, int(up[e]), -q)
    for i, e in enumerate(IDS["hx"]):
        s = N + NW + NC + i; pidx = s + NH; secondary = w[e] * cp; ua = p["hx_UA"][i]
        primary = p["primary_flow_kg_s"][i] * p["primary_cp"][i]
        add(s, s, secondary + ua); add(s, int(up[e]), -secondary); add(s, pidx, -ua)
        add(pidx, pidx, primary + ua); add(pidx, s, -ua)
    return coo_matrix((val, (row, col)), shape=(SIZE, SIZE)).tocsc(), capacity * capacity_scale


def flow(t: float, omega: float) -> np.ndarray:
    return CYCLE["C"] @ (CYCLE["a0"] + CYCLE["amplitude"] * np.sin(omega * t + CYCLE["phase"]))


def storage(t: float, omega: float):
    arg = omega * t + CYCLE["xphase"]
    return CYCLE["xbase"] + CYCLE["xamplitude"] * np.sin(arg), CYCLE["xamplitude"] * omega * np.cos(arg)


def recurrence(dt: float, capacity_scale: float = 1.0, omega_scale: float = 1.0):
    """BE error e_i per storage family: (D + C/dt) e_i = (C/dt) e_(i-1) - C((x*_i - x*_(i-1))/dt - xdot*_i)."""
    omega = OMEGA0 * omega_scale
    steps = int(round(END_S / dt)); times = dt * np.arange(steps + 1)
    errs = np.zeros((steps + 1, SIZE, 4))
    x_prev, _ = storage(0.0, omega)
    for i in range(1, steps + 1):
        d, c = matrix(flow(times[i], omega), capacity_scale)
        x, xdot = storage(times[i], omega)
        defect = np.zeros(SIZE); defect[N:] = c[N:] * ((x - x_prev) / dt - xdot)
        rhs = (c[:, None] / dt) * errs[i - 1]
        for j, family in enumerate(FAMILIES):
            rhs[LAYOUT[family], j] -= defect[LAYOUT[family]]
        errs[i] = splu(d + diags(c / dt)).solve(rhs)
        x_prev = x
    return times, errs


def order(a: float, b: float) -> float:
    return float(np.log2(abs(a / b)))


def main() -> None:
    started = time.perf_counter()
    constant = {k: bool(np.array_equal(P[k], parameters(RAW["time"]["N_t"] - 1)[k])) for k in
                ("fluid_cp", "fluid_rho", "cp_UA", "cell_volume", "cp_Csolid", "secondary_volume", "primary_rho",
                 "primary_cp", "primary_volume", "primary_flow_kg_s", "hx_UA")}
    out: dict = {"inputs": "../liquid-full-mms-asymptotic-20261007 (SHA-256 checked)", "endTimeS": END_S,
                 "operatorParametersConstantInTime": constant}

    # 1. reproduce the product at all eight stored levels
    reproduction, finals = {}, {}
    for tag, dt in LEVELS.items():
        stored = np.load(io.BytesIO(stored_bytes(STORED[tag])))
        if tag == "0p03125":
            out["closedFormFlowMaxDiffKgS"] = float(max(np.max(np.abs(flow(t, OMEGA0) - stored["flow_kg_s_reference"][i]))
                                                    for i, t in enumerate(stored["times"])))
        _, errs = recurrence(dt)
        total = errs.sum(axis=2)
        reproduction[tag] = float(max(np.max(np.abs(total[:, LAYOUT[k]] - stored[f"{name}_error"])) for k, name in
                                      (("water", "water_temperature_K"), ("solid", "solid_temperature_K"),
                                       ("hx_secondary", "hx_secondary_K"), ("hx_primary", "hx_primary_K"))))
        finals[dt] = errs[-1, SEC]
        print("reproduced", tag, reproduction[tag], flush=True)
    out["maxDiffVsStoredProductErrorK"] = reproduction

    # 2. final-time orders down to dt = 1/256, by causing family
    for dt in (2 ** -7, 2 ** -8):
        _, errs = recurrence(dt); finals[dt] = errs[-1, SEC]
    dts = sorted(finals, reverse=True)
    out["levels"] = [{"dt": dt, "finalHxSecondaryK": float(finals[dt].sum()),
                      "finalContributionsK": dict(zip(FAMILIES, finals[dt].tolist()))} for dt in dts]
    out["finalOrders"] = [{"dt": f"{a:g}->{b:g}", "total": order(finals[a].sum(), finals[b].sum()),
                           **{f: order(finals[a][j], finals[b][j]) for j, f in enumerate(FAMILIES)}}
                          for a, b in zip(dts, dts[1:])]
    d8, c8 = matrix(flow(END_S, OMEGA0))
    tau = c8 / d8.diagonal()
    out["residenceTimeS"] = {f: {"min": float(tau[LAYOUT[f]].min()), "median": float(np.median(tau[LAYOUT[f]])),
                                 "max": float(tau[LAYOUT[f]].max())} for f in FAMILIES}

    # 3. neighbouring manufactured problems
    coarse = [2.0, 1.0, .5, .25, .125, .0625]
    variants = []
    for k, s in ((1.0, 1.0), (0.25, 1.0), (4.0, 1.0), (16.0, 1.0), (1.0, 0.5), (1.0, 2.0)):
        final = {dt: float(recurrence(dt, k, s)[1][-1, SEC].sum()) for dt in coarse}
        h = 2 ** -6
        times, fine = recurrence(h, k, s)
        _, finer = recurrence(h / 2, k, s)
        a = fine.sum(axis=2)[:, SEC]; b = finer.sum(axis=2)[::2, SEC][:a.size]
        e1 = (4 * b - a) / h                      # leading coefficient over the whole trajectory
        e2 = (2 * a[-1] - 4 * b[-1]) / h ** 2
        sign_changes = times[2:][np.sign(e1[2:]) != np.sign(e1[1:-1])].tolist()  # skips the first step
        variants.append({
            "capacityScale": k, "omegaScale": s, "coarseFinalHxSecondaryK": final,
            "coarseFinalOrders": [order(final[x], final[y]) for x, y in zip(coarse, coarse[1:])],
            "e1FinalKPerS": float(e1[-1]), "e2FinalKPerS2": float(e2), "rPerS": float(e2 / e1[-1]),
            "e1FinalOverMax": float(abs(e1[-1]) / np.max(np.abs(e1))), "e1SignChangesAtS": sign_changes})
        print("variant", k, s, round(variants[-1]["rPerS"], 3), [round(o, 3) for o in variants[-1]["coarseFinalOrders"]],
              sign_changes, flush=True)
    out["variants"] = variants
    out["elapsedSeconds"] = round(time.perf_counter() - started, 1)
    (HERE / "results.json").write_text(json.dumps(out, indent=1) + "\n")


if __name__ == "__main__":
    main()
