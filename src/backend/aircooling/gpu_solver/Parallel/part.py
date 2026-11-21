"""One part of a partitioned case as a Newton problem on its OWN unknowns (``docs/development/multi-gpu.md``).

``load_collocated`` loads any LPCNS case directory as the serial ``CollocatedSolver`` (the entry's recipe: legacy
pool, float64 geometry); a part directory written by ``Parallel.partition`` is such a case.  ``PartitionedProblem``
wraps the part's local solver (own + halo cells) and a ``Parallel.comm.Distributed``:

* vectors are FIELD-BLOCKED over the own cells, ``x[f * n_own + i]`` (the serial layout restricted to the part);
* ``extend`` writes own values into a local vector and exchanges the halo, ``restrict`` takes the own rows;
* ``residual`` / ``jvp`` / ``vjp`` / ``assemble`` exchange the state, the tangent, or (transposed) the adjoint;
* ``dot`` / ``norm`` / ``max_abs`` / ``gauge`` reduce over all parts.

``localise_boundary_conditions`` replaces the patch-global inputs of the boundary conditions (section 2.5 of the
design) and refuses the ones a part cannot evaluate yet.
"""
from __future__ import annotations

import math
import os
import sys
import types
from pathlib import Path

import torch


# ---------------------------------------------------------------------------------------------- loading
def load_collocated(h5_dir, device, *, momentum_interpolation=None, coefficient=None, state_dir=None):
    """``(case, CollocatedSolver)`` of a case directory on ``device`` (the steady entry's load recipe)."""
    from FVdomain.load_case import load_case
    from Pipeline.CoupledNewton.collocated_newton import CollocatedSolver, promote_case_to_double
    from Utils import get_param
    argv = sys.argv
    try:
        sys.argv = [argv[0]]
        params = get_param.params()
    finally:
        sys.argv = argv
    params.on_gpu = -1 if device.type == "cpu" else (device.index or 0)
    params.dataset_dir = str(h5_dir)
    params.dataset_size = 1
    params.batch_size = 1
    params.state_save_dir = str(state_dir or (Path(h5_dir).parent / "state"))
    os.makedirs(params.state_save_dir, exist_ok=True)
    case = load_case(str(h5_dir), params, stage="solver", pool="legacy", device=device)
    promote_case_to_double(case)
    return case, CollocatedSolver(case.integrator._solver, device=device,
                                  momentum_interpolation=momentum_interpolation, coefficient=coefficient)


def field_offsets(unknowns) -> dict:
    """Field name -> first row block index (``COLLOCATED_UNKNOWNS`` order and widths)."""
    out, k = {}, 0
    for name, width, _, _ in unknowns:
        out[name] = k
        k += int(width)
    return out


def _csr_rows(J: torch.Tensor, rows: torch.Tensor) -> torch.Tensor:
    """``J[rows, :]`` of a CSR tensor, as CSR."""
    crow, col, val = J.crow_indices(), J.col_indices(), J.values()
    start, end = crow[rows], crow[rows + 1]
    sz = end - start
    tot = int(sz.sum())
    seg = torch.repeat_interleave(torch.arange(rows.numel(), device=J.device), sz)
    first = torch.cumsum(sz, 0) - sz
    pos = start.index_select(0, seg) + torch.arange(tot, device=J.device) - first.index_select(0, seg)
    new_crow = torch.zeros(rows.numel() + 1, dtype=crow.dtype, device=J.device)
    new_crow[1:] = torch.cumsum(sz, 0)
    return torch.sparse_csr_tensor(new_crow, col.index_select(0, pos), val.index_select(0, pos),
                                   size=(rows.numel(), J.shape[1]))


