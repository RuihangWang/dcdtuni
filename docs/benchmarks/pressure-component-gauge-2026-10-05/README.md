# Pressure-component references, 2026-10-05

The Shinaide Liquid mesh has two disconnected pressure components. All pressure
Dirichlet patches belong to the main room; the closed raised-floor plenum has
Neumann pressure. OpenFOAM's mesh-wide `needReference()` is false in that mixed
case, so the previous SIMPLE `_new` implementation left the plenum's constant
pressure undetermined.

The replacement uses the ordinary cyclic/processor pressure graph, detects
boundary anchors from the actual assembled matrix on every solve, and supplies
one pressure reference to every free component. A Robin/mixed field with zero
value fraction is free even though its `fixesValue()` method returns true.
Boundary-anchored components receive no pressure shift or extra reference.
Configured `pRefValue` remains the total kinematic pressure (m²/s²), with
`p_rgh = p - rhok*gh`.

The gauge weight includes boundary diagonal contributions. This is essential
when each MPI rank owns one of two neighbouring cells: their raw matrix diagonal
is zero while the pressure operator's coupled diagonal is nonzero. Isolated
one-cell components use an explicit gauge row; their compatibility check includes
the Neumann boundary RHS that OpenFOAM defers until solve, and allows only
relative floating-point cancellation. Unknown or AMI/ACMI pressure couplings are
explicitly rejected because `regionSplit` does not walk their actual matrix graph.

`simple-gauge-receipt.json` binds the source, isolated executable and raw results.
The installed OpenFOAM v2412 rejects `mpirun -np 1 -parallel`; the original
rejection is preserved in `np1-parallel-rejection.log`. No serial solver was run.
The supported MPI baseline has **6 failed / 3 passed** tests. The fixed suite has
**15 passed**: 13 pressure-kernel tests and 2 tiny actual `_new` laminar regressions.
These four-step solver tests verify the operator and physical fields; they are
not a claim that the full Shinaide CFD case has passed its residual-control gate.
Near-zero equilibrium fluxes can have finite normalized residuals at roundoff.

All per-case mesh/decomposition/solver stdout and scalar result rows are retained
in `kernel-raw.json`. The earlier full-solver development failures remain in
`final-pytest-output.txt`, `final-v2-pytest-output.txt` and
`final-v3-pytest-output.txt`; they exposed a temporary SIMPLE dictionary lifetime
and the deferred boundary RHS. `final-v5-pytest-output.txt` is the accepted result after correcting the test-only empty-face flux diagnostic.

Reproduce in an activated dcdtuni OpenFOAM v2412 conda environment:

```sh
taskset -c 24-31 bash tools/verify_pressure_component_gauges.sh
```

The current script compiles only into `.codex-tmp/pressure-compat-face-build/`; it does not
overwrite production `platforms/` binaries or any active run input.

## Owned transient build and final boundary write

The repo-owned PIMPLE solver uses the selected official OpenFOAM v2412 files
saved in `upstream-v2412/`. `source-receipt.json` records the primary release URL
and every file hash; the complete archive was streamed, not stored or hashed.
The momentum and temperature equations remain byte-identical to that source.
The pressure equations share the same component helper with SIMPLE. This build
explicitly supports static meshes; declared motion/topology and unsupported
pressure coupling fail before reusing an invalid graph.

The valid stock PIMPLE baseline has **1 failed / 1 passed** actual MPI2 solver
regressions: the closed-component case fails in DIC with a zero pressure diagonal.
An initial invalid PIMPLE residual-control fixture is separately preserved and
does not count as evidence of a solver defect.

The final scoped suite has **31 passed** (23.62 s): 13 pressure-kernel tests,
2 SIMPLE and 2 PIMPLE field regressions, 1 dynamic-mesh preflight rejection, and
13 runner-selection/existing resolver tests. The SIMPLE regression first exposed
a final-write defect: `simpleControl` calls `writeAndEnd()` when convergence is
detected outside the loop body, removing explicit physical boundary values.
The solver now rewrites those values after the loop exits. Final `End`, the
SIMPLE convergence marker and explicit U/T patch values are asserted in actual
MPI outputs. `pimple-fixed-pytest-output.txt` preserves the two failures before
that last write; `pimple-final-ut-pytest-output.txt` is the accepted result.

