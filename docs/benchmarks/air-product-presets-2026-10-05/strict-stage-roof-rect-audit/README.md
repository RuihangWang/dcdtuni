# Read-only regular transition roof audit

The default native path is unchanged. `DCCART_TRANSITION_ROOF_AUDIT=1` enables candidate detection and temporary, fully restored point-coordinate trials. `DCCART_TRANSITION_ROOF_RECTANGULAR=1` additionally recognizes rectilinear boxes. Neither flag writes a roof displacement. Recognition uses axis-box faces, one four-tile side and five singleton sides, nine distinct shared grid points and four distinct half-size six-face box neighbours. It excludes other refinement patterns.

The proposed normal displacement is 0.0005 times the coarse normal span at the shared face centre, half of that at its edge midpoints, and zero at its corners. Physical/coupled/face-zone vertices remain fixed. Any pair of packets sharing an affected cell is rejected, including packets sharing no moved point but overlapping point stars. Isolated packets are checked against all affected cells for face planarity, vertex halfspaces and the installed OpenFOAM strict other-face-centre plane criterion. This is a geometry-only screen: determinant, face-tet, weight and ratio authorization is intentionally absent; survivors must never be treated as accepted meshes.

The first square-box recognition found zero candidates in both actual cases. Binary preflight confirmed the native grid uses separate axis extents: representative Tri_angle nine-face cell spans [0.253333334,0.25,0.255010846] m; target Liquid spans [0.25,0.255555556,0.246875] m. An explicit rectangular mode was therefore tested, with detailed rejection counters.

| Actual API run | Regular rectangular packets | Protection (physical/coupled/zone, overlapping counts) | Remaining packets | Full-star overlap veto | Planarity veto | Survivors | Mesh API wall |
| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: |
| Tri_angle `4cb41e01` | 1499 | 180 / 531 / 0 | 815 | 813 | 2 | 0 | 7.094 s |
| Target Liquid | 77435 | 2679 / 5507 / 1743 | 69369 | 69369 | 0 | 0 | 84.494 s |

All complete quality failures stayed unchanged: Tri_angle failed five checks; target Liquid failed four. The native audit is not a repair or a product acceptance. Tri_angle's 48 core polyMesh files are byte-identical to its baseline. The two target audit runs are byte-identical across all 48 core files. Comparison against the older target source additionally found only boundary dictionary matchTolerance/order differences; geometric arrays were identical, and that older comparison is explicitly retained without a full-byte-identity claim.

Exact tested source patches, artifact hashes, original SSE/API receipts and preflight coordinates are preserved in this directory and the three companion roof-audit directories. The next research step may combine compatible proposals over a component, but it must retain every protected-point rule and check the entire component boundary point star. It must not relax checkMesh thresholds.
