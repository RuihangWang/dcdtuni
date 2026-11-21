"""AeroGTO graph-transformer host (Peng et al., "AeroGTO: An Efficient Graph-Transformer Operator for Learning
Large-Scale Aerodynamics of 3D Vehicle Geometries", AAAI 2025, doi:10.1609/aaai.v39i18.34083), as the AAAI-27
manuscript code ran it, in the framework interface; decision d58 (10-08): a third host next to Transolver and AB-UPT,
native and with the SCANO channel.

NOTICE / provenance. This file is our rewrite. The reference is the AAAI-27 code of our lab, Gen-FVGN-refine (read-only,
read with `git show` only) at commit 82f2a3eb59ed2a0cc6662151b8fa8f307525739c (branch dev-3D-data-driven):
  src/NNmodels/Baselines/AeroGTOBaseline.py   git blob 42f5783c337006f450ca2f1cd45d2f23b0513a61,
                                              sha256 f8aeb946a65bf545373e34c6f830b29bd9ae93c97e2c53a9fc7d08ca74fb9d3d
  src/NNmodels/TransFVGN/AeroGTOSCA_v1.py     git blob cdda75bf7b1cab05db4de15ec2c2c3ee86db562e (the AAAI SCA arm on
                                              this trunk; not ported: our channel replaces its input bridge)
  src/NNmodels/importer.py                    git blob 9a48cef26f2d033de703edfd9fc9f4cf67e9651c (Simulator.initialize_
                                              weights: the initialisation the AAAI runs trained from)
The refine module describes itself as a port of pengwei07/AeroGTO (code/src/AeroGTO.py; GitHub, branch main_new at
1ab3cf3a620b0888ca6063d348c9174d8dbc696a, blob d3934c2b261e77b8d18b1b21f35e1e3214b50116). That repository has no
licence (GitHub reports none, 2026-10-08), i.e. all rights reserved, so by Ref_code/README.md nothing of it may be
copied into this repository: this module is written from the paper and the logic of the refine module, and no upstream
or refine code is vendored. The refine module is used only as a numerical oracle in tests/test_zoo_aerogto.py, written
into scratch/ by `git show` at test time and never committed. Details: docs/ref_code_analysis/F_aerogto_port.md.
Module and parameter names follow the refine module, so its state dicts (and the AAAI checkpoints) load into this one.

Structure (the refine module; tests/test_zoo_aerogto.py checks the forward against it):
  * coordinates: the first space_dim node columns, min-max normalised over the passed points (pos - min) / (max - min
    + 1e-8) per axis. A subgraph's own bounding box: the embedding of a point depends on the sample (the refine module
    does this because its Fourier frequencies assume coordinates in about [0, 1]);
  * encoder: Fourier code of the normalised coordinates (per axis cos and sin of 2^i pi x, i = -4..4, then the
    normalised coordinates), concatenated with the other node columns -> MLP (two hidden layers, SiLU) -> s_enc
    (width) -> MLP (two hidden layers, PReLU) -> V;
  * edge features: MLP (one hidden layer, SiLU) of [x_receiver - x_sender, |.|] in the normalised coordinates;
  * `depth` mixer blocks, each: a GNN step (edge MLP of [v_sender, v_receiver, e], mean at the receiver, node MLP of
    [v, mean]; one hidden layer, GELU, LayerNorm; the first block's GNN reads [V, s_enc]) with gated residuals
    V += SiLU(a1) v, E += SiLU(a2) e (the last block's edge state is ungated and unused); then a projection attention
    on LN(V): `slices` learned query tokens attend over all points (softmax over the points), the tokens attend to each
    other (torch MultiheadAttention, `heads` heads), every point reads the tokens back (softmax over the tokens);
    W2 = V + SiLU(a3) read; W3 = W2 + SiLU(a4) Linear(LN(W2)). Every gate a starts at 0, so the trunk starts as the
    identity;
  * decoder: Linear, PReLU, Linear, PReLU, Linear.
Initialisation as the AAAI training ran it (refine importer, Simulator.initialize_weights after construction): every
nn.Linear (also the token attention's output projection) trunc_normal(std 0.02) with zero bias, LayerNorm (1, 0);
the learned queries N(0, 1), the token attention's input projection (xavier) and the PReLU slopes (0.25) as
constructed. The same seed gives the parameters of refine's class under that initialisation.
c81 addendum A1 (10-09, docs/c81_aerogto_r29_2026-10-09.md on branch aerogto-r29): model.aerogto_init = "author" uses the
AeroGTO authors' initialisation instead (pengwei07/AeroGTO code/main_ddp.py init_weights, if_init: true in both of their
configs): every nn.Linear and nn.MultiheadAttention (in_proj, out_proj) xavier_uniform with bias 0.01; LayerNorm, the
learned queries, the PReLU slopes and the zero residual gates as constructed. Under the AAAI initialisation the
residual stream starts at ~5e-6 RMS (LayerNorm inside its eps), the token attention is exactly uniform and the
attention gates' gradients sit below Adam's eps, so on R0-S the native arm never left its initial plateau (c81 stage 1);
under the authors' initialisation it leaves it in the second epoch. Like the AAAI one, it is applied to the host's
parameters only, before the channel modules exist. Default "aaai" (c81 stage 1 and every earlier run).

Changes to the refine module, and nothing else:
  1. width scaling: the refine MLPs have a fixed hidden size 256 and s_enc has 128 channels (its default width is 128).
     Here hidden = mlp_ratio x width and s_enc = width, which is the refine module at width 128, mlp_ratio 2; smaller
     widths keep the shape of the network for the parameter budget of the comparison (~0.78M);
  2. dimension: space_dim coordinates (olbc 2, R0-S and DCTS 3; refine is 3-D), so the Fourier code has 2 x 9 x
     space_dim columns and the edge features space_dim + 1; the other node columns are the dataset's (refine: unit
     normal and the five boundary values);
  3. context weights (SCANO interface): the softmax of the learned queries over the points gets the logit bias
     log(w_n / mean w) (w = ctx_w > 0), i.e. the tokens are w-weighted (quadrature) means; ctx_w = 0 removes a point from
     the tokens' keys (it is still a query and still in the GNN and the bounding box). ctx_w = 1 everywhere (count
     measure, the native arm) adds no bias: the refine computation. The bounding box and the GNN see every passed point,
     so the global state cannot be decoupled from the sample (run_config allows coupled contexts only);
  4. the attention over the points goes through attention.sdpa (fused kernels, query chunks when too large) instead
     of an explicit [tokens x points] softmax; same function, round-off differences;
  5. no gradient checkpointing, no bf16 path, one case per forward (refine also runs batch 1);
  6. (host matrix c95, d63; not in refine) box = (lo, hi): the coordinates are normalised by the fixed domain range,
     (pos - lo) / (hi - lo), instead of the passed points' bounding box (model.aerogto_box = "domain"), so a point's
     embedding no longer depends on the sample; with it the global state can be decoupled from the sample
     (context.mode = "decoupled": the learned tokens read whole-mesh anchors under the context measure, the queries
     carry weight 0; cfg_common induces the mesh edges on the query rows only, and its mesh cache keeps a query copy
     with the edges and an edge-free context copy, as in training). box = None (default): the refine computation.
Optional SCANO boundary channel (boundary.channel every_layer / first_layer, d58; as for the Transolver and AB-UPT
hosts): in each (or the first) mixer block, after the projection attention and before the block's Linear,
W2 += SiLU(a_c) read(LN(W2), blift(records), log w), the per-record attention (slice_operator.BoundaryAttention), or
with boundary.records = "slices" the read of the area-consistent slice tokens (area_slices.SliceTokenRead and the slicer,
created last). The channel modules are created after every host parameter, so the host's parameters do not change with
it, and they keep PyTorch's default initialisation (also blift and the slicer): a trunc_normal(0.02) host init on the
channel keeps its scores flat at the start (c79 diagnosis, cases/r0s/c79-r0s-accuracy/Logger/diag_v1/channel_probe.json
on the r0s-accuracy branch), so unlike the Transolver and AB-UPT hosts the host init is not applied to it. The read
enters behind a learnable gate a_c that starts at 0 (ChannelGate; the form of the host's own residual gates, SiLU(a)
with a = 0): the host's stream at the start is ~1e-5 RMS (AAAI init, zero gates) while the default-initialised read is
~0.1 RMS (review of 10-08, scratch/review_c81), so without the gate the channel arm would not start from the identity
trunk the native arm starts from. At construction the channel arm therefore computes exactly what the native arm with
the same host parameters computes; SiLU'(0) = 1/2, so the gate gets a gradient at the first step. The records' raw
features enter blift (dataset coordinates, not the subgraph's normalised ones).

Framework interface: model(node_feat [1,N,F], ctx_w [1,N], bnd_feat [1,NB,Fb], bnd_w [1,NB], edges [2,E]) -> [1,N]
([1,N,out_dim] when out_dim > 1), edges = the mesh edges induced on the passed points (needs_edges; sender = edges[0],
receiver = edges[1]; olbc cpd|neighbor_cell, R0-S the message graph, DCTS the mesh faces, each stored in both
directions).
"""
from __future__ import annotations

