# P11: local W proof survives, whole large mesh remains Failed2

Normal React job 04fdafe2 completed native meshing and ran the final strict checker. Mesh job return code 1 and checkMeshOk:false are genuine failures: D835 bad cells, W82 bad faces, R0, 5,906,777 cells, Failed2. Two locally accepted cross-parent W unions survived installed prefix and final verification, but this does not accept the entire mesh or any air solver.

The former failing packet (keep/drop 715455/945959, shared 2167751) proved two unchanged exterior bad-W face geometries for its comparison guard. Both actual prefix and final log records retain comparison/shadow severity 0.75109063333106985 and actual installed severity 0.75109063333764969. Their printed difference is 6.57984e-12. The actual severity bits are NOT unchanged: the records and source distinguish exact exterior geometric equivalence from floating recomputation. This proof must not be described as all actual metrics bit-identical.

Native Total 146.353 s is explicitly present in the log. SSE capture 150.9838256 s and archive 45.4872695 s are separate wall intervals; neither is native Total and they must not be added to it. This one run does not prove an all-case five-minute guarantee.

The fourteen project/STL input bindings are exact before/after. Independent raw cfdrun comparison against P10 finds only /runId and /sourceProject/exportedAt changes. The raw cfdrun and actual SSE/decoded/build logs are deterministic gzip copies that decompress byte-exactly. Root's actual UI observation records run/check enabled, abort absent and solver disabled after failure; this audit did not operate the browser.

The complete case has 380 files: 53 changed (760,611,866 B), 327 unchanged, 0 removed against V8. Its delta archive is 77,123,312 B, SHA 6d54baa4e3cba4d62d09abb247c83eab8a887af653e7000ef1de446bb9690bf2. Copied inventories/receipts retain the large-artifact location and decompression verification. No mesh, ELF or viewport image is included.

Build source/binary/live-mapping receipts bind the reviewed V2 proof helper/core and unchanged V8 finite header. Optional source copies were included only when their bytes match the frozen P11 input SHA; they are evidence, not production edits. Geometry equivalence is narrow to qualified exterior bad-W faces, not unchanged whole mesh topology or all numerical values.

This is a new staging package for ROOT review/publication. Existing proof, source, docs, case and Git data were not modified. manifest.json binds every selected source and staged byte; independent-review.json records exact log references, durations and limits.
