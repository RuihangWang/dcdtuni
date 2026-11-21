# React dynamic liquid graph

> **Status (as of 2026-10-21):** the compiler described here is wired into the editor: the liquid experiment panel compiles the project through `compileDynamicLiquidGraph` (`src/frontend/react/src/liquid/liquidExperiment.ts`), and DAE experiments carry the HX and primary-circuit settings described in [liquid-dae-workbench.md](liquid-dae-workbench.md). The evidence paragraph at the end is the original 2026-09-22 record.

`compileDynamicLiquidGraph` reuses the established geometry compiler on a private project snapshot. Compiler-facing names use stable construction/server UIDs, so changing visible names does not rewire physical identity. Original user labels live in `object_bindings` for renderer and chart linkage. The original project and its undo history are not modified.

Dynamic CDU mode replaces fixed supply pressure/temperature with one return-side pressure reference, a pump, and an NTU heat exchanger discharging to the supply node. Facility water is a prescribed reservoir, not a newly simulated full primary plant. Drawn `flow_control` fittings become physical valves in series with their spoke pipes; ordinary tees remain ideal mixing junctions.

The compiler embeds the shared backend mock equipment pack and binds the new pump, HX and valves to its profiles. Existing pipe geometry, coolant values and cold-plate heat/performance settings are preserved. Explicit facility supply temperature and valve opening remain project settings. This means old cases can still have unrealistic thermal component values: new demonstration cases must configure a coherent mock operating range rather than assuming conversion alone calibrates them.

Time is expressed as duration and dt; N_t is interval count plus the initial sample. Horizons and events must lie on the dt grid. Events target compiled node/edge IDs, and backend runtime performs field/type validation. Components use SI values in the graph; display lengths stay independent of stored metre coordinates.

Current evidence: six new tests exercise actual LiquidSingle/Liquid projects, pressure-reference/HX/valve emission, source immutability, display-name independence, 13 cold plates / 4,550 W in the four-rack source, events and time validation. Together with existing monitor/compiler bridge tests, **29 tests passed**; `tsc --noEmit` passed after removing an unused import. At that time UI wiring, render details, model data migration and browser interaction tests were the next steps; the UI wiring and browser runs have since been done (see the workbench pages).
