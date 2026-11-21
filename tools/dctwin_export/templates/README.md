# dctwin — data-centre design evaluator

Takes a data-centre project file (`.cfdproj`), runs the whole simulation chain without a browser, and returns
structured physical metrics:

```
.cfdproj ──▶ headless compiler ──▶ .cfdrun + STL ──▶ cut-cell mesh ──▶ OpenFOAM (buoyant Boussinesq) ──▶ result.json
         └─▶ .liquidrun ──▶ liquid-cooling DAE (PyTorch, steady / transient, implicit adjoint gradients)
```

The design modules (`backend.dcdesign`, `backend.plant`, `backend.aircooling.zonal`) turn a compact design
specification into a `.cfdproj` and score it, either with the fast zonal model (L0) or with the CFD run above
(HF, `backend.dcdesign.hf`).

`EXPORT.json` records the source revision this tree was exported from.

## Install

Linux x86-64 with conda:

```bash
./setup_env.sh            # creates the conda environment "dctwin" (OpenFOAM 2412, MPICH, Node.js, torch)
conda activate dctwin
```

The cut-cell mesher ships as a built binary (`src/backend/aircooling/third_parties/dcCartMesh/platforms/`); it
needs the conda-forge `openfoam=2412` + `mpich` of this environment. The solvers are built from source by
`setup_env.sh`.

Run `setup_env.sh` in the checkout you will use: besides the environment, it installs the headless compiler's
`node_modules` and builds the solvers inside that tree. Used as a submodule (for example ISPO's
`Ref_code/dctwin`), run it there; a second checkout needs only `cd src/headless && npm install` and
`cd src/backend/aircooling/third_parties/foam && ./deploy.sh` with the environment active. torch is 2.7.1
(`DCTWIN_TORCH_CUDA=cu126` by default, `cpu` for no GPU).

## Run

```bash
cd src
python -m backend.api air ../path/to/X.cfdproj --mesh-processes 8 --solve-processes 8 --max-processes 8 --full
python -m backend.api hybrid X.cfdproj --solve-processes 4 --max-processes 8 --full   # liquid first, then air
python -m backend.api liquid X.cfdproj --fd-check 3                                   # DAE steady + adjoint gradient
python -m backend.api build spec.json --out ../_tmp/Room/Room.cfdproj                  # modelling steps -> .cfdproj
python -m backend.api export-air X.cfdproj --out-root ../_tmp/exports                  # .cfdrun + STL only
```

From Python: `from backend.api import run_air_case, run_hybrid_case, run_liquid_case, solve_liquid_steady,
build_project, export_air_case, export_liquidrun, run_batch`.

Each run writes `<case>/log/api/<timestamp>_SGT__<kind>/result.json`: input hashes, tool versions, stage timings,
the convergence verdict (return-air temperatures steady and the cooling units removing the declared heat), and
`metrics.fields` (per cooling unit: supply/return patch temperatures and removed heat; per server: inlet-face
temperature). A failure names its stage and reason.

The API starts its own private server (`src/start_server.sh --app api`) on a free port per run, so several runs
can go in parallel, each with its own case directory.

## The OpenFOAM case

Each solve copies the mesh to `<case>/log/<timestamp>_<case>/` and runs there: a standard OpenFOAM case
(`constant/polyMesh`, `0/`, `constant/`, `system/`, processor directories when decomposed). The boundary
patches carry the project's object and face names, and `model/<name>.cfdrun` holds the boundary values. This is
the entry point for adjoint / differentiable solvers working on the same mesh and boundary conditions.

## Modelling verbs

`docs/contract-verbs.md` lists the steps `backend.api build` accepts.

## Tests

```bash
cd src && python -m pytest -q backend/tests
```

## Not included

The interactive editor, the 3-D display, AI field inference and the mesher sources are not part of this tree.
