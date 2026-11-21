# DCTwin Engine (dcdtuni)

DCTwin is a multiphysics digital twin engine for data center thermal simulation. It automates the full CFD workflow — browser-authored geometry, `.cfdrun` manifest export, OpenFOAM meshing, parallel solving, and in-browser post-processing — so engineers can iterate on data center designs without manual OpenFOAM setup.

## Key Features

- **Visual STL Modeler** — browser-based 3D editor for creating multi-region STL geometry with per-face boundary condition assignment
- **2D Floor Plan Editor** — dedicated canvas-based room layout tool with snap, grid, and multi-model drag
- **Automated CFD Pipeline** — `.cfdproj` → `.cfdrun` + STL export → mesh (dcCart cut-cell Cartesian by default; snappyHexMesh selectable) → `buoyantBoussinesqSimpleFoam_new` / `buoyantBoussinesqPimpleFoam` (parallel, MPI)
- **In-Browser Post-Processing** — temperature / velocity / pressure field visualization, streamlines, slice planes, probe sensors
- **Parallel Execution** — MPI-based parallel mesh and solve with configurable core count
- **Liquid Cooling** — Newton DAE network solver (steady state, transient march, implicit-adjoint gradients) with CDU, cold plate and pipe routing, and an air/liquid hybrid run that hands the liquid side's heat to the air solve
- **Scripted / headless API** — `python -m agent.scripted_api` builds, meshes, solves and post-processes a project without a browser (see below)
- **REST API** — the server's endpoints for geometry, mesh, solve, post-processing, file I/O, and user management
- **Demo Cases** — pre-built `.cfdproj` files under `case/`, from 2-rack lab testbeds to 238-rack production facilities

## Current state

Ten release air-cooling cases mesh with dcCart through the editor's product flow, pass the strict
`checkMesh` gate, and their latest steady solves converge under the return-air rule (every ACU
return-air temperature steady within 0.5 K; residuals are diagnostics) — receipt summary:
[docs/benchmarks/air-release-cases-2026-10-21](docs/benchmarks/air-release-cases-2026-10-21/README.md).
What works, what is in progress and the next experiments: [docs/developer/PROJECT_STATE.md](docs/developer/PROJECT_STATE.md).

## Prerequisites

The Linux server requires Git/Git LFS, Conda, a C++ compiler, Node.js and npm. The editor declares `"node": "^24.12.0"` and `"npm": ">=10.0.0"` (`engines` in `src/frontend/react/package.json`) — install Node 24.12 or a later 24.x for `npm ci` and the editor build. The scripted API's headless compiler (esbuild + JSDOM over the installed `node_modules`) has also been run on Node v20.19.4 on this host. The bootstrap creates Python 3.10/OpenFOAM2412 and liquid dependencies in `.codex-tmp/envs/dcdtuni` inside this clone, installs the tracked React lock, and builds both native source trees. Optional OV uses a supported NVIDIA driver/NVENC/Vulkan and a pinned public Kit-CAE build.

## Installation

Follow [fresh-clone deployment](docs/developer/fresh_clone_deploy.md) for details and the separate runtime acceptance gates.

```bash
git clone https://github.com/Litianyu141/dcdtuni.git
cd dcdtuni
python3 tools/bootstrap_dcdtuni.py --dry-run
python3 tools/bootstrap_dcdtuni.py --install
python3 tools/bootstrap_dcdtuni.py --preflight
```

Installation/preflight verify assets, dependencies and source/build identities; they do not certify mesh quality, numerical correctness or OV decoded video. No service is started by bootstrap.

### Python environments

Every Python process of the product runs in **one Conda environment, `dcdtuni`** (Python 3.10). `setup_env.sh` (run by
the bootstrap) creates it inside the clone at `.codex-tmp/envs/dcdtuni`; the launcher uses that one first, then an
active or named `dcdtuni` Conda environment. Nothing else needs a separate Python environment.

| What | Packages | Installed by |
|---|---|---|
| Server, CFD pipeline (OpenFOAM 2412 from conda-forge), post-processing, scripted API | `pip install -e ".[dev]"` (pyproject.toml) | `setup_env.sh` |
| Liquid cooling (DAE solver) | torch 2.7.1 (CUDA 12.8), networkx | `setup_env.sh` (best-effort; `liquid` extra) |
| SCANO AI solve and real-time tuning (worker started by the server) | torch 2.7.1 (CUDA 12.8), scipy, torch-geometric 2.8.0, torch-scatter 2.1.2 from data.pyg.org | `setup_env.sh` (best-effort; `ai` extra) |

