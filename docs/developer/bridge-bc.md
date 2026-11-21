# Boundary Conditions

## Purpose

`BridgeBCRegistry` is the handoff mechanism between visual geometry and CFD physics. It maps sanitized STL solid names to OpenFOAM boundary condition specifications, bridging the frontend browser tool (which authors geometry) and the `FieldGenerator` class (which writes `0/` field files for the solver).

The registry is an **in-memory object**. Since the `.cfdrun` V2 refactor the runner no longer derives it from geometry itself: `STLGenRunner._prepare_runtime_metadata()` calls `cfdrun_reader.resolve_run()`, which materialises the manifest's `boundaryBindings`, `porousBindings` and `sourceTerms` into `ResolvedRun.bridge_registry`. It is never written to disk. (A legacy `model/bridge_bc.json` file was removed in the 2026-04-11 refactor; the runner deletes any stale copy it finds.) This page describes the code as of 2026-10-21.

Source: `src/backend/aircooling/runner/bridge_bc.py`

## BridgeBCRegistry

```python
from backend.aircooling.runner.bridge_bc import BridgeBCRegistry, BCSpec

registry = BridgeBCRegistry()
registry.register("acu1_supply_patch_1", BCSpec(bc_type="acu_supply",
                                                 temperature=15.0,
                                                 flow_rate=4.0))
spec = registry.get("acu1_supply_patch_1")   # BCSpec or None
```

**Key methods:**

- `register(patch_name, spec)` — stores a `BCSpec` keyed by the sanitized patch name
- `get(patch_name)` — returns the `BCSpec` or `None` if not registered
- `items()` — iterates `(sanitized_name, BCSpec)` pairs
- `__contains__(patch_name)` — membership test
- `__len__()` — number of registered patches

All lookup keys are auto-sanitized: non-alphanumeric characters are replaced with `_`
(same rule as the HTML export tool), so `"ACU 1/supply face"` and `"ACU_1_supply_face"` resolve to the same key.

## BCSpec Fields

```python
class BCSpec(BaseModel):
    bc_type: str = "wall"               # physics role (see table below); default "wall"
    temperature: float | None           # Celsius — supply/fixedTemp patches
    flow_rate: float | None             # m³/s — supply/return/fan patches
    power: float | None                 # W — fan_outlet temperature calculation
    sigma: float | None                 # porosity coefficient
    perforated: PerforatedSpec | None   # replaces sigma for perforated baffles
    heat_flux: float | None             # W/m² — heat_source_flux BC
    physical_flow_area: float | None    # m² — real server face area for U derivation
    cooling_capacity: float | None      # W — ACU max sensible cooling
    min_supply_temp: float | None       # Celsius — coolant_temp + 6°C
    set_point_temp: float | None        # Celsius — ACU supply air set point
    coolant_temp: float | None          # Celsius — chilled water inlet
    return_patch_name: str | None       # paired return patch for exprFixedValue
    cooling_model_mode: str | None      # "fixedTemp" | "linearCapacity" | "tableCapacity"
    rated_return_temp: float | None     # Celsius — rated return air temp for linear model
    cooling_performance_table: list | None  # [{returnTemp, capacity}, …] for table model
    timeVarying: dict | None            # time-varying schedule from .cfdrun JSON
    surface_area: float | None          # m² — total surface area (heat sources: W→W/m²)
    direction: list | None              # unit vector (world) — acu_supply of a louvred outlet
    patch_area: float | None            # m² — area the flow rate is spread over when direction is set
    supply_angle_deg: float | None      # louvre tilt, recorded only
```

`sigma` is used for rack leakage zones and, for perforated baffles that carry no `perforated` geometry, as the legacy porosity.

## Naming Convention

Solid names come from the frontend browser export. The browser sanitizes names at export time; the runner re-sanitizes on registration to guarantee a match. The canonical naming patterns are:

| Pattern                            | Component             | Physics role        |
|------------------------------------|-----------------------|---------------------|
| `{RackName}_frame`                 | Rack frame            | wall                |
| `{RackName}_wall_{N}`              | Rack side/top wall    | wall                |
| `{RackName}_{ServerId}_inlet`      | Server inlet face     | fan_inlet           |
| `{RackName}_{ServerId}_outlet`     | Server outlet face    | fan_outlet          |
| `{ACUName}_supply_{N}`             | ACU supply face       | acu_supply          |
| `{ACUName}_return_{N}`             | ACU return face       | acu_return          |
| `{BaffleName}_wall`                | Wall-type baffle      | wall_baffle         |
| `{BaffleName}_baffle`              | Perforated baffle     | perforated_baffle   |
| `{FloorName}_remaining`            | Floor solid surface   | wall                |
| `{FloorName}_opening_{N}`          | Floor tile opening    | perforated_baffle   |

Actual solid names come from the `.cfdrun` manifest's `solidName` fields, not from the pattern above — the pattern is illustrative. The runner reads `solidName` from each ACU face, server face, and baffle record and sanitizes it directly.

## Physics Roles and OpenFOAM BCs

### wall

Standard no-slip wall, zero heat flux.

```text
U:     noSlip
T:     zeroGradient
p_rgh: fixedFluxPressure
```

### wall_baffle

Internal surface kept as a wall after `createBaffles`. Both sides share zero-gradient pressure.

```text
U:     noSlip
T:     zeroGradient
p_rgh: fixedFluxPressure (both master and slave sides)
```

### perforated_baffle

Cyclic pair created by `createBaffles`, with a `porousBafflePressure` jump condition.
The pressure drop coefficient is derived from `PerforatedSpec` using the Idelchik thin-plate formula:

```text
K = [1 + φ·0.707·(1−σ)^0.5 − σ]² / σ²
```

where σ = `openAreaPct / 100` (clamped to [1e-4, 0.9999]) and φ is a shape factor (circle 1.00, hexagon 1.05, rect 1.10, diamond 1.15). The jump is written as `porousBafflePressure` with `D = 0`, `I = K`, `length = 1`.

Special cases by porosity:

- σ = 0 → degenerates to `wall_baffle` (no flow through)
- 0 < σ < 1 → `porousBafflePressure` (`D = 0`, `I = K`)
- σ = 1 → pure cyclic (no pressure drop)

