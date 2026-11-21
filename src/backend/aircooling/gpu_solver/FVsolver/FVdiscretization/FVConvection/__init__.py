"""Numerical convection schemes organized by family.

``face_interpolation`` holds cell-to-face reconstruction kernels;
``compressible`` holds conservative Euler-flux kernels. This package-level
module re-exports both for external/test consumers; the FVflux/
FVInterpolation adapters import from the family subpackages directly
(``FVConvection.compressible`` / ``FVConvection.face_interpolation``), not
from here. ``face_interpolation``'s ``_common`` helpers (``batched_matvec``,
``select_upwind_state``, ``apply_boundary_face_values``) are internal surface
and are re-exported at the ``face_interpolation`` package level only.
"""
from .compressible import (
    compute_cell_lambda,
    compute_pressure_sensor,
    compute_undivided_laplacian,
    create_two_way_boundary_mask,
    hllc_riemann_flux,
    jst_flux,
    primitive_to_conservative,
    roe_riemann_flux,
)
from .face_interpolation import (
    limited_linear_face_value,
    limited_linear_v_face_value,
    linear_corrected_face_value,
    linear_face_value,
    linear_grad_face_value,
    linear_upwind_face_value,
    quick_face_value,
    second_order_upwind_face_value,
    upwind_face_value,
)

__all__ = [
    "compute_cell_lambda",
    "compute_pressure_sensor",
    "compute_undivided_laplacian",
    "create_two_way_boundary_mask",
    "hllc_riemann_flux",
    "jst_flux",
    "limited_linear_face_value",
    "limited_linear_v_face_value",
    "linear_corrected_face_value",
    "linear_face_value",
    "linear_grad_face_value",
    "linear_upwind_face_value",
    "primitive_to_conservative",
    "quick_face_value",
    "roe_riemann_flux",
    "second_order_upwind_face_value",
    "upwind_face_value",
]
