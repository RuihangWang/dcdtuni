# Liquid Cooling Codex Findings — Design Spec

**Date:** 2026-04-10
**Status:** Approved, ready for implementation plan
**Scope:** Close the four trust gaps Codex's adversarial review flagged in the liquid cooling stack.

---

## 1. Goal

Close the four liquid-cooling correctness gaps Codex flagged so that (a) the DiffLiquidTwin reference layout is parsed with correct elevations, (b) the CFD runner cannot silently run as air-cooled when the user asked for liquid, (c) the transient solver is mathematically consistent with the steady-state solver, and (d) auto-topology mode cannot silently ignore advanced CDU hardware settings. The deliverable is a committed PNG line chart at `docs/figures/diffliquid_comparison.png` showing our 600-second transient converging to DiffLiquidTwin's steady-state chip temperatures, plus a CI regression test that asserts the convergence.

## 2. Theme

**Close the liquid cooling trust gaps.** Each fix is independently testable and maps to exactly one Codex finding. They share the same test fixture (`Ref_code/DiffLiquidTwin/liquid_dc_two_racks.json`) and the same end-to-end validation (the 600s comparison chart), but they don't depend on each other at runtime. Single implementation session, single plan.

## 3. Architecture

```
Fix 1: DLT parser — height_unit support          →  fixes flattened hydraulic network
Fix 2: Runner gate — hard fail when results      →  fixes silent air-cooled fallback
       missing or don't cover all liquid servers
Fix 3: Transient coupling — back-solve UA_cp /   →  makes transient consistent with
       h_air from steady-state solution              steady state, t→∞ lands on DLT
Fix 4: Topology — reject advanced CDU fields      →  fixes silent ignoring of
       in auto-mode                                   pump/HX/valve config
─────────────────────────────────────────────────────────────────────────────
Deliverable: tests/test_liquid/test_diffliquid_comparison.py
             Runs our transient vs DLT steady-state on two_racks config,
             plus scripts/plot_diffliquid_comparison.py that saves
             docs/figures/diffliquid_comparison.png.
```

Four small backend patches (each under ~50 lines changed) + one new test file + one new plot script + one committed PNG. No cross-cutting refactor. No new runtime dependencies — `matplotlib` is already in the `dcdtuni` conda env.

## 4. Key Decisions (locked in during brainstorming)

| # | Decision | Chosen | Rejected alternatives |
|---|----------|--------|----------------------|
| 1 | What does "compare against DLT" mean? | **Steady-state endpoint comparison.** Run DLT's steady-state solver on the two-rack layout, use those chip temperatures as horizontal reference lines. Our transient runs for 600s and must converge to them. | (B) wrap DLT in a transient shell (overambitious, modifies ref code); (C) structure-only comparison (doesn't answer Codex) |
| 2 | How strict is the runner gate? | **Fail hard.** Missing/unreadable/incomplete `liquid_results.json` raises `RuntimeError` with an actionable message pointing at `POST /api/liquid/solve`. | (B) auto-solve inside the runner (blurs responsibility); (C) fail hard + frontend precheck (polish, defer) |
| 3 | How is the transient coupled to steady state? | **Back-solve `UA_cp` and `h_air_eff` from the steady-state snapshot.** By construction, the transient lands on the DLT steady-state temperature as t → ∞. | (B) parameterized RC config (reinvents option A with extra fields); (C) per-timestep implicit coupling (too slow, overkill) |
| 4 | How is the auto-topology advanced-fields bug handled? | **Reject configs that set non-default advanced fields in auto-mode**, with an error telling the user to switch to manual topology. | (B) wire advanced components into auto-mode graph (multi-day rewrite); (C) warn-only (Codex already said warning is insufficient) |

## 5. Fixes

### 5.1 Fix 1 — DLT parser `height_unit` support

**File:** `src/backend/liquidcooling/diffliquid_parser.py` around lines 70-90.

