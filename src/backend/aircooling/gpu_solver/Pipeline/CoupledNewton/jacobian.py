"""``J`` at ``x``: the double-backward product, the colouring, and the assembly.

One tape per linearisation and one probe per colour per field.  The colouring
comes from ``SparseSolver.jacobian_assembly`` unchanged; only the unpacking is
written here, because that module's convention is cell-interleaved and this
package's rows and columns are FIELD-BLOCKED.  ``linearise_forward``
is the same product by forward-mode AD, for the JFNK operator
(``Pipeline.CoupledNewton.jfnk``).

Read off the ``CollocatedSolver`` passed in: ``solver``, ``unknowns``,
``num_fields``, ``n_cell``, ``n_unknowns``, ``velocity``, ``pressure``,
``device``, ``dtype``, ``_sparsity``, ``_plan``, and ``residual``; and, when
present, ``assembly_batch`` and the opt-in ``colouring_cache`` /
``colouring_cache_refresh`` (see ``sparsity``).
"""
from __future__ import annotations

import logging
import contextlib
import os
import time
from collections.abc import Mapping
from typing import Callable

import torch

from SparseSolver.jacobian_assembly import build_sparsity

from Pipeline.CoupledNewton.casestate import _eager


@contextlib.contextmanager
def _coefficient(self, coefficient: str | None):
    """``d_f`` differentiated or detached for the duration, then put back.

    ``base_solver.momentum_weighted_flux`` reads ``_mwi_frozen_coefficient``
    while the tape is being BUILT -- it decides whether ``d_f`` is
    ``.detach()``ed inside the forward pass -- so scoping the override around
    that pass is enough; every later probe differentiates the graph that pass
    left behind.  ``None`` touches nothing, which is what keeps a default
    assembly byte-identical.

    Detaching changes a DERIVATIVE and never a value, so the residual the tape
    records is the same function either way (verified to ``max|dr| = 0.0`` in
    the null-cluster verification, and gated here by
    ``test_collocated_newton.py::test_the_coefficient_override_moves_no_
    residual``, which needs a real case and therefore lives with the fixture).

    **Which is exactly why the two callers want opposite values.**  A forward
    Newton solve may use a Jacobian that is not the true one -- it pays in
    convergence RATE and lands on the same root, and ``frozen`` is measured
    faster, sparser and better matched to the assembler's two-hop colouring, so
    it is the forward default.  The implicit function theorem has no such
    freedom: it inverts ``dr/dx`` and contracts ``dr/dtheta``, and if either is
    the derivative of a different function the gradient is simply wrong.
    Measured on the 8^3 cavity, frozen returns ``dL/dnu = -1.031e+02``
    where a four-point finite-difference plateau says ``+9.4457e+01`` -- the
    wrong sign and 209% error, with each half independently responsible.  Hence
    ``newton.differentiable_solution`` passes ``"implicit"`` here regardless of
    what the forward ran.
    Restored in a ``finally`` because the assembly, the export and the line
    search all run on the same discretisation object.
    """
    if coefficient is None:
        yield
        return
    if coefficient not in ("implicit", "frozen"):
        raise ValueError(f"unknown coefficient {coefficient!r}; valid: "
                         "implicit, frozen")
    case = self.solver
    previous = case._mwi_frozen_coefficient
    case.configure_momentum_interpolation(coefficient=coefficient)
    try:
        yield
    finally:
        case._mwi_frozen_coefficient = previous


def linearise(self, x: torch.Tensor, *, coefficient: str | None = None,
              differentiable: bool = False
              ) -> Callable[[torch.Tensor], torch.Tensor]:
    """``d -> J d`` at ``x``, with the tape built ONCE.

    The Jacobian-vector product is the standard double-backward identity:
    differentiating ``grad_x (r . u)`` with respect to the dummy cotangent
    ``u`` gives ``J d``.  What matters for cost is that the expensive half --
    the forward evaluation and its backward graph -- is built here and every
    subsequent probe is a second-order pass over the tape that already exists.

    ``differentiable=True`` retains derivatives with respect to each direction,
    allowing a finite-budget training loss to reach a learned preconditioner's
    output. The original ``x`` remains detached: this is a fixed-state linear
    operator, not differentiation through successive Newton linearizations.
    The default does not retain the direction graph and preserves inference.
    Reusing a differentiable tape across training losses requires targeting
    only the preconditioner parameters with autograd.grad, or backward(inputs=
    parameters). Unrestricted backward traverses the internal state tape and
    may free it; rebuild the linearization if that broader derivative is needed.
    Measured on the 48^3 cavity: 5.7 ms to evaluate the residual, 4.3 ms per
    probe afterwards, so an assembly of 368 probes costs 1.6 s rather than the
    3.7 s a fresh forward per probe would.

    ``coefficient`` overrides whether ``d_f`` is differentiated for THIS tape
    only.  ``None`` (the default, and what ``assemble`` uses) leaves the case
    as configured.  ``"implicit"`` is what a matrix-free Krylov operator wants
    and what ``assemble`` cannot have: differentiating ``d_f`` makes a row
    reach three hops while the colouring covers two, so the assembled copy
    then misses its own JVP by 1.3-22.8% along the near-null pressure cluster
    (null-cluster report section 5.1).  A product that is never
    assembled has no colouring to outrun.
    """
    warp = getattr(self, "_warp", None)                  # Warp backend (opt-in): the fused JVP
    if warp is not None and not getattr(self, "_warp_bypass", False):
        return warp.product(self, x, coefficient, differentiable)
    # The state is fixed for this linearization. The optional graph preserves
    # derivatives with respect to the input direction (e.g. a learned
    # preconditioner output), not with respect to the original state x.
    # The default remains the detached inference product used by Newton.
    with _eager(), _coefficient(self, coefficient):
        leaf = x.detach().clone().requires_grad_(True)
        residual = self.residual(leaf)
        cotangent = torch.zeros_like(residual, requires_grad=True)
        (adjoint,) = torch.autograd.grad(
            (residual * cotangent).sum(), leaf, create_graph=True)

    def product(direction: torch.Tensor) -> torch.Tensor:
        with _eager():
            (out,) = torch.autograd.grad(
                (adjoint * direction.reshape(-1)).sum(), cotangent,
                retain_graph=True, create_graph=differentiable)
        return out

    def batched(directions: torch.Tensor) -> torch.Tensor:
        """``[B, N] -> [B, N]``: B products in one vmapped second-order pass."""
        with _eager():
            (out,) = torch.autograd.grad(adjoint, cotangent, grad_outputs=directions,
                                         retain_graph=True, is_grads_batched=True)
        return out

    product.batched = batched
    return product


