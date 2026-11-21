# Source-component geometry review: unresolved RED counterexamples

**Status:** isolated candidate `codex/source-component-validation-20260927@329dda86`, not mergeable and not connected to production meshing. The pure read-only validator has a valid Shinaide 354-source baseline, but that alone is not a general geometry admission proof. Its code and RED tests remain in `/Users/litianyu/Desktop/AgentWorkspac/dcdtuni-source-component-validation-20260927`, outside the main development branch.

The committed `test_source_component_validation.py` contains three intentional RED cases:

1. Two 0.001 m right triangles overlap by only 5e-13 m². Each full triangle is 5e-7 m². The current 1e-12 m² clip-area threshold silently accepts the duplicated support and double-counts that part. A smaller numerical threshold alone is unsafe: the clipping routine reports a small positive area for two triangles with **exactly shared original vertices and opposite interiors** along a legal common edge.
2. Two planar source triangles have edges at y=0 and y=-5e-13 m. The 1e-12 m on-edge test and 1e-10 m projected rounding turn a genuine tiny gap into one connected component. Strict admission must report precision ambiguity, not weld source geometry.
3. Two triangles are on z=0 and z=5e-8 m. They fit inside the current 1e-7 m single-plane tolerance and share a projected edge, but no 3D edge. The current 2D edge ownership again reports one component. Component connectivity must respect original 3D source position or fail closed.

Two positive controls constrain the repair: a 10 m rotated rectangle with a fully shared diagonal, and Shinaide `Raised_Floor` with valid nonconforming T-junctions. Both must remain admissible without editing their original STL bytes. The former exposed floating-point clipping false overlap; the latter requires in-memory edge subdivision. A repair needs robust source-triangle orientation/intersection and true 3D edge ownership, including partial shared edges, before using any tolerance to classify a relation. Tuning one absolute threshold is insufficient.

The separately observed hash/read race has been fixed in this isolated candidate: after the existing file-set check, the validator hashes the same byte buffer it parses. A test swaps the STL after the first check and now rejects it. The 50-triangle order-invariance regression also passes. The complete original Shinaide package still gives 354 source faces, 354 components and 752 triangles with canonical summary digest `1b34b954623ce3b86aa40ee9bf85c7540f7029686cca5e349a892918300f025e`; the three REDs show why this package result is insufficient for admission.

Next work should define exact/robust planar predicates and source contour topology under a versioned precision profile, then require all three REDs and both positives to pass before integration. Do not add this module to the mesher, relax `checkMesh`, or infer physical wall pairs from near geometry in the interim.
