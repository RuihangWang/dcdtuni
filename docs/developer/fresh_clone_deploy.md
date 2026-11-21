# Fresh Linux clone: React, native solvers and optional OV

The Linux machine runs the server and CFD/Kit processes. Windows can open the React UI in its browser. All product controls stay in the same React UI; Three.js and OV are renderer choices. The normal page is `http://localhost:8000/`.

## Prerequisites

Install Python 3.10 or newer, Git with Git LFS, Conda, a C++ compiler, Node **24.12.0 or later in the Node 24 line**, and npm 10 or later. The bootstrap checks actual versions; npm's `engines` warning alone is insufficient. The tracked lock includes a Rollup Linux x64 optional dependency whose floor is Node 24.12, beyond jsdom's Node 24.0 floor. Keep development and optional packages: Vite and TypeScript are build dependencies.

Use a checkout and environment prefix without whitespace: the OpenFOAM Make options use unquoted include paths. Set `WM_NCOMPPROCS=2` when running bootstrap to limit native build parallelism; CPU affinity can be applied to the bootstrap process as needed.

The bootstrap creates its environment at `.codex-tmp/envs/dcdtuni` inside this clone, rather than a global named environment. Existing launcher/build interfaces also accept `DCDTUNI_ENV_PREFIX` for a different location **inside this checkout**, ending in `dcdtuni`. To use a custom prefix consistently, export that variable for bootstrap and launch, or pass bootstrap's matching `--env-prefix` and then export the same path before launch. An already installed legacy dcdtuni environment remains a launcher fallback, but clone acceptance uses the repository environment. If a non-interactive shell has not activated that legacy environment, expose `conda` on `PATH` or set its standard `CONDA_EXE` variable to the installed executable. The launcher reports missing or failing Conda discovery with an actionable error. Bootstrap itself still requires `conda` on `PATH`; no environment is installed automatically by the launcher.

Bootstrap gives supported Conda/pip/npm/Packman/XDG-cache settings checkout-local destinations. For Kit it prepares `.codex-tmp/bootstrap-cache/omniverse-config/omniverse.toml` and sets `OMNI_CONFIG_PATH`; its data/cache/log roots remain in this checkout, including global extension-registry storage. The React `--ov` launcher uses the same configuration when started independently. Existing differing contents at that owned config path are preserved and reported for review. Conda registration is disabled using its supported register_envs setting so the new prefix need not enter a global environment registry. No HOME variable is repurposed. Third-party tools may still maintain system metadata outside these cache controls. The supported new environment, product/vendor directories, bootstrap cache/temp files and generated build outputs must stay within the checkout. Existing environments/vendor sources are preserved; nothing is silently reset.

For OV also install a supported NVIDIA driver with NVENC/Vulkan, Xvfb and vulkaninfo. Read-only preflight checks tool presence, not GPU operation. The existing launcher performs its supervised GPU/display checks when started. Building Kit does not certify decoded WebRTC video.

## Prepare the clone

```bash
git clone https://github.com/Litianyu141/dcdtuni.git
cd dcdtuni
python3 tools/bootstrap_dcdtuni.py --dry-run
python3 tools/bootstrap_dcdtuni.py --install
python3 tools/bootstrap_dcdtuni.py --preflight
./src/start_server.sh --app react --build
```

Open `http://localhost:8000/`. `--port 9000` changes the browser port; the backend WebSocket defaults to the next port. Stop the launcher with Ctrl+C.

Dry-run prints actions without installing or building. (The former third mesh engine, MeshGenerator, was retired on 2026-10-26 and is no longer built; see [Mesh engines](../reference/mesh-engines.md).) Git LFS filters are configured locally with `--skip-repo`, preserving existing pre-push hooks. Installation performs a targeted Git LFS pull for shared default images and validates each against its tracked Git/LFS content identity; it then runs Python setup with pinned OpenFOAM2412, requires liquid imports, runs `npm ci --include=dev --include=optional`, builds React, and builds both repository native trees (dcCart, the solvers). The deploy scripts use `--clean --no-test`: only their generated platforms/Make outputs are rebuilt, and installation does not run CFD smoke cases. A source snapshot is taken before and after each build, and a receipt binds unchanged source files, OpenFOAM2412/DP context, environment prefix and produced APP/LIB hashes. Preflight reopens that receipt and the current inputs/outputs; it refuses a changed or missing binding. This build identity still differs from strict mesh/solver acceptance.

The default requires torch/networkx for liquid solving. If the hardware-specific torch installation fails, bootstrap stops at the explicit liquid import check; install the appropriate torch build into the displayed repository environment prefix, then rerun. `--air-only` permits a deliberately air-only installation and does not claim liquid capabilities.

The same best-effort step installs the packages of the GPU air solver (`gpu-solver` extra: warp-lang 1.18.0, pyamg, h5py, tomli, next to torch 2.7.1/cu128, scipy and torch-geometric 2.8.0). The solver's code is in the repository (`src/backend/aircooling/gpu_solver`, checked against its `VENDORED.json` by `python tools/vendor_gpu_solver.py --check`); nothing else is downloaded or built for it. `start_server.sh --gpu` checks the six imports and starts without the solver, naming the missing package in its banner, when one fails. An existing environment is brought up to date with the three commands of [installation.md, GPU air solver](../user-guide/installation.md#gpu-air-solver-optional).

