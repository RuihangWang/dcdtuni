"""Slice-attention operator (Transolver-style trunk) with configurable boundary routes and context options.

Backbone: lift -> depth x [slice attention over an explicit context set, optional boundary route, FFN] -> head.
The global state (slice tokens) is a weighted mean over the context points: `ctx_w` carries the measure
(area or count) and is zero for points that are queried but not in the context (decoupled tau*).

Boundary routes, combinable:
  node fields (data side)   BC values on the node features of the boundary points that are passed in;
  channel                   every point attends to all boundary records (log quadrature weights as logit
                            bias; count = no bias), in every layer, in every channel_every-th layer (layers
                            0, k, 2k, ...; A4 injection depth) or only the first; with boundary.records =
                            "slices" it reads the area-consistent slice tokens of the records instead
                            (zoo/area_slices.py);
  global film               one pooled boundary summary (arc or count mean) modulates every layer's
                            LayerNorm outputs, zero-initialised so training starts from the plain trunk;
  global adaln_zero         the same summary as Unisolver/DiT adaLN-Zero: shift, scale and a gate on each
                            residual branch, all zero-initialised, so every block starts as the identity;
                            with global_dim = d > 0 the summary first passes a linear bottleneck
                            width -> d -> width (E4, Proposition D: the code that modulates every layer has
                            rank at most d), created after everything else;
                            code dropout (global_dropout = p > 0; boundary.global_dropout, decision d54, R29 on
                            H8): in training mode only, the code that reaches the layers (after the bottleneck) is
                            replaced by zeros with probability p, one draw per case (batch row) and forward from the
                            numpy generator attached with attach_code_rng (train_cfg: its own stream, kept in
                            resume.pt); the kept codes are not rescaled and evaluation always reads the code. At
                            z = 0 the zero-initialised maps give their bias alone: a learned case-independent
                            modulation, so a dropped step trains the trunk on the per-node path. No module, no
                            parameter and, with p = 0, no draw: the state dict and the arithmetic are unchanged;
  lfe input                 the LFE extender at the input (zoo/lfe_route.py), created after everything else.
  message passing           model.message_passing = m (c76; d54: dcts, r0s, olbc): m FVGN GN blocks before
                            every layer, as in the original SCANO trunk (AAAI AttnProcessor; zoo/fvgn_gn.py),
                            on the query rows only, created after everything else.

With channel = every_layer, eps = scale_free and global = none, module names, creation order and arithmetic
equal scano.NNmodels.olbc_operator.OlbcOperator (the c37 model): the same seed gives the same parameters and
c37 checkpoints load unchanged (tests/test_zoo.py). out_dim (default 1) is the width of the head's last Linear;
with 1 the output is squeezed to [B,N] as always, otherwise it is [B,N,out_dim].
"""
from __future__ import annotations

import math

import torch
import torch.nn.functional as F
from torch import nn

from torch.utils.checkpoint import checkpoint as _checkpoint

from ..attention import sdpa

# Denominator of the slice means. scale_free: no additive term (c37); its backward overflows once a slice's
# mass is subnormal in fp32. abs: upstream Transolver (1e-5) / GALE (1e-2). rel: eps times the mean positive
# context weight - scale-free like scale_free, equal to abs1e-5 when every weight is 1, finite gradients.
EPS = {'scale_free': None, 'abs1e-5': ('abs', 1e-5), 'abs1e-2': ('abs', 1e-2), 'rel1e-5': ('rel', 1e-5),
       'rel1e-2': ('rel', 1e-2)}


def slice_denominator(den, ctx_w, eps):
    """den: [B, ...] summed context weight per slice (batch first); returns the safe denominator."""
    if eps is None:
        return den.clamp_min(torch.finfo(den.dtype).tiny)
    kind, e = eps
    if kind == 'abs':
        return den + e
    scale = ctx_w.sum(1) / (ctx_w > 0).sum(1).clamp_min(1)              # [B] mean positive weight
    return den + e * scale.view(-1, *([1] * (den.dim() - 1)))


def mlp(i, h, o):
    return nn.Sequential(nn.Linear(i, h), nn.GELU(), nn.Linear(h, o))


