"""Parse ``fvSchemes.ddtSchemes`` in OpenFOAM's own syntax, for every paradigm.

This is the single source of truth for the time scheme. GNN+FVM-Loss, FIFO-ODIL and MMS
all read the same key and get the same answer, unlike a key that would
select only whether the CONVECTION flux was sampled at the new time (``"implicit"``) or
averaged over old and new (``"imex"``), leaving diffusion, pressure and continuity
unconditional. ``"implicit"`` therefore never meant backward Euler, ``"imex"`` was not an
implicit-explicit splitting (its "explicit" half still contained the new state), and the
Crank-Nicolson coefficient that ODIL actually ran lived in a different config section
entirely.

**The factorisation this enables.** A solver evaluates every spatial flux at the new time
level and receives an EFFECTIVE old state; the caller makes

    (phi_new - phi_old_effective) / dt

equal the chosen scheme's time derivative. That is how OpenFOAM does it -- which is why
``fvm::div(phi,U)`` is unconditionally implicit there while ``ddtSchemes`` still selects
between Euler, CrankNicolson and backward. It keeps ``src/FVsolver`` free of any notion of
time stepping, which matters because three paradigms share it.

Accepted spellings follow OpenFOAM's ``system/fvSchemes``::

    ddtSchemes = "Euler"
    ddtSchemes = "CrankNicolson 0.5"
    ddtSchemes = "CrankNicolson"          # coefficient defaults to 0.5, as OpenFOAM does
    ddtSchemes = "backward"
    ddtSchemes = "steadyState"            # ddt term identically zero: the steady equations
"""
from __future__ import annotations

from dataclasses import dataclass

# OpenFOAM's own default when CrankNicolson is named without an off-centring
# coefficient. 1.0 would be pure Crank-Nicolson; the reference Cylinder case and every
# calibrated configuration here use 0.5, which damps the oscillation pure CN admits.
DEFAULT_CRANK_NICOLSON_COEFFICIENT = 0.5

_ALIASES = {
    "euler": "Euler",
    "backward": "backward",
    "bdf2": "backward",
    "cranknicolson": "CrankNicolson",
    "cn": "CrankNicolson",
    "steadystate": "steadyState",
    "steady": "steadyState",
}


@dataclass(frozen=True)
class DdtScheme:
    """A parsed ``ddtSchemes`` entry.

    ``off_centering`` is OpenFOAM's ``ocCoeff``: 0 reduces Crank-Nicolson to Euler and 1
    is pure Crank-Nicolson. It is 0.0 for the two schemes that do not use one, so callers
    can pass it on unconditionally.
    """

    name: str                  # "Euler" | "CrankNicolson" | "backward"
    off_centering: float = 0.0

    @property
    def is_crank_nicolson(self) -> bool:
        return self.name == "CrankNicolson"

    @property
    def is_backward(self) -> bool:
        return self.name == "backward"

    @property
    def is_steady_state(self) -> bool:
        return self.name == "steadyState"

    def __str__(self) -> str:
        if self.is_crank_nicolson:
            return f"CrankNicolson {self.off_centering:g}"
        return self.name


