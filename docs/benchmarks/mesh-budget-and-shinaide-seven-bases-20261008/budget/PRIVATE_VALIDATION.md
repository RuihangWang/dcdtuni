# Mesh budget candidate (PRIVATE, not deployed)

This candidate changes 5 production paths plus 2 test paths. The normal baseline is
67ada7ad86ffa52a9505ae1b3bb6bde88ec16d5b, as supplied by Root. All 7 before guards
and all 423 non-target source-closure members were compared to current normal bytes.

An immutable monotonic origin/deadline now travels from server request admission
through manifest/STL publication, stdin transport, CLI, preparation, native/checkMesh
subprocesses and receipt publication. Configurations tighten the original deadline;
engine admission cannot restart it. Native/checkMesh receive the exact remaining
float interval, including fractions below one second. Before solve retirement the
same budget is checked; failed quality and expired budget remain failures.

The CLI watchdog uses the calling process's POSIX alarm and restores its handler.
Captured serial and MPI-command branches use owned session subprocesses and existing
group cleanup. This does not prove all adversarial MPI descendants are reaped:
the existing termination helper returns when the group leader exits. The controls
prove termination for their real owned responsive descendants, not arbitrary MPI.

Receipt ownership is explicit. Before this execution owns the OS case lock, timeout
only clears instance memory. It cannot acquire a fresh lock and delete another
receipt. While the execution lock is owned, expiry invalidates its own receipt.
After CLI completion, a late server timeout sets this job's rc=124 and quality=false;
it does not unlink disk state that may belong to a later lock owner. An existing
receipt is neither borrowed as this rejected job's authority nor globally revoked.

Actual evidence:
* original normal defects: 4 RED, RC1/2.17567s; same controls after repair 4 PASS.
* first candidate suite: 95 PASS/7 RED (one invalid schema fixture, six obsolete
  subprocess.run launch mocks); errors and original assertions are preserved.
* corrected initial suite: 102 PASS, then foreign-lock addition: 103 PASS.
* tighter ownership review: 3 RED/1 PASS, RC1/4.83180s. Real late server worker and
  new fcntl owner show both API workers deleting a replacement's receipt; an
  expired unlocked admission deletes a prior receipt. Locked admission preserved.
* ownership repair: 6 PASS/2.62460s, including two real locked own-receipt failures.
* final six-file suite: 106 PASS, RC0/14.00110s (pytest13.50s), CPU36–37/CUDA empty.
  Real serial/MPI-command branch controls, child PID/start/UID/PG/SID witnesses,
  blocked CLI stdin, publisher rollback and case-lock controls are retained.
  No native mesh, physical checker, solver, GPU or browser was run.

Limits: server body reads, filesystem copy/hash and Python work have cooperative
before/after checks, not hard preemption inside every synchronous operation.
The React export/admission portion is a PLAN only. Existing normal UI exports before
job_id; this candidate does not establish click-to-complete <=300s, accepted mesh
quality, convergence, all-case performance, or production cancellation E2E.
CLI/process cleanup may use its existing grace beyond the physical work deadline;
no further physical work receives a new budget. The separate manual checkMesh API
is an independent operation and has not been converted into mesh admission.

SOURCE_CLOSURE.json binds the actual by-value CPU-tested tree (430 members).
candidate.patch and TARGETS.json bind exact 7 target before/after bytes. Historical
raw receipts are immutable. The ownership RED source snapshots preserve the two
production files; the test's three failing assertions remain in the final file,
with only owning-lock contexts added to the two separate invalidation controls.
RUN_REPRO.py copies the frozen closure to a new B-owned output and replays only
the small CPU suite. It neither edits normal files nor invokes CFD binaries.