`pimple-gauge-receipt.json` binds source and isolated executable hashes.
`pimple-raw.json` retains per-case stdout, scalar rows, final pressure/U/T fields
and isolated build logs. No production binary was overwritten by these tests.
The tiny PIMPLE regression covers 0.02 s on laminar two-cell cases; a full
Shinaide CFD convergence and transient accuracy claim requires separate API
runs and physical checks.

## Free-component source/flux compatibility

A pressure gauge removes a constant nullspace; it cannot make unbalanced
Neumann forcing conservative. For this ordinary cyclic/processor scalar
Laplacian, the component column sums are zero. Necessary solvability is
`sum(raw source + noncoupled boundary source) = 0`, before adding a gauge row.
The installed scalar solve in `installed-matrix-v2412/fvScalarMatrix.C` uses
exactly that RHS. Local sums use compensated accumulation, and one sum and one
original-term magnitude per global component are reduced across MPI.
The guard permits only `64*SMALL*sumAbs` roundoff and rejects nonfinite values.
Boundary-anchored components receive no new compatibility check or shift.

Jump/custom pressure interface fields can add affine terms outside that raw
RHS, as the saved `jumpCyclicFvPatchFields.C` shows. They now fail explicitly
before this compatibility claim; AMI/ACMI remain unsupported. This is a scope
restriction, not proof that a jump operator cannot be made conservative.

The first valid MPI baseline has **2 failed / 4 passed** tests: multi-cell body
forcing and deferred Neumann boundary forcing were incorrectly allowed through
the old gauge. The fixed suite has **38 passed** (36.40 s), including seven new
MPI2 checks for balanced/unbalanced sources, actual deferred boundary-source
cancellation, a relative roundoff perturbation, an untouched anchored component,
and a valid fixedJump declaration rejected with the precise scope message.
The earlier collection/import error is retained as development history and
does not count as a numerical baseline failure.

`compat-gauge-receipt.json` and `compat-raw.json` bind the new source, isolated
binaries, build logs and per-case raw MPI output. Builds went to a new
`.codex-tmp/pressure-compat-build/`; earlier accepted executables and active
production runs were not overwritten. Source identities from preceding phases
remain in their earlier receipts and Git commits.


## Conservative face assembly roundoff

The first RHS-only guard above has a concrete numerical limitation. A periodic
flow can assemble a near-zero divergence from finite opposing face contributions.
In the saved 60-cell, MPI2 uniform-flow counterexample the assembled sum was
1.47451e-16 while the canceled RHS magnitude was 3.27863e-16. Its 2.09832e-29
RHS-only tolerance falsely rejected an otherwise conservative assembly.

The current guard derives its backward-error scale from the original face
incidences. Each internal face enters once per adjacent cell; each boundary face
enters once for its owner. Ordinary cyclic/processor pairs therefore contribute
once per adjacent cell across their global pair. The same accounting applies to
the actual deferred nonorthogonal correction: the scalar-Gamma specialization
saved in `installed-matrix-v2412/gaussLaplacianSchemes.C` subtracts
`V*div(faceFluxCorrection)` from the pressure source. Both owned solvers pass
their actual predictor flux, and the helper reads the retained nonorthogonal
face flux. Anchored cells do not contribute to the scale; fully anchored cases
skip these traversals and component reductions.

The tolerance is `64*SMALL*(original source magnitude + face-incidence magnitude)`.
It has no absolute physical floor and changes no outer convergence threshold.
The exact manufactured additional component source 1e-6 still fails. A sheared
periodic case also exercises a nonzero actual nonorthogonal correction. These
are compatibility and finite-state tests, not a claim of nonorthogonal Picard
accuracy after three kernel passes.

`compat-face-receipt.json` binds the new isolated binaries, source and 41 passing
tests (39.91 s); `compat-face-raw.json` retains fixture dictionaries, initial
fields, MPI output, result rows and build logs. Earlier failed iterations remain
listed in that receipt, including an MPI diagnostic capture failure, insufficient
six-digit fixture serialization, and a test regex error. The counterexample's
pre-fix executable hash was not recorded; final identities are recorded. Active
production inputs and executables remained unchanged.

`SIMPLEC-audit.md` and `upstream-simple-v2412/` separately document why the current
`consistent yes` template is ineffective and why blindly transplanting the
incompressible consistent correction is insufficient with buoyancy. No SIMPLEC
implementation or improvement claim is included.
