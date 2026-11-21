"""Boundary-condition interventions — the gate that stops a draw-invariant model from
passing as a boundary-complete one.

WHY THIS EXISTS. The headline well-posedness metric is draw-sensitivity: how much a
model's answer changes when you draw a different set of pivots. Low is good — it means
the answer is determined rather than an artefact of the sample. But it has exactly one
cheat: a model that IGNORES the boundary entirely is trivially draw-invariant, because
its answer never depended on which boundary faces were drawn. It scores perfectly for
the worst possible reason.

That cheat is not hypothetical. `DNE-SS-MGN` is provably boundary-blind: `cpd|neighbor_cell`
is a strict DAG (all 913,772 edges run low-index -> high-index, zero reciprocal pairs) in
which boundary faces have out-degree exactly 0, and MeshGraphNets aggregates only into
receivers (MeshGraphNetsBaseline.py:27). By induction over blocks, an interior node's
representation depends only on interior features -- the boundary condition cannot reach it.
MGN's near-zero draw sensitivity (+0.0061) is therefore meaningless, and `DNE-SS-AeroGTO`
sits in the same cell (+0.0026) for reasons we cannot yet distinguish.

So draw-sensitivity must be read against a SECOND axis: does the model respond to the
boundary at all? This module supplies that axis by intervening on the boundary values at
inference time and measuring the damage.

    zero      the boundary values are set to 0 (geometry pos/unv is kept).
              Delta-nRMSE ~ 0  =>  the model never used the boundary.
    shuffle   the boundary values are permuted across faces. Distribution preserved,
              spatial arrangement destroyed.
              Delta-nRMSE ~ 0  =>  the model uses at most the boundary's marginal
              statistics, not which face carries what.
    scale:F   the boundary values are multiplied by F.
              A graded response; a model that solves the PDE must move.

VALIDITY SELF-TEST. Because MGN is boundary-blind by proof, `zero` on MGN MUST give
Delta-nRMSE ~= 0. If it does not, this module is wired wrong and every other number it
produces is worthless. Run that case first.

THE SILENT NO-OP THIS MODULE IS BUILT TO PREVENT. The boundary enters the models through
FOUR separate branches of importer.py, and the pre-existing `GTC_ABLATE_KV` hook was
wired into exactly one of them. Running it on the other three did nothing at all -- and
"nothing at all" reads as Delta-nRMSE = 0, which is precisely the signature of "the model
ignores the boundary". A failed intervention and a real discovery are the same number.
So: every call site registers itself, `assert_fired()` raises if an intervention was
requested and no site fired, and every firing logs the tensor norm before and after.
Never report a Delta-nRMSE of ~0 without the fired-sites line in the log to back it.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import torch

logger = logging.getLogger(__name__)

ENV_VAR = "GTC_BC_INTERVENTION"

# Set by apply(); read by assert_fired() and by the runner when it labels the run.
_fired: dict[str, int] = {}


def mode() -> Optional[str]:
    """The requested intervention, or None.

    Values: 'zero' | 'zero_stream' | 'shuffle' | 'scale:<float>' | 'subset:<float>'
    | 'droppatch:<float>'. 'subset' keeps a random i.i.d. fraction of boundary
    FACES, 'droppatch' keeps a random fraction of whole PATCHES; only the latter
    tests boundary completeness, for the reason given in subset_boundary().
    """
    raw = (os.environ.get(ENV_VAR) or "").strip().lower()
    if not raw or raw in ("0", "off", "none", "false"):
        return None
    if raw in ("zero", "shuffle", "zero_stream") or raw.startswith(("scale:", "subset:", "droppatch:")):
        return raw
    raise ValueError(
        f"{ENV_VAR}={raw!r} is not a recognised intervention. "
        f"Use 'zero', 'zero_stream', 'shuffle', 'scale:<float>', 'subset:<float>', "
        f"'droppatch:<float>', or unset it. "
        f"Refusing to run: an unrecognised value would silently mean 'no intervention', "
        f"and a no-op intervention produces Delta-nRMSE = 0, which is indistinguishable "
        f"from the finding 'the model ignores the boundary'."
    )


def reset() -> None:
    _fired.clear()


def fired_sites() -> dict[str, int]:
    return dict(_fired)


def assert_fired() -> None:
    """Raise if an intervention was requested but no call site applied it.

    Without this, a mis-wired hook yields Delta-nRMSE = 0 and reads as a discovery.
    """
    m = mode()
    if m is None:
        return
    if not _fired:
        raise RuntimeError(
            f"{ENV_VAR}={m!r} was requested but NO call site fired. The boundary was never "
            f"touched, so any Delta-nRMSE you compute is 0 by construction -- which looks "
            f"exactly like 'the model ignores the boundary'. This model's boundary input "
            f"goes through a branch of importer.py that has no intervention hook. Add one "
            f"there before trusting any number from this run."
        )


def subset_boundary(
    values: torch.Tensor,
    boundary_pos: torch.Tensor,
    boundary_unv: torch.Tensor,
    boundary_batch: torch.Tensor,
    site: str,
    patch_index: Optional[torch.Tensor] = None,
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
    """Restrict the complete-boundary K/V set to a random fraction, under mode 'subset:<F>'.

    This is the completeness test, and it is a different question from 'zero' and 'shuffle'.
    Those two ask whether the boundary VALUES are read at all. This one grants the model a
    genuine boundary specification and asks whether it needs the COMPLETE one: the paper's
    whole claim is that a sampled subgraph retains only a subset g_s of the boundary data g,
    that (Omega_s, g_s) -> u is then one-to-many, and that injecting the whole of g restores
    uniqueness. Sweeping F therefore traces the claim directly. If interior error is flat in
    F, completeness buys nothing and the mechanism story is wrong however large the zeroing
    collapse was.

    We SUBSET rather than zero the dropped rows. Zeroing them would leave the attention a
    full-length set of keys in which most entries are a spurious "all fields are zero" face,
    which is not the same object as a smaller boundary and would confound absence with a
    fake value. N_b already varies per case, so a shorter set is in-distribution.

    All four boundary-indexed tensors are subset with the SAME indices, so face k keeps its
    own position, normal and value. Indices are sorted, so relative face order survives.
    """
    m = mode()
    if m is None or not m.startswith(("subset:", "droppatch:")):
        return values, boundary_pos, boundary_unv, boundary_batch
    frac = float(m.split(":", 1)[1])
    n = int(values.shape[0])

    if m.startswith("droppatch:"):
        # 🔴 WHY THIS MODE EXISTS, and why 'subset:' does NOT answer the completeness question.
        # 'subset:' drops i.i.d. FACES. Boundary conditions are piecewise constant per patch and
        # a patch carries hundreds of faces (measured: ~556 on layout, ~714 on thermal), so an
        # i.i.d. 5% face draw keeps ~28-36 faces of EVERY patch and the probability that any
        # patch loses all of its faces is ~1e-13. 'subset:' therefore never removes a boundary
        # condition -- it thins a redundant face set, and its small effect (1.8-2.8%) measures
        # within-patch face redundancy, not completeness. Two reviewers identified this
        # independently on 2026-07-25 and both were right.
        #
        # Sampling deletes PATCHES. So the control that actually tests the thesis drops whole
        # patches, keeping every face of the survivors and none of the dropped.
        if patch_index is None or patch_index.numel() != n:
            raise RuntimeError(
                f"{ENV_VAR}={m!r} needs a per-face patch index row-aligned with the K/V rows, "
                f"and got {None if patch_index is None else patch_index.numel()} against {n} "
                f"rows. Refusing to fall back on face-level subsetting, which answers a "
                f"different question and would silently look like a completeness result."
            )
        pid = patch_index.reshape(-1)
        uniq = torch.unique(pid)
        p = int(uniq.numel())
        kp = max(1, int(round(frac * p)))
        if kp >= p:
            keep = torch.arange(n, device=values.device)
            kept_patches = p
        else:
            sel = uniq[torch.randperm(p, device=uniq.device)[:kp]]
            mask = torch.isin(pid, sel)
            keep = mask.nonzero(as_tuple=True)[0]
            kept_patches = kp
        _fired[site] = _fired.get(site, 0) + 1
        if _fired[site] == 1:
            logger.info(
                f"🧪 BC intervention '{m}' FIRED at site '{site}': kept {kept_patches}/{p} "
                f"PATCHES ({100.0 * kept_patches / p:.1f}%), which is {keep.numel()}/{n} faces "
                f"({100.0 * keep.numel() / n:.1f}%). Dropped patches contribute NO face, so "
                f"their boundary condition is genuinely absent from the injected set."
            )
        return (values[keep], boundary_pos[keep], boundary_unv[keep], boundary_batch[keep])

    k = max(1, int(round(frac * n)))
    if k >= n:
        keep = torch.arange(n, device=values.device)
    else:
        keep = torch.randperm(n, device=values.device)[:k].sort().values
    _fired[site] = _fired.get(site, 0) + 1
    if _fired[site] == 1:
        logger.info(
            f"🧪 BC intervention '{m}' FIRED at site '{site}': boundary K/V set restricted "
            f"from {n} to {k} faces ({100.0 * k / n:.1f}%). Geometry and values are subset "
            f"together, so every surviving face keeps its own data. NOTE: this drops i.i.d. "
            f"FACES, not patches, so it does not test completeness -- use droppatch: for that."
        )
    return (values[keep], boundary_pos[keep], boundary_unv[keep], boundary_batch[keep])


def apply_geometry(
    boundary_pos: torch.Tensor,
    boundary_unv: torch.Tensor,
    site: str,
) -> tuple[torch.Tensor, torch.Tensor]:
    """Wipe the boundary GEOMETRY as well — only under mode 'zero_stream'.

    `zero` asks: does the model use the boundary VALUES? It leaves boundary_pos/boundary_unv
    intact, because those are geometry, and ablating geometry would confound the answer.

    But that split turned out to be the whole story. Two SCA arms
    (DNEface-SCA-TransFVGN, DNEpatch-SCA-TransFVGN) survive `zero` with their interior nRMSE
    unchanged to four decimals — the boundary VALUES are worth nothing to them — while the
    same K/V content on a Transolver trunk moves 71%. The obvious suspect is that those arms
    read the boundary's SHAPE instead of its condition, which on this dataset is a shortcut:
    Slab's geometry varies per case and the flow rates are set per rack, so the boundary
    geometry all but names the case.

    `zero_stream` tests exactly that by deleting the entire boundary stream, geometry and
    all. If the interior still does not move, the two-stream apparatus is decorative on that
    arm — it is a single-stream model with extra parameters, and the arm cannot be used as
    evidence for boundary conditioning.
    """
    if mode() != "zero_stream":
        return boundary_pos, boundary_unv
    p, u = torch.zeros_like(boundary_pos), torch.zeros_like(boundary_unv)
    _fired[site] = _fired.get(site, 0) + 1
    if _fired[site] == 1:
        logger.info(
            f"🧪 BC intervention 'zero_stream' FIRED at site '{site}': boundary GEOMETRY wiped "
            f"({boundary_pos.shape[0]} faces). The model now receives NO boundary stream at all."
        )
    return p, u


def apply(
    tensor: torch.Tensor,
    site: str,
    rows: Optional[torch.Tensor] = None,
    cols: Optional[slice] = None,
) -> torch.Tensor:
    """Intervene on the boundary values in `tensor`, returning a new tensor.

    Args:
        tensor: the boundary-carrying tensor as the model would have received it.
        site:   a name for the branch of importer.py this came from — logged, and used by
                assert_fired() to prove the intervention actually reached the model.
        rows:   bool mask selecting the boundary rows. None = every row is a boundary row
                (true for the two-stream K/V tensors, which contain nothing else).
        cols:   slice selecting the value columns. None = all columns. Used by the
                single-stream path, whose node feature is [pos(3), unv(3), bc(5)] and where
                only the bc slice may be touched — zeroing pos/unv would ablate the
                GEOMETRY, not the boundary condition, and would confound the whole test.
    """
    m = mode()
    if m is None:
        return tensor
    if m.startswith(("subset:", "droppatch:")):
        # Handled by subset_boundary(), which must also subset pos/unv/batch in step. Falling
        # through here would hit the scale: branch below and silently MULTIPLY the values by
        # the fraction (subset:0.5 -> scale by 0.5), a completely different intervention that
        # would still log as "fired".
        return tensor

    out = tensor.clone()
    view = out
    if rows is not None:
        rows = rows.reshape(-1).bool()
        if not bool(rows.any()):
            # No boundary rows in this batch: nothing to do, and nothing to claim.
            return out
    sel_rows = rows if rows is not None else slice(None)
    sel_cols = cols if cols is not None else slice(None)

    # .clone() is REQUIRED, not defensive. With rows=cols=None this is basic slicing, which
    # returns a VIEW, and .float() on an already-float32 tensor returns `self` — so without the
    # clone `before` aliases `out`, and the post-write `moved` diagnostic below compares the
    # tensor against itself and reports 0 rows changed for a shuffle that in fact permuted every
    # row. That false "0/N changed" reads exactly like the dead-site failure this module exists
    # to catch, and cost one wasted inference run on 2026-07-25.
    before = view[sel_rows, sel_cols].float().clone()
    n_touched = int(before.shape[0])
    norm_before = float(before.norm())

    if m in ("zero", "zero_stream"):
        view[sel_rows, sel_cols] = 0.0
    elif m == "shuffle":
        # Permute the boundary values ACROSS boundary rows. The multiset of values the
        # model sees is unchanged; only the assignment of value to face is destroyed.
        perm = torch.randperm(n_touched, device=out.device)
        view[sel_rows, sel_cols] = before[perm].to(out.dtype)
    else:  # scale:<float>
        factor = float(m.split(":", 1)[1])
        view[sel_rows, sel_cols] = (before * factor).to(out.dtype)

    norm_after = float(out[sel_rows, sel_cols].float().norm())
    _fired[site] = _fired.get(site, 0) + 1

    if _fired[site] == 1:  # log once per site, not once per batch
        logger.info(
            f"🧪 BC intervention '{m}' FIRED at site '{site}': "
            f"{n_touched} boundary rows, cols={cols}, "
            f"||x|| {norm_before:.4f} -> {norm_after:.4f}"
        )
        # 'shuffle' permutes rows, which is norm-preserving BY CONSTRUCTION, so norm_after ==
        # norm_before is the intended behaviour, not a symptom of a dead site. 'scale:1.0' is a
        # deliberate identity. Only 'zero' and 'scale:F!=1' should move the norm; skip the others.
        if abs(norm_before - norm_after) < 1e-9 and m not in ("scale:1.0", "shuffle"):
            logger.warning(
                f"⚠️  BC intervention at '{site}' left the tensor norm UNCHANGED "
                f"({norm_before:.6f}). Either the boundary values were already zero, or "
                f"this site is not the one the model actually reads. Do not trust a "
                f"Delta-nRMSE from this run until you know which."
            )
        elif m == "shuffle":
            # For shuffle the norm can't tell us anything; report how many rows actually moved so a
            # dead/degenerate site (identity permutation, or all-identical values) is still visible.
            moved = int((out[sel_rows, sel_cols].float() != before).any(dim=-1).sum())
            logger.info(
                f"🧪 BC 'shuffle' at '{site}': {moved}/{n_touched} boundary rows changed value "
                f"(norm preserved by design)."
            )
    return out
