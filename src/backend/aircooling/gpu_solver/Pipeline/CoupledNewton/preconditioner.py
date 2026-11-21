"""The block preconditioner, its pressure operator, and the CSR arithmetic for both.

Damped Jacobi on momentum, multigrid on a pressure operator chosen from the
momentum-interpolation arm, and a velocity correction: the SIMPLE shape.  The
three ``_csr_*`` helpers live here because this is the only concern that builds
a matrix out of other matrices; the assembly next door emits COO from probes
and never adds two operators.

Read off the ``CollocatedSolver`` passed in: ``solver``, ``interior``,
``n_cell``, ``velocity``, ``pressure``, ``mwi``, ``device``, and the blocks
``assemble`` returned.
"""
from __future__ import annotations

import time

import torch

from SparseSolver.amg import build_hierarchy

# Which operator the pressure V-cycles coarsen.  Measured on the 16^3 cavity:
# see CollocatedSolver.simple_preconditioner for the spectra that ordered these.
PRESSURE_OPERATORS = ("auto", "laplacian", "mwi_block", "simple", "schur")

# Which SHAPE the block preconditioner has, and WHICH TO SELECT.  True
# |b - J x| / |b| at the production 1600-matvec budget, measured in
# one process per operator on three independently captured flat-plate systems:
# the laminar stage-B k-omega capture, and the SST arm's early and late
# true-turbulent states -- the last being a case where every other
# arm reads ~1.0000 and 1600 preconditioned matvecs buy nothing.
#
#     shape       laminar   turb early   turb late
#     none         0.1733     0.00520      0.9996
#     simple       0.9792     0.17736      0.9999
#     restored     0.1159     0.00374      0.9981
#     meanflow     0.0986     0.00264      0.8262
#
# and, on the two BUOYANT operators of the DataCenter campaign, both captured at
# their own continuation rung 2 (one process per operator):
#
#     shape       heated cylinder   DataCenter rung 2
#     none          0.29212           0.0020638
#     simple        0.00089622        0.98749
#     restored      0.88734           0.81570
#     covered       0.00057561        9.942e-09 in 604 matvecs, CONVERGED
#
# SELECT "simple" (the default) on a case with NO transported block: the other
# two refuse such a case by construction, and every laminar and buoyant result
# this solver has is on it.
#
# SELECT "restored" or "meanflow" on a transported block whose rows have gone
# near-singular and indefinite, where "simple" is measured WORSE than no
# preconditioner at all.  Between those two, "meanflow" wins on all three
# operators above and is the one that moved a real march: on the arm-P TMR flat
# plate (SST 137x97, --ptc --equilibrate ruiz --reassemble-drift 1e-4) it fired
# the PTC handover at Newton 166 where "restored" emitted ZERO handovers in 400
# steps, and finished at ||r||2 4.14e-06 against 1.48e-04 -- 35.7x lower on 34%
# FEWER Krylov iterations (422,400 against 639,920), and 3.8% less wall clock at
# matched steps.  It costs one batched SVD per linearisation (0.01-0.02 s over
# "restored", against 4.6-7.6 s for "simple"'s AMG setup) and 128 bytes per cell
# (3.3 MB at 26,112 cells, 19.7 MB at the finest periodic-hills rung).
#
# DO NOT select "meanflow" expecting it to help a case whose UNCOVERED block is
# not the mean flow.  DataCenter runs "simple", which already covers momentum
# and pressure, and its near-null direction after preconditioning is 99.91% the
# ENERGY row; nothing in this shape touches that.
#
# SELECT "covered" for exactly that case: it is the SIMPLE head with
# "restored"'s floored tail and its Gauss-Seidel pass, i.e. the one combination
# the other three do not offer, because each of them decides head and tail
# together.  On DataCenter's rung 2 the shipped shape is 478x WORSE than no
# preconditioner and its own tail is what does it -- the energy diagonal there
# carries 30,333 negative entries of 498,337 and reaches max|1/d| = 1.14e+07 --
# and this shape converges that system in 604 matvecs.  On a real march it takes
# rung 2 from 1600 matvecs never meeting tolerance to a mean of 53 always
# meeting it, and ||r||2 7.4x lower.  It COSTS rung 1, where the floor is the
# wrong instrument (136 steps against 78), and it does NOT finish rung 2: that
# rung now fails as an outer-iteration limit cycle rather than an inner solve.
# docs/research/2026-08-18-dc-energy-coverage.md.
#
# AND IT IS NOT A CURE for a parked inner solve.  It is measured on ONE case
# family; 0.8262 is still a solve that has achieved almost nothing in 1600
# matvecs; the march above still PARKS, at max|r| 5.46e-07 against the 1e-10
# gate, with its post-handover inner solves back at 0.92-1.02 and every step
# exhausting its matvec budget.  Deflated restarting does not rescue that --
# measured flat to slightly NEGATIVE at ranks 8/20/50 on the march's own
# reassembled state, against a recorded mode-1 recovery of 63.6%.
# "buoyant" / "buoyant-sgs": the SIMPLE head plus an AMG-SOLVED energy
# block coupled to it block-triangularly (energy first, its buoyancy column fed into
# the momentum right-hand side; -sgs adds the convective back-substitution).  The
# "simple" shape sweeps the energy block with Jacobi and leaves both couplings to
# GMRES, which stalls at Ra = 1e6 (GMRES parked above 0.1 at 1600 matvecs every
# Newton step, measured on NaturalConvection/Ra1e6).
PRECONDITIONER_SHAPES = ("simple", "restored", "meanflow", "covered",
                         "buoyant", "buoyant-sgs")

