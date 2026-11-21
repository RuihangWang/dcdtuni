# One real writeNow control lifecycle — 2026-10-05

This single additional regression checks the new dcRunTransient readModifiedObjects/cap lifecycle on real MPI execution. Existing Python tests and the earlier endpoint regressions did not exercise a live stopAt edit, so one new run was necessary. No solver source, physical threshold, artificial field or time assignment was changed.

The actual React export configured the same fully checked 511-cell channel for duration 0.5 s, deltaT/maxDeltaT 0.001 s and output every 0.1 s. The authenticated solve-only API ran on the dedicated 8392 service with two MPI ranks on CPU 36–39. Genuine SIMPLE initialization again converged in 337 iterations with the unchanged 7932473e executable; the PIMPLE b49ea3b9 executable and all paired source hashes remained unchanged.

API job e4e3272b was still active after genuine PIMPLE transport solves, with the observed physical time at 0.123 s. The controller atomically changed only stopAt endTime to stopAt writeNow in that owned run's system/controlDict. It used ordinary filesystem mtimes, not timestamp forcing or process signals. The installed file watcher is timeStampMaster with a normal five-second skew; the controller waited until the original control file was over six seconds old before making the request.

Native logs explicitly record Re-reading object controlDict, then the genuine final Time = 0.127 solve, field write and normal End. All 127 real computed times are increasing and below the requested 0.5 s. The process exited normally with API returncode 0 and both executables had no remaining fuser users. The API read-file response for the native convergence.json explicitly reports TRANSIENT_INCOMPLETE, with final time 0.127 s of 0.5 s. The same verdict appears in the API SSE log; no completed verdict appears there. A normal process exit and trusted partial result files do not imply completion of the requested timeline.

The final fields at 0.127 s, initial fields, complete native steady archive, exact before/request/final control dictionaries, active-request log, native logs, API responses and source/ELF identities are preserved. The audit verifies byte-exact dictionary changes, API/native verdict agreement, actual input/field/mesh SHA256 identities, whole nonzero steady phi seeding and finite final fields. It reuses the previously archived exact geometry/source artifacts rather than copying those again.

Read-only verification:

    python3 docs/benchmarks/shinaide-product-2026-10-05/evidence/pimple-time-boundaries/write-now-control/audit_write_now.py

This proves normal live control handling and truthful incomplete reporting for this one owned run. It does not establish transient accuracy or acceptance of the large target airflow case.
