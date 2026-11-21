# Contributing

How to set up a development checkout, where the code lives, how to run each test suite and how to test changes the way users run them. This page reflects the repository as of 2026-10-21. Repository-wide rules for agents and contributors are in `AGENTS.md`.

## Development Setup

Follow [Installation](../user-guide/installation.md): `python3 tools/bootstrap_dcdtuni.py --install` creates the repository environment at `.codex-tmp/envs/dcdtuni` (Python 3.10, OpenFOAM 2412, MPICH), installs `backend` editable with the liquid extra, builds the React editor and the native trees. Machines set up earlier with a named Conda environment `dcdtuni` keep working; the launcher falls back to it.

In the commands below, `PY` is the environment's Python:

```bash
PY=.codex-tmp/envs/dcdtuni/bin/python      # or: conda run -n dcdtuni python   (older machines)
$PY -c "import backend; print(backend.__version__)"
```

## Code Structure

| Directory | Purpose |
| --------- | ------- |
| `src/backend/aircooling/runner/` | Air pipeline: `STLGenRunner`, manifest schema and reader, BCs, fields, receipts, convergence |
| `src/backend/aircooling/third_parties/` | Native code built from this repository: `dcCartMesh/`, `foam/` (`*_new` solvers, templates) |
| `src/backend/liquidcooling/` | Liquid solver: Newton DAE (production), runtime sessions, schemas, verification |
| `src/agent/scripted_api/` | Scripted API (`python -m agent.scripted_api`) |
| `src/server/` | REST server (`server.py`), security, liquid and render adapters, server tests |
| `src/frontend/react/` | The editor: React + TypeScript + Three.js; automation contract `window.__dcdtuni` in `src/automation/contract.ts` |
| `src/frontend/shared/` | Code and assets both editors use: `js/` (project format, migrations, geometry builders, export), `i18n/`, `transient/`, `vendor/`, and `textures/`, the one material pool (`materials.json` drives both editors and Kit) |
| `src/frontend/web/` | The legacy single-file editor, kept as a reference implementation (`--app html`) |
| `src/frontend/omniverse/cfd_demo/` | Omniverse display bridge (Kit extension) and the standalone demo |
| `src/agent/` | The slim agent layer on `agent.scripted_api`: edits, evaluations, budgets, records, optimizers, the air-v1 scenario and its design compiler (its own README); the pre-2026-10-22 tree (removed) is kept at the tag `agent-pre-slim-2026-10-22` |
| `tools/` | Bootstrap and maintenance tools |
| `docs/` | Documentation (MkDocs site) and dated evidence receipts in `docs/benchmarks/` |
| `case/` | Example and release cases |

**Two editors, one model.** The React editor is the product. The legacy page gets no independent features, but a feature may land in both in sync. Model, geometry, migration and export logic lives **once** in `src/frontend/shared/js/`; each editor adds only a thin UI layer (React components; legacy panel HTML and `window.*` handlers).

## Working in the Repository

Work is committed directly on `main` in small, verified commits. Several sessions may share one working tree, so stage and commit only your own paths (`git commit -m "…" -- <paths>`) rather than `git add -A`. Keep temporary files in `_tmp/` (Git-ignored) inside the checkout.

## Testing

The suites are separate; there is no single command that runs them all. Run each from the repository root. On a shared machine limit CPU use (`taskset -c <cores>`, `OMP_NUM_THREADS=2`) and hide GPUs unless you mean to test them (`CUDA_VISIBLE_DEVICES=`).

| Suite | Location | Command |
| ----- | -------- | ------- |
| Backend (runner, schema, liquid, API) | `src/backend/tests/` | `$PY -m pytest src/backend/tests --basetemp=_tmp/pt` (also the default `testpaths`, so plain `$PY -m pytest` runs it) |
| Server | `src/server/tests/` | `$PY -m pytest src/server/tests --basetemp=_tmp/pt-server` — run it **separately** from the backend suite (see below) |
| React editor | `src/frontend/react/test/` | `cd src/frontend/react && npx vitest run` (same as `npm test`), and `npx tsc --noEmit` for types |
| Shared JS modules (Node) | `src/frontend/test/*.mjs` | `(cd src/frontend && npm ci)` once for jsdom, then `node --test src/frontend/test/*.mjs` |
| Frontend Python parity tests | `src/frontend/test/*.py` | `$PY -m pytest src/frontend/test --ignore=src/frontend/test/test_browser_state_endpoint.py` |
| Tools | `tools/test_*.py` | `$PY -m pytest tools` |
| Omniverse bridge | `src/frontend/omniverse/cfd_demo/tests/` | `$PY -m pytest src/frontend/omniverse/cfd_demo/tests` |
| Agent layer | `src/agent/tests/` | `$PY -m pytest src/agent/tests` (CPU; `DCDTUNI_AGENT_SMOKE=1` adds the real mesh/liquid smokes) |

Notes:

- **Backend and server tests in one command fail to collect.** `src/server/tests/conftest.py` registers `server.py` as the module `server`, while some backend tests import `server.<submodule>` as a package. Run the two suites in separate commands.
- **`pytest-timeout`**: use `--timeout-method=thread` (or no `--timeout`). The default signal method collides with the runner's own alarm-based guard in the mesh CLI tests and produces false failures.
- **Opt-in tests**: CUDA, native OpenFOAM kernels, real meshing (`test_dccart_tri_angle_geometry.py` runs `mpirun` with many ranks) and timing benchmarks skip unless their environment variables or binaries are present. Read the skip reason before assuming a gap.
- **Tests that touch services**: `test_browser_state_endpoint.py` logs in to a running server and changes its case directory; the Omniverse launcher tests (`test_launcher_lifecycle.py`, `test_launcher_cleanup_ownership.py`, `test_ov_startup_lifecycle.py`, `test_ov_stream_port.py`) start launchers and free ports with `fuser`. Run them only against your own ports.
- Some frontend parity tests walk every `.cfdproj` on disk, including Git-ignored local datasets; failures that appear only on untracked local data are about that data.
- Test counts change quickly; read them from the runner output rather than from documents.

### Test through the server, as users do

Mesh, solve and export are tested through the server, never by calling the runner or OpenFOAM by hand (unit tests excepted). Start a server on your own port and drive it with the editor, `browser-use`, the REST API or the [scripted API](../reference/scripted-api.md):

```bash
PORT=8005
./src/start_server.sh --port $PORT &                 # local-mode credentials in src/server/auth-local/
CRED=src/server/auth-local/admin_credentials
curl -s -c _tmp/cookie.txt -X POST http://localhost:$PORT/api/auth/login \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"$(sed -n 1p $CRED)\",\"password\":\"$(sed -n 2p $CRED)\"}"
curl -s -b _tmp/cookie.txt -X POST http://localhost:$PORT/api/set-case-dir \
  -H "Content-Type: application/json" -d '{"caseDir":"/abs/path/to/case/Base_leakage"}'
curl -s -b _tmp/cookie.txt -X POST http://localhost:$PORT/api/run-mesh \
  -H "Content-Type: application/json" -d '{"meshOnly": true}'          # returns {"jobId": "<id>"}
curl -N -b _tmp/cookie.txt "http://localhost:$PORT/api/log-stream?job_id=<id>"
```

The cookie is named per port (`stlgen_session_<port>`); `curl -c/-b` handles it. A `/api/run-mesh` body without `cfdrun`/`stlFiles` uses the manifest and STL files already in the case; the editor (or `/api/browser/*`, or the scripted API) is what exports fresh ones from a project.

After changing `server.py`, restart through the launcher:

```bash
fuser -k $PORT/tcp $((PORT+1))/tcp
./src/start_server.sh --port $PORT
```

Front-end changes are verified in a real browser: a programmatic check plus a screenshot read back (see the `browser-verify` skill). The Omniverse viewport is verified only in a head browser.

## Code Style

- **Python**: no formatter is enforced; match the surrounding code, keep functions named by what they do, and write tests first for bug fixes.
- **TypeScript / React**: strict TypeScript; every project mutation goes through `projectStore.edit()` (which takes the undo checkpoint); scripts use `window.__dcdtuni`, never internal state.
- **Shared JS** (`src/frontend/shared/js/`): UMD modules loaded by both editors and by Node tests; keep state JSON-serialisable.
- **Legacy page**: the coding rules in `AGENTS.md` (undo checkpoints, tab-scoped helpers, `window.t`) still apply to `STL_generate.html`.
- **Documentation**: keep a document's language (English or Chinese); describe how things work now and what the next step is; put dated receipts in `docs/benchmarks/` and dated handoffs or plans in `docs/archive/`. The site builds with `pip install -r docs/requirements.txt` in a separate virtual environment and `python -m mkdocs build --strict`.

## Common Pitfalls

| Problem | Cause | Fix |
| ------- | ----- | --- |
| Mesh refused before it starts | Case path contains a space, quote or semicolon | Rename the case directory |
| Solve refused: no mesh quality record for this mesh | A hybrid / source-surface mesh or its input changed after the mesh was checked (a failed strict `checkMesh` alone never refuses a solve) | Re-mesh and read `mesh-quality-receipt.json` |
| Run refused: execution fields disagree | A hand-edited `.cfdrun` changed only the root (or only the blob) copy of `meshProcess`, `endTime`, … | Edit the project and re-export instead of editing the manifest |
| `buoyantBoussinesq*Foam_new` not found | Repository solvers not built | `(cd src/backend/aircooling/third_parties/foam && ./deploy.sh)` |
| Verdict `INCONCLUSIVE` | No return-air probes in the case | Add monitoring above the ACU returns |
| `mpirun` hangs in a terminal | Terminal I/O buffering | Use the server or scripted API, not standalone `mpirun` |
| `ModuleNotFoundError: backend` | Wrong Python | Use `.codex-tmp/envs/dcdtuni/bin/python` (or the named `dcdtuni` env) |

## Process Safety

!!! danger "Never use kill/pkill/killall/pgrep"
    On shared machines they can terminate VS Code Remote Server, JupyterLab and other users' work.

    **Only use** `fuser -k <port>/tcp` for the ports you started, for example:

    ```bash
    fuser -k 8005/tcp 8006/tcp
    ```
