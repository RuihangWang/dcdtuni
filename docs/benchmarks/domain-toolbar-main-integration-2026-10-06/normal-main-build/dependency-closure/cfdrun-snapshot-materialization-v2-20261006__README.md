# Snapshot post-copy capacity closure

A fresh bounded byte provider may report a small pre-copy length and return larger owned bytes. The actual subclass fixture reports 2 bytes and returns a valid schema2 JSON larger than the test-local 64-byte cap; original implementation actually accepts it (retained failing assertion). The candidate checks the actual immutable owned byte count immediately after copying, before hashing/JSON/path processing.

Only the two-line post-copy size gate and its targeted regression are new. Original v1 17 payload and manifest remain unmodified. Updated related Snapshot/reader/v2-schema suite: 148 passed. No normal RAM execution, transport, runner, server, receipt, native or browser change. Independent source-only review pending.
