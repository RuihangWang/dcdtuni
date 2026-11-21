# Read-only SIMPLEC audit, 2026-10-05

The current `buoyantBoussinesqSimpleFoam_new` ignores the template's
`SIMPLE/consistent yes`: it does not call `simple.consistent()` or use `rAtU`.
The official OpenFOAM v2412 Boussinesq solver has the same omission. This is a
phantom advertised setting; it is not an omitted upstream Boussinesq feature.

The eight selected primary release files are saved in `upstream-simple-v2412/`.
Its `source-receipt.json` binds the official release URL, tar entry and each
file SHA. The complete release archive was streamed, not stored or hashed.
The official `simpleFoam/pEqn.H` implements a consistent branch, while
`buoyantBoussinesqSimpleFoam/pEqn.H` does not. The active production binary and
all thresholds were unchanged during this audit.

Let `D = 1/A`, `C = 1/(A-H1)` and `Hhat = H/A` for the relaxed momentum matrix.
The official incompressible branch adds
`interpolate(C-D)*snGrad(p)*magSf` to the face predictor and changes the cell
predictor to `Hhat - (D-C)*grad(p)`. It then uses `C` in the pressure boundary
constraint, Laplacian and final velocity correction. It must read `H1` before
clearing the momentum matrix; the current Boussinesq code clears that matrix
earlier.

Boussinesq's reduced-pressure momentum force also contains
`B = -gh*grad(rhok)`. At a frozen buoyancy force, the existing momentum mapping is
`U = Hhat + D*(B-grad(p_rgh))`. A cell-level consistent correction preserving
that fixed point requires
`Hhat_consistent = Hhat + (D-C)*(B-grad(p_rgh_old))`,
followed by `U_new = Hhat_consistent + C*(B-grad(p_rgh_new))`.
Merely copying the pressure-only incompressible predictor and changing the
buoyancy coefficient produces an extra `(C-D)*B` at the old fixed point. In a
nonuniform-density hydrostatic equilibrium `B=grad(p_rgh)`, that shortcut can
create velocity from an initially balanced state.

A corresponding frozen-force face pressure correction starts from the original
`phiHbyA + phig_D` and adds `(C_f-D_f)*snGrad(p_rgh_old)*magSf` before solving with
`C_f`. However, a production adaptation still needs a consistent derivation for
the actual collocated face interpolation, nonorthogonal pressure flux and cell
force reconstruction. The Boussinesq velocity corrector uses `reconstruct` of
face force/pressure flux; the incompressible branch uses a cell `grad(p)`.
Those operators must not be interchanged without validating their fixed points
and pressure boundary fluxes.

No SIMPLEC implementation or convergence claim follows from this algebra.
At minimum, a later isolated variant would need zero-buoyancy reduction,
manufactured nonzero-buoyancy/hydrostatic fixed-point checks, MPI pressure/flux
consistency, positivity/conditioning of `A-H1`, and a controlled actual-case
comparison with the same physical and outer residual thresholds. Whether it
improves the present first-pressure-residual plateau remains unverified.
