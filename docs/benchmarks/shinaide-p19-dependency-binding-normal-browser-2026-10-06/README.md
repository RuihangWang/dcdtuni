# P19 dependency binding: normal React run, strict mesh failure

ROOT invoked mesh from the actual React UI on 40901, job `8a60dd61`, using baseSize0.25,8 MPI ranks and the same32-model/800-render-mesh physical engineering input as P18. Only output casePath changed. This run is not an accepted mesh or solver input.

Native Total was175.11s. Browser invocation03:05:20.745Z to final terminal03:08:37Z was about196.3s including strictcheckMesh. `checkMesh -allTopology -allGeometry -parallel` still Failed2:831 underdetermined cells and82 low-weight faces in5906774 cells. All8 union JSONLs and all16 D/W quality sets were byte-identical to P18. UI generation was enabled after failure, solver remained disabled, and browser console warnings/errors were empty. All8 mesher PIDs996888–996895 exited.

The actual eight ranks loaded the new library8146dd878405cd7a0a7e976ba8646ada3bd414270b1e4d1de108c0d2aa740a2e and unchanged applicationa2910882a3d883507821a314c7cbe13e07a010150ce468c3b68ddc8ac9153575; per-rank CPU pinning16–23,CUDA empty and30 DCCART selected flags were checked against the actual backend. All33 source controls, physical input and6 launch/supervisor/backend/Kit identities remained held through result freeze.

New observation binds the first genuine dependency: rank1/entry8/legacyCell25236 is second read halo, overlapping source facet2084850 with a current-view face; rank2/entry2/cell798295 is legacy second read halo becoming current view core. These records are matching item/counts observations, not full replay, proof of safe commutation or permission to drop a dependency gate. Both clockUnknown=false.

The74 payloads,3,512,202 bytes, are frozen in `payload/`; original manifest SHA9a01b1245f27088caa577a04b9aaa24f73ad4e89e80242c7f177655eefe6cde9 is retained unchanged. The full browser DOM includes the earlier P18 log followed by P19; the job IDs distinguish them. The saved image came from the actual P19 browser tab via a separate zero-model React evidence carrier. That carrier and mutable live logs, native binaries/cache and multi-gigabyte mesh files are excluded. Source copies are evidence, not a Main mesher deployment.

Git display checkpoint preceding this receipt: Main d9be9451371b35c28087f23f27547486a55104cc and integration4698937301fc8535d06aed025b44ee0d66a357cd, both exact on origin and shared. The first integration SSH push stalled; only its owned SSH child was terminated after identity check, and bounded retry completed. Existing worktrees remain required by the active goal.
