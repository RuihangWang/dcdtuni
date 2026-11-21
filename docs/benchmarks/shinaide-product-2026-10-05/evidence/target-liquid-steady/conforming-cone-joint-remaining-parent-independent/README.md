# Independent remaining-cone topology diagnosis

Read-only mathematics on the frozen actual REFAN + AXIS_PARETO mesh. No native/API/browser job, production source, mesh point, numerical threshold or acceptance state was changed by this audit. CPU affinity was 36–39, one BLAS/OpenMP thread and CUDA hidden. Exact source-trace and written-polyMesh hashes are in the raw audit.

The baseline joint has D2451/W733/R40/S0 and still fails three strict checks. Its 2451 determinant cells belong to 1231 source parents. Actual original-face planes classify 997 parents/2086 flagged children as already at their native axis-box centre, rather than silently treating missing candidate log rows as unchanged. AIR's later bounded-13 trial is a separate actual mesh: D1333/W163/R14/S0, still Failed3. All eight stronger certified physical triangular tetrahedra remain in that later determinant set.

## Fixed physical triangular tetrahedra

For the installed three-selected-side / one-noncoupled-physical-base branch, let triangle area be A, perimeter P, apex normal height h, and total side-face area S. The installed tensor determinant is 729 A^4 h^4/(32 S^6). The signed normal components of the three side areas sum to A even if the apex projection is outside the triangle; their tangential magnitudes are |h| times the three edge lengths /2. Minkowski therefore gives S >= sqrt(A^2 + h^2 P^2/4). Maximizing x^2/(1+x)^3 at x=2 yields D <= 54 A^2/P^4.

The tool represents original binary coordinates as exact fractions. Each edge square root is rounded downward to a 192-bit dyadic value, so the perimeter denominator is a rigorous lower bound. The resulting fraction is an upper bound, not a feasibility certificate. Eight actual tetrahedra are excluded by this stronger necessary bound; none was excluded by the older 729 A^2/(32 Lmax^4) bound. This does not prove that an arbitrary polyhedron retaining the physical triangle is impossible.

The eight tetrahedra originate from six source polygons: four n5 outlines with retained collinear boundary nodes and two simple notched n8 outlines. All are larger original faces, so preserving their outline while changing their internal fan remains possible. The four n5 faces have valid triangle/quad partitions. Both n8 faces reject every single-vertex-fan partition under the necessary gates, but alternative triangulations find 19 triangulations, 61 strictly convex partitions and five necessary-eligible partitions. The selected four-piece partitions preserve every original point, exterior directed edge, signed area and first area moment. At the old source apex, their local determinant still fails; these are topology candidates, not accepted cells.

## Two internal n6 rectangular faces

The same-parent/same-source-base weight relation identifies ten flagged faces from four source bases. In addition to the two n8 physical faces, internal sourcebase206811 (rank1, source parents54186/54760) and sourcebase89115 (rank4, parents25867/46837) retain six original outline points, including two closely spaced nodes on one side.

For each, all four triangulations give seven strict partitions and one necessary-eligible three-piece partition: two triangles and one central quadrilateral. The common canonical partition has within-base Wmin0.3234275 and Rmin0.468083. At the actual recorded source apex, with the internal base included in the determinant mask, the four parent-local minimum determinants are 0.0751722, 0.0717831, 0.0265123 and 0.0717831. Their compact local cone closure/halfspaces and volume/first volume moment pass the recorded gates.

Using the same partition on the two original source parents also gives across-base W/R/skew/nonorth maxima within the standard gates: rank1 Wmin0.497086/Rmin0.988411/Smax0.402184/Nmax8.79 degrees; rank4 Wmin1/3/Rmin1/2/Smax0.472787/Nmax25.35 degrees. Other original bases and full-parent outside neighbours are not included. No whole-parent or whole-target acceptance is claimed.

complete-ear-partitions-and-cycles.json enumerates all connected triangle unions up to n−2 per piece. For these four actual original faces it gives the same candidates/selected pieces as the narrower up-to-three enumeration. All 56 original-loop rotations, reversals and unrelated point-label assignments preserve the selected coordinate cycles exactly. This is mathematical canonical equivalence, not an actual coupled-patch writer proof.

## Source review and regression

ear-refan-next-source-reviewed.txt is a hash-bound read-only optional-helper snapshot, not installed code. Its visibility test excludes other-node touch and nonadjacent edge crossing/overlap; DP requires positive writable triangles; DSU pieces require directed cancellation, one incoming/outgoing edge and a closed cycle; fan scoring requires strict convexity and retains every original outline node. No confirmed defect was found in that snapshot. Its caller still requires an existing supported initial fan, so arbitrary non-star polygons are outside the claimed scope.

The dedicated pytest file covers exact strong bounds including outside projection, equilateral sharpness, ancestry ambiguity, JSON-safe actual axis-centre detection, simple concave triangulation, actual n8 refusals and honest old-apex determinant failure, coordinate canonical equivalence, internal/physical determinant masks, both actual n6 partitions and cone volume/first moment conservation. The initial JSON-serialization and temporary metric-key mistakes are preserved as tool-development logs; final results are separate.

Reproduction uses tools/audit_air_refan_remaining_ancestry.py with --cheap-only --derive-missing-kernel, the default written-set audit, --refan-exclusions-from, or --same-base-refan-from on the frozen actual MPI mesh. The complete candidate and cycle proofs are self-contained raw coordinates, labels and values. No result from this directory authorizes solving the large mesh; the production full-scope checker remains required.


## Actual EAR witness comparison

AIR's actual optional EAR trial abd74c5d is a later distinct topology and still reports D1321/W150/R14/S0, Failed3. The four selected original faces in its native partition audit match this independent complete enumeration exactly after coordinate-cycle normalization. All witness coordinates match the earlier original outline bits; every selected piece uses original points; the native conservation flag is false for all four.

Crucially the native partition audit's ownerApex/neighbourApex fields are captured at input stage before later AP candidate selection. The corresponding local D/W/R calculations in ear-native-four-base-partition-correspondence.json are therefore explicitly input-stage diagnostics. They are not claimed to be the final written AP or final written child metrics. No old written cell label is reused across this topology change.
