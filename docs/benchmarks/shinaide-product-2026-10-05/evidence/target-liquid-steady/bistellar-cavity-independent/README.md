# Independent rejection of one original-node 2→3 cavity

The first exact geometric cavity remains rejected after independent reconstruction. Geometry conservation does not imply acceptable cell quality.

The frozen rank1 cavity replaces written cells525057/671013 (source parents30174/53738) across face1499187. The five original nodes stay fixed. The new three tetrahedra are reconstructed from raw point sets using exact rational orientation, without importing AIR’s assembly or quality code.

Independent Fraction computations reproduce all recorded intersection fractions, positive tetrahedral volumes, total volume and first moment exactly. All six original exterior triangle cycles, traced base/zone and outside-neighbour identities remain. All six complete outside cells retain bit-identical recomputed centres and volumes. The ten points, 24 original faces and eight complete original cells are checked against original binary mesh and source/written trace arrays, including native point identity. The original written cell-zone collection is empty.

The geometry-only predicates pass: exact original-cavity vertex halfspaces, closed opposite directed edges, positive cell volumes and pyramids, actual stock other-face-centre predicates, and both tet-fan checks.

| Metric | Original cavity | Proposed cavity |
|---|---:|---:|
| minimum determinant | 0.0006305671 | 0.0000001027937 |
| determinant failing cells | 1 | 3 |
| minimum weight | 0.08103331 | 0.05552191 |
| minimum volume ratio | 0.1175716 | 0.07838109 |
| maximum skew | 0.2500000 | 0.6609271 |
| maximum nonorthogonality | 56.16238° | 85.11895° |
| nonorthogonality failing faces | 0 | 4 |

All three new cells inherit original cell671013’s passing determinant role and fail it. The two original exterior faces1499182/1773690 and two new internal faces also lose previously passing nonorthogonality roles. The resulting explicit seven role regressions reject the candidate.

Canonical owner<neighbour orientation requires flipping four exterior triangles while retaining vertex zero. Area vectors reverse exactly; complete cell centres/volumes and D/W/R/N values remain unchanged. Face-centre rounding differs by at most1.11e-16m and skew by4.87e-15, without changing any verdict.

This is a local mathematical rejection, not a native mesh run or a statement that all alternative topology is impossible. Source rows, original AIR receipts, independent output and hashes are retained.

Reproduce from the integration repository root:

    python tools/audit_air_bistellar_cavity_independent.py --input docs/benchmarks/shinaide-product-2026-10-05/evidence/target-liquid-steady/bistellar-cavity-independent/rank1-first-cavity-complete-stencil.json --output .codex-tmp/root-bistellar-independent.json
    python -m pytest src/backend/tests/test_bistellar_cavity_independent.py -q --basetemp=.codex-tmp/bistellar-independent-pytest

Optionally add --mesh with the frozen actual Mesh directory recorded in independent-result.json to reverify the original binary data. The compact input alone supports all exact geometry, complete outside reconstruction and metric computations. Eleven semantic regressions passed.
