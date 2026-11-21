"""Solve a prepared OpenFOAM-layout data-centre air case with the GPU Newton-Krylov solver, in place.

The caller (dcdtuni's air runner, a dctwin run directory) has meshed, decomposed and written the case: ``processorN/
{constant/polyMesh,0}``, ``system``, ``constant``.  This entry replaces the ``mpirun ... <solver>Foam -parallel`` call:

    python src/entries/dc_foam_solve.py --run <run directory> [--request REQUEST.json]

It converts the decomposed mesh and the boundary conditions (``Parse_mesh/parse_openfoam_fast.py``,
``Parse_mesh/dctwin_bc.py``) into ``<run>/gpuNewton/case``, runs the pseudo-time Newton-Krylov solve with the
six-family recipe (``docs/research/2026-11-10-odil-style-dc.md``) and, through ``Post_process/foam_bridge.py``, writes
what the caller reads afterwards: the solver log lines on stdout, the ``postProcessing`` monitors of the ACU patches
and the final time directory of every rank.  Everything else the solve prints goes to ``<run>/gpuNewton/engine.log``.

The converted mesh in ``<run>/gpuNewton/case`` is removed after a successful solve (``LPCNS_DC_KEEP_CASE=1`` keeps it).

REQUEST.json (all keys optional): ``stopToleranceK``, ``energyTolerance``, ``window``, ``declaredHeatW``,
``returnPatches``, ``supplyPatches``, ``rhoCp``, ``maxIterations``, ``selfStop``, ``agglomerateBoxM`` (solve on the
coarse level of the mesh: cells merged into boxes of this edge in metres, refined where a merged cell would not be a
valid finite-volume cell, boundary faces kept; ``LPCNS_DC_AGGLOMERATE`` when the key is absent; the fields are written
back to the fine cells piecewise constant), ``mergeFaces`` (without ``agglomerateBoxM``: the same cells with the
faces between two cells and the coplanar faces of one patch on one cell merged, ``Parse_mesh.foamio.decomposed.
merge_faces``; default on for a dcCart coarse level -- a run directory with ``dccart-coarsen.json`` -- whose coarse
cells keep their fine faces, off otherwise; ``LPCNS_DC_MERGE_FACES=0|1`` overrides) -- the caller's stop rule (the ACU
patches default to the function objects of ``system/controlDict``, the declared heat to the servers' of the case
when the key is absent; ``"declaredHeatW": null`` asks for the steadiness of the removed heat instead of its closure).
The GPU is the process's ``CUDA_VISIBLE_DEVICES`` (one device); without it, a device whose service stands by, else
the idle device with the most free memory.  ``LPCNS_DC_DEVICES`` (indices or ``GPU-<uuid>``, comma separated) limits
that choice to the devices the caller may use; when none of them is idle the one with the most free memory is taken.

A solver service keeps one process per device standing by with every module loaded and the CUDA context up (the
start of a fresh process -- some 4,000 file opens -- costs more than a solve on a loaded host).  Every solve still
runs in a process of its own, so it leaves nothing behind: the standing-by process takes the next solve and, as it
starts, launches its successor, which loads while the solve runs and accepts the next one after it:

    python src/entries/dc_foam_solve.py --serve [--idle-timeout SECONDS]      # one per device, in the foreground

``--run`` then hands the solve to the service of its device when one answers (``--no-service`` forces a solve in this
process); ``LPCNS_DC_SERVICE_DIR`` moves the sockets (default ``<repository>/.work/dc_service``).

A caller that knows the mesh before its run directory is ready announces it, and the standing-by process reads it
meanwhile:

    python src/entries/dc_foam_solve.py --prepare <case directory with the mesh> --key <the caller's mesh identity>

The solve whose request names the same identity (``meshSha256``) takes that mesh instead of reading the run
directory's; any other solve reads its own.  Without a standing service the announcement does nothing, unless it
carries ``--start-service SECONDS``: then a service is started for a device this process may use (it leaves after
that many idle seconds) and receives the mesh, so the modules load and the mesh is read while the caller still
prepares its run directory.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import socket
import sys
import time
import traceback
from pathlib import Path

# the six-family recipe (commit 7db9ece8), with the pair-conservative cyclic flux and the ramped continuity
# stiffness of 2026-11-11 (docs/research/2026-11-11-dcdtuni-integration.md, M2-c); an environment variable already
# set by the caller wins
RECIPE_ENV = {
    "PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True",
    "LPCNS_ODIL_BLOCKS": "hybrid", "LPCNS_ODIL_UP_CACHE": "1", "LPCNS_ODIL_TURB_DECOUPLE": "1",
    "LPCNS_ODIL_TURB_CFL_MAX": "50", "LPCNS_ODIL_T_CFL_MULT": "10", "LPCNS_ODIL_SERP_TOL": "1.1",
    "LPCNS_ODIL_SPLIT": "1", "LPCNS_ODIL_FDJVP": "1", "LPCNS_ODIL_HIER_GPU": "1", "LPCNS_ODIL_AMG_EVERY": "12",
    "LPCNS_ODIL_GMRES_Z": "1", "LPCNS_ODIL_EW_REDO": "1.3", "LPCNS_ODIL_EW_REDO_ETA": "0.1",
    # the redone step is kept only where it beats the loose one, shortened to 1/2 and 1/4 where it raises the merit
    "LPCNS_ODIL_EW_REDO_BEST": "1",
    "LPCNS_ODIL_UP_PCOL": "1", "LPCNS_ODIL_EW": "0.9,2,0.8", "LPCNS_ODIL_REBUILD_ADAPT": "0",
    "LPCNS_ODIL_TBOX": "-10,30", "LPCNS_WARP_DC_VALS32": "1", "LPCNS_WARP_DC_FREE": "1", "LPCNS_ODIL_HALF": "1",
    "LPCNS_ODIL_L1_SEL": "1.0", "LPCNS_ODIL_L1": "1.0",
    # the porous baffles' resistance in the preconditioner's momentum blocks (raised-floor tiles: 35-42 % less
    # solver time on the perimeter designs, 2026-11-12; no effect on a hall without such faces)
    "LPCNS_ODIL_POROUS_TAN": "1",
    # the continuity row's stiffness (--odil-beta 100) is reached from 1 over the first 20 steps
    "LPCNS_ODIL_BETA_RAMP": "20",
    # mesh datasets the solve does not read (edge tables, the VTK polyhedron stream)
    "LPCNS_DC_H5_SKIP": "edges,pyvista",
    # eight tangents per residual sweep at every mesh size: the 32-wide buffers the backend defaults to below
    # 0.5 M cells double the device memory of a small hall and save no time (2026-11-11, D1)
    "LPCNS_WARP_DC_BLOCK": "8", "LPCNS_WARP_DC_PROBES": "8",
    # the pseudo-time shift floors the face speed at the largest boundary speed, but at no more than 8 m/s: one
    # small fast outlet (29-50 m/s in three of the owner's halls) must not shorten the step of every slow cell
    "LPCNS_ODIL_LAM_FLOOR_CAP": "8",
    # cut-cell meshes carry thin wall cells and merged cells with skewed faces (every hall of the 2026-11-11
    # campaign, 0.04-0.5 % of the cells).  Two choices keep the pressure-velocity coupling sound on them: the linear
    # face weights are OpenFOAM's (centre-to-face distances projected on the face normal; the distance ratio favours
    # the wrong cell, or exceeds 1, when the face centre is off the line of centres), and the bracket of the
    # momentum-weighted flux interpolates the two cell gradients by cell volume (with the linear weights a thin
    # wall cell's one-sided gradient cancels the bracket's damping and that cell's velocity and pressure run away)
    "LPCNS_FACE_WEIGHTS": "normal", "LPCNS_WARP_DC_MWI_GRADW": "volume",
}
# a temperature box the caller set in the environment wins over the one each solve derives from its case
CALLER_TBOX = os.environ.get("LPCNS_ODIL_TBOX")


def _merge_faces_wanted(request: dict, run: Path) -> bool:
    env = os.environ.get("LPCNS_DC_MERGE_FACES", "").strip()
    if env in ("0", "1"):
        return env == "1"
    if request.get("mergeFaces") is not None:
        return bool(request["mergeFaces"])
    return (Path(run) / "dccart-coarsen.json").is_file()


def temperature_box(smap: dict) -> tuple[float, float]:
    """Bounds (K, excess over TRef) the march keeps the temperature in while it starts up.  The recipe's -10 .. 30
    holds for halls whose servers heat the air by 10-15 K; a hall with a 63 K server needs room above it, and a
    supply colder than TRef - 10 room below.  Upper bound: hottest fixed temperature + 1.5 x the largest server
    rise + 5 K (an intake that recirculates half of its own exhaust), never below the recipe's."""
    lo_fixed, hi_fixed = smap.get("T_fixed_excess_K") or (0.0, 0.0)
    rise = max((float(v["deltaT"]) for v in (smap.get("servers") or {}).values()), default=0.0)
    return min(-10.0, lo_fixed - 10.0), max(30.0, max(hi_fixed, 0.0) + 1.5 * rise + 5.0)