# Singular-value floor of the mean-flow point block, as a fraction of that
# block's own largest.  Same device as ``RESTORE_ALPHA`` and for the same reason:
# measured on the captured stage-B flat-plate system, the per-cell
# 4x4 blocks reach a smallest singular value of 4.19e-07 against a median of
# 1.18e-05, so an unfloored block inverse amplifies by 2.39e+06 -- WORSE than
# the 8.89e+04 of the momentum-diagonal inverse whose flooding of the Arnoldi
# basis is the recorded reason the mean flow is left alone at all.  The floor is
# what makes a block inverse a different object from the arm that was rejected.
#
# The dose-response, true ``|b - J x| / |b|`` at the production 1600-matvec
# budget, on THREE independently captured systems (the laminar stage-B flat
# plate and the SST arm's early and late true-turbulent states), against
# ``shape="restored"`` at 0.1159 / 0.0037 / 0.9981:
#
#     alpha        0.01     0.03     0.1      0.3      0.5      1.0      3.0
#     laminar      0.1242   0.1448   0.1249   0.0986   0.1035   0.1057   0.0987
#     turb early   0.00213  0.00234  0.00254  0.00264  0.00265  0.00375  0.00342
#     turb late    0.9787   0.9460   0.8359   0.8262   0.8290   0.7747   0.8330
#
# NO value wins all three -- the early state wants the floor as low as it will
# go and the late one wants it an order higher -- so 0.3 is taken as the best
# WORST case rather than as an optimum: it is the only value that beats
# ``restored`` on all three (1.17x, 1.42x, and off 1.0000 at all), and it is the
# one that crosses the 0.1 forcing target on the laminar system.
#
# **Do not make this adaptive without reading the open-problem paragraph in
# ``simple_preconditioner``.**  The obvious rule -- one global floor
# ``sigma_max / budget``, which bounds the head's operator norm exactly and
# reads only build-time quantities -- was built, calibrated on two of these
# captures and scored on a held-out third, and lost BOTH legs of its bar.
MEANFLOW_ALPHA = 0.3

# What ``newton.solve_newton`` accepts, which is one MORE than this factory can
# build: "none" is no preconditioner at all.  It is not a shape -- there is no
# apply to construct -- so it is handled where the preconditioner is chosen and
# refused here.  It exists because it is the control arm, and the recorded
# lesson is that the control arm is the one that decides whether a
# preconditioner is worth its setup: on the four captured RANS systems the
# shipped shape loses to it at 7 of 8 bundle/window cells
# (docs/research/2026-08-17-inner-solve-true-regime.md section 3).
NEWTON_PRECONDITIONERS = ("none",) + PRECONDITIONER_SHAPES

# The restoration floor, as a fraction of the block's own max|diagonal|.
# Measured on the captured stage-B flat-plate Jacobian (26,112 cells,
# 137x97 wall-stretched, cell aspect up to 2.4e4), true |b - Jx|/|b| after the
# production 1600-matvec budget, against 0.1734 for NO preconditioner.  These
# are THIS function at its own defaults (sweeps 2), re-measured through this
# code path during verification -- not the exploratory probe's one-sweep family,
# which reads 0.359 / 0.131 / 0.121 / 0.125 / 0.140 and whose 0.1 entry is a
# different arm from the four around it:
#
#     alpha    0.01    0.05    0.1     0.3     1.0
#     floor    0.287   0.118   0.116   0.120   0.121
#
# A shallow optimum at 0.1 in BOTH families, which is also where the
# amplification cap |M| ~ 30x sits; 0.01 is already worse than no
# preconditioner.  Evidence:
# docs/research/2026-08-16-line-implicit-inner-solve.md, "Dominance/equilibration
# probe", and the Verification section of
# docs/research/2026-08-16-sst-wall-active-and-precond-wire-in.md.
RESTORE_ALPHA = 0.1


# Below this the mesh is orthogonal up to round-off; see the pressure="auto" rule.
ORTHOGONAL_DEGREES = 0.5


def max_nonorthogonality_degrees(self) -> float:
    """Largest angle between an internal face normal and its owner->neighbour line.

    Computed once per solver and cached; cyclic and boundary faces are excluded
    (their partner is a ghost, whose position is not a cell centre).
    """
    cached = getattr(self, "_max_nonorth_degrees", None)
    if cached is not None:
        return cached
    lookup = torch.full((self.solver.mask_interior_cell.numel(),), -1,
                        dtype=torch.long, device=self.device)
    lookup[self.interior] = torch.arange(self.n_cell, device=self.device)
    senders = self.solver.C_senders.long()
    receivers = self.solver.F_receivers.long()
    internal = (lookup[senders] >= 0) & (lookup[receivers] >= 0)
    pos = self.solver.cpd_cell_pos.reshape(-1, 3).to(torch.float64)
    d = pos[receivers[internal]] - pos[senders[internal]]
    n = self.solver.unv_face.reshape(-1, 3).to(torch.float64)[internal]
    cos = (d * n).sum(-1).abs() / d.norm(dim=-1).clamp_min(1e-300)
    angle = float(torch.rad2deg(torch.arccos(cos.clamp(max=1.0))).max()) if cos.numel() else 0.0
    self._max_nonorth_degrees = angle
    # Once per solver (cached above); newton.py builds the preconditioner without a
    # logger, so the decision is printed where the run's stdout log records it.
    print(f"[preconditioner] max non-orthogonality {angle:.3g} deg "
          f"(pressure='auto' uses {'laplacian' if angle < ORTHOGONAL_DEGREES else 'mwi_block'})",
          flush=True)
    return angle


def compact_pressure_laplacian(self) -> torch.Tensor:
    """``-(D d_f G)`` on the COMPACT face stencil: the pressure equation itself.

    ``sum_f d_f |S_f| (p_Q - p_P) / |d_PQ|`` over a cell's faces, i.e. exactly
    the operator a segregated code's pressure equation solves, assembled from
    the mesh and from the SAME ``d_f`` the residual used rather than a guess at
    it.  Seven entries per row on a hex mesh, symmetric to 0.000, and singular
    only in the constant mode -- which is what smoothed aggregation is built
    for, and what the two alternatives are not (see ``simple_preconditioner``).

    It is a PRECONDITIONER, not a Schur complement, and does not try to be one:
    a stabilised saddle system needs a spectrally equivalent SPD pressure
    operator, and the pressure Laplacian is the standard choice for that in
    every coupled pressure-based code.
    """
    lookup = torch.full((self.solver.mask_interior_cell.numel(),), -1,
                        dtype=torch.long, device=self.device)
    lookup[self.interior] = torch.arange(self.n_cell, device=self.device)
    owner = lookup[self.solver.C_senders.long()]
    neighbour = lookup[self.solver.F_receivers.long()]
    internal = (owner >= 0) & (neighbour >= 0)
    # PART OF THE SQUARE CONTRACT, not an optional extra.  It does not fall
    # back to zeros when the attribute was absent, which is unreachable today
    # -- both COLLOCATED_UNKNOWNS-declaring discretisations set it in
    # forward() (picoext:1245, buoyantBoussinesqpicoext:1933) -- but a third
    # one written without it would have taken a preconditioner built on a
    # zero flux and reported nothing.  A missing member of the contract is an
    # error, not a default.
    flux = getattr(self.solver, "face_volumetric_flux", None)
    if flux is None:
        raise AttributeError(
            f"{type(self.solver).__name__} declares COLLOCATED_UNKNOWNS, so it "
            "offers itself as a square system, but exposes no "
            "face_volumetric_flux.  The pressure block's coefficient is "
            "face_pressure_coefficient(flux) and there is no meaningful "
            "default for it; set the attribute in forward() the way picoext "
            "and buoyantBoussinesqpicoext do")
    # DETACHED, because this matrix is a preconditioner and is never
    # differentiated.  The stashed flux is already detached (picoext:1594),
    # so on a laminar case this is a no-op; with a transported turbulence
    # model the momentum diagonal inside face_pressure_coefficient reads the
    # per-forward _nu_eff_face, which is a live function of (k, omega) on the
    # residual's tape -- found on the 6-field flat plate, where
    # the host-side AMG setup crashed on numpy() of a grad-carrying tensor.
    coefficient = (
        self.solver.face_pressure_coefficient(flux).reshape(-1)
        * self.solver.face_area.reshape(-1)
        * self.solver.nonOrthDeltaCoeffs.reshape(-1)
        * self.solver.grad_p_coeff.reshape(-1)
    ).detach()[internal]
    p, q = owner[internal], neighbour[internal]
    index = torch.cat([torch.stack([p, q]), torch.stack([q, p]),
                       torch.stack([p, p]), torch.stack([q, q])], dim=1)
    value = torch.cat([-coefficient, -coefficient, coefficient, coefficient])
    return torch.sparse_coo_tensor(
        index, value, size=(self.n_cell, self.n_cell)
    ).coalesce().to_sparse_csr()


