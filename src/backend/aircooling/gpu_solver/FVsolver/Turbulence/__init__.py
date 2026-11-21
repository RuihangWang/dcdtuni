"""Turbulence closure models -- physics closures only, no stencil math.

Design: docs/superpowers/specs/2026-08-16-turbulence-framework-design.md.
Model names follow the exact OpenFOAM spelling (design section 7).
"""

from FVsolver.Turbulence.base import TurbulenceModel, Laminar
from FVsolver.Turbulence.eddy_viscosity import EddyViscosityModel
from FVsolver.Turbulence.komega import KOmega
from FVsolver.Turbulence.kepsilon import KEpsilon
from FVsolver.Turbulence.komega_sst import KOmegaSST
from FVsolver.Turbulence.launder_sharma import LaunderSharmaKE
from FVsolver.Turbulence.mlze import MLZE
from FVsolver.Turbulence.realizable_ke import RealizableKE

# name -> class.  Concrete eddy-viscosity models (kOmega first, design
# section 3.4) register here as their phases land.  Names follow the exact
# OpenFOAM spelling (design section 7): "LaunderSharmaKE" is OpenFOAM's
# TypeName for the Launder-Sharma low-Re model (LaunderSharmaKE.H), recorded
# here as the phase-3b registry decision; "kOmegaSST" is the kOmegaSST
# TypeName (phase 6/7).
TURBULENCE_MODELS = {
    "laminar": Laminar,
    "kOmega": KOmega,
    "kOmegaSST": KOmegaSST,
    "LaunderSharmaKE": LaunderSharmaKE,
    # v2412 TypeName kEpsilon (kEpsilon.H:137): the STANDARD high-Re model
    # with wall functions, a different closure from LaunderSharmaKE (its low-Re
    # sibling) and not a flag on it -- kepsilon.py's docstring lists the four
    # structural differences.
    "kEpsilon": KEpsilon,
    # OpenFOAM TypeName realizableKE (Shih et al. 1995): kEpsilon's wall
    # treatment and transported pair with the strain-dependent rCmu, the
    # C1(eta) magS eps production and the k + sqrt(nu eps) sink; no
    # buoyancy production (Cg = 0).  Its [turbulence] wallAlphat selects
    # alphatJayatillekeWallFunction on the epsilon wall-function patches
    # (realizable_ke.py docstring).
    "realizableKE": RealizableKE,
    # Bai et al. (2024) Eq. (4), the mixing-length zero-equation model.
    # OpenFOAM ships no zero-equation incompressible RAS model, so there is no
    # v2412 TypeName to copy; "MLZE" is the paper's own abbreviation and is
    # the name our OpenFOAM transcription registers under too
    # (experiments/DataCenter/openfoam-turb/mlze/), so one string names the
    # same closure on both sides of the comparison.
    #
    # The ONLY entry here with an empty TRANSPORTED: it adds no unknown, no
    # residual row and no wall function, which is why it is the arm that tests
    # whether the coupled Newton difficulty is the closure or the mesh.
    "MLZE": MLZE,
}


def _model_class(name) -> type:
    key = "laminar" if name is None else str(name)
    model_cls = TURBULENCE_MODELS.get(key)
    if model_cls is None:
        raise ValueError(
            f"[Turbulence] unknown model {key!r} in [solver] turbulence; "
            f"valid names: {sorted(TURBULENCE_MODELS)}"
        )
    return model_cls


def create_turbulence_model(name, params, solver) -> TurbulenceModel:
    """Instantiate the model named by ``[solver] turbulence``.

    A missing key resolves to ``"laminar"`` (design section 3.2); an unknown
    name is refused BY NAME with the valid set, never left to surface as an
    AttributeError downstream (the contract.py house pattern).
    """
    return _model_class(name)(params, solver)


def transported_bc_fields(name) -> tuple:
    """BC-layer field names the named model transports, in channel order.

    Consumed by the case loader (FVdomain/Load_mesh.py) to extend the
    solver's required ``bc/*.toml`` set -- e.g. kOmega adds ("k", "omega"),
    so a turbulent case must carry ``bc/k.toml`` and ``bc/omega.toml`` with
    OpenFOAM-meaning values.  Unknown names are refused exactly as the
    factory refuses them.
    """
    return tuple(entry[0] for entry in _model_class(name).TRANSPORTED)


def log_transformed_bc_fields(name) -> tuple:
    """Fields of the named model whose bc/*.toml values are OpenFOAM-primal
    but whose stored variable is the log (kOmega: ("omega",)).  The BC layer
    applies ln() at the ghost write and at internalField init; see
    TurbulenceModel.LOG_TRANSFORMED_BC_FIELDS.
    """
    return tuple(_model_class(name).LOG_TRANSFORMED_BC_FIELDS)
