# Whole CDU selection readability

The real canvas Shift click selected m_30, but the service door alone looked green. This patch gives whole-CDU structural and detail surfaces a clear pale cyan-green diffuse colour. It removes colour-affecting texture maps from temporary material clones and sets Standard materials to metalness 0 / roughness 0.62. Basic structural fallback materials keep their type and receive the same visible colour. Original PBR materials and texture references return when selection ends.

Only whole-CDU selection receives this style. Ordinary face selection keeps its existing narrow semantics. Non-CDU tint/solid material arrays retain their previous ignored-array policy; a highlighted CDU array demoted to a face is restored before the ordinary fade is applied. Alpha masks, side, model opacity and fades remain in force. Cached materials shared by other equipment are never recoloured in place. Anchor helpers keep the previous policy.

The test builds the saved m_30 construction through the real stateV4/SceneBuilder structural and liquid-detail paths with webglAvailable=false (jsdom has no WebGL). It does not claim these Basic fallback materials are the actual browser-side side panel. It verifies every built surface, complete restoration, multiselect, cached-material isolation, face demotion, opacity/fades, alpha masks, grouped array slots and temporary clone disposal. Project JSON is unchanged.

Corrected original-module baseline: 3 of 7 tests fail. Current related checks: 119 tests across 10 files pass. One additional array-demotion regression was then added; the final 11 new tests and TypeScript typecheck pass with unchanged production source bytes. Intermediate assertion/provider/type errors are retained and identified in receipt.json. No production build, browser, service, native job, git index or commit was performed. Parent owns the production build and actual visual acceptance.

Files are frozen by receipt.json and manifest.json. The 16 previously frozen closed-cabinet source/test/fixture paths are unchanged.

## 2026-10-05 real-loader and async texture follow-up

The saved V4 fixture has no 'kind'. The revised test places its original construction in the 'cdus' bucket and calls the real projectStore.loadProject; it does not inject kind:'cdu'. The loader restores a non-enumerable CDU discriminator, and SceneBuilder's registry holds the same model object as the store. The 12-test loaded-project snapshot, logs and receipt are preserved in [loaded-project-texture-repaint](loaded-project-texture-repaint/manifest.json). Its first new assertion accidentally called the unavailable getState API; that test-only failure is retained, clearly separated from product regressions.

A subsequent canvas pick identified the grey neighbouring cabinet as m9 UPS_Rack. That view does not prove a failure of the new CDU style or a lost CDU type. No bucket fallback or opacity change was made.

An independent real event-path regression exposed a different defect: Viewport.onTextureLoaded rebuilt the meshes and restored their fade through afterBuild, but did not repaint the existing selection. The test extracts and executes both actual Viewport callbacks through the TypeScript AST, bound to the real loader, SceneBuilder and live refs. Whole-CDU and whole-model multiselect lose their paint in the source baseline while the preview fade remains correct (13 pass / 2 fail). The minimal fix repaints the current selection after texture rebuilding; the ordinary face policy remains narrow.

After the fix, all 79 related checks across five files pass, including the 15 current CDU tests, and TypeScript typecheck passes. Fresh roots, paint state, fade/depth policy, project JSON and clean loader state are checked. The preliminary source-reader and incomplete-hook harness failures are retained and labelled as harness errors; only texture-baseline-real-hooks.log is the authoritative event regression. This follow-up did not build dist, operate a browser or service, mutate a project, stage or commit files. Actual browser and OV visual acceptance remains a separate gate.