def simple_preconditioner(self, blocks, *, sweeps: int = 2, cycles: int = 8,
                          omega: float = 2.0 / 3.0, pressure: str = "auto",
                          shape: str = "simple",
                          restore_alpha: float = RESTORE_ALPHA,
                          meanflow_alpha: float = MEANFLOW_ALPHA,
                          logger=None):
    """A block preconditioner: damped Jacobi on momentum, multigrid on pressure.

    ``M^-1 [f; g]`` sweeps the momentum block, solves a pressure operator, then
    corrects the velocity -- the SIMPLE shape, which is what measured best on
    the staggered system next door against the pressure convection-diffusion
    and least-squares commutators (2.6-3.2x slower, not revisited).

    **The SIMPLE Schur does NOT carry over unchanged, and that is measured.**
    There the (2,2) block was ZERO and ``S = -D diag(F)^-1 G`` was definite.
    Here the momentum-weighted flux makes the (2,2) block ``C`` the DOMINANT
    term, and ``S = C - D diag(F)^-1 G`` comes out indefinite: measured
    on the 16^3 cavity, 883 of 4096 eigenvalues of ``-S`` have
    negative real part and its V-cycle AMPLIFIES by 51x per cycle, so eight
    cycles of defect correction multiply the error by 10^13.  ``-C`` alone --
    the momentum-weighted pressure Laplacian, which is what the stabilisation
    put there -- is symmetric positive semidefinite on the same matrix
    (eigenvalues in [0, 0.191], row sums 1.2e-17, i.e. a pure Neumann
    Laplacian) and its V-cycle converges at 0.886.  So the pressure operator is
    ``-C`` whenever the momentum-weighted flux supplies one, and the SIMPLE
    Schur when it does not.

    Both the sparse-sparse product and the pyamg hierarchy over it happen ONCE
    per linearisation, next to each other and outside the Krylov loop, which is
    the one host step this design sanctions.

    **An energy block, where the case has one, is swept and not solved.**  It
    gets the same damped Jacobi the momentum block gets and no coupling to
    ``[u, p]``, i.e. the preconditioner is block lower triangular in the
    temperature and the real coupling (buoyancy up, convection down) is left
    to GMRES.  That is a deliberate floor, not a finished design: a scalar
    convection-diffusion block is exactly what a multigrid cycle handles well,
    and if the iteration count on a buoyant case turns out to be set by this
    block, that is where to look first.

    ``shape = "restored"`` REPLACES THE TWO ARMS ABOVE, and it exists because on
    a wall-stretched RANS Jacobian the shape above is worse than no
    preconditioner at all.  Both facts are measured on the captured stage-B
    flat-plate system (26,112 cells, 6 fields, cell aspect ratio to 2.4e4;
    docs/research/2026-08-16-line-implicit-inner-solve.md).  True
    ``|b - J x| / |b|`` at the production budget (restart 400 x 4 cycles = 1600
    matvecs), min of three interleaved reps, at two independently captured
    iterates of the same run:

        arm                                 mild state   harder state
        NO preconditioner [control]            0.1734       0.3020
        this shape, "simple" (as shipped)      0.9792       0.8354
        row / Ruiz equilibration          0.8819/0.7703       -/0.7655
        block scaling (``--block-scaling``)    0.1748       0.3386
        this shape, "restored"                 0.1159       0.1292

    The diagnosis behind it: at these iterates the k and log-omega rows are
    near-singular AND indefinite -- 1787 of 26,112 k diagonals are negative at
    the mild state and 3775 at the harder one, min|d| 3.4e-8, so
    ``max|1/diag R|`` reaches 2.9e7.  Any diagonal-derived inverse inherits
    THAT conditioning rather than the block's (the k block's own sigma spans
    0.74 down to <= 6.2e-4, four orders less), so the shipped shape's apply
    amplifies by 1.1e6-3.6e6 and fills the Arnoldi basis with directions where
    the residual is not: 93% of ``|b|`` is k + log-omega, and after 400
    iterations the shipped shape leaves the log-omega part at 1.00x its start
    while the unpreconditioned solve has cut it 6x.

    So the restored shape does exactly two things:

    * the post-pressure blocks are inverted through a SIGN-CONSISTENT restored
      diagonal ``+max(|d|, alpha max|d|_block)`` instead of ``1 / d``.  The
      healthy sign is ``+1`` and that is measured three ways rather than
      assumed -- among top-half-|d| rows k splits +13031:-26 and log-omega
      +13020:-37; among strictly diagonally dominant rows k splits +12457:-721
      and log-omega +237:-0; and the keep-the-sign control arm scores 0.718
      against 0.359 for flipping at ``alpha = 0.01``, i.e. repairing the sign
      is worth 2x on its own.  The floor ``alpha`` is ``RESTORE_ALPHA``.
    * the mean-flow block is preconditioned by the IDENTITY, which is a
      measurement and not an omission: restoration composed with this
      function's own SIMPLE arm scores 0.2360, worse than no preconditioner,
      and the by-construction control (SIMPLE on the mean flow, identity on
      the turbulence) scores 0.857 -- the momentum-diagonal inverse (max
      8.9e4) floods the basis with mean-flow directions and amplifies v by
      2.6x and w by 10x.  What is left is one block Gauss-Seidel pass in the
      mean-flow -> turbulence direction, which is the direction that carries
      the coupling: ``sum|J|`` over the off-diagonal blocks is 3.24e2 from
      mean columns into turbulence rows against 1.52e0 the other way, a
      factor 213, so the two Gauss-Seidel orders measure identical (0.1207
      vs 0.1208) and only this one is offered.

    The tail sweep count is ``sweeps``, the same knob the energy block uses;
    2 (the shipped default) is the best of the family, re-measured through this
    code path: 0.1159 against 0.1219 at one sweep and 0.1207 at four.  ``0``
    is not a diagonal solve, it returns a ZERO tail block and scores 0.998 --
    the pre-existing energy branch has the same property and neither guards it.

    ``shape = "meanflow"`` is ``"restored"`` with that identity replaced by a
    floored per-cell block solve on ``[u, v, w, p]``
    (``_meanflow_block_inverse``).  ``PRECONDITIONER_SHAPES`` above carries the
    selection table, the march it moved, and the cases it is NOT for; what
    follows is why the shape stops where it does, because that is an open
    problem and not a footnote.

    **Covering BOTH blocks is necessary and jointly sufficient, and covering
    either one alone is worth almost nothing.**  Measured with an
    exact sparse LU substituted for each half in turn, true ``|b - J x| / |b|``
    at the production budget, on the laminar capture and on the mode-2 one:

        inverted exactly         laminar    mode 2
        neither (``restored``)    0.1159    0.9981
        the mean-flow block       0.1024    0.9825
        the turbulence block      0.1378    0.8936
        BOTH                      1e-06     1e-06

    The ``1e-06`` also holds on a third capture and at a post-handover state
    taken off a real march -- four states of four.  So the gap between this
    shape's 0.8262 and 1e-06 is ENTIRELY the quality of the two approximations,
    and no amount of work on one block alone closes it.

    **The axis of that quality is bounded amplification, not accuracy.**  Five
    measurements say so, and the sharpest is that an EXACT inverse of the
    mean-flow block scores 0.9825 on the mode-2 capture while a FLOORED
    APPROXIMATE inverse of THE SAME BLOCK scores 0.6234: the exact inverse loses
    to a deliberately degraded one by 0.36.  The other four: ``1/diag(F)``
    amplifies 8.89e+04 and loses to no preconditioner at all; an unfloored
    per-cell 4x4 amplifies 2.39e+06; an unfiltered wall-normal line solve reads
    0.9937; and a per-cell 2x2 block on the TURBULENCE rows -- strictly more of
    the operator, through the same filter as the diagonal it replaces -- costs
    7.8x on the laminar capture and 58x on the early turbulent one.  Smoothed
    aggregation on the momentum components, the suggestion in the energy-block
    paragraph above, does not even converge: V-cycle factors 3.2 / 6.7 / 3.4 at
    pyamg's default and up to 1.3e+10 with strength filtering.

    **THE OPEN PROBLEM.**  The floor is therefore the whole design, and the
    floors these operators want are ANTI-CORRELATED -- the laminar capture wants
    ``alpha`` an order larger than the mode-2 one -- so every construction that
    buys the mode-2 capture more of the operator pays for it on the laminar one
    (block-Jacobi sweeps over the point blocks: 0.7704 mode 2 against 0.1222
    laminar; a wall-normal line solve: 0.7803 against 0.3649).  Making the floor
    ADAPTIVE was tried and failed OUT OF SAMPLE on both legs: a single global
    level ``sigma_max / budget``, which bounds the head's operator norm exactly
    and reads only build-time quantities, calibrated on two captures and scored
    on a held-out third, hands the mode-2 capture a 2.1x LARGER effective floor
    than the laminar one when the measured optima want the opposite.  **No
    build-time quantity is yet known that separates these operators in the right
    direction**, and the largest singular value of the head is measured NOT to
    be one.  Untried, listed so the next person does not re-derive the list: per
    block DIAGONAL DOMINANCE, the DISTRIBUTION of the per-block condition
    numbers, and the DIMENSION of the near-null subspace.  Five constructions
    have been measured against this target and none of them ships;
    docs/research/2026-08-18-mean-flow-preconditioner.md has all five, the
    oracle table, and the hold-out discipline used on the adaptive arm.

    ``shape = "covered"`` is the SIMPLE head above with ``restored``'s floored
    tail and its one Gauss-Seidel pass: the combination the other three shapes
    do not offer, because each of them names a whole ``M`` in which the head and
    the tail are decided together.  ``simple`` treats the tail with a RAW
    ``1 / diag``; ``restored`` floors the tail but pays for it with the identity
    on the head.  On a buoyant case both halves are load-bearing and neither
    trade is acceptable.

    **It exists because the DataCenter ladder stops on rung 2 and this is what
    moves it.**  Measured on that rung's captured system (498,337
    cells, 5 fields, nu x 512), true ``|b - J x| / |b|`` at the production
    1600-matvec budget, every arm in one process on one operator:

        arm                                      true residual   matvecs
        shape="simple" [shipped]                    0.98749        1600
        this shape's head, tail left to identity    6.366e-04      1600
        NO preconditioner [control]                 2.064e-03      1600
        shape="restored"                            0.81570        1600
        **this shape**                              **9.942e-09**  **604, CONVERGED**

    Two things in that table are worth more than the winner.  **The shipped
    shape is 478x WORSE THAN NO PRECONDITIONER on this operator**, and **its own
    tail is what does it**: deleting the tail entirely (row two) beats it by
    1552x.  The energy diagonal here is an amplifier -- 30,333 of 498,337
    entries are NEGATIVE, ``min|d|`` is 8.79e-08 against a median of 7.09e-03,
    and ``max|1/diag(R)|`` reaches **1.14e+07**, which the floor cuts to 27.0.
    That is the same pathology ``_restored_tail_diagonal`` was written for on
    the flat plate's turbulence rows, appearing here in a temperature row.

    **The coupling is worth 1.3x of matvecs and only in company**: with the
    floored tail it takes 800 matvecs to 604, and ON ITS OWN, over the raw
    diagonal, it makes things worse (0.99592 against 0.98749).  A Gauss-Seidel
    pass into a block whose inverse amplifies by 1e7 feeds it more to amplify.

    **It also helps the case it was not tuned on.**  On the heated cylinder's
    rung-2 system at its own physical viscosity it reads 5.756e-04 against the
    shipped shape's 8.962e-04, i.e. 1.56x, with the same defaults.

    **What was measured and NOT shipped**, so that it is not re-proposed:
    solving the energy block with aggregation multigrid instead of flooring it.
    On the heated cylinder that is a five-order win (9.941e-09 in 1489 matvecs
    against 8.962e-04 at the cap) because THAT block's diagonal is healthy -- 0
    negative entries, ``min|d|`` within a factor 3 of the median -- so its ill
    conditioning is the smooth end a V-cycle is built for.  On DataCenter the
    same construction reads **0.99592, worse than the shipped shape**, and the
    unscaled form did not even build: pyamg's prolongation smoother raised
    "array must not contain infs or NaNs".  The two operators want OPPOSITE
    treatments of the same block, the discriminator is the diagonal's own
    health, and a threshold sited on two cases is the failure this file already
    carries a warning about.  So the floor ships, which wins on both, and the
    cycle does not.

    **What this shape does NOT do is reach the forcing target on its own.**  At
    restart 400 it parks at 0.116 while Eisenstat-Walker asks for 0.1; at the
    same 1600-matvec budget with restart 1600 it CROSSES 0.1 at iteration 1257
    (floor 0.0892) on the mild state, and needs 4130 iterations at the harder
    one.  ``--restart`` is therefore the second half of this device, and
    neither half substitutes for repairing the linearisation itself.
    """
    if shape not in PRECONDITIONER_SHAPES:
        raise ValueError(f"unknown preconditioner shape {shape!r}; valid: "
                         + ", ".join(PRECONDITIONER_SHAPES))
    F, G, D, C = blocks["F"], blocks["G"], blocks["D"], blocks["C"]
    energy = blocks.get("R")
    tail_coupling = None
    meanflow_inverse = None
    energy_hierarchy = head_tail_coupling = None
    if shape == "covered":
        if energy is None:
            raise ValueError(
                "shape='covered' is the SIMPLE head plus the floored treatment "
                "of the blocks PAST the pressure, and this case has none -- its "
                f"unknowns are {list(self.field_names())}.  On a case with no "
                "transported block it would degenerate to shape='simple', which "
                "is a thing to ask for by name")
        energy_inverse = 1.0 / _restored_tail_diagonal(
            self, blocks["J"], restore_alpha)
        tail_coupling = _tail_rows_head_columns(blocks["J"], self.pressure)
    elif shape in ("restored", "meanflow"):
        if energy is None:
            raise ValueError(
                "shape='restored' restores the diagonal of the blocks PAST the "
                "pressure, and this case has none -- its unknowns are "
                f"{list(self.field_names())}.  On a case with no transported "
                "block the shape degenerates to the identity, i.e. to running "
                "GMRES unpreconditioned, which is a thing to ask for by name "
                "and not to arrive at by a flag that reads as a preconditioner")
        energy_inverse = 1.0 / _restored_tail_diagonal(
            self, blocks["J"], restore_alpha)
        tail_coupling = _tail_rows_head_columns(blocks["J"], self.pressure)
        if shape == "meanflow":
            meanflow_inverse = _meanflow_block_inverse(
                self, blocks["J"], meanflow_alpha)
    elif shape in ("buoyant", "buoyant-sgs"):
        if energy is None:
            raise ValueError(
                f"shape={shape!r} couples an energy block to the SIMPLE head, and "
                f"this case has none -- its unknowns are {list(self.field_names())}")
        energy_inverse = None
        values = energy.values()
        if not bool(torch.isfinite(values).all()):
            raise ValueError(
                f"shape={shape!r}: the energy block has "
                f"{int((~torch.isfinite(values)).sum())} non-finite entries of "
                f"{values.numel()}; no hierarchy can be built on it")
        diagonal = _csr_diagonal(energy)
        if logger is not None:
            logger.info("energy block diagonal: min %.3e max %.3e, %d non-positive",
                        float(diagonal.min()), float(diagonal.max()),
                        int((diagonal <= 0).sum()))
        try:
            energy_hierarchy = build_hierarchy(energy, project_mean=False,
                                               device=self.device)
        except (ValueError, FloatingPointError) as error:
            # Smoothed aggregation can fail on a strongly convective, non-
            # diagonally-dominant energy block (pyamg raised "array must not
            # contain infs or NaNs" on NaturalConvection Ra1e6).  Keep
            # the block Gauss-Seidel coupling, sweep the energy block instead.
            if logger is not None:
                logger.warning("energy AMG failed (%s); damped-Jacobi energy sweep "
                               "with the buoyancy coupling kept", error)
            energy_hierarchy = None
            energy_inverse = 1.0 / diagonal
        head_tail_coupling = _head_rows_tail_columns(blocks["J"], self.pressure)
        tail_coupling = (_tail_rows_head_columns(blocks["J"], self.pressure)
                         if shape == "buoyant-sgs" else None)
        if logger is not None:
            logger.info("energy block %d rows: AMG hierarchy %s",
                        energy.shape[0],
                        " -> ".join(str(size) for size in energy_hierarchy.sizes))
    else:
        energy_inverse = None if energy is None else 1.0 / _csr_diagonal(energy)
    n, velocity = self.n_cell, self.velocity
    started = time.perf_counter()
    hierarchy = pressure_operator = inverse = None
    if shape in ("simple", "covered", "buoyant", "buoyant-sgs"):
        inverse = 1.0 / _csr_diagonal(F)
        # Every spelling is the NEGATED operator, so the hierarchy sees a positive
        # one and the right-hand side below is negated to match.
        if pressure == "auto":
            # The operator should approximate the Schur ``C - D diag(F)^-1 G``, and
            # WHICH term dominates it depends on whether the correction is active.
            # With it on, ``C`` dominates and is itself a pressure Laplacian; with
            # it off ``C`` is zero and the Schur IS the SIMPLE product.  Measured
            # on the 32^3 cavity, GMRES iterations to the 1e-10 gate:
            #
            #     arm        laplacian   mwi_block   simple
            #     MWI on           969        3146    23052 and then STALLED
            #     MWI off         1978           -       880
            #
            # so neither one wins both, and picking by the arm is not a fudge --
            # it is the same criterion in both cases.
            #
            # **The branch was re-run at 48^3 to see whether it crosses over**,
            # because ``d_f ~ h^2 / 6 nu`` shrinks under refinement (0.260 at
            # 16^3, 0.065 at 32^3, 0.029 at 48^3) and a Schur that stops being
            # ``C``-dominated would want the other side.  It does not cross over.
            # Two interleaved rounds, MWI on, GMRES to the 1e-10 gate:
            #
            #     mesh    laplacian   mwi_block            simple
            #     32^3     969 (9.6s)  3146 (15.1s)  13452-18252, STALLED at 3.9e-5
            #     48^3    1494 (18.1s) 5144 (38.9s)  13826,       STALLED at 3.9e-5
            #
            # The ratio laplacian:mwi_block is 3.25 at 32^3 and 3.44 at 48^3, i.e.
            # flat, and ``simple`` fails at both.  What DOES grow with refinement
            # is the iteration count itself, 38.8 to 65.0 per Newton step for a
            # 1.5x refinement, and it grows the same way for both operators -- so
            # that is the preconditioner's mesh dependence, not this branch.
            # **EVERY ARM ABOVE WAS MEASURED ON AN ORTHOGONAL CUBE, AND THE RULE
            # DOES NOT SURVIVE A NON-ORTHOGONAL MESH.** ``laplacian`` is the
            # COMPACT pressure Laplacian, i.e. the operator the mesh would have
            # if its faces were orthogonal to the lines joining cell centres; on
            # the cylinder O-grid, where 31.9% of 428,880 internal faces exceed
            # that guard and the worst reaches 43.4 degrees, it stops being a
            # stand-in for the block it is preconditioning.  Measured on
            # 147,200 cells at 8x the case viscosity, everything else equal:
            #
            #     pressure      outcome
            #     laplacian     PARKS at max|r| 1.58e-07 and never moves.  A
            #                   FRESH Jacobian at every step for 40 steps takes
            #                   ||r||_2 from 5.4438e-05 to 5.4409e-05 while GMRES
            #                   exhausts 1600 iterations per step at a requested
            #                   1.0e-01, i.e. the inner solve fails, not Newton.
            #     mwi_block     2.7e-14 in 9 steps, and the case's own Reynolds
            #                   number then reaches 4.42e-13 in 10 more.
            #
            # Three cheaper explanations were measured and refuted first: the
            # constant-pressure projection (resolve_gauge; real bug, fixed, moved
            # the floor by 4e-13), a stale linearisation (--reassemble-drift 0,
            # above), and restarted-GMRES stagnation (--restart 1600, which
            # stalls at 1.29e-05 instead).  The finite-difference check on
            # ``linearise`` passes on this mesh and the residual is bit-repeatable,
            # so the Jacobian was never the suspect.
            #
            # The rule READS THE
            # MESH, conservatively.  ``laplacian`` is kept only where it is the
            # operator it stands in for -- an orthogonal mesh (max non-orthogonality
            # below ORTHOGONAL_DEGREES, i.e. round-off) -- and every other mesh gets
            # ``mwi_block``, the true pressure block, which certified on every mesh
            # family measured (cube, cylinder O-grid).  No threshold is sited between
            # two mesh families: the cut is "orthogonal or not".  Measured with entry
            # defaults: the cylinder O-grid (43.4 deg) parked at max|r| 3.0e-7 for 30
            # steps under the old rule; the orthogonal LDC / natural-convection cases
            # keep ``laplacian`` and are unchanged.
            if not self.mwi:
                pressure = "simple"
            else:
                angle = max_nonorthogonality_degrees(self)
                pressure = "laplacian" if angle < ORTHOGONAL_DEGREES else "mwi_block"
                if logger is not None:
                    logger.info("pressure='auto': max non-orthogonality %.3g deg -> %s",
                                angle, pressure)
        if pressure == "laplacian":
            pressure_operator = _csr_add(self.compact_pressure_laplacian(),
                                         self.compact_pressure_laplacian(), -2.0)
        elif pressure == "mwi_block":
            pressure_operator = _csr_add(C, C, -2.0)                       # -C
        elif pressure == "simple":
            pressure_operator = torch.sparse.mm(D, _csr_scale_rows(G, inverse))
        elif pressure == "schur":
            # opt-in (DC-RANS P7, 2026-11-06): the SIMPLE Schur complement of the MWI system, -(C - D diag(F)^-1 G):
            # the MWI pressure block plus the term through the momentum diagonal, which is the one that carries
            # continuity when the momentum diagonal is large (a pseudo-time shift) and which mwi_block leaves out
            pressure_operator = _csr_add(torch.sparse.mm(D, _csr_scale_rows(G, inverse)), C, -1.0)
        else:
            raise ValueError(
                f"unknown pressure operator {pressure!r}; valid: "
                + ", ".join(PRESSURE_OPERATORS))
        # Whether the constant pressure mode gets projected out of the cycle is
        # DECIDED FROM THIS MATRIX, not stated here: an all-Neumann cavity leaves
        # the level free and needs the projection, while a case with a Dirichlet
        # pressure patch anywhere -- an outlet, an opening -- pins its own level
        # and is destroyed by it.  Both run this same line.
        hierarchy = build_hierarchy(pressure_operator, project_mean="auto",
                                    device=self.device)
        if logger is not None:
            logger.info(
                "pressure operator %d x %d at %.1f nnz/row, hierarchy %s built in "
                "%.1f ms (%.1f ms setup + %.1f ms staging)", n, n,
                pressure_operator.values().numel() / max(n, 1),
                " -> ".join(str(size) for size in hierarchy.sizes),
                1e3 * (time.perf_counter() - started),
                1e3 * hierarchy.setup_seconds, 1e3 * hierarchy.stage_seconds)

    def simple_head(f, g):
        u = torch.zeros_like(f)
        for _ in range(sweeps):
            u = u + omega * inverse * (f - F @ u)
        p = torch.zeros_like(g)
        rhs = -(g - D @ u)      # the hierarchy holds the NEGATED operator
        for _ in range(cycles):
            p = p + hierarchy(rhs - hierarchy.matvec(p))
        return torch.cat((u - inverse * (G @ p), p))

    def energy_solve(h):
        t = torch.zeros_like(h)
        if energy_hierarchy is None:
            for _ in range(sweeps):
                t = t + omega * energy_inverse * (h - energy @ t)
            return t
        for _ in range(cycles):
            t = t + energy_hierarchy(h - energy_hierarchy.matvec(t))
        return t

    def apply(vector: torch.Tensor) -> torch.Tensor:
        if head_tail_coupling is not None:
            # Block Gauss-Seidel, energy first: the buoyancy column J[head, T]
            # moves the solved temperature into the momentum right-hand side;
            # "-sgs" re-solves the energy with the convective row J[T, head].
            h = vector[self.pressure:]
            t = energy_solve(h)
            rest = vector[:self.pressure] - head_tail_coupling @ t
            head = simple_head(rest[:velocity], rest[velocity:])
            if tail_coupling is not None:
                t = energy_solve(h - tail_coupling @ head)
            return torch.cat((head, t))
        if meanflow_inverse is not None:
            # shape="meanflow": the per-cell mean-flow block solve.  Reshaped
            # rather than gathered: the head is FIELD-BLOCKED, so component g of
            # cell c is entry g * n_cell + c, and a [width, n_cell] view
            # transposed is exactly the [n_cell, width] batch the blocks index.
            width = self.pressure // self.n_cell
            head = torch.einsum(
                "cij,cj->ci", meanflow_inverse,
                vector[:self.pressure].view(width, self.n_cell).t()
            ).t().reshape(-1)
        elif hierarchy is None:
            # shape="restored": the IDENTITY on [u, v, w, p].  See the docstring
            # -- this arm was measured against the SIMPLE one on the same
            # system and the SIMPLE one loses by 2x (0.236 against 0.116).
            head = vector[:self.pressure]
        else:
            f, g = vector[:velocity], vector[velocity:self.pressure]
            u = torch.zeros_like(f)
            for _ in range(sweeps):
                u = u + omega * inverse * (f - F @ u)
            p = torch.zeros_like(g)
            rhs = -(g - D @ u)      # the hierarchy holds the NEGATED operator
            for _ in range(cycles):
                p = p + hierarchy(rhs - hierarchy.matvec(p))
            head = torch.cat((u - inverse * (G @ p), p))
        if energy is None:
            return head
        h = vector[self.pressure:]
        if tail_coupling is not None:
            # ONE block Gauss-Seidel pass, mean flow -> turbulence, which is the
            # 213x heavier coupling direction (docstring).  ``head`` is the
            # already-preconditioned mean-flow part, so this is the standard
            # block-triangular apply and not a second sweep of anything.
            h = h - tail_coupling @ head
        t = torch.zeros_like(h)
        for _ in range(sweeps):
            t = t + omega * energy_inverse * (h - energy @ t)
        return torch.cat((head, t))

    apply.hierarchy = hierarchy
    apply.pressure_operator = pressure_operator
    return apply