# --------------------------------------------------------------------------------------------- the part
class PartitionedProblem:
    """Newton problem of one part on its own unknowns, every reduction global (module docstring)."""

    def __init__(self, local, dist, meta: dict, maps: dict):
        self.local, self.dist, self.meta, self.maps = local, dist, meta, maps
        self.device, self.dtype = local.device, local.dtype
        self.F = int(local.num_fields)
        self.n_loc = int(local.n_cell)
        if self.n_loc != dist.n_loc:
            raise ValueError(f"the part's case has {self.n_loc} cells, its maps {dist.n_loc}")
        if not torch.equal(local.interior.cpu(), torch.arange(self.n_loc)):
            raise ValueError("interior cells must be the first compound cells")
        self.own = dist.own
        self.n_own = int(self.own.numel())
        self.n_cell = self.n_own
        self.num_fields = self.F
        self.n_unknowns = self.F * self.n_own
        self.N_global = int(meta["N"])
        self.n_unknowns_global = self.F * self.N_global
        self.unknowns = local.unknowns
        self.velocity = 3 * self.n_own
        self.pressure = 4 * self.n_own
        f = torch.arange(self.F, device=self.device)
        self.own_rows = (f[:, None] * self.n_loc + self.own[None, :]).reshape(-1)
        self.is_own = torch.zeros(self.n_loc, dtype=torch.bool, device=self.device)
        self.is_own[self.own] = True
        self._gauge_free = None
        self.mwi = local.mwi

    # ------------------------------------------------------------------------------- vectors and exchange
    def extend(self, v: torch.Tensor) -> torch.Tensor:
        q = torch.zeros(self.F, self.n_loc, dtype=v.dtype, device=self.device)
        q.index_copy_(1, self.own, v.reshape(self.F, self.n_own))
        self.dist.exchange(q)
        return q.reshape(-1)

    def restrict(self, q: torch.Tensor) -> torch.Tensor:
        return q.reshape(self.F, self.n_loc).index_select(1, self.own).reshape(-1)

    def coarse_basis(self) -> tuple[torch.Tensor, torch.Tensor]:
        """``(E_own [k, F*n_own], E_loc [k, F*n_loc])`` for the Nicolaides coarse space, cached once."""
        if getattr(self, "_coarse_basis_cache", None) is None:
            k = self.dist.world * self.F
            E_own = torch.zeros(k, self.F * self.n_own, dtype=self.dtype, device=self.device)
            E_loc = torch.empty(k, self.F * self.n_loc, dtype=self.dtype, device=self.device)
            for q in range(self.dist.world):
                for f in range(self.F):
                    j = q * self.F + f
                    if self.dist.rank == q:
                        E_own[j, f * self.n_own:(f + 1) * self.n_own] = 1.0
                    E_loc[j] = self.extend(E_own[j])
            self._coarse_basis_cache = (E_own, E_loc)
        return self._coarse_basis_cache

    def from_global(self, x_global: torch.Tensor) -> torch.Tensor:
        """The own part of a global field-blocked vector (every rank holds the global one)."""
        return x_global.reshape(self.F, self.N_global)[:, self.dist.own_global].reshape(-1)

    def gather(self, x: torch.Tensor):
        """The global field-blocked vector on rank 0 (None elsewhere)."""
        out = self.dist.gather(x.reshape(self.F, self.n_own))
        return None if out is None else out.reshape(-1)

    def dot(self, a, b) -> float:
        return self.dist.sum(float(torch.dot(a.reshape(-1), b.reshape(-1))))

    def norm(self, a) -> float:
        return math.sqrt(max(self.dot(a, a), 0.0))

    def max_abs(self, a) -> float:
        return self.dist.max(float(a.abs().max()) if a.numel() else 0.0)

    def field_norms(self, r) -> dict:
        """Global ``(max|r|, ||r||_2)`` per field, for residual-control stop rules."""
        out, rows = {}, r.reshape(self.F, self.n_own)
        for name, k in field_offsets(self.unknowns).items():
            width = next(w for n, w, _, _ in self.unknowns if n == name)
            block = rows[k:k + width]
            out[name] = (self.max_abs(block), self.norm(block))
        return out

    # ----------------------------------------------------------------------------------------- residual
    def residual(self, x: torch.Tensor) -> torch.Tensor:
        return self.restrict(self.local.residual(self.extend(x)))

    def linearise_forward(self, x: torch.Tensor, *, coefficient=None):
        """``v -> J v`` by forward-mode AD (exact; the tangent is exchanged like the state)."""
        product = self.local.linearise_forward(self.extend(x), coefficient=coefficient)
        fn = lambda v: self.restrict(product(self.extend(v)))
        if hasattr(product, "batched"):
            def _batched(V_own: torch.Tensor, V_loc: torch.Tensor | None = None) -> torch.Tensor:
                if V_loc is None:
                    V_loc = torch.stack([self.extend(V_own[i]) for i in range(V_own.shape[0])], dim=0)
                return product.batched(V_loc).index_select(1, self.own_rows)
            fn.batched = _batched
        return fn

    def linearise_forward_captured(self, x: torch.Tensor, *, coefficient=None, warmup: int = 2):
        """Opt-in (P7-mgpu task A): ``linearise_forward`` with persistent halo-exchange buffer and a single torch
        CUDA graph that captures the part's Warp JVP launches and their coalesced NCCL all-reduce.  Needs the
        reducer in its capturable mode (``LPCNS_MGPU_WARP_GRAPH=1``: plain ``all_reduce`` on device tensors)."""
        if self.device.type != "cuda":
            return self.linearise_forward(x, coefficient=coefficient)
        cacheable = getattr(self.local, "_pin", None) is None and getattr(self.local, "_ptc_shift", None) is None
        cache = getattr(self, "_captured_jvp_cache", None)
        if cache is None:
            cache = self._captured_jvp_cache = {}
        key = (coefficient,)
        if cacheable and key in cache:
            entry = cache[key]
            entry["product"].set_point(self.extend(x))
            return entry["apply"]
        product = self.local.linearise_forward(self.extend(x), coefficient=coefficient)
        q_in = torch.zeros(self.F, self.n_loc, dtype=self.dtype, device=self.device)
        q_flat = q_in.reshape(-1)
        app = getattr(self.local, "_warp", None)
        w_res = getattr(app, "W", app)
        side = torch.cuda.Stream(self.device)
        side.wait_stream(torch.cuda.current_stream(self.device))
        with torch.cuda.stream(side):
            if w_res is not None:
                w_res._in_torch_capture = True
            try:
                for _ in range(warmup):
                    product(q_flat)
            finally:
                if w_res is not None:
                    w_res._in_torch_capture = False
        torch.cuda.current_stream(self.device).wait_stream(side)
        torch.cuda.synchronize(self.device)
        graph = torch.cuda.CUDAGraph()
        app = getattr(self.local, "_warp", None)
        w_res = getattr(app, "W", app)
        saved = None
        if w_res is not None and hasattr(w_res, "_streams"):
            import warp as wp
            saved = w_res._streams.get("pair")
        with torch.cuda.graph(graph):
            if w_res is not None and hasattr(w_res, "_streams"):
                cap = torch.cuda.current_stream(self.device)
                w_res._streams["pair"] = (wp.stream_from_torch(cap), cap)
                w_res._in_torch_capture = True
            try:
                q_out = product(q_flat)
            finally:
                if w_res is not None and hasattr(w_res, "_streams"):
                    w_res._in_torch_capture = False
                    if saved is None:
                        w_res._streams.pop("pair", None)
                    else:
                        w_res._streams["pair"] = saved
        _register_captured_graph(graph)

        def apply(v):
            q_in.index_copy_(1, self.own, v.reshape(self.F, self.n_own))
            self.dist.exchange(q_in)
            graph.replay()
            return self.restrict(q_out)
        apply.graph = graph
        apply._keepalive = (product, q_in, q_flat, q_out)
        if hasattr(product, "batched"):
            def _batched(V_own: torch.Tensor, V_loc: torch.Tensor | None = None) -> torch.Tensor:
                if V_loc is None:
                    V_loc = torch.stack([self.extend(V_own[i]) for i in range(V_own.shape[0])], dim=0)
                return product.batched(V_loc).index_select(1, self.own_rows)
            apply.batched = _batched
        if cacheable and hasattr(product, "set_point"):
            cache[key] = {"product": product, "apply": apply}
        return apply

    def linearise(self, x: torch.Tensor, *, coefficient=None):
        """``v -> J v`` by the double-backward tape of the local residual (built once per state)."""
        product = self.local.linearise(self.extend(x), coefficient=coefficient)
        return lambda v: self.restrict(product(self.extend(v)))

    def linearise_transpose(self, x: torch.Tensor, *, coefficient=None, params=()):
        """``w -> J^T w``: the local VJP with the cotangent on the own rows only, then the transposed exchange
        (halo contributions added into their owners).  ``params``: extra leaves whose gradients the returned
        ``param_vjp(w)`` gives (summed over all parts)."""
        from Pipeline.CoupledNewton.casestate import _eager
        from Pipeline.CoupledNewton.jacobian import _coefficient
        leaf = self.extend(x).detach().clone().requires_grad_(True)
        with _eager(), _coefficient(self.local, coefficient):
            r = self.local.residual(leaf)

        def cotangent(w):
            c = torch.zeros(self.F * self.n_loc, dtype=self.dtype, device=self.device)
            c[self.own_rows] = w.reshape(-1)
            return c

        def apply(w):
            (g,) = torch.autograd.grad(r, leaf, cotangent(w), retain_graph=True)
            g = g.reshape(self.F, self.n_loc).contiguous()
            self.dist.exchange_add(g)
            return self.restrict(g)

        return apply

    def assemble(self, x: torch.Tensor, *, coefficient=None):
        """The part's assembled Jacobian on own + halo (``blocks`` of the serial ``assemble``), plus ``J_own``:
        its own rows, the Krylov operator of the assembled mode."""
        x_loc = self.extend(x)
        reducer = getattr(self, "reducer", None)
        if reducer is not None:
            reducer.local_only = True                 # the colouring probes do not run in lockstep
        try:
            blocks = self.local.assemble(x_loc, coefficient=coefficient)
        finally:
            if reducer is not None:
                reducer.local_only = False
        J = blocks["J"]
        return blocks, _csr_rows(J, self.own_rows), x_loc

    def assembled_operator(self, J_own):
        return lambda v: J_own @ self.extend(v)

    # ------------------------------------------------------------------------------------------- gauge
    def constant_pressure_defect(self, x: torch.Tensor) -> float:
        jvp = self.linearise(x)
        d = torch.zeros_like(x)
        d[self.velocity:self.pressure] = 1.0
        shifted = self.norm(jvp(d))
        d = torch.zeros_like(x)
        d[:self.velocity] = 1.0
        return shifted / max(self.norm(jvp(d)), 1e-300)

    def resolve_gauge(self, x: torch.Tensor) -> bool:
        from Pipeline.CoupledNewton.gauge import GAUGE_FREE_BELOW, GAUGE_PINNED_ABOVE
        if self._gauge_free is not None:
            return self._gauge_free
        defect = self.constant_pressure_defect(x)
        if defect <= GAUGE_FREE_BELOW:
            self._gauge_free = True
        elif defect >= GAUGE_PINNED_ABOVE:
            self._gauge_free = False
        else:
            raise ValueError(f"constant pressure mode defect {defect:.3e} between the gauge edges")
        self.local._gauge_free = self._gauge_free
        return self._gauge_free

    def gauge(self, x: torch.Tensor) -> torch.Tensor:
        if self._gauge_free is None:
            raise RuntimeError("gauge() before resolve_gauge()")
        if not self._gauge_free:
            return x
        p = x[self.velocity:self.pressure]
        mean = self.dist.sum(float(p.sum())) / self.N_global
        return torch.cat((x[:self.velocity], p - mean, x[self.pressure:]))

    # --------------------------------------------------------------------------------------------- PTC
    def arm_shift(self, d_own, ref_own):
        """``_ptc_shift`` of the local residual on own + halo rows (``None`` disarms)."""
        if d_own is None:
            self.local._ptc_shift = None
        else:
            self.local._ptc_shift = (self.extend(d_own), self.extend(ref_own))


