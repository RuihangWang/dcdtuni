# Coilwise reset scope and correction

The original air-v1 energy surrogate uses the airflow-weighted return-air
temperature to set common water return. Under fixed room heat, ACU flows and
supply controls, conservation fixes this mean:
sum(q_j*T_return_j) = sum(q_j*T_supply_j) + Q_room/(rho*cp).
Consequently the original plant power has no independent geometry gradient
within this closed fixed-control regime. All learning baselines must share
this known component. Geometry can still change local temperatures and the
admissible controls.

A closed two-coil counterexample exposes a separate local constraint problem:
return26/38C, supply22/22C, flow1/1m3/s, rho1.19, cp1005 and room23.919kW.
The original mixed-return rule selects water return28C, above the first coil's
26C return air, despite zero room heat error. A positive aggregate gap does not
establish each coil's hot-end pinch.

src/agent/scenario_air/energy_coilwise.py adds an explicit alternative
operating-policy assumption: common water supply and return setpoints with
ideal per-branch balancing. The return reset is capped by every provided
positive-airflow unit's return-air-minus-pinch. Hard reset is the default.
The optional smooth proxy uses the same conservative soft minimum in its
value and derivative. Zero-load units conservatively remain in this constraint
if provided; this is not evidence that an inactive coil physically binds.

The charged total cooling load remains the fixed task heat plus ACU fan heat,
preventing numerical heat imbalance from reducing the plant charge. Measured
per-coil loads/branch heat-capacity flows remain visible, and their sum must
satisfy the independent room heat-closure gate before ideal balancing is
interpreted. Cold-end crossover checks are retained. Finite-UA capacity, branch
water-flow limits, valve authority and equipment calibration remain separate.
The historical v1 source and previously frozen experiments are unchanged.
The new surrogate must be shared identically by compared methods and registered
before future learning; it is not retroactively applied to completed results.

Five focused CPU tests pass, including the independent local-pinch
counterexample, uniform-return equivalence, fixed-dT refusal, conservative
smooth scope, and a nontrivial redistribution derivative checked at two FD
sizes. These are algebraic/model-implementation checks, not actual room CFD
or equipment validation. The broader data-center objective remains constrained
design power, supported by actual local thermal constraints and transport.
