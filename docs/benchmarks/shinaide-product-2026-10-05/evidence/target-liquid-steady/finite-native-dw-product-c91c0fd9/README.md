# V6 normal React mesh run c91c0fd9 — ROOT reviewed failure receipt

This actual 8-rank run finished with return code 1 and two strict mesh failures: 867 underdetermined cells and 98 low-weight faces. The volume-ratio check passed (R0), but the large case remains unaccepted and no air solve was started.

The native mesher printed Total 144.549 s. This excludes subsequent checkMesh, export, build and archive work. Normal Allwmake took 67.215433185 s; the delta archive operation took 12.636372475 s. These are separate measurements. The point-repair stage retained its 20 s limit; a watchdog or budget is not a successful-mesh guarantee.

Independent comparison with V5 establishes 25 D labels and one W label removed, with no added D/W labels. Non-orthogonality warnings changed from 748 to 746 (three removed, one added at rank 6 face 1501125); the stock hard check remained OK with maximum 88.2022 degrees. All 56 topology, boundary and zone files, totaling 434,635,556 bytes, are bit exact against V5, ce178 and the original. Label-set comparisons are therefore tied to unchanged topology, not arbitrary labels across different meshes.

The point parser compares actual binary 64-bit payload bits. It finds 23 point rows changed from V5, 33 from ce178 and 39 from the original. ASCII labelHashSet payloads are parsed as ASCII even where the file header says binary. These format details and exact deltas are recorded in the independent comparison files.

Thirty-four complete accepted proposal lines parse successfully: two R and 32 DW (30 three-parameter and two nine-parameter). Interleaved MPI summary counters do not provide a complete ranked or rejection queue. The caps changed to 64 DW proposals and joint core 512; individual core 128, per-proposal request cap 256 and physical, source, quality, coupled and rollback gates remained in place.

All eight emitted earliest/final installed writer payloads were independently compared for identity, aliases, ordered double-precision geometry, patch and zone roles. Their total is 296,686 rank-local protected-face records, not a deduplicated global interface count. All local certificate failures are zero. This comparison does not re-run remote quality or override the final Failed2 result.

Normal before/after bindings retain the project and all 13 derived STL hashes; only the cfdrun byte hash changes. The publication copies preserve the byte binding and ROOT's original metadata interpretation separately. Source/ELF identity is in build-source-inputs.json, native-build-result.json and native-live-executable-bindings.json. The header SHA is de5741ca5bfd3b8e542af1c619ec9e31877510e5c10caa78dca818bdf7eeb466; current main source can be newer.

ui-terminal-observation.json is ROOT's actual DOM observation. It preserves the then-known reload gap and is not a new screenshot. No viewport PNG is used to prove controls. Raw SSE, decoded output and exact numbered key lines are included. Rotated prior V5 output is excluded from the current-run conclusions.

archive-chain.json binds complete ce178f4a -> V5 delta -> V6 delta. ROOT also froze the independent analysis: 126 payloads / 327,931,359 bytes, all 37 raw copies bound to V6, archive SHA 828ea47e8246cf31546ffe04e311bb6047b752b785229c3d40893db17be4c8a1. These are preserved verification receipts; this publication did not re-extract the large archives. Binary points, full mesh and ELF bytes remain in repository preservation archives.

ROOT reviewed the raw copied bytes and the limits above for publication. The original publication-draft-receipt.json records the earlier preparation step and copy identities; root-publication-review.json supersedes its pending review status. Final manifest.json binds this reviewed bundle. This publishes failure evidence, not a passed mesh or large-case solve acceptance. No business job was started for publication.
