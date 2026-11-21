# Independent continuous manufacturing contract and equation map

This PRIVATE research extension uses the actual new ring/rear .cfdproj and current Root typefixed React/compiler/DAE source snapshots. Geometry, n_cells, physical profiles, branch controls, the 56 cold plates, HX and pump remain in the input. It introduces declared synthetic Pa/W forcing and a declared nonsteady initial state. These are mathematical verification data, not measured rack/CDU operation.

The input namespace is runDefaults.liquid.experiment.dae.manufactured_benchmark, explicit version1. The real saved-experiment compiler emits it only into strict .liquidrun v4. No environment flag activates it. Backend admission verifies the graph/storage layout digest, source digest, exact sample times, units, distinct complete IDs, finite values and caps. Sources are fixed sampled benchmark data unless a controlled differentiability test explicitly supplies an independent source dependence through theta. Normal absent input retains exact previous parameter/state/checkpoint identities.

## IDs, state and units
The actual admitted graph has 229 nodes, 286 edges: 214 pipes, 56 CPs, 14 valves, one pump and one HX. There are 892 water cells, 56 solid temperatures and two HX temperatures, or 950 storage states. The full state has 1693 entries: 228 free pressures, 286 algebraic flows, 229 algebraic node temperatures and 950 storage temperatures. Water IDs are edge_id/cell/local_index, derived from the actual admitted per-edge n_cells and compiled spans. Initial pressure explicitly covers every node, including the original datum. Every edge gets a Pa momentum source; each storage balance gets a W source. Mass and ideal-mixing nodes have no manufactured source. Temperature and initial derivatives are K and K/s; flow is kg/s; time is s. No hydraulic inertia or source of mass is introduced.

Closed valves, held-flow pumps, setpoint HXs and rise-mode CPs are explicitly unsupported by this initial benchmark contract. Its actual tested graph uses ordinary open curve/power/physics forms. Declared +/-1 flow signs and positive initial Kelvin values are validated; accepted benchmark states must stay in the nonzero sign regime >1e-8 kg/s. Original physical, Newton, backtracking, dense size and thermal energy gates are unchanged.

## Constructive hydraulics, independent of a normal root
Let B have +1 at each directed from node and -1 at its to node. A deterministic BFS tree generates one fundamental cycle for each chord; the tree return path closes that chord. C is integer and B*C is exactly zero. The actual cycle dimension is computed as E-N+1=58. A deterministic feasibility LP selects cycle coefficients with through-CP/pump/HX forward circulation. Bounded independent seeded cycle perturbations make every edge nonzero. Further sinusoidal coefficients have a constructive all-time bound below .12 of each base |m|; no sign reversal is needed:
 m*(t)=C[a0+a1*sin(omega*t+phase)], omega=2*pi/32.
 m_dot is the analytic derivative. It is recorded as a mass-compatibility check, not added to the incompressible algebraic momentum equation. This synthetic feasibility trajectory can have substantial ring circulation and artificial compensating pressure sources; it is not a pump performance prediction.

Pressure is a smooth bounded vector from canonical node order, with the original datum values copied exactly. Algebraic hydraulic accuracy is assessed at every sample; there is no hydraulic time-convergence order to claim.

Independent NumPy laws use admitted physical coefficients as data:
- pipe: q=sqrt(m^2+eps^2), Re=q*D/(A*mu), eps=1e-8; f_lam=64/(Re+eps); f_turb=.25/[log10(roughness/(3.7D)+5.74/(Re+eps)^.9)+1e-10]^2; the current smooth logistic blend around Re2300/200 gives f. L_pipe=(f*length/D+K_minor)*m*q/(2*rho*A^2)+rho*g*dh.
- CP/HX: L=a*m*sqrt(m^2+eps^2)+b*m.
- pump: L=-rho*g*rpm_ratio^2[H0-a*(m/rho)^2].
- valve: Kv_eff=Kv_max times linear opening, 50^(opening-1), or sqrt(opening), per actual characteristic. L=1e5*(rho/1000)[3600/(rho*Kv_eff)]^2*m*sqrt(m^2+eps^2).
The manufactured momentum source is L(m*)-p*_from+p*_to in Pa. independent_trajectory.py never calls a product residual, thermal helper, Newton, or an observed normal solution to generate these sources.

