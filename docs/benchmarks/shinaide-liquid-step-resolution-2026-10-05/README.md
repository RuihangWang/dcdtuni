# Shinaide physical load-step resolution (2026-10-05)

Verdict: **passed**. Same complete graph and mock equipment parameters as the real React v4 input.
The pre-step prefix is stationary; this 50..90 s window resolves the original 60 s load jump.
It supplements the complete 600 s runs and preserves their equilibrium initial state.

Peak chip errors at dt 2.5/1.25/0.625/0.3125 s are recorded in `receipt.json`.
The observed orders rise to 0.872/0.930/0.959, confirming the coarse-step error was outside
the asymptotic region. The effective local cold-plate time constant C_s/G is
4.556155–4.565417 s, taken from receipt.json.cp_time_constant_s.
Here G = |mass_flow| c_p [1 - exp(-UA / (|mass_flow| c_p))], matching the
production NTU cold-plate balance. The input's C_s=100 J/K and UA=33.333333 W/K
give C_s/UA=3 s only as the large-flow approximation, where G approaches UA;
it is not the finite-flow C_s/G value of this case. These are local plate
relaxation scales with inlet temperature held, not eigen-timescales of the
complete coupled pipe/plate/HX network.
All four generated window inputs and the executable tool digest are retained.
