"""Field smoothness metrics — tensor-level online diagnostics.

See docs/superpowers/specs/2026-04-17-field-smoothness-metric-design.md for
design and math. All public functions run under torch.no_grad() and return
Python floats / lists of floats (never torch tensors) so outputs can be logged
to CSV without autograd concerns.
"""
from __future__ import annotations

import torch
from torch_geometric.utils import scatter


def laplacian_rms(
    phi: torch.Tensor,                   # [N_cpd] or [N_cpd, C]
    face_edge_index: torch.Tensor,        # [2, E_ii]
    mask_interior: torch.Tensor,          # [N_cpd] bool
    eps: float = 1e-12,
):
    """Normalised discrete Laplacian RMS. 0 for constant/linear fields on
    symmetric stencils; O(1)-O(10) for checkerboard. See spec §2.1.

    Returns a Python float if phi is 1D; a list[float] of length C if phi is 2D.
    """
    assert phi.dim() in (1, 2), f"phi must be 1D or 2D, got {phi.dim()}D"
    L, R = face_edge_index[0], face_edge_index[1]
    with torch.no_grad():
        # scatter_mean auto-broadcasts across trailing dims.
        nbr_mean = scatter(phi[R], L, dim=0, dim_size=phi.shape[0], reduce="mean")
        lap = phi - nbr_mean                                         # same shape as phi
        lap_I = lap[mask_interior]
        phi_I = phi[mask_interior]
        raw = torch.sqrt((lap_I ** 2).mean(dim=0))                   # scalar or [C]
        ref = phi_I.std(dim=0)                                        # scalar or [C]
        out = raw / (ref + eps)
        return out.item() if out.dim() == 0 else out.tolist()


def face_jump_rms(
    phi: torch.Tensor,                   # [N_cpd] or [N_cpd, C]
    face_edge_index: torch.Tensor,        # [2, E_ii]
    mask_interior: torch.Tensor,          # [N_cpd] bool (for ref normaliser)
    eps: float = 1e-12,
):
    """Normalised inter-cell jump RMS across interior-interior faces.
    See spec §2.2.

    Returns float for 1D input, list[float] for 2D.
    """
    assert phi.dim() in (1, 2), f"phi must be 1D or 2D, got {phi.dim()}D"
    L, R = face_edge_index[0], face_edge_index[1]
    with torch.no_grad():
        jump = phi[L] - phi[R]                                       # [E_ii] or [E_ii, C]
        raw = torch.sqrt((jump ** 2).mean(dim=0))                    # scalar or [C]
        ref = phi[mask_interior].std(dim=0)
        out = raw / (ref + eps)
        return out.item() if out.dim() == 0 else out.tolist()


def mono_violation_frac(
    phi: torch.Tensor,                   # [N_cpd] or [N_cpd, C]
    face_edge_index: torch.Tensor,        # [2, E_ii]
    mask_interior: torch.Tensor,          # [N_cpd] bool
):
    """Fraction of interior cells violating the discrete maximum principle
    (strictly greater than all face neighbours OR strictly less than all).
    See spec §2.3.

    Returns float for 1D input, list[float] for 2D.

    Assumes every interior cell has >=1 interior-interior face neighbour (spec §2.5);
    otherwise scatter_max / scatter_min empty-bucket defaults (-inf/+inf) would
    silently flag that cell as a violation.
    """
    assert phi.dim() in (1, 2), f"phi must be 1D or 2D, got {phi.dim()}D"
    L, R = face_edge_index[0], face_edge_index[1]
    with torch.no_grad():
        nbr_max = scatter(phi[R], L, dim=0, dim_size=phi.shape[0], reduce="max")
        nbr_min = scatter(phi[R], L, dim=0, dim_size=phi.shape[0], reduce="min")
        over = phi > nbr_max
        under = phi < nbr_min
        viol = (over | under)                                        # [N_cpd] or [N_cpd, C]
        viol_I = viol[mask_interior].to(torch.float32)
        out = viol_I.mean(dim=0)
        return out.item() if out.dim() == 0 else out.tolist()