def linearise_forward(self, x: torch.Tensor, *, coefficient: str | None = None
                      ) -> Callable[[torch.Tensor], torch.Tensor]:
    """``d -> J d`` at ``x`` by FORWARD-mode AD: one dual-number evaluation of the residual per product.

    The same derivative as ``linearise`` -- the same residual, the same ``coefficient`` override, the armed PTC
    shift and time level included -- taken the other way round: ``x`` carries ``d`` as its tangent through one
    ``residual`` call, and the residual's tangent is ``J d``.  Nothing is taped and nothing is retained between
    products, so the JFNK loop's operator costs no memory beyond one forward pass; the price is that every product
    re-evaluates the primal residual too (``Pipeline.CoupledNewton.jfnk`` has the cost model and the one-time check
    against ``linearise`` that guards this path).

    What it relies on, checked by reading and gated by ``tests/test_jfnk_jvp.py``: every op of the
    residual has a forward-mode formula, including the in-place boundary writes on views of the state (forward AD
    gives the view's base a tangent; the state does not require grad here, so ``forward`` skips the clones it
    makes for a taped state -- same values).  The one custom ``autograd.Function`` on the path,
    ``FVgrad._WlsqSolve``, has no ``jvp``: the discretisation's optional seam method
    ``forward_mode_derivatives()`` (a context manager, ``BaseSolver``) makes its WLSQ gradient take the same
    product in plain ops for the duration (same values to the bit); a discretisation without it is evaluated as
    is.  A state-dependent quantity computed under ``torch.no_grad`` is a constant to ``linearise``; whether
    forward mode carries a tangent through it or not (``Pipeline/ODIL/trainer_jfnk.py`` records a ZERO tangent
    through ``no_grad`` for ``torch.func.jvp``) does not matter here, because the ones on this path (the
    residualControl normalisations, the face-CV upwind selector, an advective outlet's old-level speed) do not
    reach the residual rows -- the one-time check would see it if one did.  ``.detach()`` -- the frozen ``d_f`` --
    drops the tangent as it drops the gradient.
    """
    import torch.autograd.forward_ad as forward_ad

    warp = getattr(self, "_warp", None)                  # Warp backend (opt-in): the same fused JVP
    if warp is not None and not getattr(self, "_warp_bypass", False):
        return warp.product(self, x, coefficient)
    seam = getattr(self.solver, "forward_mode_derivatives", None)
    point = x.detach().reshape(-1).clone()

    def product(direction: torch.Tensor) -> torch.Tensor:
        tangent = direction.detach().reshape(-1).to(dtype=point.dtype).contiguous()
        with (_eager(), _coefficient(self, coefficient), forward_ad.dual_level(),
              seam() if seam is not None else contextlib.nullcontext()):
            image = forward_ad.unpack_dual(self.residual(forward_ad.make_dual(point, tangent))).tangent
            if image is None:
                raise RuntimeError("forward-mode AD returned no tangent for the residual: no differentiable "
                                   "path from the state to the residual rows")
            image = image.clone()
        return image

    return product


def sparsity(self):
    """The distance-4 colouring, built once for the whole run.

    Reused verbatim from ``SparseSolver.jacobian_assembly``: it derives the
    exact row patterns from the mesh and colours the exact conflict graph, so
    one probe per colour resolves unambiguously into one matrix entry per row.
    Only the colouring is taken; the unpacking below writes FIELD-BLOCKED rows
    and columns, which that module's cell-interleaved convention does not.

    ``self.colouring_cache`` (a directory; opt-in, GPU-vs-OpenFOAM
    feasibility plan E2) is set by the entry's ``--colouring-cache`` the way
    ``assembly_batch`` is.  The colour array is then loaded from that directory
    instead of rebuilt, keyed by the mesh connectivity, the cyclic and rack
    pairs, the row-block layout, the operator/preconditioner role and the MWI
    coefficient mode, and it passes the same collision check a fresh colouring
    passes before any probe uses it.  Absent (the default), the call below is
    the one it always was.

    ``self.colouring`` (opt-in; the entry's
    ``--colouring``) names another colouring algorithm of
    ``jacobian_assembly.COLOURINGS`` -- e.g. "lattice": 27 colours instead of
    46-47 on the DHC meshes, i.e. 135 probes per assembly instead of 230-235.
    The assembled matrix is the same (each entry is the same derivative, seeded
    with other cells); only the probes and their unpacking tables change.
    """
    if self._sparsity is None:
        self._sparsity = build_sparsity(
            self.solver, window=1, num_fields=self.num_fields,
            # "refuse" when this matrix IS the Krylov operator, "omit" when it
            # is only the preconditioner and the matrix-free product is the
            # operator.  See ``jacobian_assembly._rack_cell_pairs``.
            # ``_approximate_operator_ok`` (opt-in, --approximate-operator): the assembled matrix may be an
            # APPROXIMATE Krylov operator (a defect correction / inexact Newton step whose residual is still
            # exact and line-searched), so a global reduction is omitted from it rather than refused.
            global_reduction=("refuse" if (self.assembly_is_operator
                                           and not getattr(self, "_approximate_operator_ok", False)) else "omit"),
            # Named for the EQUATION, since these are row blocks: a spec
            # reading ("U", "cell", 3) would label the momentum rows with the
            # column they happen to sit above.
            block_specs=tuple(
                (attribute.removeprefix("residual_"), "cell", width)
                for _, width, _, attribute in self.unknowns),
            # The colour count IS the assembly cost -- probes = window x colours
            # x fields -- and it was going unreported because no logger reached
            # here.  Measured: a DataCenter assembly costs 20.3 s
            # against the periodic hills' 6.5 s at a comparable mesh size, and
            # this line is what tells a reader which of the two factors it is.
            logger=logging.getLogger(__name__),
            **_colouring_cache_options(self),
            # --gpu-sparsity: only when set, so the default call is the one above
            **({"invert_on_device": True} if getattr(self, "sparsity_on_device", False) else {}),
            # --device-sparsity: the lattice labels, 2-hop pattern and colouring on the
            # device; only when set
            **({"device_build": True} if getattr(self, "sparsity_device_build", False) else {}),
            # --colouring: only when set to a non-default algorithm, likewise
            **({"colouring": self.colouring} if getattr(self, "colouring", "rcm") != "rcm" else {}))
        # The probe colour classes are distance-2 independent sets of the block
        # pattern, the root order of --camg-aggregation mis2o; registering them changes nothing else
        from Pipeline.CoupledNewton import block_amg
        block_amg.register_root_order(self._sparsity.num_cells, self._sparsity.colour_of_cell)
    return self._sparsity