import math

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.checkpoint import checkpoint as _checkpoint

from ..attention import sdpa
from .slice_operator import _slicer, mlp, slice_tokens
from .transolver import _channel_read

FOURIER = (-4, 9)                 # frequencies 2^i pi, i = -4 .. 4 (refine Encoder11D: FourierEmbedding(pos, -4, 9))
BBOX_EPS = 1e-8                   # refine: (pos - min) / (max - min + 1e-8)
_ACT = {'gelu': nn.GELU, 'silu': nn.SiLU, 'prelu': nn.PReLU}


class MLP(nn.Module):
    """Linear, act, (Linear, act) x (n_hidden - 1), Linear [, LayerNorm]. One activation module serves every layer
    (registered as `act` and inside `f`): with PReLU the slope is one parameter per MLP, as in the refine module."""

    def __init__(self, n_in, n_out, hidden, n_hidden=2, act='prelu', layer_norm=True):
        super().__init__()
        self.act = _ACT[act]()
        layers = [nn.Linear(n_in, hidden), self.act]
        for _ in range(n_hidden - 1):
            layers += [nn.Linear(hidden, hidden), self.act]
        layers.append(nn.Linear(hidden, n_out))
        if layer_norm:
            layers.append(nn.LayerNorm(n_out))
        self.f = nn.Sequential(*layers)

    def forward(self, x):
        return self.f(x)


