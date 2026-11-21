"""AB-UPT-style anchor-attention backbone.

NOTICE. The algorithm is from Alkin, Bleeker, Kurle, Kronlachner, Sonnleitner, Dorfer and Brandstetter, "AB-UPT:
Scaling Neural CFD Surrogates for High-Fidelity Automotive Aerodynamics Simulations via Anchored-Branched
Universal Physics Transformers", TMLR 2025, arXiv:2502.09692 (v4). This file is our reimplementation from the
paper (section 3.1 with eq. (6), section 3.2 with eq. (9), appendices C.3 and C.4.1). It contains no code from the
upstream repositories (Emmi-AI/AB-UPT, Emmi-AI/Noether; Emmi AI Non-Production License). Upstream code was read
only to check tensor shapes, hyperparameters and conventions, and is run, not copied, as a numerical oracle in
tests/test_zoo_abupt.py. It is not an official implementation.

From the paper:
  * coordinates mapped to [0, 1000] (C.4.1), embedded with the Transformer sinusoidal encoding and an MLP (eq. 6);
  * ViT-style pre-norm blocks, each an attention block followed by an MLP, RoPE in every attention (3.1, C.3);
  * anchor attention (eq. 9): every token is a query, keys and values come from the anchor tokens only, so the
    anchors do self-attention and the other tokens only read the anchors, independently of each other;
  * a linear decoder at the end.
olbc has one field on one mesh, so the geometry branch, the surface/volume split and the cross-branch blocks are
absent; the trunk is one branch's stack of anchor-attention blocks.

Context weights (SCANO interface). The anchors are the points with ctx_w > 0, and their logits get the bias
log(w_j / mean w), i.e. softmax_j(s_ij + log w_j) = w_j exp(s_ij) / sum_k w_k exp(s_ik): the anchors enter the
attention as quadrature points. With ctx_w = 1 on every point (count measure, bias zero) this is AB-UPT with all
passed points as anchors; with ctx_w = 0 on the queries it is AB-UPT's query path (decoupled tau*); with
ctx_w = area it is the area measure. Duplicating an anchor and halving both weights is then an identity.

Choices the paper leaves open (see docs/ref_code_analysis/B_abupt_noether.md):
  * the coordinate map is fixed by `pos_range` (olbc: [-1, 1]^2), never the passed points' bounding box, which
    would make the embedding depend on the sample;
  * extra node features (every column after the coordinates) go through their own MLP whose output is added to
    the position embedding; the coordinates themselves enter only through the sinusoidal code and RoPE;
  * a final LayerNorm before the linear decoder (standard for pre-norm stacks; the paper only says "a single
    linear projection"); LayerNorm eps 1e-6, truncated-normal (std 0.02) init of the linear layers;
  * RoPE splits each head's rotation pairs evenly over the axes (axis 0 first), same frequencies per axis;
    positional terms and log weights are computed in fp32 (or fp64 when the model runs in fp64).
Optional SCANO boundary channel: after each block's anchor attention (every layer or the first only),
x += BoundaryAttention(LN(x), blift(bnd_feat), log w), or, with boundary.records = "slices", the read of the
area-consistent slice tokens of the records (zoo/area_slices.py). Its modules are created after all native parameters, so
the native parameters are the same with or without it.

channel_pos (boundary.channel_pos; c75, R29 on olbc H4): how the channel's read sees positions.
  raw   (default, every run before c75) the read is plain dot-product attention, the record tokens are an MLP of the raw
        record features (coordinates included). AB-UPT tokens carry position only as the sinusoidal code of the
        coordinates x 1000 through an MLP, and position enters every native attention through RoPE; a linear fit
        recovers the coordinates from the read's input only to an RMS of 0.14-0.26 in the first layer (0.05-0.11 in
        the last; olbc, coordinates in [-1, 1]), while the keys are linear in the record position, so q.k cannot single
        out nearby records and the trained reads are the query-independent arc-weighted boundary mean
        (tools/channel_read_diag.py, cases/olbc/c75-olbc-abupt-channel);
  rope  the read uses the host's RoPE: q rotated by the angles of the points' positions, k by those of the records'
        positions (the anchor attention's frequencies), so its scores depend on relative position as the host's own
        attention does. Parameters are those of the raw read (same names, shapes and creation order);
  host  rope, and the record tokens are embedded as the host embeds its points: MLP(sincos(record position')) with the
        host's own sinusoidal code and position MLP (shared), plus blift(the record's other features).
Both need per-record positions: the first space_dim record columns are coordinates (olbc, r0s, dcts), face or patch
records.
"""
from __future__ import annotations