torch defaults to the CUDA 12.1 build; `DCDTUNI_TORCH_CUDA=cpu bash setup_env.sh` installs CPU builds. To add the AI
dependencies to an existing environment:

```bash
conda activate dcdtuni
python -m pip install torch==2.7.1 --index-url https://download.pytorch.org/whl/cu128
python -m pip install networkx scipy torch-geometric==2.8.0
python -m pip install torch-scatter==2.1.2 -f https://data.pyg.org/whl/torch-2.7.0+cu128.html
python tools/bootstrap_dcdtuni.py --preflight     # checks they import
```

Outside Conda, by design: the Omniverse display (`--ov`) runs Kit's own embedded Python from the Kit-CAE build
(`bootstrap --install --ov`), and the developer-only browser test tool (`/install-WebGL-browse-use`) keeps its own
Python 3.11 virtual environment in the developer's home. `poetry.lock` is not used by the install.

## Starting the Server

`src/start_server.sh` is the only way to start the backend and its UI. It chooses the Python environment, checks
the signed release stamp, builds the React UI when its sources are newer than `dist/`, owns the ports, starts the
Omniverse display bridge and the AI worker when asked, and cleans all of it up on exit. Do not start `server.py`,
`npm run dev` or the Kit scripts by hand.

```bash
./src/start_server.sh                              # React editor at http://localhost:8000/ (local-mode login)
./src/start_server.sh --port 9000 --host 0.0.0.0   # another port, reachable from the LAN
./src/start_server.sh --scano --ai-gpu 2           # AI solve on GPU 2, model loaded and warmed at start
./src/start_server.sh --ov --gpu 3                 # plus the Omniverse display bridge on GPU 3
./src/start_server.sh --gpu 1,2                    # the solve page offers the GPU air solver, on GPU 1 or 2
./src/start_server.sh --gpu                        # the same, on any idle GPU of the machine
./src/start_server.sh --help                       # every option, from the launcher's own header
```

The banner printed at start says which URL to open, the backend and WebSocket ports, where the local-mode
credentials are, which GPU the AI solve uses (with a warning when it lacks free memory), whether the GPU air solver
is offered and on which cards, and how long start-up took.

### Full version (copy this)

React editor + Python backend + Omniverse (Kit) display bridge + SCANO AI solve, reachable from other machines on
the LAN:

```bash
DCDTUNI_ADMIN_USER=admin DCDTUNI_ADMIN_PASSWORD='WIN#@desk' \
  ./src/start_server.sh --app react --build --port 8064 --host 0.0.0.0 \
  --ov --gpu 3 --ov-port 8302 --ov-stream-port 47999 \
  --scano --ai-gpu 2
```

| Flag | What it does |
|---|---|
| `--app react` | the React editor (the default product) |
| `--build` | force a rebuild of the React UI. Not needed after a `git pull`: the launcher rebuilds `dist/` by itself whenever a UI source file is newer than it |
| `--port 8064` | the port you open in the browser; the backend WebSocket takes `--port + 1` (8065) |
| `--host 0.0.0.0` | listen on every interface. The backend defaults to `127.0.0.1`, which only this machine can open |
| `--ov` | also start the Omniverse Kit display bridge (render + WebRTC stream; modelling stays in Three.js) |
| `--gpu 3` | the GPUs this launch computes on: a list (`--gpu 3,2,4`), or no value for every card. Kit uses the first one (it runs on ONE physical GPU; pick an idle card from `nvidia-smi`), and with `--gpu` given the solve page offers the GPU air solver on these cards |
| `--ov-port 8302` | Kit WebRTC signalling port (default 8301) |
| `--ov-stream-port 47999` | Kit UDP video port (default 47998); every Kit on the host needs its own |
| `--scano` | load the recommended AI model and warm it up at start, so the first AI solve, AI-initialised CFD solve or real-time tuning does not wait for it |
| `--ai-gpu 2` | the GPU of the AI worker (default: the first card of `--gpu` that is not Kit's, else 0). Keep it off Kit's GPU; it needs about 14 GiB free, or the launcher refuses to start and lists every GPU's free memory |
| `DCDTUNI_ADMIN_USER/PASSWORD` | the administrator you log in with; without them the launcher runs in local mode: it creates (once) and reuses `src/server/auth-local/admin_credentials` (user `admin` on line 1, a random password on line 2, mode 0600, git-ignored) |

Then:

1. Open `http://<host>:8064/` and log in.
2. Kit needs about two minutes to start. The **OV** button beside the renderer
   selector shows `OV starting…`; click it to queue a connection and it switches
   to Omniverse by itself once Kit is ready (`OV connected`).
3. Postprocess runs in Omniverse with kit-cae's own tools (boundary surface
   field, IndeX volume, GPU streamlines, plane slice) from the same panel.

