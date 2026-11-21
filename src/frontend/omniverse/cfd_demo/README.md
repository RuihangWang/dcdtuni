# Omniverse CFD — Kit bridge and reference demo

This directory holds three things:

- **`kit/extensions/dc.cfd_bridge/`** — the Kit extension that turns browser
  commands into USD changes. Both Omniverse paths below use it.
- **`scripts/start_demo.sh` + `scripts/supervise_kit.py`** — the supervised,
  single-GPU Kit launcher. `src/start_server.sh` calls it; do not start it by
  hand.
- **`web/`** — the reference demo UI for the GTC data-centre scene (volume
  rendering, ROI, streamlines, the AI tab), served on its own port.

Since the 2026-09-29 display decision, the **main React editor is the product UI
for Omniverse**: `--ov` adds a Kit display bridge beside Three.js, and the
editor switches between the two. The reference UI here stays for the GTC demo
and for checking Kit features in isolation.

## Status (2026-10-22)

Working, each with a browser receipt under `docs/benchmarks/`:

- Materials: the bridge and the Kit-native `.cfdproj` path read the editors' own
  manifest `src/frontend/shared/textures/materials.json`; textures arrive by pool
  reference and Kit binds the same files (`materials-shared-pool-2026-10-21`).
- React ↔ Omniverse switching for air and liquid projects, with scene ACKs on
  large scenes (`ov-display-browser-e2e-2026-09-30`, `ov-switch-fix-2026-10-01`,
  `ov-switch-lease-2026-10-06`); the OV button waits until Kit is ready
  (`6fc7d13c3`).
- Post-processing inside Kit with kit-cae's own tools — boundary-surface
  fields, IndeX volume, plane slices and GPU streamlines (`729e19980`, verified
  on DAML-Testbed). The server converts a solved run with `foamToEnsight`,
  signs a display grant, and Kit loads only granted data.
- Whole-object Shift picking in the OV viewport and the camera handoff back to
  Three.js (`ov-source-pick-owned-20261007`, `ov-camera-handoff-normal-20261008`).
- The reference UI's full chain: GTC scene, T/|U| volume, ROI box, clip and
  extraction, streamlines with a draggable seed sphere, `.cfdproj` geometry,
  runtime dataset loading (`ov-display-baseline-2026-09-29`).

Next steps (each a receipt to add):

- Head-browser receipt for the liquid result in Kit: colouring in the quantity
  chosen in the editor (`9757bd000`) and the flow arrows on the pipework
  (`dbeaf46a2`, `setLiquidFrame` with an arrows block). Both are implemented
  and unit-tested; the frame ACK reports how many arrows Kit drew.

- OV post-processing receipts on larger cases (Tri_angle, Base604, Shinaide);
  only T and |U| are mapped to Kit fields so far, and several slices or
  streamline sets show the first visible one.
- Read viewport drags (slice disc, seed sphere) back into the shared panel —
  Kit already answers `getSliceState`; the React native path does not call it yet.
- Large scenes: content-hashed texture reuse and a geometry-fingerprint scene key,
  so an edit stops resending the whole scene.
- Reference UI under the launcher: pass `DC_CFD_RUNTIME_DIR` and the signalling
  port to Vite (see "Run the reference UI").

## Run

Everything starts through `src/start_server.sh`. One Kit uses exactly one GPU
(default GPU 0, pinned by UUID; Vulkan's device is resolved to the same card by
`scripts/resolve_vulkan_gpu.py`) and serves one WebRTC client at a time.

**Main path — React editor with the Omniverse display:**

```bash
./src/start_server.sh --app react --ov --gpu 0 --build
./src/start_server.sh --app react --ov --gpu 3 --port 8064 --ov-port 8302 --ov-stream-port 47999
```

Open the `--port`; the OV button beside the renderer turns ready once Kit is up
(about two minutes on a cold start). A second Kit on the same host needs its own
`--gpu`, `--ov-port` (signalling, default 8301) and `--ov-stream-port` (UDP
video, default 47998).

**Run the reference UI:**

```bash
./src/start_server.sh --app omniverse --port 8081      # Kit + Vite UI on :8081
```

