"""Transolver (Wu et al., ICML 2024) for irregular meshes, adapted from the MIT-licensed upstream
(thuml/Transolver@75e0f67, PDE-Solving-StandardBenchmark/model/{Physics_Attention,Transolver_Irregular_Mesh}.py;
verbatim copies and licence in scano/NNmodels/vendor/transolver/).

Changes to upstream, and nothing else (tests/test_zoo_transolver.py checks parity with the vendored code):
  1. context weights: the slice tokens are sum_n w_n s_n fx_n / (sum_n w_n s_n + eps). With w = 1 and
     eps = 1e-5 (abs1e-5, or rel1e-5) this is upstream's Physics_Attention_Irregular_Mesh; w = area gives
     quadrature, w = 0 removes a point from the global state (decoupled tau*). Other eps modes as in
     slice_operator.EPS;
  2. optional SCANO boundary channel per block (after the physics attention, before the MLP), for the
     "host + SCANO channel vs native host" comparison. Its modules (`channels`, `blift`) are created and
     initialised after all upstream parameters, so the upstream parameters are the same with or without it,
     and with channel = 'none' the model is upstream's; with boundary.records = "slices" the channel reads the
     area-consistent slice tokens of the records (zoo/area_slices.py; the slicer is created last);
  3. timm's trunc_normal_ replaced by torch.nn.init.trunc_normal_ (same distribution), no time input, no
     unified_pos, dropout 0; the input (x, fx) is passed as one feature tensor whose first columns are the
     coordinates (upstream concatenates them in the same order).
"""
from __future__ import annotations

import torch
import torch.nn as nn
from torch.utils.checkpoint import checkpoint as _checkpoint

from .slice_operator import EPS, BoundaryAttention, _slicer, _token_read, mlp, slice_denominator, slice_tokens


class MLP(nn.Module):
    """upstream MLP with n_layers = 0, res = False, act = gelu (parameter names kept)."""

    def __init__(self, n_input, n_hidden, n_output):
        super().__init__()
        self.linear_pre = nn.Sequential(nn.Linear(n_input, n_hidden), nn.GELU())
        self.linear_post = nn.Linear(n_hidden, n_output)
        self.linears = nn.ModuleList()

    def forward(self, x):
        return self.linear_post(self.linear_pre(x))


class WeightedPhysicsAttention(nn.Module):
    """upstream Physics_Attention_Irregular_Mesh with context weights (change 1)."""

    def __init__(self, dim, heads, dim_head, slice_num, eps='abs1e-5'):
        super().__init__()
        inner_dim = dim_head * heads
        self.dim_head = dim_head
        self.heads = heads
        self.scale = dim_head ** -0.5
        self.softmax = nn.Softmax(dim=-1)
        self.dropout = nn.Dropout(0.0)
        self.temperature = nn.Parameter(torch.ones([1, heads, 1, 1]) * 0.5)
        self.eps = EPS[eps]
        self.in_project_x = nn.Linear(dim, inner_dim)
        self.in_project_fx = nn.Linear(dim, inner_dim)
        self.in_project_slice = nn.Linear(dim_head, slice_num)
        torch.nn.init.orthogonal_(self.in_project_slice.weight)
        self.to_q = nn.Linear(dim_head, dim_head, bias=False)
        self.to_k = nn.Linear(dim_head, dim_head, bias=False)
        self.to_v = nn.Linear(dim_head, dim_head, bias=False)
        self.to_out = nn.Sequential(nn.Linear(inner_dim, dim), nn.Dropout(0.0))

    def forward(self, x, ctx_w):
        B, N, _ = x.shape
        fx_mid = self.in_project_fx(x).reshape(B, N, self.heads, self.dim_head).permute(0, 2, 1, 3).contiguous()
        x_mid = self.in_project_x(x).reshape(B, N, self.heads, self.dim_head).permute(0, 2, 1, 3).contiguous()
        slice_weights = self.softmax(self.in_project_slice(x_mid) / self.temperature)            # B H N G
        weighted = slice_weights * ctx_w[:, None, :, None]
        slice_norm = weighted.sum(2)                                                              # B H G
        slice_token = torch.einsum('bhnc,bhng->bhgc', fx_mid, weighted)
        slice_token = slice_token / slice_denominator(slice_norm, ctx_w, self.eps)[:, :, :, None]
        q, k, v = self.to_q(slice_token), self.to_k(slice_token), self.to_v(slice_token)
        attn = self.dropout(self.softmax(torch.matmul(q, k.transpose(-1, -2)) * self.scale))
        out_slice_token = torch.matmul(attn, v)                                                   # B H G D
        out_x = torch.einsum('bhgc,bhng->bhnc', out_slice_token, slice_weights)
        out_x = out_x.permute(0, 2, 1, 3).reshape(B, N, self.heads * self.dim_head)
        return self.to_out(out_x)


