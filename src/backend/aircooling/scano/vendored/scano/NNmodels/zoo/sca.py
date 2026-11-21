"""SCA: the two-stream model of the AAAI-27 manuscript (refine TextCondTrans_v2), dense single-case path.

Oracle: scano.NNmodels.vendor.frozen_sca.TransFVGN.TextCondTrans_v2 (identical to Gen-FVGN-refine's
src/NNmodels/TransFVGN/TextCondTrans_v2.py up to one docstring line; docs/ref_code_analysis/E_refine_baselines_sca.md).
Parameter names and creation order are the oracle's, so the same seed gives the same parameters and oracle
checkpoints load (tests/test_zoo_sca.py).

Structure (as the oracle): an interior stream (lift + placeholder) and a boundary stream (lift over all
boundary records) are each pooled into slice tokens with their own projections and temperatures
(softplus + 1e-4, scores clamped to +-30); interior tokens attend to boundary tokens once (32 x 32 per head,
dots clamped to +-50); the result is desliced with the interior slice weights, added to the interior
features and LayerNorm-ed; then a plain Transolver trunk and an LN + Linear decoder.

Changes, each an identity at the oracle setting (context weights 1, count boundary measure, eps abs1e-5):
  1. every slice pooling (interior pooling and each trunk block) takes the context weights ctx_w, so
     w = area gives quadrature and w = 0 removes a point from the global state (decoupled tau*);
  2. the boundary pooling can weight records by arc length/area (boundary.bridge_measure = 'arc'; the weights
     are normalised to mean 1 so that the additive eps weighs as in count pooling);
  3. the slice denominator follows context.eps (slice_operator.EPS);
  4. one case per forward (B = 1 dense path only); no dropout, no gradient checkpointing, no autocast.
refine's Simulator re-initialises every Linear with trunc_normal(0.02) after construction (E section 4, item 9);
that is not reproduced here: the parameters are initialised as the class itself does.
"""
from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F

from .slice_operator import EPS, slice_denominator
from .transolver import MLP


def _pool(fx, xp, w, temperature, projector, heads, dim_head, eps, clamp=None):
    """Weighted slice pooling. fx, xp: [B,N,H*D]; w: [B,N]; returns slice weights [B,H,N,G], tokens [B,H,G,D]."""
    b, n, _ = fx.shape
    fx_mid = fx.view(b, n, heads, dim_head).permute(0, 2, 1, 3)
    x_mid = xp.view(b, n, heads, dim_head).permute(0, 2, 1, 3)
    scores = projector(x_mid) / temperature
    if clamp is not None:
        scores = scores.clamp(min=-clamp, max=clamp)
    s = F.softmax(scores, dim=-1)                                       # [B,H,N,G]
    sw = s * w[:, None, :, None]
    token = torch.einsum('bhnd,bhng->bhgd', fx_mid, sw)
    return s, token / slice_denominator(sw.sum(2), w, eps)[..., None]


class GraphPhysicsAttention(nn.Module):
    """Graph_Physics_Attention_1D (dense path) with context weights."""

    def __init__(self, dim, heads, dim_head, slice_num, eps):
        super().__init__()
        inner = dim_head * heads
        self.dim_head, self.heads, self.scale, self.eps = dim_head, heads, dim_head ** -0.5, EPS[eps]
        self.softmax = nn.Softmax(dim=-1)
        self.dropout = nn.Dropout(0.0)
        self.graph_temperature = nn.Parameter(torch.ones([1, heads, 1]) * 0.5)
        self.in_project_x = nn.Linear(dim, inner)
        self.in_project_fx = nn.Linear(dim, inner)
        self.in_project_slice = nn.Linear(dim_head, slice_num)
        torch.nn.init.orthogonal_(self.in_project_slice.weight)
        self.to_qkv = nn.Linear(dim_head, dim_head * 3, bias=False)
        self.to_out = nn.Sequential(nn.Linear(inner, dim), nn.Dropout(0.0))

    def forward(self, x, ctx_w):
        temp = F.softplus(self.graph_temperature).unsqueeze(-2) + 1e-4
        s, token = _pool(self.in_project_fx(x), self.in_project_x(x), ctx_w, temp, self.in_project_slice,
                         self.heads, self.dim_head, self.eps)
        q, k, v = self.to_qkv(token).chunk(3, dim=-1)
        out = torch.matmul(self.softmax(torch.matmul(q, k.transpose(-1, -2)) * self.scale), v)
        out_x = torch.einsum('bhgd,bhng->bhnd', out, s)
        b, h, n, d = out_x.shape
        return self.to_out(out_x.permute(0, 2, 1, 3).reshape(b, n, h * d))


