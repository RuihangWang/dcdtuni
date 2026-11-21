# Rotated-rack mesh regression

Frozen HTML/React export from Tri_angle, 2026-09-23 05:25 UTC (refreshed from
the 2026-09-11 one, whose floor predated the thin-baffle coverage gate).
Inputs only; no generated meshes or solution fields. The test copies
`triSurface` into a temporary case's `Mesh/constant/triSurface` and uses the
copied manifest.

Exercises the missing fan-server bottom corners, the two zero-thickness rack
shelves, and alternating outlet columns after sliver merging. Both serial and
four-rank meshes are checked through the native OpenFOAM reader, at the
export's own sizing (0.25 m) and at 0.17 m.

Also pins the 2026-09-23 fixes on the two racks rotated by +-135 degrees:
the fluid wedge a server's outlet (or inlet) makes with the rail where it
pokes past a grid plane, the tip of the rail/inlet corner a grid plane passes
0.6 mm from (0.17 m), the floor faces beside a rack's foot, the blanking
panel passing 5.9 um from a grid corner, the u23 shelf carried onto a grid
plane by the first-layer floor (no flap past its rear edge), and the
outlet_p0 / outlet_wall boundary — in the bottom cell row, and on every row
of Rack_2 where the feature pre-split had already cut the cell along the
outlet plane.

The weld and the first-layer floor move horizontal planes onto grid planes
by design (up to firstLayerMax, 5 mm), so the checks allow a shift along y
and nothing else.

Run after building the repo's dcCartMesh:

```sh
DCDTUNI_RUN_OPENFOAM_TESTS=1 python -m pytest -q src/backend/tests/test_dccart_tri_angle_geometry.py
```