# ------------------------------------------------------------------------ patch-global boundary inputs
class _AllReduce(torch.autograd.Function):
    """Differentiable sum over the parts: forward, backward and forward-mode tangent are all all-reduces (the
    adjoint of a sum broadcast over ranks is the sum of the ranks' cotangents)."""

    @staticmethod
    def forward(x, reducer, key):
        y = reducer.raw_sum(x.detach().contiguous().clone())
        if key is not None:
            reducer.cache[key] = y.detach().clone()
        return y

    @staticmethod
    def setup_context(ctx, inputs, output):
        ctx.reducer = inputs[1]

    @staticmethod
    def backward(ctx, g):
        return _AllReduce.apply(g, ctx.reducer, None), None, None

    @staticmethod
    def jvp(ctx, t, _r, _k):
        return _AllReduce.apply(t, ctx.reducer, None)


class PatchReducer:
    """The patch sums a boundary condition takes inside the residual, over the faces this part OWNS and then
    all-reduced (torch: ``torch_sum``, differentiable; Warp: ``warp_reduce`` on a dual array in place).

    Every rank must make the same calls in the same order (it does: every part holds every patch, possibly with no
    face, and the residual / JVP of a Newton or Krylov step runs on all ranks at once).  The colouring probes of a
    part's assembly do NOT run in lockstep, so the assembly runs with ``local_only``: the sums are the values of the
    last collective evaluation (the same state) and their derivative is dropped -- the global coupling is omitted
    from the assembled matrix, which is a preconditioner, as ``assembly_is_operator = False`` omits it serially."""

    def __init__(self, dist):
        self.dist = dist
        self.local_only = False
        self.cache = {}
        self.calls = 0
        self.capturable = (os.environ.get("LPCNS_MGPU_WARP_GRAPH", "0") == "1" and not dist.host)

    def raw_sum(self, y):
        if self.dist.world > 1:
            import torch.distributed as tdist
            self.calls += 1
            if self.dist.host:
                z = y.cpu()
                tdist.all_reduce(z)
                y.copy_(z)
            else:
                tdist.all_reduce(y)
        return y

    def allreduce(self, y):
        return self.raw_sum(y.detach().contiguous().clone())

    def torch_sum(self, key, partial):
        if self.dist.world == 1:
            return partial
        if self.local_only:
            return self.cache[key].to(partial.dtype).clone()
        return _AllReduce.apply(partial, self, key)

    def warp_reduce(self, key, arr):
        """In place on a Warp dual array ``[chunks, n, 1 + tangents]``.  The cache tensors are persistent (updated
        with ``copy_``), so the sequence can be replayed from a CUDA graph (``capturable``, opt-in
        LPCNS_MGPU_WARP_GRAPH=1); the graph key carries ``mode()``."""
        import warp as wp
        if self.dist.world == 1:
            return
        t = wp.to_torch(arr)
        c = self.cache.get(("w", key))
        if c is None or c.shape[0] != t.shape[1]:
            c = self.cache[("w", key)] = torch.zeros(t.shape[1], dtype=t.dtype, device=t.device)
        if self.local_only:
            t[..., 0] = c[None, :]
            t[..., 1:] = 0.0
            return
        if self.capturable:
            import torch.distributed as tdist
            tdist.all_reduce(t)
        else:
            self.raw_sum(t)
        c.copy_(t[0, :, 0])

    def _cache_slot(self, key, n, dtype, device):
        c = self.cache.get(("w", key))
        if c is None or c.shape[0] != n:
            c = self.cache[("w", key)] = torch.zeros(n, dtype=dtype, device=device)
        return c

    def warp_reduce_coalesced(self, red_t: torch.Tensor, n_fro: int, n_rack: int, stage: str = "all"):
        """Coalesced in-place reduction of ``[1, nfro + 2 * nrack, ...]`` for ``nch == 1``."""
        if self.dist.world == 1:
            return
        t = red_t.unsqueeze(-1) if red_t.ndim == 2 else red_t
        nfro, nrack = max(int(n_fro), 1), max(int(n_rack), 1)
        do_fro = bool(n_fro) and stage in ("all", "fro")
        do_rack = bool(n_rack) and stage in ("all", "rack")
        if not do_fro and not do_rack:
            return
        cfro = self._cache_slot("fro", nfro, t.dtype, t.device) if do_fro else None
        crnum = self._cache_slot("rack_num", nrack, t.dtype, t.device) if do_rack else None
        crden = self._cache_slot("rack_den", nrack, t.dtype, t.device) if do_rack else None
        if self.local_only:
            if do_fro:
                t[:, :nfro, 0] = cfro[None, :]
                t[:, :nfro, 1:] = 0.0
            if do_rack:
                t[:, nfro:nfro + nrack, 0] = crnum[None, :]
                t[:, nfro + nrack:, 0] = crden[None, :]
                t[:, nfro:, 1:] = 0.0
            return
        sub = red_t if stage == "all" else (red_t[:, :nfro] if stage == "fro" else red_t[:, nfro:])
        if self.capturable:
            import torch.distributed as tdist
            tdist.all_reduce(sub)
        else:
            self.raw_sum(sub)
        if do_fro:
            cfro.copy_(t[0, :nfro, 0])
        if do_rack:
            crnum.copy_(t[0, nfro:nfro + nrack, 0])
            crden.copy_(t[0, nfro + nrack:, 0])

    def __call__(self, key, arr):
        return self.warp_reduce(key, arr)

    def mode(self):
        return "local" if self.local_only else "collective"