class Block(nn.Module):
    """Transolver_block (GraphTransolver) with context weights."""

    def __init__(self, heads, hidden, mlp_ratio, slice_num, eps):
        super().__init__()
        self.ln_1 = nn.LayerNorm(hidden)
        self.Attn = GraphPhysicsAttention(hidden, heads, hidden // heads, slice_num, eps)
        self.ln_2 = nn.LayerNorm(hidden)
        self.mlp = MLP(hidden, hidden * mlp_ratio, hidden)

    def forward(self, fx, ctx_w):
        fx = self.Attn(self.ln_1(fx), ctx_w) + fx
        return self.mlp(self.ln_2(fx)) + fx


class SCA(nn.Module):
    def __init__(self, node_in, bnd_in, width=128, depth=4, heads=4, slices=32, mlp_ratio=2, eps='abs1e-5',
                 channel_measure='count', out_dim=1):
        super().__init__()
        self.hidden_dim, self.num_heads, self.dim_head = width, heads, width // heads
        self.scale = self.dim_head ** -0.5
        self.eps, self.channel_measure = EPS[eps], channel_measure
        self.internal_lift = MLP(node_in, width * 2, width)
        self.boundary_lift = MLP(bnd_in, width * 2, width)
        self.placeholder = nn.Parameter((1.0 / width) * torch.rand(width))
        self.boundary_graph_temperature = nn.Parameter(torch.ones([1, heads, 1]) * 0.5)
        self.boundary_in_project_merged = nn.Linear(width, width * 2)
        self.boundary_in_project_slice = nn.Linear(self.dim_head, slices)
        torch.nn.init.orthogonal_(self.boundary_in_project_slice.weight)
        self.internal_graph_temperature = nn.Parameter(torch.ones([1, heads, 1]) * 0.5)
        self.internal_in_project_merged = nn.Linear(width, width * 2)
        self.internal_in_project_slice = nn.Linear(self.dim_head, slices)
        torch.nn.init.orthogonal_(self.internal_in_project_slice.weight)
        self.to_q = nn.Linear(self.dim_head, self.dim_head, bias=False)
        self.to_kv = nn.Linear(self.dim_head, self.dim_head * 2, bias=False)
        self.to_out = nn.Sequential(nn.Linear(width, width), nn.Dropout(0.0))
        self.norm = nn.LayerNorm(width)
        self.blocks = nn.ModuleList([Block(heads, width, mlp_ratio, slices, eps) for _ in range(depth)])
        self.decoder = nn.Sequential(nn.LayerNorm(width), nn.Linear(width, out_dim))

    def forward(self, node_feat, ctx_w, bnd_feat, bnd_w):
        h_int = self.internal_lift(node_feat) + self.placeholder[None, None, :]
        h_bnd = self.boundary_lift(bnd_feat)
        bnd_fx, bnd_xp = self.boundary_in_project_merged(h_bnd).chunk(2, dim=-1)
        int_fx, int_xp = self.internal_in_project_merged(h_int).chunk(2, dim=-1)
        bnd_temp = (F.softplus(self.boundary_graph_temperature) + 1e-4).unsqueeze(-2)
        int_temp = (F.softplus(self.internal_graph_temperature) + 1e-4).unsqueeze(-2)
        # arc weights normalised to mean 1, so the fixed additive eps weighs as it does for count pooling
        w_b = bnd_w / bnd_w.mean(-1, keepdim=True) if self.channel_measure == 'arc' else torch.ones_like(bnd_w)
        _, bnd_token = _pool(bnd_fx, bnd_xp, w_b, bnd_temp, self.boundary_in_project_slice,
                             self.num_heads, self.dim_head, self.eps, clamp=30.0)
        int_s, int_token = _pool(int_fx, int_xp, ctx_w, int_temp, self.internal_in_project_slice,
                                 self.num_heads, self.dim_head, self.eps, clamp=30.0)
        q = self.to_q(int_token)
        k, v = self.to_kv(bnd_token).chunk(2, dim=-1)
        attn = F.softmax((torch.matmul(q, k.transpose(-1, -2)) * self.scale).clamp(min=-50.0, max=50.0), dim=-1)
        out_x = torch.einsum('bhgd,bhng->bhnd', torch.matmul(attn, v), int_s)
        b, h, n, d = out_x.shape
        hid = self.norm(h_int + self.to_out(out_x.permute(0, 2, 1, 3).reshape(b, n, h * d)))
        for blk in self.blocks:
            hid = blk(hid, ctx_w)
        out = self.decoder(hid)
        return out.squeeze(-1) if out.shape[-1] == 1 else out      # [B,N] for out_dim 1, else [B,N,out_dim]