def _coefficient_mode(self):
    """The case's configured MWI coefficient, ``"frozen"``/``"implicit"`` (``None`` if unset).

    Part of the colouring-cache key.  The colouring covers two
    hops whatever the mode, so both modes would load the same colours; it is
    keyed anyway because an implicit ``d_f`` reaches three hops (``linearise``),
    and a colouring that ever follows that must not be shared across modes.
    Read outside ``_coefficient``'s per-tape override, so it is the configured
    value, the same for every assembly of a run.
    """
    frozen = getattr(self.solver, "_mwi_frozen_coefficient", None)
    return None if frozen is None else ("frozen" if frozen else "implicit")


def _colouring_cache_options(self) -> dict:
    """``build_sparsity`` keywords for ``self.colouring_cache``; ``{}`` when it is unset."""
    directory = getattr(self, "colouring_cache", None)
    if directory is None:
        return {}
    return {"colouring_cache": os.fspath(directory),
            "colouring_cache_refresh": bool(getattr(self, "colouring_cache_refresh", False)),
            "cache_tag": {"caller": "Pipeline.CoupledNewton.jacobian",
                          "coefficient": _coefficient_mode(self)}}


def _plan_setting(self):
    """What the colouring and its plan were built under, besides the mesh (cache path only).

    The mesh, the field layout and the block list are fixed for a solver's
    lifetime; these three are not.  ``assembly_is_operator`` is reset by every
    ``solve_newton(matrix_free=...)`` call and decides whether a global-reduction
    coupling is refused or omitted, so a plan built under one role must not
    serve the other.  Stored on the solver as ``self._plan_tag``.  (Named
    ``_plan_tag`` itself, the module function and
    the instance attribute shared one name, and binding the function into
    ``CollocatedSolver`` -- as ``_probe_plan`` and ``_fixed_pattern_map`` are --
    would have made ``getattr(self, "_plan_tag")`` a bound method that never
    equals a tag.)
    """
    return (os.fspath(self.colouring_cache),
            "refuse" if self.assembly_is_operator else "omit",
            _coefficient_mode(self))


def invalidate_probe_plan(self):
    """Drop the colouring and the probe plan; the next assembly rebuilds (or reloads) both.

    The explicit spelling of what was left
    implicit: ``solve_newton`` sets ``self._sparsity = None`` on every call, but
    the plan built from that colouring stays, and ``_probe_plan`` never reads
    the colouring again once it has a plan -- so that line never forces a
    rebuild, and a later solve with a different ``matrix_free`` on the same
    solver would keep the first solve's plan and skip the global-reduction
    refusal it should re-run (no caller in the repo does that today: the entry
    passes one ``--matrix-free`` to every rung).  The default path keeps that
    behaviour (one build per solver, bit for bit); the cached path
    calls this whenever ``_plan_setting`` changes.

    E4's probe-to-CSR map (``_fixed_pattern_map``) is derived from the plan
    and goes with it.  It is keyed on the plan's identity
    and would be rebuilt anyway, but until then it pins two nnz-length index
    arrays (``perm``, ``col``) -- about 7 GB at 3.5M unknowns -- while the new
    plan's map is sorted beside them.
    """
    self._sparsity = None
    self._plan = None
    self._plan_tag = None
    if getattr(self, "_fixed_map", None) is not None:
        self._fixed_map = None
    if getattr(self, "_block_map", None) is not None:
        self._block_map = None         # Block assembly map: derived from the plan as well


def _probe_plan(self):
    """Per colour, the cells to seed and where that colour's entries land.

    Every term depends on the mesh and the colouring alone, never on the
    state, so a Newton run recomputes it once instead of once per assembly.
    Each entry is ``(cells, present, column_cell)``: the colour's cells, the
    cell rows whose column set contains one of them, and which cell that is.

    With ``colouring_cache`` set the plan also records its
    setting (``_plan_setting``, kept in ``self._plan_tag``) and is invalidated
    explicitly (``invalidate_probe_plan``) when that changes; the plan rebuilt
    from a loaded colouring is the same tensors a fresh build gives.  Unset,
    nothing here differs from before.
    """
    tag = None
    if getattr(self, "colouring_cache", None) is not None:
        tag = _plan_setting(self)
        if getattr(self, "_plan_tag", None) != tag:
            # Also drops a colouring read through ``self.sparsity`` before any
            # plan existed: it carries no tag, so it is not known to match.
            invalidate_probe_plan(self)
    if self._plan is None:
        sparsity = self.sparsity
        table = sparsity.blocks[0].entity_by_colour    # cell rows: one table
        plan = []
        for colour in range(sparsity.num_colours):
            cells = sparsity.cells_of_colour[colour]
            if cells.numel() == 0:
                continue
            source = table[:, colour].long()
            present = (source >= 0).nonzero(as_tuple=True)[0]
            if present.numel() == 0:
                continue
            plan.append((cells, present, source.index_select(0, present)))
        self._plan = plan
        if tag is not None:
            self._plan_tag = tag
    return self._plan