import weakref as _weakref
_CAPTURED_GRAPHS: _weakref.WeakSet = _weakref.WeakSet()
_DESTROY_HOOKED = False


def _register_captured_graph(graph):
    global _DESTROY_HOOKED
    _CAPTURED_GRAPHS.add(graph)
    if not _DESTROY_HOOKED:
        import torch.distributed as tdist
        orig_destroy = tdist.destroy_process_group

        def _safe_destroy(group=None):
            for g in list(_CAPTURED_GRAPHS):
                try:
                    g.reset()
                except Exception:
                    pass
            _CAPTURED_GRAPHS.clear()
            return orig_destroy() if group is None else orig_destroy(group)

        tdist.destroy_process_group = _safe_destroy
        _DESTROY_HOOKED = True


def _owned(problem, owner) -> torch.Tensor:
    """``[F, 1]`` 1.0 where the face's owner cell is one of this part's own cells."""
    return problem.is_own[owner.long()].to(problem.dtype).reshape(-1, 1)


def localise_boundary_conditions(problem: PartitionedProblem) -> list[str]:
    """Replace the patch-global inputs of the part's boundary conditions by global ones (design 2.5); refuse what
    a part cannot evaluate.  Returns a log of what was changed."""
    from FVdomain.SetBC.onthefly import setRack, setU
    disc = problem.local.solver
    area = problem.meta["patch_area"]
    R = problem.reducer
    fb = disc.fvpatch_batch
    log = []
    for bc in disc.bcpatch_batch.iter_conditions():
        name = type(bc).__name__
        if isinstance(bc, setRack._RackVelocityBase):
            bc._partition_area = float(area[bc.patch_name])
            bc._patch_geometry = types.MethodType(_rack_patch_geometry, bc)
            log.append(f"{bc.patch_name}: {name} speed q_V / A with the global patch area {bc._partition_area:.9g}")
        elif isinstance(bc, setRack.Rackoutlettemperature):
            if bc.model != "cbbm":
                raise NotImplementedError(f"{bc.patch_name}: {name} model={bc.model!r}: the pointwise IBBM pairing "
                                          "reads cells of another part; not partitioned (design section 10)")
            if bc.lag_mean:
                bc._partition_problem = problem
                bc.eval = types.MethodType(_cbbm_lagged_eval, bc)
                log.append(f"{bc.patch_name}: {name} CBBM lagged mean, all-reduced over owned faces of "
                           f"{bc.partner_patch_name}")
            else:
                _, t_owner, _ = bc._cbbm_indices(disc, 0)
                m = _owned(problem, t_owner)
                key = f"cbbm:{bc.patch_name}"

                def mean(intake, weights, _m=m, _k=key):
                    num = R.torch_sum(_k + ":num", (intake * weights * _m).sum(dim=-2, keepdim=True))
                    den = R.torch_sum(_k + ":den", (weights * _m).sum(dim=-2, keepdim=True))
                    return num / den
                bc._area_mean = mean
                log.append(f"{bc.patch_name}: {name} live CBBM {bc.mean_weight} mean, all-reduced over owned faces")
        elif isinstance(bc, setU.Flowrateoutletvelocity):
            gp = int(bc.patch_idx)
            s, e = int(fb.start_idx_face[gp]), int(fb.end_idx_face[gp])
            m = _owned(problem, disc.neighbor_cpd_cell[0, s:e])
            key = f"fro:{bc.patch_name}"
            bc._patch_sum = (lambda terms, what=None, _m=m, _k=key:
                             R.torch_sum(f"{_k}:{what}", (terms * _m).sum(dim=-2, keepdim=True)))
            log.append(f"{bc.patch_name}: {name} flow-rate estimate all-reduced over owned faces")
        elif isinstance(bc, setU.Flowrateinletvelocity):
            total = float(area[bc.patch_name])
            bc._patch_total = lambda values, _a=total: torch.as_tensor(_a, dtype=values.dtype, device=values.device)
            log.append(f"{bc.patch_name}: {name} with the global patch area {total:.9g}")
    model = getattr(disc, "turbulence", None)
    if model is not None and type(model).__module__.split(".")[-1] in ("komega_sst", "mlze"):
        raise NotImplementedError(f"{type(model).__name__} reads the nearest-wall distance over the whole mesh; "
                                  "inject it from the global wall faces before partitioning this model")
    return log