def _restored_tail_diagonal(self, matrix: torch.Tensor,
                            alpha: float) -> torch.Tensor:
    """``+max(|d|, alpha max|d|)`` per block PAST the pressure, sign restored.

    Two separate repairs in one expression, and both are measured (see
    ``simple_preconditioner``'s ``shape`` section): the ABSOLUTE VALUE puts
    every row back on the healthy ``+1`` sign that the diagonally dominant rows
    of these blocks carry, and the FLOOR stops the near-singular tail (min|d|
    3.4e-8 against a block max of O(0.3)) from turning the inverse into an
    amplifier.  Per block rather than globally, because the blocks past the
    pressure are different equations on different scales; ``self.unknowns[2:]``
    is the same slice ``equilibration.turbulence_block_scale`` reads.
    """
    diagonal = _csr_diagonal(matrix)[self.pressure:]
    restored = torch.empty_like(diagonal)
    offset = 0
    for _, width, _, _ in self.unknowns[2:]:
        rows = width * self.n_cell
        block = diagonal[offset:offset + rows].abs()
        restored[offset:offset + rows] = torch.maximum(
            block, alpha * block.max())
        offset += rows
    return restored


def _meanflow_block_inverse(self, matrix: torch.Tensor,
                            alpha: float) -> torch.Tensor:
    """``[n_cell, w, w]`` floored inverses of the per-cell ``[u, v, w, p]`` blocks.

    The mean-flow rows of a collocated Jacobian meet their OWN cell in every
    mean-flow column, and those ``w x w`` numbers are a small saddle system in
    their own right -- momentum diagonals, the pressure-gradient coefficients,
    the divergence coefficients, and the momentum-interpolation term on the
    pressure diagonal.  Inverting them is a BLOCK inverse and not a diagonal
    one, which matters because the recorded rejection of every mean-flow arm on
    this system is specifically about a diagonal-derived inverse: ``1 /
    diag(F)`` reaches 8.89e+04 and floods the Arnoldi basis with directions the
    residual is not in (``simple_preconditioner``'s ``shape`` section).

    **The floor is not optional here, and that is measured rather than feared.**
    On the captured stage-B flat-plate system the raw blocks are WORSE
    conditioned than the diagonal they replace -- smallest singular value
    4.19e-07 against a median of 1.18e-05, so ``max |P^-1|`` is 2.39e+06, i.e.
    27x the amplification that sank the diagonal arm.  Filtering through the SVD
    at ``sigma >= alpha sigma_max`` per block caps it at ``1 / (alpha
    sigma_max)`` and leaves the healthy blocks' inverse EXACTLY unchanged, which
    is the same bargain ``_restored_tail_diagonal`` strikes one field group
    over.

    Built once per linearisation, like every other piece here, and the setup is
    ONE batched SVD -- measured 0.01-0.02 s over ``shape="restored"`` on the
    26,112-cell system, two orders below the 4.6-7.6 s the ``"simple"`` shape
    spends building its AMG hierarchy on the same matrix.  The apply is one
    batched ``w x w`` matvec, ``w^2`` multiply-adds per cell, which on the
    march of docs/research/2026-08-18-mean-flow-preconditioner.md sec. 7 comes
    out FASTER end to end than the shape it replaces (1087.1 s against 1129.9 s
    over 150 Newton steps at an identical Krylov budget).  Storage is ``w^2``
    doubles per cell: 3.3 MB at 26,112 cells, 19.7 MB at the finest hills rung.
    """
    n, width = self.n_cell, self.pressure // self.n_cell
    block = _point_blocks(matrix, n, width)
    u, s, vh = torch.linalg.svd(block)
    floored = torch.maximum(s, alpha * s[:, :1])
    return torch.einsum("cji,cj,ckj->cik", vh, 1.0 / floored, u)


