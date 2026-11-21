"""Fused sliced-ELL block kernels for the coupled-AMG V-cycle.

Roadmap ``docs/research/2026-10-07-gpu-optimisation-roadmap.md`` B2.2 / B2.3.  The fast kernels
(``block_amg.FastPattern``) apply every level matrix as a SCALAR CSR through cuSPARSE: per GS colour two SpMVs and
three elementwise kernels, and 25 x (8 + 4) = 300 bytes per 5 x 5 block (value + column index of every scalar
entry).  Here a level matrix is stored by BLOCKS in a sliced ELLPACK layout and one Triton kernel does a whole
multicolour-GS colour pass (``dx_c = omega Sinv_c (b - A x)_c``), a residual ``b - A x``, a product ``A x`` or an
update ``y += A x``:

* 4 bytes of column index per block instead of per scalar: 204 bytes per fp64 block (-32 %), 104 per block with the
  values stored in fp32 (``value_dtype=torch.float32``, -65 %).  The ARITHMETIC is fp64 in both cases: a value is
  converted to fp64 in registers and multiplies an fp64 vector entry, sums are fp64.  With fp32 storage the cycle is
  therefore the V-cycle of the hierarchy whose values were rounded to fp32 ONCE -- a fixed linear map (to fp64
  rounding, as the fp64 cycle), so right-preconditioned GMRES stays valid (no flexible GMRES: the measured floor at
  2e-4 came from fp32 ARITHMETIC, i.e. a preconditioner that is not linear at the 1e-7 level).
* One launch per colour (+ one in-place add) instead of five; deterministic (no atomics: each row's sum is taken in
  a fixed order), so two applications are bitwise equal.

Layout (``BlockLayout``): the unknowns of one level, cells in their GS-colour order (the ``FastLayout`` rank), the
``F`` fields of a cell interleaved: position ``F rank(i) + f``.  A colour is a contiguous slice.

Storage (``SellPattern`` / ``SellMatrix``): rows (in a row layout's rank order) are cut into slices of ``C`` rows
that never cross a group (colour) boundary; slice ``s`` with ``K_s`` = its longest row stores its blocks slot-major,
``values[vbase_s + (k FG + q) C + r]`` = entry ``q`` (row field ``q // G``, column field ``q % G``) of the ``k``-th
block (column order) of row ``r`` of the slice, and ``col[cbase_s + k C + r]`` its column (rank in the column
layout).  A warp then reads ``C`` consecutive values per entry ``q``: coalesced.  Slots past a row's length are
never read (masked by the row length), so their padding costs memory, not bandwidth.

Without Triton (CPU, or a CUDA device where the smoke kernel fails) every operation has a torch reference that
decodes the same storage (``SellMatrix.reference_*``); tests compare the two and both against the block and E3 paths.
"""
from __future__ import annotations

import os

import torch

SLICE = 32                         # the largest slice height (rows per Triton program)
# slice height / slots per iteration policy of a pattern built without explicit ones (SellPattern): C = the power of
# two nearest below (average rows per group) / programs, within [c_min, c_max]; kb = lanes // C (so C kb rows x slots
# are in flight per program).  A module dict so the tuning bench (GPUoptM3/krylov_profile.py) can vary it.
POLICY = {"programs": 256, "lanes": 32, "c_min": 4, "c_max": SLICE, "kb_full": 2, "pdl": 0, "tile4": 0}
# ("tile4" 1, W2 opt-in: the [C, KB, F, G] tile kernel ``_sell_kernel4`` for every sliced-ELL operation; 2 (W2f): for
#  the GS colour passes only -- the W2e kernel bench: tile4 7-12 % faster per sweep on W01's levels 0 / 1, 2-6 % slower
#  for their residuals)
# ("pdl" 1, W2 opt-in via --camg-sell-policy pdl=1: the sliced-ELL kernels are programmatic dependent launches)
# (measured: at the full slice height 2 slots per iteration are the fastest GS sweep on the 64^3 / 88^3 /
# 128^3 fine levels -- 4.28 vs 5.18 ms fp64 at 128^3 -- and 4 rows x 8 slots on their coarse level 1)
_FILL_CHUNK = 1 << 21              # blocks per chunk of a fill / inverse fill (index temporaries ~ chunk x FG x 8 B)
# Warp backend (off = the default kernels, bit for bit):
#   ROWS_KERNEL  ``--camg-rows-kernel``: patterns with slice height >= min_c run the Warp thread-per-row kernel
#                (``block_kernels_warp``) instead of the Triton tile (same storage, sums in another order)
#   ZERO_SKIP    ``--camg-zero-skip``: the first pre-smoothing sweep from x = 0 skips the blocks whose column colour is
#                not below the row's (they multiply exact zeros: the same pass bit for bit, ~half its bytes)
ROWS_KERNEL = {"on": False, "min_c": 16}
ZERO_SKIP = {"on": False}
#   WARP_FILL    ``--camg-warp-fill`` (W6): ``SellPattern.fill`` as one Warp kernel (the same values in the same places)
WARP_FILL = {"on": False}

try:                               # pragma: no cover - availability depends on the build
    import triton
    import triton.language as tl
    HAVE_TRITON = True
    try:                           # grid dependency control (programmatic dependent launch, sm_90+): POLICY["pdl"]
        import triton.language.extra.cuda  # noqa: F401
    except Exception:              # noqa: BLE001
        pass
