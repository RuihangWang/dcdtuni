# OV current-frame screenshot fix — frozen source, not browser acceptance

The actual Operation Log screenshot button now uses the mounted OV video's decoded current frame. It captures native videoWidth/videoHeight through a detached 2D canvas; unavailable/missing/zero-size OV frames produce an explicit Omniverse error in the existing log, never a hidden Three fallback or invalid screenshot POST. Without OV, the existing immediate Three render hook/canvas fallback is preserved.

Only three source/test paths are changed; exact hashes and copies are in receipt.json and source/. Original source baseline showed five meaningful failures and two passing Three cases. Final focused run: seven new button tests + one existing OV input test = eight passing; standalone tsc --noEmit exited0. Production build/browser/API/services/native/solver/index/commit were not run.

The first green-attempt matcher incompatibility and initial test typing errors are preserved separately. The existing OV-input test's incomplete SDK mock logs a connection failure even though its focus assertion passes; this is not live-video evidence.

Real decoded pixel/PNG capture and dimensions must still be checked by the user-visible screenshot button in root's actual browser. The jsdom tests stub drawing/serialization while asserting renderer selection, current-frame gate, dimensions, Three render order, error logging and saved POST bytes.
