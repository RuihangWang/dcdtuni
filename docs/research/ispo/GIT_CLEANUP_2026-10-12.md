# Pro 6000 repository cleanup — 2026-10-12

The owner requested branch integration, removal of unnecessary material and Git synchronization after the editor reported too many active changes.

The initial inventory contained335058 untracked files (8291500395 bytes), mostly generated candidate projects and exports. Precise ignore rules cover only case/agent/*/agent_outputs and case/agent/*/selected. Existing tracked primary evidence remains tracked; case roots, real input projects, source, protocols, and evidence stay visible. Raw outputs, logs, checkpoints and failures are retained on disk. Git synchronization does not back up ignored runtime data.

All writing agents froze their changes before staging. Their incomplete readers and wing observer are explicitly listed in SOURCE_SNAPSHOT_STATUS_2026-10-12.md as drafts. Current E4, CPU20 and verification-mesh source bytes and running processes are preserved. Git publication is not scientific acceptance.

Only rebuildable ignored Python bytecode and redundant pytest current symlink aliases were removed. Alias targets and raw results were untouched. Exact inventories, pre-edit .gitignore and tracked patch are retained in _tmp/git-cleanup-2026-10-12_162541_SGT/. The small removal manifests and branch recovery material are archived in docs/archive/2026-10-12/branch-cleanup/.

Fetched origin/main is an ancestor of the current research line, so integration is a fast-forward. The superseded claude/admiring-ritchie-2ax67m software-WebGL branch has a verified bundle and patch before retirement; the current software-rendering option already has its own implementation and test. The active goal/air-20261005 and goal/integration-20261005 engineering branches are retained and their unmerged work is not adopted.

Final commit, push, clean-status and source-preservation checks are recorded in the closing receipt after actual completion.
