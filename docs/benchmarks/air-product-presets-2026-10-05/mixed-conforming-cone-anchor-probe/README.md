# Original-vertex fan diagnostic

The unchanged 54-cell buffered transition closes to 198 cells when every convex polygon with essential collinear vertices uses the first coordinate-canonical original-vertex fan whose triangles all have strictly positive signed area. Clean bases remain whole polygons; if no positive original-vertex fan exists, the algorithm retains the face-centre fan fallback. Both original-neighbour incidences use the same subfaces with opposite orientation.

Four mathematical guard tests pass. The real two-rank OpenFOAM allTopology/allGeometry check reports Mesh OK, zero failures and warnings; original coordinates and each parent volume are conserved. Minimum determinant is 0.016232, weight 0.2 and volume ratio 0.166667; maximum non-orthogonality is 45 degrees and skewness 0.395283. Exact inputs, checker and stdout are SHA-bound in receipt.json.

This is an analytic diagnostic, not product-case acceptance. It has no physical multi-patch identity, faceZone, processorCyclic or transformed cyclic validation. The earlier 270-cell face-centre-fan receipt remains its immutable historical result and source identity.
