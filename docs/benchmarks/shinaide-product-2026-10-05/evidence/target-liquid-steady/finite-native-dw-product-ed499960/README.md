# V7 normal React mesh run ed499960 — ROOT reviewed failure receipt

The actual 8-rank React mesh job finished with return code 1 and two strict failures: 844 underdetermined cells and 98 low-weight faces. The volume-ratio check passed. No large-case air solve was started.

Native mesh generation printed Total 144.868 s. Normal Allwmake took 69.618983959 s, full-case delta preservation 33.943833505 s, and independent result audit 24.515223341 s. Build, checkMesh, export and preservation are separate measurements. A bounded repair stage is not a successful-mesh guarantee.

Independent comparison against V6 establishes 23 D labels removed with no additions, and unchanged W labels. Non-orthogonality warnings changed from 746 to 743: four removed, one added at rank 3 face 1468706. The stock non-orthogonality hard check remained OK. All 56 topology, boundary and zone files (434,635,556 bytes) are bit exact against V6, V5, ce178 and the original, binding these comparisons to unchanged topology.

Actual binary64 point payload comparisons find 13 rows changed against V6, 36 against V5, 46 against ce178 and 52 against the original. Forty-seven complete accepted proposal lines parse: two R, 43 three-parameter DW and two nine-parameter DW. The interleaved log is not a complete ranked/rejected queue.

V7 only increased DW proposal capacity to 256, joint outside cells to 512 and joint faces to 4096. Existing individual stencil limits, finite per-proposal requests, clock limits, source roles, physical conservation, quality gates, coupled geometry and final rollback remain in place. The exact source header SHA is 60937f7f1f0d8c8bb0aa3f3d3cae71364c435c618ece2a64612be0364fac43ee. All eight running native processes were bound to the built library SHA 9a73d864ec05313fa16672b880c6899b6e615328927443450725ae49fbb1e6e7.

All eight earliest/final installed writer payloads compare successfully for identities, aliases, ordered double-precision geometry, patch and zone roles. The 296,686 protected-face records are rank-local, not a deduplicated global count. This local certificate does not replace full strict checkMesh.

ROOT verified project and all 13 derived STL hashes before and after the run. Full cfdrun JSON comparison changes only /runId and /sourceProject/exportedAt; mesh, physics, source and geometry inputs are identical. The archived mesh-quality-receipt.json is a rejected V6 artifact bound to the older cfdrun. It is explicitly excluded from V7 quality proof. Current terminal status, current log and current label sets establish V7 failure; no receipt refresh is claimed.

ui-terminal-observation.json records ROOT's real DOM result: mesh/check controls unlocked and solver disabled. It is not a screenshot. Raw SSE and native log bytes are preserved. Large point/mesh/ELF payloads remain in repository preservation archives. archive-chain.json binds complete ce178 -> V5 delta -> V6 delta -> V7 delta. Existing ROOT decompressed-byte receipts are copied here; publication did not repeat large extraction or start a business job.

This bundle publishes reviewed failure evidence. It does not claim a passed Shinaide mesh, a large-case steady solve, or transient acceptance.
