# Runner SIGTERM idempotence

Actual normal-UI mesh cancellations dcb23b08 and 7f1fc62f stopped all eight MPI ranks and unlocked the UI, but their original SSE contained SystemExit 143 tracebacks raised while logging or threading was shutting down. Both original SSE files are retained byte for byte here.

The stop supervisor signals each owned group and then the launcher process. The runner previously executed its SIGTERM cleanup and sys.exit on every delivery. The changed runner fences queued Python callbacks with a closure flag and ignores further SIGTERM deliveries immediately on the first callback, before logging or active-step cleanup. The active-step cleanup and successful cancellation exit 143 remain; supervisor SIGKILL escalation and job cleanup deadlines are unchanged. Normal simulation exceptions and active-step cleanup exceptions are not swallowed.

The focused nine tests failed eight cases and passed one against the original source. The changed source passes all nine plus seven existing CLI and case-lock tests (16 passed). Three subprocess tests deliver real SIGTERM during child cleanup, atexit, and threading shutdown and require the stage to complete, return code 143, and no ignored traceback. These controlled subprocesses contain no CFD/native solver and do not replace the pending actual eight-rank browser validation.

Only run_cfd.py and the focused test file change. No server, process-tree policy, UI, native mesh, service, GPU, or index mutation is part of this patch.
