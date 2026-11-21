"""GNOT-style operator (Hao et al., "GNOT: A General Neural Operator Transformer for Operator Learning",
ICML 2023), written from the paper's equations. The upstream repository has no licence, so no upstream code is
copied or adapted here; nothing is checked against it numerically (docs/ref_code_analysis/D section 2 describes
the upstream implementation that this follows).

Structure: a trunk MLP embeds the query points; a branch MLP embeds the input function (here: the boundary
records of the case), once. Each block applies
    h <- h + CrossAttn(LN h, LN z)          normalised linear attention of the points to the boundary records
    h <- h + FFN(LN h)                      a gated mixture of experts, gates from the coordinates
    h <- h + SelfAttn(LN h)                 normalised linear attention over the passed points
    h <- h + FFN(LN h)
Normalised linear attention: queries and keys go through a softmax over the feature dimension, and
    out_i = q_i (sum_j w_j k_j v_j^T) / (q_i . sum_j w_j k_j),
i.e. one d x d summary per head and cost linear in the number of points and records, with rank <= head dim.
Weights w: boundary records (bridge_measure: count, or arc length/area) and the self-attention context (ctx_w:
count or area; 0 for queries in the decoupled tau* setting). Upstream corresponds to count weights on both.
Optional SCANO channel as a plug-in (zoo/channel_plugin.py; c95, d63): after every channel_every-th block every
point reads the boundary records with the SCANO read (softmax per point, log-area bias), next to GNOT's own
normalised linear cross-attention (rank <= head dim). Created after every GNOT parameter.
boundary.bridge_measure = "off" (c107, with the channel only): GNOT's own boundary cross-attention is skipped and the
channel is its only boundary route; the unused native modules are still created, so the parameters and their
initialisation at a seed are those of the host with both routes.
"""
from __future__ import annotations

import torch
import torch.nn as nn


def mlp(i, h, o):
    return nn.Sequential(nn.Linear(i, h), nn.GELU(), nn.Linear(h, h), nn.GELU(), nn.Linear(h, o))


def linear_attention(q, k, v, w):
    """q: [B,H,N,D]; k, v: [B,H,M,D]; w: [B,M] >= 0 (some > 0). Returns [B,H,N,D]."""
    q = q.softmax(-1)
    kw = k.softmax(-1) * w[:, None, :, None]
    summary = torch.einsum('bhmd,bhme->bhde', kw, v)
    num = torch.einsum('bhnd,bhde->bhne', q, summary)
    den = torch.einsum('bhnd,bhd->bhn', q, kw.sum(2))[..., None]
    return num / den


class LinearAttention(nn.Module):
    def __init__(self, width, heads):
        super().__init__()
        self.h, self.dh = heads, width // heads
        self.q = nn.Linear(width, width)
        self.k = nn.Linear(width, width)
        self.v = nn.Linear(width, width)
        self.out = nn.Linear(width, width)

    def split(self, x):
        b, n, _ = x.shape
        return x.view(b, n, self.h, self.dh).transpose(1, 2)

    def forward(self, x, ctx, w):
        o = linear_attention(self.split(self.q(x)), self.split(self.k(ctx)), self.split(self.v(ctx)), w)
        b, h, n, d = o.shape
        return self.out(o.transpose(1, 2).reshape(b, n, h * d))


class GatedExperts(nn.Module):
    """Dense mixture of expert FFNs with softmax gates from the point coordinates (one expert = plain FFN)."""

    def __init__(self, width, hidden, experts, space_dim):
        super().__init__()
        self.experts = nn.ModuleList(nn.Sequential(nn.Linear(width, hidden), nn.GELU(), nn.Linear(hidden, width))
                                     for _ in range(experts))
        self.gate = mlp(space_dim, width, experts) if experts > 1 else None

    def forward(self, x, pos):
        if self.gate is None:
            return self.experts[0](x)
        g = self.gate(pos).softmax(-1)                                    # [B,N,E]
        return sum(g[..., e:e + 1] * ex(x) for e, ex in enumerate(self.experts))


class Block(nn.Module):
    def __init__(self, width, heads, hidden, experts, space_dim):
        super().__init__()
        self.ln_c, self.ln_z = nn.LayerNorm(width), nn.LayerNorm(width)
        self.cross = LinearAttention(width, heads)
        self.ln_1 = nn.LayerNorm(width)
        self.ffn_1 = GatedExperts(width, hidden, experts, space_dim)
        self.ln_s = nn.LayerNorm(width)
        self.self_attn = LinearAttention(width, heads)
        self.ln_2 = nn.LayerNorm(width)
        self.ffn_2 = GatedExperts(width, hidden, experts, space_dim)

    def forward(self, h, z, w_b, ctx_w, pos):
        if z is not None:                     # bridge_measure "off" (c107): no own boundary cross-attention
            h = h + self.cross(self.ln_c(h), self.ln_z(z), w_b)
        h = h + self.ffn_1(self.ln_1(h), pos)
        hs = self.ln_s(h)
        h = h + self.self_attn(hs, hs, ctx_w)
        return h + self.ffn_2(self.ln_2(h), pos)


class GNOT(nn.Module):
    def __init__(self, node_in, bnd_in, width=96, depth=3, heads=1, experts=1, mlp_ratio=2, space_dim=2,
                 bridge_measure='count', out_dim=1, channel='none', channel_measure='arc', channel_every=1,
                 record_slices=None):
        super().__init__()
        self.space_dim, self.bridge_measure = space_dim, bridge_measure
        self.trunk = mlp(node_in, width, width)
        self.branch = mlp(bnd_in, width, width)
        self.blocks = nn.ModuleList(Block(width, heads, width * mlp_ratio, experts, space_dim) for _ in range(depth))
        self.head = mlp(width, width, out_dim)
        self.plugin = None                     # SCANO channel (c95), created after every native parameter
        if channel != 'none':
            from .channel_plugin import ChannelPlugin, plugin_layers
            self.plugin = ChannelPlugin(width, heads, bnd_in, plugin_layers(channel, depth, channel_every),
                                        channel_measure, record_slices)

    @property
    def needs_record_area(self):
        return self.plugin is not None and self.plugin.needs_record_area

    def forward(self, node_feat, ctx_w, bnd_feat, bnd_w, bnd_area=None):
        """node_feat: [B,N,F] (first space_dim columns are coordinates); ctx_w: [B,N]; bnd_feat: [B,NB,Fb]."""
        pos = node_feat[..., :self.space_dim]
        h = self.trunk(node_feat)
        if self.bridge_measure == 'off':      # c107: the SCANO channel is the boundary route (unused native modules
            z = w_b = None                    # are kept, so the native parameters and their init stay as they are)
        else:
            z = self.branch(bnd_feat)
            w_b = bnd_w if self.bridge_measure == 'arc' else torch.ones_like(bnd_w)
        bt = log_w = None
        if self.plugin is not None:
            bt, log_w = self.plugin.tokens(bnd_feat, bnd_w, bnd_area)
        for i, blk in enumerate(self.blocks):
            h = blk(h, z, w_b, ctx_w, pos)
            if self.plugin is not None:
                h = self.plugin.read(i, h, bt, log_w)
        out = self.head(h)
        return out.squeeze(-1) if out.shape[-1] == 1 else out      # [B,N] for out_dim 1, else [B,N,out_dim]
