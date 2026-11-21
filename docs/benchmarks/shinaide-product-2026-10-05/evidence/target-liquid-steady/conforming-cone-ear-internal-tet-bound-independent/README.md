# All-four-selected tetrahedron necessary bound

This is an independent read-only audit of AIR's actual optional EAR_REFAN trial abd74c5d, whose strict written mesh remains D1321/W150/R14/S0 and Failed3. It does not start native/API/browser jobs, change any mesh/source/threshold, or accept the target.

For a tetrahedron with triangular base area A, perimeter P, apex height h and sum of side areas S, outward closure gives Sf0+Sf1+Sf2+Sf3=0. Every three-row minor has the same squared determinant. Cauchy–Binet therefore gives det(sum_i Sf_i Sf_i^T)=4 det(Sf1,Sf2,Sf3)^2=A^4 h^4. When all four faces are selected by installed checkMesh, avgArea=(A+S)/4 and the determinant divides the normalized tensor determinant by eight:

D_all4=512 A^4 h^4/(A+S)^6.

The three side vectors give S>=sqrt(A^2+h^2 P^2/4) by Minkowski, also when the apex projection is outside the base. With x=sqrt(1+(hP/(2A))^2)>=1,

D_all4<=8192 A^2/P^4 * (x−1)^2/(x+1)^4 <=128 A^2/P^4.

The last maximum occurs at x=3. An equilateral regular tetrahedron reaches 8/27, exactly the sharp bound. This is the all-four-selected branch, which must not be replaced with the physical-base/three-selected-side constant54.

The tool preserves exact binary-coordinate fractions and uses the inherited downward 192-bit dyadic edge-square-root lower perimeter, producing a rigorous mathematical upper bound for the installed formula. A larger upper bound is only a necessary condition, not a feasibility certificate. Floating runtime quality, all other geometry and the full checker are still required.

## Actual scope and candidate classification

From all1321 actual determinant-set cells, 550 are tetrahedra with four selected internal/coupled faces, 522 have a noncoupled physical base and three selected sides, and249 are outside these tetrahedron branches. The actual all-four-selected bound certifies66 tetrahedra with fixed triangular bases impossible for every apex. They derive from33 original five-node internal sourcefaces, all source patch/zone−1. The remaining three-selected-side physical tetrahedron set has zero constant54 exclusions. Exact actual selected faces, original sourcebase/parent identifiers and coordinates, fractions, and current written mesh/trace hashes are in actual-ear-selected-mask-bound-audit.json. No old topology's cell labels were reused.

All33 original n5 outlines have a conserving generic refan alternative: three triangulations, five strictly convex partitions, and four candidates satisfying the constant128 necessary triangle gate and within-base W/R. The max-min shape/W/R/fewest-pieces rule selects one quadrilateral and one triangle while keeping every original point, directed exterior edge, signed area and first area moment. The quadrilateral's determinant, actual all-child and both-parent outside-neighbour quality are not certified by these necessary filters.

This result supplies an optional generic triangle gate for internal/coupled split bases. It does not prove arbitrary polyhedra impossible, does not authorize a production change, and does not bypass the full topology/geometry checker.

## Final written apex identity

The earlier native partition audit captures AP before later optimization, so its fields alone do not prove final written AP. Here the exact current cell-parent map identifies every written child of each of six source parents belonging to four original bases. Their complete vertex-set intersection contains one new native point, which is the final written apex. A shared original boundary point, multiple common points, or a single child is explicitly refused.

All six final written apex coordinates happen to match the input-stage witness bits for these specific parents. Actual current written cell faces give n8 sourcebase1958933/1958934 minimum determinant0.000261864/0.000779858, still failing. The two n6 internal bases give four parent-local minima above0.02. This is a direct current-topology field/mesh proof, not an assumption based on the witness and not whole-parent acceptance.

## Regression and reproduction

Nine dedicated tests cover Cauchy–Binet, the installed512 normalization, external apex projection, regular-tetrahedron sharpness, exact branch constant separation, actual66/33 classification and conserving alternatives, plus unique-new-apex identity and honest written determinant failures. Temporary source-metadata and point-count reader errors are preserved as development logs; final output and pytest logs are separate.

Use tools/audit_air_tetra_all_selected_bound.py --mesh <frozen actual MPI mesh> --output <repo JSON> for actual branch classification; --refan-from <bound JSON> for original-outline candidates; or --mesh <mesh> --written-apex-from <four-base puremath JSON> for final written AP/child determinant proof. All outputs here are read-only mathematical diagnostics.