def fourier_code(pos):
    """[..., d] -> [..., 2 * 9 * d + d]: per axis cos(2^i pi x) for i = -4..4, then sin, axes in order, then x."""
    i = torch.arange(FOURIER[0], FOURIER[0] + FOURIER[1], device=pos.device, dtype=pos.dtype)
    ang = (2.0 ** i * math.pi) * pos[..., None]                              # [..., d, 9]
    code = torch.cat([ang.cos(), ang.sin()], -1).flatten(-2)                 # [..., d * 18]
    return torch.cat([code, pos], -1)


def scatter_mean(src, index, size):
    """Mean of the rows of src [E, C] per index (a row without entries is 0, as torch_scatter.scatter_mean)."""
    total = src.new_zeros(size, src.shape[-1]).index_add_(0, index, src)
    count = src.new_zeros(size).index_add_(0, index, src.new_ones(index.numel()))
    return total / count.clamp_min(1)[:, None]


class Encoder(nn.Module):
    def __init__(self, node_in, space_dim, width, hidden):
        super().__init__()
        self.enc_s = MLP(2 * FOURIER[1] * space_dim + node_in, width, hidden, act='silu', layer_norm=False)
        self.fv = MLP(width, width, hidden, act='prelu', layer_norm=False)

    def forward(self, feats, space_dim):
        """feats [N, F] with normalised coordinates in front -> (V [N, W], s_enc [N, W])."""
        s_enc = self.enc_s(torch.cat([fourier_code(feats[:, :space_dim]), feats[:, space_dim:]], -1))
        return self.fv(s_enc), s_enc