`--port` is the UI port (the launcher default is 8000; 8081 is what the head-browser
scripts and the project page expect). The UI dials signalling port 8301 unless
the URL says otherwise, so with `--ov-port P` open `http://<host>:8081/?signalingPort=P`.
The launcher does not yet hand the Kit runtime directory to Vite, so the UI's
side channels (scene tree, dataset info, Kit log panel) read
`.codex-tmp/ov-bridge-metadata/` instead of the live Kit's files.

**Logs and stopping.** Each Kit gets a runtime directory
`.codex-tmp/ov-runtime/<signal-port>.XXXXXX/` holding `kit.log`,
`launcher.log`, `scene-tree.json` and `dataset-info.json`. Stop with Ctrl+C in
the launcher terminal, or `fuser -k <port>/tcp <ov-port>/tcp` — never
`kill`/`pkill`.

**Remote access.** Forward the UI port, the backend WebSocket (for `--app react`)
and the signalling port, e.g.
`ssh -L 8081:localhost:8081 -L 8301:localhost:8301 <user>@<host>`. The UDP video
needs a direct network path to the host.

`scripts/dc_ov_demo.sh` is the pre-launcher process manager: it starts Kit and
Vite by hand, defaults to another GPU and tails `/tmp/dc_cfd_demo.kit.log`,
which nothing writes any more. Use the launcher instead.

## What the reference UI does

The left panel has two tabs — **Geometry** (model tree) and **Post-processing**
(dataset + field viz); the right panel is the **Inspector**.

- **Load** the GTC scene, or a `.cfdproj` through the file dialog.
- **Volume render** the CFD field as **Temperature** or **Velocity (|U|)**, with
  Low/High threshold sliders (colormap domain, applied at render time).
- **Load a new dataset at runtime**: a preset or a server-side path. Native VTK
  formats (.vtu/.vtp/.vtk/.vti/.vts) load directly; **.vtm** (foamToVTK
  multiblock) is converted to .vtu automatically. The dataset needs `T` and/or
  `U` to colour.
- **Region of interest**: a draggable box; **Clip** limits what the voxeliser
  reads, **Extract** cuts the ROI's cells out and makes them the dataset.
- **Streamlines** from a draggable seed sphere, coloured by T or |U|, confined
  to the ROI when clipping is on. **Sync from viewport** pulls a gizmo drag of
  the box or the sphere back into the render and the panel.
- **Model tree / Inspector**: show, hide, select, focus and set the opacity of
  each geometry group. The rack/ACU power and flow fields there are display-only
  mock values; they do not feed any solve.
- **AI tab** (SCANO prediction): needs the separate AI worker, which the
  launcher does not start — see `DEPLOY.md`. Without it the tab stays offline.

## Architecture

```
 Browser UI (React)                           Omniverse Kit (kit-cae, headless, 1 GPU)
 ┌──────────────────────┐   WebRTC video ◀─── ┌─────────────────────────────────┐
 │ <video> viewport     │                     │ RTX render + NVIDIA IndeX volume │
 │ panels / inspector   │   sendMessage ───▶  │ omni.kit.livestream.receive_…    │
 └──────────────────────┘   (JSON command)    │ dc.cfd_bridge → USD mutation     │
                                              └─────────────────────────────────┘
```

- **Streaming**: kit-cae's `omni.cae_streaming.kit` runs headless on Xvfb `:99`;
  the browser receives video and sends commands through
  `@nvidia/omniverse-webrtc-streaming-library`.
- **Answers back**: the main React path gets a `renderAck` per command
  (request id, chunked scene transfer, see
  `docs/developer/render-scene-contract.md`). The reference UI reads Kit's
  state through files the bridge writes plus Vite endpoints
  (`/api/scene-tree`, `/api/dataset-info`, `/api/kit-log`), and parses log
  echoes for drag read-back.
- **`dc.cfd_bridge`** (`kit/extensions/dc.cfd_bridge/dc/cfd_bridge/`):
  `extension.py` routes commands, `parsers.py` turns JSON into typed arguments,
  `handlers.py` and `scene.py` change the stage (scene build, volume rebuilds,
  geometry groups, ROI, streamlines), `render_scene.py` builds USD from the
  editor's render-scene snapshot, `liquid_colour.py` colours liquid results.