RECIPE_ARGS = [
    "--gpu", "0", "--mwi", "on", "--pressure", "mwi_block", "--backend", "warp", "--init", "internal",
    "--operator", "jvp", "--coefficient", "implicit", "--driver", "odil", "--no-export", "--no-auto-ramp",
    "--nu-ramp", "1", "--pt-cfl0", "5", "--pt-growth", "2", "--pt-eta", "0.03", "--pt-krylov", "80",
    "--pt-turb-cfl", "1", "--pt-pos-diag", "1", "--pt-clip", "0.2", "--odil-law", "ser+", "--odil-beta", "100",
    "--pt-cfl-max", "12", "--odil-rebuild-every", "80", "--conservative-cyclic-flux",
]
APPLICATION = "gpuNewtonBuoyantBoussinesqFoam"
SRC = Path(__file__).resolve().parents[1]
# Device memory of one solve, in MiB: a fixed part and a part per million cells, the latter larger up to 0.5 M cells
# (the backend captures its kernel launches there).  Read from the six hall families (0.73-1.16 million cells) and
# eight of the owner's halls (0.10-0.89 million; docs/research/2026-11-11-dcdtuni-integration.md, "Device memory"
# and D1); a solve whose mesh does not fit the free memory of its device is refused before the time loop instead
# of failing inside it.  LPCNS_DC_MEMORY_MODEL="<base>,<per million>" replaces the numbers (one slope for every
# size), LPCNS_DC_MEMORY_CHECK=0 skips the check.
MEMORY_MODEL = {"baseMiB": 800.0, "perMillionMiB": 18000.0, "smallPerMillionMiB": 25000.0, "smallUpToCells": 500_000}
OUT_OF_MEMORY = "gpu_out_of_memory"
STATUS_MARK = "\x1e"                # a service reply line starting with it is the final status (JSON), not log text


def _load_modules() -> float:
    """Import everything a solve needs (the dominant start-up cost of a fresh process); returns the seconds."""
    t = time.perf_counter()
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    for k, v in RECIPE_ENV.items():
        os.environ.setdefault(k, v)
    os.environ.setdefault("OMP_NUM_THREADS", "4")
    os.environ.setdefault("MKL_NUM_THREADS", "4")
    import entries.solve_with_coupled_newton  # noqa: F401
    import Parse_mesh.dctwin_bc  # noqa: F401
    import Parse_mesh.parse_openfoam_fast  # noqa: F401
    import Post_process.foam_bridge  # noqa: F401
    return time.perf_counter() - t


def _lazy_modules_path() -> Path:
    return _service_dir() / "lazy_modules.json"


def _load_lazy_modules() -> int:
    """Import what earlier solves imported during the solve (recorded by :func:`_record_lazy_modules`), so that a
    process standing by has them loaded; returns how many were new."""
    import importlib
    try:
        names = json.loads(_lazy_modules_path().read_text())
    except (OSError, ValueError):
        return 0
    fresh = 0
    for name in names:
        if name not in sys.modules:
            with contextlib.suppress(Exception):
                importlib.import_module(name)
                fresh += 1
    return fresh


def _record_lazy_modules(before: set) -> None:
    new = {m for m in set(sys.modules) - before if not m.startswith("_")}
    if not new:
        return
    path = _lazy_modules_path()
    with contextlib.suppress(OSError, ValueError):
        known = set(json.loads(path.read_text())) if path.is_file() else set()
        if not new <= known:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
            tmp.write_text(json.dumps(sorted(known | new)))
            os.replace(tmp, path)