Over SSH, forward the browser, WebSocket and signalling ports:
`ssh -L 8064:localhost:8064 -L 8065:localhost:8065 -L 8302:localhost:8302 <user>@<host>`
(the WebRTC video itself is UDP and needs a direct LAN route to the host).

Stop: `Ctrl+C` in the launcher terminal, or from another shell
`fuser -k 8064/tcp 8065/tcp 8302/tcp`. One server holds ONE case directory, so
run one launcher per person on distinct ports.

### Ready-to-copy commands

Run from the repository root. Change the ports, GPUs and password to yours; check free GPUs with `nvidia-smi`.
The first start with `DCDTUNI_ADMIN_USER` / `DCDTUNI_ADMIN_PASSWORD` creates that administrator in the backend's
user database (`src/server/auth/`); later starts must give the same password, or the backend refuses to start
("Explicit bootstrap credentials must authenticate an administrator"). Add `--auth-dir <your dir>` to keep a
separate user database.

**Without Omniverse: editor + AI solve, reachable from the LAN**

```bash
DCDTUNI_ADMIN_USER=admin DCDTUNI_ADMIN_PASSWORD='WIN#@desk' \
  ./src/start_server.sh --port 8064 --host 0.0.0.0 --scano --ai-gpu 0
```

Open `http://<host>:8064/`. The AI model is loaded at start; meshing, CFD, the AI solve and real-time tuning all
work, and post-processing renders in the browser (Three.js).

**CFD only: no Omniverse, AI model loaded at the first AI solve, reachable from the LAN**

```bash
DCDTUNI_ADMIN_USER=admin DCDTUNI_ADMIN_PASSWORD='WIN#@desk' \
  ./src/start_server.sh --port 8064 --host 0.0.0.0
```

Starts fastest and holds no GPU memory until someone runs an AI solve (on GPU 0, or `--ai-gpu N`).

**On this machine only, local-mode login**

```bash
./src/start_server.sh --scano
```

Open `http://localhost:8000/`; user `admin`, password on line 2 of `src/server/auth-local/admin_credentials`.

**With the GPU air solver** (the solve page then offers OpenFOAM or the GPU solver; OpenFOAM stays the default)

```bash
./src/start_server.sh --gpu 1,2                     # local-mode login; GPU solves on card 1 or 2
./src/start_server.sh --gpu                         # the same, on any idle card of the machine
DCDTUNI_ADMIN_USER=admin DCDTUNI_ADMIN_PASSWORD='<password>' \
  ./src/start_server.sh --port 8064 --host 0.0.0.0 --gpu 1,2,3 --scano --ai-gpu 0   # LAN, GPU solver and AI solve
```

Name cards with enough free memory (`nvidia-smi`): one GPU solve needs about 0.8 GiB + 18 GiB per million
cells and runs on the idle card of the list with the most free memory; cards may be `nvidia-smi` indices or
`GPU-…` UUIDs. In the editor set **Solver engine** (求解引擎) on the solve page to **GPU Newton-Krylov solver
(differentiable)** (GPU 可微求解器), then run the solve as usual; the page shows each card's memory and utilisation while it runs. A solve that does not fit ends with
an out-of-memory message: restart with more cards, or cards with more free memory, after `--gpu`. The banner at
start says whether the GPU solver is offered; without `--gpu` the engine choice is not shown.

**Two people on one host** (one server holds one case directory, so one launcher each, on its own ports and GPUs)

```bash
./src/start_server.sh --port 8064 --host 0.0.0.0 --scano --ai-gpu 2      # person A: 8064/8065
./src/start_server.sh --port 8104 --host 0.0.0.0 --scano --ai-gpu 4      # person B: 8104/8105
```

**Editor development: Vite hot reload, backend on `--port + 2`**

