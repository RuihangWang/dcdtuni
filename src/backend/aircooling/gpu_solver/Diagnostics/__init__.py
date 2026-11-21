"""Diagnostics — torch-native analysis tools for FVM solution tensors.

Current modules:
    field_smoothness: three atomic smoothness / oscillation metrics operating
                      directly on cell-centred tensors (no VTM I/O).
"""
from .field_smoothness import (
    laplacian_rms,
    face_jump_rms,
    mono_violation_frac,
    all_metrics,
    build_interior_face_edge_index,
)

__all__ = [
    "laplacian_rms",
    "face_jump_rms",
    "mono_violation_frac",
    "all_metrics",
    "build_interior_face_edge_index",
]
