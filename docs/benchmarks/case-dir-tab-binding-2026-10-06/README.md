# Windows in-app browser: case directory binding across tabs

A real mesh job (`689195fe`) used another tab's process-global case directory. The fix captures the submitting tab's directory before asynchronous export and sends it explicitly for mesh, solve, check and status requests. The backend binds uploads and worker execution to that directory, rejects invalid explicit paths without fallback or directory creation, and retains legacy omitted-path behavior. Save suggestions now use the submitting tab's current project path.

The real two-tab retry (`6a251ac0`) generated 4,156 cells in the intended thermal case and passed `checkMesh -allTopology -allGeometry`. The global selection remained RingRear, and all 69 foreign Mesh/model/receipt files retained their original hashes. Retained evidence includes browser DOM, screenshot, the exact GUI-authored project and independently checked native input identities.

Main and integration each passed 61 backend checks and 100 focused frontend checks, typecheck and production build. The final save/path regression set passed 74 checks in each tree; both final builds after the corrected `Mesh/case.foam` message also passed. These checks used the current working-tree dependencies; this narrow commit does not certify unrelated pending changes.

The first physical solve request (`301a9759`) failed during initialization because `_load()` omitted the `sourceRegions` identity pin. Its original browser trace remains in the evidence. A separately reviewed minimal repair is now applied to both working trees, with 63 related checks passing in each; normal browser solve retry is in progress. This record does not claim convergence or full large-case mesh acceptance.

CPP and liquid global contexts remain separately guarded and require further work. All 35 native controls retain their original values; no mesh quality threshold was relaxed. Complete wrong/correct native cases and the unsuccessful launcher setup log remain in the integration tree under `.codex-tmp/case-dir-cross-tab-normal-20261006`. Main OV backend and Kit were preserved. See the dated handoff for continuation.