def assemble(self, x: torch.Tensor, *, coefficient: str | None = None,
             fixed_pattern: str | None = None):
    """``J`` at ``x`` as a CSR matrix plus the blocks the preconditioner reads.

    ``coefficient`` overrides whether ``d_f`` is differentiated for the duration
    of this assembly only, exactly as ``linearise`` does and through the same
    context manager.  It exists because the adjoint needs the TRUE ``dr/dx``
    while the forward Newton is happy with the cheaper frozen one; see
    ``newton.differentiable_solution``.  Note the pattern caveat: an implicit
    ``d_f`` reaches three hops and the colouring covers two, so the assembled
    implicit matrix carries a ~2e-9 relative defect against its own
    matrix-free product.

    ``F`` is momentum against velocity, ``G`` momentum against pressure, ``D``
    continuity against velocity and ``C`` continuity against pressure.  ``C``
    is IDENTICALLY ZERO without the momentum-weighted flux, which is another
    way of saying the same thing the checkerboard probe says: the bare
    continuity row does not see the pressure at all, so the square system it
    belongs to is singular in the grid-parity mode.

    ``R`` is everything past the pressure -- the energy row against the
    temperature column on a Boussinesq case, and absent on an isothermal one.
    The blocks that couple it to the rest (buoyancy in the momentum rows,
    convection by ``u`` in the energy row) stay in ``J`` and are carried by
    the Krylov iteration; only the preconditioner treats the corner as
    block-diagonal.

    ``fixed_pattern`` (opt-in, GPU-vs-OpenFOAM feasibility plan
    E4) unpacks the SAME probes through a probe-to-CSR map built once per
    probe plan (``_fixed_pattern_map``) instead of the per-probe
    ``value != 0`` filter and the ``coalesce`` below.  ``"structural"`` keeps
    every entry the colouring can produce, exact zeros included, so ``J``'s
    pattern is the same at every assembly of a run; ``"compact"`` then drops
    the zeros in one pass and returns exactly the matrices of the default
    path.  Both return a ``FixedPatternBlocks`` that cuts F/G/D/C/R only when
    they are read, from the zero-free matrix, so the blocks are the default
    path's in both modes.  ``None`` (the default) runs the code below
    unchanged.
    """
    correction = getattr(self, "_operator_correction", None)
    if correction is not None and getattr(correction, "precondition_plain", False):
        # Learned operator correction (opt-in): the assembled matrix only builds the
        # PRECONDITIONER while the Krylov operator is the exact JVP of F + C (the entry requires --operator jvp), so
        # the preconditioner is built from the PLAIN operator's J.  No correction attached -- every other caller --
        # never reaches this branch.
        self._operator_correction = None
        try:
            return assemble(self, x, coefficient=coefficient, fixed_pattern=fixed_pattern)
        finally:
            self._operator_correction = correction
    if fixed_pattern == "blocks":
        return _assemble_blocks(self, x, coefficient=coefficient)
    if fixed_pattern is not None:
        return _assemble_fixed(self, x, coefficient=coefficient, mode=fixed_pattern)
    product = self.linearise(x, coefficient=coefficient)
    n, fields = self.n_cell, self.num_fields
    rows, cols, vals = [], [], []
    # Opt-in batching of the probes (``self.assembly_batch`` > 1): B colour/field
    # probes go through one vmapped second-order pass; the entries and their order are the same.
    batch = int(getattr(self, "assembly_batch", 1) or 1)
    probes = [(cells, present, column_cell, field)
              for cells, present, column_cell in self._probe_plan() for field in range(fields)]

    def images():
        if batch <= 1:
            tangent = torch.zeros(fields, n, device=self.device, dtype=self.dtype)
            for cells, present, column_cell, field in probes:
                tangent.zero_()
                tangent[field, cells] = 1.0
                yield present, column_cell, field, product(tangent.reshape(-1)).reshape(fields, n)
            return
        for start in range(0, len(probes), batch):
            chunk = probes[start:start + batch]
            tangents = torch.zeros(len(chunk), fields, n, device=self.device, dtype=self.dtype)
            for b, (cells, _, _, field) in enumerate(chunk):
                tangents[b, field, cells] = 1.0
            out = product.batched(tangents.reshape(len(chunk), -1)).reshape(len(chunk), fields, n)
            for b, (_, present, column_cell, field) in enumerate(chunk):
                yield present, column_cell, field, out[b]

    for present, column_cell, field, image in images():
        # All row-fields at once.  Per row-field, the old spelling cost a
        # separate nonzero -- a device sync each, one per field per probe
        # -- for a filter whose total element count does not depend on how
        # it is sliced.  An [F, P] mask is one sync and one gather, and
        # nonzero() walks a boolean row-major, so the entries come out in
        # the same order the separate passes emitted them.
        value = image.index_select(1, present)
        hit = value != 0
        where = hit.nonzero()
        take = where[:, 1]
        rows.append(where[:, 0] * n + present.index_select(0, take))
        cols.append(field * n + column_cell.index_select(0, take))
        vals.append(value[hit])

    # The tape goes before the COO section, not after it.  ``product`` is the
    # linearised residual and nothing below reads it, but it holds the forward
    # pass alive, and the lines that follow are the most allocation-heavy in the
    # solver -- so keeping it to the end of the function costs its whole size at
    # exactly the peak instant.
    del product

    # Each fragment list is also dropped the moment its concatenation exists,
    # rather than at the end of the function, so the probe's scattered output
    # and the concatenated copy of it are not both resident under the sort.
    #
    # The index pair is CONCATENATED STRAIGHT INTO the [2, nnz] array the COO
    # constructor wants.  Building ``row`` and ``col`` separately and then
    # ``torch.stack``ing them holds a second copy of both -- and it holds it
    # across ``coalesce()``, which is itself the most allocation-heavy call in
    # the solver.  Filling in place leaves three nnz-length arrays under the
    # sort instead of five, which is 11.1 GiB at the fine periodic-hills rung
    # (hills memory report).
    nonzeros = sum(int(piece.numel()) for piece in vals)
    indices = torch.empty((2, nonzeros), dtype=torch.long, device=self.device)
    torch.cat(rows, out=indices[0])
    del rows
    torch.cat(cols, out=indices[1])
    del cols
    val = torch.cat(vals)
    del vals

    full = torch.sparse_coo_tensor(
        indices, val, size=(self.n_unknowns, self.n_unknowns))
    # Dropped BEFORE the sort, not after: the constructor above keeps its own
    # references, so these two names are the only thing that would otherwise
    # keep the uncoalesced arrays alive alongside the coalesced ones.
    del indices, val
    full = full.coalesce().to_sparse_csr()

    full_crow = full.crow_indices()
    full_col = full.col_indices()
    full_val = full.values()

    def block(row_lo, row_hi, col_lo, col_hi):
        """``full[row_lo:row_hi, col_lo:col_hi]`` as CSR, cut from the CSR.

        Re-filtering the scattered COO triplet would cost a boolean
        and an ``nonzero`` over ALL nonzeros for EACH of the five blocks, plus a
        ``coalesce`` of each result -- 36.7 GiB of the assembly's peak at the
        fine periodic-hills rung, and the largest single item in it.  Cutting
        from ``full`` touches only the rows the block actually spans.

        It is also the same arithmetic, not merely a close one: ``full`` is
        ``coalesce()``d from that identical triplet, so any duplicate entry has
        already been summed exactly as the old per-block ``coalesce()`` summed
        it, and a coalesced CSR has its columns sorted within each row, so
        filtering a row's columns preserves the CSR invariant without a second
        sort.
        """
        lo, hi = int(full_crow[row_lo]), int(full_crow[row_hi])
        span = full_col[lo:hi]
        keep = (span >= col_lo) & (span < col_hi)
        # ``running[i]`` is how many of the span's first ``i`` entries survive
        # the column filter, so reading it AT THE ROW BOUNDARIES is the new row
        # pointer directly -- no per-row counting pass.
        running = torch.zeros(span.numel() + 1, dtype=full_crow.dtype,
                              device=self.device)
        torch.cumsum(keep.to(full_crow.dtype), 0, out=running[1:])
        crow = running.index_select(
            0, full_crow[row_lo:row_hi + 1] - lo)
        return torch.sparse_csr_tensor(
            crow, span[keep] - col_lo, full_val[lo:hi][keep],
            size=(row_hi - row_lo, col_hi - col_lo))

    velocity, pressure = self.velocity, self.pressure
    blocks = {
        "J": full,
        "F": block(0, velocity, 0, velocity),
        "G": block(0, velocity, velocity, pressure),
        "D": block(velocity, pressure, 0, velocity),
        "C": block(velocity, pressure, velocity, pressure),
    }
    if self.n_unknowns > pressure:
        blocks["R"] = block(pressure, self.n_unknowns,
                            pressure, self.n_unknowns)
    return blocks


