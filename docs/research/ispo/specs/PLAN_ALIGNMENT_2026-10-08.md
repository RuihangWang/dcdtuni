# Current plan alignment and primary playground -- 2026-10-08

The current local plan/handoff/task file hashes are in PLAN_INPUTS_2026-10-08.json.
Latest direct user instruction selects canonical Shinaide_liquid.cfdproj as the
main Agent modeling/design/optimization playground. This updates scene priority;
controlled2D, airfoil, full method/baselines/generalization/E8 remain in scope.

Primary source: case/Shinaide_Liquid/Shinaide_liquid.cfdproj (SHA594558e1...16e626).
Pro and40901 sources match; original is read-only. Research derivative:
case/agent/shinaide-hybrid-playground-20261008,14racks/56liquidServer/4ACU/1CDU.
First prove actual wind/liquid heat handoff, each solver, closure, consumed control
parameters and facility electricity. Original synthetic provenance stays explicit.
The pure-air compiler rejects liquid projects so it cannot silently delete topology.

The handoff's eight-rack M1 project remains a useful engineering fixture. OPEN,
CAC and HAC production exports plus backend resolution are now verified, as is
rotated-y CAC: actual STL bounds,1.2m wall clearance,no overlap,physical inlet/
supply normals,32server/8ACU inventory and326400W inputs,distinct supply controls
and equal commanded returns,3 porous panels at sigma.02 and real top-gap fillers.
This is geometry/BC validation only. G1 physical closure/temperature shifts,
G2 three-grid sufficiency and G3 initial-condition noise are still open.

Implementation mapping:
- Task1/storage/logging and W-SEC: existing accepted repository mechanisms retained.
- Task3-5: existing energy/objective/metrics implementations retained; calibration,
  mixed wind/liquid integration and physical domain validity still need evidence.
- Task6: typed Task/Design/rows and architecture rules adopted from the plan,
  with nonfinite/identity/reference/count safeguards;16 actual rule/pose tests pass.
- Task7: bound single supply/return setter feeds true production BCs and fingerprints.
- Task8: production exporter-v2 handles slab wall-baffle aggregate ownership using
  actual source STL sheets, retaining original porous BCs and bytes; frozen-v1 intact.
- Task11: typed slab/inrow OPEN/CAC/HAC compiler, true quaternions,UID dictionary
  keys/reference rewrites,physical geometry gates. Two additional tests reject
  liquid-topology loss and equipment taller than the room.
- Task9/10/12 and main-playground mixed compiler/oracle: full physical/end-to-end
  acceptance remains open; no geometry test is called a solved CFD case.

The running70 controlled trials are the first64-update seven-arm ten-seed submatrix,
with22 pinned sources and complete setup/runtime charges. They are not the flagship
rank32/G8x16/2000-SFT/task-distribution/tuning protocol in handoff10.5.
Keep them immutable and freeze the future flagship protocol separately.

Plan examples are reviewed against actual APIs/physics. In particular a numerical
head needs an explicit fixed reference density; merely disabling a language adapter
does not specify it. Hard official score and differentiable proxy stay distinct.
Failed attempts are charged directly, not again by dividing by success fraction.
Assumptions about variance/cost or gradient accuracy are not universal guarantees.

Air rho/cp used by the current native evaluator are1.19kg/m3 and1005J/(kg K).
The derived task records these actual values. Their difference from the plan's
recommended1.18/1006 must be included in the next G0/physical validation, not mixed
between plant accounting and CFD.

Engineering observations awaiting resolution include the canonical saved room
height/planeHeight mismatch and missing explicit hybridHeatBudget. Fields
cooling.power and coldplate Q are inputs; the actual accepted liquid heat removal
and a declared mock IT/air allocation must establish the mixed heat budget.

Coilwise energy refinement and exact known-component boundary: see [COILWISE_ENERGY_2026-10-08.md](COILWISE_ENERGY_2026-10-08.md). New ideal common-return surrogate is separate from unchanged v1; use shared task configuration before future training.
