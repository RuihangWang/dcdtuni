# Triangle-derived apex proposals on frozen N2
Bounded offline proposals use actual written geometry and complete affected parent/interface stencils. No CPP, mesh, solver, service, API, browser, or production threshold changed. Whole-mesh acceptance remains Failed 3.

For a triangle, the incenter minimizes side-area sum at fixed normal height, including outside-base projections. Global single-base D optimum is at inward height sqrt(2)*inradius for three selected physical-side faces, or sqrt(8)*inradius for all four selected faces. It reaches the proven 54/128 bound, but need not lie in the original parent kernel or improve all children.

Each ideal uses the actual triangle/inward direction. Original planes with the 3 um guard clip the current-to-ideal segment; four bounded blends follow. All children, fixed external CC/V, affected faces, D/W/R/S/N Pareto counts/labels/worst severity, positive geometry and V/M1, and full-rank existing-point clearance are evaluated.

Six physical-side representatives give 24 trials. Only rank 3 parent 10906 blend .25 passes local no-new Pareto/geometry: Dmin improves about 4.13e-6 to 6.34e-6 but four bad D remain. This is partial severity improvement, not repaired geometry. Rank 4 parent 26710 reduces D/N bad counts but is refused because worst N rises. All-four selected-face data: three representatives, twelve trials, none accepted.

Ten tests preserve ideal formulas, clipping and actual accepted-partial/refused-worst examples. Combined source-box suite: 15 passed in 3.77 s. Initial NumPy serializer failure is retained; final serialization handles NumPy scalars and refuses unknown values instead of replacing them with null.