@contextlib.contextmanager
def _engine_log(work: Path):
    """Everything written to fd 1 / 2 inside goes to ``<work>/engine.log`` (the caller's stream is ``out``)."""
    sys.stdout.flush()
    sys.stderr.flush()
    saved = os.dup(1), os.dup(2)
    elog = os.open(work / "engine.log", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        os.dup2(elog, 1)
        os.dup2(elog, 2)
        yield
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        os.dup2(saved[0], 1)
        os.dup2(saved[1], 2)
        for fd in (*saved, elog):
            os.close(fd)


PREPARED: dict = {}                     # {"key", "case", "arrays", "skip", "seconds"}: the mesh a caller announced


class DeviceMemoryError(RuntimeError):
    """The mesh does not fit the free memory of the device of this solve."""

    def __init__(self, message: str, gpu: dict):
        super().__init__(message)
        self.gpu = gpu


def memory_estimate_mib(cells: int) -> int:
    """Device memory a solve of ``cells`` cells needs, MiB (``MEMORY_MODEL``)."""
    small = cells <= MEMORY_MODEL["smallUpToCells"]
    base, per_million = MEMORY_MODEL["baseMiB"], MEMORY_MODEL["smallPerMillionMiB" if small else "perMillionMiB"]
    override = os.environ.get("LPCNS_DC_MEMORY_MODEL", "").strip()
    if override:
        base, per_million = (float(v) for v in override.split(","))
    return int(round(base + per_million * cells / 1e6))


def _device_memory() -> dict:
    """Name, total and free memory (MiB) of the device of this process; the memory this process already holds in
    its allocator counts as free.  Empty without CUDA."""
    import torch
    if not torch.cuda.is_available():
        return {}
    free, total = torch.cuda.mem_get_info(0)
    held = torch.cuda.memory_reserved(0) - torch.cuda.memory_allocated(0)
    return {"name": torch.cuda.get_device_name(0), "totalMiB": round(total / 2 ** 20),
            "freeMiB": round((free + held) / 2 ** 20)}


def release_device_memory() -> None:
    """Hand the device memory of an earlier solve of this process back before the next one is sized: its objects
    are unreachable but sit in reference cycles until a collection, and the Krylov basis cache is keyed by size."""
    import gc
    inner = sys.modules.get("Pipeline.CoupledNewton.odil_inner")
    if inner is not None:
        getattr(inner, "_GMRES_BASIS", {}).clear()
    gc.collect()
    torch = sys.modules.get("torch")
    if torch is not None and torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()           # the peak a solve reports is its own


def check_memory(cells: int) -> dict:
    """Refuse (``DeviceMemoryError``) a mesh whose estimate exceeds the free memory of the device."""
    gpu = dict(_device_memory(), estimateMiB=memory_estimate_mib(cells))
    if (os.environ.get("LPCNS_DC_MEMORY_CHECK", "1") != "0" and "freeMiB" in gpu
            and gpu["estimateMiB"] > gpu["freeMiB"]):
        raise DeviceMemoryError(
            f"not enough GPU memory: {cells} cells need about {gpu['estimateMiB']} MiB, the device "
            f"({gpu['name']}, CUDA_VISIBLE_DEVICES={os.environ.get('CUDA_VISIBLE_DEVICES', '')}) has "
            f"{gpu['freeMiB']} MiB free of {gpu['totalMiB']} MiB", gpu)
    return gpu


def _out_of_memory(exc: BaseException) -> bool:
    """Whether ``exc`` is an allocation failure on the device (torch's, Warp's or the check above)."""
    if isinstance(exc, DeviceMemoryError):
        return True
    import torch
    oom = getattr(torch, "OutOfMemoryError", None) or torch.cuda.OutOfMemoryError
    text = str(exc).lower()
    return isinstance(exc, oom) or (isinstance(exc, (RuntimeError, MemoryError)) and (
        "out of memory" in text or "failed to allocate" in text or "cuda_error_out_of_memory" in text))


def _h5_skip() -> tuple:
    return tuple(g for g in os.environ.get("LPCNS_DC_H5_SKIP", "").split(",") if g)


def prepare_mesh(mesh_dir: Path, key: str) -> dict:
    """Read the mesh of ``mesh_dir`` (a case directory with ``processorN/constant/polyMesh``) and build the solver's
    arrays ahead of the solve that will name the same ``key`` (the caller's identity of the mesh)."""
    from Parse_mesh.foamio.decomposed import read_case
    from Parse_mesh.parse_openfoam_fast import DEFAULT_FVCONFIG, _khop_from_fvconfig, build_arrays
    t = time.perf_counter()
    PREPARED.clear()
    case = read_case(mesh_dir, keep="connected_open", check_points=False)
    skip = _h5_skip()
    khop = _khop_from_fvconfig(DEFAULT_FVCONFIG)[0]
    arrays = build_arrays(case, khop=khop, skip=skip)
    PREPARED.update(key=str(key), case=case, arrays=arrays, skip=skip, khop=khop, seconds=time.perf_counter() - t)
    return PREPARED


def _rank_layout(case_dir: Path) -> list:
    """(processor directory, cells) of each partition of ``case_dir``, the cell count from the ``nCells:`` note of its
    owner file (None where the note is absent); empty for a serial case."""
    import gzip
    import re
    from Parse_mesh.foamio.decomposed import processor_dirs
    layout = []
    for rank in processor_dirs(case_dir):
        poly = rank / "constant" / "polyMesh"
        cells = None
        for name, opener in (("owner", open), ("owner.gz", gzip.open)):
            if (poly / name).is_file():
                with opener(poly / name, "rb") as stream:
                    found = re.search(rb"nCells:\s*(\d+)", stream.read(4096))
                cells = int(found.group(1)) if found else None
                break
        layout.append((rank.name, cells))
    return layout


def _take_prepared(run: Path, key):
    """The announced mesh when it is the one of this solve (same identity), re-pointed at ``run``; else None."""
    if not key or PREPARED.get("key") != str(key) or PREPARED.get("skip") != _h5_skip():
        return None
    # the identity names the mesh, not its partitions: a caller that re-decomposed the mesh for the solve (another
    # process count) hands a run whose ranks are not those that were read ahead
    if _rank_layout(run) != _rank_layout(Path(PREPARED["case"].path)):
        PREPARED.clear()
        return None
    held = dict(PREPARED)
    PREPARED.clear()
    case = held["case"]
    case.path = run
    for rank in case.ranks:
        rank.path = run / Path(rank.path).name
    return held


_NO_CALLER = object()


def solve_run(run: Path, request: dict | None, out, extra=(), work: Path | None = None, t0: float | None = None,
              timing: dict | None = None, caller_tbox=_NO_CALLER) -> dict:
    """One solve of ``run`` in this process: the caller's lines go to ``out``; returns the status (also written to
    ``<work>/result.json``).

    ``caller_tbox`` is the temperature box (``LPCNS_ODIL_TBOX``) of the process that asked for this solve, ``None``
    when it set none; a service is handed it with each job. Without it (an older client, or a solve in this
    process) the box this process was started with stands. A service started by one caller kept ITS box for every
    later caller: a client that widened the box had it silently replaced (dcdtuni Base201_GPU: -10,80 asked, the
    service's derived -10,30 used, heat closure stuck at -4 %)."""
    caller = CALLER_TBOX if caller_tbox is _NO_CALLER else caller_tbox
    t0 = time.perf_counter() if t0 is None else t0
    _PHYS_ENV_KEYS = (
        "LPCNS_ODIL_PHYS_DT", "LPCNS_ODIL_PHYS_OLD", "LPCNS_ODIL_PHYS_RTOL", "LPCNS_ODIL_PHYS_TOL",
        "LPCNS_ODIL_PHYS_TURB_RTOL", "LPCNS_ODIL_PHYS_TURB_TOL", "LPCNS_ODIL_PHYS_FLOW_TOL",
        "LPCNS_ODIL_PHYS_TURB_REF_MIN", "LPCNS_ODIL_TURB_MIN", "LPCNS_ODIL_PHYS_T_MULT",
        "LPCNS_ODIL_PHYS_STEPS", "LPCNS_ODIL_PHYS_SAVE", "LPCNS_ODIL_EW", "LPCNS_ODIL_FDJVP",
        "LPCNS_ODIL_TBOX", "LPCNS_DC_AGGLOMERATE_VRATIO", "LPCNS_DC_AGGLOMERATE_SPLIT_DIR",
        "LPCNS_DC_AGGLOMERATE_ASPECT",
    )
    _saved_env = {k: os.environ.get(k) for k in _PHYS_ENV_KEYS}
    run = Path(run).resolve()
    work = Path(work).resolve() if work else run / "gpuNewton"
    case_dir = work / "case"
    case_dir.mkdir(parents=True, exist_ok=True)
    if request is None:
        req_path = work / "request.json"
        request = json.loads(req_path.read_text()) if req_path.is_file() else {}
    request = dict(request, runDir=str(run))
    phys_dt = float(request.get("physDt") or 0.0)
    if phys_dt > 0.0:
        os.environ["LPCNS_ODIL_PHYS_DT"] = str(phys_dt)
        if request.get("physOldState"):
            os.environ["LPCNS_ODIL_PHYS_OLD"] = str(request["physOldState"])
        elif "LPCNS_ODIL_PHYS_OLD" in os.environ and not request.get("keepPhysOldEnv"):
            os.environ.pop("LPCNS_ODIL_PHYS_OLD", None)
        os.environ["LPCNS_ODIL_PHYS_RTOL"] = str(request.get("physRtol", os.environ.get("LPCNS_ODIL_PHYS_RTOL", "1e-3")))
        os.environ["LPCNS_ODIL_PHYS_TOL"] = str(request.get("physTol", os.environ.get("LPCNS_ODIL_PHYS_TOL", "1e-6")))
        os.environ["LPCNS_ODIL_PHYS_TURB_RTOL"] = str(request.get("physTurbRtol", os.environ.get("LPCNS_ODIL_PHYS_TURB_RTOL", "1.0")))
        os.environ["LPCNS_ODIL_PHYS_TURB_TOL"] = str(request.get("physTurbTol", os.environ.get("LPCNS_ODIL_PHYS_TURB_TOL", "1e-3")))
        if "physFlowTol" in request:
            os.environ["LPCNS_ODIL_PHYS_FLOW_TOL"] = str(request["physFlowTol"])
        if "physTurbRefMin" in request:
            os.environ["LPCNS_ODIL_PHYS_TURB_REF_MIN"] = str(request["physTurbRefMin"])
        if "turbMin" in request:
            os.environ["LPCNS_ODIL_TURB_MIN"] = str(request["turbMin"])
        os.environ["LPCNS_ODIL_PHYS_T_MULT"] = str(request.get("physTMult", os.environ.get("LPCNS_ODIL_PHYS_T_MULT", "1.0")))
        os.environ["LPCNS_ODIL_PHYS_STEPS"] = str(int(request.get("physSteps", os.environ.get("LPCNS_ODIL_PHYS_STEPS", "1"))))
        if request.get("physSavePrefix"):
            os.environ["LPCNS_ODIL_PHYS_SAVE"] = str(request["physSavePrefix"])
        # T1 requirement: fixed forcing (empty EW) and FD JVP are required for physical BDF1 dual-time steps
        os.environ["LPCNS_ODIL_EW"] = str(request.get("physEw", ""))
        os.environ["LPCNS_ODIL_FDJVP"] = "1"
        request.setdefault("selfStop", False)
        request.setdefault("declaredHeatW", None)
        if not request.get("agglomerateAspect"):
            for _ak in ("LPCNS_DC_AGGLOMERATE_VRATIO", "LPCNS_DC_AGGLOMERATE_SPLIT_DIR", "LPCNS_DC_AGGLOMERATE_ASPECT"):
                os.environ.pop(_ak, None)
    timing = dict(timing or {})
    status = {"application": APPLICATION, "run": str(run), "ok": False, "pid": os.getpid()}
    # what this solve runs with beyond the recipe: solver settings taken from the environment and extra flags
    if caller is None:
        os.environ["LPCNS_ODIL_TBOX"] = RECIPE_ENV["LPCNS_ODIL_TBOX"]     # not the box of this process's last solve
    else:
        os.environ["LPCNS_ODIL_TBOX"] = caller                            # the asking caller's, not this process's
    overrides = {k: v for k, v in sorted(os.environ.items())
                 if k.startswith(("LPCNS_ODIL_", "LPCNS_WARP_")) and RECIPE_ENV.get(k) != v}
    status["recipe"] = {"overrides": overrides, "extra": [str(a) for a in extra]}
    out.write(f"Exec   : {APPLICATION} -parallel\nCase   : {run}\n"
              f"Device : CUDA_VISIBLE_DEVICES={os.environ.get('CUDA_VISIBLE_DEVICES', '')}\n"
              + (f"Recipe : {json.dumps(status['recipe'])}\n" if overrides or extra else "") + "\n")
    out.flush()
    with _engine_log(work):
        try:
            timing["imports"] = timing.get("imports", 0.0) + _load_modules()
            loaded = set(sys.modules)
            import entries.solve_with_coupled_newton as entry
            from Parse_mesh import dctwin_bc
            from Parse_mesh.foamio.decomposed import read_case
            from Parse_mesh.parse_openfoam_fast import convert
            from Post_process import foam_bridge

            t = time.perf_counter()
            agg = str(request.get("agglomerateBoxM") or os.environ.get("LPCNS_DC_AGGLOMERATE", "")).strip()
            # LPCNS_DC_AGGLOMERATE_CACHE=<dir>: the agglomerated mesh (geometry and patch types only; the boundary
            # values are read from the run's fields below) is kept there per mesh and agglomeration settings, so the
            # solves of one mesh (a sensor fit: one per snapshot and iterate) agglomerate once
            agg_cache = None
            if agg and os.environ.get("LPCNS_DC_AGGLOMERATE_CACHE") and request.get("meshSha256"):
                import hashlib
                import pickle
                key = json.dumps([request["meshSha256"], agg, sorted((k, v) for k, v in os.environ.items()
                                                                      if k.startswith("LPCNS_DC_AGGLOMERATE_")
                                                                      and k != "LPCNS_DC_AGGLOMERATE_CACHE")])
                agg_cache = Path(os.environ["LPCNS_DC_AGGLOMERATE_CACHE"]) / (
                    hashlib.sha256(key.encode()).hexdigest()[:24] + ".pkl")
            held = _take_prepared(run, request.get("meshSha256"))
            if agg_cache is not None and agg_cache.is_file():
                with open(agg_cache, "rb") as fh:
                    case = pickle.load(fh)
                timing["agglomerateCache"] = time.perf_counter() - t
                status["agglomerate"] = case.report.get("agglomerate")
                held, agg = None, ""                                      # read and agglomerated already
            elif held is not None:
                case = held["case"]
                timing["meshPrepared"] = round(held["seconds"], 3)       # spent before this solve was requested
            else:
                # (the coincident-point report of the reader is a diagnostic of 1-5 s; the solve does not use it)
                case = read_case(run, keep="connected_open", check_points=False)
            timing["meshRead"] = time.perf_counter() - t
            if agg:
                # the coarse level of the same mesh (docs/research/2026-11-13-unified-steady-framework.md, L1):
                # cells merged into boxes of this edge (m), boundary faces kept; fields are written back to the
                # fine cells piecewise constant
                from Parse_mesh.foamio.decomposed import adaptive_box_labels, agglomerate, near_patch_cells
                t = time.perf_counter()
                mb = os.environ.get("LPCNS_DC_AGGLOMERATE_MERGE_BOUNDARY", "1") == "1"
                h = float(agg)
                # within one box edge of the patches that drive the flow (unit supplies and returns, baffles: the
                # perforated tiles and panels) the boxes are smaller (LPCNS_DC_AGGLOMERATE_NEAR, metres; default 0: off)
                near_h = float(os.environ.get("LPCNS_DC_AGGLOMERATE_NEAR", "0"))
                near = None
                if near_h > 0:
                    drive = set(request.get("supplyPatches") or []) | set(request.get("returnPatches") or [])
                    drive |= {p.name for p in case.patches if p.type == "cyclic"}
                    if os.environ.get("LPCNS_DC_AGGLOMERATE_NEAR_WALLS", "0") == "1":
                        # the obstacles inside the hall too (racks, fillers, pipes; not the room's own walls): a box
                        # around the end of a thin obstacle joins the fluid on its sides (PERIM, 2026-11-13)
                        drive |= {p.name for p in case.patches if p.type == "wall" and not p.name.startswith("Room_")}
                    near = near_patch_cells(case, drive, h)
                jump = os.environ.get("LPCNS_DC_AGGLOMERATE_JUMP", "1")
                labels, ainfo = adaptive_box_labels(
                    case, h, merge_boundary=mb, near=near, near_h=near_h,
                    max_nonortho_deg=float(os.environ.get("LPCNS_DC_AGGLOMERATE_NONORTHO", "75")),
                    max_level_jump=None if jump.lower() in ("", "none", "off") else int(jump),
                    max_volume_ratio=float(os.environ.get("LPCNS_DC_AGGLOMERATE_VRATIO", "0")) or None,
                    max_aspect=float(os.environ.get("LPCNS_DC_AGGLOMERATE_ASPECT", "0")) or None,
                    max_passes=int(os.environ.get("LPCNS_DC_AGGLOMERATE_PASSES", "20")),
                    dissolve=os.environ.get("LPCNS_DC_AGGLOMERATE_DISSOLVE", "1") == "1",
                    layers=os.environ.get("LPCNS_DC_AGGLOMERATE_LAYERS", "0") == "1",
                    absorb_ratio=float(os.environ.get("LPCNS_DC_AGGLOMERATE_ABSORB", "0.1")) or None)
                keep = ainfo.pop("keep_faces_of")
                ainfo["unmerged_cells"] = int(keep.sum())
                case = agglomerate(case, labels, ainfo, merge_boundary=mb, keep_faces_of=keep)
                status["agglomerate"] = case.report["agglomerate"]
                timing["agglomerate"] = time.perf_counter() - t
                if held is not None:
                    held["arrays"] = None                                 # built for the fine mesh
                if agg_cache is not None:
                    agg_cache.parent.mkdir(parents=True, exist_ok=True)
                    tmp = agg_cache.with_suffix(f".tmp{os.getpid()}")
                    with open(tmp, "wb") as fh:
                        pickle.dump(case, fh, protocol=pickle.HIGHEST_PROTOCOL)
                    os.replace(tmp, agg_cache)
            elif _merge_faces_wanted(request, run):
                # a dcCart coarse level keeps the fine faces on its coarse cells (Base201 L1: 44,387 cells, 4.07 M
                # faces); the Warp kernels loop over a cell's faces and ghosts, so they cost as the fine mesh would
                from Parse_mesh.foamio.decomposed import merge_faces
                t = time.perf_counter()
                n_before = int(case.n_faces)
                case = merge_faces(case)
                status["mergeFaces"] = {**case.report["agglomerate"], "faces_before": n_before,
                                        "faces": int(case.n_faces)}
                timing["mergeFaces"] = time.perf_counter() - t
                if held is not None:
                    held["arrays"] = None                                 # built for the unmerged faces
            status["cells"] = int(case.n_cells)
            release_device_memory()
            status["gpu"] = check_memory(status["cells"])
            t = time.perf_counter()
            # the areas the translation reads are those of the flow-rate patches, which the subset keeps whole
            areas = {p.name: float(case.magSf[p.start:p.start + p.size].sum()) for p in case.patches if p.size}
            smap = dctwin_bc.translate(run, case_dir, areas)
            if caller is None:
                os.environ["LPCNS_ODIL_TBOX"] = "%g,%g" % temperature_box(smap)
            status["temperatureBoxK"] = [float(v) for v in os.environ["LPCNS_ODIL_TBOX"].split(",")]
            timing["boundaryConditions"] = time.perf_counter() - t
            t = time.perf_counter()
            arrays = None
            if held is not None:                                         # built for the default stencil reach
                from Parse_mesh.parse_openfoam_fast import DEFAULT_FVCONFIG, _khop_from_fvconfig
                fvc = case_dir / "fvconfig.toml"
                if _khop_from_fvconfig(fvc if fvc.exists() else DEFAULT_FVCONFIG)[0] == held["khop"]:
                    arrays = held["arrays"]
            info = convert(run, case_dir, case_name=run.name, keep="connected_open", case=case, skip=_h5_skip(),
                           arrays=arrays)
            held = arrays = None
            timing["mesh"] = timing["meshRead"] + time.perf_counter() - t
            timing["meshParts"] = {k: round(float(v), 3) for k, v in info["timing_s"].items()}
            # a request without the key (a run not prepared by a caller) takes the servers' declared heat; a caller
            # that sends null says the hall has heat this number does not count (fixed-temperature objects), and
            # the energy leg of the stop rule is then the steadiness of the removed heat, not its closure
            if "declaredHeatW" not in request and smap.get("declared_it_heat_W"):
                request["declaredHeatW"] = float(smap["declared_it_heat_W"])
            req_used = work / "request.used.json"
            req_used.write_text(json.dumps(request, indent=1))
            foam_bridge.CASES.clear()
            foam_bridge.RESULTS.clear()
            foam_bridge.CASES[str(run)] = case
            foam_bridge.OUT = out
            out.write(f"Mesh   : {case.n_cells} cells ({timing['mesh']:.1f} s), boundary conditions "
                      f"({timing['boundaryConditions']:.1f} s)\n\nStarting time loop\n\n")
            out.flush()

            max_it = int(float(request.get("maxIterations") or 1500))
            solver_args = ["--dataset_dir", str(case_dir), "--exp-name", "foam-bridge", *RECIPE_ARGS,
                           "--max-newton", str(2 * max_it), "--foam-bridge", str(req_used), *extra]
            t = time.perf_counter()
            status["solverReturn"] = entry.main(solver_args)
            timing["solveTotal"] = time.perf_counter() - t
            res = foam_bridge.RESULTS.get(str(run))
            if res is None:
                raise RuntimeError("the solve ended without writing the final time directory")
            status.update(ok=True, result=res)
            status["gpu"] = dict(status.get("gpu") or {}, **(res.get("gpu") or {}))
            timing["write"] = res["writeSeconds"]
            _record_lazy_modules(loaded)
            if os.environ.get("LPCNS_DC_KEEP_CASE", "0") != "1":
                # the converted mesh (0.8 GB per million cells) served this solve only; the run directory keeps
                # the boundary conditions, the request, the log and the result
                for h5 in case_dir.glob("*.h5"):
                    h5.unlink(missing_ok=True)
        except BaseException as exc:                                      # noqa: BLE001 - reported to the caller
            traceback.print_exc()
            status["error"] = f"{type(exc).__name__}: {exc}"
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                status["interrupted"] = True
            elif _out_of_memory(exc):
                # the caller tells its user to give the solve a device with more free memory
                status["errorCode"] = OUT_OF_MEMORY
                with contextlib.suppress(Exception):
                    # freeMiB stays the reading taken before the solve; the device at the failure is added
                    gpu = dict(status.get("gpu") or {}, **(getattr(exc, "gpu", None) or {}))
                    if not isinstance(exc, DeviceMemoryError):
                        now = _device_memory()
                        gpu["freeAtFailureMiB"] = now.get("freeMiB")
                        for key in ("name", "totalMiB", "freeMiB"):
                            gpu.setdefault(key, now.get(key))
                    if status.get("cells"):
                        gpu.setdefault("estimateMiB", memory_estimate_mib(status["cells"]))
                    status["gpu"] = gpu
        finally:
            for _ek, _ev in _saved_env.items():
                if _ev is None:
                    os.environ.pop(_ek, None)
                else:
                    os.environ[_ek] = _ev
            _release()
    timing["total"] = time.perf_counter() - t0
    status["timing_s"] = timing
    (work / "result.json").write_text(json.dumps(status, indent=1, default=str))
    with contextlib.suppress(OSError, ValueError):
        if status["ok"]:
            out.write(f"\ngpuNewton: {status['result']['stopReason']}; {status['result']['iterations']} iterations, "
                      f"{timing['total']:.1f} s\n\nEnd\n\n")
        else:
            out.write(f"\n--> gpuNewton FATAL ERROR\n"
                      + ("GPU out of memory\n" if status.get("errorCode") == OUT_OF_MEMORY else "")
                      + f"{status.get('error')}\n(see {work / 'engine.log'})\n")
        out.flush()
    return status


def _release() -> None:
    """Drop the hand-over tables of a solve."""
    with contextlib.suppress(Exception):
        from Post_process import foam_bridge
        foam_bridge.CASES.clear()
        foam_bridge.OUT = None


# --------------------------------------------------------------------------------------------- service
def _device_tag() -> str:
    dev = os.environ.get("CUDA_VISIBLE_DEVICES", "").split(",")[0].strip()
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in dev) or "default"


