# Toolbar wrapped primary and full natural measurement (V2)

The first normal browser build preserved the liquid timeline at default/minimum heights and aligned full-width tool tabs, but actual English text wrapped the renderer row below its vertically shrunk primary container and into the tabs. This is a real remaining UI failure, retained in the normal-UI saved helper; it is not counted as a pass.

V2 prevents shrink on tools-region children and accounts for the full tools scrollHeight plus independently measured timeline/footer chrome when calculating natural height. The toolbar body remains the bounded scroll region; the timeline stays reserved. No engineering, material, mesh, solver, OV service or saved physical-project modification.

The new old-source regression failed once (retained RED), then passed. The initial GREEN invocation used a nonexistent panelResizers.test.tsx path and ran only the two matching files (28 PASS); it is retained as a limited run. The correctly scoped existing four-file suite plus new regression passed 37 checks. Production build and browser acceptance are still pending.