except Exception:                  # noqa: BLE001
    triton = tl = None
    HAVE_TRITON = False

# process-wide state: whether the Triton kernels run on CUDA here (decided once by a smoke test), the error if not
TRITON_STATE = {"checked": False, "ok": False, "error": None}


def _pow2_floor(n: int) -> int:
    p = 1
    while 2 * p <= n:
        p *= 2
    return p


def _pow2(n: int) -> int:
    p = 1
    while p < n:
        p *= 2
    return p


# =================================================================================================== Triton kernel
if HAVE_TRITON:                    # pragma: no cover - compiled on the GPU only

    @triton.jit(do_not_specialize=["slice0", "lo_col"])
    def _sell_kernel(row0_ptr, nrow_ptr, K_ptr, vbase_ptr, cbase_ptr, len_ptr, val_ptr, col_ptr,
                     x_ptr, b_ptr, y_ptr, sinv_ptr, omega_ptr, slice0, lo_col,
                     MODE: tl.constexpr, F: tl.constexpr, G: tl.constexpr, C: tl.constexpr, QP: tl.constexpr,
                     KB: tl.constexpr, ZHI: tl.constexpr, PDL: tl.constexpr):
        """One slice of ``C`` rows; ``KB`` block slots of every row per iteration, ``QP`` (>= F G, a power of two)
        lanes per block, so a row's tile is ``[KB QP]`` lanes (lane j: slot ``j // QP``, entry ``q = j % QP``, column
        field ``q % G``).  MODE 0: y = A x; 1: y = b - A x; 2: y += A x; 3: y = omega Sinv (b - A x) on the slice's
        rows; 4: x += omega Sinv (b - A x) on them, in place (proper colourings only).  ``sinv`` is stored per slice,
        slot-major like the values (``SellPattern.diagonal_store``).  ``PDL`` (W2, opt-in): launched as a
        programmatic dependent launch, the program reads its slice tables (static), then waits for the previous
        kernel (griddepcontrol.wait) before any vector access, and lets the next kernel launch after its stores."""
        s = slice0 + tl.program_id(0)
        row0 = tl.load(row0_ptr + s)
        nrow = tl.load(nrow_ptr + s)
        K = tl.load(K_ptr + s)
        vbase = tl.load(vbase_ptr + s)
        cbase = tl.load(cbase_ptr + s)
        if PDL:
            tl.extra.cuda.gdc_wait()
        r = tl.arange(0, C)
        j = tl.arange(0, KB * QP)
        kk = j // QP
        q = j % QP
        g = q % G
        qok = q < F * G
        rmask = r < nrow
        rows = (row0 + r).to(tl.int64)
        lens = tl.load(len_ptr + rows, mask=rmask, other=0)
        acc = tl.zeros([C, KB * QP], dtype=tl.float64)
        for k0 in range(0, K, KB):
            slot = k0 + kk
            live = rmask[:, None] & (slot[None, :] < lens[:, None]) & qok[None, :]
            cols = tl.load(col_ptr + cbase + slot[None, :] * C + r[:, None], mask=live, other=0).to(tl.int64)
            if ZHI:
                # W2 zero skip (first sweep from x = 0): columns at or past lo_col (rank order = colour order) hold
                # exact zeros of x; their values are not read
                live = live & (cols < lo_col)
            xg = tl.load(x_ptr + cols * G + g[None, :], mask=live, other=0.0)
            v = tl.load(val_ptr + vbase + (slot[None, :] * (F * G) + q[None, :]) * C + r[:, None], mask=live,
                        other=0.0)
            acc += v.to(tl.float64) * xg.to(tl.float64)
        if KB > 1:
            acc2 = tl.sum(tl.reshape(acc, [C, KB, QP]), axis=1)
        else:
            acc2 = acc
        q2 = tl.arange(0, QP)
        if MODE < 3:
            for f in tl.static_range(F):
                t = tl.sum(tl.where(((q2 >= f * G) & (q2 < (f + 1) * G))[None, :], acc2, 0.0), axis=1)
                yo = rows * F + f
                if MODE == 0:
                    tl.store(y_ptr + yo, t, mask=rmask)
                elif MODE == 1:
                    bv = tl.load(b_ptr + yo, mask=rmask, other=0.0)
                    tl.store(y_ptr + yo, bv - t, mask=rmask)
                else:
                    yv = tl.load(y_ptr + yo, mask=rmask, other=0.0)
                    tl.store(y_ptr + yo, yv + t, mask=rmask)
        else:
            # res at every lane's column field: res_q[r, q] = (b - A x)[r, q % F]
            g2 = q2 % F
            res_q = tl.zeros([C, QP], dtype=tl.float64)
            for f in tl.static_range(F):
                t = tl.sum(tl.where(((q2 >= f * F) & (q2 < (f + 1) * F))[None, :], acc2, 0.0), axis=1)
                bv = tl.load(b_ptr + rows * F + f, mask=rmask, other=0.0)
                res_q += tl.where((g2 == f)[None, :], (bv - t)[:, None], 0.0)
            S = tl.load(sinv_ptr + s.to(tl.int64) * (F * F * C) + q2[None, :] * C + r[:, None],
                        mask=rmask[:, None] & (q2 < F * F)[None, :], other=0.0)
            prod = S.to(tl.float64) * res_q
            omega = tl.load(omega_ptr)
            for f in tl.static_range(F):
                dx = tl.sum(tl.where(((q2 >= f * F) & (q2 < (f + 1) * F))[None, :], prod, 0.0), axis=1)
                yo = rows * F + f
                if MODE == 3:
                    tl.store(y_ptr + yo, dx * omega, mask=rmask)
                else:
                    xo = tl.load(x_ptr + yo, mask=rmask, other=0.0)
                    tl.store(x_ptr + yo, xo + dx * omega, mask=rmask)
        if PDL:
            tl.extra.cuda.gdc_launch_dependents()


