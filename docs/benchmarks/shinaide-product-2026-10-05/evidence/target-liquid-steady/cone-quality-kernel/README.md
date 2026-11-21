# Shared-apex quality kernel diagnostic

This is a pure offline mathematical diagnostic of frozen written geometry and native parent traces. It writes no mesh, changes no original point, and runs no native checker, solver, API or browser.

For an exact planar convex fixed base with area centre B and shared apex A, a cone has centre (3B+A)/4 and volume outwardBaseSf dot (B-A)/3. A shared side has fixed edge P,Q and Sf(A)=0.5*(Q-P) cross (A-P). Since Sf dot A = Sf dot P = Sf dot Q, its owner distance numerator reduces exactly to 0.75*Sf(A) dot (P-Bowner); the neighbour expression is symmetric. Thus the apparent quadratic dependence cancels. On the positive-pyramid branch, stock weight >=0.05 and volume ratio >=0.01 are each two affine inequalities, including the installed VSMALL denominator.

Frozen quads contain 1–2e-16m coordinate discrepancies. The exact necessary subsystem omits those unsupported bases or source planes; omitting constraints enlarges its domain, so an exact Farkas contradiction in that subsystem still excludes the fixed-topology, positive-pyramid, single-apex model. A separate near-planar affine model produces candidates. It never projects a point or relaxes an acceptance threshold. Every proposal is evaluated with the actual floating OpenFOAM face-centre, cell-centre and volume formulas across all children and all locally complete affected interfaces.

The joint frozen parents r3/9731, r3/9732 and r3/9885 have local candidates satisfying all five Pareto criteria. For r3/9731, 35 children and 92 affected faces give Dmin=0.0052163, Wmin=0.0960844, Rmin=0.106298 and maxSkew=0.940677. This is local offline geometry, not a native mesh PASS.

In the later 13-proposal frozen mesh, r3/9777 and r3/9792 remove their local low W/R faces, but increase the worst pre-existing nonorthogonality despite fewer bad labels. They remain refused by the full Pareto rule. r3/9836 creates a new bad nonorthogonal face; r3/621818 creates new bad determinant cells and a bad nonorthogonal face. These counterexamples are retained.

The later r1/30503, 25237, 30263 and 54186 give exact rational fixed-topology single-apex contradictions. For r1/30263, just the two neighbour-weight rows on faces1496129 and1496152, weighted by 1/2 each, yield exactly zero apex coefficients and negative constant -0.005737378636550152 in normalized coordinates. This does not exclude changing the partition or optimizing other parents together.

[receipt.json](receipt.json) supplies all representative summaries, original formula sources, thresholds, dependencies and scope. Three *-input.json files preserve the actual point/topology/source-plane stencils and full raw-mesh/trace hashes. Matching *-analysis.json files contain exact row coefficients, certificate multipliers and complete original/candidate metrics. [pytest-output.txt](pytest-output.txt) records 21 passing tests; [sha256.json](sha256.json) binds these files.

Reproduce from the repository root:

    python tools/audit_air_cone_quality_kernel.py --input docs/benchmarks/shinaide-product-2026-10-05/evidence/target-liquid-steady/cone-quality-kernel/latest-rank1-input.json --output .codex-tmp/cone-quality-kernel-rank1-recomputed.json
    python tools/audit_air_cone_quality_kernel.py --input docs/benchmarks/shinaide-product-2026-10-05/evidence/target-liquid-steady/cone-quality-kernel/latest-rank3-input.json --output .codex-tmp/cone-quality-kernel-rank3-recomputed.json
    python -m pytest src/backend/tests/test_cone_quality_kernel.py -q --basetemp .codex-tmp/cone-quality-kernel/pytest-reproduced

The output command paths stay inside this repository. Global all-source-point separation, unknown coupled neighbours, native serialization/canonicalization, every original geometry/Pareto condition, full stock checker and total 300s mesh deadline remain production gates.

Final six-ratio follow-up uses the later frozen quality-kernel mesh. Its complete eight-rank lowVolRatioFaces sets contain six faces, all bound to the native parent/base trace and independently recalculated below 0.01. Four concern r3/9836: a W/R proposal again creates bad nonorthogonal face 1467321 and is refused. Two concern r1/652847 against fixed external parents 25236 and 46412. The exact planar ten-child model gives a two-row rational contradiction: neighbour-weight constraints on faces 37870 and 88011 require AP.x<=2.1343514837566175m and AP.x>=2.1421894736842106m. Moving this parent apex alone cannot satisfy both. Joint neighbour motion or topology changes remain possible and require full validation.

[final-ratio-parents-receipt.json](final-ratio-parents-receipt.json), the two final-ratio-parents input/analysis files and the byte-preserved [final-ratio-trace-map.json](final-ratio-trace-map.json) preserve that scope. The original 21-test receipt remains unchanged; the follow-up full suite adds two actual-stencil regressions (23 tests passed), with its output retained separately.
