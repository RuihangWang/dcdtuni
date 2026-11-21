# Read-only full-quality geometry diagnosis and preserved-base cone counterexample

The frozen target steady solve converged under the original residual thresholds, but its identical mesh failed the full geometry checker. The solver evidence therefore does not authorize a transient steady seed. This audit does not modify the mesh or relax any checker threshold.

The stronger v2 classifier checks all cell vertices against all outward face planes and every polygon vertex against every directed edge support line. It also requires planar polygons, positive cell volume and area closure within the recorded audit tolerances. Local turn signs alone can miss a self-intersecting polygon. The original v1 raw is retained; v2 moves two target cells from the convex group to the nonconvex-face-polygon group.

| Saved checker-set classification | Target original mesh | Actual Tri preserved-base pyramid prototype |
| --- | ---: | ---: |
| Convex within audit tolerance, with coplanar facets | 123,924 | 42,696 |
| Vertex outside an outward face halfspace | 1,623 | 673 |
| Nonconvex face polygon | 51 | 22 |
| Nonplanar face, convexity unproved | 0 | 9 |
| Total saved concave-cell labels | 125,598 | 43,400 |

The OpenFOAM v2412 primitiveMesh::checkConcaveCells test flags a face if another face centre lies at a normalized outward-plane dot product greater than -1e-6. Coplanar fragments with distinct face centres therefore trigger it even when the cell is convex within the geometric audit tolerance. This explains a large subset of the saved flags; it does not establish a checker pass or make the genuinely concave cells acceptable.

The Tri prototype supplies an actual obstruction to a local preserved-base pyramid repair. In processor0 cell 24576, base face 24057 contains the collinear chain 11948→11966→11965. Their cross product is exactly zero in the written coordinates. Side faces 70400 and 70401 share the same apex and outward plane; their normalized normals have dot 1 and their face-centre test dot is 0. They connect to distinct neighboring cells 24579 and 24580. A local merge of those side faces cannot preserve the one-face-one-cell-pair incidence. The complete coordinates, face lists, adjacency and input hashes are in tri-pyramid-collinear-child-counterexample.json and the actual v2 Tri audit.

A separate geometry-only cone probe on original target rank0 cell 65536 conserves parent volume to floating-point roundoff, but four child pyramids still trigger the face-plane criterion. The independent split-cube test gives the same mechanism. Convexity and positive pyramid volumes are necessary geometric checks, not sufficient conditions for conforming strict-quality children.

The native opt-in all-vertex merge gate can reject a genuine concave union that the existing centroid-inside test admits. The opt-in polygon edge-support gate can reject a concave face join while retaining collinear vertices needed by neighboring faces. Neither gate by itself removes adaptive coplanar interfaces or proves the final face-tet, interpolation-weight and volume-ratio checks. Approximate coplanarity of candidate joins also does not prove exact face planarity.

A face-centre fan could preserve a convex planar polygon's outline and avoid an original-vertex fan's collinear triangles, but its connectivity must be identical on both adjacent cells and across processor/cyclic interfaces. Subdividing one interface gives an unmodified neighbor more coplanar facets, so refinement may propagate. No such repair is claimed or enabled by this audit.

Recompute using tools/audit_air_mesh_geometry.py with --mesh pointing to the recorded frozen mesh, --workers 8, and --detail-limit 4 for the Tri prototype. The JSON receipt binds tool/test bytes, each raw result, the installed checker source and actual mesh inputs. Nine focused tests passed. No solver or native mesher was run.

The tet-count interpretation is corrected in target-tet-cleanup-partition-receipt.json: the original report's35 failure events correspond to32 distinct face labels; the later cleanup has24 events on21 labels. This difference comes from separate owner, neighbour and original-vertex-base increments, not coupled duplication. Original raw artifacts are retained.
