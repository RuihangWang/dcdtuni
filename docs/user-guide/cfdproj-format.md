# .cfdproj Format

A `.cfdproj` file is the editor's project file. It holds the editable model of the room — geometry, equipment, boundary-condition settings per surface, the liquid-cooling network, run defaults and, since version 5, the time-variable values of a transient scenario — so the user can close the browser and resume later.

!!! info "Current format: schemaVersion 5 (as of 2026-10-21)"
    The React editor saves **schemaVersion 5**: the v4 layout (`model.geometry.{location,size,quaternion,scale,faces,cutouts}`, physics at `geometry.faces[*].patches[*].physics`, models bucketed under `constructions.{racks,acus,baffles,floors,walls,…}`) plus a top-level `transient` block. Every time-variable value (server power, ACU supply temperature and airflow, cold-plate heat, pump speed, valve opening, …) lives there as keyframe tracks; a steady project has one keyframe at t = 0 per track. Files of every older version are migrated on load; saves always write v5.

    Field references: [.cfdproj v5](../reference/cfdproj-v5-schema.md) (what v5 adds, parameter registry, migration rules) and [.cfdproj v4](../reference/cfdproj-v4-schema.md) (the layout v5 builds on). The legacy HTML editor (`--app html`) reads and writes v4 only.

---

## What is a .cfdproj File?

A `.cfdproj` is a UTF-8 JSON document with these top-level keys, in this order:

```json
{
  "schemaVersion": 5,
  "uid": "…",
  "name": "MyRoom",
  "meta": { "inputUnit": "m", "casePath": "case/MyRoom", … },
  "geometry": { "height": 3.6, "plane": [ … ], "temperature": 22, … },
  "geometryTopology": { … },
  "models": { "geometryModels": { "racks": {}, "acus": {} }, … },
  "constructions": { "racks": {}, "acus": {}, "walls": {}, "baffles": {}, "floors": {}, "boxes": {}, "cdus": {}, "pipeSegments": {}, "pipes": {}, … },
  "instrumentation": { "probes": { … }, … },
  "runDefaults": { "meshConfig": { … }, "simulationConfig": { … }, "liquid": { … } },
  "transient": { "mode": "steady", "durationS": 600, "gridS": 1, "start": { … }, "tracks": [ … ] }
}
```

| Key | Content |
|-----|---------|
| `meta` | Project identity, input unit, the case directory (`casePath`, stored relative to the repository when the case lies inside it, so projects open in any clone) |
| `geometry` | The room: height, floor polygon, room temperature |
| `geometryTopology` | Optional declared relations between source surfaces (draft; air export refuses projects that carry it until its compiler exists) |
| `models` | Reusable templates (rack and ACU templates, equipment and material libraries) |
| `constructions` | Every placed object, bucketed by kind and keyed by `uid` |
| `instrumentation` | Probes, sensors and measured values |
| `runDefaults` | The editable defaults of a run: mesh and solver settings, the liquid experiment, an optional hybrid heat budget |
| `transient` | v5 only: steady/transient mode for air and liquid, duration, time grid, start condition and the keyframe tracks |

Camera, selection, panel layout and other UI preferences are **not** in the file; the editor keeps them per project `uid` in the browser's `localStorage`.

!!! warning "Slab_dataset stays on v2"
    The bulk training set under `case/Slab_dataset/` is kept on disk in the legacy v2 layout by design. These projects open in the editor (migrated on load), but their old v1 `.cfdrun` files are not runnable; open the project and re-export to run one.

---

## Relationship to .cfdrun

```
.cfdproj (editor project, saved on demand)
│  editable room model + run defaults + transient tracks
│
│  user starts Mesh / Solve (or the scripted API compiles the project)
│          │
│          ▼  the live project is materialised at t = 0 and compiled;
│             a transient air run also gets per-binding schedules
│
└──► .cfdrun (run manifest, regenerated for every run)
         STL references and per-patch bindings, source terms,
         mesh and solver settings, the geometry blob the mesh pipeline reads
         │
         ▼
     STLGenRunner → OpenFOAM
```

The `.cfdproj` is the editor's file; the `.cfdrun` is the frozen input of one run. The runner never reads the `.cfdproj`. Because the `.cfdrun` is built from the **live** editor state, unsaved edits reach the run, and `case/<name>/model/<name>.cfdrun` can be newer than the saved project. See [.cfdrun Format](cfdrun-format.md).

!!! tip
    When debugging a run, read the `.cfdrun` that the run executed (archived in `case/<name>/log/<timestamp>_<name>/model/`) rather than the `.cfdproj`.

The liquid side works the same way: the liquid experiment compiles the project into a `.liquidrun` (version 3, or 4 for a transient timeline) for the Newton DAE solver.

---

## Save and Load Behavior

**Saving**

- The editor does not auto-save. Click **💾 Save Project** (an asterisk marks unsaved changes). The React editor writes the file to a server path you choose (`POST /api/write-file`) or downloads it.
- The saved document is the whole project, not a delta. In v5 the time-variable fields are stripped from the models and saved only as tracks (`projectForSave` in `src/frontend/react/src/io/projectFiles.ts`).
- Saving refuses a project whose transient block, liquid experiment, hybrid heat budget or topology declaration does not validate, and reports the offending path.

**Loading**

- **📂 Load Project** opens a dialog that browses the server's file system (starting at the repository's `case/`) or picks a file from your own disk.
- The editor first checks that the JSON is a project at all (it must carry `constructions`, `construction`, `models` or `cfdRoom`), then migrates it: v1–v3 → v4 (`migrateCfdprojToV4`, `src/frontend/shared/js/cfdproj_migrate.js`), then v4 → v5 (`migrateCfdprojV4toV5`, `src/frontend/shared/js/cfdproj_transient.js`). The migration happens in memory; nothing is written until you save.
- In memory the editor keeps the time-variable fields materialised at the timeline's playhead, so the inspector and the 3D scene read ordinary model fields.
- A file with `schemaVersion` greater than 5 is refused.
- If the saved case directory does not exist on this machine, the editor asks for the case directory again.

---

## Schema Versions

| `schemaVersion` | Meaning |
|----------------|---------|
| absent / `1` | Legacy air-only project |
| `2` | Liquid-cooling kinds added (`cdu`, `crh`, `cold_plate`, …); flat top-level structure |
| `3` | Nested structure (`meta`, `model`, `construction[]`, `simulation`, `ui`, …). Reference: [legacy v3 schema](../reference/legacy/cfdproj-schema.md) |
| `4` | `geometry.*` model shape, `constructions.*` buckets, `runDefaults`; UI state moved out of the file (2026-06-14). Reference: [v4 schema](../reference/cfdproj-v4-schema.md) |
| `5` | **Current.** v4 plus the `transient` block; time-variable fields live only in tracks (2026-09-30). Reference: [v5 schema](../reference/cfdproj-v5-schema.md) |

The migrators exist in JavaScript (`src/frontend/shared/js/`) and as Python twins (`src/frontend/scripts/cfdproj_migrate.py`, `cfdproj_transient.py`); parity tests in `src/frontend/test/` keep the two in step. A file already at the target version passes through unchanged.

---

## Version Compatibility

| Opening… | in the React editor | in the legacy HTML editor |
|----------|--------------------|---------------------------|
| v1–v4 | migrated to v5 on load; saved as v5 | migrated to v4 on load; saved as v4 |
| v5 | opens as is | refused with a message to use the React editor (it has no timeline and would drop the tracks) |
| > v5 | refused | refused |

Unknown fields in a recognised version are carried through a load/save round trip. Do not edit migrated fields by hand; open the file in the editor and save it.
