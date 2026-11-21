# V5 normal React mesh run 5a55bbf9 — ROOT reviewed failure receipt

This actual 8-rank run finished with return code 1 and two strict mesh failures: 892 underdetermined cells and 99 low-weight faces. The volume-ratio check passed (R0), but the large case remains unaccepted and no air solve was started.

The native mesher printed Total 143.284 s. This excludes subsequent checkMesh, export, build and archive work. Normal Allwmake took 86.444674821 s; the delta archive operation took 12.367428727 s. These are separate measurements. The 20 s point-repair budget and 300 s runner watchdog are limits, not evidence that the mesh passes.

Compared with ce178f4a, ROOT verified 15 D labels and one W label disappeared with no added D/W failures. Non-orthogonality warnings changed from 754 to 748 (seven removed, one added); the stock non-orthogonality check remained OK with maximum 88.2022 degrees. The 56 topology, boundary and zone files, totaling 434,635,556 bytes, remained bit exact. The point comparison records 16 changed points from the original and 10 from ce178; the existing R point 20894 was safely updated again by DW, so the old R coordinates are not all unchanged.

Normal source verification checked 12 original grouped STL files. Before/after input bindings retain the project and all 13 derived STL hashes; only the cfdrun byte hash changes. The raw hash used by the role contract is f28097763be26bfa94dca3e1f01e5ec16240caf6c568116af294d1bc8fd04781. Byte identity and the ROOT authored interpretation of run metadata are distinct evidence scopes.

The source/ELF bindings are preserved in build-source-inputs.json, native-build-result.json and native-live-executable-bindings.json. The header SHA is 81e5a0806ba3602d451393e480948cb46daa70ca423dcc2258edc1cfa329e3d9. Native coupled-certificate failures were zero on all eight ranks, which does not replace final full checkMesh. DW proposal cap was 8 and joint core cap was 128. Backend tests used the actual installed modules: pytest reported 104 passed in 11.91 s; the enclosing command wall time was 15.414223389 s.

ui-terminal-observation.json records ROOT's actual browser DOM observation after completion. It also preserves the then-known reload/solver-tab usability gap. A viewport image cannot prove sidebar or terminal state; no PNG is included here. Newer frontend repairs must not be retroactively attributed to this run.

Both raw SSE and decoded logs are copied without edits. verified-key-lines.txt provides exact numbered excerpts; rank summary lines remain interleaved. Build/import receipts saying the business job had not yet started describe their earlier preparation step.

archive-chain.json binds complete ce178f4a -> V5 delta -> V6 delta and names the preserved large archives. Archive integrity checks are the original ROOT decompressed-byte receipts; this publication did not re-extract those archives. Binary mesh arrays and ELF bytes remain in repository preservation archives rather than this small bundle.

ROOT reviewed the raw copied bytes and the limits above for publication. The original publication-draft-receipt.json records the earlier preparation step and copy identities; root-publication-review.json supersedes its pending review status. Final manifest.json binds this reviewed bundle. This publishes failure evidence, not a passed mesh or large-case solve acceptance. No business job was started for publication.
