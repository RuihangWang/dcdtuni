# Mesh control labels

Visible mesh labels now focus their existing controls. Refinement inputs name their geometry row and separate surface minimum, surface maximum and feature values; the rack-region scalar retains its own name. Location inputs include the location role and axis. No stored values, presets, geometry, layout, execution or project files change.

The existing extra-seed navigation and integer-refinement tests passed 11/11. The production build passed after fixing strict array indexing in the new accessible names; the initial type-check failure is preserved. Actual main-browser verification is recorded separately after deployment.

Actual main browser: all 37 inputs/selects have associated labels or explicit localized accessible names. Clicking the base-size label focused its input; native Tab moved to max cells. Before/after control values were exact, and the English panel had clientWidth=scrollWidth=280. Chinese was restored. The two earlier session errors remain in the console; no new error occurred in this verification. The screenshot was visually inspected inline, without a whole-UI image export. All 717 tracked React source/test files match main byte-for-byte.
