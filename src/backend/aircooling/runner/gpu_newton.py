"""The GPU Newton-Krylov air solver as a second solver engine (opt-in; OpenFOAM stays the default).

``run_cfd --solver-engine gpuNewton`` (server request key ``solverEngine``, scripted API ``solver_engine``) solves
the prepared steady case with the GPU solver of ``src/backend/aircooling/gpu_solver`` instead of ``mpirun ...
buoyantBoussinesqSimpleFoam_new``. Everything around the solve is unchanged: the run directory, its dictionaries and
boundary conditions are generated as for OpenFOAM, and the solver writes into that directory what the OpenFOAM
solver writes -- its log lines (relayed into the run log), the ``postProcessing`` monitors of the ACU patches and
the probes, and the final time directory of every partition with explicit boundary values -- so the execution
receipt, the sensor harvest, the convergence verdict (``convergence.assess_run``) and the field metrics read it as
they read an OpenFOAM result.

The engine is offered only by a server started with ``start_server.sh --gpu`` (which sets ``DCDTUNI_GPU_SOLVER=1``
and, for a list of devices, ``DCDTUNI_GPU_SOLVER_GPU``); :func:`require_enabled` refuses it otherwise. The solver
runs in a process of its own, on one GPU, always with this process's interpreter: the machine's one ``dcdtuni``
conda environment (``setup_env.sh``; torch for CUDA 12.8, which runs on every card the project uses).

The level of the mesh chooses the model: an L0 mesh (``meshConfig.coarsenLevel = "L0"``, dcCart coarsening) is
solved by the zonal pressure-enthalpy network of that mesh (``entries/dc_zonal_solve.py``: its own residual and
Newton certificate in ``<run>/gpuNewton/zonal/result.json``, the fields ``T``, ``p_rgh``, ``U`` at time 1), every
other mesh by the buoyant Boussinesq RANS Newton-Krylov solver (``entries/dc_foam_solve.py``):

``DCDTUNI_GPU_SOLVER_GPU``      the CUDA devices the solver may use (indices or ``GPU-<uuid>``, comma separated).
                                One device: the solve runs there. Several: on the idle one with the most free
                                memory (the one with the most free memory when none is idle). Unset: the solver's
                                own choice among all devices (a device whose solver service stands by, else the
                                idle device with the most free memory; none idle is an error)
``DCDTUNI_GPU_SOLVER_THREADS``  CPU threads of the solver process (default 4)
``DCDTUNI_GPU_SOLVER_STANDBY``  seconds a solver service stands by after its last solve (default 600; 0: no service
                                is started, every solve loads the solver afresh)
``DCDTUNI_GPU_SOLVER_ROOT``     another solver checkout (holds ``src/entries/dc_foam_solve.py``) in place of the
                                vendored copy -- for solver development (run by the same interpreter)

A solver service (``dc_foam_solve.py --serve``, one per device) keeps a loaded solver process standing by; the
command below hands the solve to it when one answers and solves in its own process otherwise. :func:`prewarm` tells
that service which mesh comes next as soon as the runner has bound the solve to it -- and starts the service when
none stands by -- so the solver loads and reads the mesh while the runner prepares the run directory; the request
names the same mesh identity (``meshSha256``, this project's ``mesh_identity_sha256``) and the solver uses what it
read only when the two agree. The service's sockets and log are in ``.codex-tmp/gpu-solver`` of this repository.

Scope: steady solves of a partitioned (dcCart) mesh. The stop rule is this project's own: the request the runner
writes (``<run>/gpuNewton/request.json``) carries ``stopToleranceK``, the energy tolerance, the window and the
declared heat, the solver applies them to the monitors it writes and also honours ``stopAt writeNow`` from the
steady-stop watcher. The verdict of record stays ``assess_run`` on the files in the run directory.
"""

from __future__ import annotations

import ast
import fcntl
import functools
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