if HAVE_TRITON:                    # pragma: no cover - compiled on the GPU only

    @triton.jit(do_not_specialize=["slice0", "lo_col"])
    def _sell_kernel4(row0_ptr, nrow_ptr, K_ptr, vbase_ptr, cbase_ptr, len_ptr, val_ptr, col_ptr,
                      x_ptr, b_ptr, y_ptr, sinv_ptr, omega_ptr, slice0, lo_col,
                      MODE: tl.constexpr, F: tl.constexpr, G: tl.constexpr, C: tl.constexpr, FP: tl.constexpr,
                      GP: tl.constexpr, KB: tl.constexpr, ZHI: tl.constexpr, PDL: tl.constexpr):
        """``_sell_kernel`` on a ``[C, KB, FP, GP]`` tile (W2 opt-in, ``POLICY["tile4"]``): the column of a block is
        loaded once (``[C, KB]``) and its G entries of x once (``[C, KB, GP]``), both broadcast over the block's F x G
        entries, instead of once per entry lane (the flat ``[C, KB x 32]`` tile loads each column 25 times and each x
        entry 5 times per 5 x 5 block).  Same storage and modes; sums in another order.  ``PDL`` as in
        ``_sell_kernel`` (W2f: the slice tables read, then griddepcontrol.wait before any vector or value access)."""
        s = slice0 + tl.program_id(0)
        row0 = tl.load(row0_ptr + s)
        nrow = tl.load(nrow_ptr + s)
        K = tl.load(K_ptr + s)
        vbase = tl.load(vbase_ptr + s)
        cbase = tl.load(cbase_ptr + s)
        r = tl.arange(0, C)
        kk = tl.arange(0, KB)
        f = tl.arange(0, FP)
        g = tl.arange(0, GP)
        rmask = r < nrow
        rows = (row0 + r).to(tl.int64)
        lens = tl.load(len_ptr + rows, mask=rmask, other=0)
        if PDL:
            tl.extra.cuda.gdc_wait()
        q = f[:, None] * G + g[None, :]                                          # [FP, GP]
        fg_ok = (f[:, None] < F) & (g[None, :] < G)
        acc = tl.zeros([C, KB, FP, GP], dtype=tl.float64)
        for k0 in range(0, K, KB):
            slot = k0 + kk
            live = rmask[:, None] & (slot[None, :] < lens[:, None])             # [C, KB]
            cols = tl.load(col_ptr + cbase + slot[None, :] * C + r[:, None], mask=live, other=0).to(tl.int64)
            if ZHI:
                live = live & (cols < lo_col)
            xg = tl.load(x_ptr + cols[:, :, None] * G + g[None, None, :],
                         mask=live[:, :, None] & (g < G)[None, None, :], other=0.0)  # [C, KB, GP]
            v = tl.load(val_ptr + vbase + (slot[None, :, None, None] * (F * G) + q[None, None, :, :]) * C
                        + r[:, None, None, None], mask=live[:, :, None, None] & fg_ok[None, None, :, :], other=0.0)
            acc += v.to(tl.float64) * xg[:, :, None, :].to(tl.float64)
        t = tl.sum(tl.sum(acc, axis=3), axis=1)                                 # [C, FP]
        yo = rows[:, None] * F + f[None, :]
        omask = rmask[:, None] & (f[None, :] < F)
        if MODE == 0:
            tl.store(y_ptr + yo, t, mask=omask)
        elif MODE == 1:
            bv = tl.load(b_ptr + yo, mask=omask, other=0.0)
            tl.store(y_ptr + yo, bv - t, mask=omask)
        elif MODE == 2:
            yv = tl.load(y_ptr + yo, mask=omask, other=0.0)
            tl.store(y_ptr + yo, yv + t, mask=omask)
        else:
            bv = tl.load(b_ptr + yo, mask=omask, other=0.0)
            res = bv - t                                                         # [C, FP]; zero on padded f
            qs = f[:, None] * F + g[None, :]
            smask = rmask[:, None, None] & ((f[:, None] < F) & (g[None, :] < F))[None, :, :]
            S = tl.load(sinv_ptr + s.to(tl.int64) * (F * F * C) + qs[None, :, :] * C + r[:, None, None], mask=smask,
                        other=0.0)                                               # [C, FP, GP]
            dx = tl.sum(S.to(tl.float64) * res[:, None, :], axis=2)              # GP == FP: res's index is g here
            omega = tl.load(omega_ptr)
            if MODE == 3:
                tl.store(y_ptr + yo, dx * omega, mask=omask)
            else:
                xo = tl.load(x_ptr + yo, mask=omask, other=0.0)
                tl.store(x_ptr + yo, xo + dx * omega, mask=omask)
        if PDL:
            tl.extra.cuda.gdc_launch_dependents()