def inject_near_wall_dist(problem) -> int:
    """nearWallDist of the GLOBAL mesh on the part's declaring wall faces (``Parallel.partition``), installed so that
    ``Turbulence.near_wall_dist.apply_near_wall_dist`` finds it done.  Returns the number of faces."""
    if "near_wall_faces" not in problem.maps:
        return 0
    from FVsolver.Turbulence.near_wall_dist import _declaring_ranges
    app = problem.local.solver
    faces = problem.maps["near_wall_faces"].long().to(problem.device)
    wd = app.wall_distance_face.detach().clone()
    wd[faces] = problem.maps["near_wall_y"].to(device=wd.device, dtype=wd.dtype).reshape(-1, *wd.shape[1:])
    app.wall_distance_face = wd
    app._near_wall_dist_state = (wd, frozenset(_declaring_ranges(app)))
    return int(faces.numel())


def attach_warp(problem) -> None:
    """The DC Warp backend (``buoyantBoussinesqpicoext_kepsilon_warp``) on the part: residual, exact JVP and the
    colouring probes of its assembly, with the patch sums over owned faces all-reduced between the kernels."""
    from FVsolver.applications.buoyantBoussinesqpicoext_kepsilon_warp import attach_warp_dc, supports
    app = problem.local.solver
    if not supports(app):
        raise NotImplementedError("--backend warp per part: the DC Warp backend supports buoyant k-epsilon cases")
    # world 1: no collective, so the backend's own CUDA-graph path is kept (reduce None)
    app._partition = {"is_own_cell": problem.is_own, "allreduce": problem.reducer.allreduce,
                      "reduce": problem.reducer if problem.dist.world > 1 else None}
    # one volumetric flux per cyclic face pair, as the serial data-centre recipe sets it (--conservative-cyclic-flux).
    # Without it the two sides of a baffle each carry their own flux and the pair creates or destroys air: 2.9 m3/s
    # of a hall's 69 at the ceiling baffles of case_09, leaving through the pressure-reference return
    # (LPCNS_MGPU_CONSERVATIVE_CYCLIC=0: the earlier behaviour)
    if os.environ.get("LPCNS_MGPU_CONSERVATIVE_CYCLIC", "1") == "1":
        app._conservative_cyclic_flux = True
    attach_warp_dc(problem.local)


