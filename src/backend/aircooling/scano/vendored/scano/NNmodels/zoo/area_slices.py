"""Area-consistent slice-token aggregation of the boundary records and its per-layer reads (SCANO's mu + B and iota;
docs/research/PLAN.md section 3.3, decision d30). Used by every host with boundary.records = "slices".

Aggregation, once per forward, per head (BoundarySlicer):
  1. h_y: the lifted record (the host's blift), projected per head;
  2. soft clustering w_g(y) = softmax_g(proj(h_y) / temperature), g = 1..G, summing to 1 over the slices of a record;
  3. slice mass M_g = sum_y a_y w_g(y) (a_y the record's weight under the channel measure: the face area for arc, 1 for
     count); sum_g M_g = A_total;
  4. token z_g = sum_y a_y w_g(y) h_y / (M_g + TOTAL_EPS * A_total).
Reads, every layer, every point x (SliceTokenRead):
  5. normalised (first/third-kind data): o(x) = sum_g softmax_g(q(x).k(z_g) / sqrt(d) + log M_g) v(z_g), through
     attention.sdpa (fused kernel, or query chunks on the math path);
  6. unnormalised (second-kind flux, rack Delta T): o_N(x) = sum_g (M_g / mass_ref) exp(q_N(x).k_N(z_g) / sqrt(d))
     v_N(z_g), evaluated as exp(z - max(lse - LSE_MAX, 0)) v_N with z = s + log M_g - log mass_ref (the same function
     as the normalised read of z times exp(min(lse, LSE_MAX)); query chunks above attention.MAX_SCORE_ELEMENTS). Its
     output projection starts at zero, so a run starts from the normalised path alone. mass_ref is in the unit of
     the record weights (m^2 on DCTS under arc; records under count).
Properties fixed by tests/test_area_slices.py: splitting a record into k copies of weight a/k leaves both reads
unchanged (the denominator only adds a multiple of the total weight); hard assignment by patch gives the
area-weighted patch means as tokens; one slice per record turns the normalised read into per-record attention with a
log-area logit; with zero scores the unnormalised read is the weighted sum of v over the records; the cost is linear
in records and in points (the [points x records] score matrix is never formed).
Cost: the aggregation runs once per forward, (records x G x width); each read costs (points x G x width).
Initialisation: the slicer and the reads use PyTorch's default init (orthogonal slice projection, as the AAAI SCA)
in every host; in Transolver and AB-UPT they are created after the host's own init, and the unnormalised output is
zeroed again after the host initialises its channels.

Two options added for c64 (2026-10-07; both off by default, so every earlier run computes exactly as before):
  * boundary.slice_assign = "spatial" (SpatialSlicer): slices grouped by spatial adjacency, the fallback PLAN section 4
    H5 registers for a non-monotone budget curve. Step 2 is replaced by a hard partition of the records into G parts,
    the same for every head: area-weighted recursive coordinate bisection of the record centroids (spatial_partition;
    records at identical positions always stay together, so splitting a record into copies of smaller weight leaves
    the partition, the masses and the tokens unchanged). Steps 3-4 are then segment sums. The partition depends only
    on the record positions and weights; it is computed on the CPU in float64 and cached by their content, so it is
    computed once per mesh and recomputed for other record sets (D4 meshes, K1 ladders, REC subsets, DCTS cases).
  * boundary.slice_lse_max (SliceTokenRead lse_max, default LSE_MAX = 30): the clamp of the unnormalised read's log
    mass factor. Below the clamp the read is unchanged bit for bit; above it the read is exp(lse_max) x the normalised
    read of the same scores. On R0-S the c56 runs drove this factor far past exp(30) (scratch/diag_c56), so a lower
    clamp bounds it while keeping the totals the read exists for (a LayerNorm on o_N would remove them).
Role-partitioned outlet slices (boundary.outlet_slices = G_o > 0, DCTS; R29 audit item 1, 10-07; off by default): the
server-outlet records (role one-hot) get G_o spatial slices of their own (OutletSlicer: spatial_partition of their
positions with unit weights, tokens through the host slicer's in_fx, no new parameters); the other records go to the
host slicer as before. A rack's exhaust is then a token of its own instead of 1e-3 of a learned slice's mass.
"""
from __future__ import annotations

