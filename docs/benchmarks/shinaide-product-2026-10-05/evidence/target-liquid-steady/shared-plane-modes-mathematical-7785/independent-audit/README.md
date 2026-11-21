# Independent 14-point shared-plane audit

This bounded offline audit passes. It does not authorize the actual Shinaide mesh, an installed writer, a stock checker run, or CFD.

The frozen AIR manifest `fcaf628d79329232ba73a6d400b49d35132a66d454e43e1087b97836b3897edb` and receipt `638177ec3f2c7623abd568ddef1a4884d1b4b7bd3b1a9cdfb34c0971f22c5308` were verified against their actual bytes: 37 primary files, 143 dependency payload files, and 52 bound rank-1/rank-3 source files. Full original compact coordinates, face cycles, owner/neighbour, patch/zone/provenance, cell face lists and moments match the actual mesh.

The original and candidate compound documents were independently reconstructed from the two components using collision-free rank tags. The unified 373 core plus 271 complete outside cells were evaluated again; counts are D 47→22, interpolation weight 32→18, volume ratio 2→0, skew 0→0, and non-orthogonality warnings 54→54. The explicit scalar symmetric-tensor determinant agrees with the saved NumPy determinant within 6.67e-16 and yields the same threshold classifications. Weight and ratio values match exactly; the largest independent non-orthogonality rounding difference is 1.52e-12 degrees.

Original passing D/weight/ratio/skew roles remain protected. Non-orthogonality warning labels exchange: 18 originally at or below 70 degrees become warnings, and 18 old warnings disappear. The peak improves from 88.2021527719 to 83.1271563773 degrees. This is a count/peak warning budget, not a no-new-warning-label guarantee.

All 271 outside cell centres and volumes remain double-byte exact. The full core boundary integral for volume and first moment was independently recomputed from x-directed divergence identities on exterior shell triangles; it equals the original all-cell origin-tetra Fraction result exactly before and after. Floating-point totals still differ by relative volume 1.27e-15 and first moment norm 3.27e-17 m⁴. Exact total boundary conservation does not imply each moved cell has unchanged volume.

Eight incident nontriangular faces were checked by every four-node determinant over exact binary-coordinate Fractions. All were exactly planar originally and remain so. Internal rank-1 face 1381082 rotates its normal by 20.9616966328 degrees; proposed nodes depart from its old support plane by 2.0216782041 mm. Thus the allowed motion preserves planarity while permitting internal support-plane rotation; it is not artificially locked to old internal planes. The reused complete geometry guard evaluates new face/cell geometry and remains a conservative additional screen.

The one physical moving point, rank-3 point 569596 on `ACU_03_Front_patch`, is an interior point of a planar physical footprint. Its four incident physical polygons are three triangles and one quad. An independent oriented-edge outer-ring oracle establishes exact signed area and first-moment equality, fixed outer-ring coordinates and fixed normal coordinate 2.8185203125 m. All pieces retain positive convex orientation. The existing raw-STL normal-position discrepancy is retained, not corrected or certified. No coupled or zoned face exists in either complete touched stencil. Accordingly this is a union of two disjoint bounded rank domains, not proof of a changed cross-rank coupled interface.

Both existing scratch point copies were independently decoded as actual LSB label32/scalar64 OpenFOAM binary payloads. Header/suffix bytes and all nonmoving actual rank points are unchanged. Exactly the declared 3+11 points change, and all candidate-document coordinates match the reread payloads. This checks controlled point-file serialization, not installed C++ aliasing/sorting/moments or native mesh acceptance.

The first local audit completed numerical assertions but failed JSON serialization of a NumPy integer; its source/logs are retained. The second local physical oracle assumed four triangles and rejected the actual 3-triangle+1-quad footprint; its source/logs are retained. The final oracle checks arbitrary convex polygon pieces exactly and passes. AIR/source evidence was never changed.

Reproduce from the integration repository on 40901:

```sh
CUDA_VISIBLE_DEVICES='' PYTHONDONTWRITEBYTECODE=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 TMPDIR="$PWD/.codex-tmp/shared-plane-independent-20261005/tmp" taskset -c 24-31 /mnt/lv/home/litianyu/miniconda3/envs/dcdtuni/bin/python .codex-tmp/shared-plane-independent-20261005/independent_shared_plane_audit.py
```

`receipt.json` binds the final audit/script/invocation and installed formula sources. `manifest.json` binds this local evidence only; no original AIR manifest, actual mesh, production source, Git index, native process, browser, API, service or GPU was changed.
