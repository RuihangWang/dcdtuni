# Data-centre design evaluator

Takes a data-centre project file (`.cfdproj`), runs the whole simulation chain without a browser or a server,
and returns structured physical metrics:

```
.cfdproj ──▶ headless compiler ──▶ .cfdrun + STL ──▶ cut-cell mesh ──▶ air solve ──▶ result.json
         │                                                             ├─ gpuNewton: Newton-Krylov on one GPU
         │                                                             └─ openfoam:  buoyant Boussinesq (reference)
         └─▶ .liquidrun ──▶ liquid-cooling DAE (PyTorch, steady / transient, implicit adjoint gradients)
```

Both air solvers read the same decomposed mesh and the same OpenFOAM-format boundary conditions and are judged
by the same convergence rule (return-air temperatures steady and the cooling units removing the declared heat),
so a design can be scored with either and the two can be compared case by case.

Everything runs in the calling Python process and in child processes it starts and stops itself. There is no
HTTP server, no port, no login and no launcher script.

## What is in this folder

| Part | Paths | Maintained |
|---|---|---|
| Generated core | everything listed in `EXPORT.json` (`paths`) | exported from the modelling tool; never edited here. A new export replaces these files and removes the ones it no longer ships. `python -m dceval check` reports a file that differs from the export |
| Front door | `src/dceval/` (`evaluate`, the command line) and `src/backend/api/direct_session.py` (the in-process run session); part of the generated core | as above |
| GPU air solver | `src/backend/aircooling/gpu_solver/` (part of the generated core; `VENDORED.json` records its source revision and file hashes) | as above |
| Design modules | `src/backend/dcdesign/` (design language, families, compiler, L0 and HF scoring), `src/backend/plant/`, `src/backend/aircooling/zonal.py` and their tests; part of the generated core | developed in the modelling tool and exported with it, so a design change reaches this folder with the next export |
| Folder-owned | `src/backend/render/` and its tests | in this repository; an export never touches them (`OWNED.md`) |

`EXPORT.json` records the source revision, the solver revision and the SHA-256 of every generated file.

## Install

Linux x86-64 with conda; an NVIDIA GPU for the `gpuNewton` solver.

```bash
./setup_env.sh            # conda environment "dceval": OpenFOAM 2412, MPICH, Node.js, torch, the GPU solver's packages
conda activate dceval
```

`setup_env.sh` installs this folder into the environment (`pip install -e .`), so `dceval` and `backend` import
from any directory. It also installs the headless compiler's `node_modules` and builds the OpenFOAM solvers
inside this tree. The cut-cell mesher ships as a built binary
(`src/backend/aircooling/third_parties/dcCartMesh/platforms/`); it needs the conda-forge `openfoam=2412` +
`mpich` of this environment.

Run with the environment's own interpreter. OpenFOAM, MPI and the mesher's libraries are taken from that
interpreter's environment: each run puts its `bin` first on `PATH` and drops any other OpenFOAM or Open MPI
from `PATH` and `LD_LIBRARY_PATH` for its child processes, so a system OpenFOAM sourced in the shell does not
interfere.

## Evaluate a design from Python

```python
from dceval import evaluate

result = evaluate("designs/room.cfdproj", "runs/room-001",   # project file, working directory of this run
                  solver="gpuNewton", gpus=[0, 1], processes=8)
result["status"]                              # "succeeded" or "failed"
result["failure"]                             # None, or {"stage": ..., "reason": ...}
result["metrics"]["convergence"]["verdict"]   # CONVERGED / NOT_CONVERGED / INCONCLUSIVE
result["metrics"]["fields"]                   # per cooling unit and per server, see below
result["resultPath"]                          # the result.json this dict was written to
```