OPENFOAM = "openfoam"
GPU_NEWTON = "gpuNewton"
ENGINES = (OPENFOAM, GPU_NEWTON)
WORK_DIR = "gpuNewton"
ENABLE_ENV = "DCDTUNI_GPU_SOLVER"                      # "1": this launch offers the engine (start_server.sh --gpu)
# the vendored solver (tools/vendor_gpu_solver.py; its source commit is in VENDORED.json) and its entry
VENDORED = Path(__file__).resolve().parents[1] / "gpu_solver"
ENTRY = Path("entries") / "dc_foam_solve.py"
ZONAL_ENTRY = Path("entries") / "dc_zonal_solve.py"        # the L0 level: the zonal network of the mesh
CHECKOUT_ENTRY = Path("src") / ENTRY                    # the same entry in a solver checkout
# what the solver imports beyond this project's core install (the `gpu-solver` extra; start_server.sh checks them)
REQUIRED_MODULES = ("torch", "warp", "pyamg", "torch_geometric", "h5py", "scipy")
DEFAULT_STANDBY_SECONDS = 600.0
# GPU memory of one solve: the solver's own model (gpu_solver/entries/dc_foam_solve.py, MEMORY_MODEL -- a fixed part
# and a part per mesh cell, measured on this project's halls; docs/reference/scripted-api.md, "Solver engine"). It
# is read from the vendored source, so the page's estimate and the solver's refusal cannot drift apart.
OUT_OF_MEMORY = "gpu_out_of_memory"                     # errorCode of <run>/gpuNewton/result.json


def resolve_engine(value: Any) -> str:
    """``None``/empty -> ``openfoam``; anything else must be one of :data:`ENGINES`."""
    if value is None or value == "":
        return OPENFOAM
    if value not in ENGINES:
        raise ValueError(f"solver engine must be one of {ENGINES}, got {value!r}")
    return value


def _devices(text: Optional[str]) -> List[str]:
    return [part.strip() for part in (text or "").split(",") if part.strip()]


