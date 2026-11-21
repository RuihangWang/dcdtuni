# schema/

The document formats this editor reads and writes, typed.

| file | what it describes | the authority it must match |
|---|---|---|
| `cfdproj.ts` | the `.cfdproj` project document. Typed on the V4 shape; the editor saves schema 5, which adds the `transient` block of keyframe tracks (see `src/frontend/shared/js/cfdproj_transient.js`) | `src/frontend/shared/js/cfdproj_schema.js` (accepts 4 and 5) |
| `cfdrun.ts` | the `.cfdrun` run manifest. Typed on the V2 shape; `legacy/cfdrunBuild.ts` now writes `schemaVersion: 4`, whose root `simulationConfig` is the only one | `src/frontend/shared/js/cfdrun_schema.js`, and `src/backend/aircooling/runner/cfdrun_schema.py` for what the runner accepts (2, 3 and 4) |
| `liquidrun.ts` | the derived v2 `.liquidrun` execution manifest | the JSON Schema distributed by `src/backend/liquidcooling/liquidrun_schema.py` |
| `liquidrunDAE.ts` | the derived Newton-DAE `.liquidrun` (v3 and later) and the saved DAE experiment inputs | the backend's DAE schema; Python stays authoritative for graph and residual admission |
| `manufacturedBenchmark.ts` | the optional `manufactured_benchmark` block of a DAE `.liquidrun` (manufactured-solution verification; synthetic research data) | the generated strict schema plus the graph binding checked on the actual compile; the backend also verifies the canonical SHA |

**These files are not the authority.** The formats are defined by the shared JS
modules (which both editors load) and, for a run, by the Python side that
validates the manifest. What lives here is the TypeScript view of them: types the
React app can be checked against, plus typed accessors to the shared modules so
there is exactly one implementation of "build an empty manifest" and "is this
manifest sound".

## The rule

Every value that also exists in the shared modules is **imported or asserted
equal**, never re-typed and left alone:

- `cfdrun.ts` gets `emptyManifest` / `validateManifest` from `window.cfdrunSchema`.
- `BcType` and `PatchRole` are hand-written unions — TypeScript cannot derive a
  union from a runtime `Set` — so `test/schema/cfdrun.test.ts` asserts the union
  and the Set are the same set, and fails if either side gains an entry alone.
- `BUCKETS` is asserted equal to the format module's `BUCKETS` the same way.

That test is not ceremony. On its first run it found `BUCKETS` missing
`liquidFlowNetworks`, which three saved projects already used, and it reads real
`case/*/*.cfdproj` and `case/*/model/*.cfdrun` files rather than fixtures — a
type that only matches a file written for the test proves nothing.

## Two traps

**`raisedFloor` and `falseCeiling` are not buckets.** `emptyConstructions()` in
`js/cfdproj_schema.js` seeds them as `null`, not `{}`, because a room has at
most one of each. They are in `SINGLETON_SLOTS`, and a loop over `BUCKETS` must
not reach into them expecting a uid-keyed dict.

**`meshGeometry` is not parity with the root config.** The manifest still
carries a transitional blob that the runner reads geometry and `meshConfig`
through, and it is a DERIVED shape: nested `gravity {x,y,z}` where the root has
flat `gravityX/Y/Z`, computed `blockMeshBounds`, its own room temperature. From
schema 4 on, the solver settings come only from the root `simulationConfig`
(the backend derives the execution blob from it); the geometry part of the blob
is still to be migrated. Anything reading the blob must say which of the two it
means. See `docs/reference/cfdrun-v2-schema.md` §14.
