# Independent readonly ce178f4a versus 495546f2 audit

This real UI run remains Failed2: 907 determinant failures and 100 low-weight faces, R0. It is not an accepted full mesh or a solver validation.

The current case's 56 topology fields (faces, owner, neighbour, boundary, cellZones, faceZones, pointZones on all eight ranks; 434,635,556 bytes) match the frozen 495 manifest SHA exactly before label comparison. All 56 stored sets were then compared against exact old495 bytes. Only rank3 determinant cells 609195 and 609211 disappear (909 to 907), with no new labels. All 100 W labels, all 754 N warning labels, 106514 edgeFaces, 21799 multiRegionPoints, and both region sets (5322909 plus 583870 cells, total5906779) remain exactly equal. All unchanged sets are byte-identical as well.

There is no low-volume-ratio failure set in either stage. The actual new stock log confirms Face volume ratio check OK, minimum0.0103201; skew3.65657 OK and Nmaximum88.2022. Absence of a set alone is not used as an R0 proof.

All 15 business files were independently rebound to the root before/after manifests. The 13 STL and project bytes remain exact. The cfdrun changes only /runId and /sourceProject/exportedAt; all mesh, physics, sourceProject/contentHash, materialization, geometry, and boundary data are unchanged.

No old archive was extracted. The old495 cfdrun was rebuilt by applying the eight previously recorded metadata replacements to the immutable7785 cfdrun and serializing JSON indent2 with no terminal newline. Its entire byte SHA matches b74bcae56133c1b26ffa33c61dadfedccfdd442567ec3110145e41d2e76b8019. The old495 rank1 warning set was reconstructed by enumerating a single deletion from the immutable7785 set: exactly one candidate matches the recorded old495 SHA. Every other old set already matched the old495 manifest, so no older-stage N label counts are silently substituted.

Commands: audit_input_and_topology.py then audit_all_sets.py with the dcdtuni Python, taskset36-39, CUDA_VISIBLE_DEVICES empty, OMP/OPENBLAS threads1, PYTHONDONTWRITEBYTECODE1. Both audit tools are pure readonly business/source checks; only this independent-review directory is written. The label parser correction for compact OpenFOAM ASCII set bodies was completed before freezing.

Scope: no native/API/checker/solver/browser/build/index operations, no old proofs or current case mutations. These are observed full-run topology/failure/input facts, not authorization for a new semantic zone contract or claims of global quality success. The baseline/final installed writer certificates remain separate parent/AIR checks.
