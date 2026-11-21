"""Surface-normal gradient (snGrad) kernels for diffusion discretization.

``sn_grad`` holds the OpenFOAM-style snGrad family (orthogonal, uncorrected,
corrected). This package-level module is the stable import surface consumed
by the FVInterpolation adapter.
"""
from .sn_grad import corrected_sn_grad, orthogonal_sn_grad

__all__ = [
    "corrected_sn_grad",
    "orthogonal_sn_grad",
]
