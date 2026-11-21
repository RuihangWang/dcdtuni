# cfdrun Naming Cleanup — Design

**Status:** approved for planning (user chose Option C on 2026-04-17)
**Scope:** full cleanup of residual `cfd_export.json` / `export_cfd` / `--from-stlgen` references across the `dcdtuni` repo and its `STLGeneratorForOpenFoam` submodule.

## Context

The 2026-04-11 refactor renamed the frontend-exported browser manifest from `cfd_export.json` to `<basename>.cfdrun` (same JSON content; new file extension + location contract). The CLI flag was renamed from `--from-stlgen` to `--from-cfdrun` and the old flag deleted. But the rename was partial: many internal variables, API payload keys, endpoint names, HTML function names, docstrings, and — critically — the `api-debug-testing.md` skill document still speak the old vocabulary.

The smoking gun: an agent (me) following `api-debug-testing.md` as the source of truth just spent a session POSTing `cfdExportJson`, reading `<case>/model/cfd_export.json`, and writing to `/tmp/cfd_export.json`. The skill doc taught these patterns; the legacy code paths accept them; nothing in the system complained. This is how legacy names propagate. Every new agent onboarding through that skill doc will repeat the mistake.

This design eliminates the mismatch atomically: frontend and backend ship together (same submodule), so we do not need compat shims across the rename. Code outside the submodule that reaches through the API (parent-repo test scripts, ad-hoc curl) is minimal and will be updated in the same PR.

## Goals

1. **Stop misleading new agents.** The skill documentation (`api-debug-testing.md`) must match the code it documents.
2. **Internal consistency.** A reader of `stlgen_runner.py` should not see `self.cfd_export_path` holding a `.cfdrun` path.
3. **Preserve rollback safety.** Code paths that intentionally keep the legacy `cfd_export.json` file around for pre-refactor rollback (and tests that verify this) must remain.
4. **No wire-protocol break for in-flight callers.** If a pinned-older frontend posts `cfdExportJson`, the server should still succeed (with a one-line deprecation warning). Same for the `/api/browser/export-cfd` endpoint.

## Non-goals

- Renaming the Pydantic schema class `CFDExport`. The schema *is* the manifest's canonical type; "CFDExport" is a reasonable name and renaming it produces large churn with no clarity gain.
- Rewriting the one-off scripts `stl_browser_debug.py` and `integrate_v5_cfd_station.py`. They are not in the live pipeline.
- Touching historical design docs under `AGENTS/specs/2026-04-09*` and `AGENTS/specs/2026-04-10*`. Those are frozen historical records.
- Reformatting the "File format is identical to the legacy cfd_export.json" user-facing rollback hints — these are deliberate.

## Architecture

Four layers to change; one layer intentionally untouched.

### Layer 1: Skill documentation (highest priority)

**Files:**
- `STLGeneratorForOpenFoam/.claude/skills/api-debug-testing.md` — rewrite end-to-end
- `AGENTS.md:876,880` — update the `--from-stlgen` checklist items to the current state
- `server.py:754` — fix the docstring comment referencing `--from-stlgen`

**Target vocabulary:**
- "export the `.cfdrun` manifest" instead of "export `cfd_export.json`"
- `/api/browser/export-cfdrun` as the primary endpoint name; keep `/api/browser/export-cfd` as a silent alias
- `cfdrunJson` as the primary POST body key; server accepts both `cfdrunJson` and the old `cfdExportJson` (deprecation warning logged)
- `--from-cfdrun <path>` as the only supported CLI flag
- File location: `<case>/model/<basename>.cfdrun` (glob, not a fixed filename)

### Layer 2: Backend wire / internal naming

**`server.py`:**
- New route `/api/browser/export-cfdrun` → forwards to the same handler as `/api/browser/export-cfd`. Keep the old route as an alias for one release.
- Browser worker function `export_cfd_json` → rename to `export_cfdrun_content` internally; old function-name string accepted as an alias for `_browser_call()` dispatch.
- `handle_prepare_case` and `handle_run_mesh`: read `cfdrunJson` first, fall back to `cfdExportJson` with a `warnings.warn`/print deprecation log line. No behavioral change when the old key is supplied.

**`stlgen_runner.py`:**
- `STLGenRunner.__init__(cfd_export_path=...)` → rename parameter to `cfdrun_path` (positional-compatible; the only caller in-repo is `run_cfd.py` which already uses the positional form).
- Instance attribute `self.cfd_export_path` → rename to `self.cfdrun_path`.
- The default-fallback lookup `model_dir / "cfd_export.json"` becomes a glob: pick the single `*.cfdrun` in `model_dir`. If none exist, **raise `FileNotFoundError`** with a message pointing the user at "re-export from the UI or pass `--from-cfdrun` explicitly" — **no silent legacy-file fallback**. The previously-planned "fall through to `cfd_export.json` if present" was rejected on 2026-04-18 per the user's global no-silent-fallback stance (see the fan_outlet temperature-rise BC fix in commit 7ae60e4 for the same philosophy applied to field generation).
- Rolled-back-checkout protection is moved to the test layer: `test_cleanup_preserves_cfd_export.py` still asserts that the runner does not delete a pre-existing `cfd_export.json`, so a rolled-back pre-refactor `main` checkout can still read it. But the NEW runner will not USE that file as input — the user must explicitly pass `--from-stlgen <path>` on the old main branch, or re-export as `.cfdrun` on the current branch. This makes the rollback path deliberate rather than implicit.
- Docstrings throughout: replace "parses cfd_export.json" → "parses the `.cfdrun` manifest (JSON content identical to the legacy `cfd_export.json`)".

