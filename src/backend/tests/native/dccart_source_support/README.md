# Native dcCart source-support RED

This fixture requires the matching OpenFOAM v2412 environment and a source-matched
`dcCartMesh` executable. It changes no project, STL, mesher setting or quality
threshold. Generate two identical orthogonal zero-thickness sheet geometries:
one against grid planes, one away from them. `Allrun` meshes each on at most
four CPU cores and checks exported STL triangle support at the `02baffles`
debug stage. No CFD solver is run.

```bash
export DCDT_MESHER=/absolute/path/to/dcCartMesh
export DCDT_CPUS=0-3
bash ./Allrun /absolute/new/scratch/path
```

At commit `d449aca9`, the control passes and the near-grid case exits **2**:
12 unique zone faces, 3 outside source support, maximum in-plane gap about
2.195 mm. The output JSON is `<scratch>/near-grid/stage-support.json`.
This is a deliberately failing invariant. After a safe algorithm repair the
same command should exit zero; a changed failure count alone is not a pass.

`check_stage_support.py` can inspect a targeted `iface_dbg` stage from other
cases with explicit `--map ZONE:STL:SOLID` bindings and optional `--roi`.
It checks face centroids only. If no mapped faces are found, it errors rather
than accepting an empty sample. The Shinaide stage mapping and limits are in
`docs/archive/dccart-source-validation-2026-09-27/dccart-stage-support-trace-2026-09-27.md`.