- **Volume rebuilds**: kit-cae voxelises one field into a NanoVDB grid when the
  volume is created, so switching T↔U or toggling the ROI clip recreates the
  volume (rotating prim paths; the old one is retired with compositing off
  first). Threshold sliders only change the colormap domain.

## Command reference (reference UI: `web/src/streamMessages.ts` → `extension.py`)

| Command | Args | Effect |
|---|---|---|
| `loadScene` | `path` (optional) | build/reveal the scene from a scene JSON or `.cfdproj`; empty = `DC_CFD_JSON` |
| `resetScene` | — | clean slate: tear down dataset/volume/streamlines, hide geometry, grid and ROI (sent once per page load) |
| `setVolume` | `field` (T/U), `visible` | show the field volume (rebuild) or hide it |
| `setVolumeRange` | `min`, `max` | colormap domain (threshold sliders) |
| `setRoiVisible` | `visible` | show/hide the ROI box and select it for the gizmo |
| `setRoiBox` | `min[3]`, `max[3]` | move/resize the ROI to a world AABB |
| `setRoiClip` | `enabled` | clip the volume to the ROI (rebuild) |
| `extractRoi` | `format` (vtu/vti) | cut the ROI's cells out and make them the dataset |
| `syncFromViewport` | — | adopt a gizmo-dragged ROI box / seed sphere, re-render, echo the values |
| `setSeedSphere` | `visible`, `center`, `radius`, `nSeeds` | position the streamline seed preview |
| `setStreamlines` | `enabled`, `field`, `seedCenter`, `seedRadius`, `nSeeds`, `maxSteps`, `direction` | trace (or remove) streamlines on U, coloured by T or \|U\| |
| `setObjectVisible` | `name`, `visible` | show/hide a geometry group |
| `setObjectOpacity` | `name`, `opacity` | set a group's material opacity |
| `selectObject` | `name` | highlight a group ("" clears) |
| `focusObject` | `name` | frame the camera on a group |
| `loadDataset` | `path` | swap the CFD dataset (server-side path; .vtm converted) |

The main React path uses the acknowledged render commands instead
(`beginRenderScene` … `loadRenderScene`, `loadResultDataset`, `setSlice`,
`setVolumeDisplay`, `setSurfaceDisplay`, `loadLiquidResult`, `querySourcePick`,
…); see `react/src/renderer/ovProtocol.ts`.

## Prerequisites and data

Deploying on another server: see [DEPLOY.md](DEPLOY.md) and
[fresh-clone deployment](../../../../docs/developer/fresh_clone_deploy.md).
`tools/bootstrap_dcdtuni.py --install --ov` builds the pinned kit-cae v2.0.1 at
`src/backend/kit-cae`; the GTC scene, the pre-baked volume and the SCANO
checkpoint travel out of band.

`data/internal.vtu` is the GTC CFD result and `data/ranges.json` the per-field
min/max that seeds the colormap domains; regenerate with
`scripts/convert_gtc_cfd.sh` / `scripts/field_ranges.py`.

## Development notes

- **Bridge (`dc.cfd_bridge/*.py`) changes need a Kit restart**: stop the
  launcher and start it again, then reload the browser tab. Web changes
  hot-reload.
- **Tests** are Python, in `tests/`:
  `python -m pytest src/frontend/omniverse/cfd_demo/tests` from the repository
  root (the launcher-lifecycle tests start real processes and free their own
  ports with `fuser`). `web/` has no unit tests of its own.
- **Browser checks** use a real head browser on Xvfb `:99`
  (`scripts/head_verify_*.py`, Playwright `headless=False`, software decode),
  never headless browser-use, which renders the stream black and takes a GPU.
  The scripts target the reference UI at `http://localhost:8081` and read the
  Kit log from `KIT_LOG` — point it at the runtime directory's `kit.log`.
- Debug from the browser console: `window.cfd.<command>(…)` is wired in
  `main.tsx`.
- One WebRTC client at a time: a black viewport plus an `NVST_R_BUSY` flood in
  `kit.log` means another browser holds the slot; close it or restart Kit.
