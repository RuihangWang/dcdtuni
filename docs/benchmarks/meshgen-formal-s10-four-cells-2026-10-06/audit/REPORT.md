# First-hand finding and causal scope

The actual S10 census reports1344cells and four kind1 closure failures: compact cells457,463,469,475. Each has13faces, level4, splitPiece1 and vector residual (0,+1/256,0). The four residual magnitudes are exactly0.00390625 in the literal printed-coordinate arithmetic. kind1.familyExclusive in the native JSON is coplanar1/none3, while S10R text labels allfour fam=coplanar. These native classifier outputs are preserved separately; the geometric plane result below does not redefine them.

## Complete actual face arithmetic

For each of the four cells, face10 is patch17, neighbour−1, four vertices on Y=0.5, occupying one quadrant of the Thermal_block footprint X/Z=[0.25,0.375]. Its area vector is (0,+1/256,0), and its printed centroid pyramid is negative. The remaining12faces sum exactly to zero. Splitting collinear directed edges at all stored printed points and cancelling opposite directions produces zero edge residual for those12faces; adding face10 leaves four unpaired directed segments. This is an extra wrongly owned cap in the supplied upper-cell shell, not evidence of a missing balancing fluid face. Reversing face10 alone changes the residual to−Y/256 and fails closure.

Diagnostic omission of face10, without mutating any mesh or declaring a repair accepted, yields a closed positive L-prism with exact V=15/32768 and reference-invariant exact first moment. The native open shell's bbox-centre reference gives V=13/32768 and reproduces its printed centroid to the displayed rounding; reference zero gives a different open-shell volume. That original open V is not a physical conservation invariant. Bottom and top fluid-complement areas are already both5/1024 for each cell.

The Fraction proof is exact for the diagnostic decimal values (dyadic coordinates here). These are %.9g log rows, not an exported binary point array; no native point-bit identity is asserted. All four complete coordinate tables and raw line numbers are retained in actual-cell-faces-points.json. Exact results and counterexamples are in fraction-closure-and-cap-ownership.json.

## Native surface and emitter ownership

The actual mapping record for Thermal_block_Bottom is BARRIER_SHEET, wallPair, wall, floodBarrier=true, from boundaryBindings/heatSource. It must retain legitimate neighbouring wallPair half ownership. Alphabetically sorted report.patchNames is not the actual emitter array. Source-backed reconstruction uses Cartesian.cpp:1441–1486 (STL first appearance), main.cpp:1482–1575 (file and target first appearance), plus all4copied literal STLs. Actual native patch17 and triangles34/35 are Thermal_block_Bottom; actual patches12–17 are Front/Back/Left/Right/Top/Bottom. The two bottom triangles have total area vector−Y/64. The report and emitter name arrays differ, despite index17 coinciding. actual-surface-patch-order.json binds the exact role/order/triangle records.

Cartesian.cpp:11933–11962 sideOfPt3 classifies a point by barrier signature or representative visibility without verifying membership in its owning leaf. Wall-fragment direct probes at12551–12552 use tc±10*clipTol*n. Wrap inheritance at12603–12643 can replace failed classifications. Direct emission at12664–12694 and edge fallback at12696–12716 append the physical face to whichever kept local component is returned. Current strict-interior complement rescue at12737–12754 excludes leaf-boundary planes.

Actual phase rows for upper leaf747 (phase457) and leaf768 (phase478) contain complete boundary faces with Y range0.5–0.59375. Their Thermal_block bottom fragment has centroid Y=0.5 and normal−Y. Thus p+ is below that upper leaf for positive clipTol, yet the actual classifier assigns it to the local kept component and emits the +Y wall tile. Raw lower phase161 supplies the same footprint and a kept p+ emission; analogous t34/35 rows exist for the other quadrants. Raw rows, including thread interleaving, are preserved in phase-emitter-raw-witness.json.

Phase IDs457/464/471/478 are not joined to compact S10 IDs457/463/469/475 by number. No persisted origCi/compaction map was found or guessed. Correspondence is limited to source roles, plane and literal geometric footprint; a full cross-stage cell mapping remains UNKNOWN.

## What this proves and what remains

The supplied actual upper-cell shell has an erroneous cap, and source/log evidence identifies out-of-leaf wall-half classification as a concrete emitter defect. It supports the narrowly scoped ownership guard in MINIMAL_SOURCE_DESIGN.md. It does not authorize globally deleting bottom physical faces, treating wallPair as solid-only, changing S10 tolerance, adding closure faces or flipping wrong faces.

Complete native counterpart shells, all boundary area/moment ownership, whole-mesh V/M1, final stock checker and runtime outcome after a guard are not yet proved. No repair has been applied or run. The strict refusal remains correct. Original physical input, source and CLI identities are held, and no solver input was produced.