**Current bug:** `_parse_height` handles `"height": 1.2` (scalar meters) and `{"position_u": 14}` (dict form). It does not handle `"height_unit": 14` (scalar integer U-count), which is the shape actually used in `Ref_code/DiffLiquidTwin/liquid_dc_two_racks.json` (20 occurrences across rack manifold and server ports). Those ports silently fall back to `0.0`, flattening the hydraulic network and invalidating any comparison to DLT.

**Fix:**

```python
def _parse_height(port: dict, u_pitch: float) -> float:
    if "height" in port:
        return float(port["height"])
    if "height_unit" in port:                             # NEW branch
        return float(port["height_unit"]) * u_pitch
    h = port.get("position_u")
    if isinstance(h, dict) and "position_u" in h:
        return float(h["position_u"]) * u_pitch
    return 0.0
```

**Test:** `tests/test_liquid/test_diffliquid_parser.py` — add `test_height_unit_parsed_from_two_racks_json` that loads `Ref_code/DiffLiquidTwin/liquid_dc_two_racks.json`, calls `load_diffliquid_layout()`, and asserts:
1. At least one rack manifold port has `height != 0.0` and equals its `height_unit` × 0.04445.
2. Supply-side server ports sit physically higher than the corresponding manifold ports (sanity check that elevations propagate end-to-end).

**Why it matters:** Fix 3's transient depends on the steady-state solution, which depends on the topology, which depends on the parser. Wrong elevations → wrong flow split → wrong chip temperatures → meaningless reference line on the chart.

### 5.2 Fix 2 — Runner hard-fail gate

**File:** `src/backend/aircooling/runner/stlgen_runner.py` lines 1802-1828.

**Current bug:** When `inheritLiquidResults: true`, the runner tries to load `model/liquid_results.json`. Missing, unreadable, or incomplete files trigger a `logger.warning(...)` and the run continues using each server's original `power_W` — a simulation that claims liquid cooling but silently runs as air-cooled.

**Fix:**

```python
# In _apply_liquid_results (or wherever inheritLiquidResults is read):
if self._data.get("inheritLiquidResults", False):
    results_path = self.model_dir / "liquid_results.json"

    if not results_path.exists():
        raise RuntimeError(
            f"inheritLiquidResults=true but {results_path} does not exist. "
            "Run POST /api/liquid/solve before running the CFD pipeline, "
            "or uncheck 'Inherit Liquid Cooling Results' in the solver panel."
        )

    try:
        with open(results_path) as f:
            liquid_results = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        raise RuntimeError(
            f"Failed to read {results_path}: {e}. "
            "The liquid results file may be corrupted — re-run POST /api/liquid/solve."
        ) from e

    expected_server_ids = self._collect_liquid_cooled_server_ids()
    got_server_ids = set(liquid_results.get("servers", {}).keys())
    missing = expected_server_ids - got_server_ids
    if missing:
        raise RuntimeError(
            f"liquid_results.json is incomplete — missing servers: {sorted(missing)}. "
            "Re-run POST /api/liquid/solve to regenerate."
        )

    # (existing residual_heat_to_air_W application continues)
```

**New helper:** `_collect_liquid_cooled_server_ids(self) -> set[str]` scans `self._data["models"]` for servers that have a `heat_source` child model (the Phase 1-8 liquid-cooling signal) and returns their IDs.

**Tests:** `tests/test_liquid/test_runner_gate.py`, four cases:
1. `test_raises_when_file_missing` — flag true, file absent, `RuntimeError` mentions "does not exist".
2. `test_raises_when_file_corrupted` — file present but contains garbage bytes, `RuntimeError` mentions "Failed to read".
3. `test_raises_when_results_incomplete` — results file missing one liquid-cooled server ID, `RuntimeError` mentions "missing servers".
4. `test_happy_path_with_complete_results` — valid file with all servers, runner succeeds and applies `residual_heat_to_air_W`.

**Why it matters:** This is Codex's highest-severity finding — a silent correctness bug in the production runner. Independent of the chart.

### 5.3 Fix 3 — Transient coupling to steady-state

**Files:** `src/backend/liquidcooling/solver/transient.py` (main rewrite), plus a minor factory that reads from a solved `SteadyResult`.

