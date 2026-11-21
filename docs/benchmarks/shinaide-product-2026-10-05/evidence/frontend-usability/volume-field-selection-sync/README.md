# Volume field selection and numeric precision

The ordinary CUA browser on main 217fd5ed served /assets/index-aqNcbVYG.js.
At 2026-10-05T00:55:56.554Z, choosing Umag without Apply reused the loaded
temperature grid and labelled 295.150–295.562 as m/s. Its sidebar retained
295.1 and 295.6 from the old field. The raw read-only DOM observation is
volume-field-before.json; no hidden browser state or API write was used.

The repair binds the rendered grid, fallback range and legend to its cached
field. Changing fields clears their limits and aborts an outstanding display
request. A late response cannot install its limits on another field.
Collapsing the settings section retains the legend for the visible volume.
Temperature inputs use two decimal places; other fields use the existing
significant-digit NumberField and scale-relative steps. An unedited focus/blur
preserves the underlying range. The completed-volume log also retains small
velocity bounds instead of displaying both as zero.

The final four component regressions all fail against the original main
PostprocessPanel (volume-field-final-baseline-tests.log), and pass after
the repair. They cover old-field removal, small velocities with raw-value
retention, cancellation of a late response, and collapse with a visible grid.
The separate log baseline fails the new small-speed case and the corrected
pressure precision expectation. Final related validation: 42 tests in eight
files pass; TypeScript and the production build succeed (745 modules, Vite
9.94 s, /assets/index-Xnznt4Ze.js). Existing jsdom canvas warnings in two
older component suites and the Vite chunk-size warning remain in the raw logs.

The first baseline used a host-scoped selector for a document-body portal and
failed at setup. That selector was corrected before the valid three- and
four-failure baselines. The first repaired build exposed a tuple-inference
TypeScript error, then an explicit tuple annotation fixed it. All original
logs are retained here. These are display tests, not CFD accuracy claims.
Main deployment and browser validation now complete on 0146e51c, with
main asset /assets/index-Co6H5t6A.js (main Vite build 10.20 s). Every tracked
React source and test file is byte-identical to the integration tree.
main-browser-receipt.json records K/F synchronization across three bars and six
inputs, full-value retention on untouched focus/Tab/Apply, removal of the old
temperature volume before speed Apply, the actual 64³ speed volume, native
Enter on the close button, and folding the configuration with its legend intact.
At 1280 px the three header right edges are 959.43/869.43/779.43 px,
with equal vertical alignment and no title overlap or horizontal page overflow.
The speed percentile window is 0.004117355681955814–0.005629195831716061 m/s;
the full resampled range in the log is 0.00337–0.00578 m/s. Both are preserved
without presenting one as the other. No new console error was recorded during
this reload and validation; two older errors remain explicitly in the receipt.

volume-speed-product-canvas.png is the real product-button screenshot,
917514 bytes, independently PNG-signature/hash checked. It contains the
rendered canvas only, not the surrounding controls or DOM colorbar overlays.
The whole browser layout was inspected inline and its header rectangles are
recorded; no standalone whole-UI screenshot file is claimed.