class TransolverBlock(nn.Module):
    def __init__(self, heads, hidden, mlp_ratio, slice_num, last_layer, out_dim, eps):
        super().__init__()
        self.last_layer = last_layer
        self.ln_1 = nn.LayerNorm(hidden)
        self.Attn = WeightedPhysicsAttention(hidden, heads, hidden // heads, slice_num, eps)
        self.ln_2 = nn.LayerNorm(hidden)
        self.mlp = MLP(hidden, hidden * mlp_ratio, hidden)
        if last_layer:
            self.ln_3 = nn.LayerNorm(hidden)
            self.mlp2 = nn.Linear(hidden, out_dim)

    def forward(self, fx, ctx_w, channel=None, bt=None, log_w=None):
        fx = self.Attn(self.ln_1(fx), ctx_w) + fx
        if channel is not None:                                        # change 2
            fx = fx + channel['boundary'](channel['ln'](fx), bt, log_w)
        fx = self.mlp(self.ln_2(fx)) + fx
        if self.last_layer:
            return self.mlp2(self.ln_3(fx))
        return fx


def _channel_read(width, heads, record_slices):
    """The per-block SCANO channel: per-record attention, or the slice-token read (boundary.records = slices)."""
    if record_slices is None:
        return BoundaryAttention(width, heads)
    from .area_slices import SliceTokenRead
    return SliceTokenRead(width, heads, **_token_read(record_slices))


def _rezero_unnormalised(channels):
    """The host's init (trunc normal) also hits the unnormalised read's output, which starts at zero."""
    for m in channels.modules():
        out_n = getattr(m, 'out_n', None)
        if isinstance(out_n, nn.Linear):
            nn.init.zeros_(out_n.weight)
            nn.init.zeros_(out_n.bias)


class Transolver(nn.Module):
    def __init__(self, node_in, bnd_in, width=160, depth=4, heads=4, slices=32, mlp_ratio=2, eps='abs1e-5',
                 channel='none', channel_measure='arc', out_dim=1, lfe=None, record_slices=None, channel_init='host'):
        super().__init__()
        if channel_init not in ('host', 'default'):
            raise ValueError(f'Transolver: unknown channel_init {channel_init!r} (host or default)')
        self.channel, self.channel_measure = channel, channel_measure
        self.preprocess = MLP(node_in, width * 2, width)
        self.blocks = nn.ModuleList([
            TransolverBlock(heads, width, mlp_ratio, slices, last_layer=(i == depth - 1), out_dim=out_dim, eps=eps)
            for i in range(depth)])
        self.apply(self._init_weights)
        self.placeholder = nn.Parameter((1 / width) * torch.rand(width, dtype=torch.float))
        # change 2: everything below is created after the upstream parameters
        layers = {'every_layer': range(depth), 'first_layer': range(1), 'none': range(0)}[channel]
        self.channels = nn.ModuleDict({str(i): nn.ModuleDict({'ln': nn.LayerNorm(width),
                                                              'boundary': _channel_read(width, heads, record_slices)})
                                       for i in layers})
        self.blift = mlp(bnd_in, width, width) if channel != 'none' else None
        if channel != 'none':
            if channel_init == 'host':        # every run before c84: the host's trunc_normal(0.02) on the channel too
                self.channels.apply(self._init_weights)
                self.blift.apply(self._init_weights)
            _rezero_unnormalised(self.channels)   # the unnormalised read's output starts at zero either way
        self.lfe = None
        if lfe is not None:                        # LFE input route, created last (its own Flax-style init)
            from .lfe_route import LFEInput
            self.lfe = LFEInput(node_in, bnd_in, width, **lfe)
        self.slicer = _slicer(record_slices, width, heads)   # created last
        # activation checkpointing of the blocks and the LFE route on the paired steps of dataset.pairs_dir runs
        # (fvgn_gn.gn_checkpointing, c86: the room's and its pair's graphs are alive together until the one backward);
        # a plain attribute, not a parameter or buffer: the state dict and the parameter creation are unchanged
        self.checkpoint = False

    @property
    def needs_record_area(self):
        """Outlet slices partition by the physical face areas (cfg_common._forward passes bnd_area)."""
        return getattr(self.slicer, 'outlet', False)

    @staticmethod
    def _init_weights(m):
        if isinstance(m, nn.Linear):
            nn.init.trunc_normal_(m.weight, std=0.02)
            if m.bias is not None:
                nn.init.constant_(m.bias, 0)
        elif isinstance(m, nn.LayerNorm):
            nn.init.constant_(m.bias, 0)
            nn.init.constant_(m.weight, 1.0)

    def forward(self, node_feat, ctx_w, bnd_feat=None, bnd_w=None, bnd_area=None):
        fx = self.preprocess(node_feat) + self.placeholder[None, None, :]
        ckpt = self.checkpoint and torch.is_grad_enabled()         # recomputed in the backward, same arithmetic
        if self.lfe is not None:
            fx = (_checkpoint(self.lfe, fx, node_feat, bnd_feat, bnd_w, use_reentrant=False) if ckpt
                  else self.lfe(fx, node_feat, bnd_feat, bnd_w))
        bt = log_w = None
        if self.blift is not None:
            bt = self.blift(bnd_feat)
            if self.slicer is not None:                                # boundary.records = "slices"
                bt, log_w = slice_tokens(self.slicer, bt, bnd_w, self.channel_measure, bnd_feat, bnd_area)
            else:
                log_w = (torch.log(bnd_w / bnd_w.mean(-1, keepdim=True)) if self.channel_measure == 'arc'
                         else torch.zeros_like(bnd_w))
        for i, block in enumerate(self.blocks):
            ch = self.channels[str(i)] if str(i) in self.channels else None
            fx = (_checkpoint(block, fx, ctx_w, ch, bt, log_w, use_reentrant=False) if ckpt
                  else block(fx, ctx_w, ch, bt, log_w))
        return fx.squeeze(-1) if fx.shape[-1] == 1 else fx         # [B,N] for out_dim 1, else [B,N,out_dim]
