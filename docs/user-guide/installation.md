# Installation

This guide installs DCTwin on a Linux machine, starts the editor and checks that everything works. The detailed, step-by-step account of what the installer does (and the optional Omniverse runtime) is in [Fresh clone deployment](../developer/fresh_clone_deploy.md).

---

## System Requirements

| Component | Requirement | Notes |
| --------- | ----------- | ----- |
| **OS** | Linux x86_64 (Ubuntu 22.04 or later) | macOS works for development only, see [macOS notes](../developer/macos_build_notes.md) |
| **Conda** | Miniconda or Anaconda on `PATH` | the installer creates the environment inside the checkout |
| **Python** | CPython 3.10 | pinned by the installer |
| **OpenFOAM** | 2412 from conda-forge | installed into the environment; no system OpenFOAM needed |
| **MPI** | MPICH from conda-forge | installed into the environment |
| **C++ compiler** | system `g++` | builds `dcCartMesh` and the repository's solvers |
| **Node / npm** | Node 24.12 or later in the 24 line, npm 10 or later | builds the React editor |
| **Git** | with Git LFS | default textures and images are LFS objects |
| **RAM / CPU** | 16 GB / 8 cores minimum | 64 GB and 24+ cores for large rooms (millions of cells) |
| **GPU** | optional | Air-cooling CFD runs on CPU with the default OpenFOAM engine. The optional GPU air solver (`start_server.sh --gpu`) needs an NVIDIA card with CUDA 12 and about 0.8 GiB + 18 GiB per million solved cells of free memory (25 GiB per million for meshes up to 0.5 million cells; a 24 GB card holds about 1.2 million cells), see [GPU air solver](#gpu-air-solver-optional). The liquid-cooling DAE solver runs on CPU in float64 by default; CUDA gives the same numbers and is optional. The Omniverse display needs one NVIDIA GPU with NVENC and Vulkan. |

Use a checkout path without spaces: OpenFOAM's build options do not quote include paths.

---

## Step 1: Clone the Repository

```bash
git clone https://github.com/Litianyu141/dcdtuni.git dcdtuni
cd dcdtuni
```

---

## Step 2: Run the Bootstrap

`tools/bootstrap_dcdtuni.py` is the supported installer. It only prepares the checkout; it never starts a service.

```bash
python3 tools/bootstrap_dcdtuni.py --dry-run     # print the planned actions, change nothing
python3 tools/bootstrap_dcdtuni.py --install     # install and build
python3 tools/bootstrap_dcdtuni.py --preflight   # read-only check of the result
```

`--install` does the following, in order:

1. Pulls the Git LFS images the editor needs and checks each against its tracked identity.
2. Runs `setup_env.sh`, which creates a Conda environment **inside the checkout** at `.codex-tmp/envs/dcdtuni` (CPython 3.10, OpenFOAM 2412, MPICH, GMP from conda-forge) and installs the `backend` package editable from `./src` with that environment's own `pip`.
3. Requires the liquid-cooling dependencies (`torch`, `networkx`) to import. If the hardware-specific torch install fails, install a suitable torch build into the printed environment and rerun; `--air-only` deliberately skips this check. The same step installs the packages of the AI solve and of the GPU air solver (best effort; see [GPU air solver](#gpu-air-solver-optional)).
4. Runs `npm ci` and builds the React editor (`src/frontend/react/dist`).
5. Builds the two native trees: `dcCartMesh` (the default mesh engine) and the repository solvers `buoyantBoussinesqSimpleFoam_new` / `buoyantBoussinesqPimpleFoam_new` / `writeExplicitBC`. Binaries land in each tree's `platforms/` directory; the conda OpenFOAM is untouched.
6. Writes a build receipt that `--preflight` checks again later.

Add `--ov` to all three commands to also fetch and build the public NVIDIA Kit-CAE runtime for the Omniverse display (see [Fresh clone deployment](../developer/fresh_clone_deploy.md#add-the-public-ov-runtime)).

!!! note "Why `pip`, not `poetry install`?"
    Dependencies are installed with the environment's own `python -m pip install -e .`. Poetry does not understand Conda environments and would install into the base interpreter. `pyproject.toml` still uses `poetry-core` as its build backend, so the declared dependencies are the same.

### Which Python environment the tools use

- The launcher (`src/start_server.sh`) uses `.codex-tmp/envs/dcdtuni` when it exists. On machines that were set up earlier with a **named** Conda environment `dcdtuni`, the launcher falls back to it.
- To run Python yourself (tests, small scripts), use the same environment: `.codex-tmp/envs/dcdtuni/bin/python …`, or `conda run -p .codex-tmp/envs/dcdtuni python …`. On an older machine with the named environment, `conda activate dcdtuni` / `conda run -n dcdtuni` works as before.
- `DCDTUNI_ENV_PREFIX` (a path inside the checkout ending in `dcdtuni`) selects a different repository environment for both the bootstrap and the launcher.

### Without the bootstrap

The bootstrap is a thin, checked wrapper around these steps, which also work by hand:

```bash
./setup_env.sh                                                         # repository environment + backend
(cd src/backend/aircooling/third_parties/foam && ./deploy.sh)          # *_new solvers
(cd src/backend/aircooling/third_parties/dcCartMesh && ./deploy.sh)    # dcCartMesh
(cd src/frontend/react && npm ci && npm run build)                     # React editor
```

---

### GPU air solver (optional)

The steady air solve has a second engine, a GPU Newton-Krylov solver that lives in the repository
(`src/backend/aircooling/gpu_solver`) and runs in the same Conda environment as everything else. It is offered only
by a launch with `--gpu` (see [the launcher options](../../README.md)); OpenFOAM stays the default engine.

| What | Requirement |
|---|---|
| Card | NVIDIA, CUDA 12 runtime (the `cu128` torch build brings its own libraries; the driver must support CUDA 12.8) |
| Memory | about 0.8 GiB + 18 GiB per million solved cells (25 GiB per million for meshes up to 0.5 million cells), free on ONE card (a solve uses one card). Measured on halls of 0.10-1.16 million cells: a 24 GB card holds about 1.2 million; by the same estimate, not yet measured, a 48 GB card about 2.6 million and a 96 GB card about 5 million |
| Python packages | `torch 2.7.1` (`cu128`), `torch-scatter 2.1.2`, `scipy`, `torch-geometric 2.8.0`, `warp-lang 1.18.0`, `pyamg >=5,<6`, `h5py >=3.9`, `tomli` (Python 3.10): the `gpu-solver` extra of `pyproject.toml` |

`setup_env.sh` installs these packages together with the liquid-cooling and AI ones. Into an existing environment:

```bash
conda run -n dcdtuni python -m pip install torch==2.7.1 --index-url https://download.pytorch.org/whl/cu128
conda run -n dcdtuni python -m pip install torch-scatter==2.1.2 -f https://data.pyg.org/whl/torch-2.7.0+cu128.html
conda run -n dcdtuni python -m pip install "scipy>=1.10,<2.0" torch-geometric==2.8.0 warp-lang==1.18.0 "pyamg>=5.0,<6" "h5py>=3.9" "tomli>=2.0"
conda run -n dcdtuni python -c "import torch, warp, pyamg, torch_geometric, h5py, scipy; print(torch.__version__, torch.cuda.is_available())"
```

`start_server.sh --gpu` checks the six packages at start. When one is missing the launcher still starts, without
the GPU solver, and its banner names the package. While the GPU engine is selected the solve page shows every card
of the launch with its memory and utilisation; a solve that does not fit its card ends with an out-of-memory error
that asks for a restart of `start_server.sh` with more cards (or cards with more free memory) after `--gpu`.

## Step 3: Start the Server

`src/start_server.sh` is the only way to start the web frontends. With no arguments it serves the React editor and the API on one port, **8000**:

```bash
./src/start_server.sh
```

The banner reports the mode and the ports it actually used, for example:

```text
=========================================
  模式        : react (生产 / dist)
  浏览器打开  : http://localhost:8000/
  后端        : http://localhost:8000  (WS 8001)
=========================================
```

Open the address on the "浏览器打开" line in your browser.

### Logging in

The server has no built-in password. The launcher passes administrator credentials to the server in this order:

1. the environment pair `DCDTUNI_ADMIN_USER` / `DCDTUNI_ADMIN_PASSWORD`;
2. `--admin-credentials <file>`: an owned, mode-0600 file with the username on the first line and the password on the second;
3. otherwise **local mode**: on first start the launcher creates `src/server/auth-local/admin_credentials` (username `admin`, a random password, mode 0600, ignored by Git) and reuses it afterwards.

In local mode, log in as `admin` with the password on the second line of that file:

```bash
sed -n 2p src/server/auth-local/admin_credentials
```

For a shared or LAN deployment, pass the environment pair or `--admin-credentials` instead.

### Ports and other options

```bash
./src/start_server.sh --port 9000                 # UI and API on 9000; WebSocket defaults to 9001
./src/start_server.sh --host 0.0.0.0              # listen on all interfaces (default: 127.0.0.1 only)
./src/start_server.sh --build                     # force a rebuild of the React editor (a stale dist/ is rebuilt anyway)
./src/start_server.sh --app html                  # the legacy single-file editor (unmaintained) at /STL_generate.html
./src/start_server.sh --app react --ov --gpu 0    # React editor plus the Omniverse display on one GPU
./src/start_server.sh --case-dir /abs/path/case   # remaining arguments go to server.py
```

If a port is held by **another user's** process, the launcher refuses and asks for a different `--port`; it frees only ports held by your own processes. Each launcher process holds one global case directory, so run one launcher per person or per parallel task, on different ports. `./src/start_server.sh --help` prints the full option list.

---

## Step 4: Verify

```bash
# The backend package imports (use the repository environment's Python)
.codex-tmp/envs/dcdtuni/bin/python -c "import backend; print(backend.__version__)"

# The server answers; /api/* needs a session, so log in first
CRED=src/server/auth-local/admin_credentials
curl -s -c _tmp/cookie.txt -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"$(sed -n 1p $CRED)\",\"password\":\"$(sed -n 2p $CRED)\"}"
curl -s -b _tmp/cookie.txt http://localhost:8000/api/server-info
```

A healthy response contains `"dctwinAvailable": true`, the version, the Python path, the case directory (`caseDirOk` turns true once a case is selected), `"app": "react"` and whether the Omniverse bridge is enabled. Without a session cookie `/api/*` answers HTTP 401.

Then open a tracked project from `case/` (for example `case/Base_leakage/Base_leakage.cfdproj`) through **📂 Load Project** and check that the scene and textures appear. The [Quick Start](quick-start.md) continues from here to a mesh and a solve.

---

## Container Images

DCTwin does not provide a maintained Docker image. The old root-level Dockerfiles were removed because they referenced obsolete run scripts. If you need a container, build one from a current OpenFOAM base image and run the bootstrap inside it.

---

## Troubleshooting

### `mpirun` appears to hang during parallel mesh or solve

`mpirun` can block when launched from an interactive terminal. Trigger mesh and solve through the editor or the REST API (or the [scripted API](../reference/scripted-api.md)); the runner launches `mpirun -np N <tool> -parallel` itself. Do not run `mpirun` by hand for testing.

```bash
curl -s -b _tmp/cookie.txt -X POST http://localhost:8000/api/run-mesh \
  -H "Content-Type: application/json" -d '{"meshOnly": true}'
```

### `ModuleNotFoundError: No module named 'backend'`

You are using a Python outside the repository environment. Use `.codex-tmp/envs/dcdtuni/bin/python` (or the named `dcdtuni` environment on an older machine). If the import still fails, rerun `python3 tools/bootstrap_dcdtuni.py --install`.

!!! note "`import dcdtuni` always fails"
    The project is called `dcdtuni` in `pyproject.toml`, but the importable package is `backend` (`packages = [{include = "backend", from = "src"}]`).

### Port conflicts

The launcher frees ports held by your own processes and refuses ports held by others. To stop your own server from another terminal:

```bash
fuser -k 8000/tcp 8001/tcp
```

!!! danger "Do not use `kill`, `pkill` or `killall`"
    On shared servers they can terminate other users' processes, including VS Code Remote Server and JupyterLab. Only use `fuser -k <port>/tcp`.

### `buoyantBoussinesqSimpleFoam_new is not built`

The runner prefers the repository's own solvers and does not silently fall back to a stock solver for transient runs. Build them with `(cd src/backend/aircooling/third_parties/foam && ./deploy.sh)` or rerun the bootstrap. Details: `src/backend/aircooling/third_parties/foam/README.md`.

### OpenFOAM not found, or library mismatches

OpenFOAM comes from the repository environment. The launcher removes any system OpenFOAM and system Open MPI entries from `PATH` and `LD_LIBRARY_PATH` before starting the server, so start it through the launcher. Do not source a system OpenFOAM (`/usr/lib/openfoam/...`) in the same shell for manual OpenFOAM commands.
