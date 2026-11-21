# SeedInspector event simulation repair

The targeted existing file reproduces two failures at unchanged source 89f8b48b (5 passed / 2 failed). SeedInspector, NumberField, seedActions, seedConfig and the real Base_leakage input are all byte-identical to that baseline. This is not a liquid-default production regression.

The old test assigned input.value through React's instrumented instance setter, so React's value tracker considered the next input event unchanged. NumberField's real onChange did not set edited.current. Its intentional no-edit blur guard then correctly preserved the model. The test now uses the native prototype setter, separately flushed focus/input/real blur and retains both original .2 and status-change assertions. The reset test additionally proves it has an actual .2 stored override before resetting, preventing a previous false-positive reset.

Only test/components/SeedInspector.test.tsx was edited. No production component, input, physics, schema, API or source material was changed. Final targeted result: 7 passed. Tests used CPU36–39 / two workers / CUDA empty and repo-owned TMP/cache. The proof binds the actual fixture and original files by SHA; its test-only.patch contains the full small repair.

This is component test evidence, not browser acceptance. No native or browser was started.
