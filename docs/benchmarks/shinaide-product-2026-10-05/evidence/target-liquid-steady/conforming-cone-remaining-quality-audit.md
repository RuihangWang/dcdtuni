# Remaining experimental cone quality failures

Actual job 421ad7b3 remains rejected by full checkMesh. The read-only tool reproduces all 3353 low-determinant cells, 984 low-weight faces, 68 low-volume-ratio faces and two skew faces using installed OpenFOAM v2412 definitions. Minima are 4.704352046365065e-11 (determinant), 0.002299159177636109 (weight), and 0.0023044574922382473 (volume ratio); maximum skewness is 4.9017183578043415. No mesh, STL point, solver, quality threshold or native source was changed.

All low-determinant cells are 2174 tetrahedra or 1179 quadrilateral pyramids. Their internal/coupled face counts are three (1328), four (1106), or five (919). All 984 low-weight faces and all 68 low-ratio faces are internal; the low-ratio set is contained in the low-weight set. The other two sampled faces are ACU_01_Front_part1 and ACU_01_Back_part1 wall triangles.

## Concrete geometry and scope

Rank 3 face 1468042 joins quadrilateral pyramids 498443 and 498446. Its normal owner and neighbour centre distances are 54.0083696 micrometres and 23.4364790 millimetres; volumes are 1.7135050839458874e-7 and 7.435611590655176e-5 cubic metres. Owner 498443 has a rectangular geometric base 47.2221746 by 15.4296875 millimetres. The opposite vertex has normal height 0.7055106 millimetres and a projection extending 60.1511936 millimetres beyond the base support line. This indicates both a small height and a large tangential offset.

Rank 1 cell 526531 has one physical triangular face, 1918567 on ACU_01_Front_patch. Its triangle has minimum/maximum edges 1.8370567/66.4607872 millimetres and minimum angle 0.4080713 degrees. The opposite vertex height is 7.71484375 millimetres and its projection extends 30.9236712 millimetres outside the triangle. This differs from the preceding rectangular-base example: a small base angle already constrains attainable shape quality.

Of the 1179 quadrilateral pyramids, 1169 opposite-vertex projections lie outside their geometric base. Of 1328 tetrahedra with exactly one physical triangle, 1307 projections lie outside that triangle. For the other 846 tetrahedra all four base possibilities are recorded. These counts describe written geometry, not original cone parent identities. Shared vertices do not establish common ancestry. Actual pre-decomposition-parent to written-cell tracing is unavailable for this run, so cross-parent volume-ratio remediation cannot be claimed.

## Fixed triangular base: a bound for every possible apex

This proof applies to a three-dimensional tetrahedron with exactly one noncoupled physical triangular base and three selected internal/coupled side faces. The installed checker excludes the physical base: polyMeshCheck.C calls syncTools::getInternalOrCoupledFaces; syncTools unsets noncoupled boundary faces. The actual four certified cells have three internal faces, one physical face, and no coupled face. The actual checker log reports geometric directions (1 1 1).

Let the fixed triangle have area A, longest edge L, and perimeter P. Let h be the absolute perpendicular apex height, and S the sum of the three side-face area magnitudes. The triple product of the three oriented side-area vectors has magnitude A²h²/2. Their average magnitude is S/3. The installed normalized determinant is therefore

D = 729 A⁴ h⁴ / (32 S⁶).

Vector closure gives S ≥ A. Every side triangle has area at least half its base-edge length times h, so S ≥ hP/2 ≥ hL. Both bounds hold for arbitrary tangential apex location, including projections outside the base. If h ≤ A/L, using S ≥ A yields D ≤ 729A²/(32L⁴); if h ≥ A/L, using S ≥ hL gives the same bound. Thus

D ≤ 729 A² / (32 L⁴) for every possible apex.

A² and L² are computed as exact rational functions of the original written binary floating-point triangle coordinates, so the reported certificate uses exact fractions without rounded square roots or an optimized candidate apex. The four rank-1 certificates are:

| Written cell | Physical base face | Exact-fraction upper bound displayed numerically |
|---|---|---|
| 526531 | 1918567 | 0.0002736533367533999 |
| 604333 | 1919145 | 0.00000772826576167792 |
| 507880 | 1919136 | 0.0008421633188747668 |
| 507898 | 1920059 | 0.0008421633188747668 |

All four bounds are below the unchanged 0.001 threshold. Moving only an internal kernel point cannot repair these four tetrahedra while retaining their triangular base and face-selection topology. A different conforming base partition or cell decomposition would be required, with original outline, area, moment, volume, coupled mapping and full quality checks preserved. This bound does not prove that an apex-only repair exists for the other cells.

The certificate is mathematical for exact geometry; installed floating-point determinant accumulation is also separately reproduced from the actual written mesh and matches the raw checker minimum. The margin to the threshold is large compared with binary rounding; tests verify the side-vector determinant identity and hundreds of outside/inside projected apex samples.

The full JSON binds input, five core mesh files per rank, failure sets, diagnostic tools and installed formula sources by SHA-256. It contains every flagged cell/face metric and all base/apex candidates. The smaller samples JSON preserves complete point/face topology for 38 representative samples, including the four certified triangle coordinates in the full summary. Native raw/checker logs and sets were already preserved in conforming-cone-writer-compatible-native. Large rejected experimental meshes remain hash-bound at their original path; this is diagnostic evidence, never product mesh acceptance.
