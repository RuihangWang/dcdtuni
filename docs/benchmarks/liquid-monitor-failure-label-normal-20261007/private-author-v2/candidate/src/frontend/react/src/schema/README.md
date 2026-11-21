# schema/

The two document formats this editor reads and writes, typed.

| file | what it describes | the authority it must match |
|---|---|---|
| `cfdproj.ts` | the `.cfdproj` V4 project document | `src/frontend/shared/js/cfdproj_schema.js` |
| `cfdrun.ts` | the `.cfdrun` V2 run manifest | `src/frontend/shared/js/cfdrun_schema.js`, and `src/backend/aircooling/runner/cfdrun_schema.py` for what the runner will accept |

**These files are not the authority.** The formats are defined by the shared JS
modules (which both editors load) and, for a run, by the Python runner that
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
`liquidFlowNetworks`, which three saved projects already use, and it reads real
`case/*/*.cfdproj` and `case/*/model/*.cfdrun` files rather than fixtures — a
type that only matches a file written for the test proves nothing.

## Two traps

**`raisedFloor` and `falseCeiling` are not buckets.** `emptyConstructions()`
seeds them as `null`, not `{}` (js/cfdproj_schema.js:63), because a room has at
most one of each. They are in `SINGLETON_SLOTS`, and a loop over `BUCKETS` must
not reach into them expecting a uid-keyed dict.

**`meshGeometry` is not parity with the root config.** The manifest carries a
transitional v1 blob that the runner still reads geometry through, and it is a
DERIVED shape: nested `gravity {x,y,z}` where the root has flat `gravityX/Y/Z`,
computed `blockMeshBounds`, its own `roomTemperature`. Anything reading it must
say which of the two it means. See `docs/reference/cfdrun-v2-schema.md` §14.
