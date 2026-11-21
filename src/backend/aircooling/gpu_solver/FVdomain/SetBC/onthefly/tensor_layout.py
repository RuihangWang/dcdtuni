"""Tensor layout helpers for on-the-fly boundary conditions.

Runtime boundary fields use an entity axis immediately before their channel
axis.  Consequently both the legacy ``[N, C]`` layout and transient
``[..., N, C]`` layouts can be indexed without ever touching a leading time
or batch axis.  Compound-cell gradients have one additional trailing spatial
direction axis and therefore use ``[..., N, C, D]``.
"""

from __future__ import annotations

import torch


def field_get(field: torch.Tensor, index) -> torch.Tensor:
    """Select cells/faces from ``[..., N, C]`` preserving leading axes."""
    return field[..., index, :]


def field_set_(field: torch.Tensor, index, value) -> torch.Tensor:
    """Assign cells/faces in ``[..., N, C]`` preserving leading axes."""
    field[..., index, :] = value
    return field


def gradient_get(gradient: torch.Tensor, index) -> torch.Tensor:
    """Select cells from ``[..., N, C, D]`` preserving leading axes.

    Legacy BC tests and a few older solver paths store a flattened gradient as
    ``[N, C*D]``.  That rank-2 representation remains supported.
    """
    if gradient.ndim == 2:
        return gradient[index, :]
    return gradient[..., index, :, :]


def gradient_set_(gradient: torch.Tensor, index, value) -> torch.Tensor:
    """Assign cells in ``[..., N, C, D]`` (or legacy ``[N, C*D]``)."""
    if gradient.ndim == 2:
        gradient[index, :] = value
        return gradient
    gradient[..., index, :, :] = value
    return gradient