import hashlib
import math
from collections import OrderedDict

import numpy as np
import torch
from torch import nn

from ..attention import MAX_SCORE_ELEMENTS, sdpa

LSE_MAX = 30.0          # exp(30) ~ 1e13: the unnormalised read's mass factor is clamped here (fp32 stays finite)
# Denominator of the slice means: M_g + TOTAL_EPS x the total record weight. Copy invariant (the total is), finite fp32
# gradients for an empty slice; a slice holding a fraction f of the weight keeps f / (f + 1e-8) of its token. It is
# the slicer's own convention, independent of the host trunk's context.eps.
TOTAL_EPS = 1e-8


def slice_den(mass, total, tot_eps=TOTAL_EPS):
    """Denominator of the slice means: mass [B,H,G] + tot_eps * total weight [B] (tot_eps = 0: mass alone)."""
    if tot_eps == 0:
        return mass.clamp_min(torch.finfo(mass.dtype).tiny)
    return mass + tot_eps * total.view(-1, 1, 1)


def mass_read(q, k, v, bias, max_elements=None, lse_max=LSE_MAX):
    """sum_g exp(q.k_g + bias_g) v_g for q [B,H,N,d] (already scaled), k/v [B,H,G,d], bias [B,H,1,G], with the total
    exponent clamped at lse_max (default LSE_MAX); rows are independent, so above the score threshold the queries run
    in chunks."""
    limit = MAX_SCORE_ELEMENTS if max_elements is None else max_elements
    b, h, n, _ = q.shape
    g = k.shape[-2]
    step = n if b * h * n * g <= limit else max(1, limit // (b * h * g))
    out = []
    for i in range(0, n, step):
        z = torch.matmul(q[:, :, i:i + step], k.transpose(-1, -2)) + bias                 # [B,H,n_i,G]
        lse = torch.logsumexp(z, dim=-1, keepdim=True)
        out.append(torch.matmul(torch.exp(z - (lse - lse_max).clamp_min(0)), v))
    return out[0] if len(out) == 1 else torch.cat(out, 2)


class BoundarySlicer(nn.Module):
    def __init__(self, width, heads, slices, tot_eps=TOTAL_EPS):
        super().__init__()
        if tot_eps < 0:
            raise ValueError('tot_eps must be >= 0 (an absolute eps would break the copy invariance)')
        self.h, self.dh, self.g, self.tot_eps = heads, width // heads, slices, float(tot_eps)
        self.in_x = nn.Linear(width, width)
        self.in_fx = nn.Linear(width, width)
        self.proj = nn.Linear(self.dh, slices)
        nn.init.orthogonal_(self.proj.weight)
        self.temperature = nn.Parameter(torch.full((1, 1, heads, 1), 0.5))

    def assignment(self, bt):
        """Soft slice weights [B,NB,H,G] of the lifted records bt [B,NB,W]."""
        b, nb, _ = bt.shape
        xs = self.in_x(bt).view(b, nb, self.h, self.dh)
        return torch.softmax(self.proj(xs) / self.temperature.clamp_min(0.05), dim=-1)

    def forward(self, bt, area, w=None):
        """bt [B,NB,W]: lifted records; area [B,NB] > 0: record weights. Returns tokens [B,H,G,dh] and log masses
        [B,H,G]. w overrides the assignment (tests). Masses and denominators are accumulated in at least fp32."""
        b, nb, _ = bt.shape
        if w is None:
            w = self.assignment(bt)
        fx = self.in_fx(bt).view(b, nb, self.h, self.dh)
        acc = torch.promote_types(w.dtype, torch.float32)
        a = area.to(acc)
        mass = torch.einsum('bnhg,bn->bhg', w.to(acc), a)                                          # [B,H,G]
        num = torch.einsum('bnhg,bnhd->bhgd', w, fx * area[:, :, None, None].to(fx.dtype))         # no [B,NB,H,G] product
        tok = num / slice_den(mass, a.sum(1), self.tot_eps).to(num.dtype)[..., None]
        return tok, torch.log(mass.clamp_min(torch.finfo(mass.dtype).tiny))


def spatial_partition(pos, weight, parts, tie_tol=1e-12):
    """Labels [n] in 0..parts-1 of n records at positions pos [n, d] with weights weight [n] > 0: area-weighted recursive
    coordinate bisection (numpy, float64, deterministic).

    A part that is to hold k slices is sorted lexicographically by its coordinates, the axis of largest extent first
    (the lowest axis on ties), and cut once into a first group of ceil(k/2) slices and a second of floor(k/2): the cut
    lies between two consecutive records of different position, at the cumulative weight closest to ceil(k/2)/k of
    the part's weight (among cuts within tie_tol x the part's weight of the closest, the first in the sorted order),
    and, when the part has at least k distinct positions, leaves at least as many distinct positions on each side as
    that side has slices, so that every slice receives records (on a graded mesh a few large faces would otherwise
    fill whole slices by area and leave others empty). Records at identical positions are never separated, so
    splitting a record into copies of smaller weight changes neither the extents, the distinct positions nor the
    cumulative weight at any allowed cut: the partition of the positions is the same. A part with fewer distinct
    positions than slices leaves some of its slices empty."""
    pos = np.asarray(pos, dtype=np.float64)
    w = np.asarray(weight, dtype=np.float64)
    n = len(pos)
    if pos.ndim != 2 or w.shape != (n,) or parts < 1:
        raise ValueError(f'expected pos [n, d], weight [n] and parts >= 1; got {pos.shape}, {w.shape}, {parts}')
    if n and not (np.isfinite(pos).all() and np.isfinite(w).all() and (w > 0).all()):
        raise ValueError('record positions must be finite and weights positive')
    labels = np.zeros(n, dtype=np.int64)
    stack = [(np.arange(n), 0, int(parts))]
    while stack:
        idx, first, k = stack.pop()
        if k == 1 or len(idx) < 2:
            labels[idx] = first
            continue
        p = pos[idx]
        ax = int(np.argmax(p.max(0) - p.min(0)))
        axes = [ax] + [a for a in range(p.shape[1]) if a != ax]
        o = idx[np.lexsort(tuple(p[:, a] for a in reversed(axes)))]       # stable: ties keep the index order
        ps = pos[o]
        allowed = np.nonzero(np.any(ps[1:] != ps[:-1], axis=1))[0]        # cut after row j (left = o[:j + 1])
        if len(allowed) == 0:
            labels[idx] = first
            continue
        cw = np.cumsum(w[o])
        k1 = (k + 1) // 2
        left = np.arange(1, len(allowed) + 1)                              # distinct positions left of each cut
        feasible = (left >= k1) & (len(allowed) + 1 - left >= k - k1)
        if feasible.any():
            allowed = allowed[feasible]
        dist = np.abs(cw[allowed] - cw[-1] * k1 / k)
        j = allowed[np.nonzero(dist <= dist.min() + tie_tol * cw[-1])[0][0]]
        stack.append((o[j + 1:], first + k1, k - k1))
        stack.append((o[:j + 1], first, k1))
    return labels


class SpatialSlicer(nn.Module):
    """Slices grouped by spatial adjacency (boundary.slice_assign = "spatial"): the records are split into `slices`
    parts by spatial_partition of their positions (the first pos_dims record columns) under the record weights, the
    same parts for every head; masses = the parts' weights, tokens = their weighted means of in_fx(h_y) (the
    BoundarySlicer with a one-hot assignment, computed as segment sums). Only in_fx carries parameters. Partitions are
    cached by the content of (positions, weights) in an LRU of `cache_size` entries."""
    spatial = True

    def __init__(self, width, heads, slices, pos_dims, tot_eps=TOTAL_EPS, cache_size=1024):
        super().__init__()
        if tot_eps < 0:
            raise ValueError('tot_eps must be >= 0 (an absolute eps would break the copy invariance)')
        if pos_dims is None or pos_dims < 1:
            raise ValueError('spatial slices need the number of position columns of the records (pos_dims)')
        self.h, self.dh, self.g, self.tot_eps = heads, width // heads, slices, float(tot_eps)
        self.pos_dims = int(pos_dims)
        self.in_fx = nn.Linear(width, width)
        self.cache_size = cache_size
        self._cache = OrderedDict()

    def labels(self, pos, weight):
        """[NB] partition labels (CPU int64) of one record set: pos [NB, pos_dims], weight [NB]."""
        p = pos.detach().to('cpu', torch.float64).numpy()
        w = weight.detach().to('cpu', torch.float64).numpy()
        key = (p.shape, self.g, hashlib.blake2b(np.ascontiguousarray(p).tobytes() + np.ascontiguousarray(w).tobytes(),
                                                digest_size=20).hexdigest())
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        lab = torch.from_numpy(spatial_partition(p, w, self.g))
        self._cache[key] = lab
        while len(self._cache) > self.cache_size:
            self._cache.popitem(last=False)
        return lab

    def forward(self, bt, area, pos):
        """bt [B,NB,W]: lifted records; area [B,NB] > 0: record weights; pos [B,NB,pos_dims]: record positions.
        Returns tokens [B,H,G,dh] and log masses [B,H,G] (masses and denominators in at least fp32)."""
        b, nb, _ = bt.shape
        fx = self.in_fx(bt).view(b, nb, self.h, self.dh)
        acc = torch.promote_types(fx.dtype, torch.float32)
        toks, masses = [], []
        for i in range(b):
            lab = self.labels(pos[i, :, :self.pos_dims], area[i]).to(bt.device)
            a = area[i].to(acc)
            mass = torch.zeros(self.g, dtype=acc, device=bt.device).index_add_(0, lab, a)               # [G]
            num = torch.zeros(self.g, self.h, self.dh, dtype=fx.dtype, device=bt.device).index_add_(
                0, lab, fx[i] * area[i][:, None, None].to(fx.dtype))                                      # [G,H,dh]
            mass = mass[None].expand(self.h, self.g)                                                      # [H,G]
            den = slice_den(mass[None], a.sum()[None], self.tot_eps)[0]
            toks.append((num.transpose(0, 1) / den.to(num.dtype)[..., None]))
            masses.append(mass)
        mass = torch.stack(masses)
        return torch.stack(toks), torch.log(mass.clamp_min(torch.finfo(mass.dtype).tiny))


class OutletSlicer(nn.Module):
    """Role-partitioned slices (boundary.outlet_slices = G_o > 0; DCTS, R29 audit item 1): the server-outlet records,
    marked by their role one-hot (column outlet_col of the records), get G_o slices of their own, a spatial partition
    of their positions weighted by their PHYSICAL face areas (passed apart from the record weights: geometry only, so a
    changed load never moves a part, and splitting a face into copies changes nothing; spatial_partition); every
    other record goes to the host's slicer as before (`base`, learned or spatial). The outlet tokens are segment means
    of base.in_fx(h_y) under the record weights (the same projection as the learned tokens: no new parameters, and the
    base slicer's parameters are created exactly as without the option), their masses the parts' weights; the tokens
    and log masses of both groups are concatenated (G + G_o slices). An empty part has mass 0, log mass log(tiny) and a
    zero token, so both reads ignore it. Partitions are cached by the content of the outlet positions and areas."""
    outlet = True

    def __init__(self, base, outlet_slices, outlet_col, pos_dims, tot_eps=TOTAL_EPS, cache_size=1024):
        super().__init__()
        if outlet_slices < 1 or outlet_col is None or pos_dims is None or pos_dims < 1:
            raise ValueError('outlet slices need outlet_slices >= 1, the outlet role column and pos_dims')
        self.base = base
        self.go, self.outlet_col, self.pos_dims, self.tot_eps = int(outlet_slices), int(outlet_col), int(pos_dims), \
            float(tot_eps)
        self.h, self.dh = base.h, base.dh
        self.cache_size = cache_size
        self._cache = OrderedDict()

    def labels(self, pos, area):
        p = pos.detach().to('cpu', torch.float64).numpy()
        w = area.detach().to('cpu', torch.float64).numpy()
        key = (p.shape, self.go, hashlib.blake2b(np.ascontiguousarray(p).tobytes() + np.ascontiguousarray(w).tobytes(),
                                                 digest_size=20).hexdigest())
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        lab = torch.from_numpy(spatial_partition(p, w, self.go))
        self._cache[key] = lab
        while len(self._cache) > self.cache_size:
            self._cache.popitem(last=False)
        return lab

    def forward(self, bt, area, pos, outlet, phys_area):
        """bt [B,NB,W], area [B,NB] > 0 (record weights), pos [B,NB,pos_dims], outlet [B,NB] bool, phys_area [B,NB] > 0
        (physical face areas, for the partition). Returns tokens [B,H,G+G_o,dh] and log masses [B,H,G+G_o]."""
        toks, logm = [], []
        for i in range(bt.shape[0]):
            o = outlet[i]
            r = ~o
            if getattr(self.base, 'spatial', False):
                tb, lb = self.base(bt[i:i + 1, r], area[i:i + 1, r], pos[i:i + 1, r])
            else:
                tb, lb = self.base(bt[i:i + 1, r], area[i:i + 1, r])
            fx = self.base.in_fx(bt[i, o]).view(-1, self.h, self.dh)
            acc = torch.promote_types(fx.dtype, torch.float32)
            a = area[i, o].to(acc)
            lab = (self.labels(pos[i, o], phys_area[i, o]) if o.any() else torch.zeros(0, dtype=torch.long)).to(bt.device)
            mass = torch.zeros(self.go, dtype=acc, device=bt.device).index_add_(0, lab, a)
            num = torch.zeros(self.go, self.h, self.dh, dtype=fx.dtype, device=bt.device).index_add_(
                0, lab, fx * area[i, o][:, None, None].to(fx.dtype))
            mass = mass[None].expand(self.h, self.go)
            den = slice_den(mass[None], area[i].to(acc).sum()[None], self.tot_eps)[0]
            to = num.transpose(0, 1) / den.to(num.dtype)[..., None]                                 # [H,G_o,dh]
            toks.append(torch.cat([tb[0], to], 1))
            logm.append(torch.cat([lb[0], torch.log(mass.clamp_min(torch.finfo(mass.dtype).tiny)).to(lb.dtype)], 1))
        return torch.stack(toks), torch.stack(logm)


class SliceTokenRead(nn.Module):
    """Per-layer read of the slice tokens by every point: normalised path, plus the unnormalised path when
    unnormalized is True. Called as host channels are: read(x [B,N,W], tokens [B,H,G,dh], log_mass [B,H,G])."""

    def __init__(self, width, heads, unnormalized=True, mass_ref=1.0, lse_max=LSE_MAX):
        super().__init__()
        if not lse_max > 0:
            raise ValueError('lse_max must be > 0')
        self.h, self.dh = heads, width // heads
        self.log_ref = math.log(mass_ref)
        self.lse_max = float(lse_max)
        self.q = nn.Linear(width, width)
        self.kv = nn.Linear(self.dh, 2 * self.dh)
        self.out = nn.Linear(width, width)
        self.unnormalized = unnormalized
        if unnormalized:
            self.q_n = nn.Linear(width, width)
            self.kv_n = nn.Linear(self.dh, 2 * self.dh)
            self.out_n = nn.Linear(width, width)
            nn.init.zeros_(self.out_n.weight)
            nn.init.zeros_(self.out_n.bias)

    def forward(self, x, tok, log_mass):
        b, n, _ = x.shape
        lm = log_mass[:, :, None, :]                                                      # [B,H,1,G]
        q = self.q(x).view(b, n, self.h, self.dh).transpose(1, 2)                          # [B,H,N,dh]
        k, v = self.kv(tok).chunk(2, dim=-1)                                               # [B,H,G,dh]
        o = sdpa(q, k, v, attn_mask=lm.to(q.dtype))
        y = self.out(o.transpose(1, 2).reshape(b, n, self.h * self.dh))
        if self.unnormalized:
            q_n = self.q_n(x).view(b, n, self.h, self.dh).transpose(1, 2) * self.dh ** -0.5
            k_n, v_n = self.kv_n(tok).chunk(2, dim=-1)
            o_n = mass_read(q_n, k_n, v_n, (lm - self.log_ref).to(q_n.dtype), lse_max=self.lse_max)
            y = y + self.out_n(o_n.transpose(1, 2).reshape(b, n, self.h * self.dh))
        return y


# Near-field read (boundary.near_radius = r > 0; c102, the fix registered in c96 section 3 for a compression loss).
# The slice tokens carry the far field of the boundary integral: Proposition R1 bounds a coarse aggregate's error by
# (a / r)^2 at distance r from a patch of size a, so close to the walls the tokens cannot replace the faces. Beside the
# token read, every point therefore reads the face records within physical distance r exactly: per-record attention
# with the log-area logit (the same self-normalised quadrature as the per-face channel, restricted to the disc of radius
# r, so its weights converge under refinement and copying a record leaves it unchanged), with the record's offset from
# the point (scaled by 1 / r) added to its key. At most near_k records are read (the nearest); points with no record
# within r read nothing. The output projection starts at zero, so a fresh model computes exactly as without the read.

def near_records(pts, rpos, radius, k, chunk=4096):
    """(idx [N, k'] long, valid [N, k'] bool, rel [N, k', P]) for points pts [N, P] and records rpos [NB, P] in the same
    coordinates: the k' = min(k, NB) nearest records of every point (torch.cdist in point chunks), valid where the
    distance is <= radius, rel = (record - point) / radius. No gradient (positions are inputs)."""
    if not radius > 0 or k < 1:
        raise ValueError('near_records needs radius > 0 and k >= 1')
    with torch.no_grad():
        kk = min(int(k), rpos.shape[0])
        r = rpos.float()
        idx, dist = [], []
        for i in range(0, pts.shape[0], chunk):
            d = torch.cdist(pts[i:i + chunk].float(), r)
            dv, di = d.topk(kk, dim=1, largest=False, sorted=True)
            idx.append(di)
            dist.append(dv)
        idx, dist = torch.cat(idx), torch.cat(dist)
        rel = (rpos[idx] - pts[:, None, :]) / radius
    return idx, dist <= radius, rel


class NearFieldRead(nn.Module):
    """The near-field read (module comment above). forward(x [1,N,W], near) with near = {'bt': [1,NB,W] lifted face
    records, 'log_w': [1,NB] log record weights, 'idx', 'valid', 'rel': near_records of the N points} -> [1,N,W]."""

    def __init__(self, width, heads, pos_dims):
        super().__init__()
        self.h, self.dh = heads, width // heads
        self.q = nn.Linear(width, width)
        self.k = nn.Linear(width, width)
        self.v = nn.Linear(width, width)
        self.rel = nn.Linear(pos_dims, width)
        self.out = nn.Linear(width, width)
        nn.init.zeros_(self.out.weight)
        nn.init.zeros_(self.out.bias)

    def forward(self, x, near, chunk=8192):
        if x.shape[0] != 1:
            raise ValueError('the near-field read runs one case per forward (batch 1)')
        n, w = x.shape[1], x.shape[2]
        q = self.q(x[0]).view(n, self.h, self.dh)
        kb, vb, lw = self.k(near['bt'][0]), self.v(near['bt'][0]), near['log_w'][0]
        idx, valid, rel = near['idx'], near['valid'], near['rel']
        scale = self.dh ** -0.5
        outs = []
        for i in range(0, n, chunk):
            ii, ok = idx[i:i + chunk], valid[i:i + chunk]
            c, kk = ii.shape
            key = (kb[ii] + self.rel(rel[i:i + chunk].to(x.dtype))).view(c, kk, self.h, self.dh)
            val = vb[ii].view(c, kk, self.h, self.dh)
            s = torch.einsum('chd,ckhd->chk', q[i:i + chunk], key) * scale + lw[ii][:, None, :].to(x.dtype)
            s = s.masked_fill(~ok[:, None, :], -1e30)
            a = torch.softmax(s, -1) * ok[:, None, :].to(x.dtype)      # a point with no record within r reads 0
            outs.append(torch.einsum('chk,ckhd->chd', a, val).reshape(c, w))
        return self.out(torch.cat(outs)[None])