def triton_available(device) -> bool:
    """True when the Triton kernels run on ``device``: a CUDA device, Triton importable, ``LPCNS_NO_TRITON`` unset,
    and a smoke test (a 2-block product in every mode, checked against the torch reference) passed once in this
    process.  The first failure is kept in ``TRITON_STATE['error']`` and every later call says False."""
    if not HAVE_TRITON or os.environ.get("LPCNS_NO_TRITON"):
        return False
    if torch.device(device).type != "cuda":
        return False
    if not TRITON_STATE["checked"]:
        TRITON_STATE["checked"] = True
        # the smoke test checks the Triton kernels and the torch fill alone: the opt-in Warp paths (W2 / W6) are off
        # while it runs (a Warp fill variant that does not compile inside the smoke test would disable
        # Triton for the whole process)
        saved = (dict(ROWS_KERNEL), dict(WARP_FILL))
        ROWS_KERNEL["on"] = WARP_FILL["on"] = False
        try:
            _smoke(torch.device(device))
            TRITON_STATE["ok"] = True
        except Exception as exc:   # noqa: BLE001 -- any failure (compiler, driver, wrong result) disables the kernels
            TRITON_STATE["ok"] = False
            TRITON_STATE["error"] = f"{type(exc).__name__}: {exc}"[:600]
        finally:
            ROWS_KERNEL.update(saved[0])
            WARP_FILL.update(saved[1])
    return TRITON_STATE["ok"]


def _smoke(device) -> None:
    """Every kernel mode once, on a 5-cell pattern with two groups, fp64 and fp32 values, against the reference."""
    gen = torch.Generator(device="cpu").manual_seed(7)
    n, F = 5, 3
    row = torch.tensor([0, 0, 1, 2, 2, 3, 4, 4], device=device)
    col = torch.tensor([0, 2, 1, 2, 4, 3, 0, 4], device=device)
    val = torch.randn(row.numel(), F, F, generator=gen, dtype=torch.float64).to(device)
    S = torch.randn(n, F, F, generator=gen, dtype=torch.float64).to(device)
    x = torch.randn(n * F, generator=gen, dtype=torch.float64).to(device)
    b = torch.randn(n * F, generator=gen, dtype=torch.float64).to(device)
    pat = SellPattern(row, col, n, n, F, F, groups=[0, 2, 5], C=4)
    omega = torch.tensor([0.7], dtype=torch.float64, device=device)
    for dtype in (torch.float64, torch.float32):
        M = pat.fill(val, dtype)
        sinv = pat.diagonal_store(S, dtype)
        ref = M.reference_mv(x)
        checks = {}
        y = torch.empty_like(x)
        M._launch(0, x, None, y)
        checks["mv"] = (y, ref)
        y = torch.empty_like(x)
        M._launch(1, x, b, y)
        checks["residual"] = (y, b - ref)
        y = b.clone()
        M._launch(2, x, None, y)
        checks["mv_add"] = (y, b + ref)
        dx = torch.zeros_like(x)
        M._launch(3, x, b, dx, sinv=sinv, omega=omega, group=1)
        lo, hi = pat.group_rows(1)
        want = torch.zeros_like(x)
        want[F * lo:F * hi] = torch.einsum("nfg,ng->nf", M.reference_diagonal(sinv, (lo, hi)),
                                           (b - ref)[F * lo:F * hi].view(-1, F)).reshape(-1) * 0.7
        checks["gs"] = (dx, want)
        # MODE 4 (in place) on a pattern whose groups are a proper colouring
        prow = torch.tensor([0, 0, 1, 2, 3, 4, 4], device=device)
        pcol = torch.tensor([0, 2, 1, 2, 3, 0, 4], device=device)
        ppat = SellPattern(prow, pcol, n, n, F, F, groups=[0, 2, 5], C=4)
        if not ppat.proper or pat.proper:
            raise RuntimeError("SellPattern.proper misjudged the smoke patterns")
        PM = ppat.fill(val[:prow.numel()], dtype)
        psinv = ppat.diagonal_store(S, dtype)
        xi = x.clone()
        PM._launch(4, xi, b, xi, sinv=psinv, omega=omega, group=1)
        lo, hi = ppat.group_rows(1)
        want = x.clone()
        want[F * lo:F * hi] += torch.einsum("nfg,ng->nf", PM.reference_diagonal(psinv, (lo, hi)),
                                            (b - PM.reference_mv(x))[F * lo:F * hi].view(-1, F)).reshape(-1) * 0.7
        checks["gs_inplace"] = (xi, want)
        for name, (got, exp) in checks.items():
            if not torch.allclose(got, exp, rtol=1e-12, atol=1e-12):
                raise RuntimeError(f"Triton SELL kernel '{name}' ({dtype}) differs from the reference by "
                                   f"{float((got - exp).abs().max()):.3e}")


