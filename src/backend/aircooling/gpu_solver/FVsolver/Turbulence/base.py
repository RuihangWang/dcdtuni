"""Turbulence-model contract and the Laminar model.

Design: docs/superpowers/specs/2026-08-16-turbulence-framework-design.md
(section 3.1 states this contract verbatim; section 3.2 defines Laminar).
Division of labour per that design: a model owns PHYSICS CLOSURES only
(nu_t, sources, diffusivities, transforms, floors); the host discretisation
owns ALL stencil math and assembles each transported scalar itself.  No
discretisation kernels may ever live in this package.
"""

from abc import ABC, abstractmethod

import torch


class TurbulenceModel(ABC):
    """What a discretisation may ask of a turbulence model, and nothing else.

    Contract (design doc section 3.1):

    - ``TRANSPORTED``: extra transported fields, in ``COLLOCATED_UNKNOWNS``
      entry format ``(field, width, bc_attr, residual_attr)``.  The HOST solver
      appends them after its own blocks, so the ``[U, p(, T)]`` order that
      ``CoupledNewton/contract.py`` pins is never disturbed.  Laminar: ``()``.
      A two-equation model stores its log-transformed variable under the
      OpenFOAM field name (e.g. ``"omega"``); the BC layer transforms.
    - ``nu_eff_face``: momentum diffusivity per face, consumed by
      ``diffusion_flux``, ``momentum_diagonal`` (hence ``d_f``, the MWI flux,
      and the preconditioner's pressure operator), and the dev2 term (phase 2).
    - ``is_uniform``: True skips the dev2 assembly and the transported-field
      loop entirely, keeping the laminar forward bit-identical.
    - ``transport_closures`` / ``enforce_wall_bcs``: closure tensors and
      ghost-cell writes for the model's own fields; empty until a concrete
      eddy-viscosity model lands (design phases 3-4).
    """

    # (field, width, bc_attr, residual_attr) entries appended by the host
    # solver to its instance COLLOCATED_UNKNOWNS (collocated_newton.py:127
    # reads the tuple off the instance, so no driver change is needed).
    TRANSPORTED: tuple = ()

    # Transported fields whose STORED/solved variable is the log of the
    # OpenFOAM field named in bc/*.toml (design section 2.1: omega-tilde =
    # ln omega, while the field keeps the name "omega" so configs stay
    # readable against their OpenFOAM twin).  The BC layer reads this to
    # apply the transform at the ghost write and at internalField init;
    # nothing else may consume it.
    LOG_TRANSFORMED_BC_FIELDS: tuple = ()

    def __init__(self, params, solver):
        # The host solver is passed for future models that need init-time
        # inspection; it is NOT stored here -- every contract method receives
        # the solver explicitly, so a model cannot silently read state the
        # host did not hand it.
        self.params = params

    @abstractmethod
    def nu_eff_face(self, solver, state) -> torch.Tensor:
        """Momentum diffusivity per face, ``[.., N_face, 1]``.

        Laminar returns ``solver.nu_coeff`` UNCHANGED (the same tensor
        object -- the phase-1 bit-identity hinge, design section 3.1).
        """

    @abstractmethod
    def is_uniform(self) -> bool:
        """True when nu_eff is the registered constant-per-face nu_coeff."""

    def nut_face(self, solver, state):
        """Turbulent viscosity per face ``[.., N_face, 1]``, or ``None`` for a
        model that carries no eddy viscosity.

        Phase-3 accessor (the phase-2 verifier's interface-gap finding): the
        transported equations need nu_t SEPARATELY from ``nu_eff_face``
        because their diffusivities are ``nu + alphaK nu_t`` /
        ``nu + alphaOmega nu_t``, not nu_eff.  Calling order inside one
        forward: the host calls ``nu_eff_face`` first (the single computation
        site); ``nut_face`` then returns the same per-forward tensor, so the
        momentum diffusivity and the transported diffusivities can never be
        built from two different nu_t evaluations.  The default returns
        ``None`` so Laminar and the test-only manufactured models need not
        implement it.
        """
        return None

    def alphat_wall_face(self, solver, state, alphat_face):
        """Turbulent thermal diffusivity per face with the model's WALL
        treatment applied, ``[.., N_face, 1]``.

        Called by the buoyant host's energy row as ``alphaEff = alpha +
        alphat_wall_face(solver, fvfield, nut_face/Prt)`` -- TEqn.H:2-5 of
        buoyantBoussinesqSimpleFoam: the interior ``alphat = nut/Prt`` and,
        on wall patches, whatever alphat BC the case runs
        (``alphat.correctBoundaryConditions()``, TEqn.H:3).  ``state`` is the
        BC-enforced fvfield, read after ``nu_eff_face`` this forward.

        Default: no wall treatment -- ``alphat_face`` is returned ITSELF (the
        same tensor object), so the energy diffusivity of every model that
        does not override this is bit-identical to ``alpha + nut_face/Prt``.
        RealizableKE overrides it (``wallAlphat = "jayatilleke"``).
        """
        return alphat_face

    def derived_gradient_operands(self, solver, state) -> dict:
        """OPTIONAL extra WLSQ passes on DERIVED cell fields (phase 3b).

        Some closures need gradients of fields that are functions of the
        state rather than the state itself -- Launder-Sharma's D term needs
        ``grad(sqrt(k))`` and its E term ``grad(grad U)``.  Stencil math
        stays in the host (design 3.1), so the model returns the CELL FIELDS
        to differentiate -- ``{name: tensor [.., N_cpd, C]}`` -- and the
        host runs ONE extra gradient pass over their concatenation and hands
        ``grad_<name>_int`` (``[.., N_int, C, 3]``) back through the
        ``transport_closures`` state dict.

        ``state`` carries cpd-level views: ``grad_u_cpd`` (``[.., N_cpd, 3,
        3]``, the unified WLSQ velocity gradient after the cyclic refill;
        non-cyclic ghost rows are ZERO -- pass it through
        ``solver.ghost_fill_owner`` before returning it as an operand) and
        ``<field>_cpd`` (``[.., N_cpd, w]``, BC-enforced) per TRANSPORTED
        field.

        Default {}: models that request nothing pay nothing -- the host
        skips the extra gradient pass entirely (call-counter gated,
        tests/test_derived_gradient_gating.py), so KOmega and Laminar
        forwards are unchanged by this hook's existence.
        """
        return {}

    @abstractmethod
    def transport_closures(self, solver, state) -> dict:
        """Per transported field: face diffusivity and per-cell source, as
        tensors the host's generic scalar-transport assembly consumes."""

    @abstractmethod
    def enforce_wall_bcs(self, solver, fvfield) -> dict:
        """Ghost-cell writes for the model's own fields -- plain
        Dirichlet/Neumann through the existing SetBC machinery; no row
        pinning (design section 2.4)."""


class Laminar(TurbulenceModel):
    """No turbulence: the discretisation's registered viscosity, untouched.

    ``[solver] turbulence = "laminar"`` (present in every live fvconfig) and
    an absent key both resolve here, so every existing case runs "the Laminar
    model" the moment the hook lands -- and the phase-1 hash gate proves that
    sentence changed nothing (design section 3.2).
    """

    TRANSPORTED: tuple = ()

    def nu_eff_face(self, solver, state) -> torch.Tensor:
        # Identity, not a copy: returning the registered tensor OBJECT makes
        # the laminar path's bit-identity structural rather than numerical.
        return solver.nu_coeff

    def is_uniform(self) -> bool:
        return True

    def transport_closures(self, solver, state) -> dict:
        return {}

    def enforce_wall_bcs(self, solver, fvfield) -> dict:
        return {}
