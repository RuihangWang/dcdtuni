# React editor

The dcdtuni editor, and since 2026-10-04 the launcher's default app. It shares
the `.cfdproj` format modules, the i18n dictionaries and the geometry builders
with the legacy single-file editor (`src/frontend/web/STL_generate.html`); they
live once in `src/frontend/shared/` and are reached through `src/legacy/`.

## Running it

**Always through the launcher**, never a bare `npm run dev` — the launcher binds
the listeners, starts or reuses the backend, points the Vite proxy at it and
refuses ports that belong to someone else:

```bash
./src/start_server.sh                      # production: react/dist + /api on ONE port (default 8000)
./src/start_server.sh --build              # rebuild dist first; do this after every pull or source edit
./src/start_server.sh --port 8010          # the port you open; WebSocket = port + 1
./src/start_server.sh --dev                # Vite + HMR on --port, backend on --port + 2
./src/start_server.sh --dev --api-port 8005   # Vite only, reusing a backend already on 8005
./src/start_server.sh --ov --gpu 0         # also start the Omniverse display bridge on one GPU
```

`--api-port` is only meaningful with `--dev`: in production the backend *is*
the `--port` server, and the launcher rejects the flag there. Login uses the
launcher's local-mode file `src/server/auth-local/admin_credentials` unless you
pass `DCDTUNI_ADMIN_USER` / `DCDTUNI_ADMIN_PASSWORD`.

## Layout

```
src/
  App.tsx  main.tsx  projectStore.ts  i18n.ts      the app shell and the store
                                                   (projectStore.edit() takes the
                                                   undo checkpoint itself)
  automation/    window.__dcdtuni, the automation contract scripts and agents drive
  components/    every React panel and inspector
  domain/        render-free domain objects: entity registry, pose/geometry/material kernels
  geometry/      models, faces, patches, racks, seeds — what a .cfdproj describes
  headless/      projectCompiler: the export chain without a browser (agent.scripted_api uses it)
  interaction/   picking, selection, the move/rotate/translate gizmos, the view cube
  io/            load, save, STL and .cfdrun export, the zip package, the file browser
  liquid/        liquid-cooling authoring, the dynamic graph, monitoring and results
  postprocess/   fields, slices, streamlines, volume, colormaps, the result-revision gate
  renderer/      the scene snapshot, Three.js and Omniverse renderers, the OV session
  room2d/        the 2D floor-plan designer (also published as window.room2d)
  run/           mesh and solve: engine and config, pre-flight, job streaming, monitors
  schema/        the typed views of .cfdproj / .cfdrun / .liquidrun (see schema/README.md)
  transient/     keyframes, the timeline and result playback (cfdproj v5)
  ui/            layout metrics, resize handles, render order
  legacy/        typed accessors for the shared web/js modules (window.* UMD globals)
test/            every *.test.ts(x), mirroring src/, plus fixtures
parity/          the React-vs-legacy harness (see parity/README.md)
```

Rules the layout depends on:

- **Nothing imports across a boundary by reaching into a sibling's internals.**
  A panel imports `../geometry/rackSlots`, not a path inside another component.
- **Scripts reach the editor only through `window.__dcdtuni`** (and
  `window.room2d` for the 2D editor). Everything in the contract goes through
  `projectStore.edit`, so a scripted edit gets the same undo checkpoint as a
  clicked one. Do not drive the editor by writing into state.
- **Shared UMD modules attach to `window`.** Prefer the typed accessors in
  `legacy/`, which fail in one place with a clear message when a script is
  missing. Some panels still read shared modules and viewport handles
  (`__viewportRender`, `__sceneBuilder`) from `window` directly; new code should
  use the accessors or the viewport registries instead of adding globals.

## Tests

```bash
cd src/frontend/react
npx vitest run          # the whole suite; npx vitest run test/<file> for one file
npx tsc --noEmit
```

Tests live in `test/`, mirroring `src/`. Pure logic is unit-tested; anything
that needs a real browser (WebGL, drags, the server, the Omniverse stream) is
verified in a browser through the `browser-verify` skill and, against the legacy
editor, through `parity/`. The suite size changes weekly, so count it with
`npx vitest run` rather than trusting a number in a document.