class SliceAttention(nn.Module):
    def __init__(self, width, heads, slices, eps='scale_free'):
        super().__init__()
        self.h, self.dh, self.m = heads, width // heads, slices
        self.eps = EPS[eps]
        self.in_x = nn.Linear(width, width)
        self.in_fx = nn.Linear(width, width)
        self.slice = nn.Linear(self.dh, slices)
        self.temperature = nn.Parameter(torch.full((1, 1, heads, 1), 0.5))
        self.token_attn = nn.MultiheadAttention(self.dh, 1, batch_first=True)
        self.out = nn.Linear(width, width)

    def forward(self, x, ctx_w):
        """x: [B,N,W]; ctx_w: [B,N] non-negative context weights (0 = not in the context)."""
        b, n, _ = x.shape
        xs = self.in_x(x).view(b, n, self.h, self.dh)
        fx = self.in_fx(x).view(b, n, self.h, self.dh)
        s = torch.softmax(self.slice(xs) / self.temperature.clamp_min(0.05), dim=-1)      # [B,N,H,M]
        sw = s * ctx_w[:, :, None, None]
        num = torch.einsum('bnhm,bnhd->bhmd', sw, fx)
        den = sw.sum(1)[..., None]                                                         # [B,H,M,1]
        tok = num / slice_denominator(den, ctx_w, self.eps)
        t = tok.reshape(b * self.h, self.m, self.dh)
        t = t + self.token_attn(t, t, t, need_weights=False)[0]
        tok = t.view(b, self.h, self.m, self.dh)
        out = torch.einsum('bnhm,bhmd->bnhd', s, tok).reshape(b, n, self.h * self.dh)
        return self.out(out)


class BoundaryAttention(nn.Module):
    def __init__(self, width, heads):
        super().__init__()
        self.h, self.dh = heads, width // heads
        self.q = nn.Linear(width, width)
        self.k = nn.Linear(width, width)
        self.v = nn.Linear(width, width)
        self.out = nn.Linear(width, width)

    def forward(self, x, bt, log_w):
        """x: [B,N,W]; bt: [B,NB,W]; log_w: [B,NB] log quadrature weights (zeros = count measure)."""
        b, n, _ = x.shape
        nb = bt.shape[1]
        q = self.q(x).view(b, n, self.h, self.dh).transpose(1, 2)
        k = self.k(bt).view(b, nb, self.h, self.dh).transpose(1, 2)
        v = self.v(bt).view(b, nb, self.h, self.dh).transpose(1, 2)
        bias = log_w[:, None, None, :].to(q.dtype)
        o = sdpa(q, k, v, attn_mask=bias)     # split over the queries only when the scores would be huge
        return self.out(o.transpose(1, 2).reshape(b, n, self.h * self.dh))


class AttnPool(nn.Module):
    """boundary.global_pool = attention (mock review item 12): a read of the lifted records without the domain point,
    per layer: H learned queries attend to the records with the log-weight bias of the global measure, one code [B, W].
    The same records as SCANO's channel, read globally instead of per point; copying a record into k pieces of weight
    a / k leaves the code unchanged."""

    def __init__(self, width, heads):
        super().__init__()
        self.h, self.dh = heads, width // heads
        self.q = nn.Parameter(torch.randn(heads, self.dh) * self.dh ** -0.5)
        self.k = nn.Linear(width, width)
        self.v = nn.Linear(width, width)
        self.out = nn.Linear(width, width)

    def forward(self, bt, log_w):
        """bt [B,NB,W] lifted records; log_w [B,NB] log weights (zeros = count) -> [B,W]."""
        b, nb, _ = bt.shape
        k = self.k(bt).view(b, nb, self.h, self.dh)
        v = self.v(bt).view(b, nb, self.h, self.dh)
        s = torch.einsum('hd,bnhd->bhn', self.q.to(k.dtype), k) * self.dh ** -0.5 + log_w[:, None, :].to(k.dtype)
        o = torch.einsum('bhn,bnhd->bhd', torch.softmax(s, -1), v)
        return self.out(o.reshape(b, self.h * self.dh))


