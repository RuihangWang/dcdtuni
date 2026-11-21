# Renderer-aware screenshot recommendation
Read-only recommendation; no implementation performed.

MAIN LogPanel.tsx:14–23 always invokes __viewportRender / #center-view canvas. OvViewport.tsx:19 retains that Three canvas and mounts .ov-render-viewport above it (194–198). Therefore the current screenshot button captures hidden Three content while OV is visible.

Minimum reliable branch:
1. In captureScreenshot (or one small testable helper), detect a mounted .ov-render-viewport before invoking __viewportRender.
2. If it exists, capture that element's video only after readyState>=HAVE_CURRENT_DATA (2) and finite positive videoWidth/videoHeight. Use those intrinsic dimensions, not CSS dimensions. Create a 2D canvas, drawImage(video,0,0,w,h), then PNG toDataURL.
3. If OV is mounted but missing a decoded frame, report a localized no-frame message and do not POST or fall back to hidden Three. Catch drawImage/security/toDataURL failures explicitly.
4. If no OV viewport is mounted, retain existing synchronous Three re-render + canvas PNG path. Do not change renderer/project state, scene ACK, Kit startup or streaming lifecycle.
5. Send either PNG through existing authenticated browser POST /api/screenshot (server.py4683–4699). It writes CASE_DIR/screenshots. Output remains renderer pixels only; it does not contain React controls, sidebars, terminal or full UI. /api/browser/screenshot instead controls a separate server-owned browser worker and cannot capture the user's actual IAB OV connection.

Meaningful focused tests should click the real LogPanel screenshot button:
- OV decoded video at1600x900: 2D drawImage receives that exact video/dimensions; Three render spy remains untouched; posted PNG belongs to video canvas.
- OV mounted, readyState0 or video dimensions0: no Three draw/no POST, clear failure log.
- Three mode: existing __viewportRender happens immediately before PNG encode.
- OV draw/encode error or HTTP error: report failure and do not claim saved.
No actual GPU/SDK/browser fixture is needed for these DOM branch tests; actual IAB video capture then confirms saved PNG dimensions and content.

Separate directory-boundary source finding: extension.py632 defaults texture cache to /tmp/dcdtuni-render-textures, and render_scene.py396/399 creates/writes that path when texture data exist. The three launcher files do not set DCDTUNI_RENDER_TEXTURE_CACHE; inherited runtime environment was not read. Recommend explicit repo runtime/render-textures environment for future launches. This is separate from the successful current scene ACK and is not evidence of an observed outside write.