# =================================================================================================== storage
class BlockLayout:
    """Cell-interleaved vector layout of one level in its rank order (M3): unknown (cell i, field f) at
    ``F rank[i] + f``; group c (a GS colour) is the slice ``[F off_c, F off_{c+1})``.  ``rank`` / ``offsets`` are
    those of the level's ``FastLayout`` (same colour order, same in-colour order)."""

    def __init__(self, rank: torch.Tensor, offsets: list, F: int):
        self.rank = rank
        self.n, self.F = int(rank.numel()), int(F)
        self.offsets = [int(o) for o in offsets]
        self.slices = [(self.F * a, self.F * b) for a, b in zip(self.offsets[:-1], self.offsets[1:])]
        self.natural = bool(self.n == 0 or torch.equal(rank, torch.arange(self.n, device=rank.device)))
        self._index = {}

    def _gather(self, kind: str, direction: str) -> torch.Tensor:
        idx = self._index.get((kind, direction))
        if idx is None:
            n, F, dev = self.n, self.F, self.rank.device
            cells = torch.arange(n, device=dev)
            f = torch.arange(F, device=dev)
            pos = (F * self.rank[:, None] + f[None, :]).reshape(-1)                 # [n F] in (cell, field) order
            src = (f[None, :] * n + cells[:, None]).reshape(-1) if kind == "fb" else torch.arange(n * F, device=dev)
            if direction == "in":            # layout[pos] = vec[src]  ->  layout = vec[idx]
                idx = torch.empty(n * F, dtype=torch.long, device=dev)
                idx[pos] = src
            else:                            # vec[src] = layout[pos]  ->  vec = layout[idx]
                idx = torch.empty(n * F, dtype=torch.long, device=dev)
                idx[src] = pos
            self._index[(kind, direction)] = idx
        return idx

    def to_layout(self, vec: torch.Tensor, kind: str = "fb") -> torch.Tensor:
        if self.natural and kind == "ci":
            return vec
        return vec.index_select(0, self._gather(kind, "in"))

    def from_layout(self, vec: torch.Tensor, kind: str = "fb") -> torch.Tensor:
        if self.natural and kind == "ci":
            return vec
        return vec.index_select(0, self._gather(kind, "out"))

    def tensors(self) -> list:
        return list(self._index.values())


