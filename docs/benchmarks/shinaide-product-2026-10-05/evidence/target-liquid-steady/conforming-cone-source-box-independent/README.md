# Frozen N2 source-box necessary conditions
This is a pure offline classification of the actual nonorth-sequential mesh, not a native split, repair, or accepted case. Stock check remains Failed 3.

The JSON binds all eight rank traces and actual bad-D sets. Of 1,086 D-bad cells, 205 are non-tet quad pyramids under 158 original source parents. There are 59 exact binary-coordinate source boxes and 111 satisfying separately recorded native floating tolerances. Twenty-one exact geometry-only thin/seam candidates cover 40 bad cells; 41 tolerance-only necessary candidates cover 78. These overlap and are not additive.

The exact gate checks original coordinates, six original planes, outward support, directed edges, convex face outlines with collinear boundary vertices allowed, exact box V/M1. Longest-axis splitting uses an original interior vertex coordinate nearest the midpoint, otherwise the original bbox midpoint. It proves only rectangular subbox V/M1 conservation, not that inserted outline nodes produce legal writable fans or conforming neighbour subdivisions.

Frozen traces lack pre-ghost rank-star mapping. Post-ghost cyclic/processor and original zone stars are checked, but preGhostCellRankStarUnavailableInFrozenTrace remains true. Native protected-star, transported plane, conforming subdivision, fan legality, and full installed checkMesh remain required. Physical patches may be subdivided on the same original plane and outline with their roles retained. Coordinates are never rounded to manufacture an exact box.

Parent 652920 is not an exact dyadic-coordinate box: the 8.88e-16 m plane and 3.81e-15 relative V discrepancies remain under the tolerance diagnostic, not erased.

Reproduce using tools/audit_air_source_box_split.py with --mesh and --output. Matching source-box and triangle tests passed together: 15 in 3.77 s on CPU 36–39. Earlier partial-closure and temporary helper-edit syntax failures remain separate logs. Final directed edges are counted before geometry refusal.