class GNN(nn.Module):
    def __init__(self, node_size, edge_size, out_size, hidden):
        super().__init__()
        self.f_edge = MLP(edge_size + 2 * node_size, edge_size, hidden, n_hidden=1, act='gelu')
        self.f_node = MLP(edge_size + node_size, out_size, hidden, n_hidden=1, act='gelu')

    def forward(self, v, e, edges):
        s, r = edges[0], edges[1]
        e_new = self.f_edge(torch.cat([v[s], v[r], e], -1))
        return self.f_node(torch.cat([v, scatter_mean(e_new, r, v.shape[0])], -1)), e_new


class ProjectionAttention(nn.Module):
    """`tokens` learned queries read the points (softmax over the points, log-weight bias), the tokens attend to each
    other, the points read the tokens (softmax over the tokens). Single-head projections of full width."""

    def __init__(self, tokens, width, heads):
        super().__init__()
        self.Q = nn.Parameter(torch.randn(tokens, width))
        self.to_q_1 = nn.Linear(width, width)
        self.to_k_1 = nn.Linear(width, width)
        self.to_v_1 = nn.Linear(width, width)
        self.attention2 = nn.MultiheadAttention(embed_dim=width, num_heads=heads, batch_first=True)
        self.to_q_2 = nn.Linear(width, width)
        self.to_k_2 = nn.Linear(width, width)
        self.to_v_2 = nn.Linear(width, width)

    def forward(self, x, bias):
        """x [N, W]; bias None or [N] log weights (-inf = not a key) -> [N, W]."""
        q1 = self.to_q_1(self.Q)[None, None]                                       # [1,1,M,W]
        k1, v1 = self.to_k_1(x)[None, None], self.to_v_1(x)[None, None]           # [1,1,N,W]
        t = sdpa(q1, k1, v1, attn_mask=None if bias is None else bias.to(q1.dtype)[None, None, None])[0]  # [1,M,W]
        t = self.attention2(t, t, t, need_weights=False)[0]
        q2 = self.to_q_2(x)[None, None]
        out = sdpa(q2, self.to_k_2(t)[None], self.to_v_2(t)[None])                 # [1,1,N,W]
        return out[0, 0]


class MixerBlock(nn.Module):
    def __init__(self, width, hidden, tokens, heads, first, last):
        super().__init__()
        self.last = last
        self.gnn = GNN(2 * width if first else width, width, width, hidden)
        self.ln1 = nn.LayerNorm(width)
        self.ln2 = nn.LayerNorm(width)
        self.linear = nn.Linear(width, width)
        self.MHA = ProjectionAttention(tokens, width, heads)
        self.alpha_1 = nn.Parameter(torch.tensor(0.0))
        if not last:
            self.alpha_2 = nn.Parameter(torch.tensor(0.0))
        self.alpha_3 = nn.Parameter(torch.tensor(0.0))
        self.alpha_4 = nn.Parameter(torch.tensor(0.0))
        self.act = nn.SiLU()

    def forward(self, v, e, edges, s_enc, bias, channel=None, bt=None, log_w=None):
        dv, de = self.gnn(v if s_enc is None else torch.cat([v, s_enc], -1), e, edges)
        v = v + self.act(self.alpha_1) * dv
        e = e + de if self.last else e + self.act(self.alpha_2) * de
        w = v + self.act(self.alpha_3) * self.MHA(self.ln1(v), bias)
        if channel is not None:                                       # SCANO channel (d58)
            w = w + channel['gate'](channel['boundary'](channel['ln'](w)[None], bt, log_w)[0])
        return w + self.act(self.alpha_4) * self.linear(self.ln2(w)), e


