Read-only P12 vs P11 failure-set audit

Jobs: P11 04fdafe2; P12 52d0e89c. All 56 topology/zone/boundary entries have equal SHA and byte count in the root complete manifests. This audit freshly hashes only recovered old small sets and current P12 small sets, not the full mesh.

ASCII labelSet lists are parsed and count/uniqueness checked even where the Foam header says binary. Recovered old payloads must exactly match the P11 full manifest; each current payload matches P12 and is rechecked after comparison.

{"D": {"addedTotal": 1, "newTotal": 833, "oldTotal": 835, "removedTotal": 3}, "N": {"addedTotal": 0, "newTotal": 742, "oldTotal": 742, "removedTotal": 0}, "W": {"addedTotal": 0, "newTotal": 82, "oldTotal": 82, "removedTotal": 0}}

Three logged checkpoint commits do not mean three D failures were globally cleared. Native Total is 151.93 s from the actual result receipt; P12 still has Failed 2 checks and is not accepted. Recovery/source files, exact per-rank removed/added labels, unknowns and archive scan times are in failure-set-comparison.json. No native/checker/API/browser/source/index operation was performed.
