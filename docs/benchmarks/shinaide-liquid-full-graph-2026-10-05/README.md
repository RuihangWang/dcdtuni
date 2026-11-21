# Shinaide complete-graph liquid verification (2026-10-05)

Verdict: **passed** for the declared fixed-topology thermal/adjoint domain.
Input is an actual React-browser-exported liquidrun v4; all 187 nodes/242 edges/56 plates remain.
See [derivation](../../archive/liquid-2026-09-10/shinaide_liquid_full_graph_verification_2026-10-05.md), `receipt.json`,
and the three `manufactured-dt*.liquidrun` verification inputs.

The original 600 s dt=10 s run converges and conserves energy, while its sharp-load chip peak
has a 2.99 K time-discretization error against the independent sampled-hold closed form.
Complete dt=5/2.5 s runs reduce it to 2.12/1.23 K. Smooth nonuniform full-network MMS
shows backward-Euler order 0.978/0.972; finer sharp-step evidence is recorded separately.
Mock equipment parameters support code verification; field calibration is outside this evidence.
