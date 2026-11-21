# Parent independent recomputation of actual written geometry

The parent reran all three read-only audit tools against the hash-bound old rejected and newly writer-compatible target meshes. No native mesher, checker, solver or product job was launched by this recomputation.

The old written failure has exactly 38 open tetrahedra on six ranks, whose unmatched edges infer 38 missing triangles. All original point IDs remain present, the shortest point distance is 4.88887 mm, and every inferred triangle is below the unchanged writer area-drop threshold. This does not establish the first native mutation.

The old local cyclic mesh has 24 pairs and 25186 faces per side. Geometric multisets and face indices match, but 28 faces have wrong vertex-zero/order and produce 42 first nonidentity point assignments.

The newer written mesh has 24 cyclic pairs and seven processor pairs, with 25186 and 123157 faces per side respectively. All roles, counts, indexed polygons, point-zero and same-start reverse order match exactly. Actual processorCyclic count is zero. The summaries exactly equal the agent records.

Raw recomputed JSON retains actual mesh and tool hashes. These geometric proofs are separate from full mesh acceptance: the newer large target still fails four quality checks.
