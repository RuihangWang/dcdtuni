# One actual bistellar cavity, rejected

The frozen job `41c554a7` still fails the full stock quality gate. This directory evaluates one original-node 2-to-3 candidate without writing a mesh or running a solver.

The candidate is rank 1 cells 525057/671013, input-stage parents 30174/53738, ordinary internal shared face 1499187 (source base 103110, zone −1). Both cells belong to air region 0. The first bounded search on parent30430 found four edge/outside intersections and no strict-interior cavity. The subsequent bounded actual-D-seed search stops at the first exact legal cavity: 24 refusals and this one candidate, without further enumeration.

Exact rational lifting of the original binary64 coordinates proves that the new AP-to-AP edge crosses the shared triangle strictly inside, the original six-triangle cavity is convex, all three new tetrahedral volumes are positive, and total volume and first moment are preserved exactly. All six original exterior point cycles, patch/zone identities and actual outside-neighbour roles are retained. The compact stencil contains both original cells and all six direct neighbours with their complete original cell topology.

The geometric preflight passes, but quality rejects the candidate. The original minimum determinant is 0.000630567 with one failed cell; the replacement minimum is 1.02794e−7 with all three cells failing. Maximum nonorthogonality increases from 56.1624° to 85.1189°, producing four failed faces. Weight, volume ratio and skew remain within their original thresholds. One removed cell previously passed determinant, so each replacement inherits that passing role: all three would regress. Two retained exterior faces and two new internal faces also regress nonorthogonality. No production change follows from this refused candidate.

The exact partition proofs are distinct from OpenFOAM-style floating volume/moment reconstruction (relative volume difference 3.23e−14). The nine prospective faces separately verify canonical owner<neighbour, original point0 and area-vector reversal. The eight tests exercise a symmetric exact-volume bipyramid, edge/vertex/outside refusal, singular crossing, coordinate translation/permutation, outward directed edge closure, canonical owner reversal and a zero-volume tetrahedron.

Reproduce the local quality from the saved complete stencil, without reading a live mesh:

```bash
PYTHONPATH=tools python tools/audit_air_bistellar_quality.py \
  --stencil docs/benchmarks/air-product-presets-2026-10-05/bistellar-cavity-audit/rank1-first-cavity-complete-stencil.json \
  --geometry-tool ../integration/tools/audit_air_mesh_geometry.py \
  --output .codex-tmp/bistellar-cavity-audit/reproduced.json
```

`receipt.json` and `manifest.json` bind the source tools, complete original stencil and separate exact/quality evidence. The diagnostic oracle dependencies remain explicit; this small candidate is not a native product acceptance or a proof that every alternative flip is impossible.
