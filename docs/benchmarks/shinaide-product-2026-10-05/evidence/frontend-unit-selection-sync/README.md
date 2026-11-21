# Global temperature unit and directory Enter guard

This receipt covers the React semantic fixes only. No browser, native solver, API, or case execution was performed here.

While a new directory listing was loading, the global Enter handler could confirm an old directory or previously selected file despite the disabled Confirm button. Enter now follows the same busy guard; Escape still cancels.

Temperature colorbars, the parent postprocess panel, and the slice editor now subscribe to one global unit selection. The colorbar owns its current unit label, so an old parent label cannot disagree with its ticks or dialog. Finite pending colorbar range edits convert from their previous explicit unit to the new unit, preserving Kelvin meaning. Focused temperature NumberFields remount on a unit change, discarding an uncommitted draft that would otherwise be interpreted in the new unit. Generic NumberField source, display precision, and steps are unchanged.

The original two-file baseline has 7 failures and 3 passes. The final direct semantic suite has 19 passes; the related 10-file regression suite has 68 passes. Production typecheck and Vite build pass. The first post-fix test run exposed an overprecise test expectation for the existing one-decimal volume display; the corrected assertion also verifies that no-edit focus/blur preserves the original Kelvin value. The first build required explicit non-null test mock indexing. Both first attempts remain recorded. Raw stdout whitespace and final blank lines remain byte-for-byte; the scoped source/tests/README/JSON diff check passes.

Raw output: [baseline](baseline-tests.txt), [first fixed target](first-fixed-target-tests.txt), [final target](fixed-target-tests.txt), [related regression](fixed-regression-tests.txt), [first build](first-production-build.txt), [production build](production-build.txt). [receipt.json](receipt.json) identifies scope, source paths, unchanged NumberField hash, and generated bundle identities. [sha256.json](sha256.json) binds source, tests, and these raw files.

Reproduce from src/frontend/react:

    npm test -- test/FileBrowserInteractions.test.tsx test/components/PostprocessUnitSync.test.tsx test/ppUnits.test.ts --maxWorkers=1 --minWorkers=1
    npm test -- test/FileBrowserInteractions.test.tsx test/fileBrowserModel.test.ts test/components/PostprocessUnitSync.test.tsx test/ppUnits.test.ts test/ppNumberFormat.test.ts test/components/NumberField.test.ts test/components/NumberFieldRefusal.test.tsx test/components/PostprocessTimeSelection.test.tsx test/components/PostprocessPlayhead.test.tsx test/legacy/colorbarGlobal.test.ts --maxWorkers=1 --minWorkers=1
    npm run build

All new artifacts and TMPDIR were inside the integration worktree. Subsequent real-browser product verification belongs to the main integration campaign and is not claimed by these component tests.
