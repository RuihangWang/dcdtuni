# Native dcCart source identity tests

These tests require OpenFOAM v2412 and a dcCart library built from the matching
source. Set `DC_CART_SRC` to that source tree's `src` directory,
`DCCART_TEST_LIBBIN` to its built library directory, and `FOAM_USER_APPBIN` to a
scratch bin directory. In this directory run `wmake -j1`.

```sh
DCDT_IDENTITY_PROBE=/absolute/scratch/bin/dccartSourceIdentityProbe \
  bash ./Allrun /absolute/new-scratch/parser-cases
DCDT_MESHER=/absolute/matching/bin/dcCartMesh \
  bash ./check-driver-rejection.sh /absolute/new-scratch/driver-case
```

`Allrun` performs 13 native constructor/metadata/query cases without generating
an octree, mesh or solution. It covers legacy absence, empty/partial metadata,
full records, path-like UIDs, repeated object UID with unique face UIDs, unknown
file/region, duplicate face UID, empty/blank UIDs, missing fields, unsupported
representation and unknown fields. It also checks that metadata loading leaves
sheet geometry and patch names/types unchanged.

The driver rejection regression uses an eight-cell empty-geometry fixture.
Before the handoff existed, the driver silently accepted metadata for an unknown
file and meshed the fixture; after the fix it must reject that metadata before
meshing. This is not a Shinaide mesh or a CFD solve.

Raw duplicate dictionary keys are outside this test contract: OpenFOAM's default
MERGE parsing has already combined them before the C++ loader sees the data.
The supported export path is the Python-validated map that cannot generate
repeated file/region keys. Duplicate sourceFaceUid values in distinct parsed
records are explicitly rejected. No global OpenFOAM parser mode is changed.

The ambiguity regression deliberately retains two geometry entries referencing
the same STL via the native entry list after parsing. Both surfaces are actually
loaded; the loader must reject the filename rather than bind the first one.
This does not claim detection of raw repeated metadata keys: duplicate region
blocks or duplicate literal fields inside one region may already have been
combined by OpenFOAM MERGE. That supported-export-path boundary is unchanged.
