# `src/frontend/test` — tests for the shared frontend code

These suites test the code BOTH editors use — the `.cfdproj` / `.cfdrun` format
modules in `src/frontend/shared/js/` and their Python twins in
`src/frontend/scripts/` — plus a few guards on the legacy HTML page. The React
editor's own suite is elsewhere: `src/frontend/react/test/`, run with
`npx vitest run` from `src/frontend/react`.

## Running them

```bash
node --test src/frontend/test/*.mjs                    # the shared JS modules + legacy-page guards
python -m pytest src/frontend/test                      # the Python migrators/readers and JS↔Python parity
python -m pytest src/frontend/test --ignore=src/frontend/test/test_cfdproj_transient_parity.py   # quick run (skips the slow parity sweep)
```

Run pytest from the repository root inside the `dcdtuni` environment; the parity
tests call `node`, so it must be on `PATH`. As of 2026-10-21 the node suite is
all green and the quick pytest run is green apart from the skips listed below.

## What is here

| Files | What they check |
|---|---|
| `test_cfdproj_schema.mjs`, `test_cfdproj_state_v4.mjs`, `test_cfdproj_migrate*.mjs`, `test_cfdproj_transient.mjs` | the `.cfdproj` format modules: validation (v4 and v5), state shape, migrations, the v5 transient tracks |
| `test_cfdrun_schema.mjs`, `test_cfdrun_export.mjs`, `test_pressure_linear_schema.mjs` | the `.cfdrun` builder and schema |
| `test_model_library.mjs`, `test_patch_filter.mjs`, `test_patch_selection.mjs` | shared model-library and patch helpers |
| `test_html_*.mjs` | guards on the legacy page: refusing a v5 project (`test_html_v5_guard.mjs`), case-path restore, rack face labels, the post-processing patch tree, solve partitions |
| `test_cfdproj_migrate_py.py`, `test_cfdproj_migrate_v4_py.py`, `test_cfdproj_transient_py.py`, `test_load_cfdproj.py`, `test_migrate_patches.py`, `test_mirror_outlet_patches.py`, `test_cmi_germany_yup_conversion.py` | the Python migrators and readers the runner and tools use |
| `test_cfdproj_migrate_parity.py`, `test_cfdproj_migrate_v4_parity.py`, `test_cfdproj_transient_parity.py` | JavaScript and Python produce the same document from the same input |
| `test_browser_state_endpoint.py` | the server's `GET /api/browser/state`; runs only when a server is up |
| `e2e_tests.sh` | an old browser-use script for the legacy page (see below) |

The legacy page's 26 source-scanning tests and 3 runtime tests were deleted with
the cutover (`ebed328e9`, 2026-08-31); the tag
`legacy-html-editor-last-working` still has them.

## Skips, and how to bring them back (measured 2026-10-21)

- **`test_cmi_germany_yup_conversion.py` — 26 skips.** Its fixture
  `case/CMI-Germany/CMI-Germany.cfdproj` is gone (the leftover case directory
  was removed on 2026-10-19). The assertions stay as the specification of that
  conversion and run again once a fixture is restored.
- **`test_cfdproj_migrate_v4_py.py` — 2 skips.** The two tests that migrate a
  REAL v3 project look only at the five `case/` projects in `CASES`, which were
  migrated to V4 in place at `b821e27`, so the skip reason says no v3 project is
  left. That is not quite true: the 16 label projects under
  `docs/manuscript/paper_experiment/case_*/label/` are still `schemaVersion 3`
  with a `construction` list. Next step: point `CASES` at those labels (copies,
  never migrated in place) and the two tests run again.
- **`test_browser_state_endpoint.py` — 1 skip without a server.** It drives
  `DCDTUNI_PORT` (default 8000) and skips unless a server of this user listens
  there; it logs in with `DCDTUNI_ADMIN_USER` / `DCDTUNI_ADMIN_PASSWORD`, else
  the launcher's local-mode file (`src/frontend/scripts/admin_login.py`). To run
  it, start `./src/start_server.sh --port <port>` and set `DCDTUNI_PORT` to the
  port its banner printed (passes against a local-mode server, 2026-10-22).
- **`test_cfdproj_transient_parity.py` reads the TRACKED `case/**/*.cfdproj`** (`git ls-files`;
  every project on disk only outside a checkout). Until 2026-10-22 it read every file on disk, so
  the git-ignored `case/Slab_dataset/` — about a thousand `schemaVersion 2` dataset projects the
  v4→v5 migration never reads — failed there. The tracked set is large (the `case/agent/`
  candidates), so this file is the slow part of the run.

## `e2e_tests.sh`

It opens `STL_generate.html` through browser-use and writes a pre-V4
`state.models` array (`id/anchor/base/faces`). The legacy page has stored a V4
`{materialModels, geometryModels}` object since the V4 refactor, so the script
would overwrite that state and its assertions no longer mean anything. Next
step: move the pure-function cases that are still useful into a `node --test`
`.mjs` file and retire the script.

## Ports

Nothing here should drive someone else's server. The runtime scripts read
`DCDTUNI_PORT` (default 8040), and a server holds ONE global case directory, so
start your own:

```bash
./src/start_server.sh --app html --port 8040 --case-dir /abs/case    # for the legacy-page scripts
DCDTUNI_PORT=8040 bash src/frontend/test/e2e_tests.sh
```
