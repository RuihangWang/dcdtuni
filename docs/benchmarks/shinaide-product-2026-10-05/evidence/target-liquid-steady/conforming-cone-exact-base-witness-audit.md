# Exact native-to-written cone-base witness audit

This is a read-only diagnosis of the failed optional Tri conforming-cone preflight. It does not authorize a mesh, solver seed or product case. The native API attempt returned code 1 before final mesh write/checkMesh. Its original conditions, matrix, raw SSE and all eight rank witness JSON files are preserved unchanged.

Every one of the 18 native pre-write rejected face polygons matches exactly one old written Tri polygon on the same rank, after allowing cyclic rotation or reversed winding. All float64 coordinate bits match; native labels and written labels are reported separately. The two internal faces each contribute two owner/neighbour incidences, so the total is 20 failed cell-base incidences. All rejections have native reasonMask 2 (nonplanarity), with no duplicate-coordinate or canonical-area/centre mismatch implicated by the independent scan.

The independent native split guard reproduces all 18 flags: 13 warped source faces require no split; five require a fan, representing seven cell-base incidences. The maximum discrepancy between the independently recomputed and native plane deviation is 1.256265057258954e-15 m.

The five split-required faces are:

| Rank | Native pre-write face | Written face | Source | Deviation |
| --- | ---: | ---: | --- | ---: |
| 3 | 150103 | 2370 | internal, six points with collinear vertices | 2.06454e-11 m |
| 4 | 140700 | 627 | internal, six points with collinear vertices | 2.06451e-11 m |
| 3 | 143243 | 41377 | Default_Rack_2_frame, five-point projected concave polygon | 7.85272e-5 m |
| 3 | 143244 | 41378 | Default_Rack_2_frame, five-point projected concave polygon | 7.85287e-5 m |
| 3 | 143247 | 41381 | Default_Rack_2_frame, five-point projected concave polygon | 7.85289e-5 m |

Keeping an unsplit existing warped polygon preserves its discrete base coordinates and connectivity. It does not prove that its new cone child has acceptable face tets, volume, strict cell-plane support, weights or determinant. The five split-required warped polygons are outside the planar fan proof. No planarity threshold, original point, outline, C++ source, native binary or mesh was modified by this audit.

The written-mesh scan used the existing eight-rank optional-cleanup Tri mesh. Its complete core file hashes and all rejected polygons are in conforming-cone-tri-written-base-audit.json. Replaying the saved pure scan reproduced its per-rank records exactly. The native-to-written matching can be reproduced after native worktree cleanup from the committed coordinate witnesses alone:

    python tools/audit_air_cone_base_witnesses.py \
      --written docs/benchmarks/shinaide-product-2026-10-05/evidence/target-liquid-steady/conforming-cone-tri-written-base-audit.json \
      --native-dir docs/benchmarks/shinaide-product-2026-10-05/evidence/target-liquid-steady/conforming-cone-native-base-witnesses \
      --output .codex-tmp/conforming-cone-base-replay.json

Four focused tests preserve cyclic/reversed identity, reject a one-ULP change, exercise collinear/concave split decisions and reject nonfinite coordinates. They passed. This audit does not verify distributed coupled pairing or transformed boundaries.
