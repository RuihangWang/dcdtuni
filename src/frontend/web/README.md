# The legacy HTML editor — kept and runnable

`STL_generate.html` and its 2D editor, from before the React cutover. It is
**not** archived-and-dead: `--app html` serves it, and it is kept deliberately.
The launcher's default app is the React editor; name the legacy one explicitly:

```bash
./src/start_server.sh --app html --port 8060
# then open http://localhost:8060/STL_generate.html   ( / redirects there )
```

One port serves both the page and `/api` — there is no build step and no Vite,
so it starts in about a second. Login works the same as for React: the
launcher's local-mode file `src/server/auth-local/admin_credentials`, or the
`DCDTUNI_ADMIN_USER` / `DCDTUNI_ADMIN_PASSWORD` pair.

## Why it is kept

It is the only RUNNING reference for behaviour the React port may not have
reproduced. Both migration-gap registers are closed
([docs/archive/react-migration-2026-08/](../../../docs/archive/react-migration-2026-08/react-migration-gaps-round4.md)),
and the next step is a fresh run of the parity sweep in
[`react/parity/`](../react/parity/README.md), whose checked-in results date from
2026-08-30. Every earlier round found things that were only visible by driving
the real app.

## How it changes now (two-editor convention, 2026-09-17/18)

The legacy page gets no independent innovation, but a feature may land in BOTH
editors in step. What decides it is where the logic lives:

- model, geometry, conflict and migration logic goes into
  `src/frontend/shared/js/` **once**;
- each editor carries only a thin UI layer over it — here, panel HTML and
  `window.*` handlers; in React, components.

Stacked servers, absolute server-patch inputs and camera-adaptive helper sizing
were all done this way. The coding rules for this file (undo checkpoint first,
tab-scoped helpers, `window.t = t`) are in `AGENTS.md`.

## Where its files come from

Only the page, its 2D editor and its theme live here. Everything else it loads
is in `src/frontend/shared/` — `js/` (the `.cfdproj` format modules and the
geometry builders), `i18n/`, `vendor/`, `textures/` — because both editors
share those. `server.py` resolves the split in `translate_path`.

| URL | from |
|---|---|
| `/STL_generate.html`, `/room_2d_editor.js`, `/benchmark.js` | `src/frontend/web/` |
| `/theme/*` | `src/frontend/web/theme/` |
| `/js/*`, `/i18n/*`, `/vendor/*`, `/textures/*` | `src/frontend/shared/` |

## What replaced each part in React

| legacy | now |
|---|---|
| the app | `src/frontend/react/` |
| driving it from a script | `window.__dcdtuni`, the automation contract (`react/src/automation/contract.ts`); `window.room2d` exists in both |
| its 26 source-scanning Python tests | React's vitest suite (behaviour) + `react/parity/stl_oracle.py` (STL, byte for byte) |
| `theme/base.css` + `cyberpunk-dark.css` | values carried into `react/src/styles.css` |

Its own 29 Python tests were removed with the cutover (`ebed328e`); the tag
`legacy-html-editor-last-working` still has them. The page is guarded today by
the `test_html_*.mjs` node tests in [`src/frontend/test/`](../test/README.md)
(for example `test_html_v5_guard.mjs`, which keeps it from silently opening a
schema-5 project it cannot represent).
