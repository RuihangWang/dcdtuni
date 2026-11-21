# Air postprocess request revision contract

Status: isolated `codex/pp-revision-gate-20260927` branch. The selected-time
patch, backend gate, React calls, and legacy HTML calls are one review unit.
Do not merge only part of it; the gate intentionally returns HTTP 428 to an
old client that omits the revision.

## Failure reproduced

`CachedPostProcessor` is a process-wide singleton. On the selected-time
worktree, synthetic T=301 K at time 100 and T=321 K at time 200 produced this
real route sequence:

1. Tab A: `POST /api/pp/init` for `log/run`, `timeValue=100` → 200.
2. Tab B: same run, `timeValue=200` → 200.
3. Tab A: `GET /api/pp/field/T` → **200 and 321 K**.

On the current main branch, the same sequence with distinct `runA` and `runB`
also returned B's 321 K to A. The source cases and VTK arrays were synthetic;
the HTTP route handlers and cache class were real. A local AbortController or
disabled selector protects only one tab. `Cache-Control: no-store` does not
protect a process-wide in-memory cache.

## Backend wire contract

`POST /api/pp/init` keeps the existing request. After a complete load it now
adds `runId`, actual `timeValue`, and opaque `revision` to its JSON response.
`runId` is a SHA-256 digest of the canonical run path; it is a diagnostic
identity, not an authorization credential. The revision is a fresh UUID for
each completed load, including a force reload. A cache hit for the same loaded
run and requested time retains the revision. A failed replacement invalidates
the old revision. The current case-directory generation is bound too:
switching caseDir revokes the old snapshot. `runDir` is normalized under the
selected case directory, and a path resolving outside it returns 400;
explicit `.vtm` imports inside it remain valid.

Every result-data call must send `X-PP-Revision: <revision>`:

| Endpoint | Current React caller | Legacy HTML caller |
| --- | --- | --- |
| `GET /api/pp/mesh` | `postprocess/index.ts:fetchMeshMeta` | `STL_generate.html:ppInitPostProcess` |
| `GET /api/pp/mesh/binary` | `postprocess/index.ts:fetchMesh` | `STL_generate.html:ppInitPostProcess` |
| `GET /api/pp/field/{name}` | `postprocess/index.ts:fetchField` | `STL_generate.html:ppSwitchField` |
| `POST /api/pp/slice` | `postprocess/index.ts:fetchSlice` | add/edit slice fetches |
| `POST /api/pp/streamlines` | `postprocess/index.ts:fetchStreamlines` | add/edit streamline fetches |
| `POST /api/pp/volume` | `postprocess/ppVolume.ts:fetchVolume` | `_ppFetchVolumeData` |

The server returns 428 `pp_revision_required` if the header is absent and 409
`pp_revision_stale` if it no longer names the loaded snapshot. It never
automatically adopts the other tab's run. Mesh metadata includes the identity
in JSON; binary responses echo `X-PP-Revision` in an exposed response header.
Binary bodies remain unchanged. The server preflight now permits this header.

`GET /api/pp/runs`, AI status, and AI demo-model path are not frame data.
AI start/infer require the same revision. Worker startup/inference runs outside
the postprocess lock so another tab may switch; before accepting their outputs
the server rechecks run, time, case generation and revision. A worker is bound
to its starting revision. Successful field injection advances the revision,
clears field caches and returns the new identity to both clients.

## Client integration and stale-response handling

React keeps `{runDir, timeValue, runId, revision}` in one active session
reference after `initRun`. It passes `revision` to every listed fetch, validates the
response echo before decoding binary, and verify the local session is still
active after every awaited request before updating mesh, field, slice,
streamline, volume, or AI state. The React `PostprocessPanel` has all of these
call sites, including edit dialogs. Clear that session on run/time/project
change and when the user clears postprocess. On 409 or 428, clear old overlays
and ask the user to reopen the *selected* run/time; do not silently render the
new server snapshot or auto-adopt its token.

The legacy HTML stores the token in `_ppState` and uses `_ppFetchForSession`
for add/edit requests. Its `initPending`/`initGeneration` invalidates an old
load as soon as the selected run or time changes, even before the first mesh
arrives. Older external clients must re-init and send the returned token; an
implicit tokenless fallback would restore the bug.

## Lock and remaining limits

The isolated backend patch uses an instance `RLock` for load/invalidation and
each guarded request. Validation occurs only after acquiring that lock, and
the lock is held while geometry/field/slice/streamline/volume payloads are
built. This prevents a slow request from reading a mixture of two snapshots.
The current route wrapper also holds it during socket writes; a stalled
client can therefore delay another tab's init. A later immutable snapshot or
prepare-then-send response split can reduce lock hold time without weakening
the version check. Multi-process serving would need shared or sticky snapshot
ownership; the current development server is one `ThreadingHTTPServer`
process. The revision identifies the loaded in-memory snapshot, not a hash of
OpenFOAM files modified after load.

The patch does not alter source-surface, mesh-quality, or CFD acceptance gates.
The AI worker consumes a separate H5 graph and has no OpenFOAM `timeValue`
input; this gate prevents wrong-snapshot injection but does not prove its
prediction corresponds to the selected CFD time. That requires a later
time-aware AI data contract.

## CPU verification on the isolated branch

- Real route handlers with synthetic PyVista fields: A time 100 (301 K), B
  time 200 (321 K), A's old field request → 409; B's current request →
  200/321 K. Separate run and case-directory switches, in-flight volume,
  in-flight AI inference and case switch during init are also covered.
- Related backend tests: 49 passed. Full backend suite: 1625 passed,
  40 skipped, 10 pre-existing failures (legacy liquid ODIL thermal loop,
  meshgen 2, polyMesh 2, room enclosure 1, T-template 4), 3 subtests passed.
- React Vitest: 258 files / 2651 tests passed, including React's overlapping
  init/old field and executed legacy HTML functions. TypeScript production
  build passed. All four legacy inline JavaScript blocks passed `node --check`.
- This isolation run did not operate a live two-tab browser or the actual AI
  worker. It did not use a GPU. The handler/PyVista and frontend execution
  tests verify the race contract; live UI acceptance remains a separate check.
