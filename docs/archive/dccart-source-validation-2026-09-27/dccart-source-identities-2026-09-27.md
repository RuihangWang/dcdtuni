# dcCart optional source identity handoff

This change only transports and validates provenance. It does not modify sheet
grouping, geometric tolerance, coverage, cutting, refinement, boundary conditions
or coordinates. It starts from b403ca63 and does not include candidate1028.

The geometry dictionary's owner (`snappyHexMeshDict` in the normal runner path,
`dcCartMeshDict` for direct geometry inputs) may contain:

```
dcdtuniSourceSurfaces
{
    merged_wall_baffle.stl
    {
        con_f
        {
            sourceObjectUid "m_22";
            sourceFaceUid "proj_Shinaide/m_22/source-sheet";
            representation zeroThicknessSheet;
        }
    }
}
```

The driver calls `dcCartSurfaces::readSourceSurfaceIdentities(dictionary)` after
loading geometry. The method returns the record count and replaces the optional
identity table after validation. Query with
`sourceIdentity(surfaceIndex, localStlRegionId)`, which returns a const pointer
or null for an unannotated region. These are local STL region ids, not global
patch ids. Invalid indices are rejected. A returned pointer remains valid until
the next successful metadata load or destruction of the surfaces object.
`info()[surfaceIndex].stlFile` identifies the actual geometry file key.

Validation requires actual loaded STL files and regions, literal dictionary
keys, exactly the three declared fields, nonempty/non-whitespace UIDs, globally
unique sourceFaceUid values, and representation `zeroThicknessSheet` or
`surfaceFace`. sourceObjectUid can repeat across a physical object's faces.
UIDs are Foam strings, preserving slash-delimited paths. Representation is
stored as metadata; it does not change or infer wall/carving physics.

The entire block is optional. Absent, empty and partial blocks preserve legacy
geometry and leave unannotated regions without identity. Provided records must
be complete. No `.cfdproj` lookup or geometric pairing is inferred here. The
Python producer must remap original exported files to the actual mesh-stage
files before writing the block.

## Strictness boundary

OpenFOAM defaults to MERGE when parsing duplicate dictionary keys. Once an
IOdictionary has been built, repeated raw file/region keys cannot be recovered.
This first phase supports the Python-validated export map, which cannot emit
those duplicate keys; it does not claim to reject arbitrary hand-written
repeated keys or change the global OpenFOAM parser. Repeated sourceFaceUid values
in distinct parsed records are rejected by the C++ loader.

## Evidence (CPU only)

- RED: the previous driver accepted `ghost.stl` metadata and returned0 on the
  eight-cell native fixture (0.012s).
- GREEN: 12 native constructor/parser/query tests and the actual driver rejection
  check passed. Legacy absence, empty and partial blocks were checked; repeated
  object UID and slash-containing face UIDs were round-tripped.
- Actual prepared Shinaide dictionary and STL files were copied byte-for-byte
  from `/mnt/lv/home/litianyu/benchmarks/dcdtuni_shinaide_source_dict_trial_20260927/Mesh`.
  The probe only constructs surfaces and reads metadata: **IDENTITIES284**,
  **GEOMETRY_UNCHANGED**,0.20s,31,268KiB maximum RSS, one CPU/nice10/CUDA hidden.
  It returned m_22 / `proj_Shinaide/m_22/source-sheet` for con_f in
  `merged_wall_baffle.stl`, and m_52 / `proj_Shinaide/m_52/source-sheet` for
  Raised_Floor in `wallBaffle_floor_Raised_Floor.stl`.

Full logs, matching compiled artifacts and copied input hashes are under40901
`/mnt/lv/home/litianyu/benchmarks/dccart_source_identity_evidence_20260927/`:
`red.log`, `green.log`, `green-driver.log`, `shinaide-native.log`, and
`native-shinaide/input-sha256.json`. No full Shinaide meshing or solve was run.
Native source and build instructions are in
`src/backend/tests/native/dccart_source_identity/`.

## Incremental ambiguous-file rejection

A filename must match exactly one loaded surface. The loader now scans all
surface entries and rejects a second match with `ambiguous STL file`; it no
longer silently selects the first match. The native fixture explicitly retains
two geometry entries referencing the same actual STL after parsing, and loads
both surfaces. Before this fix it returned one identity on surface0 and none
on surface1 (12 tests passed, the ambiguity case failed). After the fix all
**13 native tests pass**; the prepared Shinaide constructor check still reports
**284 identities and GEOMETRY_UNCHANGED**. Compilation used at most4 CPU cores,
nice10, CUDA hidden; no meshing or solve was added. Logs are
`ambiguity-red.log`, `ambiguity-green.log`, and `ambiguity-shinaide.log` under
the evidence directory above.

This runtime ambiguity is different from repeated literal metadata keys. In
`panels.stl { front_a {...} front_a {...} }`, or when a field appears twice
inside `front_a`, OpenFOAM MERGE may have combined the input before this loader
runs. Such raw duplicates cannot be detected retrospectively; production
continues to rely on the Python-validated map. No global parser policy changed.

After the React exporter added 70 rack blanking sheets, a new Shinaide-derived
test copy passed export-time hashes and per-solid positive area for 354 records.
Its prepared dcCart dictionary contains 354 unique mesh-stage mappings;
`foamDictionary` parsed all 354, and the native constructor probe returned
`IDENTITIES 354` and `GEOMETRY_UNCHANGED`. This is still a provenance read,
not a mesh-quality or CFD solve result. See
`docs/benchmarks/shinaide-blanking-source-binding-2026-09-27.json`.
