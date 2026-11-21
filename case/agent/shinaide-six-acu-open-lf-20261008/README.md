# Seven-case Shinaide joint-LF preparation

This case connects the accepted six-unit Shinaide layout to the complete normal/fault mock-task evaluation. The current inputs are scenarios_v3/<scenario>/project.cfdproj and the authoritative index SEVEN_CASES_PREPARED_V3.json. These are actual production-canonical projects, with original 14 racks, 56 liquidServer UIDs, fixed server fan flows, IT and liquid components preserved.

## Ready for the next physical experiment

Seven projects pass the actual project schema, strict production liquid graph compiler and the actual-source air geometry/BC compiler. Every project has six ACU bodies and its own source-bound port records. Across all seven, liquid graphs have the same 187 nodes / 242 edges and parameters; all eleven grouped-STL files have identical bytes. The physical OPEN variant explicitly removes the three source non-equipment containment baffles. meta.enabled alone is only viewport visibility and would not remove them from CFD transport.

- Normal: all six ACUs deliver 13.9216/6 m3/s at 20 C.
- Each of six single-unit faults: failed supply/return/fan/capacity are exactly zero; the other five deliver 2.8 m3/s at 20 C.
- The liquid pump remains N=1 in every case.
- The full six-architecture research scope remains; this is the first OPEN reference entry.
- Each scenario has an actual .cfdproj, scenario description, source difference, native graph, air manifest and geometry, plus its own dated Agent output.

Canonical preparation run: 2026-10-08_231533_SGT__seven-real-OPEN-zero-flow-canonical-case-preparation-v3-b9eb28cc.
Latest pure component gate: 2026-10-08_231815_SGT__pure-seven-case-LF-structure-energy-constraint-gate-v4-canonical-source-adb3a854.
Twenty-four tests pass. No new air-network, liquid, CFD, mesh or GPU solve has executed. The tests use explicitly synthetic heat/state fixtures for arithmetic and structural checks and cannot produce physical fields.

## Current implementation

- External scenario authority: src/agent/harness/shinaide_six_acu_protocol_v3.py.
- Same-source paid execution entry: src/agent/harness/shinaide_six_acu_oracle_v4.py.
- Accepted liquid heat / explicit closed ACU contract: shinaide_fan_contract_v6.py.
- Real OPEN geometry / zero-flow export: shinaide_six_acu_air_compile_v2.py, shinaide_six_acu_air_driver_v2.mjs, shinaide_air_export_entry_v4_open_zero.ts and shinaide_zero_flow_cfdrun_build_v1.js.
- Active-variable air equations: src/agent/scenario_hybrid/air_network_open_v1.py and open_network_numerics_v1.py.
- One shared plant with inactive-unit ledger: energy_inactive_acu_v1.py.
- Full registered mock task: six_acu_constraints_v2.py.

The runtime retains every physical ACU body and port UID in the external ledger. A closed unit has no internal flow aperture, pressure/thermal terminal, active supply thermostat, fan electricity or coil heat-removal term. Its return temperature is unavailable, not an invented finite temperature. The numerical state has 251 entries normally and 248 after one closed unit; the outer design-control order is thirteen entries, including all six supply temperatures, all six flows and the liquid pump command. Topology/fault masks are discrete task data, not differentiable design controls.

OPEN uses actual port/STL areas and a pressure/enthalpy network with explicit open-end and above-equipment passages. The old sealed-roof leaf reduction is not reused. Pressure initialization, linear enthalpy initialization and full coupled Newton are implemented with the original residual tolerance; real convergence is the next experiment.

Installed plant design load stays fixed at 98.4 kW (84 kW IT plus six rated 2.4 kW fans), including during faults. Removing one active fan from the numerical energy branch does not redesign the plant. Actual post-fan room heat, fan power, liquid heat and secondary pump electricity remain separately recorded and feed one common primary plant.

## Actual causes and fixes

