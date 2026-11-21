> **Most deployments need only the fresh-clone path.** The shared React editor with
> Three.js/Omniverse switching is set up by [fresh-clone deployment](../../../../docs/developer/fresh_clone_deploy.md)
> and `python3 tools/bootstrap_dcdtuni.py --install --ov`, then opened with
> `./src/start_server.sh --app react --ov --gpu 0 --build`. Main React needs Node 24.12+ (npm 10+) and
> the repository-contained environment. Ordinary `.cfdproj` geometry and solved runs need nothing below.
> This page is the extra, restricted procedure for the GTC demo scene, its pre-baked volume and the
> SCANO AI tab. Keep any new archive or temporary output inside the checkout.

# Deploying the GTC demo assets on another server

Two channels, because git cannot carry the whole thing:

| Channel | What | Size |
|---|---|---|
| `git clone` | all code — web UI, Kit extension, scripts, SCANO source, project page | 289 MB (`.git`) |
| out-of-band (Drive / rsync / scp) | 4 binary assets git does not track | **1.57 GB** |
| rebuilt on the far end | kit-cae, node_modules, the Python environment | — |

Nothing needs to be committed first. The code is already on `origin/main`.

## 1. On this machine — build the asset bundle

```bash
cd src/frontend/omniverse/cfd_demo
scripts/pack_deploy_bundle.sh                      # dry run: prints the manifest
scripts/pack_deploy_bundle.sh ../../../../.codex-tmp/gtc_demo_assets.tar.zst
```

The bundle holds the four things that cannot be regenerated on the far end:

- **`src/backend/kit-cae/data/GTC/`, 616 MB** — the scene geometry (USDZ + textures). This is
  our own asset: `git ls-files data/GTC` inside the kit-cae clone returns nothing, so
  cloning NVIDIA's public repo does **not** supply it.
- **`cfd_demo/data/internal.vtu`, 171 MB** — the pre-baked CFD volume. `convert_gtc_cfd.sh`
  can rebuild it, but only from a fully solved OpenFOAM case, which is much larger than
  the `.vtu`. Shipping the result is cheaper than shipping its input.
- **`SCANO/GTC_A3_Numerical/logger/model/best.state`, 99 MB** — trained weights (git-ignored).
- **`dataset/case0`, 687 MB of 939 MB** — the AI worker's base case. `bc_synth` overlays the
  UI's sparse boundary conditions onto this case's `boundary_conditions.json`, and the
  one-step solve runs on its mesh. The packer drops `VTK/`, `500/` and `_1_bak/` (253 MB
  of training-era leftovers nothing here opens).

Paths inside the archive are repo-relative, so the far end untars at the repo root and
every file lands where `start_demo.sh` already looks.

> The archive contains the un-anonymised case name `GTC` and the SCANO checkpoint. The
> paper is under double-blind review — share the Drive link **restricted**, never "anyone
> with the link". See the anonymity notes in `project_page/README.md`.

## 2. On the target server

```bash
# a. code — the repo is PRIVATE, so this needs a deploy key or a PAT
git clone git@github.com:<owner>/dcdtuni.git && cd dcdtuni

# b. kit-cae — NVIDIA's public repo, pinned to v2.0.1 (Kit 109), built in place.
#    Clone it BEFORE step c: the bundle writes into src/backend/kit-cae/data/GTC, and git
#    refuses to clone into a non-empty directory. The bootstrap does the clone, the build,
#    the optional vtk download and the relative-link rewrite in one step:
python3 tools/bootstrap_dcdtuni.py --install --ov
#    Without vtk (from the build or DC_CFD_VTK_SITE), omni.cae.delegate.vtk logs
#    "VTK packages not available" and no .vtu/.vti result loads.

# c. assets
tar -I zstd -xf gtc_demo_assets.tar.zst -C .

# d. reference web UI dependencies (the bootstrap installs the main React app's)
(cd src/frontend/omniverse/cfd_demo/web && npm install)
```

`GB300.usd` — the hero racks — comes from NVIDIA's public `kit_cae_user_guide_data`
download, which the bundle does not carry. It is optional: `scene.py` checks the asset
exists and, if not, prints `GB300 asset missing …; GTC racks only` and builds the scene
from the GTC racks alone. Point `DC_CFD_RACK_USD` at the file once the download is in place.

## 3. Start

```bash
./src/start_server.sh --app omniverse --port 8081               # reference UI on :8081, Kit on GPU 0
./src/start_server.sh --app omniverse --port 8081 --gpu 2       # another card
```

