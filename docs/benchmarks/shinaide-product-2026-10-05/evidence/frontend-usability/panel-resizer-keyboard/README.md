# Keyboard-accessible panel resizing

Four existing resize edges now expose named, focusable separators with orientation, controlled panel, localized keyboard help and actual drawn size/bounds. Arrow keys move the edge by 10px; Shift uses 40px. Wrong-axis arrows do not change sizes or propagate to viewport shortcuts. Focus and quiet theme-colored grips make the edges discoverable.

The existing automatic layout stays active until a side is manually resized. That side then overrides the automatic 23vw share, while the CSS 260px viewport gap and responsive fit remain in force. Steps start at the drawn rectangle, so the default 294.4px side at a 1280px viewport can actually become 304.4px. Explicit manual width survives App's collapse reset and a resizer remount. Side and toolbar collapse remain non-resizable; the existing 28px log can reopen. The short-window log minimum wins over its percentage cap.

Initial focused regression: 17 failures and one pass in the original 18 keyboard cases. Two later collapse/remount regressions also failed before manual-width retention. Final scope: 22 new keyboard cases and 22 existing layout/toolbar cases, all 44 pass; TypeScript and production Vite build pass. CPU36-39, two Vitest workers. No service, native run, API, browser or index operations.

The independently found toolbar border mismatch also has a before-fix failure: scrollHeight100 versus a natural102.25px rectangle (100.25px content and two1px borders) reported aria max100. Natural measurement now includes actual computed borders and the unpinned fractional border-box rectangle. The same case passes, including exact10px keyboard steps back to its true natural maximum. Its separate raw failure invocation is appended to baseline-keyboard.log; receipt preserves the SHA of each original raw invocation.

Reproduction from src/frontend/react:

    taskset -c 36-39 node node_modules/vitest/vitest.mjs run test/panelResizerKeyboard.test.tsx test/toolbarRemeasure.test.tsx test/layoutMetrics.test.ts --maxWorkers=2 --minWorkers=2
    taskset -c 36-39 npm run build

The dedicated DOM tests use a stated CSS geometry model because jsdom does not lay out CSS. They also assert the actual per-side CSS rule. These are source/component proofs; real browser focus/rectangle proof is pending root. In the actual UI use ArrowRight on the left edge, ArrowLeft on the right, ArrowUp to shrink the toolbar, and ArrowUp to grow the log. Confirm aria-valuenow against the rectangle. Collapse the side/toolbar and confirm that its separator is hidden, then reopen a manually sized side.

Manual layout restoration for the browser check must remove both that side's --SIDE-panel-share and --SIDE-panel-manual-width custom properties before restoring its original requested width. This intentionally resets the explicit preference and returns to automatic sharing.