| Argument | Meaning |
|---|---|
| `cfdproj` | the project file |
| `case_dir` | working directory of this evaluation: mesh, solve directories and logs go here (default: the project file's directory). One evaluation at a time per directory; different directories can run side by side |
| `solver` | `"gpuNewton"` (default: the differentiable GPU Newton-Krylov solver, steady solves) or `"openfoam"` (the CPU reference) |
| `gpus` | cards the GPU solver may use: `nvidia-smi` indices or `GPU-...` UUIDs, as a list or a comma separated string. One solve uses one card, the idle one with the most free memory. `None`: any card |
| `processes` | MPI ranks of the mesher and of the OpenFOAM solve; also the number of mesh partitions (the GPU solver reads the partitioned mesh). Default 8 |
| `stages` | `("mesh", "solve")` (default), `("mesh",)`, or `("solve",)` to solve again on the accepted mesh of `case_dir` |
| `mesh_engine`, `end_time`, `run_settings` | overrides of what the project stores (`run_settings` is `{"meshConfig": {...}, "simulationConfig": {...}}`) |
| `mesh_timeout`, `solve_timeout`, `liquid_timeout` | hard limits in seconds (defaults 1800, 14400, 3600). Past a limit the job and every process it started are stopped and the run fails at that stage |
| `raise_on_failure` | raise instead of returning a failed result |

A project whose liquid-cooled servers also have fans is run as a hybrid case automatically: the liquid network
is solved first (in this process, on `liquid_device`, default `cpu`) and its heat is handed to the air solve.

Invalid arguments raise `ValueError`. A run that fails is returned with `status == "failed"`, not raised.

## Command line

```bash
python -m dceval evaluate designs/room.cfdproj --case-dir runs/room-001 --gpus 0,1 --processes 8
python -m dceval evaluate designs/room.cfdproj --case-dir runs/room-001 --solver openfoam --processes 8
python -m dceval evaluate designs/room.cfdproj --case-dir runs/room-001 --stages solve
python -m dceval check        # every exported file as the export wrote it? (exit 1 when not)
```

stdout carries exactly one line of JSON: a summary (`status`, `failure`, `resultPath`, `cells`, `convergence`,
`rackInlet`, `energyBalance`, ...), or the whole result with `--full`. Exit code 0: succeeded; 1: the run
failed; 2: invalid arguments. `python -m dceval evaluate --help` lists every option.

## The result

`evaluate` returns the content of `<case_dir>/log/api/<timestamp>_SGT__<kind>/result.json` plus `resultPath`:

| Key | Content |
|---|---|
| `status`, `failure` | `succeeded` when every requested stage ran to its end, else `failed` with the stage (`input`, `export`, `preflight`, `liquid`, `mesh`, `solve`, `metrics`, `internal`) and the reason. Solver convergence is reported, never folded into the status |
| `inputs` | the arguments as run, the project's SHA-256, the solver |
| `stages` | per stage: seconds, return code, `mesh.cells`, `mesh.checkMeshOk` and the mesh quality record, `solve.runDir`, `solve.solveAssessment`, `solve.solverEngine` |
| `metrics.convergence` | `verdict` and `reason`, with the return-air, energy-balance and residual parts behind it |
| `metrics.fields` | per cooling unit: supply and return patch temperatures and removed heat; per server: inlet-face temperature; `rackInlet.summary`; `energyBalance` |
| `metrics.sensors` | the monitored series the solver wrote |
| `metrics.liquid`, `metrics.heatLedger` | hybrid cases: the liquid network at the handed-over sample, and the heat given to the air side |
| `identity` | input hashes, the revision of this folder, the versions and hashes of the tools that ran |
| `artifacts` | paths: the manifest sent to the runner, the mesh and solve job logs, the solve directory, `result.json` |

## Choosing the GPU solver and its cards

`solver="gpuNewton"` (the default) selects the GPU solver; `gpus` (`--gpus`) names the cards it may
use. Indices are those `nvidia-smi` prints; they are converted to UUIDs before the solver sees them, because
CUDA numbers cards differently. Without `gpus` the cards named in `DCDTUNI_GPU_SOLVER_GPU` (UUIDs, comma
separated) are used, else any card.

After a solve a loaded solver process stands by on its card for ten minutes, so the next solve starts without
loading the solver again (`DCDTUNI_GPU_SOLVER_STANDBY=<seconds>`; `0` turns this off). Its sockets and log are
in `.codex-tmp/gpu-solver/` of this folder.

Device memory grows with the mesh. Before a solve the solver compares its estimate for the mesh with the free
memory of the card and refuses when the mesh cannot fit; a solve that runs out of memory fails the same way.
Either failure ends the run with status `failed`, error code `gpu_out_of_memory` in
`<solve dir>/gpuNewton/result.json`, and a message that says how much the mesh needs and how much the card had.
Name more cards, or cards with more free memory.

## The lower-level API

`dceval.evaluate` is built on `backend.api`, which stays available for finer control:

```python
from backend.api import (run_air_case, run_hybrid_case, run_liquid_case, solve_liquid_steady,
                         build_project, export_air_case, export_liquidrun, run_batch)
```

```bash
cd src
python -m backend.api air X.cfdproj --solver-engine gpuNewton --mesh-processes 8 --solve-processes 8 --max-processes 8 --full
python -m backend.api liquid X.cfdproj --fd-check 3                    # DAE steady + adjoint gradient
python -m backend.api build spec.json --out ../_tmp/Room/Room.cfdproj  # modelling steps -> .cfdproj
python -m backend.api export-air X.cfdproj --out-root ../_tmp/exports  # .cfdrun + STL only
```

`docs/contract-verbs.md` lists the modelling steps `build` accepts. Some docstrings of `backend.api` still
describe a private server; in this folder the same calls run in process (`backend/api/direct_session.py`).

## The solve directory

Each solve copies the mesh to `<case_dir>/log/<timestamp>_<case>/` and runs there: a standard OpenFOAM case
(`constant/polyMesh`, `0/`, `constant/`, `system/`, processor directories). The boundary patches carry the
project's object and face names, and `model/<name>.cfdrun` holds the boundary values. The GPU solver writes its
fields back in the same OpenFOAM format (`gpuNewton/`: request, log and result), so post-processing and
rendering read one layout whichever solver ran.

## Tests

```bash
cd src && python -m pytest -q backend/tests
```

## Not included

The interactive editor, the web pages and their server, AI field inference and the mesher sources are not part
of this folder.