import math

import torch
import torch.nn as nn
import torch.nn.functional as F

from ..attention import sdpa
from .slice_operator import BoundaryAttention, _slicer, mlp, slice_tokens
from .transolver import _channel_read, _rezero_unnormalised

CHANNEL_POS = ('raw', 'rope', 'host')

_HALF = (torch.float16, torch.bfloat16)


def _full(x):
    """Half inputs are promoted to fp32 for positional terms and log weights; fp32/fp64 stay as they are."""
    return x.float() if x.dtype in _HALF else x


def _axis_angles(pos, pairs_per_axis, total_pairs, max_wavelength):
    """pos [..., ndim] -> angles [..., total_pairs]: per axis pos * omega_k, omega_k = max_wavelength^(-k/pairs),
    k < pairs_per_axis, axes concatenated (axis 0 first), zero angles for the remaining pairs."""
    k = torch.arange(pairs_per_axis, device=pos.device, dtype=pos.dtype)
    omega = max_wavelength ** (-k / pairs_per_axis)
    ang = (pos[..., :, None] * omega).flatten(-2)
    return F.pad(ang, (0, total_pairs - ang.shape[-1]))


class SincosEmbed(nn.Module):
    """Transformer sinusoidal encoding of `ndim` coordinates into `dim` channels: per axis [sin(p w), cos(p w)] over
    the largest even share of `dim`, axes concatenated, zero padding for the rest. No parameters."""

    def __init__(self, dim, ndim, max_wavelength=10000.0):
        super().__init__()
        self.dim, self.ndim, self.max_wavelength = dim, ndim, float(max_wavelength)
        self.pairs = (dim // ndim) // 2

    def forward(self, pos):
        p = _full(pos)
        k = torch.arange(self.pairs, device=p.device, dtype=p.dtype)
        ang = p[..., :, None] * self.max_wavelength ** (-k / self.pairs)             # [..., ndim, pairs]
        emb = torch.cat([ang.sin(), ang.cos()], -1).flatten(-2)
        return F.pad(emb, (0, self.dim - emb.shape[-1]))


def rotate(x, cos, sin):
    """RoPE: rotate the pairs (x[2j], x[2j+1]) of x [B,H,P,D] by the angles given as cos/sin [B,1,P,D/2]."""
    xf = _full(x)
    x1, x2 = xf[..., 0::2], xf[..., 1::2]
    return torch.stack([x1 * cos - x2 * sin, x1 * sin + x2 * cos], -1).flatten(-2).to(x.dtype)


def anchor_set(ctx_w):
    """Anchors = points with ctx_w > 0. Returns (idx [B,M] of the anchors in their original order, or None when
    every point is an anchor; logit bias [B,1,1,M] = log(w / mean over the anchors), -inf on padding slots of
    rows with fewer anchors, or None when it is zero everywhere, i.e. count weights)."""
    w = _full(ctx_w)
    is_anchor = w > 0
    counts = is_anchor.sum(1)
    if bool((counts == 0).any()):
        raise ValueError('every case needs at least one anchor (a point with ctx_w > 0)')
    m = int(counts.max())
    if m == w.shape[1]:
        idx, wa, valid = None, w, None
    else:
        idx = torch.argsort((~is_anchor).to(torch.int32), dim=1, stable=True)[:, :m]
        wa = w.gather(1, idx)
        valid = torch.arange(m, device=w.device) < counts[:, None]
        wa = torch.where(valid, wa, torch.ones_like(wa))
    mean = (wa if valid is None else wa * valid).sum(1, keepdim=True) / counts[:, None]
    log_w = torch.log(wa / mean)
    if valid is not None:
        log_w = log_w.masked_fill(~valid, float('-inf'))
    bias = None if bool((log_w == 0).all()) else log_w[:, None, None, :]
    return idx, bias


def _gather_points(t, idx):
    """t [B,P,...] -> [B,M,...] at idx [B,M] (idx None = all points)."""
    if idx is None:
        return t
    return t.gather(1, idx.view(*idx.shape, *([1] * (t.dim() - 2))).expand(-1, -1, *t.shape[2:]))


class AnchorAttention(nn.Module):
    """Every point is a query; keys and values from the anchors; RoPE on queries and keys; log-weight bias."""

    def __init__(self, width, heads):
        super().__init__()
        self.h, self.dh = heads, width // heads
        self.q = nn.Linear(width, width)
        self.k = nn.Linear(width, width)
        self.v = nn.Linear(width, width)
        self.out = nn.Linear(width, width)

    def forward(self, x, anchors, rot):
        """x [B,P,W]; anchors = anchor_set(ctx_w); rot = (cos_q, sin_q, cos_k, sin_k), [B,1,P|M,D/2]."""
        idx, bias = anchors
        b, p, width = x.shape
        xa = _gather_points(x, idx)
        m = xa.shape[1]
        q = self.q(x).view(b, p, self.h, self.dh).transpose(1, 2)
        k = self.k(xa).view(b, m, self.h, self.dh).transpose(1, 2)
        v = self.v(xa).view(b, m, self.h, self.dh).transpose(1, 2)
        cos_q, sin_q, cos_k, sin_k = rot
        q, k = rotate(q, cos_q, sin_q), rotate(k, cos_k, sin_k)
        o = sdpa(q, k, v, attn_mask=None if bias is None else bias.to(q.dtype))
        return self.out(o.transpose(1, 2).reshape(b, p, width))


class RotaryBoundaryAttention(BoundaryAttention):
    """The SCANO channel read with the host's relative-position mechanism (channel_pos rope / host): RoPE on the
    queries at the points' positions and on the keys at the records' positions, then BoundaryAttention's softmax with
    the log-weight bias. Same parameters as BoundaryAttention (names, shapes, creation order)."""

    def forward(self, x, bt, log_w, rot):
        """x [B,N,W]; bt [B,NB,W]; log_w [B,NB]; rot = (cos_q, sin_q [B,1,N,D/2], cos_k, sin_k [B,1,NB,D/2])."""
        b, n, _ = x.shape
        nb = bt.shape[1]
        q = self.q(x).view(b, n, self.h, self.dh).transpose(1, 2)
        k = self.k(bt).view(b, nb, self.h, self.dh).transpose(1, 2)
        v = self.v(bt).view(b, nb, self.h, self.dh).transpose(1, 2)
        cos_q, sin_q, cos_k, sin_k = rot
        q, k = rotate(q, cos_q, sin_q), rotate(k, cos_k, sin_k)
        o = sdpa(q, k, v, attn_mask=log_w[:, None, None, :].to(q.dtype))
        return self.out(o.transpose(1, 2).reshape(b, n, self.h * self.dh))


class Block(nn.Module):
    """Pre-norm: x + AnchorAttention(LN x) [+ optional SCANO boundary channel] then x + MLP(LN x)."""

    def __init__(self, width, heads, mlp_ratio):
        super().__init__()
        self.ln_1 = nn.LayerNorm(width, eps=1e-6)
        self.attn = AnchorAttention(width, heads)
        self.ln_2 = nn.LayerNorm(width, eps=1e-6)
        self.mlp = mlp(width, mlp_ratio * width, width)

    def forward(self, x, anchors, rot, channel=None, bt=None, log_w=None, crot=None, near=None):
        x = x + self.attn(self.ln_1(x), anchors, rot)
        if channel is not None:
            h = channel['ln'](x)
            if crot is None:
                y = channel['boundary'](h, bt, log_w)
            else:                                                      # channel_pos rope / host
                y = channel['boundary'](h, bt, log_w, crot)
            if near is not None and 'near' in channel:                 # c102: face records within the near radius
                y = y + channel['near'](h, near)
            x = x + y
        return x + self.mlp(self.ln_2(x))


class ABUPT(nn.Module):
    def __init__(self, node_in, bnd_in, width=128, depth=4, heads=4, mlp_ratio=2, channel='none',
                 channel_measure='arc', space_dim=2, pos_range=(-1.0, 1.0), max_wavelength=10000.0, out_dim=1,
                 record_slices=None, channel_pos='raw', lfe=None, channel_rope_lmin=0.0):
        super().__init__()
        if width % heads or (width // heads) % 2:
            raise ValueError(f'width {width} must split into {heads} heads of even size (RoPE pairs)')
        if channel_pos not in CHANNEL_POS:
            raise ValueError(f'channel_pos must be one of {CHANNEL_POS}, got {channel_pos!r}')
        if channel_pos != 'raw' and (channel == 'none' or record_slices is not None or bnd_in <= space_dim):
            raise ValueError('channel_pos rope / host is a property of the per-record channel read: it needs a '
                             'channel and face (or patch) records whose first space_dim columns are coordinates, not '
                             'slice tokens')
        self.space_dim, self.channel, self.channel_measure = space_dim, channel, channel_measure
        self.channel_pos = channel_pos
        # boundary.channel_rope_lmin (c98, R29 of task 50): the channel read keeps only the RoPE pairs whose wavelength is
        # at least this share of the coordinate range (0 = every pair, as before); the other pairs are not rotated in
        # the read, so it cannot key on offsets finer than the coarsest mesh of the family. The host's own attention
        # keeps every pair.
        if not 0.0 <= float(channel_rope_lmin) < 1.0:
            raise ValueError('channel_rope_lmin must be in [0, 1)')
        self.channel_rope_lmin = float(channel_rope_lmin)
        lo, hi = (float(v) for v in pos_range)
        self.pos_lo, self.pos_scale = lo, 1000.0 / (hi - lo)
        self.max_wavelength = float(max_wavelength)
        dh = width // heads
        self.rope_pairs, self.rope_pairs_per_axis = dh // 2, (dh // space_dim) // 2
        self.pos_embed = SincosEmbed(width, space_dim, max_wavelength)
        self.pos_mlp = mlp(width, width, width)
        self.feat_mlp = mlp(node_in - space_dim, width, width) if node_in > space_dim else None
        self.blocks = nn.ModuleList(Block(width, heads, mlp_ratio) for _ in range(depth))
        self.norm = nn.LayerNorm(width, eps=1e-6)
        self.head = nn.Linear(width, out_dim)
        self.apply(self._init_weights)
        # SCANO boundary channel: created after every native parameter, so those do not change with it
        layers = {'every_layer': range(depth), 'first_layer': range(1), 'none': range(0)}[channel]
        read = (lambda: _channel_read(width, heads, record_slices)) if channel_pos == 'raw' else \
            (lambda: RotaryBoundaryAttention(width, heads))
        self.channels = nn.ModuleDict({str(i): nn.ModuleDict({'ln': nn.LayerNorm(width, eps=1e-6),
                                                              'boundary': read()})
                                       for i in layers})
        # host: the record's coordinates go through the host's own position embedding (shared), the rest through blift
        self.blift = (mlp(bnd_in - (space_dim if channel_pos == 'host' else 0), width, width)
                      if channel != 'none' else None)
        if channel != 'none':
            self.channels.apply(self._init_weights)
            self.blift.apply(self._init_weights)
            _rezero_unnormalised(self.channels)
        self.slicer = _slicer(record_slices, width, heads)   # created after the channel
        # boundary.near_radius (c102): the near-field read of the face records beside the slice-token read, created
        # after the channel and the slicer (its output starts at zero), so the other parameters do not change with it
        self.near_radius, self.near_k = 0.0, 0
        if record_slices is not None and record_slices.get('near_radius', 0.0) > 0:
            from .area_slices import NearFieldRead
            self.near_radius, self.near_k = float(record_slices['near_radius']), int(record_slices['near_k'])
            for ch in self.channels.values():
                ch['near'] = NearFieldRead(width, heads, space_dim)
        # LFE input route (boundary.lfe, host matrix c95 / H10c: the other boundary plug-in, compared with the channel
        # on the same host): created after everything else (its own Flax-style init), so the host and the channel
        # parameters do not change with it; it acts once, on the input tokens, as on the Transolver host
        self.lfe = None
        if lfe is not None:
            from .lfe_route import LFEInput
            self.lfe = LFEInput(node_in, bnd_in, width, **lfe)

    @property
    def needs_record_area(self):
        """Outlet slices partition by the physical face areas (cfg_common._forward passes bnd_area)."""
        return getattr(self.slicer, 'outlet', False)

    def read_pairs(self, device):
        """[total_pairs] bool: the RoPE pairs the channel read rotates, those whose wavelength 2 pi / omega is at least
        channel_rope_lmin of the coordinate range (positions span 1000 units); padding pairs (angle 0) are kept."""
        k = torch.arange(self.rope_pairs_per_axis, device=device, dtype=torch.float64)
        omega = (self.max_wavelength ** (-k / self.rope_pairs_per_axis)).repeat(self.space_dim)
        keep = 2 * math.pi / omega >= self.channel_rope_lmin * 1000.0
        pad = self.rope_pairs - keep.numel()
        return torch.cat([keep, torch.ones(pad, dtype=torch.bool, device=device)])

    @staticmethod
    def _init_weights(m):
        if isinstance(m, nn.Linear):
            nn.init.trunc_normal_(m.weight, std=0.02)
            if m.bias is not None:
                nn.init.zeros_(m.bias)
        elif isinstance(m, nn.LayerNorm):
            nn.init.ones_(m.weight)
            nn.init.zeros_(m.bias)

    def positions(self, node_feat):
        """Coordinates mapped by the fixed affine map pos_range -> [0, 1000] (fp32/fp64)."""
        return (_full(node_feat[..., :self.space_dim]) - self.pos_lo) * self.pos_scale

    def embed(self, node_feat):
        """Input tokens [B,N,W]: MLP(sincos(pos')) + MLP(non-coordinate features)."""
        x = self.pos_mlp(self.pos_embed(self.positions(node_feat)).to(node_feat.dtype))
        if self.feat_mlp is not None:
            x = x + self.feat_mlp(node_feat[..., self.space_dim:])
        return x

    def rope(self, node_feat, idx):
        ang = _axis_angles(self.positions(node_feat), self.rope_pairs_per_axis, self.rope_pairs,
                           self.max_wavelength)[:, None]                              # [B,1,P,D/2]
        cos, sin = ang.cos(), ang.sin()
        if idx is None:
            return cos, sin, cos, sin
        g = idx[:, None, :, None].expand(-1, 1, -1, cos.shape[-1])
        return cos, sin, cos.gather(2, g), sin.gather(2, g)

    def forward(self, node_feat, ctx_w, bnd_feat=None, bnd_w=None, bnd_area=None):
        """node_feat [B,N,F] (first space_dim columns = coordinates); ctx_w [B,N] >= 0 (anchors: > 0);
        bnd_feat [B,NB,Fb], bnd_w [B,NB] > 0 (used only by the channel). Returns [B,N] (out_dim 1) or
        [B,N,out_dim]."""
        x = self.embed(node_feat)
        if self.lfe is not None:
            x = self.lfe(x, node_feat, bnd_feat, bnd_w)
        anchors = anchor_set(ctx_w)
        rot = self.rope(node_feat, anchors[0])
        bt = log_w = crot = near = None
        if self.blift is not None:
            if self.channel_pos == 'host':                             # records embedded as the host's points
                bt = (self.pos_mlp(self.pos_embed(self.positions(bnd_feat)).to(bnd_feat.dtype))
                      + self.blift(bnd_feat[..., self.space_dim:]))
            else:
                bt = self.blift(bnd_feat)
            if self.channel_pos != 'raw':                              # RoPE of the read: points (q), records (k)
                ak = _axis_angles(self.positions(bnd_feat), self.rope_pairs_per_axis, self.rope_pairs,
                                  self.max_wavelength)[:, None]
                crot = (rot[0], rot[1], ak.cos(), ak.sin())
                if self.channel_rope_lmin > 0:                         # identity rotation for the short wavelengths
                    keep = self.read_pairs(ak.device)
                    crot = tuple(torch.where(keep, c, torch.ones_like(c)) if j % 2 == 0
                                 else torch.where(keep, c, torch.zeros_like(c)) for j, c in enumerate(crot))
            w = _full(bnd_w)
            if self.near_radius:                                       # c102: before slicing, the lifted face records
                from .area_slices import near_records
                if bt.shape[0] != 1:
                    raise ValueError('near_radius runs one case per forward (batch 1)')
                p = self.space_dim
                idx, valid, rel = near_records(node_feat[0, :, :p], bnd_feat[0, :, :p], self.near_radius, self.near_k)
                lw = (torch.log(w / w.mean(-1, keepdim=True)) if self.channel_measure == 'arc' else torch.zeros_like(w))
                near = {'bt': bt, 'log_w': lw, 'idx': idx, 'valid': valid, 'rel': rel}
            if self.slicer is not None:                                # boundary.records = "slices"
                bt, log_w = slice_tokens(self.slicer, bt, w, self.channel_measure, bnd_feat, bnd_area)
            else:
                log_w = (torch.log(w / w.mean(-1, keepdim=True)) if self.channel_measure == 'arc'
                         else torch.zeros_like(w))
        for i, block in enumerate(self.blocks):
            x = block(x, anchors, rot, self.channels[str(i)] if str(i) in self.channels else None, bt, log_w, crot,
                      near)
        out = self.head(self.norm(x))
        return out.squeeze(-1) if out.shape[-1] == 1 else out      # [B,N] for out_dim 1, else [B,N,out_dim]
