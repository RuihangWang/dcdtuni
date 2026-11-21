# SPDX-FileCopyrightText: Copyright (c) 2023 - 2026 NVIDIA CORPORATION & AFFILIATES.
# SPDX-FileCopyrightText: All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# Modified for SCANO (2026-09-30): re-implemented from the files listed below with the changes 1-5 of the
# module docstring; see scano/NNmodels/vendor/physicsnemo_gale/NOTICE.
"""GeoTransolver with GALE attention (NVIDIA PhysicsNeMo), unstructured-mesh path, as a framework backbone.

Adapted from the Apache-2.0 upstream NVIDIA/physicsnemo@c911a874 (verbatim copies, licence and provenance in
scano/NNmodels/vendor/physicsnemo_gale/):
  models/geotransolver/geotransolver.py      GeoTransolver (__init__, forward)
  models/geotransolver/context_projector.py  ContextProjector, GlobalContextBuilder (geometry tokenizer only)
  nn/module/gale.py                          GALE, GALEBlock, _gale_cross_init, _mix_self_and_cross,
                                             _gale_compute_slice_attention_cross, _gale_forward_impl
  nn/module/physics_attention.py             PhysicsAttentionBase / IrregularMesh, _compute_slices_from_projections
  nn/module/mlp_layers.py                    Mlp (one hidden layer, GELU)

Mapping to the framework interface model(node_feat [B,N,F], ctx_w [B,N], bnd_feat [B,NB,Fb], bnd_w [B,NB]):
upstream's local_embedding is node_feat; its `geometry` input is the boundary records bnd_feat, which the
geometry tokenizer (a ContextProjector) pools into slice tokens [B,H,S,D] without projecting them back to points.
That context is built once, before the block loop, and every GALE block cross-attends to it from its own slice
tokens (keys/values from the context, queries from the slice tokens), mixed with the slice self-attention by
sigmoid(state_mixing). No global_embedding, no local positions. Output head: out_dim channels (1 = squeezed).

Parameter names and creation order are upstream's (context_builder.geometry_tokenizer, preprocess.0, blocks.i,
ln_mlp_out.0), so the same seed gives the same parameters and upstream state_dicts load unchanged; at the
upstream setting (context weights 1, bridge_measure count, eps abs1e-2) the forward equals upstream's
(tests/test_zoo_geotransolver.py). Changes, each an identity at that setting:
  1. context weights ctx_w in the interior slice pooling of every block: tokens are
     sum_n w_n s_n fx_n / (sum_n w_n s_n + eps). w = 1 is upstream's count pooling, w = area gives quadrature,
     w = 0 removes a point from the global state (decoupled tau*). Deslicing uses the unweighted slice
     weights, as upstream;
  2. boundary-record weights in the geometry tokenizer's pooling: bridge_measure = count (upstream, unit
     weights) or arc (bnd_w);
  3. the pooling denominator follows context.eps (slice_operator.EPS, slice_denominator); upstream is an
     additive 1e-2 (abs1e-2). The slice temperature clamp [0.5, 5] is kept as upstream;
  4. plain torch, fp32: no DTensor / ShardTensor redistribution, no Transformer Engine, no activation
     checkpointing, no warp ball-query local features;
  5. only upstream's default unstructured GALE path: no Transolver++ (plus), GALE_FA / GALE_FPP, structured
     conv paths, concrete dropout, time input, multi-stream tuples or embedding/point-feature returns;
     dropout 0 (the dropout modules are omitted; they hold no parameters).
Optional SCANO channel as a plug-in (zoo/channel_plugin.py; c95, d63; not upstream): after every channel_every-th
GALE block every point reads the boundary records with the SCANO read, next to the geometry context of GALE's
cross-attention. Created after every upstream parameter, so upstream state_dicts still load into the host part.
"""
from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F

from .slice_operator import EPS, slice_denominator