def _rack_patch_geometry(self, integrator, patch_idx_offset: int):
    """``setRack._RackVelocityBase._patch_geometry`` with ``A`` the area of the WHOLE patch (all parts)."""
    start, end, normals, areas, owner, ghost, _ = type(self)._patch_geometry(self, integrator, patch_idx_offset)
    return start, end, normals, areas, owner, ghost, self.flow_rate / self._partition_area


def _cbbm_lagged_eval(self, fvfield, integrator, patch_idx_offset: int, **kwargs):
    """Eq. 15 with the lagged mean, without the face pairing (a part may hold only one side of a rack)."""
    if self._lagged_mean is None:
        raise RuntimeError(f"{self.patch_name}: the partitioned CBBM mean is not seeded "
                           "(Parallel.part.update_rack_means before the first residual)")
    fb = integrator.fvpatch_batch
    gp = self.patch_idx + patch_idx_offset
    s, e = int(fb.start_idx_face[gp]), int(fb.end_idx_face[gp])
    ghost = integrator.neighbor_cpd_cell[1, s:e]
    t = fvfield["t_cpd_cell"]
    t[..., ghost, 0:1] = torch.full_like(t[..., ghost, 0:1], self._lagged_mean + self.deltaT)
    fvfield["t_cpd_cell"] = t
    return fvfield


