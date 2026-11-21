# OV viewer lifecycle minimal fix — frozen authored packet

The fix modifies only IT src/frontend/react/src/renderer/ovStreamLease.ts and OvViewport.tsx, plus the existing test/ovStreamLease.test.ts and one related new test/ovViewportLifecycle.test.tsx. No backend, geometry, server, Kit or native source was changed. No build, browser, SDK service call, Git or CFD run was performed. The tests use explicitly controlled SDK lifecycle doubles; the viewport test mounts/unmounts actual React but mocks the network SDK and command client. CPU 28–31, CUDA empty, one test worker, TMPDIR inside this evidence directory. TypeScript --noEmit creates no product build.

Prior independent source diagnosis is immutable at ../ov-switch-lease-diagnosis-20261006/manifest.json (0ba36d5430043be3767bfd1552dfbb926a639b9e2132ee8a049eb2e006a25607). The installed public SDK dependency is copied here and independently hash-bound: 5.18.2 ESM 722,124 B / 46b9ee6862c2eda0d02f07d34dc1272801e3040e97cadadd60578d286b02e395. The source behavior being addressed is that connect's initialization returns inProgress separately from terminal onStart, can overwrite a prior connected/cancelled state, and terminate while connecting only writes the canceled marker. Calling destructive terminate before initialization settles can discard the static controller while its old await continuation still creates a runtime.

Implementation:
- createOvStartReceipt records only success/error/canceled onStart terminal events and exposes a one-shot owned receipt. Nonterminal warning/progress does not authorize scene synchronization.
- terminateOvViewer first waits for the original initialization promise. No destructive stop is attempted while this promise is pending, and no delayed stop is attempted after cleanup's bounded failure deadline.
- It retains a separate terminal onStart receipt. Public SDK none alone does not release a successfully initialized viewer while that receipt remains pending. Initialization rejection still triggers cleanup of a possible created controller and may complete at public idle without a terminal event that will never be emitted.
- When public terminate returned inProgress and terminal onStart is confirmed, the same owner may make exactly one serial completion attempt. This addresses onStart already finished before the init promise overwrote connected with connecting. An SDK already-terminating rejection is preserved while polling for the same owner's idle; it never opens another connection or performs another physical teardown in the modeled double.
- Without an explicit terminal receipt, the legacy helper has its original single stop behavior. This avoids manufacturing a receipt for older callers. The real OvViewport always supplies the receipt.
- OvViewport records terminal lifecycle before checking disposed. A disposed callback does not capture a scene, synchronize it or call onReady. Active success enters normal synchronization; active error/canceled goes through the existing fail/fallback path.
- The entire OvStreamLeases owner/tail/queued-cancel/blocked class and singleton suffix is byte-exact with baseline. No blocked-state reset, SDK private-state assignment or forced double connection has been added. The complete original five lease tests are an exact unchanged prefix of the final regression file.

Final source identity is exact in source-final.json:
- ovStreamLease.ts 5,259 B / 09b1c35a1ddecc22dfa6e1a54c0d588538485ba997a1f0f7920882fe9f3e9496.
- OvViewport.tsx 11,662 B / cbf24e300e4d62330f54cd867ef501d6422a875d85beeef73be00d5e2f174c26.
- ovStreamLease.test.ts 11,966 B / e37d1328a9ac4e7198c61a541189ff27768424cdf34d7d407f50891493e6a5cb.
- ovViewportLifecycle.test.tsx 3,000 B / 366cf92e70094d7071f7e84ce512a239ddf19ee39fdb01f373441e3e6159afdd.

Evidence sequence and honest exclusions:
Baseline product sources and original tests are saved before any edit. The canonical RED is red-final.log / red-final-command.json: 7 failed, 6 passed, two files, no unhandled errors, return 1, 3.813 s. It runs the seven new failing source-order/disposed cases plus the original five and an initialization-failure positive control against unchanged baseline product sources. The initial red.log has a test assertion-handler unhandled rejection and red-clean.log has an authored Chai assertion-proxy error; both raw attempts are retained, excluded as clean acceptance evidence, and corrected only in test promise/error handling before the canonical RED. The actual relevant source failures remain the same, not weakened or recaptured as green.

First product green attempt green-first.log was 12 pass / 1 failure: the receipt-free legacy fake was incorrectly given a second stop. Product logic was narrowed to require explicit settled terminal receipt for that completion attempt; original test expectations were kept unchanged. The final green.log / green-command.json is 13/13 pass, two files, return 0, 16.989 s outer (15.17 s Vitest; environment initialization accounted for most time), with exact final source hashes. The viewport test's two non-null assertions were type-only changes after its canonical RED; the assertions and input lifecycle did not change. typecheck.log is empty, typecheck-command.json records tsc --noEmit return 0 in 34.341 s. These are targeted controlled regression and type evidence, not proof of normal SDK or Kit shutdown.

The modeled RED/GREEN cases are:
1. Initialization pending with status none: no destructive stop or detached-controller race.
2. onStart already succeeded, then late inProgress overwrites connected: canceled marker receives a serial completion.
3. Late initialization then terminal SDK auto-cancellation: ownership remains held until idle.
4. Public none while terminal onStart still pending: no premature release.
5. SDK automatic termination already running: completion rejection does not create a second physical teardown and waits for its idle.
6. Initialization rejection: cleanup without a non-emitted start receipt.
7. Hung initialization: deadline failure preserves blocked/queued-no-connect; its late fulfillment cannot issue a stop after failure.
8. Actual React disposal before warning/success callbacks: terminal receipt persists but no scene or onReady work runs.
Original serialization, canceled queued viewer and blocked future-owner guards still pass unchanged.

Remaining boundary:
No claim is made that this is the unique cause of ROOT's original browser timeout, or that OV -> Three -> OV now works in the live SDK. The previous reloaded main viewport was not touched. SDK callbacks or initialization that never settle, a previously detached controller, or any failed public teardown still result in the bounded fail-closed owner barrier. Public idle is an exported SDK/controller receipt, not an independent remote media/worker shutdown proof. A normal real browser check must still show repeated live and fast round trips, stable engineering/camera state, no old/new overlap, and no stuck status. If the public SDK cannot finish that check, document the exact SDK state and propose a fresh isolated iframe/document runtime; no such architecture change or unsafe barrier reset is implemented here.

All source and evidence are now frozen for ROOT's independent source review. The packet contains raw original and corrective records rather than silently overwriting failed attempts.
