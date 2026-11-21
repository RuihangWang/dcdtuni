# Trace-confirmed parent9885: internal kernel placement

This is a read-only mathematical diagnostic on the rejected experimental mesh. It does not generate a mesh or authorize a CFD seed. Actual compact writer maps establish that all 35 selected written cells are descendants of rank-3 input-stage parent9885. Their single shared apex is written point7864/native754041, which is beyond the 752916 input-stage source points. It is a newly inserted internal point, not an original STL/source vertex. Every face incident on it has both cells in this same mapped parent.

The 64 core written mesh files in the trace run are byte-identical to the preceding writer-compatible run, with the producer parity JSON preserved. Native input-stage ancestry means the alive cell immediately before optional cone decomposition, not a surface or octree ancestor.

All 35 children and 92 affected faces were evaluated, including 33 complete cross-parent neighbouring cells. Their original cell centres and volumes come from their actual closed polygons. No affected coupled face occurs in this parent; the tool fails instead of guessing remote values. The unchanged outside neighbour521112 already has determinant 0.0005638417378606792. Therefore the candidate passes the 35-child/92-face checks described here, not all 68 cells or the whole mesh.

The score is min(D/0.001, weight/0.05, volumeRatio/0.01, 4/skew). Existing thresholds are retained.

| Candidate | Min determinant | Min weight | Min volume ratio | Max skew | Min normalized score |
|---|---:|---:|---:|---:|---:|
| original | 1.25624735756e-09 | 0.00229915917764 | 0.00230445749224 | 1.2938079344 | 1.25624735756e-06 |
| chebyshev | 0.000449018330476 | 0.0481395958716 | 0.0505742183022 | 1.59212922265 | 0.449018330476 |
| gridBest | 0.00846664695355 | 0.0943937507584 | 0.104232662747 | 0.94676066685 | 1.88787501517 |
| axisKernelCenter | 0.00301737838458 | 0.0909090909091 | 0.1 | 2.17963961658 | 1.81818181818 |
| kernelXYParentBBoxZ | 0.00477150438262 | 0.0937501922563 | 0.103448509953 | 1.81104922099 | 1.87500384513 |

The maximum-plane-radius LP point is a meaningful failed candidate: it removes all four low-volume-ratio failures but still leaves one low-weight face and creates a third low-determinant child. Maximum kernel radius alone does not establish child mesh quality. A 150-point deterministic mathematical grid finds [3.795,2.7235,1.557], which passes these four local metric gates. It is a fixture-specific search, not a production mesher algorithm or proof of an optimum.

A coordinate-independent candidate can be derived from the actual original face planes. For this axis-aligned parent, the plane-kernel bounds are [3.7639,2.7000000476837154,1.4761375] to [3.8264,2.74722222225,1.599575]. Their centre is [3.79515,2.7236111349668577,1.53785625] and it also passes the four local metric gates. The upper z bound comes from the complete parent plane set, not the outer bounding-box maximum1.6150046875. The tiny off-axis normal components are recorded; axis recognition only proposes a candidate, and every actual original plane is still rechecked with the unchanged 3-micrometre kernel margin. A non-axis parent is explicitly unsupported by this analytic probe.

The centre has the same optimal minimum-plane margin as the LP candidate, but a different position within that nonunique optimal region. This explains why a deterministic geometric centre can have better cell quality than an arbitrary LP vertex. The mixed kernel-x/y and original-bbox-z centre is separately recorded, with its distinct geometric definition.

For the grid candidate and the derived plane-kernel centre, complete local checks confirm:

- Every original base polygon and every non-apex written coordinate is exactly unchanged.
- All directed child edge cycles close, all child volumes are positive, and vertex/face halfspaces are satisfied to floating rounding.
- The installed face-centre concavity criterion has zero triggers. All face polygons remain planar/convex and pass the native relative planarity criterion.
- Both centre-based and common-original-vertex tetrahedral fans have positive quality. The minimum area remains above the existing writer floor.
- Child volume sum and first volume moment preserve the original input-stage parent, and new-point separation is far above three micrometres.
- All incident internal-face non-orthogonality, owner/neighbour centre weights, volume ratios and physical-wall skewness are recomputed with actual external neighbours.

The original, LP-rejected, grid, analytic-centre and mixed-centre rows are all preserved; original failures are not replaced by the successful local candidate. Four pytest tests independently rebuild the closed-polygon fixture and original external-neighbour geometry, reproduce the local metrics and mass moments, verify the LP counterexample, and retain the scope of the unchanged bad external cell. Project-context agent independently matched the grid metrics and checked the same geometry and original-face preservation.

The producer trace helper changed while this audit was running to add format/rank/label validation. The SHA guard rejected that change. Its original extraction bytes are retained, the updated helper is frozen in the evidence directory, and re-extraction reproduces the complete numeric parent dictionary exactly. Both identities and the rejection note are preserved.

No native executable, source, written mesh, boundary condition, quality threshold or original physical point was modified by this experiment. A production strategy would still need all-parent quality guards, exact coupled conformity, full real checkMesh and the whole-job deadline. Kernel placement cannot repair the separately certified four fixed skinny triangular-base tetrahedra; they require a different conforming partition or grouping.