class RecordMassRead(nn.Module):
    """The unnormalised read of face records (boundary.record_read = both; PLAN R4, the G = N_b end of the slice
    tokens' second read): sum_y (a_y / mass_ref) exp(q.k_y / sqrt(dh)) v_y per head, through area_slices.mass_read
    (query chunks, the exponent clamped as for the slice tokens). Splitting a record into k copies of area a / k leaves
    it unchanged. The output projection starts at zero, so a fresh model reads as with the normalised read alone."""

    def __init__(self, width, heads, mass_ref):
        super().__init__()
        if not mass_ref > 0:
            raise ValueError('mass_ref must be > 0')
        self.h, self.dh = heads, width // heads
        self.log_ref = math.log(mass_ref)
        self.q = nn.Linear(width, width)
        self.k = nn.Linear(width, width)
        self.v = nn.Linear(width, width)
        self.out = nn.Linear(width, width)
        nn.init.zeros_(self.out.weight)
        nn.init.zeros_(self.out.bias)

    def forward(self, x, bt, log_area):
        """x [B,N,W]; bt [B,NB,W] lifted records; log_area [B,NB] log record areas (absolute)."""
        from .area_slices import mass_read
        b, n, _ = x.shape
        nb = bt.shape[1]
        q = self.q(x).view(b, n, self.h, self.dh).transpose(1, 2) * self.dh ** -0.5
        k = self.k(bt).view(b, nb, self.h, self.dh).transpose(1, 2)
        v = self.v(bt).view(b, nb, self.h, self.dh).transpose(1, 2)
        o = mass_read(q, k, v, (log_area - self.log_ref)[:, None, None, :].to(q.dtype))
        return self.out(o.transpose(1, 2).reshape(b, n, self.h * self.dh))


class Layer(nn.Module):
    def __init__(self, width, heads, slices, boundary_channel, eps='scale_free', film=False, ada=False, token_read=None):
        super().__init__()
        self.ln1 = nn.LayerNorm(width)
        self.slice = SliceAttention(width, heads, slices, eps)
        self.boundary = None
        if boundary_channel:                       # token_read: area-consistent slice tokens (zoo/area_slices.py)
            if token_read is None:
                self.boundary = BoundaryAttention(width, heads)
            else:
                from .area_slices import SliceTokenRead
                self.boundary = SliceTokenRead(width, heads, **token_read)
        self.ln2 = nn.LayerNorm(width) if boundary_channel else None
        self.ln3 = nn.LayerNorm(width)
        self.ffn = mlp(width, 2 * width, width)
        self.boundary_n = None                     # RecordMassRead under boundary.record_read = both (set by SliceOperator)
        self.near = None                           # NearFieldRead under boundary.near_radius > 0 (set by SliceOperator)
        self.film = None
        if film:                                   # created last: disabled film leaves the init order unchanged
            self.film = nn.Linear(width, 4 * width)
            nn.init.zeros_(self.film.weight)
            nn.init.zeros_(self.film.bias)
        self.ada = None
        if ada:                                    # adaLN-Zero (Unisolver / DiT): shift, scale, gate per branch
            self.ada = nn.Linear(width, 6 * width)
            nn.init.zeros_(self.ada.weight)
            nn.init.zeros_(self.ada.bias)

    def forward(self, x, ctx_w, bt, log_w, z=None, log_area=None, near=None):
        h1, h3 = self.ln1(x), None
        g1 = g3 = 1.0
        if self.film is not None:
            sh1, sc1, sh3, sc3 = self.film(z)[:, None, :].chunk(4, -1)
            h1 = h1 * (1 + sc1) + sh1
        if self.ada is not None:
            sh1, sc1, g1, sh3, sc3, g3 = self.ada(z)[:, None, :].chunk(6, -1)
            h1 = h1 * (1 + sc1) + sh1
        x = x + g1 * self.slice(h1, ctx_w)
        if self.boundary is not None:
            h2 = self.ln2(x)
            y = self.boundary(h2, bt, log_w)
            if self.boundary_n is not None:        # the unnormalised read of the face records (record_read both)
                y = y + self.boundary_n(h2, bt, log_area)
            if self.near is not None:              # the face records within the near radius (c102)
                y = y + self.near(h2, near)
            x = x + y
        h3 = self.ln3(x)
        if self.film is not None:
            h3 = h3 * (1 + sc3) + sh3
        if self.ada is not None:
            h3 = h3 * (1 + sc3) + sh3
        return x + g3 * self.ffn(h3)


