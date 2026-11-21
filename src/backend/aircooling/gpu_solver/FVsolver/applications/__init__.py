"""FVM discretisations — one module per OpenFOAM-style application.

Each module here exports a ``Solver`` class and IS a discretisation: it turns a
mesh plus a field state into residuals.  A case selects one by name through
``[solver] type`` in its ``fvconfig.toml``, and ``SOLVER_MODULES`` below is the
single place that maps the name to the module.

WHAT IS **NOT** HERE.  Anything that *consumes* a discretisation rather than
being one:

  - the coupled Newton solver           -> ``src/Pipeline/CoupledNewton/``
  - the data-driven adapter layer       -> ``src/Pipeline/NeuralOperator/``
  - the ODIL window and trainers        -> ``src/Pipeline/ODIL/``

This file was once a data-driven solver factory, i.e. one of three
tenants held the package's front door while the other two -- including the
majority tenant -- were addressed only by module path.

NOT A GRAPH SOLVER.  ``zonal_network`` (torch reference) and ``zonal_network_warp`` (Warp kernels) are the L0 model of
a data hall: the zonal pressure-enthalpy air network on a coarse mesh (every cell a zone), read from the same case
and boundary files as the RANS application; they are driven by ``entries/dc_zonal_solve.py`` and
``Pipeline/CoupledNewton/zonal_window.py``, not selected through ``[solver] type``.

Suffix convention: ``ext`` = explicit face-flux assembly, ``cmpt`` =
component-wise implicit.  See CLAUDE.md's "Viscous/Thermal Diffusion" table for
what that changes.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:                                    # pragma: no cover
    from FVsolver.applications.base_solver import BaseSolver

# name a case may write in [solver] type  ->  module under this package.
# The import is deferred to call time: loading every discretisation at startup
# costs seconds and a case uses exactly one.
SOLVER_MODULES = {
    "picoext": "picoext",
    "picocmpt": "picocmpt",
    "buoyantboussinesqpicoext": "buoyantBoussinesqpicoext",
    "buoyantboussinesqpicocmpt": "buoyantBoussinesqpicocmpt",
    "rhocoext": "rhocoext",
    "rhoco_segment_csr": "rhoco_segment_csr",
    # density-based compressible Euler / Navier-Stokes / SA-neg on the compact stencil (Gen-FVGN-3D ports)
    "rhocoinviscompt": "rhocoinviscompt",
    "rhocoviscompt": "rhocoviscompt",
    "diffusionext": "diffusionext",
}

# Density-based compressible applications: primitive [rho, u, v, w, p] state, ghost cells mirrored across
# boundary faces, rho/U/p boundary files.  The ported pair (rhocoinviscompt, rhocoviscompt) mirrors the ghosts
# across the face plane; rhocoext keeps the point mirror through the face centre.
COMPRESSIBLE_SOLVERS = frozenset({"rhocoinviscompt", "rhocoviscompt", "rhoco_segment_csr", "rhocoext"})
PLANE_MIRROR_SOLVERS = frozenset({"rhocoinviscompt", "rhocoviscompt"})


def create_solver(solver_type: str, params) -> "BaseSolver":
    """Instantiate the discretisation a case selected.

    ``solver_type`` is matched case-insensitively, because ``fvconfig.toml``
    files in this repo write it lower-case while the module names are
    camel-cased.
    """
    key = str(solver_type).lower()
    module_name = SOLVER_MODULES.get(key)
    if module_name is None:
        raise ValueError(
            f"Unknown solver type: {solver_type!r}. "
            f"Supported types: {', '.join(sorted(SOLVER_MODULES))}"
        )
    import importlib
    module = importlib.import_module(f"{__name__}.{module_name}")
    return module.Solver(params)
