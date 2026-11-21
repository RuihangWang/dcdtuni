"""Classical learned separable linear-operator control for the fixed thermal task.

No exact sine modes, coefficients or transfer functions enter the model. The
linearity prior is supplied deliberately; spatial boundary/query bases are
learned from the same deterministic coordinate features as the other controls.
This is a diagnostic control, not a new SCANO mechanism.
"""
import math
import torch
from torch import nn


class LinearDeepONet(nn.Module):
    """Area-integrated BC moments contracted with a learned pointwise query basis.

    width is the latent dimension (and upper bound on the separable kernel rank).
    For fixed geometry/weights the output is exactly linear in boundary_value.
    Weights are normalized to the top-face area convention shared by the other
    controlled models. There is no additive output bias or BC-dependent basis.
    """
    def __init__(self, width=128):
        super().__init__()
        if not isinstance(width, int) or width < 1:
            raise ValueError('width must be a positive integer')
        self.width = width
        self.boundary_basis = nn.Sequential(
            nn.Linear(35, width), nn.SiLU(), nn.Linear(width, width),
            nn.SiLU(), nn.Linear(width, width))
        self.query_basis = nn.Sequential(
            nn.Linear(35, width), nn.SiLU(), nn.Linear(width, width),
            nn.SiLU(), nn.Linear(width, width))

    def forward(self, query, xy, boundary_value, physical_weights, mode='measure'):
        """[B,Q,3], [B,N,2], [B,N,1], [B,N] -> normalized scalar [B,Q,1]."""
        # Local import also permits models.build_model to import this class.
        from .models import features
        if mode != 'measure':
            raise ValueError('LinearDeepONet uses physical measure aggregation only')
        boundary_pos = torch.cat([xy, torch.ones_like(xy[..., :1])], dim=-1)
        phi = self.boundary_basis(features(boundary_pos))
        weights = physical_weights / physical_weights.sum(dim=1, keepdim=True)
        moments = (phi * boundary_value * weights[..., None]).sum(dim=1)
        psi = self.query_basis(features(query))
        return (psi * moments[:, None, :]).sum(dim=-1, keepdim=True) / math.sqrt(self.width)
