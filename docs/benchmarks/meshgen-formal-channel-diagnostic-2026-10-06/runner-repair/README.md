# MeshGenerator native diagnostic report path repair

The normal React job b5a0cbc5 returned rc=1 at native S10 closure (cell457,
ΣSf=0.00390625). Its original runner read CLI ingest mapping instead of the
native S7 sidecar and logged `stage=None`. This change fixes that diagnostic
consumer. It does not fix or accept the refused native mesh.

Only production path: `src/backend/aircooling/runner/stlgen_runner.py`.
`--report` remains scratch/dc_report.json, byte-exact native command text.
Native S7 is read at out_poly.parent/dc_report.json by rc1, timeout, missing
export and the unchanged degenerate-floor diagnostic. Missing S7 never falls
back to ingest, and ingest/non-S7 JSON cannot produce a native verdict summary.

Original runner ff868797… is complete in before/. Final 8fd23969036d52fa7093e36d22eb1f7683fe7e23ae3b15c5325e9ca852636419
is in after/. Whole source before this pipeline and after the assembly method
is byte-exact, including ordinary RAM archival v4, CaseLock, source/batch,
quality, cancellation and all thresholds. Formal vendor source is unchanged.

The new test and two actual-byte JSON fixtures exercise production diagnostic
and assembly methods with `_run_cmd` controlled, not a mesh/checkMesh/solver.
First RED: 6 failed/1 passed (2.68s pytest). First related run: 17 passed/1 failed
(2.19s), retained: a negative test mistakenly disallowed `stage=None` even as
quoted rejected identity. Only that assertion was clarified to prohibit verdict
summaries/full-report output; exact diff and old test are retained. Final:
18 passed/16 deselected (0.79s pytest), seven new + eleven existing MeshGenerator
unit cases. No native, API, browser, services, build or Git actions.

All invocation receipts bind exact source/test SHA, CPU32–35, CUDA empty.
The original57 diagnostic packet was rehashed in full and left unchanged.
pytest temp/cache directories are local incidental controlled fixtures and are
excluded from this evidence manifest. No old evidence, case or product was
removed. Original raw logs, source and report bodies are included verbatim.