class SliceOperator(nn.Module):
    def __init__(self, node_in, bnd_in, width=128, depth=4, heads=4, slices=32, channel='every_layer',
                 channel_measure='arc', global_route='none', global_measure='arc', eps='scale_free', lfe=None,
                 out_dim=1, record_slices=None, channel_every=1, global_dim=0, global_dropout=0.0,
                 message_passing=0, server_route=False, record_read='normalized', mass_ref=1.0, global_pool='mean'):
        super().__init__()
        if channel_every < 1 or (channel_every != 1 and channel != 'every_layer'):
            raise ValueError(f'channel_every = {channel_every} needs channel = every_layer and a value >= 1')
        if global_dim and (global_route == 'none' or not 0 < global_dim < width):
            raise ValueError(f'global_dim = {global_dim} needs a global route and 0 < global_dim < width')
        if global_dropout and (global_route == 'none' or not 0 < global_dropout < 1):
            raise ValueError(f'global_dropout = {global_dropout} needs a global route and 0 < global_dropout < 1')
        self.out_dim = out_dim
        self.channel, self.channel_measure = channel, channel_measure
        self.global_route, self.global_measure = global_route, global_measure
        self.channel_every = channel_every
        uses_records = channel != 'none' or global_route != 'none'
        self.lift = mlp(node_in, width, width)
        self.blift = mlp(bnd_in, width, width) if uses_records else None
        self.layers = nn.ModuleList(
            Layer(width, heads, slices, boundary_channel=reads_channel(channel, i, channel_every),
                  eps=eps, film=global_route == 'film', ada=global_route == 'adaln_zero',
                  token_read=_token_read(record_slices))
            for i in range(depth))
        self.head = nn.Sequential(nn.LayerNorm(width), nn.Linear(width, out_dim))
        self.lfe = None
        if lfe is not None:                        # created last: the trunk's parameters do not change
            from .lfe_route import LFEInput
            self.lfe = LFEInput(node_in, bnd_in, width, **lfe)
        self.slicer = _slicer(record_slices, width, heads)   # created last: the other parameters do not change
        self.global_code = None
        if global_dim:                             # created last as well: the reference's parameters do not change
            self.global_code = nn.Sequential(nn.Linear(width, global_dim), nn.Linear(global_dim, width))
        self.gn = None
        if message_passing:                        # created last (c76; after global_code, d54): FVGN GN blocks of the
            from .fvgn_gn import TrunkMessagePassing          # original SCANO trunk - every other parameter (and
            self.gn = TrunkMessagePassing(width, depth, message_passing)   # its seed stream) stays as without it
        self.route = None
        if server_route:                           # boundary.server_route (dcts, B2): created after everything else, so
            if self.slicer is None or type(self.slicer).__name__ != 'BoundarySlicer' or channel == 'none':
                raise ValueError('server_route needs a channel reading learned slice tokens (BoundarySlicer)')
            self.route = nn.ModuleList(nn.Sequential(nn.LayerNorm(width), nn.Linear(width, width))   # every other
                                       for _ in range(depth))                         # parameter is as without it
            for r in self.route:                   # zero output: a fresh model computes exactly as without the route
                nn.init.zeros_(r[1].weight)
                nn.init.zeros_(r[1].bias)
        # boundary.global_pool = attention (mock review item 12): one query-independent read of the records per layer
        # (AttnPool), created after everything else, so every other parameter is as without it
        self.pool = None
        if global_pool == 'attention':
            if global_route not in ('film', 'adaln_zero') or global_dim:
                raise ValueError('global_pool attention needs a global route (film / adaln_zero) and no global_dim')
            self.pool = nn.ModuleList(AttnPool(width, heads) for _ in range(depth))
        elif global_pool != 'mean':
            raise ValueError(f'unknown global_pool {global_pool!r}')
        self.record_mass = record_read == 'both'
        if self.record_mass:                       # boundary.record_read (R29 10-10): created after everything else;
            if record_slices is not None or channel == 'none' or channel_measure != 'arc':   # its output starts at
                raise ValueError('record_read both needs face records read under arc by the channel')   # zero
            for i, layer in enumerate(self.layers):
                if layer.boundary is not None:
                    layer.boundary_n = RecordMassRead(width, heads, mass_ref)
        # boundary.near_radius (c102): the near-field read of the face records beside the slice-token read, created
        # after everything else (its output starts at zero), so every other parameter is as without it
        self.near_radius, self.near_k, self.near_pos = 0.0, 0, 0
        if record_slices is not None and record_slices.get('near_radius', 0.0) > 0:
            from .area_slices import NearFieldRead
            if channel == 'none' or not record_slices.get('pos_dims'):
                raise ValueError('near_radius needs the channel and the record positions (pos_dims)')
            self.near_radius = float(record_slices['near_radius'])
            self.near_k = int(record_slices['near_k'])
            self.near_pos = int(record_slices['pos_dims'])
            for layer in self.layers:
                if layer.boundary is not None:
                    layer.near = NearFieldRead(width, heads, self.near_pos)
        # code dropout: plain attributes (no module, no parameter, no buffer), so the state dict is unchanged
        self.global_dropout = float(global_dropout)
        self.code_rng = None                       # numpy Generator of the drop draws (attach_code_rng)
        # activation checkpointing of the slice tokens and of every layer (train.act_checkpoint = "always", c120: G > 64
        # on DCTS, where a paired step keeps two forwards' token reads): a plain attribute, set only inside
        # fvgn_gn.gn_checkpointing(model, on, layers=True); recomputed in the backward with the same arithmetic
        self.checkpoint_layers = False

    @property
    def server_route(self):
        """boundary.server_route: cfg_common._forward passes the inlet rows and outlet records (route_rows)."""
        return self.route is not None

    @property
    def message_passing(self):
        """GN blocks per layer (model.message_passing); cfg_common._forward passes the mesh edges of the query rows."""
        return 0 if self.gn is None else self.gn.m

    @property
    def message_passing_hops(self):
        """Mesh hops the GN path reaches (3 per block and layer; zoo/fvgn_gn.py), 0 without it."""
        return 0 if self.gn is None else self.gn.hops

    @property
    def needs_record_area(self):
        """Outlet slices partition by the physical face areas (cfg_common._forward passes bnd_area)."""
        return getattr(self.slicer, 'outlet', False)

    def route_setup(self, bt, bnd_w, route):
        """boundary.server_route (B2), once per forward: the slice assignment of the outlet records (of the lifted,
        unrouted records bt [1,NB,W]), their channel-measure weights, and a function of the slicer's log masses giving
        the slice denominators (area_slices.slice_den). route: cfg_common.route_rows."""
        from .area_slices import slice_den
        if route is None or bt.shape[0] != 1:
            raise ValueError('server_route needs the route rows of cfg_common._forward (batch 1)')
        w = bnd_w if self.channel_measure == 'arc' else torch.ones_like(bnd_w)
        rec = route['out_rec']
        total = w.sum(1)
        eps = self.slicer.tot_eps
        return {**route, 'assign': self.slicer.assignment(bt[:, rec])[0], 'a': w[0, rec],
                'den_of': lambda log_mass: slice_den(torch.exp(log_mass), total.to(log_mass.dtype), eps)[0]}

    def route_tokens(self, i, x, r):
        """[H,G,dh] change of the slice tokens in layer i (B2): every server's inlet rows of x [1,N,W] are averaged,
        mapped by route[i] (zero for a server with no inlet row) and added to the lifted records of its outlet faces;
        the tokens of these records change by sum a w_g in_fx(delta) / den_g with the unrouted assignment w (the
        aggregation of area_slices.BoundarySlicer is linear in the records' in_fx features)."""
        s, rows, srv = r['n_servers'], r['rows'], r['row_server']
        pooled = x.new_zeros(s, x.shape[-1]).index_add_(0, srv, x[0, rows])
        cnt = torch.bincount(srv, minlength=s).to(x.dtype)
        delta = self.route[i](pooled / cnt.clamp_min(1)[:, None]) * (cnt > 0).to(x.dtype)[:, None]
        dn = delta[r['out_server']]
        fx = F.linear(dn, self.slicer.in_fx.weight).view(dn.shape[0], self.slicer.h, self.slicer.dh)
        num = torch.einsum('nhg,n,nhd->hgd', r['assign'].to(fx.dtype), r['a'].to(fx.dtype), fx)
        return num / r['den'][..., None].to(num.dtype)

    def forward(self, node_feat, ctx_w, bnd_feat=None, bnd_w=None, bnd_area=None, edges=None, edge_raw=None,
                query_rows=None, route=None):
        """node_feat: [B,N,F]; ctx_w: [B,N]; bnd_feat: [B,NB,Fb]; bnd_w: [B,NB] (>0). Returns [B,N] (out_dim 1)
        or [B,N,out_dim].
        With model.message_passing: edges [2, E] (one per mesh face, indices into the first query_rows points) and
        edge_raw [E, 4]; the GN blocks act on the first query_rows points only (AttnProcessor: the layer reads
        x + GN(x)); the other points (decoupled anchors, the mesh cache's context copy) carry the trunk without GN."""
        x = self.lift(node_feat)
        ckpt = self.checkpoint_layers and torch.is_grad_enabled()
        if self.lfe is not None:
            x = self.lfe(x, node_feat, bnd_feat, bnd_w)
        bt = log_w = z = log_area = near = None
        if self.blift is not None:
            bt = self.blift(bnd_feat)
            if self.channel != 'none' and self.slicer is None:
                log_w = (torch.log(bnd_w / bnd_w.mean(-1, keepdim=True)) if self.channel_measure == 'arc'
                         else torch.zeros_like(bnd_w))
                if self.record_mass:               # absolute log areas for the unnormalised record read
                    log_area = torch.log(bnd_w)
            if self.global_route in ('film', 'adaln_zero'):
                w = bnd_w if self.global_measure == 'arc' else torch.ones_like(bnd_w)
                if self.pool is None:
                    z = (bt * w[..., None]).sum(1) / w.sum(1, keepdim=True)
                    if self.global_code is not None:   # the only path from the summary to the layers: rank <= d
                        z = self.global_code(z)
                    if self.global_dropout and self.training:      # code dropout: training-mode forwards only
                        z = drop_code(z, self.global_dropout, self.code_rng)
                else:                              # one query-independent read per layer (global_pool attention)
                    lw = torch.log(w / w.mean(-1, keepdim=True))
                    z = [pool(bt, lw) for pool in self.pool]
                    if self.global_dropout and self.training:      # one draw per case drops every layer's code
                        keep = drop_code(torch.ones_like(z[0][:, :1]), self.global_dropout, self.code_rng)
                        z = [zi * keep for zi in z]
            if self.near_radius:                   # c102: the lifted face records and their near sets, before slicing
                from .area_slices import near_records
                if bt.shape[0] != 1:
                    raise ValueError('near_radius runs one case per forward (batch 1)')
                p = self.near_pos
                idx, valid, rel = near_records(node_feat[0, :, :p], bnd_feat[0, :, :p], self.near_radius, self.near_k)
                lw = (torch.log(bnd_w / bnd_w.mean(-1, keepdim=True)) if self.channel_measure == 'arc'
                      else torch.zeros_like(bnd_w))
                near = {'bt': bt, 'log_w': lw, 'idx': idx, 'valid': valid, 'rel': rel}
            if self.slicer is not None:            # the channel reads the slice tokens instead of the records
                if self.route is not None:             # B2: the outlet records' assignment and the slice denominators
                    routed = self.route_setup(bt, bnd_w, route)
                if ckpt:
                    bt, log_w = _checkpoint(slice_tokens, self.slicer, bt, bnd_w, self.channel_measure, bnd_feat,
                                            bnd_area, use_reentrant=False)
                else:
                    bt, log_w = slice_tokens(self.slicer, bt, bnd_w, self.channel_measure, bnd_feat, bnd_area)
                if self.route is not None:
                    routed['den'] = routed['den_of'](log_w)
        e = None
        if self.gn is not None:
            if edges is None or edge_raw is None or query_rows is None or x.shape[0] != 1:
                raise ValueError('model.message_passing needs edges, edge_raw and query_rows (cfg_common._forward)')
            e = self.gn.lift_edges(edge_raw)       # = self.gn.lift(edge_raw); checkpointed under gn_checkpointing
        for i, layer in enumerate(self.layers):
            if e is not None:                      # AttnProcessor: TransBlock(x + GN(x)) on the query rows
                q = query_rows
                g, e = self.gn.layer(i, x[0, :q], edges, e)
                x = torch.cat([x[:, :q] + g[None], x[:, q:]], 1)
            tok = bt if self.route is None else bt + self.route_tokens(i, x, routed)[None]
            zi = z[i] if isinstance(z, list) else z
            x = (_checkpoint(layer, x, ctx_w, tok, log_w, zi, log_area, near, use_reentrant=False) if ckpt
                 else layer(x, ctx_w, tok, log_w, zi, log_area, near))
        out = self.head(x)
        return out.squeeze(-1) if self.out_dim == 1 else out