The launcher runs `scripts/start_demo.sh` under its supervisor, waits for Kit, then
serves the UI on `--port`. Each Kit gets a runtime directory
`.codex-tmp/ov-runtime/<signal-port>.XXXXXX/` with `kit.log` and `launcher.log`.

Everything machine-specific is resolved at runtime. Override only what the far end needs:

| Variable / flag | Default | Use when |
|---|---|---|
| `--ov-gpu`, or the first card of `--gpu` (`DC_CFD_GPU`) | `0` | a different physical card should be used; CUDA is pinned by UUID and Vulkan's device is resolved to the same card. `--gpu` also names the cards of the GPU air solver and the AI worker (launcher `--help`) |
| `--ov-port` (`DC_CFD_SIGNAL_PORT`) | `8301` | another Kit already signals on 8301 |
| `--ov-stream-port` (`DC_CFD_STREAM_PORT`) | `47998` | another Kit already uses that UDP video port |
| `DC_CFD_KIT_BUILD` | `src/backend/kit-cae/_build/linux-x86_64/release` | the kit-cae build lives elsewhere |
| `DC_CFD_ASSET_ROOT` | the checkout | a worktree borrows built Kit and assets from another checkout |
| `DC_CFD_VTK_SITE` | probed via `python3.10 -c 'import site'` | vtk is installed somewhere unusual |
| `DC_CFD_RACK_USD`, `DC_CFD_GB300_COUNT` | the kit-cae GB300 example, `8` | more or fewer photoreal hero racks (more is much slower to start) |
| `DC_CFD_STARTUP_TIMEOUT` | `300` s | a slow first start (shader compilation) |
| `DC_CFD_GTC_DATASET` | `/mnt/lv/home/Public/GTC/processed_dataset_unconverged` | the dataset sits elsewhere (only the generator scripts need it) |

`DC_CFD_AI` must stay `0`: this launcher no longer starts the AI GPU worker and refuses
any other value.

### The AI tab (optional)

The SCANO prediction tab talks to a separate HTTP worker,
`SCANO/GTC_A3_Numerical/scripts/ai_worker.py` (port `DC_CFD_AI_PORT`, default 8302;
cards `DC_CFD_AI_GPUS`, default 0). Since 2026-10-04 no launcher starts it, so the tab is
offline under `start_server.sh`. To use it, start the worker yourself with a Python
3.11 + torch environment on a card other than Kit's, before opening the UI. Next step:
an explicit, separately-pinned AI-worker option in the launcher, and a tab message that
names it.

## 4. Verify

```bash
KIT_LOG=$(ls -d .codex-tmp/ov-runtime/8301.* | tail -1)/kit.log \
    python3 src/frontend/omniverse/cfd_demo/scripts/head_verify_stream_ok.py   # stream reaches a browser
```

It runs a real head browser on Xvfb `:99` with software H.264 decode against
`http://localhost:8081`. Do **not** use headless `browser-use` here — it renders the
WebRTC viewport black and takes a GPU slot. `head_verify_ai_flow.py` additionally needs
the AI worker from the section above.

## Ports

The UI is `--port` (8081 in the examples, launcher default 8000), signalling is
`--ov-port` (default 8301) and video is UDP `--ov-stream-port` (default 47998). Forward
the two TCP ports over SSH; the UDP video needs a direct path to the host:

```bash
ssh -L 8081:localhost:8081 -L 8301:localhost:8301 <user>@<host>
```

The reference UI dials 8301 unless the URL carries `?signalingPort=<port>`, so add that
when you choose another `--ov-port`.

The Kit streamer serves **one** client at a time. A black viewport plus an `NVST_R_BUSY`
flood in `kit.log` means the slot is held elsewhere; close the other browser, or stop the
launcher and start it again.

## Troubleshooting

**`kit-cae wrapper is missing or not executable`** (in `launcher.log` / `kit.failure`) —
step 2b was skipped or its build failed. The supervisor checks this before it starts
Xvfb or Kit, so nothing is left running. Run the bootstrap with `--ov` again
(`DC_CFD_ASSET_ROOT` swaps the whole asset root, not just the Kit location; use
`DC_CFD_KIT_BUILD` to point at another build).

**`[start_demo] WARNING: GTC scene assets missing`** or **`note: optional pre-baked result
… absent`** — the asset bundle (step 2c) is not extracted. The backend still starts and
`.cfdproj` geometry still loads; only the GTC scene and the pre-baked volume are missing.