def parse_ddt_scheme(raw) -> DdtScheme:
    """Parse one ``ddtSchemes`` value.

    Rejects the legacy ``"implicit"`` and ``"imex"`` spellings by name rather than
    falling back to a default. Both name a convection-flux choice rather than
    a time scheme, so silently mapping them would carry the old ambiguity forward into a
    config that now looks unambiguous -- and for ``imex`` it would also silently change
    the numerics of any run that inherited it.
    """
    if isinstance(raw, DdtScheme):
        return raw
    text = str(raw).strip()
    if not text:
        raise ValueError(
            "fvSchemes.ddtSchemes is empty; give it an OpenFOAM time scheme, e.g. "
            '"CrankNicolson 0.5", "Euler" or "backward"'
        )

    tokens = text.replace(";", " ").split()
    key = tokens[0].lower().replace("_", "").replace("-", "")

    # "imex" and "implicit" name a CONVECTION time level, and for the PCNO
    # (GNN + FVM-loss) paradigm that asymmetry is the design rather than a defect.
    # Diffusion and pressure are elliptic STIFF terms there: averaging them over
    # old and new would put half of a stiff operator on data the optimiser does
    # not control, so they stay at the new time level unconditionally.  Only
    # convection may be trapezoidal.  This function does not REJECT both spellings
    # as "not a consistent scheme", which took the uniform OpenFOAM factorisation
    # as the only legitimate one; under that reading eight shipped Cylinder
    # configs silently degraded to backward Euler, because the raise was swallowed
    # by a bare except in the only production caller.
    #
    #   imex     -> convection averaged 0.5/0.5 over old and new  (== CrankNicolson 0.5)
    #   implicit -> convection at the new time level only          (== Euler)
    #
    # The mapping is exact, so a config may keep either spelling.
    if key == "imex":
        return DdtScheme(name="CrankNicolson", off_centering=1.0)
    if key == "implicit":
        return DdtScheme(name="Euler")

    if key not in _ALIASES:
        raise ValueError(
            f"unknown fvSchemes.ddtSchemes = {text!r}; supported schemes are "
            '"Euler", "CrankNicolson [coefficient]", "backward" and "steadyState"'
        )
    name = _ALIASES[key]

    if len(tokens) > 2:
        raise ValueError(
            f"fvSchemes.ddtSchemes = {text!r} has too many tokens; expected a scheme "
            "name optionally followed by one coefficient"
        )

    if name != "CrankNicolson":
        if len(tokens) == 2:
            raise ValueError(
                f"fvSchemes.ddtSchemes = {text!r}: only CrankNicolson takes an "
                f"off-centring coefficient, {name} does not"
            )
        return DdtScheme(name)

    if len(tokens) == 1:
        return DdtScheme(name, DEFAULT_CRANK_NICOLSON_COEFFICIENT)
    try:
        coefficient = float(tokens[1])
    except ValueError as exc:
        raise ValueError(
            f"fvSchemes.ddtSchemes = {text!r}: the CrankNicolson off-centring "
            f"coefficient must be a number, got {tokens[1]!r}"
        ) from exc
    if not 0.0 <= coefficient <= 1.0:
        raise ValueError(
            f"fvSchemes.ddtSchemes = {text!r}: the CrankNicolson off-centring "
            "coefficient must lie in [0, 1] (0 is Euler, 1 is pure Crank-Nicolson)"
        )
    return DdtScheme(name, coefficient)


def ddt_scheme_from_fvconfig(fvconfig) -> DdtScheme:
    """Parse the scheme out of a loaded ``fvconfig.toml`` mapping."""
    schemes = (fvconfig or {}).get("fvSchemes", {})
    if "ddtSchemes" not in schemes:
        raise KeyError(
            "fvconfig.toml is missing [fvSchemes] ddtSchemes; it is the single switch "
            'for the time scheme, e.g. ddtSchemes = "CrankNicolson 0.5"'
        )
    return parse_ddt_scheme(schemes["ddtSchemes"])


def effective_old_state(
    *,
    phi_old,
    phi_new,
    previous_effective_old=None,
    scheme: DdtScheme,
):
    """The old state a new-time-only solver must be handed to realise ``scheme``.

    The contract is that the solver's implied derivative ``(phi_new - result) / dt``
    equals the scheme's. For Euler that is the real previous field. For Crank-Nicolson,
    substituting ``q_n = (phi_new - phi_old)/dt`` into ``phi_old_eff = phi_new - dt*D_n``
    with ``D_n = (1+c) q_n - c D_{n-1}`` collapses the recurrence to

        phi_old_eff = phi_old - c*(phi_new - phi_old) + c*(phi_old - phi_old_eff_prev)

    so the only history a marching caller carries is the PREVIOUS effective old state --
    same shape and dtype as phi, no derivative field, nothing new to store or resume.

    ``previous_effective_old = None`` marks the first step of a trajectory, where there is
    no history and the scheme degenerates to Euler. FIFO-ODIL seeds its window the same
    way.

    ``backward`` is not handled here: BDF2 derives its second order from two previous
    STATES rather than from a carried derivative, so it needs a different call shape and
    only the ODIL window implements it.
    """
    if scheme.is_steady_state:
        # The ddt-annihilating choice: (phi_new - phi_new)/dt is zero in value AND
        # contributes zero to the Jacobian (the two occurrences cancel exactly), so
        # the solver evaluates the pure steady equations. OpenFOAM's steadyState
        # scheme does the same by dropping the fvm::ddt matrix contribution.
        return phi_new
    if scheme.is_backward:
        raise ValueError(
            "effective_old_state does not implement 'backward'; BDF2 needs two previous "
            "states and is implemented only in the FIFO-ODIL window"
        )
    if scheme.name == "Euler" or scheme.off_centering == 0.0:
        return phi_old
    if previous_effective_old is None:
        # First step of the trajectory: no history to off-centre against, so fall back to
        # Euler rather than inventing one.
        return phi_old
    c = scheme.off_centering
    return phi_old - c * (phi_new - phi_old) + c * (phi_old - previous_effective_old)