Setup uses conda-forge with `--override-channels`, pinning CPython 3.10 and its `python_abi=3.10=*_cp310` alongside openfoam=2412 and MPICH. The explicit ABI prevents an OpenFOAM dependency solve from substituting GraalPy. Setup checks the actual CPython implementation, environment prefix and OpenSSL backend before pip; preflight repeats these checks. Python dependencies follow pyproject.toml through pip's editable installation; this entry does not claim pip consumes poetry.lock or that every Python/OS package is locked. React uses its tracked npm lock. Downloads require access to conda-forge, PyPI/PyTorch, NVIDIA's npm registry and Git LFS.

## Add the public OV runtime

```bash
python3 tools/bootstrap_dcdtuni.py --dry-run --ov
python3 tools/bootstrap_dcdtuni.py --install --ov
python3 tools/bootstrap_dcdtuni.py --preflight --ov
./src/start_server.sh --app react --ov --gpu 0 --build
```

Bootstrap clones public NVIDIA Kit-CAE v2.0.1, verifies exact commit b79bec14f509c30fbf2bc65520e6e2efced9f06f, then follows that tag's official `./repo.sh build -r` and `./repo.sh pip_download`. The latter provides optional VTK packages needed by display/data delegates. The build includes Kit extension precaching; it does not start the product server or a persistent streaming service. The vendor checkout is src/backend/kit-cae; it is downloaded/rebuilt, not vendored into this Git repository. Existing tracked vendor edits are refused and preserved for review. Bootstrap does not apply historical server-specific site-packages paths, GPU3 settings or vendor patches. Its Packman cache and Kit extension data root are inside the Kit checkout (`src/backend/kit-cae/_cache/`), and after `pip_download` bootstrap rewrites every absolute link of the build as a relative one, so the checkout is relocatable; the Kit-output check refuses absolute links and links leaving the Kit checkout.

Kit global paths follow [NVIDIA Kit Tokens documentation](https://docs.omniverse.nvidia.com/kit/docs/kit-manual/latest/guide/tokens.html).

Primary instructions: [NVIDIA v2.0.1 Build.md](https://github.com/NVIDIA-Omniverse/kit-cae/blob/v2.0.1/docs/Build.md), [NVIDIA v2.0.1 source](https://github.com/NVIDIA-Omniverse/kit-cae/tree/v2.0.1), [npm ci](https://docs.npmjs.com/cli/v10/commands/npm-ci/).

The bridge declares its Kit dependencies (including VTK and NanoVDB delegates; public VTK delegate metadata brings in vtk_libs) and the launcher supplies GPU/display settings, including explicit glInterop=false. No upstream license/source header is changed. Verify the installed public runtime using the browser checks below.

GPU0 is default. Choose another physical GPU number deliberately when needed. `--ov-port 8302 --ov-stream-port 47999` gives another Kit independent signaling/video ports. The browser must reach backend HTTP and OV signaling, plus the backend WebSocket port when the optional admin terminal is enabled (`--enable-terminal`). With SSH forwarding, forward those TCP endpoints; WebRTC media also needs UDP connectivity under the deployment network. A listening signaling socket does not prove working video.

For clone acceptance leave DC_CFD_ASSET_ROOT and DC_CFD_KIT_BUILD unset so the launcher uses this checkout's public Kit build. Do not inherit experimental DCCART flags from debugging. Bootstrap does not define/enable the 32 experimental flags. Native build identity and runtime mesh quality are checked separately.

## Runtime acceptance after installation

1. Start React, open a tracked project and confirm default textures load.
2. In that same UI switch Three.js to OV and back. Confirm decoded video, scene acknowledgment and project/camera continuity; test common visibility/grid/mesh/post-processing. Preflight/readiness alone is insufficient.
3. Build dcCart from this checkout's final reviewed own source; generate the real target mesh and pass full strict checkMesh (the 5-minute mesh time is a soft target since 2026-10-20: measured and logged, not enforced). Preserve source/build binding and actual logs.
4. Run intended steady/transient air and liquid/DAE workflows with numerical/MMS/gradient acceptance. Installation and small source controls cannot certify them.

Bootstrap refuses borrowed platforms output and changed or missing native build receipts. Mesh quality and renderer operation require the runtime checks above.

## Git LFS when contributing new assets

Bootstrap preserves an existing custom pre-push hook and does not replace it. Before pushing new LFS assets, ensure that hook invokes `git lfs pre-push "$@"`. Use `git lfs update --manual` for integration instructions when preserving project-specific hook logic. If there is no pre-push hook, `git lfs update` can install the standard hook; first inspect `git config --get core.hooksPath` so you know its destination. These are contributor actions, separate from bootstrap downloading the tracked default assets. Do not use `--force` to replace an existing user hook.

## Optional private historical demo

Normal .cfdproj geometry/display does not need private GTC data, trained weights, pre-baked internal.vtu or the old GTC USD scene. The launcher warns when optional assets are absent; bootstrap does not copy them. The old standalone demo's restricted out-of-band procedure remains in [cfd_demo/DEPLOY.md](../../src/frontend/omniverse/cfd_demo/DEPLOY.md). It is separate from the main React/OV installation.

## Projects in a fresh clone

A `.cfdproj` saves its case directory (`meta.casePath`) relative to the repository when the directory lies inside it (`case/Tri_angle`), so the projects under `case/` open in any clone at any path. The server resolves a relative path against its repository root (never its working directory) and reports the portable form back; the editor stores that form. A path that does not exist on this machine — a project made elsewhere, or a directory that moved — opens a prompt to choose the case directory again (the server file browser starts at the repository's `case/`), or it can be set later on the System tab.