def update_rack_means(problem: PartitionedProblem, x: torch.Tensor, *, values: dict | None = None) -> list:
    """Set every lagged CBBM mean: from ``values`` (patch name -> mean), else the area-weighted mean of the intake
    owner cells' T at ``x`` over the owned faces of the partner patch, all-reduced.  Returns
    ``[(patch, previous, new, |change|)]`` like ``setRack.cbbm_outer_pass``."""
    from FVdomain.SetBC.onthefly import setRack
    disc = problem.local.solver
    t_row = field_offsets(problem.unknowns).get("T")
    report = []
    x_t = None
    for bc in disc.bcpatch_batch.iter_conditions():
        if not (isinstance(bc, setRack.Rackoutlettemperature) and bc.model == "cbbm" and bc.lag_mean):
            continue
        if values is not None:
            fresh = float(values[bc.patch_name])
        else:
            if x_t is None:
                x_t = x.reshape(problem.F, problem.n_own)[t_row]
                t_loc = torch.zeros(problem.n_loc, dtype=x.dtype, device=x.device)
                t_loc[problem.own] = x_t
            fb = disc.fvpatch_batch
            k = int(bc.partner_patch_idx)
            s, e = int(fb.start_idx_face[k]), int(fb.end_idx_face[k])
            owner = disc.neighbor_cpd_cell[0, s:e].long()
            mine = problem.is_own[owner]
            area = disc.face_area[s:e].reshape(-1).to(x.dtype)
            part = float((t_loc[owner[mine]] * area[mine]).sum())
            fresh = problem.dist.sum(part) / float(problem.meta["patch_area"][bc.partner_patch_name])
        previous = bc._lagged_mean
        bc._lagged_mean = fresh
        report.append((bc.patch_name, previous, fresh,
                       float("inf") if previous is None else abs(fresh - previous)))
    return report