# ------------------------------------------- fixed probe-to-CSR map
#
# GPU-vs-OpenFOAM feasibility plan (docs/research/2026-10-04-gpu-vs-openfoam-
# feasibility.md, sections 3 and 3.2).  ``assemble`` above pays at EVERY
# assembly for work that depends on the mesh alone: a ``value != 0`` filter
# with one ``nonzero`` -- one device sync -- per probe, a ``coalesce`` that
# sorts every nonzero by (row, col) to reach CSR order, and four or five block
# cuts with two host reads each.  Where a probe's entries land is fixed by the
# colouring, so the sort is done ONCE here: the permutation from probe order to
# CSR order is built at the first assembly and every later assembly is one
# gather.  The filter is also what made the matrix PATTERN move between Newton
# steps -- an entry that cancels to exactly zero drops out -- and a moving
# pattern is what the coupled AMG's plan cache tripped on (``cached_amg_plan``
# in the entry; the LDC96 reuse failure).
#
#   "structural"  J stores every entry the colouring can produce, zeros
#                 included (as +0.0), so its pattern is the same at every
#                 assembly of a run: the pattern a once-per-run AMG plan needs
#                 (plan E5).  No device sync per assembly.  The coupled AMG must
#                 then be built with ``block_amg.from_field_blocked_csr(...,
#                 drop_zero_blocks=True)`` to see today's block pattern; the
#                 entry does that.
#   "compact"     the same gather, then the zeros dropped in ONE pass (one
#                 sync): J's crow, col and values are exactly the default
#                 path's, so a deterministic run is bit-identical to it.
#   "blocks"      (``_assemble_blocks``) the
#                 same probes written straight into the coupled AMG's 5 x 5
#                 BlockCSR on the structural BLOCK pattern: no scalar CSR, no
#                 nnz-length map.  The Krylov operator of an assembling step is
#                 then the hierarchy's fine-level scalar copy (``BlockJacobian``).
FIXED_PATTERNS = ("structural", "compact")          # the scalar-CSR modes of _assemble_fixed
ASSEMBLY_PATTERNS = FIXED_PATTERNS + ("blocks",)   # every assemble(fixed_pattern=...) mode (M5 adds "blocks")
# The largest key array ``_fixed_pattern_map`` sorts in one call; a longer one is sorted bucket by bucket
# (``sort_unique_keys``): torch's sort refuses more than INT_MAX entries.
SORT_LIMIT = 2 ** 31 - 1


