# Immutable cfdrun snapshot materialization foundation

An explicit owned byte snapshot can supply the existing cfdrun reader without reading its logical artifact file. Original file mode remains available. The frozen snapshot binds schema 2/3/4 bytes, SHA256, canonical case/model logical path and fresh parsed JSON; mutable byte providers cannot change its contents. Schema/STL resolution, original BC lowering and materialization remain the existing reader implementation.

Original missing-capability baseline: 14 failed and 1 file-control passed. First implementation: 16 passed. Related snapshot/reader/v2-schema regression: 147 passed. Commands and original raw failures/results are retained. No normal frontend/native build, runtime server/runner/receipt/transport change, browser action or actual RAM air execution occurred. This foundation requires independent source review and subsequent complete parent/child integration.

Explicit snapshot mode keeps resolve_stl default true and rejects missing real STL. Logical path is identity only; it must stay in the selected canonical case/model directory, and no source-file fallback is used. Empty, greater-than-64MiB, invalid versions/JSON or mismatched expected hash refuse. Physics and native strict quality remain unchanged.
