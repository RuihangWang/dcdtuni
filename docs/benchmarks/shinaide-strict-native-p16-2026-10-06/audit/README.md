# P16 actual normal React mesh: preserved, strict acceptance still failed

ROOT used the normal React UI to run job 11e5fb58 in Shinaide_LegacyJournalP16_CUA. Actual final checkMesh failed two checks: D831 (printed minimum 2.4094e-7), W82 (minimum .0140854); R passed (minimum .0105268). The mesh has 5,906,774 cells. N hard check passed with maximum 86.9077 degrees and 742 warning faces. UI returned 1, enabled mesh Run/Check, left the solver tab disabled and removed the abort control. Captured console warnings/errors were empty. This entire mesh is not accepted and was not used for a solver run.

The actual native log prints Total 166.071 s and End. ROOT's rounded start/final log timestamps span 194 s. Actual UI clickedAt 00:59:00.568Z to the final printed timestamp 01:02:15Z spans 194.432 s; the later 01:05:57.575Z evidence capture is an observation time. Copy, hashing, build and audit durations are separate. No arbitrary-case timing guarantee is made.

## Preserved first-hand inputs

case/ contains every one of the 372 regular physical case files, 2,454,470,089 bytes, including all eight ranks, sets, raw source/written arrays, writer JSONs, model inputs and original logs. No symlinks were accepted. case-copy-manifest.json binds each source SHA before copying, copied SHA and source SHA after copying, plus exact source namespace/stat checks and a final complete source/copy pass. The copy took 4.41656 s. A further fresh rehash of the owned copies is in final-copy-hash-verification.json; the large live Mesh was not reread for this second pass.

root-browser/ contains all 19 ROOT browser/observer payloads, 4,647,971 bytes. root-browser-copy-bindings.json records source/copy/source SHA equality. ROOT's 1,318,474-byte P16_LegacyJournal_BrowserProof_Only.cfdproj (SHA45933aee...) is a zero-model proof-transfer helper, not a physical input. The physical project remains separate in case/. Both actual CUA JPEGs are 1280 by 720; final SHA34c6c6c1..., pre SHAdca5deeb.... These are ROOT CUA browser captures, not product canvas screenshot API output.

P15 and P16 physical project typed JSON differs only at meta.casePath. The exact P16 published cfdrun is 483,188 bytes, SHAa074393a4d67f20a39d9f439640b8e72130001521df5a4cf96a66bc92063f73e; the actual CLI expected SHA and Owned input snapshot log match it. The CLI/log bind STL batch88987445c6c7775377c443101dcd18a57fd7aff2f9952187ed77322fff3b5194. This audit does not independently import or rerun the batch helper.

ROOT's observer selected ten processes, including eight actual dcCart ranks. All eight executable/mapped-library identities are APPc2013d92.../LIB2c18c05f..., with rank CPU assignments 16 through 23. Ten selected processes are not a full eleven-process claim, nor a claim of final process absence. held-source/ and source-and-causal-bindings.json bind the unchanged applied two-source journal implementation, author preview, independent source review and normal build.

## Actual journal admission

Installed context records captured four views on rank1 and six on rank2; all other ranks captured zero. All ten captured views failed the first legacy predicate. Current, attempted, generated, prepared and committed repair counts are zero on every rank. Legacy local accepted counts are [0,86,9,89,4,13,20,2], summing to223; old literal global examined2550/accepted223/protected328/geometry1924/quality75, two rounds, one capped rank, maximum reported old-literal wall .326354 s.

This proves the journal did not admit any captured P16 view. It does not identify why: completeness, recorded count, resource limits, identity, budget and dependency overlap all return the same coarse legacy reason. No journal count, retained capacity, charged payload, accumulated work or overlap reason was printed. Later original active-point, point-double-bit, W packet, cell-ring and source-origin gates were not reached for these ten views. Their actual P16 outcome is unknown; zero later counters do not prove those gates passed. See OBSERVABILITY_DESIGN.md for the smallest proposed instrumentation; no further implementation is authorized or performed here.

MPI stdout fragments are kept exactly. The rank5 admission tail and one repair tail are interleaved; missing fields stay unknown rather than being joined to anonymous fragments. Literal JSONL rows are not all journal legacy rows: rank1 has88 rows but86 legacy accepts because its two W-packet records are also present. No internal rejection cause is inferred from these records.

All eight final writer JSONs report zero failures and 296,686 protected face records total. They explicitly say local-pass-awaiting-global and remoteQualityRecomputed=false. This audit read the actual statuses/counts and binds the files; it does not independently certify every earliest/final DP value, real compact-owner remapping or remote quality. Native End is not strict mesh acceptance.

## Reproducible bounded audit

audit_final_v3.py is the successful stdlib-only frozen-copy audit. audit-final-v3.stdout/stderr/result.json retain exit0. It strictly parses the actual ASCII labelSet body after FoamFile, including an inline empty 0() even when the header says binary; totals independently recompute to D831/W82/N742. Rank-local compact labels are not compared across P15/P16 topology without a mapping.

Original audit-tool mistakes and their failures are preserved: initial empty-list parser, nonexistent header-name read, v1 Total-line whitespace assertion and v2 assumption that each MPI rank has a whole16-23 CPU range. The corrected actual data has Total 166.071 s and each native rank bound to one distinct CPU16..23. These are audit tooling failures, not product failures. No business/native/checkMesh/solver command, API/browser/service/Git action, build/test or source mutation was performed during this read-only audit.

manifest.json declares every actual payload in this owned directory (including the complete case and original failed audit artifacts), with size and SHA. Existing author, independent, build, ROOT and physical-case artifacts were not overwritten.
