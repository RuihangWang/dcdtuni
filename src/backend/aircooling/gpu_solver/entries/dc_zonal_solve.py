"""Steady L0 solve of a prepared OpenFOAM-layout data-centre case with the zonal mesh network, in place.

    python src/entries/dc_zonal_solve.py <run directory> [--out <dir>] [--rtol 1e-10] [--no-write]
    python src/entries/dc_zonal_solve.py --run <run directory>              # as dcdtuni's air runner calls it

The run directory is what dcdtuni prepares for a solve (``processorN/`` or a serial ``constant/polyMesh``, ``0/``,
``constant/``, ``system/``) on an L0 mesh (dcCart coarsening ``coarsenLevel = "L0"``: every patch of the room kept).
The boundary conditions are translated exactly as for the RANS solve (``Parse_mesh/dctwin_bc.py`` into
``<out>/bc``), the network is built from the mesh (``FVsolver/applications/zonal_network.py``) and solved by Newton in
Warp kernels (``zonal_network_warp.py``).  Written:

* ``<out>/result.json`` (default ``<run>/zonal``; ``--run``: ``<run>/gpuNewton/zonal``): the certificate (Newton on
  the network's own residual: volume rows below ``rtol`` times the imposed flow, enthalpy rows below that times
  10 K), timing, the intake temperature of every server, the network's size -- the verdict of record of an L0 run;
* unless ``--no-write``: the zone pressure, temperature and velocity as the OpenFOAM fields ``p_rgh``, ``T`` and ``U``
  at time ``1`` of every partition (``T`` in the case's own units; ``U`` the flux-to-cell reconstruction of the
  network's flows, ``ZonalMesh.velocity``), and ``postProcessing/probes/0/T`` at the ``probeLocations`` of
  ``system/controlDict`` (the zone holding each point);
* the log lines the runner relays (stdout): ``Exec``, one line per Newton iteration, ``End``.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

FORMAT = "lpcns.dc-zonal-solve/v2"


def _write_probes(run: Path, net, T, t_ref: float) -> int:
    from Post_process.foam_bridge import probe_locations
    ctl = run / "system" / "controlDict"
    if not ctl.is_file():
        return 0
    pts, _, fields = probe_locations(ctl.read_text(encoding="utf-8", errors="replace"))
    if not len(pts) or "T" not in fields:
        return 0
    cells = net.cells_at(pts)
    d = run / "postProcessing" / "probes" / "0"
    d.mkdir(parents=True, exist_ok=True)
    head = [f"# Probe {i} ({p[0]:g} {p[1]:g} {p[2]:g})" for i, p in enumerate(pts)]
    head.append("#" + "Probe".rjust(13) + "".join(f"{i:>15d}" for i in range(len(pts))))
    head.append("#" + "Time".rjust(13))
    row = f"{1:>14d}" + "".join(f" {T[c] + t_ref:14.8g}" for c in cells)
    (d / "T").write_text("\n".join(head + [row]) + "\n", encoding="utf-8")
    return len(pts)


def solve_run(run: Path, out: Path | None = None, *, rtol: float = 1e-10, write: bool = True,
              device: str | None = None, log=None) -> dict:
    import torch
    from FVsolver.applications.zonal_network import build_zonal_mesh
    from FVsolver.applications.zonal_network_warp import WarpZonal
    from Parse_mesh import dctwin_bc
    from Parse_mesh.foamio.decomposed import read_case

    say = log or (lambda *_: None)
    t0 = time.perf_counter()
    run = Path(run).resolve()
    out = Path(out) if out is not None else run / "zonal"
    out.mkdir(parents=True, exist_ok=True)
    say("Exec   : zonalNetwork (L0: the zonal pressure-enthalpy network of the mesh, Newton in Warp)")
    timing = {}
    t = time.perf_counter()
    case = read_case(run, keep="connected_open")
    timing["meshRead"] = time.perf_counter() - t
    t = time.perf_counter()
    areas = {p.name: float(case.magSf[p.start:p.start + p.size].sum()) for p in case.patches if p.size}
    dctwin_bc.translate(run, out, areas)
    t_ref = float(dctwin_bc._scalar_entry(dctwin_bc.read_case_dicts(run)["transport"], "TRef"))
    timing["boundaryConditions"] = time.perf_counter() - t
    dev = device or ("cuda" if torch.cuda.is_available() else "cpu")
    t = time.perf_counter()
    net = build_zonal_mesh(run, out / "bc", out / "fvconfig.toml", case=case, device=dev)
    wz = WarpZonal(net, dev)
    timing["network"] = time.perf_counter() - t
    say(f"zonal network: {net.report['cells']} zones, {net.report['links']} links ({net.report['porous_links']} "
        f"porous), {net.report['inflow_patches']} inflow / {net.report['outflow_patches']} outflow items, "
        f"{net.report['racks']} racks, {net.report['pressure_openings']} openings")
    t = time.perf_counter()
    X, info = wz.solve(net.default_values, rtol=rtol)
    timing["solve"] = time.perf_counter() - t
    for k, r in enumerate(info["history"], 1):
        say(f"Newton iteration {k}: max residual / tolerance = {r:.6e}")
    say(f"zonal solve: {info['method']}, {info['iterations']} iterations, max residual / tolerance "
        f"{info['residual_rel_max']:.3e} -> {'converged' if info['converged'] else 'NOT converged'}")
    status = {"format": FORMAT, "run": str(run), "ok": bool(info["converged"]), "network": net.report,
              "certificate": {k: v for k, v in info.items() if k != "history"},
              "rule": "Newton on the zonal network's residual: volume rows <= rtol x imposed flow, enthalpy rows "
                      "<= rtol x imposed flow x 10 K", "rtol": rtol,
              "residualHistory": info["history"], "TRef": t_ref}
    if info["converged"]:
        ti = net.rack_inlet_temperature(X)[0].detach().cpu().tolist()
        status["rackIntakeT"] = {name: v + t_ref for name, v in zip(net.racks, ti)}
        T = X[0, :, 1].detach().cpu().numpy()
        status["zoneT"] = {"min": float(T.min()) + t_ref, "max": float(T.max()) + t_ref}
        if write:
            from Post_process.to_foam import write_fields
            t = time.perf_counter()
            U = net.velocity(X, net.default_values)[0].detach().cpu().numpy()
            status["zoneSpeed"] = {"max": float((U ** 2).sum(1).max() ** 0.5),
                                   "volumeMean": float(((U ** 2).sum(1) ** 0.5 * case.V).sum() / case.V.sum())}
            written = write_fields(case, run, "1", {"T": T + t_ref, "p_rgh": X[0, :, 0].detach().cpu().numpy(),
                                                    "U": U}, template_time="0", explicit=True)
            status["written"] = len(written)
            status["probes"] = _write_probes(run, net, T, t_ref)
            timing["write"] = time.perf_counter() - t
    timing["total"] = time.perf_counter() - t0
    status["timing_s"] = timing
    (out / "result.json").write_text(json.dumps(status, indent=1))
    say("End")
    return status


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("run", type=Path, nargs="?")
    ap.add_argument("--run", dest="run_opt", type=Path, default=None, help="the runner's call: log lines on stdout, "
                    "result in <run>/gpuNewton/zonal")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--rtol", type=float, default=1e-10)
    ap.add_argument("--no-write", action="store_true")
    a = ap.parse_args(argv)
    run = a.run_opt or a.run
    if run is None:
        ap.error("a run directory is required")
    out = a.out if a.out is not None else (run.resolve() / "gpuNewton" / "zonal" if a.run_opt else None)
    log = (lambda line: print(line, flush=True)) if a.run_opt else None
    st = solve_run(run, out, rtol=a.rtol, write=not a.no_write, log=log)
    if not a.run_opt:
        print(json.dumps({k: st[k] for k in ("ok", "certificate", "timing_s")}, indent=1))
    return 0 if st["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
