# OV sequential switch lease — 2026-10-06

The actual normal MAIN browser completed two sequential OV -> Three.js -> OV roundtrips after the minimal viewer-lease repair. Each return to Three.js was enabled and confirmed; each later OV connection produced its own scene-ack. Including initial OV entry, the raw record contains three scene acknowledgements. The final decoded video is 1600 x 900, readyState4 with advancing playback; recorded errors are empty. Before/after normal Load/Save engineering projects are byte-exact: 444,441 B, SHA256 0ca2730b7891cf34c6a98267d690f8c9212016c2c024348116665f05a490dd1a.

This accepts the demonstrated ordinary sequential single-client switching scope. It does not certify rapid toggles, simultaneous clients, all OV postprocessing, transient rendering, material parity, CFD mesh/solver acceptance, or any-case deadline success.

## First-hand actual browser gate
See [runtime receipt](ov-switch-lease-live-browser-20261006/receipt.json), [complete browser raw record](ov-switch-lease-live-browser-20261006/browser-raw-proof.json), and its original [manifest](ov-switch-lease-live-browser-20261006/manifest.json).
- MAIN normal page uses index-BEZp9LUS.js, built source MAIN f8795d2946eb3414e6f17f634a3474277f95a886 / IT 3c181f16ddf5c67fc6be5929df50521ea0f804f1.
- Initial scene-ack: 2026-10-05T22:39:23.597Z.
- First return/re-entry scene-ack: 22:40:13.318Z.
- Second return/re-entry scene-ack: 22:40:56.755Z.
- Final video time43.151292s, 1600x900/readyState4; recorded errors[].
- No service restart occurred during this OV browser gate. The separately authorized later IT backend restart is not an OV/Kit restart and is not part of this switching acceptance.

The retained JPEGs are ROOT's unedited browser evidence. Runtime control, selector and ACK facts come from raw browser/DOM/event records; a rendered screenshot alone is not used to infer hidden controls or backend completion. The proof-only cfdproj carries the screenshot relay record and is not a simulation input. The paired engineering cfdproj files establish byte preservation.

## Original failure is preserved
The older [restoration receipt](ov-live-restoration-browser-proof-20261006/receipt.json) and [raw browser proof](ov-live-restoration-browser-proof-20261006/browser-proof.json) document the real reconnect barrier failure with index-a1vBEN6m.js. A subsequent restored video after page recovery was not a successful roundtrip. All four declared original payloads and their manifest remain byte-exact; that failure is not relabeled as a pass.

## Source repair and controlled tests
The [source diagnosis](ov-switch-lease-diagnosis-20261006/REPORT.md) binds installed NVIDIA WebRTC SDK5.18.2 and the built SDK chunk, with exact lifecycle excerpts. It identifies source-order races but does not prove those races uniquely caused the older browser timeout.

The [authored fix report](ov-switch-lease-fix-20261006/REPORT.md) and [source patch](ov-switch-lease-fix-20261006/source.patch) cover two production paths (ovStreamLease.ts, OvViewport.tsx) and two focused tests. The lease still serializes owners and fails closed; no SDK private-state reset or forced simultaneous connection was introduced. Cleanup waits for initialization and the separately tracked terminal onStart receipt. Disposed callbacks record lifecycle termination without starting scene synchronization.

The canonical controlled RED is 7 failed/6 passed; the final controlled run is13/13 pass, and tsc --noEmit returned0. Earlier authored assertion/error-handling failures and the12-pass/1-fail first-green attempt remain preserved. These are mock SDK/React lifecycle tests, separate from the real installed-SDK browser gate above. SDK source/license/chunk copies are the original declared references; no package was installed by publication.

## Normal source integration and builds
[ROOT source integration](root-ov-source-integration-20261006/receipt.json) retains the source4 acceptance and commit identities. That original receipt says build/runtime pending at its earlier timestamp; later build/runtime records close those gates rather than rewriting it.

Normal MAIN build returned0 in47.4388s (entry index-BEZp9LUS.js); normal IT build returned0 in31.5384s (entry index-AWScKNyD.js). Both include the existing project typecheck in npm run build. Their original declared twelve-file packets preserve source19/protected6/flags/process/listener checks and missing-only old asset restoration with zero collisions. Build receipts are not browser evidence.
- [MAIN build receipt](ov-switch-lease-main-build-20261006/receipt.json)
- [IT build receipt](ov-switch-lease-it-build-20261006/receipt.json)

## Publication inventory
The top-level manifest lists only actual published files. publication-receipt.json records every source-package identity and byte-exact check.
- Fix: original33 payloads/2,222,022B all SHA checked; only32/899,938B copied. ROOT explicitly approved excluding the sole generated node-compile-cache entry (1,322,084B). Its exact SHA and reason remain in the fix publication receipt; the original33 manifest is preserved as original-manifest.json. This is not claimed as a complete33-file copy.
- Diagnosis13, live browser8, IT build12, MAIN build12, prior failed restoration4: all declared payloads and original manifests copied exactly.
- ROOT source integration had no original manifest; its three explicitly named files are independently inventoried in a new manifest.
- No dist-before, npm cache, temporary test directory, build output tree or new case is included. All declared failed/pass raw logs are preserved, even where Git would ordinarily ignore .log.

Publication performed only read/hash/copy of frozen evidence and new documentation. It did not execute Git, tests, build, browser, API, SDK, business mesher/solver, service, source or case actions. HANDOFF_APPEND_DRAFT.md is separate text for ROOT to review; no original HANDOFF file was edited.
