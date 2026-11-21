"""Assemble the ODIL Jacobian once per linearization instead of re-differentiating it.

**The point is fewer iterations to the SAME answer, not a more accurate answer.** That
distinction matters here and getting it backwards once already led to abandoning this
work. The inner solve shows semi-convergence: driving the least-squares residual down
makes the *nonlinear* residual worse (measured, monotonically, 1.24e-2 at 250 CGLS
iterations against 1.53e-2 at 4000), because truncated CGLS is itself the regulariser.
Semi-convergence dictates WHERE to stop; it says nothing against reaching that stopping
point faster. If the optimum is 250 unpreconditioned iterations, a preconditioner that
reaches the same subspace quality in 25 is a 10x speedup at identical final quality --
which is exactly why AMG makes a coupled CFD solve fast.

The structural gap against OpenFOAM is that OpenFOAM assembles a sparse matrix once per
timestep and then runs ~1 ms matvecs, while we ran a full AD pass through the whole
finite-volume discretisation for every Krylov iteration, measured at 48 ms for one
``J v`` plus one ``J^T u``. At 3750 Krylov iterations per physical step that difference
is the whole 180 s.

Assembly is possible because the Jacobian's pattern is fixed by the mesh. A cell
residual depends on its face neighbours' fluxes, which depend on their WLSQ gradients,
which depend on THEIR neighbours, so a row touches the 2-hop ball around its cell. That
premise is verified rather than assumed: perturbing one cell with a unit tangent makes
12-15 continuity rows respond, every one inside the 2-hop ball.

**Probing needs a distance-4 colouring, not distance-2.** Two columns may share a probe
only if no row touches both; since a row touches a 2-hop ball, columns conflict as soon
as ``dist <= 4``. Face rows widen this further -- a staggered face control volume
touches the 2-hop balls of BOTH adjacent cells -- so the conflict graph is built
exactly, as ``P^T P`` over the real row patterns, rather than from a guessed hop count.
Measured on the Cylinder mesh: 59 colours for cell rows alone, 92 once face rows are
included.

**Which row kinds enter that graph follows the caller's block list.** A row kind the
caller never stacks separates nothing that ever gets unpacked, so including it only
buys colours, and colours are the whole assembly cost. On the 48^3 cavity a
collocated caller with cell blocks only gets 60 colours instead of 92 (240 probes
instead of 368) and skips the 338688 x 92 face table, which alone was 8.43 s of an
11.1 s ``build_sparsity``. Callers that do stack face rows are on the
identical path they were.

**Both orientations are stored, and that is not redundancy.** PyTorch's CSR
transpose-matvec goes through atomics and collapses: measured at 35 nonzeros per row,
``A @ v`` takes 0.80 ms while ``A.t() @ u`` takes 39.95 ms. CGLS needs both products
every iteration, so ``J`` and ``J^T`` each get their own CSR.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence, Tuple

import contextlib
import glob
import hashlib
import json
import logging
import os
import time
import uuid

import numpy as np
import torch

# Fallback block layout: continuity, 3-component momentum and energy on cells,
# then the staggered face-CV momentum row. It is the 5-channel Boussinesq stack
# this module was written against and is kept only so a caller that does not
# supply ``block_specs`` gets it. ODIL callers pass the list resolved
# from ``solver.type`` (``ODIL.window.residual_block_specs``); this module takes
# it as plain tuples rather than importing ODIL, because SparseSolver is shared
# substrate and must not depend on a paradigm package.
_DEFAULT_BLOCK_SPECS: Tuple[Tuple[str, str, int], ...] = (
    ("cont", "cell", 1),
    ("mom", "cell", 3),
    ("energy", "cell", 1),
    ("face", "face", 1),
)


@dataclass(frozen=True)
class RowBlock:
    """One residual block, and which mesh entity each of its rows belongs to.

    ``entity_by_colour[e, c]`` is the unique interior cell of colour ``c`` inside the
    column set of an entity-``e`` row, or -1 when that colour is absent. Uniqueness is
    what the distance-4 colouring buys and is what lets one probe be unpacked into one
    matrix entry per row.
    """

    name: str
    num_entities: int
    components: int
    row_offset: int                  # into the stacked residual vector
    entity_by_colour: torch.Tensor   # [num_entities, num_colours] int32


@dataclass
class ColouredSparsity:
    num_cells: int
    num_colours: int
    colour_of_cell: torch.Tensor     # [N] int32
    cells_of_colour: list            # colour -> LongTensor of cell ids
    blocks: list                     # list[RowBlock], in stacked order
    window: int
    num_fields: int

    @property
    def num_probes(self) -> int:
        return self.window * self.num_colours * self.num_fields


def _interior_lookup(solver):
    mask = solver.mask_interior_cell
    interior = mask.nonzero(as_tuple=True)[0]
    lookup = torch.full((mask.numel(),), -1, dtype=torch.long, device=interior.device)
    lookup[interior] = torch.arange(int(interior.numel()), device=interior.device)
    return lookup, int(interior.numel())


def _cyclic_cell_pairs(solver, lookup):
    """Interior-cell pairs joined across every cyclic patch, as numpy index arrays.

    Returns empty arrays when the mesh has no cyclic patch, so callers need no branch.
    """
    from FVdomain.SetBC.onthefly.setCyclic import get_cyclic_patch_pair_indices

    fvpatch = getattr(solver, "fvpatch_batch", None)
    neighbour_cpd = getattr(solver, "neighbor_cpd_cell", None)
    if fvpatch is None or neighbour_cpd is None:
        return np.array([], dtype=np.int64), np.array([], dtype=np.int64)

    partner = getattr(fvpatch, "neighbor_patch_idx", None)
    if partner is None:
        partner = getattr(fvpatch, "neighbour_patch_idx", None)
    if partner is None:
        return np.array([], dtype=np.int64), np.array([], dtype=np.int64)

    left, right = [], []
    for patch_idx in range(int(partner.numel())):
        if int(partner.reshape(-1)[patch_idx]) < 0:
            continue                       # not a cyclic patch
        try:
            src_owner, _src_ghost, tgt_owner, _tgt_ghost = (
                get_cyclic_patch_pair_indices(fvpatch, neighbour_cpd, patch_idx)
            )
        except (ValueError, AttributeError, IndexError):
            continue
        a = lookup[src_owner].cpu().numpy()
        b = lookup[tgt_owner].cpu().numpy()
        keep = (a >= 0) & (b >= 0)
        left.append(a[keep])
        right.append(b[keep])

    if not left:
        return np.array([], dtype=np.int64), np.array([], dtype=np.int64)
    return np.concatenate(left), np.concatenate(right)


def _rack_cell_pairs(solver, lookup, global_reduction: str = "refuse"):
    """Interior-cell pairs joined across every rack black-box coupling.

    Same shape of object as ``_cyclic_cell_pairs`` and injected at the same
    point, but the pairing rule is different and cannot be shared: cyclic
    patches are coincident modulo a rigid transform and their faces correspond
    by index, whereas a rack pair is separated by the rack DEPTH and its
    correspondence is rebuilt geometrically by ``setRack``. So the pairs are
    taken from the BC objects that own that construction rather than
    re-derived here.

    A coupled condition that declares ``has_global_reduction`` is REFUSED rather
    than skipped. Its derivative is a dense rank-1 block spanning a whole patch,
    which is not a mesh edge at any hop count, so injecting nothing for it would
    reproduce the exact defect this function exists to close -- an unrepresented
    coupling whose entries land on other cells and whose absence raises nothing.
    (Bai's Eq. 15 CBBM outlet is the one such model; Eq. 17 is what ships.)

    Returns empty arrays when no rack BC is present, so callers need no branch.
    """
    batch = getattr(solver, "bcpatch_batch", None)
    if batch is None:
        # Every case without a coupled BC leaves on this line, so it pays
        # neither the import nor a walk over the patch groups.
        return np.array([], dtype=np.int64), np.array([], dtype=np.int64)

    omitted = []
    for group in getattr(batch, "coupled", []):
        for condition in group:
            if getattr(condition, "has_global_reduction", False):
                if global_reduction == "omit":
                    # The caller has told us this matrix is a PRECONDITIONER and
                    # not the Krylov operator.  A preconditioner is allowed to be
                    # approximate -- dropping a rank-1 block per rack changes the
                    # convergence rate and not the fixed point, because the true
                    # product comes from the matrix-free JVP, which carries the
                    # mean's derivative exactly.  Refusing here is what forced
                    # `lagMean = true`, and that froze the DataCenter rack
                    # discharge at its cold-start seed for a whole 6600 s Newton
                    # solve (intake mean 0.000 K
                    # against the -12.56 K the same solve had reached).
                    omitted.append(getattr(condition, "patch_name", "?"))
                    continue
                raise ValueError(
                    f"{type(condition).__name__} on patch "
                    f"'{getattr(condition, 'patch_name', '?')}' declares a global "
                    "reduction, whose Jacobian is a dense block over a whole patch. "
                    "A coloured mesh-stencil sparsity cannot represent it, and "
                    "assembling anyway writes its derivative onto unrelated cells. "
                    "Use the pointwise model on the assembled Newton path, "
                    "or run matrix-free so this matrix is only a preconditioner."
                )

    if omitted:
        # Loud, once per sparsity build, and it names the patches: a coupling
        # left out of a matrix silently is the exact defect the refusal above
        # exists to close, so omitting it deliberately has to say so.
        logging.getLogger(__name__).warning(
            "assembled matrix is a PRECONDITIONER ONLY: the global-reduction "
            "block on %s is omitted from it. The Krylov operator is the "
            "matrix-free product, which carries that derivative exactly.",
            ", ".join(omitted))

    from FVdomain.SetBC.onthefly.setRack import rack_cell_pairs

    return rack_cell_pairs(solver, lookup)


# ------------------------------------------------- on-disk colouring cache
#
# On-disk colouring cache (docs/research/2026-10-04-gpu-vs-openfoam-
# feasibility.md).  The colouring is a pure function of the mesh connectivity and of
# the row kinds the caller stacks; without a cache every process rebuilds it: the log
# timestamps put it at 16-23 s of the first assembly on the three paper cases (about
# 22 s on LDC96), a cost OpenFOAM pays once per case (decomposePar), not once per run.
#
# The cache stores ONLY the colour array -- the output of the expensive half, the
# conflict graph ``P^T P``, the RCM order and the first-fit sweep -- under a sha256 of
# everything that half reads.  A hit re-derives everything else (the 2-hop patterns,
# the per-entity tables through ``_invert``, ``cells_of_colour``) exactly as a fresh
# build does, so a loaded colouring is VERIFIED on this mesh by the same collision
# check before a probe uses it rather than trusted: an entry that would misfile an
# entry raises that check, and is then discarded, rebuilt and overwritten.  The key
# is computed after ``_rack_cell_pairs``, so the global-reduction refusal still runs
# on every build, cached or not.
COLOURING_CACHE_FORMAT = "lpcns-colouring-cache/1"
# Change this string whenever the colouring ALGORITHM changes (conflict graph,
# ordering, greedy rule): the key changes with it, so older entries are ignored
# instead of loaded.  The numpy/scipy versions are in the key for the same reason
# (RCM tie-breaking belongs to scipy).
_COLOURING_ALGORITHM = ("conflict = sum of P^T P over the probed row kinds; "
                        "order = scipy reverse_cuthill_mckee(1-hop mesh + I, "
                        "symmetric_mode=True); first-fit greedy, int32")

# The probe colourings ``build_sparsity(colouring=...)`` can make (roadmap
# docs/research/2026-10-07-gpu-optimisation-roadmap.md B1.1).  "rcm" is the colouring above and the default: its
# cache key is the one it always had, so every existing entry stays valid.  Every other name hashes its own text
# below, so its entries never collide with the default's (and a change of one of these algorithms must change its
# text, exactly as for ``_COLOURING_ALGORITHM``).
IG_PASSES = 300          # iterated-greedy passes at most ...
IG_PATIENCE = 60         # ... stopping after this many passes without a smaller colour count
IG_SEED = 20261008       # the seeded random class orders (numpy default_rng)
LATTICE_MAX_OFFSETS = 343     # a labelling with more distinct in-row label offsets than a 7^3 box is "not structured"
LATTICE_MAX_DIFFERENCES = 1000
LATTICE_SPAN = 24             # moduli searched: the longest row (the clique bound) .. + this
COLOURINGS = ("rcm", "smallest-last", "dsatur", "ig", "lattice")
_COLOURING_ALGORITHMS = {
    "rcm": _COLOURING_ALGORITHM,
    "smallest-last": ("conflict = sum of P^T P over the probed row kinds (no self loops); order = smallest-last "
                      "(Matula-Beck bucket queue: repeatedly remove a vertex of least remaining degree, ties by CPython set.pop "
                      "order); "
                      "first-fit greedy in reverse removal order, int32"),
    "dsatur": ("conflict = sum of P^T P over the probed row kinds (no self loops); DSATUR (Brelaz): next vertex = "
               "largest number of distinct neighbour colours, ties by larger conflict degree, then lower index; "
               "first-fit, int32"),
    "ig": (f"rcm (as {_COLOURING_ALGORITHM!r}), then iterated greedy (Culberson): each pass recolours the colour "
           f"classes as blocks, first fit, in the order reverse / largest class first / seeded random (numpy "
           f"default_rng({IG_SEED}), pass p uses the (p mod 3)-th), at most {IG_PASSES} passes, stop after "
           f"{IG_PATIENCE} without a smaller count; int32"),
    "lattice": (f"labels = per-axis line ranks of the interior cell centres (internal face axis = argmax |centre "
                f"difference|, scipy connected_components of each axis's faces, rank by that coordinate inside a "
                f"component); D = every label difference of two columns of one probed row; colour = (a i + b j + "
                f"c k) mod m for the smallest m from the longest row to + {LATTICE_SPAN} and the first (a, b, c) in "
                f"lexicographic order with a.d != 0 mod m for all d in D, compacted to 0..K-1 in residue order; "
                f"not structured (> {LATTICE_MAX_OFFSETS} offsets or > {LATTICE_MAX_DIFFERENCES} differences) or "
                f"no such m: the ig colouring; int32"),
}


def colouring_cache_key(*, num_cells: int, owner, neighbour, cyclic, rack,
                        block_specs, window: int, num_fields: int,
                        global_reduction: str, tag=None, colouring: str = "rcm",
                        labels=None) -> str:
    """sha256 over everything the colouring and its tables depend on.

    ``owner``/``neighbour`` are the face arrays AFTER the interior lookup (ghost
    sides are -1), so they carry the cell count, the internal connectivity and
    which faces are boundary faces; ``cyclic`` and ``rack`` are the extra edges
    ``build_sparsity`` injects.  ``block_specs`` decides which row kinds enter
    the conflict graph.  ``window`` and ``num_fields`` do not move the colouring
    and are hashed anyway, as is ``tag`` (the caller's own context, e.g. the
    momentum-interpolation coefficient mode): a key that is too specific costs
    one rebuild, one that is too loose loads a colouring for a different
    problem.  Every item is length-prefixed, so no two inputs share a byte stream.

    ``colouring`` names the algorithm (``COLOURINGS``).  "rcm",
    the default, hashes exactly the items it always hashed -- the key of every
    existing entry is unchanged -- and any other name hashes its own algorithm
    text instead, plus ``labels`` (the lattice colouring's integer cell labels,
    which carry the geometry it reads), so the entries of different algorithms
    never collide.
    """
    if colouring not in _COLOURING_ALGORITHMS:
        raise ValueError(f"unknown colouring {colouring!r}; valid: {', '.join(COLOURINGS)}")
    import scipy

    digest = hashlib.sha256()

    def text(label, value):
        blob = json.dumps(value, sort_keys=True, separators=(",", ":"),
                          default=str).encode()
        digest.update(f"{label}:{len(blob)}:".encode())
        digest.update(blob)

    def array(label, values):
        values = np.ascontiguousarray(np.asarray(values), dtype="<i8")
        digest.update(f"{label}:<i8:{values.size}:".encode())
        digest.update(values.tobytes())

    text("format", COLOURING_CACHE_FORMAT)
    text("algorithm", _COLOURING_ALGORITHMS[colouring])
    if colouring != "rcm":
        text("colouring", colouring)
        if labels is not None:
            array("labels", labels)
    text("versions", {"numpy": np.__version__, "scipy": scipy.__version__})
    text("num_cells", int(num_cells))
    array("owner", owner)
    array("neighbour", neighbour)
    array("cyclic_a", cyclic[0])
    array("cyclic_b", cyclic[1])
    array("rack_a", rack[0])
    array("rack_b", rack[1])
    text("block_specs", [[str(name), str(entity), int(components)]
                         for name, entity, components in block_specs])
    text("window", int(window))
    text("num_fields", int(num_fields))
    text("global_reduction", str(global_reduction))
    text("tag", {} if tag is None else tag)
    return digest.hexdigest()


def colouring_cache_path(directory, key: str) -> str:
    """Where the entry for ``key`` lives inside ``directory``."""
    return os.path.join(os.fspath(directory), f"colouring-{key}.npz")


def invalidate_colouring_cache(directory, key: str | None = None) -> list:
    """Explicit invalidation: delete one entry (``key``) or every entry in ``directory``.

    Returns the paths removed.  Stale entries are never READ -- a changed mesh,
    layout, role, coefficient mode or library version is a different key -- so
    this exists for a colouring-code change made without updating
    ``_COLOURING_ALGORITHM``, and for disk hygiene.  The entry's own
    ``--colouring-cache-refresh`` does the one-key form from the command line.
    """
    if key is not None:
        candidates = [colouring_cache_path(directory, key)]
    else:
        candidates = sorted(glob.glob(os.path.join(os.fspath(directory),
                                                   "colouring-*.npz")))
    removed = []
    for path in candidates:
        try:
            os.remove(path)
        except FileNotFoundError:
            continue
        removed.append(path)
    return removed


def _read_colouring(path: str, key: str, num_cells: int):
    """``((colour, num_colours, degree_mean, build_seconds), "hit")`` or ``(None, reason)``.

    Never raises for a bad file: a truncated, foreign or renamed entry is a miss
    with its reason logged, and the caller rebuilds.  What it checks is only what
    ``_invert`` cannot -- the file is ours, for this key, and its colours index
    the table in range; whether the colouring separates every row's columns is
    left to ``_invert``'s own collision check, the one a fresh build passes.
    """
    if not os.path.exists(path):
        return None, "no entry"
    try:
        with np.load(path, allow_pickle=False) as entry:
            layout = str(entry["layout"])
            stored = str(entry["key"])
            colour = np.array(entry["colour"])
            num_colours = int(entry["num_colours"])
            degree_mean = np.float64(entry["conflict_degree_mean"])
            build_seconds = float(entry["build_seconds"])
    except Exception as failure:      # noqa: BLE001 -- any failure to READ is a miss
        # Was a list of five types (OSError, EOFError, KeyError, ValueError,
        # BadZipFile), which misses cases: a well-formed archive with a
        # non-scalar ``num_colours`` raises TypeError from ``int()``, and a
        # foreign or damaged zip can raise struct.error, zlib.error or
        # NotImplementedError from zipfile -- each of which would have failed
        # the SOLVE over a cache file.  Nothing read here is trusted anyway: the
        # checks below and ``_invert``'s collision check decide.
        return None, f"unreadable ({type(failure).__name__}: {failure})"
    if layout != COLOURING_CACHE_FORMAT:
        return None, f"format {layout!r}, expected {COLOURING_CACHE_FORMAT!r}"
    if stored != key:
        return None, "the key stored inside does not match its file name"
    if colour.dtype != np.dtype(np.int32) or colour.shape != (num_cells,):
        return None, (f"colour array {colour.dtype}{list(colour.shape)}, "
                      f"expected int32[{num_cells}]")
    if num_cells and (int(colour.min()) < 0 or int(colour.max()) + 1 != num_colours):
        return None, (f"colours span [{int(colour.min())}, {int(colour.max())}] "
                      f"against num_colours = {num_colours}")
    return (colour, num_colours, degree_mean, build_seconds), "hit"


def _write_colouring(path: str, key: str, colour, num_colours: int,
                     degree_mean, build_seconds: float, summary: dict):
    """Write one entry atomically; ``None`` on success, else the ``OSError``.

    Written to a unique temporary name in the same directory and renamed into
    place, so a reader never sees half a file and four jobs building the same
    mesh at once each replace the entry with identical bytes.  A failed write
    (quota, read-only file system) is reported and never fails the solve.
    """
    temporary = f"{path}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(temporary, "wb") as handle:
            # A file handle, not a path: np.savez appends ".npz" to a path that
            # lacks it, and the temporary name deliberately does.
            np.savez(handle,
                     layout=np.array(COLOURING_CACHE_FORMAT),
                     key=np.array(key),
                     colour=np.ascontiguousarray(colour, dtype=np.int32),
                     num_colours=np.int64(num_colours),
                     conflict_degree_mean=np.float64(degree_mean),
                     build_seconds=np.float64(build_seconds),
                     summary=np.array(json.dumps(summary, sort_keys=True,
                                                 default=str)))
        os.replace(temporary, path)
    except OSError as failure:
        with contextlib.suppress(OSError):
            os.remove(temporary)
        return failure
    return None


def _first_fit_colouring(probed, adjacency, num_cells: int):
    """The exact conflict graph and its RCM-ordered first-fit colouring.

    Returns ``(colour, degree)``: the int32 colour of every cell and the conflict
    graph's row lengths, which only the log line reads.  Moved out of
    ``build_sparsity`` so that a
    colouring-cache hit skips exactly this half and nothing else.
    """
    from scipy.sparse.csgraph import reverse_cuthill_mckee

    # Exact conflict graph: columns conflict iff some row touches both. Summed over
    # the row kinds present, because a row kind the caller never stacks constrains
    # nothing that gets unpacked.
    conflict = probed[0].T @ probed[0]
    for pattern in probed[1:]:
        conflict = conflict + (pattern.T @ pattern)
    conflict = conflict.tocsr()
    conflict.data[:] = 1

    degree = np.diff(conflict.indptr)
    # First-fit greedy, visiting cells in a bandwidth-reducing order so the sweep
    # stays spatially coherent and reuses colours behind itself. Colours are the
    # whole assembly cost -- one colour is four probes -- and the ordering decides
    # how many. Measured on the 48^3 cavity's cell-row conflict graph, against a
    # floor of 25 (the widest row) and a greedy bound of 130:
    #     reverse Cuthill-McKee   45 colours, 0.37 s
    #     largest-degree-first    60 colours, 0.38 s   (what this was)
    #     random order            58-59 colours
    #     smallest-last           44 colours, 2.77 s
    #     DSATUR                  42 colours, 7.77 s
    # The last two buy 1 and 3 colours for 7x and 20x the setup, which at ~7 ms a
    # probe does not pay back over a run's handful of assemblies. RCM is taken on
    # the 1-hop MESH graph, not the conflict graph: it is far sparser, and cell
    # ordering is what the coherence is about.
    order = reverse_cuthill_mckee(adjacency.tocsr(), symmetric_mode=True)
    colour = np.full(num_cells, -1, dtype=np.int32)
    indptr, indices = conflict.indptr, conflict.indices
    for vertex in order:
        used = colour[indices[indptr[vertex]:indptr[vertex + 1]]]
        used = set(used[used >= 0].tolist())
        candidate = 0
        while candidate in used:
            candidate += 1
        colour[vertex] = candidate
    return colour, degree


# --------------------------------------------- better probe colourings
#
# Roadmap docs/research/2026-10-07-gpu-optimisation-roadmap.md B1.1.  Every probe is one second-order pass over
# the tape, so the colour count IS the assembly cost (probes = colours x fields).  The first-fit sweep above gives
# 46-47 colours on the DHC 64^3-152^3 meshes, whose conflict graph is the distance-4 (L1) graph of a hexahedral
# lattice: a row touches the 2-hop ball (25 cells), which is a clique, so 25 colours is a lower bound.  The
# alternatives below are opt-in (``build_sparsity(colouring=...)``); each is a proper colouring of the SAME exact
# conflict graph, and the result passes ``_invert``'s collision check exactly as the default's does, so a probe
# still unpacks into one entry per row -- every Jacobian entry is the same derivative, seeded with other cells.
#   smallest-last, dsatur   classical orderings on the explicit conflict graph (host, Python loops: minutes at
#                           128^3 -- comparison and fallback; the 48^3 note above measured 44 / 42 colours there)
#   ig                      rcm, then iterated greedy (Culberson): recolouring whole colour classes as blocks in a
#                           new class order never needs more colours, often fewer; vectorised per class on the
#                           solver's device
#   lattice                 for meshes whose cells carry a structured (i, j, k) labelling (any single-block
#                           hexahedral mesh, graded or not): a linear colouring (a i + b j + c k) mod m that
#                           separates every label difference occurring inside a row -- found by an exact search,
#                           correct by construction and checked by the collision check like every other.  On the
#                           distance-4 lattice graph (1, 5, 8) mod 27 is such a colouring: 27 colours against the
#                           clique bound 25.  A mesh without such a labelling falls back to ig.
# Measured (DHC 64^3-152^3): rcm 46-47, smallest-last 45-46, DSATUR 42-43 (450 s at 128^3), ig 44-45,
# lattice 27 ((1, 4, 10) mod 27 found first; no modulus 25 or 26 separates the 64 differences) in 0.7-10 s; the
# lattice Jacobian equals rcm's to the atomics' rounding floor (bitwise in deterministic mode, frozen d_f) and the
# assembly takes x0.58-0.60 of the time.


def _conflict_graph(probed):
    """``_first_fit_colouring``'s exact conflict graph without its diagonal: CSR, data 1, sorted column indices."""
    conflict = probed[0].T @ probed[0]
    for pattern in probed[1:]:
        conflict = conflict + (pattern.T @ pattern)
    conflict = conflict.tocsr()
    conflict.data[:] = 1
    conflict.setdiag(0)
    conflict.eliminate_zeros()
    conflict.sort_indices()
    return conflict


def _row_lengths(pattern):
    """Stored entries per row of a scipy CSR (numpy) or a ``sparsity_device.DeviceCSR`` (a device tensor)."""
    if torch.is_tensor(getattr(pattern, "indptr", None)):
        return pattern.indptr[1:] - pattern.indptr[:-1]
    return np.diff(pattern.indptr)


def _clique_bound(probed) -> int:
    """The longest probed row: all of its columns conflict pairwise, so no colouring has fewer colours."""
    return max((int(_row_lengths(pattern).max()) if pattern.shape[0] else 0) for pattern in probed)


def _first_fit_in_order(conflict, order, num_cells: int):
    """First-fit greedy over ``order`` on a conflict CSR (the sweep of ``_first_fit_colouring``)."""
    colour = np.full(num_cells, -1, dtype=np.int32)
    indptr, indices = conflict.indptr, conflict.indices
    for vertex in order:
        used = colour[indices[indptr[vertex]:indptr[vertex + 1]]]
        used = set(used[used >= 0].tolist())
        candidate = 0
        while candidate in used:
            candidate += 1
        colour[vertex] = candidate
    return colour


def _smallest_last_colouring(probed, num_cells: int):
    """Smallest-last order (Matula-Beck bucket queue), then first fit in reverse removal order: ``(colour, degree)``.

    Ties inside a degree bucket go by CPython's ``set.pop`` order, which for int keys depends only on the sequence of
    insertions and removals, i.e. it is the same on every run."""
    conflict = _conflict_graph(probed)
    indptr, indices = conflict.indptr, conflict.indices
    degree = np.diff(indptr)
    current = degree.tolist()
    removed = np.zeros(num_cells, dtype=bool)
    buckets = [set() for _ in range((int(degree.max()) if num_cells else 0) + 1)]
    for vertex, d in enumerate(current):
        buckets[d].add(vertex)
    removal = np.empty(num_cells, dtype=np.int64)
    low = 0
    for position in range(num_cells):
        while not buckets[low]:
            low += 1
        vertex = buckets[low].pop()
        removed[vertex] = True
        removal[position] = vertex
        neighbours = indices[indptr[vertex]:indptr[vertex + 1]]
        for u in neighbours[~removed[neighbours]].tolist():
            d = current[u]
            buckets[d].discard(u)
            buckets[d - 1].add(u)
            current[u] = d - 1
        low = max(low - 1, 0)
    return _first_fit_in_order(conflict, removal[::-1], num_cells), degree


def _dsatur_colouring(probed, num_cells: int):
    """DSATUR (Brelaz 1979) on the exact conflict graph: ``(colour, degree)``.

    The next vertex has the most distinct neighbour colours, ties by larger conflict degree, then lower index (a
    lazy heap); it takes the lowest colour no neighbour has.  Neighbour colours are bit sets in uint64 words."""
    import heapq

    conflict = _conflict_graph(probed)
    indptr, indices = conflict.indptr, conflict.indices
    degree = np.diff(indptr)
    words = ((int(degree.max()) if num_cells else 0) + 2 + 63) // 64
    seen = np.zeros((num_cells, words), dtype=np.uint64)
    saturation = np.zeros(num_cells, dtype=np.int64)
    colour = np.full(num_cells, -1, dtype=np.int32)
    heap = [(0, -d, vertex) for vertex, d in enumerate(degree.tolist())]
    heapq.heapify(heap)
    full = (1 << 64) - 1
    while heap:
        negative_saturation, _, vertex = heapq.heappop(heap)
        if colour[vertex] >= 0 or -negative_saturation != saturation[vertex]:
            continue                                         # coloured already, or a stale entry
        candidate = 0
        for word in range(words):
            free = ~int(seen[vertex, word]) & full
            if free:
                candidate = 64 * word + ((free & -free).bit_length() - 1)
                break
        colour[vertex] = candidate
        neighbours = indices[indptr[vertex]:indptr[vertex + 1]]
        word, bit = divmod(candidate, 64)
        mask = np.uint64(1 << bit)
        fresh = neighbours[(colour[neighbours] < 0) & ((seen[neighbours, word] & mask) == 0)]
        if fresh.size:
            seen[fresh, word] |= mask
            saturation[fresh] += 1
            for u, s, d in zip(fresh.tolist(), saturation[fresh].tolist(), degree[fresh].tolist()):
                heapq.heappush(heap, (-s, -d, u))
    return colour, degree


def _iterated_greedy(conflict, colour, *, device="cpu", passes: int = IG_PASSES, patience: int = IG_PATIENCE,
                     seed: int = IG_SEED, lower_bound: int = 0):
    """Culberson's iterated greedy from ``colour`` on a conflict CSR without diagonal: ``(colour, counts)``.

    One pass recolours the colour classes one whole class at a time, first fit against the classes already
    recoloured in this pass.  A class is an independent set, so its vertices can take their colours at once
    (vectorised: one gather of their neighbours' new colours), and the pass needs at most as many colours as it
    started with (the t-th class processed gets colours < t + 1).  Class orders cycle reverse / largest first /
    seeded random.  ``counts`` is the colour count before the first pass and after each pass."""
    indptr = torch.as_tensor(np.asarray(conflict.indptr, dtype=np.int64)).to(device)
    indices = torch.as_tensor(np.asarray(conflict.indices, dtype=np.int32)).to(device)
    current = torch.as_tensor(np.asarray(colour, dtype=np.int64)).to(device)
    rng = np.random.default_rng(seed)
    counts = [int(current.max()) + 1 if current.numel() else 0]
    stale = 0
    for step in range(passes):
        k = counts[-1]
        if k <= max(lower_bound, 1) or stale >= patience:
            break
        sizes = np.bincount(current.cpu().numpy(), minlength=k)
        if step % 3 == 0:
            order = np.arange(k)[::-1]
        elif step % 3 == 1:
            order = np.argsort(-sizes, kind="stable")
        else:
            order = rng.permutation(k)
        members = torch.argsort(current, stable=True)
        ends = np.cumsum(sizes)
        new = torch.full_like(current, -1)
        for c in order.tolist():
            vertices = members[int(ends[c] - sizes[c]):int(ends[c])]
            if vertices.numel() == 0:
                continue
            first, lengths = indptr[vertices], indptr[vertices + 1] - indptr[vertices]
            total = int(lengths.sum())
            if total == 0:
                new[vertices] = 0
                continue
            row = torch.repeat_interleave(torch.arange(vertices.numel(), device=device), lengths,
                                          output_size=total)
            position = (torch.arange(total, device=device)
                        + torch.repeat_interleave(first - (torch.cumsum(lengths, 0) - lengths), lengths,
                                                  output_size=total))
            taken = new.index_select(0, indices.index_select(0, position).long())
            keep = taken >= 0
            used = torch.zeros((vertices.numel(), k + 1), dtype=torch.bool, device=device)
            used[row[keep], taken[keep]] = True
            new[vertices] = (~used).to(torch.int32).argmax(dim=1).to(new.dtype)
        current = new
        counts.append(int(current.max()) + 1)
        stale = 0 if counts[-1] < k else stale + 1
    return current.cpu().numpy().astype(np.int32), counts


def _interior_positions(solver):
    """The interior cells' centres ``[N, 3]`` float64 on the host, or ``None`` when the solver has none."""
    positions = getattr(solver, "cpd_cell_pos", None)
    if positions is None:
        return None
    positions = positions.reshape(positions.shape[0], -1)[solver.mask_interior_cell]
    return positions.detach().to("cpu", torch.float64).numpy()


def lattice_labels(positions, owner, neighbour, num_cells: int):
    """Integer labels ``[N, 3]`` int64 from the internal faces and the cell centres, or ``None``.

    Each internal face ``(owner, neighbour)`` (interior cell indices) is given the axis along which its two
    centres differ most; for every axis, the connected components of that axis's faces are the grid lines, and
    a cell's label is its rank by that coordinate inside its line.  On a single-block hexahedral mesh these are
    the block indices (i, j, k).  On any other mesh they are SOME integers: the lattice colouring is correct for
    whatever labels it gets (it separates the label differences that actually occur), they only decide whether
    it finds few colours."""
    from scipy.sparse.csgraph import connected_components
    import scipy.sparse as sp

    if positions is None or num_cells == 0 or np.asarray(owner).size == 0:
        return None
    positions = np.asarray(positions, dtype=np.float64)
    if positions.shape != (num_cells, 3):
        return None
    owner, neighbour = np.asarray(owner, dtype=np.int64), np.asarray(neighbour, dtype=np.int64)
    axis = np.argmax(np.abs(positions[neighbour] - positions[owner]), axis=1)
    labels = np.zeros((num_cells, 3), dtype=np.int64)
    cells = np.arange(num_cells, dtype=np.int64)
    for a in range(3):
        on = axis == a
        graph = sp.coo_matrix((np.ones(int(on.sum()), dtype=np.int8), (owner[on], neighbour[on])),
                              shape=(num_cells, num_cells))
        _, line = connected_components(graph, directed=False)
        order = np.lexsort((cells, positions[:, a], line))
        starts = np.flatnonzero(np.r_[True, line[order][1:] != line[order][:-1]])
        lengths = np.diff(np.r_[starts, num_cells])
        labels[order, a] = np.arange(num_cells, dtype=np.int64) - np.repeat(starts, lengths)
    return labels


def _lattice_difference_set(probed, labels):
    """Every label difference ``labels[u] - labels[v]`` of two columns sharing a probed row, one of each +-d pair.

    The rows are reduced to their distinct patterns of label offsets (relative to the row's lowest column) first,
    so the cost is per pattern, not per row.  ``(differences, reason)``: ``None`` and why when the labelling is
    not structured (too many distinct offsets or differences)."""
    found = []
    for pattern in probed:
        rows = pattern.shape[0]
        lengths = np.diff(pattern.indptr)
        if rows == 0 or not lengths.any():
            continue
        nonempty = lengths > 0
        columns = pattern.indices.astype(np.int64)
        reference = np.zeros(rows, dtype=np.int64)
        reference[nonempty] = np.minimum.reduceat(columns, pattern.indptr[:-1][nonempty])
        row_of = np.repeat(np.arange(rows, dtype=np.int64), lengths)
        shift = labels[columns] - labels[reference[row_of]]
        low = shift.min(axis=0)
        span = shift.max(axis=0) - low + 1
        if float(np.prod(span.astype(np.float64))) > 2.0 ** 62:
            return None, "label offsets too spread to encode"
        flat = ((shift[:, 0] - low[0]) * span[1] + (shift[:, 1] - low[1])) * span[2] + (shift[:, 2] - low[2])
        del shift
        if int(np.prod(span)) <= 2 ** 24:              # a structured labelling: a dense lookup, no sort
            hit = np.zeros(int(np.prod(span)), dtype=bool)
            hit[flat] = True
            unique = np.flatnonzero(hit)
            if unique.size > LATTICE_MAX_OFFSETS:
                return None, f"{unique.size} distinct in-row label offsets (> {LATTICE_MAX_OFFSETS})"
            remap = np.full(hit.size, -1, dtype=np.int64)
            remap[unique] = np.arange(unique.size)
            code = remap[flat]
        else:
            unique, code = np.unique(flat, return_inverse=True)
            code = code.reshape(-1)
        del flat
        offsets = np.stack([unique // (span[1] * span[2]) + low[0], (unique // span[2]) % span[1] + low[1],
                            unique % span[2] + low[2]], axis=1)
        if offsets.shape[0] > LATTICE_MAX_OFFSETS:
            return None, f"{offsets.shape[0]} distinct in-row label offsets (> {LATTICE_MAX_OFFSETS})"
        member = np.zeros((rows, offsets.shape[0]), dtype=bool)
        member[row_of, code] = True
        for packed in np.unique(np.packbits(member[nonempty], axis=1), axis=0):
            present = offsets[np.unpackbits(packed)[:offsets.shape[0]].astype(bool)]
            found.append((present[:, None, :] - present[None, :, :]).reshape(-1, 3))
    if not found:
        return np.zeros((0, 3), dtype=np.int64), None
    differences = np.unique(np.concatenate(found), axis=0)
    first = np.where(differences[:, 0] != 0, differences[:, 0],
                     np.where(differences[:, 1] != 0, differences[:, 1], differences[:, 2]))
    differences = differences[first > 0]                       # one of each +-d pair, no zero
    if differences.shape[0] > LATTICE_MAX_DIFFERENCES:
        return None, f"{differences.shape[0]} label differences (> {LATTICE_MAX_DIFFERENCES})"
    return differences, None


def _lattice_search(differences, low: int, high: int):
    """The smallest ``m`` in ``[low, high]`` and first ``(a, b, c)`` with ``a.d != 0 (mod m)`` for every row of
    ``differences``: ``(m, (a, b, c))``, or ``None``."""
    if differences.shape[0] == 0:
        return 1, (0, 0, 0)
    for m in range(max(low, 1), high + 1):
        residues = np.arange(m, dtype=np.int64)
        d = differences % m
        for a in range(m):
            values = ((a * d[:, 0])[:, None, None] + d[:, 1, None, None] * residues[None, :, None]
                      + d[:, 2, None, None] * residues[None, None, :]) % m
            ok = (values != 0).all(axis=0)
            if ok.any():
                b, c = np.argwhere(ok)[0]
                return m, (a, int(b), int(c))
    return None


def _lattice_colouring(probed, labels):
    """``(colour, info)`` from ``(a i + b j + c k) mod m``, or ``(None, info)`` with the reason (see the block above)."""
    bound = _clique_bound(probed)
    info = {"lower_bound": bound}
    if labels is None:
        info["reason"] = "no cell centres (or no internal faces) to label the cells by"
        return None, info
    differences, reason = _lattice_difference_set(probed, labels)
    if differences is None:
        info["reason"] = reason
        return None, info
    found = _lattice_search(differences, bound, bound + LATTICE_SPAN)
    info.update(differences=int(differences.shape[0]), extent=[int(e) + 1 for e in labels.max(axis=0)])
    if found is None:
        info["reason"] = f"no modulus in [{bound}, {bound + LATTICE_SPAN}] separates the {differences.shape[0]} differences"
        return None, info
    m, coefficients = found
    residue = (labels @ np.asarray(coefficients, dtype=np.int64)) % m
    _, colour = np.unique(residue, return_inverse=True)
    info.update(modulus=m, coefficients=list(coefficients))
    return colour.reshape(-1).astype(np.int32), info


def _alternative_colouring(colouring: str, probed, adjacency, num_cells: int, *, labels=None, device="cpu"):
    """``build_sparsity``'s colouring for a non-default ``colouring``: ``(colour, degree_mean, info)``.

    ``degree_mean`` is the conflict graph's mean degree (``nan`` for a lattice colouring, which never forms it);
    ``info`` says what was done (stored in the cache entry's summary and logged)."""
    if colouring == "smallest-last":
        colour, degree = _smallest_last_colouring(probed, num_cells)
        return colour, float(degree.mean()) if num_cells else 0.0, {"colouring": colouring}
    if colouring == "dsatur":
        colour, degree = _dsatur_colouring(probed, num_cells)
        return colour, float(degree.mean()) if num_cells else 0.0, {"colouring": colouring}
    if colouring == "ig":
        start, _ = _first_fit_colouring(probed, adjacency, num_cells)
        conflict = _conflict_graph(probed)
        colour, counts = _iterated_greedy(conflict, start, device=device, lower_bound=_clique_bound(probed))
        return colour, float(np.diff(conflict.indptr).mean()) if num_cells else 0.0, {
            "colouring": colouring, "rcm_colours": counts[0], "passes": len(counts) - 1, "counts": counts}
    if colouring == "lattice":
        colour, info = _lattice_colouring(probed, labels)
        info["colouring"] = colouring
        if colour is not None:
            return colour, float("nan"), info
        colour, degree_mean, fallback = _alternative_colouring("ig", probed, adjacency, num_cells, device=device)
        info.update(fallback="ig", **{k: v for k, v in fallback.items() if k != "colouring"})
        return colour, degree_mean, info
    raise ValueError(f"unknown colouring {colouring!r}; valid: {', '.join(COLOURINGS)}")


def _invert(pattern, label, colour, num_colours: int, device):
    """entity -> colour -> the unique cell of that colour in the entity's columns.

    A module function, not a closure inside ``build_sparsity``, so
    that a colouring loaded from the cache passes the SAME collision check as a
    fresh one.

    The collision check counts the filled slots of the table instead of sorting
    the keys.  Sorting would be ``np.unique(flat).size !=
    flat.size``: a host sort of every (row, colour) key -- 16.7M keys on DHC88 g4
    -- that cost every Newton run about 13 s of its sparsity build there (3.6-3.8 s
    on DHC64), colouring-cache hit or not.  The keys lie in ``[0, n_rows *
    num_colours)`` (``colour`` is in range: a fresh colouring by construction, a
    cached one by ``_read_colouring``), every write stores a column index >= 0 into
    a table of -1, so the number of slots written equals the number of DISTINCT
    keys: the keys are all distinct exactly when ``flat.size`` slots are filled.
    Same verdict, same message, and the table is the same scatter
    (distinct keys: no write order to depend on), so a passing table is bit for
    bit the old one (tests/test_colouring_cache.py; the meshes:
    experiments/NaturalConvection/CoupledNewton/InitialGuess/diagnostics/invert_check.py).
    """
    n_rows = pattern.shape[0]
    table = np.full((n_rows, num_colours), -1, dtype=np.int32)
    row_ids = np.repeat(np.arange(n_rows, dtype=np.int64),
                        np.diff(pattern.indptr))
    col_ids = pattern.indices.astype(np.int64)
    flat = row_ids * num_colours + colour[col_ids]
    del row_ids
    slots = table.reshape(-1)              # a view: the scatter writes ``table``
    slots[flat] = col_ids.astype(np.int32)
    if int(np.count_nonzero(slots >= 0)) != flat.size:
        raise RuntimeError(
            f"Colouring collision in the {label} rows: two cells of one colour "
            "share a row's column set, so a probe cannot be unpacked "
            "unambiguously. The conflict graph or the stencil assumption is wrong."
        )
    return torch.as_tensor(table, device=device)


def _mean_row_length(pattern) -> float:
    """Mean stored entries per row of a scipy CSR or a ``sparsity_device.DeviceCSR`` (the sparsity log line)."""
    if torch.is_tensor(getattr(pattern, "indptr", None)):
        return float((pattern.indptr[1:] - pattern.indptr[:-1]).double().mean())
    return float(np.diff(pattern.indptr).mean())


def _invert_on_device(pattern, label, colour, num_colours: int, device):
    """``_invert`` computed on ``device`` (opt-in): the same table, bit for bit.

    Measured (DHC64, colouring-cache hit): ``_invert`` took 3.74 s of the 4.0 s sparsity
    build -- the host-side ``np.unique`` sort of the 6.5M (row, colour) keys of the 2-hop pattern, a 262144 x 46
    scatter and the table's copy -- inside the Newton timer of every run, cache or not.  Here the CSR pattern is
    copied to the device once and the same keys are formed there; the collision check is the same test (all keys
    distinct, by ``torch.unique``) with the same message, and the table is the same scatter of the column cell
    into ``row * num_colours + colour``: entries are distinct integers, so nothing depends on the order of
    writes and the result equals ``_invert``'s exactly (``torch.equal``; tests/test_colouring_cache_jacobian)."""
    n_rows = pattern.shape[0]
    if torch.is_tensor(getattr(pattern, "indptr", None)):     # a sparsity_device.DeviceCSR (PureGPU stage 3)
        indptr, col_ids = pattern.indptr.to(device).long(), pattern.indices.to(device).long()
    else:
        indptr = torch.as_tensor(np.asarray(pattern.indptr, dtype=np.int64)).to(device)
        col_ids = torch.as_tensor(np.asarray(pattern.indices)).to(device).long()
    colour_t = torch.as_tensor(np.asarray(colour)).to(device).long()
    row_ids = torch.repeat_interleave(torch.arange(n_rows, device=device), indptr[1:] - indptr[:-1],
                                      output_size=int(col_ids.numel()))
    flat = row_ids * num_colours + colour_t.index_select(0, col_ids)
    del row_ids
    if int(torch.unique(flat).numel()) != int(flat.numel()):
        raise RuntimeError(
            f"Colouring collision in the {label} rows: two cells of one colour "
            "share a row's column set, so a probe cannot be unpacked "
            "unambiguously. The conflict graph or the stencil assumption is wrong."
        )
    table = torch.full((n_rows * num_colours,), -1, dtype=torch.int32, device=device)
    table[flat] = col_ids.to(torch.int32)
    return table.view(n_rows, num_colours)


def build_sparsity(solver, *, window: int, num_fields: int = 5,
                   block_specs: Sequence[Tuple[str, str, int]] | None = None,
                   global_reduction: str = "refuse",
                   logger=None,
                   colouring_cache=None,
                   colouring_cache_refresh: bool = False,
                   cache_tag: dict | None = None,
                   invert_on_device: bool = False,
                   colouring: str = "rcm",
                   device_build: bool = False) -> ColouredSparsity:
    """Derive row patterns, the exact conflict graph, and a greedy colouring.

    ``device_build`` (opt-in, ``--device-sparsity``): with ``colouring="lattice"``
    and cell rows only, the labels, the 2-hop cell pattern, the label differences and the colouring are formed on
    the solver's device (``sparsity_device``; the same labels, pattern, differences and colours as the host path,
    so the same cache key and entries) and the tables by ``_invert_on_device``.  Where no lattice colouring exists
    the ig fallback runs on the host.  Any other colouring, or face rows: the host path.

    ``colouring`` (opt-in): the colouring
    algorithm, one of ``COLOURINGS`` (see the block above
    ``_conflict_graph``).  "rcm", the default, runs exactly the code it always
    ran, under the cache key it always had.  Every other name colours the same
    exact conflict graph another way (fewer colours = fewer probes), has its
    own cache entries, and passes the same collision check.  The tables a probe
    is unpacked through depend on the colouring; the matrix they assemble does
    not (every entry is the same derivative, seeded with other cells).

    ``invert_on_device`` (opt-in): the
    per-entity tables (``_invert``: collision check + entity -> colour -> cell
    table) are computed on the solver's device by ``_invert_on_device`` --
    the same tables, bit for bit, and the same collision verdict.  False (the
    default) runs exactly the host code it always ran.

    Everything here depends on mesh geometry and connectivity alone, so it is built
    once and reused for every linearization of the entire run.

    ``num_fields`` is the unknown vector's channel count; its default is the
    5-channel Boussinesq layout this module was written against, and ODIL always
    passes the value its state tensor carries.

    ``solver`` is the case's discretisation, not an integrator;
    the only members read off it were ``_solver`` and ``mask_interior_cell`` --
    the second of which ``Integrator.__getattr__`` delegates to the solver
    anyway.  Taking the solver directly is what let collocated_newton delete a
    fabricated three-attribute stand-in object whose whole purpose was to satisfy
    this signature.

    ``block_specs`` is the caller's stacked-residual block list as
    ``(name, entity, components)``; blocks whose entity is neither ``"cell"``
    nor ``"face"`` are dense rows with no mesh stencil and are skipped here (see
    ``AssembledJacobian.attach_row_sums``). Passing it is how a solver family
    without an energy equation gets a Jacobian whose row blocks line up with its
    residual instead of reserving empty energy rows.

    ``colouring_cache`` (opt-in) is a directory:
    the colour array is loaded from ``colouring-<sha256>.npz`` there instead of
    being rebuilt, and written there after a build (see the block above
    ``colouring_cache_key``).  ``colouring_cache_refresh`` skips the load and
    overwrites the entry.  ``cache_tag`` is the caller's own context, hashed into
    the key.  ``None`` (the default) builds exactly what was built before.
    """
    import scipy.sparse as sp

    if colouring not in COLOURINGS:
        raise ValueError(f"unknown colouring {colouring!r}; valid: {', '.join(COLOURINGS)}")
    lookup, num_cells = _interior_lookup(solver)
    owner = lookup[solver.cpd_owner].cpu().numpy()
    neighbour = lookup[solver.cpd_neighbor].cpu().numpy()
    num_faces = owner.size

    internal = (owner >= 0) & (neighbour >= 0)
    o, n = owner[internal], neighbour[internal]
    # M4a: the lattice colouring's cell labels, from the mesh's own internal faces (before the cyclic / rack pairs
    # are added) and the cell centres; computed before the cache key, which hashes them.
    dev_build = (bool(device_build) and colouring == "lattice"
                 and "face" not in {entity for _name, entity, _components in (block_specs or _DEFAULT_BLOCK_SPECS)})
    labels_t = None
    if dev_build:
        # Device build: the labels on the device; the host copy only feeds the cache key
        from SparseSolver import sparsity_device as SD
        dev = solver.mask_interior_cell.device
        cell_pos = getattr(solver, "cpd_cell_pos", None)
        pos_t = (None if cell_pos is None else
                 cell_pos.reshape(cell_pos.shape[0], -1)[solver.mask_interior_cell].to(dev, torch.float64))
        labels_t = SD.lattice_labels_device(pos_t, torch.as_tensor(o, device=dev), torch.as_tensor(n, device=dev),
                                            num_cells)
        labels = None if labels_t is None else labels_t.cpu().numpy()
    else:
        labels = (lattice_labels(_interior_positions(solver), o, n, num_cells)
                  if colouring == "lattice" else None)

    # Cyclic patches couple cells on OPPOSITE sides of the domain. That edge exists in
    # no number of mesh-adjacency hops, so omitting it drops real Jacobian entries --
    # measured, leaving it out gave a 2.6e-2 relative error against the matrix-free
    # product, stable across trials, rather than the ~1e-7 of float32 roundoff. The
    # Cylinder mesh is 2-D extruded with cyclic patches in z, so this is not an edge
    # case here, it is the default.
    cyc_o, cyc_n = _cyclic_cell_pairs(solver, lookup)
    if cyc_o.size:
        o = np.concatenate([o, cyc_o])
        n = np.concatenate([n, cyc_n])

    # A rack black-box BC (Bai et al. 2024 Eq. 17) reads the paired intake owner
    # across an EXCLUDED rack block, so the two cells are 7-21 mesh hops apart --
    # measured on a DC-shaped lattice. Without this edge the failure
    # is worse than the cyclic one: 57 of 64 required entries do not vanish, they
    # resolve to a DIFFERENT cell of the same colour, i.e. the rack derivative is
    # written into the wrong column and nothing raises. With the edge, 64/64 land
    # on the correct cell.
    rack_o, rack_n = _rack_cell_pairs(solver, lookup, global_reduction)
    if rack_o.size:
        o = np.concatenate([o, rack_o])
        n = np.concatenate([n, rack_n])

    def host_adjacency():
        a = sp.coo_matrix(
            (np.ones(2 * o.size, dtype=np.int8),
             (np.concatenate([o, n]), np.concatenate([n, o]))),
            shape=(num_cells, num_cells),
        ).tocsr()
        a.data[:] = 1
        a = a + sp.eye(num_cells, format="csr", dtype=np.int8)
        a.data[:] = 1
        return a

    if dev_build:
        # PureGPU stage 3: the same 2-hop pattern (as a set) on the device; the host adjacency only if the
        # lattice colouring fails and the ig fallback needs it
        adjacency = None
        cell_pattern = SD.two_hop_pattern(torch.as_tensor(o, device=dev), torch.as_tensor(n, device=dev), num_cells)
    else:
        adjacency = host_adjacency()
        cell_pattern = adjacency @ adjacency          # rows: cells -> 2-hop ball
        cell_pattern.data[:] = 1

    # Only the row kinds the caller actually stacks are built. A face row sees both
    # adjacent cells' 2-hop balls, so carrying it widens the conflict graph and costs
    # colours -- measured on the 48^3 cavity, 92 colours with face rows against 60
    # without, i.e. 368 probes against 240 for a caller that has no face block.
    # Building it unconditionally also cost the 338688 x 92 face table, measured at
    # 8.43 s of an 11.1 s build_sparsity.
    # Entities outside these two are dense rows with no mesh stencil; they are
    # skipped below and must not reach the pattern lookup.
    wanted = {entity for _name, entity, _components
              in (block_specs or _DEFAULT_BLOCK_SPECS)} & {"cell", "face"}
    patterns = {"cell": cell_pattern}
    if "face" in wanted:
        # Boundary faces have only an owner; the ghost side carries no unknown.
        rows, cols = [], []
        for side in (owner, neighbour):
            valid = side >= 0
            rows.append(np.nonzero(valid)[0])
            cols.append(side[valid])
        incidence = sp.coo_matrix(
            (np.ones(sum(r.size for r in rows), dtype=np.int8),
             (np.concatenate(rows), np.concatenate(cols))),
            shape=(num_faces, num_cells),
        ).tocsr()
        face_pattern = incidence @ cell_pattern
        face_pattern.data[:] = 1
        patterns["face"] = face_pattern

    # The row kinds whose patterns enter the exact conflict graph
    # (``_first_fit_colouring``, which carries the reasoning).
    probed = [patterns[entity] for entity in ("cell", "face") if entity in wanted]
    if not probed:
        raise ValueError(
            "block_specs names no 'cell' or 'face' row block, so there is no mesh "
            "stencil to probe and nothing for a colouring to separate")

    device = solver.mask_interior_cell.device
    entity_rows = {"cell": num_cells, "face": num_faces}

    invert = _invert_on_device if (invert_on_device or dev_build) else _invert

    def tables(colour, num_colours):
        return {
            entity: (entity_rows[entity],
                     invert(patterns[entity], entity, colour, num_colours, device))
            for entity in ("cell", "face") if entity in wanted
        }

    colouring_info = {}

    def alternative():
        # A non-default colouring; ``colouring_info`` says what was done.
        if dev_build:
            colour, info = SD.lattice_colouring_device(probed, labels_t, span=LATTICE_SPAN,
                                                       max_offsets=LATTICE_MAX_OFFSETS,
                                                       max_differences=LATTICE_MAX_DIFFERENCES, search=_lattice_search)
            info["colouring"] = colouring
            info["device"] = True
            if colour is not None:
                degree_mean = float("nan")
            else:                       # no lattice colouring: the host ig fallback on host copies of the pattern
                host_cell = sp.csr_matrix((np.ones(int(cell_pattern.indices.numel()), dtype=np.int8),
                                           cell_pattern.indices.cpu().numpy(), cell_pattern.indptr.cpu().numpy()),
                                          shape=cell_pattern.shape)
                colour, degree_mean, fallback = _alternative_colouring("ig", [host_cell], host_adjacency(),
                                                                       num_cells, device=device)
                info.update(fallback="ig", **{k: v for k, v in fallback.items() if k != "colouring"})
        else:
            colour, degree_mean, info = _alternative_colouring(colouring, probed, adjacency, num_cells,
                                                               labels=labels, device=device)
        colouring_info.clear()
        colouring_info.update(info)
        return colour, degree_mean

    if colouring_cache is None:
        # The default: the same conflict graph, sweep and inversion, in the same
        # order as the uncached build.
        if colouring == "rcm":
            colour, degree = _first_fit_colouring(probed, adjacency, num_cells)
            degree_mean = degree.mean()
        else:
            colour, degree_mean = alternative()
        num_colours = int(colour.max()) + 1
        entity_tables = tables(colour, num_colours)
    else:
        resolved_specs = tuple(block_specs or _DEFAULT_BLOCK_SPECS)
        key = colouring_cache_key(
            num_cells=num_cells, owner=owner, neighbour=neighbour,
            cyclic=(cyc_o, cyc_n), rack=(rack_o, rack_n),
            block_specs=resolved_specs, window=window, num_fields=num_fields,
            global_reduction=global_reduction, tag=cache_tag,
            **({} if colouring == "rcm" else {"colouring": colouring, "labels": labels}))
        path = colouring_cache_path(colouring_cache, key)
        log = logger if logger is not None else logging.getLogger(__name__)
        loaded, reason = ((None, "refresh requested") if colouring_cache_refresh
                          else _read_colouring(path, key, num_cells))
        if loaded is not None:
            colour, num_colours, degree_mean, build_seconds = loaded
            try:
                entity_tables = tables(colour, num_colours)
            except RuntimeError as failure:
                # Only the collision check's own verdict (``_invert``'s message)
                # condemns the entry.  Once any
                # RuntimeError did, and a CUDA out-of-memory while the tables are
                # copied to the device IS a RuntimeError: it was logged as a
                # colliding entry, the good entry was discarded, and the
                # colouring was rebuilt (the very cost the cache exists to skip)
                # only to fail at the same copy.  Anything else propagates.
                if not str(failure).startswith("Colouring collision"):
                    raise
                loaded, reason = None, f"fails the collision check on this mesh ({failure})"
            else:
                log.info("colouring cache HIT %s: %d colours loaded and passed the "
                         "collision check (the build it replaces took %.1f s)",
                         path, num_colours, build_seconds)
        if loaded is None:
            if reason not in ("no entry", "refresh requested"):
                log.warning("colouring cache entry %s discarded: %s; rebuilding",
                            path, reason)
            started = time.perf_counter()
            if colouring == "rcm":
                colour, degree = _first_fit_colouring(probed, adjacency, num_cells)
                degree_mean = degree.mean()
            else:
                colour, degree_mean = alternative()
            build_seconds = time.perf_counter() - started
            num_colours = int(colour.max()) + 1
            # A fresh colouring that collides raises here exactly as it always
            # did, before anything is written.
            entity_tables = tables(colour, num_colours)
            summary = {
                "num_cells": num_cells, "num_faces": num_faces,
                "cyclic_pairs": int(cyc_o.size), "rack_pairs": int(rack_o.size),
                "block_specs": resolved_specs, "window": window,
                "num_fields": num_fields, "global_reduction": global_reduction,
                "tag": cache_tag, "num_colours": num_colours,
                "written": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
            if colouring != "rcm":
                summary["colouring"] = dict(colouring_info)
            failure = _write_colouring(path, key, colour, num_colours, degree_mean,
                                       build_seconds, summary)
            if failure is None:
                log.info("colouring cache MISS (%s): built %d colours in %.1f s, "
                         "wrote %s", reason, num_colours, build_seconds, path)
            else:
                log.warning("colouring cache MISS (%s): built %d colours in %.1f s, "
                            "could not write %s (%s)", reason, num_colours,
                            build_seconds, path, failure)

    # Stacked order must match stacked_residual, which is why the block list
    # comes from the caller rather than being repeated here.
    blocks, offset = [], 0
    for name, entity, components in (block_specs or _DEFAULT_BLOCK_SPECS):
        if entity not in entity_tables:
            continue                       # dense row: no mesh stencil to probe
        entities, table = entity_tables[entity]
        blocks.append(RowBlock(name, entities, components, offset, table))
        offset += window * entities * components

    if logger is not None:
        logger.info(
            "Jacobian sparsity: %d cells, probed row kinds %s touching %s cells "
            "per row; exact conflict degree %.0f -> %d colours; %d probes per "
            "assembly",
            num_cells, "+".join(sorted(wanted)),
            ", ".join(f"{_mean_row_length(patterns[e]):.1f}"
                      for e in ("cell", "face") if e in wanted),
            degree_mean, num_colours, window * num_colours * num_fields,
        )
        if colouring != "rcm":
            # M4a: which colouring made these colours (a cache hit re-reads the entry, so no build details).
            logger.info("Jacobian colouring %s: %d colours (lower bound %d = the longest probed row)%s",
                        colouring, num_colours, _clique_bound(probed),
                        f"; {json.dumps(colouring_info, sort_keys=True, default=str)}" if colouring_info
                        else " (cache hit)")

    cells_of_colour = [
        torch.as_tensor(np.nonzero(colour == c)[0], device=device, dtype=torch.long)
        for c in range(num_colours)
    ]
    return ColouredSparsity(
        num_cells=num_cells, num_colours=num_colours,
        colour_of_cell=torch.as_tensor(colour, device=device),
        cells_of_colour=cells_of_colour, blocks=blocks,
        window=window, num_fields=num_fields,
    )


class AssembledJacobian:
    """``J`` and ``J^T`` as CSR matrices, filled by coloured probing.

    Both are stored because PyTorch's CSR transpose-matvec is ~50x slower than its
    forward matvec, and CGLS needs one of each per iteration.
    """

    def __init__(self, matrix: torch.Tensor, transpose: torch.Tensor,
                 shape: tuple[int, int], dense_tail: torch.Tensor | None = None):
        self.matrix = matrix
        self.transpose = transpose
        # Rows appended AFTER the sparse ones, stored dense as [K, n]. Coloured probing
        # cannot represent a row without a local mesh stencil -- one dense row makes
        # every column conflict with every other and the colouring degenerates -- but a
        # handful of such rows are cheap to carry explicitly. See attach_row_sums.
        self.dense_tail = dense_tail
        extra = 0 if dense_tail is None else dense_tail.shape[0]
        self.shape = (shape[0] + extra, shape[1])

    def matvec(self, v: torch.Tensor) -> torch.Tensor:
        v = v.reshape(-1)
        out = self.matrix @ v
        if self.dense_tail is None:
            return out
        return torch.cat((out, self.dense_tail @ v))

    def rmatvec(self, u: torch.Tensor) -> torch.Tensor:
        u = u.reshape(-1)
        if self.dense_tail is None:
            return self.transpose @ u
        split = self.transpose.shape[1]
        return (self.transpose @ u[:split]) + (self.dense_tail.T @ u[split:])

    def attach_row_sums(self, row_ranges, scale: float) -> "AssembledJacobian":
        """Append rows that are ``scale`` times the sum of an existing row range.

        A residual row defined as the SUM of a block of other rows has, by linearity, a
        Jacobian row equal to the same sum of their Jacobian rows -- so it needs no
        probing at all and is exact rather than approximated. The global mass-balance
        row is exactly of this kind: the per-cell continuity residual is the
        volume-integrated divergence, so summing it over the interior telescopes to the
        net flux through the domain boundary.

        Each sum is one transpose-matvec against the range's indicator vector, which is
        why the transpose is worth storing. ``scale`` converts from the weight already
        baked into the summed rows to the weight the new row should carry.
        """
        return self.attach_weighted_row_sums(row_ranges, None, scale)

    def attach_weighted_row_sums(
        self, row_ranges, weights: torch.Tensor | None, scale: float
    ) -> "AssembledJacobian":
        """Append rows that are ``scale`` times a WEIGHTED sum of a row range.

        Same linearity argument as :meth:`attach_row_sums`, with a per-row weight
        vector in place of the indicator. The first spatial moments of the continuity
        residual are exactly this shape -- ``sum_cells w_k(cell) * r_cont(cell)`` --
        so their Jacobian rows are the same weighted sum of the continuity Jacobian
        rows, again exact and again free of probing.

        ``weights`` is ``[K, span]``: ``K`` new rows per range, each weighting the
        ``stop - start`` rows of that range. ``None`` means a single unweighted sum,
        which is what makes :meth:`attach_row_sums` a special case rather than a
        second implementation. Rows are emitted range-major (all ``K`` rows of the
        first range, then the second), matching a ``[W, K, 1]`` residual block.
        """
        rows = []
        m = self.transpose.shape[1]
        dtype = self.transpose.values().dtype
        for start, stop in row_ranges:
            span = stop - start
            if weights is not None and int(weights.shape[-1]) != span:
                raise ValueError(
                    f"weight vectors have length {int(weights.shape[-1])} but the "
                    f"row range [{start}, {stop}) spans {span} rows"
                )
            for index in range(1 if weights is None else int(weights.shape[0])):
                combination = torch.zeros(m, device=self.transpose.device, dtype=dtype)
                combination[start:stop] = (
                    1.0 if weights is None else weights[index].to(dtype)
                )
                rows.append(scale * (self.transpose @ combination))
        tail = torch.stack(rows)
        self.dense_tail = tail if self.dense_tail is None else torch.cat(
            (self.dense_tail, tail)
        )
        self.shape = (self.shape[0] + tail.shape[0], self.shape[1])
        return self

    def _as_state_rows(self, rows: torch.Tensor) -> torch.Tensor:
        """Flatten to ``[K, n]`` and check ``n`` against the state dimension.

        A row of the wrong length would otherwise be caught only by a shape error
        deep inside a later matvec, or not at all if it happened to broadcast.
        """
        rows = rows.reshape(rows.shape[0], -1)
        if rows.shape[1] != self.shape[1]:
            raise ValueError(
                f"constant rows have {rows.shape[1]} columns but the Jacobian's "
                f"state dimension is {self.shape[1]}"
            )
        return rows

    def attach_constant_rows(
        self, rows: torch.Tensor, scale: float
    ) -> "AssembledJacobian":
        """Append rows given DIRECTLY in state space, as ``scale * rows``.

        The sibling methods above derive their rows from rows ``J`` already holds.
        This one takes them ready-made, for a residual whose Jacobian row the caller
        knows in closed form rather than as a combination of other rows -- the
        pressure-reference gauge is exactly that, a volume-weighted mean of the
        pressure channel, and it needs neither a probe nor a transpose matvec.
        """
        rows = self._as_state_rows(rows)
        tail = (scale * rows).to(
            device=self.transpose.device, dtype=self.transpose.values().dtype
        )
        self.dense_tail = tail if self.dense_tail is None else torch.cat(
            (self.dense_tail, tail)
        )
        self.shape = (self.shape[0] + tail.shape[0], self.shape[1])
        return self

    def overwrite_constant_rows(
        self, start: int, rows: torch.Tensor, scale: float
    ) -> "AssembledJacobian":
        """Rewrite dense-tail rows ``[start, start + K)`` with ``scale * rows``.

        A dense row that depends on the linearization POINT rather than only on the
        mesh has to be refreshed whenever the base point moves, even on a path that
        deliberately reuses the sparse blocks. It cannot ride along with them and it
        cannot be caught going stale either: the reuse decision is a drift probe
        along one direction against a million rows, and a handful of tail rows
        contribute nothing measurable to it.
        """
        rows = self._as_state_rows(rows)
        if self.dense_tail is None:
            raise ValueError("there is no dense tail to overwrite")
        stop = start + rows.shape[0]
        if start < 0 or stop > self.dense_tail.shape[0]:
            raise ValueError(
                f"rows [{start}, {stop}) fall outside a dense tail of "
                f"{self.dense_tail.shape[0]} rows"
            )
        self.dense_tail[start:stop] = (scale * rows).to(
            device=self.dense_tail.device, dtype=self.dense_tail.dtype
        )
        return self

    def scale_rows_(self, row_scale: torch.Tensor) -> "AssembledJacobian":
        """Multiply row ``i`` of ``J`` by ``row_scale[i]``, in place, exactly.

        Equation-block reweighting multiplies whole row blocks of the residual by a
        scalar, which is precisely a diagonal row scaling of ``J``. So a cached
        Jacobian can be carried across a reweighting by rescaling it rather than
        reassembling -- exactly, not approximately.

        Without this, reweighting and reuse are in genuine conflict rather than merely
        an implementation conflict: measured, a weight change registers as large drift
        (it scales entire blocks), so a drift-triggered cache reassembles on every head
        and the reuse win vanishes -- 46.6 s/head instead of 10.6 s.

        In the forward CSR the values are ordered by row, so each value's row comes from
        the crow pointers; in the transpose the values are ordered by column, so each
        value's row of ``J`` is its column index there.
        """
        if self.dense_tail is not None:
            # The tail rows are LINEAR COMBINATIONS of sparse rows, so rescaling the
            # sparse blocks silently invalidates them: a tail built from the continuity
            # block at weight w still carries w after that block is rescaled to w'.
            # Rebuilding is correct and cheap; guessing is neither.
            raise NotImplementedError(
                "scale_rows_ cannot carry a derived dense tail across a reweighting; "
                "rebuild the tail with attach_row_sums after rescaling."
            )
        scale = row_scale.to(self.matrix.values().dtype)

        crow = self.matrix.crow_indices()
        counts = (crow[1:] - crow[:-1]).to(torch.long)
        row_of_value = torch.repeat_interleave(
            torch.arange(counts.numel(), device=scale.device), counts
        )
        self.matrix.values().mul_(scale[row_of_value])

        self.transpose.values().mul_(
            scale[self.transpose.col_indices().long()]
        )
        return self

    def column_norms_squared(self) -> torch.Tensor:
        """Exact ``diag(J^T J)``, i.e. the squared 2-norm of each column of ``J``.

        Free once ``J`` is assembled: the transpose is already stored in CSR, and its
        ROWS are ``J``'s columns, so this is one segmented sum of squares. Estimating
        the same quantity matrix-free would need one probe per column.
        """
        transpose = self.transpose
        crow = transpose.crow_indices()
        values = transpose.values()
        counts = (crow[1:] - crow[:-1]).to(torch.long)
        row_of_entry = torch.repeat_interleave(
            torch.arange(counts.numel(), device=values.device), counts
        )
        out = torch.zeros(counts.numel(), device=values.device, dtype=values.dtype)
        out.index_add_(0, row_of_entry, values * values)
        if self.dense_tail is not None:
            # diag(J^T J) sums over ALL rows; omitting the tail would understate every
            # column it touches, and a dense row touches all of them.
            out = out + (self.dense_tail * self.dense_tail).sum(dim=0)
        return out

    def jacobi_preconditioner(self, *, floor: float = 1.0e-30):
        """Diagonal preconditioner for the normal equations, from exact column norms.

        Least-squares columns here carry genuinely different scales -- different
        physical fields, and cells whose volumes span orders of magnitude through the
        near-wall refinement. That second one is a documented failure of this case: the
        volume-weighted unnormalised objective leaves refined near-wall cells at roughly
        1e-4 of the norm budget, so the optimiser never converges them. Scaling by
        ``diag(J^T J)`` puts every column on the same footing and attacks exactly that.

        This is the cheap, exact member of the preconditioner family. Forming ``J^T J``
        itself is not viable at this size -- its sparsity is the column conflict graph,
        measured at ~1120 nonzeros per row over 1.47M rows, some 1.6e9 entries -- so a
        genuine AMG needs a SPARSIFIED approximation rather than the true product.
        """
        diagonal = self.column_norms_squared().clamp_min(floor)
        return lambda vector: vector / diagonal


def assemble(jv_product: Callable[[torch.Tensor], torch.Tensor],
             sparsity: ColouredSparsity, *, num_rows: int, device,
             dtype=torch.float32, progress=None,
             probe_batch: int = 1) -> AssembledJacobian:
    """Fill the Jacobian with one probe per (time row, colour, field).

    The time axis cannot be collapsed: the window Jacobian is block lower triangular in
    time, so probing several time levels together mixes contributions that cannot be
    separated afterwards.

    ``jv_product`` takes a flat tangent of length ``window * num_cells * num_fields``
    and returns the flat stacked residual tangent.

    ``probe_batch`` pushes several tangents through one ``vmap``. Colours and fields at a
    fixed time level are independent probes, so batching them is exact -- each still
    yields its own tangent, they are merely computed together. 1 keeps the original
    strictly serial loop and is the A/B control.

    **What it is worth, measured rather than assumed.** A serial probe is 33.6 ms and a
    batched one bottoms out at 15.7 ms, saturating at 2.14x by batch 20: about half a
    probe is fixed per-kernel overhead that batching removes, and the rest is bandwidth
    that does not compress. That takes an assembly from ~34 s to ~16 s, worth roughly
    1.2x on a whole step at the observed reassembly cadence. It is worth having and it is
    not worth expecting more from.

    That ``vmap`` works here at all is a measurement too, not an inference: the same code
    makes ``torch.compile`` capture zero graphs, but vmap and dynamo fail on different
    things. The batched tangents match the serial ones to 1e-7, the same float32 level as
    the JVP self-check.
    """
    num_cols = sparsity.window * sparsity.num_cells * sparsity.num_fields
    rows_all, cols_all, vals_all = [], [], []
    batch = max(1, int(probe_batch))

    def _tangent(time_index, colour, field):
        tangent = torch.zeros(
            sparsity.window, sparsity.num_cells, sparsity.num_fields,
            device=device, dtype=dtype,
        )
        tangent[time_index, sparsity.cells_of_colour[colour], field] = 1.0
        return tangent.reshape(-1)

    for time_index in range(sparsity.window):
        probes = [
            (colour, field)
            for colour in range(sparsity.num_colours)
            if sparsity.cells_of_colour[colour].numel() > 0
            for field in range(sparsity.num_fields)
        ]
        for start in range(0, len(probes), batch):
            chunk = probes[start:start + batch]
            if len(chunk) == 1:
                colour, field = chunk[0]
                results = [jv_product(_tangent(time_index, colour, field))]
            else:
                tangents = torch.stack([
                    _tangent(time_index, colour, field) for colour, field in chunk
                ])
                results = list(torch.vmap(jv_product)(tangents))
            for (colour, field), jv in zip(chunk, results):
                for block in sparsity.blocks:
                    source = block.entity_by_colour[:, colour]      # [E]
                    present = source >= 0
                    if not bool(present.any()):
                        continue
                    entity = present.nonzero(as_tuple=True)[0]
                    cell = source[entity].long()
                    # Column of (time_index, cell, field) in the flat unknown vector.
                    col = (time_index * sparsity.num_cells * sparsity.num_fields
                           + cell * sparsity.num_fields + field)
                    # The window Jacobian is block LOWER TRIANGULAR in time: a row at
                    # window time k depends on the unknowns of every t <= k. So a probe
                    # at time t lands in the rows of ALL later time levels too, and
                    # scanning only k == t would silently drop every time-coupling
                    # entry -- the assembled matrix would still look plausible.
                    for row_time in range(time_index, sparsity.window):
                        base = (block.row_offset
                                + row_time * block.num_entities * block.components)
                        for comp in range(block.components):
                            row = base + entity * block.components + comp
                            value = jv[row]
                            keep = value != 0
                            if not bool(keep.any()):
                                continue
                            rows_all.append(row[keep])
                            cols_all.append(col[keep])
                            vals_all.append(value[keep])
                if progress is not None:
                    progress(time_index, colour, field)

    # Memory discipline matters more than it looks here. At 165M nonzeros every
    # full-length copy is ~1.3 GB per int64 array, and the naive sequence -- keep the
    # fragment list, concatenate, build COO, coalesce, repeat for the transpose --
    # holds five or six of them at once and peaked around 18 GB. Each list is
    # concatenated and dropped immediately, and index arrays stay int32 (the largest
    # index is a few million, far inside its range), which halves them.
    def _drain(chunks, dtype):
        out = torch.cat(chunks).to(dtype)
        chunks.clear()
        return out

    rows = _drain(rows_all, torch.int32)
    cols = _drain(cols_all, torch.int32)
    vals = _drain(vals_all, dtype)
    del rows_all, cols_all, vals_all
    torch.cuda.empty_cache()

    def _to_csr(row_idx, col_idx, shape):
        coo = torch.sparse_coo_tensor(
            torch.stack([row_idx.long(), col_idx.long()]), vals, size=shape,
        ).coalesce()
        csr = coo.to_sparse_csr()
        del coo
        torch.cuda.empty_cache()
        return csr

    forward = _to_csr(rows, cols, (num_rows, num_cols))
    backward = _to_csr(cols, rows, (num_cols, num_rows))
    del rows, cols, vals
    torch.cuda.empty_cache()
    return AssembledJacobian(forward, backward, (num_rows, num_cols))