def _point_blocks(matrix: torch.Tensor, n: int, width: int) -> torch.Tensor:
    """``[n, width, width]``: for each cell, the entries of ``matrix`` whose row
    and column are both that cell, over the first ``width`` field blocks.

    The state is FIELD-BLOCKED, so the block's ``(f, g)`` entry lives at row
    ``f * n + c`` and column ``g * n + c``.  Walked one field-row range at a
    time for the reason ``_csr_diagonal`` is chunked: the temporaries are
    bounded by one field's nonzeros rather than the matrix's.
    """
    crow, col, values = (matrix.crow_indices(), matrix.col_indices(),
                         matrix.values())
    out = torch.zeros(n, width, width, dtype=values.dtype, device=values.device)
    for f in range(width):
        lo, hi = int(crow[f * n]), int(crow[(f + 1) * n])
        if hi == lo:
            continue
        row = torch.repeat_interleave(
            torch.arange(n, device=values.device),
            crow[f * n + 1:(f + 1) * n + 1] - crow[f * n:(f + 1) * n])
        column = col[lo:hi]
        field = torch.div(column, n, rounding_mode="floor")
        hit = ((field < width) & (column - field * n == row)).nonzero(
            as_tuple=True)[0]
        out[row.index_select(0, hit), f, field.index_select(0, hit)] = \
            values[lo:hi].index_select(0, hit)
    return out


