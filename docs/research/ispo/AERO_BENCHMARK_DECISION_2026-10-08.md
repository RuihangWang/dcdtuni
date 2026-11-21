# Aerodynamic benchmark decision — 2026-10-08

The user-approved v2 plan requires a parameterized aerodynamic scenario with
mature DAFoam RANS adjoints, a cheap differentiable model, full information and
baseline comparisons, at least five training seeds, generalization and final
design verification. It does not prescribe the later native high-Re CL case.

The completed native finite-TE case retains its actual rank17/two-grid evidence
and all failed continuous-adjoint attempts. Its direct volume map has now passed
28 geometry/units checks. A separate static audit identifies incompatible empty
surface-state indexing in the examined DAFoam path and possible auxiliary wall
distance PDEs during construction. Neither concern is relabeled as an executed
runtime failure. No native empty runtime or third-party solver fork was launched.

Developing new solver state ownership is outside the method's intended
contribution. The main DAFoam aerodynamic study will therefore use the separately
registered supported CST16 benchmark, retaining the scientific scope:
case/agent/dafoam-cst16-supported-benchmark-20261008/protocol_v1.json.

This benchmark preserves the official actual profile at zero perturbation,
uses16 independent upper/lower CST perturbations, the supported symmetry and
inletOutlet boundary arrangement, and a drag objective under lift and geometry
constraints. Reynolds number, requirements, anchor geometry and one-to-three
operating conditions define the task/generalization family. Coarse/fine/
verification grids, cheap panel/declared drag closure, physical residual mapping,
full shape FD, gradient and grid gates remain required.

It is a new evaluator definition before training, not a renamed successful
port or reuse of the old native CL/official CD-alpha values. The old results
remain historical interface evidence with their own identity. The new baseline,
shape adjoints, all comparisons and final design checks must purchase their own
source-bound evidence. No CFD was authorized by the proposal document alone.

Root accepted the source hashes and pure geometry scope of the three packages:
- native map: terminal edf3fb3967136aaed92707c1bc8e08da467f55e66f15361cd1bbaa9f564ba874;
- static constructor audit: 3e14119247d8d9a5b6fbf88d7f5fdd67bae83d5445931c77c0a6b1db20a304ff;
- supported benchmark: 29b8ca4905f8b82703f1c5a04cd96ac3d0938b34dd4ec0593d11dc28464d46e1.

Next is actual IDWarp/MPhys coordinate-order/JVP/VJP and mesh acceptance with
zero flow/adjoint calls, then the separately approved bounded5-primal/2-adjoint
first-direction gate. One direction will not close full sixteen-dimensional
validation. The first runtime stage remains serial, CPU28–29, two threads,
no GPU, with only repository-owned run/cache writes in the read-only image.