**`cfdproj_schema.py`, `bridge_bc.py`, `liquid_runner.py`, `fields.py`:**
- Docstring updates only. Content format reference remains "legacy `cfd_export.json`" where it aids historical context; active description uses `.cfdrun`.

### Layer 3: Frontend wire / internal naming

**`STL_generate.html`:**
- `buildCfdExportJson()` → rename to `buildCfdrunJson()`. Add `window.buildCfdExportJson = buildCfdrunJson;` as a one-line alias (so old bookmarklets/scripts calling it still work; removed in a later release).
- Local variable `cfdExportJson` at the two POST sites (lines ~26907, ~27124) → rename to `cfdrunJson`.
- POST body key `cfdExportJson: cfdExportJson` → change to `cfdrunJson: cfdrunJson`.
- `/api/browser/export-cfd` calls in headless/debug helpers (if any) → repoint to `/api/browser/export-cfdrun`.

### Layer 4: Tests and historical rollback (intentionally untouched)

- `test_cleanup_preserves_cfd_export.py` — name and contents stay. This is the rollback-safety test.
- `stlgen_runner.py:816-820` NOTE about deliberately not deleting the legacy file — stays.
- Historical spec docs under `AGENTS/specs/2026-04-0*` — frozen.
- Pydantic schema class name `CFDExport` — stays (see non-goals).

## Data flow

```
┌─────────────────────────────┐
│ HTML: buildCfdrunJson()     │  (was: buildCfdExportJson)
└────────┬────────────────────┘
         │ POST /api/run-mesh
         │   body: { cfdrunJson: <str>, cfdrunFilename, stlFiles, ... }
         ▼
┌─────────────────────────────┐
│ server.py: handle_run_mesh  │
│   reads data["cfdrunJson"]  │  (falls back to data["cfdExportJson"])
└────────┬────────────────────┘
         │
         ▼
┌─────────────────────────────┐
│ _write_active_cfdrun        │  (unchanged — writes <basename>.cfdrun)
│   → <case>/model/*.cfdrun   │
└────────┬────────────────────┘
         │
         ▼
┌─────────────────────────────┐
│ python -m dcdtuni.runner.   │
│   run_cfd --from-cfdrun <p> │  (unchanged — already correct)
└────────┬────────────────────┘
         ▼
┌─────────────────────────────┐
│ STLGenRunner(case, p)       │
│   self.cfdrun_path = p      │  (was: self.cfd_export_path)
└─────────────────────────────┘
```

## Backward compatibility

**In-flight API callers:** server accepts both `cfdrunJson` and `cfdExportJson` in POST body; both `/api/browser/export-cfd` and `/api/browser/export-cfdrun` routes work. Old key paths log one-line deprecation warnings (not errors).

**Rolled-back checkouts:** if a user rolls back to pre-refactor `main`, their case still has `cfd_export.json` in `model/` (we never delete it), and the old runner's `--from-stlgen` path still reads it. The existing `test_cleanup_preserves_cfd_export.py` guards this contract.

**Legacy file discovery:** `STLGenRunner.__init__` without `cfdrun_path` now globs `model/*.cfdrun`; if nothing matches AND a `cfd_export.json` exists, it uses that as a last-resort fallback. Logs a deprecation hint. This handles "case directory from 6 months ago" without requiring user action.

**Deprecation window:** one release cycle. Aliases (the route, the body key, the window function) are marked for removal in the next design pass.

## Testing

- **Frontend structural test** (`test_refinement_redesign_frontend.py`): add assertion that `buildCfdrunJson` exists and `buildCfdExportJson` is a one-line alias.
- **Backend wire test** (new): two tests for `handle_run_mesh` — one posts `cfdrunJson`, one posts legacy `cfdExportJson`. Both must succeed; second must log a deprecation.
- **Runner loader test**: patch `STLGenRunner` with only `cfd_export.json` on disk (no `.cfdrun`); confirm it loads the legacy file with a deprecation log.
- **End-to-end headless**: existing `/api/browser/export-cfd` (alias) and new `/api/browser/export-cfdrun` both return the same JSON structure; headless browser `buildCfdrunJson()` returns the expected shape.
- **Existing test suite**: all 40 refinement-redesign frontend tests + 82 runner tests stay green. `test_cleanup_preserves_cfd_export.py` untouched.

## Rollout risk

**Low-risk changes:**
- Skill doc rewrite, AGENTS.md edits, docstring fixes — documentation only.
- `STLGenRunner.cfd_export_path` → `cfdrun_path`: only one internal caller (`run_cfd.py`), which uses positional form.

**Medium-risk changes:**
- Frontend POST key rename: breaks if the server bumps without the submodule bumping. Mitigation: server accepts both keys during the deprecation window; shipping-order is "server-alias first, then frontend" which is trivially safe.
- Endpoint alias addition: risk of routing conflict. Mitigation: alias registered explicitly, tested in CI.

**Out-of-scope risk acknowledged:**
- Post-deprecation removal of aliases is a SEPARATE change not covered by this spec.

## Implementation order

1. Skill doc + AGENTS.md + stale docstring comments (Layer 1; documentation — no runtime effect).
2. Server dual-key acceptance + endpoint alias (Layer 2; server-only; deployable before frontend).
3. Frontend rename + parent-repo consumer updates (Layer 3; ships as submodule bump).
4. Runner internal rename (`cfd_export_path` → `cfdrun_path`) + docstrings (Layer 2; low-risk, isolated).
5. Add tests covering the dual-key server behavior and the fallback loader.

Each step is independently shippable; order preserves "server accepts old first, then frontend sends new".