def all_metrics(
    phi_cpd_cell: torch.Tensor,           # [N_cpd, C]
    face_edge_index: torch.Tensor,
    mask_interior: torch.Tensor,
    var_names: list[str],
    eps: float = 1e-12,
) -> dict[str, float]:
    """Run all three atomic metrics once each (vectorised across C) and unpack
    into a flat dict {'<var>_lap', '<var>_jump', '<var>_mono'}. Suitable for
    **-expansion into logger.log_residuals. See spec §4.2.
    """
    assert phi_cpd_cell.dim() == 2, (
        f"all_metrics expects a 2D [N_cpd, C] tensor, got {phi_cpd_cell.dim()}D"
    )
    C = phi_cpd_cell.shape[1]
    if len(var_names) != C:
        raise ValueError(
            f"var_names has length {len(var_names)} but phi_cpd_cell has "
            f"{C} channels — must match exactly."
        )
    laps = laplacian_rms(phi_cpd_cell, face_edge_index, mask_interior, eps)
    jumps = face_jump_rms(phi_cpd_cell, face_edge_index, mask_interior, eps)
    monos = mono_violation_frac(phi_cpd_cell, face_edge_index, mask_interior)

    # When C == 1, atomic functions return a plain float, not a list. Normalise.
    def _as_list(x):
        return x if isinstance(x, list) else [x]
    laps, jumps, monos = _as_list(laps), _as_list(jumps), _as_list(monos)

    out: dict[str, float] = {}
    for i, name in enumerate(var_names):
        out[f"{name}_lap"] = laps[i]
        out[f"{name}_jump"] = jumps[i]
        out[f"{name}_mono"] = monos[i]
    return out


def checkerboard_loss_cb(
    phi: torch.Tensor,
    face_ei_compact: torch.Tensor,
    face_ei_wide: torch.Tensor,
    mask_interior: torch.Tensor,
    eps: float = 1e-12,
) -> float:
    """Checkerboard coefficient loss based on Hopman et al. (2025).

    Computes (L - Lc) operator: difference between compact-stencil and
    wide-stencil Laplacians. Smooth/physical fields produce near-zero
    values; checkerboard modes produce large values.

    Args:
        phi: [N_cpd, C] field values
        face_ei_compact: [2, E1] bidirectional 1-hop interior edges
        face_ei_wide: [2, E2] bidirectional 2-hop interior edges
        mask_interior: [N_cpd] bool
        eps: numerical stability

    Returns:
        Scalar loss (mean squared checkerboard signal over interior cells)
    """
    with torch.no_grad():
        L1, R1 = face_ei_compact[0], face_ei_compact[1]
        L2, R2 = face_ei_wide[0], face_ei_wide[1]

        nbr_mean_compact = scatter(phi[R1], L1, dim=0, dim_size=phi.shape[0], reduce="mean")
        nbr_mean_wide = scatter(phi[R2], L2, dim=0, dim_size=phi.shape[0], reduce="mean")

        lap_compact = phi - nbr_mean_compact
        lap_wide = phi - nbr_mean_wide

        cb_signal = (lap_compact - lap_wide)[mask_interior]

        ref = phi[mask_interior].std(dim=0)
        cb_normalized = cb_signal / (ref + eps)

        return float((cb_normalized ** 2).mean().item())


def build_interior_face_edge_index(
    neighbor_cpd_cell: torch.Tensor,   # [2, F] unidirectional face-level (L, R) pairs
    mask_interior: torch.Tensor,        # [N_cpd] bool
) -> torch.Tensor:
    """Return [2, E_ii] bidirectional long tensor of interior-interior face pairs.

    Matches the spec: each physical face contributes two directed edges (L,R) and
    (R,L), so scatter_*(phi[R], L) naturally collects each cell's face neighbours.
    Boundary faces (one side not interior) are dropped.
    """
    assert neighbor_cpd_cell.dim() == 2 and neighbor_cpd_cell.shape[0] == 2, (
        f"neighbor_cpd_cell must be [2, F], got {tuple(neighbor_cpd_cell.shape)}"
    )
    assert mask_interior.dtype == torch.bool, (
        f"mask_interior must be bool, got {mask_interior.dtype}"
    )
    L, R = neighbor_cpd_cell[0], neighbor_cpd_cell[1]
    both_interior = mask_interior[L] & mask_interior[R]
    uni = neighbor_cpd_cell[:, both_interior].to(torch.long)
    bidir = torch.cat([uni, uni.flip(0)], dim=1)
    return bidir
