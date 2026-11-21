# Shinaide liquid — hybrid air/liquid release case

The active project in this directory is Shinaide_liquid.cfdproj, one of the
ten release cases.
It preserves geometry, rear rack manifolds, independent supply/return rings,
hybrid fan flow/heat budget v2, liquid settings and transient tracks from the
saved GUI budget baseline. Only the display name and checkout-relative case
path were normalized during consolidation.

Defaults: dcCart, base size 0.25 m, eight mesh workers; the 5-minute mesh
time is a soft target that is timed, never enforced. All equipment/thermal
assumptions retain their synthetic/mock provenance.

Status (2026-10-21): the dcCart mesh passes strict checkMesh, and the hybrid
air solve — liquid DAE heat handed to the air side through `hybridHeat` —
converged under the return-air rule (every ACU return within 0.5 K over the
last window). The receipt summary is in
docs/benchmarks/air-release-cases-2026-10-21/README.md. Liquid runs and the
browser/Omniverse display have their own campaign evidence. Next: a transient
air run from this converged steady state.

Old variants used by tests/generators are exact JSON fixtures under
case/verification/shinaide-legacy-projects; they are not additional active
project choices. Git history retains their original files.
Current project state: docs/developer/PROJECT_STATE.md. The 2026-10-15
consolidation handoff is archived at
docs/archive/handoffs-2026-09-10/HANDOFF_EMERGENCY_2026-10-15.md.
