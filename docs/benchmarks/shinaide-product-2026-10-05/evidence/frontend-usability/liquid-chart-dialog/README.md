# Liquid result recovery and readable cold-plate chart

The ordinary main browser reopened the saved run-98aaa7d2d698471b84dbc34a388144e3
through its visible results selector. No second 600 s solve was needed.
The 56 servers and 61 accepted samples were restored, Home/End selected
65.1/80.0 °C and moved the shared playhead to 0/600 s, both temperature-coloring
controls worked, and no old air colorbar survived. The read-only observations
are in main-liquid-before-dialog.json. The original dt=10 input retains its
full value after untouched focus/Tab. These observations do not add a new
physics accuracy or OV claim.

Clicking the first server opened its cold-plate chart at y=3853.40 in a 720 px
viewport, with a 240 px width and an internal-edge-ID title. It was off screen.
The repair uses a native centered dialog with a wide responsive chart, an
authoritative result-bound rack/server title, localized chip/inlet/outlet
legends, and a labelled native close button. Escape follows native dialog
cancel, which unmounts the chart; results and field values are preserved.

All three original label regressions fail on the old panel source.
Four repaired label/cancellation regressions and 55 related tests pass
(59 tests in eight files, final log 3.66 s). Production TypeScript/Vite build
passes (745 modules, Vite 9.67 s, integration asset index-Bi1_btpb.js).
The first repaired test log retains locale-not-wrapped-in-act warnings; its
fixture updates were wrapped and the final validation log is retained.
The isolated chart test supplies a chart-series mock to inspect the outgoing
labels; real chart rendering and native focus behavior require browser proof.

Also retained is the primary main-source offline geometry-math rerun:
41 tests pass in 6.22 s, and all 22 repository-relative kernel manifest hashes
were independently checked. This validates local derivations and witnesses,
not acceptance of the complete mesh. Fresh main-dialog browser proof follows.

## Keyboard focus follow-up

A normal browser Escape close at 2026-10-05 01:24:34 UTC dismissed the native dialog but left focus on BODY. The new regression fails on that source (4 pass / 1 fail). The component now captures the invoking control before showModal and restores its focus after closing/unmounting, without scrolling. The dialog API in jsdom is explicitly modelled for this focus regression; this is not a claim about a real browser until the main deployment is checked.

The complete existing eight-file related set plus the added focus regression passes 60 tests. Production TypeScript/Vite build succeeds. The first five-test focused invocation used several unmatched historical filenames and is retained as such; it is not the 60-test suite.

## Main native-dialog result

Main 449f74fd serves index-DoZhRLAS.js. The actual 1280×720 browser displays the complete 860×313.25 dialog at x210/y203.375, with all three Chinese series and 61 unchanged samples. Escape and Enter both return focus to the invoking server. The final server in the long list also opens its own rack/server title; mouse close restores that server's focus and leaves the panel scroll position exactly unchanged.

Native Tab behavior is preserved honestly: one Tab places document.activeElement on BODY while the dialog remains modal; the following ordinary Tab, sent without locator refocusing, returns to the close button. We do not assert that every intermediate Tab keeps document focus inside the dialog. The actual screenshots visibly confirm the real series. Main and integration tracked React source, tests and i18n files are byte-identical.