```bash
./src/start_server.sh --dev --port 8064 --ui-host 0.0.0.0 --host 0.0.0.0
```

### All options

**Mode and UI**

| Option | Default | What it does |
|---|---|---|
| `--app react\|html\|omniverse\|api` | `react` | `react`: the editor (the product). `html`: the archived single-file editor, unmaintained since 2026-10-23. `omniverse`: the Kit CFD demo UI. `api`: `/api` only, no page (what `python -m agent.scripted_api` starts privately for each run) |
| `--build` | off | force a rebuild of the React UI. Rarely needed: `dist/` is rebuilt by itself whenever a UI source file is newer |
| `--dev` | off | React development mode: Vite serves the UI with hot reload on `--port`, the backend runs on `--api-port` (default `--port + 2`). Implies `--app react` |

**Ports and network**

| Option | Default | What it does |
|---|---|---|
| `--port N` | `8000` | the port you open in the browser. In `react`, `html` and `api` mode the backend serves the page and `/api` on this one port |
| `--ws-port N` | `--port + 1` | the backend's WebSocket port (log stream, terminal) |
| `--host 0.0.0.0` | `127.0.0.1` | the backend's listen address. Without it only this machine can open the page |
| `--ui-host ADDR` | `127.0.0.1` | the Vite listen address in `--dev` mode |
| `--api-port N` | `--port + 2` | `--dev` only: the backend port; naming one that is already running reuses that backend instead of starting another |
| `--no-backend` | off | `--dev` only: start the UI without a backend (one is already running on `--api-port`) |

The launcher never moves to another port: a port another user holds is an error (pick another `--port`), and a
port your own processes hold is freed first, so do not rerun it on the port of a server you are still using.

**Login**

| Option | Default | What it does |
|---|---|---|
| `DCDTUNI_ADMIN_USER` / `DCDTUNI_ADMIN_PASSWORD` | unset | the administrator you log in with (environment variables) |
| `--admin-credentials FILE` | unset | the same from a file you own, mode 0600: user on line 1, password on line 2 |
| `--auth-dir DIR` | see below | where the backend keeps its user database |

With neither, the launcher runs in local mode: it creates once and reuses `src/server/auth-local/admin_credentials`
(user `admin`, a random password, mode 0600, git-ignored) and uses that directory as `--auth-dir`. For a shared or
LAN deployment pass the environment variables or `--admin-credentials` with your own `--auth-dir`.

**Case**

| Option | Default | What it does |
|---|---|---|
| `--case-dir /abs/case` | none (the editor sets it from the project it opens) | the case directory the server starts on. One server holds ONE case directory and runs one mesh/solve job at a time, so run one launcher per person on distinct ports |

**Omniverse display bridge** (`--app react` only)

| Option | Default | What it does |
|---|---|---|
| `--ov` | off | also start the Kit display bridge: RTX render streamed over WebRTC into the editor; modelling stays in Three.js. Needs `bootstrap_dcdtuni.py --install --ov` once |
| `--ov-gpu N` | the first card of `--gpu`, else `0` | the ONE physical GPU Kit renders on; pick an idle card from `nvidia-smi` |
| `--ov-port N` | `8301` | Kit's WebRTC signalling port |
| `--ov-stream-port N` | `47998` | Kit's UDP video port. Every Kit on the host needs its own pair of ports |

**GPU compute** (the GPU air solver; shared with Kit and the AI worker)

| Option | Default | What it does |
|---|---|---|
| `--gpu [LIST]` | off | the cards this launch may compute on: `nvidia-smi` indices or `GPU-…` UUIDs, comma separated; without a value, every card. Only a launch with `--gpu` offers the GPU air solver: the solve page then shows a solver-engine choice (OpenFOAM stays the default) and the server accepts `solverEngine: "gpuNewton"`. Each GPU solve runs on one card: the idle one of the list with the most free memory. Kit (`--ov`) takes the first card of the list unless `--ov-gpu` names one; the AI worker takes the first card that is not Kit's unless `--ai-gpu` names one. `--scano` stays the switch of the AI worker |
| `DCDTUNI_GPU_SOLVER_STANDBY` | `600` | seconds a loaded solver process stands by after its last solve (it starts with the first GPU solve and makes the next ones start at once); `0`: none |

The GPU air solver is `src/backend/aircooling/gpu_solver` and runs in the same conda environment; it needs the
`gpu-solver` extra (`setup_env.sh` installs it). When a package is missing the launcher still starts, without the
solver, and the banner says which package.

