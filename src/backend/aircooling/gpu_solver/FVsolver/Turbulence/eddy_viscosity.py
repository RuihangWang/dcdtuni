"""EddyViscosityModel -- what all Boussinesq-hypothesis RANS models share.

Design: docs/superpowers/specs/2026-08-16-turbulence-framework-design.md,
section 3.3.  Deferred at phase 3 ("until its second concrete consumer");
phase 3b's LaunderSharmaKE is that second consumer, so the machinery KOmega
proved is factored here as a PURE refactor -- KOmega's 15 tests and the
phase-3 laminar hash gates are the regression net proving it changed
nothing.

What lives here (exactly what the two concrete models genuinely share):

- the coeff-dict override machinery: constants and floor knobs come from the
  optional ``[turbulence]`` table of fvconfig the way OpenFOAM coeffDicts
  work, defaults from the subclass ``DEFAULTS``, unknown keys refused BY
  NAME (the contract.py house pattern);
- the per-forward nu_t stash discipline: ``nu_eff_face`` is the SINGLE
  computation site (subclass supplies the closure via
  ``_nut_cell_from_state``); ``nut_face`` then returns that same per-forward
  tensor, so the momentum diffusivity and the transported-equation
  diffusivities can never be built from two different nu_t evaluations
  (base.py ``nut_face`` docstring);
- ``is_uniform() = False`` -- every eddy-viscosity model activates the dev2
  assembly and the transported-field loop.

What stays in the subclasses: the closure algebra itself (nu_t, sources,
damping functions, floors) and the ``TRANSPORTED`` declaration.  Wall
distance (design 3.3) is still NOT declared here: neither concrete model
consumes it (KOmega by design, Launder-Sharma damps by ReT, not y+), so it
waits for SST/SA rather than landing speculatively.
"""

from abc import abstractmethod

import torch

from FVsolver.Turbulence.base import TurbulenceModel
from FVsolver.Turbulence.smooth import sqrt_floored

# Wall-distance fallback for cells of a case that carries NO wall patch
# (wall-free MMS boxes, periodic domains).  Any value large enough to park
# the F1/F2 arguments at their far-field limit would do; the number is never
# consumed where it could matter, because wall-less cases take the EXACT
# far-field branch (F1 = F2 = 0, see KOmegaSST) and their y rows only keep
# the blend chain finite inside a mixed batch.  Module constant, not a
# config knob: no measurement selects it.
_Y_NO_WALL = 1.0e10


def _wall_face_spans(solver):
    """Global face index ranges [(start, end), ...] of every wall patch.

    Wall patches are identified exactly the way this repo identifies them
    everywhere else: by the BC layer's geometry-dependent ``Wall`` classes
    (setOmega.Wall writes the low-Re omega ghost value; setTurbScalar's
    Wall pins k = 0 / epsilon-tilde = 0), instantiated from
    ``type = "wall"`` entries in bc/*.toml.  The batch offsets follow the
    same (group list, per-case offset) walk ``enforce_bc_1st`` does; a
    patch carrying both a k-Wall and an omega-Wall dedupes through the
    span set.
    """
    spans = set()
    batch = solver.bcpatch_batch
    for group in ("first", "mixed", "cyclic", "empty", "coupled"):
        case_lists = getattr(batch, group, None) or []
        offsets = getattr(batch, f"{group}_patch_idx_offset", None) or [0]
        for case_i, bc_list in enumerate(case_lists):
            for bc in bc_list:
                if type(bc).__name__ != "Wall":
                    continue
                gp = int(bc.patch_idx) + int(offsets[case_i])
                spans.add((int(solver.fvpatch_batch.start_idx_face[gp]),
                           int(solver.fvpatch_batch.end_idx_face[gp])))
    return sorted(spans)