def _head_rows_tail_columns(matrix: torch.Tensor, split: int) -> torch.Tensor:
    """Rows ``[:split]`` of a CSR matrix restricted to columns ``[split:]``.

    The buoyancy column of a Boussinesq Jacobian (momentum rows, temperature
    columns), re-indexed so column ``split`` becomes 0.
    """
    crow = matrix.crow_indices()
    last = int(crow[split])
    head = crow[:split + 1]
    column = matrix.col_indices()[:last]
    value = matrix.values()[:last]
    row = torch.repeat_interleave(
        torch.arange(split, device=matrix.device), head[1:] - head[:-1])
    keep = column >= split
    counts = torch.zeros(split, dtype=crow.dtype, device=matrix.device)
    counts.scatter_add_(0, row[keep],
                        torch.ones_like(row[keep], dtype=crow.dtype))
    return torch.sparse_csr_tensor(
        torch.cat((torch.zeros(1, dtype=crow.dtype, device=matrix.device),
                   counts.cumsum(0))),
        column[keep] - split, value[keep],
        size=(split, matrix.shape[1] - split))


def _tail_rows_head_columns(matrix: torch.Tensor, split: int) -> torch.Tensor:
    """Rows ``[split:]`` of a CSR matrix restricted to columns ``[:split]``.

    Values-only: the rows are already a contiguous CSR range and the surviving
    columns keep their order inside each row, so the sub-block is built without
    a COO round trip and without a sort.
    """
    crow = matrix.crow_indices()
    first = int(crow[split])
    tail = crow[split:] - crow[split]
    column = matrix.col_indices()[first:]
    value = matrix.values()[first:]
    rows = tail.numel() - 1
    row = torch.repeat_interleave(
        torch.arange(rows, device=matrix.device), tail[1:] - tail[:-1])
    keep = column < split
    counts = torch.zeros(rows, dtype=crow.dtype, device=matrix.device)
    counts.scatter_add_(0, row[keep],
                        torch.ones_like(row[keep], dtype=crow.dtype))
    return torch.sparse_csr_tensor(
        torch.cat((torch.zeros(1, dtype=crow.dtype, device=matrix.device),
                   counts.cumsum(0))),
        column[keep], value[keep], size=(rows, split))


