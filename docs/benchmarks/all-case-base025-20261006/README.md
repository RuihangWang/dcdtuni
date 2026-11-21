# Case base-size consistency (2026-10-06)

All 1,535 parseable normal case cfdproj files were inspected. Their meshConfig.meshBaseSize was already 0.25 m. 504 retained legacy runDefaults.simulationConfig.baseSize fields were 0.3; these were changed to 0.25 with full originals retained inside .codex-tmp/all-case-base-size-consistency-normal-20261006/before. Each migration changes only that numeric token and preserves all other bytes and permissions. The final census found 2,039 base-size fields, all 0.25. Historical cfdrun and result artifacts were not migrated.

Only three affected files are tracked. The Git patch changes the legacy field against HEAD, preserving pre-existing GTC_2026 working-tree changes as unstaged changes. Migration receipt and exact map bind all 504 before/after byte identities. This is configuration consistency evidence; it is not mesh quality, five-minute acceptance or CFD convergence evidence.
