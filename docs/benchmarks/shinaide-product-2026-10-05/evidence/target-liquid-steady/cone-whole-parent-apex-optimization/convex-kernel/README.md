# Fixed-topology common-AP convex determinant separation

All three previously searched complete same-rank stencils have an **exact necessary-row separation certificate** for the joint absolute determinant, weight and nonorthogonality requirements. They do not become full mesh acceptance, nor a statement that a topology change cannot repair the case.

| Parent | Qualified bad fixed-base tets | Exact necessary rows | Certificate negative constant |
|---|---:|---:|---:|
| r0 p19921 | 5 | 524 | −0.018426220147497972 |
| r3 p10906 | 4 | 618 | −0.06317671058690383 |
| r6 p9316 | 5 | 288 | −0.03844421725962784 |

Each certificate uses four nonnegative multipliers, an exact zero three-component gradient and an exact negative constant. The constants refer to positively normalized affine rows; they are not physical mesh-quality values. LP infeasibility alone never certifies anything. All 14 actual bad tetrahedra are qualified on their actual installed selected-face branch, and their original determinant matches the corrected formula.

## Correct area identity

A denotes fixed triangular-base area, h its positive perpendicular height, and B=(b−a)×(c−a). Orient H=B·(AP−a)>0, so H=2Ah. Let the three outward side-area vectors form rows of L. Direct cross-product expansion gives

    det(L) = ±H²/8 = ±A²h²/2.

The installed three-dimensional selected-face determinant is det(sum Sf Sfᵀ)/(8 meanArea⁶). Therefore

    D3 = A⁴ h⁴ / (32 meanSideArea⁶)
       = 729 H⁴ / (512 S_side⁶).

For four selected faces, the base vector is −sum(side vectors); the Gram determinant gains det(I+11ᵀ)=4. Hence

    D4 = A⁴ h⁴ / (8 meanAllArea⁶)
       = 32 H⁴ / (A+S_side)⁶.

The proposed A²/(128 or 32) hypothesis fails dimensional/scale invariance and is explicitly retained in proposed-formula-counterexample.json. A=3,h=1.2 gives actual D3=0.07777731474129149 and D4=0.1561891695739479, 36 times the hypothesis values. The corrected identity covers an apex projection outside the base triangle; no incenter/projection-inside assumption is used.

installed-formula-source-identity.json binds the actual v2412 primitiveMeshTools.C and polyMeshCheck.C files. Lines 867–926 select internal/coupled areas, divide by mean selected area and take determinant/8. polyMeshCheck.C lines 424/444 set and compare the original 0.001 threshold. The full installed stock checker is still required.

## Convex determinant constraint and rigorous outer cut

For δ=0.001 and n=3 or 4, let S_n be the sum of selected area magnitudes and define

    g(AP) = S_n(AP) − C_n H(AP)^(2/3),
    C3⁶ = 729/(512δ),   C4⁶ = 32/δ.

On H>0, D_n≥δ is exactly g≤0. Side area vectors are affine in AP, their magnitudes are convex norms, and −H^(2/3) is convex. Thus each fixed-tet determinant feasible set is convex. This says nothing about other cell types' determinants.

At positive H0, concavity gives

    C H^(2/3) ≤ a H + b,
    a = (2/3) C H0^(−1/3),   b = (1/3) C H0^(2/3).

The implementation obtains rational a_upper and b_upper by exact integer sixth-root bounds: a_upper⁶≥a⁶ and b_upper⁶≥b⁶. For each side, choose a rational norm-support vector u with ||u||≤1, so ||Sf||≥u·Sf for every AP. The four-selected branch also uses an exact rational lower bound A_lower≤A. A determinant-passing AP must satisfy the necessary outer row

    a_upper H + b_upper − sum(u·Sf) − A_lower ≥ 0.

A_lower is zero on the three-side branch. These bounds all enlarge the necessary feasible set. They never impose a sufficient inner shape as an infeasibility argument. Root inequalities and norm-support squares are exact Fraction comparisons.

## W/R and necessary N directions

All original-parent kernel and W/R rows are rebuilt by the existing exact fixed-planar model from original binary points. No approximated, nonplanar or unknown coupled row is silently used. The three inputs have zero omitted exact models. The positive-pyramid branch makes absolute owner/neighbour distances agree with signed distances. Original exterior cell CC/V remain declared frozen constants where an external interface is modeled.

For a same-parent side, d=Cn−Co=3/4*(baseCentreNeighbour−baseCentreOwner) is constant while Sf(AP) is affine. Nonorthogonality≤70° requires Sf·d≥cos(70°)||Sf||||d||. A rational c_lower≤cos(70°), norm_lower≤||d|| and any ||u||≤1 produce the necessary row

    Sf·d − c_lower norm_lower (u·Sf) ≥ 0.

Even u·Sf<0 is safe: it merely weakens the necessary row. For a fixed external face the analogous support is on affine d(AP), with fixed Sf. The installed denominator includes positive ROOTVSMALL; omitting it here is a necessary relaxation, not a weaker native acceptance threshold. The rational cosine lower bound follows Machin's arctangent identity and directed alternating series, then truncates downward.

Every critical N row in these three certificates is a same-parent side. The parent r6p9316 certificate uses only three D rows and one N row; it does not depend on an external W row. The other certificates also include a W row. All frozen face coordinates, owner/neighbour, patch type, selected-face mask and incident bases are included in certificate-support-geometry.json for primary reconstruction.

## Certificate and scope

The generator constructs finite necessary cuts at geometry-derived seeds and runs a floating LP for a possible sparse witness. It then reconstructs multipliers in exact Fraction arithmetic. A certificate is emitted only when all multipliers are nonnegative, all three gradient components cancel exactly and their constant is strictly negative. Positively normalizing a row does not change its necessary direction.

The impossibility concerns one common AP under this fixed base/fan topology, positive heights and declared exterior state. It explains why partial AP improvements can coexist with failure to clear all original D/W/N thresholds. Different fan partitions, local reconnections or changing external CC/V are separate hypotheses. No full native mesh, modified physical geometry, numerical solver, relaxed quality threshold or 300 s deadline is authorized.

## Reproduction

Run in the repository's existing CPU environment:

    PYTHONDONTWRITEBYTECODE=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 CUDA_VISIBLE_DEVICES= python tools/audit_air_tetra_convex_apex_kernel.py --input docs/benchmarks/shinaide-product-2026-10-05/evidence/target-liquid-steady/cone-whole-parent-apex-optimization/rank0-parent19921-input.json --output .codex-tmp/tetra-convex-recheck.json

Use the other retained inputs for p10906 or p9316. Raw JSON includes every exact necessary row, original query and directed bound, LP trace, critical certificate and input SHA256. The test reconstructs critical rows from original face/base coordinates and checks the exact combination independently of LP status.

    PYTHONDONTWRITEBYTECODE=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 CUDA_VISIBLE_DEVICES= python -m pytest src/backend/tests/test_tetra_convex_apex_kernel.py -q -p no:cacheprovider --basetemp=.codex-tmp/tetra-convex-recheck-tests

18 tests passed in 5.14 s. An independent agent also recomputed the exact multipliers, directed roots/norm supports and algebraic necessary direction; final primary native acceptance remains outstanding.