class SellPattern:
    """Symbolic sliced-ELL storage of an ``F x G`` block pattern (see the module docstring).

    ``row_pos`` / ``col_pos``: each block's row in the row layout's rank order and column in the column layout's
    rank order; ``n_rows`` / ``n_cols`` cells.  ``groups``: row offsets slices must not cross (a level's colour
    offsets), default one group.  Within a row the blocks are kept in column-rank order (the summation order of the
    product).  Built once per pattern and cached on the plan level; ``fill`` makes a ``SellMatrix`` per numeric
    phase."""
    def __init__(self, row_pos: torch.Tensor, col_pos: torch.Tensor, n_rows: int, n_cols: int, F: int, G: int,
                 groups=None, C: int | None = None, host_order: bool = False, kb: int | None = None):
        device = row_pos.device
        self.n_rows, self.n_cols = int(n_rows), int(n_cols)
        bounds = [0, self.n_rows] if groups is None else [int(b) for b in groups]
        if bounds[0] != 0 or bounds[-1] != self.n_rows:
            raise ValueError(f"groups {bounds[:3]}...{bounds[-2:]} do not cover the {self.n_rows} rows")
        # slice height and slots per iteration (``POLICY``): C rows per program, chosen so a group (a colour pass)
        # has ~POLICY['programs'] programs where it can; kb slots per iteration so that C kb ~ POLICY['lanes']
        if C is None:
            sizes = [b - a for a, b in zip(bounds[:-1], bounds[1:]) if b > a]
            avg = (sum(sizes) / len(sizes)) if sizes else 1.0
            C = _pow2_floor(max(1, int(avg // POLICY["programs"])))
            C = min(POLICY["c_max"], max(POLICY["c_min"], C))
        if kb is None:
            kb = POLICY["kb_full"] if int(C) >= POLICY["c_max"] else max(1, POLICY["lanes"] // int(C))
        self.F, self.G, self.C, self.kb = int(F), int(G), int(C), int(kb)
        if self.C & (self.C - 1) or self.kb & (self.kb - 1):
            raise ValueError(f"slice height {self.C} and slots per iteration {self.kb} must be powers of two")
        row_pos, col_pos = row_pos.long(), col_pos.long()
        nnzb = int(row_pos.numel())
        order = torch.argsort(row_pos * max(self.n_cols, 1) + col_pos)
        counts = torch.bincount(row_pos, minlength=self.n_rows) if nnzb else torch.zeros(
            self.n_rows, dtype=torch.long, device=device)
        ptr = torch.zeros(self.n_rows + 1, dtype=torch.long, device=device)
        ptr[1:] = torch.cumsum(counts, 0)
        row0 = [torch.arange(a, b, self.C, device=device) for a, b in zip(bounds[:-1], bounds[1:]) if b > a]
        row0 = torch.cat(row0) if row0 else torch.zeros(0, dtype=torch.long, device=device)
        ends = torch.as_tensor(bounds[1:], dtype=torch.long, device=device)
        gend = ends[torch.searchsorted(ends, row0, right=True)] if row0.numel() else row0
        nrow = torch.minimum(gend - row0, torch.full_like(row0, self.C))
        ns = int(row0.numel())
        # the slice of every row and the longest row of every slice
        row_slice = torch.repeat_interleave(torch.arange(ns, device=device), nrow) if ns else torch.zeros(
            0, dtype=torch.long, device=device)
        K = torch.zeros(ns, dtype=torch.long, device=device)
        if ns and self.n_rows:
            K.scatter_reduce_(0, row_slice, counts, reduce="amax", include_self=True)
        FG = self.F * self.G
        vlen, clen = K * (FG * self.C), K * self.C
        vbase = torch.zeros(ns, dtype=torch.long, device=device)
        cbase = torch.zeros(ns, dtype=torch.long, device=device)
        if ns > 1:
            vbase[1:] = torch.cumsum(vlen, 0)[:-1]
            cbase[1:] = torch.cumsum(clen, 0)[:-1]
        self.total_v = int(vlen.sum()) if ns else 0
        self.total_c = int(clen.sum()) if ns else 0
        self.n_slices, self.nnzb = ns, nnzb
        # group -> (first slice, slice count); slices are made group by group, in order
        self.group_bounds = bounds
        firsts, s0 = [], 0
        for a, b in zip(bounds[:-1], bounds[1:]):
            m = (b - a + self.C - 1) // self.C if b > a else 0
            firsts.append((s0, m))
            s0 += m
        self.group_slices = firsts
        # per-slice tables the kernel reads (int32 where they fit, the bases int64)
        self.row0 = row0.to(torch.int32)
        self.nrow = nrow.to(torch.int32)
        self.K = K.to(torch.int32)
        self.vbase, self.cbase = vbase, cbase
        self.lens = counts.to(torch.int32)
        self.ptr = ptr
        self.row_slice = row_slice
        self.local = (torch.arange(self.n_rows, device=device) - row0[row_slice]) if ns else row_slice
        # column array: padding slots point at column 0 (never read: masked by the row length)
        self.col = torch.zeros(max(self.total_c, 1), dtype=torch.int32, device=device)
        for a in range(0, nnzb, _FILL_CHUNK):
            b = min(a + _FILL_CHUNK, nnzb)
            r, k = self._row_and_slot(a, b)
            pos = cbase[row_slice[r]] + k * self.C + self.local[r]
            self.col[pos] = col_pos[order[a:b]].to(torch.int32)
        # proper: square, and no off-diagonal block joins two rows of one group (a GS colouring that is a proper colouring
        # of the stored pattern) -- then a colour pass may update x in place in its own kernel (MODE 4)
        self.proper = False
        if self.n_rows == self.n_cols and groups is not None and nnzb:
            gb = torch.as_tensor(bounds, dtype=torch.long, device=device)
            g_row = torch.searchsorted(gb, row_pos, right=True)
            g_col = torch.searchsorted(gb, col_pos, right=True)
            self.proper = not bool(((row_pos != col_pos) & (g_row == g_col)).any())
        # M5 lean plans: ``order`` (one int64 per block) is read only by fills; it may wait in pinned host memory
        self.order = order.cpu().pin_memory() if (host_order and device.type == "cuda") else order

    def group_rows(self, c: int):
        """Row range ``(lo, hi)`` of group ``c``."""
        return self.group_bounds[c], self.group_bounds[c + 1]

    def _row_and_slot(self, a: int, b: int):
        """Rows and in-row slots of the sorted blocks ``a:b``."""
        j = torch.arange(a, b, device=self.ptr.device)
        r = torch.searchsorted(self.ptr, j, right=True) - 1
        return r, j - self.ptr[r]

    def _positions(self, a: int, b: int) -> torch.Tensor:
        """Value position of entry 0 of each sorted block ``a:b`` (entry q sits at ``+ q C``)."""
        r, k = self._row_and_slot(a, b)
        return self.vbase[self.row_slice[r]] + k * (self.F * self.G * self.C) + self.local[r]

    def tensors(self) -> list:
        return [self.row0, self.nrow, self.K, self.vbase, self.cbase, self.lens, self.ptr, self.row_slice,
                self.local, self.col, self.order]

    def fill(self, val: torch.Tensor, dtype: torch.dtype | None = None) -> "SellMatrix":
        """The ``SellMatrix`` of block values ``val`` (``[nnzb, F, G]`` in the order the pattern was built from),
        stored in ``dtype`` (default ``val.dtype``).  A pure scatter: every value is copied (and rounded, for fp32
        storage) once."""
        if tuple(val.shape) != (self.nnzb, self.F, self.G):
            raise ValueError(f"values of shape {tuple(val.shape)} for a pattern of {self.nnzb} {self.F}x{self.G} blocks")
        dtype = dtype or val.dtype
        if (WARP_FILL["on"] and val.is_cuda and val.dtype == torch.float64 and dtype in (torch.float64, torch.float32)
                and max(self.total_v, val.numel()) <= 2 ** 31 - 1):
            # (a value array above one Warp array dimension -- the fine level at 152^3 -- takes the torch fill; W12d)
            # W6 (opt-in): the same scatter in one Warp kernel (block_kernels_warp.fill); a kernel that fails to build
            # turns the Warp fill off for the process (recorded) and the torch fill below runs
            from Pipeline.CoupledNewton import block_kernels_warp as _bkw
            try:
                return SellMatrix(self, _bkw.fill(self, val, dtype))
            except Exception as exc:   # noqa: BLE001
                WARP_FILL.update(on=False, error=f"{type(exc).__name__}: {exc}"[:400])
                import warnings
                warnings.warn(f"--camg-warp-fill off for this process: {WARP_FILL['error']}", RuntimeWarning)
        out = torch.zeros(max(self.total_v, 1), dtype=dtype, device=val.device)
        FG = self.F * self.G
        q = torch.arange(FG, device=val.device) * self.C
        flat = val.reshape(self.nnzb, FG)
        for a in range(0, self.nnzb, _FILL_CHUNK):
            b = min(a + _FILL_CHUNK, self.nnzb)
            pos = (self._positions(a, b)[:, None] + q[None, :]).reshape(-1)
            order = self.order[a:b].to(val.device, non_blocking=True)
            out[pos] = flat.index_select(0, order).reshape(-1).to(dtype)
        return SellMatrix(self, out)

    def unfill(self, values: torch.Tensor) -> torch.Tensor:
        """The ``[nnzb, F, G]`` block values a ``fill`` stored in ``values`` (exact for a same-dtype fill)."""
        FG = self.F * self.G
        out = torch.empty(self.nnzb, FG, dtype=values.dtype, device=values.device)
        q = torch.arange(FG, device=values.device) * self.C
        for a in range(0, self.nnzb, _FILL_CHUNK):
            b = min(a + _FILL_CHUNK, self.nnzb)
            pos = self._positions(a, b)[:, None] + q[None, :]
            out.index_copy_(0, self.order[a:b].to(values.device, non_blocking=True), values[pos])
        return out.view(self.nnzb, self.F, self.G)

    def diagonal_store(self, Dval: torch.Tensor, dtype: torch.dtype | None = None) -> torch.Tensor:
        """Per-row ``F x F`` blocks (``[n_rows, F, F]`` in rank order, e.g. the smoother inverses) in this pattern's
        slices, slot-major: ``out[s FF C + q C + r]`` (the kernel's ``sinv`` operand)."""
        FF = self.F * self.F
        out = torch.zeros(max(self.n_slices * FF * self.C, 1), dtype=dtype or Dval.dtype, device=Dval.device)
        if self.n_rows:
            q = torch.arange(FF, device=Dval.device) * self.C
            pos = (self.row_slice * (FF * self.C) + self.local)[:, None] + q[None, :]
            out[pos.reshape(-1)] = Dval.reshape(self.n_rows, FF).reshape(-1).to(out.dtype)
        return out


class SellMatrix:
    """Numeric sliced-ELL block matrix: ``pattern`` + ``values``.  Vectors are flat, cell-interleaved in the rank
    order of the row / column layouts (``BlockLayout``)."""

    def __init__(self, pattern: SellPattern, values: torch.Tensor):
        self.pattern, self.values = pattern, values

    @property
    def nbytes(self) -> int:
        return self.values.numel() * self.values.element_size()

    def astype(self, dtype: torch.dtype) -> "SellMatrix":
        return self if self.values.dtype == dtype else SellMatrix(self.pattern, self.values.to(dtype))

    def _use_triton(self, x: torch.Tensor) -> bool:
        return x.is_cuda and triton_available(x.device)

    def _launch(self, mode: int, x, b, y, sinv=None, omega=None, group: int | None = None, lo_col=None):
        P = self.pattern
        s0, ns = (0, P.n_slices) if group is None else P.group_slices[group]
        if ns == 0:
            return
        if ROWS_KERNEL["on"] and (mode < 3 or ROWS_KERNEL.get("gs", True)):
            # (``gs`` False: the rows kernel for the products only -- W2bc2 measured it 16 % slower than the Triton
            # tile for the GS colour passes and 4-17 % faster for the residual and the prolongation on the fine level)
            from Pipeline.CoupledNewton import block_kernels_warp as _bkw
            if _bkw.supported(self, ROWS_KERNEL["min_c"]):
                if not _bkw.ready(self, mode, lo_col is not None):
                    # the kernel variant could not be built (recorded): the rows kernel is off for the process
                    ROWS_KERNEL.update(on=False, error=_bkw.LAST_ERROR.get("error"))
                else:
                    _bkw.launch(self, mode, x, b, y, sinv=sinv, omega=omega, group=group, lo_col=lo_col)
                    return
        t4 = POLICY.get("tile4", 0)
        if t4 and (mode < 3 or P.F == P.G) and (t4 != 2 or mode >= 3):
            # W2 opt-in: the [C, KB, FP, GP] tile (one column / x load per block); tile4 2: GS passes only
            FP, GP = max(2, _pow2(P.F)), max(2, _pow2(P.G))
            kb4 = max(1, min(P.kb, 4096 // max(1, P.C * FP * GP)))
            kb4 = _pow2_floor(kb4)
            pdl = bool(POLICY.get("pdl", 0))
            _sell_kernel4[(ns,)](P.row0, P.nrow, P.K, P.vbase, P.cbase, P.lens, self.values, P.col,
                                 x, b if b is not None else x, y, sinv if sinv is not None else self.values,
                                 omega if omega is not None else x, s0, 0 if lo_col is None else int(lo_col),
                                 MODE=mode, F=P.F, G=P.G, C=P.C, FP=FP, GP=GP, KB=kb4, ZHI=lo_col is not None,
                                 PDL=pdl, num_warps=POLICY.get("num_warps", 4), launch_pdl=pdl)
            return
        _sell_kernel[(ns,)](P.row0, P.nrow, P.K, P.vbase, P.cbase, P.lens, self.values, P.col,
                            x, b if b is not None else x, y, sinv if sinv is not None else self.values,
                            omega if omega is not None else x, s0, 0 if lo_col is None else int(lo_col),
                            MODE=mode, F=P.F, G=P.G, C=P.C, QP=max(2, _pow2(P.F * P.G)), KB=P.kb,
                            ZHI=lo_col is not None, PDL=bool(POLICY.get("pdl", 0)),
                            num_warps=POLICY.get("num_warps", 4), launch_pdl=bool(POLICY.get("pdl", 0)))

    # ------------------------------------------------------------------ operations
    def mv(self, x: torch.Tensor) -> torch.Tensor:
        """``A x``."""
        if self._use_triton(x):
            y = torch.empty(self.pattern.n_rows * self.pattern.F, dtype=torch.float64, device=x.device)
            self._launch(0, x, None, y)
            return y
        return self.reference_mv(x)

    def residual(self, x: torch.Tensor, b: torch.Tensor) -> torch.Tensor:
        """``b - A x``."""
        if self._use_triton(x):
            y = torch.empty_like(b)
            self._launch(1, x, b, y)
            return y
        return b - self.reference_mv(x)

    def mv_add(self, x: torch.Tensor, y: torch.Tensor) -> torch.Tensor:
        """``y += A x`` in place; returns ``y``."""
        if self._use_triton(x):
            self._launch(2, x, None, y)
            return y
        return y.add_(self.reference_mv(x))

    def gs_colour(self, c: int, x: torch.Tensor, b: torch.Tensor, sinv: torch.Tensor, omega: torch.Tensor,
                  dx: torch.Tensor, fresh: bool = False) -> None:
        """One multicolour block-GS colour pass, in place on ``x``: ``dx_c = omega Sinv_c (b - A x)_c``, then
        ``x_c += dx_c`` (the reference's order: every row of the colour reads the x of before the pass).  On a proper
        colouring of the stored pattern (``pattern.proper``) one kernel does both (MODE 4: no row of the colour reads
        another row of it, so the in-place update cannot be seen by the pass); otherwise two (MODE 3 into ``dx``, then
        the add)."""
        P = self.pattern
        lo, hi = P.group_rows(c)
        a, e = P.F * lo, P.F * hi
        if e == a:
            return
        if self._use_triton(x):
            # W2 zero skip (opt-in, ``fresh`` from the cycle's first pre-sweep): x is zero from colour c on, i.e. at
            # every column rank >= lo of this square level matrix
            lo_col = lo if (fresh and ZERO_SKIP["on"] and P.n_rows == P.n_cols) else None
            if P.proper:
                self._launch(4, x, b, x, sinv=sinv, omega=omega, group=c, lo_col=lo_col)
                return
            self._launch(3, x, b, dx, sinv=sinv, omega=omega, group=c, lo_col=lo_col)
        else:
            t = b[a:e] - self.reference_mv(x, rows=(a // P.F, e // P.F))
            S = self.reference_diagonal(sinv, rows=(a // P.F, e // P.F))
            dx[a:e] = torch.einsum("nfg,ng->nf", S, t.view(-1, P.F)).reshape(-1) * omega
        x[a:e].add_(dx[a:e])

    # ------------------------------------------------------------------ torch reference (decodes the storage)
    def _decode(self, rows=None):
        """(row, column, block value [F, G] in fp64) of every stored block of rows ``rows = (lo, hi)``."""
        P = self.pattern
        lo, hi = (0, P.n_rows) if rows is None else rows
        a, b = int(P.ptr[lo]), int(P.ptr[hi])
        if b == a:
            empty = torch.zeros(0, dtype=torch.long, device=self.values.device)
            return empty, empty, torch.zeros(0, P.F, P.G, dtype=torch.float64, device=self.values.device)
        r, k = P._row_and_slot(a, b)
        s = P.row_slice[r]
        cols = P.col[P.cbase[s] + k * P.C + P.local[r]].long()
        q = torch.arange(P.F * P.G, device=self.values.device) * P.C
        vpos = (P.vbase[s] + k * (P.F * P.G * P.C) + P.local[r])[:, None] + q[None, :]
        return r, cols, self.values[vpos].to(torch.float64).view(-1, P.F, P.G)

    def reference_mv(self, x: torch.Tensor, rows=None) -> torch.Tensor:
        """``(A x)[rows]`` in torch (fp64), from the stored blocks."""
        P = self.pattern
        lo, hi = (0, P.n_rows) if rows is None else rows
        r, cols, V = self._decode((lo, hi))
        xs = x.view(-1, P.G)
        y = torch.zeros(hi - lo, P.F, dtype=torch.float64, device=x.device)
        if r.numel():
            y.index_add_(0, r - lo, torch.einsum("efg,eg->ef", V, xs[cols].to(torch.float64)))
        return y.reshape(-1)

    def reference_diagonal(self, store: torch.Tensor, rows=None) -> torch.Tensor:
        """The ``[rows, F, F]`` blocks of a ``diagonal_store`` (fp64)."""
        P = self.pattern
        lo, hi = (0, P.n_rows) if rows is None else rows
        idx = torch.arange(lo, hi, device=store.device)
        FF = P.F * P.F
        q = torch.arange(FF, device=store.device) * P.C
        pos = (P.row_slice[idx] * (FF * P.C) + P.local[idx])[:, None] + q[None, :]
        return store[pos].to(torch.float64).view(-1, P.F, P.F)
