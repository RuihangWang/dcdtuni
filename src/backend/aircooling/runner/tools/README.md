# Runner tools

Operational scripts that drive the CFD pipeline from outside it.

They live here rather than in a top-level `scripts/` directory because
`.gitignore` carries a bare `scripts/` line, which matches at ANY depth — so
anything placed there is silently untracked. `regen_all_meshes.py` sat in that
hole and was never committed.

## regen_all_meshes.py

Re-meshes every `case/*` that has exported STLs and a `.cfdrun` through the
server REST API (the same path a user's mesh button takes) and applies two
gates per case:

- **Time.** The tool's own per-case budget, `--timeout` (default 300 s). A case
  still meshing at the budget is aborted through `/api/abort-job` and reported
  as a failure in the summary.
- **Patches.** Every STL region the runner declared as a boundary patch has to
  exist in `constant/polyMesh/boundary`, with faceZone baffles counted as their
  master/slave pair (`backend.aircooling.runner.mesh_acceptance`).

Note the difference from the product pipeline: since 2026-10-20 the runner's
five-minute mesh time is a **soft target** — timed and logged, never aborted
(`mesh_budget.py`). This tool still enforces its budget as a hard gate. For a
large case that is legitimately slower, raise `--timeout`; the next step is a
soft mode here as well (report SLOW, let the mesh finish, still run the patch
gate) so the batch tool and the product read time the same way.

```bash
# recommended: your own server through the launcher (another terminal), then point the tool at it
./src/start_server.sh --port 8120
python -m backend.aircooling.runner.tools.regen_all_meshes --port 8120 --cases Base_leakage,Slab
python -m backend.aircooling.runner.tools.regen_all_meshes --port 8120 --timeout 900 \
    --out _tmp/regen/report.json --log-dir _tmp/regen/logs
```

Login reads `DCDTUNI_ADMIN_USER` / `DCDTUNI_ADMIN_PASSWORD`, otherwise the
launcher's local-mode file `src/server/auth-local/admin_credentials`. Without
`--port` the tool starts `server.py` itself on a free port; that server only
comes up when the environment pair is set (it is not given the credential
file), so prefer `--port` with a launcher-started server. Next step: start that
private server through `start_server.sh --app api`, as `agent.scripted_api` does.

`--log-dir` keeps each case's full job log: every mpirun step overwrites
`Mesh/log.mpi_solver`, so the on-disk copy belongs to whichever step ran last.

Dataset directories (`case/GTC_dataset`, `case/Slab_dataset`,
`docs/manuscript/paper_experiment/case_*`) have no meshes and are not meant to
get any — pass `--cases` if a run must not touch them.

## mesh_source_support_probe.py

A read-only diagnostic: it reads one processor's binary polyMesh and the
prepared ASCII STL files and checks that each dcCart boundary face sample
projects onto its declared source solid within explicit tolerances. It is not a
mesh repair and not an acceptance gate on its own.