def _csr_add(left: torch.Tensor, right: torch.Tensor, weight: float) -> torch.Tensor:
    """``left + weight * right`` for two sparse matrices of equal shape, as CSR.

    Written through COO because the sum of two CSR patterns is a different
    pattern, and coalescing a concatenated COO is the one spelling that does not
    assume either operand's pattern contains the other's.
    """
    left, right = left.to_sparse_coo().coalesce(), right.to_sparse_coo().coalesce()
    return torch.sparse_coo_tensor(
        torch.cat((left.indices(), right.indices()), dim=1),
        torch.cat((left.values(), weight * right.values())),
        size=left.shape).coalesce().to_sparse_csr()


def _csr_diagonal(matrix: torch.Tensor, chunk: int = 1 << 20) -> torch.Tensor:
    """The main diagonal of a square CSR matrix, on its own device.

    Walked in row CHUNKS rather than in one pass.  The one-pass spelling built
    an nnz-length row index, an nnz-length boolean and an ``nonzero`` over it,
    which is fine at every mesh this solver had seen and is 11.12 GiB at the
    690,481,714 nonzeros of the fine periodic-hills rung -- asked for at the
    worst possible moment, immediately after an assembly, while the matrix it
    is reading is still resident.  That single allocation was the last thing
    standing between that rung and a 96 GB card (hills memory
    report).  Chunking bounds the temporaries at one chunk's nonzeros and
    changes no arithmetic: each row's diagonal is found the same way, and the
    chunks partition the rows.
    """
    rows = matrix.shape[0]
    crow = matrix.crow_indices()
    col = matrix.col_indices()
    values = matrix.values()
    out = torch.zeros(rows, device=matrix.device, dtype=matrix.dtype)
    for start in range(0, rows, chunk):
        stop = min(start + chunk, rows)
        lo, hi = int(crow[start]), int(crow[stop])
        if hi == lo:
            continue
        row = torch.repeat_interleave(
            torch.arange(start, stop, device=matrix.device),
            crow[start + 1:stop + 1] - crow[start:stop])
        hit = (row == col[lo:hi]).nonzero(as_tuple=True)[0]
        out[row.index_select(0, hit)] = values[lo:hi].index_select(0, hit)
    return out


def _csr_scale_rows(matrix: torch.Tensor, scale: torch.Tensor) -> torch.Tensor:
    """``diag(scale) @ matrix``, without forming the diagonal matrix."""
    crow = matrix.crow_indices()
    row = torch.repeat_interleave(
        torch.arange(matrix.shape[0], device=matrix.device), crow[1:] - crow[:-1])
    return torch.sparse_csr_tensor(
        crow, matrix.col_indices(), matrix.values() * scale.index_select(0, row),
        size=matrix.shape)