def _service_dir() -> Path:
    return Path(os.environ.get("LPCNS_DC_SERVICE_DIR") or SRC.parent / ".work" / "dc_service")


@contextlib.contextmanager
def _short_path(path: Path):
    """A Unix socket address is limited to ~107 bytes: reach ``path`` through an open descriptor of its directory."""
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        yield f"/proc/self/fd/{fd}/{path.name}"
    finally:
        os.close(fd)


def _connect(path: Path, timeout: float = 2.0):
    if not path.exists():
        return None
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        with _short_path(path) as addr:
            s.connect(addr)
    except OSError:
        s.close()
        return None
    s.settimeout(None)
    return s


def serve(idle_timeout: float, listen_fd: int | None = None, wait_fd: int | None = None) -> int:
    """Stand by for one solve of this process's device (``listen_fd``/``wait_fd``: handed over by the predecessor,
    whose solve ends when ``wait_fd`` reaches end of file)."""
    sdir = _service_dir()
    sdir.mkdir(parents=True, exist_ok=True)
    path = sdir / f"{_device_tag()}.sock"
    info_path = path.with_suffix(".json")
    if listen_fd is None:
        if (live := _connect(path)) is not None:
            live.close()
            print(f"dc_foam_solve: a service already answers on {path}", flush=True)
            return 1
        path.unlink(missing_ok=True)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        with _short_path(path) as addr:
            server.bind(addr)
        server.listen(16)
        info = {"device": os.environ.get("CUDA_VISIBLE_DEVICES", ""), "socket": str(path),
                "started": time.strftime("%Y-%m-%d %H:%M:%S"), "solves": 0}
    else:
        server = socket.socket(fileno=listen_fd)
        info = json.loads(info_path.read_text()) if info_path.is_file() else {"solves": 0}

    def leave(why: str) -> None:
        print(f"dc_foam_solve[{os.getpid()}]: {why}, leaving", flush=True)
        server.close()
        path.unlink(missing_ok=True)
        info_path.unlink(missing_ok=True)

    try:
        seconds = _load_modules()
        _load_lazy_modules()
        import torch
        import warp as wp
        wp.init()
        if torch.cuda.is_available():
            torch.zeros(1, device="cuda")                               # the CUDA context
    except BaseException as exc:                                          # noqa: BLE001
        leave(f"could not load ({type(exc).__name__}: {exc})")
        return 1
    if wait_fd is not None:
        with os.fdopen(wait_fd, "rb") as pipe:
            pipe.read()                                                 # until the predecessor's solve has ended
    info.update(pid=os.getpid(), importSeconds=round(seconds, 2), ready=time.strftime("%Y-%m-%d %H:%M:%S"))
    info_path.write_text(json.dumps(info, indent=1))
    print(f"dc_foam_solve[{os.getpid()}]: standing by for {info.get('device') or 'the default device'} on {path} "
          f"(modules in {seconds:.1f} s, idle timeout {idle_timeout:g} s)", flush=True)
    while True:
        server.settimeout(idle_timeout if idle_timeout > 0 else None)
        try:
            conn, _ = server.accept()
        except socket.timeout:
            leave("idle timeout")
            return 0
        except KeyboardInterrupt:
            leave("interrupted")
            return 130
        stream = conn.makefile("rw", encoding="utf-8", newline="\n")
        try:
            job = json.loads(stream.readline() or "{}")
        except (OSError, ValueError) as exc:
            print(f"dc_foam_solve[{os.getpid()}]: job dropped: {type(exc).__name__}: {exc}", flush=True)
            conn.close()
            continue
        if job.get("command") in ("stop", "ping"):
            with contextlib.suppress(OSError):
                stream.write(STATUS_MARK + json.dumps({"ok": True, **info}) + "\n")
                stream.flush()
            conn.close()
            if job["command"] == "stop":
                leave("stop requested")
                return 0
            continue
        if job.get("command") == "prepare":
            # the mesh of the solve that follows: read now, while the caller prepares its run directory (a solve
            # request that arrives meanwhile waits in the listen queue)
            with contextlib.suppress(OSError):
                stream.write(STATUS_MARK + json.dumps({"ok": True, "pid": os.getpid()}) + "\n")
                stream.flush()
            conn.close()
            try:
                held = prepare_mesh(Path(job["mesh"]), str(job["key"]))
                info["prepared"] = held["key"]
                print(f"dc_foam_solve[{os.getpid()}]: mesh {held['key'][:16]} of {job['mesh']} read ahead "
                      f"({held['seconds']:.1f} s, {held['case'].n_cells} cells)", flush=True)
            except Exception as exc:                                      # noqa: BLE001 - a hint only
                PREPARED.clear()
                info.pop("prepared", None)
                print(f"dc_foam_solve[{os.getpid()}]: mesh of {job.get('mesh')} not read ahead: "
                      f"{type(exc).__name__}: {exc}", flush=True)
            info_path.write_text(json.dumps(info, indent=1))
            continue
        if "run" not in job:
            conn.close()
            continue
        break
    # this process takes the solve; its successor loads meanwhile and accepts once this process has ended
    t0 = time.perf_counter()
    import fcntl
    import subprocess
    busy = os.open(path.with_suffix(".lock"), os.O_RDWR | os.O_CREAT, 0o644)   # held until this process ends
    fcntl.flock(busy, fcntl.LOCK_EX)
    back, hold = os.pipe()
    os.set_inheritable(hold, False)
    info["solves"] = int(info.get("solves", 0)) + 1
    info.pop("prepared", None)                                           # the successor holds no mesh
    info_path.write_text(json.dumps(info, indent=1))
    try:
        subprocess.Popen([sys.executable, "-u", str(Path(__file__).resolve()), "--serve", "--idle-timeout",
                          str(idle_timeout), "--listen-fd", str(server.fileno()), "--wait-fd", str(back)],
                         pass_fds=(server.fileno(), back), stdin=subprocess.DEVNULL, start_new_session=True,
                         env=_service_env())
    except OSError as exc:
        print(f"dc_foam_solve[{os.getpid()}]: no successor ({exc}); the service ends with this solve", flush=True)
        path.unlink(missing_ok=True)
        info_path.unlink(missing_ok=True)
    os.close(back)
    server.close()
    status = solve_run(Path(job["run"]), job.get("request"), stream, job.get("extra") or (), t0=t0,
                       timing={"service": True, "imports": 0.0},
                       caller_tbox=job["callerTbox"] if "callerTbox" in job else _NO_CALLER)
    with contextlib.suppress(OSError, ValueError):
        stream.write(STATUS_MARK + json.dumps(status, default=str) + "\n")
        stream.flush()
    print(f"dc_foam_solve[{os.getpid()}]: {job['run']}: {'ok' if status['ok'] else status.get('error')} "
          f"({status['timing_s']['total']:.1f} s)", flush=True)
    with contextlib.suppress(OSError):
        conn.close()
    return 0 if status["ok"] else 1


