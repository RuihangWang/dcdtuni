#!/usr/bin/env python3
"""Transient inversion of Base201 on its site telemetry, at L0 (the zonal network of the L0 mesh) or L1 (the RANS of
the L1 mesh): the measured unit speeds, supply temperatures and rack powers drive the window, the room sensors of a
seeded 70 % split and the running units' return temperatures are fitted, the other 30 % of the sensors are held out
and scored by the same solves (the observers carry every sensor; only the fitted ones have measurements).

    cd src && python ../tools/base201_transient_inversion.py --level L0 --steps 31 --gpu <uuid>

Writes ``summary.json`` (parameters, misfits, RMSE of the fitted / held-out sensors and the returns, prior and
posterior, per step) and ``sensors.csv`` (measured, prior and posterior per sensor and step) into ``--out``; the
service's own run directory (``result.json``) sits below it.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from agent.dcrans import invert_dcrans  # noqa: E402
from agent.dcrans.base201_telemetry import CADENCE_S, Telemetry  # noqa: E402

ROOM = ["P_scale", "dTsrv", "Q100", "bT", "lsBlank", "ltau"]
# measured rack power is the heat (P_scale near 1, not the design-heat prior 1.5); the rest are the service defaults
PRIOR = [1.0, 12.0, 1.0, 0.0, -1.5, 0.0]
SIGMA = [0.5, 4.0, 0.25, 1.0, 1.0, 1.0]


def _latest_run(level: str) -> Path:
    runs = sorted((REPO / "case" / f"Base201_{level}" / "log").glob(f"20*_Base201_{level}"))
    if not runs:
        raise SystemExit(f"no exported run of Base201_{level}: run its project through the air runner first")
    return runs[-1]


def _auto_k0(tm: Telemetry, steps: int, start: int) -> int:
    # the first window from ``start`` in which the set of running units does not change (a unit switching on or off
    # inside the window is a boundary event the debug window should not start with)
    on = tm.speed > 0
    for k0 in range(start, len(tm.time) - steps):
        if (on[k0:k0 + steps] == on[k0]).all():
            return k0
    raise SystemExit("no window with a constant set of running units")


def _rmse(pred, meas, names):
    d = [np.asarray(pred[n]) - np.asarray(meas[n]) for n in names if n in pred]
    if not d:
        return None, None
    d = np.stack(d)                                   # (names, steps)
    return float(np.sqrt(np.mean(d ** 2))), [float(v) for v in np.sqrt(np.mean(d ** 2, axis=0))]


def main(argv=None):
    global ROOM, PRIOR, SIGMA
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--level", choices=("L0", "L1"), default="L0")
    ap.add_argument("--case-run", type=Path, default=None, help="the exported run (default: the project's latest)")
    ap.add_argument("--data", type=Path, default=REPO / "_tmp" / "base201_telemetry",
                    help="room201.npz and b201_paths.json (site data, not in git)")
    ap.add_argument("--k0", default="auto", help="first sample of the window, or auto (constant running set)")
    ap.add_argument("--k-start", type=int, default=1440, help="where auto starts looking (1440 = day 3, 00:00)")
    ap.add_argument("--steps", type=int, default=31, help="samples in the window (120 s apart)")
    ap.add_argument("--spinup", type=int, default=0,
                    help="samples before the window that are simulated but not fitted (the initial state's memory)")
    ap.add_argument("--initial-state", type=Path, default=None,
                    help="L1: a solver state of this case to start from (no steady block), at sample k0 - spinup")
    ap.add_argument("--frac", type=float, default=0.7)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--free", default=",".join(ROOM), help="the room parameters fitted (comma separated)")
    ap.add_argument("--prior", action="append", default=[], metavar="NAME=V",
                    help="a room parameter's prior mean and start value (repeatable), e.g. lsBlank=-0.5")
    ap.add_argument("--sigma", action="append", default=[], metavar="NAME=S",
                    help="a room parameter's prior width (repeatable), e.g. P_scale=0.1 Q100=0.5")
    ap.add_argument("--include-rev", action="store_true",
                    help="the stopped units' reverse leakage lqRev as a parameter (else their patches carry no flow)")
    ap.add_argument("--server-min-flow", type=float, default=0.0,
                    help="a server's airflow is at least this fraction of its nominal flow (idle fans)")
    ap.add_argument("--phys-rtol", type=float, default=None,
                    help="L1: a time step is solved once its merit fell by this factor (step model default 0.05)")
    ap.add_argument("--supply-window", type=int, default=1,
                    help="the supply temperatures as the centred moving mean over this many samples (odd; 1: raw)")
    ap.add_argument("--per-rack-leakage", action="store_true")
    ap.add_argument("--rack-leakage-sigma", type=float, default=0.15)
    ap.add_argument("--no-returns", action="store_true", help="fit the room sensors only")
    ap.add_argument("--obs-sigma", type=float, default=0.5)
    ap.add_argument("--max-iters", type=int, default=10)
    ap.add_argument("--tol", type=float, default=1e-5)
    ap.add_argument("--fd-check", action="store_true")
    ap.add_argument("--gpu", default=None)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--q100-max", type=float, default=3.0,
                    help="upper bound of Q100 (the ACU flow multiplier of the project's nominal airflow; Base201's is synthetic)")
    ap.add_argument("--jacobian", choices=("dense", "broyden", "matrix_free"), default="dense",
                    help="the fit's Jacobian: dense every iteration, or exact once then Broyden updates")
    ap.add_argument("--worker-timeout-s", type=float, default=86400.0,
                    help="wall-clock cap of the service worker (the service default is 3600 s; an L1 window runs longer)")
    a = ap.parse_args(argv)

    if a.initial_state and (a.level != "L1" or a.spinup < 1):
        raise SystemExit("--initial-state: L1 only, with --spinup >= 1 (the state's own sample is not observed)")
    if a.include_rev:      # the service's order: lqRev after lsBlank (default_datacentre_param_specs)
        ROOM, PRIOR, SIGMA = ROOM[:5] + ["lqRev"] + ROOM[5:], PRIOR[:5] + [-0.7] + PRIOR[5:], SIGMA[:5] + [0.8] + SIGMA[5:]
        if "lqRev" not in a.free:
            a.free += ",lqRev"
    case_run = (a.case_run or _latest_run(a.level)).resolve()
    project = case_run.parents[1]
    tm = Telemetry.load(project, a.data)
    k0 = _auto_k0(tm, a.steps, a.k_start) if a.k0 == "auto" else int(a.k0)
    ks = list(range(k0, k0 + a.steps))
    ks_run = list(range(k0 - a.spinup, k0 + a.steps))   # simulated; the first spinup samples carry no observations
    train, held = tm.split(a.frac, a.seed)
    running = [u for j, u in enumerate(tm.units) if (tm.speed[ks, j] > 0).all()]
    snaps = tm.snapshots(ks_run, supply_window=a.supply_window)
    meas_all = tm.observations(ks, returns=True, running_returns_only=False)
    returns = [] if a.no_returns else [f"return_T:{u}" for u in running]
    fit_names = train + returns
    obs = [{} for _ in range(a.spinup)] + [{n: row[n] for n in fit_names} for row in meas_all]
    meas = {n: [row[n] for row in meas_all] for n in tm.sensors + [f"return_T:{u}" for u in running]}
    free = [ROOM.index(n.strip()) for n in a.free.split(",") if n.strip()]
    sigma = list(SIGMA)
    for item in a.sigma:
        name, val = item.split("=", 1)
        sigma[ROOM.index(name.strip())] = float(val)
    for item in a.prior:
        name, val = item.split("=", 1)
        PRIOR[ROOM.index(name.strip())] = float(val)
    tag = "".join(f"_{x.replace('=', '')}" for x in a.sigma)
    tag += "".join(f"_m{x.replace('=', '')}" for x in a.prior)
    tag += f"_spin{a.spinup}" if a.spinup else ""
    tag += "_u0" if a.initial_state else ""
    tag += "_rev" if a.include_rev else ""
    tag += f"_qmin{a.server_min_flow:g}" if a.server_min_flow else ""
    tag += f"_rtol{a.phys_rtol:g}" if a.phys_rtol else ""
    tag += f"_sup{a.supply_window}" if a.supply_window > 1 else ""
    tag += f"_q100max{a.q100_max:g}" if a.q100_max != 3.0 else ""
    tag += f"_{a.jacobian}" if a.jacobian != "dense" else ""
    out = (a.out or REPO / "_tmp" / "base201-transient" / f"{a.level}_k{k0}_n{a.steps}{tag}").resolve()
    out.mkdir(parents=True, exist_ok=True)

    say = lambda *x: print(*x, flush=True)
    say(f"case {case_run}")
    say(f"window k {k0}..{ks[-1]} ({(a.steps - 1) * CADENCE_S / 60:.0f} min), {len(running)} running units, "
        f"rack heat {sum(snaps[0]['rack_heat_W'].values()) / 1e3:.1f} kW, sensors fit {len(train)} / held {len(held)}, "
        f"returns {len(returns)}, free {[ROOM[i] for i in free]}, per-rack leakage {a.per_rack_leakage}")
    t = time.time()
    res = invert_dcrans(case_run, obs, level=a.level, mode="transient", dt_s=CADENCE_S, snapshots=snaps,
                        probe_coords=tm.sensor_xyz.tolist(), probe_names=tm.sensors, return_acus=running,
                        prior=PRIOR, prior_sigma=sigma, theta0=PRIOR, free_indices=free, per_rack_leakage=a.per_rack_leakage,
                        rack_leakage_sigma=a.rack_leakage_sigma, obs_sigma=a.obs_sigma, max_iters=a.max_iters,
                        tol=a.tol, fd_check=a.fd_check, output_root=out, label=f"base201-{a.level}-transient",
                        gpu=a.gpu, initial_state=a.initial_state, include_rev=a.include_rev,
                        server_min_flow=a.server_min_flow,
                        jacobian=a.jacobian,
                        worker_timeout_s=a.worker_timeout_s,
                        options={"step_options": {"phys_rtol": a.phys_rtol} if a.phys_rtol else {},
                                 "q100_bounds": [0.3, a.q100_max]})
    wall = time.time() - t
    m = dict(res.get("metrics") or {})
    m.update({k: v for k, v in res.items() if k not in m})
    summary = {"level": a.level, "case_run": str(case_run), "k0": k0, "steps": a.steps, "dt_s": CADENCE_S,
               "split": {"frac": a.frac, "seed": a.seed, "fit": train, "held_out": held}, "running_units": running,
               "spinup": a.spinup, "supply_window": a.supply_window, "initial_state": str(a.initial_state) if a.initial_state else None,
               "free": [ROOM[i] for i in free], "prior": PRIOR, "prior_sigma": sigma, "per_rack_leakage": a.per_rack_leakage,
               "obs_sigma": a.obs_sigma, "status": m.get("status"), "reason": m.get("reason"),
               "converged": m.get("converged"), "iterations": m.get("iterations"), "wall_s": wall,
               "param_names": m.get("param_names"), "theta_post": m.get("theta_post"),
               "posterior_sigma": m.get("posterior_sigma"), "misfit": m.get("misfit"),
               "data_misfit": m.get("data_misfit"), "contract": m.get("contract"),
               "runDir": res.get("runDir"), "error": res.get("error")}
    skip = a.spinup - (1 if a.initial_state else 0)      # rows of the returned trajectories before the window
    for key in ("observations_prior", "observations_post"):
        m[key] = {n: v[skip:] for n, v in (m.get(key) or {}).items()}
    for tag in ("prior", "post"):
        pred = m.get(f"observations_{tag}") or {}
        for grp, names in (("fit", train), ("held_out", held), ("returns", [f"return_T:{u}" for u in running])):
            tot, per = _rmse(pred, meas, names)
            summary[f"rmse_{grp}_{tag}"] = tot
            summary[f"rmse_{grp}_{tag}_per_step"] = per
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    pp, po = m.get("observations_prior") or {}, m.get("observations_post") or {}
    with open(out / "sensors.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["name", "group", "k", "t_s", "measured_C", "prior_C", "post_C"])
        for n in meas:
            grp = "fit" if n in train else "held_out" if n in held else "return" if n in returns else "return_unfitted"
            for i, k in enumerate(ks):
                w.writerow([n, grp, k, i * CADENCE_S, meas[n][i], (pp.get(n) or [math.nan] * len(ks))[i],
                            (po.get(n) or [math.nan] * len(ks))[i]])
    say(f"{summary['status']} ({summary['reason']}), {summary['iterations']} iterations, {wall:.1f} s")
    for grp in ("fit", "held_out", "returns"):
        say(f"  RMSE {grp:9s} prior {summary[f'rmse_{grp}_prior']} -> post {summary[f'rmse_{grp}_post']}")
    if summary["theta_post"]:
        say("  theta_post", dict(zip(summary["param_names"][:len(ROOM)], [round(v, 4) for v in summary["theta_post"]])))
    say(f"  summary {out / 'summary.json'}")
    return 0 if summary["status"] == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
