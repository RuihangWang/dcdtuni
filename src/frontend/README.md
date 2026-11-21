# dcdtuni frontend

The visual side of dcdtuni: the editor that builds a data-centre model, exports
the CFD inputs (multi-solid ASCII STL plus a `.cfdrun` manifest), drives meshing
and solving through the Python server, and shows the results.

As of 2026-10-21 the product editor is the **React app** in `react/`. The legacy
single-file editor in `web/` is kept runnable as the reference implementation,
and `omniverse/` holds the Omniverse (Kit) display bridge and its reference demo.

## Start

From a fresh clone, prepare everything with the bootstrap (see
[fresh-clone deployment](../../docs/developer/fresh_clone_deploy.md)): it fetches
the tracked LFS textures, the locked React dependencies, a repository-contained
Python environment and both native build trees.

```bash
python3 tools/bootstrap_dcdtuni.py --dry-run
python3 tools/bootstrap_dcdtuni.py --install
python3 tools/bootstrap_dcdtuni.py --preflight
```

Then start through the launcher — it is the only supported way to serve any of
the frontends:

```bash
./src/start_server.sh                        # React editor + /api on one port, default 8000
./src/start_server.sh --build                # rebuild react/dist first (after a pull or an edit)
./src/start_server.sh --port 8010            # another port; WebSocket defaults to port + 1
./src/start_server.sh --dev                  # Vite with hot reload on --port, backend on --port + 2
./src/start_server.sh --app html             # legacy editor at /STL_generate.html
./src/start_server.sh --app react --ov --gpu 0 --build   # React + Omniverse display on one GPU
```

- Open the port the banner prints. The launcher frees ports held by your own
  processes and refuses ports held by another user — pick another `--port`.
- Login: with no credentials given, the launcher uses local mode and keeps the
  administrator pair in `src/server/auth-local/admin_credentials` (two lines,
  user then password). Set `DCDTUNI_ADMIN_USER` / `DCDTUNI_ADMIN_PASSWORD`, or
  pass `--admin-credentials <file>`, to choose your own.
- One server holds one global case directory. For parallel work, run one
  launcher per task on its own port.

## What is here

| Path | What it is |
|---|---|
| [`react/`](react/README.md) | The editor: React + TypeScript + Three.js r184. Scripts drive it through `window.__dcdtuni` (`react/src/automation/contract.ts`). |
| `shared/` | What both editors load: `js/` (the `.cfdproj` format modules, geometry builders, the `.cfdrun` builder), `i18n/`, `vendor/`, and `textures/` — the ONE material and texture pool: `materials.json` (`dcdtuni.material-library/v1`) names every rendered material, texture set, photo, patch preset and the shared lighting; both editors, the Omniverse bridge and Kit read it, so a change there shows in Three.js and OV alike. Model, geometry and format logic lives here once; each editor adds only a thin UI layer. |
| [`web/`](web/README.md) | The legacy HTML editor (`STL_generate.html`), served by `--app html`. Kept as the running reference. |
| [`omniverse/cfd_demo/`](omniverse/cfd_demo/README.md) | The Kit extension `dc.cfd_bridge`, the supervised Kit launcher used by `start_server.sh`, and the reference demo UI. |
| [`omniverse/project_page/`](omniverse/project_page/README.md) | The static SCANO project page. |
| `scripts/` | Python readers and migrators for `.cfdproj` (shared with the runner and the tests) and small debugging utilities. |
| [`test/`](test/README.md) | Node and pytest suites for the shared modules and guards on the legacy page. The React suite lives in `react/test/`. |
| [`Instruction.md`](Instruction.md) | The original Chinese walkthrough of the legacy HTML editor (historical). |

## How the frontend meets the backend

1. Build or load a project in the editor (`.cfdproj`, schema v4/v5).
2. Export: the editor writes the STL solids to `case/<name>/Mesh/constant/triSurface/`
   and the `.cfdrun` manifest to `case/<name>/model/`.
3. Mesh and solve: the server (`src/server/server.py`) hands the manifest to
   `STLGenRunner`, which meshes with dcCartMesh by default and solves with the
   repository-built `buoyantBoussinesq*Foam_new`.
4. Results land in `case/<name>/log/<timestamp>_<name>/`; the editor's
   post-processing panel (Three.js, or Omniverse with `--ov`) reads them there.

Scripts and agents can run the same chain without a browser through the scripted
API (`python -m agent.scripted_api air|liquid …`, see
[scripted-api.md](../../docs/reference/scripted-api.md)); it runs the editor's own
export modules headless.

## Related documents

- Repository overview: [`../../README.md`](../../README.md)
- User guides: [`docs/user-guide/`](../../docs/user-guide/index.md)
- Project status: [`docs/developer/PROJECT_STATE.md`](../../docs/developer/PROJECT_STATE.md)