def drop_code(z, p, rng):
    """Code dropout (boundary.global_dropout = p): each row of the code z [B, W] (one case each) is replaced by zeros
    with probability p, one uniform draw u per row from the numpy generator `rng` (dropped when u < p, in row order);
    the other rows are returned unchanged and not rescaled (a dropout of the condition, not of activations: the
    deployed model reads the code as it was trained to)."""
    if rng is None:
        raise RuntimeError('boundary.global_dropout > 0 in training mode needs the code-dropout generator: '
                           'attach one with scano.NNmodels.zoo.attach_code_rng (train_cfg does)')
    drop = torch.from_numpy(rng.random(z.shape[0]) < p).to(z.device)
    return torch.where(drop[:, None], torch.zeros_like(z), z)


def attach_code_rng(model, rng):
    """Give every SliceOperator in `model` (also inside a wrapper, zoo/type_encoding.py) whose code dropout is on the
    numpy generator its training-mode forwards draw from; returns how many (0: the model drops no code, nothing
    attached). train_cfg attaches np.random.default_rng([seed, 2]) and keeps its state in resume.pt."""
    n = 0
    for m in model.modules():
        if isinstance(m, SliceOperator) and m.global_dropout:
            m.code_rng = rng
            n += 1
    return n


def reads_channel(channel, i, every=1):
    """Whether layer i (from 0) has the boundary channel: every_layer reads in layers 0, every, 2 every, ...
    (every = 1: all of them), first_layer only in layer 0."""
    return (channel == 'every_layer' and i % every == 0) or (channel == 'first_layer' and i == 0)


