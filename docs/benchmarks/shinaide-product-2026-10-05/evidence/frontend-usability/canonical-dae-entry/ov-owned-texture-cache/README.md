# Owned OV texture cache: source and CPU regression freeze

The normal launch owner now sets the real Kit child cache to its absolute
checkout-local runtime/render-textures, regardless of inherited cache
overrides or current working directory. A direct bridge invocation without an
explicit safe directory is rejected before texture authoring. The independent
export CLI still accepts its existing explicit texture_dir; its source is
unchanged.

The first eight regressions failed on the original source. The first related
suite then passed 50 tests and failed one old fake fixture because it reused the
live user's real signaling port. That original log is retained. The corrected
fixture uses its own free port; the final four-file suite passed **51 tests in
16.26 seconds** on CPU 36–39 with CUDA empty.

source-identity.json binds exactly six source/test paths and four unchanged
paths. source-snapshot.tar.gz contains the six final exact source bytes.
scoped-source.patch is computed against the preserved original files and the
two new files. manifest.json hashes only the explicit frozen top-level
payloads; temporary pytest/cache directories are excluded and their symlinks
are never followed.

This freeze establishes source behavior and CPU fake-subprocess regressions.
No real Kit/GPU probe, service operation, browser operation, frontend build,
Git index mutation or commit was performed. Real cache authoring and absence
of new outside-repository writes remain the parent-owned deployment gate.
Existing outside cache and the prior independent liquid audit are untouched.
