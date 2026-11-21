#!/usr/bin/env python3
"""Steady solve of one parsed case on a GPU, COLLOCATED and square.

    python src/entries/solve_with_coupled_newton.py --dataset_dir <case>/h5 --gpu 0

The unknowns are one velocity vector and one pressure per interior CELL, against
one momentum vector and one continuity scalar per the same cell, so the Jacobian
is square and a Newton step is one linear solve.  A case whose ``[solver] type``
is the Boussinesq one adds a temperature unknown and an energy row to both sides
of that sentence; WHICH it is comes from the case, and no flag here selects it.
A staggered entry solving the same
equations with the unknowns on FACES was deleted
once the two had been judged on the same case; the numbers are in
``docs/research/`` and the code is in git history.
This one exists because the staggered arrangement is first order on irregular
meshes by its authors' own analysis and the collocated one is not, and because it
reaches the repository's whole ``[fvSchemes]`` surface rather than the handful of
operators the staggered path reimplemented.

Everything about the problem comes from ``--dataset_dir``: mesh, boundary
conditions, viscosity, and every scheme.  Nothing here carries a case default.
The two flags that override the case exist so two arms can be compared without
editing it, and both are logged when they bite: ``--mwi`` switches the
momentum-weighted face flux, which is what makes the square system see the
grid-parity pressure mode at all, and ``--coefficient`` chooses whether its
``d_f`` is differentiated or frozen.
"""
from __future__ import annotations

import argparse
import contextlib
import csv
from datetime import datetime
import json
import math
import os
from pathlib import Path
import shlex
import sys
import time
try:
    import tomllib
except ModuleNotFoundError:                 # Python 3.10
    import tomli as tomllib

# BEFORE torch, because the allocator reads this when the CUDA context is created
# and ignores it afterwards.  Measured on the heated cylinder: peak
# RESERVED falls 16.41 -> 13.16 GiB, a 3.25 GiB saving and a 1.65x -> 1.33x
# reserved-to-allocated ratio, with allocated unchanged (9.96 vs 9.87, which is
# run-to-run noise).  It is pure allocator behaviour -- no tensor moves and no
# number changes.  It matters most at scale: the reserved-minus-allocated gap is
# a flat MULTIPLIER, not an amortising overhead (measured 1.52x default / 1.15x
# expandable across a 4096x cell range), and the largest contiguous request in a
# 3D solve is the Krylov basis, 25.06 GiB at 8.4M cells, which is what OOM'd the
# ldc128 fixture.  An explicit setting in the environment still wins.
_ALLOC_DEFAULTED = "PYTORCH_CUDA_ALLOC_CONF" not in os.environ     # (W12h: main may switch it for large Warp runs)
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

_SRC_DIR = str((Path(__file__).resolve().parents[1]).resolve())
sys.path[:] = [_SRC_DIR] + [
    location for location in sys.path if str(Path(location or os.getcwd()).resolve()) != _SRC_DIR
]
for _name, _module in tuple(sys.modules.items()):
    if _name.split('.')[0] not in {'FVdomain','Pipeline','Utils','FVsolver',
                                    'Post_process','entries'} or _module is None:
        continue
    _locations = ([getattr(_module,'__file__')] if getattr(_module,'__file__',None)
                  else []) + list(getattr(_module,'__path__',()))
    if any(not Path(location).resolve().is_relative_to(_SRC_DIR)
           for location in _locations):
        raise RuntimeError(f'preloaded numerical module outside frozen source: {_name}')

import torch

from FVdomain.SetBC.onthefly.BCbase import VAR_CONFIG  # noqa: E402
from FVdomain.SetBC.onthefly.setRack import cbbm_outer_pass  # noqa: E402
from FVdomain.load_case import load_case  # noqa: E402
from Pipeline.CoupledNewton.collocated_newton import (  # noqa: E402
    EQUILIBRATIONS, NEWTON_PRECONDITIONERS, PRESSURE_OPERATORS, RESTORE_ALPHA,
    RUIZ_SWEEPS, CollocatedSolver, NotAParityLattice, grid_parity_amplitude,
    promote_case_to_double,
)
from Pipeline.CoupledNewton.jacobian import BlockJacobian  # noqa: E402  (block assembly)
from Pipeline.CoupledNewton.stall import STALL_DEFAULT, StallWatch, parse_stall  # noqa: E402
from Post_process.to_vtk import export_mesh_with_patches  # noqa: E402
from SparseSolver.jacobian_assembly import COLOURINGS  # noqa: E402  (probe colourings)
from Utils.Logger import Logger  # noqa: E402


def _ptc_forced_growth(value: str) -> float:
    threshold = float(value)
    if not math.isfinite(threshold) or threshold < 1.0:
        raise argparse.ArgumentTypeError("must be finite and >= 1")
    return threshold


def _unit_interval(value: str) -> float:
    try:
        number = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a number in [0, 1]") from error
    if not 0.0 <= number <= 1.0:
        raise argparse.ArgumentTypeError("must be a number in [0, 1]")
    return number


def lu_options(args) -> dict:
    """``splu`` keywords of ``--learned-action lu``: exactly ``{"permc_spec": "COLAMD"}`` -- the recorded
    call -- at the defaults; ``diag_pivot_thresh`` only when ``--lu-diag-pivot`` moves it off SuperLU's own 1.0."""
    options = {"permc_spec": getattr(args, "lu_permc", "COLAMD")}
    if getattr(args, "lu_diag_pivot", 1.0) != 1.0:
        options["diag_pivot_thresh"] = args.lu_diag_pivot
    return options


def _max_backtracks(value: str) -> int:
    try:
        budget = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a positive integer") from error
    if budget <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return budget


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Steady collocated square Newton solve, on a GPU")
    parser.add_argument("--dataset_dir", required=True,
                        help="the case's h5 directory (mesh + fvconfig.toml + bc/)")
    parser.add_argument("--gpu", type=int, default=0,
                        help="device the case AND the whole solve run on "
                             "(-1 = CPU)")
    parser.add_argument("--max-newton", type=int, default=30,
                        help="Newton step budget")
    parser.add_argument("--max-backtracks", type=_max_backtracks, default=8,
                        help="trial step-length budget per line search (1, 1/2, ...); "
                             "default 8 preserves historical work. More trials keep "
                             "the acceptance test and residual gates unchanged and "
                             "do not guarantee acceptance or convergence")
    parser.add_argument("--nl-accel", default="off", choices=("off", "ngmres"),
                        help="nonlinear acceleration of the Newton loop (opt-in): 'ngmres' = nonlinear GMRES with "
                             "the Newton step as its right preconditioner (Brune et al. 2015): after every "
                             "accepted step the least-squares combination of the last --nl-accel-m states is "
                             "evaluated once and taken only if it lowers ||r||2 below the Newton step's; the "
                             "history is cleared after two refusals in a row and at every solve_newton call "
                             "(every rung).  Refused with --ptc, --ptc-on-stall, --cold-start staged and "
                             "--nonlinear-elimination.  'off' (default): the loop is unchanged")
    parser.add_argument("--nl-accel-m", type=int, default=5,
                        help="--nl-accel: the number of stored states (depth), >= 1")
    parser.add_argument("--tol", type=float, default=1e-12,
                        help="stop once max|r| falls below this")
    parser.add_argument("--residual-control", default=None, metavar="FIELD=REL,...",
                        help="opt-in engineering stop on the final rung (data-centre line 2026-11-06), like OpenFOAM's "
                             "residualControl: every listed field's residual 2-norm below REL times its first value, "
                             "e.g. 'U=1e-3,p_rgh=1e-3,T=1e-4,k=1e-3,epsilon=1e-3' (Pipeline.CoupledNewton.stop_rules)")
    parser.add_argument("--approximate-operator", action="store_true",
                        help="opt-in (data-centre line 2026-11-06): with --operator assembled, let the assembled matrix "
                             "be an approximate operator -- couplings it cannot hold (global reductions such as the "
                             "CBBM intake mean) are omitted instead of refused; the residual and the line search stay "
                             "exact, so the fixed point is unchanged and only the convergence rate is affected")
    parser.add_argument("--qoi-stagnation", type=float, default=None, metavar="TOL",
                        help="opt-in engineering stop on the final rung: the engineering quantities moved by less than "
                             "TOL (kelvin for temperatures) twice in a row. With a dctwin_map.json in the case "
                             "directory these are the ACU return and server intake temperatures (Post_process.dc_qoi), "
                             "otherwise the mean and max temperature. Combined with --residual-control both must hold")
    pt = parser.add_argument_group(
        "pseudo-time driver (opt-in, DC-RANS P2 2026-11-06; Pipeline/CoupledNewton/pseudo_time.py)",
        "--driver pseudo-time replaces solve_newton by a pseudo-transient march (local dt from a CFL with the "
        "convective + viscous spectral radius, one inexact Newton-Krylov step per pseudo-time step, CFL ramp, "
        "rejection with cutback) that stops on --residual-control / --qoi-stagnation; without it nothing below is read")
    pt.add_argument("--odil-atol", type=float, default=1e-9,
                    help="--driver odil: stop when ODIL3D's scaled residual max(rms mom / U^2 h^2, rms cont / U h^2) "
                         "is below this")
    pt.add_argument("--odil-restart", type=int, default=40, help="--driver odil: GMRES restart length")
    pt.add_argument("--odil-uref", type=float, default=1.0, help="--driver odil: reference velocity (scaling, beta)")
    pt.add_argument("--odil-beta", type=float, default=1.0, help="--driver odil: artificial compressibility factor")
    pt.add_argument("--odil-max-du", type=float, default=0.5, help="--driver odil: velocity change limit x U_ref")
    pt.add_argument("--odil-reject", type=float, default=3.0, help="--driver odil: reject when r > this x r_prev")
    pt.add_argument("--odil-krylov-dtype", default="float32", choices=("float32", "float64"))
    pt.add_argument("--odil-lam-floor", type=float, default=None,
                    help="--driver odil on the data-centre backend: face-speed floor of lam (default U_ref)")
    pt.add_argument("--odil-law", default="ser", choices=("ser", "ser+", "ramp"),
                    help="--driver odil on the data-centre backend: CFL law (ODIL3D's SER, or x growth per step)")
    pt.add_argument("--odil-rebuild-every", type=int, default=1,
                    help="--driver odil on the data-centre backend: Picard blocks rebuilt every N steps")
    pt.add_argument("--driver", default="newton", choices=("newton", "pseudo-time", "defect", "odil"),
                    help="the nonlinear driver of every rung: 'newton' (default, solve_newton) or 'pseudo-time'")
    pt.add_argument("--pt-cfl0", type=float, default=5.0, help="initial CFL")
    pt.add_argument("--pt-cfl-max", type=float, default=1.0e4, help="largest CFL")
    pt.add_argument("--pt-cfl-min", type=float, default=1.0e-2, help="smallest CFL (missed solves and cutbacks stop here)")
    pt.add_argument("--pt-law", default="ramp", choices=("ramp", "ser"), help="CFL law")
    pt.add_argument("--pt-growth", type=float, default=1.5, help="CFL growth factor per accepted step")
    pt.add_argument("--pt-uref", type=float, default=None,
                    help="velocity floor of the spectral radius (default: largest boundary normal speed at the start)")
    pt.add_argument("--pt-turb-cfl", type=float, default=1.0, help="CFL factor of the turbulence rows")
    pt.add_argument("--pt-pos-diag", type=float, default=1.0,
                    help="add this x max(0, -J_ii) to the pseudo-time shift (0: off)")
    pt.add_argument("--pt-eta", type=float, default=0.1, help="GMRES relative tolerance per step")
    pt.add_argument("--pt-krylov", type=int, default=60, help="GMRES iterations per step (one cycle)")
    pt.add_argument("--pt-scaling", default="none", choices=("none", "field", "jacobi"),
                    help="norm GMRES minimises: plain, each field weighted by 1/its residual norm, or diag(J + V/dt)^-1")
    pt.add_argument("--pt-precond", default="assembled", choices=("assembled", "compact-frozen", "compact-picard"),
                    help="matrix camg is built on: the colouring-assembled J, or the Warp compact blocks")
    pt.add_argument("--pt-pc-kind", default="camg",
                    choices=("camg", "simple", "covered", "restored", "meanflow", "buoyant", "buoyant-sgs"),
                    help="preconditioner on the shifted blocks: coupled block AMG, or a SIMPLE-family shape of "
                         "preconditioner.simple_preconditioner (with --pressure, --sweeps, --cycles)")
    pt.add_argument("--pt-max-linear-rel", type=float, default=None,
                    help="reject a step whose GMRES ended above this relative residual (default: off)")
    pt.add_argument("--pt-coarse", default="none", metavar="SPACE",
                    help="two-level correction after the preconditioner with piecewise constants of T: 'none', "
                         "'global' (one constant) or 'boxes:<m>' (about m boxes)")
    pt.add_argument("--pt-anderson", type=int, default=0, metavar="M",
                    help="Anderson mixing of depth M over the accepted states (0: off)")
    pt.add_argument("--pt-recycle", type=int, default=0, metavar="K",
                    help="GCRO-DR with K recycled directions carried across steps instead of GMRES (0: GMRES)")
    pt.add_argument("--pt-forcing", default="fixed", choices=("fixed", "ew"),
                    help="GMRES tolerance per step: fixed --pt-eta, or Eisenstat-Walker on the merit (0.05..0.3)")
    pt.add_argument("--pt-camg", default="", metavar="KEY=VAL,...",
                    help="extra block_amg.CoupledAMG keywords of the pseudo-time preconditioner, e.g. pre=2,post=2")
    pt.add_argument("--pt-rebuild-growth", type=float, default=0.0, metavar="K",
                    help="also rebuild when GMRES needs more than K x the iterations of the first solve after a build")
    pt.add_argument("--pt-profile", action="store_true", help="synchronised timers of the step's parts, logged at the end")
    pt.add_argument("--pt-reuse-plan", action="store_true",
                    help="camg rebuilds keep the previous plan and recompute only values (assembled J)")
    pt.add_argument("--pt-retry", default="rebuild", choices=("rebuild", "old"),
                    help="missed solve: rebuild at once, or first continue with the kept preconditioner and twice the "
                         "Krylov budget (and keep it across rejections)")
    pt.add_argument("--dm-steps", type=int, default=5000, help="--driver defect: defect-correction steps at most")
    pt.add_argument("--dm-cfl0", type=float, default=1.0)
    pt.add_argument("--dm-cfl-max", type=float, default=50.0)
    pt.add_argument("--dm-growth", type=float, default=1.05)
    pt.add_argument("--dm-anderson", type=int, default=5)
    pt.add_argument("--dm-pressure-stage", action="store_true", help="SIMPLE pressure correction after each V-cycle")
    pt.add_argument("--dm-rebuild-every", type=int, default=50)
    pt.add_argument("--dm-inner", type=int, default=0, help="fixed GMRES(k) per defect step (0: one bare V-cycle)")
    pt.add_argument("--dm-law", default="ser", choices=("ser", "ramp"))
    pt.add_argument("--dm-turb-cfl", type=float, default=None, help="turbulence CFL factor of the march (default --pt-turb-cfl)")
    pt.add_argument("--dm-anderson-at-cap", action="store_true")
    pt.add_argument("--dm-warmup", type=int, default=0, help="pseudo-time warm-up steps before the defect march")
    pt.add_argument("--dm-hybrid", action="store_true", help="Parallel.newton.HybridCompactFast as the defect preconditioner")
    pt.add_argument("--dm-handover", type=float, default=1e-3,
                    help="merit below which the defect march hands over to the pseudo-time Newton-Krylov tail")
    pt.add_argument("--pt-handover", type=float, default=None, metavar="MERIT",
                    help="below this merit, Newton-like steps (--pt-handover-cfl, --pt-handover-krylov)")
    pt.add_argument("--pt-handover-cfl", type=float, default=1.0e6)
    pt.add_argument("--pt-handover-krylov", type=int, default=400)
    pt.add_argument("--pt-segregate", action="store_true",
                    help="each step solves the mean-flow + T block, then the turbulence block at the updated flow")
    pt.add_argument("--pt-rebuild-factor", type=float, default=4.0,
                    help="rebuild the preconditioner when the CFL moved by more than this factor since its build")
    pt.add_argument("--pt-rebuild-every", type=int, default=10, help="rebuild the preconditioner every N steps")
    pt.add_argument("--pt-clip", type=float, default=0.2, help="turbulence unknowns stay >= this x their old value")
    pt.add_argument("--pt-limit", type=float, default=None,
                    help="turbulence unknowns stay <= (1 + this) x their old value (default: no upper limit)")
    pt.add_argument("--pt-reject", type=float, default=1.0,
                    help="reject a step whose UNSTEADY-residual merit exceeds this x the steady merit before it")
    parser.add_argument("--qoi-window", type=int, default=2, metavar="N",
                        help="--qoi-stagnation must hold for N consecutive accepted steps (default 2; registration "
                             "section 4c: 10)")
    parser.add_argument("--qoi-window-span", action="store_true",
                        help="opt-in (registration section 4d): --qoi-stagnation also needs |q_n - q_{n-N}| < TOL over "
                             "the window of N = --qoi-window accepted steps")
    parser.add_argument("--pin-state", default=None, metavar="PATH",
                        help="opt-in (DC-RANS P17): a full-domain state (field-blocked tensor); with --pin-map, the halo "
                             "cells (layer > 0) of a partition sub-case are held at it (identity rows): Dirichlet data "
                             "on the cut")
    parser.add_argument("--pin-map", default=None, metavar="PATH", help="the partition part's maps.pt (cells, layer)")
    parser.add_argument("--merit-guard", action="store_true",
                        help="opt-in (registration section 4f): the stop also needs the steady merit < 1e-2 and fallen "
                             ">= 2x over the --qoi-window span")
    parser.add_argument("--dctwin-plateau", type=float, default=None, metavar="K",
                        help="opt-in: dctwin's own convergence verdict (S0 amendment 4g) -- every ACU return T's "
                             "peak-to-peak over the last --dctwin-window accepted steps <= K (dctwin: 0.5) and the ACU "
                             "heat within --dctwin-heat of the declared heat at every one of them")
    parser.add_argument("--dctwin-window", type=int, default=100, metavar="N",
                        help="--dctwin-plateau window in accepted steps (dctwin: 100 SIMPLE iterations)")
    parser.add_argument("--dctwin-heat", type=float, default=0.01, metavar="REL",
                        help="--dctwin-plateau heat tolerance (dctwin: 0.01)")
    parser.add_argument("--merit-guard-level", type=float, default=1e-2, metavar="M",
                        help="--merit-guard: the merit must be below M (default 1e-2)")
    parser.add_argument("--merit-guard-drop", type=float, default=2.0, metavar="D",
                        help="--merit-guard: the merit must have fallen by >= D x over the window (default 2)")
    parser.add_argument("--heat-closure", type=float, default=None, metavar="REL",
                        help="opt-in engineering stop on the final rung (registration section 4c): the ACU heat removed "
                             "(Post_process.dc_qoi heat_W:total) within REL of the declared IT heat of dctwin_map.json "
                             "(declared_it_heat_W, or sum rho cp Q deltaT of its servers); needs a dctwin map; combined "
                             "with the other rules all must hold")
    parser.add_argument("--gate", type=float, default=1e-10,
                        help="acceptance threshold on max|r|; the exit status is "
                             "non-zero above it, and it is ALSO what a "
                             "continuation rung must clear to hand its state to "
                             "the next one (rung_advance_gate).  NOTE what it "
                             "certifies: with "
                             "the momentum-weighted flux on, the residual driven "
                             "to zero is the STABILISED one, and the correction "
                             "does not vanish at convergence on a fixed mesh")
    parser.add_argument("--rung-tol", type=float, default=None,
                        help="tolerance for INTERMEDIATE continuation rungs, "
                             "which are different physics and exist only to warm-"
                             "start the next one.  Default None keeps today's "
                             "behaviour: every rung is solved to --tol and judged "
                             "by --gate.  Measured on the DataCenter MLZE arm "
                             "2026-08-19-02-01-10 (--nu-ramp 4096,...,1, 12.2 h "
                             "for 4 rungs of 9): rungs 1-3 were driven to "
                             "5.2e-13 / 9.8e-13 / 7.9e-13 and took 166 Newton "
                             "steps, where reaching 1e-6 took 56 -- a 66% saving, "
                             "12.2 h of progress in about 4.1 h.  When set, it is "
                             "ALSO the advance gate for those rungs, so a rung "
                             "that stalls above it still reports RAMP INCOMPLETE; "
                             "the FINAL rung keeps --tol and --gate untouched.  "
                             "UNTESTED RISK: whether a rung warm-started from a "
                             "1e-6 predecessor still converges is exactly what "
                             "this flag is for measuring")
    parser.add_argument("--inner-rtol", type=float, default=1e-8,
                        help="the tightest relative tolerance a GMRES step may "
                             "be asked for")
    parser.add_argument("--no-forcing", dest="forcing", action="store_false",
                        help="solve every Newton step to --inner-rtol instead of "
                             "letting Eisenstat-Walker track the outer progress")
    parser.add_argument("--restart", type=int, default=400,
                        help="GMRES cycle length.  The total matvec budget is "
                             "this TIMES --max-cycles, so raising it alone "
                             "raises the budget too")
    parser.add_argument("--max-cycles", type=int, default=4,
                        help="number of GMRES cycles per Newton step; the "
                             "matvec budget is --restart x this.  Ships at 4, "
                             "which is the value hardcoded in newton.py until "
                             "2026-08-18.  It exists so the restart WIDTH can "
                             "be varied at a fixed budget: --restart 1600 "
                             "--max-cycles 1 and --restart 400 --max-cycles 4 "
                             "both spend 1600 matvecs and differ only in "
                             "whether the basis is ever discarded")
    parser.add_argument("--region-deflation", default="auto", choices=("auto", "on", "off"),
                        help="conjugate heat transfer (CHT1 README): the coarse correction that carries each solid "
                             "material's temperature level (Pipeline/CoupledNewton/region_deflation.py) composed "
                             "with the preconditioner; auto = on when the case has a solid material, off otherwise "
                             "(every other case: the shipped path, unchanged)")
    parser.add_argument("--deflate", type=int, default=0,
                        help="carry this many harmonic Ritz vectors across each "
                             "GMRES restart (GMRES-DR).  0 ships and is the "
                             "plain iteration BIT FOR BIT.  On the DataCenter "
                             # argparse expands every help string through ``help % params``, so a
                             # literal percent MUST be doubled -- an
                             # unescaped one raises for the WHOLE parser,
                             # not just its own option, which is what made
                             # --help unusable on this entry until
                             # the % is doubled.
                             "handover 8 recovers 63.6%% of what an unrestarted "
                             "1600-vector basis buys at +0.2 GiB of peak against "
                             "its +22.3 GiB; the useful value tracks the count "
                             "of modes within 100x of the Krylov space's "
                             "reachable floor, so it is a property of the "
                             "operator and not a universal constant")
    parser.add_argument("--sweeps", type=int, default=2,
                        help="damped Jacobi sweeps on the momentum block")
    parser.add_argument("--freeze-precond", action="store_true",
                        help="build the preconditioner once per continuation "
                             "rung and reuse it for every Newton step, while "
                             "the Jacobian still follows --reassemble-drift; "
                             "the residual and the matvec stay exact, so the "
                             "fixed point is unchanged")
    parser.add_argument("--cycles", type=int, default=1,
                        help="multigrid V-cycles on the pressure operator, per "
                             "GMRES iteration; past two the outer iteration "
                             "count stops falling and the cost keeps rising "
                             "(collocated_newton.solve_newton has the sweep)")
    parser.add_argument("--fast-linear-kernels", action="store_true",
                        help="preconditioner build and Krylov solve without torch deterministic algorithms (the residual and line search keep them; newton.linear_kernels, 2026-10-04): removes a per-index_add overhead that taxed multicolour GS cycles")
    parser.add_argument("--deterministic-builds", action="store_true",
                        help="with --fast-linear-kernels: the preconditioner builds keep torch's deterministic algorithms "
                             "(only the Krylov solves drop them), so every build -- the smoother's omega included -- "
                             "is the same from run to run (newton.BUILDS)")
    parser.add_argument("--reassemble-drift", type=float, default=0.2,
                        help="how far the state may move, relative to its own "
                             "norm, before the Jacobian is rebuilt; 0 rebuilds "
                             "every step and is the control arm")
    parser.add_argument("--ptc", action="store_true",
                        help="pseudo-transient continuation on the differential "
                             "rows for a cold start with no Newton basin "
                             "(transported turbulence): backward-Euler diagonal "
                             "on momentum/energy/turbulence rows, continuity "
                             "algebraic, SER-controlled, explicit handover to "
                             "pure Newton (newton.solve_newton's knob table)")
    parser.add_argument("--ptc-rungs", default="all", choices=("all", "first"),
                        help="WHICH continuation rungs arm PTC.  'all' is the "
                             "shipped behaviour and re-arms at --ptc-theta0 on "
                             "every rung.  'first' arms only the cold start, "
                             "because every later rung is a WARM start that "
                             "inherits a converged state from the rung below, "
                             "and the recorded heated-cylinder datum is that on "
                             "a warm start PTC re-arms and marches instead of "
                             "letting Newton jump (44 steps against 19 with it "
                             "off).  Measured on DataCenter 2026-08-18: rung 2 "
                             "never reaches the handover on any arm or either "
                             "ladder, parking theta at 0.057-0.06 against the "
                             "1e-4 threshold "
                             "(docs/research/2026-08-18-dc-energy-coverage.md)")
    parser.add_argument("--ptc-theta0", type=float, default=0.2,
                        help="initial 1/CFL of the PTC march (ADflow CFL0 = 5)")
    parser.add_argument("--ptc-growth", type=float, default=2.0,
                        help="max pseudo-timestep growth per accepted step")
    parser.add_argument("--ptc-handover", type=float, default=1e-4,
                        help="relative ||r||2 drop at which PTC hands over to "
                             "pure Newton")
    parser.add_argument("--ptc-max-forced-growth", type=_ptc_forced_growth,
                        default=None,
                        help="optional finite >=1 limit on the steady L2 residual "
                             "growth of a ceiling-forced PTC candidate; reject and "
                             "stop at the last safe iterate when exceeded. "
                             "Default keeps legacy forced acceptance; ordinary "
                             "accepted pseudo-time steps are unchanged")
    parser.add_argument("--ptc-controller", default="ser", choices=("ser", "mrdm", "expur"),
                        help="the pseudo-time controller: 'ser' (default; Kelley & Keyes 1998, under the residual "
                             "ceiling, with forced steps at the ceiling), 'mrdm', or 'expur' (Ceze & Fidkowski 2013, "
                             "Eq. 13; newton.expur_theta): a full step raises the CFL by --ptc-growth, a damped step "
                             "with lambda >= --ptc-hold-lambda holds it, lambda below that or a refused step cuts it by "
                             "--ptc-cut, the CFL within [--ptc-cfl-min, --ptc-cfl-max]; no forced steps, and a step "
                             "refused at --ptc-cfl-min on a fresh linearisation stops the march")
    parser.add_argument("--ptc-hold-lambda", type=float, default=0.01, help="expur: the smallest line-search factor "
                        "that holds the CFL (below it the CFL is cut)")
    parser.add_argument("--ptc-cut", type=float, default=10.0, help="expur: the factor a cut divides the CFL by")
    parser.add_argument("--ptc-cfl-max", type=float, default=1.0e10, help="expur: the largest CFL (1/theta)")
    parser.add_argument("--ptc-cfl-min", type=float, default=1.0, help="expur: the smallest CFL (1/theta)")
    parser.add_argument("--inner-history", action="store_true",
                        help="also write inner_residuals.json: the full "
                             "per-iteration GMRES residual curve for every "
                             "Newton step, buffered in memory and dumped once "
                             "at the end.  Diagnostic only -- gmres already "
                             "builds this tuple, so nothing new is computed "
                             "and no number moves")
    parser.add_argument("--iterate-error", action="store_true",
                        help="opt-in (2026-10-04, GPU-vs-OpenFOAM feasibility "
                             "plan E0): keep every Newton iterate on the host "
                             "(8 bytes per unknown per step) and, after the "
                             "solve, write iterate_error.csv (+ .json): per "
                             "iterate its ramp, rung, step, Newton-timer "
                             "seconds net of those host copies, and the "
                             "volume-weighted relative L2 error of every field "
                             "against the FINAL iterate, the pressure "
                             "mean-removed when the case leaves its level free "
                             "(the formula of Diagnostics.steady_vs_openfoam."
                             "score).  Also adds an 'elapsed' column to "
                             "newton_residual.csv and implies "
                             "--component-timers.  For time-to-accuracy "
                             "against OpenFOAM; no number of the solve moves")
    parser.add_argument("--iterate-error-ref", default=None, metavar="PATH",
                        help="opt-in (2026-10-12, InitialGuess README 12.60, J3): with --iterate-error, score the "
                             "iterates against this saved state (a root certified by another run; read like "
                             "--state) instead of the final iterate, so a run that does not certify still has an "
                             "error history; the solve itself is unchanged")
    parser.add_argument("--contraction-probe", action="store_true",
                        help="opt-in (2026-10-12, InitialGuess README 12.61, J4): at the first Newton step of every "
                             "call, Deuflhard's a-priori contraction estimate Theta_0 = ||dbar_1|| / ||d_0|| (the "
                             "simplified Newton correction at the full trial point, same Jacobian, preconditioner and "
                             "tolerance; Proposition 2's quantity, ~ h_0 / 2) in newton_residual.csv columns "
                             "contraction_theta / contraction_theta_max (largest per-field ratio); one extra residual "
                             "and linear solve, the iterates are unchanged")
    parser.add_argument("--krylov-audit-identity", action="store_true",
                        help="with the audit (implies --krylov-audit): each coupled-AMG hierarchy of a solve against its "
                             "own build -- the exact checksums of its stored values, its action by the path it runs "
                             "against its eager cycle, and against a hierarchy rebuilt from a copy of the matrix and the "
                             "keywords it was built from ([krylov audit] rows, key identity); readings only")
    parser.add_argument("--save-first-correction", default=None, metavar="PATH",
                        help="opt-in: torch.save the first computed Newton correction of the run (before the line "
                             "search) with the iterate it corrects, {'step', 'x'}, to PATH; with --state at a "
                             "certified root it is the distance the certificate leaves open; the solve is unchanged")
    parser.add_argument("--step-norm-log", action="store_true",
                        help="opt-in (2026-10-13, RANS tier README 8.13, J5): the size of every computed Newton "
                             "correction before the line search, ||d_k||_2 and per field ||d_k,f|| / ||x_f||, in "
                             "newton_residual.csv columns step_l2 / step_rel_max / step_rel_fields; at the first step "
                             "of a call Proposition 2's ||J(q0)^-1 F(q0)|| up to the solve's error; logged only")
    parser.add_argument("--component-timers", action="store_true",
                        help="opt-in (2026-10-04): synced per-step "
                             "jacobian_seconds, sparsity_seconds, "
                             "precond_seconds and krylov_seconds columns in "
                             "newton_residual.csv and a total in the log "
                             "(newton.solve_newton component_timers).  The "
                             "device syncs it adds change when the clock is "
                             "read, never what is computed.  On a GPU it also "
                             "logs each step's memory (allocated after the "
                             "preconditioner build, per-step peaks; cuda_*_gib "
                             "columns) and a camg build memory line")
    parser.add_argument("--pressure", default="auto",
                        choices=list(PRESSURE_OPERATORS),
                        help="which operator the pressure V-cycles coarsen: "
                             "'auto' ships and picks the one that approximates "
                             "the Schur for the arm in use ('laplacian', the "
                             "compact pressure equation, with the correction on; "
                             "'simple', D diag(F)^-1 G, with it off).  The three "
                             "explicit names are the comparison arms "
                             "(collocated_newton, PRESSURE_OPERATORS)")
    parser.add_argument("--learned-action", default=None,
                        help="path of a LocalActionNet state_dict; when given, the "
                             "cached local learned action (Pipeline.CoupledNewton."
                             "learned_action.LearnedAction: learned one-hop block "
                             "action + the same pressure V-cycle as --precond simple) "
                             "REPLACES --precond as the inner preconditioner, rebuilt "
                             "at every assembly and subject to --freeze-precond.  "
                             "'classical' instead of a path selects the untrained "
                             "action (SIMPLE with one sweep) for a same-structure "
                             "control, and 'ilu' a SuperLU threshold incomplete "
                             "factorisation of the same assembled Jacobian (CPU) "
                             "for a classical control.  Research arm; not a "
                             "shipped default")
    parser.add_argument("--ilu-drop-tol", type=float, default=1e-3,
                        help="drop tolerance of the --learned-action ilu control")
    parser.add_argument("--ilu-fill", type=float, default=5.0,
                        help="fill factor of the --learned-action ilu control")
    parser.add_argument("--lu-permc", default="COLAMD", choices=("COLAMD", "MMD_AT_PLUS_A", "MMD_ATA", "NATURAL"),
                        help="--learned-action lu: SuperLU column ordering (opt-in, 2026-10-07).  COLAMD (default) is "
                             "every recorded run; MMD_AT_PLUS_A (minimum degree on A + A^T, the usual choice for a "
                             "structurally symmetric FV Jacobian) is the faster candidate -- line-B job 2202704 "
                             "measured COLAMD at fill 38-56x, 99 s (57.6k unknowns) and 231 s (230k) per factorisation")
    parser.add_argument("--lu-diag-pivot", type=_unit_interval, default=1.0,
                        help="--learned-action lu: SuperLU diag_pivot_thresh in [0, 1] (opt-in, 2026-10-07).  1.0 "
                             "(default, not passed: the recorded call) is partial pivoting; a threshold below 1 keeps "
                             "diagonal pivots and with them a symmetric ordering such as MMD_AT_PLUS_A")
    # Robust linear solve (experiments/NaturalConvection/CoupledNewton/RobustLinear/README.md); all off by default
    parser.add_argument("--cudss-precision", default="fp64", choices=("fp64", "fp32"),
                        help="opt-in (II-4): --learned-action cudss / --camg-direct-escalate: precision of the cuDSS "
                             "GPU LU factors (the Krylov iteration stays float64)")
    parser.add_argument("--cudss-hybrid-gib", type=float, default=0.0, metavar="GIB",
                        help="opt-in (II-4): cuDSS hybrid memory mode with GIB GiB of device memory (factors beyond "
                             "it stay in host memory); 0 (default) = factors on the device")
    parser.add_argument("--camg-direct-escalate", type=int, default=0, metavar="K",
                        help="opt-in (II-4, 2026-10-08): camg / camg2w: a linear solve not converged after K GMRES "
                             "cycles (the stagnation rescue's cycles included) continues with the cuDSS GPU LU of the "
                             "same J, and so does every later solve with that preconditioner "
                             "(robust_linear.DirectEscalation); a solve converged within K cycles is unchanged and "
                             "builds no factorisation.  0 (default) = off")
    parser.add_argument("--lu-pin", default="always", choices=("gauge", "always"),
                        help="--learned-action lu: when the constant pressure mode is pinned for the factorisation "
                             "(max|diag| added to the first pressure diagonal).  'always' (default) is the "
                             "pre-2026-10-03 behaviour, a rank-one perturbation on a case whose outlet pins the "
                             "level.  'gauge' (opt-in, 2026-10-03, RANS C7 plan C7b): pin only when resolve_gauge "
                             "finds the pressure level FREE, so the LU of a pinned case (the flat plate) is the "
                             "exact inverse.  A free-gauge case factorises the same matrix either way")
    parser.add_argument("--patch-mask", default=None,
                    help="E1: bool .npy over cells; learned coefficients spliced onto "
                         "masked cells and incident faces, classical elsewhere")
    parser.add_argument("--cell-mass-probe", default=None,
                    help="E1: .npy [K, n_cell] per-cell residual mass at GMRES restarts "
                         "of the FIRST linearisation only")
    parser.add_argument("--probe-policy", default=None,
                    help="E3: k,m,h for the restart-boundary probing policy between the "
                         "classical and the learned candidate, e.g. 5,0.10,2")
    parser.add_argument("--face-adj-dump", default=None,
                    help="E1b: write .npz(src, dst) of the cell face graph at the first "
                         "assembly, for connected-cluster mask generation")
    parser.add_argument("--patch-schwarz", default=None,
                    help="E1b: bool .npy over cells; exact sparse direct solve of the "
                         "assembled Jacobian restricted to masked dofs, multiplicative "
                         "Schwarz under the uncut classical action, rebuilt per reassembly")
    parser.add_argument("--patch-mode", default="incident",
                    help="E1: splice interface, incident | inner | halo")
    parser.add_argument("--cell-cond-dump", default=None,
                    help="E1: .npy [n_cell] diagonal-dominance score of first Jacobian")
    parser.add_argument("--learned-hidden", type=int, default=64)
    parser.add_argument("--learned-passes", type=int, default=2)
    parser.add_argument("--precond", default="simple",
                        choices=list(NEWTON_PRECONDITIONERS),
                        help="which SHAPE the block preconditioner has, or "
                             "'none' for no preconditioner at all -- the "
                             "control arm, which the shipped shape loses to on "
                             "7 of 8 measured RANS bundle/window cells, and "
                             "the measured partner for --equilibrate ruiz.  "
                             "'simple' ships: damped Jacobi on momentum, "
                             "multigrid on the pressure operator, and a raw "
                             "1/diag sweep on any transported block.  "
                             "'restored' is for a linearisation whose "
                             "transported rows have gone near-singular and "
                             "indefinite, where the shipped shape is measured "
                             "WORSE than no preconditioner at all: it inverts "
                             "those blocks through a sign-consistent restored "
                             "diagonal and leaves the mean flow to the "
                             "identity (preconditioner.simple_preconditioner "
                             "has the floor table and the diagnosis).  It "
                             "needs a transported block and refuses a case "
                             "without one.  Pair it with a larger --restart: "
                             "at 400 it parks just above the "
                             "Eisenstat-Walker request.  'meanflow' is that "
                             "shape with the identity replaced by a floored "
                             "per-cell block solve on [u, v, w, p], and on a "
                             "transported block it is the better of the two: "
                             "measured 2026-08-18 it beats 'restored' on all "
                             "three captured flat-plate systems (0.0986 vs "
                             "0.1159 laminar, 0.00264 vs 0.00374 turbulent "
                             "early, 0.8262 vs 0.9981 turbulent late) and on "
                             "the arm-P flat-plate march it fired the PTC "
                             "handover at Newton 166 where 'restored' emitted "
                             "none in 400 steps, finishing 35.7x lower in "
                             "||r||2 on 34%% fewer Krylov iterations.  It is "
                             "OFF by default because it is measured on ONE "
                             "case family; do NOT expect it on a case whose "
                             "uncovered block is not the mean flow (DataCenter "
                             "runs 'simple' and its near-null direction is "
                             "99.91%% the energy row -- use 'covered' there), "
                             "and it does not stop a march parking: that flat "
                             "plate still ends at max|r| 5.46e-07 against the "
                             "1e-10 gate "
                             "(docs/research/2026-08-18-mean-flow-preconditioner.md).  "
                             "'covered' is the 'simple' head with 'restored''s "
                             "floored tail and its Gauss-Seidel pass, i.e. the "
                             "one combination the other three do not offer.  "
                             "It is for a case whose transported block has a "
                             "near-singular or sign-mixed diagonal while its "
                             "pressure machinery is working.  On DataCenter's "
                             "rung 2 the shipped shape reads 0.98749 after "
                             "1600 matvecs, 478x WORSE than no preconditioner, "
                             "and this one converges in 604; on a real march it "
                             "takes that rung from 1600 matvecs never meeting "
                             "tolerance to a mean of 53 always meeting it.  It "
                             "COSTS rung 1 and does NOT finish rung 2 "
                             "(docs/research/2026-08-18-dc-energy-coverage.md)")
    parser.add_argument("--restore-alpha", type=float, default=RESTORE_ALPHA,
                        help="--precond restored only: the restored diagonal's "
                             "floor as a fraction of its own block's "
                             "max|diagonal|; the dose-response that chose the "
                             "default is in preconditioner.RESTORE_ALPHA")
    parser.add_argument("--equilibrate", default="off",
                        choices=list(EQUILIBRATIONS),
                        help="which SYSTEM the inner solve is posed on, which "
                             "is not the same question as which preconditioner "
                             "is applied to it (CoupledNewton/equilibration.py "
                             "opens with why).  'off' ships.  'ruiz' hands "
                             "GMRES the two-sided infinity-norm equilibrated "
                             "system (Dr J Dc) y = Dr (-r) and steps Dc y, "
                             "with the recovery CHECKED every step and the "
                             "true unscaled linear residual logged as "
                             "inner_rel_true beside the scaled one.  Measured "
                             "2026-08-17 on four Jacobians captured from the "
                             "R1 flat-plate march: the best inner-solve arm "
                             "there by 8x-227x, and a LOSER at the laminar "
                             "iterate every other arm was ranked on -- it is "
                             "regime-specific and the mechanism is not known.  "
                             "Pair it with --precond none; composing it with a "
                             "preconditioner is exact and measured an order of "
                             "magnitude worse than the scaling alone")
    parser.add_argument("--ruiz-sweeps", type=int, default=RUIZ_SWEEPS,
                        help="--equilibrate ruiz only: infinity-norm sweeps.  "
                             "Three is enough and one is not; the dose-response "
                             "is in equilibration.RUIZ_SWEEPS")
    parser.add_argument("--matrix-free", action="store_true",
                        help="hand the Krylov solve the Jacobian-vector "
                             "product itself instead of the assembled matrix, "
                             "with d_f DIFFERENTIATED.  This is about "
                             "ACCURACY, not memory: the assembler colours a "
                             "two-hop row pattern, so an assembled matrix can "
                             "only carry the frozen-d_f linearisation, and "
                             "measured 2026-08-17 at the state the flat-plate "
                             "march dies on, that linearisation is 4-95%% wrong "
                             "along the smooth low-wavenumber pressure cluster "
                             "the residual walks into while the implicit JVP "
                             "matches a central difference to ~2e-04.  The "
                             "preconditioner, the Ruiz multipliers and the PTC "
                             "timestep base still come from the frozen "
                             "assembled matrix -- the standard JFNK "
                             "arrangement, legitimate because none of the "
                             "three moves the fixed point.  IT COSTS ONE "
                             "DOUBLE-BACKWARD PASS PER KRYLOV VECTOR against a "
                             "CSR matvec, and the assembly is still paid")
    parser.add_argument("--matrix-free-coefficient", default="implicit",
                        choices=("implicit", "frozen"),
                        help="--matrix-free only: which d_f the JVP carries.  "
                             "'frozen' is the control arm that separates "
                             "'matrix-free' from 'implicit d_f'; it has no "
                             "other reason to be run")
    jfnk_group = parser.add_argument_group(
        "JFNK mode (opt-in, 2026-10-05; Pipeline/CoupledNewton/jfnk.py)",
        "The Krylov operator is the EXACT Jacobian at every Newton step (the matrix assembled at that "
        "iterate, or a Jacobian-vector product of the residual: the same operator) and the preconditioner "
        "is rebuilt by policy; a step that keeps it under --operator jvp/auto assembles nothing.  Both "
        "defaults together are today's solve, call for call.  With either one set, --reassemble-drift is "
        "not used, and --matrix-free, --freeze-precond, --equilibrate and --reuse-across-steps are refused.  "
        "Use --coefficient implicit for the true Jacobian of an MWI residual")
    jfnk_group.add_argument("--backend", default="torch", choices=("torch", "warp"),
                            help="residual / JVP / colouring-probe backend (2026-10-14): 'torch' (default) the "
                                 "application's forward; 'warp' the fused Warp kernels of the same discretisation "
                                 "(buoyantBoussinesqpicoext, docs/research/2026-10-14-warp-backend.md); a "
                                 "configuration the Warp kernels do not cover is refused")
    jfnk_group.add_argument("--operator", default="assembled", choices=("assembled", "jvp", "auto"),
                            help="the Krylov operator: 'assembled' (default) the matrix assembled at this "
                                 "iterate -- with a --precond-rebuild policy every step still assembles and "
                                 "only the preconditioner build is skipped; 'jvp' the exact JVP at every step "
                                 "(an assembly only to rebuild the preconditioner); 'auto' the assembled "
                                 "matrix on the steps that assemble anyway (rebuilds) and the JVP otherwise")
    jfnk_group.add_argument("--precond-rebuild", default="every", metavar="POLICY",
                            help="when the preconditioner is rebuilt: 'every' (default) every Newton step; "
                                 "'every:k' when it has served k steps; 'growth:g' when the previous linear "
                                 "solve needed more than g x the iterations PER DECADE of residual reduction "
                                 "of the first solve after the last build, or missed its tolerance; 'rung' at "
                                 "each continuation rung start (each time step in the transient entry); terms "
                                 "combine with '+', e.g. rung+growth:2.  Always also at the first step, after a "
                                 "refused line search on a kept preconditioner, and at a PTC arming, handover "
                                 "or cutback")
    jfnk_group.add_argument("--precond-rebuild-first-rung", default=None, metavar="POLICY",
                            help="opt-in: the first continuation rung under this --precond-rebuild policy (e.g. every), "
                                 "the later rungs under --precond-rebuild; default: one policy for every rung")
    jfnk_group.add_argument("--jvp", default="tape", choices=("tape", "forward"),
                            help="how the JVP operator is computed: 'tape' (default) one residual tape per "
                                 "Newton step and one second-order pass per product (exactly one colouring "
                                 "probe of the assembly); 'forward' forward-mode AD, one dual-number residual "
                                 "evaluation per product, no tape (lighter, dearer per product), checked once "
                                 "against 'tape' and replaced by it if the check fails")
    ne_group = parser.add_argument_group(
        "Nonlinear elimination, P1 baseline B5 (opt-in, 2026-10-07; Pipeline/CoupledNewton/elimination.py)",
        "Right nonlinear preconditioning after Luo, Cai, Yan, Xu & Keyes, SISC 42(6) 2020: before every "
        "linearisation the residual rows of the cells with the largest residual (plus a halo) are solved for those "
        "cells' unknowns with every other unknown frozen (local Newton, SuperLU of the subset's Jacobian rows and "
        "columns), and the production Newton step is taken from the corrected state.  Off by default (every --ne-* "
        "flag is then unread).  B5 runs it cold from rest without continuation: --nu-ramp 1 --no-auto-ramp")
    ne_group.add_argument("--nonlinear-elimination", default=None, metavar="Q[,LAYERS]",
                          help="switch B5 on: Q is the fraction of cells in the bad core (--ne-rule fraction) or tau "
                               "(--ne-rule threshold: cells whose score is >= tau x the largest), 0 < Q < 1; LAYERS "
                               "(default 1) is the nesting depth: when the subproblem's line search stalls, its worst "
                               "--ne-nest-fraction of cells is eliminated one layer down first (multilayer NE)")
    ne_group.add_argument("--ne-nest-fraction", type=float, default=0.25,
                          help="share of a stalled layer's cells eliminated one layer down (default 0.25)")
    ne_group.add_argument("--ne-backtracks", type=int, default=8,
                          help="trial lengths 1, 1/2, ... of each local Newton step (default 8)")
    ne_group.add_argument("--ne-rule", default="fraction", choices=("fraction", "threshold"),
                          help="how Q picks the bad core (default fraction)")
    ne_group.add_argument("--ne-score", default="field", choices=("field", "max"),
                          help="cell score: 'field' (default) each field's |r| over its largest, then the largest "
                               "over the cell's fields; 'max' the raw max_f |r_f(c)|")
    ne_group.add_argument("--ne-halo", type=int, default=1,
                          help="rings of the Jacobian's cell graph added around the core (default 1)")
    ne_group.add_argument("--ne-inner", type=int, default=4,
                          help="local Newton steps per layer (default 4)")
    ne_group.add_argument("--ne-rtol", type=float, default=1e-2,
                          help="the local solve stops at ||F_S|| <= this x its start (default 1e-2)")
    ne_group.add_argument("--ne-refresh", default="chord", choices=("chord", "newton"),
                          help="'chord' (default): the subset Jacobian is assembled and factored once per layer; "
                               "'newton': reassembled and refactored at every local step")
    ne_group.add_argument("--ne-switch", type=float, default=1e-3,
                          help="hand over to plain Newton for the rest of the rung once ||r||2 <= this x its start "
                               "(default 1e-3; 0 = eliminate at every step)")
    ne_group.add_argument("--ne-max-cells", type=int, default=0,
                          help="cap on the core's cell count, largest scores kept (default 0 = no cap)")
    ne_group.add_argument("--ne-ordering", default="COLAMD",
                          choices=("COLAMD", "MMD_AT_PLUS_A", "MMD_ATA", "NATURAL"),
                          help="SuperLU column ordering of the subset's LU (scipy splu permc_spec; default COLAMD)")
    ne_group.add_argument("--ne-max-growth", type=float, default=None,
                          help="discard a correction that raises ||r||2 by more than this factor (default: keep "
                               "every correction, as right preconditioning does)")
    parser.add_argument("--mwi", default=None, choices=("on", "off"),
                        help="OVERRIDE the case's [fvSchemes] "
                             "momentumInterpolation.  'off' leaves the "
                             "grid-parity pressure mode in the null space of the "
                             "continuity rows, which is the control arm and not a "
                             "configuration to run a case in")
    parser.add_argument("--coefficient", default="frozen",
                        choices=("implicit", "frozen"),
                        help="whether d_f is differentiated ('implicit') or "
                             "detached ('frozen', the default).  FROZEN IS THE "
                             "EXACT ONE HERE, not the approximation its name "
                             "suggests: differentiating d_f = 1/2(V_P/a_P + "
                             "V_Q/a_Q) makes a Jacobian row reach THREE hops "
                             "because a_P sums over a cell's faces, while the "
                             "assembler colours the two hops adjacency@adjacency "
                             "gives it.  Measured 2026-08-15 on the heated "
                             "cylinder: implicit leaves 56 cells outside the "
                             "assembled pattern and ||Jv - J.v||/||Jv|| = "
                             "2.16e-09, three orders past the suite's own 1e-12 "
                             "gate; frozen gives 0 cells outside and 2.27e-16.  "
                             "It is also 32%% sparser (56.78 nnz/row against "
                             "83.09), 2.8 GB lighter, and faster or neutral on "
                             "every case measured -- cylinder -8.1%%, LDC 48^3 "
                             "-7.3%% (and one Newton step FEWER), natural "
                             "convection Ra=1e5 -0.8%% (noise).  Detaching "
                             "changes a derivative, never a value: the two "
                             "converged states agree to 1.42e-10 relative L2.  "
                             "THAT LAST SENTENCE IS ALSO WHY THIS FLAG DOES NOT "
                             "GOVERN --grad-check.  'Exact' above means the "
                             "assembled matrix agrees with the JVP OF THE SAME "
                             "FROZEN TAPE; it does not mean the frozen tape is "
                             "the derivative of the residual that was solved.  "
                             "The forward may ignore that -- a Jacobian that is "
                             "not the true one costs Newton convergence RATE and "
                             "moves no root -- and the implicit function theorem "
                             "may not.  Measured 2026-08-18 on the 8^3 cavity "
                             "against a four-point central-difference plateau of "
                             "dL/dnu = 9.445682235e+01: frozen returns "
                             "-1.031e+02, the WRONG SIGN and 209%% error, and "
                             "both halves are responsible (J frozen alone 0.593, "
                             "dr/dnu frozen alone 1.645).  So the adjoint "
                             "defaults to implicit and REFUSES frozen while the "
                             "momentum-weighted flux is on, independently of "
                             "this flag; see differentiable_solution and "
                             "docs/research/2026-08-18-adjoint-bringup.md.")
    parser.add_argument("--cbbm-passes", type=int, default=8,
                        help="maximum CBBM outer passes (Bai Eq. 15). Each "
                             "pass runs the whole continuation, then refreezes "
                             "every lagged rack's intake mean. Inert on a case "
                             "with no lagged rack, so the default costs "
                             "nothing anywhere else.")
    parser.add_argument("--cbbm-tol", type=float, default=0.01,
                        help="CBBM fixed-point tolerance in kelvin. 0.01 is "
                             "the reference's, and Bai's own numerical-speed "
                             "criterion (his Sec. 4.6) is the same 0.01 C on "
                             "the same quantity.")
    parser.add_argument("--buoyant-recipe", action=argparse.BooleanOptionalAction, default=True,
                        help="laminar Boussinesq cases (unknowns U, p_rgh, T; no turbulence fields) "
                             "default to the true Jacobian + coupled block AMG recipe "
                             "(--learned-action camg --coefficient implicit --reassemble-drift 0 "
                             "--nu-ramp 4,2; docs/provenance/jupiter-openfoam-20261002 section 8): "
                             "every validated buoyant case certifies with it, NaturalConvection Ra1e6 "
                             "only with it.  Each piece applies only when its own flag is not given; "
                             "the ramp is skipped for --state / non-zero --init.  "
                             "--no-buoyant-recipe restores the pre-2026-10-02 default")
    parser.add_argument("--rans-recipe", action="store_true",
                        help="opt-in (2026-10-07, line B pilot, docs/research/2026-10-07-b-line-rans-design.md "
                             "section 5 S-B1): a case whose [solver] turbulence is a transported model (kOmega, "
                             "kOmegaSST, k-epsilon, ...; isothermal or Boussinesq) gets the RANS JFNK recipe: "
                             "--learned-action camg --camg-l1 --coefficient implicit --reassemble-drift 0 --ptc "
                             "--operator auto --precond-rebuild rung+growth:2.  Each piece applies only when its own "
                             "flag is not given (--camg-l1 only on a camg* action, so --learned-action lu keeps its "
                             "exact LU); a case without transported turbulence is refused before it loads.  The "
                             "pieces are decided from the case's fvconfig.toml, so they hold for every rung.  "
                             "Default off: a run without the flag is unchanged.  --buoyant-recipe never touches a "
                             "turbulent case, so the two do not interact")
    cold_group = parser.add_argument_group(
        "staged cold start (opt-in, 2026-10-07, line B S-G1; Pipeline.CoupledNewton.coldstart)")
    cold_group.add_argument("--cold-start", default="off", choices=("off", "staged"),
                            help="'staged': the FIRST continuation rung is solved as a staged homotopy from the "
                                 "first iterate -- A: turbulence pinned (frozen nu_t), mean flow by Newton + PTC; "
                                 "(optional) B: mean flow pinned, turbulence rows by PTC with the positivity map; C: the "
                                 "released full residual by PTC (SER, handover) to the rung's tolerance, positivity "
                                 "map while the shift is armed, restarted with 10x theta0 if it stops early.  Every "
                                 "later rung, the gate and the certificate are unchanged (max|F| on the unmodified "
                                 "residual).  Needs a transported turbulence model (refused before the load "
                                 "otherwise); use it with --init internal.  'off' (default) is today's solve")
    cold_group.add_argument("--cold-stages", default="A,C",
                            help="the stage sequence, ending with one C: A,C (default), C alone (no staging), A,B,C (B = a short turbulence relaxation sweep: bound it with --cold-stage-maxit; solved to convergence it has no positive steady state in a developed shear flow, coldstart docstring)")
    cold_group.add_argument("--cold-stage-rtol", type=float, default=1e-3,
                            help="stages A/B stop at this relative drop of their own (pinned) max|r|")
    cold_group.add_argument("--cold-stage-maxit", type=int, default=30,
                            help="Newton step budget of each A/B stage (C gets --max-newton)")
    cold_group.add_argument("--cold-k-fraction", type=float, default=0.1,
                            help="positivity map: during PTC a cell's k may not fall below this fraction of its "
                                 "current value in one trial (0 = off)")
    cold_group.add_argument("--cold-log-cap", type=float, default=2.0,
                            help="positivity map: during PTC ln(omega) moves by at most this per trial (0 = off)")
    cold_group.add_argument("--cold-k-floor", type=float, default=None, metavar="KMIN",
                            help="opt-in: every bounded trial of the staged cold start (stage C, and the finish with "
                                 "--cold-finish-trial / --cold-finish-trial-always) keeps k at or above KMIN, ln k at "
                                 "or above ln KMIN with --log-turbulence: the reference code's bound(k, kMin) after "
                                 "its k solves (kMin = SMALL = 1e-15 unless the case sets it; "
                                 "coldstart.BoundedTurbulenceTrial).  Default: no floor")
    cold_group.add_argument("--cold-mean-weight", type=float, default=1.0,
                            help="stage C: PTC weight of the mean-flow rows relative to the turbulence rows (1 = "
                                 "the same local pseudo-timestep everywhere)")
    cold_group.add_argument("--cold-theta0", type=float, default=None,
                            help="initial 1/CFL of every stage (default: --ptc-theta0)")
    cold_group.add_argument("--cold-restarts", type=int, default=2,
                            help="how often an early-stopped stage C is re-armed with 10x theta0")
    cold_group.add_argument("--cold-finish-below", type=float, default=None, metavar="MAXR",
                            help="opt-in (2026-10-08): stage C stops once max|r| < MAXR and stage D finishes from "
                                 "that state with the run's own recipe (its PTC + handover, Krylov, preconditioner) "
                                 "and the plain-norm merit -- nothing pinned, no PTC weights, no positivity map; the "
                                 "Jacobi merit froze PTC below ~1e-6 on the hills (job 2203060).  Default: C runs to "
                                 "the run's tolerance (the behaviour before).  With --save-rung-states every stage's "
                                 "end state is saved as x_stage_<label>.pt")
    cold_group.add_argument("--cold-merit", default="jacobi", choices=("jacobi", "l2"),
                            help="merit function of every stage's line search and PTC controller: 'jacobi' = "
                                 "||diag(J)^-1 r|| (the RANS rows sit decades apart; solve_newton merit), 'l2' = "
                                 "||r|| (the control arm).  The gate reads max|r| either way")
    cold_group.add_argument("--cold-finish-restarts", type=int, default=0,
                            help="opt-in (RANS tier pilot-5 R29, 2026-10-09): with --cold-finish-below, stage D2 is also "
                                 "entered when D stops early, and an early-stopped D2 is re-armed with 10x its 1/CFL at "
                                 "most this many times (stage C's rule).  Default 0: D2 as before")
    cold_group.add_argument("--cold-finish-trial", action="store_true",
                            help="opt-in (RANS tier R29 F-c, 2026-10-09): with --cold-finish-below, stage D keeps stage "
                                 "C's bounded turbulence trial (--cold-k-fraction / --cold-log-cap) while its PTC is "
                                 "armed (off after D's handover; D2 unchanged).  Default: D without a trial map")
    cold_group.add_argument("--cold-finish-trial-always", action="store_true",
                            help="opt-in: with --cold-finish-below, stages D and D2 keep stage C's bounded turbulence "
                                 "trial on every trial, also after the handover to Newton, so a cell iterated in ln k / "
                                 "ln epsilon moves by at most --cold-log-cap per trial.  Default: as --cold-finish-trial")
    cold_group.add_argument("--cold-wall-seed", action="store_true",
                            help="opt-in: before the first Newton step, every epsilonWallFunction cell of the first "
                                 "iterate gets the log-law equilibrium k = u_tau^2/sqrt(Cmu) and its wall epsilon, "
                                 "u_tau from the cell's own velocity (nutUWallFunction's y+ iteration), where that y+ "
                                 "is above yPlusLam -- the turbulence a segregated solver's first iteration produces "
                                 "there (coldstart.wall_function_seed).  Only the first iterate changes; a case without an "
                                 "epsilon wall function, or at rest, is unchanged.  Default off")
    cold_group.add_argument("--cold-boundary-layer-seed", action="store_true",
                            help="opt-in: before the first Newton step, the cells of a flat epsilonWallFunction plate "
                                 "that lie inside the turbulent boundary layer of the zero-pressure-gradient "
                                 "correlations (delta = 0.37 x Re_x^-1/5, Cf = 0.0576 Re_x^-1/5) get the layer's k = "
                                 "u_tau^2/sqrt(Cmu) (1 - d/delta) on top of their own, and epsilon = Cmu^0.75 "
                                 "k^1.5 / min(kappa d, 0.09 delta) where that is larger (coldstart.boundary_layer_seed). "
                                 " Only the first iterate changes; a case without an epsilon wall function is "
                                 "unchanged.  Default off")
    cold_group.add_argument("--cold-boundary-layer-wall-epsilon", action="store_true",
                            help="opt-in, with --cold-boundary-layer-seed: the wall-function cells inside the layer "
                                 "take epsilon from their wall constraint Cmu^0.75 k^1.5 / (kappa y), so the seed "
                                 "satisfies those rows (coldstart.boundary_layer_seed(wall_epsilon=True)).  Default off")
    cold_group.add_argument("--cold-boundary-layer-velocity", action="store_true",
                            help="opt-in, with --cold-boundary-layer-seed: the streamwise velocity of the cells inside "
                                 "the layer takes the layer's profile u_c (d/delta)^(1/7), u_c the cell's own value, so "
                                 "the frozen-turbulence stage starts from a mean flow that belongs to the seeded "
                                 "turbulence (coldstart.boundary_layer_seed(velocity=True)).  Default off")
    cold_group.add_argument("--cold-boundary-layer-wall-cells", action="store_true",
                            help="opt-in, with --cold-boundary-layer-seed: the wall-function cells whose centre lies "
                                 "above the correlation's thickness (near the leading edge) take the layer's wall "
                                 "value k = u_tau^2/sqrt(Cmu), never lowered, and epsilon from their patch's wall "
                                 "constraint at that k (coldstart.boundary_layer_seed(wall_cells=True)).  Default off")
    cold_group.add_argument("--cold-boundary-layer-wall-k", action="store_true",
                            help="opt-in, with --cold-boundary-layer-seed: --cold-boundary-layer-wall-cells, and also "
                                 "every wall-function cell inside the layer whose start k is below the wall value "
                                 "u_tau^2/sqrt(Cmu) rises to it, its epsilon the layer's at that k "
                                 "(coldstart.boundary_layer_seed(wall_cells=\"below\")).  Default off")
    cold_group.add_argument("--cold-channel-flow", action="store_true",
                            help="opt-in: before the boundary-layer start, the mean flow of the first iterate becomes "
                                 "the quasi-one-dimensional inviscid flow of the channel under the upper symmetry "
                                 "boundary y0 + h(x): U_e = U_in h(x_in)/h(x) along the streamlines (y - y0)/h(x) = "
                                 "const, p from Bernoulli with the outlet's value (coldstart.channel_flow_seed).  On "
                                 "a channel of constant height it is the uniform start itself.  Default off")
    parser.add_argument("--ptc-handover-below", type=float, default=None, metavar="MAXR",
                        help="opt-in (2026-10-08): the PTC shift also hands over to Newton once max|r| < MAXR (besides "
                             "the relative --ptc-handover drop of ||r||2); for runs that start near a root, where the "
                             "relative rule parks the 1/CFL (solve_newton ptc_handover_below).  Default: the relative "
                             "rule alone")
    parser.add_argument("--ptc-on-stall", type=int, default=0, metavar="K",
                        help="opt-in (2026-10-11, InitialGuess README 12.46): a genuine stall (a step refused on a fresh "
                             "linearisation, where a rung used to stop, or a CAP FALLBACK) arms pseudo-transient continuation from that "
                             "state, at most K times per rung (solve_newton ptc_on_stall); theta --ptc-stall-theta0, "
                             "handover and controller as --ptc.  Default 0: a stall stops the rung, as before")
    parser.add_argument("--ptc-stall-theta0", type=float, default=None, metavar="THETA",
                        help="initial 1/CFL of a stall-armed PTC march (default: --ptc-theta0)")
    parser.add_argument("--ptc-stall-theta-min", type=float, default=0.0, metavar="THETA",
                        help="opt-in (12.46 R29): floor of the 1/CFL while a stall arming is active (default 0: SER alone)")
    parser.add_argument("--mwi-d-scale", type=float, default=None, metavar="ALPHA",
                        help="opt-in (2026-11-07, DC-RANS S0-D part 3): scale the MWI coefficient d_f = V / a_P by "
                             "ALPHA, the under-relaxed rAU of SIMPLE (alpha_U V / a_P, e.g. 0.7); default off")
    parser.add_argument("--save-latest-state", action="store_true",
                        help="opt-in (2026-10-08): write the current iterate to x_latest.pt in the run's Logger dir after "
                             "every Newton step (atomically), so a run stopped by its cap keeps its last iterate; it "
                             "changes no number of the solve")
    parser.add_argument("--conservative-cyclic-flux", action="store_true",
                        help="opt-in (2026-10-08): the two boundary faces of every periodic (cyclic) face pair carry ONE "
                             "volumetric flux, +-(F_a - F_b)/2, instead of each side's own evaluation, so the "
                             "continuity rows sum to zero for every state (BaseSolver.conservative_cyclic_flux).  "
                             "Without it the net mass flux through the periodic planes of the 2-layer hills is "
                             "2.98e-9 / 3.8e-11 / 1.3e-12 (80x60x2 / 160x120x2 / 320x240x2 roots), a residual "
                             "component no Newton step removes.  Default off: the residual is the one before")
    parser.add_argument("--spanwise-invariant", action="store_true",
                        help="opt-in: for a 2D problem on a two-layer cyclic extrusion, keep every iterate, Krylov step "
                             "and trial state invariant across the two cell layers (each unknown replaced by its "
                             "two-layer mean, inside the gauge projection; CollocatedSolver.enable_spanwise_invariance). "
                             "The residual and the certificate are the full ones.  Refused on a mesh that is not two "
                             "paired layers.  Default off")
    parser.add_argument("--log-turbulence", action="store_true",
                        help="opt-in: Newton iterates on the logarithms of the turbulence fields the model stores "
                             "primal (k and epsilon for standard k-epsilon): a change of unknowns, not of "
                             "discretisation -- the residual is the primal one at exp(x), so the root and the "
                             "certificate are unchanged, and states in and out of every solve stay primal "
                             "(Pipeline.CoupledNewton.logvars).  The positivity map of --cold-start caps these "
                             "unknowns per trial by --cold-log-cap.  Default off")
    parser.add_argument("--compare-root", action="append", default=None, metavar="STATE",
                        help="opt-in (2026-10-07): after the solve, compare the final state with a saved state of "
                             "the SAME mesh (x_rung<k>.pt, x_final.pt, or a dict with 'x'; repeatable): per-field "
                             "volume-weighted relative L2, the pressure mean-removed when its level is free (the "
                             "--iterate-error / P1 same-root convention), 'same root' when every field is <= "
                             f"{SAME_ROOT_TOL:g}; the reference's own max|r| on this case is reported beside it.  "
                             "Written to compare_root.json; it changes no number of the solve or the exit status")
    parser.add_argument("--auto-ramp", action=argparse.BooleanOptionalAction,
                        default=True,
                        help="when a zero-initialised run WITHOUT --nu-ramp stops "
                             "above the gate, retry it once from the same first "
                             "iterate with the continuation --nu-ramp "
                             f"{AUTO_RAMP_SCHEDULE} (logged as AUTO-RAMP; the "
                             "failed attempt's steps stay in the history and "
                             "timing).  Runs that certify are untouched; "
                             "--no-auto-ramp restores the single cold attempt "
                             "(added 2026-10-02: the cylinder Re 40 cold start "
                             "stalls without it)")
    parser.add_argument("--beta-ramp", default=None, metavar="B0,B1,...",
                        help="buoyancy continuation (CHT-1 README 5, 2026-10-15; opt-in): multipliers on the case's "
                             "expansion coefficient beta, each in [0, 1), solved in order before the case's own beta. "
                             "The viscosity ladder (--nu-ramp) runs at beta x B0 (B0 = 0: the forced-convection "
                             "problem), then one rung per further multiplier at the case's own viscosity, then beta x 1 "
                             "-- the Richardson-number continuation of mixed convection, where a cold start at full "
                             "buoyancy overshoots (a still fluid conducts its heat source to an unbounded "
                             "temperature in the first linearisation). Both backends (Warp: CHT-0b, 2026-10-20)")
    parser.add_argument("--nu-ramp", default=None,
                        help="viscosity continuation: a comma-separated list of "
                             "MULTIPLIERS on the case's own nu, solved in the "
                             "order given, each warm-starting the next.  The "
                             "case's own value is always solved last and is "
                             "appended here rather than written by the caller, "
                             "so a ramp cannot end anywhere but on the problem "
                             "the case declares -- and a ramp that ends EARLY "
                             "says RAMP INCOMPLETE and exits non-zero whatever "
                             "max|r| reads.  A multiplier of 8 is a "
                             "Reynolds number 8x lower, i.e. a nearly linear "
                             "problem, which is also the control that separates "
                             "'this Reynolds number is hard' from 'the "
                             "preconditioner does not work on this mesh'")
    parser.add_argument("--continuation", default="ladder", choices=("ladder", "adaptive"),
                        help="how a start from rest reaches the case's viscosity.  'ladder' (default): the "
                             "fixed --nu-ramp multipliers, or one solve at the case's viscosity without them.  "
                             "'adaptive': natural continuation in s in [0, 1] with nu x 8^(1 - s) (and the "
                             "diffusivity scaled alike, i.e. Rayleigh / 64^(1 - s) at an unchanged Prandtl number), "
                             "from the cold-start multiplier 8 of AUTO-RAMP at s = 0 to the case at s = 1, with "
                             "the one step controller of Pipeline/CoupledNewton/continuation.py (first step 1/8, "
                             "doubled after a rung of at most 4 Newton steps, halved on a rung that fails, given "
                             "up below 2^-12 or after 1,000 Newton steps; intermediate rungs at most 10 steps to "
                             "1e-6 of the first iterate's max|r| at s = 0, the anchor at s = 0 and the rung at "
                             "s = 1 at most 60 steps, the latter to --tol and judged by --gate).  It applies to "
                             "starts from rest (--init zero or internal) and to a 'rest' fallback of "
                             "--learned-init-fallback; a --state start is solved at the case's viscosity.  Every "
                             "attempt, failed ones included, is in the Newton timer and the history "
                             "(newton_residual.csv columns continuation_s / continuation_final / "
                             "continuation_accepted; continuation.json).  Refused with --nu-ramp, --rung-tol, "
                             "--beta-ramp, --cold-start staged, --operator-correction-mode frozen / auto, "
                             "--init openfoam and a 'rest:' fallback with multipliers; AUTO-RAMP does not retry")
    parser.add_argument("--save-rung-states", action="store_true",
                        help="torch.save each continuation rung's END state to "
                             "the run's Logger dir (x_rung<k>.pt, ~20 MB each "
                             "at DataCenter size).  Exists so the rung-4/5 "
                             "operator -- where every 2026-08-20 DataCenter arm "
                             "parks -- can be captured ONCE and diagnosed "
                             "offline instead of paying a 12-minute climb per "
                             "experiment")
    parser.add_argument("--operator-correction", default=None, metavar="MODEL",
                        help="opt-in (2026-10-08, experiment C1): solve the CORRECTED equation F + C_phi = 0, with "
                             "C_phi the learned conservative face-flux correction saved in MODEL "
                             "(Pipeline.CoupledNewton.operator_correction; trained by "
                             "experiments/NaturalConvection/CoupledNewton/OperatorCorrection/opcorr.py).  It enters the residual, so the Jacobian, "
                             "the line search and the certificate all read F + C_phi.  Default off: the residual is "
                             "the one before, bit for bit")
    parser.add_argument("--operator-correction-precond", default="corrected", choices=("corrected", "plain"),
                        help="with --operator-correction: build the preconditioner from the corrected Jacobian "
                             "(default) or from the PLAIN one ('plain', 2026-10-08, C1 diagnostic D2), the Krylov "
                             "operator then being the exact JVP of F + C (requires --operator jvp)")
    parser.add_argument("--operator-correction-mode", default="newton", choices=("newton", "frozen", "auto"),
                        help="with --operator-correction: 'newton' (default) Newton on F + C; 'frozen' (2026-10-08, C1) "
                             "the defect-correction iteration q_{k+1} solving F(q) = -C(q_k) with the run's own Newton "
                             "and preconditioner on the PLAIN operator, until max|F + C| < --gate "
                             "(Pipeline.CoupledNewton.operator_correction.frozen_correction_solve); the certificate is "
                             "the same max|F + C|; 'auto' (2026-10-09, C1 pilot5) the frozen path first and, if it does "
                             "not certify (it stops on divergence: rho(J_F^-1 J_C) >= 1), Newton on F + C from the same start")
    parser.add_argument("--operator-correction-outer", type=int, default=30, metavar="N",
                        help="with --operator-correction-mode frozen: the outer-iteration budget")
    parser.add_argument("--save-final-state", action="store_true",
                        help="opt-in: atomically save the final bare float64 x and a "
                             "producer-only provenance receipt after the complete run; "
                             "this is not an independent root certificate")
    parser.add_argument("--save-state-to", default=None, metavar="PATH",
                        help="opt-in (2026-10-11): torch.save the final x (a bare tensor, the layout of x_rung<k>.pt) to PATH at "
                             "the end of the run, accepted or not; for figures of a run that has no rung to save (a direct "
                             "start); no case inventory or receipt (that is --save-final-state)")
    parser.add_argument("--dump-residual", default=None, metavar="PATH",
                        help="opt-in (2026-11-06, HF parity): torch.save the per-cell residual of the FIRST iterate "
                             "(--init / --state / the zero start) to PATH and of the final x to PATH with '.final' "
                             "before the suffix, each a dict {x, r, blocks, n_cell} (bare float64 tensors in the "
                             "field-blocked layout of x; blocks = [(field, width, row block)]); the solve is unchanged")
    parser.add_argument("--dump-linear-systems", default=None, metavar="DIR",
                        help="opt-in (2026-10-02): write every assembled Jacobian J(x), the "
                             "state x and the residual F(x) to DIR/step<k>.npz (CSR, float64, "
                             "field-blocked) plus DIR/meta.npz (cell centres, volumes, field "
                             "layout) once, for offline preconditioner benchmarks; the solve "
                             "itself is unchanged.  Since 2026-10-03 each step also records "
                             "whether its J carries the PTC shift (shifted, ptc_theta, "
                             "ptc_shift) and which unknowns are logarithms (log_fields), and "
                             "meta.npz the field widths (install_linear_system_dump)")
    parser.add_argument("--assembly-batch", type=int, default=1,
                        help="probes per vmapped second-order pass in the Jacobian assembly (2026-10-02); "
                             "the entries are bit-identical to 1 (checked on Ra1e6, LDC32, cylinder), "
                             "32 is 1.2-1.85x faster per assembly at the cost of memory")
    parser.add_argument("--camg-lam", default=None, metavar="MODEL",
                        help="O1-HR-2r (2026-10-08; default off): the classical camg / camg2w cycle with LEARNED smoother "
                             "diagonals -- the order-1 lam head (CoupledAMGNet heads=lam, train.log of MODEL) modulates "
                             "every build's GS block inverses (block_amg.stabilized_diagonal); everything else is the "
                             "classical path: P / R, block kernels, plan reuse, CUDA graphs and the l1 stagnation rescue "
                             "(the rescue cycle stays the classical l1 one).  The network runs once per hierarchy build")
    parser.add_argument("--camg-stab-rule", default=None, metavar="RULE:VALUE",
                        help="O1-B R4b (2026-10-17; default off): the classical camg* cycle with the CLASSICAL per-row "
                             "stabilised smoother diagonal D + diag(lam r) (block_amg.SinvStabilizer), e.g. "
                             "'uniform:0.02' (lam = 0.02 on every row; the robust deep value-paired cycle of "
                             "LearnedCoarseSpace R5) -- the classical counterpart of --camg-lam; not with it")
    parser.add_argument("--camg-d1", default=None, metavar="MODEL",
                        help="Order1Headroom B1 (default off): the classical camg* cycle with a trained D1 head "
                             "(CoupledAMGNet heads=d1 / d1row, train.log of MODEL) -- per block row (or scalar row) a "
                             "bounded damping factor and diagonal augmentation of every smoothed level's GS sweep, one "
                             "network pass per hierarchy build -- with its build-time stability scale: at a checked "
                             "build the raw output is scaled by the first theta in (1, 0.5, 0.25, 0) whose cycle "
                             "contraction (12 power steps of I - M A) is at most max(1, the classical cycle's) x 1.02 "
                             "(theta 0 = the classical cycle); between checks the last theta is kept.  P / R, block "
                             "kernels, plan reuse, CUDA graphs and the l1 stagnation rescue stay the classical path")
    parser.add_argument("--camg-d1-check-every", type=int, default=4, metavar="K",
                        help="with --camg-d1: the stability scale is checked at the run's first build and at every K-th "
                             "build after it (K = 1: every build); the builds in between keep the last theta")
    parser.add_argument("--camg-d1-theta", type=float, default=None, metavar="THETA",
                        help="with --camg-d1: a fixed scale of the head's raw output, no check (0 = the classical "
                             "cycle, no network pass; a diagnostic)")
    parser.add_argument("--camg-d1-retain", type=int, default=0, metavar="B",
                        help="with --camg-d1: the progress-based retention of the deployed W's budget mode "
                             "(block_amg.BudgetedLearned, continue_learned): every linear solve starts on the head's "
                             "cycle; at the first restart boundary, after B iterations, a solve whose residual fell by "
                             "--camg-rescue-factor keeps the head, any other continues with the classical cycle (M_C on "
                             "the same plan, built lazily) or, for a loose stalled solve under --camg-rescue-l1, with "
                             "the l1 rescue cycle; replaces the stagnation rescue wrapper.  0 = off")
    parser.add_argument("--camg-d1-stall-handover", action="store_true",
                        help="with --camg-d1-retain: a solve the head keeps is handed to the classical cycle (same plan, "
                             "lazily, from the current iterate) at the first later restart boundary whose GMRES cycle "
                             "did not lower the residual by --camg-rescue-factor from that cycle's start (the "
                             "stagnation trigger's factor at every boundary), for loose and tight solves alike")
    parser.add_argument("--camg-d1-reuse", default="build", choices=("build", "rung"),
                        help="with --camg-d1: 'build' (default) runs the network at every hierarchy build; 'rung' at "
                             "the first build of every solve_newton call (a continuation rung; a time step of the "
                             "transient entry), its raw output laid on that call's later builds")
    parser.add_argument("--camg-field-damping", default=None, metavar="W_U,W_P[,W_T]",
                        help="Order1Headroom A1b (default off): the classical camg* cycle with a constant damping per "
                             "field of its GS sweep (velocity, pressure, temperature rows; e.g. 1.0,0.8,0.9), "
                             "block_amg.FieldDamping; everything else the classical path; not with --camg-d1 / --camg-lam")
    parser.add_argument("--camg-config-every", default="first", choices=("first", "rung"),
                        help="O1-C (2026-10-18; with --learned-action camgcfg:<model.pt>): when the trained ranking "
                             "chooses the AMG configuration from the current matrix -- 'first' (default) at the run's "
                             "first preconditioner build, kept for the run; 'rung' again at the first build of every "
                             "solve_newton call (continuation step, time step).  --learned-action camgcfg:<point.json> "
                             "runs one fixed point of Pipeline.CoupledNewton.amg_config (per-level aggregation, "
                             "transfers, sweeps, damping, per-row stabilisation, coarsest size)")
    parser.add_argument("--camg-config-policy", default="library", choices=("descent", "library", "ensemble"),
                        help="O1-C C1d (2026-10-18; with camgcfg:<model.pt>[,<model.pt>...]): how the rankings choose -- "
                             "'library' (default; Q's argmin over the configurations evaluated in training), 'descent' "
                             "(coordinate descent on Q from the classical points; C1c: leaves the data) or 'ensemble' "
                             "(several rankings, argmin of mean + --camg-config-kappa x spread over the library)")
    parser.add_argument("--camg-config-kappa", type=float, default=1.0,
                        help="O1-C C1d: the spread weight of --camg-config-policy ensemble")
    parser.add_argument("--camg-config-dump", default=None, metavar="FILE",
                        help="O1-C C6 (with camgcfg:<model.pt>): append each choice's record and the state embedding the "
                             "ranking computed from the Jacobian (JSON lines) to FILE -- in-loop data for the selector")
    parser.add_argument("--camg-config-values-only", action="store_true",
                        help="O1-C C5: the library policy chooses among value-based points only (the order-1 switch's "
                             "configuration form; set by --order1-alternate-config)")
    parser.add_argument("--camg-lam-embed", default=None, choices=("passive",),
                        help="C2X2 R29 (2026-10-11; with --camg-lam; default off = the model must have this system's "
                             "fields): a lam model trained on MORE fields (the Boussinesq head on u, v, w, p, T) runs on "
                             "an isothermal system through NNmodels.coupled_amg_net.PassiveScalarEmbed -- the flow as "
                             "Boussinesq at Ra = 0 with a passive temperature; on a system with the model's own fields "
                             "it changes nothing")
    parser.add_argument("--camg-lam-fast", action="store_true",
                        help="O1-HR-2r C3 fix (2026-10-08; with --camg-lam): the network's edge inputs in fp32, its pattern-only "
                             "parts cached per block pattern, and one synthetic warm-up pass at load (process setup)")
    parser.add_argument("--camg-lam-reuse", type=int, default=1, metavar="R",
                        help="O1-HR-2r C3 fix (with --camg-lam): run the network at every R-th hierarchy build and lay its "
                             "cached output on the current matrix in between (NNmodels.coupled_amg_net.LamReuse); 1 = every "
                             "build (default)")
    parser.add_argument("--order1-switch", type=int, default=0, metavar="N",
                        help="E-ABL R29 (2026-10-08, InitialGuess README 12.36; opt-in): the classical inner preconditioner "
                             "until a production linear solve needs N or more GMRES iterations, then the learned order-1 "
                             "form this run gives (--camg-lam, or --learned-action camgnet:<model>) for the rest of the solve "
                             "(sticky; solves on the --camg-l1-start cycle do not count). 0 = off (default)")
    parser.add_argument("--order1-switch-rel", type=float, default=0.0, metavar="K",
                        help="InitialGuess README 12.37 (2026-10-09; opt-in, with --order1-switch N): a classical production "
                             "solve switches only when it needs >= max(N, K x the run's first production solve) GMRES "
                             "iterations -- the run's own first solve calibrates its mesh size and tolerance. 0 = the "
                             "absolute budget N alone (default, 12.36)")
    parser.add_argument("--order1-switch-cap", action="store_true",
                        help="InitialGuess README 12.51 (2026-10-11; opt-in, with --order1-switch N): while the switch is "
                             "armed, a classical production solve stops at the switch's threshold instead of its own "
                             "GMRES budget, so the switch fires there and the next Newton step runs the learned form "
                             "(newton.solve_newton linear_cap). Default off: the switch as in 12.36 / 12.37")
    parser.add_argument("--order1-keep", default="any", choices=("any", "evidence", "evidence-time"),
                        help="InitialGuess README 12.37 (opt-in, with --order1-switch N): 'evidence' keeps the learned "
                             "form only if its first evidential solve needs fewer GMRES iterations per decade of residual "
                             "reduction than the solve that switched; otherwise the run returns to the classical cycle for "
                             "the rest of the solve (a solve the stagnation rescue ended at its probe is no evidence; "
                             "after 3 such solves the learned form stays). 'any' (default, 12.36): sticky")
    parser.add_argument("--order1-alternate", action="append", default=None, metavar="MODEL",
                        help="R6 / E-ABL-H2 R29 (2026-10-10; opt-in, repeatable): a further lam model (--camg-lam form) "
                             "the evidence rule tries when the current learned form's first evidential solve is refused, "
                             "judged against the same switching solve; the classical cycle returns only when every "
                             "learned form was refused. Needs --order1-switch N, --order1-keep evidence and --camg-lam")
    parser.add_argument("--order1-alternate-deep", action="append", default=None, metavar="RULE|MODEL",
                        help="O1-B N1 (2026-10-18; opt-in, repeatable): a further order-1 form the evidence rule tries "
                             "after the --camg-lam form (and any --order1-alternate) -- the deep value-paired cycle "
                             "(camgua22, --camg-gpu-setup --camg-aggregation pairwise1, its own plan cache) with the "
                             "per-row stabilisation of a classical rule ('uniform:0.02', --camg-stab-rule) or of a lam "
                             "model trained on that cycle (a model path, --camg-lam).  Needs --order1-switch N and "
                             "--order1-keep evidence")
    parser.add_argument("--order1-alternate-config", action="append", default=None, metavar="MODEL",
                        help="O1-C C5 (2026-10-18; opt-in, repeatable): a further order-1 form the evidence rule tries -- "
                             "the AMG configuration the trained ranking MODEL chooses from the Jacobian at the switch "
                             "(--learned-action camgcfg:MODEL, library policy restricted to value-based points: the "
                             "classical structural cycle has just stalled).  Needs --order1-switch N and --order1-keep "
                             "evidence")
    parser.add_argument("--order1-config-first", action="store_true",
                        help="O1-C C5 (with --order1-alternate-config): the learned configuration is the switch's first "
                             "order-1 form, the --camg-lam form and any deep form its alternates")
    parser.add_argument("--order1-deep-first", action="store_true",
                        help="O1-B N1 (2026-10-18; with --order1-alternate-deep): the first --order1-alternate-deep form "
                             "is the switch's first order-1 form and the --camg-lam form becomes its alternate (the "
                             "evidence rule then hands a stalling deep cycle back to the learned stabilisation)")
    parser.add_argument("--order1-classical", default="camg2w", metavar="ACTION",
                        help="with --order1-switch and --learned-action camgnet:<model>: the classical camg action used "
                             "until the switch (default camg2w, the camgnet safeguard's own classical cycle)")
    parser.add_argument("--order1-handback-below", type=float, default=None, metavar="MAXR",
                        help="RANS tier R29 F-b (2026-10-09; opt-in, with --learned-action camgnet:<model>, not with "
                             "--order1-switch): the learned order-1 form until a Newton step ends with max|r| < MAXR, "
                             "then --order1-classical's cycle for the rest of the solve (sticky).  Default: off")
    parser.add_argument("--camg-lam-reuse-rung", action="store_true",
                        help="E-ABL (2026-10-08; with --camg-lam-reuse R > 1): also run the network at the first hierarchy "
                             "build of every continuation rung, so a ladder gets the once-per-rung form that O1-HR-2r E4 "
                             "measured on single-rung runs (without it, R = 1000 runs the network once per process). "
                             "A single-rung run is unchanged")
    parser.add_argument("--camg-select", choices=("race", "contraction", "budget"), default="race",
                        help="camgnet selection safeguard (2026-10-02): 'race' runs --camg-race-steps GMRES "
                             "steps on the Newton right-hand side with the learned and the classical "
                             "(GS 2+2, w 0.7) coupled AMG and keeps the faster one in residual decades "
                             "per second; 'contraction' keeps the smaller |r - J M r|/|r| on two random "
                             "probes (the first rule: it ignores the cost per application and picked the "
                             "classical arm on 12 of 13 cylinder Re40 steps); 'budget' skips any pre-selection: "
                             "the learned cycle always runs first under --camg-budget and the classical cycle is "
                             "built only at a hand-over (no second AMG build or race per linearisation)")
    parser.add_argument("--camg-race-steps", type=int, default=10)
    parser.add_argument("--camg-stab-every", type=int, default=1,
                        help="camgnet + --camg-stabilize: run the stability check every k-th linearisation and re-apply "
                             "its blend factors in between (1 = every linearisation)")
    parser.add_argument("--camg-bf16", action="store_true",
                        help="camgnet: run the network's MLPs in bf16 at inference (W itself stays float64)")
    parser.add_argument("--camg-warp-onehop", action="store_true",
                        help="camgnet: the learned one-hop smoother's blocks (W from the network's output and the "
                             "current matrix) in one Warp kernel over the blocks at inference, without the gathered "
                             "diagonal inverses and intermediate products of the torch path; the same blocks to "
                             "rounding (coupled_amg_net.WARP_ONEHOP)")
    parser.add_argument("--camg-fast-features", action="store_true",
                        help="opt-in (2026-10-16, W26): camgnet's smoother network reads its edge inputs in fp32 with "
                             "the pattern-only parts (reverse block index, degrees, aggregate / diagonal flags) cached "
                             "per block pattern, as --camg-lam-fast does for the lam network (fast_features); one "
                             "synthetic warm-up pass at load.  Rounding-level differences in W")
    parser.add_argument("--camg-stabilize", action="store_true",
                        help="camgnet: blend each learned one-hop W towards damped Jacobi until its smoothing "
                             "iteration does not grow (block_amg.CoupledAMG stabilize, power-iteration check)")
    parser.add_argument("--camg-stab-composite", action="store_true",
                        help="camgnet + --camg-stabilize: judge the rate of the smoother the cycle runs (W + the "
                             "hybrid GS sweeps) instead of W alone (2026-10-03: W alone over-blends mid Newton states)")
    parser.add_argument("--camg-stab-select", choices=("first", "argmin"), default="first",
                        help="camgnet + --camg-stabilize: keep the first theta whose rate is acceptable (default) or "
                             "the theta with the smallest measured rate (2026-10-03)")
    parser.add_argument("--camg-net-every", type=int, default=1,
                        help="camgnet: run the network every k-th linearisation only; in between W is rebuilt from the "
                             "cached network output and the new matrix, and the AMG plan (aggregation) is reused "
                             "(NNmodels.coupled_amg_net.ThetaReuse); 1 = network and plan at every linearisation.  "
                             "Since 2026-10-04 both are reused only while the block PATTERN is identical "
                             "(cached_amg_plan); before, an equal block count was taken as the same pattern")
    parser.add_argument("--camg-keep-w", action="store_true",
                        help="opt-in (2026-10-22, close9; with --camg-net-every R > 1): between the network's forwards "
                             "each level keeps the learned W it was given for the rest of the continuation rung instead "
                             "of rebuilding it from the cached network output and the new matrix; the first build of a "
                             "rung rebuilds W from the cached output and the rung's matrix (ThetaReuse keep_w).  "
                             "Default: W rebuilt at every build")
    parser.add_argument("--camg-theta-refresh", type=float, default=None, metavar="G",
                        help="opt-in (2026-10-24, X3; with --camg-net-every R > 1 in the JFNK mode): the network also "
                             "runs at the next build when the first linear solve after a build that laid the cached "
                             "network output on a new matrix needed more than G x the iterations per decade of the "
                             "first solve after the network's last forward (the --precond-rebuild growth measure; "
                             "ThetaReuse refresh_growth).  Default: the --camg-net-every schedule alone")
    parser.add_argument("--camg-turb-split", default=None, metavar="ORDER:TURB",
                        help="opt-in (2026-10-08, line B R1): with --learned-action camg, take the transported "
                             "turbulence rows out of the coupled cycle -- the mean flow by the PG-AMG on its own "
                             "blocks, the turbulence block by an exact host LU (TURB lu) or its own PG-AMG (amg), "
                             "block Gauss-Seidel in ORDER tm (turbulence first), mt or sym "
                             "(Pipeline.CoupledNewton.turbulence_split).  With --learned-action camgnet:<model> "
                             "(2026-10-09, RANS tier) the model's learned cycle, as trained, is the MEAN-FLOW block's "
                             "cycle (the model must read that block's F x F blocks, e.g. a 4-field u v w p model); the "
                             "turbulence block, the budget hand-over and the selection's classical arm are the "
                             "classical split with --order1-classical's cycle.  Default: the coupled cycle")
    parser.add_argument("--camg-split-learned-l1", action="store_true",
                        help="opt-in (2026-10-09, RANS tier R29): with --camg-turb-split and --learned-action camgnet, the "
                             "learned mean-flow cycle runs on an l1 plan with W and its features on the l1 smoother "
                             "diagonal (the stabiliser's theta = 0 is then the classical split's l1 Jacobi).  Default: "
                             "the cycle as the model was trained")
    parser.add_argument("--camg-split-diagonal-race", type=int, default=0, metavar="K",
                        help="opt-in (2026-10-09, RANS tier README 9.8): with --camg-turb-split and --learned-action "
                             "camgnet, the run's FIRST learned build builds the learned mean-flow cycle both as trained "
                             "(raw diagonal) and with W on the l1 smoother diagonal (--camg-split-learned-l1's form), "
                             "runs K GMRES iterations from zero on that linearisation's Newton right-hand side with "
                             "each, and keeps the form with fewer iterations per decade of residual reduction for the "
                             "rest of the run (a tie keeps l1).  0 = off (default)")
    parser.add_argument("--camg-split-diagonal-race-at", default="first", choices=("first", "handover"),
                        help="with --camg-split-diagonal-race K (RANS tier README 9.10): 'first' races at the run's "
                             "first learned build (9.8); 'handover' builds the l1 form until the first build whose "
                             "Jacobian carries no PTC shift (theta = 0, after the hand-over) and races there -- the "
                             "late, unshifted Jacobians are where the raw diagonal fails (9.9)")
    parser.add_argument("--camg-l1", action="store_true",
                        help="camg/camgnet: l1 point-block smoothers, D + diag(sum_j |A_ij|) (Baker et al. 2011), in "
                             "every cycle including the classical hand-over arm -- keeps the GS cycle stable on "
                             "the near-singular early Newton Jacobians (2026-10-03).  A camgnet model whose train.log "
                             "records --l1 runs its LEARNED cycle with the trained l1 variant whatever this flag says "
                             "(logged as a warning); the classical and rescue arms still follow this flag")
    parser.add_argument("--camg-rescue-l1", type=int, default=0, metavar="B",
                        help="camg/camgnet: stagnation rescue (block_amg.StagnationRescue): a linear solve whose "
                             "first B GMRES iterations (the learned budget for camgnet) reduce the residual by "
                             "less than --camg-rescue-factor continues with the l1-smoothed GS 2+2 cycle "
                             "(the early cylinder Jacobians where GS 2+2 is unstable, 2026-10-03); 0 = off")
    parser.add_argument("--camg-rescue-follows-l1-rule", action="store_true",
                        help="opt-in: with --camg-rescue-l1 under --camg-adaptive-l1, no l1 rescue cycle is built: "
                             "the cycle runs the rule's selection, so the rescue never switches a level the "
                             "selection refused (block_amg.l1_rescue_kw); without the rule the rescue is the l1 "
                             "cycle on every level, as without the flag")
    parser.add_argument("--camg-rescue-measured", action="store_true",
                        help="opt-in: with --camg-rescue-l1, a stalled solve switches to the l1 rescue only when the "
                             "l1 rule's own reading (16 GMRES steps of the reference cycle from its seed, on the "
                             "build's matrix) reduces more with l1 on every aggregated level than with the plan that "
                             "runs (block_amg.l1_rescue_measurement, once per build), and returns to the cycle it "
                             "left when the rescue's first GMRES cycle misses --camg-rescue-factor; a solve handed "
                             "back is not rescued again.  Not with --camg-rescue-follows-l1-rule")
    parser.add_argument("--camg-rescue-lend-reused", action="store_true",
                        help="opt-in: the stagnation rescue's l1 plan takes the primary's finest-level symbolic data "
                             "(aggregates, colours, products; block_amg level0_from) also when the primary runs on a plan "
                             "reused from an earlier matrix of the same pattern, not only on a plan built from this "
                             "matrix: the rescue then builds its coarser levels only (the full l1 plan does not fit "
                             "beside the primary on the largest grids)")
    parser.add_argument("--camg-rescue-factor", type=float, default=0.1)
    parser.add_argument("--camg-learned-continue", action="store_true",
                        help="camgnet budget mode: a learned cycle that cut the residual by --camg-rescue-factor "
                             "within its budget keeps the whole solve instead of handing it to the classical cycle")
    parser.add_argument("--camg-rescue-current", type=int, default=0, choices=(0, 1),
                        help="opt-in (2026-10-16, WarpBackend README 27): with --camg-rescue-l1 under a policy that keeps "
                             "the preconditioner, a rescue / hand-over arm that ran a whole GMRES cycle without cutting "
                             "the residual by --camg-rescue-factor continues with the l1 cycle built on the step's own "
                             "assembled matrix (block_amg._FreshEscalation; once per solve).  W18: the classical solver "
                             "1.4x faster on the 147k cylinder at Re 40, the learned one's cold-start path longer there "
                             "(13 -> 22 Newton steps); off by default, so the paper's final protocol is unchanged")
    parser.add_argument("--camg-rescue-min-rtol", type=float, default=None,
                        help="rescue only solves asked for a relative tolerance >= this (e.g. 1e-2: the loose "
                             "early Newton solves); default: any solve")
    parser.add_argument("--camg-rescue-rule", default="factor", choices=("factor", "rate"),
                        help="opt-in (II-4 job 2, 2026-10-08): with --camg-rescue-l1, 'rate' rescues a solve only if "
                             "the primary cycle's rate over the probe cannot reach the solve's tolerance within the "
                             "GMRES iterations left after it (block_amg.StagnationRescue rule 'rate'); 'factor' "
                             "(default) is the --camg-rescue-factor test alone, unchanged")
    parser.add_argument("--camg-rescue-maxit", type=int, default=None, metavar="K",
                        help="opt-in (II-4 job 2, 2026-10-08): with --camg-rescue-l1, a RESCUED solve gets at most K "
                             "iterations of the l1 rescue cycle after the probe and then ends with the step it has "
                             "(0: the probe's step); not applied while --camg-l1-start keeps the primary on l1; "
                             "default: no cap (the rescue runs to the GMRES budget)")
    parser.add_argument("--camg-rescue-deflate", type=int, default=0, metavar="K",
                        help="opt-in: with --camg-rescue-l1, GMRES-DR with K harmonic Ritz vectors on the restarts "
                             "of a RESCUED solve (from the rescue cycle's first restart on), the plain iteration on "
                             "every other solve and before the switch; works with --krylov-basis-chunk; default 0 "
                             "(no deflation)")
    parser.add_argument("--camg-hybrid", type=int, default=None,
                        help="camgnet: GS sweeps appended to the learned post-smoothing (default: as trained)")
    parser.add_argument("--camg-budget", type=int, default=80,
                        help="camgnet: a kept learned cycle runs the FIRST GMRES cycle only, capped at this "
                             "many iterations; if that does not converge GMRES continues with the classical "
                             "cycle (cost <= classical + budget learned iterations per linearisation); 0 = off")
    parser.add_argument("--camg-online", default=None,
                        help="with camgnet: 'steps,patch_cells,patches,lr' on-line fine-tuning of the smoother "
                             "network on BFS patches of every new Jacobian (plan E5), e.g. 20,20000,2,3e-4")
    parser.add_argument("--camg-online-buffer", type=int, default=8,
                        help="replay buffer size (patch systems) of --camg-online")
    parser.add_argument("--dump-every", type=int, default=1,
                        help="with --dump-linear-systems: keep every k-th assembly only")
    parser.add_argument("--state", default=None,
                        help="path to a saved state (x_rung<k>.pt or x_final.pt)"
                             " used as the first Newton iterate INSTEAD of "
                             "--init; pair it with a TRUNCATED --nu-ramp that "
                             "starts at the rung after the one that saved it.  "
                             "A dict with key 'x' (run_phase5's x_checkpoint.pt, "
                             "the r0 checkpoints) is read too (2026-10-03).  "
                             "'head:<model_best.pt>[@exact|@split]' (W9, 2026-10-14): a "
                             "mesh-native state head's start produced in this process on "
                             "the GPU (Pipeline/CoupledNewton/learned_start.py)")
    parser.add_argument("--state-guard", default=None, metavar="PATH",
                        help="residual guard of a learned start (C-CYL R29, 2026-10-10; opt-in): a second start, e.g. "
                             "the network's base state alone (predict.py untrained:graph); the run starts from "
                             "whichever of --state and this state has the smaller ||F||_2 (one more residual "
                             "evaluation, inside the timer).  Needs --state")
    # Deployment mode of a learned / transferred start.  Default off: a run without
    # these flags is the shipped path, keyword for keyword.
    parser.add_argument("--learned-init-abort", default=None, metavar="K,RHO,KMAX[,GROWTH]",
                        help="opt-in (P1 deployment mode, 2026-10-06): watch the FIRST rung of a run that starts "
                             "from --state (or a non-zero --init) and abort it -- logged as LEARNED-INIT ABORT -- "
                             "when a Newton step is refused (lambda = 0), when max|r| grows past GROWTH (default "
                             "10) times its start, when ||r||2 after K steps is above RHO times its start, or when "
                             "KMAX steps pass without the certificate; the time spent stays in the run's timer and "
                             "the steps in its history.  Then --learned-init-fallback acts.  e.g. 3,0.1,10")
    parser.add_argument("--learned-init-fallback", default="none", metavar="SPEC[|SPEC...]",
                        help="what follows a LEARNED-INIT ABORT, tried in order until one certifies (each one "
                             "logged as LEARNED-INIT FALLBACK i/n): 'ramp:M1[,M2..]' a short continuation (nu "
                             "multipliers, the trailing 1 implied) from --learned-init-fallback-state (default: "
                             "the --state start); 'rest[:M1,..]' the continuation from rest (default the buoyant "
                             "recipe's 4,2); 'ptc' pseudo-transient continuation from the start at the case's own "
                             "viscosity; 'none' (default) stops.  e.g. 'ramp:1.7783|rest:12.649,6.3246,3.1623'")
    parser.add_argument("--learned-init-fallback-state", default=None, metavar="PATH",
                        help="the start of a 'ramp:' fallback (e.g. the head's u0 at the first rung's Ra, design "
                             "1.4 F1); loaded before the timer like --state")
    parser.add_argument("--learned-init-stall", default=None, metavar="WINDOW,DROP,FAILED|off",
                        help="with --learned-init-abort (2026-10-06, after p1_tests2): the stall rules every "
                             "watched attempt also aborts on (Pipeline/CoupledNewton/stall.py): FAILED consecutive "
                             "linear solves that missed their tolerance with a relative residual above 1e-2, or "
                             "||r||2 above DROP times its value WINDOW steps earlier, per rung.  Default 4,0.5,2 "
                             "when the monitor is on; 'off' disables them")
    parser.add_argument("--learned-init-refused", type=int, default=1, metavar="N",
                        help="with --learned-init-abort (CHT2 README section 19, 2026-10-22): the refused-step rule "
                             "fires after N CONSECUTIVE refused steps (lambda = 0) of a rung; the default 1 is the first "
                             "refused step, keyword for keyword.  N = 2 lets the solver's own recovery of a refused step "
                             "(a retry on a fresh preconditioner, a cap fallback, the retaken first step of "
                             "--first-fallback-l1-on-refusal) run once before the start is abandoned; a refused step "
                             "the solver stops on aborts at once; the other rules are unchanged")
    parser.add_argument("--learned-init-watch", choices=("all-but-last", "direct"), default="all-but-last",
                        help="with --learned-init-abort (2026-10-06): the attempts the monitor watches -- the "
                             "direct start and every fallback but the LAST (default: a watched fallback that "
                             "aborts is logged 'LEARNED-INIT FALLBACK i/n ABORTED' and the ladder moves on; the "
                             "last one, normally 'rest', runs as the classical continuation would), or only the "
                             "direct start ('direct', the first implementation)")
    # See experiments/NaturalConvection/CoupledNewton/InitialGuess/README.md.  Default off: a run without these flags is the
    # shipped path, keyword for keyword.
    parser.add_argument("--learned-init-project", type=int, default=0, metavar="K",
                        help="opt-in (F1b, 2026-10-07): K SIMPLE pressure-correction steps on the --state start "
                             "before the first Newton step (Pipeline/CoupledNewton/continuity_projection.py: the "
                             "compact MWI pressure Laplacian the SIMPLE preconditioner builds, u -= (V/a_P) grad p', "
                             "p += p'), inside the Newton timer; makes the start's face fluxes divergence-free so "
                             "the central-convection momentum diagonal loses its continuity term; 0 = off")
    parser.add_argument("--camg-l1-start", action="store_true",
                        help="opt-in (F1b-b, 2026-10-07): camg / camg2w build l1 point-block smoothers until the "
                             "first Newton step accepted at lambda = 1, then the production cycle (--camg-l1 as "
                             "given) from a forced rebuild at the next step")
    # Fixed-overhead fixes of a learned start (experiments/NaturalConvection/CoupledNewton/InitialGuess/README.md).  Default off.
    parser.add_argument("--learned-init-project-gpu", action="store_true",
                        help="opt-in (2026-10-14, Warp backend W7): with --learned-init-project-fast, the pressure "
                             "hierarchy of the projection is the coupled block AMG built on the GPU (device MIS-2 "
                             "aggregation, 1x1 blocks) instead of the host pyamg hierarchy")
    parser.add_argument("--learned-init-project-fast", action="store_true",
                        help="opt-in (2026-10-08): the --learned-init-project K projection without its implementation "
                             "overhead (continuity_projection fast path): residuals without deterministic scatter "
                             "kernels, one pyamg pressure hierarchy for all K steps (each step solves its own "
                             "Laplacian to the same tolerance), the correction's gradient without a torch.compile "
                             "recompilation, and no second / third residual evaluation of the start for the log line "
                             "(the projection's own record is logged).  Same K, same steps, same sign rule; the "
                             "certificate is unchanged.  Needs --learned-init-project K >= 1")
    parser.add_argument("--learned-init-project-energy", type=int, nargs="?", const=2, default=0, metavar="E",
                        help="opt-in (CHT2 README section 18, 2026-10-22): after the continuity projection, up to E "
                             "(default 2) Newton steps on the energy rows in T alone at the start's own flow "
                             "(Pipeline/CoupledNewton/energy_projection.py: the T-T block of the solver's Jacobian, "
                             "GMRES with a pyamg hierarchy and the region coarse correction), inside the Newton timer; "
                             "with --state-guard both starts are projected before the guard compares them.  One more "
                             "continuity and energy step when the new T raises the momentum max|r| above 1e-5 "
                             "(buoyancy).  0 = off")
    parser.add_argument("--camg-l1-rescue-shared", action="store_true",
                        help="opt-in (2026-10-08): while --camg-l1-start's l1 cycle is the primary preconditioner, "
                             "the --camg-rescue-l1 stagnation rescue continues with THAT cycle instead of building a "
                             "second, identical l1 hierarchy on the same matrix (one AMG numeric build less in the "
                             "first Newton step); after the hand-over to the production cycle the rescue is built as "
                             "before.  Needs --camg-l1-start and --camg-rescue-l1 B")
    parser.add_argument("--step-clock", action="store_true",
                        help="stamp every Newton row with the Newton timer (newton_residual.csv column 'elapsed'), "
                             "as --iterate-error does but without keeping any iterate: the timed run's wall time at "
                             "each step for the time-to-accuracy readings of an untimed replay; no number of the run "
                             "moves")
    parser.add_argument("--save-iterates", action="store_true",
                        help="diagnostic (F1b, 2026-10-07): with --iterate-error, also write every recorded Newton "
                             "iterate to <run>/iterates.pt (8 bytes per unknown per step) for an offline Jacobian "
                             "probe; no number of the run moves")
    parser.add_argument("--first-forcing", type=float, default=None, metavar="ETA",
                        help="opt-in (F1b-d, 2026-10-07): the FIRST Newton step of every rung asks GMRES for "
                             "max(ETA, --inner-rtol) instead of the --inner-rtol floor (Eisenstat-Walker starts at "
                             "step 2; newton.solve_newton first_forcing), e.g. 1e-2; ignored with --no-forcing")
    parser.add_argument("--first-fallback-l1", type=int, default=None, metavar="N",
                        help="opt-in (W11, WarpBackend README 20.3): with the cheap first step (no --camg-l1-start, "
                             "--first-maxit), a first Newton step that leaves ||r||_2 above 0.99 of the start's -- the "
                             "production cycle stalled on the first linear solve, as on wall-graded meshes -- is taken "
                             "again on the l1-start cycle, as the run's first step (--first-forcing, at most N GMRES "
                             "iterations, e.g. 300 as the registered first step) until the first full step; once per run")
    parser.add_argument("--first-fallback-l1-on-refusal", action="store_true",
                        help="opt-in (CHT2 README section 19, 2026-10-22): with --first-fallback-l1, a REFUSED first "
                             "Newton step (lambda = 0) is retaken on the l1-start cycle too, from the same state "
                             "(newton.solve_newton first_restart on_refusal); without it a refused first step on its "
                             "fresh preconditioner ends the call, as before")
    parser.add_argument("--first-maxit", type=int, default=None, metavar="N",
                        help="opt-in (F1b-e, 2026-10-07): the FIRST Newton step of every rung stops its GMRES after at "
                             "most N iterations over all cycles (newton.solve_newton first_maxit, gmres max_iterations), "
                             "whatever tolerance it asked for; e.g. 200")
    parser.add_argument("--forcing-cap-below", default=None, metavar="R:ETA",
                        help="opt-in (II-4 job 4, 2026-10-08): every Newton step taken from a state with max|r| < R "
                             "asks GMRES for min(Eisenstat-Walker forcing, ETA) (still floored at --inner-rtol), e.g. "
                             "1e-8:1e-3; with ETA below --camg-rescue-min-rtol the stagnation rescue leaves those "
                             "solves to the primary cycle (newton.inner_forcing); refused with --no-forcing")
    parser.add_argument("--forcing-cap-stall", default=None, metavar="RHO:ETA",
                        help="opt-in (II-4 job 5, 2026-10-08): from Newton step 3 of a rung on, a step taken after a "
                             "linear solve that ended above its tolerance (truncated by --camg-rescue-maxit or the "
                             "GMRES budget) and whose step cut ||r||2 by less than RHO asks GMRES for "
                             "min(Eisenstat-Walker forcing, ETA) (floored at --inner-rtol), e.g. 0.9:1e-3: the "
                             "stagnation-triggered form of --forcing-cap-below (newton.forcing_stalled); refused with "
                             "--no-forcing")
    parser.add_argument("--forcing-cap-fallback", type=float, default=None, metavar="LMIN",
                        help="opt-in (II-4 job 5, 2026-10-08), with --forcing-cap-below / --forcing-cap-stall: a "
                             "capped Newton step that the line search refuses or cuts below lambda LMIN (e.g. 0.0625) "
                             "is not taken; the rung continues from the same state with both caps off and the "
                             "stagnation rescue's --camg-rescue-maxit lifted (the l1 rescue runs to the GMRES budget, "
                             "the production path); newton.solve_newton cap_fallback")
    parser.add_argument("--poly-degree", type=int, default=0,
                        help="degree of a minimum-residual polynomial "
                             "preconditioner composed on top of --precond; 0 "
                             "ships and is every pre-2026-08-19 run.  It buys "
                             "no matvecs -- the measured reduction is 1.00x -- "
                             "it buys a smaller Gram-Schmidt product, which is "
                             # "%%" not "%": argparse runs every help string
                             # through `help % dict(vars(action))`, so a bare
                             # "% o" here parsed as the %o format spec and made
                             # --help raise TypeError for the WHOLE entry.
                             "92-98%% of an inner solve here.  On the periodic "
                             "hills Jacobian at an equal 6,4xx-matvec budget, "
                             "three interleaved reps: 56.48 s -> 5.61 s and "
                             "true relative 1.086e-01 -> 4.003e-02.  Degree is "
                             "measured flat from 8 to 200, so 8-24 is the "
                             "range; above 100 the product form loses "
                             "ordering-invariance and is refused")
    parser.add_argument("--init", default="zero",
                        choices=("zero", "internal", "openfoam"),
                        help="what the first Newton iterate is.  'zero' ships "
                             "and is every pre-2026-08-18 run.  'internal' is "
                             "the case's own bc/<field>.toml internalField, "
                             "which is the honest control arm for the next "
                             "one and the only sane cold start for a "
                             "transported turbulence model -- at zero, k = 0 "
                             "and the stored ln(omega) = 0 means omega = 1, a "
                             "state no bc toml asked for.  'openfoam' reads a "
                             "reference solution and maps it onto our cells by "
                             "nearest cell centre (needs --foam); it is the "
                             "COMPETITOR'S ANSWER as a start and every run "
                             "using it has to be reported as such")
    parser.add_argument("--foam", default=None,
                        help="--init openfoam: the reference case's .foam file. "
                             "Fields are matched to ours BY NAME, so the two "
                             "codes must spell them the same way (both call "
                             "them U, p_rgh, T, k, omega); reconstructed or "
                             "decomposed is detected from the case directory")
    parser.add_argument("--foam-time", type=float, default=None,
                        help="--init openfoam: which written time to read; the "
                             "last one by default.  A reference still running "
                             "has its latest time only PARTLY converged, which "
                             "is a property of the start, not of this flag")
    parser.add_argument("--foam-offset", action="append", default=None,
                        metavar="FIELD=VALUE",
                        help="--init openfoam: subtract VALUE from the "
                             "reference's FIELD before it becomes our unknown. "
                             "This is how a case whose stored variable is an "
                             "EXCESS over a reference level reads an absolute "
                             "one -- a temperature carried as T - T_ref against "
                             "a reference writing kelvin.  Repeatable.  The "
                             "number belongs to the case, so it is passed by "
                             "the launcher and never defaulted here")
    parser.add_argument("--grad-check", action="store_true",
                        help="also report dL/dnu through the implicit function "
                             "theorem against a central difference")
    parser.add_argument("--foam-bridge", default=None, metavar="REQUEST.json",
                        help="serve the caller's OpenFOAM-layout run directory (monitor files, log lines, the "
                             "caller's stop rule, final time directory): Post_process/foam_bridge.py; written by "
                             "entries/dc_foam_solve.py")
    parser.add_argument("--no-export", dest="export", action="store_false",
                        help="skip the VTU write")
    parser.add_argument("--exp-name", default=None,
                        help="tag appended to the run directory name")
    parser.add_argument("--colouring-cache", default=None, metavar="DIR",
                        help="opt-in (2026-10-04, GPU-vs-OpenFOAM feasibility plan E2): keep "
                             "the Jacobian probe colouring in DIR/colouring-<sha256>.npz and "
                             "load it instead of rebuilding it (the conflict graph and greedy "
                             "colouring, estimated at 16-23 s of the first assembly on the "
                             "paper cases).  The key covers the mesh connectivity, cyclic and "
                             "rack pairs, cell count, row-block layout, operator or "
                             "preconditioner role, MWI coefficient mode and the numpy/scipy "
                             "versions, so an entry is the colouring a fresh build makes and "
                             "the matrices are bit-identical; it also passes the same "
                             "collision check as a fresh one before any probe uses it, and an "
                             "entry that fails is rebuilt and overwritten.  "
                             "The log says HIT or MISS and the build time.  Default: built "
                             "every run, as before")
    parser.add_argument("--colouring-cache-refresh", action="store_true",
                        help="with --colouring-cache: ignore this case's entry, rebuild the "
                             "colouring and overwrite the entry (explicit invalidation)")
    parser.add_argument("--colouring", default="rcm", choices=COLOURINGS,
                        help="opt-in (2026-10-08, GPU optimisation M4a): the Jacobian probe colouring "
                             "(jacobian_assembly.build_sparsity colouring; probes per assembly = colours x fields).  "
                             "rcm (default): RCM-ordered first fit, 46-47 colours on the DHC meshes.  lattice: a "
                             "linear colouring (a i + b j + c k) mod m of the cells' structured labels, found by an "
                             "exact search over the label differences inside the rows (single-block hex meshes; "
                             "otherwise it falls back to ig).  ig: rcm then iterated-greedy recolouring on the "
                             "device.  dsatur / smallest-last: classical orderings (host, minutes on large meshes).  "
                             "Every colouring passes the same collision check and assembles the same matrix; each "
                             "has its own --colouring-cache entries (rcm keeps its keys)")
    parser.add_argument("--camg-reuse-plan", action="store_true",
                        help="opt-in (2026-10-04, GPU-vs-OpenFOAM feasibility plan E5): camg/camgnet build the "
                             "coupled AMG plan (strength, aggregation, colouring, SpGEMM and transpose index plans) "
                             "once per rung and plan setting and reuse it while the Jacobian's block pattern is "
                             "identical; a reuse recomputes only the numeric phase (level matrices, Galerkin "
                             "products, smoother inverses, prolongation omega).  The learned arm and the classical "
                             "hand-over arm share one plan when their settings agree, and the coarsest level's "
                             "unused colouring is skipped (report colours: 0 there).  The aggregation is FROZEN "
                             "from the first linearisation of each rung: with the entry's theta 0 it depends on "
                             "the block pattern only, so this is the plan a fresh build makes except where a "
                             "coarse block is exactly zero at one linearisation and not the other (see "
                             "--camg-plan-refresh).  A changed pattern rebuilds and is logged.  The residual "
                             "certificate is unchanged.  Default: a new plan at every linearisation, as before")
    parser.add_argument("--camg-sweep-guard", nargs="?", const="coarse", default=None, choices=("coarse", "all"),
                        help="opt-in (classical camg*, every cycle the factory builds, a stagnation rescue's included): "
                             "at every build each level's block Gauss-Seidel sweep -- the coarse levels ('coarse', the "
                             "default when the flag is given bare) or every level ('all') -- is probed on its own "
                             "operator and its damping halved, at most four times, until the probe no longer grows; a "
                             "level whose sweep contracts keeps its damping, bit for bit")
    parser.add_argument("--camg-memory-trace", action="store_true",
                        help="opt-in (diagnosis, no numerical effect): log the device's allocated, reserved and peak "
                             "memory at the stagnation rescue's build, switch and hand-back and in the measured "
                             "rescue's reading (block_amg ``memory_note``)")
    parser.add_argument("--camg-rescue-lean-reading", action="store_true",
                        help="opt-in, with --camg-rescue-measured: the reading's block products (its reference cycle's "
                             "and its GMRES steps') added slice by slice (block_amg ``LEAN_READING``), so no product "
                             "holds two temporaries of the matrix's full block count; the reductions agree to rounding")
    parser.add_argument("--camg-rescue-escalate", type=float, default=0.0, metavar="R",
                        help="opt-in, with --camg-rescue-measured: once a Newton step lowers ||r||2 by less than the "
                             "fraction R (0 < R < 1), the measured rescue neither refuses a stalled solve on its "
                             "reading nor hands it back after one cycle for the rest of that Newton call (continuation "
                             "rung): every later stalled solve is rescued to its budget (block_amg RESCUE_ESCALATION); "
                             "0 = off (default)")
    parser.add_argument("--camg-rescue-hand-back-once", action="store_true",
                        help="opt-in, with --camg-rescue-measured: the measured rescue hands a stalled solve back to "
                             "its primary at most once per Newton call (continuation rung); after that first hand-back "
                             "every later stalled solve of the call is rescued to its budget (block_amg HAND_BACK_ONCE)")
    parser.add_argument("--cudss-probe", type=float, default=0.0, metavar="TOL",
                        help="opt-in: every cuDSS factorisation of the direct-LU preconditioner checked by solving one "
                             "fixed right-hand side; above TOL it is analysed again with matching and checked again, "
                             "and raises if that fails too (robust_linear.DirectLUFactory); 0 = off (default)")
    parser.add_argument("--cudss-deterministic", action="store_true",
                        help="opt-in: every cuDSS factorisation of the run in cuDSS's deterministic mode "
                             "(SparseSolver.cudss ``deterministic_flag``, LPCNS_CUDSS_DETERMINISTIC=1): the factorisations "
                             "repeat bit for bit; other kernels of a run may still differ at round-off")
    parser.add_argument("--camg-unsmoothed-p", action="store_true",
                        help="opt-in (classical camg*): the tentative, piecewise-constant prolongation of the "
                             "aggregates (no smoothing step), with its Galerkin coarse operators; default: smoothed")
    parser.add_argument("--camg-plan-refresh", type=int, default=0, metavar="N",
                        help="with --camg-reuse-plan: also rebuild each plan every N linearisations within a "
                             "rung (bounds the frozen-aggregation risk); 0 = only at rung starts and pattern "
                             "changes")
    parser.add_argument("--fixed-jacobian-pattern", nargs="?", const="structural", default=None,
                        choices=("structural", "compact", "blocks"),
                        help="opt-in (2026-10-04, GPU-vs-OpenFOAM feasibility plan E4): unpack the "
                             "Jacobian probes through a probe-to-CSR map built once per probe plan "
                             "instead of a value != 0 filter with one device sync per probe and a sort "
                             "of every nonzero per assembly (jacobian.assemble fixed_pattern); F/G/D/C/R "
                             "are cut only when a preconditioner reads them.  'structural' (the bare "
                             "flag) stores every entry the colouring can produce, exact zeros included, "
                             "so J's pattern is the same at every assembly; the coupled AMG then drops "
                             "all-zero blocks (block_amg.from_field_blocked_csr drop_zero_blocks) and the "
                             "ilu/lu controls, --patch-schwarz, --dump-linear-systems, the E1 dumps and "
                             "--camg-online drop the stored zeros, so they and the F/G/D/C/R blocks are "
                             "built from today's matrix, while the Krylov matvec also reads the stored "
                             "zeros (the log line 'structural entries are exactly zero' gives their "
                             "share; it is large on a dim 2 case, whose w rows and columns are zero "
                             "off the diagonal).  'compact' drops the zeros in one pass after the gather: J is "
                             "today's matrix bit for bit in deterministic mode.  'blocks' (2026-10-07, GPU "
                             "optimisation M5) writes the same probes straight into the coupled AMG's 5x5 blocks on "
                             "the structural block pattern (jacobian._assemble_blocks: the BlockCSR the structural "
                             "J converts to, bit for bit) and keeps no scalar CSR and no nnz-length map; the Krylov "
                             "operator of an assembling step is the hierarchy's fine-level scalar copy of that "
                             "matrix (sums in another order: rounding level).  It needs a classical camg* with "
                             "--camg-fast-kernels --camg-reuse-plan and --operator auto or jvp.  The residual and the "
                             "gate are untouched.  Default: today's assembly")
    parser.add_argument("--fast-assembly-kernels", action="store_true",
                        help="opt-in (2026-10-04, feasibility plan E4): run the Jacobian assembly (the "
                             "residual tape and every probe) without torch deterministic algorithms, as "
                             "--fast-linear-kernels does for the preconditioner build and the Krylov "
                             "solve (newton.linear_kernels).  J is then reproducible to rounding only; "
                             "the residual the line search and the gate read is evaluated outside the "
                             "assembly and stays deterministic, so the certificate is unchanged.  "
                             "Default: deterministic assembly")
    parser.add_argument("--camg-fast-kernels", action="store_true",
                        help="opt-in (2026-10-04, GPU-vs-OpenFOAM feasibility plan E3): every coupled AMG of "
                             "camg/camgnet (learned, classical hand-over and rescue arms) applies its V-cycle "
                             "with scalar CSR copies of the level matrices, the learned W, P, R and the smoother "
                             "inverses (cuSPARSE; int32 indices) in a per-level layout where each GS colour is one "
                             "contiguous slice (block_amg.CoupledAMG fast_kernels), instead of the block "
                             "gather + einsum + index_add path; the coarsest level's unused colouring is skipped.  "
                             "Same cycle, sums in another order: GMRES iterates reproduce to rounding only.  The "
                             "copies are filled in the AMG build (logged in its report as fast_kernels, about 1.5x "
                             "the hierarchy's block values).  The network, the stability check and the training "
                             "path keep the block kernels; the residual certificate is unchanged.  Default: the "
                             "block kernels, as before")
    parser.add_argument("--fast-line-search", action="store_true",
                        help="opt-in (2026-10-07, GPU optimisation M1a): the residuals the Newton loop "
                             "evaluates (each call's start and every line-search trial) run without torch "
                             "deterministic algorithms (newton.solve_newton fast_line_search), removing the "
                             "sort-based deterministic scatter (~288 ms per residual at 64^3).  The Newton "
                             "trajectory is then reproducible to rounding only.  THE CERTIFICATE IS UNCHANGED: "
                             "the residual at the returned state is re-evaluated after the Newton timer in the "
                             "deterministic mode and the gate reads that value.  Default: deterministic residuals "
                             "everywhere, as before")
    parser.add_argument("--device-sparsity", action="store_true",
                        help="opt-in (2026-10-11, PureGPU stage 3; with --colouring lattice): the Jacobian sparsity's "
                             "cell labels, 2-hop pattern, label differences and lattice colouring are built on the "
                             "solver's device (SparseSolver/sparsity_device.py: the same labels, pattern and colours as "
                             "the host build, the same colouring-cache key) and its tables by the device inversion.  "
                             "Another colouring or face rows: the host build.  Default: the host build, as before")
    parser.add_argument("--gpu-sparsity", action="store_true",
                        help="opt-in (2026-10-07, GPU optimisation M1b): the Jacobian sparsity's per-entity "
                             "tables (the colouring's collision check and the entity -> colour -> cell table, "
                             "jacobian_assembly._invert: 3.7 s of the 4.0 s sparsity build at 64^3, inside the "
                             "Newton timer, colouring cache or not) are computed on the solver's device "
                             "(jacobian_assembly._invert_on_device): the same tables bit for bit, the same "
                             "collision verdict.  Default: on the host, as before")
    parser.add_argument("--camg-cuda-graph", action="store_true",
                        help="opt-in (2026-10-07, GPU optimisation M1c; needs --camg-fast-kernels and CUDA): "
                             "every coupled AMG captures its fast V-cycle once as a CUDA graph (the gauge stays "
                             "eager) and "
                             "replays it for every later application with a field-blocked vector "
                             "(block_amg.CoupledAMG cuda_graph): the same kernels in the same order without "
                             "~1,000 Python / dispatcher launches per cycle.  The first replay is checked "
                             "against the eager cycle (relative difference <= 1e-12, else the graph is dropped "
                             "and the cycle stays eager; report: cuda_graph).  The graph holds a private memory "
                             "pool (a few vectors) released with the hierarchy.  Default: eager V-cycles, as "
                             "before")
    # GPU memory (docs/research/2026-10-07-gpu-optimisation-roadmap.md).  Every flag
    # is opt-in; without them the run makes exactly the calls it made before.
    parser.add_argument("--krylov-basis-chunk", type=int, default=0, metavar="K",
                        help="opt-in (2026-10-07, GPU optimisation M5): allocate GMRES's Arnoldi basis in blocks of K "
                             "vectors as the iteration reaches them (gmres basis_chunk) instead of --restart + 1 "
                             "vectors up front (10.2 GiB at restart 400 on DHC88, 52 GiB on DHC152, while the "
                             "production solves there use 8-114 vectors).  Iterates agree to rounding (the second "
                             "Gram-Schmidt product sums block by block).  0 (default): one buffer, as before")
    parser.add_argument("--camg-lean-build", action="store_true",
                        help="opt-in (2026-10-07, GPU optimisation M5; coupled AMG with --camg-fast-kernels): lean "
                             "AMG plans and numeric passes (block_amg.CoupledAMGPlan lean): the SpGEMM triple index "
                             "arrays, the block-GS colour edges and the transpose columns live in pinned host memory "
                             "and are streamed per chunk; D^-1 A and D^-T A^T are formed chunk by chunk inside the "
                             "tentative-prolongation products instead of as whole arrays; the products accumulate in "
                             "place; the pattern-only transposed BlockCSR carries no values.  Same values (to "
                             "rounding where the build is already non-deterministic).  Default: as before")
    parser.add_argument("--camg-drop-block-values", action="store_true",
                        help="opt-in (2026-10-07, GPU optimisation M5; needs --camg-fast-kernels): after each "
                             "hierarchy's fast scalar copies are filled, its BLOCK values (level matrices, smoother "
                             "inverses, P, R; 5.1 GiB on DHC88) are released level by level: the fast V-cycle never "
                             "reads them.  A lazily built rescue arm starts from the fine values rebuilt exactly from "
                             "their scalar copy (block_amg.CoupledAMG.fine_block_values).  Default: as before")
    parser.add_argument("--camg-int32-groups", action="store_true",
                        help="opt-in (2026-10-07, GPU optimisation M5; needs --camg-fast-kernels): a fast scalar CSR "
                             "with more than 2^31 - 1 entries (the fine level of a 152^3 mesh) keeps int32 column "
                             "indices and per-colour-group int32 row pointers, and its products run group by group, "
                             "instead of falling back to int64 indices.  Changes only such matrices.  Default: int64 "
                             "fallback, as before")
    # The coupled AMG's setup (GPU optimisation roadmap B3).  Every flag is opt-in; without them the run
    # makes exactly the calls it made before.
    parser.add_argument("--camg-structural-plan", action="store_true",
                        help="opt-in (2026-10-08, GPU optimisation M2a; needs --camg-reuse-plan and "
                             "--fixed-jacobian-pattern structural or blocks): every coupled AMG plan is built from the "
                             "STRUCTURAL strength graph (every stored off-diagonal block is a connection, whatever its "
                             "value; block_amg.CoupledAMGPlan strength='pattern'), so its aggregates, colourings and "
                             "index plans depend on the fixed block pattern alone; the plan is kept for the whole run "
                             "(rung starts no longer rebuild it) and every build is a numeric-only pass (omega always "
                             "recomputed).  The aggregation can differ from the value-based one where a block is "
                             "exactly zero (the still start of rung 1), so iteration counts may change; the residual "
                             "certificate is unchanged.  Default: a value-based plan per rung, as before")
    parser.add_argument("--camg-plan-cache", default=None, metavar="DIR",
                        help="with --camg-structural-plan (M2a): read / write the structural plans' aggregation and "
                             "colouring decisions per block pattern in DIR (Pipeline.CoupledNewton.amg_plan_cache; "
                             "sha256 key over the pattern, the plan settings and the algorithm versions; checked where "
                             "used, a bad entry is rebuilt), so a run on a known mesh makes no host aggregation or "
                             "colouring.  Default: no cache (decisions built once per run)")
    parser.add_argument("--camg-aggregation", default="standard",
                        choices=("standard", "naive", "mis2", "mis2o", "mis2c30", "mis2c36", "mis2c42", "pairwise1", "pairwise2", "pairwise3", "pairwise4", "pairwise5"),
                        help="PureGPU stage 1 (2026-10-11): the coupled AMG aggregation.  standard (default) = pyamg "
                             "standard aggregation on the host strength graph, as before; pairwise<k> = k passes of "
                             "AmgX-style handshake pairing on the device strength graph (block_amg."
                             "aggregate_pairwise_device: aggregates of about 2**k cells, no host stage); mis2 (stage 2) = "
                             "MIS-2 aggregation on the device (block_amg.aggregate_mis2_device, Bell-Dalton-Olson: the GPU "
                             "form of standard aggregation, compact aggregates, no host stage); mis2o (stage 3) = "
                             "MIS-2 with its roots picked in the order of the Jacobian probe colour classes (the regular "
                             "root lattice of pyamg's sequential pass, on the device); needs "
                             "--camg-structural-plan or --camg-gpu-setup")
    parser.add_argument("--camg-gpu-setup", action="store_true",
                        help="opt-in (2026-10-08, GPU optimisation M2b; coupled AMG): the plan's strength graph is "
                             "computed on the device (block_amg.strength_on_device: the same graph as the host "
                             "block_strength, bit for bit) and the smoother colouring by Jones-Plassmann on the device "
                             "(block_amg.jones_plassmann_colouring: a valid colouring, but not the host greedy one -- "
                             "colour counts and the GS order differ, so iteration counts may change).  Default: host "
                             "strength and greedy colouring, as before")
    parser.add_argument("--camg-fused-numeric", action="store_true",
                        help="opt-in (2026-10-08, GPU optimisation M2c; coupled AMG): the numeric phase with fewer "
                             "block products: the smoothed prolongation / restriction sum the A blocks per output "
                             "block first and take one 5x5 product per P / R block (instead of two per A block), one "
                             "smoother-diagonal inverse serves omega and the level, and omega's power iteration uses "
                             "torch's BSR mat-vec (cuSPARSE) on the GPU.  Rounding-level differences.  Default: as "
                             "before")
    # The coupled AMG's V-cycle apply path (GPU optimisation roadmap B2.2 / B2.3).  Opt-in; without the
    # flags the run makes exactly the calls it made before.
    parser.add_argument("--camg-block-kernels", action="store_true",
                        help="opt-in (2026-10-08, GPU optimisation M3; needs --camg-fast-kernels): every coupled AMG "
                             "applies its fast V-cycle with fused sliced-ELL BLOCK kernels (Pipeline.CoupledNewton."
                             "block_kernels; Triton on CUDA, a torch reference elsewhere): one kernel per GS colour "
                             "pass (omega Sinv (b - A x) of the colour's rows) instead of two cuSPARSE products and "
                             "three elementwise kernels, 4 bytes of index per 5x5 block instead of per scalar entry; "
                             "residual, restriction and prolongation in the same storage; the fine-level operator of "
                             "an assembling step (--fixed-jacobian-pattern blocks) from the fp64 copy.  Sums in another "
                             "order: rounding-level differences; deterministic.  A cycle the block kernels do not "
                             "cover (learned W, Chebyshev / Jacobi smoothing, trouble set) keeps the scalar copies "
                             "(report: block_kernels).  Default: scalar cuSPARSE copies, as before")
    parser.add_argument("--camg-block-values", choices=("fp64", "fp32"), default="fp64",
                        help="with --camg-block-kernels (M3): the storage of the V-cycle's values (level matrices, "
                             "smoother inverses, P, R; never the dense coarsest LU nor the fine-level operator, which "
                             "stay fp64).  fp32 halves the bytes the cycle reads; the arithmetic stays fp64, so the "
                             "preconditioner is the fixed linear V-cycle of the hierarchy rounded once to fp32 and "
                             "right-preconditioned GMRES applies unchanged (iteration counts may move by a few "
                             "percent; the certificate is unchanged).  Default: fp64")
    parser.add_argument("--camg-block-w", action="store_true",
                        help="opt-in (2026-10-16, W23; needs --camg-block-kernels): a learned one-hop W runs on the "
                             "block kernels too (block_amg.CoupledAMG(block_w=True), O1-HR-2r item B): W has A's "
                             "block pattern, so A's sliced-ELL layout carries it and x += W (b - A x) is one fused "
                             "residual pass and one fused y += W r pass, instead of the scalar cuSPARSE copies the "
                             "learned cycle otherwise keeps (report: block_kernels fallback 'learned one-hop W').  "
                             "Rounding-level differences; no effect on a cycle without a learned W")
    parser.add_argument("--camg-stab-fast", action="store_true",
                        help="opt-in (2026-10-16, W23): the learned W's stability check (power iterations per "
                             "build) on cuSPARSE BSR products of the level's own values (block_amg stab_fast, "
                             "O1-HR-2r item B) instead of the block products; the same check to rounding")
    parser.add_argument("--restart-gib", type=float, default=0.0, metavar="GIB",
                        help="opt-in (2026-10-08, GPU optimisation M3; M2's open item): the GMRES cycle length is "
                             "raised above --restart, up to --restart-max, as far as a full Arnoldi basis (cycle + 1 "
                             "float64 vectors) fits in GIB GiB -- resolved once per mesh from its unknown count "
                             "(16 GiB: 400 at 64^3 / 88^3, 203 at 128^3, --restart at 152^3).  With "
                             "--krylov-basis-chunk the memory is spent only by solves that run that long (the "
                             "last-rung solves at 64^3 Ra 1e7 exhaust a 150-cycle budget).  0 (default): --restart "
                             "as given")
    parser.add_argument("--restart-max", type=int, default=400,
                        help="with --restart-gib: the longest cycle it may choose (default 400, the shipped --restart)")
    # Warp backend (experiments/MultiCase/CoupledNewton/WarpBackend/README.md): opt-in, off =
    # the default torch backend
    parser.add_argument("--camg-warp-numeric", action="store_true",
                        help="opt-in (2026-10-14, Warp backend W6): every coupled-AMG Galerkin product (A P, R A P) and "
                             "P0 block sum of a numeric pass is one Warp kernel over the output blocks, on the triples "
                             "sorted by output once (Pipeline.CoupledNewton.block_amg_warp); a lean --camg-fused-numeric "
                             "plan keeps them on the device as 8-byte segments instead of 24-byte triples in pinned "
                             "host memory.  Rounding-level differences")
    parser.add_argument("--camg-warp-omega", action="store_true",
                        help="opt-in (2026-10-14, Warp backend W6): the prolongation weight's power iteration applies "
                             "D^-1 A with one Warp kernel per product instead of cuSPARSE BSR + einsum.  Rounding-level "
                             "differences")
    parser.add_argument("--krylov-basis-fp32", action="store_true",
                        help="opt-in algorithm variant (2026-10-14, Warp backend W2): with --krylov-fused-orth or "
                             "--krylov-device-loop, the Arnoldi basis is stored in fp32 and every product, sum and "
                             "norm is computed in fp64 (compressed-basis GMRES): half the Gram-Schmidt traffic and "
                             "basis memory; the iterate and the true residual stay fp64")
    parser.add_argument("--camg-lazy-colours", action="store_true",
                        help="opt-in (2026-10-14, Warp backend W6): a structural plan forms every GS colour's rows "
                             "with one device sort (one read-back) and the block-GS edge lists only if the block path "
                             "reads them (the fast cycle never does): the same plan, ~250 host round trips fewer on W01")
    parser.add_argument("--camg-omega-device", action="store_true",
                        help="opt-in (2026-10-14, Warp backend W6): the prolongation weight's power iteration keeps "
                             "its norms on the device (one read-back per estimate instead of one per iteration); the "
                             "same omega bit for bit")
    parser.add_argument("--camg-share-plan", action="store_true",
                        help="opt-in (2026-10-14, Warp backend W6; with --camg-structural-plan): a structural plan "
                             "differing from an earlier one of the same pattern only in l1 / diag_eps shares its "
                             "symbolic data (Pipeline.CoupledNewton.amg_plan_cache.SHARE): the same hierarchy, one "
                             "symbolic build fewer (W01: the l1 start plan and the production plan)")
    parser.add_argument("--camg-rows-kernel", action="store_true",
                        help="opt-in (2026-10-14, Warp backend W2; with --camg-block-kernels): sliced-ELL patterns of "
                             "slice height >= 16 (the fine levels, their P and the fine-level operator) run a Warp "
                             "thread-per-row kernel (Pipeline.CoupledNewton.block_kernels_warp) instead of the Triton "
                             "tile: one column and x read per block.  Rounding-level differences")
    parser.add_argument("--camg-rows-products", action="store_true",
                        help="opt-in (2026-10-14, Warp backend W2): the Warp rows kernel for the sliced-ELL PRODUCTS "
                             "only (residual, prolongation, restriction, fine-level operator; slice height >= 16), the "
                             "GS colour passes keep the Triton tile (WarpBackend README 9.2)")
    parser.add_argument("--camg-zero-skip", action="store_true",
                        help="opt-in (2026-10-14, Warp backend W2; with --camg-block-kernels): the first pre-smoothing "
                             "GS sweep of every level starts from x = 0 and reads only the blocks whose column colour "
                             "is below the row's (the others multiply exact zeros): the same sweep bit for bit")
    parser.add_argument("--camg-adaptive-l1", type=float, default=None, metavar="T",
                        help="coupled AMG (camg*, camgnet): the operator-decided l1 rule in every cycle of the run, the "
                             "classical and the learned one alike -- an aggregated level whose damped block Jacobi has "
                             "an estimated spectral radius above T switches to the l1 smoother diagonal (block_amg "
                             "adaptive_l1; with a learned cycle decided before the network reads the level).  For "
                             "camgnet a threshold the model's train.log records (plan_json) takes precedence.  "
                             "Default: off")
    parser.add_argument("--camg-adaptive-l1-gate", type=float, default=None, metavar="G",
                        help="with --camg-adaptive-l1: the levels the radius test flags switch only when the classical "
                             "reference cycle on the plan (one Gauss-Seidel sweep each way at 0.7, no network) grows "
                             "by more than G per application, and then the plan (no switch, those levels with every "
                             "coarser one, or l1 on every level) whose reference cycle reduces most in 16 GMRES "
                             "steps (block_amg CoupledAMG._l1_gate; the same in both arms).  For camgnet a gate the model's train.log records (plan_json) takes precedence.  "
                             "Default: no gate")
    parser.add_argument("--camg-coarse-max", type=int, default=None, metavar="N",
                        help="coupled AMG (camg*, camgnet): stop coarsening once a level has at most N rows (the "
                             "plan's coarse_max; default: the plan's own value).  Given, it also replaces the "
                             "coarse_max of a hierarchy a camgnet model's train.log records (plan_json), so that "
                             "the classical and the learned cycle share one hierarchy")
    parser.add_argument("--camg-coarse-inverse", action="store_true",
                        help="opt-in (2026-10-14, Warp backend W2; with --camg-block-kernels): the coarsest level is "
                             "applied as one GEMV with its explicit inverse (computed once per hierarchy from the same "
                             "LU) instead of the dense triangular solves.  Rounding-level differences")
    parser.add_argument("--camg-coarse-inverse-min-pivot", type=float, default=None, metavar="T",
                        help="opt-in, with --camg-coarse-inverse: on a case whose pressure level is free, a hierarchy "
                             "whose coarse LU has its smallest pivot below T times its largest applies the coarsest "
                             "level by the LU (block_amg COARSE_INVERSE min_pivot); a pinned level is unchanged")
    parser.add_argument("--camg-coarse-gauge", action="store_true",
                        help="opt-in: on a case whose pressure level is free, every block cycle's coarse correction "
                             "without the constant pressure mode (block_amg COARSE_GAUGE); a pinned level is unchanged")
    parser.add_argument("--krylov-audit-levels", action="store_true",
                        help="instrumentation (implies --krylov-audit): each audit row also reads, per level of every "
                             "coupled-AMG hierarchy, the growth per application of its pre-smoother's and of its "
                             "V-cycle's error propagation (krylov_audit.level_contraction); nothing in the run changes")
    parser.add_argument("--krylov-audit", action="store_true",
                        help="instrumentation: after every Newton step's Krylov solve, the true linear residual of "
                             "the step, its slope, the residual's directional derivative, the preconditioner's "
                             "repeatability and linearity, and each hierarchy's coarse LU pivots "
                             "(Pipeline/CoupledNewton/krylov_audit.py); nothing in the run changes")
    parser.add_argument("--true-residual-check", type=float, default=0.0, metavar="FACTOR",
                        help="opt-in: after every Newton step's (and linear step's) Krylov solve, the step's true linear "
                             "residual (one product); it is flagged when it exceeds both FACTOR times the recurrence "
                             "the solve reported and the requested tolerance; the Newton rows then carry the true "
                             "residual GMRES ended on as inner_rel, with inner_recurrence and inner_step_true beside "
                             "it.  The step is the solve's own (--true-residual-redo solves a flagged step again).  0 "
                             "(default): off")
    parser.add_argument("--camg-warp-fill", action="store_true",
                        help="opt-in (2026-10-14, Warp backend W6; with --camg-block-kernels): the sliced-ELL value "
                             "fills of every build in one Warp kernel each (block_kernels_warp.fill): the same values")
    parser.add_argument("--camg-sell-policy", default=None, metavar="KEY=V,...",
                        help="opt-in (2026-10-14, Warp backend W2; with --camg-block-kernels): overrides of the "
                             "sliced-ELL layout policy (Pipeline.CoupledNewton.block_kernels.POLICY: programs, lanes, "
                             "c_min, c_max, kb_full, num_warps), e.g. programs=1024,c_min=1 for one-row slices with "
                             "32 slots per iteration on the coarse levels.  Sums in another order")
    parser.add_argument("--krylov-fused-orth", action="store_true",
                        help="opt-in (2026-10-14, Warp backend W2): GMRES's DGKS-CGS2 orthogonalisation as fused device "
                             "kernels (SparseSolver.gmres_warp: inner products + norm in one pass, combination + norm "
                             "in one pass, the re-orthogonalisation test on the device, one host read-back per "
                             "iteration instead of four).  Rounding-level differences")
    parser.add_argument("--krylov-best-cycle-return", action="store_true",
                        help="opt-in: every GMRES solve returns its cycle end with the smallest true residual when its "
                             "last one is ten times above it (a cycle whose Arnoldi vectors lost their relation can end "
                             "far above its start), and a cycle that breaks down above the tolerance is followed by a "
                             "fresh cycle (SparseSolver.gmres best_cycle_return); bit for bit the plain solve otherwise")
    parser.add_argument("--krylov-best-cycle-gain", type=float, default=None, metavar="G",
                        help="variant of --krylov-best-cycle-return: the best cycle end is returned only when it lies "
                             "below G times the solve's start residual (a best end that reduced nothing, as the "
                             "primary's probe at a noise floor, is not returned; the last end is); default 0.5 (the gmres "
                             "default), 0 returns the best end at any level")
    parser.add_argument("--true-residual-redo", action="store_true",
                        help="with --true-residual-check: a step the check flags is solved again with every coarsest "
                             "level by its LU and the V-cycles eager, and the step with the smaller true residual is "
                             "taken (both solves counted); default: the check flags and logs only")
    parser.add_argument("--krylov-device-loop", action="store_true",
                        help="opt-in (2026-10-14, Warp backend W2): GMRES runs each cycle's Arnoldi loop on the device "
                             "(SparseSolver.gmres_warp.DeviceCycle): preconditioner, operator, fused Gram-Schmidt, "
                             "Hessenberg column, Givens rotations, convergence test and least squares, the step "
                             "captured as a CUDA graph and iterated by a conditional WHILE node; the host reads "
                             "counters and history once per cycle.  Rounding-level differences")
    parser.add_argument("--krylov-orth-impl", choices=("triton", "warp"), default=None,
                        help="opt-in (2026-10-15, Warp backend W10b-A): the implementation of the fused Gram-Schmidt "
                             "kernels of --krylov-fused-orth / --krylov-device-loop (SparseSolver.gmres_warp.BACKEND): "
                             "'triton' (the default, the W2 kernels) or 'warp' (the NVIDIA Warp port: loaded from the "
                             "warm Warp kernel cache, no Triton import, no Triton first-use cost in the first Newton "
                             "step).  Same arithmetic, fixed-order sums: rounding-level differences")
    mgpu = parser.add_argument_group(
        "multi-GPU (opt-in, docs/development/multi-gpu.md): the case is cut into --nproc parts with --halo layers "
        "of halo cells and solved by Parallel.newton (partitioned Newton-PTC, distributed FGMRES, restricted "
        "additive Schwarz) with one process per GPU; without --nproc nothing below is read")
    mgpu.add_argument("--nproc", type=int, default=0,
                      help="number of parts = processes = GPUs (0: the single-GPU solve, unchanged)")
    mgpu.add_argument("--partition", default="metis",
                      help="part of each cell: 'procs' (the H5 dataset cell|proc, the OpenFOAM decomposition), "
                           "'metis' (pymetis), 'rcb' (coordinate bisection) or a .npy array")
    mgpu.add_argument("--partition-dir", default=None,
                      help="reuse / write the partition here (default <case>/Logger/partition/<n>_<method>_h<halo>)")
    mgpu.add_argument("--halo", type=int, default=3,
                      help="halo layers; must cover the residual's value dependency (3 measured, Parallel.depth)")
    mgpu.add_argument("--gpus", default=None,
                      help="cards of the ranks, comma-separated indices or UUIDs (default 0..nproc-1)")
    mgpu.add_argument("--dist-backend", default=None, choices=("nccl", "gloo"),
                      help="nccl (default on GPUs) or gloo through host memory when NCCL cannot start")
    mgpu.add_argument("--part-precond", default="camg", choices=("camg", "simple", "dense", "none"),
                      help="the local preconditioner inside the restricted additive Schwarz")
    mgpu.add_argument("--part-coarse", action="store_true",
                      help="two-level Schwarz: a coarse correction with one constant per part per field")
    mgpu.add_argument("--part-coarse-agg", default=None, metavar="M[:p|all]",
                      help="aggregation coarse space: M geometric aggregates per part on p_rgh or all fields")
    mgpu.add_argument("--pt-startup-steps", type=int, default=None,
                      help="partitioned pseudo-time start-up rule: this many accepted steps with the two below")
    mgpu.add_argument("--pt-startup-reject", type=float, default=None)
    mgpu.add_argument("--pt-startup-cfl-min", type=float, default=None)
    mgpu.add_argument("--qoi-log-every", type=int, default=0, metavar="N",
                      help="partitioned pseudo-time: the dctwin quantities and their comparison with the reference "
                           "every N accepted steps, as JSON lines in the run directory (qoi_log.jsonl)")
    return parser


def restart_by_memory(restart: int, restart_max: int, gib: float, n_unknowns: int) -> int:
    """``--restart-gib``: ``max(restart, min(restart_max, floor(gib GiB / (8 n)) - 1))`` -- the longest
    cycle up to ``restart_max`` whose full basis (cycle + 1 vectors of ``n`` float64) fits in ``gib`` GiB, never below
    ``restart``."""
    if gib <= 0:
        return int(restart)
    fit = int(gib * 2 ** 30 // (8 * max(int(n_unknowns), 1))) - 1
    return max(int(restart), min(int(restart_max), fit))


def l1_rescue_kw_of(args, kw: dict) -> dict | None:
    """The keywords of the ``--camg-rescue-l1`` stagnation rescue's cycle for a cycle built from ``kw``: None without
    the rescue; with ``--camg-rescue-follows-l1-rule`` None under the operator-decided l1 rule
    (``block_amg.l1_rescue_kw``: the cycle runs the selection's plan, so no rescue switches a level the selection
    refused); otherwise ``kw`` with l1 on every level."""
    from Pipeline.CoupledNewton import block_amg
    if not int(getattr(args, "camg_rescue_l1", 0) or 0):
        return None
    if getattr(args, "camg_rescue_follows_l1_rule", False):
        return block_amg.l1_rescue_kw(kw)
    return {**kw, "l1": True}


def measured_rescue_kw(args, amg, blocks, gauge_field) -> dict:
    """``--camg-rescue-measured``: the ``measure`` / ``hand_back`` keywords of a rescue class
    (``block_amg._MeasuredRescue``) for the cycle ``amg``; ``blocks`` is a zero-argument context manager yielding the
    build's matrix on the device with its values.  Empty without the flag (the rescue as before)."""
    if not getattr(args, "camg_rescue_measured", False):
        return {}
    from Pipeline.CoupledNewton import block_amg

    def measure():
        with blocks() as A:
            return block_amg.l1_rescue_measurement(amg, A, gauge_field)
    return {"measure": measure, "hand_back": True}


def rescue_rule_kwargs(args, l1_primary: bool = False, maxit_lifted: bool = False) -> dict:
    """``block_amg.StagnationRescue`` keywords of ``--camg-rescue-rule`` / ``--camg-rescue-maxit`` (II-4 job 2): none for
    the defaults (the constructor call is the one it always was); for 'rate' the rule and the GMRES iterations left
    after the probe, ``(max_cycles - 1) x (restart - deflate)`` (a GMRES-DR cycle after the first spends restart - k
    new iterations); ``maxit`` unless the primary itself is the l1 cycle (``l1_primary``: --camg-l1-start before the
    first full step, where the rescue duplicates the primary) or ``maxit_lifted`` (II-4 job 5: after a
    --forcing-cap-fallback the rescue runs uncapped, the production path)."""
    out = {}
    rule = getattr(args, "camg_rescue_rule", "factor")
    if rule != "factor":
        restart, deflate = int(args.restart), int(getattr(args, "deflate", 0) or 0)
        out.update(rule=rule, left=max(1, (int(args.max_cycles) - 1) * max(1, restart - deflate)))
    maxit = getattr(args, "camg_rescue_maxit", None)
    if maxit is not None and not l1_primary and not maxit_lifted:
        out["maxit"] = int(maxit)
    return out


def refuse_misused_m3_flags(args) -> None:
    """The V-cycle flags are refused, not ignored, on a run they cannot act on."""
    if getattr(args, "camg_block_kernels", False) and not getattr(args, "camg_fast_kernels", False):
        raise ValueError("--camg-block-kernels replaces the fast kernels' scalar copies: it needs --camg-fast-kernels")
    if getattr(args, "camg_block_values", "fp64") != "fp64" and not getattr(args, "camg_block_kernels", False):
        raise ValueError("--camg-block-values sets the storage of the block kernels: it needs --camg-block-kernels")
    if getattr(args, "camg_block_w", False) and not getattr(args, "camg_block_kernels", False):
        raise ValueError("--camg-block-w puts the learned W on the block kernels: it needs --camg-block-kernels")


def refuse_misused_m2_flags(args) -> None:
    """The AMG-setup flags are refused, not ignored, on a run they cannot act on."""
    spec = getattr(args, "learned_action", None) or ""
    camg = spec.startswith(("camg", "camgnet:"))
    structural = bool(getattr(args, "camg_structural_plan", False))
    for flag, on in (("--camg-structural-plan", structural),
                     ("--camg-gpu-setup", getattr(args, "camg_gpu_setup", False)),
                     ("--camg-fused-numeric", getattr(args, "camg_fused_numeric", False))):
        if on and not camg:
            raise ValueError(f"{flag} acts on the coupled AMG only (--learned-action camg* or camgnet:<model>)")
    if getattr(args, "camg_plan_cache", None) and not structural and not spec.startswith("camgcfg:"):
        raise ValueError("--camg-plan-cache stores structural plan decisions: it needs --camg-structural-plan "
                         "(or --learned-action camgcfg:, whose structural choices use it)")
    if structural:
        if not getattr(args, "camg_reuse_plan", False):
            raise ValueError("--camg-structural-plan keeps one plan for the run through the plan cache of "
                             "--camg-reuse-plan: it needs --camg-reuse-plan")
        if getattr(args, "fixed_jacobian_pattern", None) not in ("structural", "blocks"):
            raise ValueError("--camg-structural-plan needs a block pattern fixed for the run: "
                             "--fixed-jacobian-pattern structural or blocks")
        if getattr(args, "camg_plan_refresh", 0):
            raise ValueError("--camg-plan-refresh rebuilds value-based plans on a schedule; a structural plan "
                             "(--camg-structural-plan) does not depend on values")


def refuse_misused_camgcfg_flags(args) -> None:
    """``--learned-action camgcfg:<target>`` sets the hierarchy (aggregation per level, strength,
    transfers, depth), the cycle and the per-row stabilisation itself, so the flags that would set them too are
    refused, not silently overridden; ``--camg-config-every rung`` needs a trained ranking (``.pt``)."""
    spec = getattr(args, "learned_action", None) or ""
    if not spec.startswith("camgcfg:"):
        if getattr(args, "camg_config_every", "first") != "first":
            raise ValueError("--camg-config-every acts on --learned-action camgcfg:<model.pt> only")
        return
    target = spec.split(":", 1)[1]
    paths = target.split(",")
    for p in paths:
        if not p.endswith((".pt", ".json")) or not Path(p).is_file():
            raise ValueError(f"--learned-action camgcfg:<point.json | model.pt[,model.pt...]>: no such file {p!r}")
    if len(paths) > 1 and (any(p.endswith(".json") for p in paths)
                           or getattr(args, "camg_config_policy", "library") != "ensemble"):
        raise ValueError("several rankings in camgcfg: need --camg-config-policy ensemble (and .pt files only)")
    for flag, on in (("--camg-aggregation", getattr(args, "camg_aggregation", "standard") != "standard"),
                     ("--camg-structural-plan", getattr(args, "camg_structural_plan", False)),
                     ("--camg-stab-rule", getattr(args, "camg_stab_rule", None)),
                     ("--camg-lam", getattr(args, "camg_lam", None)),
                     ("--camg-l1", getattr(args, "camg_l1", False))):
        if on:
            raise ValueError(f"{flag}: --learned-action camgcfg: sets the hierarchy, cycle and stabilisation itself")
    if getattr(args, "camg_plan_cache", None) and not getattr(args, "camg_reuse_plan", False):
        raise ValueError("--camg-plan-cache with camgcfg: needs --camg-reuse-plan (the plan cache of the run)")
    if getattr(args, "camg_config_every", "first") != "first" and not target.endswith(".pt"):
        raise ValueError("--camg-config-every rung: a fixed point (JSON) is not chosen again")


def _coarse_by_lu_redo():
    """``--true-residual-check``'s redo context: every coupled AMG's coarsest level by its LU, V-cycles eager."""
    from Pipeline.CoupledNewton import block_amg
    return block_amg.coarse_by_lu


def refuse_misused_m5_flags(args) -> None:
    """The memory flags are refused, not ignored, on a run they cannot act on.

    ``--fixed-jacobian-pattern blocks`` assembles J only as the classical coupled AMG's 5 x 5 blocks
    (``jacobian.BlockJacobian``): the factory must be a classical ``camg*`` with ``--camg-fast-kernels`` (its fine-level
    copy is the Krylov operator of an assembling step) and ``--camg-reuse-plan`` (structural blocks kept), in the JFNK
    mode with ``--operator auto`` or ``jvp``; whatever reads a scalar J or the F/G/D/C/R blocks is refused."""
    spec = args.learned_action or ""
    classical = spec.startswith("camg") and not spec.startswith("camgnet:")
    fast = bool(getattr(args, "camg_fast_kernels", False))
    if int(getattr(args, "krylov_basis_chunk", 0) or 0) < 0:
        raise ValueError(f"--krylov-basis-chunk must be >= 0, not {args.krylov_basis_chunk}")
    if int(getattr(args, "krylov_basis_chunk", 0) or 0) and getattr(args, "deflate", 0):
        raise ValueError("--krylov-basis-chunk is not combined with --deflate (GMRES-DR rewrites the basis as one "
                         "matrix)")
    if getattr(args, "camg_rescue_measured", False):
        if not int(getattr(args, "camg_rescue_l1", 0) or 0):
            raise ValueError("--camg-rescue-measured decides the l1 rescue: it needs --camg-rescue-l1")
        if getattr(args, "camg_rescue_follows_l1_rule", False):
            raise ValueError("--camg-rescue-measured and --camg-rescue-follows-l1-rule are two forms of the rescue: "
                             "give one")
    if getattr(args, "camg_rescue_hand_back_once", False) and not getattr(args, "camg_rescue_measured", False):
        raise ValueError("--camg-rescue-hand-back-once limits the measured rescue's hand-backs: it needs "
                         "--camg-rescue-measured")
    escalate = float(getattr(args, "camg_rescue_escalate", 0.0) or 0.0)
    if escalate:
        if not getattr(args, "camg_rescue_measured", False):
            raise ValueError("--camg-rescue-escalate escalates the measured rescue: it needs --camg-rescue-measured")
        if not 0.0 < escalate < 1.0:
            raise ValueError(f"--camg-rescue-escalate R needs 0 < R < 1, not {escalate:g}")
    if getattr(args, "camg_rescue_lean_reading", False) and not getattr(args, "camg_rescue_measured", False):
        raise ValueError("--camg-rescue-lean-reading slices the measured rescue's reading: it needs "
                         "--camg-rescue-measured")
    rescue_deflate = int(getattr(args, "camg_rescue_deflate", 0) or 0)
    if rescue_deflate:
        if rescue_deflate < 0 or rescue_deflate >= int(args.restart):
            raise ValueError(f"--camg-rescue-deflate {rescue_deflate}: give 0 < K < --restart ({args.restart})")
        if not int(getattr(args, "camg_rescue_l1", 0) or 0):
            raise ValueError("--camg-rescue-deflate acts on the restarts of a rescued solve: it needs --camg-rescue-l1")
        if getattr(args, "deflate", 0):
            raise ValueError("--camg-rescue-deflate (rescued solves) and --deflate (every solve) are not combined")
        if getattr(args, "krylov_device_loop", False):
            raise ValueError("--camg-rescue-deflate is not combined with --krylov-device-loop")
    # With --order1-switch and a camgnet form the memory flags act on the switch's
    # classical factory (--order1-classical) only -- main builds the learned factory without them (learned_args_of)
    m5_spec = (str(getattr(args, "order1_classical", "camg2w")) if int(getattr(args, "order1_switch", 0) or 0) > 0
               and spec.startswith("camgnet:") else spec)
    m5_classical = m5_spec.startswith("camg") and not m5_spec.startswith("camgnet:")
    for flag, on in (("--camg-lean-build", getattr(args, "camg_lean_build", False)),
                     ("--camg-drop-block-values", getattr(args, "camg_drop_block_values", False)),
                     ("--camg-int32-groups", getattr(args, "camg_int32_groups", False))):
        if on and not (m5_classical and fast):
            raise ValueError(f"{flag} acts on the classical coupled AMG's fast kernels: it needs --learned-action "
                             "camg* (not camgnet) and --camg-fast-kernels")
    if getattr(args, "fixed_jacobian_pattern", None) != "blocks":
        return
    refused = [reason for reason, given in (
        ("--learned-action other than a classical camg* (only the coupled AMG reads the block matrix)", not classical),
        ("a run without --camg-fast-kernels (the fine-level scalar copy is the Krylov operator)", not fast),
        ("a run without --camg-reuse-plan (the structural block pattern must be kept)",
         not getattr(args, "camg_reuse_plan", False)),
        ("--operator assembled / the default operator (a block assembly has no scalar J to multiply)",
         getattr(args, "operator", "assembled") not in ("auto", "jvp")),
        ("--ptc", getattr(args, "ptc", False)),
        ("--equilibrate", getattr(args, "equilibrate", "off") != "off"),
        ("--matrix-free", getattr(args, "matrix_free", False)),
        ("--dump-linear-systems / --cell-cond-dump / --face-adj-dump",
         bool(getattr(args, "dump_linear_systems", None) or getattr(args, "cell_cond_dump", None)
              or getattr(args, "face_adj_dump", None))),
        ("--patch-schwarz / --probe-policy", bool(getattr(args, "patch_schwarz", None)
                                                  or getattr(args, "probe_policy", None))),
        ("--nonlinear-elimination", bool(getattr(args, "nonlinear_elimination", None)))) if given]
    if refused:
        raise ValueError("--fixed-jacobian-pattern blocks (M5 block assembly) refuses " + "; ".join(refused))


def constant_of(theta_pde, key: str) -> float:
    value = theta_pde[key]
    if isinstance(value, dict):
        if value.get("value_type") != "constant":
            raise ValueError(
                f"[theta_pde] {key} is {value.get('value_type')!r}; this steady "
                "solver takes one constant value for the whole domain")
        value = value["val"]
    return float(value)


def load_state(path, device, dtype) -> torch.Tensor:
    """``--state``: a bare saved iterate, or a dict that carries it under ``'x'``.

    The bare tensor (``x_rung<k>.pt``, ``x_final.pt``, ``--save-final-state``) is read by the same
    call, bit for bit.  The dict form is what ``run_phase5.py`` writes as its
    single-slot ``x_checkpoint.pt`` (``{'x', 'stage', 'iteration'}``) and what the RANS r0
    checkpoints under ``.work/.../r0-ckpt/`` are; the end-to-end C7 lanes start from one
    (RANS C7 plan, C7a).  ``weights_only`` still holds: such a dict is tensors, strings and ints.
    """
    blob = torch.load(path, map_location=device, weights_only=True)
    if isinstance(blob, dict):
        if "x" not in blob:
            raise ValueError(f"--state {path} is a dict without the key 'x' (keys: {sorted(blob)})")
        blob = blob["x"].reshape(-1)
    return blob.to(dtype)


def lu_factorisation_matrix(A, row: int, pin: bool):
    """The CSC matrix ``--learned-action lu`` factorises: ``A``, or ``A`` with the pressure level pinned.

    The pin (``max|diag|`` added to diagonal ``row``, the first pressure unknown) is what a case
    with a FREE pressure level needs, because its assembled Jacobian is exactly singular and
    SuperLU refuses it; the Krylov operator is untouched and every application is gauged.  On a
    case whose outlet pins the level the same pin made the "exact" LU a rank-one perturbation of
    ``J``, so GMRES needed a few iterations per Newton step to undo it and an LU path could not
    replay ``run_phase5 --camg lu``.  ``pin`` is decided by the
    caller (``--lu-pin``).
    """
    if not pin:
        return A
    pinned = A.tolil()
    pinned[row, row] += float(abs(A.diagonal()).max())
    return pinned.tocsc()


def normalise_learned_action(args) -> None:
    """``--learned-action chillon-pg:<ckpt>``: the published learned AMG smoother of Chillon et al. at our learned
    smoother's position in our cycle.  The checkpoint (that network's weights, beside a ``train.log`` whose args
    record ``model: chillon`` and our cycle's settings, as ``sg_order1.py`` writes it) then runs through the
    ``camgnet:`` path: ``role_network_kwargs`` builds the network with it, every camgnet flag applies."""
    spec = str(getattr(args, "learned_action", None) or "")
    if spec.startswith("chillon-pg:"):
        path = spec.split(":", 1)[1]
        if read_train_args(Path(path)).get("model") != "chillon":
            raise ValueError(f"--learned-action {spec}: the train.log beside the checkpoint does not record "
                             "model 'chillon'")
        args.learned_action = "camgnet:" + path


def read_train_args(model_path) -> dict:
    """The ``args {...}`` record of a LearnedCoupledAMG run's ``train.log`` beside ``model_path``.

    The LAST such line wins (train.log is opened for append, so a resumed run writes a second
    one); a missing train.log raises.
    """
    import ast
    train_args = {}
    for line in (Path(model_path).parent / "train.log").read_text().splitlines():
        if line.startswith("args "):
            train_args = ast.literal_eval(line[5:])
    return train_args


def trained_l1(value):
    """``train.py --l1`` as recorded in train.log -> the ``l1`` a plan takes (False, True or 'signed')."""
    if isinstance(value, str):
        value = value.strip()
        if value == "signed":
            return "signed"
        if value.lower() in ("", "0", "false", "off", "none"):
            return False
        if value.lower() in ("1", "true", "on"):
            return True
        raise ValueError(f"train.log records l1={value!r}; expected 0, 1 or signed")
    return bool(value)


def classical_camg_kw(spec: str, l1: bool) -> dict:
    """The coupled-AMG keywords of a ``--learned-action camg*`` spec: GS smoother, PG restriction (``-t``: R = P^T),
    ``camg2*`` 2+2 sweeps, ``camg2w`` damping 0.7 (the camgnet safeguard's classical arm as a solver of its own,
    run alone), ``camg1w`` one sweep each way at 0.7 (the cycle the order-1 network modulates, without it), ``l1``
    from ``--camg-l1``.  A ``camgnet:`` spec gives the base keywords the learned cycle starts from
    (the entry's camg_kw before the train.log settings).  ``camgcfg:<target>``
    gives the plan and cycle keywords of a point of ``amg_config`` -- the JSON point ``target``, or for a
    trained ranking (``.pt``) the deep cycle until its first choice (``NNmodels.amg_config_net.ConfigChoice``)."""
    if spec.startswith("camgcfg:"):
        from Pipeline.CoupledNewton import amg_config
        target = spec.split(":", 1)[1]
        if target.endswith(".pt"):
            return amg_config.solver_kwargs(amg_config.CLASSICAL["deep"])
        with open(target) as fh:
            return amg_config.solver_kwargs(json.load(fh))
    kw = {"smoother": "gs", "restriction": "transpose" if spec.endswith("-t") else "pg"}
    if l1:
        kw["l1"] = True
    if spec.startswith("camg2"):
        kw.update(pre=2, post=2)
    if spec.startswith("camg2w"):
        kw.update(gs_omega=0.7)
    if spec.startswith("camg1w"):                     # one sweep each way at 0.7: the order-1 network's base cycle
        kw.update(pre=1, post=1, gs_omega=0.7)
    if spec in ("camgua12", "camgua22", "camgua22a"):
        # The AmgX-style deep cycle (LearnedCoarseSpace README) -- unsmoothed aggregation
        # (R = P^T, piecewise constant), multicolour GS 1+2 / 2+2 at 0.75, down to <= 500 cells; with
        # --camg-gpu-setup --camg-aggregation pairwise1 its aggregates follow the value strength (AmgX SIZE_2).
        # "camgua22a" (R1d): l1 on the levels whose damped block Jacobi amplifies (block_amg adaptive_l1 = 2)
        kw.update(smooth_prolongation=False, gs_omega=0.75, pre=1 if spec == "camgua12" else 2, post=2,
                  max_levels=40, coarse_max=500)
        if spec == "camgua22a":
            kw["adaptive_l1"] = 2.0
    return kw


def camg_stab_rule_modulator(spec: str):
    """``block_amg.SinvStabilizer`` of a ``--camg-stab-rule RULE:VALUE`` spec, e.g.
    ``uniform:0.02``; RULE is one of ``block_amg.STAB_RULES``."""
    from Pipeline.CoupledNewton import block_amg
    rule, sep, value = str(spec).partition(":")
    if not sep or rule not in block_amg.STAB_RULES:
        raise ValueError(f"--camg-stab-rule {spec!r}: RULE:VALUE with RULE one of {block_amg.STAB_RULES}")
    return block_amg.SinvStabilizer(rule, float(value))


def deep_alternate_args(args, spec: str):
    """The arguments of an ``--order1-alternate-deep`` form: ``args`` (the switch's learned
    arguments) on the deep value-paired cycle -- ``camgua22`` with device value pairing, no structural plan or plan
    cache (value-based plans) -- stabilised by the classical rule ``spec`` (``RULE:VALUE``, e.g. ``uniform:0.02``) or
    by the lam model at the path ``spec``."""
    import copy
    out = copy.copy(args)
    out.learned_action = "camgua22"
    out.camg_gpu_setup, out.camg_aggregation = True, "pairwise1"
    out.camg_structural_plan, out.camg_plan_cache = False, None
    if ":" in str(spec) and not str(spec).endswith(".pt"):
        camg_stab_rule_modulator(spec)                       # refused here if malformed
        out.camg_stab_rule, out.camg_lam = str(spec), None
        for name, value in (("camg_lam_fast", False), ("camg_lam_reuse", 1), ("camg_lam_reuse_rung", False)):
            if hasattr(out, name):                           # the lam network's options leave with the network
                setattr(out, name, value)
    else:
        out.camg_stab_rule, out.camg_lam = None, str(spec)
    # the form itself switches nothing (as order1_switch.classical_args_of): every switch option off, or the refusals
    # of its own arguments stop it (e.g. --order1-switch-rel / --order1-keep left on a switch-free form)
    for name, value in (("order1_switch", 0), ("order1_switch_rel", 0.0), ("order1_keep", "any"),
                        ("order1_switch_cap", False), ("order1_alternate", None), ("order1_alternate_deep", None),
                        ("order1_deep_first", False), ("order1_alternate_config", None), ("order1_config_first", False)):
        if hasattr(out, name):
            setattr(out, name, value)
    return out


def config_alternate_args(args, model: str):
    """The arguments of an ``--order1-alternate-config`` form: ``args`` (the switch's learned
    arguments) on the AMG configuration the ranking ``model`` chooses from the Jacobian at the switch
    (``--learned-action camgcfg:model``, library policy, value-based points only, device setup), with no setting of its
    own the configuration would duplicate (aggregation, structural plan, plan cache, stabilisation, l1) and every switch
    option off (as ``deep_alternate_args``)."""
    import copy
    out = copy.copy(args)
    out.learned_action = f"camgcfg:{model}"
    out.camg_config_policy, out.camg_config_values_only, out.camg_config_every = "library", True, "first"
    out.camg_gpu_setup, out.camg_aggregation = True, "standard"
    out.camg_structural_plan, out.camg_plan_cache = False, None
    out.camg_stab_rule, out.camg_lam, out.camg_l1 = None, None, False
    for name, value in (("camg_lam_fast", False), ("camg_lam_reuse", 1), ("camg_lam_reuse_rung", False)):
        if hasattr(out, name):
            setattr(out, name, value)
    for name, value in (("order1_switch", 0), ("order1_switch_rel", 0.0), ("order1_keep", "any"),
                        ("order1_switch_cap", False), ("order1_alternate", None), ("order1_alternate_deep", None),
                        ("order1_deep_first", False), ("order1_alternate_config", None), ("order1_config_first", False)):
        if hasattr(out, name):
            setattr(out, name, value)
    return out


def camgnet_train_settings(train_args: dict, camg_l1: bool):
    """What a camgnet checkpoint needs from its train.log beyond the network sizes.

    Returns ``(net_kw, cycle_kw, note)``.  ``net_kw`` goes to the ``CoupledAMGNet`` constructor
    (``jacobi_omega``, ``features``, ``dinv_eps`` / ``dinv_guard``, ``onehop_form``, ``w_diagonal``),
    ``cycle_kw`` into the learned cycle's ``CoupledAMG`` keywords (``l1``, ``jacobi_omega``: the
    stabilize reference must be the same damped l1 Jacobi the network's W reduces to at Theta = 0;
    ``diag_eps``: the singular-value floor of the smoother diagonal the model was trained with, which
    the network refuses to run without), and ``note`` is a warning to log, or None.  Every key is set
    only when the train.log records it, so a model trained before these flags existed is built and run
    with the defaults -- including on an l1 cycle (``--camg-l1``): its W stays on the raw diagonal
    (``w_diagonal`` "raw", the constructor default), as recorded.  A train.log that records ``l1``
    (train.py since C7) builds W on the smoother diagonal (``w_diagonal`` "smoother").

    A model trained with l1 builds its W on the l1 diagonal; running its learned cycle without l1
    would hand the network a near-singular raw diagonal it never saw (cond up to 8.2e7 on the SST
    plate), so the trained variant is FORCED onto the learned cycle and the override is logged.
    The classical and rescue arms keep following ``--camg-l1``.
    """
    net_kw, cycle_kw, note = {}, {}, None
    if "jacobi_omega" in train_args:
        net_kw["jacobi_omega"] = cycle_kw["jacobi_omega"] = float(train_args["jacobi_omega"])
    if "features" in train_args:
        net_kw["features"] = str(train_args["features"])
    for key, cast in (("dinv_eps", float), ("dinv_guard", str), ("onehop_form", str), ("theta_form", str),
                      ("lam_form", str)):
        if key in train_args:
            net_kw[key] = cast(train_args[key])
    if net_kw.get("dinv_eps", 0.0) > 0:
        cycle_kw["diag_eps"] = net_kw["dinv_eps"]
    if "w_diagonal" in train_args or "l1" in train_args:
        net_kw["w_diagonal"] = str(train_args.get("w_diagonal", "smoother"))
    if "lam" in str(train_args.get("heads", "")).split(","):
        # The lam head's base rule and bound (its zero output is that classical rule)
        from Pipeline.CoupledNewton.block_amg import STAB_FIELDS
        rule = train_args.get("lam_rule", "none")
        net_kw.update(lam_rule=None if rule in (None, "none") else str(rule),
                      lam_value=float(train_args.get("lam_value", 0.0)),
                      lam_fields=STAB_FIELDS[str(train_args.get("lam_fields", "all"))],
                      lam_eps=float(train_args.get("lam_eps", 0.0)), lam_scale=float(train_args.get("lam_scale", 3.0)))
    if "l1" in train_args:
        l1 = trained_l1(train_args["l1"])
        if l1:
            cycle_kw["l1"] = l1
            if not (l1 is True and camg_l1):
                note = (f"camgnet: the model was trained with l1={l1!r}, so its learned cycle runs with "
                        f"l1={l1!r} although --camg-l1 is {'set' if camg_l1 else 'not set'} (the classical and "
                        f"rescue arms follow --camg-l1)")
        elif camg_l1:
            note = ("camgnet: the model was trained WITHOUT l1 but --camg-l1 is set: its learned cycle smooths "
                    "on the l1 diagonal it never saw in training")
    return net_kw, cycle_kw, note


def _ptc_theta_from_shift(shift, steady_before, velocity: int, pressure: int):
    """The PTC theta of an assembly, recovered from its armed shift ``theta * base``, or None.

    ``newton.solve_newton`` arms ``ptc_d = theta * base`` with ``base = |steady diag|`` of the
    PREVIOUS assembly (``_ptc_base``, scaling 'diag'), zero on continuity; both entries run that
    scaling.  ``steady_before`` is that diagonal as the dump hook saw it, so ``ptc_d / base`` is
    theta to one rounding on every row with ``base > 0``.  A spread above 1e-9 (another scaling,
    or no matching previous assembly) returns None rather than a wrong number.
    """
    if steady_before is None:
        return None
    base = steady_before.abs()
    base[velocity:pressure] = 0.0
    rows = base > 0
    if not bool(rows.any()):
        return None
    ratio = shift.reshape(-1)[rows] / base[rows]
    high, low = float(ratio.max()), float(ratio.min())
    if not math.isfinite(high) or not math.isfinite(low) or high - low > 1e-9 * abs(high):
        return None
    return float(ratio.median())


def install_linear_system_dump(solver, directory, args, *, refuse_existing: bool = False) -> None:
    """Record every assembled Jacobian for offline preconditioner benchmarks.

    Wraps ``solver.assemble`` on this instance only: each call writes
    ``step<k>.npz`` with the field-blocked CSR ``J``, the state ``x`` and the
    residual ``F(x)`` (the Newton right-hand side is ``-F``).  ``meta.npz`` holds
    what a matrix-only benchmark cannot recover: cell centres and volumes, the
    field names and widths, and the coefficient mode.  Values are copied to the
    host after the assembly returns, so the solve sees the same blocks.

    Metadata written next to the arrays above,
    which are unchanged:

    * every step: ``shifted`` -- whether this ``J`` carries the PTC diagonal (it is then
      ``J_steady + diag(ptc_shift)``, while ``F`` is still the steady residual because
      ``x`` is the shift's reference); ``ptc_shift`` -- that diagonal, on shifted steps only;
      ``ptc_theta`` -- 0 on an unshifted step, the recovered theta on a shifted one
      (``_ptc_theta_from_shift``; left out when it cannot be recovered); ``log_fields`` --
      the unknowns ``x`` holds as logarithms at this step (the turbulence model's
      ``LOG_TRANSFORMED_BC_FIELDS``, plus ``k`` under ``run_phase5 --log-k``).
    * ``meta.npz``: ``field_widths`` (per entry of ``field_names``) and ``log_fields``
      (the model's, at install time).

    The theta recovery reads the diagonal of EVERY assembly, dumped or skipped by
    ``dump_every``, which costs one O(nnz) pass (and a CSR copy when J is not CSR) and keeps
    one vector; the blocks handed to the solve are never touched.  Further,
    it does so only when PTC can arm a shift (``args.ptc``; an args object without it counts
    as on), so a run without ``--ptc`` pays nothing on its skipped assemblies.  The
    per-assembly seconds a dump run records include the hook.  ``refuse_existing`` (opt-in) raises before
    anything is written when ``directory`` already holds ``step*.npz``: the step counter
    restarts at 000 in each process, so a second run pointed at a used directory would
    overwrite the first one's systems (C7 plan, critique 1).
    """
    import numpy as np
    from Pipeline.CoupledNewton.preconditioner import _csr_diagonal
    out = Path(directory)
    if refuse_existing and out.is_dir() and any(out.glob("step*.npz")):
        raise FileExistsError(
            f"linear-system dump directory {out.resolve()} already holds step*.npz; each run "
            "restarts at step000 and would overwrite them -- use a fresh directory")
    out.mkdir(parents=True, exist_ok=True)
    inner = solver.assemble
    counter = {"k": 0}
    base = solver.solver
    names = [str(name) for name in solver.field_names()]
    model_logs = tuple(getattr(type(getattr(base, "turbulence", None)), "LOG_TRANSFORMED_BC_FIELDS", ()))
    meta_extra = {"log_fields": np.array([n for n in names if n in model_logs], dtype=str)}
    widths = [int(entry[1]) for entry in getattr(solver, "unknowns", ())]
    if len(widths) == len(names):
        meta_extra["field_widths"] = np.array(widths, dtype=np.int64)
    np.savez(out / "meta.npz",
             cell_pos=base.cpd_cell_pos[solver.interior].detach().cpu().numpy(),
             cell_volume=base.cell_volume.reshape(-1).detach().cpu().numpy(),
             field_names=np.array(solver.field_names()), n_cell=solver.n_cell,
             num_fields=solver.num_fields, velocity=solver.velocity,
             pressure=solver.pressure, dim=int(getattr(base, "dim", 3)),
             coefficient=str(args.coefficient), dataset_dir=os.path.abspath(args.dataset_dir),
             **meta_extra)
    # Steady diagonal of the previous assembly: the base newton.py scales the next PTC shift by.
    track = {"steady": None}
    need_diagonal = bool(getattr(args, "ptc", True))      # only PTC arms shifts whose theta needs the diagonal

    def assemble_and_dump(x, *a, **k):
        shift = getattr(solver, "_ptc_shift", None)
        blocks = inner(x, *a, **k)
        counter["k"] += 1
        ptc = {"shifted": np.bool_(shift is not None)}
        if shift is None:
            ptc["ptc_theta"] = np.float64(0.0)
        if need_diagonal or shift is not None:
            J = blocks["J"]
            diagonal = _csr_diagonal(J if J.layout == torch.sparse_csr else J.to_sparse_csr()).detach()
            if shift is None:
                steady = diagonal
            else:
                ptc_d = shift[0].detach().reshape(-1)
                theta = _ptc_theta_from_shift(ptc_d, track["steady"], solver.velocity, solver.pressure)
                if theta is not None:
                    ptc["ptc_theta"] = np.float64(theta)
                steady = diagonal - ptc_d
            track["steady"] = steady
        if (counter["k"] - 1) % max(int(getattr(args, "dump_every", 1)), 1):
            return blocks
        if shift is not None:
            ptc["ptc_shift"] = shift[0].detach().reshape(-1).cpu().numpy()
        logs = [n for n in names if n in model_logs]
        if getattr(solver, "log_k", None) is not None and "k" in names and "k" not in logs:
            logs.append("k")
        J = blocks["J"].to_sparse_csr()
        if getattr(args, "fixed_jacobian_pattern", None) == "structural":
            # The dumps feed gpu_bench, the fast-kernel checks and training corpora,
            # which build their AMG from the stored pattern; they get today's matrix, not its zeros.
            from Pipeline.CoupledNewton.jacobian import drop_explicit_zeros
            J = drop_explicit_zeros(J)
        F = solver.residual(x).detach()
        np.savez(out / f"step{counter['k'] - 1:03d}.npz",
                 data=J.values().detach().cpu().numpy(),
                 indices=J.col_indices().cpu().numpy(),
                 indptr=J.crow_indices().cpu().numpy(), shape=np.array(J.shape),
                 x=x.detach().cpu().numpy(), F=F.cpu().numpy(),
                 coefficient=str(k.get("coefficient", args.coefficient)),
                 log_fields=np.array([n for n in names if n in logs], dtype=str), **ptc)
        return blocks

    solver.assemble = assemble_and_dump


# Default recipe for laminar Boussinesq cases (--buoyant-recipe), with the flag each piece answers to.
BUOYANT_RECIPE = (("learned_action", "--learned-action", "camg"),
                  ("coefficient", "--coefficient", "implicit"),
                  ("reassemble_drift", "--reassemble-drift", 0.0),
                  ("nu_ramp", "--nu-ramp", "4,2"))
TURBULENCE_FIELDS = ("k", "omega", "epsilon", "nuTilda", "nut")


def apply_buoyant_recipe(args, case_solver, argv, logger) -> dict:
    """Switch a laminar Boussinesq case to the validated buoyant recipe, flag by flag.

    Measured: with the shipped frozen-coefficient Jacobian and
    the SIMPLE-shaped preconditioner, NaturalConvection Ra1e6 never certifies and the heated
    cylinder takes 600-870 s; with the true Jacobian and the coupled block AMG every buoyant case
    certifies (Ra1e6 in 40 s, heated cylinder 277-349 s).  Isothermal and turbulent cases are not
    touched.  A flag the user passed explicitly always wins."""
    if not args.buoyant_recipe:
        return {}
    names = [entry[0] for entry in getattr(case_solver, "COLLOCATED_UNKNOWNS", ())]
    if "T" not in names or any(n in names for n in TURBULENCE_FIELDS):
        return {}
    given = {a.split("=", 1)[0] for a in argv if a.startswith("--")}
    changed = {}
    for key, flag, value in BUOYANT_RECIPE:
        if flag in given:
            continue
        if key == "nu_ramp" and (args.state is not None or args.init != "zero"
                                 or getattr(args, "continuation", "ladder") == "adaptive"):
            continue
        setattr(args, key, value)
        changed[key] = value
    if changed:
        logger.info("BUOYANT-RECIPE: laminar Boussinesq case (%s) -> %s; pass a flag explicitly or "
                    "--no-buoyant-recipe to override", ", ".join(names), changed)
    return changed


# RANS recipe (--rans-recipe, opt-in), with the flag each piece answers to, in the order
# they are applied (the --camg-l1 piece reads the RESOLVED --learned-action).
RANS_RECIPE = (("learned_action", "--learned-action", "camg"),
               ("camg_l1", "--camg-l1", True),
               ("coefficient", "--coefficient", "implicit"),
               ("reassemble_drift", "--reassemble-drift", 0.0),
               ("ptc", "--ptc", True),
               ("operator", "--operator", "auto"),
               ("precond_rebuild", "--precond-rebuild", "rung+growth:2"))
LAMINAR_TURBULENCE = ("", "laminar", "none")


def case_turbulence(fvconfig) -> str:
    """The case's ``[solver] turbulence`` closure name (``laminar`` when the key is absent)."""
    return str((fvconfig.get("solver") or {}).get("turbulence", "laminar")).strip()


def apply_rans_recipe(args, fvconfig, argv) -> dict:
    """``--rans-recipe``: the RANS JFNK recipe, flag by flag; returns ``{attribute: value}`` of what it set.

    Why a recipe: ``--buoyant-recipe`` only acts on LAMINAR Boussinesq
    cases, and the RANS runs on record each spell the combination differently.  This is the combination the R1
    linear-solver work left standing on the turbulent Jacobians -- the coupled block AMG with l1 point-block
    smoothers (stable where GS 2+2 is not), the true Jacobian (implicit d_f), pseudo-transient
    continuation (k-omega rows have no Newton basin from a uniform field), and the JFNK mode (exact operator,
    preconditioner kept while it works).  Decided from the case's fvconfig BEFORE the case loads, because the
    JFNK policy is resolved there; a laminar case is refused rather than silently given a turbulence recipe.  A flag
    the caller passed explicitly always wins (the same rule as ``apply_buoyant_recipe``); ``--camg-l1`` is set only
    when the resolved ``--learned-action`` is a coupled AMG, so ``--learned-action lu`` keeps its exact LU.
    """
    if not getattr(args, "rans_recipe", False):
        return {}
    model = case_turbulence(fvconfig)
    if model.lower() in LAMINAR_TURBULENCE:
        raise ValueError(f"--rans-recipe acts on a case with transported turbulence; this case's [solver] "
                         f"turbulence is {model or 'unset'!r} (a laminar Boussinesq case has --buoyant-recipe)")
    given = {a.split("=", 1)[0] for a in argv if a.startswith("--")}
    changed = {}
    for key, flag, value in RANS_RECIPE:
        if flag in given:
            continue
        if key == "camg_l1" and not str(args.learned_action or "").startswith("camg"):
            continue
        setattr(args, key, value)
        changed[key] = value
    return changed


def refuse_misused_diagonal_race_flags(args) -> None:
    """``--camg-split-diagonal-race K``: K >= 0; on, it needs the learned split
    (``--camg-turb-split`` with ``--learned-action camgnet:``) and replaces ``--camg-split-learned-l1``."""
    k = int(getattr(args, "camg_split_diagonal_race", 0) or 0)
    if k < 0:
        raise ValueError(f"--camg-split-diagonal-race K needs K >= 0, not {k}")
    if k == 0:
        return
    if not getattr(args, "camg_turb_split", None):
        raise ValueError("--camg-split-diagonal-race acts on the learned mean-flow cycle of --camg-turb-split")
    if not (args.learned_action is not None and str(args.learned_action).startswith("camgnet:")):
        raise ValueError("--camg-split-diagonal-race needs --learned-action camgnet:<model>")
    if getattr(args, "camg_split_learned_l1", False):
        raise ValueError("--camg-split-diagonal-race chooses the diagonal itself: not with --camg-split-learned-l1")


def diagonal_race_split(state, split_of, model, kw, *, trained_l1, trained_diagonal, b, Jv, device, log=None,
                        gmres=None, race_now: bool = True):
    """``--camg-split-diagonal-race K``: the learned mean-flow split of one build.

    The run's first call builds the split with both diagonals -- as trained (``trained_diagonal``, plan l1
    ``trained_l1``: part 1's LS) and on the l1 smoother diagonal (W ``"smoother"``, l1 plan: R29's LS1) -- runs K
    GMRES iterations from zero on this linearisation's Newton right-hand side ``b`` with each and keeps, for the rest
    of the run (``state["pick"]``), the form with fewer iterations per decade of relative residual reduction (a tie,
    or neither moving, keeps l1, the robust form).  Later calls build the kept form only.  The race's builds and
    iterations are inside the run's timer.  ``state`` = ``{"k", "pick", "record"}``.  ``b`` may be a callable (evaluated
    only when racing).  ``race_now`` False (the race waits for the first unshifted build): the l1 form,
    no race, no pick yet."""
    def build(form):
        model.w_diagonal = "smoother" if form == "l1" else trained_diagonal
        return split_of({"modulator": model, **kw, "l1": True if form == "l1" else trained_l1})

    if state["pick"] is not None:
        return build(state["pick"])
    if not race_now:
        state["waited"] = int(state.get("waited", 0)) + 1      # builds on the l1 form before the race
        return build("l1")
    if gmres is None:
        from SparseSolver.gmres import gmres
    if callable(b):
        b = b()
    k, arms, record = int(state["k"]), {}, {}
    for form in ("raw", "l1"):
        M = build(form)
        if torch.device(device).type == "cuda":
            torch.cuda.synchronize(device)
        began = time.perf_counter()
        result = gmres(Jv, b, M=M, restart=k, max_cycles=1, rtol=0.0, atol=0.0)
        if torch.device(device).type == "cuda":
            torch.cuda.synchronize(device)
        rel = float((b - Jv(result.x)).norm() / b.norm())
        per_decade = (k / -math.log10(rel)) if 0.0 < rel < 1.0 else (0.0 if rel == 0.0 else math.inf)
        arms[form] = M
        record[form] = {"rel": rel, "its_per_decade": per_decade, "seconds": time.perf_counter() - began}
    pick = "raw" if record["raw"]["its_per_decade"] < record["l1"]["its_per_decade"] else "l1"
    model.w_diagonal = "smoother" if pick == "l1" else trained_diagonal
    state.update(pick=pick, record=record)
    if log is not None:
        log("--camg-split-diagonal-race %d: raw diagonal %.3e (%.1f its / decade, %.2f s), l1 diagonal %.3e (%.1f its "
            "/ decade, %.2f s), after %d l1 builds -> the %s form for the rest of the run", k, record["raw"]["rel"],
            record["raw"]["its_per_decade"], record["raw"]["seconds"], record["l1"]["rel"],
            record["l1"]["its_per_decade"], record["l1"]["seconds"], int(state.get("waited", 0)), pick)
    return arms[pick]


def refuse_misused_handback_flags(args) -> None:
    """``--order1-handback-below``: a positive max|r|, a learned camgnet form to hand
    back from, and not with ``--order1-switch`` (one order-1 rule per run)."""
    below = getattr(args, "order1_handback_below", None)
    if below is None:
        return
    if not (float(below) > 0.0 and math.isfinite(float(below))):
        raise ValueError(f"--order1-handback-below must be a positive max|r|, not {below}")
    if not (args.learned_action is not None and str(args.learned_action).startswith("camgnet:")):
        raise ValueError("--order1-handback-below needs a learned order-1 form (--learned-action camgnet:<model>)")
    if int(getattr(args, "order1_switch", 0) or 0) > 0:
        raise ValueError("--order1-handback-below and --order1-switch: one order-1 rule per run")


def refuse_a_misused_nl_accel(args) -> None:
    """``--nl-accel ngmres`` (opt-in): refused BEFORE the case loads where it cannot apply (``solve_newton``
    refuses the same combinations; here they fail in a second, not after the load).  With ``--nl-accel off`` (the
    default) nothing is read."""
    if getattr(args, "nl_accel", "off") == "off":
        return
    if int(getattr(args, "nl_accel_m", 0)) < 1:
        raise ValueError(f"--nl-accel-m must be >= 1, not {args.nl_accel_m}")
    refused = [flag for flag, given in (
        ("--ptc", getattr(args, "ptc", False)), ("--ptc-on-stall", getattr(args, "ptc_on_stall", 0)),
        ("--cold-start staged", getattr(args, "cold_start", "off") != "off"),
        ("--nonlinear-elimination", bool(getattr(args, "nonlinear_elimination", None)))) if given]
    if refused:
        raise ValueError("--nl-accel (nonlinear GMRES) refuses " + "; ".join(refused))


def refuse_a_misused_cold_start(args, fvconfig) -> None:
    """``--cold-start staged`` (opt-in): refused BEFORE the case loads where it cannot apply.

    The stages pin and release TRANSPORTED turbulence rows, so a laminar case has nothing to stage (refused rather
    than run as a plain solve under a misleading banner); the stage sequence, the bounds and the budgets are checked
    here too, so a typo fails in a second and not after the load.  With ``--cold-start off`` (the default) nothing is
    read."""
    if getattr(args, "cold_start", "off") == "off":
        return
    from Pipeline.CoupledNewton import coldstart
    model = case_turbulence(fvconfig)
    if model.lower() in LAMINAR_TURBULENCE:
        raise ValueError(f"--cold-start staged pins and releases transported turbulence rows; this case's [solver] "
                         f"turbulence is {model or 'unset'!r}")
    coldstart.parse_stages(args.cold_stages)
    if not (args.cold_stage_rtol > 0.0 and math.isfinite(args.cold_stage_rtol)):
        raise ValueError("--cold-stage-rtol must be positive")
    if args.cold_stage_maxit <= 0 or args.cold_restarts < 0:
        raise ValueError("--cold-stage-maxit must be positive and --cold-restarts nonnegative")
    if not 0.0 <= args.cold_k_fraction < 1.0:
        raise ValueError("--cold-k-fraction must lie in [0, 1)")
    if not (args.cold_log_cap >= 0.0 and math.isfinite(args.cold_log_cap)):
        raise ValueError("--cold-log-cap must be finite and >= 0")
    if not (args.cold_mean_weight >= 0.0 and math.isfinite(args.cold_mean_weight)):
        raise ValueError("--cold-mean-weight must be finite and >= 0")
    if args.cold_theta0 is not None and not (args.cold_theta0 > 0.0 and math.isfinite(args.cold_theta0)):
        raise ValueError("--cold-theta0 must be positive")
    if getattr(args, "cold_finish_below", None) is not None and not (
            args.cold_finish_below > 0.0 and math.isfinite(args.cold_finish_below)):
        raise ValueError("--cold-finish-below must be positive")
    if getattr(args, "cold_finish_trial", False) and getattr(args, "cold_finish_below", None) is None:
        raise ValueError("--cold-finish-trial acts on stage D: it needs --cold-finish-below")
    if getattr(args, "cold_finish_trial_always", False) and getattr(args, "cold_finish_below", None) is None:
        raise ValueError("--cold-finish-trial-always acts on stages D and D2: it needs --cold-finish-below")
    if getattr(args, "cold_k_floor", None) is not None and not (math.isfinite(args.cold_k_floor)
                                                                 and args.cold_k_floor > 0.0):
        raise ValueError(f"--cold-k-floor must be finite and > 0, got {args.cold_k_floor}")


# Continuation used by --auto-ramp: one rung at 8x the case viscosity, the
# schedule the cylinder launchers ship (experiments/Cylinder/CoupledNewton/README.md).
AUTO_RAMP_SCHEDULE = "8"

# --continuation adaptive: the path nu x ANCHOR_MULTIPLIER^(1 - s), s in [0, 1], starts at the cold-start multiplier
# of AUTO-RAMP and ends on the case's own viscosity (the diffusivity, where the case has one, takes the same factor).
ANCHOR_MULTIPLIER = float(AUTO_RAMP_SCHEDULE)


def path_multiplier(s: float) -> float:
    """The viscosity multiplier at path parameter ``s`` of ``--continuation adaptive``: 8 at s = 0, exactly 1 at
    s = 1, geometric in between (s is linear in log nu)."""
    return ANCHOR_MULTIPLIER ** (1.0 - float(s))


def refuse_a_misused_continuation(args) -> None:
    """``--continuation adaptive``: refused before the case loads with the options that set another continuation
    or another tolerance for its rungs, or a start that is not from rest.  ``ladder`` (the default) reads nothing."""
    if getattr(args, "continuation", "ladder") != "adaptive":
        return
    refused = [flag for flag, given in (
        ("--nu-ramp (a fixed ladder)", args.nu_ramp is not None),
        ("--rung-tol (the controller sets the rungs' tolerance)", args.rung_tol is not None),
        ("--beta-ramp", getattr(args, "beta_ramp", None) is not None),
        ("--cold-start staged", getattr(args, "cold_start", "off") != "off"),
        ("--operator-correction-mode frozen / auto",
         getattr(args, "operator_correction_mode", "newton") in ("frozen", "auto")),
        ("--init openfoam without --state (not a start from rest)",
         args.init == "openfoam" and args.state is None)) if given]
    rest_ladders = [item.strip() for item in str(args.learned_init_fallback or "none").split("|")
                    if item.strip().startswith("rest:")]
    if rest_ladders:
        refused.append(f"--learned-init-fallback {', '.join(rest_ladders)} (a fixed ladder from rest; "
                       "'rest' alone runs the adaptive continuation)")
    if refused:
        raise ValueError("--continuation adaptive refuses " + "; ".join(refused))


def beta_ramp_multipliers(spec):
    """``--beta-ramp``: the buoyancy multipliers before the case's own beta (each in [0, 1)), or None."""
    if spec is None or str(spec).strip() == "":
        return None
    values = [float(piece) for piece in str(spec).split(",") if piece.strip()]
    if not values or any(not 0.0 <= value < 1.0 for value in values):
        raise ValueError(f"--beta-ramp multipliers must lie in [0, 1); got {values}")
    return values


def ramp_multipliers(spec) -> list:
    """The continuation schedule, always ending on the case's own viscosity.

    ``None`` gives ``[1.0]``, which is one solve of the case as declared.  The
    trailing 1.0 is appended here rather than parsed, so no spelling of the flag
    can leave the run reporting a converged residual for a viscosity the case
    does not have.
    """
    if spec is None:
        return [1.0]
    values = [float(piece) for piece in str(spec).split(",") if piece.strip()]
    if any(value <= 0.0 for value in values):
        raise ValueError(f"--nu-ramp multipliers must be positive; got {values}")
    schedule = values + [1.0]
    # A caller who writes the trailing 1 out (`--nu-ramp 4096,512,64,8,1`, which
    # is what the DataCenter launcher ships) would otherwise get SIX rungs, the last two
    # identical, so the physical viscosity was announced as "rung 5 of 6" and
    # then solved a second time from its own answer.
    deduped = schedule[:1]
    for value in schedule[1:]:
        if value != deduped[-1]:
            deduped.append(value)
    return deduped


class LearnedInitAbort(RuntimeError):
    """Raised from the Newton progress hook when ``--learned-init-abort`` gives up on the start."""


def parse_learned_init_abort(spec):
    """``"K,RHO,KMAX[,GROWTH]"`` -> ``{"k", "rho", "kmax", "growth"}`` (None for None)."""
    if spec is None:
        return None
    parts = [piece.strip() for piece in str(spec).split(",") if piece.strip()]
    if len(parts) not in (3, 4):
        raise ValueError(f"--learned-init-abort {spec!r}: give K,RHO,KMAX[,GROWTH]")
    k, rho, kmax = int(parts[0]), float(parts[1]), int(parts[2])
    growth = float(parts[3]) if len(parts) == 4 else 10.0
    if k < 1 or kmax < k or not (rho > 0) or not (growth > 1):
        raise ValueError(f"--learned-init-abort {spec!r}: need 1 <= K <= KMAX, RHO > 0, GROWTH > 1")
    return {"k": k, "rho": rho, "kmax": kmax, "growth": growth}


def parse_learned_init_fallback(spec) -> list:
    """``"ramp:M1,M2|rest:M1|ptc|none"`` -> ``[(kind, multipliers or None), ...]`` ('none' alone: ``[]``)."""
    out = []
    for item in [piece.strip() for piece in str(spec or "none").split("|") if piece.strip()]:
        kind, _, value = item.partition(":")
        if kind == "none" and not value:
            continue
        if kind not in ("ramp", "rest", "ptc"):
            raise ValueError(f"--learned-init-fallback {spec!r}: unknown kind {kind!r} (ramp, rest, ptc, none)")
        if kind == "ptc":
            if value:
                raise ValueError(f"--learned-init-fallback {spec!r}: 'ptc' takes no value")
            out.append(("ptc", None))
            continue
        if kind == "ramp" and not value:
            raise ValueError(f"--learned-init-fallback {spec!r}: 'ramp' needs its multipliers")
        multipliers = ramp_multipliers(value or "4,2")
        out.append((kind, multipliers))
    return out


class LearnedInitMonitor:
    """The deployment monitor of a learned start (design 1.4): fed every Newton row of a watched ladder (every rung of
    it: a predict-then-continue start has a short ramp of its own) by the progress hook; raises ``LearnedInitAbort``
    on a refused step, on max|r| growth over the rung's start, on too little reduction after ``k`` steps of a rung or
    when ``kmax`` steps of a rung pass without the certificate ``gate``; with ``stall`` (a ``stall.StallWatch``,
    ``--learned-init-stall``) also on consecutive failed linear solves or no progress over a window.  Rows are
    kept with their rung index (the aborted attempt's history).  ``arm`` starts a new attempt (the direct start or a
    watched fallback)."""

    def __init__(self, k: int, rho: float, kmax: int, growth: float, gate: float, clock=time.time, stall=None,
                 refused: int = 1):
        self.k, self.rho, self.kmax, self.growth, self.gate = int(k), float(rho), int(kmax), float(growth), float(gate)
        self.clock = clock
        self.stall = stall
        self.refused = int(refused)          # --learned-init-refused: consecutive refused steps that abort (default 1)
        self.refused_run = 0
        self.rows, self.armed, self.rungs, self.started = [], False, 0, None
        self.rung_start = None
        self.reason, self.step, self.seconds = None, None, None

    def arm(self, started: float) -> None:
        self.armed, self.started, self.rows, self.rungs, self.rung_start = True, float(started), [], 0, None
        self.reason, self.step, self.seconds = None, None, None
        self.refused_run = 0
        if self.stall is not None:
            self.stall.reset()

    def disarm(self) -> None:
        self.armed = False

    def check(self, entry: dict) -> None:
        if not self.armed:
            return
        it = int(entry.get("it", 0))
        if it == 0 or self.rung_start is None:
            self.rungs += 1
            self.rung_start = dict(entry)
            self.refused_run = 0
        row = dict(entry)
        row["rung"] = self.rungs
        self.rows.append(row)
        stalled = self.stall.check(entry) if self.stall is not None else None    # fed every row (it = 0 too)
        if it == 0:
            return
        start = self.rung_start
        reason = None
        lam = entry.get("lambda", float("nan"))
        refused_now = bool(entry.get("stalled")) or (lam == lam and float(lam) == 0.0)
        self.refused_run = self.refused_run + 1 if refused_now else 0
        # --learned-init-refused N > 1: a refused row the solver itself goes on from (a retry on a fresh
        # preconditioner, a cap fallback, the retaken first step) is tolerated up to N - 1 in a row; one it stops on
        # aborts at once, as with N = 1
        retried = bool(entry.get("retry_on_fresh_jacobian") or entry.get("cap_fallback") or entry.get("first_retake"))
        if refused_now and (self.refused_run >= self.refused or not retried):
            reason = f"step {it} refused (lambda = 0)" + (f", {self.refused_run} in a row" if self.refused > 1 else "")
        elif float(entry["max_abs"]) > self.growth * float(start["max_abs"]):
            reason = (f"max|r| grew {float(entry['max_abs']) / max(float(start['max_abs']), 1e-300):.3g}x "
                      f"(> {self.growth:g}x) by step {it}")
        elif it == self.k and float(entry["l2"]) > self.rho * float(start["l2"]):
            reason = (f"||r||2 ratio {float(entry['l2']) / max(float(start['l2']), 1e-300):.3g} > {self.rho:g} "
                      f"after {it} steps")
        elif it >= self.kmax and float(entry["max_abs"]) >= self.gate:
            reason = f"no certificate within {self.kmax} steps (max|r| = {float(entry['max_abs']):.3e})"
        elif stalled is not None:
            reason = f"stall: {stalled}"
        if reason is not None and self.rungs > 1:
            reason = f"rung {self.rungs}: {reason}"
        if reason is not None:
            self.reason, self.step = reason, it
            self.seconds = self.clock() - self.started
            self.armed = False
            raise LearnedInitAbort(reason)


def refuse_misused_learned_init_flags(args) -> None:
    """Refused before the case loads: the monitor needs a start to judge, the fallback a monitor."""
    abort = parse_learned_init_abort(args.learned_init_abort)
    ladder = parse_learned_init_fallback(args.learned_init_fallback)
    if abort is not None and args.state is None and args.init == "zero":
        raise ValueError("--learned-init-abort watches a learned / transferred start: give --state (or a non-zero "
                         "--init); a cold start has AUTO-RAMP")
    if ladder and abort is None:
        raise ValueError("--learned-init-fallback acts after a LEARNED-INIT ABORT: give --learned-init-abort")
    if args.learned_init_fallback_state is not None and not any(kind == "ramp" for kind, _ in ladder):
        raise ValueError("--learned-init-fallback-state is the start of a 'ramp:' fallback: give one")
    refused = getattr(args, "learned_init_refused", 1)
    if refused < 1:
        raise ValueError("--learned-init-refused N needs N >= 1 (1 = the first refused step aborts, the default)")
    if refused != 1 and abort is None:
        raise ValueError("--learned-init-refused changes the monitor's refused-step rule: give --learned-init-abort")
    if getattr(args, "first_fallback_l1_on_refusal", False) and getattr(args, "first_fallback_l1", None) is None:
        raise ValueError("--first-fallback-l1-on-refusal extends --first-fallback-l1: give --first-fallback-l1 N")
    stall_spec = getattr(args, "learned_init_stall", None)
    parse_stall(stall_spec)                                    # nonsense refused here
    if stall_spec is not None and abort is None:
        raise ValueError("--learned-init-stall adds rules to the monitor: give --learned-init-abort")
    project = getattr(args, "learned_init_project", 0) or 0
    if project < 0:
        raise ValueError("--learned-init-project K needs K >= 0 (0 = off)")
    if project and args.state is None:
        raise ValueError("--learned-init-project projects a --state start: give --state")
    # learned-start overhead fixes
    if getattr(args, "learned_init_project_fast", False) and not project:
        raise ValueError("--learned-init-project-fast speeds up the --learned-init-project K projection: give K >= 1")
    energy_steps = getattr(args, "learned_init_project_energy", 0) or 0
    if energy_steps < 0:
        raise ValueError("--learned-init-project-energy E needs E >= 0 (0 = off)")
    if energy_steps and args.state is None:
        raise ValueError("--learned-init-project-energy projects a --state start: give --state")
    if getattr(args, "camg_l1_rescue_shared", False) and not (
            (getattr(args, "camg_l1_start", False) or getattr(args, "first_fallback_l1", False))
            and getattr(args, "camg_rescue_l1", 0)):
        raise ValueError("--camg-l1-rescue-shared lets the stagnation rescue reuse --camg-l1-start's l1 cycle: give "
                         "--camg-l1-start (or --first-fallback-l1) and --camg-rescue-l1 B")
    if getattr(args, "first_fallback_l1", None) is not None:
        if args.first_fallback_l1 < 1:
            raise ValueError("--first-fallback-l1 N needs N >= 1")
        if not (getattr(args, "learned_action", None) or "").startswith(("camg", "camgnet:")):
            raise ValueError("--first-fallback-l1 turns on the coupled AMG's l1 start: give --learned-action camg*")
    first = getattr(args, "first_forcing", None)
    if first is not None and not (0.0 < first < 1.0):
        raise ValueError("--first-forcing ETA needs 0 < ETA < 1 (a relative GMRES tolerance)")
    first_maxit = getattr(args, "first_maxit", None)
    if first_maxit is not None and first_maxit < 1:
        raise ValueError("--first-maxit N needs N >= 1")
    if getattr(args, "save_iterates", False) and not getattr(args, "iterate_error", False):
        raise ValueError("--save-iterates writes the iterates --iterate-error records: give --iterate-error")
    escalate = getattr(args, "camg_direct_escalate", 0) or 0
    if escalate < 0:
        raise ValueError("--camg-direct-escalate K needs K >= 1 (0 = off)")
    action = getattr(args, "learned_action", None) or ""
    if escalate and not (action.startswith("camg") and not action.startswith("camgnet:")):
        raise ValueError("--camg-direct-escalate wraps the classical coupled AMG: give --learned-action camg / camg2 / "
                         "camg2w")
    if getattr(args, "cudss_hybrid_gib", 0.0) < 0:
        raise ValueError("--cudss-hybrid-gib needs GIB >= 0 (0 = off)")
    if getattr(args, "camg_rescue_rule", "factor") != "factor" and not getattr(args, "camg_rescue_l1", 0):
        raise ValueError("--camg-rescue-rule chooses when the stagnation rescue fires: it needs --camg-rescue-l1 B")
    maxit = getattr(args, "camg_rescue_maxit", None)
    if maxit is not None and (maxit < 0 or not getattr(args, "camg_rescue_l1", 0)):
        raise ValueError("--camg-rescue-maxit K caps the stagnation rescue: it needs K >= 0 and --camg-rescue-l1 B")
    if forcing_cap_of(args) is not None and not getattr(args, "forcing", True):
        raise ValueError("--forcing-cap-below caps the Eisenstat-Walker forcing: it does nothing with --no-forcing")
    if forcing_stall_cap_of(args) is not None and not getattr(args, "forcing", True):
        raise ValueError("--forcing-cap-stall caps the Eisenstat-Walker forcing: it does nothing with --no-forcing")
    fallback = getattr(args, "forcing_cap_fallback", None)
    if fallback is not None:
        if not (0.0 < fallback <= 1.0):
            raise ValueError("--forcing-cap-fallback LMIN needs 0 < LMIN <= 1 (a line-search step length)")
        if forcing_cap_of(args) is None and forcing_stall_cap_of(args) is None:
            raise ValueError("--forcing-cap-fallback acts on capped steps: give --forcing-cap-below or "
                             "--forcing-cap-stall")


def cap_fallback_hook(learned_factory, logger):
    """``newton.solve_newton(on_cap_fallback=)`` of ``--forcing-cap-fallback``: from the call
    on, the production rescue path -- every later build of the factory (``camg_rescue_mode``) and the live
    preconditioner (and the wrappers around it, through ``.primary``) run the l1 rescue uncapped."""
    def hook(precondition):
        from Pipeline.CoupledNewton.block_amg import StagnationRescue
        mode = getattr(learned_factory, "camg_rescue_mode", None)
        if mode is not None:
            mode["maxit_lifted"] = True
        lifted, obj = 0, precondition
        for _ in range(6):
            if obj is None:
                break
            if isinstance(obj, StagnationRescue):
                lifted += int(obj.lift_maxit())
            obj = getattr(obj, "primary", None)
        logger.info("--forcing-cap-fallback: a capped step was not taken; the caps are off for this rung and the "
                    "stagnation rescue runs uncapped from now on (%d live rescue cap(s) lifted)", lifted)
        return lifted
    return hook


def forcing_stall_cap_of(args):
    """``--forcing-cap-stall RHO:ETA`` as ``(RHO, ETA)`` floats (``newton.solve_newton(forcing_stall_cap=)``), ``None``
    when not given; malformed specs, RHO outside (0, 1] and ETA outside (0, 1) are refused."""
    spec = getattr(args, "forcing_cap_stall", None)
    if spec is None:
        return None
    parts = str(spec).split(":")
    try:
        if len(parts) != 2:
            raise ValueError
        rho, eta = float(parts[0]), float(parts[1])
    except ValueError:
        raise ValueError(f"--forcing-cap-stall needs RHO:ETA (two numbers, e.g. 0.9:1e-3), not {spec!r}") from None
    from Pipeline.CoupledNewton.newton import check_stall_cap
    try:
        return check_stall_cap((rho, eta))
    except ValueError as exc:
        raise ValueError(f"--forcing-cap-stall {spec}: {exc}") from None


def forcing_cap_of(args):
    """``--forcing-cap-below R:ETA`` as ``(R, ETA)`` floats (``newton.solve_newton(forcing_cap=)``), ``None`` when not
    given; malformed specs, R <= 0 and ETA outside (0, 1) are refused."""
    spec = getattr(args, "forcing_cap_below", None)
    if spec is None:
        return None
    parts = str(spec).split(":")
    try:
        if len(parts) != 2:
            raise ValueError
        below, eta = float(parts[0]), float(parts[1])
    except ValueError:
        raise ValueError(f"--forcing-cap-below needs R:ETA (two numbers, e.g. 1e-8:1e-3), not {spec!r}") from None
    from Pipeline.CoupledNewton.newton import check_forcing_cap
    try:
        return check_forcing_cap((below, eta))
    except ValueError as exc:
        raise ValueError(f"--forcing-cap-below {spec}: {exc}") from None


def refuse_the_polynomial_over_a_block_preconditioner(args) -> None:
    """``--poly-degree`` composed with a block ``--precond`` is measured to break.

    Measured on the DataCenter MLZE case (498,337 cells), rung 1 of
    the viscosity ramp, three arms differing in ONE flag:

    ==============================  ==========================================
    ``--precond covered``, poly 24  DIVERGES.  ``lambda = 0`` and "NO STEP
                                    REDUCED THE RESIDUAL" from Newton 1;
                                    ``max|r|`` 2.51e+01 -> 1.80e+41 by Newton 8.
                                    Unchanged by ``--equilibrate ruiz``, so it
                                    is not the scaling.
    ``--precond covered``, poly 0   HEALTHY.  2.51e+01 -> 4.90 -> 0.744, all
                                    ``lambda = 1``, so it is not the restart.
    ``--precond none``, poly 24     HEALTHY AND FAST.  Same descent, and the
                                    inner solve falls to 2, 3 and 12 Arnoldi
                                    steps where the unpreconditioned control
                                    needs 100, 11, 37 and 41.
    ==============================  ==========================================

    The composition is algebraically right -- the polynomial is built for
    ``B = A . M`` and applied as ``M . p_B``, so GMRES sees ``B p_B`` -- and it
    is exercised by a unit test.  What is NOT established is that ``p_B`` is
    usable when ``M`` is one of the shipped block shapes, whose momentum head is
    separately measured to AMPLIFY error by ~5x at its defaults.  Every
    polynomial measurement on record was taken at ``--precond none``.

    So this refuses rather than warns: the failure is silent in the inner-solve
    report and only shows up as a march that will not take a step.
    """
    if args.poly_degree and args.precond != "none":
        raise ValueError(
            f"--poly-degree {args.poly_degree} with --precond {args.precond} is "
            "measured to diverge (DataCenter MLZE rung 1: max|r| 2.51e+01 -> "
            "1.80e+41 by Newton 8, lambda = 0 from Newton 1, unchanged by "
            "--equilibrate ruiz).  The polynomial is only measured to work as "
            "the SOLE preconditioner: use --precond none, where the same case "
            "descends normally on 2-12 Arnoldi steps per solve"
        )


def rung_advance_gate(args) -> float:
    """The residual a continuation rung must reach before it hands its state on.

    It is the ACCEPTANCE GATE, ``--gate``, unless ``--rung-tol`` is given, in
    which case it is that -- because asking a rung for 1e-6 and then refusing to
    advance it below 1e-10 would declare RAMP INCOMPLETE on every rung that did
    exactly what it was asked.  Either way it is resolved by this function so
    that the choice is a decision with a name rather than whichever tolerance
    happened to be in scope at the ``break``.

    The bug: the comparison read ``--tol`` (1e-12) while the run was
    judged by ``--gate`` (1e-10), and a rung landing between the two ended the
    ramp AND passed the acceptance test.  Measured on the DataCenter laminar arm
    (docs/research/2026-08-18-dc-first-solve.md): rung 1 of
    5 reached max|r| = 2.5960e-11, ||r||_2 = 1.1705e-09, relative 2.3307e-12, so
    the ramp stopped after nu x 4096 -- Re ~ 9 -- while the entry printed no
    "NOT ACCEPTED" and exited 0.  A Reynolds-9 answer was reported as a
    converged data centre.
    """
    return args.gate if args.rung_tol is None else args.rung_tol


def _rack_delta(solver, patch_name: str) -> float:
    """The dT of the named rack discharge, for the log line only."""
    batch = getattr(solver, "bcpatch_batch", None)
    for group in (getattr(batch, "coupled", []) if batch is not None else []):
        for condition in group:
            if getattr(condition, "patch_name", None) == patch_name:
                return float(getattr(condition, "deltaT", 0.0))
    return 0.0


def write_cbbm_history(passes, logger) -> str:
    """CBBM outer-iteration residuals to CSV, one row per rack per pass.

    Written unconditionally whenever a lagged rack exists, because the
    fixed-point residual has to be quotable beside every number the run
    produces -- the DataCenter reference reported a converged fixed point
    while sitting 83x outside its own tolerance, and nothing on disk said so.
    """
    path = os.path.join(logger.saving_path, "cbbm_history.csv")
    with open(path, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["pass", "patch", "previous_K", "new_K", "change_K",
                         "worst_change_K"])
        for entry in passes:
            for rack in entry["racks"]:
                writer.writerow([entry["pass"], rack["patch"],
                                 "" if rack["previous"] is None
                                 else f"{rack['previous']:.6f}",
                                 f"{rack['new']:.6f}",
                                 "" if rack["previous"] is None
                                 else f"{rack['change']:.6f}",
                                 f"{entry['worst']:.6f}"])
    logger.info("CBBM outer-iteration history written to %s", path)
    return path


def run_continuation(solver, nu, multipliers, x0, solve_rung, advance_gate,
                     logger, rung_checkpoint=None):
    """Walk the viscosity ladder, warm-starting each rung from the last.

    ``solve_rung(x, index)`` returns ``(x, history)`` for the rung whose
    coefficients are already set; ``index`` is 1-based and is passed because
    whether PTC is armed is a property of WHICH rung this is -- rung 1 is the
    only cold start, and every later rung inherits a converged state.  The return carries whether the ladder finished, because a
    partial ramp and a complete one are otherwise indistinguishable in every
    artifact a reader checks -- the residual, the gate line and the exit status.
    """
    # A case that carries a thermal diffusivity has its ramp applied to BOTH
    # coefficients, which is what keeps the intermediate rungs on the same fluid.
    # Scaling nu alone gives Ra/m at Pr*m, so
    # an arm labelled "Ra 1e6, 32x" was solving Ra = 3.1e4 at Pr = 22.7: not
    # low-Rayleigh air but a different fluid, whose momentum and thermal boundary
    # layers separate by sqrt(Pr).  The final rung is m = 1 either way, so only
    # the reported INTERMEDIATE rungs were ever wrong -- but a Nusselt number or
    # u_max quoted from one of them is not comparable to any benchmark entry.
    alpha_coeff = getattr(solver.solver, "alpha_coeff", None)
    alpha_base = None if alpha_coeff is None else alpha_coeff.clone()
    x, history, complete = x0, [], True
    solved = 0
    for index, multiplier in enumerate(multipliers, start=1):
        solver.solver.nu_coeff = torch.full_like(
            solver.solver.nu_coeff, nu * multiplier)
        if alpha_base is not None:
            solver.solver.alpha_coeff = alpha_base * multiplier
        logger.info(
            "continuation rung %d/%d: nu = %.6g (%.4gx)%s -- Rayleigh %.4gx, "
            "Prandtl UNCHANGED", index, len(multipliers), nu * multiplier,
            multiplier,
            "" if alpha_base is None else
            f", alpha = {float(alpha_base.flatten()[0]) * multiplier:.6g}",
            1.0 / multiplier ** 2 if alpha_base is not None else 1.0 / multiplier)
        # ACCUMULATE across rungs, never rebind.  Measured: rebinding
        # left `history` holding only the FINAL rung, so on every continuation run
        # the step count, the GMRES total, the assembly count, the assembly seconds
        # and the residual-history file on disk were all last-rung-only, while
        # `elapsed` below spans the whole run.  The log line reports assembly
        # seconds "of the total" -- a last-rung numerator over a whole-run
        # denominator -- so the one number anyone would quote to argue assembly
        # cost is negligible was systematically understated, in our favour.
        x, rung_history = solve_rung(x, index)
        for entry in rung_history:
            entry["rung"] = index
            entry["nu_multiplier"] = multiplier
        history += rung_history
        solved = index
        if rung_checkpoint is not None:
            # AFTER the rung's own gate check would be too late -- a stalled
            # rung's state is exactly the fixture an offline diagnosis wants.
            rung_checkpoint(index, multiplier, x)
        if history[-1]["max_abs"] >= advance_gate and index < len(multipliers):
            # Carrying a stalled rung forward would warm-start the next one from
            # a state that solves nothing, and the run would still report the
            # final rung's residual as if the ramp had worked.
            complete = False
            logger.info(
                "RAMP INCOMPLETE: rung %d/%d (nu = %.6g, %.4gx) stopped at "
                "max|r| = %.3e, which is above the %.1e gate it has to clear "
                "to hand its state on.  Rungs %s are NOT attempted, and the "
                "state this run reports solves nu x %.4g, NOT the viscosity "
                "the case declares",
                index, len(multipliers), nu * multiplier, multiplier,
                history[-1]["max_abs"], advance_gate,
                ", ".join(f"{value:g}x" for value in multipliers[index:]),
                multiplier)
            break
    return {"x": x, "history": history, "rungs_solved": solved,
            "rungs_total": len(multipliers), "complete": complete,
            "final_multiplier": multipliers[solved - 1] if solved else 1.0}


def run_adaptive_continuation(solver, nu, x0, solve_rung, *, tol, gate, logger, rung_checkpoint=None,
                              on_rung=None, controller=None):
    """``--continuation adaptive``: natural continuation in ``s`` from nu x 8 at s = 0 to the case's nu at s = 1.

    The viscosity is ``nu x path_multiplier(s)``; a case with a thermal diffusivity has it scaled by the same factor,
    as ``run_continuation`` does, so the path keeps the Prandtl number and moves the Rayleigh number as
    ``1 / path_multiplier(s)^2``.  The steps, the budgets and the give-up rules are those of
    ``Pipeline.CoupledNewton.continuation.natural_continuation`` with ``controller`` (default ``UNIFIED``).  The
    anchor at s = 0 starts from ``x0`` (``None``: rest).  Intermediate rungs stop at ``rung_rtol`` times the anchor's
    first ``max|r|`` (never below ``tol``); the rung at s = 1 is solved to ``tol`` and accepted below ``gate``, the
    entry's acceptance gate.

    ``solve_rung(x, index, tol=..., maxit=...)`` is the entry's ``solve_newton`` call of a rung whose coefficients
    are already set.  ``index`` counts the attempts from 1, so the anchor is the entry's rung 1, its only cold start
    (``--ptc-rungs first`` and ``--precond-rebuild-first-rung`` act on it).  ``on_rung(index, multiplier)`` is
    called before each attempt, and ``rung_checkpoint(index, multiplier, x)`` after it.

    Returns ``run_continuation``'s record: ``complete`` is True exactly when the rung at s = 1 was accepted.  On a
    path that is given up, ``x`` is the last accepted root and the coefficients are left at its multiplier, so the
    entry's certificate reads that state's own residual.  ``history`` holds the Newton rows of every attempt, the
    discarded ones included, each tagged with its attempt (``rung``), ``nu_multiplier``, ``continuation_s``,
    ``continuation_final`` and ``continuation_accepted``.  ``continuation`` is the path's record for
    ``continuation.json``."""
    from Pipeline.CoupledNewton.continuation import UNIFIED, Rung, natural_continuation, rung_tolerance

    controller = UNIFIED if controller is None else controller
    alpha_coeff = getattr(solver.solver, "alpha_coeff", None)
    alpha_base = None if alpha_coeff is None else alpha_coeff.clone()

    def set_parameter(s):
        multiplier = path_multiplier(s)
        solver.solver.nu_coeff = torch.full_like(solver.solver.nu_coeff, nu * multiplier)
        if alpha_base is not None:
            solver.solver.alpha_coeff = alpha_base * multiplier
        return multiplier

    def describe(multiplier):
        if alpha_base is None:
            return f"nu = {nu * multiplier:.6g} ({multiplier:.6g}x), Reynolds / {multiplier:.6g}"
        return (f"nu = {nu * multiplier:.6g} ({multiplier:.6g}x), alpha = "
                f"{float(alpha_base.flatten()[0]) * multiplier:.6g}, Rayleigh / {multiplier ** 2:.6g} at the "
                f"case's Prandtl number")

    set_parameter(0.0)
    first = x0 if x0 is not None else torch.zeros(solver.n_unknowns, device=solver.device, dtype=solver.dtype)
    reference = float(solver.residual(first).abs().max())
    intermediate = rung_tolerance(False, reference, tol, controller)
    logger.info("ADAPTIVE CONTINUATION: nu x %g^(1 - s) from s = 0 (%s) to s = 1, from %s; the first iterate's "
                "max|r| at s = 0 is %.6e, so intermediate rungs stop at %.3e (%g of it, at most %d Newton steps), "
                "the anchor within %d and the rung at s = 1 within %d steps to %.1e (gate %.1e); first step %g, "
                "x%g after a rung of at most %d steps, x%g on a failed rung, given up below %g or after %d Newton "
                "steps", ANCHOR_MULTIPLIER, describe(path_multiplier(0.0)),
                "rest" if x0 is None else "the first iterate", reference, intermediate, controller.rung_rtol,
                controller.rung_maxit, controller.anchor_maxit, controller.final_maxit, tol, gate, controller.first,
                controller.grow, controller.grow_below, controller.shrink, controller.floor, controller.max_newton)
    history = []

    def corrector(s, x, *, final, anchor, maxit, index):
        multiplier = set_parameter(s)
        if on_rung is not None:
            on_rung(index, multiplier)
        rung_tol = tol if final else intermediate
        logger.info("continuation attempt %d: s = %.6g, %s; %s, tolerance %.3e, at most %d Newton steps", index, s,
                    describe(multiplier), "the anchor from " + ("rest" if x is None else "the first iterate")
                    if anchor else "from the root at the last accepted s", rung_tol, maxit)
        x_out, rows = solve_rung(x, index, tol=rung_tol, maxit=maxit)
        reached = float(rows[-1]["max_abs"]) if rows else float("inf")
        accepted = math.isfinite(reached) and reached < (gate if final else rung_tol)
        for entry in rows:
            entry.update(rung=index, nu_multiplier=multiplier, continuation_s=s, continuation_final=bool(final),
                         continuation_accepted=bool(accepted))
        history.extend(rows)
        if rung_checkpoint is not None:
            rung_checkpoint(index, multiplier, x_out)
        return Rung(x_out, sum(1 for entry in rows if entry.get("it", 0) > 0), accepted,
                    {"multiplier": multiplier, "tol": rung_tol, "max_abs": reached,
                     "gmres": int(sum(int(entry.get("inner_iterations", 0) or 0) for entry in rows))})

    path = natural_continuation(corrector, x0, controller=controller, log=logger.info)
    complete = bool(path["ok"])
    reached_s = path["reached"]
    if complete:
        final_multiplier = path_multiplier(1.0)
    else:
        final_multiplier = set_parameter(0.0 if reached_s is None else reached_s)
    accepted = [a for a in path["attempts"] if a["accepted"]]
    record = {"path": f"nu x {ANCHOR_MULTIPLIER:g}^(1 - s)", "anchor_multiplier": ANCHOR_MULTIPLIER,
              "scales_diffusivity": alpha_base is not None, "controller": controller.as_dict(),
              "reference_max_abs": reference, "intermediate_tol": intermediate, "final_tol": float(tol),
              "gate": float(gate), "complete": complete, "reason": path["reason"], "reached_s": reached_s,
              "newton_steps": int(path["newton_steps"]),
              "newton_steps_discarded": int(sum(a["newton_steps"] for a in path["attempts"] if not a["accepted"])),
              "attempts": path["attempts"], "accepted_s": [a["s"] for a in accepted],
              "accepted_multipliers": [a["multiplier"] for a in accepted]}
    logger.info("ADAPTIVE CONTINUATION %s: %d attempts (%d accepted), %d Newton steps (%d of them in discarded "
                "attempts); accepted s = %s", "complete" if complete else f"GIVEN UP ({path['reason']})",
                len(path["attempts"]), len(accepted), record["newton_steps"], record["newton_steps_discarded"],
                ", ".join(f"{s:.6g}" for s in record["accepted_s"]) or "none")
    if not complete:
        logger.info("RAMP INCOMPLETE: the adaptive continuation stopped at s = %s; the state this run reports solves "
                    "nu x %.4g, NOT the viscosity the case declares",
                    "none (the anchor)" if reached_s is None else f"{reached_s:.6g}", final_multiplier)
    return {"x": path["x"], "history": history, "rungs_solved": len(accepted),
            "rungs_total": len(accepted) + (0 if complete else 1), "complete": complete,
            "final_multiplier": final_multiplier, "continuation": record}


def refuse_an_unsteady_case(fvconfig) -> None:
    """Refuse a case whose time derivative is live.

    This entry solves ``r(x) = 0`` with no time level, and the residual it drives
    is whatever ``picoext`` assembles.  A case with ``unsteady = true`` puts a
    nonzero coefficient on the unsteady term, so the equation being solved would
    silently become a single backward-Euler step from a zero old state rather than
    the steady one.
    """
    theta_pde = fvconfig.get("theta_pde") or {}
    if bool(theta_pde.get("unsteady", False)):
        raise ValueError(
            "[theta_pde] unsteady = true, and this entry has no time level: the "
            'steady equations need unsteady = false with ddtSchemes = '
            '"steadyState"')


def field_slices(meta_data, solved_fields):
    """``{field: slice}`` into ``phi_cpd_cell``, in the CASE's own channel order.

    ``to_vtk`` splits that tensor by ``boundary_info``'s key order and
    ``VAR_CONFIG``'s channel counts, so the writer here agrees with it by
    construction.  ``solved_fields`` is what the discretisation declared it has
    unknowns for (``COLLOCATED_UNKNOWNS``), and the two must be the same SET while
    the ORDER stays the case's: an isothermal case and a Boussinesq case reach
    this line through the same code, and a case whose fields the solver has no
    unknowns for is refused rather than exported with a silently zero channel.
    """
    fields = list(meta_data["boundary_info"].keys())
    if set(fields) != set(solved_fields):
        raise ValueError(
            f"this case carries the fields {fields}; the solver it declares has "
            f"unknowns for {list(solved_fields)}")
    slices, start = {}, 0
    for name in fields:
        width = VAR_CONFIG[name]
        slices[name] = slice(start, start + width)
        start += width
    return slices, start


def block_report(solver, residual) -> str:
    """``max|r|`` PER ROW BLOCK, every block by name.

    Reporting "everything past the velocity" as continuity would fold a
    Boussinesq energy row into the continuity number, and an energy equation
    that never converged would then hide behind a continuity one that did --
    which is exactly how a fifth unknown gets certified without ever being
    solved.
    """
    split, offset = [], 0
    for _, width, _, attribute in solver.unknowns:
        rows = width * solver.n_cell
        split.append(f"{attribute.removeprefix('residual_')} "
                     f"{float(residual[offset:offset + rows].abs().max()):.6e}")
        offset += rows
    return ", ".join(split)


def blocks_to_unknown(solver, column_of):
    """``[u | v | w | p | ...]`` from a per-field ``[N_cell, width]`` supplier.

    The one place the flat unknown's layout is written down on this path, and
    it is the transpose ``scatter_state`` inverts: block ``f`` occupies rows
    ``f * N_cell : (f + 1) * N_cell`` in ``COLLOCATED_UNKNOWNS`` order, with
    the cells inside a block in ``solver.interior`` order.  Driven by NAME, so
    an initialiser cannot put T's values in the pressure block on a case whose
    channel order differs from the solver's block order.
    """
    blocks = []
    for name, width, _, _ in solver.unknowns:
        column = column_of(name, width)
        if column.shape != (solver.n_cell, width):
            raise ValueError(
                f"initial {name!r} has shape {tuple(column.shape)}; this case "
                f"wants {(solver.n_cell, width)}")
        blocks.append(column.transpose(0, 1).reshape(-1))
    return torch.cat(blocks)


def internal_start(solver, case):
    """The case's own ``internalField`` values as the first Newton iterate.

    Read from the pool rather than re-parsed, so whatever the loader did to a
    value on the way in is already applied -- omega in particular is STORED as
    ln(omega) and the pool holds the transformed number.
    """
    slices, _ = field_slices(case.meta_data, solver.field_names())
    phi = case.pool.phi_cpd_cell_pool.to(device=solver.device,
                                         dtype=solver.dtype)
    return blocks_to_unknown(
        solver, lambda name, width: phi[solver.interior, slices[name]])


def parse_field_offsets(specs):
    """``FIELD=VALUE`` strings -> ``{field: value}``, the amount to subtract."""
    offsets = {}
    for spec in specs or ():
        name, separator, value = spec.partition("=")
        if not separator:
            raise ValueError(
                f"--foam-offset wants FIELD=VALUE, got {spec!r}")
        offsets[name.strip()] = float(value)
    return offsets


def openfoam_start(solver, foam_path, offsets, time, logger):
    """A reference solver's converged field as the first Newton iterate.

    Two conversions stand between the reference's variables and ours, and
    both are applied HERE rather than inside the reader, because both are
    facts about our unknowns rather than about OpenFOAM's file format:

    * ``offsets`` shifts a field the case stores as an excess over a level the
      reference writes absolutely.  It comes from the launcher because the
      level belongs to the case, and getting it wrong is not detectable from
      either code's files.
    * a field the active turbulence model declares in
      ``LOG_TRANSFORMED_BC_FIELDS`` is stored as its logarithm, the same
      transform ``setOmega`` applies at a ghost write.  A non-positive value
      is refused rather than floored: OpenFOAM bounds omega away from zero, so
      one arriving here means the wrong array was read, and a floor would turn
      that into a plausible-looking ln(1e-300) start.
    """
    import numpy as np

    from FVdomain.openfoam_field import read_cell_fields

    names = [name for name, _, _, _ in solver.unknowns]
    targets = solver.solver.cpd_cell_pos[solver.interior].detach().cpu().numpy()
    values, time_value, distance = read_cell_fields(
        foam_path, names, targets, match_dims=3, time=time)
    log_fields = getattr(type(solver.solver.turbulence),
                         "LOG_TRANSFORMED_BC_FIELDS", ())

    def column_of(name, width):
        column = values[name][:, :width]
        if name in offsets:
            column = column - offsets[name]
        if name in log_fields:
            smallest = float(column.min())
            if smallest <= 0.0:
                raise ValueError(
                    f"the reference's {name!r} reaches {smallest:.6e} and this "
                    "case stores its logarithm; OpenFOAM bounds it away from "
                    "zero, so this is the wrong array rather than a hard cell")
            column = np.log(column)
        return torch.from_numpy(np.ascontiguousarray(column)).to(
            device=solver.device, dtype=solver.dtype)

    # The match distance is the whole gate on this start: it is what says the
    # two meshes describe the same geometry, and a large one means the field
    # was interpolated across the room rather than read off the same cell.
    logger.info(
        "--init openfoam: %s at time %g, %d cells matched by nearest cell "
        "centre in 3D, distance max %.6e mean %.6e (offsets %s, "
        "log-transformed %s)", foam_path, time_value, solver.n_cell,
        float(distance.max()), float(distance.mean()),
        offsets or "none", list(log_fields) or "none")
    return blocks_to_unknown(solver, column_of)


def dump_residual(solver, x, path, logger, which: str) -> None:
    """``--dump-residual``: ``{x, r, blocks, n_cell}`` of one iterate, for parity diagnostics (opt-in)."""
    x = (torch.zeros(solver.n_unknowns, device=solver.device, dtype=solver.dtype) if x is None else x).detach()
    r = solver.residual(x).detach()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    torch.save({"x": x.to("cpu"), "r": r.to("cpu"), "n_cell": int(solver.n_cell),
                "blocks": [(name, width, attribute.removeprefix("residual_"))
                           for name, width, _, attribute in solver.unknowns]}, path)
    logger.info("--dump-residual: %s iterate and its residual (max|r| %.6e) -> %s", which,
                float(r.abs().max()), path)


def export_state(solver, x, case, logger, run_name: str) -> str:
    """Write the solved fields as a VTM, patches included, under this run's dir.

    The patches carry REAL values here, which is why they are exported at all.
    The staggered entry next door writes interior only, and correctly: its
    unknowns live on faces and its ghost rows hold nothing that was solved for.
    This solver's ghosts hold the boundary conditions themselves -- the lid's
    prescribed velocity, a zero-gradient wall's mirrored owner -- as long as they
    are read after ``picoext.forward`` has enforced them, which is what
    ``state_with_boundaries`` is for.  Building the array with ``torch.zeros`` and
    filling only the interior would have
    put a zero on the lid and dressed it as a wall value that was solved for.
    """
    slices, channels = field_slices(case.meta_data, solver.field_names())
    mask = solver.solver.mask_interior_cell.reshape(-1).cpu()
    state = solver.state_with_boundaries(x).cpu()
    phi = torch.zeros(mask.shape[0], channels, dtype=torch.float64)
    # Laid out by NAME, never by position: the solver's block order and the
    # case's channel order are two different orders that happen to agree on the
    # isothermal case, and a positional copy would put p_rgh in T's channel on
    # any case where they do not.
    offset = 0
    for name, width, _, _ in solver.unknowns:
        phi[:, slices[name]] = state[:, offset:offset + width]
        offset += width
    output_dir = os.path.join(logger.saving_path, "rollout_results",
                              str(case.case_name))
    os.makedirs(output_dir, exist_ok=True)
    return export_mesh_with_patches(
        mesh=case.meta_data, phi_cpd_cell=phi, mask_interior_cell=mask,
        save_path=os.path.join(output_dir, run_name), export_patches=True)


def parity_amplitude(solver, x) -> float:
    """The solved pressure's grid-parity content, on this run's own cells."""
    return grid_parity_amplitude(solver.solver.cpd_cell_pos[solver.interior],
                                 x[solver.velocity:solver.pressure])


def write_history(history, logger) -> str:
    path = os.path.join(logger.saving_path, "newton_residual.csv")
    fields = ["it", "max_abs", "l2", "lambda", "stalled", "drift", "inner_rtol",
              "inner_iterations", "inner_rel", "assembly_seconds", "reassembled"]
    if any("rung" in entry for entry in history):
        # ``it`` restarts at 0 on every rung, so without these two the CSV of a
        # continuation run cannot be split back into its ladder.
        fields += ["rung", "nu_multiplier"]
    if any("continuation_s" in entry for entry in history):
        # --continuation adaptive: each attempt's path parameter, whether it is the rung at s = 1, and whether it
        # was accepted (the rows of a discarded attempt stay: its steps are part of the cost)
        fields += ["continuation_s", "continuation_final", "continuation_accepted"]
    if any("ptc_theta" in entry for entry in history):
        # Only a PTC run carries the columns, so a default run's artifact is
        # byte-for-byte what it was before the mode existed.
        fields += ["ptc_theta", "ptc_event"]
    if any("ptc_forced_candidate_l2" in entry for entry in history):
        # Opt-in guard telemetry only; legacy CSV columns stay unchanged.
        fields += ["ptc_forced_candidate_l2", "ptc_forced_growth_ratio",
                   "ptc_max_forced_growth"]
    if any("inner_recurrence" in entry for entry in history):
        # --true-residual-check: ``inner_rel`` is the true residual GMRES ended on; its recurrence and the step's own
        # true residual beside it, and whether the solve was done again
        fields += ["inner_recurrence", "inner_step_true", "inner_flagged", "inner_redone"]
    if any("l1_rule" in entry for entry in history):
        # --camg-adaptive-l1: the rule's decisions of the step's builds and the l1 levels of the latest plan
        fields += ["l1_rule", "l1_levels"]
    if any("inner_rel_true" in entry for entry in history):
        # Same rule, and here the column is the point: on an equilibrated run
        # ``inner_rel`` is measured in the scaled system and this one is not.
        fields += ["equilibrate", "inner_rel_true"]
    if any("matrix_free" in entry for entry in history):
        # Same rule again: the column records WHICH linearisation the Krylov
        # solve ran on, which is the whole content of the mode.
        fields += ["matrix_free", "linearise_seconds"]
    if any("precond_rebuilt" in entry for entry in history):
        # JFNK mode: operator, rebuild and JVP columns (jfnk.JFNK_COLUMNS).
        from Pipeline.CoupledNewton.jfnk import JFNK_COLUMNS
        fields += list(JFNK_COLUMNS)
    if any("jacobian_seconds" in entry for entry in history):
        # --component-timers / --iterate-error: the synced split
        # of assembly_seconds, plus the inner solve.  Same opt-in rule.
        fields += ["jacobian_seconds", "sparsity_seconds", "precond_seconds",
                   "krylov_seconds"]
    if any("cuda_peak_gib" in entry for entry in history):
        # The same flag on a CUDA device: the step's GPU memory.
        fields += ["cuda_built_gib", "cuda_build_peak_gib", "cuda_peak_gib",
                   "cuda_allocated_gib"]
    if any("ne_active" in entry for entry in history):
        # --nonlinear-elimination: what the elimination did before each step.  Same opt-in rule.
        from Pipeline.CoupledNewton.elimination import NE_COLUMNS
        fields += list(NE_COLUMNS)
    if any("forcing_capped" in entry for entry in history):
        # --forcing-cap-below: the endgame cap set this row's inner_rtol.  Same opt-in rule.
        fields += ["forcing_capped"]
    if any("forcing_cap_stall" in entry for entry in history):
        # --forcing-cap-stall: the stagnation trigger held for this row.  Same opt-in rule.
        fields += ["forcing_cap_stall"]
    if any("cap_fallback" in entry for entry in history):
        # --forcing-cap-fallback: this capped step was not taken.  Same opt-in rule.
        fields += ["cap_fallback", "cap_fallback_lambda"]
    if any("elapsed" in entry for entry in history):
        # --iterate-error: Newton-timer seconds at which the row was logged,
        # net of the iterate copies (IterateRecorder).
        fields += ["elapsed"]
    if any("stage" in entry for entry in history):
        # --cold-start staged: which stage a row belongs to, and the positivity map's telemetry.
        fields += ["stage"] + [key for key in ("trial_map", "trial_map_active", "bounded_cells", "capped_cells")
                               if any(key in entry for entry in history)]
    if any("l2_true" in entry for entry in history):
        # solve_newton(merit="jacobi"): ``l2`` is the merit ||D^-1 r|| on those rows, this the plain ||r||.
        fields += ["l2_true"]
    if any("contraction_theta" in entry for entry in history):
        # --contraction-probe: Theta_0 of the first step of each call.  Same opt-in rule.
        fields += ["contraction_theta", "contraction_theta_max", "contraction_probe_inner_rel",
                   "contraction_probe_iterations"]
    if any("step_l2" in entry for entry in history):
        # --step-norm-log: the Newton correction of every step.  Same opt-in rule.
        fields += ["step_l2", "step_rel_max", "step_rel_fields"]
    if any("accel_tried" in entry for entry in history):
        # --nl-accel: the nonlinear GMRES combination of each step.  Same opt-in rule.
        from Pipeline.CoupledNewton.nonlinear_acceleration import ACCEL_COLUMNS
        fields += list(ACCEL_COLUMNS)
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for entry in history:
            writer.writerow({key: entry.get(key, "") for key in fields})
    return path


def write_inner_history(history, logger) -> str:
    """The per-iteration GMRES residual curve for every step, as JSON.

    Written ONCE, after the solve, from tuples already buffered in memory --
    the timed loop never touches a file.  Answers the one question
    ``inner_rel`` cannot: within a step that spends its whole matvec budget
    without meeting tolerance, at which iteration did the linear residual stop
    improving?  A curve that is flat after a few hundred says the remaining
    matvecs bought nothing.
    """
    path = os.path.join(logger.saving_path, "inner_residuals.json")
    with open(path, "w") as handle:
        json.dump([{"it": entry["it"],
                    "requested": entry.get("inner_rtol"),
                    "iterations": entry.get("inner_iterations"),
                    "residuals": list(entry.get("inner_residuals", ()))}
                   for entry in history if "inner_residuals" in entry],
                  handle)
    return path


# ---------------------------------------------------------------- --iterate-error
# Time-to-accuracy against OpenFOAM (GPU-vs-OpenFOAM feasibility plan, docs/research/
# 2026-10-04-gpu-vs-openfoam-feasibility.md sections 2.1 and 2.4).  OpenFOAM's side is the
# volume-integrated error of every iteration against its own converged field; this is ours.
# The residual alone cannot answer it: max|r| and the solution error fall at different rates,
# and a SIMPLE iteration and a Newton step are not comparable units.
ITERATE_ERROR_LEVELS = (1e-2, 1e-3, 1e-4, 1e-6, 1e-8)
# --compare-root: two states are the same root when every field's volume-weighted relative L2 is at
# most this (P1 design 1.3, experiments/NaturalConvection/CoupledNewton/InitialGuess/diagnostics/same_root.py: two certified runs of one root agree
# to ~1e-6, another branch differs by O(1e-2 - 1)).
SAME_ROOT_TOL = 1.0e-5


def unknown_layout(solver):
    """``[(name, offset, width)]`` of the flat unknown in ``COLLOCATED_UNKNOWNS`` order.

    It is the layout ``blocks_to_unknown`` writes: field ``f`` occupies ``width * n_cell``
    rows, component-major, with cells in ``solver.interior`` order.
    """
    layout, offset = [], 0
    for name, width, _, _ in solver.unknowns:
        layout.append((str(name), offset, int(width)))
        offset += int(width) * solver.n_cell
    return layout


def gauged_fields(solver, layout) -> set:
    """The fields compared mean-removed: the pressure block when ``resolve_gauge`` found its level FREE.

    That is the case ``steady_vs_openfoam --gauge mean`` is run for (case.json ``gauge``), and the
    block ``solver.gauge`` projects.  A pinned pressure, and every other field, is compared as is.
    """
    if not solver._gauge_free:
        return set()
    return {name for name, offset, width in layout if offset == solver.velocity and width == 1}


def field_relative_errors(x, reference, layout, volumes, mean_fields=()) -> dict:
    """``{field: ||x_f - ref_f||_w / ||ref_f||_w}`` with cell weights ``volumes / sum(volumes)``.

    This is ``Diagnostics.steady_vs_openfoam.score``'s ``rel_l2``: the sum runs over the cells and
    the components of a field, and a field in ``mean_fields`` loses its volume-weighted mean on
    both sides first.  ``score`` is not imported because it works on [n, width] numpy arrays read
    back from a VTM.  ``tests/test_iterate_error.py`` checks this function against it.  NaN where
    the reference field is identically zero.
    """
    n = int(volumes.numel())
    w = volumes.reshape(1, n) / volumes.sum()
    out = {}
    for name, offset, width in layout:
        ours = x[offset:offset + width * n].reshape(width, n)
        ref = reference[offset:offset + width * n].reshape(width, n)
        if name in mean_fields:
            ours = ours - (w * ours).sum(dim=1, keepdim=True)
            ref = ref - (w * ref).sum(dim=1, keepdim=True)
        numerator = float((w * (ours - ref).square()).sum().sqrt())
        denominator = float((w * ref.square()).sum().sqrt())
        out[name] = numerator / denominator if denominator > 0 else float("nan")
    return out


class IterateRecorder:
    """``--iterate-error``: every Newton iterate on the host, stamped on the Newton timer.

    ``observe(entry)`` is called from the progress hook.  It stamps ``entry["elapsed"]`` (the
    ``elapsed`` column of newton_residual.csv) and keeps the row for the checkpoint that
    ``solve_newton`` calls right after it.  ``checkpoint_for(rung, multiplier)`` returns that
    ``checkpoint(iteration, x)`` callable for one rung.  ``new_ramp(x_start)`` marks the start
    of a continuation ladder (the first one, an AUTO-RAMP retry, a CBBM pass), whose start
    state becomes step 0 of rung 1 when that rung's first history row arrives.  ``None`` means
    ``solve_newton``'s own zero start.

    ``elapsed`` is ``clock() - start`` minus the seconds spent here copying iterates to the host,
    so the time-to-accuracy curve is the solver's own.  The copies are synchronous, but by then
    the line search has already drained the device (its ``float(norm)``), so they cost only the
    transfer.  The total is ``overhead``, logged.  Memory is 8 bytes per unknown per step on the
    host: about 28 MB per step on LDC96 (3.5M unknowns).
    """

    def __init__(self, start: float, clock=time.time):
        self.start, self.clock = float(start), clock
        self.overhead = 0.0
        self.rows, self.states = [], []
        self.ramp, self.rung, self.multiplier = 0, 0, 1.0
        self._pending = False         # a new ramp started and its it=0 row has not arrived yet
        self._pending_state = None    # that ramp's start state (None: the zero start)
        self._last = None

    def elapsed(self) -> float:
        return self.clock() - self.start - self.overhead

    def _keep(self, x):
        began = self.clock()
        state = x.detach().to("cpu", torch.float64, copy=True)
        self.overhead += self.clock() - began
        return state

    def new_ramp(self, x_start) -> None:
        self.ramp += 1
        self._pending = True
        self._pending_state = None if x_start is None else self._keep(x_start)

    def checkpoint_for(self, rung: int, multiplier: float):
        self.rung, self.multiplier = int(rung), float(multiplier)
        return self.record

    def observe(self, entry) -> None:
        entry["elapsed"] = self.elapsed()
        self._last = entry
        if entry.get("it") == 0 and self._pending:
            self._append(entry, 0, self._pending_state)
            self._pending, self._pending_state = False, None

    def record(self, iteration, x) -> None:
        self._append(self._last, iteration, self._keep(x))

    def _append(self, entry, iteration, state) -> None:
        entry = entry if entry is not None else {}
        # Read the clock ONLY when the row carries no stamp.  The spelling
        # ``entry.get("elapsed", self.elapsed())`` evaluated its default on every row: one extra clock
        # reading per recorded iterate, which the scripted clocks of tests/test_iterate_error.py
        # (the feas25 phase-0 gate) run out of, so the gate failed and the flag was never passed.
        elapsed = entry["elapsed"] if "elapsed" in entry else self.elapsed()
        self.rows.append({"ramp": self.ramp, "rung": self.rung, "nu_multiplier": self.multiplier,
                          "it": int(iteration), "elapsed": elapsed,
                          "max_abs": entry.get("max_abs", ""), "l2": entry.get("l2", "")})
        self.states.append(state)

    def errors(self, final, layout, volumes, mean_fields=()) -> list:
        """One row per recorded iterate: ``rel_l2_<field>`` against ``final`` and their ``rel_l2_max``."""
        reference = final.detach().to("cpu", torch.float64).reshape(-1)
        weights = volumes.detach().to("cpu", torch.float64).reshape(-1)
        # One weight per interior cell, or the field slices below are cut at the wrong offsets
        # -- a volume array that also covered boundary cells could still reshape
        # for U and score it against shifted cells without an error.
        expected = sum(width for _, _, width in layout) * weights.numel()
        if reference.numel() != expected:
            raise ValueError(f"{weights.numel()} cell volumes and the layout {layout} need {expected} "
                             f"unknowns; the final iterate has {reference.numel()}")
        out = []
        for row, state in zip(self.rows, self.states):
            x = torch.zeros_like(reference) if state is None else state.reshape(-1)
            errors = field_relative_errors(x, reference, layout, weights, mean_fields)
            finite = [value for value in errors.values() if math.isfinite(value)]
            out.append({**row, **{f"rel_l2_{name}": value for name, value in errors.items()},
                        "rel_l2_max": max(finite) if finite else float("nan")})
        return out


def time_to_accuracy(rows, levels=ITERATE_ERROR_LEVELS) -> dict:
    """``{eps: first row with rel_l2_max <= eps, or None}``, rows in recorded (time) order."""
    out = {}
    for eps in levels:
        hit = next((row for row in rows if row["rel_l2_max"] <= eps), None)
        out[eps] = None if hit is None else {key: hit[key] for key in
                                             ("elapsed", "ramp", "rung", "it", "rel_l2_max")}
    return out


def write_iterate_error(rows, directory, meta) -> str:
    """``iterate_error.csv`` (one row per iterate) and ``iterate_error.json`` (``meta`` + time to each level)."""
    path = os.path.join(directory, "iterate_error.csv")
    base = ["ramp", "rung", "nu_multiplier", "it", "elapsed", "max_abs", "l2"]
    fields = base + [key for key in (rows[0] if rows else {}) if key not in base]
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    reached = time_to_accuracy(rows)
    with open(os.path.join(directory, "iterate_error.json"), "w") as handle:
        json.dump({**meta, "iterates": len(rows),
                   "time_to_accuracy": {f"{eps:g}": hit for eps, hit in reached.items()}},
                  handle, indent=1)
    return path


def report_iterate_error(recorder, solver, x, logger, *, reached: float, gate: float,
                         complete: bool, reference=None, reference_path: str | None = None) -> None:
    """Score every recorded iterate against the final ``x`` and write the two files.

    ``reference`` (opt-in, ``--iterate-error-ref``): score against this
    state -- a root certified by another run -- instead of the final iterate, so that a run which does not certify
    still has an error history.  ``None`` (the default) is every call before, row for row.
    """
    layout = unknown_layout(solver)
    mean_fields = gauged_fields(solver, layout)
    rows = recorder.errors(x if reference is None else reference, layout, solver.solver.cell_volume, mean_fields)
    certified = bool(reached < gate and complete)
    path = write_iterate_error(rows, logger.saving_path, {
        "reference": "final iterate" if reference is None else "state %s" % reference_path,
        "reference_max_abs": reached, "gate": gate,
        "certified": certified, "fields": [name for name, _, _ in layout],
        "mean_removed_fields": sorted(mean_fields),
        "instrumentation_seconds": recorder.overhead,
        # Unix time at which 'elapsed' is zero, so a process-wall time-to-accuracy can be derived
        # from the launcher's own start stamp (feasibility plan 2.4 reports both).
        "newton_timer_started_unix": recorder.start})
    logger.info("iterate error (volume-weighted rel L2 vs the %s, %s mean-removed) of %d "
                "iterates written to %s; host copies took %.3f s and are subtracted from 'elapsed'%s",
                "final iterate" if reference is None else "reference state %s" % reference_path,
                ", ".join(sorted(mean_fields)) or "no field", len(rows), path, recorder.overhead,
                "" if certified or reference is not None else
                "  <-- the final iterate is NOT certified, so neither is this reference")
    for eps, hit in time_to_accuracy(rows).items():
        logger.info("  max-field error <= %.0e %s", eps,
                    "never" if hit is None else
                    "first at %.2f s (ramp %d, rung %d, step %d: %.3e)" % (
                        hit["elapsed"], hit["ramp"], hit["rung"], hit["it"], hit["rel_l2_max"]))


def compare_roots(solver, x, paths, *, reached: float, gate: float, certified: bool, directory, log) -> dict:
    """``--compare-root``: the final state against saved states of the same mesh, the entry's same-root rule.

    Per reference: ``field_relative_errors`` (volume-weighted relative L2 per field, the free pressure level
    mean-removed -- the ``--iterate-error`` convention, identical to experiments/NaturalConvection/CoupledNewton/InitialGuess/diagnostics/
    same_root.compare_states), the largest of them, ``same_root`` = largest <= ``SAME_ROOT_TOL``, and the
    reference's own ``max|r|`` on THIS case (so an uncertified reference cannot pass for a root).  A reference
    of another size is reported as such, never compared.  Nothing of the solve moves."""
    layout = unknown_layout(solver)
    mean_fields = gauged_fields(solver, layout)
    volumes = solver.solver.cell_volume
    out = {"state_max_abs": reached, "gate": gate, "state_certified": certified, "tolerance": SAME_ROOT_TOL,
           "mean_removed_fields": sorted(mean_fields), "references": []}
    for path in paths:
        item = {"path": os.path.abspath(path)}
        try:
            reference = load_state(path, x.device, x.dtype)
        except Exception as failure:                    # an instrument must not end the run
            item["error"] = repr(failure)
            out["references"].append(item)
            log("--compare-root %s: unreadable (%s)", path, failure)
            continue
        if reference.numel() != x.numel():
            item["error"] = f"{reference.numel()} unknowns, this case has {x.numel()}: another mesh"
        else:
            errors = field_relative_errors(x.detach().reshape(-1), reference, layout,
                                           volumes.detach().reshape(-1), mean_fields)
            finite = [value for value in errors.values() if math.isfinite(value)]
            worst = max(finite) if finite else float("nan")
            ref_max = float(solver.residual(reference).abs().max())
            item.update(fields=errors, max=worst, same_root=bool(worst <= SAME_ROOT_TOL),
                        reference_max_abs=ref_max, reference_certified=bool(ref_max < gate))
        out["references"].append(item)
        log("--compare-root %s: %s", path, item.get("error") or (
            "rel L2 %s -> max %.3e: %s (reference max|r| %.3e, %s; this state max|r| %.3e, %s)" % (
                ", ".join(f"{k} {v:.3e}" for k, v in item["fields"].items()), item["max"],
                "SAME ROOT" if item["same_root"] else "NOT the same root", item["reference_max_abs"],
                "certified" if item["reference_certified"] else "NOT certified", reached,
                "certified" if certified else "NOT certified")))
    with open(os.path.join(directory, "compare_root.json"), "w") as handle:
        json.dump(out, handle, indent=1)
    return out


def cached_amg_plan(cache: dict, A, fresh: bool, build):
    """``(plan, reason)``: the cached coupled-AMG plan if it was built on exactly ``A``'s block
    pattern and ``fresh`` is False (``reason`` None), else ``build(A)``, stored in ``cache``
    (``reason`` "fresh", "first" or "pattern").

    The key is the block pattern, not the block count
    ``nnzb`` alone.  ``CoupledAMGPlan.numeric`` lays new values on the PLAN's ``(row, col)``, so a
    pattern with the same count but different blocks gave a silently wrong hierarchy.
    ``jacobian.assemble`` keeps only ``value != 0`` entries, so the pattern moves between Newton
    steps.  An LDC96 reuse run (``ldc96-Re1000/logs/newton-e3-learned-reuse4.log``,
    final rung, step 5) reused the plan.  Its stability check read a Jacobi rate of 4.9e58,
    against 0.94 one linearisation earlier at max|r| 4.2e-9.  GMRES then spent 1280 iterations
    and the run missed the 1e-10 gate.  The feasibility plan explains this as a pattern change at
    an equal block count, and the README's negative verdict on ``--camg-net-every 4`` rests on
    this run.  ``ThetaReuse`` had the same key and gets the same fix
    (``coupled_amg_net.same_block_pattern``).
    """
    from Pipeline.CoupledNewton.block_pattern import same_block_pattern

    plan = cache.get("plan")
    if fresh:
        reason = "fresh"
    elif plan is None:
        reason = "first"
    elif not (plan.levels[0].n == A.shape[0]
              and same_block_pattern(plan.levels[0].row, plan.levels[0].col, A.row, A.col)):
        reason = "pattern"
    else:
        return plan, None
    # The plan being replaced is dropped BEFORE the build: its SpGEMM index plans
    # and fast-kernel patterns are several GiB at 3.4M unknowns, and nothing reads them again (the caller's previous
    # preconditioner, which also held it, is released by solve_newton before it calls the factory).
    plan = None
    cache.pop("plan", None)
    plan = build(A)
    cache["plan"] = plan
    return plan, reason


def classical_pattern_kwargs(plan_kw: dict) -> dict:
    """A structural configuration's plan keywords as the classical solver C builds the same plan
    -- a one-slot ``level_plan`` that repeats the plan's own aggregation and smoothing dropped (the same hierarchy,
    ``test_level_plan_reproduces_plans``) and no device setup (C's structural plan is a host plan) -- so a learned choice
    of the PG point reads C's plan-cache entry and builds C's hierarchy."""
    kw = dict(plan_kw)
    lp = kw.get("level_plan")
    if lp and len(lp) == 1 and lp[0].get("aggregation") == kw.get("aggregation") \
            and bool(lp[0].get("smooth", kw.get("smooth_prolongation"))) == bool(kw.get("smooth_prolongation")):
        kw["level_plan"] = None
    kw["gpu_setup"] = False
    return kw


class CamgPlanReuse:
    """``--camg-reuse-plan``: coupled-AMG plans reused across Newton linearisations.

    Design: ``docs/research/2026-10-04-gpu-vs-openfoam-feasibility.md``.  Without it the camg factory builds a
    whole ``CoupledAMGPlan`` (strengths, host colouring, pyamg aggregation, SpGEMM index plans, transposes, and a
    classical numeric pass the hierarchy then repeats) at EVERY linearisation, and once more for each lazily built
    hand-over / rescue cycle, although the block pattern rarely changes.  Here one plan per plan setting
    (``block_amg.plan_key``: the learned arm and the classical hand-over arm share one when their settings agree)
    is built at the first linearisation of a rung and reused while the block pattern is identical
    (``cached_amg_plan``, ``coupled_amg_net.same_block_pattern``); a reuse recomputes only the numeric phase --
    level matrices, Galerkin products, smoother inverses and ``omega`` (``CoupledAMGPlan.numeric(refresh_omega=True)``,
    once per new matrix, keyed on the identity of its value tensor through a weak reference, so no old Jacobian is
    kept alive).  A rebuild happens at every rung start (``new_rung``; the strength-based aggregation is frozen per
    rung), on a changed block pattern, and every ``refresh_every`` linearisations when that is > 0.

    Convergence risk of the frozen aggregation: with the camg entry's ``theta = 0`` the aggregates and colourings
    are functions of the block pattern (``CoupledAMGPlan`` docstring), so a reused plan is the one a fresh build
    makes unless a coarse Galerkin block is exactly zero at one linearisation and not at the other; the residual
    certificate does not depend on the preconditioner either way (GMRES and the Newton gate read the true
    residual).  ``refresh_every`` bounds the drift for a plan that would depend on values.

    ``structural=True`` (``--camg-structural-plan``; default False = all of the
    above): the plans are value-free STRUCTURAL plans (``block_amg.CoupledAMGPlan(strength="pattern")``) --
    functions of the block pattern alone, which ``--fixed-jacobian-pattern structural`` / ``blocks`` keeps fixed for the
    whole run -- so a rung start does not drop them: one plan per plan setting per RUN, every later linearisation a
    numeric-only pass (omega always recomputed).  ``plan_cache`` (``--camg-plan-cache DIR``): their aggregation and
    colouring decisions are read from / written to a per-pattern entry (``amg_plan_cache``), so a run on a known mesh
    makes no host aggregation or colouring at all.  A changed block pattern still rebuilds ("pattern").
    """

    def __init__(self, refresh_every: int = 0, structural: bool = False, plan_cache=None, log=None, pattern_cache=None):
        """``pattern_cache`` (--learned-action camgcfg: with --camg-plan-cache): a decision cache
        for the structural (``strength="pattern"``) plans among value-based ones -- a configuration that chooses the
        classical PG point gets C's structural plan from C's cache entry (``classical_pattern_kwargs``) and keeps it
        for the run, as C does, while value-based plans are rebuilt per rung as without it."""
        if int(refresh_every) < 0:
            raise ValueError(f"--camg-plan-refresh must be >= 0, not {refresh_every}")
        self.refresh_every = int(refresh_every)
        self.entries = {}           # plan key -> {"plan", "built" (linearisation), "omega_for" (weakref to values)}
        self.seen = set()           # plan keys built at least once (log: "first" vs "rung")
        self.linearisation = 0      # factory calls so far (assemblies and PTC re-arms)
        self.rungs = 0
        self.built = {"first": 0, "rung": 0, "pattern": 0, "refresh": 0}
        self.reused = 0             # numeric-only builds on a plan from an earlier matrix
        # Structural plans kept for the run, their decisions optionally cached per pattern
        self.structural = bool(structural)
        self.plan_cache = plan_cache
        self.log = log
        self.cache_events = []      # one dict per structural plan build: source (hit / miss / rejected / no cache), s
        self.setup_log = []         # M2: per build, synced plan / numeric + fill seconds (camg factory, timed runs)
        if plan_cache is not None and not self.structural:
            raise ValueError("--camg-plan-cache needs --camg-structural-plan")
        self.pattern_cache = pattern_cache
        if pattern_cache is not None and self.structural:
            raise ValueError("pattern_cache is for value-based runs; a structural run has plan_cache")

    def new_rung(self) -> None:
        """The next linearisation is the first of a rung: drop every plan (it is rebuilt on its next request).
        M2a structural plans are value-free and the pattern is fixed: they are kept (one plan per run)."""
        self.rungs += 1
        if self.structural:
            # the run's first plan (the primary cycle's) is kept; a lazily built hand-over / rescue arm's plan goes, as
            # it did at every rung start before (memory), and is rebuilt -- from the decision cache -- if asked again
            first = next(iter(self.entries), None)
            for key in [k for k in self.entries if k != first]:
                del self.entries[key]
            return
        if self.pattern_cache is not None:
            # O1-C C4: the structural plans stay (value-free, the pattern is fixed), value-based ones are rebuilt
            for key in [k for k in self.entries if dict(k[0]).get("strength") != "pattern"]:
                del self.entries[key]
            return
        self.entries.clear()

    def new_linearisation(self) -> None:
        self.linearisation += 1

    def release(self) -> str:
        """Drop every cached plan once the solves are over and return the final ``summary``.

        Without the flag the last plan dies with the last preconditioner when ``solve_newton`` returns; the cache
        is referenced from ``main`` and would hold its plans (several GB of index plans at LDC96 scale) on the GPU
        through the post-processing -- the export and ``--grad-check``'s adjoint solve."""
        self.entries.clear()
        return self.summary()

    def plan_for(self, A, cycle_kw: dict, build=None):
        """``(plan, reason, refresh_omega)`` for ``block_amg.CoupledAMG(A, **cycle_kw)``.

        ``reason`` is None for a cached plan, else "first", "rung", "pattern" or "refresh"; ``refresh_omega`` is
        True when the plan's ``omega`` belongs to another matrix than ``A``.  ``build(A)`` defaults to the plan
        ``CoupledAMG`` would build itself (``block_amg.plan_kwargs``)."""
        import weakref

        from Pipeline.CoupledNewton import block_amg
        # the block size is part of the key: the k-omega split builds a mean-flow cycle (F = 4) and a
        # turbulence cycle (F = 2) on the SAME cell graph, so the block pattern alone handed the second the first's plan
        key = (block_amg.plan_key(**cycle_kw), int(A.F))
        entry = self.entries.setdefault(key, {})
        pattern = self.pattern_cache is not None and cycle_kw.get("strength") == "pattern"
        if build is None and (self.structural or pattern):
            # The value-free structural plan, its decisions from / to the per-pattern cache; or
            # a configuration's classical PG point in a value-based run, on C's own plan keywords and cache entry
            from Pipeline.CoupledNewton.amg_plan_cache import structural_plan
            plan_kw = block_amg.plan_kwargs(**cycle_kw)
            if pattern:
                plan_kw = classical_pattern_kwargs(plan_kw)

            def build(A):
                plan, info = structural_plan(A, plan_kw, self.plan_cache if self.structural else self.pattern_cache,
                                             self.log)
                self.cache_events.append(info)
                return plan
        if build is None:
            build = lambda A: block_amg.CoupledAMGPlan(A, **block_amg.plan_kwargs(**cycle_kw))
        due = (self.refresh_every > 0 and "plan" in entry
               and self.linearisation - entry["built"] >= self.refresh_every)
        plan, reason = cached_amg_plan(entry, A, due, build)
        if reason is not None:
            reason = {"fresh": "refresh", "first": "rung" if key in self.seen else "first"}.get(reason, reason)
            self.built[reason] += 1
            self.seen.add(key)
            entry["built"] = self.linearisation
            entry["omega_for"] = weakref.ref(A.val)
            return plan, reason, False
        refresh = entry["omega_for"]() is not A.val
        if refresh:
            self.reused += 1
            entry["omega_for"] = weakref.ref(A.val)
        return plan, None, refresh

    def summary(self) -> str:
        text = "%d plans built (%s), %d numeric-only reuses" % (
            sum(self.built.values()), ", ".join(f"{k} {v}" for k, v in self.built.items() if v), self.reused)
        if self.structural:
            # Where each structural plan's decisions came from, and the seconds of its builds
            sources = {}
            for e in self.cache_events:
                sources[e["source"]] = sources.get(e["source"], 0) + 1
            text += "; structural plans: %s, %.2f s (decisions %s)" % (
                ", ".join(f"{k} {v}" for k, v in sources.items()) or "none",
                sum(e.get("build_s", 0.0) + e.get("key_s", 0.0) for e in self.cache_events),
                ", ".join("%s %.2f s" % (k, sum(e.get("setup_seconds", {}).get(k, 0.0) for e in self.cache_events))
                          for k in ("strength", "colouring", "aggregation", "symbolic", "checks")))
        return text

    def setup_summary(self) -> str | None:
        """The run's AMG setup time split, from the timed builds (None when none was timed)."""
        if not self.setup_log:
            return None
        plans = [e for e in self.setup_log if e.get("reason") is not None]
        parts = {}
        for e in self.setup_log:
            for k, v in (e.get("numeric") or {}).items():
                parts[k] = parts.get(k, 0.0) + v
        total_plan = sum(e.get("plan_s", 0.0) for e in self.setup_log)
        total_cycle = sum(e.get("cycle_s", 0.0) for e in self.setup_log)
        return ("%d builds: %d plans %.3f s, numeric + fill %.3f s, setup total %.3f s%s" % (
            len(self.setup_log), len(plans), total_plan, total_cycle, total_plan + total_cycle,
            (" (numeric parts: %s)" % ", ".join("%s %.3f" % item for item in parts.items())) if parts else ""))


def refuse_misused_camg_plan_flags(args) -> None:
    """``--camg-reuse-plan`` / ``--camg-plan-refresh`` on a run they cannot act on is refused,
    not ignored: a timing row labelled "plan reused" must be a run whose plan was reused.  Called after the
    buoyant recipe, which may have chosen ``--learned-action camg`` itself."""
    camg = args.learned_action is not None and args.learned_action.startswith(("camg", "camgnet:"))
    if (args.camg_reuse_plan or args.camg_plan_refresh) and not camg:
        raise ValueError("--camg-reuse-plan / --camg-plan-refresh act on the coupled AMG only "
                         "(--learned-action camg* or camgnet:<model>)")
    if args.camg_plan_refresh < 0:
        raise ValueError(f"--camg-plan-refresh must be >= 0, not {args.camg_plan_refresh}")
    if args.camg_plan_refresh and not args.camg_reuse_plan:
        raise ValueError("--camg-plan-refresh needs --camg-reuse-plan")
    # Same rule -- a timing row labelled "fast kernels" must be a coupled-AMG run
    if getattr(args, "camg_fast_kernels", False) and not camg:
        raise ValueError("--camg-fast-kernels acts on the coupled AMG only (--learned-action camg* or "
                         "camgnet:<model>)")
    # The graph replays the fast cycle, so it needs the fast kernels (and so the coupled AMG)
    if getattr(args, "camg_cuda_graph", False) and not getattr(args, "camg_fast_kernels", False):
        raise ValueError("--camg-cuda-graph replays the fast V-cycle: it needs --camg-fast-kernels")
    # The per-rung refresh acts on a reused lam network only
    if getattr(args, "camg_lam_reuse_rung", False) and not (getattr(args, "camg_lam", None)
                                                            and int(getattr(args, "camg_lam_reuse", 1)) > 1):
        raise ValueError("--camg-lam-reuse-rung needs --camg-lam with --camg-lam-reuse R > 1")
    # The kept W is the W of a reused camgnet network
    if getattr(args, "camg_keep_w", False) and not (str(args.learned_action or "").startswith("camgnet:")
                                                    and int(getattr(args, "camg_net_every", 1)) > 1):
        raise ValueError("--camg-keep-w needs --learned-action camgnet:<model> with --camg-net-every R > 1")
    # The refresh rule acts on a reused camgnet network's cached output, one reuse rule at a time
    refresh = getattr(args, "camg_theta_refresh", None)
    if refresh is not None:
        if not (str(args.learned_action or "").startswith("camgnet:") and int(getattr(args, "camg_net_every", 1)) > 1):
            raise ValueError("--camg-theta-refresh needs --learned-action camgnet:<model> with --camg-net-every R > 1")
        if not (math.isfinite(float(refresh)) and float(refresh) > 1.0):
            raise ValueError(f"--camg-theta-refresh G needs a finite G > 1, not {refresh!r}")
        if getattr(args, "camg_keep_w", False):
            raise ValueError("--camg-theta-refresh is not combined with --camg-keep-w (a kept W is not rebuilt from "
                             "the cached output, so the solve after it does not judge that output)")
    # The order-1 switch needs a learned order-1 form to switch to
    budget = int(getattr(args, "order1_switch", 0) or 0)
    if budget < 0:
        raise ValueError(f"--order1-switch N needs N >= 0 (0 = off), not {budget}")
    if budget:
        from Pipeline.CoupledNewton.order1_switch import has_learned_order1
        if not has_learned_order1(args):
            raise ValueError("--order1-switch switches to a learned order-1 form: give --camg-lam or --learned-action "
                             "camgnet:<model>")
        if str(args.learned_action).startswith("camgnet:") and not str(args.order1_classical).startswith("camg"):
            raise ValueError("--order1-classical must be a classical camg action (camg, camg2, camg2w, ...)")
    if getattr(args, "order1_switch_cap", False) and not budget:
        raise ValueError("--order1-switch-cap caps the classical solves of an armed order-1 switch: it needs "
                         "--order1-switch N")
    rel = float(getattr(args, "order1_switch_rel", 0.0) or 0.0)
    if rel < 0 or (rel > 0 and not budget) or (getattr(args, "order1_keep", "any") != "any" and not budget):
        raise ValueError("--order1-switch-rel K (K >= 0) and --order1-keep evidence act on the order-1 switch: they "
                         "need --order1-switch N")
    if getattr(args, "order1_alternate", None) and not (budget and getattr(args, "order1_keep", "any") != "any"
                                                        and getattr(args, "camg_lam", None)):
        raise ValueError("--order1-alternate MODEL is a further lam model the evidence rule chooses: it needs "
                         "--order1-switch N, --order1-keep evidence and --camg-lam")
    if getattr(args, "order1_alternate_deep", None) and not (budget and getattr(args, "order1_keep", "any") != "any"):
        raise ValueError("--order1-alternate-deep is an order-1 form the evidence rule chooses: it needs "
                         "--order1-switch N and --order1-keep evidence")
    if getattr(args, "order1_deep_first", False) and not getattr(args, "order1_alternate_deep", None):
        raise ValueError("--order1-deep-first reorders the --order1-alternate-deep form: give it")
    if getattr(args, "order1_alternate_config", None) and not (budget and getattr(args, "order1_keep", "any") != "any"):
        raise ValueError("--order1-alternate-config is an order-1 form the evidence rule chooses: it needs "
                         "--order1-switch N and --order1-keep evidence")
    if getattr(args, "order1_config_first", False) and not getattr(args, "order1_alternate_config", None):
        raise ValueError("--order1-config-first reorders the --order1-alternate-config form: give it")
    if getattr(args, "order1_config_first", False) and getattr(args, "order1_deep_first", False):
        raise ValueError("--order1-config-first and --order1-deep-first both name the first form: give one")


def elimination_from_args(args):
    """``--nonlinear-elimination``: the run's ``elimination.NonlinearElimination``, or ``None`` by
    default (``solve_newton`` then gets no ``elimination`` keyword: today's call).  Refused before the case loads,
    with a reason: a malformed spec or setting, and ``--ptc`` (a second globalisation)."""
    if args.nonlinear_elimination is None:
        return None
    from Pipeline.CoupledNewton.elimination import EliminationConfig, NonlinearElimination, parse_spec
    if args.ptc:
        raise ValueError("--nonlinear-elimination (B5) and --ptc are two globalisations; run one of them")
    q, layers = parse_spec(args.nonlinear_elimination)
    config = EliminationConfig(q=q, layers=layers, rule=args.ne_rule, halo=args.ne_halo, inner=args.ne_inner,
                               rtol=args.ne_rtol, refresh=args.ne_refresh, switch=args.ne_switch,
                               score=args.ne_score, max_cells=args.ne_max_cells, max_growth=args.ne_max_growth,
                               ordering=args.ne_ordering, nest_fraction=args.ne_nest_fraction,
                               backtracks=args.ne_backtracks)
    return NonlinearElimination(config)


def jfnk_policy_from_args(args):
    """``--operator`` / ``--precond-rebuild`` / ``--jvp`` (JFNK mode): the policy, or ``None`` by default.

    ``None`` -- both defaults -- means the run hands ``solve_newton`` none of the JFNK keywords, so it is today's
    call.  Otherwise the returned ``jfnk.PrecondPolicy`` is the ONE policy object of the run: it carries the kept
    preconditioner across Newton steps and continuation rungs (time steps in the transient entry).  Refused, before
    the case loads, with a reason: a malformed policy, ``--jvp forward`` where no JVP runs (the assembled operator
    under the default policy), and the flags that are another spelling of what the mode decides --
    ``--matrix-free`` (the old JVP operator), ``--freeze-precond`` (preconditioner reuse), ``--equilibrate`` (a
    scaling read off the assembled matrix, not measured stale).  None of these is set by the buoyant recipe, so
    both entries call this before the case loads."""
    from Pipeline.CoupledNewton.jfnk import PrecondPolicy
    policy = PrecondPolicy(args.precond_rebuild)          # parses, or raises with the reason
    jfnk = args.operator != "assembled" or not policy.rebuilds_every_step
    if not jfnk:
        if args.jvp != "tape":
            raise ValueError("--jvp chooses how the JFNK operator's product is computed; with --operator "
                             "assembled and --precond-rebuild every no JVP runs (use --operator jvp or auto)")
        return None
    refused = [flag for flag, given in (
        ("--matrix-free (the JVP operator is --operator jvp/auto here)", args.matrix_free),
        ("--freeze-precond (--precond-rebuild decides)", args.freeze_precond),
        ("--equilibrate", args.equilibrate != "off"),
        ("--reuse-across-steps (the policy carries the preconditioner across steps)",
         bool(getattr(args, "reuse_across_steps", False)))) if given]
    if refused:
        raise ValueError("JFNK mode (--operator / --precond-rebuild) refuses " + "; ".join(refused))
    return policy


def jfnk_solve_kwargs(args, policy) -> dict:
    """The ``solve_newton`` keywords of the JFNK mode; ``{}`` without it (today's call)."""
    if policy is None:
        return {}
    return {"krylov_operator": args.operator, "precond_policy": policy, "jvp": args.jvp}


def keep_identity(amg, A, plan, kw):
    """``--krylov-audit-identity``: keep on the hierarchy what its rebuild needs -- a copy of the block values it was
    built from, its plan and its keywords -- and the exact checksums of its stored values (``krylov_audit``); returns
    ``amg``, unchanged otherwise."""
    from Pipeline.CoupledNewton import block_amg as _bam, krylov_audit as _ka
    if _ka.KRYLOV_AUDIT.get("identity") and getattr(A, "val", None) is not None:
        amg.identity_args = (_bam.BlockCSR(A.row, A.col, A.val.detach().clone(), A.shape), plan, dict(kw))
        amg.identity_checksums = _ka.state_checksums(amg)
    return amg


def log_sweep_guard(amg, logger):
    """``--camg-sweep-guard``: one log line for a build in which a level's sweep was damped (its measured
    growth, the damping it now has, the halvings and the growth after them); returns ``amg``."""
    damped = [r for r in (getattr(amg, "sweep_guard", None) or []) if r.get("halvings")]
    if damped:
        logger.info("camg sweep guard: %s", "; ".join(
            "level %d growth %.3g -> damping %.3g (%d halvings, growth %.3g)"
            % (r["level"], r["growth"], r["omega"], r["halvings"], r.get("growth_after", float("nan"))) for r in damped))
    return amg


def first_rung_policy(args, rung: int, keywords: dict) -> dict:
    """``--precond-rebuild-first-rung POLICY``: the first continuation rung (rungs count from 1) is solved under its own
    rebuild policy, a fresh object, so that nothing it keeps reaches the next rung, whose start the run's policy rebuilds
    at.  ``keywords`` (the rung's ``solve_newton`` keywords) is changed in place and returned; it is left as it is on
    every other rung, without the flag, and without the JFNK mode (no policy to replace)."""
    if getattr(args, "precond_rebuild_first_rung", None) and rung == 1 and "precond_policy" in keywords:
        from Pipeline.CoupledNewton.jfnk import PrecondPolicy
        keywords["precond_policy"] = PrecondPolicy(args.precond_rebuild_first_rung)
    return keywords


def log_jfnk_mode(args, policy, solver, logger) -> None:
    """The mode's banner, and what it does not read."""
    if policy is None:
        return
    logger.info("JFNK mode: Krylov operator %s%s, preconditioner rebuilt by policy %r (and at the first step, "
                "a refused step on a kept preconditioner, a PTC arming / handover / cutback); a step that keeps "
                "it %s", args.operator,
                "" if args.operator == "assembled" else f" (exact JVP by {args.jvp})", policy.spec,
                "still assembles (the operator is the assembled matrix)" if args.operator == "assembled"
                else "assembles nothing")
    if args.reassemble_drift != 0.0:
        logger.info("JFNK mode: --reassemble-drift %g is not used (the operator is exact at every Newton step; "
                    "the preconditioner's reuse is the policy's)", args.reassemble_drift)
    if solver.mwi and solver.solver._mwi_frozen_coefficient:
        logger.warning("JFNK mode with the frozen d_f coefficient: the 'exact' operator is the frozen "
                       "linearisation, not the Jacobian of the MWI residual (pass --coefficient implicit)")


def _o1_clock(device):
    """``time.time()`` after the device's queued work with ``LPCNS_O1_TIMERS=1`` (profile runs: every mark is a device
    sync), else ``None`` -- a timed run gets no sync from it."""
    if os.environ.get("LPCNS_O1_TIMERS") != "1":
        return None
    if torch.device(device).type == "cuda":
        torch.cuda.synchronize(device)
    return time.time()


def _blocks_on_device(A):
    """``A`` with its values present: ``--camg-drop-block-values`` releases the camg factory's fine
    values once the hierarchy holds their scalar copy, and leaves ``A.rebuild_values`` (the hierarchy's
    ``fine_block_values``) to rebuild them; a lazily built rescue arm starts from them.  The very object when the values
    are there (every run without the flag)."""
    if A.val is not None:
        return A
    from Pipeline.CoupledNewton import block_amg
    return block_amg.BlockCSR(A.row, A.col, A.rebuild_values(), A.shape)


def camg_fast_kernel_kwargs(args, kw: dict) -> dict:
    """The ``block_amg.CoupledAMG`` keywords of one camg factory cycle with ``--camg-fast-kernels``:
    ``kw`` plus ``fast_kernels=True`` and ``colour_coarsest=False`` (the fast cycle never reads the coarsest
    level's colouring; on a passed plan the plan decides).  Without the flag ``kw`` itself is returned,
    so every cycle is built by exactly the call it was built by before."""
    if not getattr(args, "camg_fast_kernels", False):
        return kw
    # The memory keywords, each only when its flag is given (block_amg.CoupledAMG
    # lean / drop_block_values / int32_groups); without them the dict below is the one it was
    m5 = {key: True for key, flag in (("lean", "camg_lean_build"), ("drop_block_values", "camg_drop_block_values"),
                                      ("int32_groups", "camg_int32_groups")) if getattr(args, flag, False)}
    if getattr(args, "camg_cuda_graph", False):
        # The fast cycle replayed from a CUDA graph (block_amg.CoupledAMG._graphed)
        return {**kw, "fast_kernels": True, "colour_coarsest": False, "cuda_graph": True, **m5}
    return {**kw, "fast_kernels": True, "colour_coarsest": False, **m5}


def camg_m2_kwargs(args, kw: dict) -> dict:
    """``kw`` plus the AMG-setup plan keywords, each only when its flag is given:
    ``strength="pattern"`` (``--camg-structural-plan``), ``gpu_setup=True`` (``--camg-gpu-setup``),
    ``fused_numeric=True`` (``--camg-fused-numeric``).  Without the flags ``kw`` itself is returned."""
    m2 = {}
    if getattr(args, "camg_structural_plan", False):
        m2["strength"] = "pattern"
    if getattr(args, "camg_gpu_setup", False):
        m2["gpu_setup"] = True
    if getattr(args, "camg_fused_numeric", False):
        m2["fused_numeric"] = True
    if getattr(args, "camg_aggregation", "standard") != "standard":
        m2["aggregation"] = args.camg_aggregation
    return {**kw, **m2} if m2 else kw


def camg_coarse_max_default(args, kw: dict) -> dict:
    """``kw`` with ``coarse_max`` from ``--camg-coarse-max`` when the flag is given and ``kw`` names no coarsest
    size of its own: the cycles not built from the entry's ``camg_kw`` (the classical arm of the learned cycle's
    selection, the classical cycle of the turbulence split) then sit on the same hierarchy as the others.  A cycle
    that names one (``camg_kw``: the flag's value, or a learned model's recorded hierarchy) keeps it."""
    value = getattr(args, "camg_coarse_max", None)
    return kw if value is None or "coarse_max" in kw else {**kw, "coarse_max": int(value)}

def camg_m3_kwargs(args, kw: dict) -> dict:
    """``kw`` plus the V-cycle keywords, only when ``--camg-block-kernels`` is given:
    ``block_kernels=True`` and ``block_values`` (``--camg-block-values``), and ``block_w=True`` with ``--camg-block-w``
    plus ``stab_fast=True`` with ``--camg-stab-fast``.  Without the flags ``kw`` itself is
    returned."""
    extra = {"stab_fast": True} if getattr(args, "camg_stab_fast", False) else {}
    if not getattr(args, "camg_block_kernels", False):
        return {**kw, **extra} if extra else kw
    if getattr(args, "camg_block_w", False):
        extra["block_w"] = True
    return {**kw, "block_kernels": True, "block_values": getattr(args, "camg_block_values", "fp64"), **extra}


def gradient_report(solver, x, nu, logger, steps=(3e-2, 1e-2, 3e-3, 1e-3,
                                                   3e-4, 1e-4)) -> None:
    """``dL/dnu`` by the implicit function theorem, against a central difference.

    The finite difference is SWEPT rather than taken at one step size.  A single
    ``h`` cannot tell an agreeing gradient from a coincidence: the difference
    quotient carries an O(h^2) truncation error at large ``h`` and an O(eps/h)
    cancellation error at small ``h``, so only a PLATEAU -- a run of step sizes
    over which the quotient stops moving -- says the number it agrees with is
    the derivative.  Each arm re-solves the case from the base solution, and its
    own ``max|r|`` is reported next to it, because a finite difference is only
    as good as the two solves it differences.
    """
    weight = torch.linspace(-1.0, 1.0, solver.n_unknowns, dtype=torch.float64,
                            device=solver.device)
    lo, hi = solver.velocity, solver.pressure

    def loss(state):
        # The pressure part enters through its DEVIATION from the mean, so a
        # gauge-free case does not make dL/dnu a function of an unconstrained
        # level.  Every other block, temperature included, enters directly; the
        # trailing term is an empty sum on a case with no block past the
        # pressure, which keeps the isothermal number bit-identical.
        pressure = state[lo:hi]
        return ((weight[:lo] * state[:lo]).sum()
                + (weight[lo:hi] * (pressure - pressure.mean())).sum()
                + (weight[hi:] * state[hi:]).sum())

    original = solver.solver.nu_coeff

    def bind(value):
        solver.solver.nu_coeff = torch.ones_like(original) * value

    parameter = torch.tensor(nu, dtype=torch.float64, device=solver.device,
                             requires_grad=True)
    try:
        telemetry = {}
        started = time.time()
        loss(solver.differentiable_solution(
            x, parameter, bind=bind, report=telemetry.update)).backward()
        adjoint_seconds = time.time() - started
        adjoint = float(parameter.grad)
        logger.info(
            "dL/dnu = %.12e by one transposed solve: %d GMRES iterations to "
            "an achieved %.2e (requested %.1e), %.2f s",
            adjoint, telemetry.get("iterations", -1),
            telemetry.get("achieved_rel_residual", float("nan")),
            telemetry.get("requested_rtol", float("nan")), adjoint_seconds)
        logger.info("%10s %24s %14s %14s", "h/nu", "central difference",
                    "rel gap", "worst max|r|")
        for relative in steps:
            step = relative * nu
            values, worst = [], 0.0
            for sign in (+1, -1):
                bind(torch.full_like(original, nu + sign * step))
                state, history = solver.solve_newton(x0=x)
                worst = max(worst, history[-1]["max_abs"])
                values.append(float(loss(state)))
            difference = (values[0] - values[1]) / (2.0 * step)
            logger.info("%10.1e %24.12e %14.3e %14.3e", relative, difference,
                        abs(difference - adjoint) / max(abs(adjoint), 1e-300),
                        worst)
    finally:
        solver.solver.nu_coeff = original


def build_preconditioner_factory(args, solver, case, device, logger, cold, plan_reuse=None):
    """``(learned_factory, learned_model, camg_plan_reuse)``: the inner preconditioner ``--learned-action`` selects.

    A function of its own, not part of ``main``, so the transient entry
    (``solve_transient_with_coupled_newton.py``) builds the same camg / camg2w / camgnet
    (race, contraction or budget selection, rescue, on-line adaptation), ilu / lu and
    LocalActionNet factories from the same flags instead of a second copy of them.
    ``learned_factory`` is ``None`` when no ``--learned-action`` is given, which hands
    ``solve_newton`` its shipped block preconditioner.  ``learned_model``
    is the LocalActionNet the ``--probe-policy`` wrapper in ``main`` reads (``None`` on
    every other path; before the move that wrapper raised NameError there instead).
    ``cold`` is the zero state ``main`` resolves the gauge on, read only by the
    ``--lu-pin`` log line.  Nothing in the body changed.

    ``plan_reuse`` (--order1-switch): an existing ``CamgPlanReuse`` to share instead of creating one (the
    switch's classical and learned factories then share the plan cache).

    ``camg_plan_reuse`` is the
    ``CamgPlanReuse`` of ``--camg-reuse-plan``, or ``None`` without the flag.  The factory's
    closures read it; the caller owns its rung boundaries (``new_rung``: the steady entry at
    every ``solve_newton`` call, the transient entry never, so one plan spans the march) and
    its ``release`` once the solves are over.  The E2-E5 flags (``--camg-fast-kernels``,
    ``--camg-reuse-plan``, ``--camg-plan-refresh``) act inside the factory, so both entries
    build every coupled AMG through the same ``camg_cycle``.
    """
    if getattr(args, "krylov_orth_impl", None):
        # Opt-in: set here too so the transient and sweep entries (the steady parser, this factory)
        # see --krylov-orth-impl; the steady main sets it earlier and logs it
        from SparseSolver import gmres_warp as _gw
        _gw.BACKEND["impl"] = args.krylov_orth_impl
    learned_model = None
    learned_factory = None
    # --camg-reuse-plan: the camg factory's plan cache (CamgPlanReuse), set below and returned;
    # the steady entry's _e1_kwargs tells it where each rung starts
    camg_plan_reuse = None
    refuse_misused_camg_plan_flags(args)
    refuse_misused_m5_flags(args)        # after the buoyant recipe, which may choose the camg itself
    refuse_misused_m2_flags(args)        # likewise
    refuse_misused_m3_flags(args)        # likewise
    refuse_misused_camgcfg_flags(args)   # likewise
    if getattr(args, "save_first_correction", None):
        from Pipeline.CoupledNewton import newton as _nw
        _nw.STEP_DUMP.update(path=str(args.save_first_correction), done=False)
        logger.info("--save-first-correction: the first Newton correction goes to %s", args.save_first_correction)
    if (getattr(args, "krylov_audit", False) or getattr(args, "krylov_audit_levels", False)
            or getattr(args, "krylov_audit_identity", False)):
        from Pipeline.CoupledNewton import krylov_audit as _ka
        _ka.KRYLOV_AUDIT["on"] = True
        if getattr(args, "krylov_audit_identity", False):
            _ka.KRYLOV_AUDIT["identity"] = True
            logger.info("--krylov-audit-identity: every audited hierarchy is checked against its own build and a "
                        "rebuild from the matrix it was built from (key identity)")
        logger.info("--krylov-audit: every Newton step's Krylov solve read against its system ([krylov audit] rows)")
        if getattr(args, "krylov_audit_levels", False):
            _ka.KRYLOV_AUDIT["levels"] = True
            logger.info("--krylov-audit-levels: each row also reads every level's smoother and V-cycle contraction")
    if args.learned_action is not None and args.learned_action.startswith(("camg", "camgnet:")):
        # Classical COUPLED block AMG (Pipeline.CoupledNewton.block_amg) on the assembled
        # matrix: whole-cell aggregation, Petrov-Galerkin smoothed aggregation, multicolour
        # point-block Gauss-Seidel, one V-cycle per application, on the GPU.  "camg" = 1+1
        # sweeps; "camg2" = 2+2; "camg-t" uses R = P^T (the Galerkin variant that fails on
        # the buoyant Jacobians of experiments/MultiCase/CoupledNewton/TrueJacobianBench).
        from Pipeline.CoupledNewton import block_amg
        from Pipeline.CoupledNewton.contract import VELOCITY_WIDTH
        camg_spec = args.learned_action
        camg_kw = classical_camg_kw(camg_spec, args.camg_l1)
        if getattr(args, "camg_coarse_max", None) is not None:
            if int(args.camg_coarse_max) < 1:
                raise ValueError(f"--camg-coarse-max must be >= 1, not {args.camg_coarse_max}")
            camg_kw["coarse_max"] = int(args.camg_coarse_max)
        if getattr(args, "camg_unsmoothed_p", False):
            camg_kw["smooth_prolongation"] = False          # tentative (piecewise-constant) prolongation
        if getattr(args, "camg_sweep_guard", None):
            camg_kw["sweep_guard"] = args.camg_sweep_guard  # sweeps damped where they grow (coarse or all levels)
        if getattr(args, "camg_memory_trace", False):
            from Pipeline.CoupledNewton import block_amg as _bam_trace
            _bam_trace.MEMORY_TRACE.update(on=True, log=logger.info)
        if getattr(args, "camg_rescue_lean_reading", False):
            from Pipeline.CoupledNewton import block_amg as _bam_lean
            _bam_lean.LEAN_READING["on"] = True
        if getattr(args, "camg_adaptive_l1", None):
            if not float(args.camg_adaptive_l1) > 0:
                raise ValueError(f"--camg-adaptive-l1 must be > 0, not {args.camg_adaptive_l1}")
            camg_kw["adaptive_l1"] = float(args.camg_adaptive_l1)
            gate = float(getattr(args, "camg_adaptive_l1_gate", None) or 0.0)
            if gate > 0:
                camg_kw["adaptive_l1_gate"] = gate
            block_amg.set_adaptive_l1(float(args.camg_adaptive_l1), gate)  # every plan of the run, the arms' alike
        elif getattr(args, "camg_adaptive_l1_gate", None):
            raise ValueError("--camg-adaptive-l1-gate acts on --camg-adaptive-l1")
        camg_model = None
        camg_diag_race = None       # --camg-split-diagonal-race: {"k", "pick", "record"}
        camg_w_diagonal_trained = "raw"
        camg_trained = {}           # l1 / jacobi_omega the train.log records (camgnet_train_settings)
        # --camg-turb-split with camgnet (opt-in): the network acts on the split's
        # MEAN-FLOW block only (u, v, w, p: the field count it was trained on), so it is built for that block's
        # width; the turbulence block and the classical hand-over keep the classical cycle (split_classical_kw)
        split_mean_width = None
        if getattr(args, "camg_turb_split", None):
            from Pipeline.CoupledNewton import coldstart as _coldstart_w
            _tf = set(_coldstart_w.turbulence_fields(solver))
            split_mean_width = solver.num_fields - sum(int(w) for name, w, *_ in solver.unknowns if str(name) in _tf)
        split_classical_kw = classical_camg_kw(
            str(getattr(args, "order1_classical", "camg2w")) if camg_spec.startswith("camgnet:") else camg_spec,
            args.camg_l1)
        if camg_spec.startswith("camgnet:"):
            # Learned modulation (NNmodels.coupled_amg_net, experiments/NaturalConvection/CoupledNewton/LearnedCoupledAMG):
            # "camgnet:<model_best.pt>"; the network sizes are read from the run's train.log.
            from NNmodels.coupled_amg_net import CoupledAMGNet
            model_path = Path(camg_spec.split(":", 1)[1])
            train_args = read_train_args(model_path)
            net_kw, camg_trained, l1_note = camgnet_train_settings(train_args, args.camg_l1)
            if getattr(args, "camg_split_learned_l1", False) and split_mean_width is None:
                raise ValueError("--camg-split-learned-l1 acts on the learned mean-flow cycle of --camg-turb-split")
            # A field-role checkpoint (train.py --field-roles; U1, 2026-11-02): one parameter set for any field
            # layout, built with its recorded role options and bound once to this system's roles (from the field
            # names, ``field_roles``); {} for a fixed-field-count checkpoint, whose construction is unchanged
            from NNmodels.coupled_amg_net import role_network_kwargs
            camg_role_kw = role_network_kwargs(train_args)
            # (the fast features -- fp32 edge inputs, the pattern-only parts cached per pattern -- read nothing
            # role-specific; the on-line adaptation is not set up for a role network)
            if camg_role_kw and args.camg_online:
                raise ValueError("camgnet: a field-role checkpoint (train.log field_roles) is not set up for "
                                 "--camg-online")
            if split_mean_width is not None and not camg_role_kw:
                # the field count the checkpoint reads (edge input 2 F^2 + 2), checked before the load
                edge_in = int(torch.load(model_path, map_location="cpu")["edge_encoder.0.weight"].shape[1])
                model_width = int(round(math.sqrt((edge_in - 2) / 2)))
                if model_width != split_mean_width:
                    raise ValueError(f"--camg-turb-split with camgnet: the model reads {model_width} x {model_width} "
                                     f"blocks, the split's mean-flow block is {split_mean_width} wide")
            camg_model = CoupledAMGNet(
                solver.num_fields if split_mean_width is None else split_mean_width,
                hidden=train_args.get("hidden", 64), passes=train_args.get("passes", 3),
                scale=train_args.get("scale", 0.5), learn_levels=train_args.get("learn_levels", 1),
                heads=tuple(train_args.get("heads", "theta_p,theta_r,psi").split(",")), **net_kw,
                **camg_role_kw).to(device)
            camg_model.load_state_dict(torch.load(model_path, map_location=device))
            camg_model.eval()
            if camg_role_kw:
                from NNmodels.coupled_amg_net import field_roles as _field_roles
                # with --camg-turb-split the network acts on the mean-flow block: the roles of that block's fields
                # (a role network has no fixed field count, so no block-width check applies; A1, 2026-11-09)
                _role_names = [name for name, _, _, _ in solver.unknowns
                               if split_mean_width is None or str(name) not in _tf]
                camg_model.bind_roles(_field_roles(_role_names, camg_model.F))
                logger.info("camgnet: field-role network %s (%s), roles bound %s", model_path, camg_role_kw,
                            camg_model._roles)
            camg_model.bf16_inference = args.camg_bf16
            if getattr(args, "camg_warp_onehop", False):
                from NNmodels import coupled_amg_net as _cnet
                _cnet.WARP_ONEHOP["on"] = True
                logger.info("--camg-warp-onehop: the one-hop smoother's blocks in one Warp kernel")
            if getattr(args, "camg_fast_features", False):
                camg_model.fast_features = True
                logger.info("--camg-fast-features: fp32 edge inputs, cached pattern parts; warm-up pass %.3f s (setup)",
                            camg_model.warmup(device))
            camg_w_diagonal_trained = camg_model.w_diagonal
            if getattr(args, "camg_split_learned_l1", False):
                # Opt-in: the split's learned mean-flow cycle on an l1 plan with W and its
                # features on the l1 smoother diagonal: theta = 0 of the stabiliser is then the l1 Jacobi the classical
                # split runs (contractive on the high-aspect wall cells where the raw damped Jacobi is not)
                camg_model.w_diagonal = "smoother"
            if int(getattr(args, "camg_split_diagonal_race", 0) or 0) > 0:
                # Opt-in: the first learned build races the two diagonals (below)
                refuse_misused_diagonal_race_flags(args)
                if split_mean_width is None:
                    raise ValueError("--camg-split-diagonal-race acts on the learned mean-flow cycle of --camg-turb-split")
                camg_diag_race = {"k": int(args.camg_split_diagonal_race), "pick": None, "record": None}
            camg_kw.update(pre=train_args.get("pre", 1), post=train_args.get("post", 1),
                           smoother=train_args.get("smoother", "gs"), gs_omega=train_args.get("gs_omega", 1.0),
                           hybrid=args.camg_hybrid if args.camg_hybrid is not None else train_args.get("hybrid", 0),
                           stabilize=args.camg_stabilize)
            camg_kw.update(camg_trained)
            if train_args.get("plan_json"):
                # A model trained on another hierarchy (train.py --plan-json, e.g. the deep
                # value-paired unsmoothed one) is deployed on that hierarchy: its smoothing / depth keywords join the
                # learned cycle's; the aggregation and the device setup stay with the entry's own flags
                # (--camg-aggregation, --camg-gpu-setup), which must name the same, and are checked here
                trained_plan = json.loads(train_args["plan_json"]) if isinstance(train_args["plan_json"], str) \
                    else dict(train_args["plan_json"])
                for key in ("smooth_prolongation", "max_levels", "coarse_max", "adaptive_l1", "adaptive_l1_gate"):
                    if key in trained_plan:
                        camg_kw[key] = trained_plan[key]
                if getattr(args, "camg_coarse_max", None) is not None:
                    camg_kw["coarse_max"] = int(args.camg_coarse_max)        # one hierarchy for both cycles
                    logger.info("camgnet: --camg-coarse-max %d replaces the trained coarse_max %s",
                                int(args.camg_coarse_max), trained_plan.get("coarse_max"))
                want_agg = trained_plan.get("aggregation", "standard")
                if want_agg != getattr(args, "camg_aggregation", "standard") or (
                        trained_plan.get("gpu_setup", False) and not getattr(args, "camg_gpu_setup", False)):
                    raise ValueError(f"camgnet: the model was trained on plan {trained_plan}; give --camg-aggregation "
                                     f"{want_agg}{' --camg-gpu-setup' if trained_plan.get('gpu_setup') else ''}")
                logger.info("camgnet: trained hierarchy %s -> learned cycle %s", trained_plan,
                            {k: camg_kw[k] for k in ("smooth_prolongation", "max_levels", "coarse_max") if k in camg_kw})
            if l1_note:
                logger.warning("%s", l1_note)
            if net_kw:
                logger.info("camgnet: train.log settings %s -> network %s, learned cycle %s", model_path.parent,
                            net_kw, camg_trained)
            if args.camg_stab_composite:
                camg_kw["stab_composite"] = True
            if args.camg_stab_select != "first":
                camg_kw["stab_select"] = args.camg_stab_select
        camg_lam_model = None
        camg_lam_reuse = None
        if getattr(args, "camg_lam", None):
            # Learned smoother diagonals in the classical cycle (--camg-lam)
            if camg_model is not None or not camg_spec.startswith("camg"):
                raise ValueError("--camg-lam modulates the classical camg / camg2w cycle; not with camgnet or other actions")
            from NNmodels.coupled_amg_net import CoupledAMGNet
            lam_path = Path(args.camg_lam)
            lam_args = read_train_args(lam_path)
            if "lam" not in str(lam_args.get("heads", "")).split(","):
                raise ValueError(f"--camg-lam {lam_path}: the model has no lam head (heads {lam_args.get('heads')!r})")
            lam_kw, _, _ = camgnet_train_settings(lam_args, False)
            lam_state = torch.load(lam_path, map_location=device)
            lam_fields = solver.num_fields
            if getattr(args, "camg_lam_embed", None) == "passive":
                # A model trained on more fields (Boussinesq) on this system through the
                # passive-scalar embedding (NNmodels.coupled_amg_net.PassiveScalarEmbed); same fields = unchanged
                from NNmodels.coupled_amg_net import fields_of_state
                lam_fields = fields_of_state(lam_state, lam_kw.get("features", "v1"))
            camg_lam_model = CoupledAMGNet(
                lam_fields, hidden=lam_args.get("hidden", 64), passes=lam_args.get("passes", 3),
                scale=lam_args.get("scale", 0.5), learn_levels=lam_args.get("learn_levels", 1),
                heads=tuple(str(lam_args["heads"]).split(",")), **lam_kw).to(device)
            camg_lam_model.load_state_dict(lam_state)
            camg_lam_model.eval()
            camg_lam_model.bf16_inference = args.camg_bf16
            if getattr(args, "camg_lam_fast", False):
                camg_lam_model.fast_features = True
                logger.info("--camg-lam-fast: fp32 edge inputs, cached pattern parts; warm-up pass %.3f s (setup)",
                            camg_lam_model.warmup(device))
            for key, want in (("pre", camg_kw.get("pre", 1)), ("post", camg_kw.get("post", 1)),
                              ("gs_omega", camg_kw.get("gs_omega", 1.0))):
                if key in lam_args and lam_args[key] != want:
                    logger.warning("--camg-lam: the model was trained with %s=%r, the cycle runs %r", key, lam_args[key], want)
            logger.info("--camg-lam: learned smoother diagonals from %s (rule %s %s)", lam_path,
                        lam_args.get("lam_rule"), lam_args.get("lam_value"))
            if lam_fields != solver.num_fields:
                from NNmodels.coupled_amg_net import PassiveScalarEmbed
                camg_lam_model = PassiveScalarEmbed(camg_lam_model, solver.num_fields)
                logger.info("--camg-lam-embed passive: the %d-field model on this %d-field system, the extra fields a "
                            "passive scalar (mean velocity diagonal, no coupling)", lam_fields, solver.num_fields)
            if int(getattr(args, "camg_lam_reuse", 1)) > 1:
                from NNmodels.coupled_amg_net import LamReuse
                camg_lam_reuse = LamReuse(camg_lam_model, int(args.camg_lam_reuse))
                logger.info("--camg-lam-reuse %d: the network at every %d-th hierarchy build%s", args.camg_lam_reuse,
                            args.camg_lam_reuse, " and at the first build of every rung (--camg-lam-reuse-rung)"
                            if getattr(args, "camg_lam_reuse_rung", False) else "")
        if getattr(args, "camg_lam_reuse_rung", False) and camg_lam_reuse is None:
            raise ValueError("--camg-lam-reuse-rung needs --camg-lam with --camg-lam-reuse R > 1")
        if getattr(args, "camg_stab_rule", None):
            # The classical stabilised smoother diagonal in the classical cycle; everything else
            # (P / R, block kernels, plan reuse, CUDA graphs, the l1 rescue) as without it
            if camg_lam_model is not None or camg_model is not None or not camg_spec.startswith("camg"):
                raise ValueError("--camg-stab-rule modulates the classical camg* cycle; not with --camg-lam or camgnet")
            camg_lam_model = camg_stab_rule_modulator(args.camg_stab_rule)
            logger.info("--camg-stab-rule %s: smoother diagonals D + diag(lam r), %s", args.camg_stab_rule,
                        camg_lam_model.describe())
        camg_d1 = None
        if getattr(args, "camg_field_damping", None) or getattr(args, "camg_d1", None):
            if (camg_lam_model is not None or camg_model is not None or not camg_spec.startswith("camg")
                    or camg_spec.startswith("camgcfg:")):
                raise ValueError("--camg-field-damping / --camg-d1 modulate the classical camg* cycle; not with "
                                 "--camg-lam, --camg-stab-rule, camgnet or camgcfg")
            if getattr(args, "camg_field_damping", None) and getattr(args, "camg_d1", None):
                raise ValueError("--camg-field-damping and --camg-d1 both modulate the GS sweep: one of them")
            if getattr(args, "camg_d1", None) and (getattr(args, "camg_turb_split", None)
                                                   or getattr(args, "camg_l1_start", False)):
                raise ValueError("--camg-d1 acts on the plain coupled cycle: not with --camg-turb-split or "
                                 "--camg-l1-start")
        if getattr(args, "camg_field_damping", None):
            row_names = [str(name) for name, width, *_ in solver.unknowns for _ in range(int(width))]
            camg_lam_model = block_amg.FieldDamping.from_dampings(args.camg_field_damping, row_names,
                                                                  camg_kw.get("gs_omega", 1.0))
            logger.info("--camg-field-damping %s: the classical sweep's update scaled per field, %s (rows %s)",
                        args.camg_field_damping, camg_lam_model.describe(), ",".join(row_names))
        if getattr(args, "camg_d1", None):
            # The D1 head (Order1Headroom B1 / B1b): node outputs of the order-1 network as a bounded per-row damping
            # and diagonal augmentation of the classical sweep, with the build-time stability scale (below, at the
            # build)
            from NNmodels.coupled_amg_net import D1_HEADS, CoupledAMGNet, D1Scaled
            d1_path = Path(args.camg_d1)
            d1_args = read_train_args(d1_path)
            d1_heads = tuple(str(d1_args.get("heads", "")).split(","))
            if not any(h in D1_HEADS for h in d1_heads):
                raise ValueError(f"--camg-d1 {d1_path}: the model has no D1 head (heads {d1_heads!r})")
            d1_kw, _, _ = camgnet_train_settings(d1_args, False)
            d1_model = CoupledAMGNet(solver.num_fields, hidden=d1_args.get("hidden", 64),
                                     passes=d1_args.get("passes", 3), scale=d1_args.get("scale", 0.5),
                                     learn_levels=d1_args.get("learn_levels", 1), heads=d1_heads, **d1_kw).to(device)
            d1_model.load_state_dict(torch.load(d1_path, map_location=device))
            d1_model.eval()
            for key, want in (("pre", camg_kw.get("pre", 1)), ("post", camg_kw.get("post", 1)),
                              ("gs_omega", camg_kw.get("gs_omega", 1.0)), ("smoother", camg_kw.get("smoother"))):
                if key in d1_args and d1_args[key] != want:
                    raise ValueError(f"--camg-d1 {d1_path}: trained with {key}={d1_args[key]!r}, the cycle runs {want!r}")
            if int(args.camg_d1_check_every) < 1:
                raise ValueError("--camg-d1-check-every must be >= 1")
            fixed = getattr(args, "camg_d1_theta", None)
            retain = int(getattr(args, "camg_d1_retain", 0) or 0)
            if retain < 0:
                raise ValueError("--camg-d1-retain must be >= 0")
            stall = bool(getattr(args, "camg_d1_stall_handover", False))
            if stall and not retain:
                raise ValueError("--camg-d1-stall-handover acts on the retained solves: it needs --camg-d1-retain B")
            reuse = getattr(args, "camg_d1_reuse", "build")
            camg_d1 = {"mod": D1Scaled(d1_model, 1.0 if fixed is None else float(fixed), reuse=reuse), "builds": 0,
                       "every": int(args.camg_d1_check_every), "fixed": fixed, "checks": [], "seconds": 0.0,
                       "retain": retain, "reuse": reuse}
            camg_lam_model = camg_d1["mod"]
            logger.info("--camg-d1: D1 head %s (heads %s, %d levels) on the classical %s cycle; %s; network %s; %s",
                        d1_path, ",".join(d1_heads), d1_model.learn_levels, camg_spec,
                        "fixed theta %g, no check" % fixed if fixed is not None else
                        "stability scale checked at the first build and every %d-th build after it" % camg_d1["every"],
                        "at every build" if reuse == "build" else "at the first build of every solve_newton call",
                        ("progress-based retention after %d iterations (classical cycle, l1 rescue)%s"
                         % (retain, ", hand-over on a later stagnant cycle" if stall else "")) if retain
                        else "the stagnation rescue as the classical cycle's")
        elif (getattr(args, "camg_d1_theta", None) is not None or int(getattr(args, "camg_d1_retain", 0) or 0)
              or getattr(args, "camg_d1_reuse", "build") != "build" or getattr(args, "camg_d1_stall_handover", False)):
            raise ValueError("--camg-d1-theta / --camg-d1-retain / --camg-d1-reuse / --camg-d1-stall-handover need "
                             "--camg-d1")
        camg_config = None
        if camg_spec.startswith("camgcfg:"):
            # The hierarchy and cycle of one point of the AMG
            # configuration space, fixed or chosen per matrix by a trained ranking; the point's per-level stabilisation
            # is the modulator of every coupled AMG of the factory (refuse_misused_camgcfg_flags: no other modulator)
            from NNmodels.amg_config_net import ConfigChoice
            from NNmodels.coupled_amg_net import field_roles
            roles = field_roles([name for name, _, _, _ in solver.unknowns], solver.num_fields)
            camg_config = ConfigChoice(camg_spec.split(":", 1)[1], device, roles,
                                       every=getattr(args, "camg_config_every", "first"), log=logger.info,
                                       policy=getattr(args, "camg_config_policy", "library"),
                                       kappa=getattr(args, "camg_config_kappa", 1.0),
                                       values_only=getattr(args, "camg_config_values_only", False),
                                       dump=getattr(args, "camg_config_dump", None))
            camg_lam_model = camg_config.modulator
            logger.info("--learned-action %s: %s (field roles %s)", camg_spec,
                        "trained ranking (%d), %s policy, choice %s" % (len(camg_config.models), camg_config.policy,
                                                                         camg_config.every)
                        if camg_config.model is not None
                        else "fixed point %s" % json.dumps(camg_config.cfg, sort_keys=True), ",".join(roles))
        camg_timings, camg_fallbacks, camg_race, camg_budget_log = [], [], [], []
        if camg_d1 is not None:
            camg_d1["budget_log"] = camg_budget_log            # --camg-d1-retain: one entry per budgeted solve

        def camg_sync_clock() -> float:
            if device.type == "cuda":
                torch.cuda.synchronize(device)
            return time.perf_counter()
        camg_rescue_log = []
        # --camg-direct-escalate (opt-in): one cuDSS object per run (the analysis is kept per pattern)
        camg_escalation_log, camg_direct_factory = [], None
        if getattr(args, "camg_direct_escalate", 0):
            from Pipeline.CoupledNewton.robust_linear import DirectLUFactory
            camg_direct_factory = DirectLUFactory(precision=args.cudss_precision, hybrid_gib=args.cudss_hybrid_gib,
                                                  log=logger.info, probe_tol=getattr(args, "cudss_probe", 0.0) or None)
            camg_direct_factory.n_cell = int(getattr(solver, "n_cell", 0) or 0) or None   # the probe's row layout
            logger.info("--camg-direct-escalate %d: a solve unconverged after %d GMRES cycles continues with the cuDSS "
                        "LU (%s) of the same J", args.camg_direct_escalate, args.camg_direct_escalate,
                        args.cudss_precision)
        # --camg-l1-start: l1 smoothers until main's progress hook switches this off at the
        # first full step; read at every build below
        camg_l1_start = {"on": bool(getattr(args, "camg_l1_start", False))}
        # --forcing-cap-fallback: main's fallback hook lifts the rescue's maxit (later builds)
        camg_rescue_mode = {"maxit_lifted": False}
        camg_guard = 1.0
        camg_adapter = None
        if camg_model is not None and args.camg_online:
            # Stage 2 of the two-stage training: fine-tune the
            # smoother network ON THE PROBLEM BEING SOLVED.  At every linearisation BFS patches of
            # the new Jacobian join a replay buffer and a few Adam steps of the off-line gmres loss
            # run BEFORE the learned cycle is built, so this linearisation already uses the adapted
            # network.  The race below still decides per linearisation; the answer is never at stake.
            from Pipeline.CoupledNewton.camg_train import LinearSystem, OnlineAdapter
            LinearSystem.gs_omega, LinearSystem.hybrid = camg_kw.get("gs_omega", 1.0), camg_kw.get("hybrid", 0)
            online_plan_kw = {"smoother": camg_kw["smoother"], "restriction": "pg", "coarse_max": 300}
            if "l1" in camg_trained:
                # an l1-trained model adapts on the plans it was trained on
                online_plan_kw["l1"] = camg_trained["l1"]
            if "diag_eps" in camg_trained:
                online_plan_kw["diag_eps"] = camg_trained["diag_eps"]
            if "jacobi_omega" in camg_trained:
                LinearSystem.jacobi_omega = camg_trained["jacobi_omega"]
            o_steps, o_size, o_count, o_lr = args.camg_online.split(",")
            camg_adapter = OnlineAdapter(
                camg_model, solver.n_cell, solver.num_fields, lr=float(o_lr), steps=int(o_steps),
                patch_size=int(o_size), patches=int(o_count), buffer=args.camg_online_buffer, m=16,
                pre=camg_kw["pre"], post=camg_kw["post"],
                plan_kw=online_plan_kw,
                log=logger.info)
            logger.info("on-line adaptation: %s Adam steps on %s patches of %s cells per linearisation, lr %s, "
                        "buffer %d", o_steps, o_count, o_size, o_lr, args.camg_online_buffer)

        camg_reuse, camg_plan_cache, camg_stab_cache = None, {}, {"stab": None, "calls": 0}
        turb_split_fields = ()
        if getattr(args, "camg_turb_split", None):
            # --camg-turb-split (opt-in): Pipeline.CoupledNewton.turbulence_split
            from Pipeline.CoupledNewton import coldstart as _coldstart
            order, _, turb = args.camg_turb_split.partition(":")
            if order not in ("tm", "mt", "sym") or turb not in ("lu", "amg"):
                raise ValueError(f"--camg-turb-split wants ORDER:TURB with ORDER tm|mt|sym and TURB lu|amg, got "
                                 f"{args.camg_turb_split!r}")
            if args.fixed_jacobian_pattern == "blocks":
                raise ValueError("--camg-turb-split splits the coupled cycle on a scalar J: not with "
                                 "--fixed-jacobian-pattern blocks")
            if camg_model is not None:
                # A learned camgnet model is the MEAN-FLOW block's cycle; its field
                # count must be that block's (the network reads F x F blocks), and the paths that read the full
                # coupled matrix with the network are not split-aware
                if camg_model.F != split_mean_width:
                    raise ValueError(f"--camg-turb-split with camgnet: the model reads {camg_model.F} x "
                                     f"{camg_model.F} blocks, the split's mean-flow block is {split_mean_width} wide")
                for flag, on in (("--camg-online", bool(args.camg_online)),
                                 ("--camg-net-every > 1", int(args.camg_net_every) > 1)):
                    if on:
                        raise ValueError(f"--camg-turb-split with camgnet: {flag} acts on the coupled matrix")
            turb_split_fields = set(_coldstart.turbulence_fields(solver))
            logger.info("--camg-turb-split %s: the turbulence rows %s leave the coupled cycle (mean flow by %s, "
                        "turbulence block by %s, block Gauss-Seidel order %s)", args.camg_turb_split,
                        sorted(turb_split_fields),
                        "its own PG-AMG" if camg_model is None else
                        f"the LEARNED cycle of {camg_spec.split(':', 1)[1]} (as trained: "
                        f"{ {k: v for k, v in camg_kw.items() if k != 'l1'} }, l1={(True if getattr(args, 'camg_split_learned_l1', False) else camg_trained.get('l1', False))!r}"
                        f"{' (--camg-split-learned-l1: W on the l1 smoother diagonal)' if getattr(args, 'camg_split_learned_l1', False) else ''}; "
                        f"classical hand-over and turbulence block {split_classical_kw})",
                        "an exact host LU" if turb == "lu" else "its own PG-AMG", order)
        if camg_model is not None and args.camg_net_every > 1:
            from NNmodels.coupled_amg_net import ThetaReuse
            camg_reuse = ThetaReuse(camg_model, args.camg_net_every, keep_w=getattr(args, "camg_keep_w", False),
                                    refresh_growth=getattr(args, "camg_theta_refresh", None))
            logger.info("network output reused: forward every %d linearisations, AMG plan reused in between",
                        args.camg_net_every)
            if camg_reuse.refresh_growth is not None:
                logger.info("--camg-theta-refresh %g: the network also runs at the build after one whose first solve "
                            "needed more than %g x the iterations per decade of the first solve after its last forward",
                            camg_reuse.refresh_growth, camg_reuse.refresh_growth)
            if camg_reuse.keep_w:
                logger.info("--camg-keep-w: between forwards the learned W is kept for the rest of the rung; the first "
                            "build of each rung rebuilds it from the cached network output and the rung's matrix")
        if args.camg_reuse_plan and plan_reuse is not None:
            camg_plan_reuse = plan_reuse          # --order1-switch: the classical factory shares the learned one's cache
        elif args.camg_reuse_plan:
            if getattr(args, "camg_structural_plan", False):
                # Structural (value-free) plans, one per plan setting per run
                cache_dir = os.path.abspath(args.camg_plan_cache) if getattr(args, "camg_plan_cache", None) else None
                camg_plan_reuse = CamgPlanReuse(args.camg_plan_refresh, structural=True, plan_cache=cache_dir,
                                                log=logger.info)
                logger.info("--camg-structural-plan: one STRUCTURAL coupled AMG plan per plan setting for the whole run "
                            "(strength = the block pattern, value-free; kept across rungs; every build is a "
                            "numeric-only pass, omega recomputed)%s",
                            f"; decisions cached per pattern in {cache_dir}" if cache_dir else "; no decision cache")
            elif camg_spec.startswith("camgcfg:") and getattr(args, "camg_plan_cache", None):
                # Value-based plans per rung; a chosen classical PG point on C's structural plan,
                # its decisions from / to the same per-pattern cache as C
                cache_dir = os.path.abspath(args.camg_plan_cache)
                camg_plan_reuse = CamgPlanReuse(args.camg_plan_refresh, pattern_cache=cache_dir, log=logger.info)
                logger.info("--camg-plan-cache with camgcfg: a structural (PG) choice reads C's plan decisions from %s; "
                            "value-based plans per rung", cache_dir)
            else:
                camg_plan_reuse = CamgPlanReuse(args.camg_plan_refresh)
                logger.info("--camg-reuse-plan: one coupled AMG plan per rung and plan setting, rebuilt when the block "
                            "pattern changes%s; a reuse recomputes the numeric phase only (omega included)",
                            f" and every {args.camg_plan_refresh} linearisations" if args.camg_plan_refresh else "")
        if args.camg_reuse_plan and args.component_timers:
            # Every numeric pass timed by part (synced; numbers unchanged), so a run without the AMG-setup
            # flags reports the same AMG setup split as one with them
            block_amg.NUMERIC_TIMERS["on"] = True
        if any(getattr(args, a, False) for a in ("camg_warp_numeric", "camg_warp_omega", "camg_omega_device")):
            # Warp backend (opt-in): the numeric build's products / power iteration as Warp kernels
            block_amg.WARP_NUMERIC["on"] = bool(getattr(args, "camg_warp_numeric", False))
            block_amg.WARP_NUMERIC["omega"] = bool(getattr(args, "camg_warp_omega", False))
            block_amg.WARP_NUMERIC["omega_device"] = bool(getattr(args, "camg_omega_device", False))
            logger.info("W6 Warp numeric build: Galerkin products and block sums %s, omega power iteration %s",
                        "Warp" if block_amg.WARP_NUMERIC["on"] else "torch",
                        "Warp" if block_amg.WARP_NUMERIC["omega"] else "torch")
        if getattr(args, "camg_lazy_colours", False):
            block_amg.LAZY_COLOURS["on"] = True
            logger.info("--camg-lazy-colours: GS colour rows from one device sort, block-GS edge lists on first use")
        if getattr(args, "krylov_basis_fp32", False):
            # Opt-in, set here so the steady and the transient entry both see it: the Arnoldi basis
            # stored in fp32 with every product in fp64 (compressed-basis GMRES), with the fused kernels only
            from SparseSolver import gmres as _gm
            _gm.KRYLOV["basis_fp32"] = True
            logger.info("--krylov-basis-fp32: the GMRES basis is stored in fp32 (arithmetic fp64; fused kernels only)")
        if getattr(args, "camg_sell_policy", None):
            # Warp backend (opt-in): sliced-ELL layout policy overrides (block_kernels.POLICY)
            from Pipeline.CoupledNewton import block_kernels as _bk1
            for item in args.camg_sell_policy.split(","):
                key, _, value = item.partition("=")
                if key.strip() not in _bk1.POLICY and key.strip() != "num_warps":
                    raise ValueError(f"--camg-sell-policy: unknown key {key!r} (keys: {sorted(_bk1.POLICY)})")
                _bk1.POLICY[key.strip()] = int(value)
            logger.info("--camg-sell-policy: sliced-ELL policy %s", _bk1.POLICY)
        if any(getattr(args, a, False) for a in ("camg_rows_kernel", "camg_zero_skip", "camg_coarse_inverse",
                                                  "camg_warp_fill", "camg_rows_products")):
            # Warp backend (opt-in): V-cycle kernels
            from Pipeline.CoupledNewton import block_kernels as _bk2
            _bk2.WARP_FILL["on"] = bool(getattr(args, "camg_warp_fill", False))
            _bk2.ROWS_KERNEL["on"] = bool(getattr(args, "camg_rows_kernel", False)
                                          or getattr(args, "camg_rows_products", False))
            _bk2.ROWS_KERNEL["gs"] = not getattr(args, "camg_rows_products", False) or bool(
                getattr(args, "camg_rows_kernel", False))
            _bk2.ZERO_SKIP["on"] = bool(getattr(args, "camg_zero_skip", False))
            block_amg.COARSE_INVERSE["on"] = bool(getattr(args, "camg_coarse_inverse", False))
            logger.info("W2 V-cycle kernels: Warp rows kernel %s (slice height >= %d), first-sweep zero skip %s, "
                        "coarse inverse %s", _bk2.ROWS_KERNEL["on"], _bk2.ROWS_KERNEL["min_c"], _bk2.ZERO_SKIP["on"],
                        block_amg.COARSE_INVERSE["on"])
        if getattr(args, "camg_coarse_inverse_min_pivot", None) is not None:
            block_amg.COARSE_INVERSE["min_pivot"] = float(args.camg_coarse_inverse_min_pivot)
            logger.info("--camg-coarse-inverse-min-pivot %g: on a free pressure level the coarsest level by its LU "
                        "when its smallest pivot is below that fraction of the largest",
                        block_amg.COARSE_INVERSE["min_pivot"])
        if getattr(args, "camg_coarse_gauge", False):
            block_amg.COARSE_GAUGE["on"] = True
            logger.info("--camg-coarse-gauge: on a free pressure level every block cycle's coarse correction without "
                        "the constant pressure mode")
        if getattr(args, "camg_share_plan", False):
            from Pipeline.CoupledNewton import amg_plan_cache as _apc
            _apc.SHARE["on"] = True
            logger.info("--camg-share-plan: structural plans that differ only in l1 / diag_eps share their symbolic "
                        "data (W6)")
        if getattr(args, "camg_gpu_setup", False):
            logger.info("--camg-gpu-setup: AMG strength graphs on the device (bit for bit the host graph) and "
                        "Jones-Plassmann smoother colourings on the device (not the host greedy colouring)")
        if getattr(args, "camg_fused_numeric", False):
            logger.info("--camg-fused-numeric: AMG numeric passes with fewer block products (P / R block sums, "
                        "one inverse per level, BSR power iteration for omega): rounding-level differences")
        if args.camg_fast_kernels:
            logger.info("--camg-fast-kernels: every coupled AMG applies its V-cycle with scalar CSR copies "
                        "(cuSPARSE) in colour-sliced layouts; the network, stability check and training keep the "
                        "block kernels (report: fast_kernels memory)")
        if getattr(args, "camg_cuda_graph", False):
            logger.info("--camg-cuda-graph: every coupled AMG replays its fast V-cycle from a CUDA graph captured "
                        "at its first application and checked against it (relative difference <= %.0e); "
                        "report: cuda_graph", block_amg.GRAPH_REL_TOL)
        if getattr(args, "camg_block_kernels", False):
            from Pipeline.CoupledNewton import block_kernels as _bk
            logger.info("--camg-block-kernels: every coupled AMG applies its fast V-cycle with fused sliced-ELL block "
                        "kernels, %s values with fp64 arithmetic (Triton %s; report: block_kernels)",
                        args.camg_block_values, "importable" if _bk.HAVE_TRITON else "NOT importable: torch reference")

        def camg_cycle_built(A, **kw):
            """``block_amg.CoupledAMG(A, **kw)`` -- the very same call without --camg-reuse-plan -- or, with it, that
            cycle on ``camg_plan_reuse``'s plan for ``kw``'s plan settings.  Every coupled AMG of
            the factory below, the lazily built hand-over and rescue arms included, is made here.  With
            --camg-fast-kernels each of them gets ``fast_kernels=True`` (camg_fast_kernel_kwargs).
            With --learned-action camgcfg: (O1-C) the point's plan and cycle settings replace the classical ones."""
            if camg_config is not None:
                kw = camg_config.apply(A, kw)
            kw = camg_fast_kernel_kwargs(args, kw)
            kw = camg_m2_kwargs(args, kw)
            kw = camg_m3_kwargs(args, kw)
            kw = camg_coarse_max_default(args, kw)
            # a plan that lends its finest level's symbolic data to this cycle's plan (block_amg ``level0_from``: the
            # stagnation rescue on its primary's matrix); not a cycle keyword
            level0 = kw.pop("level0_from", None)
            if camg_plan_reuse is None:
                return keep_identity(block_amg.CoupledAMG(A, **kw), A, None, kw)
            kw = {**kw, "colour_coarsest": False}
            # The plan / numeric split of every build, synced, with an AMG-setup flag or --component-timers
            m2 = (any(kw.get(key) for key in ("gpu_setup", "fused_numeric")) or kw.get("strength") == "pattern"
                  or bool(getattr(args, "component_timers", False)))
            t_plan = camg_sync_clock() if m2 else None
            build = None
            if level0 is not None and not (camg_plan_reuse.structural or (
                    camg_plan_reuse.pattern_cache is not None and kw.get("strength") == "pattern")):
                build = (lambda A, kw=kw, level0=level0: block_amg.CoupledAMGPlan(
                    A, level0_from=level0, **block_amg.plan_kwargs(**kw)))
            plan, reason, refresh = camg_plan_reuse.plan_for(A, kw, build=build)
            if reason is not None and getattr(plan, "level0_shared", False):
                logger.info("camg plan of this cycle (l1=%r) on the lent finest level of its primary's plan",
                            kw.get("l1"))
            if m2:
                t_cycle = camg_sync_clock()
                camg_plan_reuse.setup_log.append({"plan_s": t_cycle - t_plan if reason is not None else 0.0, "reason": reason})
            if reason is not None:
                logger.info("camg plan built (%s, %d blocks, l1=%r)%s: %s", reason, A.nnzb, kw.get("l1", False),
                            " in %.3f s (%s)" % (camg_plan_reuse.setup_log[-1]["plan_s"], ", ".join(
                                "%s %.3f" % item for item in getattr(plan, "setup_seconds", {}).items()))
                            if m2 else "", camg_plan_reuse.summary())
                if camg_plan_reuse.cache_events and reason is not None and kw.get("strength") == "pattern":
                    ev = camg_plan_reuse.cache_events[-1]
                    logger.info("camg plan cache: %s%s%s (key %.3f s)", ev["source"],
                                f" ({ev['reason']})" if ev.get("reason") not in (None, "hit") else "",
                                f", entry {ev['path']}" if ev.get("path") else "", ev.get("key_s", 0.0))
            amg = keep_identity(block_amg.CoupledAMG(A, plan=plan, refresh_omega=refresh, **kw), A, plan, kw)
            amg.fresh_plan = reason is not None          # the plan was built from A in this call
            if m2:
                rec = camg_plan_reuse.setup_log[-1]
                rec["cycle_s"] = camg_sync_clock() - t_cycle
                rec["numeric"] = dict(getattr(plan, "numeric_seconds", None) or {})
                logger.info("camg setup split: plan %.3f s (%s), numeric + fill %.3f s (%s)", rec["plan_s"],
                            reason or "reused", rec["cycle_s"],
                            ", ".join("%s %.3f" % item for item in rec["numeric"].items()))
            return amg

        def camg_cycle(A, **kw):
            """``camg_cycle_built``; with --component-timers on a CUDA device it also logs the device memory the build
            took (allocated before and after, and the step's peak so far), every arm alike (a lazily built rescue arm
            included)."""
            if not (getattr(args, "component_timers", False) and torch.cuda.is_available()):
                return log_sweep_guard(camg_cycle_built(A, **kw), logger)
            before = torch.cuda.memory_allocated()
            amg = log_sweep_guard(camg_cycle_built(A, **kw), logger)
            logger.info("camg build memory (l1=%r): allocated %.2f -> %.2f GiB, the step's peak so far %.2f GiB",
                        kw.get("l1", False), before / 2 ** 30, torch.cuda.memory_allocated() / 2 ** 30,
                        torch.cuda.max_memory_allocated() / 2 ** 30)
            return amg

        def d1_build(A, cycle_kw: dict, kw: dict):
            """--camg-d1: this build's cycle with the D1 head at its stability scale.  A checked build (the first, then
            every --camg-d1-check-every-th) tries theta = 1, 0.5, 0.25 in turn and keeps the first whose cycle
            contraction (block_amg.cycle_contraction) is at most max(1, the classical cycle's) x 1.02, else theta 0 (the
            classical cycle).  The classical cycle is built only when a head's contraction exceeds 1.02 (below that the
            bound holds whatever the classical value).  Other builds keep the last theta; with --camg-d1-theta no check.
            Every second of the check is spent inside the build (its timing and the solve's total)."""
            d1, mod = camg_d1, camg_d1["mod"]
            mod.new_build()
            d1["builds"] += 1
            if d1["fixed"] is not None or (d1["builds"] - 1) % d1["every"]:
                return camg_cycle(A, **cycle_kw, **{**kw, "modulator": mod})
            t0 = camg_sync_clock()
            gf = cycle_kw.get("gauge_field")
            rates, rho_c, ref, amg, chosen, power_s, builds = {}, None, None, None, 0.0, 0.0, 0

            def contraction(cycle):
                t = camg_sync_clock()
                r = block_amg.cycle_contraction(cycle, A, gf)
                return r, camg_sync_clock() - t
            for th in (1.0, 0.5, 0.25):
                mod.theta = th
                cand = camg_cycle(A, **cycle_kw, **{**kw, "modulator": mod})
                builds += 1
                r, s = contraction(cand)
                rates[th], power_s = r, power_s + s
                if math.isfinite(r) and r > 1.02 and rho_c is None:
                    ref = camg_cycle(A, **cycle_kw, **kw)
                    builds += 1
                    rho_c, s = contraction(ref)
                    power_s += s
                if math.isfinite(r) and r <= max(1.0, rho_c if rho_c is not None else 1.0) * 1.02:
                    amg, chosen = cand, th
                    break
                del cand
            if amg is None:
                mod.theta = 0.0
                if ref is None:
                    ref = camg_cycle(A, **cycle_kw, **kw)
                    builds += 1
                amg = ref
            ref = None
            dt = camg_sync_clock() - t0
            d1["seconds"] += dt
            d1["checks"].append({"build": d1["builds"], "theta": chosen, "rates": rates, "classical": rho_c,
                                 "seconds": dt, "power_s": power_s, "cycles_built": builds})
            logger.info("D1 check (build %d): theta %g; cycle contraction %s, classical %s; %d cycles built, %.3f s "
                        "(power steps %.3f s; checks so far %d, %.3f s)", d1["builds"], chosen,
                        ", ".join("%g: %.4f" % item for item in rates.items()),
                        "not needed" if rho_c is None else "%.4f" % rho_c, builds, dt, power_s, len(d1["checks"]),
                        d1["seconds"])
            return amg

        def camg_memory():
            """``(allocated, peak)`` GiB on the CUDA device under --component-timers (for OOM
            follow-up), else None.  The peak is the one since the Newton step began: ``solve_newton`` resets the
            allocator's peak at every step when its component timers are on."""
            if not ((args.component_timers or args.iterate_error) and device.type == "cuda"):
                return None
            return (torch.cuda.memory_allocated(device) / 2 ** 30, torch.cuda.max_memory_allocated(device) / 2 ** 30)

        def block_matrix_of(blocks):
            """The block matrix of an assembly, as learned_factory reads it (also the current-matrix
            rescue's, block_amg._FreshEscalation)."""
            if isinstance(blocks, BlockJacobian):
                return blocks["A"]
            J = blocks["J"]
            if J.layout != torch.sparse_csr:
                J = J.to_sparse_csr()
            return block_amg.from_field_blocked_csr(J.crow_indices(), J.col_indices(), J.values().detach(),
                                                    solver.n_cell, solver.num_fields,
                                                    drop_zero_blocks=(args.fixed_jacobian_pattern == "structural"
                                                                      and not args.camg_reuse_plan))

        def learned_factory(blocks, x):
            warp_backend = getattr(solver, "_warp", None)
            if warp_backend is not None and hasattr(warp_backend, "release_probe_buffers"):
                warp_backend.release_probe_buffers()   # W12c: large meshes free the probe buffers before the build
            mem_snapshot_at_build(logger.info)    # LPCNS_MEM_SNAP (W12c); nothing when unset
            started_build = time.time()
            memory = [("at the call", camg_memory())]
            # --fixed-jacobian-pattern blocks: the assembly IS the block matrix
            # (jacobian.BlockJacobian); no scalar J exists, and the hierarchy's fine-level copy becomes the step's
            # Krylov operator below
            block_jacobian = isinstance(blocks, BlockJacobian)
            if block_jacobian:
                A = blocks["A"]
            else:
                # J is already CSR on every assembly path; to_sparse_csr() is then not called at all, so
                # this name can never be a second copy of J (418M entries at DHC88 structural) for the build's length
                J = blocks["J"]
                if J.layout != torch.sparse_csr:
                    J = J.to_sparse_csr()
                # --fixed-jacobian-pattern structural: J stores exact zeros; dropping the all-zero
                # blocks gives the block pattern and values of today's J, so the AMG is the one it always was.
                # With --camg-reuse-plan the zero blocks are KEPT: the
                # dropped pattern moves at every linearisation under fast probes (13.9M -> 14.5M blocks on Cyl-589k),
                # so every build was a 'pattern' rebuild and the plan was never reused; the structural block pattern is
                # fixed for the run, and the strength graph ignores zero blocks, so the aggregates are those of the
                # first build.
                A = block_amg.from_field_blocked_csr(J.crow_indices(), J.col_indices(), J.values().detach(),
                                                     solver.n_cell, solver.num_fields,
                                                     drop_zero_blocks=(args.fixed_jacobian_pattern == "structural"
                                                                       and not args.camg_reuse_plan))
            memory.append(("with the block matrix", camg_memory()))
            t_blocks, t_hierarchy = _o1_clock(device), None          # LPCNS_O1_TIMERS=1 only (profile runs)
            free = solver.resolve_gauge(x)
            if camg_plan_reuse is not None:
                camg_plan_reuse.new_linearisation()
            reuse_kw = {}
            if camg_reuse is not None and camg_plan_reuse is not None:
                # --camg-reuse-plan decides the plan (camg_cycle); ThetaReuse still decides the network forwards
                camg_reuse.tick()
                reuse_kw = {"modulator": camg_reuse}
            elif camg_reuse is not None:
                camg_reuse.tick()
                fresh = (camg_reuse.calls - 1) % camg_reuse.every == 0
                # Keyed on the block PATTERN, not on nnzb (cached_amg_plan has the failure).
                plan, rebuilt = cached_amg_plan(
                    camg_plan_cache, A, fresh,
                    lambda A: block_amg.CoupledAMGPlan(A, smoother=camg_kw["smoother"],
                                                       restriction=camg_kw["restriction"],
                                                       l1=camg_kw.get("l1", False),
                                                       diag_eps=camg_kw.get("diag_eps", 0.0)))
                if rebuilt == "pattern":
                    logger.info("AMG plan rebuilt off schedule: the Jacobian's block pattern changed (%d blocks)",
                                A.nnzb)
                reuse_kw = {"plan": plan, "modulator": camg_reuse}
            if camg_budget_log:
                n_fresh = sum(bool(e.get("fresh")) for e in camg_budget_log)
                logger.info("learned budget so far: %d hand-overs to the classical cycle in %d budgeted solves%s",
                            sum(e["handover"] for e in camg_budget_log), len(camg_budget_log),
                            ", %d continued on the step's own matrix" % n_fresh if n_fresh else "")
            if camg_adapter is not None:
                if args.fixed_jacobian_pattern == "structural":
                    # The BFS patches and their AMG plans are read off the stored
                    # pattern, so the on-line adapter gets today's matrix, as the AMG above does.
                    from Pipeline.CoupledNewton.jacobian import drop_explicit_zeros
                    J = drop_explicit_zeros(J)
                observe_s = camg_adapter.observe(J, -solver.residual(x).detach())
                with torch.enable_grad():
                    rec = camg_adapter.adapt()
                logger.info("ONLINE adapt %d: patches %.2f s, %d Adam steps %.2f s, gmres loss %.3f -> %.3f "
                            "(mean of last 5), buffer %d", len(camg_adapter.history), observe_s, rec["steps"],
                            rec["seconds"], rec["loss_first"] or float("nan"),
                            rec["loss_mean_last5"] or float("nan"), rec["buffer"])
            stab_kw = {}
            if camg_kw.get("stabilize") and args.camg_stab_every > 1:
                camg_stab_cache["calls"] += 1
                if (camg_stab_cache["calls"] - 1) % args.camg_stab_every and camg_stab_cache["stab"]:
                    stab_kw = {"stab_cached": camg_stab_cache["stab"]}
            l1_start_kw = {"l1": True} if camg_l1_start["on"] else {}
            if l1_start_kw:
                logger.info("--camg-l1-start: l1 smoothers for this build (no full step accepted yet)")
            split_of = None
            with torch.no_grad():
                if getattr(args, "camg_turb_split", None):
                    # --camg-turb-split (opt-in): the turbulence rows out of the coupled cycle
                    from Pipeline.CoupledNewton.turbulence_split import TurbulenceSplit
                    order, turb = args.camg_turb_split.split(":")
                    n_turb = sum(int(w) for name, w, *_ in solver.unknowns if str(name) in turb_split_fields)
                    # the turbulence block: always the classical cycle (camg_kw itself without a learned model: the
                    # classical split bit for bit; with camgnet: the classical keywords of --order1-classical)
                    turb_kw = camg_kw if camg_model is None else split_classical_kw

                    def split_of(mean_kw, A=A, free=free, n_turb=n_turb, order=order, turb=turb, turb_kw=turb_kw):
                        return TurbulenceSplit(
                            A, lambda A_mm: camg_cycle(A_mm, gauge_field=(VELOCITY_WIDTH if free else None),
                                                       **mean_kw),
                            turb_fields=tuple(range(solver.num_fields - n_turb, solver.num_fields)), order=order,
                            turb=turb, turb_cycle=lambda A_tt: camg_cycle(A_tt, gauge_field=None,
                                                                          **{**turb_kw, **l1_start_kw}),
                            gauge=solver.gauge)
                    if camg_model is None:
                        amg = split_of({**camg_kw, **l1_start_kw})
                    else:
                        # The learned cycle on the mean-flow block, as trained (l1 only
                        # where the train.log records it); stabilise / budget / race safeguards below act on the split
                        learned_l1 = True if getattr(args, "camg_split_learned_l1", False) else camg_trained.get("l1", False)
                        if camg_diag_race is None:
                            amg = split_of({"modulator": camg_model, **camg_kw, "l1": learned_l1, **stab_kw})
                        else:
                            # With --camg-split-diagonal-race-at handover the race waits for the first
                            # build without a PTC shift (theta = 0); the l1 form builds until then
                            race_now = (getattr(args, "camg_split_diagonal_race_at", "first") == "first"
                                        or getattr(solver, "_ptc_shift", None) is None)
                            amg = diagonal_race_split(
                                camg_diag_race, split_of, camg_model, {**camg_kw, **stab_kw},
                                trained_l1=camg_trained.get("l1", False), trained_diagonal=camg_w_diagonal_trained,
                                b=lambda: -solver.residual(x).detach(),
                                Jv=lambda v: (blocks["J"] @ v.unsqueeze(1)).squeeze(1), device=device, log=logger.info,
                                race_now=race_now)
                elif camg_d1 is not None:
                    amg = d1_build(A, {"gauge_field": (VELOCITY_WIDTH if free else None), "gauge": solver.gauge},
                                   {**camg_kw, **reuse_kw, **stab_kw, **l1_start_kw})
                else:
                    if camg_lam_reuse is not None:
                        camg_lam_reuse.tick()
                    amg = camg_cycle(A, gauge_field=(VELOCITY_WIDTH if free else None),
                                     gauge=solver.gauge,
                                     **{"modulator": camg_model if camg_model is not None else
                                        (camg_lam_reuse if camg_lam_reuse is not None else camg_lam_model),
                                        **camg_kw, **reuse_kw, **stab_kw, **l1_start_kw})
                primary = amg              # the CoupledAMG on A (M5: its fine level is the block assembly's operator)
                t_hierarchy = _o1_clock(device)
                if camg_kw.get("stabilize") and not stab_kw:
                    # a split's stability record is its learned mean-flow cycle's
                    camg_stab_cache["stab"] = (amg.mean if split_of is not None else amg).stab
                level0 = amg.plan.levels[0] if getattr(amg, "plan", None) is not None else None
                if level0 is not None and level0.row is not A.row:
                    # A reused plan keeps its own row / col; the lazily built arms
                    # below get A on THOSE index arrays, so their closures do not pin the factory's equal copy
                    # (0.27 GB at DHC88) for the preconditioner's lifetime and their plan look-up hits the identity
                    # path of same_block_pattern.  Same blocks in the same order (checked), same values object.
                    from Pipeline.CoupledNewton.block_pattern import same_block_pattern
                    if level0.n == A.shape[0] and same_block_pattern(level0.row, level0.col, A.row, A.col):
                        A = block_amg.BlockCSR(level0.row, level0.col, A.val, A.shape)
                if camg_model is not None and args.camg_select == "budget":
                    # race-free mode: clean timings show the linear solve 1.5-1.8x
                    # faster with the learned cycle while the second AMG build + two races cost ~4 s per
                    # linearisation; the budget hand-over alone bounds the cost, so the classical cycle is
                    # built only if a hand-over happens
                    gf, gauge_fn = (VELOCITY_WIDTH if free else None), solver.gauge
                    # the l1 rescue's cycle (None under the operator-decided l1 rule: the hand-over cycle runs the
                    # selection's plan, ``block_amg.l1_rescue_kw``)
                    rescue_kw = l1_rescue_kw_of(args, dict(smoother="gs", restriction="pg", pre=2, post=2,
                                                           gs_omega=0.7))
                    rescue = (lambda A=A, gf=gf, gauge_fn=gauge_fn, kw=rescue_kw: camg_cycle(
                        A, gauge_field=gf, gauge=gauge_fn, **kw)) if rescue_kw is not None else None
                    hand_over = (lambda A=A, gf=gf, gauge_fn=gauge_fn: camg_cycle(
                        A, gauge_field=gf, gauge=gauge_fn, smoother="gs", restriction="pg",
                        pre=2, post=2, gs_omega=0.7, l1=args.camg_l1))
                    if split_of is not None:
                        # The hand-over / rescue of a learned split is the CLASSICAL split
                        # (--order1-classical's cycle on the mean-flow block; l1 for the rescue)
                        hand_over = lambda: split_of({**split_classical_kw, **l1_start_kw})
                        split_rescue = l1_rescue_kw_of(args, split_classical_kw)
                        rescue = (lambda kw=split_rescue: split_of(kw)) if split_rescue is not None else None
                    fresh_of = ((lambda blocks, gf=gf, gauge_fn=gauge_fn, kw=rescue_kw: camg_cycle(
                        block_matrix_of(blocks), gauge_field=gf, gauge=gauge_fn, **kw))
                        if rescue_kw is not None and args.camg_rescue_current and split_of is None else None)
                    measured = (measured_rescue_kw(args, amg, lambda A=A: contextlib.nullcontext(A), gf)
                                if rescue is not None and hasattr(amg, "_reference_reduction") else {})
                    amg = block_amg.BudgetedLearned(
                        amg, hand_over,
                        max(args.camg_budget, 1), camg_budget_log, rescue=rescue,
                        rescue_factor=args.camg_rescue_factor, rescue_min_rtol=args.camg_rescue_min_rtol,
                        continue_learned=args.camg_learned_continue, fresh_of=fresh_of, **measured)
                elif camg_d1 is not None and camg_d1["retain"] > 0:
                    # --camg-d1-retain B: the deployed W's progress-based retention around the D1 cycle -- the head
                    # keeps a solve whose residual fell by the rescue factor within B iterations; otherwise the solve
                    # continues with the classical cycle on the same plan (lazy), or the l1 rescue for a loose stalled
                    # solve; both arms start from the fine values the hierarchy keeps (_blocks_on_device)
                    gf, gauge_fn = (VELOCITY_WIDTH if free else None), solver.gauge
                    hand_over = (lambda A=A, gf=gf, gauge_fn=gauge_fn: camg_cycle(
                        _blocks_on_device(A), gauge_field=gf, gauge=gauge_fn, **{**camg_kw, **reuse_kw, **l1_start_kw}))
                    rescue_kw = l1_rescue_kw_of(args, camg_kw)
                    rescue = ((lambda A=A, gf=gf, gauge_fn=gauge_fn, kw=rescue_kw: camg_cycle(
                        _blocks_on_device(A), gauge_field=gf, gauge=gauge_fn, **kw))
                        if rescue_kw is not None else None)
                    measured = (measured_rescue_kw(args, amg, lambda A=A: contextlib.nullcontext(_blocks_on_device(A)),
                                                   gf)
                                if rescue is not None and hasattr(amg, "_reference_reduction") else {})
                    amg = block_amg.BudgetedLearned(
                        amg, hand_over, camg_d1["retain"], camg_budget_log, rescue=rescue,
                        rescue_factor=args.camg_rescue_factor, rescue_min_rtol=args.camg_rescue_min_rtol,
                        continue_learned=True, late_stagnation=bool(getattr(args, "camg_d1_stall_handover", False)),
                        **measured)
                elif camg_model is not None:
                    # Cost safeguard for the learned smoother (selection): build the
                    # classical coupled AMG (GS 2+2, damping 0.7) as well and keep whichever has the
                    # smaller stationary contraction |r - J M r| / |r| on the same two random probes
                    # r = J e.  The answer is never at stake (residual certificate); the selection
                    # bounds the cost by the classical method's, up to the probe's noise.
                    classical = (split_of({**split_classical_kw, **l1_start_kw}) if split_of is not None else
                                 camg_cycle(A, gauge_field=(VELOCITY_WIDTH if free else None),
                                            gauge=solver.gauge, smoother="gs", restriction="pg",
                                            pre=2, post=2, gs_omega=0.7, l1=args.camg_l1))
                    Jv = lambda v: (blocks["J"] @ v.unsqueeze(1)).squeeze(1)
                    if args.camg_select == "race":
                        # Short GMRES race: each arm runs k GMRES steps
                        # from zero on the Newton right-hand side -F(x).  First version kept the arm
                        # with more residual decades PER SECOND; on cylinder Re40 that picked a cheap
                        # learned cycle that reduced fast early and then STAGNATED (3 solves ran out
                        # at 1600 its; 514.8 s vs 304.2 s with the contraction rule).  Now the learned
                        # arm is eligible only if it reduces at least as much PER ITERATION as the
                        # classical arm and moved at all, and then runs under a budget (below).
                        from SparseSolver.gmres import gmres as _gmres
                        b = -solver.residual(x).detach()

                        def race(M):
                            M(b)                                   # warm-up (lazy kernels, caches)
                            if device.type == "cuda":
                                torch.cuda.synchronize()
                            t0 = time.perf_counter()
                            res = _gmres(Jv, b, M=M, restart=args.camg_race_steps, max_cycles=1,
                                         rtol=0.0, atol=0.0)
                            if device.type == "cuda":
                                torch.cuda.synchronize()
                            dt = time.perf_counter() - t0
                            rel = float((b - Jv(res.x)).norm() / b.norm())
                            return rel, dt, -math.log10(max(rel, 1e-300)) / max(dt, 1e-9)

                        (l_rel, l_dt, l_rate), (c_rel, c_dt, c_rate) = race(amg), race(classical)
                        verdict = ("%d-step GMRES race: learned %.2e in %.3f s (%.1f dec/s) vs classical "
                                   "%.2e in %.3f s (%.1f dec/s)" % (args.camg_race_steps, l_rel, l_dt, l_rate,
                                                                   c_rel, c_dt, c_rate))
                        # a tie in which neither arm moved (1.00 vs 1.00 on cylinder Re20 step 2)
                        # goes to the classical arm, the stronger default
                        keep = l_rel <= camg_guard * c_rel and l_rel < 0.9
                        camg_race.append({"learned": [l_rel, l_dt], "classical": [c_rel, c_dt], "kept": keep})
                    else:
                        gen = torch.Generator(device="cpu").manual_seed(len(camg_timings))
                        probes = [Jv(solver.gauge(torch.randn(solver.n_unknowns, generator=gen,
                                                              dtype=solver.dtype).to(device))) for _ in range(2)]
                        score = lambda M: max(float((r - Jv(M(r))).norm() / r.norm()) for r in probes)
                        learned_c, classical_c = score(amg), score(classical)
                        verdict = "contraction %.3f vs classical %.3f" % (learned_c, classical_c)
                        keep = learned_c < camg_guard * classical_c
                    if not keep:
                        logger.info("FALLBACK: learned coupled AMG %s -> classical coupled AMG for this "
                                    "linearisation", verdict)
                        camg_fallbacks.append(len(camg_timings))
                        amg = classical
                    else:
                        logger.info("learned coupled AMG kept: %s", verdict)
                        if args.camg_budget > 0:
                            amg = block_amg.BudgetedLearned(amg, classical, args.camg_budget, camg_budget_log)
            rescue_kw = l1_rescue_kw_of(args, camg_kw)
            if (camg_model is None and args.camg_rescue_l1 and rescue_kw is None
                    and not camg_rescue_mode.get("rule_noted")):
                camg_rescue_mode["rule_noted"] = True
                logger.info("--camg-rescue-follows-l1-rule: under the operator-decided l1 rule no l1 rescue is built; "
                            "the cycle runs the selection's plan (the l1_rule / l1_levels columns of the Newton rows)")
            if (camg_model is None and rescue_kw is not None
                    and not (camg_d1 is not None and camg_d1["retain"] > 0)):
                if camg_rescue_log:
                    n_fresh = sum(bool(e.get("fresh")) for e in camg_rescue_log)
                    logger.info("stagnation rescue so far: %d of %d solves%s", sum(e["rescue"] for e in camg_rescue_log),
                                len(camg_rescue_log), ", %d continued on the step's own matrix" % n_fresh if n_fresh else "")
                    if getattr(args, "camg_rescue_measured", False):
                        logger.info("--camg-rescue-measured so far: %d stalled solves kept on the primary by the "
                                    "reading, %d handed back after the rescue's first cycle",
                                    sum(bool(e.get("refused")) for e in camg_rescue_log),
                                    sum(bool(e.get("handed_back")) for e in camg_rescue_log))
                    if getattr(args, "camg_rescue_rule", "factor") == "rate":
                        last = camg_rescue_log[-1]
                        logger.info("--camg-rescue-rule rate: %d solves kept on the primary by the rate test; last "
                                    "solve: rate %s, needed %s of %s", sum(bool(e.get("kept_by_rate"))
                                                                           for e in camg_rescue_log),
                                    last.get("rate"), last.get("needed"), last.get("left"))
                gf, gauge_fn = (VELOCITY_WIDTH if free else None), solver.gauge
                def rescue_arm(A=A, gf=gf, gauge_fn=gauge_fn, donor=primary, rescue_kw=rescue_kw):
                    """The l1 rescue on the primary's matrix; it differs only in its diagonal.  With the block kernels
                    it shares the primary's fine-level copies (block_amg ``fine_donor``) instead of filling its own,
                    after --camg-drop-block-values its numeric pass reads the fine values from the memory of that
                    copy, parked in host memory meanwhile (``fine_values_in_place``), instead of a rebuilt tensor, and
                    when the primary's plan was built from this A its plan takes that plan's finest level's symbolic
                    data (``level0_from``); with --camg-rescue-lend-reused also when the primary runs on a plan
                    reused from an earlier matrix of the same pattern."""
                    kw = dict(gauge_field=gf, gauge=gauge_fn, fine_donor=donor, **rescue_kw)
                    if getattr(donor, "fresh_plan", False) or (
                            getattr(args, "camg_rescue_lend_reused", False) and getattr(donor, "plan", None) is not None):
                        # the primary's plan was built from this very A (or, opt-in, reused for it): its finest
                        # level's symbolic data are what the primary runs on, and the l1 plan takes them
                        # (block_amg ``level0_from``) instead of building its own finest level
                        kw["level0_from"] = donor.plan
                    block_amg.memory_note("rescue build", lent="level0_from" in kw)
                    if (A.val is None and getattr(args, "camg_drop_block_values", False)
                            and hasattr(donor, "fine_values_in_place")):
                        with donor.fine_values_in_place() as val:
                            built = camg_cycle(block_amg.BlockCSR(A.row, A.col, val, A.shape), **kw)
                    else:
                        built = camg_cycle(_blocks_on_device(A), **kw)
                    block_amg.memory_note("rescue built")
                    return built
                if (getattr(args, "camg_l1_rescue_shared", False) and l1_start_kw and amg is primary
                        and not stab_kw and reuse_kw.get("modulator") is None
                        and not getattr(args, "camg_lam", None) and not getattr(args, "camg_turb_split", None)):
                    # --camg-l1-rescue-shared: the primary IS the l1 cycle the rescue would
                    # build on this A (same smoother settings, plain classical cycle): continue with it, build nothing
                    rescue_arm = primary
                    logger.info("--camg-l1-rescue-shared: the stagnation rescue of this build continues with its l1 "
                                "primary (no second l1 hierarchy)")
                fresh_of = ((lambda blocks, gf=gf, gauge_fn=gauge_fn, kw=rescue_kw: camg_cycle(
                    block_matrix_of(blocks), gauge_field=gf, gauge=gauge_fn, **kw))
                    if args.camg_rescue_current and split_of is None else None)
                @contextlib.contextmanager
                def primary_blocks(A=A, donor=primary):
                    """The build's matrix with its values on the device (the rescue arm's own access to them)."""
                    if (A.val is None and getattr(args, "camg_drop_block_values", False)
                            and hasattr(donor, "fine_values_in_place")):
                        with donor.fine_values_in_place() as val:
                            yield block_amg.BlockCSR(A.row, A.col, val, A.shape)
                    else:
                        yield _blocks_on_device(A)
                measured = (measured_rescue_kw(args, primary, primary_blocks, gf)
                            if hasattr(primary, "_reference_reduction") else {})
                amg = block_amg.StagnationRescue(
                    amg, rescue_arm,
                    args.camg_rescue_l1, args.camg_rescue_factor, camg_rescue_log,
                    min_rtol=args.camg_rescue_min_rtol, fresh_of=fresh_of,
                    **rescue_rule_kwargs(args, l1_primary=bool(camg_kw.get("l1")) or camg_l1_start["on"],
                                         maxit_lifted=camg_rescue_mode["maxit_lifted"]), **measured)
            if block_jacobian:
                # The step's Krylov operator is the hierarchy's fine-level matrix (= this J); the block
                # matrix itself is released by the mapping (the hierarchy and the rescue closure hold it)
                blocks.attach_operator(primary.fine_operator())
                blocks.release_blocks()
            if getattr(primary, "block_values_dropped", False):
                # M5 --camg-drop-block-values: the hierarchy released its block values; the factory's own A (the
                # rescue closure's) lets go of the fine values too, so their device memory is freed, and keeps the
                # exact rebuild from the fine scalar copy (_blocks_on_device) for a rescue arm built later
                A.val = None
                A.rebuild_values = primary.fine_block_values
            if getattr(args, "camg_direct_escalate", 0):
                # Opt-in: the cuDSS LU of this very J, built only if a solve escalates
                from Pipeline.CoupledNewton.robust_linear import DirectEscalation, field_csr_from_blocks
                pin_row = solver.velocity if (args.lu_pin == "always" or free) else None

                def direct_of(A=A, J=(None if block_jacobian else J), pin_row=pin_row):
                    crow, col, val = ((J.crow_indices(), J.col_indices(), J.values()) if J is not None
                                      else field_csr_from_blocks(A))
                    return camg_direct_factory.build(crow, col, val, pin_row=pin_row, gauge=solver.gauge)
                amg = DirectEscalation(amg, direct_of, args.camg_direct_escalate, camg_escalation_log,
                                       logger=logger.info)
                if camg_escalation_log:
                    logger.info("direct escalation so far: %d of %d solves escalated",
                                sum(1 for e in camg_escalation_log if e.get("at_cycle")), len(camg_escalation_log))
            if device.type == "cuda":
                torch.cuda.synchronize()
            camg_timings.append(time.time() - started_build)
            logger.info("coupled AMG rebuilt in %.3f s: %s", camg_timings[-1], amg.report())
            if t_blocks is not None and t_hierarchy is not None:
                logger.info("build split (LPCNS_O1_TIMERS, synchronised): block matrix %.3f s, hierarchy %.3f s (plan, "
                            "numeric pass, stability check or cached blend, fast fill), wrappers %.3f s",
                            t_blocks - started_build, t_hierarchy - t_blocks,
                            started_build + camg_timings[-1] - t_hierarchy)
            memory.append(("built", camg_memory()))
            if memory[0][1] is not None:
                # --component-timers on CUDA: "at the call" is what the new build
                # starts from (solve_newton has released the previous preconditioner and J by then), the peaks are
                # the step's so far (assembly included)
                logger.info("camg build memory (GiB allocated / peak since the step began): %s",
                            ", ".join("%s %.2f / %.2f" % (stage, *m) for stage, m in memory))
            if camg_plan_reuse is not None:
                logger.info("camg plan reuse so far: %s", camg_plan_reuse.summary())
            if warp_backend is not None and getattr(warp_backend, "is_large", lambda: False)() \
                    and "Pipeline.CoupledNewton.block_amg_warp" in sys.modules:
                # Large meshes keep the Warp products' segment tables (7.5 GiB at
                # 152^3) in pinned host memory between builds; the next build copies them back
                moved = sys.modules["Pipeline.CoupledNewton.block_amg_warp"].spill_all()
                if moved:
                    logger.info("camg Warp segment tables: %.2f GiB to pinned host memory until the next build "
                                "(allocated now %.2f GiB)", moved / 2 ** 30, torch.cuda.memory_allocated() / 2 ** 30)
                if os.environ.get("LPCNS_EMPTY_CACHE_AFTER_SPILL") == "1":
                    torch.cuda.empty_cache()          # W12h probe: the cached free segments back to the driver
                free, total = torch.cuda.mem_get_info()
                logger.info("device memory after the build: %.2f of %.2f GiB free at the driver; torch allocated "
                            "%.2f, reserved %.2f GiB (W12h)", free / 2 ** 30, total / 2 ** 30,
                            torch.cuda.memory_allocated() / 2 ** 30, torch.cuda.memory_reserved() / 2 ** 30)
            mem_snapshot_at_build(logger.info, "built")   # LPCNS_MEM_SNAP (W12f); nothing when unset
            return amg
        learned_factory.camg_l1_start = camg_l1_start     # F1b-b: main's progress hook switches it off
        learned_factory.camg_lam_reuse = camg_lam_reuse   # E-ABL: main's rung hook (--camg-lam-reuse-rung)
        learned_factory.camg_reuse = camg_reuse           # close9: main's rung hook (--camg-keep-w)
        learned_factory.camg_config = camg_config         # O1-C: main's rung hook (--camg-config-every rung)
        learned_factory.camg_d1 = camg_d1                 # Order1Headroom B1: the D1 checks, read at the end of the run
        learned_factory.camg_rescue_mode = camg_rescue_mode   # II-4 job 5: main's cap-fallback hook lifts the maxit
        logger.info("inner preconditioner: coupled block AMG %s (%s)%s", camg_spec, camg_kw,
                    " with l1 smoothers until the first full step (--camg-l1-start)" if camg_l1_start["on"] else "")
    elif args.learned_action == "cudss":
        # Opt-in (experiments/NaturalConvection/CoupledNewton/RobustLinear/README.md): the EXACT LU of the assembled J on the GPU
        # (NVIDIA cuDSS, SparseSolver.cudss), the --learned-action lu control without the host round trip.  One cuDSS
        # object per run: the reordering + symbolic analysis is done once per sparsity pattern (a fixed structural
        # pattern: once per run), every rebuild is a numeric refactorisation.  Pressure level pinned as for lu.
        from Pipeline.CoupledNewton.robust_linear import DirectLUFactory
        direct_factory = DirectLUFactory(precision=args.cudss_precision, hybrid_gib=args.cudss_hybrid_gib,
                                         log=logger.info, probe_tol=getattr(args, "cudss_probe", 0.0) or None)
        direct_factory.n_cell = int(getattr(solver, "n_cell", 0) or 0) or None   # the probe's row layout

        def learned_factory(blocks, x):
            J = blocks["J"]
            if J.layout != torch.sparse_csr:
                J = J.to_sparse_csr()
            pin = args.lu_pin == "always" or solver.resolve_gauge(x)
            return direct_factory.build(J.crow_indices(), J.col_indices(), J.values(),
                                        pin_row=solver.velocity if pin else None, gauge=solver.gauge)
        learned_factory.direct_factory = direct_factory
        logger.info("inner preconditioner: cuDSS GPU LU (%s%s) of the assembled J", args.cudss_precision,
                    f", hybrid memory {args.cudss_hybrid_gib:g} GiB" if args.cudss_hybrid_gib else "")
    elif args.learned_action in ("bilu0", "bilu1", "bilu0-mc", "bilu1-mc"):
        # Classical control inside the production loop: block ILU(k) of the cell blocks of the SAME assembled
        # Jacobian, factorised and applied on the GPU (level-scheduled, SparseSolver.block_ilu), rebuilt by the
        # preconditioner policy.  A free pressure level is pinned for the factorisation only; every output is
        # gauged.
        from SparseSolver.block_ilu import field_blocked_factory
        bilu_log = []
        bilu = field_blocked_factory(solver.num_fields, device, fill=int(args.learned_action[4]), log=bilu_log,
                                     order="mc" if args.learned_action.endswith("-mc") else "rcm",
                                     pin=lambda x: solver.velocity if solver.resolve_gauge(x) else None)

        def learned_factory(blocks, x):
            apply = bilu(blocks, x)
            logger.info("block ILU rebuilt: %s", bilu_log[-1])
            gauge = solver.gauge
            return lambda v: gauge(apply(v))
        logger.info("inner preconditioner: block ILU(%s), %s order, of the assembled J on the device",
                    args.learned_action[4], "multicolour" if args.learned_action.endswith("-mc") else "RCM")
    elif args.learned_action in ("ilu", "lu"):
        # Classical control inside the production loop: a threshold incomplete
        # factorisation of the SAME assembled approximate Jacobian the block
        # preconditioner is built from, rebuilt at every reassembly.  SuperLU's
        # ILUTP runs on the CPU, so the factorisation and every application
        # cross the bus; that cost is the point of the comparison and is logged.
        import numpy as np
        import scipy.sparse as sp
        import scipy.sparse.linalg as spla
        ilu_timings = []

        def learned_factory(blocks, x):
            started_build = time.time()
            J = blocks["J"].to_sparse_csr()
            A = sp.csr_matrix((J.values().detach().cpu().numpy(), J.col_indices().cpu().numpy(),
                               J.crow_indices().cpu().numpy()), shape=J.shape).tocsc()
            if args.fixed_jacobian_pattern == "structural":
                # The structural J stores exact zeros, and COLAMD orders by pattern; dropped,
                # the factorised matrix is today's (same CSC arrays).
                A.eliminate_zeros()
            if args.learned_action == "lu":
                # EXACT factorisation: the "perfect preconditioner" control
                # that separates a linear-solver failure from a nonlinear one.  The
                # constant pressure mode, if free, is pinned for the factorisation only
                # (and only then, --lu-pin gauge: lu_factorisation_matrix).
                pin = args.lu_pin == "always" or solver.resolve_gauge(x)
                lu = spla.splu(lu_factorisation_matrix(A, solver.velocity, pin), **lu_options(args))
                build = time.time() - started_build
                ilu_timings.append({"build_seconds": build, "nnz_J": int(A.nnz),
                                    "nnz_LU": int(lu.L.nnz + lu.U.nnz)})
                logger.info("exact LU rebuilt in %.3f s (fill %.1fx)%s", build,
                            ilu_timings[-1]["nnz_LU"] / max(A.nnz, 1),
                            "" if lu_options(args) == {"permc_spec": "COLAMD"} else f" [splu {lu_options(args)}]")
                gauge = solver.gauge

                def apply_lu(v):
                    y = lu.solve(v.detach().cpu().numpy().astype(np.float64))
                    return gauge(torch.as_tensor(y, device=device, dtype=solver.dtype))
                return apply_lu
            try:
                lu = spla.spilu(A, drop_tol=args.ilu_drop_tol, fill_factor=args.ilu_fill,
                                permc_spec="COLAMD")
            except RuntimeError as singular:
                # A case with no Dirichlet pressure anywhere (all-wall cavities, the
                # fixedFluxPressure natural-convection box) has the constant pressure
                # mode in J's null space and SuperLU refuses it ("Factor is exactly
                # singular").  Pin that mode IN THE PRECONDITIONER ONLY: one pressure
                # diagonal raised to the largest diagonal magnitude.  The Krylov
                # operator is untouched and every direction is gauged below.
                row = solver.velocity
                scale = float(abs(A.diagonal()).max())
                pinned = A.tolil()
                pinned[row, row] = pinned[row, row] + scale
                # Rows whose diagonal is exactly zero (structurally empty rows, e.g. a
                # 2D case's out-of-plane block) get the same scale on the diagonal.
                zero = np.flatnonzero(A.diagonal() == 0.0)
                for r in zero:
                    pinned[r, r] = scale
                A = pinned.tocsc()
                logger.info("ILU: %d zero-diagonal rows regularised for the factorisation",
                            len(zero))
                lu = spla.spilu(A, drop_tol=args.ilu_drop_tol, fill_factor=args.ilu_fill,
                                permc_spec="COLAMD")
                logger.info("ILU: %s -> constant pressure mode pinned at row %d for the "
                            "factorisation only", singular, row)
            build = time.time() - started_build
            ilu_timings.append({"build_seconds": build, "nnz_J": int(A.nnz),
                                "nnz_LU": int(lu.L.nnz + lu.U.nnz)})
            logger.info("learned action rebuilt: ILUT(drop=%.0e, fill=%.1f) in %.3f s "
                        "(%d rebuilds so far, fill %.1fx)", args.ilu_drop_tol, args.ilu_fill,
                        build, len(ilu_timings), ilu_timings[-1]["nnz_LU"] / max(A.nnz, 1))
            gauge = solver.gauge

            def apply(v):
                y = lu.solve(v.detach().cpu().numpy().astype(np.float64))
                return gauge(torch.as_tensor(y, device=device, dtype=solver.dtype))
            return apply
        logger.info("inner preconditioner: cached local action (ilu), pressure %s, cycles %d",
                    args.pressure, args.cycles)
        if args.learned_action == "lu" and args.lu_pin != "always":
            logger.info("LU pressure pin: the constant pressure mode is %s for the factorisation (--lu-pin %s)",
                        "PINNED" if solver.resolve_gauge(cold) else "NOT pinned", args.lu_pin)
    elif args.learned_action:
        from Pipeline.CoupledNewton.adapter import ConstraintAdapter
        from Pipeline.CoupledNewton.learned_action import LearnedAction
        cyclic_face = case.fv_graph.graph_face.cyclic_face
        learned_model = None
        if args.learned_action != "classical":
            from NNmodels.local_action import LocalActionNet
            learned_model = LocalActionNet(solver.num_fields, hidden=args.learned_hidden,
                                           passes=args.learned_passes).to(device)
            learned_model.load_state_dict(torch.load(args.learned_action, map_location=device))
            learned_model.eval()
        learned_pressure = args.pressure
        patch_mask = None
        if args.patch_mask:
            import numpy as _np
            patch_mask = torch.as_tensor(_np.load(args.patch_mask), dtype=torch.bool,
                                           device=device)
        if learned_pressure == "auto":
            learned_pressure = "laplacian" if solver.mwi else "simple"
        learned_timings = []

        def learned_factory(blocks, x):
            started_build = time.time()
            adapter = ConstraintAdapter(solver, blocks, cyclic_face=cyclic_face, state=x)
            action = LearnedAction(adapter, model=learned_model, cycles=args.cycles,
                                   pressure=learned_pressure, coarse=True,
                                   patch_mask=patch_mask,
                                   patch_mode=args.patch_mode).prepare(differentiable=False)
            if device.type == "cuda":
                torch.cuda.synchronize(device)
            learned_timings.append({"build_seconds": time.time() - started_build,
                                    **action.summary()})
            logger.info("learned action rebuilt: %s in %.3f s (%d rebuilds so far)",
                        "untrained/classical" if learned_model is None else args.learned_action,
                        learned_timings[-1]["build_seconds"], len(learned_timings))
            return action
        logger.info("inner preconditioner: cached local action (%s), pressure %s, cycles %d",
                    args.learned_action, learned_pressure, args.cycles)
    return learned_factory, learned_model, camg_plan_reuse


def _watched_rows(monitor, multipliers) -> list:
    """The rows of the attempt the monitor just aborted, each with the nu multiplier of its rung."""
    rows = []
    for entry in monitor.rows:
        entry = dict(entry)
        rung = int(entry.get("rung", 1))
        entry.setdefault("nu_multiplier", multipliers[min(rung, len(multipliers)) - 1])
        rows.append(entry)
    return rows


def run_learned_init(one_ramp, monitor, x0, x_fallback, args, solver, logger, started) -> dict:
    """``--learned-init-abort`` / ``--learned-init-fallback`` (P1 deployment mode, design 1.4): the first ladder from
    the learned start under the monitor; on ``LearnedInitAbort`` the fallbacks in order, each a fresh ladder (the
    JFNK policy reset by ``one_ramp``), until one certifies.  The aborted rows and every attempt stay in the history
    and in the timer; ``ramp["learned_init"]`` records what happened.

    Motivation (a 'ramp:' fallback from the head's u0 stalled unwatched for 20 steps of
    17.5 s until the run's cap): with ``--learned-init-watch all-but-last`` (default) every fallback but the LAST is
    watched by the same monitor (its rules and the stall rules); a watched fallback that aborts is logged
    ``LEARNED-INIT FALLBACK i/n ABORTED at newton step k after T s (t s in this fallback): reason``, its rows join
    the history, and the ladder moves on.  The last fallback (normally 'rest', the classical continuation) runs
    unwatched, as the classical path would.  The DIRECT result stays the attempt before the first ABORT line."""
    monitor.arm(started)
    try:
        ramp = one_ramp(x0)
        ramp["learned_init"] = {"aborted": False}
        return ramp
    except LearnedInitAbort:
        pass
    finally:
        monitor.disarm()
    direct = ramp_multipliers(args.nu_ramp)
    first = direct[0]
    failed = _watched_rows(monitor, direct)
    logger.warning("LEARNED-INIT ABORT at newton step %d after %.1f s: %s", monitor.step, monitor.seconds,
                   monitor.reason)
    ladder = parse_learned_init_fallback(args.learned_init_fallback)
    record = {"aborted": True, "step": monitor.step, "seconds": monitor.seconds, "reason": monitor.reason,
              "fallbacks": []}
    if not ladder:
        logger.warning("LEARNED-INIT FALLBACK none: the run stops at the aborted start")
        return {"x": x0, "history": failed, "rungs_solved": 1, "rungs_total": 1, "complete": False,
                "final_multiplier": first, "learned_init": record}
    history, ramp = failed, None
    ptc = args.ptc
    watch = getattr(args, "learned_init_watch", "all-but-last")
    for index, (kind, multipliers) in enumerate(ladder, start=1):
        args.ptc = bool(ptc or kind == "ptc")
        args.nu_ramp = (None if kind == "ptc" or multipliers is None or len(multipliers) <= 1 else
                        ",".join(repr(m) for m in multipliers[:-1]))
        start = None if kind == "rest" else (x_fallback if (kind == "ramp" and x_fallback is not None) else x0)
        watched = watch == "all-but-last" and index < len(ladder)
        adaptive = kind == "rest" and getattr(args, "continuation", "ladder") == "adaptive"
        logger.warning("LEARNED-INIT FALLBACK %d/%d: %s from %s (%s)%s", index, len(ladder), kind,
                       "rest" if start is None else ("the fallback state" if start is x_fallback else "the start"),
                       "the adaptive continuation" if adaptive else f"nu multipliers {ramp_multipliers(args.nu_ramp)}",
                       ", watched" if watched else "")
        if watched:
            began = time.time()
            monitor.arm(started)
            try:
                ramp = one_ramp(start)
            except LearnedInitAbort:
                history = history + _watched_rows(monitor, ramp_multipliers(args.nu_ramp))
                logger.warning("LEARNED-INIT FALLBACK %d/%d ABORTED at newton step %d after %.1f s (%.1f s in this "
                               "fallback): %s", index, len(ladder), monitor.step, monitor.seconds,
                               time.time() - began, monitor.reason)
                record["fallbacks"].append({"kind": kind, "multipliers": ramp_multipliers(args.nu_ramp),
                                            "certified": False, "aborted": True, "step": monitor.step,
                                            "seconds": monitor.seconds, "reason": monitor.reason})
                continue
            finally:
                monitor.disarm()
        else:
            ramp = one_ramp(start)
        history = history + ramp["history"]
        reached = float(solver.residual(ramp["x"]).abs().max())
        certified = bool(ramp["complete"] and reached < args.gate)
        record["fallbacks"].append({"kind": kind, "multipliers": ramp_multipliers(args.nu_ramp),
                                    "certified": certified, "max_abs": reached})
        if adaptive and "continuation" in ramp:
            # the adaptive path's accepted multipliers, not the ladder the 'rest' spec would have named
            record["fallbacks"][-1]["multipliers"] = list(ramp["continuation"]["accepted_multipliers"])
            record["fallbacks"][-1]["continuation"] = "adaptive"
        if certified:
            break
    args.ptc = ptc
    ramp["history"] = history
    ramp["learned_init"] = record
    return ramp


_MEM_SNAP = {}


def mem_snapshot_at_build(log=print, stage="call"):
    """LPCNS_MEM_SNAP=<file>: the CUDA caching allocator's history, recorded from the
    start of main, dumped at the first preconditioner build -- at its call (``<file>``) and once it is built
    (``<file>.built``, W12f) -- every live tensor with the Python stack that allocated it (w8/memsnap_top.py groups
    them).  Unset: nothing recorded, nothing dumped."""
    path = os.environ.get("LPCNS_MEM_SNAP")
    if not path or _MEM_SNAP.get(stage) or not torch.cuda.is_available():
        return
    _MEM_SNAP[stage] = True
    if stage != "call":
        path = f"{path}.{stage}"
    torch.cuda.synchronize()
    torch.cuda.memory._dump_snapshot(path)
    log("LPCNS_MEM_SNAP: allocator snapshot at the first preconditioner build (%s) -> %s (allocated %.2f GiB)"
        % (stage, path, torch.cuda.memory_allocated() / 2 ** 30))



def _pt_camg_kwargs(text):
    """``--pt-camg 'pre=2,post=2,block_values=fp32'`` -> dict (ints, floats, booleans 0/1 by key, strings)."""
    out = {}
    for item in filter(None, (t.strip() for t in (text or "").split(","))):
        k, v = item.split("=")
        k, v = k.strip(), v.strip()
        if k in ("fast_kernels", "block_kernels", "lean", "cuda_graph", "l1"):
            out[k] = v not in ("0", "false", "False")
        else:
            try:
                out[k] = int(v)
            except ValueError:
                try:
                    out[k] = float(v)
                except ValueError:
                    out[k] = v
    return out


def _engineering_stop_rule(args, solver):
    """``--residual-control`` / ``--qoi-stagnation`` as one ``solve_newton(stop_rule=)`` (None when neither is set)."""
    if getattr(args, "foam_bridge", None):
        # --foam-bridge REQUEST.json (entries/dc_foam_solve.py): the caller's OpenFOAM-layout run directory is served
        # by Post_process.foam_bridge.FoamBridge, which is the stop rule (the caller's thresholds) and the writer
        import json as _json
        from Pipeline.CoupledNewton.stop_rules import field_slices as _fs
        from Post_process.foam_bridge import CASES, FoamBridge
        req = _json.load(open(args.foam_bridge))
        bridge = FoamBridge(solver, args.dataset_dir, req["runDir"], req, _fs(solver.unknowns, int(solver.n_cell)),
                            case=CASES.get(str(req["runDir"])))
        solver._foam_bridge = bridge
        return bridge
    if (getattr(args, "residual_control", None) is None and getattr(args, "qoi_stagnation", None) is None
            and getattr(args, "heat_closure", None) is None and getattr(args, "dctwin_plateau", None) is None):
        return None
    import os
    import torch
    from Pipeline.CoupledNewton.stop_rules import (AllOf, HeatClosure, MeritGuard, QuantityStagnation,
                                                   ResidualControl, field_slices, parse_residual_control)
    n = int(solver.n_cell)
    slices = field_slices(solver.unknowns, n)
    rules = []
    if args.residual_control:
        rules.append(ResidualControl(parse_residual_control(args.residual_control), slices))
    if args.qoi_stagnation is not None:
        su, st = slices["U"], slices["T"]
        case_dir = args.dataset_dir
        if os.path.exists(os.path.join(case_dir, "dctwin_map.json")):
            from Post_process.dc_qoi import DCQuantities
            dq = DCQuantities(case_dir, device=solver.device, dtype=solver.dtype)

            # registration section 4c: the TEMPERATURES only (ACU return flux-mean T, server intake area-mean T);
            # the heats in W enter only through --heat-closure
            t_idx = [i for i, name in enumerate(dq.names) if name.startswith(("return_T:", "inlet_T:"))]

            def quantities(x):
                return dq.vector(dq(x[su].reshape(3, n).T, x[st]))[t_idx]
        else:
            # no dctwin map: the volume-mean and the maximum T (registration section 4c)
            vol = solver.solver.cell_volume.reshape(-1)[:n].to(solver.dtype)

            def quantities(x):
                t = x[st]
                return torch.stack([(t * vol).sum() / vol.sum(), t.max()])
        rules.append(QuantityStagnation(quantities, args.qoi_stagnation, window=getattr(args, "qoi_window", 2),
                                        span=getattr(args, "qoi_window_span", False)))
    if getattr(args, "merit_guard", False):
        rules.append(MeritGuard(slices, window=getattr(args, "qoi_window", 10),
                                level=getattr(args, "merit_guard_level", 1e-2), drop=getattr(args, "merit_guard_drop", 2.0)))
    if getattr(args, "heat_closure", None) is not None:
        case_dir = args.dataset_dir
        map_path = os.path.join(case_dir, "dctwin_map.json")
        if not os.path.exists(map_path):
            raise ValueError("--heat-closure needs a dctwin_map.json in the case directory (the declared IT heat)")
        import json
        from Parse_mesh.dctwin_bc import declared_it_heat
        from Post_process.dc_qoi import DCQuantities
        su, st = slices["U"], slices["T"]
        dmap = json.load(open(map_path))
        declared = float(dmap.get("declared_it_heat_W") or declared_it_heat(dmap["servers"]))
        hq = DCQuantities(case_dir, device=solver.device, dtype=solver.dtype)

        def heat(x):
            return hq(x[su].reshape(3, n).T, x[st])["heat_W:total"]
        rules.append(HeatClosure(heat, declared, args.heat_closure))
    if getattr(args, "dctwin_plateau", None) is not None:
        case_dir = args.dataset_dir
        map_path = os.path.join(case_dir, "dctwin_map.json")
        if not os.path.exists(map_path):
            raise ValueError("--dctwin-plateau needs a dctwin_map.json in the case directory")
        import json
        from Parse_mesh.dctwin_bc import declared_it_heat
        from Post_process.dc_qoi import DCQuantities
        from Pipeline.CoupledNewton.stop_rules import DctwinPlateau
        su, st = slices["U"], slices["T"]
        dmap = json.load(open(map_path))
        declared = float(dmap.get("declared_it_heat_W") or declared_it_heat(dmap["servers"]))
        pq = DCQuantities(case_dir, device=solver.device, dtype=solver.dtype)
        r_idx = [i for i, name in enumerate(pq.names) if name.startswith("return_T:")]

        # the removed heat as dctwin's rule measures it: the enthalpy flow through the unit patches with the
        # solver's own face flux (DCQuantities.__call__, ``un``); LPCNS_DC_HEAT=supply: the earlier total.  The
        # face order is checked once on the supply patches, whose discrete flow is prescribed
        _flux_heat = {"on": os.environ.get("LPCNS_DC_HEAT", "flux") == "flux", "checked": False}

        def _un():
            un = getattr(solver.solver, "face_volumetric_flux", None)
            if not _flux_heat["on"] or un is None:
                return None
            un = un.reshape(-1)
            if not _flux_heat["checked"]:
                _flux_heat["checked"] = True
                q_disc = 0.0
                for a_ in dmap["acus"].values():
                    s_, n_ = pq.span[a_["supply"]["patch"]]
                    q_disc += float((un[s_:s_ + n_].double().cpu().numpy() * pq.face_area_np[s_:s_ + n_]).sum())
                q_decl = sum(float(a_["supply"]["Q"]) for a_ in dmap["acus"].values())
                log_ = __import__("logging").getLogger("Logger.Collocated-Newton")
                if abs(q_disc + q_decl) > 0.01 * max(q_decl, 1e-30):
                    _flux_heat["on"] = False
                    log_.warning("dctwin rule: discrete supply flow %.4f m3/s against %.4f declared; the heat total "
                                 "falls back to supply flow times return rise", q_disc, -q_decl)
                    return None
                log_.info("dctwin rule: heat total from the units' enthalpy flow (discrete supply flow %.4f m3/s)", q_disc)
            return un

        def _out(x):
            return pq(x[su].reshape(3, n).T, x[st], un=_un())

        _once = {}

        def _returns(x):
            # DctwinPlateau reads returns(x) and then heat(x) of the same iterate: the quantities are evaluated once
            _once["out"] = o = _out(x)
            return pq.vector(o)[r_idx]

        def _heat(x):
            o = _once.pop("out", None)
            return (o if o is not None else _out(x))["heat_W:total"]

        rules.append(DctwinPlateau(_returns, _heat, declared,
                                   tol=args.dctwin_plateau, window=args.dctwin_window, heat_rel=args.dctwin_heat))
    rule = AllOf(*rules)
    rule.needs_flux = True              # the dctwin rule reads the face flux of the state it is shown
    __import__("logging").getLogger("Logger.Collocated-Newton").info("engineering stop rule on the final rung: %s",
                ", ".join(type(r).__name__ for r in rule.rules))
    return rule

def main(argv=None) -> int:
    if os.environ.get("LPCNS_MEM_SNAP") and torch.cuda.is_available():
        torch.cuda.memory._record_memory_history(max_entries=1_000_000)
    args = build_parser().parse_args(argv)
    normalise_learned_action(args)
    if getattr(args, "cudss_deterministic", False):
        os.environ["LPCNS_CUDSS_DETERMINISTIC"] = "1"   # read by every cuDSS factorisation left at deterministic=None
    if getattr(args, "nproc", 0):
        # --nproc (opt-in, docs/development/multi-gpu.md): the partitioned solve runs in its own processes;
        # 0 (the default) never reaches this line's body, so the single-GPU path below is unchanged.
        from Parallel.solve import run_from_entry
        return run_from_entry(args)
    dataset_dir = os.path.abspath(args.dataset_dir)
    case_dir = os.path.dirname(dataset_dir)
    with open(os.path.join(dataset_dir, "fvconfig.toml"), "rb") as handle:
        fvconfig = tomllib.load(handle)
    refuse_an_unsteady_case(fvconfig)
    refuse_the_polynomial_over_a_block_preconditioner(args)
    refuse_misused_learned_init_flags(args)
    refuse_a_misused_continuation(args)
    if args.colouring_cache_refresh and not args.colouring_cache:
        # Refused before the case loads: a refresh of no cache would be ignored silently.
        raise ValueError("--colouring-cache-refresh needs --colouring-cache DIR")
    # --rans-recipe (opt-in): BEFORE the JFNK policy is resolved, because it may choose the operator and
    # the rebuild policy; {} (nothing touched) without the flag.
    rans_recipe_changes = apply_rans_recipe(args, fvconfig, sys.argv[1:] if argv is None else list(argv))
    # --cold-start (opt-in): refused before the load when it cannot apply; a no-op when off.
    refuse_a_misused_cold_start(args, fvconfig)
    # --nl-accel (opt-in): refused before the load where it cannot apply; a no-op when off.
    refuse_a_misused_nl_accel(args)
    # JFNK mode: the run's one preconditioner policy, or None (the default solve); refused before the load.
    jfnk_policy = jfnk_policy_from_args(args)
    # Nonlinear elimination: the run's eliminator, or None (the default solve); refused before the load.
    eliminator = elimination_from_args(args)
    nu = constant_of(fvconfig["theta_pde"], "mu") / constant_of(
        fvconfig["theta_pde"], "rho")

    certificate_start = None
    if args.save_final_state:
        from entries.steady_final_artifacts import (
            data_root_for_dataset, hash_case_inputs, inventory_case,
            sha256 as artifact_sha256, source_fingerprint, source_head,
            source_module_paths,
        )
        code_root = Path(__file__).resolve().parents[2]
        source_module_paths(code_root, required=(
            'FVdomain.load_case', 'Pipeline.CoupledNewton.collocated_newton',
            'Utils.Logger', 'entries.steady_final_artifacts'))
        data_root = data_root_for_dataset(dataset_dir)
        case_identity = inventory_case(
            dataset_dir,
            data_root / "docs/provenance/steady-cases-20260927/inventory.json",
            data_root)
        start_kind = ("state" if args.state is not None else
                      "openfoam" if args.init == "openfoam" else args.init)
        start_asset = (args.state if args.state is not None else
                       args.foam if start_kind == "openfoam" else None)
        certificate_start = {
            "code_root": code_root,
            "data_root": data_root,
            "case": case_identity,
            "input_sha256": hash_case_inputs(dataset_dir),
            "source_tree_sha256": source_fingerprint(code_root),
            "entry_sha256": artifact_sha256(__file__),
            "source_head": source_head(code_root),
            "start": {"kind": start_kind,
                      "asset_path": None if start_asset is None else str(Path(start_asset).resolve()),
                      "asset_sha256": (None if start_asset is None or
                                       not Path(start_asset).is_file() else
                                       artifact_sha256(start_asset))},
        }

    from Utils import get_param

    original_argv = sys.argv
    try:
        sys.argv = [original_argv[0]]
        params = get_param.params()
    finally:
        sys.argv = original_argv
    params.on_gpu = args.gpu
    params.dataset_dir = dataset_dir
    params.dataset_size = 1
    params.batch_size = 1

    timestamp = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
    run_name = timestamp if args.exp_name is None else f"{timestamp}-{args.exp_name}"
    logger = Logger("Collocated-Newton", params=params, use_csv=False,
                    saving_path=os.path.join(case_dir, "Logger",
                                             "Collocated-Newton", run_name))
    params.state_save_dir = logger.saving_path
    # A Re=40 production run's training.log recorded its 82.8 s,
    # 19 Newton steps and max|r| = 4.42e-13 without recording that it was run
    # with --pressure mwi_block --nu-ramp 8, and neither flag is a default: on
    # the shipped defaults the same case parks at 1.58e-07 forever.  The
    # artifact carrying a record now carries the configuration that produced it.
    logger.info("argv: %s", shlex.join(sys.argv))           # quoted: an empty value (--nu-ramp '') survives
    if rans_recipe_changes:
        logger.info("RANS-RECIPE: case turbulence %r -> %s; pass a flag explicitly to override a piece",
                    case_turbulence(fvconfig), rans_recipe_changes)
    logger.info("resolved flags: %s",
                ", ".join(f"{k}={v!r}" for k, v in sorted(vars(args).items())))

    device = torch.device(f"cuda:{args.gpu}"
                          if args.gpu >= 0 and torch.cuda.is_available() else "cpu")
    logger.info("Loading %s on %s ...", dataset_dir, device)
    started = time.time()
    case = load_case(dataset_dir, params, stage="solver", pool="legacy",
                     device=device)
    moved = promote_case_to_double(case)
    if getattr(args, "mwi_d_scale", None) is not None:
        case.integrator._solver._mwi_d_scale = float(args.mwi_d_scale)
    apply_buoyant_recipe(args, case.integrator._solver,
                         sys.argv[1:] if argv is None else list(argv), logger)
    if getattr(args, "conservative_cyclic_flux", False):
        # opt-in: one volumetric flux per periodic face pair (BaseSolver.conservative_cyclic_flux);
        # set before the solver evaluates its first residual
        case.integrator._solver._conservative_cyclic_flux = True
        logger.info("--conservative-cyclic-flux: every cyclic face pair carries one volumetric flux (+-(F_a - F_b)/2); "
                    "%d pairs", int(case.integrator._solver._cyclic_face_pairs()[0].numel()))
    solver = CollocatedSolver(
        case.integrator._solver, device=device,
        momentum_interpolation=None if args.mwi is None else args.mwi == "on",
        coefficient=args.coefficient)
    logger.info(
        "Collocated square system: %d cells x %d (%s) = %d unknowns on %s "
        "(%d mesh tensors promoted to float64), nu = %.6g, convection %r, "
        "gradient %r, momentum interpolation %s (%s d_f), built in %.1f s",
        solver.n_cell, solver.num_fields, ", ".join(solver.field_names()),
        solver.n_unknowns, device, moved, nu,
        solver.solver.U_scheme, solver.solver.params.fvschemes["gradSchemes"],
        "MWI" if solver.mwi else "OFF (control arm: the grid-parity pressure "
        "mode is unconstrained)",
        "frozen" if solver.solver._mwi_frozen_coefficient else "implicit",
        time.time() - started)
    if getattr(args, "spanwise_invariant", False):
        # opt-in: iterates kept invariant across the two cell layers (CollocatedSolver.enable_spanwise_invariance)
        logger.info("--spanwise-invariant: %d cell pairs across the two layers; every iterate, step and trial is "
                    "replaced by its two-layer mean", solver.enable_spanwise_invariance())
    if getattr(args, "log_turbulence", False):
        # opt-in: Newton in ln of the primal turbulence fields (CollocatedSolver.enable_log_turbulence)
        logger.info("--log-turbulence: Newton iterates on the logarithms of %s", solver.enable_log_turbulence())
    if os.environ.get("LPCNS_DEBUG_NONDETERMINISTIC") == "1":
        # The reduction-order diagnostic (CollocatedSolver); never a timed or reported solve
        logger.warning("LPCNS_DEBUG_NONDETERMINISTIC=1: torch deterministic algorithms %s for this run",
                       "ON" if torch.are_deterministic_algorithms_enabled() else "OFF")
    if getattr(args, "backend", "torch") == "warp":
        if (getattr(solver.solver, "_turb_width", 0) and hasattr(solver.solver, "gh_face")
                and type(solver.solver.turbulence).__name__ in ("KEpsilon", "RealizableKE")):
            # buoyant k-epsilon RANS (the data-centre cases, buoyantBoussinesqpicoext_kepsilon_warp)
            from FVsolver.applications.buoyantBoussinesqpicoext_kepsilon_warp import attach_warp_dc as attach_warp
        elif getattr(solver.solver, "_turb_width", 0):            # k-omega RANS (W4, picoext_komega_warp)
            from FVsolver.applications.picoext_komega_warp import attach_warp_komega as attach_warp
        else:
            from FVsolver.applications.buoyantBoussinesqpicoext_warp import attach_warp
        from FVsolver.applications.buoyantBoussinesqpicoext_warp import TANGENT_BLOCK_CELLS
        if (_ALLOC_DEFAULTED and device.type == "cuda" and int(solver.n_cell) > TANGENT_BLOCK_CELLS
                and os.environ.get("LPCNS_WARP_EXPANDABLE", "0") != "1"):
            # Above 2.5M cells (152^3) on Warp, the allocator's expandable segments
            # crash the process ("CUDA error: context is destroyed" / a segmentation fault) as soon as it releases
            # cached memory -- an empty_cache() after the build, or the full Krylov basis of the learned commands --
            # and the plain segments run the same step (peak 85.2 of 95 GiB).  Every segment from here on is plain;
            # an explicit PYTORCH_CUDA_ALLOC_CONF, or LPCNS_WARP_EXPANDABLE=1, keeps the setting as it is.
            # W12j: plain segments alone fragment (the rebuild's temporaries split the cached 19.8 GiB basis block);
            # blocks above 2 GiB are never split, so a free one returns whole (the whole 152^3 learned solve certified,
            # 35.6 s; 512 MiB also certified but its V-cycle capture did not fit).  LPCNS_WARP_ALLOC replaces it.
            alloc = os.environ.get("LPCNS_WARP_ALLOC") or "expandable_segments:False,max_split_size_mb:2048"
            torch.cuda.memory._set_allocator_settings(alloc)
            logger.info("BACKEND warp: %d cells > %d: CUDA allocator settings %r from here on (W12h)",
                        int(solver.n_cell), TANGENT_BLOCK_CELLS, alloc)
        t_warp = time.time()
        attach_warp(solver)
        logger.info("BACKEND warp: residual, JVP and colouring probes run the fused Warp kernels (built in %.1f s)",
                    time.time() - t_warp)
    if getattr(args, "deterministic_builds", False):
        from Pipeline.CoupledNewton import newton as _nw
        _nw.BUILDS["deterministic"] = True
        logger.info("--deterministic-builds: preconditioner builds keep torch's deterministic algorithms (the "
                    "Krylov solves follow --fast-linear-kernels)")
    if getattr(args, "krylov_fused_orth", False):
        # Warp backend (opt-in): every GMRES call's Gram-Schmidt step as fused device kernels
        from SparseSolver import gmres as _gm
        _gm.KRYLOV["fused_orth"] = True
        logger.info("--krylov-fused-orth: GMRES orthogonalises with fused DGKS-CGS2 kernels, one read-back per "
                    "iteration (SparseSolver.gmres_warp)")
    if getattr(args, "krylov_best_cycle_return", False):
        from SparseSolver import gmres as _gm
        _gm.KRYLOV["best_cycle_return"] = True
        if getattr(args, "krylov_best_cycle_gain", None) is not None:
            _gm.KRYLOV["best_cycle_gain"] = float(args.krylov_best_cycle_gain)
        logger.info("--krylov-best-cycle-gain %g: the best cycle end is returned only below that fraction of the "
                    "solve's start residual (0: at any level)", _gm.KRYLOV["best_cycle_gain"])
        logger.info("--krylov-best-cycle-return: every GMRES solve returns its best cycle end when the last is ten "
                    "times above it; a cycle that breaks down above its tolerance is followed by a fresh one")
    if getattr(args, "krylov_device_loop", False):
        from SparseSolver import gmres as _gm
        _gm.KRYLOV["device_loop"] = True
        logger.info("--krylov-device-loop: every GMRES cycle's Arnoldi loop runs on the device (CUDA conditional "
                    "WHILE node; SparseSolver.gmres_warp.DeviceCycle)")
    if int(getattr(args, "krylov_basis_chunk", 0) or 0) > 0:
        for _flag, _on in (("--krylov-fused-orth", getattr(args, "krylov_fused_orth", False)),
                           ("--krylov-device-loop", getattr(args, "krylov_device_loop", False))):
            if _on:
                logger.warning("%s does not act with --krylov-basis-chunk %d: the fused kernels and the device "
                               "loop work on the single basis buffer, so every GMRES call runs the plain host loop "
                               "(--krylov-basis-chunk 0 keeps the flag in force)", _flag, int(args.krylov_basis_chunk))
    if getattr(args, "krylov_orth_impl", None):
        # Warp backend (opt-in): the fused kernels' implementation (also set in
        # build_preconditioner_factory, which the transient and sweep entries share)
        from SparseSolver import gmres_warp as _gw
        _gw.BACKEND["impl"] = args.krylov_orth_impl
        logger.info("--krylov-orth-impl %s: the fused Gram-Schmidt kernels run %s", args.krylov_orth_impl,
                    "in NVIDIA Warp (no Triton import)" if args.krylov_orth_impl == "warp" else "in Triton")
    if getattr(args, "operator_correction", None):
        # --operator-correction: bound and armed before the gauge decision below, so the constant
        # pressure mode is judged on the corrected Jacobian (C_phi reads only pressure differences: it stays free)
        from Pipeline.CoupledNewton.operator_correction import load_and_attach, nusselt_count_check
        correction = load_and_attach(solver, args.operator_correction)
        logger.info("OPERATOR-CORRECTION: the residual is F + C_phi with C_phi from %s (%s; %s)",
                    args.operator_correction, correction.config, nusselt_count_check(correction.bind(solver)))
        if getattr(args, "operator_correction_precond", "corrected") == "plain":
            if args.operator != "jvp":
                raise ValueError("--operator-correction-precond plain builds the preconditioner from the plain J; "
                                 "the Krylov operator must then be the exact JVP of F + C: pass --operator jvp")
            correction.precondition_plain = True
            logger.info("OPERATOR-CORRECTION: preconditioner from the PLAIN Jacobian, Krylov operator the JVP of F + C")
    elif getattr(args, "operator_correction_precond", "corrected") != "corrected":
        raise ValueError("--operator-correction-precond needs --operator-correction")
    solver.assembly_batch = int(args.assembly_batch)
    if getattr(args, "backend", "torch") == "warp" and solver.assembly_batch <= 1:
        solver.assembly_batch = 16          # the Warp backend's batched probes (attach sets 16; W4b)
    if args.colouring_cache:
        # --colouring-cache: read by jacobian.sparsity, as
        # assembly_batch above is.  Unset, neither attribute exists and the colouring is built
        # without the cache.
        solver.colouring_cache = os.path.abspath(args.colouring_cache)
        solver.colouring_cache_refresh = bool(args.colouring_cache_refresh)
    if args.gpu_sparsity:
        # --gpu-sparsity: read by jacobian.sparsity; unset, the attribute does not exist and
        # the sparsity tables are built on the host
        solver.sparsity_on_device = True
    if getattr(args, "device_sparsity", False):
        # --device-sparsity: read by jacobian.sparsity; unset, nothing changes
        solver.sparsity_device_build = True
    if args.colouring != "rcm":
        # --colouring: read by jacobian.sparsity; rcm (the default) sets nothing, so the
        # default colouring is built (or loaded)
        solver.colouring = args.colouring
    if getattr(args, "restart_gib", 0.0):
        # --restart-gib: resolved once per mesh from its unknown count, so the cycle length is a
        # deterministic function of the case; unset, --restart is used as given
        args.restart = restart_by_memory(int(args.restart), int(args.restart_max), float(args.restart_gib),
                                         solver.n_unknowns)
        logger.info("--restart-gib %.3g: GMRES cycle length %d (--restart floor, --restart-max %d, %d unknowns: a full "
                    "basis of %.2f GiB)", args.restart_gib, args.restart, args.restart_max, solver.n_unknowns,
                    (args.restart + 1) * 8 * solver.n_unknowns / 2 ** 30)
    if args.dump_linear_systems:
        install_linear_system_dump(solver, args.dump_linear_systems, args)
    log_jfnk_mode(args, jfnk_policy, solver, logger)
    if eliminator is not None:
        logger.info("NONLINEAR ELIMINATION (B5, Luo et al. 2020): %s; the Newton step is taken from the corrected "
                    "state, the certificate reads the unmodified residual; continuation ladder %s",
                    eliminator.config.describe(), ramp_multipliers(args.nu_ramp))

    cold = torch.zeros(solver.n_unknowns, device=device, dtype=solver.dtype)
    defect = solver.constant_pressure_defect(cold)
    logger.info(
        "constant pressure mode: relative Jacobian defect %.6e, so the pressure "
        "level is %s and the Newton step %s projected", defect,
        "FREE" if solver.resolve_gauge(cold) else "PINNED by a boundary",
        "is" if solver._gauge_free else "is NOT")

    # BEFORE the timer: reading a reference case off disk is I/O against a
    # foreign solver's output, not solve time, and folding it into the wall
    # clock would flatter or damage the comparison depending on the disk.
    if args.state is not None and str(args.state).startswith("head:"):
        # Opt-in --state head:<model>: the learned start produced in this process on the GPU (mesh
        # bundle from this solver, inference plan, forward); it never leaves the device.  Before the timer, as a file.
        from Pipeline.CoupledNewton.learned_start import head_start
        x0, _ = head_start(args.state, solver, case, dataset_dir, device, log=logger.info)
    elif args.state is not None:
        # A saved rung state trumps --init: it exists precisely so a rung-4/5
        # experiment can start where a 12-minute climb ended.
        x0 = load_state(args.state, device, solver.dtype)
        if x0.numel() != solver.n_unknowns:
            raise ValueError(
                f"--state {args.state} carries {x0.numel()} unknowns, the case "
                f"declares {solver.n_unknowns}: wrong mesh or wrong case")
    elif args.init == "openfoam":
        if args.foam is None:
            raise ValueError("--init openfoam needs --foam <case>.foam")
        x0 = openfoam_start(solver, os.path.abspath(args.foam),
                            parse_field_offsets(args.foam_offset),
                            args.foam_time, logger)
    elif args.init == "internal":
        x0 = internal_start(solver, case)
    else:
        # solve_newton's own zero start; kept as None so the shipped path is
        # the identical call it has always been.
        x0 = None
    if getattr(args, "cold_channel_flow", False):
        # Opt-in: the inviscid mean flow of the channel under the upper symmetry boundary in the first iterate
        # (coldstart.channel_flow_seed); the uniform start itself on a channel of constant height
        if x0 is None:
            raise ValueError("--cold-channel-flow needs a first iterate (--init internal / openfoam or --state)")
        from Pipeline.CoupledNewton import coldstart as _coldstart_channel
        x0, channel_report = _coldstart_channel.channel_flow_seed(solver, x0)
        logger.info("--cold-channel-flow: %s", channel_report)
    if getattr(args, "cold_boundary_layer_seed", False):
        # Opt-in: a turbulent flat-plate boundary layer in k and epsilon of the first iterate
        # (coldstart.boundary_layer_seed); inert on a model without an epsilon wall function
        if x0 is None:
            raise ValueError("--cold-boundary-layer-seed needs a first iterate (--init internal / openfoam or --state)")
        from Pipeline.CoupledNewton import coldstart as _coldstart_seed
        x0, bl_seed_report = _coldstart_seed.boundary_layer_seed(
            solver, x0, wall_epsilon=getattr(args, "cold_boundary_layer_wall_epsilon", False),
            velocity=getattr(args, "cold_boundary_layer_velocity", False),
            wall_cells=("below" if getattr(args, "cold_boundary_layer_wall_k", False)
                        else getattr(args, "cold_boundary_layer_wall_cells", False)))
        logger.info("--cold-boundary-layer-seed: %s", bl_seed_report)
    if getattr(args, "cold_wall_seed", False):
        # Opt-in: the wall-function cells of the first iterate at the log-law equilibrium of its
        # velocity (coldstart.wall_function_seed); inert on a model without an epsilon wall function.  After the
        # boundary-layer start when both are given: the wall seed only raises a cell's k, so it fills the wall cells
        # the layer leaves out (near the leading edge the layer is thinner than the first cell)
        if x0 is None:
            raise ValueError("--cold-wall-seed needs a first iterate (--init internal / openfoam or --state)")
        from Pipeline.CoupledNewton import coldstart as _coldstart_seed
        x0, wall_seed_report = _coldstart_seed.wall_function_seed(solver, x0)
        logger.info("--cold-wall-seed: %s", wall_seed_report)
    x_fallback = None
    if args.learned_init_fallback_state is not None:
        # P1 deployment mode: the 'ramp:' fallback's start, read off disk before the timer like --state
        x_fallback = load_state(args.learned_init_fallback_state, device, solver.dtype)
        if x_fallback.numel() != solver.n_unknowns:
            raise ValueError(f"--learned-init-fallback-state {args.learned_init_fallback_state} carries "
                             f"{x_fallback.numel()} unknowns, the case declares {solver.n_unknowns}")
    if x0 is not None:
        start_residual = solver.residual(x0)
        logger.info(
            "--init %s: max|r| = %.6e, ||r||2 = %.6e before the first step "
            "(%s)", args.state if args.state is not None else args.init, float(start_residual.abs().max()),
            float(start_residual.norm()), block_report(solver, start_residual))
    if getattr(args, "dump_residual", None):
        # opt-in, before the timer like the start's own residual above; None is the zero start
        dump_residual(solver, x0, args.dump_residual, logger, "first")
    x_guard = None
    if getattr(args, "state_guard", None) is not None:
        if args.state is None:
            raise ValueError("--state-guard needs --state (the learned start it guards)")
        x_guard = load_state(args.state_guard, device, solver.dtype)    # off disk before the timer, like --state
        if x_guard.numel() != solver.n_unknowns:
            raise ValueError(f"--state-guard {args.state_guard} carries {x_guard.numel()} unknowns, the case "
                             f"declares {solver.n_unknowns}")

    # camg_plan_reuse: --camg-reuse-plan's plan cache (E5) or None; _e1_kwargs and the end of the run read it
    order1_switch = None
    if int(getattr(args, "order1_switch", 0) or 0) > 0:
        # The classical factory of the same run first; the learned
        # order-1 factory is built AT the switch (a solve that never switches builds
        # nothing learned); both share one plan cache.  Every refusal runs here, before the timer: on the run's own
        # flags (the switch's checks), on the classical factory's (the M5 flags act there) and on the learned one's.
        from Pipeline.CoupledNewton.order1_switch import Order1Switch, classical_args_of, learned_args_of
        refuse_misused_camg_plan_flags(args)
        learned_args = learned_args_of(args)
        for check in (refuse_misused_camg_plan_flags, refuse_misused_m5_flags, refuse_misused_m2_flags,
                      refuse_misused_m3_flags):
            check(learned_args)
        classical_factory, learned_model, camg_plan_reuse = build_preconditioner_factory(
            classical_args_of(args, args.order1_classical), solver, case, device, logger, cold)

        def build_learned_factory(plan=camg_plan_reuse):
            return build_preconditioner_factory(learned_args, solver, case, device, logger, cold, plan_reuse=plan)[0]
        # Opt-in: the relative trigger and the evidence rule; a learned solve the stagnation rescue
        # ended at its probe (--camg-rescue-maxit cap) is no evidence
        probe = (int(args.camg_rescue_l1) + int(args.camg_rescue_maxit)
                 if getattr(args, "camg_rescue_l1", 0) and getattr(args, "camg_rescue_maxit", None) is not None else 0)
        import copy
        alternates = []                     # Opt-in: further lam models, chosen by evidence
        for model in getattr(args, "order1_alternate", None) or []:
            alt_args = copy.copy(learned_args)
            alt_args.camg_lam = model
            for check in (refuse_misused_camg_plan_flags, refuse_misused_m5_flags, refuse_misused_m2_flags,
                          refuse_misused_m3_flags):
                check(alt_args)
            alternates.append(lambda a=alt_args, plan=camg_plan_reuse: build_preconditioner_factory(
                a, solver, case, device, logger, cold, plan_reuse=plan)[0])
        deep = []
        for spec in getattr(args, "order1_alternate_deep", None) or []:
            # The deep stabilised cycle as a further form; value-based plans, so its own plan
            # cache (plan_reuse=None: the factory makes one from its own, non-structural arguments)
            deep_args = deep_alternate_args(learned_args, spec)
            for check in (refuse_misused_camg_plan_flags, refuse_misused_m5_flags, refuse_misused_m2_flags,
                          refuse_misused_m3_flags):
                check(deep_args)
            deep.append(lambda a=deep_args: build_preconditioner_factory(
                a, solver, case, device, logger, cold, plan_reuse=None)[0])
            logger.info("--order1-alternate-deep %s: the deep value-paired stabilised cycle is a further order-1 form", spec)
        configs = []
        for model in getattr(args, "order1_alternate_config", None) or []:
            # The learned AMG configuration as a further form, chosen at the switch from the
            # Jacobian of that step; value-based plans, so its own plan cache (as the deep form)
            cfg_args = config_alternate_args(learned_args, model)
            for check in (refuse_misused_camg_plan_flags, refuse_misused_m5_flags, refuse_misused_m2_flags,
                          refuse_misused_m3_flags, refuse_misused_camgcfg_flags):
                check(cfg_args)
            configs.append(lambda a=cfg_args: build_preconditioner_factory(
                a, solver, case, device, logger, cold, plan_reuse=None)[0])
            logger.info("--order1-alternate-config %s: the learned AMG configuration is a further order-1 form", model)
        first = build_learned_factory
        if configs and getattr(args, "order1_config_first", False):
            first, alternates = configs[0], [build_learned_factory] + alternates + deep + configs[1:]
            logger.info("--order1-config-first: the learned configuration first, the --camg-lam form its alternate")
        elif deep and getattr(args, "order1_deep_first", False):
            first, alternates = deep[0], [build_learned_factory] + alternates + deep[1:] + configs
            logger.info("--order1-deep-first: the deep stabilised cycle first, the --camg-lam form its alternate")
        else:
            alternates = alternates + deep + configs
        order1_switch = learned_factory = Order1Switch(classical_factory, None, args.order1_switch, logger.info,
                                                       learned_builder=first,
                                                       rel=float(getattr(args, "order1_switch_rel", 0.0) or 0.0),
                                                       keep=getattr(args, "order1_keep", "any"), probe=probe,
                                                       alternates=alternates)
        if order1_switch.rel > 0 or order1_switch.keep != "any":
            logger.info("--order1-switch %d (README 12.37): threshold max(N, %g x the run's first production solve); "
                        "keep %s (rescue probe %d)", args.order1_switch, order1_switch.rel, order1_switch.keep, probe)
    else:
        learned_factory, learned_model, camg_plan_reuse = build_preconditioner_factory(
            args, solver, case, device, logger, cold)
    order1_handback = None
    if getattr(args, "order1_handback_below", None) is not None:
        # Opt-in: the learned form until max|r| < MAXR, then the classical cycle of
        # --order1-classical (built at the hand-back, sharing the plan cache); refused here, before the timer
        from Pipeline.CoupledNewton.order1_switch import HandBack, classical_args_of
        refuse_misused_handback_flags(args)

        def build_classical_factory(plan=camg_plan_reuse):
            return build_preconditioner_factory(classical_args_of(args, args.order1_classical), solver, case, device,
                                                logger, cold, plan_reuse=plan)[0]
        order1_handback = learned_factory = HandBack(learned_factory, build_classical_factory,
                                                     args.order1_handback_below, logger.info)
        logger.info("--order1-handback-below %g: the learned order-1 form until a Newton step ends below max|r| %g, "
                    "then the classical cycle (%s) for the rest of the solve", args.order1_handback_below,
                    args.order1_handback_below, args.order1_classical)
    if order1_switch is not None:
        logger.info("--order1-switch %d: the classical cycle (%s) until a production linear solve needs >= %d GMRES "
                    "iterations, then the learned order-1 form (%s) for the rest of the solve", args.order1_switch,
                    args.order1_classical if str(args.learned_action).startswith("camgnet:") else args.learned_action,
                    args.order1_switch, f"--camg-lam {args.camg_lam}" if getattr(args, "camg_lam", None)
                    else args.learned_action)
    if getattr(args, "camg_theta_refresh", None) is not None:
        # The refresh rule reads the JFNK policy's solves; a wrapped factory or the default solve would leave it unread
        theta_reuse = getattr(learned_factory, "camg_reuse", None)
        if theta_reuse is None or jfnk_policy is None:
            raise ValueError("--camg-theta-refresh reads the JFNK policy's linear solves: it needs --operator jvp/auto "
                             "or a --precond-rebuild policy, and the camgnet factory itself (not an --order1-switch "
                             "or --order1-handback-below wrapper)")
        jfnk_policy.observers.append(theta_reuse.observe)

    # conjugate heat transfer: the region coarse correction (region_deflation.py), built per assembled Jacobian
    region_wrapper = None
    if getattr(args, "region_deflation", "auto") != "off":
        from Pipeline.CoupledNewton.region_deflation import RegionDeflation, region_columns
        region_cols = region_columns(solver.solver, int(solver.n_cell), int(solver.pressure), device=device)
        if args.region_deflation == "on" and not region_cols:
            raise ValueError("--region-deflation on: the case has no solid material")
        if region_cols:
            n_unknowns = int(solver.n_unknowns)
            region_wrapper = (lambda M, blocks, x, cols=region_cols, n=n_unknowns:
                              RegionDeflation(M, blocks["J"], cols, n))
            logger.info("region deflation: %d solid material(s), %s temperature unknowns",
                        len(region_cols), [int(c.numel()) for c in region_cols])

    def continuity_pass(x, k):
        """``--learned-init-project K``'s projection of ``x`` on the configured path, and its log line."""
        from Pipeline.CoupledNewton.continuity_projection import project_continuity
        if getattr(args, "learned_init_project_fast", False):
            x, rec = project_continuity(solver, x, int(k), logger=logger, fast_residual=True, reuse_hierarchy=True,
                                        eager_gradient=True,
                                        gpu_hierarchy=bool(getattr(args, "learned_init_project_gpu", False)))
        else:
            x, rec = project_continuity(solver, x, int(k), logger=logger)
        first, last = rec["before"], rec["after"]
        logger.info("LEARNED-INIT PROJECT done: max|r| %.6e -> %.6e, ||r||2 %.6e -> %.6e (%s) in %.2f s%s",
                    first["all"]["max_abs"], last["all"]["max_abs"], first["all"]["l2"], last["all"]["l2"],
                    ", ".join(f"{name} {last[name]['max_abs']:.6e}" for name in ("momentum", "continuity", "energy")
                              if name in last), rec["seconds"],
                    "" if rec["stopped"] is None else "  (stopped: %s)" % rec["stopped"])
        return x, rec

    def energy_pass(x):
        """``--learned-init-project-energy E`` (CHT2 README section 18): T re-solved for the start's own flow, and one
        more continuity + energy step when the new T raises the momentum rows (buoyancy) above 1e-5."""
        from Pipeline.CoupledNewton.energy_projection import project_energy
        fast = bool(getattr(args, "learned_init_project_fast", False))
        x, rec = project_energy(solver, x, int(args.learned_init_project_energy), logger=logger, fast_residual=fast)
        seconds = rec["seconds"]
        jumped = rec["after"]["momentum"]["max_abs"] > max(1e-5, 10.0 * rec["before"]["momentum"]["max_abs"])
        if jumped and getattr(args, "learned_init_project", 0):
            x, c2 = continuity_pass(x, 1)
            x, e2 = project_energy(solver, x, 1, logger=logger, fast_residual=fast)
            rec["extra"] = {"continuity": c2, "energy": e2}
            rec["after"] = e2["after"]
            seconds += c2["seconds"] + e2["seconds"]
        b, a = rec["before"], rec["after"]
        logger.info("LEARNED-INIT PROJECT-ENERGY done: energy max|r| %.6e -> %.6e, momentum %.6e -> %.6e, continuity "
                    "%.6e -> %.6e, solid materials %s, %d step(s)%s in %.2f s", b["energy"]["max_abs"],
                    a["energy"]["max_abs"], b["momentum"]["max_abs"], a["momentum"]["max_abs"],
                    b["continuity"]["max_abs"], a["continuity"]["max_abs"], rec["solid_materials"], len(rec["steps"]),
                    " + one more continuity / energy step (momentum rose)" if "extra" in rec else "", seconds)
        return x, rec

    def project_start(x):
        """``--learned-init-project K`` (when given), then ``--learned-init-project-energy E``."""
        rec = {}
        if getattr(args, "learned_init_project", 0):
            x, rec["continuity"] = continuity_pass(x, args.learned_init_project)
        x, rec["energy"] = energy_pass(x)
        return x, rec

    start_projected = None
    if device.type == "cuda":
        torch.cuda.synchronize(device)
    started = time.time()
    if x_guard is not None:
        if getattr(args, "learned_init_project_energy", 0):
            # CHT2 README section 18: both starts projected (continuity, then energy) before the guard compares them
            x0, rec_state = project_start(x0)
            x_guard, rec_guard = project_start(x_guard)
            start_projected = {"state": rec_state, "guard": rec_guard}
        # The residual guard of a learned start, inside the timer -- the network's start or the
        # second state (its base state alone), whichever has the smaller ||F||_2
        n_state, n_guard = float(solver.residual(x0).norm()), float(solver.residual(x_guard).norm())
        chosen = "--state-guard" if n_guard < n_state else "--state"
        if n_guard < n_state:
            x0 = x_guard
        if device.type == "cuda":
            torch.cuda.synchronize(device)
        logger.info("--state-guard: ||F||_2 %.6e at --state, %.6e at the guard state -> start from %s (%.3f s)",
                    n_state, n_guard, chosen, time.time() - started)
        if start_projected is not None:
            start_projected["chosen"] = chosen
            start_projected["norms"] = {"state": n_state, "guard": n_guard}
        x_guard = None
    # Opt-in: both are None / False on a default run, which then
    # passes solve_newton exactly the keyword arguments it always did.
    iterate_recorder = IterateRecorder(started) if args.iterate_error else None
    # --step-clock: the same Newton timer stamped on every row (newton_residual.csv "elapsed"), no state copied
    step_clock = started if (getattr(args, "step_clock", False) and iterate_recorder is None) else None
    component_timers = bool(args.component_timers or args.iterate_error)

    probe_rows = []
    _probe_state = {"done": False, "cond": False}

    def cell_mass_probe(cycle, res):
        n_cell = res.numel() // solver.num_fields
        probe_rows.append(res.reshape(n_cell, solver.num_fields)
                        .norm(dim=1).detach().cpu().numpy())

    def cond_dumper(J):
        import numpy as npy
        import scipy.sparse as sp
        A = sp.csr_matrix((J.values().detach().cpu().numpy(),
                             J.col_indices().cpu().numpy(),
                             J.crow_indices().cpu().numpy()), shape=J.shape)
        F = solver.num_fields
        n = A.shape[0] // F
        sq = A.copy()
        sq.data = sq.data ** 2
        tot = npy.asarray(sq.sum(axis=1)).ravel().reshape(n, F)
        diag = npy.zeros((n, F))
        for i in range(n):
            blk = sq[i * F:(i + 1) * F, i * F:(i + 1) * F]
            diag[i] = npy.asarray(blk.sum(axis=1)).ravel()
        off = npy.maximum(tot - diag, 0.0)
        score = npy.sqrt(off) / npy.maximum(npy.sqrt(diag), 1e-300)
        npy.save(args.cell_cond_dump, score.sum(axis=1) / F)

    if args.probe_policy:
        def probe_wrapper(pre_c, blocks, x):
            from Pipeline.CoupledNewton.adapter import ConstraintAdapter
            from Pipeline.CoupledNewton.learned_action import LearnedAction
            cyclic_face = case.fv_graph.graph_face.cyclic_face
            adapter = ConstraintAdapter(solver, blocks, cyclic_face=cyclic_face, state=x)
            act = LearnedAction(adapter, model=learned_model, cycles=args.cycles,
                              pressure=args.pressure, coarse=True)
            learned_fn = act.prepare(differentiable=False)
            A = lambda v: blocks["J"] @ v
            pol = _Policy(pre_c, learned_fn, A, pk, args.restart)
            e3_stats.clear()
            e3_stats.update(policy=pol)
            return pre_c

    schwarz_wrapper = None
    if args.patch_schwarz:
        import numpy as _npz
        import scipy.sparse as _spz
        import scipy.sparse.linalg as _splz
        _smask = _npz.load(args.patch_schwarz)

        def schwarz_wrapper(pre, blocks, x=None):
            J = blocks["J"].to_sparse_csr()
            A = _spz.csr_matrix((J.values().detach().cpu().numpy(),
                                 J.col_indices().cpu().numpy(),
                                 J.crow_indices().cpu().numpy()), shape=J.shape).tocsc()
            if args.fixed_jacobian_pattern == "structural":
                A.eliminate_zeros()     # SuperLU orders by pattern; today's matrix
            F = solver.num_fields
            dofs = _npz.repeat(_smask, F)
            lu = _splz.splu(A[dofs][:, dofs].tocsc())
            idx = _npz.flatnonzero(dofs)
            JT = blocks["J"]
            gauge = solver.gauge

            def apply(v):
                vd = v.detach().cpu().numpy()
                z1d = _npz.zeros_like(vd)
                z1d[idx] = lu.solve(vd[idx])
                z1 = torch.as_tensor(z1d, device=v.device, dtype=v.dtype)
                return pre(v - JT @ z1) + gauge(z1)
            return apply

    def _adj_dumper(J):
        import numpy as _npa
        csr = J.to_sparse_csr()
        rows = csr.crow_indices().cpu().numpy()
        cols = csr.col_indices().cpu().numpy()
        F = solver.num_fields
        ri = _npa.repeat(_npa.arange(len(rows) - 1), _npa.diff(rows)) // F
        ci = cols // F
        keep = ri != ci
        _npa.savez(args.face_adj_dump, src=ri[keep], dst=ci[keep])

    probe_policy = None
    e3_stats = {}
    if args.probe_policy:
        pk, pm, ph = (float(x) for x in args.probe_policy.split(","))
        pk = int(pk)

        class _Policy:
            """Restart-boundary candidate switching with k-step mini-GMRES probes.

            A candidate preconditioner only ever acts INSIDE Krylov, so the probe
            measures it the same way: k GMRES steps with the candidate on the current
            residual.  Contractions are compared per Krylov step (k-th root), because
            the active candidate's observed contraction spans a whole restart cycle.
            """

            def __init__(self, classical, learned, A, probe_steps, restart_len):
                self.cand = {"classical": classical, "learned": learned}
                self.A = A
                self.pk = probe_steps
                self.rl = max(int(restart_len), 1)
                self.current = "classical"
                self.switches = 0
                self.probing_steps = 0
                self.contractions = []
                self.cycle_start_norm = None
                self.last_active_contraction = None
                self.cycles_since_switch = 10 ** 9

            def _contract(self, name, r):
                from SparseSolver.gmres import gmres
                res = gmres(self.A, r, M=self.cand[name], restart=self.pk,
                            max_cycles=1, rtol=0.0, atol=0.0)
                rr = r - self.A(res.x)
                return float(rr.norm()) / max(float(r.norm()), 1e-300)

            def decide(self, cycle, residual, current_fn):
                rn = float(residual.norm())
                if self.cycle_start_norm is not None:
                    self.last_active_contraction = rn / max(self.cycle_start_norm, 1e-300)
                    self.contractions.append(round(self.last_active_contraction, 4))
                self.cycle_start_norm = rn
                self.cycles_since_switch += 1
                if self.cycles_since_switch < ph:
                    return self.cand[self.current]
                other = "learned" if self.current == "classical" else "classical"
                c_other = self._contract(other, residual) ** (1.0 / self.pk)
                self.probing_steps += self.pk
                base = (self.last_active_contraction ** (1.0 / self.rl)
                        if self.last_active_contraction else 1.0)
                if c_other < base - pm:
                    self.current = other
                    self.switches += 1
                    self.cycles_since_switch = 0
                return self.cand[self.current]

    def _probe_decide(cycle, residual, current_fn):
        pol = e3_stats.get("policy")
        return pol.decide(cycle, residual, current_fn) if pol is not None else current_fn

    _e1_first_rung = {"done": False}
    # --continuation adaptive: the multiplier of the attempt being built (run_adaptive_continuation's on_rung);
    # None outside that path, where a rung's multiplier is its place in the --nu-ramp ladder.  ``cold_first`` is
    # set before the first ladder (below, where the start is final)
    adaptive_rung = {"multiplier": None}
    cold_first = {"x": None}

    def _e1_kwargs(rung):
        kw = {}
        first = not _e1_first_rung["done"]
        _e1_first_rung["done"] = True
        if first and args.cell_mass_probe and not _probe_state["done"]:
            _probe_state["done"] = True
            kw["restart_probe"] = cell_mass_probe
        if first and args.cell_cond_dump and not _probe_state["cond"]:
            _probe_state["cond"] = True
            kw["first_assembly_dump"] = cond_dumper
        if schwarz_wrapper is not None:
            kw["precond_wrapper"] = schwarz_wrapper
        if args.probe_policy and first:
            kw["cycle_policy"] = _probe_decide
            kw.setdefault("precond_wrapper", probe_wrapper)
        if args.face_adj_dump and not _probe_state.get("adj", False):
            _probe_state["adj"] = True
            kw["first_assembly_dump"] = _adj_dumper
        if iterate_recorder is not None:
            # Evaluated while the rung's solve_newton call is being built, so the multiplier is the
            # one this rung runs at (also after AUTO-RAMP rewrites --nu-ramp).
            kw["checkpoint"] = iterate_recorder.checkpoint_for(
                rung, ramp_multipliers(args.nu_ramp)[rung - 1] if adaptive_rung["multiplier"] is None
                else adaptive_rung["multiplier"])
        if component_timers:
            kw["component_timers"] = True
        # --fixed-jacobian-pattern / --fast-assembly-kernels: added only
        # when given, so a default run passes solve_newton exactly the keywords it always did.
        if args.fixed_jacobian_pattern is not None:
            kw["fixed_jacobian_pattern"] = args.fixed_jacobian_pattern
        if args.fast_assembly_kernels:
            kw["fast_assembly_kernels"] = True
        if args.fast_line_search:
            # Added only when given, as the assembly flags above
            kw["fast_line_search"] = True
        if args.krylov_basis_chunk:
            # Added only when given, as the assembly flags above
            kw["krylov_basis_chunk"] = int(args.krylov_basis_chunk)
        if camg_plan_reuse is not None:
            # --camg-reuse-plan: called once per solve_newton call, i.e. at every rung start
            # (AUTO-RAMP retries and CBBM passes included), so the aggregation is frozen per rung
            camg_plan_reuse.new_rung()
        if getattr(args, "camg_lam_reuse_rung", False) and getattr(learned_factory, "camg_lam_reuse", None) is not None:
            # Opt-in: the lam network runs again at this rung's first hierarchy build
            learned_factory.camg_lam_reuse.new_rung()
        if getattr(args, "camg_keep_w", False) and getattr(learned_factory, "camg_reuse", None) is not None:
            # Opt-in: this rung's first build rebuilds W from the cached network output
            learned_factory.camg_reuse.new_rung()
        if getattr(learned_factory, "camg_config", None) is not None:
            # --camg-config-every rung -- the ranking chooses again at this call's first build
            learned_factory.camg_config.new_rung()
        if getattr(learned_factory, "camg_d1", None) is not None:
            # --camg-d1-reuse rung: the D1 network runs again at this call's first build
            learned_factory.camg_d1["mod"].new_rung()
        # JFNK mode: added only when asked for, as the assembly flags above; one policy for the run.
        kw.update(jfnk_solve_kwargs(args, jfnk_policy))
        if eliminator is not None:
            # Added only when asked for, as the JFNK keywords above.
            kw["elimination"] = eliminator
        if getattr(args, "first_forcing", None) is not None:
            # Added only when asked for, so a default run passes the default keywords.
            kw["first_forcing"] = float(args.first_forcing)
        if getattr(args, "first_maxit", None) is not None:
            # Added only when asked for, as --first-forcing above.
            kw["first_maxit"] = int(args.first_maxit)
        if getattr(args, "first_fallback_l1", None):
            # --first-fallback-l1: the hook's retaken first step (the dict is filled at run time)
            kw["first_restart"] = first_restart
        if getattr(args, "order1_switch_cap", False) and order1_switch is not None:
            # Opt-in: the armed switch caps the classical solve at its threshold
            kw["linear_cap"] = order1_switch.armed_cap
        if getattr(args, "ptc_handover_below", None) is not None:
            # --ptc-handover-below (opt-in): added only when given, as --first-forcing above
            kw["ptc_handover_below"] = float(args.ptc_handover_below)
        if getattr(args, "ptc_on_stall", 0):
            # --ptc-on-stall (opt-in): added only when given, as --first-forcing above
            kw["ptc_on_stall"] = int(args.ptc_on_stall)
            if getattr(args, "ptc_stall_theta0", None) is not None:
                kw["ptc_stall_theta0"] = float(args.ptc_stall_theta0)
            if getattr(args, "ptc_stall_theta_min", 0.0):
                kw["ptc_stall_theta_min"] = float(args.ptc_stall_theta_min)
        if getattr(args, "contraction_probe", False):
            # --contraction-probe (opt-in): added only when given, as --first-forcing above
            kw["contraction_probe"] = True
        if getattr(args, "step_norm_log", False):
            # --step-norm-log (opt-in): added only when given, as --contraction-probe
            kw["step_norm_log"] = True
        if getattr(args, "save_latest_state", False):
            # --save-latest-state (opt-in): the current iterate after every Newton step, written
            # atomically to x_latest.pt (a capped or killed run keeps its last iterate); the existing checkpoint
            # (the iterate recorder) is still called first
            inner = kw.get("checkpoint")

            def latest(iteration, x, inner=inner):
                if inner is not None:
                    inner(iteration, x)
                path = os.path.join(logger.saving_path, "x_latest.pt")
                torch.save(x.detach().cpu(), path + ".tmp")
                os.replace(path + ".tmp", path)
            kw["checkpoint"] = latest
        if forcing_cap_of(args) is not None:
            # Added only when asked for, as --first-forcing above.
            kw["forcing_cap"] = forcing_cap_of(args)
        if forcing_stall_cap_of(args) is not None:
            # Added only when asked for, as --first-forcing above.
            kw["forcing_stall_cap"] = forcing_stall_cap_of(args)
        if getattr(args, "forcing_cap_fallback", None) is not None:
            # Added only when asked for, as --first-forcing above.
            kw["cap_fallback"] = float(args.forcing_cap_fallback)
            kw["on_cap_fallback"] = cap_fallback_hook(learned_factory, logger)
        return kw

    abort_spec = parse_learned_init_abort(args.learned_init_abort)
    monitor = (None if abort_spec is None else
               LearnedInitMonitor(abort_spec["k"], abort_spec["rho"], abort_spec["kmax"], abort_spec["growth"],
                                  args.gate, stall=StallWatch.from_spec(args.learned_init_stall
                                                                        if args.learned_init_stall is not None
                                                                        else STALL_DEFAULT),
                                  refused=getattr(args, "learned_init_refused", 1)))

    first_fallback = {"used": False, "l2": None}     # --first-fallback-l1
    first_restart = {}                               # solve_newton first_restart, filled by the hook below

    escalation = {"R": float(getattr(args, "camg_rescue_escalate", 0.0) or 0.0), "l2": None}
    once = bool(getattr(args, "camg_rescue_hand_back_once", False))
    if once:
        from Pipeline.CoupledNewton import block_amg as _bam_once
        _bam_once.HAND_BACK_ONCE["on"] = True

    def newton_progress(entry):
        if once and int(entry["it"]) == 0:
            # --camg-rescue-hand-back-once: a new Newton call (rung) starts with its one hand-back still to come
            from Pipeline.CoupledNewton import block_amg as _bam_once
            _bam_once.RESCUE_ESCALATION["on"] = False
        if escalation["R"] > 0.0:
            # --camg-rescue-escalate: a step that lowers ||r||2 by less than R escalates the measured rescue for the
            # rest of this Newton call; a new call (its step 0) starts unescalated
            from Pipeline.CoupledNewton import block_amg as _bam_esc
            ratio = _bam_esc.escalation_step(escalation, entry["it"], entry["l2"], escalation["R"])
            if ratio is not None:
                logger.info("--camg-rescue-escalate %g: step %d lowered ||r||2 by a ratio of %.6f; every later stalled "
                            "solve of this Newton call is rescued to its budget", escalation["R"], int(entry["it"]),
                            ratio)
        if iterate_recorder is not None:
            iterate_recorder.observe(entry)
        elif step_clock is not None:
            entry["elapsed"] = time.time() - step_clock
        logger.info(
            "newton %2d  max|r| = %.6e   ||r||2 = %.6e   lambda = %.4g   "
            "gmres %d to %.1e (reorth %d)   %s%s%s(%.1f s)", entry["it"],
            entry["max_abs"], entry["l2"], entry.get("lambda", float("nan")),
            entry.get("inner_iterations", 0), entry.get("inner_rtol", 0.0),
            entry.get("reorthogonalisations", 0),
            ("NO STEP REDUCED THE RESIDUAL " if entry.get("stalled") else "")
            + ("FORCING CAPPED " if entry.get("forcing_capped") else "")
            + ("(STALL) " if entry.get("forcing_cap_stall") else "")
            + (f"CAP FALLBACK (step of lambda {entry['cap_fallback_lambda']:g} not taken) "
               if entry.get("cap_fallback") else ""),
            "REASSEMBLED " if entry.get("reassembled") else "",
            (f"theta {entry['ptc_theta']:.3g} "
             + (f"[{entry['ptc_event']}] " if entry.get("ptc_event") else "")
             if "ptc_theta" in entry else "")
            + (("PC REBUILT (%s) " % entry["rebuild_reason"] if entry["precond_rebuilt"]
                else "pc kept (age %d) " % entry["precond_age"])
               + ("op jvp/%s: %d products %.2f s " % (entry["jvp_kind"], entry["jvp_products"],
                                                      entry["jvp_seconds"])
                  if entry["operator"] == "jvp" else "op assembled ")
               if "precond_rebuilt" in entry else ""),
            time.time() - started)
        if "accel_tried" in entry:
            # --nl-accel: what the nonlinear GMRES combination did at this step (also in newton_residual.csv)
            logger.info("nonlinear GMRES %2d: tried %d, taken %d, depth %d, ratio %s, restart %d", entry["it"],
                        int(entry["accel_tried"]), int(entry["accel_taken"]), int(entry["accel_depth"]),
                        "%.4e" % entry["accel_ratio"] if entry["accel_ratio"] != "" else "-",
                        int(entry["accel_restart"]))
        if entry.get("ne_active"):
            # What the elimination did before this step's linearisation
            logger.info("elimination %2d: %s, %d cells (%d core + halo, %d unknowns, depth %d), ||r||2 %.4e -> "
                        "%.4e, max|r| %.4e -> %.4e, subset ||F_S|| %.4e -> %.4e in %d local step(s)%s (%.2f s: "
                        "assembly %.2f, host LU %.2f, %d LU(s)); Newton-model error of the local direction %.2e",
                        entry["it"], entry["ne_status"] or "-", entry["ne_cells"],
                        entry["ne_core_cells"], entry["ne_unknowns"], entry["ne_layers_run"], entry["ne_l2_before"],
                        entry["ne_l2_after"], entry["ne_max_before"], entry["ne_max_after"],
                        entry["ne_subset_l2_before"], entry["ne_subset_l2_after"], entry["ne_local_iterations"],
                        "" if entry["ne_accepted"] else " REJECTED", entry["ne_seconds"],
                        entry["ne_assembly_seconds"], entry["ne_factor_seconds"], entry["ne_factorisations"],
                        entry["ne_model_error"])
        elif "ne_active" in entry:
            logger.info("elimination %2d: off (switched over to plain Newton)", entry["it"])
        if entry.get("contraction_theta", "") != "":
            # --contraction-probe: also in the log, so a run cut by its time limit keeps it
            logger.info("contraction probe: Theta_0 %.4e (largest per-field ratio %s), simplified correction solved "
                        "to %.1e in %d iterations", entry["contraction_theta"],
                        "%.4e" % entry["contraction_theta_max"] if entry["contraction_theta_max"] != "" else "n/a",
                        entry["contraction_probe_inner_rel"], entry["contraction_probe_iterations"])
        if entry.get("step_l2", "") != "":
            # --step-norm-log: also in the log, so a run cut by its time limit keeps it
            logger.info("step norm %2d: ||d||2 %.4e, largest per-field ||d_f|| / ||x_f|| %s (fields %s)", entry["it"],
                        entry["step_l2"], "%.4e" % entry["step_rel_max"] if entry["step_rel_max"] != "" else "n/a",
                        entry["step_rel_fields"] or "n/a")
        if "cuda_peak_gib" in entry:
            # --component-timers on CUDA: solve_newton's per-step GPU memory
            # (the build pair is nan on a step that reused its linearisation)
            logger.info("memory %2d (GiB): allocated %.2f after the preconditioner build (peak %.2f through assembly "
                        "+ build), peak %.2f through the step, allocated %.2f at its end", entry["it"],
                        entry["cuda_built_gib"], entry["cuda_build_peak_gib"], entry["cuda_peak_gib"],
                        entry["cuda_allocated_gib"])
        if order1_handback is not None and order1_handback.observe(entry) and jfnk_policy is not None:
            jfnk_policy.request("order1")     # --order1-handback-below (R29 F-b): rebuild on the classical cycle
        if order1_switch is not None and order1_switch.observe(entry) and jfnk_policy is not None:
            # --order1-switch: this step's classical solve crossed the threshold -- rebuild on the learned form --
            # or (--order1-keep evidence) the learned form was refused -- rebuild on the classical cycle
            jfnk_policy.request("order1")
        l1_start = getattr(learned_factory, "camg_l1_start", None)
        if (l1_start is not None and l1_start["on"] and entry.get("it", 0) >= 1 and not entry.get("stalled")
                and entry.get("lambda") == 1.0):
            # --camg-l1-start (F1b-b): the first full step is in; the production cycle from a forced rebuild
            l1_start["on"] = False
            if jfnk_policy is not None:
                jfnk_policy.request("handover")
            logger.info("--camg-l1-start: first full step at Newton %d; production smoothers from the next build",
                        entry["it"])
        if (getattr(args, "first_fallback_l1", False) and not getattr(args, "camg_l1_start", False)
                and l1_start is not None and not first_fallback["used"]):
            # --first-fallback-l1: the cheap first step made no progress -> the l1 start after all
            # (after the switch-off test above, so step 1's own full step does not switch it straight off again)
            if entry.get("it", 0) == 0:
                first_fallback["l2"] = float(entry["l2"])
            elif (entry.get("it") == 1 and not l1_start["on"] and first_fallback["l2"] is not None
                  and float(entry["l2"]) >= 0.99 * first_fallback["l2"]):
                first_fallback["used"] = True
                l1_start["on"] = True
                first_restart.update(offset=int(entry["it"]), maxit=int(args.first_fallback_l1))
                if getattr(args, "first_fallback_l1_on_refusal", False):
                    first_restart["on_refusal"] = True
                    if entry.get("stalled"):
                        entry["first_retake"] = True      # the monitor's --learned-init-refused reads it
                if jfnk_policy is not None:
                    jfnk_policy.request("handover")
                logger.info("--first-fallback-l1 %d: Newton step 1 left ||r||2 at %.4g of the start's; the first step "
                            "again on the l1-start cycle (at most %d GMRES iterations) until the first full step",
                            args.first_fallback_l1, float(entry["l2"]) / first_fallback["l2"], args.first_fallback_l1)
        if monitor is not None:
            # --learned-init-abort (P1 deployment mode): raises LearnedInitAbort on a watched rung (the first ladder)
            monitor.check(entry)

    # The case's own diffusivity, read before any ladder runs.  run_continuation
    # takes alpha's base off the discretisation, and a ladder that ends by an exception mid-rung (LearnedInitAbort in
    # deployment mode) leaves nu and alpha at that rung's multiple m.  nu is re-derived from the case each ladder, alpha
    # was not: every fallback after an aborted multi-rung attempt (a 'ramp:' fallback, a predict-then-continue start)
    # solved Ra / m at Pr / m and was certified there -- pilot 2's Lo / LoC runs on DHC64 1e6 / 3.16e6 and DHC32 1e6
    # (rung lines "alpha = 0.000858 ... 0.000429" against B1's 0.000858 ... 0.0002145; same-root errors 0.49-0.54).
    case_alpha = getattr(solver.solver, "alpha_coeff", None)
    case_alpha = None if case_alpha is None else case_alpha.clone()       # as run_continuation clones it
    case_beta = getattr(solver.solver, "beta", None)                      # --beta-ramp's base
    case_beta = None if case_beta is None else case_beta.clone()
    # Warp: the backend re-reads beta whenever its tensor is replaced (sync_coefficients on every
    # call, the stamps of _COEF_SOURCES), so a buoyancy rung reaches its kernels (test_cht_warp.py, beta replaced)
    if getattr(args, "beta_ramp", None) and case_beta is None:
        raise ValueError("--beta-ramp: a Boussinesq case only (the case has no expansion coefficient beta)")

    # --cold-start: pending until the first rung of the first ladder has been solved staged.
    cold_state = {"pending": args.cold_start == "staged", "summary": []}
    cold_theta0 = args.ptc_theta0 if args.cold_theta0 is None else args.cold_theta0

    def rung_tolerance(rung):
        # An intermediate rung is a warm-start generator for the next one,
        # not an answer: it solves DIFFERENT physics and nothing reads its
        # state except the rung after it.  The final rung keeps --tol.
        return (args.tol if (args.rung_tol is None
                             or rung == len(ramp_multipliers(args.nu_ramp)))
                else args.rung_tol)

    def solve_rung(x, rung, **stage):
        """One ``solve_newton`` call of continuation rung ``rung`` with the run's keywords.  ``stage`` -- the
        staged cold start's per-stage overrides, empty on every other call -- replaces keywords by
        name, so the call without it is exactly the call this entry has always made."""
        keywords = dict(
            x0=x, tol=rung_tolerance(rung),
            maxit=args.max_newton, rtol=args.inner_rtol,
            max_backtracks=args.max_backtracks,
            restart=args.restart, max_cycles=args.max_cycles,
            deflate=args.deflate, **_e1_kwargs(rung),
            **({"deflate_rescued": int(args.camg_rescue_deflate)} if args.camg_rescue_deflate else {}),
            **({"true_residual_check": float(args.true_residual_check),
                "true_residual_redo": _coarse_by_lu_redo() if getattr(args, "true_residual_redo", False) else None}
               if args.true_residual_check else {}),
            sweeps=args.sweeps, cycles=args.cycles,
            forcing=args.forcing, reassemble_drift=args.reassemble_drift,
            fast_linear_kernels=args.fast_linear_kernels,
            freeze_preconditioner=args.freeze_precond,
            pressure=args.pressure, precond_shape=args.precond,
            restore_alpha=args.restore_alpha,
            equilibrate=args.equilibrate, ruiz_sweeps=args.ruiz_sweeps,
            poly_degree=args.poly_degree,
            matrix_free=args.matrix_free,
            matrix_free_coefficient=args.matrix_free_coefficient,
            ptc=args.ptc and (args.ptc_rungs == "all" or rung == 1),
            ptc_theta0=args.ptc_theta0,
            ptc_growth=args.ptc_growth, ptc_handover=args.ptc_handover,
            ptc_max_forced_growth=args.ptc_max_forced_growth,
            **({"ptc_controller": "expur", "ptc_hold_lambda": args.ptc_hold_lambda, "ptc_cut": args.ptc_cut,
                "ptc_cfl_max": args.ptc_cfl_max, "ptc_cfl_min": args.ptc_cfl_min}
               if getattr(args, "ptc_controller", "ser") == "expur"
               else ({"ptc_controller": args.ptc_controller}
                     if getattr(args, "ptc_controller", "ser") != "ser" else {})),
            capture_inner=args.inner_history,
            preconditioner_factory=(None if args.probe_policy else learned_factory),
            progress=newton_progress)
        if region_wrapper is not None:
            keywords["precond_wrapper"] = region_wrapper
        if getattr(args, "nl_accel", "off") != "off":
            # --nl-accel (opt-in): a fresh accelerator per call, so every rung (and every AUTO-RAMP retry) starts
            # with an empty history; added only when asked for, so a default run passes the keywords it always did
            from Pipeline.CoupledNewton.nonlinear_acceleration import NGMRESAccelerator
            keywords["accelerator"] = NGMRESAccelerator(depth=int(args.nl_accel_m), mode=args.nl_accel)
        first_rung_policy(args, rung, keywords)
        keywords.update(stage)
        if getattr(args, "approximate_operator", False):
            solver._approximate_operator_ok = True
        rule = _engineering_stop_rule(args, solver) if rung == len(ramp_multipliers(args.nu_ramp)) else None
        if rule is not None:
            keywords["stop_rule"] = rule
        if getattr(args, "pin_state", None):
            # --pin-state / --pin-map (opt-in, P17): hold the sub-case's halo at the full-domain state
            if getattr(solver, "_p17_pin", None) is None:
                full = torch.load(args.pin_state, map_location="cpu", weights_only=True)
                full = (full["x"] if isinstance(full, dict) else full).reshape(-1).double()
                maps = torch.load(args.pin_map, map_location="cpu", weights_only=False)
                n_ = int(solver.n_cell)
                F_ = full.numel() // int(solver.num_fields)
                cells = maps["cells"].long()
                anchor = full.reshape(int(solver.num_fields), F_)[:, cells].reshape(-1).to(device)
                mask = (maps["layer"] > 0).repeat(int(solver.num_fields)).to(device)
                # a penalty 1e3 (x - anchor) on the halo rows (the PTC-shift slot of the residual), not identity rows:
                # identity rows have no couplings and camg would keep them as singleton aggregates down to its dense
                # coarsest level (the first P17-a launch ran out of memory there)
                solver._ptc_shift = (mask.to(torch.float64) * 1.0e3, anchor)
                solver._p17_pin = (mask, anchor)
                logger.info("--pin-state: %d of %d cells held at %s (penalty 1e3)", int((maps["layer"] > 0).sum()), n_,
                            args.pin_state)
            mask, anchor = solver._p17_pin
            base = x if x is not None else torch.zeros(solver.n_unknowns, device=device, dtype=solver.dtype)
            x = torch.where(mask, anchor, base)
        if getattr(args, "driver", "newton") == "defect":
            # --driver defect (opt-in, DC-RANS P14): the defect-correction march, then the pseudo-time tail below
            from Pipeline.CoupledNewton.defect_march import solve_defect
            start = x if x is not None else torch.zeros(solver.n_unknowns, device=device, dtype=solver.dtype)
            n_ = int(solver.n_cell)
            vol = solver.solver.cell_volume.reshape(-1)[:n_].to(solver.dtype)
            t_rows = slice(4 * n_, 5 * n_)

            def _q(z):
                t = z[t_rows]
                return torch.stack([(t * vol).sum() / vol.sum(), t.max()])
            if rule is not None:
                for r_ in getattr(rule, "rules", ()):
                    if hasattr(r_, "set_reference"):
                        r_.set_reference(solver.residual(start).detach())   # 4f: the merit of the cold start
            if args.dm_warmup > 0:
                # P16-b: a short pseudo-time warm-up before the defect march
                from Pipeline.CoupledNewton.pseudo_time import solve_pseudo_time
                start, _ = solve_pseudo_time(
                    solver, start, maxit=args.dm_warmup, turb_cfl=args.pt_turb_cfl, eta=args.pt_eta,
                    krylov=args.pt_krylov, scaling=args.pt_scaling, max_linear_rel=args.pt_max_linear_rel,
                    camg_kwargs=_pt_camg_kwargs(args.pt_camg), reuse_plan=args.pt_reuse_plan, log=logger.info)
            x, dm_history = solve_defect(
                solver, start, maxit=args.dm_steps, cfl0=args.dm_cfl0, cfl_max=args.dm_cfl_max,
                growth=args.dm_growth, anderson=args.dm_anderson, inner=args.dm_inner, law=args.dm_law,
                anderson_at_cap=args.dm_anderson_at_cap, hybrid=args.dm_hybrid,
                turb_cfl=args.pt_turb_cfl if args.dm_turb_cfl is None else args.dm_turb_cfl,
                pressure_stage=args.dm_pressure_stage, rebuild_every=args.dm_rebuild_every,
                handover=args.dm_handover, camg_kwargs=_pt_camg_kwargs(args.pt_camg) or None, quantities=_q,
                log=logger.info)
            args.driver = "pseudo-time"          # the tail of this rung
        if getattr(args, "driver", "newton") == "odil":
            # --driver odil (opt-in, 2026-11-10): ODIL3D's inner solve on our residual and exact JVP
            from Pipeline.CoupledNewton.odil_inner import solve_odil, solve_odil_dc
            start = x if x is not None else torch.zeros(solver.n_unknowns, device=device, dtype=solver.dtype)
            if hasattr(getattr(solver, "_warp", None), "compact_jacobian"):
                return solve_odil_dc(
                    solver, start, maxit=args.max_newton, cfl0=args.pt_cfl0, cfl_max=args.pt_cfl_max,
                    growth=args.pt_growth, reject=args.odil_reject, max_du=args.odil_max_du, beta=args.odil_beta,
                    turb_cfl=args.pt_turb_cfl, pos_diag=args.pt_pos_diag, clip=args.pt_clip,
                    restart=args.odil_restart, krylov=args.pt_krylov, rtol=args.pt_eta,
                    rebuild_every=args.odil_rebuild_every, uref=args.pt_uref,
                    krylov_dtype=getattr(torch, args.odil_krylov_dtype), stop_rule=rule,
                    tol=rung_tolerance(rung), law=args.odil_law, lam_floor=args.odil_lam_floor,
                    max_linear_rel=args.pt_max_linear_rel, progress=newton_progress, log=logger.info)
            return solve_odil(
                solver, start, maxit=args.max_newton, atol=args.odil_atol, cfl0=args.pt_cfl0,
                cfl_max=args.pt_cfl_max, growth=args.pt_growth, reject=args.odil_reject, max_du=args.odil_max_du,
                uref=args.odil_uref, beta=args.odil_beta, restart=args.odil_restart, krylov=args.pt_krylov,
                rtol=args.pt_eta, krylov_dtype=getattr(torch, args.odil_krylov_dtype), progress=newton_progress,
                log=logger.info)
        if getattr(args, "driver", "newton") == "pseudo-time":
            # --driver pseudo-time (opt-in, DC-RANS P2): the pseudo-transient march instead of solve_newton
            from Pipeline.CoupledNewton.pseudo_time import solve_pseudo_time
            start = x if x is not None else torch.zeros(solver.n_unknowns, device=device, dtype=solver.dtype)
            return solve_pseudo_time(
                solver, start, stop_rule=rule, maxit=args.max_newton, tol=rung_tolerance(rung),
                cfl0=args.pt_cfl0, cfl_max=args.pt_cfl_max, cfl_min=args.pt_cfl_min, law=args.pt_law,
                growth=args.pt_growth,
                u_ref=args.pt_uref, turb_cfl=args.pt_turb_cfl, pos_diag=args.pt_pos_diag, eta=args.pt_eta,
                krylov=args.pt_krylov, scaling=args.pt_scaling, precond=args.pt_precond,
                rebuild_factor=args.pt_rebuild_factor, rebuild_every=args.pt_rebuild_every, clip=args.pt_clip,
                limit=args.pt_limit, reject=args.pt_reject, pc_kind=args.pt_pc_kind, pressure=args.pressure,
                sweeps=args.sweeps, cycles=args.cycles, segregate=args.pt_segregate,
                max_linear_rel=args.pt_max_linear_rel, coarse=args.pt_coarse, anderson=args.pt_anderson,
                recycle=args.pt_recycle, forcing=args.pt_forcing, rebuild_growth=args.pt_rebuild_growth,
                profile=args.pt_profile, reuse_plan=args.pt_reuse_plan, retry=args.pt_retry,
                handover=args.pt_handover, handover_cfl=args.pt_handover_cfl, handover_krylov=args.pt_handover_krylov, camg_kwargs=_pt_camg_kwargs(args.pt_camg),
                progress=newton_progress,
                log=logger.info)
        return solver.solve_newton(**keywords)

    def rung_solver(x, rung):
        """``solve_rung``, except that the FIRST rung of the first ladder is the staged cold start with
        ``--cold-start staged`` (``Pipeline.CoupledNewton.coldstart``).  AUTO-RAMP retries and CBBM
        passes, which re-enter the ladder, solve it plainly."""
        if not (cold_state["pending"] and rung == 1):
            return solve_rung(x, rung)
        from Pipeline.CoupledNewton import coldstart
        cold_state["pending"] = False
        logger.info("COLD-START staged on rung 1: stages %s, stage rtol %.1e, stage budget %d, positivity map "
                    "k-fraction %g / ln(omega) cap %g, stage-C mean-flow PTC weight %g, theta0 %g, %d restart(s), "
                    "merit %s; turbulence %s pinned in A, mean flow %s pinned in B", args.cold_stages,
                    args.cold_stage_rtol, args.cold_stage_maxit, args.cold_k_fraction, args.cold_log_cap,
                    args.cold_mean_weight, cold_theta0, args.cold_restarts, args.cold_merit,
                    coldstart.turbulence_fields(solver), coldstart.mean_flow_fields(solver))
        return coldstart.run_staged(
            solver, x if x is not None else torch.zeros(solver.n_unknowns, device=device, dtype=solver.dtype),
            lambda state, **stage: solve_rung(state, rung, **stage),
            stages=args.cold_stages, final_tol=rung_tolerance(rung), final_maxit=args.max_newton,
            stage_rtol=args.cold_stage_rtol, stage_maxit=args.cold_stage_maxit, theta0=cold_theta0,
            mean_weight=args.cold_mean_weight, fraction=args.cold_k_fraction, log_cap=args.cold_log_cap,
            restarts=args.cold_restarts, merit=args.cold_merit, log=logger.info, summary=cold_state["summary"],
            **({} if args.cold_finish_below is None else {"finish_below": args.cold_finish_below}),
            **({"finish_trial": "always"} if getattr(args, "cold_finish_trial_always", False) else
               ({"finish_trial": True} if getattr(args, "cold_finish_trial", False) else {})),
            **({"finish_restarts": args.cold_finish_restarts} if getattr(args, "cold_finish_restarts", 0) else {}),
            **({"k_floor": args.cold_k_floor} if getattr(args, "cold_k_floor", None) is not None else {}),
            **({} if not args.save_rung_states else {"checkpoint": save_stage_state}))

    if getattr(args, "operator_correction_mode", "newton") in ("frozen", "auto"):
        # --operator-correction-mode frozen: every rung is the defect-correction iteration around the
        # rung's own Newton solve of the plain operator (the correction disarmed, -C(q_k) as a FAS right-hand side);
        # the certificate below reads max|F + C| with the correction armed, as in Newton mode.
        if getattr(solver, "_operator_correction", None) is None:
            raise ValueError("--operator-correction-mode frozen needs --operator-correction")
        from Pipeline.CoupledNewton.operator_correction import frozen_correction_solve
        _newton_rung_solver = rung_solver

        def rung_solver(x, rung, _inner=_newton_rung_solver):
            start = x if x is not None else torch.zeros(solver.n_unknowns, device=device, dtype=solver.dtype)
            x_out, rung_history, outer = frozen_correction_solve(
                solver, start, lambda state: _inner(state, rung), gate=args.gate,
                max_outer=int(args.operator_correction_outer), log=logger.info)
            frozen_outer.extend(outer)
            if args.operator_correction_mode == "auto" and outer[-1] > args.gate:
                # 'auto': the defect-correction iteration did not contract to the gate -> Newton on F + C
                # (the correction armed again by frozen_correction_solve) from the SAME start
                logger.info("OPERATOR-CORRECTION auto: the frozen path stopped at max|F + C| = %.3e after %d outer "
                            "steps; Newton on F + C from the start", outer[-1], len(outer) - 1)
                frozen_outer.append(float("nan"))              # marks the hand-over in operator_correction.json
                x_out, newton_history = _inner(start, rung)
                rung_history = (list(newton_history) if not rung_history
                                else list(rung_history) + list(newton_history)[1:])
            if not rung_history:
                # certified before any inner solve: one (zero-step) Newton call on F + C gives the entry its history row
                x_out, rung_history = _inner(x_out, rung)
            return x_out, rung_history
    frozen_outer = []

    def save_stage_state(label, state):
        # --save-rung-states with --cold-start: every stage's end state, so a capped run keeps one
        path = os.path.join(logger.saving_path, f"x_stage_{label}.pt")
        torch.save(state.detach().cpu(), path)
        logger.info("cold-start stage %s state saved -> %s", label, os.path.basename(path))

    def one_ramp(x_start):
        # ``x_start``, not the enclosing ``x0``: this took the ORIGINAL first
        # iterate, so every CBBM pass after the first silently
        # re-climbed the whole viscosity ladder from scratch while its log said
        # "warm-start the next pass from the converged state of this one".
        # Every ladder starts at the case's own coefficients (bit-identical on any path that completed its ladders:
        # their last rung is m = 1; run_continuation overwrites nu at its first rung anyway).
        solver.solver.nu_coeff = torch.full_like(solver.solver.nu_coeff, nu)
        if case_alpha is not None:
            solver.solver.alpha_coeff = case_alpha.clone()
        if iterate_recorder is not None:
            iterate_recorder.new_ramp(x_start)
        if jfnk_policy is not None:
            # JFNK: a ladder (the first one, an AUTO-RAMP retry, a CBBM pass) is a new problem and
            # starts from a fresh build; within it the policy carries the preconditioner across rungs.
            jfnk_policy.reset()
        checkpoint = (
            None if not args.save_rung_states else
            (lambda index, multiplier, state: (
                torch.save(state.detach().cpu(),
                           os.path.join(logger.saving_path,
                                        f"x_rung{index}.pt")),
                logger.info("rung %d (%gx) state saved -> x_rung%d.pt",
                            index, multiplier, index))[-1]))
        if args.continuation == "adaptive" and (x_start is None or x_start is cold_first["x"]):
            # --continuation adaptive, a start from rest (or from the case's internal field): the adaptive path.
            # Any other start (a --state, a 'ramp:' fallback state, a CBBM pass's root) is solved below at the
            # case's own viscosity, as a ladder without --nu-ramp is.
            try:
                return run_adaptive_continuation(
                    solver, nu, x_start, solve_rung, tol=args.tol, gate=args.gate, logger=logger,
                    rung_checkpoint=checkpoint, on_rung=lambda index, multiplier: adaptive_rung.update(
                        multiplier=multiplier))
            finally:
                adaptive_rung["multiplier"] = None
        betas = beta_ramp_multipliers(getattr(args, "beta_ramp", None))
        if betas is None:
            return run_continuation(
                solver, nu, ramp_multipliers(args.nu_ramp), x_start,
                rung_solver,
                rung_advance_gate(args), logger,
                rung_checkpoint=checkpoint)
        # --beta-ramp: the viscosity ladder at beta x B0, then the buoyancy rungs at nu x 1; every
        # ladder starts from the case's own beta (read once, as case_alpha), an incomplete one leaves it where it stopped
        beta_base = case_beta
        solver.solver.beta = beta_base * betas[0]
        logger.info("buoyancy continuation: viscosity ladder at beta x %g, then beta x %s at the case's viscosity",
                    betas[0], ", ".join(f"{b:g}" for b in betas[1:] + [1.0]))
        res = run_continuation(
            solver, nu, ramp_multipliers(args.nu_ramp), x_start,
            rung_solver,
            rung_advance_gate(args), logger,
            rung_checkpoint=checkpoint)
        n_nu = len(ramp_multipliers(args.nu_ramp))
        res["rungs_total"] = n_nu + len(betas)
        if not res["complete"]:
            return res
        for k, b in enumerate(betas[1:] + [1.0], start=1):
            solver.solver.beta = beta_base * b
            logger.info("buoyancy rung %d/%d: beta x %g (nu x 1)", k, len(betas), b)
            x_b, hist = rung_solver(res["x"], n_nu)        # the final rung's tolerance: rung index n_nu
            for entry in hist:
                entry["rung"] = n_nu + k
                entry["nu_multiplier"] = 1.0
                entry["beta_multiplier"] = b
            res["history"] += hist
            res["x"], res["rungs_solved"] = x_b, n_nu + k
            if checkpoint is not None:
                checkpoint(n_nu + k, b, x_b)
            if hist[-1]["max_abs"] >= rung_advance_gate(args) and b < 1.0:
                res["complete"] = False
                logger.info("RAMP INCOMPLETE: buoyancy rung beta x %g stopped at max|r| = %.3e; the state solves "
                            "beta x %g, NOT the case's buoyancy", b, hist[-1]["max_abs"], b)
                break
        return res

    # CBBM outer iteration (Bai Eq. 15).  Inert unless the case declares
    # `lagMean = true` on a rack discharge: `cbbm_outer_pass` returns
    # (0.0, []) when it finds no lagged rack, so the loop runs once and exits,
    # which is the shipped behaviour for every non-DataCenter case.
    #
    # Why the loop is OUT here rather than inside the Newton solve: the frozen
    # mean is what makes the discharge ghost a plain Dirichlet, and that is the
    # whole reason the Jacobian is assemblable at all.  Refreezing mid-solve
    # would put the dense rank-1 block back into the linearisation the
    # assembler already refused.
    if start_projected is not None:
        # --learned-init-project-energy with --state-guard: both starts were projected before the guard (above)
        with open(os.path.join(logger.saving_path, "projection.json"), "w") as handle:
            json.dump(start_projected, handle, indent=1)
    elif getattr(args, "learned_init_project_energy", 0) and not getattr(args, "learned_init_project", 0) \
            and x0 is not None:
        x0, projection = energy_pass(x0)
        with open(os.path.join(logger.saving_path, "projection.json"), "w") as handle:
            json.dump({"energy": projection}, handle, indent=1)
    elif getattr(args, "learned_init_project", 0) and x0 is not None:
        # Opt-in: continuity projection of the start, INSIDE the Newton timer (started above)
        from Pipeline.CoupledNewton.continuity_projection import project_continuity
        if getattr(args, "learned_init_project_fast", False):
            # --learned-init-project-fast: the projection's fast path, and the log line from
            # its own record (the start's and the projected state's residuals it evaluated) instead of two more
            # deterministic residual evaluations inside the Newton timer
            x0, projection = project_continuity(solver, x0, int(args.learned_init_project), logger=logger,
                                                fast_residual=True, reuse_hierarchy=True, eager_gradient=True,
                                                gpu_hierarchy=bool(getattr(args, "learned_init_project_gpu", False)))
            first, last = projection["before"], projection["after"]
            logger.info("LEARNED-INIT PROJECT done: max|r| %.6e -> %.6e, ||r||2 %.6e -> %.6e (%s) in %.2f s%s "
                        "[--learned-init-project-fast: %d hierarchy build(s), start residual %.3f s]",
                        first["all"]["max_abs"], last["all"]["max_abs"], first["all"]["l2"], last["all"]["l2"],
                        ", ".join(f"{name} {last[name]['max_abs']:.6e}" for name in ("momentum", "continuity",
                                                                                     "energy") if name in last),
                        projection["seconds"], "" if projection["stopped"] is None else
                        "  (stopped: %s)" % projection["stopped"],
                        sum(1 for st in projection["steps"] if st.get("hierarchy_built")),
                        projection.get("start_residual_seconds", float("nan")))
        else:
            before = solver.residual(x0)
            x0, projection = project_continuity(solver, x0, int(args.learned_init_project), logger=logger)
            logger.info("LEARNED-INIT PROJECT done: max|r| %.6e -> %.6e, ||r||2 %.6e -> %.6e (%s) in %.2f s%s",
                        float(before.abs().max()), projection["after"]["all"]["max_abs"], float(before.norm()),
                        projection["after"]["all"]["l2"], block_report(solver, solver.residual(x0)),
                        projection["seconds"], "" if projection["stopped"] is None else
                        "  (stopped: %s)" % projection["stopped"])
        if getattr(args, "learned_init_project_energy", 0):
            # CHT2 README section 18 (no guard): the energy projection after the continuity projection
            x0, energy_record = energy_pass(x0)
            projection = {"continuity": projection, "energy": energy_record}
        with open(os.path.join(logger.saving_path, "projection.json"), "w") as handle:
            json.dump(projection, handle, indent=1)
    # --continuation adaptive: the first iterate that counts as a start from rest besides the zero start (None) --
    # the case's internal field; a --state start does not
    cold_first["x"] = x0 if (args.state is None and args.init == "internal") else None
    if monitor is None:
        ramp = one_ramp(x0)
    else:
        ramp = run_learned_init(one_ramp, monitor, x0, x_fallback, args, solver, logger, started)
    if (args.auto_ramp and args.nu_ramp is None and args.init == "zero"
            and args.state is None and args.continuation != "adaptive"):
        first = float(solver.residual(ramp["x"]).abs().max())
        if not (first < args.gate and ramp["complete"]):
            logger.warning(
                "AUTO-RAMP: the cold start stopped at max|r| = %.3e above the %.1e "
                "gate after %d steps; retrying from the same first iterate with "
                "--nu-ramp %s (disable with --no-auto-ramp)", first, args.gate,
                len(ramp["history"]) - 1, AUTO_RAMP_SCHEDULE)
            failed_history = ramp["history"]
            args.nu_ramp = AUTO_RAMP_SCHEDULE
            ramp = one_ramp(x0)
            ramp["history"] = failed_history + ramp["history"]
    if once:
        from Pipeline.CoupledNewton import block_amg as _bam_once
        _bam_once.HAND_BACK_ONCE["on"] = False
        _bam_once.RESCUE_ESCALATION["on"] = False
    if escalation["R"] > 0.0:
        # --camg-rescue-escalate: the switch is process-wide; clear it after the solve, so that nothing called later
        # in this process inherits an escalation
        from Pipeline.CoupledNewton import block_amg as _bam_esc
        _bam_esc.RESCUE_ESCALATION["on"] = False
    pol = e3_stats.get("policy")
    if pol is not None:
        print("E3STAT switches=%d probing_steps=%d contractions=%s" %
              (pol.switches, pol.probing_steps, pol.contractions[:60]), flush=True)
    if args.cell_mass_probe and probe_rows:
        import numpy as _np
        _np.save(args.cell_mass_probe, _np.stack(probe_rows))
    cbbm_history = []
    for outer in range(1, max(1, args.cbbm_passes) + 1):
        # One residual evaluation so the BC-enforced temperature belongs to
        # THIS pass's converged state and not to whatever forward happened to
        # run last inside the line search.
        solver.residual(ramp["x"])
        worst, report = cbbm_outer_pass(
            solver.solver,
            {"t_cpd_cell": getattr(solver.solver, "bc_t_cpd_cell", None)})
        if not report:
            break
        for name, previous, fresh, change in report:
            logger.info(
                "[CBBM] pass %d  %s: intake mean %s -> %.5f K, discharge "
                "%.5f K (change %s)", outer, name,
                "seed" if previous is None else f"{previous:.5f}",
                fresh, fresh + _rack_delta(solver.solver, name),
                "seed" if previous is None else f"{change:+.5f}")
        cbbm_history.append({"pass": outer, "worst": worst,
                             "racks": [{"patch": n, "previous": p_, "new": f,
                                        "change": c}
                                       for n, p_, f, c in report]})
        logger.info("[CBBM] pass %d  max |dT_out| = %s K   (tolerance %.4g K)",
                    outer, "seed" if worst == float("inf") else f"{worst:.5f}",
                    args.cbbm_tol)
        if worst < args.cbbm_tol:
            logger.info("[CBBM] fixed point reached after %d pass(es) at "
                        "%.5f K", outer, worst)
            break
        if outer == max(1, args.cbbm_passes):
            logger.warning(
                "[CBBM] NOT CONVERGED: stopped at %.5f K against a %.4g K "
                "tolerance after %d passes. Any number quoted from this run "
                "carries that residual.", worst, args.cbbm_tol, outer)
            break
        # Warm-start the next pass from the converged state of this one; only
        # the four frozen scalars moved.
        ramp = one_ramp(ramp["x"])
    x, history = ramp["x"], ramp["history"]
    if order1_handback is not None:
        logger.info("%s", order1_handback.summary())
    if order1_switch is not None:
        logger.info("%s", order1_switch.summary())
    if getattr(learned_factory, "camg_config", None) is not None:
        logger.info("%s", learned_factory.camg_config.summary())     # O1-C: the choices and their network seconds
    if camg_plan_reuse is not None:
        # --camg-reuse-plan: every solve is done (ladder, AUTO-RAMP retry, CBBM passes);
        # free the cached plans before the post-processing and log the whole-run build-reason counts T3 records
        logger.info("camg plan reuse, whole run: %s", camg_plan_reuse.release())
        if camg_plan_reuse.setup_summary() is not None:
            # The AMG setup split of the whole run (timed builds: an AMG-setup flag or --component-timers)
            logger.info("camg setup, whole run: %s", camg_plan_reuse.setup_summary())
    # (the network module is loaded only by a run that uses the learned smoother)
    O1_TIMING = getattr(sys.modules.get("NNmodels.coupled_amg_net"), "O1_TIMING", None)
    if O1_TIMING:
        # LPCNS_O1_TIMERS=1: the smoother network's forward split of the whole run
        logger.info("camgnet timing, whole run: %s", ", ".join(
            f"{k} {v:.3f}" if isinstance(v, float) else f"{k} {v}" for k, v in O1_TIMING.items()))
    if getattr(learned_factory, "camg_reuse", None) is not None and hasattr(learned_factory.camg_reuse, "summary"):
        # --camg-net-every R > 1: the builds that ran the network and those that reused its output
        logger.info("network output reuse, whole run: %s", learned_factory.camg_reuse.summary())
    d1_state = getattr(learned_factory, "camg_d1", None)
    if d1_state is not None:
        # --camg-d1: builds, checks, the thetas chosen and the seconds the checks spent inside the builds
        thetas = [c["theta"] for c in d1_state["checks"]]
        logger.info("--camg-d1, whole run: %d builds, %d checks (thetas %s), %d level forwards of the network, %d level "
                    "builds on a reused output, checked builds %.3f s (power steps %.3f s, %d extra cycles built)",
                    d1_state["builds"], len(d1_state["checks"]), ",".join("%g" % t for t in thetas),
                    d1_state["mod"].forwards, d1_state["mod"].reused, d1_state["seconds"],
                    sum(c["power_s"] for c in d1_state["checks"]), sum(c["cycles_built"] - 1 for c in d1_state["checks"]))
        if d1_state.get("retain"):
            blog = d1_state.get("budget_log") or []
            logger.info("--camg-d1-retain %d, whole run: %d budgeted solves, %d handed over (%d of them to the l1 rescue, "
                        "%d at a later stagnation)", d1_state["retain"], len(blog),
                        sum(bool(e.get("handover")) for e in blog), sum(bool(e.get("rescue")) for e in blog),
                        sum(bool(e.get("late")) for e in blog))
    if getattr(args, "camg_keep_w", False) and getattr(learned_factory, "camg_reuse", None) is not None:
        # What the reuse did over the whole run (level forwards, level builds that kept W)
        logger.info("--camg-keep-w, whole run: %d level forwards of the network, %d level builds kept W",
                    learned_factory.camg_reuse.fresh_forwards, learned_factory.camg_reuse.kept_builds)
    if jfnk_policy is not None:
        # JFNK: every solve is done; the kept preconditioner goes before the post-processing, and the
        # run's totals are read off the history newton_residual.csv records (every rung; an AUTO-RAMP retry
        # includes the failed attempt; a CBBM run, as everywhere else in this entry, its last pass).
        from Pipeline.CoupledNewton import jfnk as _jfnk
        jfnk_policy.release()
        logger.info("JFNK mode, whole run (operator %s, jvp %s, policy %s): %s", args.operator, args.jvp,
                    jfnk_policy.spec, _jfnk.describe(_jfnk.totals(ramp["history"])))
    if cbbm_history:
        write_cbbm_history(cbbm_history, logger)
    if device.type == "cuda":
        torch.cuda.synchronize(device)
    elapsed = time.time() - started
    if eliminator is not None:
        # The elimination's share of the Newton timer (its seconds are inside ``elapsed``)
        totals = eliminator.totals
        logger.info("nonlinear elimination, whole run: %d eliminating steps, %.1f s (assembly %.1f s, host LU %.1f s), "
                    "%d local Newton steps, %d corrections rejected, %d singular subsets, %d LUs, depth %d",
                    totals["steps"], totals["seconds"], totals["assembly_seconds"], totals["factor_seconds"],
                    totals["local_iterations"], totals["rejected"], totals["singular"], totals["factorisations"],
                    totals["max_depth"])

    if args.cold_start != "off" and (getattr(solver, "_pin", None) is not None or solver._ptc_shift is not None):
        # The staged cold start's certificate is the case's own residual; a stage that leaked its pin or shift
        # would make the line below read another function (coldstart clears both on every exit path).
        raise RuntimeError("a pinned row or a PTC shift is still armed after the solve")
    residual = solver.residual(x)
    reached = float(residual.abs().max())

    logger.info(
        "Newton finished in %.1f s and %d steps, %d GMRES iterations, %d "
        "assemblies (%.1f s of the total); residual history written to %s",
        elapsed, len(history) - 1,
        sum(entry.get("inner_iterations", 0) for entry in history),
        sum(1 for entry in history if entry.get("reassembled")),
        sum(entry.get("assembly_seconds", 0.0) for entry in history),
        write_history(history, logger))
    if args.inner_history:
        logger.info("inner GMRES residual curves written to %s",
                    write_inner_history(history, logger))
    if ramp.get("continuation") is not None:
        # --continuation adaptive: the path's record (constants, tolerances, every attempt), with the certificate
        from Pipeline.CoupledNewton.continuation import json_ready
        record_path = os.path.join(logger.saving_path, "continuation.json")
        with open(record_path, "w") as handle:
            json.dump(json_ready({**ramp["continuation"], "certificate_max_abs": reached,
                                  "certified": bool(reached < args.gate and ramp["complete"]),
                                  "newton_seconds": elapsed}), handle, indent=1, allow_nan=False)
        logger.info("adaptive continuation record written to %s", record_path)
    logger.info("max|r| = %.6e  (%s) against the %.1e gate",
                reached, block_report(solver, residual), args.gate)
    if getattr(solver, "_operator_correction", None) is not None:
        # The certificate above is max|F + C_phi|; the correction's global sums at the final state
        from Pipeline.CoupledNewton.operator_correction import conservation_report
        report = conservation_report(solver, solver._operator_correction, x)
        if frozen_outer:
            report["frozen_outer_max_abs"] = list(frozen_outer)          # --operator-correction-mode frozen
            report["mode"] = "frozen"
        with open(os.path.join(logger.saving_path, "operator_correction.json"), "w") as handle:
            json.dump(report, handle, indent=1)
        logger.info("OPERATOR-CORRECTION at the final state: certificate max|F + C| = %.6e, continuity block of C "
                    "sums to %.3e (relative %.3e), max|C| = %.3e", report["certificate"], report["cont"]["sum"],
                    report["cont"]["relative"], report["max_abs_correction"])
    if getattr(args, "camg_cuda_graph", False):
        # The whole run's graph counters (captures kept / rejected / failed, replays)
        from Pipeline.CoupledNewton import block_amg as _block_amg
        logger.info("--camg-cuda-graph, whole run: %s", _block_amg.graph_stats())
    if getattr(args, "krylov_device_loop", False):
        # Warp backend: the device loop's counters (solves, captures, graph launches, eager steps,
        # capture failures and the last one's reason)
        from SparseSolver import gmres_warp as _gmres_warp
        logger.info("--krylov-device-loop, whole run: %s", dict(_gmres_warp.STATS))
    if args.fast_line_search:
        # The number above is the certificate; say how it was evaluated
        logger.info("--fast-line-search: the Newton loop's residuals ran without deterministic algorithms; the "
                    "max|r| above is a fresh evaluation at the returned state with deterministic algorithms %s "
                    "(last loop row max|r| = %.6e)", "ON" if torch.are_deterministic_algorithms_enabled() else "OFF",
                    history[-1].get("max_abs", float("nan")))
    if args.cold_start != "off":
        cold_path = os.path.join(logger.saving_path, "cold_start.json")
        with open(cold_path, "w") as handle:
            json.dump({"stages": cold_state["summary"], "certificate_max_abs": reached, "gate": args.gate,
                       "ramp_complete": bool(ramp["complete"]),
                       "certified": bool(reached < args.gate and ramp["complete"]),
                       "certificate_residual": "unmodified (no pinned row, no PTC shift, no trial map)",
                       "newton_steps": sum(1 for entry in history if entry.get("it", 0) > 0),
                       "newton_seconds": elapsed,
                       "settings": {key: getattr(args, key) for key in (
                           "cold_start", "cold_stages", "cold_stage_rtol", "cold_stage_maxit", "cold_k_fraction",
                           "cold_log_cap", "cold_mean_weight", "cold_restarts", "cold_merit", "cold_finish_below",
                           "cold_finish_trial", "cold_finish_trial_always", "cold_finish_restarts", "cold_k_floor",
                           "init", "nu_ramp")}
                                   | {"cold_theta0": cold_theta0}}, handle, indent=1)
        logger.info("COLD-START certificate on the UNMODIFIED residual (no pinned row, no PTC shift, no trial map): "
                    "max|r| = %.6e against the %.1e gate after %d Newton steps in %.1f s; stages -> %s", reached,
                    args.gate, sum(1 for entry in history if entry.get("it", 0) > 0), elapsed, cold_path)
    if args.compare_root:
        try:
            compare_roots(solver, x, args.compare_root, reached=reached, gate=args.gate,
                          certified=bool(reached < args.gate and ramp["complete"]),
                          directory=logger.saving_path, log=logger.info)
        except Exception as failure:      # an instrument must not change a run's exit status
            logger.error("--compare-root failed: %s", failure, exc_info=True)
    # Which viscosity the number above belongs to, on the same screen as the
    # number.  A partial ramp reads identically to a complete one everywhere
    # else, which is how a nu x 4096 answer was once reported as a converged
    # case (docs/research/2026-08-18-dc-first-solve.md).
    logger.info("the answer is continuation rung %d of %d: nu = %.6g, %.4gx the "
                "case's own %.6g%s", ramp["rungs_solved"], ramp["rungs_total"],
                nu * ramp["final_multiplier"], ramp["final_multiplier"], nu,
                "" if ramp["complete"] else
                "  <-- RAMP INCOMPLETE, this is NOT the case's own viscosity")
    if component_timers:
        logger.info("component timers (synced, all rungs): jacobian %.1f s, sparsity %.1f s, preconditioner "
                    "%.1f s, krylov %.1f s; assembly_seconds %.1f s",
                    *(sum(entry.get(key, 0.0) for entry in history) for key in (
                        "jacobian_seconds", "sparsity_seconds", "precond_seconds", "krylov_seconds",
                        "assembly_seconds")))
    if iterate_recorder is not None:
        try:
            ref_path = getattr(args, "iterate_error_ref", None)
            report_iterate_error(iterate_recorder, solver, x, logger, reached=reached,
                                 gate=args.gate, complete=bool(ramp["complete"]),
                                 reference=(None if ref_path is None else
                                            load_state(ref_path, x.device, x.dtype).reshape(x.shape)),
                                 reference_path=ref_path)
        except Exception as failure:      # an instrument must not change a run's exit status
            logger.error("--iterate-error: scoring the iterates failed: %s", failure, exc_info=True)
        if getattr(args, "save_iterates", False):
            # Diagnostic: the recorded iterates for an offline Jacobian probe
            try:
                path = os.path.join(logger.saving_path, "iterates.pt")
                torch.save({"rows": iterate_recorder.rows,
                            "states": [None if s is None else s.to(torch.float64) for s in iterate_recorder.states]},
                           path)
                logger.info("--save-iterates: %d iterates written to %s", len(iterate_recorder.states), path)
            except Exception as failure:  # an instrument must not change a run's exit status
                logger.error("--save-iterates failed: %s", failure, exc_info=True)
    try:
        logger.info("grid-parity pressure mode amplitude %.6e of |p - mean p|",
                    parity_amplitude(solver, x))
    except NotAParityLattice as undefined:
        # NAMED, not NaN.  A bare nan reached this line on the DataCenter run of
        # the viscosity ramp and said nothing about which of the field, the solver or the
        # instrument was at fault; the reading is one leg of the convergence
        # standard, so an absent leg has to announce itself.  Caught rather than
        # propagated because a diagnostic must not end a converged run.
        logger.info("grid-parity pressure mode amplitude UNDEFINED here: %s",
                    undefined)
    if device.type == "cuda":
        # max with the run's peak solve_newton folded away before resetting the allocator's peak per step
        # (--component-timers); without the flag the attribute is absent and this is the plain reading
        logger.info("peak CUDA memory %.1f GB",
                    max(torch.cuda.max_memory_allocated(device), getattr(solver, "_cuda_peak_bytes", 0)) / 2 ** 30)
        if any(getattr(args, flag, False) for flag in ("krylov_basis_chunk", "camg_lean_build", "camg_drop_block_values",
                                                       "camg_int32_groups")) or args.fixed_jacobian_pattern == "blocks":
            # Only with a memory flag: what the device held beyond the tensors (allocator reserve,
            # CUDA-graph pools, library workspaces, the context) at the end of the run
            free, total = torch.cuda.mem_get_info(device)
            logger.info("M5 memory: peak reserved %.2f GiB (peak allocated since the last step reset %.2f GiB); "
                        "device used at the end %.2f of %.2f GiB",
                        torch.cuda.max_memory_reserved(device) / 2 ** 30,
                        torch.cuda.max_memory_allocated(device) / 2 ** 30, (total - free) / 2 ** 30, total / 2 ** 30)
    if getattr(solver, "_foam_bridge", None) is not None:
        _fb = solver._foam_bridge.finish(x)
        logger.info("--foam-bridge: %s", _fb)
        solver._foam_bridge.result = _fb
    if getattr(args, "save_state_to", None):
        Path(args.save_state_to).parent.mkdir(parents=True, exist_ok=True)
        torch.save(x.detach().to("cpu"), args.save_state_to)
        logger.info("--save-state-to: final state saved -> %s", args.save_state_to)
    if getattr(args, "dump_residual", None):
        _dump = Path(args.dump_residual)
        dump_residual(solver, x, _dump.with_name(_dump.stem + ".final" + _dump.suffix), logger, "final")
    if args.export:
        logger.info("Wrote %s", export_state(solver, x, case, logger, run_name))
    if args.grad_check:
        gradient_report(solver, x, nu, logger)
    accepted = True
    if reached >= args.gate:
        # Steps are counted over the WHOLE ramp while --max-newton is the budget
        # of ONE rung, so the two are only the same number on a single-rung run.
        # Printed as "96 of 60" on a DataCenter ramp before the
        # denominator was named.
        logger.info("NOT ACCEPTED: the run stopped above the gate after %d "
                    "Newton steps over %d rung(s), budget %d per rung",
                    len(history) - 1, ramp["rungs_solved"], args.max_newton)
        accepted = False
    if not ramp["complete"]:
        # Independent of the gate ON PURPOSE.  The residual belongs to whatever
        # viscosity the ramp reached, so a small one certifies nothing about the
        # case; the DataCenter arm exited 0 on max|r| = 2.596e-11 at nu x 4096.
        logger.info("NOT ACCEPTED: the continuation ended at rung %d of %d, so "
                    "this answer is nu x %.4g and not the case's own viscosity, "
                    "whatever max|r| reads", ramp["rungs_solved"],
                    ramp["rungs_total"], ramp["final_multiplier"])
        accepted = False
    if args.save_final_state:
        from entries.steady_final_artifacts import (
            atomic_json, hash_case_inputs, save_final_artifacts,
            sha256 as artifact_sha256, source_fingerprint, source_module_paths,
            tensor_fingerprint, validate_loaded_sidecars,
        )
        from FVdomain.Load_mesh import (CUSTOM_BC_SOURCE_SHA256,
                                        CUSTOM_BC_SOURCE_FAILURES)
        last = history[-1]
        if last.get("max_abs", float("inf")) < args.tol:
            stop_reason = "internal_tolerance"
        elif last.get("stalled"):
            stop_reason = "line_search_refused"
        elif last.get("it", 0) >= args.max_newton:
            stop_reason = "max_iterations"
        else:
            stop_reason = "normal_return_above_internal_tolerance"
        if not cbbm_history:
            cbbm_status = "not_applicable"
        else:
            cbbm_status = ("converged" if cbbm_history[-1]["worst"] < args.cbbm_tol
                           else "incomplete")
        start_contract = certificate_start
        try:
            required_sidecars=(('bc/setU.py',)
                               if start_contract['case']['family']=='Cylinder' else ())
            executed_sidecars=validate_loaded_sidecars(
                dataset_dir,start_contract['input_sha256'],
                CUSTOM_BC_SOURCE_SHA256,CUSTOM_BC_SOURCE_FAILURES,
                required=required_sidecars)
            alpha_config=(constant_of(fvconfig['theta_pde'],'alpha')
                          if 'alpha' in fvconfig['theta_pde'] else None)
            alpha_tensor=getattr(solver.solver,'alpha_coeff',None)
            alpha_effective=(float(alpha_tensor.flatten()[0])
                             if isinstance(alpha_tensor,torch.Tensor)
                             and alpha_tensor.numel()>0 else None)
            alpha_expected=(float(torch.tensor(alpha_config,dtype=torch.float32))
                            if alpha_config is not None else None)
            coefficient_names=('nu_coeff','alpha_coeff','theta_PDE',
                               'source_term','heat_source_term','source_term_base')
            coefficient_hashes={
                name:tensor_fingerprint(getattr(solver.solver,name))
                for name in coefficient_names
                if isinstance(getattr(solver.solver,name,None),torch.Tensor)}
            producer_fields = {
                "case": start_contract["case"],
                "input_sha256": start_contract["input_sha256"],
                "input_unchanged": (hash_case_inputs(dataset_dir) ==
                                    start_contract["input_sha256"]),
                "source": {
                    "tree_sha256": start_contract["source_tree_sha256"],
                    "entry_sha256": start_contract["entry_sha256"],
                    "git_head": start_contract["source_head"],
                    "imported_modules": source_module_paths(
                        start_contract["code_root"], required=(
                            'FVdomain.load_case',
                            'Pipeline.CoupledNewton.collocated_newton',
                            'Utils.get_param', 'entries.steady_final_artifacts')),
                    "unchanged": (source_fingerprint(start_contract["code_root"]) ==
                                  start_contract["source_tree_sha256"] and
                                  artifact_sha256(__file__) ==
                                  start_contract["entry_sha256"]),
                },
                "resolved_flags": vars(args).copy(),
                "start": start_contract["start"],
                "continuation": {
                    "complete": bool(ramp["complete"]),
                    "rungs_solved": int(ramp["rungs_solved"]),
                    "rungs_total": int(ramp["rungs_total"]),
                    "final_multiplier": float(ramp["final_multiplier"]),
                    "multipliers": list(ramp_multipliers(args.nu_ramp)),
                },
                "cbbm": {"status": cbbm_status,
                         "passes": len(cbbm_history),
                         "last_worst": (None if not cbbm_history else
                                        cbbm_history[-1]["worst"]),
                         "tolerance": args.cbbm_tol},
                "stop": {"reported_gate_passed": bool(accepted),
                         "planned_return_code": 0 if accepted else 1,
                         "solver_stop_reason": stop_reason,
                         "last_stalled": bool(last.get("stalled", False)),
                         "last_max_abs": float(last["max_abs"]),
                         "last_iteration": int(last["it"]),
                         "gate": args.gate, "tol": args.tol},
                "producer_original_max_abs": reached,
                "history_sha256": artifact_sha256(
                    Path(logger.saving_path) / "newton_residual.csv"),
                "field_names": list(solver.field_names()),
                "unknowns": solver.n_unknowns,
                "native_nu": nu,
                "native_alpha":{
                    "configured_alpha":alpha_config,
                    "effective_alpha":alpha_effective,
                    "expected_effective_alpha":alpha_expected,
                    "rounding_rule":("float32_then_float64"
                                     if alpha_config is not None else None),
                    "all_loaded_entries_match":(
                        bool((alpha_tensor==alpha_expected).all())
                        if isinstance(alpha_tensor,torch.Tensor) and
                        alpha_expected is not None else None),
                },
                "native_coefficient_hashes":coefficient_hashes,
                "executed_bc_source_sha256":executed_sidecars,
            }
            receipt = save_final_artifacts(logger.saving_path, x, producer_fields)
            logger.info("final producer-only state/receipt saved: x_final.pt %s, "
                        "producer.json; independent original-F verification required",
                        receipt["state"]["sha256"])
        except BaseException as error:
            # Keep a failure record even if the opt-in final artifact is invalid.
            atomic_json(Path(logger.saving_path) / "producer_failure.json",
                        {"kind": "steady-final-producer-failure",
                         "certified": False, "error": repr(error),
                         "reported_gate_passed": bool(accepted)})
            logger.error("opt-in final-state provenance failed: %s", error)
            return 2
    return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