def nearest_wall_distance_cpd(solver):
    """Per-cpd-cell nearest-wall distance ``([N_cpd, 1], has_wall_mask)``.

    The general-mesh ingredient SST's F1/F2 need (design 3.3 declares the
    access at this subfamily level; KOmega and Launder-Sharma never read
    it): for every INTERIOR cell, the minimum Euclidean distance to any
    wall-patch face centre of ITS OWN case -- per-case, so batched cases
    that overlap in physical space can never see each other's walls.  No
    Eikonal solve and no orthogonal-only shortcut: the face-centre min is
    exact on any mesh up to the in-plane face-centre spacing (an O(h^2)
    perturbation of the continuum distance at O(L) depth), which is the
    same discrete wall distance OpenFOAM's default ``wallDist`` meshWave
    seeds from.

    Ghost rows are owner-filled (the first-order patch closure every other
    derived cpd field uses); cells of a wall-less case read ``_Y_NO_WALL``
    and are flagged False in the returned per-cpd ``has_wall`` mask so the
    caller can take the exact far-field branch.  Static mesh geometry:
    built under ``no_grad``, cached by the caller per registration.
    """
    device = solver.cpd_cell_pos.device
    dtype = solver.cpd_cell_pos.dtype
    n_cpd = solver.cpd_cell_pos.shape[-2]
    with torch.no_grad():
        y_cpd = torch.full((n_cpd, 1), _Y_NO_WALL, dtype=dtype, device=device)
        has_wall = torch.zeros(n_cpd, dtype=torch.bool, device=device)
        spans = _wall_face_spans(solver)
        if spans:
            face_idx = torch.cat([torch.arange(s, e, device=device)
                                  for s, e in spans])
            wall_pos = solver.face_pos[face_idx]
            wall_batch = solver.batch_face[face_idx]
            interior_ids = solver.mask_interior_cell.nonzero(
                as_tuple=True)[0]
            cell_pos = solver.cpd_cell_pos[interior_ids]
            cell_batch = solver.batch_cell
            for g in range(int(solver.num_graphs)):
                w_g = wall_pos[wall_batch == g]
                if w_g.shape[0] == 0:
                    continue
                sel = (cell_batch == g).nonzero(as_tuple=True)[0]
                ids_g = interior_ids[sel]
                pos_g = cell_pos[sel]
                # cdist in chunks: cap the pairwise block near 2^22 entries
                # so a fine mesh against a large wall set stays in memory
                # (2^22, not 2^24: the direct kernel below materialises the
                # [chunk, n_wall, 3] difference, 3x the matmul form's block).
                chunk = max(1, (1 << 22) // max(1, w_g.shape[0]))
                for c0 in range(0, pos_g.shape[0], chunk):
                    block = pos_g[c0:c0 + chunk]
                    # donot_use_mm is REQUIRED, not a tuning knob.  cdist's
                    # default expands ||a-b||^2 = |a|^2 + |b|^2 - 2a.b via
                    # matmul whenever the block exceeds 25 rows; for a
                    # wall-resolved cell at y1 = 9.5e-7 with coordinates of
                    # O(1) that difference cancels to nothing.  Measured on
                    # TMR_FlatPlate/komega-137x97: the default
                    # returns y = 0 EXACTLY for 3657 of 26112 fp32 interior
                    # cells (14%) and >1% relative error for 6113 (23%),
                    # max |err| 3.2e-4; F1/F2 then still evaluate to their
                    # correct wall limit 1.0 but their GRADIENTS come back
                    # NaN (0 * inf), poisoning the Jacobian row and the
                    # adjoint.  The direct kernel is exact to roundoff at
                    # both dtypes (fp32 rel 1.6e-8, fp64 2.7e-11 abs) and
                    # is also FASTER here (1.6 ms vs 110 ms fp32; 0.1 ms vs
                    # 1928 ms fp64) at a third of the peak memory.
                    d = torch.cdist(
                        block, w_g,
                        compute_mode="donot_use_mm_for_euclid_dist",
                    ).min(dim=-1).values
                    y_cpd[ids_g[c0:c0 + chunk], 0] = d
                has_wall[ids_g] = True
        # Boundary closure: EVERY ghost row (cyclic included -- for y the
        # rotation-aware cyclic refill does not apply, and the owner copy
        # is the same first-order closure ghost_fill_owner uses) takes its
        # owner cell's value, so boundary-face interpolations of any
        # y-derived field see finite, physical numbers.
        bfaces = solver.mask_boundary_face.view(-1).nonzero(as_tuple=True)[0]
        owner = solver.neighbor_cpd_cell[0, bfaces]
        ghost = solver.neighbor_cpd_cell[1, bfaces]
        y_cpd[ghost] = y_cpd[owner]
        has_wall[ghost] = has_wall[owner]
    return y_cpd, has_wall[:, None]


def wall_cell_map(solver, var_name: str = "TURB_OMEGA"):
    """Wall faces and wall-adjacent cells of the constrained wall, or None.

    v2412's wall functions write TWO fields in the wall-adjacent cell, not
    one, and BOTH families do it in one loop:

      omega  ``omega[celli] = omega0[celli]`` and ``G[celli] = G0[celli]``
             (omegaWallFunctionFvPatchScalarField.C:527-533), row pinned at
             ibid.:606;
      eps    ``G[celli] = G0[celli]`` and ``epsilon[celli] = epsilon0[celli]``
             (epsilonWallFunctionFvPatchScalarField.C:537-543), row pinned by
             ``matrix.setValues(patch().faceCells(), patchInternalField())``
             at ibid.:616.

    The transported half is a BC-layer constraint
    (``BaseSolver._apply_cell_constraints``); the G half is a k-row SOURCE and
    therefore belongs to the turbulence model, which is why the same index
    sets are needed here.  ``var_name`` selects the family by the constrained
    variable's ``VARType`` NAME -- "TURB_OMEGA" for the low-Re omega wall,
    "TURB_EPSILON" for ``epsilonWallFunction`` -- because the wall-cell
    geometry is identical and only the constrained field differs.

    Patch selection is EXACTLY the constraint machinery's -- a BC declaring
    ``constrains_adjacent_cell`` on THAT variable -- rather than
    ``_wall_face_spans``' broader "any BC class named Wall".  The two sets
    coincide on every case in the tree, but they are not the same question: a
    k-only wall (``setK.Wall``) carries no wall function and must not get the
    production override, and (v2412 epsWF.C:101-113 counts the corner weights
    over ``epsilonWallFunction`` patches ONLY) a wall patch carrying a
    different epsilon BC contributes no face.
    ``test_wall_production_cells_are_the_pinned_cells`` asserts the two sets
    agree cell for cell so the override and the pinned row can never disagree
    about which cells are wall-adjacent.

    Returns ``(face_idx, ghost_cpd, cell_cpd, cell_row, corner_slot)``:
    ``face_idx``/``ghost_cpd`` are per WALL FACE and aligned (the ghost-rows-
    follow-face-order alignment every mirror BC relies on), ``cell_cpd`` /
    ``cell_row`` are the unique wall-adjacent cells in cpd and interior
    indexing, and ``corner_slot`` scatters faces onto them -- OpenFOAM's
    ``cornerWeights_ = 1/(wall faces on this cell)`` mean (ibid.:115-120,
    applied :244-342) is exactly a ``scatter-mean`` through this slot.
    """
    # A host with no BC layer at all is not a mesh with no wall -- it is a
    # closure-only unit fixture (the SimpleNamespace hosts the gradcheck
    # batteries build).  Those cases have no wall by construction, and asking
    # for one must not be an AttributeError.
    batch = getattr(solver, "bcpatch_batch", None)
    if batch is None:
        return None
    faces, ghosts = [], []
    for bcpatch_i, bcpatch in enumerate(batch.first):
        offset = batch.first_patch_idx_offset[bcpatch_i]
        for bc in bcpatch:
            if not getattr(bc, "constrains_adjacent_cell", False):
                continue
            # Name, not an enum import: this package deliberately reads the BC
            # layer by duck-typing (``_wall_face_spans`` does the same), so
            # Turbulence/ keeps importing nothing from FVdomain/.
            if getattr(getattr(bc, "varType", None), "name", "") != var_name:
                continue
            gp = int(bc.patch_idx) + int(offset)
            s_f = int(solver.fvpatch_batch.start_idx_face[gp])
            e_f = int(solver.fvpatch_batch.end_idx_face[gp])
            s_c = int(solver.fvpatch_batch.start_idx_cpd_cell[gp])
            e_c = int(solver.fvpatch_batch.end_idx_cpd_cell[gp])
            dev = solver.cell_volume.device
            faces.append(torch.arange(s_f, e_f, device=dev))
            ghosts.append(torch.arange(s_c, e_c, device=dev))
    if not faces:
        return None
    face_idx = torch.cat(faces)
    ghost_cpd = torch.cat(ghosts)
    owner_cpd = solver.neighbor_cpd_cell[0, face_idx]
    cell_cpd, corner_slot = torch.unique(owner_cpd, return_inverse=True)
    lookup = torch.full((int(solver.num_cpd_cells),), -1, dtype=torch.long,
                        device=cell_cpd.device)
    interior = solver.mask_interior_cell.reshape(-1).nonzero(as_tuple=True)[0]
    lookup[interior] = torch.arange(interior.numel(), device=cell_cpd.device)
    cell_row = lookup[cell_cpd]
    if int(cell_row.min()) < 0:
        raise ValueError(
            f"a {var_name} wall face owns a cell that is not interior; its "
            "production override would write a k row that does not exist")
    return face_idx, ghost_cpd, cell_cpd, cell_row, corner_slot


def omega_wall_cell_map(solver):
    """``wall_cell_map`` on the omega variable -- the phase-4 spelling, kept
    because the low-Re omega wall is its only caller and its gates name it."""
    return wall_cell_map(solver, "TURB_OMEGA")


class EddyViscosityModel(TurbulenceModel):
    """RANS subfamily base: coeff overrides + the nu_t stash discipline."""

    # Subclass constants + smooth-floor knobs, overridable through the
    # optional [turbulence] table of fvconfig (OpenFOAM coeffDict pattern).
    DEFAULTS: dict = {}

    # VARType NAME of the field whose wall BC pins its adjacent cell, i.e.
    # which wall-function family this model runs.  omega for the k-omega
    # lineage (kOmega, kOmegaSST); "TURB_EPSILON" for a high-Re kEpsilon.
    # Read by ``wall_cell_map`` only -- a model with no wall function leaves
    # it at the default and simply finds no constrained BC.
    WALL_CONSTRAINT_VAR: str = "TURB_OMEGA"

    def __init__(self, params, solver):
        super().__init__(params, solver)
        cfg = {}
        fvconfig = getattr(params, "fvconfig", None)
        if isinstance(fvconfig, dict):
            cfg = fvconfig.get("turbulence", {}) or {}
        unknown = set(cfg) - set(self.DEFAULTS)
        if unknown:
            raise ValueError(
                f"[{type(self).__name__}] unknown [turbulence] keys "
                f"{sorted(unknown)}; valid keys: {sorted(self.DEFAULTS)}")
        for name, default in self.DEFAULTS.items():
            value = cfg.get(name, default)
            # Coerce to the DEFAULT's own type, not unconditionally to float:
            # every coefficient here is numeric except a scheme SELECTOR
            # (KEpsilon's ``wallBlender``), and float("stepwise") is a
            # ValueError with nothing in it to say which key was wrong.
            setattr(self, name,
                    str(value) if isinstance(default, str) else float(value))
        # Per-forward nu_t stash: nu_eff_face is the SINGLE computation site
        # (contract calling order, base.py nut_face docstring).
        self._nut_cell = None
        self._nut_face = None
        # Wall-distance cache (design 3.3: access declared here, first
        # REQUIRED by SST).  Keyed on the bcpatch_batch OBJECT, which
        # register_geometrics rebinds whenever a new batch registers, so a
        # mixed-mesh reload invalidates the cache structurally.  The design
        # doc words the tensor as "built at parse/load time"; it is built
        # at first use after registration instead, because wall identity
        # lives in the BC layer (bc/*.toml "wall" types), which the parse
        # pipeline does not see -- recorded as a deliberate deviation in
        # docs/research/2026-08-16-turbulence-phase7-sst.md.
        self._wall_distance_cache = None
        # Same key discipline for the omega-wall cell map (pure connectivity).
        self._wall_cell_map_cache = None

    def wall_cell_map(self, solver):
        """Cached ``omega_wall_cell_map`` (connectivity: moves only on
        re-registration, so it is keyed on the bcpatch_batch object exactly
        like the wall-distance cache)."""
        key = getattr(solver, "bcpatch_batch", None)
        cache = self._wall_cell_map_cache
        if cache is None or cache[0] is not key:
            cache = (key, wall_cell_map(solver, self.WALL_CONSTRAINT_VAR))
            self._wall_cell_map_cache = cache
        return cache[1]

    def wall_function_production(self, solver, state, k_int):
        """v2412's turbulence production G in the wall-adjacent cell.

        The expression is character-for-character the same in the omega and
        epsilon wall functions, which is why one implementation serves both::

            G0[celli] += cornerWeights[facei]
                       * (nut_w + nu_w) * |snGrad(U)|_w
                       * Cmu^0.25 sqrt(k[celli]) / (kappa * y)

        (omegaWallFunctionFvPatchScalarField.C:335-340,
        epsilonWallFunctionFvPatchScalarField.C:340-345), and ``updateCoeffs``
        then OVERWRITES ``G[celli]`` with it (omegaWF.C:531, epsWF.C:541), so
        the k row of a wall-adjacent cell sees the log-layer production
        estimate instead of ``nu_t (grad U : dev(2 symm grad U))``.

        **Both guards are open at the shipped defaults, for UNRELATED
        reasons** -- reading one off the other is the trap this comment
        exists to block:

          omega    ``!(blender_ == STEPWISE) || yPlus > yPlusLam``
                   (omegaWF.C:333); the first clause is already true because
                   omega's per-BC default blender is BINOMIAL (omegaWF.C:405).
          epsilon  ``!lowReCorrection_ || (yPlus(facei) > yPlusLam)``
                   (epsWF.C:338); the first clause is already true because
                   ``lowReCorrection`` defaults to false (epsWF.C:414).  Its
                   blender default is the OPPOSITE of omega's -- STEPWISE
                   (epsWF.C:413) -- and plays no part in this guard.

        So there is no y+ switch on either path, and none is added here.

        Why it matters even on an integrate-to-wall grid, measured at the
        reference's own converged field on the TMR 137x97 rung
        (``probe_wall_cell_parity.py``): v2412's G0 is **5.6e5x**
        our ``nu_t GbyNu`` in the first cell, and it is **1.09x the local
        dissipation** ``betaStar omega k`` where ours is **1.9e-6** of it.  The
        two codes' first cells therefore sit on different k budgets entirely --
        OpenFOAM's on a local production-dissipation equilibrium, ours on
        diffusion against dissipation.

        Returns ``(cell_row, G0)`` with ``G0`` one value per wall-adjacent cell
        ``[.., N_wall_cell, 1]``, or ``None`` when the case has no low-Re omega
        wall.  Callers apply their model's own Pk limiter to it (SST caps with
        ``c1 betaStar k omega``, kOmega does not cap at all) and substitute it
        into the production by ``index_copy`` -- the same
        write-through-the-residual discipline the pinned omega row uses, so the
        Jacobian picks the term up by autograd and nothing is edited after
        assembly.

        Differentiability: ``|snGrad U|`` is a Euclidean norm, whose gradient is
        undefined at U = 0 -- which is EXACTLY the cold-start state and the wall
        ghost itself.  It is taken as ``sqrt_floored`` with ``s2SqrtEps`` (the
        knob the S2 square root already uses, and the same reasoning: a value
        perturbation far below fp64 in exchange for a finite slope at zero).
        ``sqrt(k)`` goes through the model's own floored ``sqrt_k``, and ``y``
        and ``nu`` are registration-time geometry.

        ``nut_w`` is read from OUR own per-forward face stash, which is what
        v2412 does too (``turbModel.nut(patchi)``, omegaWF.C:328-329 /
        epsWF.C:333-334, i.e. whatever nut wall function the case runs).  For
        an integrate-to-wall model that stash holds the linear interpolation
        where v2412 holds ``nutLowReWallFunction``'s exact zero; measured at
        the reference field it is 4.09e-13 of ``nu`` on the coarse TMR rung,
        i.e. the two agree to twelve digits inside the ``(nut_w + nu_w)`` sum.
        For a HIGH-RE model the two are not close at all, and the stash holds
        the right number only because ``nut_wall_face`` has already replaced
        it -- which is the reason that hook sits inside ``nu_eff_face``
        rather than being applied at each consumer.
        """
        rows = self.wall_cell_map(solver)
        if rows is None:
            return None
        face_idx, ghost_cpd, cell_cpd, cell_row, corner_slot = rows
        u_cpd = state["u_cpd"]
        d_u = ((u_cpd.index_select(-2, ghost_cpd)
                - u_cpd.index_select(-2, cell_cpd.index_select(0, corner_slot)))
               * solver.nonOrthDeltaCoeffs.index_select(-2, face_idx))
        mag_grad_uw = sqrt_floored((d_u * d_u).sum(dim=-1, keepdim=True),
                                   self.wallGradUEps)
        nu_w = solver.nu_coeff.index_select(-2, face_idx)
        nut_w = self.nut_face(solver, state).index_select(-2, face_idx)
        y_w = solver.wall_distance_face.index_select(-2, face_idx)
        sqrt_k_w = self.sqrt_k(k_int).index_select(-2, cell_row).index_select(
            -2, corner_slot)
        g0_face = ((nut_w + nu_w) * mag_grad_uw
                   * (self.wallCmu ** 0.25) * sqrt_k_w
                   / (self.wallKappa * y_w))
        # cornerWeights_ = 1/(wall faces on this cell): a scatter-MEAN, which is
        # the identity where a cell owns one wall face (every flat-plate rung)
        # and the average v2412 takes where it owns several.
        lead = g0_face.shape[:-2]
        n_cells = int(cell_cpd.numel())
        summed = torch.zeros(*lead, n_cells, 1, dtype=g0_face.dtype,
                             device=g0_face.device).index_add(
                                 -2, corner_slot, g0_face)
        count = torch.zeros(*lead, n_cells, 1, dtype=g0_face.dtype,
                            device=g0_face.device).index_add(
                                -2, corner_slot, torch.ones_like(g0_face))
        return cell_row, summed / count

    @abstractmethod
    def _nut_cell_from_state(self, solver, state) -> torch.Tensor:
        """Cell eddy viscosity ``[.., N_cpd, 1]`` from the BC-enforced state
        -- the model's closure algebra, evaluated once per forward by
        ``nu_eff_face`` below."""

    def nu_eff_face(self, solver, state) -> torch.Tensor:
        """nu + nu_t at faces; nu_t interpolated linearly.

        ``state`` is the BC-enforced fvfield; the transported-field ghost
        rows already carry their prescribed values, so the face
        interpolation needs no boundary special-casing here.  Stashes the
        per-forward nu_t pair for ``nut_face``/``transport_closures``
        (single-computation contract).
        """
        nut_cell = self._nut_cell_from_state(solver, state)
        nut_face = solver.interpolating_phic_to_faces(
            phi_cpd_cell=nut_cell, scheme="Linear")
        nut_face = self.nut_wall_face(solver, state, nut_face)
        self._nut_cell = nut_cell
        self._nut_face = nut_face
        return solver.nu_coeff + nut_face

    def nut_wall_face(self, solver, state, nut_face):
        """Model hook: replace nu_t on WALL faces.  Default: no replacement.

        The interpolation above is v2412's ``nutLowReWallFunction``, which is
        an exact ``Zero`` at the wall
        (nutLowReWallFunctionFvPatchScalarField.C:38-42) plus whatever the
        ghost carries -- correct for every integrate-to-wall model in this
        package.  A HIGH-RE model must put the log law there instead
        (nutkWallFunction, nutkWF.C:75-83 and the ``nutw -= nutVis`` at :167),
        and that value is what carries the wall shear stress into the momentum
        row through ``nu_eff = nu + nut``
        (linearViscousStress.C:105-117) -- without this hook a wall function
        would change the k/epsilon rows and leave the momentum wall stress
        laminar.

        Placed INSIDE ``nu_eff_face`` so the override reaches every consumer
        of the single per-forward stash at once (momentum diffusion,
        ``momentum_diagonal`` hence ``d_f`` and the MWI flux, the transported
        rows' diffusivities, and ``wall_function_production``'s ``nut_w``),
        which is exactly v2412's arrangement: ``correctNut`` calls
        ``nut.correctBoundaryConditions()`` (kEpsilon.C:46) once, and every
        later reader sees the wall value.

        Returning ``nut_face`` unchanged (the default) keeps Laminar, KOmega,
        KOmegaSST and LaunderSharmaKE structurally identical -- the SAME
        tensor object, so no numerical claim is involved.
        """
        return nut_face

    def nut_face(self, solver, state) -> torch.Tensor:
        if self._nut_face is None:
            raise RuntimeError(
                f"[{type(self).__name__}] nut_face read before nu_eff_face "
                "ran this forward; the host must call nu_eff_face first "
                "(single computation site, base.py contract)")
        return self._nut_face

    def wall_distance_cells(self, solver):
        """Cached ``(y_cpd [N_cpd, 1], has_wall [N_cpd, 1] bool)`` --
        ``nearest_wall_distance_cpd`` built once per registration (the
        bcpatch_batch identity key in ``__init__``)."""
        cache = self._wall_distance_cache
        if cache is None or cache[0] is not solver.bcpatch_batch:
            y_cpd, has_wall = nearest_wall_distance_cpd(solver)
            cache = (solver.bcpatch_batch, y_cpd, has_wall)
            self._wall_distance_cache = cache
        return cache[1], cache[2]

    def buoyancy_production(self, nut_int, state):
        """SGDH buoyancy production ``Gb = Cg nu_t beta (g . grad T)``
        [m^2/s^3], or ``None`` on an isothermal host (phase 5b, design A.2).

        Boussinesq kinematic transcription of OpenFOAM v2412's
        buoyantKEpsilon Gcoef (buoyantKEpsilon.C:101-127, ``-Cg nu_t
        (g . grad rho_k)`` with ``grad rho_k = -beta grad T``); ``Cg``
        replaces ``1/Prt`` (buoyantKEpsilon.H:45-49, default 1.0).  SIGN:
        production when ``g . grad T > 0`` (unstable stratification, heavy
        over light) -- the compressible/DTR/buoyantKEpsilon convention.
        v2412's buoyancyTurbSource INCOMPRESSIBLE branch carries the
        opposite sign (damps k when unstable); recorded in A.2 as one of
        the two v2412 code-vs-code discrepancies and deliberately NOT
        followed.

        The host signals buoyancy by supplying ``grad_t_int`` /
        ``gravity_int`` / ``beta_int`` in the closure state (the same route
        ``grad_u_int`` travels); an isothermal host supplies none, so the
        term is structurally absent there rather than numerically zero.
        """
        if "grad_t_int" not in state:
            return None
        g_dot_grad_t = (state["gravity_int"]
                        * state["grad_t_int"][..., 0, :]).sum(
                            dim=-1, keepdim=True)
        return self.Cg * nut_int * state["beta_int"] * g_dot_grad_t

    def is_uniform(self) -> bool:
        return False
