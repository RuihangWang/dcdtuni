# Shared mesh and postprocess UI/driver convergence: read-only plan

User constraint: Three.js and OV differ only as render engines. Mesh and postprocessing use the same frontend UI and driving code; conversion belongs below that driver. This report identifies current separation and a minimal migration, not an implementation or runtime acceptance. The PRIVATE v2 mask/output package remains separate, frozen and unapplied.

## Actual current reuse

- App.tsx:737 mounts one MeshPanel; :750–758 mounts air PostprocessPanel or LiquidResultsPanel by physical cooling mode, not by render engine. Preserve that air/liquid distinction: different physical datasets do not require duplicated Three/OV controls.
- MeshPreviewSession is shared state. run/meshPreviewSession.ts:255 builds its retained preview; setters at :368–418 own hidden patches/opacity/order/visibility. RendererControl.tsx:298–305 subscribes the same preview and offers its overlay to OV. MeshPanel already need not become an OV-specific UI.
- PostprocessPanel.tsx:272–357 computes the current surface, slice lists, streamline lists, preview ring/seed and volume configuration then calls the retained Three viewport. Viewport.tsx:1375–1555 installs that imperative carrier.
- PostprocessPanel.tsx:359–393 also publishes verified surface/slice/streamline numeric result layers through windResultProducer. That producer imports postprocess data and result-layer adapters, not a live Three scene. renderer/resultLayers.ts:6–15 already carries project hash, cfdrun hash, strict mesh receipt and run receipt; :21–48 carries mesh/surface/slice/streamline buffers and physical time.
- RendererControl.tsx:306–308 delivers that shared result-layer set to OV with current run/time/project guards (:102–131). This existing route is the strongest starting point for a common driver; do not recreate the numerical cuts/traces per renderer.
- liquidTempColor owns the selected sample and raw/global liquid domains. LiquidResultsPanel.tsx:84 and OvLiquidSection in OvPostProcessPanel.tsx:78–235 both reference it, so its time/sample identity can be retained.

## Present violation / missing common ownership

- RendererControl.tsx:381–383 mounts a separate OvPostProcessPanel overlay. That panel owns its own run/time selection (:246–288), dataset queue and grant/load (:370–518), volume/slice/fire state and dispatch (:520–569), and a second settings UI (:579–685). It duplicates the existing PostprocessPanel's run/frame/domain/seed/slice controls. Do not add another OV product panel.
- PostprocessPanel owns lists of slices/streamlines; direct OV controls are singleton slice plus one streamline/seed setup. Moving only JSX would silently collapse supported lists. Prefer existing ResultLayerSet transport for shared computed multi-set surfaces/slices/lines.
- Shared ResultLayerSet v1 has no volume layer (:41), while PostprocessPanel volume uses grid/resolution/palette/opacity and direct OV IndeX has a separately granted dataset plus T/U domain state. This needs an explicit adapter capability/parameter contract, not a false claim of identical volume behavior.
- MeshPreviewOverlay.ts:2, :34–59 extracts positions/material/matrices from already-built Three objects. The UI/session is shared, but this lower conversion still depends on a Three carrier. Migrate that buffer producer later to owned decoded mesh data; do not describe it as renderer-independent today.
- pp/index.ts is already renderer-neutral data fetching/decoding with session-revision headers. PostprocessPanel's viewport lookup (:80–88) and applyLayers are the current concrete adapter seam; its driver/ref state need not be rewritten into another scene engine.
- ovDisplayPreferences currently has its own per-project/run air frame/display memories. Merge that preference ownership into the shared display session; retain legacy compatibility while eliminating two independent active selections.

## Smallest coherent slice

1. Extract the existing panel's display/session controller, not its physics compiler: a single renderer-neutral PostprocessDisplaySession owns selected run, exact accepted frame/time, source/revision, field + raw unit, palettes/ranges, surface visibility, slice/streamline set IDs, volume intent, seed/ring intent and patch hidden map. Existing air/liquid UI panels remain the only controls. UI renders ACK/result status from this controller, not from an engine-specific overlay.
2. Introduce a thin PostprocessRendererAdapter interface below it: installFrame(source/context), applyDisplay(snapshot), clear, readInteraction, dispose. Three wraps the existing viewport imperative API/ppRenderer; OV wraps existing result-layer transmission and client. A mode switch replaces the adapter, replays the same authoritative intent, and never resets run/time to latest.
3. Move actual OV grant/load/restore scheduling out of OvPostProcessPanel into its adapter. Keep ovDataset.ts as the trusted backend gateway. Existing specific-time no-latest-fallback, common-MPI completeness, source/TTL/grant/mesh-quality checks are unchanged. Read current frame from the shared accepted session, not another independent run selector.
4. Prefer the common CPU result-layer products for surface/multiple cuts/streamline sets. The OV adapter lowers those buffers using existing layer transport. Volume is a renderer-specific realization of one shared intent: if OV requires a dataset handle, derive that trusted request from the same accepted source/time; keep resolution/domain/palette/opacity capabilities explicit. Unsupported parameters must be shown as such rather than silently ignored or replaced by a second panel.
5. Bind every command to projectUid/contentSHA, runId/receipt, exact accepted time/revision and a display generation. Common latest-replacement queue fences stale replies; retain Kit's existing display commit lock + scene generation check for stage writes. A fire send promise is not a completed stage ACK, so do not treat frontend late-ACK ignoring as sufficient stage safety.
6. Move OV ring/seed interaction readback into the shared controller; preserve per-set identity and metres/raw-unit semantics. Keep modeling in Three. RendererControl remains renderer transport/session/camera controls, not a second postprocess UI.

Likely exact implementation seams: components/PostprocessPanel.tsx, PostprocessRightPanel.tsx, LiquidResultsPanel.tsx and LiquidPostprocessRightPanel.tsx; renderer/RendererControl.tsx and existing OvPostProcessPanel.tsx (retire product overlay after compatibility closure); postprocess/ppRevision.ts/ppConfig.ts/index.ts, renderer/ovDataset.ts/ovDisplayPreferences.ts/resultLayerRevision.ts/windResultProducer.ts; a small common controller and two adapter modules. Mesh continues its existing MeshPanel + meshPreviewSession contract. Numeric mesh ownership can then remove meshPreviewOverlay's Three extraction without changing this UI.

## Necessary acceptance and limits

- One shared UI action produces the same run/frame/source/field and exact source-bound cut/trace buffers for both adapters. Multiple slice/streamline IDs survive switching; palette/range/opacity and user hidden patches survive. Use existing result layer producer tests as actual numeric oracles.
- Exact frame selection, nearest earlier-tie playhead, same time after Three -> OV -> Three, no implicit latest reset, stale old dataset/display/fire after new frame/project rejected at both controller and Kit stage.
- Mesh preview diagnostic/strict roles and mesh/source receipts remain distinct. A failed Shinaide mesh never becomes scalar-result or solver authority.
- Liquid selected sample/global vs per-frame domain and existing curves remain one UI/driver state; historical result gates unchanged.
- Shared controls report adapter capability honestly for volume and rendering interactions. No duplicate numeric slice/tracer implementation, no separate OV UI launch route.
- Existing targeted test entrypoints are listed in existing-test-entrypoints.json. None was run for this read-only plan. ROOT's actual browser is still required for video/geometry/volume/interaction acceptance.

No code, cases, APIs, browser, services, tests, build or Git were changed. 22 actual source files are SHA-bound and read-after unchanged. This plan is small enough to migrate incrementally but is not merely hiding the old OV panel while leaving two competing drivers.
