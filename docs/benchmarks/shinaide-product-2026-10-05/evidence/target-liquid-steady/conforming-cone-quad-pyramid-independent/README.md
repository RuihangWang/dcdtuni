# Current quad-pyramid determinant and original-parent height audit

This is a read-only mathematical audit of the frozen 920f quality-kernel mesh.
It does not write a mesh, change production C++, run native/API/browser, or accept
the full case. The actual full checker remains failed.

## Actual classification

The current underdetermined set contains 1270 cells. Its 244 non-tetrahedral
cells all have five vertices and five faces (four triangles and one quad).
Each has exactly one original base and the same unique newly inserted apex as
every written child of its original parent. There are 193 original parent groups:
169 quads include their internal/coupled base in the installed determinant mask;
75 have a physical base excluded. There are no un-coned original cells in this
244-cell subset. The older EAR subset was 249; it is not the current set.

242 written quad bases are exactly planar and strictly convex in binary-coordinate
Fraction arithmetic. Two warped bases are unsupported by the exact proof.
Their coordinates are preserved rather than rounded into a certificate.

## Projection-independent selected-area bound

Write A for base area, P for perimeter, h for unsigned apex height, and S for the
sum of side areas. For each selected face, use the actual installed area vector.
The raw side Gram determinant is K_side h^4; with the base included it is
K_all h^4. K is fixed by the planar original base. Changing apex projection
only shears the third column of the side-area matrix, so it does not change
this coefficient.

The vector of nonnegative side-area components gives Minkowski's inequality:
S >= sqrt(A^2 + h^2 P^2/4). This also holds when apex projection lies outside
the base, using signed projected side heights before taking their norms.

For an n-edge physical base (n selected sides), installed OF-D is bounded by

    n^6 K_side h^4 / [8 (A^2 + h^2 P^2/4)^3].

Its unconstrained maximizing height is sqrt(8) A/P, and its any-apex bound is

    (8 n^6/27) K_side / (A^2 P^4).

For an internal/coupled base (n+1 selected faces), the bound is

    (n+1)^6 K_all h^4 /
    [8 (A + sqrt(A^2 + h^2 P^2/4))^6].

Its unconstrained maximizing height is sqrt(32) A/P, and its any-apex bound is

    (n+1)^6 K_all / (32 A^2 P^4).

The triangular cases reproduce the prior 54 and 128 bounds exactly. A square
physical pyramid attains 8/27, and its all-five-selected branch attains
0.59604644775390625, at their respective maximizing heights. The tests include
arbitrary apex projections outside the base and tilted bases.

Perimeters and square roots use 192-bit lower rational bounds, giving upper
rather than lower determinant bounds. The tool never treats an upper bound
above the threshold as proof of feasibility.

## Original-parent height cap

The source parent is reconstructed from all its original alive incident faces,
with owner/neighbour orientation. No inserted apex enters the original-vertex
list. A height cap is certified only when the original directed shell is closed,
its faces are exactly planar, and either an original outward face halfspace
directly imposes h <= hmax or the closed source shell is convex. In the direct
case, that exact face is recorded as a witness. Unsupported source shells are
left undecided rather than relying on an unverified general hull premise.

Of 242 planar bases, 161 have this conservative source-only cap certificate.
55 are bounded below the unconstrained maximizing height. Seven have a strict
parent-bounded determinant upper bound below the unchanged 0.001 threshold:

| Rank | Source parent | Current written cell | Bound |
| --- | --- | --- | --- |
| 1 | 652847 | 462653 | 0.00033750638119296245 |
| 1 | 652847 | 750848 | 0.00023156342368378484 |
| 1 | 652847 | 750855 | 0.00007014993982124619 |
| 1 | 652920 | 750864 | 0.0006215078304561186 |
| 1 | 652920 | 750865 | 0.00042688350636838746 |
| 1 | 652956 | 750875 | 0.0004268835030772829 |
| 1 | 652956 | 750876 | 0.0004268835030772829 |

All seven select the internal base as well as the four sides. Their original
height cap is 0.001837056732177711 m. Source parent 652847 is also the subject of
an independent fixed-topology weight contradiction; this determinant proof
does not depend on that result. The seven certificates rule out repairing all
their current cone children solely by choosing another common apex inside
the original kernel. They do not rule out preserving-boundary repartition,
compatible local union, or a different general polyhedral construction.

Without the parent cap, every quad any-apex bound exceeds the threshold
(minimum 0.0025666900702435626). The two exact-planarity refusals and 81 height
certificate refusals remain explicitly unproved.

## Actual arithmetic cross-check and scope

The projection-independent formula at every qualified current written apex
matches the independently reconstructed installed determinant with maximum
absolute difference 3.415236843329339e-17. All written/source maps and the
actual underdetermined sets are SHA-bound in the JSON. The original parent
coordinates, directed closure evidence, cap face labels and exact fractions
are retained for every qualified row.

No generic full-mesh capability claim follows from these local proofs.
Agglomeration or repartition must separately preserve patch/material identities,
original boundary outlines, volume and first moment, coupled interfaces and
all affected outside quality. This directory contains no such accepted repair.

## Reproduction

From the integration repository, with the recorded frozen AIR mesh still present:

    OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 CUDA_VISIBLE_DEVICES= taskset -c 36-39 \
      python tools/audit_air_pyramid_remaining_bound.py \
      --mesh /mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/air/.codex-tmp/strict-stage-conforming-cones-quality-kernel/Shinaide_liquid_1d98c44a/Mesh \
      --output docs/benchmarks/shinaide-product-2026-10-05/evidence/target-liquid-steady/conforming-cone-quad-pyramid-independent/actual-quality-kernel-pyramids.json

    OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 CUDA_VISIBLE_DEVICES= taskset -c 36-39 \
      python tools/audit_air_pyramid_remaining_bound.py \
      --parent-heights-from docs/benchmarks/shinaide-product-2026-10-05/evidence/target-liquid-steady/conforming-cone-quad-pyramid-independent/actual-quality-kernel-pyramids.json \
      --output docs/benchmarks/shinaide-product-2026-10-05/evidence/target-liquid-steady/conforming-cone-quad-pyramid-independent/actual-parent-height-bound.json

    OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 CUDA_VISIBLE_DEVICES= taskset -c 36-39 \
      python -m pytest src/backend/tests/test_pyramid_remaining_bound.py -q \
      --basetemp .codex-tmp/pyramid-bound-pytest

Recorded: 16 tests passed in 0.92 s. Native/API/browser runs: zero.