class ChannelGate(nn.Module):
    """SiLU(alpha) x read, alpha a scalar starting at 0: the form of the host's residual gates (MixerBlock alpha_k), so
    the channel arm starts from the identity trunk like the native arm (module docstring)."""

    def __init__(self):
        super().__init__()
        self.alpha = nn.Parameter(torch.tensor(0.0))

    def forward(self, x):
        return F.silu(self.alpha) * x


class Mixer(nn.Module):
    def __init__(self, space_dim, width, hidden, depth, tokens, heads):
        super().__init__()
        self.fe = MLP(space_dim + 1, width, hidden, n_hidden=1, act='silu', layer_norm=False)
        self.blocks = nn.ModuleList(MixerBlock(width, hidden, tokens, heads, first=(i == 0), last=(i == depth - 1))
                                    for i in range(depth))


class Decoder(nn.Module):
    def __init__(self, width, out_dim):
        super().__init__()
        self.final_mlp_node = nn.Sequential(nn.Linear(width, width), nn.PReLU(), nn.Linear(width, width), nn.PReLU(),
                                            nn.Linear(width, out_dim))

    def forward(self, v):
        return self.final_mlp_node(v)


def aaai_init(module):
    """The AAAI training initialisation (refine importer, Simulator._init_weights), applied to every submodule."""
    for m in module.modules():
        if isinstance(m, nn.Linear):
            nn.init.trunc_normal_(m.weight, std=0.02)
            if m.bias is not None:
                nn.init.constant_(m.bias, 0)
        elif isinstance(m, nn.LayerNorm):
            nn.init.constant_(m.bias, 0)
            nn.init.constant_(m.weight, 1.0)


def author_init(module):
    """The AeroGTO authors' initialisation (pengwei07/AeroGTO code/main_ddp.py init_weights; c81 addendum A1): every
    nn.Linear and nn.MultiheadAttention (in_proj and out_proj) xavier_uniform with bias 0.01; everything else as
    constructed."""
    for m in module.modules():
        if isinstance(m, nn.Linear):
            nn.init.xavier_uniform_(m.weight)
            if m.bias is not None:
                nn.init.constant_(m.bias, 0.01)
        elif isinstance(m, nn.MultiheadAttention):
            nn.init.xavier_uniform_(m.in_proj_weight)
            if m.in_proj_bias is not None:
                nn.init.constant_(m.in_proj_bias, 0.01)
            nn.init.xavier_uniform_(m.out_proj.weight)
            if m.out_proj.bias is not None:
                nn.init.constant_(m.out_proj.bias, 0.01)


HOST_INITS = {'aaai': aaai_init, 'author': author_init}


def bbox_normalise(pos):
    """[N, d] -> (pos - min) / (max - min + 1e-8) per axis over the passed points."""
    lo, hi = pos.amin(0, keepdim=True), pos.amax(0, keepdim=True)
    return (pos - lo) / (hi - lo + BBOX_EPS)


def token_bias(ctx_w):
    """[N] context weights -> None (all equal: no bias, the refine computation) or log(w / mean over w > 0), -inf
    where w = 0."""
    if bool((ctx_w == ctx_w[0]).all()) and float(ctx_w[0]) > 0:
        return None
    pos = ctx_w > 0
    if not bool(pos.any()):
        raise ValueError('AeroGTO needs at least one point with a positive context weight')
    mean = ctx_w[pos].mean()
    return torch.where(pos, torch.log(ctx_w.clamp_min(torch.finfo(ctx_w.dtype).tiny) / mean),
                       torch.full_like(ctx_w, float('-inf')))