def rack_means_of(solver) -> dict:
    """Patch name -> lagged CBBM mean of a serial or local solver (for tests and hand-over)."""
    from FVdomain.SetBC.onthefly import setRack
    return {bc.patch_name: bc._lagged_mean for bc in solver.solver.bcpatch_batch.iter_conditions()
            if isinstance(bc, setRack.Rackoutlettemperature)}


# --------------------------------------------------------------------------------------------- setup
def setup_part(part_dir, rank: int, world: int, device, *, backend=None, momentum_interpolation=None,
               coefficient=None, backend_kind="torch", log=print):
    """Load part ``rank`` of the partition in ``part_dir`` on ``device`` and return its ``PartitionedProblem``."""
    from Parallel.comm import Distributed
    part_dir = Path(part_dir)
    meta = torch.load(part_dir / "meta.pt", weights_only=False)
    if int(meta["nparts"]) != int(world):
        raise ValueError(f"{part_dir} holds {meta['nparts']} parts, the run has {world} processes")
    maps = torch.load(part_dir / f"part{rank}" / "maps.pt", weights_only=False)
    host = backend == "gloo" and device.type == "cuda"
    dist = Distributed(rank, world, device, maps["send"], maps["recv"], maps["own"], maps["cells"], meta["N"],
                       host=host)
    case, local = load_collocated(part_dir / f"part{rank}" / "h5", device,
                                  momentum_interpolation=momentum_interpolation, coefficient=coefficient,
                                  state_dir=part_dir / f"part{rank}" / "state")
    problem = PartitionedProblem(local, dist, meta, maps)
    problem.case = case
    problem.reducer = PatchReducer(dist)
    lines = localise_boundary_conditions(problem)
    n_near = inject_near_wall_dist(problem)
    if n_near:
        lines.append(f"nearWallDist of the global mesh on {n_near} local wall faces")
    if backend_kind == "warp":
        attach_warp(problem)
        lines.append("DC Warp backend attached (patch sums all-reduced between the kernels)")
    for line in lines:
        log(f"[rank {rank}] {line}")
    return problem
