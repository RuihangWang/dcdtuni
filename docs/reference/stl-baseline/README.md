# STL export baselines

`Base_leakage.json` is the fingerprint of what the legacy editor exports for
`case/Base_leakage`: per file, the sha256 of the STL text, its byte count, its
facet count, and the solid names inside it.

It exists because STL export is **not** a function of the project file. It reads
`renderMeshes` — the Three.js meshes the scene actually built, each tagged with
`userData.exportName` — so it is a function of the rendered geometry. Any change
to the geometry builder, and any move of it toward React, has to be checked
against this rather than by looking at the result.

A near-match is not a match. A flipped normal, a dropped degenerate triangle, or
one fewer decimal place all still produce a valid STL that renders correctly and
an OpenFOAM patch that is silently wrong.

**Status (as of 2026-10-21):** `Base_leakage.json` was captured on 2026-08-27
from the legacy editor. Since then `case/Base_leakage/Base_leakage.cfdproj` and
the shared geometry builders (`src/frontend/shared/js/geometry*.js`,
`rack_state.js`) have changed, and the baseline has not been re-captured, so a
mismatch today does not by itself mean a regression. Next step: re-capture
Base_leakage from both editors (`--app html` and the default React app),
compare the two captures with each other, and replace this baseline with the
reviewed result.

## Re-checking

Start a server through the launcher, open the editor in the browser-use
browser, then capture and compare:

```bash
./src/start_server.sh --app html --port $PORT        # legacy page at /STL_generate.html
browser-use open http://localhost:$PORT/STL_generate.html
python3 src/frontend/react/parity/stl_oracle.py capture case/Base_leakage _tmp/stl_now
python3 src/frontend/react/parity/stl_oracle.py compare docs/reference/stl-baseline _tmp/stl_now
```

For the React editor use the default `./src/start_server.sh --port $PORT` and
open `http://localhost:$PORT/`. `stl_oracle.py` logs the page in with the
launcher's credentials: the `DCDTUNI_ADMIN_USER` / `DCDTUNI_ADMIN_PASSWORD`
pair when both are set, else the local-mode file
`src/server/auth-local/admin_credentials` that a plain `./src/start_server.sh`
creates (helper: `src/frontend/scripts/admin_login.py`), so a server started
either way needs nothing extra. A refused login stops the capture there.

`compare` reads `_index.json` from each side, so pointing it at this directory
works without keeping the STL text in the repo.

## Two ways this check has already been fooled

**Loading a project does not build geometry.** `_applyProjectData` leaves the
scene empty; `rebuildGeometry()` has to be called. Without it the export
succeeds and returns every group empty — 18 files, 0 facets — and that baseline
would match *anything*. `stl_oracle.py` now calls the rebuild and refuses to
write a zero-facet capture.

**`browser-use eval` returns one line.** STL is multi-line, so reading it back
gave just the `solid <name>` header: 26 bytes, 0 facets, which looks exactly
like a scene with no geometry rather than like a broken transport. The values
now cross as JSON.
