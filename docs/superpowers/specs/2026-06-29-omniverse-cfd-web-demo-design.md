# Design Spec — Omniverse Web CFD Demo (Data-Center, GTC scene)

- **Date:** 2026-06-29
- **Status:** Approved direction; pending spec review
- **Owner:** litianyu
- **Location of new code:** `src/frontend/omniverse/cfd_demo/` (per repo convention: all Omniverse code under `src/frontend/omniverse/`)

---

## 1. Goal

A browser-based demo where:

1. A web page embeds an **Omniverse Kit viewport** (render-only; all UI is custom HTML/React, no native OV UI).
2. A button loads the **GTC data-center USD scene** (geometry: racks, ACUs, boxes).
3. The user **volume-renders the CFD result** as a **temperature (T)** or **velocity-magnitude (|U|)** field.
4. The user toggles a **Region-of-Interest (ROI)** box, **drags/resizes it in the viewport**, and the volume renders **only inside the ROI**.
5. The user **adjusts the colormap threshold** (scalar min/max) of the rendered field.
6. Every feature is **verified visually with `browser-use`** (programmatic check + screenshot read-back).

The CFD data is the user's own solved case at
`case/GTC/log/2026-06-20-05-50-44_GTC` (OpenFOAM `buoyantBoussinesqSimpleFoam`, time 100).

## 2. Decisions (locked)

| Decision | Choice | Rationale |
|---|---|---|
| Rendering/CAE backend | **Extend kit-cae** (`omni.cae_streaming.kit`, Kit SDK 109) | Already loads the GTC scene + renders a NanoVDB/IndeX CFD volume + has the exact ROI mechanism (simulation-in-context). NVIDIA-official viz. |
| Browser→Kit command transport | **WebRTC data channel**, blueprint pattern | User wants the blueprint's underlying implementation logic. Single connection; reuses the blueprint's React command layer and `manager`-style Kit dispatch. |
| CFD data source | **The user's own GTC results** (regenerate `internal.vtu` via `foamToVTK`) | Faithful to the actual solve; tooling verified present in env `dcdtuni`. |
| Web framework | **React + Vite + TypeScript**, forked from the blueprint web shell | Matches "参考 blueprint"; clean component model; the streaming SDK integration already exists there. |
| Web visual theme | **Match the existing dcdtuni frontend** (dark glassy panels, cyan/blue accents, mono readouts) while keeping the blueprint's component architecture | Product cohesion with `src/frontend/web/STL_generate.html`; the two reference aesthetics already align (dark glass + blue/cyan). |
| New Kit code | **One extension** `dc.cfd_bridge` | Minimal surface; everything else is reused kit-cae machinery. |

## 3. Architecture

```
Browser  (lean React + Vite app, Vite dev server :8081)
  ├─ <video>  ← WebRTC video (RTX-rendered frames)
  ├─ pointer/mouse → WebRTC native input  (nativeTouchEvents:true → drives the ROI gizmo)
  └─ commands → WebRTC data channel
        AppStreamer.sendMessage({ event_type:"send_message_from_event",
                                  payload:{ command_name, message } })
        (message = JSON.stringify(args); only command_name+message survive the bridge)
        │
        ▼  WebRTC signaling :8301
Kit-CAE streaming app  (omni.cae_streaming.kit, Kit 109, headless + Xvfb :99, 1 GPU)
  ├─ omni.kit.livestream.app ......... fires carb event "omni.kit.livestream.receive_message"
  ├─ omni.cae.* (viz / index / importer / delegate.vtk) .. NanoVDB volume, ROI clip, colormap  [present]
  ├─ omni.kit.manipulator.prim ....... drag gizmo for the ROI box                               [present]
  └─ dc.cfd_bridge  (NEW) ............ subscribes to the carb event → dispatch by command_name;
                                        auto-loads the GTC scene on startup
```

**Reference anchors (verified during exploration):**