def sort_unique_keys(key: torch.Tensor, size: int, limit: int | None = None):
    """``(sorted keys, permutation, crow)`` of the distinct keys ``row * size + col`` (int64, rows in ``[0, size)``):
    the result of ``torch.sort`` and the CSR row pointer, ``crow[r]`` = the number of keys in the rows below ``r``.

    Up to ``limit`` entries (default ``SORT_LIMIT``): one ``torch.sort``.  Above it the keys are split into buckets of
    consecutive rows, each holding at most an eighth of the limit (the row histogram accumulated over slices of at
    most 2^30 entries); every bucket's positions are gathered slice by slice, its keys sorted and written at the
    bucket's place in the output.  The keys are distinct, so the sorted order is unique and both ways give the same
    permutation; the large path holds the input, the two outputs and one bucket's work at a time."""
    limit = int(SORT_LIMIT if limit is None else limit)
    total = int(key.numel())
    dev = key.device
    if total <= limit:
        sorted_key, perm = torch.sort(key)
        crow = torch.searchsorted(sorted_key, torch.arange(size + 1, device=dev) * size)
        return sorted_key, perm, crow
    chunk = min(limit, 1 << 30)                      # the slices the histogram and the gathers read
    counts = torch.zeros(size, dtype=torch.long, device=dev)
    for s in range(0, total, chunk):
        counts += torch.bincount(torch.div(key[s:s + chunk], size, rounding_mode="floor"), minlength=size)
    crow = torch.zeros(size + 1, dtype=torch.long, device=dev)
    crow[1:] = torch.cumsum(counts, 0)
    del counts
    target = max(limit // 8, 1)
    edges = [0]
    while edges[-1] < size:
        start = int(crow[edges[-1]])
        nxt = int(torch.searchsorted(crow, torch.tensor([start + target], device=dev), right=True)[0]) - 1
        edges.append(min(max(nxt, edges[-1] + 1), size))
    sorted_key = torch.empty_like(key)
    perm = torch.empty(total, dtype=torch.long, device=dev)
    for lo, hi in zip(edges[:-1], edges[1:]):
        k_lo, k_hi = lo * size, hi * size
        a, b = int(crow[lo]), int(crow[hi])
        pos = []
        for s in range(0, total, chunk):
            part = key[s:s + chunk]
            pos.append(torch.nonzero((part >= k_lo) & (part < k_hi)).reshape(-1) + s)
        pos = torch.cat(pos)
        kb, ob = torch.sort(key.index_select(0, pos))
        sorted_key[a:b] = kb
        perm[a:b] = pos.index_select(0, ob)
        del pos, kb, ob
    return sorted_key, perm, crow


def _fixed_pattern_map(self):
    """The probe-to-CSR map of the current probe plan, built once and cached on ``self``.

    ``(row, col)`` of every entry the probes can produce, in the order
    ``_assemble_fixed`` writes them -- probe by probe, in ``assemble``'s probe
    order, and within a probe the ``[F, P]`` block ``image.index_select(1,
    present)`` returns, row-major -- sorted ONCE into CSR order.  Kept: the CSR
    ``crow`` / ``col`` (shared by every ``J`` built from this map; nothing in
    the solver writes index arrays in place) and ``perm``, the probe-order
    position of each CSR slot.

    Keyed on the IDENTITY of ``self._plan``: whatever rebuilds the plan (a new
    solver, or the colouring cache's ``invalidate_probe_plan``) rebuilds this.
    A duplicate ``(row, col)`` needs two cells of one colour in one row's column
    set, which ``build_sparsity``'s collision check already refuses; it is
    checked again because a gather, unlike the ``coalesce`` it replaces, would
    store two entries where ``coalesce`` summed them.

    Mesh-only, so ``solve_newton(component_timers=True)`` builds it with the
    probe plan, under ``sparsity_seconds``.
    """
    plan = self._probe_plan()
    cached = getattr(self, "_fixed_map", None)
    if cached is not None and cached["plan"] is plan:
        return cached
    # A stale map (its plan was rebuilt, e.g. ``invalidate_probe_plan``) is dropped BEFORE the new one
    # is sorted: otherwise two maps -- ``perm``, ``col`` and the old plan, about
    # 16 B per structural entry -- are resident across the sort, the moment this function peaks.
    self._fixed_map = cached = None
    started = time.perf_counter()
    n, fields, size = self.n_cell, self.num_fields, self.n_unknowns
    probes = [(cells, present, column_cell, field)
              for cells, present, column_cell in plan for field in range(fields)]
    row_of_field = (torch.arange(fields, device=self.device) * n).unsqueeze(1)      # [F, 1]
    offsets, total = [], 0
    for _, present, _, _ in probes:
        offsets.append(total)
        total += fields * present.numel()
    # Written straight into one buffer rather than a list of per-probe keys
    # concatenated afterwards, which held two nnz-length int64 copies at once: 6 GB more at the
    # 750M entries of the fine periodic-hills rung, right before the sort that is this map's peak.
    flat_key = torch.empty(total, dtype=torch.long, device=self.device)
    for (_, present, column_cell, field), start in zip(probes, offsets):
        # row-major [F, P]: row field f at cell present[p], column field ``field`` at column_cell[p]
        torch.add((row_of_field + present) * size, field * n + column_cell,
                  out=flat_key[start:start + fields * present.numel()].view(fields, -1))
    # one sort up to SORT_LIMIT entries, bucket by bucket above it (the same order: the keys are distinct)
    key, perm, crow = sort_unique_keys(flat_key, size)
    del flat_key
    if key.numel() > 1 and bool((key[1:] == key[:-1]).any()):
        raise RuntimeError(
            "fixed Jacobian pattern: two probes write the same (row, col); the "
            "colouring must give each row one cell per colour (build_sparsity's "
            "collision check), and a gather cannot sum duplicates as coalesce did")
    col = key % size
    del key
    if int(crow[-1]) != total:       # also the one sync that makes the timing below honest
        raise RuntimeError(f"fixed Jacobian pattern: crow ends at {int(crow[-1])}, expected {total}")
    cached = {"plan": plan, "probes": probes, "offsets": offsets, "nnz": total,
              "perm": perm, "crow": crow, "col": col, "zeros_logged": False,
              "seconds": time.perf_counter() - started}
    self._fixed_map = cached
    logging.getLogger(__name__).info(
        "fixed Jacobian pattern (E4): %d structural entries (%.1f per row); probe-to-CSR "
        "map built once in %.2f s", total, total / max(size, 1), cached["seconds"])
    return cached


def _probe_images(self, product, probes, batch: int):
    """Each probe's ``[F, n]`` image, in ``probes`` order.

    The generator ``images`` inside ``assemble``, with the same tangents and the
    same opt-in batching, minus the bookkeeping the fixed map does not need.
    """
    n, fields = self.n_cell, self.num_fields

    def seed(row, cells):
        # the same seeds, without the host round trip of a boolean-index assignment (W2d / W3 sync counts: one per
        # probe); an index tensor fills by index
        if cells.device != row.device:
            cells = cells.to(row.device)
        if cells.dtype == torch.bool:
            row.masked_fill_(cells, 1.0)
        else:
            row.index_fill_(0, cells, 1.0)

    if batch <= 1:
        tangent = torch.zeros(fields, n, device=self.device, dtype=self.dtype)
        for cells, _, _, field in probes:
            tangent.zero_()
            seed(tangent[field], cells)
            yield product(tangent.reshape(-1)).reshape(fields, n)
        return
    for start in range(0, len(probes), batch):
        chunk = probes[start:start + batch]
        tangents = torch.zeros(len(chunk), fields, n, device=self.device, dtype=self.dtype)
        for b, (cells, _, _, field) in enumerate(chunk):
            seed(tangents[b, field], cells)
        out = product.batched(tangents.reshape(len(chunk), -1)).reshape(len(chunk), fields, n)
        for b in range(len(chunk)):
            yield out[b]


def drop_explicit_zeros(matrix: torch.Tensor) -> torch.Tensor:
    """``matrix`` (CSR) without its stored zeros: what ``assemble``'s ``value != 0`` filter keeps.

    ``-0.0`` counts as zero, as it does in that filter.  One sync (the
    ``nonzero``).  Rows keep their column order, so the result is a valid CSR
    without a sort; its row pointer is read off a running count of the kept
    entries, as ``assemble``'s block cut reads its own.
    """
    crow, col, values = matrix.crow_indices(), matrix.col_indices(), matrix.values()
    keep = values != 0
    running = torch.zeros(values.numel() + 1, dtype=crow.dtype, device=values.device)
    torch.cumsum(keep.to(crow.dtype), 0, out=running[1:])
    where = keep.nonzero(as_tuple=True)[0]
    return torch.sparse_csr_tensor(running.index_select(0, crow), col.index_select(0, where),
                                   values.index_select(0, where), size=matrix.shape)


def _csr_block(full, row_lo, row_hi, col_lo, col_hi):
    """``full[row_lo:row_hi, col_lo:col_hi]`` as CSR: ``assemble``'s ``block``, lifted out unchanged."""
    full_crow, full_col, full_val = full.crow_indices(), full.col_indices(), full.values()
    lo, hi = int(full_crow[row_lo]), int(full_crow[row_hi])
    span = full_col[lo:hi]
    keep = (span >= col_lo) & (span < col_hi)
    running = torch.zeros(span.numel() + 1, dtype=full_crow.dtype, device=full_col.device)
    torch.cumsum(keep.to(full_crow.dtype), 0, out=running[1:])
    crow = running.index_select(0, full_crow[row_lo:row_hi + 1] - lo)
    return torch.sparse_csr_tensor(
        crow, span[keep] - col_lo, full_val[lo:hi][keep],
        size=(row_hi - row_lo, col_hi - col_lo))


class FixedPatternBlocks(Mapping):
    """``assemble``'s block dict with ``J`` built and F/G/D/C/R cut on first read.

    The coupled-AMG arms read ``J`` alone, so the block cuts the default path
    makes at every assembly were paid for nothing there.  The first read of
    any other block cuts ALL of them, exactly as the default path does, from
    the matrix without stored zeros (``drop_explicit_zeros``), so ``F``, ``G``,
    ``D``, ``C`` and ``R`` are exactly the default path's blocks in both modes:
    the SIMPLE-shaped preconditioners, the learned local action and the PTC
    re-arm see the matrices they always saw.  Keys, ``in``, ``len`` and the
    iteration order are the default dict's; ``get`` and ``dict(blocks)`` read
    through ``__getitem__``.  Read-only: ``newton._reshift`` copies into a
    plain dict before it writes.

    All at once, not one block per read: the zero-free
    copy is a whole second ``J`` in "structural" mode, and cutting only the
    block asked for kept it pinned for as long as the blocks lived whenever a
    consumer read some blocks but not all -- the learned local action's
    ``ConstraintAdapter`` reads F, G, C and holds the blocks for the
    preconditioner's lifetime -- which is memory the default path never held.
    """

    def __init__(self, J, cuts: dict, source):
        self._built = {"J": J}
        self._cuts = dict(cuts)          # name -> (row_lo, row_hi, col_lo, col_hi)
        self._source = source            # the zero-free CSR, or a callable that makes it once

    def __getitem__(self, name):
        if name in self._built:
            return self._built[name]
        if name not in self._cuts:
            raise KeyError(name)         # a name the default dict lacks too
        source = self._source() if callable(self._source) else self._source
        self._source = None              # read once: every block is cut below
        for key, bounds in self._cuts.items():
            self._built[key] = _csr_block(source, *bounds)
        return self._built[name]

    def __contains__(self, name):
        return name == "J" or name in self._cuts

    def __iter__(self):
        return iter(("J", *self._cuts))

    def __len__(self):
        return 1 + len(self._cuts)

    def built(self) -> tuple:
        """The blocks cut so far (``J`` is always built)."""
        return tuple(self._built)


def _assemble_fixed(self, x: torch.Tensor, *, coefficient: str | None, mode: str):
    """``assemble(fixed_pattern=mode)``: the same tape and probes, unpacked through ``_fixed_pattern_map``.

    The values are the probes' own numbers, gathered: in deterministic mode
    every entry the default path keeps is bit-identical here, at the same CSR
    slot, and ``"structural"`` adds the entries the default path filtered out,
    each stored as +0.0 (``+ 0.0`` maps a probe's -0.0 there and leaves every
    other value bit for bit).  Memory: the per-assembly temporaries are two
    nnz-length value arrays; the ``[2, nnz]`` index pair and the sort of the
    default path are gone, and the map keeps one nnz-length ``perm`` beside the
    ``col`` it shares with ``J``.
    """
    if mode not in FIXED_PATTERNS:
        raise ValueError(f"unknown fixed_pattern {mode!r}; valid: {', '.join(FIXED_PATTERNS)}")
    # Mesh-only, and built BEFORE the tape so its sort does not stack on the tape's peak.
    layout = _fixed_pattern_map(self)
    product = self.linearise(x, coefficient=coefficient)
    fields = self.num_fields
    batch = int(getattr(self, "assembly_batch", 1) or 1)
    probes = layout["probes"]
    flat = torch.empty(layout["nnz"], device=self.device, dtype=self.dtype)
    for (_, present, _, _), start, image in zip(probes, layout["offsets"],
                                                _probe_images(self, product, probes, batch)):
        flat[start:start + fields * present.numel()].view(fields, -1).copy_(
            image.index_select(1, present))
    # The tape goes first, as in ``assemble``: it holds the forward pass alive.
    del product
    values = flat.index_select(0, layout["perm"])
    del flat
    if mode == "structural":
        values.add_(0.0)                 # -0.0 -> +0.0 before J can alias ``values``
    full = torch.sparse_csr_tensor(layout["crow"], layout["col"], values,
                                   size=(self.n_unknowns, self.n_unknowns))
    if mode == "compact":
        full = drop_explicit_zeros(full)
        zeros = None if layout["zeros_logged"] else layout["nnz"] - int(full.values().numel())
        source = full
    else:
        zeros = None if layout["zeros_logged"] else int((values == 0).sum())
        source = (lambda: drop_explicit_zeros(full))
    if zeros is not None:
        # Once per map: how many stored entries the structural pattern adds at the
        # first assembly, i.e. the extra matvec traffic "structural" pays.
        layout["zeros_logged"] = True
        logging.getLogger(__name__).info(
            "fixed Jacobian pattern (E4, %s): %d of %d structural entries are exactly zero "
            "at this first assembly (%.1f%%); 'structural' stores them, 'compact' and the "
            "default drop them", mode, zeros, layout["nnz"], 100.0 * zeros / max(layout["nnz"], 1))
    velocity, pressure, size = self.velocity, self.pressure, self.n_unknowns
    cuts = {"F": (0, velocity, 0, velocity), "G": (0, velocity, velocity, pressure),
            "D": (velocity, pressure, 0, velocity), "C": (velocity, pressure, velocity, pressure)}
    if size > pressure:
        cuts["R"] = (pressure, size, pressure, size)
    return FixedPatternBlocks(full, cuts, source)


# ------------------------------------------------- block assembly
#
# docs/research/2026-10-07-gpu-optimisation-roadmap.md (memory).  The "structural" assembly above
# writes J as a field-blocked scalar CSR (418M stored entries on DHC88: values, int64 column indices
# and the int64 ``perm`` of its probe-to-CSR map, 3.3 GB each, the map resident for the whole run),
# and the coupled AMG then converts that CSR into 5 x 5 blocks (``block_amg.from_field_blocked_csr``)
# -- a fourth nnz-sized array plus the conversion's temporaries -- before anything else is built.
# On a 152^3 mesh (2.17e9 stored entries) neither the CSR's int32 row pointer nor one GPU's memory
# holds that.  ``_assemble_blocks`` writes each probe straight into the block values the AMG reads;
# the map is one block index per (row cell, colour), i.e. per BLOCK (16.7M on DHC88).


def _block_assembly_map(self):
    """The probe-to-BLOCK map of the current probe plan, built once and cached on ``self``.

    Plan entry ``c`` (one colour) seeds the cells of that colour; each of its ``present`` row cells ``i`` reads
    exactly one seeded cell ``column_cell[i]`` in its stencil.  The pairs ``(i, column_cell[i])`` over all
    entries are therefore the structural BLOCK pattern of J: the blocks ``block_amg.from_field_blocked_csr`` finds
    in a "structural" J with ``drop_zero_blocks=False`` (every field pair of such a pair is a structural entry,
    ``_fixed_pattern_map``).  Sorted once by the key ``i n + j`` -- the order of that function's ``torch.unique``
    -- so ``row``, ``col`` are its BlockCSR index arrays; ``slots[c]`` is the block index of each present row of
    entry ``c``.  A repeated key needs two cells of one colour in one row's stencil, which ``build_sparsity``'s
    collision check refuses; it is checked again here (a write, unlike a sum, would lose one of them).

    Mesh-only: ``solve_newton(component_timers=True)`` builds it with the probe plan under ``sparsity_seconds``.
    Keyed on the identity of ``self._plan`` (``invalidate_probe_plan`` drops it)."""
    plan = self._probe_plan()
    cached = getattr(self, "_block_map", None)
    if cached is not None and cached["plan"] is plan:
        return cached
    self._block_map = cached = None
    started = time.perf_counter()
    n = self.n_cell
    keys = torch.cat([present * n + column_cell for _, present, column_cell in plan])
    total = int(keys.numel())
    uniq, inverse = torch.unique(keys, sorted=True, return_inverse=True)
    del keys
    if int(uniq.numel()) != total:
        raise RuntimeError(
            "block assembly: two probes read the same (row cell, column cell) block; the colouring must give "
            "each row one cell per colour (build_sparsity's collision check)")
    index_dtype = torch.int32 if total < 2 ** 31 - 1 else torch.long
    slots, offset = [], 0
    for _, present, _ in plan:
        m = int(present.numel())
        slots.append(inverse[offset:offset + m].to(index_dtype))
        offset += m
    del inverse
    cached = {"plan": plan, "slots": slots, "row": uniq // n, "col": uniq % n, "nnzb": total,
              "seconds": time.perf_counter() - started}
    del uniq
    self._block_map = cached
    logging.getLogger(__name__).info(
        "block assembly (M5): %d structural 5x5 blocks (%.2f per cell), probe-to-block map built once in %.2f s",
        total, total / max(n, 1), cached["seconds"])
    return cached


class _BlockOperator:
    """``J @ v`` for a ``BlockJacobian`` whose preconditioner attached the product (``attach_operator``)."""

    def __init__(self, apply, shape):
        self.apply, self.shape = apply, shape

    def __matmul__(self, v: torch.Tensor) -> torch.Tensor:
        if v.dim() != 1:
            raise ValueError("BlockJacobian's operator takes one flat vector")
        return self.apply(v)


class BlockJacobian(Mapping):
    """``assemble(fixed_pattern="blocks")``: J as the coupled AMG's 5 x 5 BlockCSR, and no scalar CSR.

    ``"A"`` is the ``block_amg.BlockCSR`` of J on the structural block pattern -- bit for bit the one
    ``from_field_blocked_csr(structural J, drop_zero_blocks=False)`` gives -- and the camg factory builds the
    hierarchy on it directly.  ``"J"`` is the Krylov operator of the step: no matrix exists, so the factory
    attaches the product of the hierarchy's fine-level scalar copy, which IS this matrix in another storage order
    (``block_amg.CoupledAMG.fine_operator``; sums in another order, so products agree with the CSR's to rounding),
    and then releases ``"A"`` here (``release_blocks``: the hierarchy holds it).  Reading ``"J"`` before the
    attach, or ``"A"`` after the release, raises ``KeyError`` with the reason.  F/G/D/C/R do not exist: only a
    consumer of the block matrix (the classical coupled AMG) can take this assembly, which the entry enforces."""

    def __init__(self, A):
        self._A = A
        self._operator = None

    def __getitem__(self, name):
        if name == "A":
            if self._A is None:
                raise KeyError("A: the block matrix was released to the preconditioner (BlockJacobian.release_blocks)")
            return self._A
        if name == "J":
            if self._operator is None:
                raise KeyError("J: a block assembly keeps no scalar Jacobian; its Krylov operator is attached by the "
                               "preconditioner build (BlockJacobian.attach_operator)")
            return self._operator
        raise KeyError(name)

    def __contains__(self, name):
        return name in ("J", "A")

    def __iter__(self):
        return iter(("J", "A"))

    def __len__(self):
        return 2

    @property
    def n_unknowns(self) -> int:
        A = self._A
        return None if A is None else A.shape[0] * A.F

    def attach_operator(self, apply) -> None:
        """``apply(v) = J v`` (flat, field-blocked): what ``blocks["J"] @ v`` returns from now on."""
        size = self.n_unknowns
        self._operator = _BlockOperator(apply, (size, size))

    def release_blocks(self) -> None:
        self._A = None


def _assemble_blocks(self, x: torch.Tensor, *, coefficient: str | None):
    """``assemble(fixed_pattern="blocks")``: the same tape and probes, written into 5 x 5 blocks.

    Probe ``(c, g)`` (colour c, column field g) returns ``image[f, i]`` = row field f of cell i against column
    field g of cell ``column_cell[i]``; it is entry ``(f, g)`` of block ``slots[c][i]``.  Every entry of every
    block is written exactly once (all fields f of all present rows, for every g), in the order of the default
    probes, and then ``+ 0.0`` maps a probe's -0.0 to +0.0 -- the value the "structural" assembly stores and
    ``from_field_blocked_csr`` accumulates onto 0.0 -- so the block values equal that path's bit for bit
    (tests/test_m5_memory.py).  Memory: the blocks (8 F^2 bytes per block) and one probe image at a time; no
    nnz-length index exists."""
    from Pipeline.CoupledNewton.block_amg import BlockCSR

    layout = _block_assembly_map(self)
    product = self.linearise(x, coefficient=coefficient)
    n, fields = self.n_cell, self.num_fields
    batch = int(getattr(self, "assembly_batch", 1) or 1)
    plan = layout["plan"]
    probes = [(cells, present, column_cell, field)
              for cells, present, column_cell in plan for field in range(fields)]
    slot_of = [slot for slot in layout["slots"] for _ in range(fields)]
    blocks = torch.zeros(layout["nnzb"], fields, fields, device=self.device, dtype=self.dtype)
    row_fields = torch.arange(fields, device=self.device).unsqueeze(0)          # [1, F]
    current = (None, None)                                                       # (slot tensor, its long copy)
    for (_, present, _, field), slot, image in zip(probes, slot_of, _probe_images(self, product, probes, batch)):
        if current[0] is not slot:
            current = (slot, slot.long().unsqueeze(1))                           # [P, 1], once per colour
        column_field = torch.full((1, 1), field, dtype=torch.long, device=self.device)
        blocks.index_put_((current[1], row_fields, column_field), image.index_select(1, present).t())
    # The tape goes first, as in ``assemble``: it holds the forward pass alive.
    del product
    blocks.add_(0.0)                    # -0.0 -> +0.0, as the structural assembly + the block conversion
    return BlockJacobian(BlockCSR(layout["row"], layout["col"], blocks, (n, n)))