def launch_status(env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Whether this launch offers the engine and on which devices (``/api/server-info`` -> ``gpuSolver``)."""
    env = os.environ if env is None else env
    enabled = env.get(ENABLE_ENV) == "1"
    return {"enabled": enabled, "devices": (_devices(env.get("DCDTUNI_GPU_SOLVER_GPU")) or None) if enabled else None}


def require_enabled(env: Optional[Dict[str, str]] = None) -> None:
    """The engine is offered only by a server started with ``start_server.sh --gpu``."""
    if not launch_status(env)["enabled"]:
        raise ValueError("solverEngine gpuNewton is not enabled on this server: start it with "
                         "start_server.sh --gpu (all GPUs) or --gpu <list>")


class GpuMemoryError(RuntimeError):
    """The GPU solver ended for lack of memory on its card."""


_CELLS = re.compile(r"nCells:\s*(\d+)")


def mesh_cells(mesh_case: Path) -> Optional[int]:
    """Cells of the mesh in ``mesh_case`` (``<case>/Mesh``), summed over its partitions, from the note in each
    ``owner`` header; None without a mesh or a note."""
    from backend.aircooling.runner.mesh_layout import mesh_directories
    total = 0
    try:
        directories = mesh_directories(Path(mesh_case))
        for directory in directories:
            with open(directory / "owner", "rb") as handle:
                found = _CELLS.search(handle.read(4096).decode("latin-1"))
            if not found:
                return None
            total += int(found.group(1))
    except OSError:
        return None
    return total or None


@functools.lru_cache(maxsize=None)
def memory_model() -> Dict[str, float]:
    """``MEMORY_MODEL`` of the vendored solver entry (a literal there; read without importing the solver)."""
    tree = ast.parse((VENDORED / ENTRY).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "MEMORY_MODEL" for t in node.targets):
            return dict(ast.literal_eval(node.value))
    raise RuntimeError(f"{VENDORED / ENTRY} defines no MEMORY_MODEL")


def estimate_memory_mib(cells: int) -> int:
    """GPU memory one solve of a mesh with ``cells`` cells needs, in MiB (the solver's model: a fixed part and a
    part per cell, the latter larger up to ``smallUpToCells`` cells)."""
    model = memory_model()
    per_million = model["smallPerMillionMiB" if cells <= model["smallUpToCells"] else "perMillionMiB"]
    return int(round(model["baseMiB"] + per_million * cells / 1e6))


def device_readings(env: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    """What the solve page shows live: every card this launch may use (``DCDTUNI_GPU_SOLVER_GPU``, else all) with
    its memory, its utilisation and what the solver does on it -- ``"solving"``, ``"standby"`` (a loaded solver
    process waits there) or None.  ``nvidia-smi`` now; an empty list when it cannot be asked."""
    env = os.environ if env is None else env
    allowed = set(_devices(env.get("DCDTUNI_GPU_SOLVER_GPU")))
    try:
        rows = subprocess.run(["nvidia-smi", "--query-gpu=index,uuid,name,memory.total,memory.used,utilization.gpu",
                               "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10,
                              check=True).stdout.splitlines()
        config = configuration(env)
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    service_dir = config["service_dir"]
    found = []
    for row in rows:
        parts = [part.strip() for part in row.split(",")]
        if len(parts) != 6 or (allowed and parts[0] not in allowed and parts[1] not in allowed):
            continue
        try:
            total, used = int(parts[3]), int(parts[4])
        except ValueError:
            continue
        found.append({"index": parts[0], "uuid": parts[1], "name": parts[2], "totalMiB": total, "usedMiB": used,
                      "freeMiB": total - used, "utilization": int(parts[5]) if parts[5].isdigit() else None,
                      "solver": _service_state(service_dir, parts[1]) if service_dir is not None else None})
    return found


def _service_state(service_dir: Path, uuid: str) -> Optional[str]:
    """``"solving"`` while the solver process of that card holds its lock, ``"standby"`` while one listens."""
    try:
        handle = os.open(service_dir / f"{uuid}.lock", os.O_RDONLY)
    except OSError:
        handle = None
    if handle is not None:
        try:
            fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except OSError:
            return "solving"
        finally:
            os.close(handle)
    return "standby" if (service_dir / f"{uuid}.sock").exists() else None


def memory_status(cells: Optional[int], env: Optional[Dict[str, str]] = None,
                  readings: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Whether a mesh of ``cells`` cells fits a card of this launch: ``{"cells", "estimateMiB", "bestFreeMiB",
    "fits"}`` (``fits`` None when the mesh size or the cards are unknown).  An upper estimate: ``cells`` is the
    whole mesh, a solve takes its open connected region (0.75 of the cells in the in-row halls measured), and the
    solver itself decides with the exact count before its time loop."""
    readings = device_readings(env) if readings is None else readings
    estimate = estimate_memory_mib(cells) if cells else None
    best = max((row["freeMiB"] for row in readings), default=None)
    return {"cells": cells, "estimateMiB": estimate, "bestFreeMiB": best,
            "fits": None if estimate is None or best is None else bool(best >= estimate)}


def memory_message(status: Dict[str, Any], *, failed: bool = False) -> str:
    """What a user reads when the memory is short: before a solve (``status`` of ``memory_status``, an upper
    estimate) or, ``failed``, after the solver ended for lack of memory on its card."""
    size = f"{status['estimateMiB'] / 1024:.1f} GiB" if status.get("estimateMiB") else None
    cells = f" ({status['cells'] / 1e6:.2f} million cells)" if status.get("cells") else ""
    free = f"{status['bestFreeMiB'] / 1024:.1f} GiB" if status.get("bestFreeMiB") is not None else None
    if failed:
        text = ("GPU 显存不足，求解已终止 / out of GPU memory, the solve was stopped: this mesh" + cells
                + (f" needs about {size}" if size else " needs more GPU memory")
                + (f", the card of this solve had {free} free. " if free else ". "))
    else:
        text = ("GPU 显存可能不足 / GPU memory may be short: this mesh" + cells
                + (f" needs up to about {size}" if size else " needs more GPU memory")
                + (f", the card with the most free memory has {free} free. " if free else ". "))
    return text + ("请重新启动 start_server.sh，并用 --gpu 指定更多或空闲显存更大的显卡 "
                   "(restart start_server.sh and name more cards, or cards with more free memory, with --gpu)；"
                   "或改用 OpenFOAM 求解引擎 (or solve with the OpenFOAM engine).")


def failure_reason(run_dir: Path) -> Optional[str]:
    """After a failed solve: the user-facing reason when the solver reported that it ran out of GPU memory
    (``errorCode`` of ``<run>/gpuNewton/result.json``), else None."""
    try:
        result = json.loads((Path(run_dir) / WORK_DIR / "result.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(result, dict) or result.get("errorCode") != OUT_OF_MEMORY:
        return None
    gpu = result.get("gpu") if isinstance(result.get("gpu"), dict) else {}
    cells = result.get("cells")
    return memory_message({"cells": cells, "estimateMiB": gpu.get("estimateMiB") or
                           (estimate_memory_mib(cells) if cells else None), "bestFreeMiB": gpu.get("freeMiB")},
                          failed=True)


def _number(env: Dict[str, str], name: str, default: str, *, integer: bool, minimum: float) -> float:
    text = env.get(name, default)
    try:
        value = int(text) if integer else float(text)
    except ValueError:
        raise ValueError(f"{name} must be {'an integer' if integer else 'a number'}, got {text!r}") from None
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum:g}, got {text}")
    return value


def configuration(env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Where the solver is and how to start it; raises with what is missing."""
    env = os.environ if env is None else env
    root_text = env.get("DCDTUNI_GPU_SOLVER_ROOT")
    vendored = not root_text
    root = VENDORED if vendored else Path(root_text).expanduser()
    entry = root / (ENTRY if vendored else CHECKOUT_ENTRY)
    if not entry.is_file():
        raise FileNotFoundError(f"the gpuNewton solver is missing: {root} has no {entry.relative_to(root)}"
                                + ("" if vendored else " (DCDTUNI_GPU_SOLVER_ROOT)"))
    python = Path(sys.executable)                       # one environment per machine: the one running this
    return {"root": root, "entry": entry, "python": python, "vendored": vendored,
            "threads": int(_number(env, "DCDTUNI_GPU_SOLVER_THREADS", "4", integer=True, minimum=1)),
            "standby": _number(env, "DCDTUNI_GPU_SOLVER_STANDBY", str(DEFAULT_STANDBY_SECONDS), integer=False,
                               minimum=0),
            "devices": _devices(env.get("DCDTUNI_GPU_SOLVER_GPU")),
            # the vendored solver keeps nothing in the source tree: sockets and service log go to the runtime area
            "service_dir": Path(__file__).resolve().parents[4] / ".codex-tmp" / "gpu-solver" if vendored else None}


def identity(env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """What solved: the vendored copy's source commit (``VENDORED.json``) or, for another checkout, its commit and
    whether its tree is modified; and the interpreter."""
    config = configuration(env)
    found: Dict[str, Any] = {"engine": GPU_NEWTON, "root": str(config["root"]), "python": str(config["python"])}
    if config["vendored"]:
        try:
            source = json.loads((config["root"] / "VENDORED.json").read_text(encoding="utf-8")).get("source") or {}
        except (OSError, ValueError):
            source = {}
        found.update(vendored=True, commit=source.get("commit"), modified=source.get("modified"))
        return found

    def git(*args: str) -> Optional[str]:
        try:
            return subprocess.run(["git", "-C", str(config["root"]), *args], capture_output=True, text=True,
                                  timeout=30, check=True).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None
    status = git("status", "--porcelain", "--untracked-files=no")
    found.update(vendored=False, commit=git("rev-parse", "HEAD"), modified=None if status is None else bool(status))
    return found


def build_request(manifest: Dict[str, Any], *, stop_tolerance_k: float,
                  mesh_sha256: Optional[str] = None) -> Dict[str, Any]:
    """The stop rule the solver applies, from the manifest this solve executes."""
    from backend.aircooling.runner.convergence import (
        DEFAULT_ENERGY_TOLERANCE, MIN_WINDOW_SAMPLES, declared_air_heat,
    )
    from backend.aircooling.utils.const import air_specific_heat, rho_air
    config = (manifest.get("simulationConfig") if manifest.get("schemaVersion") == 4
              else (manifest.get("meshGeometry") or {}).get("simulationConfig")) or {}
    if (config.get("solverType") or "steady") != "steady":
        raise ValueError("the gpuNewton solver engine solves steady cases only")
    tolerance = config.get("energyBalanceTolerance", DEFAULT_ENERGY_TOLERANCE)
    supply, returns = [], []
    for binding in (manifest.get("boundaryBindings") or {}).values():
        if isinstance(binding, dict) and binding.get("bcType") in ("acu_supply", "acu_return"):
            (supply if binding["bcType"] == "acu_supply" else returns).append(binding["patchName"])
    if not returns:
        raise ValueError("the gpuNewton solver engine needs at least one ACU return patch to judge convergence on")
    declared, uncounted = declared_air_heat(manifest)
    request: Dict[str, Any] = {
        "format": "dcdtuni.gpu-newton-request/v1",
        "stopToleranceK": float(stop_tolerance_k),
        "energyTolerance": float(tolerance),
        "window": int(MIN_WINDOW_SAMPLES),
        # None: the declared heat is incomplete, the energy leg is then the steadiness of the ACU heat
        "declaredHeatW": float(declared) if declared > 0 and not uncounted else None,
        "returnPatches": sorted(returns),
        "supplyPatches": sorted(supply),
        "rhoCp": float(rho_air * air_specific_heat),
        "selfStop": True,
    }
    if mesh_sha256:
        request["meshSha256"] = str(mesh_sha256)
    end_time = config.get("endTime")
    if isinstance(end_time, (int, float)) and not isinstance(end_time, bool) and end_time > 0:
        request["maxIterations"] = int(end_time)
    return request


def is_zonal(manifest: Dict[str, Any]) -> bool:
    """The manifest's mesh is the L0 level (``meshConfig.coarsenLevel = "L0"``): the zonal network solves it."""
    return str((manifest.get("meshConfig") or {}).get("coarsenLevel") or "").strip().upper() == "L0"


def _launch_prefix(config: Dict[str, Any], entry: Optional[Path] = None) -> list:
    """The solver's own environment: none of this process's Python or OpenFOAM library paths reach it, and no
    package of the user's site directory shadows the environment's (a CPU-only torch there would)."""
    unset = ["LD_LIBRARY_PATH", "PYTHONPATH", "PYTHONHOME"]
    assign = []
    devices = config["devices"]
    if len(devices) == 1:
        assign.append(f"CUDA_VISIBLE_DEVICES={devices[0]}")
        unset.append("LPCNS_DC_DEVICES")
    else:
        unset.append("CUDA_VISIBLE_DEVICES")
        if devices:
            assign.append(f"LPCNS_DC_DEVICES={','.join(devices)}")
        else:
            unset.append("LPCNS_DC_DEVICES")
    if config["service_dir"] is not None:
        assign.append(f"LPCNS_DC_SERVICE_DIR={config['service_dir']}")
    assign += [f"OMP_NUM_THREADS={config['threads']}", f"MKL_NUM_THREADS={config['threads']}",
               "PYTHONUNBUFFERED=1", "PYTHONNOUSERSITE=1"]
    return ["env", *(part for name in unset for part in ("-u", name)), *assign,
            str(config["python"]), "-u", str(entry or config["entry"])]


def prewarm(mesh_dir: Path, mesh_sha256: str, env: Optional[Dict[str, str]] = None) -> bool:
    """Hand a solver service the mesh of the solve that follows (``mesh_dir`` with the identity ``mesh_sha256``);
    returns at once.  When no service stands by, one is started (unless ``DCDTUNI_GPU_SOLVER_STANDBY=0``) and loads
    while the runner prepares the run directory.  A solve never depends on this: the solver reads the run
    directory's mesh itself whenever the identities differ or nothing was read."""
    config = configuration(env)
    if not mesh_sha256:
        return False
    standby = ["--start-service", f"{config['standby']:g}"] if config["standby"] > 0 else []
    subprocess.Popen([*_launch_prefix(config), "--prepare", str(mesh_dir), "--key", str(mesh_sha256), *standby],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    return True


def prepare(run_dir: Path, manifest: Dict[str, Any], *, stop_tolerance_k: float,
            mesh_sha256: Optional[str] = None, env: Optional[Dict[str, str]] = None) -> str:
    """Write ``<run>/gpuNewton/request.json`` and return the shell command that solves ``run_dir`` in place (an L0
    mesh: the zonal network, :func:`is_zonal`)."""
    config = configuration(env)
    request = build_request(manifest, stop_tolerance_k=stop_tolerance_k, mesh_sha256=mesh_sha256)
    work = Path(run_dir) / WORK_DIR
    work.mkdir(exist_ok=True)
    (work / "request.json").write_text(json.dumps(request, indent=1) + "\n", encoding="utf-8")
    entry = None
    if is_zonal(manifest):
        entry = config["root"] / (ZONAL_ENTRY if config["vendored"] else Path("src") / ZONAL_ENTRY)
        if not entry.is_file():
            raise FileNotFoundError(f"the L0 zonal solver is missing: {entry}")
    return " ".join(shlex.quote(part) for part in [*_launch_prefix(config, entry), "--run", "."])


def zonal_verdict(run_dir: Path) -> Optional[Dict[str, Any]]:
    """The verdict of an L0 run: the zonal network's own Newton certificate (``gpuNewton/zonal/result.json``);
    None for any other run."""
    path = Path(run_dir) / WORK_DIR / "zonal" / "result.json"
    if not path.is_file():
        return None
    res = json.loads(path.read_text(encoding="utf-8"))
    cert = res.get("certificate") or {}
    return {"verdict": "CONVERGED" if res.get("ok") else "NOT_CONVERGED",
            "rule": "L0 zonal network: " + str(res.get("rule", "Newton on the network's residual")),
            "model": "zonal pressure-enthalpy network (L0)", "result": str(path),
            "certificate": cert, "rtol": res.get("rtol"), "network": res.get("network")}
