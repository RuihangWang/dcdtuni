# dcCart at base 0.34 m: the two faces checkMesh refused, and the fix (2026-10-22)

**Fixed.** With the dc-builder example (`fixed_034.cfdproj`, a two-row room
with perforated blanking panels) meshed at `meshBaseSize` 0.34 m, strict
checkMesh failed one check: two faces with a volume ratio below 0.01 (minimum
0.00636). The cause is a corner crumb no merge can host (below). dcCart's new
terminal round, `baffleCrumb`, hands such a crumb to the solid when every face
it hands over keeps checkMesh's boundary skewness. The same project now passes
the strict gate. At 0.25 m it passed before as well, and the layout was never
the cause (stream B3 already showed that).

| checkMesh at 0.34 m | before (`checkMesh-geometry.txt`) | after (`checkMesh-geometry-after.txt`) |
|---|---|---|
| strict air mesh quality | refused (small volume ratio) | accepted, 0 failed checks |
| face volume ratio, minimum | 0.00636 (2 faces below 0.01) | 0.0178 (check OK) |
| face interpolation weight, minimum | 0.061 | 0.107 |
| cell volume, minimum | 6.24e-7 m³ | 1.40e-6 m³ |
| max skewness | 3.625 | 3.625 |
| max non-orthogonality | 87.14 | 87.14 |

The round's own line (`baffle-crumb-round.txt`): candidates 2, handed 2 to the
solid, none kept for skew. Both crumbs are the two mirrored corner pockets
described below.

## Reproduce

```bash
cd src
python -m backend.api air ../docs/benchmarks/dccart-034-volume-ratio-2026-10-22/fixed_034.cfdproj \
    --mesh-only --mesh-processes 8 --max-processes 8
```

(copy the project to a scratch case directory first; the API writes the case
there). `checkMesh-geometry.txt` and `dccart-merge-summary.txt` are the
relevant lines of that run's mesh log.

## The two cells

Both faces are on rank 1, the two copies of one corner mirrored about the
room's centre line (z = 1.66–1.667 and z = 3.333–3.34). For the first:

| Cell | Volume (m³) | Extent (x, y, z in m) | Relation |
|---|---|---|---|
| crumb | 6.24e-7 | 3.000–3.017, 0.5417–0.5471, 1.660–1.667 | the cell itself, 0.83 % of its level-3 hex |
| host across x+ | 9.81e-5 | 3.017–3.060, 0.500–0.547, 1.660–1.708 | **ratio 0.0064**: the failing face (5.4 × 6.7 mm) |
| below (y−) | 9.58e-6 | 3.000–3.017, 0.458–0.542, 1.660–1.667 | ratio 0.065 |
| beside (z+) | 7.80e-6 | 3.000–3.017, 0.542–0.547, 1.667–1.750 | ratio 0.080 |

Its other three faces are boundary faces: the Rack_A_3 frame (wall, z−), the
fan server's bottom (wall, y+) and the slave side of the 10U perforated
blanking panel (x−, a baffle). So the crumb is the corner pocket between the
blanking panel's plane, the rack frame and the server bottom, left by the
grid line at y = 0.541875 (a multiple of 0.34 / 64) 5.2 mm below the server
bottom at y = 0.547093.

## Why no merge removes it

dcCart merged 7846 slivers in this mesh; this one has no admissible host:

- **into the large host:** the union is the host's box with a notch at one
  corner, and the notch's faces have the union's centre on their outer side
  (negative face pyramids, a hard checkMesh failure);
- **into the cell below or beside:** the union is a convex box, but the face it
  then shares with the large host is off-centre: skewness ≈ 4.3 by checkMesh's
  formula (|d| = 0.038 m, face offset 0.033 m, floor 0.2·|d|), above checkMesh's
  limit of 4;
- **three-cell unions** (crumb + both small neighbours) are L-shaped and fail
  the pyramids the same way;
- **handing it to the solid** is not allowed for a cell with a baffle face,
  because the baffle is written as a master/slave pair, and removing one side
  would orphan the other.

We tried extending the final weight repair to volume-ratio faces, under its
own guards; on this mesh every union it tried was refused, for the reasons
above. That change was withdrawn, because there was no experiment showing it
helped.

## The fix: `baffleCrumb`

The last round of `mergeSmallCells` (`dcCartCutCells.C`, after `wedgeKill`;
setting `baffleCrumbFraction`, dcCartMeshDict key of the same name, default
0.01, 0 disables) admits a cell only when all of these hold:

- it is below 1 % of its hex and carries a faceZone (baffle) face, every one of
  them shared with a cell across the sheet;
- it shares a plain face with a neighbour more than a hundred times its volume:
  checkMesh's volume ratio fails there;
- it is not inside a thin closed shell, and its dominant boundary patch is a
  wall;
- every face it hands over, the zone face to the cell across the sheet included,
  keeps checkMesh's boundary skewness from the receiver's own centre within the
  merge limit (4).

The last condition is what the earlier baffle exemptions were guarding against:
killing a baffle-side wedge was measured on DAML to leave the receiver a micron
notch at its far corner (skew about 36). Here each crumb's handed faces are the
crumb's own millimetre faces, and the round would keep a crumb whose faces fail.
The panel loses the crumb's 3.6e-5 m² piece of sheet as wall, about 0.0025 % of
the 1.46 m² panel.

Like the other kill rounds, the decision is rank-invariant: the owner proposes
(cell, dominant patch) from geometry and global ids, the list is gathered, and
every rank applies all of it. When the round hands over nothing it changes
nothing, so a mesh that passes strict checkMesh today (and so has no face below
volume ratio 0.01) is unchanged; `release-remesh.md` records the ten release
cases re-meshed with the new binary.
