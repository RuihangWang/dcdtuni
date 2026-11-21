# Engineering input revisions and same-UID project load — 2026-10-06

Two actual browser defects were repaired in integration84125184, cherry-picked to mainca3f3696.

1. Inspector's uncontrolled description retained an earlier project's draft after loading a different document with the same UID and committed description. Its input remount key now includes the explicit project-load revision; ordinary same-document draft behavior remains. New regression first failed3/4; repaired4new+6existing tests pass. Normal browser loaded422944characters and then an empty description at the same UID; textarea correctly cleared.
2. Liquid Panel used a generic render revision to mark a prepared experiment stale. New `documentInputRevision` advances for engineering edits/load/undo/redo/live edits, and stays unchanged for playback/log/save/refused edits. New actual-store/Panel regression first failed4/8; repaired8new+18store+1Panel tests pass. The first invalid test-authoring fixture is retained and excluded from the valid red/green count.

Normal isolated React UI verified playhead0→24.5 and Save do not stale the prepared run; a real duration24.5→25 edit does. A new complete50-frame run with automatic playback to24.5 remained non-stale and its2800 heat audits closed. [Actual original and fixed browser evidence](../shinaide-liquid-mixed-keyframes-2026-10-06/README.md).

Both relevant typechecks and builds passed. Final IT build was index-CytyScZ4.js; MAIN normal production build index-BNawPZKI.js passed with source inputs, protected projects and all six owned runtime PIDs/listeners unchanged. Existing MAIN OV toolbar screenshots were restored byte-identically after build. The MAIN live OV page was not reloaded; fixed-UI E2E acceptance belongs to the isolated same-source React page. A separate static audit records that full page reload does not automatically reattach an old LiquidRuntimeClient session; no reload recovery implementation is claimed.

Subdirectories retain red/green raw logs, tests/source identities, build preservation and integration receipts. Publication manifest covers all transferred payloads.
