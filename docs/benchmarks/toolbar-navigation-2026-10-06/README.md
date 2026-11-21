# Toolbar and orientation navigation — 2026-10-06

Top-level tools now have three equal-width disclosure buttons with one shared-width expandable panel. The cube is a persistent viewport child and remains visible when the toolbar is collapsed. The corner triad draws six directions in camera-space depth order, with rear dashed/hollow directions, an opaque origin hub and opaque foreground shafts.

The implementation freeze has 28 size/SHA-bound payloads. ROOT revalidated all payloads and all nine current source hashes before recording browser acceptance separately. An independent source review found no blocking issue; the original freeze's pending-browser statement is intentionally retained.

Validation: seven relevant test files, 56 tests pass; TypeScript and production build pass. ROOT operated the actual Windows in-app browser at port 8396 with the real 32-model Shinaide engineering scene: all tool groups, narrow layout, complete toolbar collapse, cube drag and face click. No captured browser console errors. Exact measurements and limits are in root-browser-acceptance.json.

This acceptance does not certify mesh generation or air convergence. Latest Shinaide P13 remains Failed2, with D831 and W82. Main/OV deployment regression is a subsequent receipt.
