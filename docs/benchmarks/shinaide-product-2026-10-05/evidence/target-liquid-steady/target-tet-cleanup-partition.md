# Read-only bad-tet cleanup comparison and deterministic polygon partition

The optional native collinear-point cleanup removed the original-vertex-base fan failures, but left planar concave polygons whose face-centre fans fail. This independent comparison and in-memory partition does not modify any native mesh, source STL, solver or checker threshold.

| Count | Original target | Post-cleanup target |
| --- | ---: | ---: |
| Official failure events |35|24|
| Distinct saved bad face labels |32|21|
| Owner face-centre fan failures |21|21|
| Neighbour face-centre fan failures |3|3|
| Original-vertex-base fan failures |11|0|

The installed polyMeshTetDecomposition::checkFaceTets increments its error counter separately for the three tests. Its labelHashSet inserts a face once. All32 original and21 remaining flagged faces are noncoupled, so the former coupled-accounting explanation was incorrect for this target. The official function excerpt, original complete log hashes and saved original/SSE diagnostic lines are preserved. Reconstructing every original fan from the saved JSON point/centre inputs reproduces its recorded quality with zero difference.

All eight ranks retain the same parsed owner/neighbour addressing arrays. Every remaining bad face retains its original coordinates, vertex count, owner, neighbour, patch, area vector and face centre. File-byte identity is not claimed: the written addressing headers changed. The remaining21 are17 wall faces,1 physical patch face and3 internal faces. They are planar within1.78e-15m, but their all-vertex directed edge-support tests show genuinely concave polygons.

The prototype uses strictly positive deterministic ears selected by geometric tip coordinates. It rejects nonplanar, repeated-point, self-crossing/touching and degenerate input. It retains every original boundary edge and point. A second mode greedily joins adjacent pieces only when the resulting polygon is convex and its original-centre original-point/centre fan qualities remain positive. Reversed vertex ordering independently yields identical geometric piece sets. Cells sharing a split face receive the same partition. All24 affected cells are reconstructed simultaneously using the installed OpenFOAM face-area, face-centre and signed pyramid cell-centre definitions.

| Reconstructed measure | Ear triangles | Greedy convex pieces |
| --- | ---: | ---: |
| New pieces replacing21 faces |123|43|
| Minimum quality with recomputed cell centres |6.07510e-11|5.25097e-8|
| Remaining selected owner/neighbour/base fan events |0/0/0|0/0/0|
| Maximum relative affected-cell volume change |2.53e-14|3.22e-14|
| Affected cells still triggering strict face-centre test |24|24|
| Coplanar trigger pairs before→after |72→916|72→164|

Every new piece is convex; every original outline and point is retained; all directed affected-cell edges pair oppositely. All affected-cell face fan qualities and centroid pyramid volumes remain positive. The selected21 faces' reconstructed24 failure events disappear, but all24 affected cells still fail primitiveMesh::checkConcaveCells. Nineteen are convex within the audit tolerance with coplanar facets; five retain true vertex-halfspace violations. Trigger pair counts are distinct from flagged-cell counts: more pairs do not by themselves imply more flagged cells.

Even though the polygon outline, area vector and cell volumes are preserved, the installed absolute-weighted fan definition of a concave polygon's face centre changes after partition. The maximum partition-area centroid change is1.7344mm; the maximum affected-cell-centre change is0.10470mm. This discretization change is reported explicitly.

Eight focused tests pass, including outline/collinear-point retention, reversed and cyclic-start determinism, invalid-input rejection and a cube proving that triangulation introduces the strict coplanar-face trigger. Recompute the prototype with tools/audit_air_face_partition.py --comparison <saved target-tet-cleanup-comparison.json> --output <new output.json>. No native checker is invoked. This proves a local tet repair mechanism, not complete mesh acceptance; full determinant, weight, ratio, skewness and distributed topology verification remain required.