def _solving(path: Path) -> bool:
    """Whether the service of socket ``path`` is in a solve now (its process holds the lock)."""
    import fcntl
    try:
        fd = os.open(path.with_suffix(".lock"), os.O_RDONLY)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        return False
    except OSError:
        return True
    finally:
        os.close(fd)


def _device_table() -> list:
    """``(index, uuid, used MiB, total MiB, load %)`` of the devices this process may use (``nvidia-smi``, limited
    to ``LPCNS_DC_DEVICES`` when that is set)."""
    import subprocess
    try:
        rows = subprocess.run(["nvidia-smi", "--query-gpu=index,uuid,memory.used,memory.total,utilization.gpu",
                               "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=30,
                              check=True).stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        return []
    allowed = {d.strip() for d in os.environ.get("LPCNS_DC_DEVICES", "").split(",") if d.strip()}
    table = []
    for row in rows:
        parts = [v.strip() for v in row.split(",")]
        try:
            entry = (parts[0], parts[1], float(parts[2]), float(parts[3]), float(parts[4]))
        except (IndexError, ValueError):
            continue
        if not allowed or entry[0] in allowed or entry[1] in allowed:
            table.append(entry)
    return table


def _idle_device() -> str | None:
    """UUID of the idle device with the most free memory; None when there is none.  Among devices the caller named
    (``LPCNS_DC_DEVICES``) the one with the most free memory is taken when none is idle."""
    table = _device_table()
    idle = [e for e in table if e[2] <= 1024 and e[4] <= 10]
    if not idle and os.environ.get("LPCNS_DC_DEVICES", "").strip():
        idle = table
    return max(idle, key=lambda e: e[3] - e[2])[1] if idle else None


def _services(key: str | None = None) -> list:
    """The service sockets a client of this process may use: its device's (``CUDA_VISIBLE_DEVICES`` set), else all
    of them -- those that hold the mesh ``key`` first, then the idle ones, then those in a solve."""
    sdir = _service_dir()
    if os.environ.get("CUDA_VISIBLE_DEVICES"):
        return [sdir / f"{_device_tag()}.sock"]
    candidates = sorted(sdir.glob("*.sock")) if sdir.is_dir() else []
    if candidates and os.environ.get("LPCNS_DC_DEVICES", "").strip():
        mine = {name for e in _device_table() for name in e[:2]}
        candidates = [path for path in candidates if path.stem in mine]

    def holds(path: Path) -> bool:
        try:
            return bool(key) and json.loads(path.with_suffix(".json").read_text()).get("prepared") == key
        except (OSError, ValueError):
            return False
    candidates.sort(key=lambda path: (not holds(path), _solving(path)))   # stable
    return candidates


def _service_env(**extra) -> dict:
    """The environment a service process starts with: this process's, with the temperature box as the CALLER gave
    it.  The recipe's default this process filled in, or the box it derived for its last hall, is not the caller's:
    a service that inherited either took it for an explicit box and kept it for every hall it solved."""
    env = dict(os.environ, **extra)
    if CALLER_TBOX is None:
        env.pop("LPCNS_ODIL_TBOX", None)
    else:
        env["LPCNS_ODIL_TBOX"] = CALLER_TBOX
    return env


def _start_service(idle_timeout: float) -> Path | None:
    """Start a service for a device this process may use (detached; its lines go to ``service_<device>.log`` in
    the service directory); returns its socket once it is bound, None when no device is free or it did not come
    up.  The socket is bound before the modules load, so requests sent meanwhile wait in its queue."""
    import subprocess
    device = os.environ.get("CUDA_VISIBLE_DEVICES", "").split(",")[0].strip() or _idle_device()
    if not device:
        return None
    env = _service_env(CUDA_VISIBLE_DEVICES=device)
    sdir = _service_dir()
    sdir.mkdir(parents=True, exist_ok=True)
    tag = "".join(c if c.isalnum() or c in "-_" else "_" for c in device)
    path = sdir / f"{tag}.sock"
    with open(sdir / f"service_{tag}.log", "ab") as log:
        proc = subprocess.Popen([sys.executable, "-u", str(Path(__file__).resolve()), "--serve", "--idle-timeout",
                                 str(idle_timeout)], env=env, stdin=subprocess.DEVNULL, stdout=log,
                                stderr=subprocess.STDOUT, start_new_session=True)
    deadline = time.monotonic() + 60.0
    while time.monotonic() < deadline and proc.poll() is None:
        if path.exists():
            return path
        time.sleep(0.05)
    return None


def announce_mesh(mesh_dir: Path, key: str, start_service: float | None = None) -> int:
    """Tell a standing service (the first idle one) to read ``mesh_dir`` ahead of the solve that names ``key``.
    Without a service nothing happens, unless ``start_service`` (idle seconds) asks for one to be started."""
    candidates = [path for path in _services() if not _solving(path)]
    for attempt in range(2):
        for path in candidates:
            if (conn := _connect(path)) is None:
                continue
            with conn, conn.makefile("rw", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps({"command": "prepare", "mesh": str(Path(mesh_dir).resolve()),
                                         "key": key}) + "\n")
                stream.flush()
                if attempt == 0:
                    stream.readline()                                   # (a service just started answers once loaded)
            print(f"dc_foam_solve: mesh announced to {path.name}")
            return 0
        started = _start_service(start_service) if attempt == 0 and start_service is not None else None
        if started is None:
            break
        candidates = [started]
    print("dc_foam_solve: no idle service; the solve will read its mesh itself")
    return 0


def _through_service(run: Path, request: dict | None, extra) -> int | None:
    """Hand the solve to the service of this device (without ``CUDA_VISIBLE_DEVICES``: to the service that holds
    this solve's mesh, else one that is not in a solve, else the first that answers); None when none answers."""
    key = (request or {}).get("meshSha256")
    if key is None and request is None:
        with contextlib.suppress(OSError, ValueError):
            key = json.loads((run / "gpuNewton" / "request.json").read_text()).get("meshSha256")
    candidates = _services(key)
    for path in candidates:
        conn = _connect(path)
        if conn is None:
            continue
        with conn, conn.makefile("rw", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps({"run": str(run), "request": request, "extra": list(extra),
                                     "callerTbox": CALLER_TBOX}) + "\n")
            stream.flush()
            status = None
            for line in stream:
                if line.startswith(STATUS_MARK):
                    status = json.loads(line[1:])
                    break
                sys.stdout.write(line)
                sys.stdout.flush()
            if status is None:
                sys.stdout.write("\n--> gpuNewton FATAL ERROR\nthe solver service closed the connection\n")
                return 1
            return 0 if status.get("ok") else 1
    return None


def main(argv=None) -> int:
    t0 = time.perf_counter()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--run", default=None, help="the prepared run directory (processorN/, system/, constant/)")
    ap.add_argument("--request", default=None, help="the caller's stop rule (JSON); default <run>/gpuNewton/request.json")
    ap.add_argument("--work", default=None, help="working directory (default <run>/gpuNewton; in-process solves only)")
    ap.add_argument("--serve", action="store_true", help="serve solves of this process's device (see the module text)")
    ap.add_argument("--idle-timeout", type=float, default=3600.0, help="--serve: leave after this many idle seconds "
                                                                       "(0: never)")
    ap.add_argument("--listen-fd", type=int, default=None, help=argparse.SUPPRESS)      # --serve, from a predecessor
    ap.add_argument("--wait-fd", type=int, default=None, help=argparse.SUPPRESS)
    ap.add_argument("--stop-service", action="store_true", help="ask the service of this device to leave")
    ap.add_argument("--no-service", action="store_true", help="solve in this process even when a service answers")
    ap.add_argument("--prepare", default=None, metavar="MESH_DIR",
                    help="announce the mesh of the solve that follows to a standing service (with --key)")
    ap.add_argument("--key", default=None, help="--prepare: the caller's identity of that mesh (the request's "
                                                "meshSha256)")
    ap.add_argument("--start-service", type=float, default=None, metavar="SECONDS",
                    help="--prepare: when no service stands by, start one that leaves after this many idle seconds")
    ap.add_argument("--phys-dt", type=float, default=None, help="physical BDF1 time step dt [s] (>0 enables transient dual-time stepping)")
    ap.add_argument("--phys-old", default=None, help="path to previous physical-step state .pt for --phys-dt")
    ap.add_argument("--phys-tol", type=float, default=None, help="absolute floor for physical-step convergence")
    ap.add_argument("--phys-rtol", type=float, default=None, help="relative merit drop for physical-step convergence")
    ap.add_argument("--phys-t-mult", type=float, default=None, help="effective thermal capacitance multiplier on energy rows")
    ap.add_argument("--phys-steps", type=int, default=None, help="number of physical time steps to march")
    ap.add_argument("--phys-save", default=None, help="prefix to save step states <prefix>_j.pt")
    ap.add_argument("--extra", nargs=argparse.REMAINDER, default=[], help="further solver flags (after the recipe)")
    args = ap.parse_args(argv)
    if args.serve:
        return serve(args.idle_timeout, args.listen_fd, args.wait_fd)
    if args.prepare:
        if not args.key:
            ap.error("--prepare needs --key")
        return announce_mesh(Path(args.prepare), args.key, args.start_service)
    if args.stop_service:
        conn = _connect(_service_dir() / f"{_device_tag()}.sock")
        if conn is None:
            print("dc_foam_solve: no service answers for this device")
            return 1
        with conn, conn.makefile("rw", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps({"command": "stop"}) + "\n")
            stream.flush()
            stream.readline()
        return 0
    if not args.run:
        ap.error("--run is required")
    run = Path(args.run).resolve()
    request = json.loads(Path(args.request).read_text()) if args.request else None
    if any(v is not None for v in (args.phys_dt, args.phys_old, args.phys_tol, args.phys_rtol,
                                   args.phys_t_mult, args.phys_steps, args.phys_save)):
        if request is None:
            req_p = run / "gpuNewton" / "request.json"
            request = json.loads(req_p.read_text()) if req_p.is_file() else {}
        else:
            request = dict(request)
        if args.phys_dt is not None:
            request["physDt"] = float(args.phys_dt)
        if args.phys_old is not None:
            request["physOldState"] = str(args.phys_old)
        if args.phys_tol is not None:
            request["physTol"] = float(args.phys_tol)
        if args.phys_rtol is not None:
            request["physRtol"] = float(args.phys_rtol)
        if args.phys_t_mult is not None:
            request["physTMult"] = float(args.phys_t_mult)
        if args.phys_steps is not None:
            request["physSteps"] = int(args.phys_steps)
        if args.phys_save is not None:
            request["physSavePrefix"] = str(args.phys_save)
    if not args.no_service and not args.work:
        code = _through_service(run, request, args.extra)
        if code is not None:
            return code
    if not os.environ.get("CUDA_VISIBLE_DEVICES"):
        device = _idle_device()
        if device is None:
            print("\n--> gpuNewton FATAL ERROR\nno idle CUDA device (set CUDA_VISIBLE_DEVICES to the device this "
                  "solve may use)", flush=True)
            return 1
        os.environ["CUDA_VISIBLE_DEVICES"] = device
    out = os.fdopen(os.dup(1), "w", buffering=1)
    status = solve_run(run, request, out, args.extra, work=args.work, t0=t0)
    out.flush()
    return 0 if status["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