- Command dispatch model — blueprint
  `src/frontend/omniverse/Ref_code/omniverse-dsx-blueprint-for-ai-factories/source/extensions/manager/manager/extension.py`
  (`_RECEIVE_EVENT = "omni.kit.livestream.receive_message"`, `carb.eventdispatcher…observe_event`, `_route_command` if/elif on `command_name`).
- Browser command sender — blueprint
  `…/omniverse-dsx-blueprint-for-ai-factories/web/src/streamMessages.ts` (`AppStreamer.sendMessage({event_type:"send_message_from_event", payload:{command_name, message}})`).
- GTC scene + CFD volume + ROI (proven, programmatic) — kit-cae
  `src/frontend/omniverse/Ref_code/kit-cae/scripts/load_gtc_final.py`.
- Streaming app + GPU/port settings — kit-cae
  `…/kit-cae/source/apps/omni.cae_streaming.kit` (signal port 8301, `renderer.activeGpu`, `omni.services.transport.server.http port=8300`).

## 4. The new Kit extension: `dc.cfd_bridge`

### 4.1 Responsibilities

1. **Startup pre-load** of the GTC scene into the stage — geometry (racks/ACUs/boxes), the imported CFD dataset, the NanoVDB volume prim, and the ROI box — **all created hidden** so the heavy import happens once at boot. `loadScene` then merely **reveals + frames** the geometry (fast, demonstrative); `setVolume`/`setRoiVisible` reveal their prims on demand. Geometry placement is ported from `place_geometry_from_json()` in `load_gtc_final.py`.
2. **Command channel**: subscribe to `omni.kit.livestream.receive_message`, decode the `send_message_from_event` envelope, dispatch by `command_name`. Marshal all USD edits onto the main thread (handlers are `async`, awaited on Kit's update loop).
3. **Outbound** (one message): push `roiChanged {min,max}` to the browser after the user drags the ROI gizmo, so the sliders stay in sync.

### 4.2 Command set (data-channel `command_name`s)

| command_name | message (JSON) | Kit action | kit-cae API used |
|---|---|---|---|
| `loadScene` | `{}` | Show GTC geometry; frame camera | `place_geometry_from_json`, `frame_prims` |
| `setCamera` | `{name}` | Set active viewport camera | `get_active_viewport().camera_path` |
| `setRoiVisible` | `{visible}` | Show/hide ROI box; enable pickability; select it (so the gizmo grabs it) | `UsdGeom.Imageable.MakeVisible/Invisible`, `ctx.set_pickable` |
| `setRoiBox` | `{min:[x,y,z], max:[x,y,z]}` | Move/resize ROI box (slider-driven) | `TransformPrimSRT` |
| `setVolume` | `{field:"T"\|"U", visible}` | Create/show NanoVDB volume; color by field | `CreateCaeVizVolume(type="vdb")`, `FieldSelectionAPI("colors").SetTargets`, mode `vector_magnitude` for U |
| `setVolumeRange` | `{min,max}` | Set colormap threshold | Colormap prim `domain=(min,max)` |
| `setRoiClip` | `{enabled}` | Bind/unbind ROI on the volume (whole-domain ↔ ROI-only) | `DatasetVoxelizationAPI("source").GetRoiRel().SetTargets([...] / [])` |

Outbound: `roiChanged {min,max}` (Kit→browser).

### 4.3 ROI mechanism — exact semantics and constraints

- ROI box = `CreateCaeVizBoundingBox` → a `UsdGeom.BasisCurves` (12 edges) with a pivot-centered translate/rotate/scale op stack.
- Clip = `DatasetVoxelizationAPI(volume,"source").GetRoiRel().SetTargets([box])`; at voxelization the operator does `dataBounds.IntersectWith(roiWorldBounds)` (`omni.cae.viz/python/utils.py`).
- **Drag** = stock `omni.kit.manipulator.prim` gizmo (W=move, R=scale). Works in-viewport because WebRTC forwards pointer events (`nativeTouchEvents:true`); **no custom input code**.
- **Constraint A — AABB only:** the clip is the axis-aligned bounding box of the ROI prim. Rotating the box only changes its AABB; it cannot produce an oblique cut. (Acceptable: ROI is a rectangular region, matching the requirement.)
- **Constraint B — NanoVDB only:** ROI clipping exists only for `type="vdb"` volumes. We always use `vdb`, so this is satisfied.
- Field switch (T↔U) and colormap range update **re-execute automatically** via the operator's change tracking; no rebuild needed.

### 4.4 Integration without polluting `Ref_code/`

- `dc.cfd_bridge` lives under `src/frontend/omniverse/cfd_demo/kit/extensions/`.
- A thin app `dc_cfd_demo.kit` (or a launch flag set) depends on kit-cae's built `omni.cae_streaming` extensions and adds our extension via an extra `--ext-folder` + `--enable dc.cfd_bridge`.
- `Ref_code/kit-cae` is used read-only (its `_build` extensions + `data/GTC/GeoUSD` assets). No edits to `Ref_code/`.

## 5. CFD data pipeline

1. Convert the user's results to a kit-cae-compatible VTU (verified feasible; case already reconstructed, 964,878 cells, T+U present):
   ```bash
   conda run -n dcdtuni foamToVTK \
     -case case/GTC/log/2026-06-20-05-50-44_GTC \
     -latestTime -fields '(T U)' -no-finite-area
   ```
2. Place the resulting `internal.vtu` under `src/frontend/omniverse/cfd_demo/data/`.
3. On startup the extension imports the VTU, locates the `T` and `U` field prims, and computes the global **T range** and **|U| range** to seed the default colormap `domain` and the UI slider bounds.

## 6. Web frontend

### 6.1 Reuse vs strip (from the blueprint web app)

**Reuse near-verbatim:** the viewport (`pages/LocalStream.tsx`), the WebRTC provider/connection (`providers/OmniverseApiProvider.tsx`, `lib/OmniverseApi.ts`), the command sender (`streamMessages.ts`), `StreamLoader`/`StreamError`, the toolbar/panel UI primitives (`components/ui/*`, `ToggleButtonNative`, `IconButtonNative`).

**Strip:** AI agent/chat (`panels/agent/*`, `omni.ai.aiq.dsx` HTTP side-channel), ArcGIS map (`@arcgis/core`, `map/*`), OIDC auth (`oidc-client-ts`, `react-oidc-context`, `@omniverse/*`), power/electrical/analytics business panels (`recharts`, `metrics/*`).

### 6.2 UI layout (overlay on the WebRTC video)

- **Left toolbar rail:** Load Scene · Camera presets · Reset.
- **Right "CFD Control" panel:**
  - **Field**: Off / Temperature / Velocity (radio).
  - **ROI**: Show-ROI toggle; X/Y/Z min–max sliders; "clip volume to ROI" toggle.
  - **Threshold**: colormap min/max range slider + live color legend (units: K for T, m/s for |U|).
- **Visual theme:** dark glassy panels (`rgba(10,12,18,.92)` + `backdrop-filter: blur`), cyan/blue accents (`#4a9eff`, `rgba(0,212,255,…)`), green for active/success, `Share Tech Mono` for numeric readouts — matching `src/frontend/web/STL_generate.html`.

### 6.3 Signaling config

`signalingServer` = host, `signalingPort` = **8301** (kit-cae default), overridable via `?server=&port=` query params and `VITE_*` env, exactly as the blueprint resolves them.

## 7. File layout

```
src/frontend/omniverse/cfd_demo/
  kit/
    extensions/dc.cfd_bridge/        # NEW: command bridge + GTC auto-load (only new Kit code)
      config/extension.toml
      dc/cfd_bridge/{extension.py, dispatch.py, scene.py, volume.py, roi.py}
    apps/dc_cfd_demo.kit             # thin .kit: omni.cae_streaming + dc.cfd_bridge
  web/                               # lean React+Vite app (forked blueprint shell)
  data/                              # regenerated internal.vtu (+ copy/symlink of GeoUSD assets)
  scripts/
    convert_gtc_cfd.sh               # foamToVTK conversion
    start_demo.sh                    # Xvfb :99 + Kit streaming + Vite
  README.md
```
`Ref_code/` is untouched.

## 8. Launch & deployment

- Single GPU (host has 8× RTX 4090; OV uses 1 — pick an idle/free GPU, configurable; kit-cae defaults to GPU 3).
- Headless render needs `DISPLAY=:99` + Xvfb (NVENC requirement). No `CUDA_VISIBLE_DEVICES` (breaks NVENC); GPU is chosen via `renderer.activeGpu`.
- `start_demo.sh`: start Xvfb → launch Kit streaming app (with our extension) → start Vite. Stop with `fuser -k 8301/tcp` etc. (never `kill`/`pkill`).
- One concurrent WebRTC client.

## 9. Verification plan (browser-use, mandatory)

Each step = a programmatic `eval` check **and** a screenshot read back (multimodal):

1. Stream connects; GTC scene visible in the viewport.
2. **Load Scene** → racks/ACUs/boxes rendered.
3. **ROI toggle** on → box wireframe appears; off → disappears.
4. **Drag ROI** in viewport → box moves/resizes (before/after screenshots).
5. **Temperature** volume → colored volume, clipped to ROI.
6. **Threshold** slider → volume color/extent visibly changes.
7. **Velocity** volume → |U| field renders.
8. **Clip-to-ROI** toggle → volume extent switches whole-domain ↔ ROI-only.

Note: WebRTC media is UDP; demo runs locally so signaling+media connect directly. (Cross-host/SSH-tunnel needs direct media reachability — out of scope for the local demo.)

## 10. Risks & de-risking

| Risk | Mitigation |
|---|---|
| **Streaming SDK message compatibility** — kit-cae ships the older `nvstream-lib.js`; blueprint uses `@nvidia/omniverse-webrtc-streaming-library`. Does `sendMessage` reach Kit as `omni.kit.livestream.receive_message`? | **Spike first**: send a single `loadScene` from a minimal client and confirm the carb event lands in `dc.cfd_bridge`. Both target the same `omni.kit.livestream.app` (Kit 109), so expected to match. Build the full UI only after this passes. |
| ROI gizmo not grabbable | `manager` disables pickability globally; we re-enable it for the ROI box and select it on `setRoiVisible`. `omni.kit.manipulator.prim` is in `kit_base_editor` (loaded). |
| Cell-count/range differences vs kit-cae's bundled VTU | Expected (our VTU is the user's newer run). We compute ranges at load time, not hardcode. |
| ROI drag → UI slider drift | `roiChanged` outbound push keeps sliders synced; if fiddly, degrade gracefully (volume still re-clips correctly; only the numeric readout lags). |

## 11. Scope

**In (MVP):** viewport embed; load GTC USD scene; ROI toggle + drag + sliders; T/|U| volume render; colormap threshold; full browser-use verification.

**Out (YAGNI):** AI chat, ArcGIS, auth/OIDC, power/thermal/analytics business panels, streamlines/slices (stretch only), NVCF cloud mode, multi-user, liquid-cooling dashboards.

## 12. Build phases (high level; detailed plan to follow)

0. **Data prep** — `foamToVTK` → `internal.vtu`; compute T and |U| ranges.
1. **Kit spike** — `dc.cfd_bridge` skeleton: auto-load GTC + receive one `loadScene` command; confirm carb event + scene streams (de-risk #10).
2. **Web shell** — fork lean blueprint app; viewport connects to kit-cae stream; verify video via browser-use.
3. **Commands** — implement full command set + wire React controls (field, ROI toggle, sliders, threshold).
4. **ROI interactivity** — gizmo dragging + `roiChanged` two-way sync.
5. **Polish + full browser-use verification pass** + README.