**Current bug:** `simulate_transient()` hardcodes `UA_cp = min(m_dot·Cp·0.8, 50.0)` and `h_air_eff = 8.0`. It never reads `ServerThermalConfig.eta` despite the struct carrying that field. The resulting transient curve has no mathematical relationship to the steady-state solver's answer — any approach to DLT's steady-state temperature is coincidental.

**Physics of the fix:**

At steady state, per-server energy balance gives us both conductances in closed form:
```
TDP          = Q_coolant_ss + Q_air_ss
Q_coolant_ss = eta_ss · m_dot_ss · Cp · (T_chip_ss − T_supply_ss)
Q_air_ss     = h_air_eff     · (T_chip_ss − T_ambient)
```

Solving for the unknowns:
```
UA_cp     = eta_ss · m_dot_ss · Cp                              ← replaces hardcoded cap
h_air_eff = (1 − eta_ss) · TDP / (T_chip_ss − T_ambient)        ← replaces hardcoded 8.0
```

Then the transient ODE:
```
C_chip · dT/dt = TDP − UA_cp·(T(t) − T_supply) − h_air_eff·(T(t) − T_ambient)
```

At `t → ∞`, `dT/dt = 0`, which by algebra gives back exactly `T_chip_ss`. The curve lands on the DLT reference line by construction. The approach time is set by `C_chip / (UA_cp + h_air_eff)` — a physically meaningful thermal time constant.

**Schema change:** Extend `ServerThermalConfig` to carry the steady-state snapshot:

```python
@dataclass
class ServerThermalConfig:
    server_id: str
    chip_tdp_W: float
    thermal_mass_J_K: float = 60.0
    cold_plate_m_dot_kg_s: float = 0.01
    eta: float = 0.85
    # NEW — required, populated from steady-state solver output
    T_chip_ss_C: float | None = None
    T_supply_ss_C: float | None = None
```

**New factory:**

```python
def server_configs_from_steady(
    steady_result: SteadyResult,
    network: LiquidNetwork,
    cp_J_kgK: float = 4186.0,
) -> list[ServerThermalConfig]:
    """Build transient configs from a solved steady-state result.

    Populates eta, m_dot, T_chip_ss, T_supply_ss per server so that
    simulate_transient() lands on the steady-state answer as t → ∞.
    """
```

**`simulate_transient` rewrite:** Replace the hardcoded constants with per-server back-solved values. Raise `ValueError` if a config is missing `T_chip_ss_C` or `T_supply_ss_C` (no more silent fallback to toy constants). Guard against `T_chip_ss ≤ ambient` (would divide by a non-positive number).

**Tests:** `tests/test_liquid/test_transient_coupling.py`, four cases:
1. `test_back_solved_ua_matches_eta_formula` — synthetic steady-state result, assert `UA_cp == eta · m_dot · Cp` exactly.
2. `test_transient_converges_to_steady_state` — 1000s integration, assert `|T_chip(end) − T_chip_ss| < 0.1 K` for every server.
3. `test_rejects_config_without_steady_snapshot` — old-style `ServerThermalConfig` (no `T_chip_ss_C`), expect `ValueError` with actionable message.
4. `test_time_constant_is_physical` — assert `(UA_cp + h_air_eff) > 0` and the resulting `tau = C/(UA+h) < 600s` so the transient actually responds within the plot window.

**Demo update:** `src/backend/liquidcooling/demo_two_racks.py` currently constructs `ServerThermalConfig` by hand at lines 113-133. Rewrite the demo to use `server_configs_from_steady()` so the demo and the comparison test share one code path.

**Why it matters:** Without this fix, the chart line has no physical meaning. With it, the line is guaranteed to land on DLT's answer and the approach rate is interpretable.

### 5.4 Fix 4 — Auto-topology advanced-fields guard

**File:** `src/backend/liquidcooling/topology.py` lines 37-71 (the `build_auto_network()` entry point).

**Current bug:** In auto-generated mode the CDU collapses to a pressure-boundary heuristic (`pressure_fixed = max(50, totalFlowRate × 3)` on supply, `0` on return). `CDUConfig.advanced` (pump RPM, HX effectiveness, HX loss, valve opening) is parsed into the schema but never wired into the hydraulic graph — tweaking pump RPM in `cfd_export.json` produces bit-identical simulation output.

