# Product screenshot scope correction

The product 📸 button produces a **WebGL viewport canvas PNG only**. It does not capture React sidebars, resize separators, buttons, ARIA values, terminal text or the full browser screen. Root's actual CUA DOM observations remain the source for those UI states; terminal SSE/process/lock records independently support cancellation cleanup.

Two earlier statements overstate the image scope:

- [panel-resizers-cua/README.md:5](../panel-resizers-cua/README.md) says “original full-view native screenshot”.
- [panel-resizers-cua/cua-observations.json:87](../panel-resizers-cua/cua-observations.json) says “Screenshot is full project view captured after returned sizes, not each intermediate key.”

Those should be read as a product viewport-canvas image saved after layout restoration. The image itself cannot prove sidebar dimensions, controls, focus, ARIA or terminal state. The actual DOM measurements recorded in that receipt remain separate evidence.

[air-cancellation-cua/README.md:5](../air-cancellation-cua/README.md) says “application-native capture after the second stop”. That phrase is also clarified here as viewport-canvas output only. Its file named unlocked-after-second-stop.png denotes the later capture context, not visual proof that the controls were unlocked. The original image is bound by two-cycle-terminal-verification.json:48 to the product path ui_screenshot_20261005_043639.png.

The primary implementation establishes this scope:

- src/frontend/react/src/components/LogPanel.tsx:14–30 (SHA256 1b17e9c68ad70c702dfebf0e6f69d1c7971a78d86ae3c0b15b79a372c0022f64) obtains __viewportRender() or #center-view canvas, calls canvas.toDataURL('image/png') and submits that imageData to POST /api/screenshot.
- src/server/server.py:4680–4698 (SHA256 60d46e470f549129f51a40c07b64ccbc9f8627b9db0188b011433421346c9b4f) decodes and saves this supplied payload as ui_screenshot_<timestamp>.png. It does not take a full-page or operating-system screenshot.

The third-run PNG is 1900×995 canvas pixels while the natural IAB viewport is root-observed 1520×956 CSS pixels. It is not a same-dimension raw CUA screen capture. Its original bytes, header, all chunk CRCs and decompressed scanlines were checked; none of those structural checks enlarge its visual scope.

This is an addendum only. Both older READMEs/JSONs, original PNGs, manifests and two shutdown-Traceback SSE streams remain unchanged. Their committed content identity was verified: text/raw files match HEAD blobs; PNG payload SHA256 and size match their committed Git LFS pointers. The original error evidence is retained. See [prior-screenshot-scope-audit.json](prior-screenshot-scope-audit.json) for exact file hashes. No old file was edited, re-captioned or replaced.