While the GPU engine is selected, the solve page shows every card of the launch live: memory in use and free,
utilisation, and whether the solver is solving or standing by on it, together with the size of the case's mesh
and the memory one solve needs at most (about 0.8 GiB + 18 GiB per million cells, 25 GiB per million up to 0.5 million cells; one solve runs on one card, the
one with the most free memory). A solve that does not fit its card ends with an out-of-memory error on the page
and in the job log: restart `start_server.sh` and name more cards, or cards with more free memory, after `--gpu`.

**AI inference** (the SCANO AI solve and real-time tuning)

| Option | Default | What it does |
|---|---|---|
| `--ai-gpu N` (`--scano-gpu`) | `DCDTUNI_SCANO_GPU`, else from `--gpu`, else `0` | the GPU of the AI worker: an `nvidia-smi` index, a `GPU-…` UUID, or `cpu`. Checked before start |
| `--scano [MODEL]` | off | load MODEL (a checkpoint id; default the recommended one) and warm it up at start, so the first AI solve does not wait for it |
| `DCDTUNI_SCANO_MIN_FREE_MIB` | `14000` | free memory the AI solve needs on its GPU (a DCTS room peaks at about 11 GiB) |

When the chosen GPU has less free memory than that, `--scano` refuses to start; without `--scano` the server starts
and the banner warns, and an AI solve on that GPU fails. Both messages list every GPU's free memory: choose another
with `--ai-gpu`. The AI solve, the AI-initialised CFD solve and real-time tuning all share this one worker.

**Passed to the backend** (`server.py`)

| Option | What it does |
|---|---|
| `--trust-loopback` | trust loopback HTTP peers without a login (never grants the terminal) |
| `--enable-terminal` | enable the token-authenticated administrator WebSocket terminal |

Any option the launcher does not know is passed to `server.py` unchanged.

**Environment**

| Variable | What it does |
|---|---|
| `DCDTUNI_ENV_PREFIX` | a repository Conda environment other than `.codex-tmp/envs/dcdtuni`; it must end in `dcdtuni` and stay inside the clone. Export the same value for the bootstrap and the launcher. Without it the launcher uses the repository environment, else an active or named `dcdtuni` Conda environment |
| `DCDTUNI_SCANO_GPU` | the AI worker's GPU when the backend is started without `--ai-gpu` |
| `DCDTUNI_PP_PREFETCH=0` | do not read the newest result in the background when a case directory is set |
| `DCDTUNI_PP_VOLUME_PROBE=1` | volume rendering through VTK's exact probe instead of cell-centre sampling (much slower) |

### Which port is what

| Mode | Open in the browser | Backend (`/api`) | WebSocket |
|---|---|---|---|
| `react` (default), `html`, `api` | `--port` | `--port` | `--ws-port` (`--port + 1`) |
| `react --dev` | `--port` (Vite) | `--api-port` (`--port + 2`) | `--api-port + 1` |
| `omniverse` | `--port` (demo UI) | Kit streams on `--ov-port` | — |

Over SSH, forward the browser and WebSocket ports (and `--ov-port` with `--ov`), e.g.
`ssh -L 8000:localhost:8000 -L 8001:localhost:8001 <user>@<host>`. The Omniverse video is UDP and needs a direct
LAN route to the host.

Stop: `Ctrl+C` in the launcher terminal, or `fuser -k <port>/tcp <ws-port>/tcp` (plus `--ov-port`) from another
shell. Never `kill`/`pkill`: the host is shared.

## How to Use

### 1. Create a New Project

1. Open `http://localhost:8000/` (or the `--port` you started on) and log in: in local mode the
   user and password are lines 1 and 2 of `src/server/auth-local/admin_credentials`; a server
   started with `DCDTUNI_ADMIN_USER` / `DCDTUNI_ADMIN_PASSWORD` takes that pair
2. Click **File → New Project**, enter a project name
3. The server creates `case/{ProjectName}/` as the working directory
4. You start with an empty 3D viewport — add models from the left panel

### 2. Load a Demo Case

Pre-built `.cfdproj` demo cases ship in the `case/` directory. To load one:

1. Click **File → Open Project**
2. Navigate to the `case/` directory on the server and select a `.cfdproj` file
3. The full scene (room, racks, ACUs, boundary conditions, mesh settings) is restored

Recommended starter cases:

| Case              | File                                               | Description                                               |
| ----------------- | -------------------------------------------------- | --------------------------------------------------------- |
| SingleRack        | `case/SingleRack/SingleRack.cfdproj`               | Minimal 1-rack setup, fast mesh, good for learning the UI |
| Base_coldAisleIso | `case/Base_coldAisleIso/Base_coldAisleIso.cfdproj` | Cold aisle containment, 2 racks + 1 ACU                   |
| DMAL_Testbed      | `case/DAML-Testbed/DMAL_Testbed-patched.cfdproj`   | Lab testbed (6.1 x 3.2 m), 2 racks + 4 ACUs               |
| STDCT             | `case/STDCT/STDCT.cfdproj`                         | Standard data center (18.2 x 8.3 m), 24 racks + 3 ACUs    |
| GTC_2025          | `case/GTC/GTC_2025.cfdproj`                        | Large facility (31 x 19.2 m), 50 racks + 6 ACUs           |
| Liquid            | `case/Liquid/Liquid.cfdproj`                       | Liquid cooling demo with CDU and pipe routing             |

### 3. Build a Data Center Model

**Add equipment** from the left panel toolbar:

| Model Type     | Purpose                                                                                      |
| -------------- | -------------------------------------------------------------------------------------------- |
| **Room**       | Define the room boundary: polygon floor plan + height (use the 2D editor for complex shapes) |
| **Rack**       | Server racks with U-slot equipment system: configure servers, power, airflow per slot        |
| **ACU**        | Air cooling units: set supply temperature, return flow rate, face positions                  |
| **Baffle**     | Internal partitions, hot/cold aisle containment walls                                        |
| **Floor**      | Raised floor with perforated tile openings                                                   |
| **Box**        | Generic equipment or heat sources                                                            |
| **Custom STL** | Import external geometry with water-transfer-printing patch projection                       |

**Position models** using:

- Drag in the 3D viewport with TransformControls
- Enter exact coordinates in the right-panel property inspector
- Use the **2D Floor Plan Editor** (room icon) for top-down layout with snap-to-grid

**Set boundary conditions** on each face/patch:

- Select a face → right panel shows CFD properties
- Assign role: `supply` (air inlet), `return` (air outlet), `wall`, or `none`
- Set temperature (°C), volume flow rate (m³/s), or heat power (W)

### 4. Configure Mesh and Solver

Switch to the **Mesh** tab in the left panel:

- **Mesh engine** — `dcCart` (default: cut-cell Cartesian, no snapping, one algorithm for any MPI rank count), or `snappy` (body-fitted snappyHexMesh); `meshgen` (MeshGenerator) was retired on 2026-10-26
- **Base cell size** — global mesh resolution; a new project starts on the recommended cell-size rule (0.25 m)
- **Refinement** — per-object refinement levels and refinement zones
- **Parallel cores** — number of MPI processes for meshing

Switch to the **Solver** tab:

- **Steady / transient** — `buoyantBoussinesqSimpleFoam_new` (steady, iterations = end time, default 500) or `buoyantBoussinesqPimpleFoam` (transient, a timeline of seconds)
- **Parallel cores** — MPI process count for solving (the solve always runs with `mpirun -np N … -parallel`)
- **Convergence** — judged by the runner after the solve and written to `convergence.json` in the run directory: a steady run is CONVERGED once every ACU return-air temperature has changed and then plateaued within the project's stop tolerance (`simulationConfig.stopToleranceK`, default 0.5 K, set in the solver tab) over the last 100 iterations (a run shorter than that window is INCONCLUSIVE, "too-short"; there is no other minimum run length since 2026-10-27); residuals are reported as diagnostics and do not gate the verdict. A monitored steady solve stops by itself once it converges

### 5. Run CFD Simulation

1. Click **Export STL + Run Mesh** — the browser exports geometry as multi-solid ASCII STL and a `.cfdrun` manifest, then submits to the server
2. Watch real-time logs in the **Operation Log** panel at the bottom
3. Meshing pipeline with dcCart: blockMesh background → dcCartMesh (per-object octree refinement and the cut-cell pass, `mpirun -np N dcCartMesh -parallel`) → createBaffles → checkMesh; with snappy: blockMesh → surfaceFeatureExtract → snappyHexMesh → createBaffles → checkMesh. The final strict checkMesh writes `mesh-quality-receipt.json`
4. After mesh completes, click **Run Solve** (or it chains automatically)
   - **Solver engine**: **OpenFOAM (CPU, MPI)** (default) or **GPU Newton-Krylov solver (differentiable)** (offered when the server was started with `--gpu`, see *With the GPU air solver* above). The GPU solver runs the steady air solve on one card of the launch and stops on the same return-air convergence rule; the rest of the pipeline is unchanged