Every porous jump is under-relaxed with `relax 0.3` (`POROUS_JUMP_RELAX` in `fields.py`). The jump is explicit in the flux; without relaxation the start-up swings of many perforated doors fed back into the flow (Shinaide's 64 doors and panels drove the continuity error from 1e-4 to 3e6 in ten iterations). The converged steady state is unchanged. Rack doors with different perforations keep their own jump coefficients.

### acu_supply

ACU supply air face. Specifies inlet velocity and temperature.

```text
U:     flowRateInletVelocity (volumetric flow rate from BCSpec.flow_rate)
T:     fixedValue (BCSpec.temperature, Celsius converted to Kelvin)
p_rgh: fixedFluxPressure
```

### acu_return

ACU return air face — a domain outlet: room air flows out to the ACU. A CRAC fan pulls in
what it blows out, so a return is **pinned to its own supply's airflow** when exactly one
plain volumetric supply names it as its `return_patch_name`, both patches are meshed, and the
supply is not a louvre-vector supply (`_pinned_acu_returns` in `fields.py`):

```text
pinned return:
U:     flowRateOutletVelocity (the paired supply's volumetric flow or airflow table)
T:     zeroGradient
p_rgh: fixedFluxPressure
```

Every return that cannot be pinned is a **pressure reference**; when every return could be
pinned, the first one by name stays the reference and mass conservation makes it carry its
own supply anyway:

```text
reference return:
U:     pressureInletOutletVelocity (zeroGradient on outflow, bounded on backflow)
T:     zeroGradient
p_rgh: fixedValue uniform 0
```

With a single return there is nothing to pin, and that return is the reference.
`DCDTUNI_ACU_RETURN_PRESSURE=1` restores the older behaviour (every return a pressure
reference). Pinning was introduced after Base2-9's eight returns kept trading air and heat
for a thousand iterations as pure pressure outlets, so the return temperatures — the
convergence criterion — never settled.

**Sealed meshes: ACUs follow the servers (owner 2026-11-10).** When containment or
full-block panels split the mesh into several regions, each region's air in must equal its
air out, and the configured ACU flows rarely satisfy that. Before pinning,
`_balance_sealed_acu_airflow` treats every paired unit (one plain volumetric supply naming
one meshed return) as one unknown flow, supply = return, and takes the flows nearest the
configured ones (least squares on the relative change) that balance every region: supplies
in it − returns in it + server outlets − server inlets = 0. A region holding an unpaired
return absorbs any imbalance and sets no balance. The new flows replace the supply flows,
so the returns are pinned to them as above; the runner logs the largest changes and writes
`acu-airflow-balance.json` next to `0/`, which the boundary-condition audit reads as the
bound flow. Nothing changes when the configured flows already balance (any open room), for
a one-region mesh, or when a balance would need a unit to stop or reverse, or a region has
no ACU that can carry its servers' air (both logged). `DCDTUNI_ACU_NO_BALANCE=1` keeps the
configured flows. Before this, CMI-Guichou's sealed hot aisles pushed 21.9 and 33.7 m3/s
into returns configured for 10.2, and the room's returns pulled air backwards.

### fan_inlet

Server inlet face (fan-bearing server). The fans draw air out of the room domain into the
server, so from the room's perspective this is a domain outlet. `flowRateOutletVelocity`
with a positive flow rate is used — OpenFOAM's `flowRateOutletVelocity` removes flow from
the domain.

```text
U:     flowRateOutletVelocity (BCSpec.flow_rate, positive; air exits room into server)
T:     inletOutlet (inletValue = value = ambient init_temperature)
p_rgh: fixedFluxPressure
```

The `inletOutlet` form behaves as `zeroGradient` on normal outflow (air leaving the room
into the server) but clamps `T` to the ambient `inletValue` if backflow / reverse flow
occurs — this guards the solver against reverse-flow blow-up.

### fan_outlet

Server outlet face. Hot air exits after passing through the server.

```text
U:     flowRateInletVelocity (BCSpec.flow_rate)
T:     uniformFixedValue (runtime expression: weightAverage(T on paired server INLET face) + ΔT)
p_rgh: fixedFluxPressure
```

Temperature tracks the live air actually reaching the server. The production path writes a
`uniformFixedValue` with a runtime `expression` sub-entry that samples the paired server
**inlet** face:
`T_outlet = weightAverage(T on the paired server inlet face) + ΔT`. Using the live CFD
temperature sampled on the server's inlet face (rather than the global ambient) means the
outlet tracks the actual air reaching that server, including aisle hot-spots and
recirculation. The rise is
`ΔT = power / (flow_rate × ρ × Cp)` where **ρ = 1.19 kg/m³**
(`rho_air` from `backend.aircooling.utils.const`), **Cp = 1005 J/(kg·K)**
(`air_specific_heat` from the same module).

A plain `fixedValue` form using `ambient + ΔT` exists only as a static/test fallback
(`static_outlet_temperature=True`) and as a mesh-time placeholder (`strict=False`); the
production solve path always uses the `weightAverage(T) + ΔT` expression.

### nonfan_fixed_temp

Non-fan equipment face with a fixed surface temperature.

```text
U:     noSlip
T:     fixedValue (BCSpec.temperature)
p_rgh: fixedFluxPressure
```

### nonfan_heat_flux

Non-fan equipment face with a power-based heat flux.

```text
U:     noSlip
T:     externalWallHeatFluxTemperature (BCSpec.heat_flux W/m²)
p_rgh: fixedFluxPressure
```

Heat flux is derived from power divided by face area:
`heat_flux = power / (span_slots × 0.04445 × rack_width)`.

### fixedTemperature

Surface held at a fixed temperature (distinct from `nonfan_fixed_temp` in name, but
identical BC form).

```text
U:     noSlip
T:     fixedValue (BCSpec.temperature, Celsius converted to Kelvin)
p_rgh: fixedFluxPressure
```

### heat_source_flux

Arbitrary heat-source surface with a prescribed flux (W/m²). Used for non-rack heat
sources where the user specifies `heat_flux` directly, or where the runner derives it
from `power / BCSpec.surface_area`. Supports time-varying schedules via
`BCSpec.timeVarying`.

```text
U:     noSlip
T:     externalWallHeatFluxTemperature (BCSpec.heat_flux W/m², or derived from power/surface_area)
p_rgh: fixedFluxPressure
```

When `BCSpec.heat_flux` is 0 or absent and `BCSpec.timeVarying.heatOutput` is present,
the runner converts the power table to flux using `BCSpec.surface_area`.

## ACU Return Face — Pressure Anchor

At least one ACU return keeps `p_rgh = fixedValue 0`: this Dirichlet condition anchors the pressure field. Without it the room is a closed system where every boundary is Neumann (`fixedFluxPressure`), the pressure matrix is singular, and the solver appears to run but diverges after about 150 iterations. `_pinned_acu_returns` therefore always leaves one return unpinned.

The reference return's combination:

| Field  | BC type                        | Value          |
|--------|--------------------------------|----------------|
| p\_rgh | `fixedValue`                   | `uniform 0`    |
| U      | `pressureInletOutletVelocity`  | `uniform (0 0 0)` |
| T      | `zeroGradient`                 | —              |

Plain `zeroGradient` on U diverged when a recirculation cell in a confined corner drove inflow through the return; `pressureInletOutletVelocity` behaves as `zeroGradient` on outflow and bounds backflow. Do not change `p_rgh` on the reference return to `fixedFluxPressure`.

## Per-Unit Flow Splitting

When a server face has several sub-patches (e.g. a fan array with individual fan patches), the runner splits the server's flow and power **by exported patch area**:

```text
Q_i = Q_total · A_i / ΣA
P_i = P_total · A_i / ΣA
```

A sub-patch the STL export clipped away (no exported area, not in the mesh) gets nothing, and the paired inlet still draws the full flow. Equal areas reduce to the old even split. The outlet temperature rise `ΔT = P/(Q·ρ·Cp)` is the same for every sub-patch. Tests: `src/backend/tests/test_fan_outlet_area_split.py`.

## Liquid Cooling Integration

Air–liquid coupling is a one-way **heat handoff** from a completed liquid DAE session to the air run; the runner does not read `model/liquid_results.json` or `inheritLiquidResults` any more. When the editor sends `hybridHeat` with `/api/run-mesh` (a hybrid project whose liquid experiment has completed), the server calls `compile_hybrid_air_heat` (`hybrid_air_heat_handoff.py`): it re-audits the liquid inputs, result and runtime receipt (`hybrid_heat_audit.py`), checks that the project revision, the air manifest and the exported STL hashes all match, and writes a derived air `.cfdrun` that carries the liquid sample's heat split. Two budget forms exist (`runDefaults.hybridHeatBudget`): version 1 turns each liquid-cooled server's air-side share into a heat flux on its exported front/outlet faces (a zero allocation becomes a wall; the flux denominator is the actual exported STL triangle area); version 2, for fan servers, hands the air side `Q_IT − Q_liquid − storage` as fan enthalpy (`hybrid_fan_heat_handoff.py`). A heat ledger (`hybrid-mesh-*-ledger.json`) is kept next to the derived manifest and in the air run directory. Tests: `src/backend/tests/test_hybrid_air_heat_handoff.py`.

## Pre-solve BC Audit

Before every solve, `STLGenRunner._audit_boundary_conditions()` (`bc_audit.py`) reads the written `0/` fields (every processor directory, or the serial case) and compares them patch by patch with the bindings of the manifest the solve executes and with the registry after its corrections (fan flow split by area, returns pinned to supplies, merged doors). It writes `bc-audit.json` next to the solver log and logs each issue; it never changes a field and never stops a solve. It was added after several silent mismatches: perforated baffles written as pure `cyclic` for a month of dcCart solves, a fan inlet turned into a wall because its paired outlet had been clipped from the STL, and rack doors with different perforations sharing one set of coefficients. Tests: `src/backend/tests/test_bc_audit.py`.