class AeroGTO(nn.Module):
    needs_edges = True

    def __init__(self, node_in, bnd_in, width=128, depth=8, heads=8, tokens=256, mlp_ratio=2, space_dim=3,
                 channel='none', channel_measure='arc', out_dim=1, record_slices=None, init='aaai', box=None):
        super().__init__()
        self.box = None if box is None else tuple(float(v) for v in box)     # change 6: fixed-domain normalisation
        if self.box is not None and not self.box[1] > self.box[0]:
            raise ValueError(f'AeroGTO: box must be (lo, hi) with hi > lo, got {box!r}')
        if init not in HOST_INITS:
            raise ValueError(f'AeroGTO: unknown host initialisation {init!r} (one of {sorted(HOST_INITS)})')
        if width % heads:
            raise ValueError(f'AeroGTO: width {width} must be divisible by heads {heads}')
        if node_in <= space_dim:
            raise ValueError('AeroGTO: the node features hold the coordinates in front of the other columns')
        self.space_dim, self.channel, self.channel_measure = space_dim, channel, channel_measure
        hidden = mlp_ratio * width
        self.encoder = Encoder(node_in, space_dim, width, hidden)
        self.mixer = Mixer(space_dim, width, hidden, depth, tokens, heads)
        self.decoder = Decoder(width, out_dim)
        HOST_INITS[init](self)
        # SCANO channel: created after every host parameter, PyTorch default initialisation (see the docstring)
        layers = {'every_layer': range(depth), 'first_layer': range(1), 'none': range(0)}[channel]
        self.channels = nn.ModuleDict({str(i): nn.ModuleDict({'ln': nn.LayerNorm(width),
                                                              'boundary': _channel_read(width, heads, record_slices),
                                                              'gate': ChannelGate()})
                                       for i in layers})
        self.blift = mlp(bnd_in, width, width) if channel != 'none' else None
        self.slicer = _slicer(record_slices, width, heads)   # created last
        # activation checkpointing of the mixer blocks on the paired steps of dataset.pairs_dir runs (fvgn_gn.
        # gn_checkpointing, c86: the room's and its pair's graphs are alive together until the one backward); a plain
        # attribute, not a parameter or buffer, so the state dict and the parameter creation are unchanged
        self.checkpoint = False

    @property
    def needs_record_area(self):
        """Outlet slices partition by the physical face areas (cfg_common._forward passes bnd_area)."""
        return getattr(self.slicer, 'outlet', False)

    def forward(self, node_feat, ctx_w, bnd_feat=None, bnd_w=None, bnd_area=None, edges=None):
        if node_feat.shape[0] != 1:
            raise ValueError('AeroGTO runs one case per forward')
        if edges is None:
            raise ValueError('AeroGTO needs the mesh edges among the passed points (needs_edges)')
        d = self.space_dim
        x = node_feat[0]
        pos = (bbox_normalise(x[:, :d]) if self.box is None
               else (x[:, :d] - self.box[0]) / (self.box[1] - self.box[0]))
        v, s_enc = self.encoder(torch.cat([pos, x[:, d:]], -1), d)
        rel = pos[edges[1]] - pos[edges[0]]                                   # receiver - sender
        e = self.mixer.fe(torch.cat([rel, torch.linalg.vector_norm(rel, dim=-1, keepdim=True)], -1))
        bias = token_bias(ctx_w[0])
        bt = log_w = None
        if self.blift is not None:
            bt = self.blift(bnd_feat)
            if self.slicer is not None:                                       # boundary.records = "slices"
                bt, log_w = slice_tokens(self.slicer, bt, bnd_w, self.channel_measure, bnd_feat, bnd_area)
            else:
                log_w = (torch.log(bnd_w / bnd_w.mean(-1, keepdim=True)) if self.channel_measure == 'arc'
                         else torch.zeros_like(bnd_w))
        for i, block in enumerate(self.mixer.blocks):
            ch = self.channels[str(i)] if str(i) in self.channels else None
            args = (v, e, edges, s_enc if i == 0 else None, bias, ch, bt, log_w)
            if self.checkpoint and torch.is_grad_enabled():          # recomputed in the backward, same arithmetic
                v, e = _checkpoint(block, *args, use_reentrant=False)
            else:
                v, e = block(*args)
        out = self.decoder(v)
        return (out.squeeze(-1) if out.shape[-1] == 1 else out)[None]          # [1,N] or [1,N,out_dim]
