# Toolbar V2 independent source review

Verdict: GO for the frozen two-product-path / one-test difference. No scoped
P1/P2 found. This is source acceptance, not a browser/CSS/ResizeObserver gate.
No tests, builds, API, browser, services, Git or business were run here.

The supplied 17 payloads (242,675 bytes) were rehashed, copied byte-for-byte,
and their original manifest retained. All three current after bodies matched.
The prior locale followup is already present in before PanelResizers89699d66…;
V2 does not alter it. The patch was read in full alongside complete resizer,
CSS toolbar rules, Toolbar structure, layout metrics and timeline context.

## Actual failure, independently inspected

The original normal-UI helper Toolbar_V1_WrapFailure_BrowserProof_Only.cfdproj
is an evidence-only saved project, not a physics case/input. Original515767B
SHAff4ea1ef44621c5d21d4afb967223ebbbf5412cea47ca7a1f3099eed0408db96
and its meta.description decoded string are preserved without re-encoding.
The existing English JPEG was inspected directly (not a fresh browser session):
the renderer OV connection row visibly crosses the View tools tab. At1280x720,
three tab rectangles have equal201.2px width but this does not prevent overlap.
The helper states the full V1 UI gate FAILED, errors[] is not a layout pass.
Expanded/minimum/English JPEGs and original base64 strings are preserved.

## Cause and bounded repair

- styles.css2843–2845: body is a column flex container; direct primary/groups
  children now flex-shrink:0. This prevents the wrapped primary height being
  squeezed while its renderer row overflows over the following groups. The
  renderer's own row width shrink/wrap rule2872 is retained; it is not a direct
  body child. Hidden sections keep display:none. No fontsize/scale squeeze.
- PanelResizers229–245: natural height is max(previous root scroll/rect,
  chrome + max(body.scrollHeight, body rectangle height) + independently measured
  timeline natural height + content rowGap). Body scrollHeight includes its own
  internal gap, outer footer gap is added once. Current body has no border or
  padding requiring another adjustment. The measure clears pinned toolbar height
  and footer max-height first, then restores prior inline values. Collapsed skips
  the body term and reserves MIN_SIZE.toolbar; the higher-specificity existing
  #toolbar.collapsed #toolbar-content rule at CSS254 continues to hide content.
- layoutMetrics118–129 remains exact: auto cap.28/manual cap.5, and natural /
  reserved bounds. Footer and32px body minimum/reservation are unchanged. If a
  footer exceeds the cap, existing footerScroll policy provides bounded own-scroll
  while body floor can shrink; ordinary footer controls retain normal heights.
- PanelResizers195 locale refit is retained, so translated wrapping invalidates
  the cache explicitly. Width ResizeObserver and class/open/hidden/child changes
  remain the same invalidation sources. V2 adds only numeric reads, no observer,
  listener, state or file work. Existing RO callback reuses cached natural height
  unless width changed; style/a11y writes are excluded from the MutationObserver
  filters. All observers/listeners/refit references have existing cleanup.
- Portal settings remain mounted to document.body in unchanged TimelineStrip;
  clipping of toolbar body/footer does not trap that portal. Existing placement /
  capture scroll / resize / Escape and outside handlers are unchanged. Cube and
  axis are outside this slice and were not accepted anew.

## Evidence boundaries

Original new mock regression RED1fail/6pass and logs are retained. Its
NoopResizeObserver and mocked root/body/footer dimensions prove the measurement
selection, not flex/grid rendering, real callback delivery, manual English
accessibility, or GPU. The first GREEN command contained a nonexistent
panelResizers.test.tsx path and actually ran only two files/28PASS; this limitation
is in the author's README and exact log. The corrected four named files show
37PASS (21layout +7remeasure +4focus +5viewbatch), as copied. No local rerun.

Next normal browser check: English/Chinese uniform and steady timeline, primary
and renderer row fully above group tabs, expanded groups scrolled rather than
compressed, min/manual/auto bounds and portal access, collapse/expand/locale and
side-width changes, no recurring RO-loop errors. The source GO does not replace
that existing actual UI requirement. Original source27, independent40 and this
V2 packet were not modified.
