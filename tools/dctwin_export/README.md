# dctwin evaluator export

`export.py` writes the design evaluator into a `cap-dcwiz/dctwin` checkout (branch `dev-stl`), for team members
who must not see the editor, display, AI inference or mesher sources. Owner decision 2026-11-01: option A (the
evaluator runs on the team's machines); dctwin's older content is not maintained; the export exists so ISPO can
later attach a differentiable OpenFOAM solver (DAFoam) to the same cases.

```bash
# from this checkout, with the dcdtuni Python; --source is a clean, committed checkout of the revision to export
python tools/dctwin_export/export.py --source _tmp/wt-ispo-export --dest ~/mycode/dctwin --dry-run
python tools/dctwin_export/export.py --source _tmp/wt-ispo-export --dest ~/mycode/dctwin
```

The source is normally the ISPO research branch with `main` merged in (the design modules live there).

| Goes out | How |
|---|---|
| `backend.api`, runner, OpenFOAM templates and solver sources, liquid DAE, `dcdesign`, `plant`, `zonal` | copied (INCLUDE) |
| server | `server.py` with the display, AI and release-stamp code cut out (`strip_python.py`), `auth_manager`, `security`, liquid modules; static root = the server's own strings |
| launcher | `templates/start_server.sh`: `--app api` only |
| project compiler | `src/headless/{compiler,builder}.min.js`: minified bundles of the two headless entries (`prebuild.mjs`; UI strings about the display and AI dropped), run by a rewritten `headless_compiler.mjs` |
| mesher | built `dcCartMesh` + `libdcCartMesh.so` only; refused unless built after the last source change and the source equals the exported revision |
| design base hall | the hybrid case under its anonymised path |
| modelling verbs | `docs/contract-verbs.md`, generated |

**The GPU air solver never goes out** (owner 2026-11-11: it and everything that names it are confidential and stay in
this repository). The adapter `runner/gpu_newton.py` is excluded, and the leak scan forbids `gpu_solver`,
`gpu_newton`, `gpuNewton`, `solverEngine` / `solver_engine` / `--solver-engine`, `GPU_SOLVER`, the solver's entry
name and its method name anywhere in the result. A revision that carries the solver therefore fails the scan (the
hooks in the runner, the scripted API and the server are still there) until a transform here cuts them; nothing
is written in that case.

Every text file is anonymised (case names, host and user names). The run stops before writing when an expected
text in a transformed file is missing (upstream changed) or the leak scan finds an excluded path or a forbidden
token; staging stays in `_tmp/dctwin-export-*` with `LEAK_REPORT.txt`.

Check after an export: run the shipped tests in the dctwin tree, compare `export-air` of one project from both
trees (STL and `.cfdrun` identical but for timestamps), and run one `backend.api air` end to end.

## The ispo profile: an evaluator without a server

`--profile ispo` writes the owner's evaluator folder inside the ISPO repository, with the GPU air solver. It
ships no server (owner 2026-11-12): `src/server`, the launcher and `backend/api/server_session.py` stay here.
What it ships instead comes from `templates_ispo/` (`ISPO_TEMPLATES` in `export.py`):

| Template | Shipped as | What it is |
|---|---|---|
| `direct_session.py` | `src/backend/api/direct_session.py` | the in-process run session: the server's `/api/run-mesh` job (publication of the inputs, the `run_cfd` command and its environment, the final status) and the liquid run endpoints, as functions |
| `dceval/` | `src/dceval/` | the front door: `evaluate(cfdproj, case_dir, solver=..., gpus=..., processes=...)` and `python -m dceval evaluate` |
| `test_direct_session.py` | `src/backend/tests/test_direct_session.py` | their tests (no mesher, no solver) |

The export rewrites one line of `backend/api/air.py` (`SESSION_MODULE`) so the air run takes that session. In
this repository the line names the server's client and `direct_session.py` is not under `src/`, so nothing here
can run a job without the server.

`direct_session.py` repeats server functions (`ported_server.py` lists them, `ported_server_functions.json`
holds the hash of each as ported). `test_ispo_profile.py` fails when one of them changes: read the change,
bring the port up to date, then `python tools/dctwin_export/ported_server.py --record`.

```bash
python -m pytest tools/dctwin_export          # with the dcdtuni Python
```
