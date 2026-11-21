# Third real browser mesh cancellation

Root started job 6ba13415 in the real React UI at 05:34:13.924 UTC, clicked stop at 05:35:07.257, and observed unlocked Run, base-size and geometry controls plus 已中断 at 05:35:18.796. Solver stayed disabled because cancelled mesh has no quality authorization. The 11.539 s interval is the gap between two observations, **not a measured response latency**.

The corrected before-stop snapshot at 05:35:03.067203 captures mpirun PID 2979803/startTicks 122466697 and eight ranks 2979818–2979825/startTicks 122466747. The original verification at 05:35:24.266344 records all nine identities gone, no remaining native process and an available fcntl case lock. Final status is done, returncode -15, cancelRequested/cancelled true and checkMeshOk false. These are the original live probe facts; this collector did not rerun process/lock checks.

The full SSE contains 310 worker log messages and one terminal JSON record, 622 physical lines. SHA256: 1f7d5a47b5b67ff64bf4ce93dd13d8162142deabba6d7126940ca2358d48254b. Neither ordinary nor ignored-shutdown Traceback markers appear. The previous two failed rounds remain unchanged.

initial-empty-absolute-argv-probe.json is intentionally retained: an incorrect path filter produced that empty list. It is not evidence of an empty native process tree. The corrected positive snapshot is processes-before-stop.json.

The seven raw original files and the script are byte-exact copies. The bounded credential review found no embedded values in the script or credential/token candidates in the raw files; the script parses authentication values at runtime from a separate AST source. That referenced source is not copied or printed, and the script was not executed during collection. The launcher log was included only after this review. See secret-audit.json for scope.

The later actual IAB product-camera output is a **WebGL viewport canvas PNG only**, 1900×995 RGBA, 1,716,887 bytes, SHA256 7fbe0f911c60e680771011c5debe9142e63223c8e27cc2818ee8f94c546cb2dc. It excludes React sidebars, buttons and terminal, and is not a CUA raw screen capture. Root's natural IAB CSS viewport was 1520×956; these are different dimensions. LogPanel.tsx:14–30 uses __viewportRender() → canvas.toDataURL(), and server.py:4680–4698 saves that payload. Signature, IHDR, every chunk CRC, IEND and decompressed scanline length were validated; the copied bytes match. Control unlock is supported by root's actual DOM booleans and SSE/process/lock evidence, never by this canvas PNG.

Source identity is bound to runner 6f9a14b8, backend 8e4452a5 and launcher 4418f728 by actual Git blob SHA256. The served prebuilt frontend is root-reported 416ad471, asset index-DmA1kaOl.js, with actual dist index/asset hashes recorded. AIR b1 diagnostic native ELF 533086e2 and explicit experimental flags were used, with CUDA masked off. New unbuilt native proposal source and unrelated React edits are outside this receipt.

Original project SHA256 remains 7ef58e39e6ba7eaa403d752f05016f5362372f5a569d8a3339d00627a9943bd7. This is cancellation-lifecycle evidence, not mesh/solver/OV acceptance. No tests, index, commits, browser/API/service or native actions were performed by this evidence collector.

launcher.log is a hash-bound safe snapshot; the source live service log can continue appending after collection. The manifest binds the copied bytes, not future source-log contents. Prior screenshot-scope wording was reviewed read-only and reported in prior-screenshot-scope-audit.json; no older evidence was edited.

Read the [product screenshot scope correction](prior-screenshot-scope-correction.md): earlier “full-view/full project view” wording is corrected by this addendum. Canvas PNGs cannot establish React sidebars, controls, ARIA or terminal state; all historical evidence bytes are preserved.
