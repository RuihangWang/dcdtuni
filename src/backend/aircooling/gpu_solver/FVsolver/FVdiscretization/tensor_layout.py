"""Tensor-layout helpers shared by finite-volume discretization primitives.

The spatial operators in this package accept both the legacy layouts
``[N, C]`` / ``[N, C, D]`` and layouts with leading batch dimensions, in
particular the transient layout ``[T, N, C]`` / ``[T, N, C, D]``.  ``N`` is
always the penultimate (field) or antepenultimate (gradient) axis.  Geometry
is static and is therefore never repeated over the leading dimensions.

For linear spatial reconstruction, leading dimensions and channels can be
packed into one multiple-right-hand-side axis.  This is both cheaper than
duplicating a graph for every time level and mathematically identical to
running the same static spatial operator independently at every time level.
"""

from __future__ import annotations

from typing import Tuple

import torch


FieldLayout = Tuple[torch.Size, int]


def pack_field_rhs(field: torch.Tensor) -> tuple[torch.Tensor, FieldLayout]:
    """Pack ``[..., N, C]`` into ``[N, prod(...)*C]``.

    The returned layout is consumed by :func:`unpack_field_rhs` and
    :func:`unpack_gradient_rhs`.
    """
    if field.ndim < 2:
        raise ValueError(f"field must have shape [..., N, C], got {tuple(field.shape)}")
    leading = field.shape[:-2]
    channels = int(field.shape[-1])
    n_entities = int(field.shape[-2])
    if not leading:
        return field, (leading, channels)
    packed = field.movedim(-2, 0).reshape(n_entities, -1)
    return packed, (leading, channels)


def unpack_field_rhs(field: torch.Tensor, layout: FieldLayout) -> torch.Tensor:
    """Undo :func:`pack_field_rhs` for an output ``[N, packed_channels]``."""
    leading, channels = layout
    if not leading:
        return field
    n_entities = int(field.shape[0])
    restored = field.reshape(n_entities, *leading, channels)
    # [N, *leading, C] -> [*leading, N, C]
    return restored.permute(*range(1, 1 + len(leading)), 0, 1 + len(leading))


def pack_gradient_rhs(gradient: torch.Tensor) -> tuple[torch.Tensor, FieldLayout]:
    """Pack ``[..., N, C, D]`` into ``[N, prod(...)*C, D]``."""
    if gradient.ndim < 3:
        raise ValueError(
            f"gradient must have shape [..., N, C, D], got {tuple(gradient.shape)}"
        )
    leading = gradient.shape[:-3]
    channels = int(gradient.shape[-2])
    n_entities = int(gradient.shape[-3])
    if not leading:
        return gradient, (leading, channels)
    packed = gradient.movedim(-3, 0).reshape(n_entities, -1, gradient.shape[-1])
    return packed, (leading, channels)


def unpack_gradient_rhs(gradient: torch.Tensor, layout: FieldLayout) -> torch.Tensor:
    """Undo RHS packing for ``[N, packed_channels, D]`` gradients."""
    leading, channels = layout
    if not leading:
        return gradient
    n_entities = int(gradient.shape[0])
    spatial_dim = int(gradient.shape[-1])
    restored = gradient.reshape(n_entities, *leading, channels, spatial_dim)
    # [N, *leading, C, D] -> [*leading, N, C, D]
    return restored.permute(
        *range(1, 1 + len(leading)),
        0,
        1 + len(leading),
        2 + len(leading),
    )


def pack_scalar_for_layout(
    scalar: torch.Tensor,
    layout: FieldLayout,
    *,
    channels: int | None = None,
) -> torch.Tensor:
    """Pack ``[..., N, 1]`` and repeat it across packed field channels.

    This is used for a per-time scalar face flux controlling a multi-channel
    interpolation.  ``layout`` must describe the field being interpolated.
    """
    leading, field_channels = layout
    field_channels = field_channels if channels is None else channels
    if not leading:
        return scalar
    if scalar.shape[:-2] != leading or scalar.shape[-1] != 1:
        raise ValueError(
            "scalar leading dimensions must match the field and its last axis "
            f"must be one; got scalar={tuple(scalar.shape)}, leading={tuple(leading)}"
        )
    n_entities = int(scalar.shape[-2])
    packed = scalar.movedim(-2, 0)  # [N, *leading, 1]
    packed = packed.expand(n_entities, *leading, field_channels)
    return packed.reshape(n_entities, -1)


def gather_entities(field: torch.Tensor, index: torch.Tensor, *, gradient: bool = False) -> torch.Tensor:
    """Gather entities while preserving every leading dimension."""
    dim = -3 if gradient else -2
    return field.index_select(dim, index)


def geometry_for(field: torch.Tensor, geometry: torch.Tensor, *, trailing_dims: int) -> torch.Tensor:
    """Add singleton leading axes so static geometry broadcasts with ``field``.

    ``trailing_dims`` counts geometry axes after its entity axis.  Examples:
    a vector geometry ``[N, D]`` has ``trailing_dims=1`` and a scalar geometry
    ``[N, 1]`` also has ``trailing_dims=1``.
    """
    field_entity_dim = field.ndim - trailing_dims - 1
    n_leading = field_entity_dim
    return geometry.reshape((1,) * n_leading + tuple(geometry.shape))