def _pool(x_mid, fx_mid, in_project_slice, temperature, w, eps):
    """upstream _compute_slices_from_projections (plus = False) with point weights w [B,N] (changes 1-3).
    x_mid, fx_mid: [B,N,H,D]. Returns the unweighted slice weights [B,N,H,S] and the tokens [B,H,S,D]."""
    proj = in_project_slice(x_mid)
    s = F.softmax(proj / torch.clamp(temperature, min=0.5, max=5).to(proj.dtype), dim=-1)
    sw = s * w[:, :, None, None]
    normed = sw / slice_denominator(sw.sum(1), w, eps)[:, None, :, :]
    return s, torch.matmul(normed.permute(0, 2, 3, 1), fx_mid.permute(0, 2, 1, 3))


class Mlp(nn.Module):
    """upstream Mlp(in, hidden, out, act='gelu'): Linear -> GELU -> Linear (names layers.0 / layers.2)."""

    def __init__(self, in_features, hidden_features, out_features):
        super().__init__()
        self.layers = nn.Sequential(nn.Linear(in_features, hidden_features), nn.GELU(),
                                    nn.Linear(hidden_features, out_features))

    def forward(self, x):
        return self.layers(x)


class ContextProjector(nn.Module):
    """upstream ContextProjector (plus = False): slice pooling of the context records, never projected back.
    Its in_project_slice keeps PyTorch's default init (upstream does not orthogonalise it here)."""

    def __init__(self, dim, heads, dim_head, slice_num, eps):
        super().__init__()
        inner = dim_head * heads
        self.dim_head, self.heads, self.eps = dim_head, heads, EPS[eps]
        self.in_project_x = nn.Linear(dim, inner)
        self.in_project_fx = nn.Linear(dim, inner)
        self.in_project_slice = nn.Linear(dim_head, slice_num)
        self.temperature = nn.Parameter(torch.ones([1, 1, heads, 1]) * 0.5)

    def forward(self, x, w):
        b, n, _ = x.shape
        x_mid = self.in_project_x(x).view(b, n, self.heads, self.dim_head)
        fx_mid = self.in_project_fx(x).view(b, n, self.heads, self.dim_head)
        return _pool(x_mid, fx_mid, self.in_project_slice, self.temperature, w, self.eps)[1]


class ContextBuilder(nn.Module):
    """upstream GlobalContextBuilder with only the geometry tokenizer (geometry_dim = boundary record width)."""

    def __init__(self, bnd_in, heads, dim_head, slice_num, eps):
        super().__init__()
        self.geometry_tokenizer = ContextProjector(bnd_in, heads, dim_head, slice_num, eps)

    def forward(self, bnd_feat, bnd_w):
        return self.geometry_tokenizer(bnd_feat, bnd_w)


class GALE(nn.Module):
    """upstream GALE (PhysicsAttentionIrregularMesh + _gale_cross_init, state_mixing_mode 'weighted')."""

    def __init__(self, dim, heads, dim_head, slice_num, context_dim, eps):
        super().__init__()
        inner = dim_head * heads
        self.dim_head, self.heads, self.eps = dim_head, heads, EPS[eps]
        self.temperature = nn.Parameter(torch.ones([1, 1, heads, 1]) * 0.5)
        self.in_project_slice = nn.Linear(dim_head, slice_num)
        torch.nn.init.orthogonal_(self.in_project_slice.weight)
        self.qkv_project = nn.Linear(dim_head, 3 * dim_head, bias=False)
        self.out_linear = nn.Linear(inner, dim)
        self.in_project_x = nn.Linear(dim, inner)
        self.in_project_fx = nn.Linear(dim, inner)
        self.cross_q = nn.Linear(dim_head, dim_head)
        self.cross_k = nn.Linear(context_dim, dim_head)
        self.cross_v = nn.Linear(context_dim, dim_head)
        self.state_mixing = nn.Parameter(torch.tensor(0.0))

    def forward(self, x, ctx_w, context):
        """x: [B,N,C]; ctx_w: [B,N]; context: [B,H,S_c,D_c]. Returns [B,N,C]."""
        b, n, _ = x.shape
        x_mid = self.in_project_x(x).view(b, n, self.heads, self.dim_head)
        fx_mid = self.in_project_fx(x).view(b, n, self.heads, self.dim_head)
        s, token = _pool(x_mid, fx_mid, self.in_project_slice, self.temperature, ctx_w, self.eps)
        qkv = self.qkv_project(token)
        q, k, v = qkv.view(*qkv.shape[:3], 3, self.dim_head).unbind(3)
        self_token = F.scaled_dot_product_attention(q, k, v, is_causal=False)
        cross_token = F.scaled_dot_product_attention(self.cross_q(token), self.cross_k(context),
                                                     self.cross_v(context), is_causal=False)
        mix = torch.sigmoid(self.state_mixing)
        out = mix * self_token + (1 - mix) * cross_token
        out_x = torch.einsum('bths,bhsd->bthd', s, out).reshape(b, n, self.heads * self.dim_head)
        return self.out_linear(out_x)


