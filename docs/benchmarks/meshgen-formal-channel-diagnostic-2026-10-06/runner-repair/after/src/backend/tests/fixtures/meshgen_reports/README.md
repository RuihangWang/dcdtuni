# Actual MeshGenerator reports

These two JSON files are unmodified byte copies from normal React job `b5a0cbc5`,
case `Meshgen_Formal_Channel_CUA`, 2026-10-06 00:19:03Z–00:19:06Z. The native job
returned rc=1 at the S10 open-cell assertion; it did not export a usable polyMesh.

- `ingest-mapping.json`: CLI `--report`, `Mesh/.meshgen_scratch/dc_report.json`;
  15,229 bytes; SHA-256 `757c6a1774aded153e7813084fd8a031e0fa8af1909a3f1dce9216c4f124e5cd`.
- `native-s7.json`: native `writeDcReportSidecar`,
  `Mesh/.meshgen_scratch/constant/dc_report.json`; 25,277 bytes;
  SHA-256 `a27d7651f8d582925265406d1d8435283e2ec71c3d508acf264805e62f082d78`.

The original browser log and all input/report/source bindings are preserved in
`.codex-tmp/meshgen-formal-channel-native-exit-independent-review-20261006`
(manifest SHA-256 `29d0357496cb2e20fc9cc3abf37f5137e2f2607a9880562e0230aba192b48c89`).
These are diagnostic fixtures, not successful mesher or quality acceptance.