The previous interface hard-coded four ACUs / nine controls, assumed three physical containment baffles and a fixed no-throughflow leaf, and rejected zero airflow in its contract and energy equations. Retaining a closed unit's positive regularized aperture while commanding zero flow would require an unbounded negative pressure; the new topology explicitly closes those paths instead.

The actual frontend producer also used a truthy fallback that replaced zero ACU flow with 4 m3/s and zero velocity with 2 m/s. A separate source-private producer changes only those four ACU flow/velocity expressions to preserve finite zero; the platform file and all old case sources remain unchanged. The strict transport adapter separately removes the legacy empty merged-wall-baffle placeholder only when the source and exported baffle sets are truly empty; physical floor bindings are retained.

The first chip-temperature fixture was transposed. Actual native output is [56 chips, 1 sample], now required by the v2 constraint reader and checked against the existing producer's field shape; no old temperature values enter these new points.

Actual production normalization also moves three unchanged legacy floor polygon arrays into geometry.polygon. The new scenarios_v3 save the producer's canonical documents, and all seven are recompiled to prove normalization is now idempotent. Earlier scenarios and costs remain.

## Registered constraints

The current task carries the existing A1 18–27 C inlet range, 45 C exhaust and active-ACU return limits, the existing 0.2 K temperature comparison tolerance, active 50 kW coil capacity without overload tolerance, full-room flow at least 13.9216 m3/s, active fan fractions 0.22–1, delivered supply range 16–30 C, 4 K approach and 2 K pinch. The root approved 85 C as an explicit synthetic chip-temperature task threshold, not a hardware rating.

At N=1 the nominal-affinity pump must remain at nonnegative flow and strictly positive head, using the actual packed native parameters. Full native hydraulic residual diagnostics are retained. Air full residual, air heat closure, liquid scaled residual, pump curve/pressure consistency, per-UID heat and steady storage use the already registered numerical gates. The task evaluates all 56 inlet/exhaust/chip values, each active coil, exact closed-unit zero terms, energy closure and room flow; missing required observations fail.

NPSH, OEM pressure ratings and measured fan pressure curves are explicitly unavailable calibration metadata. They are not invented as passes and are not added as field-certification gates to this declared mock simulation task.

## Fault authority and the HF handoff

The external seven-case registry is pinned by SHA b905bdd0e94234684df90b24505d8dea47c3754b63f69f6d87ddf20939417b9d. The source scenario and project hashes are also checked. A design policy cannot self-declare a normal unit faulty to remove its energy or constraints.

evidence/INACTIVE_HF_BOUNDARY_HANDOFF_V2_CANONICAL.json binds each failed UID and both actual patch names. The required HF closure is no normal mass flux, no-slip velocity, pressure consistent with no flux, zero normal diffusive heat flux / adiabatic T, and no active coil or 20 C fixedValue thermostat. The current source's zero-flow ACU export alone does not implement that thermal boundary. The explicit HF adapter remains to be connected and validated on 40901; no HF execution is claimed, and this pending HF connection does not block the registered mock LF experiment.

## Execution after root review

Use the existing dcdtuni Python, PYTHONPATH=src, repository-local cache/temp settings, two CPU threads and an explicit CPU affinity. For example:

    python -B -m agent.harness.shinaide_six_acu_oracle_v4 --scenario normal --execute

The other registered scenario IDs are fault-m_1001, fault-m_1002, fault-m_14, fault-m_18, fault-m_2 and fault-m_6. Without --execute the entry stops before any physics worker. Each actual liquid and air worker is owned, deadline-bounded and reaped; no retry or old physical state is substituted. The evaluated candidate is recompiled in both branches so its actual source hash, heat handoff, geometry, operating state and assessment stay tied together. I3 actions are not purchased in this seven-reference feasibility pass; the complete method experiments remain planned.

Next experiment: root-reviewed sequential execution of these seven joint LF points, followed by source-bound assessment of normal and every single-ACU fault and then the 40901 validated HF chain.