5. The solver runs in parallel via MPI in a new directory `case/<name>/log/<timestamp>_<name>/` (results never live in `Mesh/`, the reset working directory)

A small case (one or two racks) meshes in well under a minute on a few cores; solve time grows with the cell count and the number of iterations.

### 6. View Results

After the solver finishes:

1. Switch to the **Post-Processing** tab in the left panel
2. Select a field: **T** (temperature), **U** (velocity), or **p_rgh** (pressure)
3. Adjust the colormap range with the sliders
4. Use **Streamlines**, **Slice Planes**, or **Volume Rendering** for deeper analysis
5. Read **Probe Sensor** values for point measurements at specific locations

Results can also be opened in ParaView via the `case.foam` file in the solve run directory
(`case/<name>/log/<timestamp>_<name>/case.foam`).

### 7. Liquid Cooling

Liquid cooling is part of the standard install (`tools/bootstrap_dcdtuni.py --install` sets up the liquid dependencies):

1. Click the **Liquid Mode** button to enter pipe routing mode
2. Add a **CDU** (Coolant Distribution Unit) to the scene
3. Drag-connect supply (blue) and return (red) pipes from CDU to rack cold plates
4. Use the **Topology Editor** for per-server cold plate wiring
5. Run the liquid solver (Newton DAE: steady state, or a transient march over the timeline) — monitor it in the floating panel
6. View per-rack temperature summaries and per-server chip temperatures

For a hybrid air/liquid project, run the liquid side first: the air solve then carries the liquid
side's heat (`hybridHeat`), and the server writes the handoff as `model/heat-ledger.json`.

## Scripted / headless API