## Independent thermal storage and mixing
All 950 storage temperatures have nonuniform, smooth positive sinusoidal trajectories. Distinct canonical-index phases/amplitudes expose indexing errors. Their derivatives are analytic. Water/solid/HX means differ, with fixed admitted physical fluid cp/rho/mu and primary inlet/flow.

For known m*(t), upstream/downstream are chosen from its fixed signs. Let w=|m|. Pipe outlet is the terminal water cell in that direction; CP outlet is T_up + epsilon*(T_solid-T_up), epsilon=1-exp[-UA/(w*cp)]; HX outlet is its secondary temperature; pump/valve are ideal thermal passthroughs. Zero-volume node mixing is assembled independently as a finite linear system N(t)T_node=rhs(storage,t). Fixed-temperature boundary semantics are preserved. The actual all-active graph has no unobservable dry nodes. No node source is used.

For each actual water cell j, with upstream state U_j from the actual ordered span and direction:
 S_w,j=rho*cp*V_j*Tdot_j* - |m_edge|*cp*(U_j*-T_j*) - Q_cell,j.
For each actual CP:
 S_s=Csolid*Tdot_s* - Qphysical + |m|*cp*epsilon*(T_s*-T_up*).
For each secondary HX:
 S_sec=rho*cp*Vsec*Tdot_sec* - |m_sec|*cp*(T_up*-T_sec*) + UA*(T_sec*-T_pri*).
For each primary HX:
 S_pri=rhopri*cppri*Vpri*Tdot_pri* - mpri*cppri*(Tinpri-Tpri*) - UA*(Tsec*-Tpri*).
All terms are W. The 21kW physical CP source remains explicit and separate. Pipe distributed heat and primary circuit parameters remain their original admitted inputs.

Summing storage balances and cancelling internal advection through mass-consistent ideal mixing gives:
 physical_heat + manufactured_external_heat + boundary_exchange - storage_rate - primary_rejection = 0.
Product diagnostics report physical_heat_input_W and manufactured_heat_W separately, and use their total for the existing heat_input_W/energy gate. The Pa source norm is separately reported; this incompressible/thermal model has no mechanical-work-to-heat equation, so no unmodelled pump-work heat is invented.

## Initial condition and temporal contract
The namespace explicitly supplies every initial pressure, flow, node temperature, water temperature, solid temperature and HX temperature, plus every storage temperature derivative. It is not replaced by a steady solve. Source-aware initialization runs the original problem/state/parameter predicates and checks the original continuous scaled residual against the unchanged Newton atol. Wrong datum and wrong derivative are actual negative tests. Subsequent steps use the unchanged original backward_euler_step.

The finite sources are endpoint_hold: sample i enters the original BE equation on (t_(i-1),t_i]. The independently continuous reference has analytic sources; replacing those by endpoint samples is a consistent first-order discretization. The experiment compares the actual BE states to continuous x*(t_i), preserving all time/state errors. Pointwise independent evaluations include non-grid times .37,1.23,2.71,4.19,7.83s. Product F is used only to audit already independently built sources, never to create them.

Initial coarse-grid final HX errors can exhibit cancellation and delayed asymptotic order. Both final and whole-trajectory errors are retained; no failed/coarse metric is hidden, and original order gates are not lowered. Numerical convergence evidence and normal UI acceptance are distinct.

## Differentiability and replay
The original implicit Newton/adjoint is used. A fixed-source physical heat control has a nonzero sensitivity and is checked against central FD. Independently regenerated heat/pump/valve Pa/W sources construct a parameter-independent target trajectory, with expected zero sensitivity. The tests declare this source dependence explicitly through theta; no hidden time callback or residual-generated counterfactual exists. An initial valve perturbation beyond opening1 was correctly rejected and retained; the valid coupled valve test is centred at .9.

Checkpoint identity uses the existing full manifest/config hash, which binds source and initial-state digest. The original replay path is unchanged. Default-absent old fixture and new ring ordinary runs compare complete frame/checkpoint/layout/theta/state bytes, not only tolerances. The original frozen V2 thermal-expm data remain subsystem evidence; the new product-chain forcing/initial entry is separate.
