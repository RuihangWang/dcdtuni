"""Deviatoric-stress tensor algebra: ONE implementation, three consumers.

OpenFOAM's incompressible momentum equation carries the viscous stress as
``laplacian(nuEff, U) + div(nuEff * dev2(T(grad U)))``
(Ref_code/openfoam ``linearViscousStress.C:78-106``); our solvers model the
laplacian half via ``diffusion_flux`` and, until phase 2 of the turbulence
design (docs/superpowers/specs/2026-08-16-turbulence-framework-design.md,
section 2.5), omitted the explicit transpose/deviatoric half.  Under constant
nu that omission is analytically divergence-free; under a variable nu_eff(x)
the missing ``(grad U)^T . grad nu_eff - (2/3)(div U) grad nu_eff`` is the
same order as the resolved part, so every shear layer would be wrong.

The design (section 2.5 + addendum A.8 phase 2) requires the stress algebra
to be written ONCE and consumed THREE times, so the three consumers cannot
disagree about the stress tensor:

1. momentum dev2 face flux  ``div(nu_eff dev2((grad U)^T))``
   -> :func:`dev2_transpose_face_flux`, added to the viscous face flux in the
   ext solvers when the active turbulence model declares non-uniform nu_eff;
2. k-production contraction ``G = nu_t * (grad U : dev2symm(grad U))``
   (OpenFOAM ``turbulenceModel::GbyNu``: ``dev(twoSymm(gradU)) && gradU``)
   -> ``nu_t * stress_double_inner(grad_u)``, consumed by phase 3;
3. force extraction ``devRhoReff = -rho * nu_eff * dev(twoSymm(grad U))``
   -> :func:`dev_reff` (kinematic; multiply by rho outside for OpenFOAM's
   ``devRhoReff``).

Layout convention (the whole file hinges on it): every gradient tensor here
is ``grad[..., c, d] = d(u_c)/d(x_d)`` -- component rows, derivative columns,
exactly what ``FVgrad.gradient_reconstruction`` and
``FVInterpolation.interpolating_gradients_to_faces`` produce for the velocity
channels.  OpenFOAM's ``fvc::grad(U)`` has components ``d(u_j)/d(x_i)``, so
our tensor IS OpenFOAM's ``T(fvc::grad(U))`` and ``Foam::dev2(T(grad U)) =
grad_here^T - (2/3) tr(grad_here) I`` (``dev2(M) = M - (2/3) tr(M) I``).

These are pure per-entity tensor contractions: no stencil, no scatter, no
mesh knowledge beyond the face normal a caller hands in.  Discretisation
(interpolation to faces, boundary closure, divergence scatter) stays in the
consumers; physics closures (which nu_eff, which nu_t) stay in
``FVsolver/Turbulence``.  The Launder-Sharma E term (addendum A.4) will feed
gradient-of-gradient tensors through the same entry points -- nothing here
assumes the input is a VELOCITY gradient, only its ``[..., 3, 3]`` layout.

Verification: exact-algebra + fp64 gradcheck in
``tests/test_stress_ops.py``; MMS operator orders on the hex family in
``tests/test_stress_mms_order.py`` (design section 9, rows 1-3).
"""

import torch

_TWO_THIRDS = 2.0 / 3.0


def dev2_transpose(grad: torch.Tensor) -> torch.Tensor:
    """``dev2(grad^T) = grad^T - (2/3) tr(grad) I`` for ``grad [..., 3, 3]``.

    With ``grad = T(fvc::grad U)`` (see module docstring) this is exactly the
    tensor OpenFOAM interpolates and divergences in ``linearViscousStress.C``.
    """
    trace = grad.diagonal(dim1=-2, dim2=-1).sum(dim=-1)          # [...]
    eye = torch.eye(grad.shape[-1], dtype=grad.dtype, device=grad.device)
    return grad.transpose(-1, -2) - _TWO_THIRDS * trace[..., None, None] * eye


def dev_two_symm(grad: torch.Tensor) -> torch.Tensor:
    """``dev(twoSymm(grad)) = grad + grad^T - (2/3) tr(grad) I``.

    The full deviatoric rate-of-strain factor: symmetric, and identical
    whether built from ``grad`` or ``grad^T`` -- which is why the momentum
    stress, the production term and the wall force cannot disagree when they
    all come through here.  Equals ``grad + dev2_transpose(grad)``.
    """
    return grad + dev2_transpose(grad)


def dev2_transpose_face_flux(grad_face: torch.Tensor,
                             unv_face: torch.Tensor) -> torch.Tensor:
    """Face-normal flux of ``dev2(grad^T)``: ``dev2(grad_f^T) @ n_f``.

    Args:
        grad_face: face gradient tensor ``[..., N_face, 3, 3]`` in the
            module's ``[c, d]`` layout (component, derivative).
        unv_face: unit face normals ``[N_face, 3]`` (or broadcastable).

    Returns:
        ``[..., N_face, 3]`` -- per-face momentum flux density; multiply by a
        per-face diffusivity (``diffusion_flux``) and scatter with the signed
        face areas exactly like the laplacian's viscous flux.  Component i is
        ``sum_c grad[c, i] n_c - (2/3) (sum_c grad[c, c]) n_i``.

    Written as direct contractions rather than materialising the ``[3, 3]``
    tensor; ``tests/test_stress_ops.py`` pins it against the matvec of
    :func:`dev2_transpose` to fp64 roundoff.
    """
    # sum over the COMPONENT axis c: (grad^T @ n)_i = sum_c grad[c, i] n_c
    transpose_flux = (grad_face * unv_face[..., :, None]).sum(dim=-2)
    div_u = grad_face.diagonal(dim1=-2, dim2=-1).sum(dim=-1, keepdim=True)
    return transpose_flux - _TWO_THIRDS * div_u * unv_face


def stress_double_inner(grad: torch.Tensor) -> torch.Tensor:
    """``grad : dev(twoSymm(grad))`` -- OpenFOAM's ``GbyNu`` contraction.

    k-production is ``G = nu_t * stress_double_inner(grad_u)`` (kOmega.C /
    kEpsilon.C: ``G = nut * (dev(twoSymm(tgradU())) && tgradU())``; design
    section 3.3 requires it to share the stress tensor with the momentum
    residual).  The contraction is invariant under transposing ``grad``, so
    the OpenFOAM/our layout difference is immaterial here.

    Returns ``[..., 1]`` for input ``[..., 3, 3]``.
    """
    return (dev_two_symm(grad) * grad).sum(dim=(-2, -1))[..., None]


def dev_reff(nu_eff: torch.Tensor, grad: torch.Tensor) -> torch.Tensor:
    """Kinematic deviatoric effective stress ``-nu_eff * dev(twoSymm(grad))``.

    OpenFOAM's ``devRhoReff`` (``linearViscousStress.C:47-56``) divided by
    rho; the tensor wall-force extraction integrates as
    ``rho * (dev_reff @ n) * |S_f|`` over wall faces.  ``nu_eff`` is
    ``[..., 1]`` per entity (cell or face), broadcast over the tensor.
    """
    return -nu_eff[..., None] * dev_two_symm(grad)