`python -m agent.scripted_api` runs the same pipeline without a browser and writes a structured
`result.json` (inputs' SHA-256, code and tool identity, per-stage times and metrics) in a new
dated directory `<case>/log/api/<YYYY-MM-DD_HHMMSS>_SGT__<kind>/`. Air runs start their own
private server through `start_server.sh --app api` (own ports, per-run credentials) and stop it
afterwards; liquid runs are in-process. Reference: [docs/reference/scripted-api.md](docs/reference/scripted-api.md).

```bash
conda activate dcdtuni
cd src
python -m agent.scripted_api air ../case/SingleRack/SingleRack.cfdproj --mesh-only --mesh-processes 4 --max-processes 8
python -m agent.scripted_api air ../case/SingleRack/SingleRack.cfdproj --solve-only --solve-processes 4 --end-time 1500 --max-processes 8
python -m agent.scripted_api air ../case/SingleRack/SingleRack.cfdproj --mesh-processes 4 --solve-processes 4 --end-time 1500 --max-processes 8
python -m agent.scripted_api liquid ../case/LiquidSingle_Control/LiquidSingle_Control.cfdproj --mode transient --duration 10
python -m agent.scripted_api hybrid ../case/Shinaide_Liquid/Shinaide_liquid.cfdproj --mesh-processes 4 --solve-processes 4 --max-processes 8
python -m agent.scripted_api build ../_tmp/room.json --out ../_tmp/Room/Room.cfdproj
python -m agent.scripted_api batch ../_tmp/batch.json --out-root ../_tmp/batches --max-parallel 2 --max-processes 8
```

The air solve on the GPU solver instead of OpenFOAM (`--solver-engine gpuNewton`; one card per solve,
`DCDTUNI_GPU_SOLVER_GPU` lists the cards it may use, unset = any idle card; meshing still uses the MPI processes):

```bash
DCDTUNI_GPU_SOLVER_GPU=1 python -m agent.scripted_api air ../case/SingleRack/SingleRack.cfdproj --mesh-processes 12 --solve-processes 12 --max-processes 12 --solver-engine gpuNewton
DCDTUNI_GPU_SOLVER_GPU=1,2 python -m agent.scripted_api hybrid ../case/Shinaide_Liquid/Shinaide_liquid.cfdproj --solve-only --mesh-processes 12 --solve-processes 12 --max-processes 12 --solver-engine gpuNewton
```

`--solve-only` reuses a mesh whose quality receipt is accepted and that was built from the STL the
project exports now; a one-rank solve is refused unless `--allow-serial-solve`; `build` turns a list
of editor automation steps into a `.cfdproj` (the verbs: `.claude/commands/dc-builder.md`, appendix).
Run the examples on a copy of a case if you want to keep its `log/` and `Mesh/` as they are.

## Repository Structure

```text
src/
  backend/                 # Python package (editable install)
    api/                   #   scripted / headless API (python -m agent.scripted_api)
    aircooling/            #   air-cooling CFD pipeline
      runner/              #     STLGenRunner, BridgeBC, fields, mesh quality, convergence
      third_parties/       #     OpenFOAM templates, dcCartMesh, the custom solver
    liquidcooling/         #   liquid-cooling Newton DAE solver
    tests/                 #   pytest suite
    kit-cae/               #   Omniverse Kit runtime (git-ignored; bootstrap --ov builds it)
  frontend/
    react/                 #   the editor: React + TypeScript + Three.js (vitest suite in test/)
      src/automation/      #     window.__dcdtuni, the automation contract scripts drive
      src/headless/        #     headless export / build used by agent.scripted_api
    shared/                #   what both editors use: js/ (.cfdproj format, geometry), i18n/, schema/, textures/
    web/                   #   the legacy single-file editor (STL_generate.html), unmaintained since 2026-10-23
    omniverse/             #   Omniverse CFD demo and display bridge (cfd_demo/)
  server/                  # REST/SSE API server (server.py) and auth
  start_server.sh          # the launcher: the only way to start a server
case/                      # CFD working directories (demo .cfdproj files)
docs/
  developer/               # architecture, API reference, runner internals, project state
  user-guide/              # installation, quick start, modeling guide
  reference/               # .cfdproj / .cfdrun schemas, scripted API
  manuscript/              # paper sources and the anonymised evaluation cases
```

## Key File Formats

| Format     | Purpose                                                      | Created By                  |
| ---------- | ------------------------------------------------------------ | --------------------------- |
| `.cfdproj` | Complete editor state (models, camera, undo history)         | File → Save Project         |
| `.cfdrun`  | Simulation manifest (geometry refs, BCs, mesh/solver config) | Auto-exported on mesh/solve |
| `.liquidrun` | Liquid network manifest (the DAE solver's input)           | Auto-exported on a liquid solve |
| `.stl`     | Multi-solid ASCII STL geometry                               | Auto-exported on mesh/solve |

## Running Tests

```bash
conda activate dcdtuni
pytest src/backend/tests/ -x -q                   # backend (stop on first failure)
pytest src/server/tests -q                        # server
(cd src/frontend/react && npx vitest run)         # editor
```

## API Reference

Key server endpoints (every `/api/*` path except login and `auth/check` needs the session cookie):

| Method | Endpoint                      | Description                           |
| ------ | ----------------------------- | ------------------------------------- |
| POST   | `/api/auth/login`             | Authenticate (returns session cookie) |
| GET    | `/api/auth/check`             | Public: is this cookie logged in (a liveness probe that needs no login) |
| POST   | `/api/set-case-dir`           | Set the active working directory      |
| GET    | `/api/server-info`            | Check dcdtuni + OpenFOAM availability |
| POST   | `/api/run-mesh`               | Submit mesh + solve job               |
| GET    | `/api/log-stream?job_id=<id>` | SSE real-time log stream              |
| GET    | `/api/job-status?job_id=<id>` | Query job completion                  |
| POST   | `/api/abort-job`              | Stop a running job                    |
| GET    | `/api/pp/field/{field}`       | Fetch post-processing field data      |
| POST   | `/api/liquid/solve`           | Run liquid cooling solver             |
| POST   | `/api/liquid/runs`            | Create a liquid run session (then `/runs/<id>/start`, `/inputs`) |
Full API documentation: [`docs/developer/`](docs/developer/)

## Documentation

Full documentation lives in the [`docs/`](docs/) directory:

- **User Guide** — installation, quick start, STL modeling, post-processing
- **Developer Docs** — architecture, REST API reference, runner internals, boundary conditions
- **Reference** — schema definitions for `.cfdproj` and `.cfdrun`

Build and serve the docs locally:

```bash
conda run -n dcdtuni mkdocs serve
```

## License

See [LICENSE](LICENSE) for terms.