**Fix:** Reject configs that set non-default advanced fields while auto-mode is active.

```python
def build_auto_network(cfd_export: dict) -> LiquidNetwork:
    cdu_configs = _extract_cdus(cfd_export)
    for cdu in cdu_configs:
        if cdu.advanced and _has_nontrivial_advanced_fields(cdu.advanced):
            raise ValueError(
                f"CDU '{cdu.name}' has advanced hardware fields "
                f"({_list_nontrivial_fields(cdu.advanced)}) but the project is using "
                "auto-generated topology mode, which only supports a simplified "
                "pressure-boundary CDU model. To simulate pump curves, heat exchanger "
                "losses, or valve openings, switch to manual topology mode and lay out "
                "the full pipe network explicitly."
            )
    # ... existing auto-generation logic unchanged
```

**Module-private helpers:**

```python
_ADVANCED_FIELD_DEFAULTS = {
    "pump_rpm": None,
    "hx_effectiveness": None,
    "hx_loss_coefficient": None,
    "valve_opening": None,
}

def _has_nontrivial_advanced_fields(advanced: dict) -> bool:
    return any(
        advanced.get(field) is not None and advanced.get(field) != default
        for field, default in _ADVANCED_FIELD_DEFAULTS.items()
    )

def _list_nontrivial_fields(advanced: dict) -> str:
    return ", ".join(
        f"{field}={advanced[field]}"
        for field in _ADVANCED_FIELD_DEFAULTS
        if advanced.get(field) is not None
    )
```

**Rationale for the "non-None, non-default" definition:** The frontend always serializes an `advanced` dict on every project, even when empty. Using "key present" as the trigger would fire on every project. Using "set to any non-default value" makes the guard opt-in from the user's side.

**Tests:** `tests/test_liquid/test_topology_auto_guard.py`, four cases:
1. `test_auto_mode_without_advanced_fields_succeeds` — empty/default advanced dict, network builds.
2. `test_auto_mode_with_pump_rpm_raises` — `pump_rpm=3000`, `ValueError` mentions `pump_rpm`.
3. `test_auto_mode_with_multiple_advanced_fields_lists_all` — `pump_rpm` + `valve_opening`, error message lists both.
4. `test_manual_mode_ignores_advanced_guard` — same advanced fields but manual topology path, no error (guard is auto-mode only).

**Why it matters:** DLT's reference JSON uses the `load_diffliquid_layout()` path, not the auto-generation path, so this fix is orthogonal to the chart. It addresses an independent correctness bug in the main cfd_export flow that Codex flagged as "high" severity.

## 6. The comparison deliverable

### 6.1 Regression test (CI)

`tests/test_liquid/test_diffliquid_comparison.py` — asserts convergence, runs in CI, no matplotlib.

```python
def test_transient_converges_to_dlt_steady_state():
    """Validate our transient reaches DLT's steady-state endpoint within 600s."""
    layout = load_diffliquid_layout(
        "Ref_code/DiffLiquidTwin/liquid_dc_two_racks.json"
    )
    network = build_network_from_layout(layout)
    steady_result = solve_steady(network)
    server_configs = server_configs_from_steady(steady_result, network)

    transient_cfg = TransientConfig(
        servers=server_configs,
        supply_C=layout.supply_temperature_C,
        ambient_C=25.0,
        dt=1.0,
        t_end=600.0,
    )
    ts = simulate_transient(transient_cfg)

    for srv in server_configs:
        final_T = ts.chip_temps[srv.server_id][-1]
        assert abs(final_T - srv.T_chip_ss_C) < 0.1, (
            f"Server {srv.server_id}: transient landed on {final_T:.2f}°C, "
            f"steady-state was {srv.T_chip_ss_C:.2f}°C"
        )
```

### 6.2 Plot script (manual)

`scripts/plot_diffliquid_comparison.py` — run by hand to regenerate the committed PNG. Loads the same network, runs the same solvers, then draws with matplotlib:

- One subplot per server (or overlay with legend if fewer than ~4 servers).
- X axis: time (0-600s).
- Y axis: chip temperature (°C).
- Solid line: our `simulate_transient()` output.
- Dashed horizontal line at `T_chip_ss` — the DLT reference.
- Annotations: final `|ΔT|` (should be ~0) and time-to-90% (the thermal time constant).
- Saved to `docs/figures/diffliquid_comparison.png` (committed to git).

```bash
# Usage
conda run -n dcdtuni python scripts/plot_diffliquid_comparison.py
```

**Why split test and plot script:** The test asserts physics (convergence) and runs in CI with no matplotlib import, no PNG artifact churn in the repo. The plot script pulls matplotlib and writes a PNG — manual only, not part of CI. The PNG itself is committed so reviewers see the chart without running anything.

## 7. Acceptance criteria

1. **Fix 1** — DLT parser test passes; rack manifold and server port heights are nonzero when parsed from `liquid_dc_two_racks.json`.
2. **Fix 2** — All four runner-gate tests pass; missing/corrupted/incomplete `liquid_results.json` raises actionable `RuntimeError`; happy path still succeeds.
3. **Fix 3** — All four transient-coupling tests pass; `simulate_transient` no longer contains the hardcoded `50.0` cap or the hardcoded `8.0` air conductance.
4. **Fix 4** — All four auto-topology tests pass; setting `pump_rpm` in auto-mode raises a `ValueError`.
5. **Comparison test** — `pytest tests/test_liquid/test_diffliquid_comparison.py` passes (final chip temperature within 0.1 K of steady-state for every server).
6. **Comparison chart** — `python scripts/plot_diffliquid_comparison.py` produces `docs/figures/diffliquid_comparison.png` showing visible convergence behavior: each curve starts near `supply_C + small_offset`, rises over tens of seconds, and asymptotes onto the DLT reference line by `t=600s`.
7. **Regression** — The existing 55 `tests/test_liquid/` tests still pass.

## 8. Out of scope

- Wiring advanced CDU fields into the auto-mode hydraulic graph (Fix 4 defers this explicitly — users are told to switch to manual mode).
- Calling the steady-state solver inside the transient integrator (implicit coupling) — explicitly rejected in Q3 as overkill for the 10-minute chart.
- Wrapping DLT in an outer transient loop (explicitly rejected in Q1 — we treat DLT as a steady-state reference only).
- Frontend changes (precheck on the inherit checkbox) — noted as a future polish.

## 9. Files touched

| File | Change |
|------|--------|
| `src/backend/liquidcooling/diffliquid_parser.py` | Add `height_unit` branch in `_parse_height` |
| `src/backend/aircooling/runner/stlgen_runner.py` | Replace warning with RuntimeError; add completeness check; add `_collect_liquid_cooled_server_ids` helper |
| `src/backend/liquidcooling/solver/transient.py` | Rewrite `simulate_transient`; extend `ServerThermalConfig`; add `server_configs_from_steady` factory |
| `src/backend/liquidcooling/demo_two_racks.py` | Use `server_configs_from_steady` instead of hand-built configs |
| `src/backend/liquidcooling/topology.py` | Add `_has_nontrivial_advanced_fields` guard in `build_auto_network` |
| `tests/test_liquid/test_diffliquid_parser.py` | Add `test_height_unit_parsed_from_two_racks_json` |
| `tests/test_liquid/test_runner_gate.py` *(new)* | 4 tests for the hard-fail gate |
| `tests/test_liquid/test_transient_coupling.py` *(new)* | 4 tests for the back-solved conductances |
| `tests/test_liquid/test_topology_auto_guard.py` *(new)* | 4 tests for the advanced-fields guard |
| `tests/test_liquid/test_diffliquid_comparison.py` *(new)* | Convergence regression test |
| `scripts/plot_diffliquid_comparison.py` *(new)* | Manual plot script for the PNG |
| `docs/figures/diffliquid_comparison.png` *(new, committed)* | The 600s comparison chart |
