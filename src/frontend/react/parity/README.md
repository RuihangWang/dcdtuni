# React ↔ HTML parity sweep

Clicks **every** interactive control in the React editor, records what each one
changed, and checks that both editors end up with the same `runDefaults` for the
same project. Written for the standing rule that every element and every button
in the React app must be exercised through `browser-use` and aligned with the
HTML app.

## Status (2026-10-21)

The method below is current. The checked-in results (`exercise_results.json`,
`verify_summary.json`, `sweep_2026-08-30.log`, the `*_probe.json` and label
files) are the 2026-08-30 sweep (`eb5510a29`). Both migration-gap registers
built on it are closed — see
[docs/archive/react-migration-2026-08/](../../../../docs/archive/react-migration-2026-08/react-migration-gaps-round4.md).
Since then both editors have kept changing together under the two-editor
convention (shared logic in `src/frontend/shared/js/`), so the next step is a
fresh sweep: load the same project in both apps, rerun `id_diff.py`,
`label_diff.py`, `clipped.py --expand` and `exercise.py`, and commit the new
results as a dated receipt.

The login works against the current launcher (2026-10-22, see "Logging in"
below). One thing to fix in the scripts before that rerun:

- **Project loading in React.** `react_probe.py` loads a project by overriding
  `window.prompt` and clicking the first file button. React now opens a
  file-browser dialog there, so load through `window._applyProjectData(doc,
  name)` instead — same name and signature in both editors, so one snippet
  loads the same project into either.

## Running it

Both servers up through the launcher on two free ports — the legacy editor with
`./src/start_server.sh --app html --port $HTML_PORT` and the React editor with
`./src/start_server.sh --port $REACT_PORT` (or `--dev`); the banner prints the
ports actually bound — then:

```bash
export PATH="$HOME/.local/bin:$PATH"
browser-use open http://localhost:$REACT_PORT/
python3 src/frontend/react/parity/inventory.py   # A: what controls exist, per tab
python3 src/frontend/react/parity/exercise.py    # B: click every one, restore after each
python3 src/frontend/react/parity/react_probe.py # C1: read React's runDefaults
browser-use open http://localhost:$HTML_PORT/STL_generate.html
python3 src/frontend/react/parity/html_probe.py  # C2: read the HTML editor's
python3 src/frontend/react/parity/report.py      # D: the verdict
```

### Logging in

Every probe logs the open page in itself, through one helper,
`src/frontend/scripts/admin_login.py` (`login_js`, re-exported by `drive.py`).
It takes the credentials in the launcher's order: the `DCDTUNI_ADMIN_USER` /
`DCDTUNI_ADMIN_PASSWORD` pair when both are set, else the local-mode file
`src/server/auth-local/admin_credentials` (username, password on two lines)
that a plain `./src/start_server.sh` creates, else the pair written in the
probe. Two servers started in local mode from this checkout share that file, so
nothing extra is needed. The login throws when the server refuses it, so a
probe stops at the login instead of reading 401 bodies further on.

`recheck.py`, `recheck2.py` and `finish8.py` re-test the controls the sweep
cannot judge on its own — see below.

## Two coverage checks that do not click anything

The sweep only exercises what it can find. These two say what is MISSING, and
they disagree usefully — a control can have the right id and the wrong label,
or exist with no id at all:

A control can also be present, correctly labelled AND unusable, by sitting
outside the panel that holds it — every panel here clips with `overflow:
hidden`. Neither check above can see that, and the volume-rendering section
shipped exactly that way:

```bash
python3 clipped.py --expand   # every control whose box falls outside its clipper
```

```bash
python3 id_diff.py react      # per-tab visible DOM ids, React
python3 id_diff.py html       # ... and the legacy
python3 id_diff.py report     # which ids the HTML has and React does not

python3 label_diff.py react   # the same, by visible TEXT
python3 label_diff.py html
python3 label_diff.py report
```

**Load the same project in both apps first.** An empty React page against a
loaded HTML page reports most of the app as missing, and a loaded React page
against an empty HTML one reports nothing at all — the first version of this
sweep did the latter and read as complete.

`id_diff` reports ids that are PRESENT BUT HIDDEN separately from missing ones:
React unmounts a panel it is not showing while the legacy hides it, and
counting a hidden id as absent reports a rendering strategy as a gap.

`label_diff` normalises digits out (`机柜 (RACK)4` and `…12` are the same
control) and treats a label found on ANY React tab as present, because the two
apps put some controls on different tabs and that is a layout question rather
than a missing feature.

## Four things this harness learned the hard way

**`el.focus()` / `el.blur()` dispatch NOTHING in headless.** Headless Chromium
reports `document.hasFocus() === false`, and in that state those two methods
move `activeElement` but fire no `focus`/`blur`/`focusin`/`focusout` at all.
`act('text')` used them, so React's commit-on-blur `NumberField` never wrote —
and it sent only `input`, never `change`, so the legacy editor's inline
`onchange=""` never fired either. The failure was SILENT and SYMMETRIC: both
apps read back unchanged, and the sweep scored that as agreement. Every text
control's "aligned" verdict before this fix was vacuous. `act('text')` now
dispatches `focusin` / `input` / `change` / `focusout` explicitly, which covers
React (focusin/focusout) and the legacy inline handlers (change) together.

**A structural path also starts with `#`.** The element address is the nearest
id-bearing ancestor plus a path (`#mesh-settings-panel>div.prop-group[0]>…`).
The first version short-circuited on a leading `#` and called `getElementById`
on the whole string, which returns null — so all 177 controls reported
"missing". It failed safe, but it proved nothing. `lib.js` now takes the id
shortcut only when the path *is* an id.

**Restoring project data is not restoring the app.** v1 undid edits but not UI
state, so the first collapse header hid the six file buttons behind it and every
one of them read as missing. `exercise.py` now restores per control type —
checkbox by clicking again, select/text by writing the old value back, toggle
clicks by clicking again, project edits by undoing — and reloads the page when
restoration cannot be confirmed.

**A fingerprint only sees what it samples.** The sweep watched project data,
input values and control count. It did not watch CSS classes, panel geometry or
the WebGL scene, so nine controls that only flip an `active` class, resize a
panel, or add a Three.js object all read as "no effect". `recheck.py` uses a
richer snapshot and finds every one of them working. Treat a `NO_EFFECT` from
the sweep as *unexplained*, never as *dead*.

## Reading the report

`EFFECT` counts are not the point; the `NO_EFFECT` list is. Every entry on it
needs one of these explanations, and anything left over is a real defect:

- the control was already in the state the click asks for (an active tab, the
  selected node, the current cooling mode)
- the effect is outside the fingerprint → re-test with `recheck.py`
- the probe value was invalid for the field (the residual inputs take `3e-6`,
  not `0.0001X`) → re-test with `recheck2.py`
