# Plan-1 final review — deferred MINOR findings (M1–M5)

The Plan-1 final adversarial review (2026-06-12) confirmed 15 findings. The three
CRITICAL (C1 int/float byte divergence, C2 numeric-equipmentId physics loss, C3
broken `/api/browser/state`) and the IMPORTANT test/parity gaps (I1 value-canon,
I2 v2-leg coverage, I3 empty-container truthiness, I4 id-less probe crash, I5
pipe_junction coverage) were **fixed** (commits 307fbb6, e92a2f7).

These five MINOR findings are **deferred to Plan 2** — each is latent (no current
data triggers it), causes no data loss, and no JS/Python value divergence.

| # | File | Issue | Recommended fix |
|---|------|-------|-----------------|
| M1 | `js/cfdproj_schema.js:70` vs `:91` (+ Py `_v4_empty_document`) | `emptyDocument()` sets `uid:''`, which fails its own `validateDocument()` (`!doc.uid`). The test "emptyDocument validates clean" mutates `uid` first, masking it. No production path affected (migrator overwrites uid). | Seed a placeholder uid (`'proj_new'`) so the factory self-validates, OR rename the test and assert the factory is intentionally invalid until a uid is assigned. |
| M2 | `js/cfdproj_migrate.js:95-101` `_keyByName` (Py `_v4_key_by_name`) | Silently drops `name==null` specs and overwrites duplicate-named specs (last wins). Feeds `models.coolingModels.*` from `liquidCooling.specifications.*`. Identical JS/Py behavior; template libraries are out of the strict construction-losslessness scope. | Index fallback for null names + collision logging; mirror in Python. |
| M3 | `js/cfdproj_migrate.js:214-215` `_migratePlanar` (Py `_v4_planar`) | Unlike `_migrateFloor`, it does not rename opening-level `perforated -> perforation` (spec §6.5/§6.6), and `holes` overwrites `openings` when both present. No real planar model carries either; `perforated` is preserved verbatim. | Mirror `_migrateFloor`'s per-opening normalization; merge (or warn) rather than overwrite. |
| M4 | `test_cfdproj_migrate_v4.mjs` | `partitionWall` kind never directly asserted (only via shared `_migratePlanar` exercised by wall/baffle). No real case contains a `partitionWall`. | Add a `kind:'partitionWall'` fixture asserting geometry/cooling split + bucket. |
| M5 | `test_cfdproj_migrate_v4_py.py:19-23` | Hardcodes `BUCKETS` instead of importing the source of truth; can silently drift from `cfdproj_schema.js`. The JS test imports `S.BUCKETS`. | Use `cm._V4_BUCKETS` (or promote it to a public name). |

Note: M2/M3 list paths as `js/...`; the migrator actually lives at
`src/frontend/web/js/cfdproj_migrate.js` and `src/frontend/scripts/cfdproj_migrate.py`.
