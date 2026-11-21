"""LFE as a boundary route (Learned Field Extension, ICML 2026; sprmsv/olbc@82094ff, MIT).

The extender is scano.NNmodels.lfe_adapter.LFEExtender, the torch port checked against the official Flax
outputs and input gradients (tests/test_lfe_adapter.py, outputs/lfe_port_parity_v2). As in LFE, it acts once,
at the input: every point's input features attend to all boundary records of the case (count measure as
upstream, or arc-length weights as logit bias), and the resulting extension is concatenated with the lifted
point features and fused back to the host width.

Difference from the official LX(R)/LX(G): the queries are the host's points, not RIGNO region nodes or GAOT
latent tokens (docs/ref_code_analysis/C_lfe_rigno_gaot.md section 2.3); the extension has no global state, so
it never depends on which other points are passed.
"""
from __future__ import annotations

import torch
from torch import nn

from ..lfe_adapter import LFEExtender


class LFEInput(nn.Module):
    def __init__(self, node_in, bnd_in, width, latent=64, heads=2, depth=4, out=8, measure='count', p_masking=0.0):
        super().__init__()
        self.measure = measure
        self.extender = LFEExtender(b_features=bnd_in, q_features=node_in, latent=latent, heads=heads, depth=depth,
                                    out=out, p_masking=p_masking)
        self.fuse = nn.Linear(width + out, width)

    def forward(self, h, node_feat, bnd_feat, bnd_w):
        """h: [B,N,W] lifted point features; node_feat: [B,N,F]; bnd_feat: [B,NB,Fb]; bnd_w: [B,NB] > 0."""
        mask = torch.ones(bnd_feat.shape[:2], dtype=torch.bool, device=bnd_feat.device)
        ext = self.extender(bnd_feat, node_feat, mask, weights=bnd_w if self.measure == 'arc' else None)
        return self.fuse(torch.cat([h, ext], -1))
