"""Transolver++ (Luo et al., ICML 2025) adapted from the MIT-licensed upstream (thuml/Transolver_plus@d5a23bc,
models/Transolver_plus.py; verbatim copy and licence in scano/NNmodels/vendor/transolver_plus/).

Upstream's two changes to Transolver: Ada-Temp (a per-point temperature from a small MLP plus a learned bias,
clamped at 0.01) and Rep-Slice (slice weights from a Gumbel-softmax of the slice logits); the slice tokens pool
the projected point features themselves (no separate fx projection).

Changes to upstream (tests/test_zoo_transolver_pp.py checks parity with the vendored code):
  1. context weights in the slice sums, as in zoo/transolver.py (w = 1, eps abs1e-5 is upstream);
  2. Gumbel noise only in training by default; upstream also draws it at inference, so two evaluations of the
     same input differ (analysis A section 2), which a canary cannot pass. `gumbel_eval=True` restores upstream;
  3. optional SCANO boundary channel per block, created after all upstream parameters (as zoo/transolver.py);
  4. no activation checkpointing, no torch.distributed (upstream's all_reduce results are discarded, so they
     never change the output), no unified_pos, no condition input (upstream's `embedding` Linear(3, W) is kept
     so that parameters and their initialisation match upstream; it is unused, as upstream without a condition);
     timm's trunc_normal_ replaced by torch's.
"""
from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F

from .slice_operator import EPS, BoundaryAttention, mlp, slice_denominator
from .transolver import MLP


def gumbel_softmax(logits, tau, noise=True):
    """upstream's gumbel_softmax (soft), with the noise optional."""
    y = logits
    if noise:
        u = torch.rand_like(logits)
        y = logits + -torch.log(-torch.log(u + 1e-8) + 1e-8)
    return F.softmax(y / tau, dim=-1)


class EideticAttention(nn.Module):
    """upstream Physics_Attention_1D_Eidetic with context weights."""

    def __init__(self, dim, heads, dim_head, slice_num, eps, gumbel_eval=False):
        super().__init__()
        inner = dim_head * heads
        self.dim_head, self.heads, self.scale = dim_head, heads, dim_head ** -0.5
        self.eps, self.gumbel_eval = EPS[eps], gumbel_eval
        self.softmax = nn.Softmax(dim=-1)
        self.dropout = nn.Dropout(0.0)
        self.bias = nn.Parameter(torch.ones([1, heads, 1, 1]) * 0.5)
        self.proj_temperature = nn.Sequential(nn.Linear(dim_head, slice_num), nn.GELU(), nn.Linear(slice_num, 1),
                                              nn.GELU())
        self.in_project_x = nn.Linear(dim, inner)
        self.in_project_slice = nn.Linear(dim_head, slice_num)
        torch.nn.init.orthogonal_(self.in_project_slice.weight)
        self.to_q = nn.Linear(dim_head, dim_head, bias=False)
        self.to_k = nn.Linear(dim_head, dim_head, bias=False)
        self.to_v = nn.Linear(dim_head, dim_head, bias=False)
        self.to_out = nn.Sequential(nn.Linear(inner, dim), nn.Dropout(0.0))

    def forward(self, x, ctx_w):
        B, N, _ = x.shape
        x_mid = self.in_project_x(x).reshape(B, N, self.heads, self.dim_head).permute(0, 2, 1, 3).contiguous()
        temperature = torch.clamp(self.proj_temperature(x_mid) + self.bias, min=0.01)
        s = gumbel_softmax(self.in_project_slice(x_mid), temperature, noise=self.training or self.gumbel_eval)
        sw = s * ctx_w[:, None, :, None]
        token = torch.einsum('bhnc,bhng->bhgc', x_mid, sw).contiguous()
        token = token / slice_denominator(sw.sum(2), ctx_w, self.eps)[:, :, :, None]
        out = F.scaled_dot_product_attention(self.to_q(token), self.to_k(token), self.to_v(token))
        out_x = torch.einsum('bhgc,bhng->bhnc', out, s)
        return self.to_out(out_x.permute(0, 2, 1, 3).reshape(B, N, self.heads * self.dim_head))


class Block(nn.Module):
    def __init__(self, heads, hidden, mlp_ratio, slice_num, last_layer, out_dim, eps, gumbel_eval):
        super().__init__()
        self.last_layer = last_layer
        self.ln_1 = nn.LayerNorm(hidden)
        self.Attn = EideticAttention(hidden, heads, hidden // heads, slice_num, eps, gumbel_eval)
        self.ln_2 = nn.LayerNorm(hidden)
        self.mlp = MLP(hidden, hidden * mlp_ratio, hidden)
        if last_layer:
            self.ln_3 = nn.LayerNorm(hidden)
            self.mlp2 = nn.Linear(hidden, out_dim)

    def forward(self, fx, ctx_w, channel=None, bt=None, log_w=None):
        fx = self.Attn(self.ln_1(fx), ctx_w) + fx
        if channel is not None:
            fx = fx + channel['boundary'](channel['ln'](fx), bt, log_w)
        fx = self.mlp(self.ln_2(fx)) + fx
        return self.mlp2(self.ln_3(fx)) if self.last_layer else fx


class TransolverPP(nn.Module):
    def __init__(self, node_in, bnd_in, width=160, depth=4, heads=4, slices=32, mlp_ratio=2, eps='abs1e-5',
                 channel='none', channel_measure='arc', out_dim=1, gumbel_eval=False):
        super().__init__()
        self.channel, self.channel_measure = channel, channel_measure
        self.preprocess = MLP(node_in, width * 2, width)
        self.embedding = nn.Linear(3, width)             # upstream's condition embedding; unused here (see 4.)
        self.blocks = nn.ModuleList([Block(heads, width, mlp_ratio, slices, i == depth - 1, out_dim, eps, gumbel_eval)
                                     for i in range(depth)])
        self.apply(self._init_weights)
        self.placeholder = nn.Parameter((1 / width) * torch.rand(width, dtype=torch.float))
        layers = {'every_layer': range(depth), 'first_layer': range(1), 'none': range(0)}[channel]
        self.channels = nn.ModuleDict({str(i): nn.ModuleDict({'ln': nn.LayerNorm(width),
                                                              'boundary': BoundaryAttention(width, heads)})
                                       for i in layers})
        self.blift = mlp(bnd_in, width, width) if channel != 'none' else None
        if channel != 'none':
            self.channels.apply(self._init_weights)
            self.blift.apply(self._init_weights)

    @staticmethod
    def _init_weights(m):
        if isinstance(m, nn.Linear):
            nn.init.trunc_normal_(m.weight, std=0.02)
            if m.bias is not None:
                nn.init.constant_(m.bias, 0)
        elif isinstance(m, nn.LayerNorm):
            nn.init.constant_(m.bias, 0)
            nn.init.constant_(m.weight, 1.0)

    def forward(self, node_feat, ctx_w, bnd_feat=None, bnd_w=None):
        fx = self.preprocess(node_feat) + self.placeholder[None, None, :]
        bt = log_w = None
        if self.blift is not None:
            bt = self.blift(bnd_feat)
            log_w = (torch.log(bnd_w / bnd_w.mean(-1, keepdim=True)) if self.channel_measure == 'arc'
                     else torch.zeros_like(bnd_w))
        for i, block in enumerate(self.blocks):
            fx = block(fx, ctx_w, self.channels[str(i)] if str(i) in self.channels else None, bt, log_w)
        return fx.squeeze(-1) if fx.shape[-1] == 1 else fx         # [B,N] for out_dim 1, else [B,N,out_dim]