def _token_read(record_slices):
    """SliceTokenRead arguments when the channel reads area-consistent slice tokens (boundary.records = slices)."""
    if record_slices is None:
        return None
    from .area_slices import LSE_MAX
    return {'unnormalized': record_slices['unnormalized'], 'mass_ref': record_slices['mass_ref'],
            'lse_max': record_slices.get('lse_max', LSE_MAX)}


def _slicer(record_slices, width, heads):
    """The slicer of boundary.records = "slices": learned soft slices (BoundarySlicer, the default) or slices grouped by
    spatial adjacency (SpatialSlicer, boundary.slice_assign = "spatial"; positions = the first pos_dims record columns)."""
    if record_slices is None:
        return None
    if record_slices.get('assign', 'learned') == 'spatial':
        from .area_slices import SpatialSlicer
        base = SpatialSlicer(width, heads, record_slices['g'], record_slices.get('pos_dims'))
    else:
        from .area_slices import BoundarySlicer
        base = BoundarySlicer(width, heads, record_slices['g'])
    if not record_slices.get('outlet_slices'):
        return base
    from .area_slices import OutletSlicer             # dcts: the server-outlet records get slices of their own
    return OutletSlicer(base, record_slices['outlet_slices'], record_slices['outlet_col'], record_slices.get('pos_dims'))


def slice_tokens(slicer, bt, bnd_w, measure, bnd_feat=None, bnd_area=None):
    """(tokens, log masses) of the records for the per-layer reads; the records weigh by area (arc) or 1 (count).
    Spatial slicers also read the record positions, the first slicer.pos_dims columns of bnd_feat; outlet slicers
    also the physical face areas bnd_area (their partition weights)."""
    w = bnd_w if measure == 'arc' else torch.ones_like(bnd_w)
    if getattr(slicer, 'outlet', False):              # role-partitioned: the outlet role column marks the records
        if bnd_feat is None or bnd_area is None:
            raise ValueError('outlet slices need the record features (positions, role one-hot) and the face areas')
        return slicer(bt, w, bnd_feat[..., :slicer.pos_dims], bnd_feat[..., slicer.outlet_col] > 0.5, bnd_area)
    if getattr(slicer, 'spatial', False):
        if bnd_feat is None:
            raise ValueError('spatial slices need the record features (their first columns are the positions)')
        return slicer(bt, w, bnd_feat[..., :slicer.pos_dims])
    return slicer(bt, w)


def count_parameters(model):
    return sum(p.numel() for p in model.parameters())