class GALEBlock(nn.Module):
    def __init__(self, heads, hidden, mlp_ratio, slice_num, context_dim, eps):
        super().__init__()
        self.ln_1 = nn.LayerNorm(hidden)
        self.Attn = GALE(hidden, heads, hidden // heads, slice_num, context_dim, eps)
        self.ln_mlp1 = nn.Sequential(nn.LayerNorm(hidden), Mlp(hidden, hidden * mlp_ratio, hidden))

    def forward(self, fx, ctx_w, context):
        fx = self.Attn(self.ln_1(fx), ctx_w, context) + fx
        return self.ln_mlp1(fx) + fx


class GeoTransolver(nn.Module):
    def __init__(self, node_in, bnd_in, width=128, depth=4, heads=4, slices=32, mlp_ratio=4, eps='abs1e-2',
                 bridge_measure='count', out_dim=1, channel='none', channel_measure='arc', channel_every=1,
                 record_slices=None):
        super().__init__()
        if width % heads:
            raise ValueError(f'width {width} must be divisible by heads {heads}')
        dim_head = width // heads
        self.bridge_measure = bridge_measure
        self.context_builder = ContextBuilder(bnd_in, heads, dim_head, slices, eps)
        self.preprocess = nn.ModuleList([Mlp(node_in, width * 2, width)])
        self.blocks = nn.ModuleList([GALEBlock(heads, width, mlp_ratio, slices, dim_head, eps)
                                     for _ in range(depth)])
        self.ln_mlp_out = nn.ModuleList([nn.Sequential(nn.LayerNorm(width), nn.Linear(width, out_dim))])
        self.plugin = None                     # SCANO channel (c95), created after every upstream parameter
        if channel != 'none':
            from .channel_plugin import ChannelPlugin, plugin_layers
            self.plugin = ChannelPlugin(width, heads, bnd_in, plugin_layers(channel, depth, channel_every),
                                        channel_measure, record_slices)

    @property
    def needs_record_area(self):
        return self.plugin is not None and self.plugin.needs_record_area

    def forward(self, node_feat, ctx_w, bnd_feat, bnd_w, bnd_area=None):
        """node_feat: [B,N,F]; ctx_w: [B,N] (0 = not in the context); bnd_feat: [B,NB,Fb]; bnd_w: [B,NB]."""
        # arc weights normalised to mean 1, so a fixed additive eps weighs as it does for count pooling
        w_b = bnd_w / bnd_w.mean(-1, keepdim=True) if self.bridge_measure == 'arc' else torch.ones_like(bnd_w)
        context = self.context_builder(bnd_feat, w_b)
        x = self.preprocess[0](node_feat)
        bt = log_w = None
        if self.plugin is not None:
            bt, log_w = self.plugin.tokens(bnd_feat, bnd_w, bnd_area)
        for i, block in enumerate(self.blocks):
            x = block(x, ctx_w, context)
            if self.plugin is not None:
                x = self.plugin.read(i, x, bt, log_w)
        out = self.ln_mlp_out[0](x)
        return out.squeeze(-1) if out.shape[-1] == 1 else out      # [B,N] for out_dim 1, else [B,N,out_dim]
