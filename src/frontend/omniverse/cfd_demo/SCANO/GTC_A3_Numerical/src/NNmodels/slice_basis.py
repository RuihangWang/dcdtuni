"""Slice-basis capture and injection — can an ROI pass borrow a whole-domain query basis?

WHAT THIS TESTS
---------------
Restricting SCANO's pivot pool to a contiguous box costs +0.405 nRMSE (fail threshold
0.077). The boundary is NOT what breaks: the two-stream cross-attention injects all 856
patches / 102,576 boundary rows regardless of sampling, byte-identical in every arm, and
it is load-bearing (zeroing it takes interior nRMSE 0.204 -> 2.27). What breaks is the
QUERY side. The slice basis is pooled node-set-GLOBALLY eight times per forward:

    PatchCrossAttentionEncoder._compute_slice_tokens_{dense,scatter}  (:228 / :249)
        -> internal_slice_token, the cross-attention Q                (site *.internal)
    Graph_Physics_Attention_1D._graph_forward_{dense,scatter}         (:96  / :128)
        -> slice_token, x7 (attn_processor_num: 7)                    (sites *.Attn)

Each is a weighted mean over exactly the nodes handed in, and node positions enter raw
and un-recentred. Confine the sample and the basis collapses: trunk slice entropy
2.699 -> 2.286 at matched node count, ~14.9 -> ~9.8 effective slices of 32. The complete
boundary is present but addressable only through a basis pooled from the interior sample.

HYPOTHESIS: if the ROI pass REUSES a slice basis captured from a whole-domain pass
instead of pooling its own, the ROI regains the ability to address the boundary and its
accuracy recovers, with no retraining.

WHAT IS SUBSTITUTED, AND WHY THAT SPLIT
---------------------------------------
Only the pooled slice TOKENS [B, H, G, D] are substituted. The per-node assignment
weights `slice_weights` [N, H, G] and the scatter-back / deslice stay the ROI pass's own.
That split is the right one to test, for three independent reasons:

  1. SHAPE. slice_weights carries a node axis of length N_roi. A cached whole-domain
     weight matrix has a different N and simply cannot be applied. Any "fix" that made
     it applicable would be inventing a node correspondence that does not exist.
  2. SEMANTICS. slice_weights answers "which slice does THIS node belong to" — a
     property of the ROI's own nodes, and the only thing that routes the attention
     output back to the right ROI cells. Replacing it would write another node set's
     answers onto ROI cells, i.e. it would no longer be an ROI prediction at all.
  3. THE DIAGNOSIS. The token is exactly what the diagnosis says collapses: it is the
     node-set-global mean, node-count independent, and its G index is anchored by the
     FIXED, shared `in_project_slice` projector — so slice g means the same thing in
     every pass of a given checkpoint. That is what makes a cached basis transferable at
     all, and it is what the cross-attention reads (Q) and the trunk self-attends over.

So: borrow WHAT the slices mean; keep WHICH slice each ROI node is in, and keep how the
answer comes home. Anything else is not the stated hypothesis.

THE CIRCULARITY TRAP
--------------------
A basis captured from a whole-domain pass AT THE SAME OPERATING POINT is captured from a
pass that already IS the answer for those cells. "The ROI now matches it" is then close
to tautological. That run is still worth doing as a MECHANISM TEST, but it is not
evidence of a usable feature. The result that matters is CROSS-OPERATING-POINT reuse:
capture once at BC-A, inject into ROI passes at BC-B, BC-C, where no whole-domain pass
exists. This module makes the distinction measurable rather than rhetorical: the
boundary K/V site (`*.patch`) is captured but NOT injected by default, and on every
inject run its cached-vs-own relative difference is reported. That number is ~0 exactly
when the cached basis came from the same operating point, and grows with BC distance. It
is printed next to the accuracy so a same-point result can never be quoted as a
cross-point one.

USAGE (all default-off; unset -> `hook()` returns its argument unchanged)
------------------------------------------------------------------------
    SCANO_BASIS_CAPTURE=<path.pt>   run normally, dump the slice tokens at every site.
    SCANO_BASIS_INJECT=<path.pt>    substitute the cached tokens for the pooled ones.
    SCANO_BASIS_SITES=all|encoder|trunk|+patch|<comma list of exact site names>
                                    which sites to SUBSTITUTE (default: all query sites,
                                    i.e. every captured site except *.patch).
    SCANO_BASIS_REDUCE=mean|first|last   how to collapse the capture's N forwards into
                                    one basis (default mean; see _reduce()).
    SCANO_BASIS_MIX=<0..1>          blend cached into own: 1.0 = full substitution
                                    (default), 0.0 = no-op passthrough with the
                                    diagnostics still reported.
    SCANO_BASIS_LABEL=<text>        free-text provenance stamped into the cache file.

Never both CAPTURE and INJECT in one process: the capture would record tokens that had
already been overwritten by an injection, and the resulting file would be a laundered
copy of its own input.
"""
from __future__ import annotations

import atexit
import json
import logging
import os
import sys
import time
from typing import Dict, Optional

import torch

logger = logging.getLogger(__name__)

ENV_CAPTURE = "SCANO_BASIS_CAPTURE"
ENV_INJECT = "SCANO_BASIS_INJECT"
ENV_SITES = "SCANO_BASIS_SITES"
ENV_REDUCE = "SCANO_BASIS_REDUCE"
ENV_MIX = "SCANO_BASIS_MIX"
ENV_LABEL = "SCANO_BASIS_LABEL"
ENV_ENTROPY = "SCANO_BASIS_ENTROPY"

FORMAT = "scano_slice_basis/1"

# Tri-state fast path. None = not yet resolved, False = neither env var set (the default
# path: hook() returns its argument, same object, no arithmetic, no allocation).
_ACTIVE: Optional[bool] = None

_capture_path: str = ""
_inject_path: str = ""
_entropy_path: str = ""
_sites_spec: str = ""
_reduce: str = "mean"
_mix: float = 1.0

# entropy state: site -> running sums (see _record_entropy for the two definitions)
_ent: Dict[str, dict] = {}

# capture state: site -> {"sum": f64 cpu tensor, "count": int, "first": tensor, "last": tensor}
_cap: Dict[str, dict] = {}
# inject state
_cache: Dict[str, torch.Tensor] = {}          # site -> reduced token (cpu)
_cache_meta: dict = {}
_inject_sites: set = set()
# bookkeeping, for assert_fired() and the run summary
_seen: Dict[str, int] = {}
_injected: Dict[str, int] = {}
_diff_sum: Dict[str, float] = {}              # sum of per-forward relative L2(cached, own)
_diff_n: Dict[str, int] = {}
_assigned: int = 0
_flushed: bool = False


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------

def _init() -> None:
    global _ACTIVE, _capture_path, _inject_path, _entropy_path, _sites_spec, _reduce, _mix

    _capture_path = (os.environ.get(ENV_CAPTURE) or "").strip()
    _inject_path = (os.environ.get(ENV_INJECT) or "").strip()
    _entropy_path = (os.environ.get(ENV_ENTROPY) or "").strip()
    for p in (_capture_path, _inject_path, _entropy_path):
        if p.lower() in ("0", "off", "none", "false"):
            raise ValueError(
                f"{ENV_CAPTURE}/{ENV_INJECT}/{ENV_ENTROPY} take a FILE PATH, not {p!r}. Unset the "
                f"variable to disable. A value that looks like 'off' would otherwise be "
                f"used as a filename and the run would silently do the wrong thing."
            )
    if _capture_path and _inject_path:
        raise ValueError(
            f"{ENV_CAPTURE} and {ENV_INJECT} are both set. Refusing: capture would record "
            f"tokens that injection had already overwritten, producing a cache file that "
            f"is a laundered copy of its own input and an experiment that cannot fail."
        )

    _ACTIVE = bool(_capture_path or _inject_path or _entropy_path)
    if not _ACTIVE:
        return

    if _entropy_path:
        d = os.path.dirname(os.path.abspath(_entropy_path))
        if d:
            os.makedirs(d, exist_ok=True)
        atexit.register(flush)
        logger.info("🧬 slice-basis ENTROPY readout armed -> %s", _entropy_path)

    _sites_spec = (os.environ.get(ENV_SITES) or "all").strip()
    _reduce = (os.environ.get(ENV_REDUCE) or "mean").strip().lower()
    if _reduce not in ("mean", "first", "last"):
        raise ValueError(f"{ENV_REDUCE} must be mean|first|last, got {_reduce!r}")
    raw_mix = (os.environ.get(ENV_MIX) or "").strip()
    _mix = float(raw_mix) if raw_mix else 1.0
    if not (0.0 <= _mix <= 1.0):
        raise ValueError(f"{ENV_MIX} must lie in [0, 1], got {_mix}")

    if _capture_path:
        ws = int(os.environ.get("WORLD_SIZE", "1") or 1)
        if ws > 1:
            raise RuntimeError(
                f"{ENV_CAPTURE} with WORLD_SIZE={ws}. Refusing: under multi-rank TTA each "
                f"rank forwards only its own chunk of the pivot pool, so each rank would "
                f"write a DIFFERENT file all claiming to be 'the whole-domain basis', and "
                f"whichever finished last would win. Capture on one rank."
            )
        d = os.path.dirname(os.path.abspath(_capture_path))
        if d:
            os.makedirs(d, exist_ok=True)
        atexit.register(flush)
        logger.info("🧬 slice-basis CAPTURE armed -> %s", _capture_path)

    if _inject_path:
        _load_cache()


def _load_cache() -> None:
    global _cache_meta, _inject_sites
    if not os.path.exists(_inject_path):
        raise FileNotFoundError(
            f"{ENV_INJECT}={_inject_path!r} does not exist. Refusing to run: an ROI pass "
            f"that silently fell back to pooling its own basis would reproduce the "
            f"baseline number and be indistinguishable from 'the fix does not work'."
        )
    blob = torch.load(_inject_path, map_location="cpu", weights_only=False)
    if not isinstance(blob, dict) or blob.get("format") != FORMAT:
        raise ValueError(
            f"{_inject_path} is not a {FORMAT} file (got format="
            f"{blob.get('format') if isinstance(blob, dict) else type(blob).__name__!r})."
        )
    _cache_meta = dict(blob.get("meta") or {})
    sites = blob["sites"]
    for site, rec in sites.items():
        _cache[site] = _reduce_record(site, rec)
    _inject_sites = _select_sites(sorted(_cache))
    if not _inject_sites:
        raise ValueError(
            f"{ENV_SITES}={_sites_spec!r} selected NO site out of {sorted(_cache)}. "
            f"Refusing: nothing would be substituted and the run would silently be the "
            f"un-injected baseline."
        )
    logger.info(
        "🧬 slice-basis INJECT armed <- %s\n"
        "     cached sites : %s\n"
        "     substituting : %s   (reduce=%s over %s forwards, mix=%.3f)\n"
        "     provenance   : %s",
        _inject_path, sorted(_cache), sorted(_inject_sites), _reduce,
        _cache_meta.get("n_forwards"), _mix,
        json.dumps({k: _cache_meta.get(k) for k in
                    ("label", "case", "dataset_path", "pivot_env", "timestamp")},
                   default=str),
    )


def _reduce_record(site: str, rec: dict) -> torch.Tensor:
    """Collapse a capture's N per-forward tokens into the one basis to inject.

    mean (default) is meaningful, not just convenient: slice index g is anchored by the
    FIXED projector `in_project_slice`, which is the same weight matrix in every forward
    of a given checkpoint. So slice g denotes the same physical mode in every draw, and
    averaging g across draws averages like with like — it is the whole-domain basis with
    the per-draw sampling noise taken out. `first`/`last` are provided for the paired
    control: if mean and first give the same answer, draw-averaging of the basis is not
    where the effect comes from.
    """
    if _reduce == "first":
        return rec["first"].clone()
    if _reduce == "last":
        return rec["last"].clone()
    n = int(rec["count"])
    if n <= 0:
        raise ValueError(f"cached site {site!r} has count={n}")
    return (rec["sum"] / n).to(rec["first"].dtype)


def _select_sites(all_sites) -> set:
    """Resolve SCANO_BASIS_SITES against the site names actually present in the cache."""
    spec = _sites_spec.lower()
    query = [s for s in all_sites if not s.endswith(".patch")]
    if spec in ("all", "", "query"):
        return set(query)
    if spec == "+patch":
        return set(all_sites)
    if spec == "encoder":
        return {s for s in query if s.endswith(".internal")}
    if spec == "trunk":
        return {s for s in query if not s.endswith(".internal")}
    want = {s.strip() for s in _sites_spec.split(",") if s.strip()}
    unknown = want - set(all_sites)
    if unknown:
        raise ValueError(
            f"{ENV_SITES} names sites that are not in the cache: {sorted(unknown)}. "
            f"Cached sites are {list(all_sites)}."
        )
    return want


def enabled() -> bool:
    if _ACTIVE is None:
        _init()
    return bool(_ACTIVE)


def capturing() -> bool:
    return enabled() and bool(_capture_path)


def injecting() -> bool:
    return enabled() and bool(_inject_path)


# ---------------------------------------------------------------------------
# site registration
# ---------------------------------------------------------------------------

def assign_site_ids(root: torch.nn.Module) -> int:
    """Tag every slice-pooling module with its qualified name, so tokens are keyed
    layer-by-layer and a cache can only be matched back to the same layer it came from.

    Names come from `named_modules()` of the UNWRAPPED model, e.g.

        encoder                              -> encoder.internal, encoder.patch
        processpr_list.0.TransBlock.Attn     -> processpr_list.0.TransBlock.Attn
        ... .6.TransBlock.Attn

    A no-op unless capture or inject is armed, so the default path never even walks the
    module tree.
    """
    global _assigned
    if not enabled():
        return 0
    n_enc = n_attn = 0
    for name, mod in root.named_modules():
        cn = type(mod).__name__
        if cn == "PatchCrossAttentionEncoder":
            mod._scano_site = name or "encoder"
            n_enc += 1
        elif cn == "Graph_Physics_Attention_1D":
            mod._scano_site = name or "attn"
            n_attn += 1
    _assigned = n_enc + n_attn
    if _assigned == 0:
        raise RuntimeError(
            "slice-basis is armed but NO pooling module was found in the model "
            "(expected PatchCrossAttentionEncoder + Graph_Physics_Attention_1D). "
            "Refusing: with no site tagged, hook() would raise on the first forward — "
            "or worse, in some other model, quietly do nothing and yield a delta of 0, "
            "which reads exactly like 'the fix does not work'."
        )
    logger.info(
        "🧬 slice-basis sites tagged: %d encoder + %d trunk attention = %d pooling "
        "modules (%d capture/inject sites incl. the boundary K/V probe)",
        n_enc, n_attn, _assigned, n_enc * 2 + n_attn,
    )
    return _assigned


# ---------------------------------------------------------------------------
# the hook
# ---------------------------------------------------------------------------

def hook(module: torch.nn.Module, suffix: str, slice_token: torch.Tensor,
         slice_weights: Optional[torch.Tensor] = None) -> torch.Tensor:
    """Capture and/or substitute the pooled slice token at one site.

    Default path (no env var set): returns the SAME OBJECT, unmodified. One bool test,
    no allocation, no arithmetic, no dtype change. `slice_weights` is accepted but never
    read on that path, and is NEVER modified on any path — it is passed in only so the
    entropy readout can measure the assignment the pass actually used, rather than
    re-deriving it (and risking re-deriving it wrongly).
    """
    if _ACTIVE is None:
        _init()
    if not _ACTIVE:
        return slice_token
    return _hook_active(module, suffix, slice_token, slice_weights)


def _hook_active(module, suffix, slice_token, slice_weights=None):
    site = getattr(module, "_scano_site", None)
    if site is None:
        raise RuntimeError(
            f"slice-basis is armed but {type(module).__name__} carries no _scano_site. "
            f"assign_site_ids(model) was never called on this model, so tokens could not "
            f"be keyed layer-by-layer. Refusing rather than guessing a key."
        )
    if suffix:
        site = f"{site}.{suffix}"
    _seen[site] = _seen.get(site, 0) + 1

    if _capture_path:
        _record_entropy(site, slice_token, slice_weights, slice_token)
        _accumulate(site, slice_token)
        return slice_token

    # Entropy-only run: measure, change nothing. Gated on _entropy_path being SET, not on
    # _inject_path being unset — otherwise an armed-but-unconfigured hook would fall
    # through here and silently no-op, which is the one failure mode that reads exactly
    # like "the cached basis does not help".
    if _entropy_path and not _inject_path:
        _record_entropy(site, slice_token, slice_weights, slice_token)
        return slice_token

    out = _substitute(site, slice_token)
    _record_entropy(site, slice_token, slice_weights, out)
    return out


# ---------------------------------------------------------------------------
# entropy readout — the direct measurement of the proposed mechanism
# ---------------------------------------------------------------------------

def _spread_entropy(p: torch.Tensor) -> float:
    """Shannon entropy (nats) of a distribution over the LAST axis, averaged over the
    leading axes. exp(H) is the effective number of slices out of G."""
    p = p / p.sum(dim=-1, keepdim=True).clamp_min(1e-30)
    h = -(p * p.clamp_min(1e-30).log()).sum(dim=-1)
    return float(h.mean())


def _record_entropy(site, own_token, weights, out_token) -> None:
    """Two entropies, and the distinction between them is the whole point.

    TOKEN entropy — over the pooled basis itself. p_g is slice g's share of the token
        energy, so exp(H) is 'how many of the G slices actually carry the basis'. This is
        what injection replaces, so it is the readout that CAN move.
    OCCUPANCY entropy — over the per-node assignment `slice_weights`, i.e. the share of
        node mass landing in slice g. This is the quantity the diagnosis reported
        collapsing (2.699 -> 2.286 whole-domain -> box). Injection does NOT touch
        slice_weights, so this one CANNOT move, by construction. It is recorded anyway,
        precisely so that fact is measured rather than asserted: if the ROI's occupancy
        stays collapsed while its accuracy recovers, then the fix works by handing the
        ROI a better basis, NOT by making the ROI's own pooling less degenerate.
    """
    if not _entropy_path:
        return
    with torch.no_grad():
        rec = _ent.setdefault(site, {"n": 0, "tok_own": 0.0, "tok_out": 0.0,
                                     "occ": 0.0, "n_occ": 0, "G": int(own_token.shape[-2]),
                                     "series_occ": [], "series_tok_own": []})
        rec["n"] += 1
        # token energy share across slices: [B, H, G, D] -> [B, H, G]
        h_own = _spread_entropy(own_token.detach().float().pow(2).sum(dim=-1))
        rec["tok_own"] += h_own
        # Per-forward series, capped. Runner._measure_forward_flops re-runs ONE forward on
        # a CLONE of the same batch in fp32 (no autocast), so every arm's forward count is
        # draws+1 and its first entry is that fp32 replay. Keeping the series is how the
        # reader can see that entry agrees with the real bf16 forwards instead of being
        # told to assume it.
        if len(rec["series_tok_own"]) < 64:
            rec["series_tok_own"].append(h_own)
        rec["tok_out"] += (h_own if out_token is own_token else
                           _spread_entropy(out_token.detach().float().pow(2).sum(dim=-1)))
        if weights is not None:
            w = weights.detach().float()
            if w.dim() == 4:        # dense  [B, H, N, G] -> node axis 2
                mass = w.sum(dim=2)
            elif w.dim() == 3:      # scatter [N, H, G]   -> node axis 0
                mass = w.sum(dim=0)
            else:
                raise RuntimeError(
                    f"slice-basis entropy: unexpected slice_weights rank {w.dim()} at "
                    f"site {site!r} (expected 4 dense [B,H,N,G] or 3 scatter [N,H,G]). "
                    f"Summing the wrong axis would silently report an entropy over heads "
                    f"or nodes instead of slices."
                )
            h_occ = _spread_entropy(mass)
            rec["occ"] += h_occ
            rec["n_occ"] += 1
            if len(rec["series_occ"]) < 64:
                rec["series_occ"].append(h_occ)


def _accumulate(site: str, tok: torch.Tensor) -> None:
    det = tok.detach()
    rec = _cap.get(site)
    if rec is None:
        _cap[site] = {
            "sum": det.to(dtype=torch.float64, device="cpu").clone(),
            "count": 1,
            "first": det.to("cpu").clone(),
            "last": det.to("cpu").clone(),
            "shape": tuple(det.shape),
            "dtype": str(det.dtype),
        }
        return
    if tuple(det.shape) != rec["shape"]:
        raise RuntimeError(
            f"site {site!r} changed shape mid-capture: {rec['shape']} -> "
            f"{tuple(det.shape)}. A basis averaged over inconsistent shapes is not a "
            f"basis. (Slice count / head count / feature dim must be constant; only the "
            f"node count may vary between draws, and the node axis is already pooled out.)"
        )
    rec["sum"] += det.to(dtype=torch.float64, device="cpu")
    rec["count"] += 1
    rec["last"] = det.to("cpu").clone()


def _substitute(site: str, tok: torch.Tensor) -> torch.Tensor:
    cached = _cache.get(site)
    if cached is None:
        raise RuntimeError(
            f"site {site!r} was reached by this run but is ABSENT from the cache "
            f"{_inject_path!r}, whose sites are {sorted(_cache)}. Refusing: the cache "
            f"was captured from a different architecture or a different layer ordering, "
            f"and injecting the sites that do happen to match would produce a hybrid "
            f"nobody could interpret."
        )
    # Shape guard. Exact equality on ALL axes — batch, head, slice, feature. NO
    # broadcasting: a [1,8,32,32] cache silently broadcast over a [4,8,32,32] run, or a
    # 32-slice cache stretched over a 64-slice model, would still run and still produce a
    # number, and that number would be uninterpretable.
    if tuple(cached.shape) != tuple(tok.shape):
        raise RuntimeError(
            f"slice-basis shape mismatch at site {site!r}: cached {tuple(cached.shape)} "
            f"vs this run {tuple(tok.shape)} [B, heads, slices, dim_head]. Refusing to "
            f"broadcast. Cache provenance: {_cache_meta.get('label')!r} / "
            f"{_cache_meta.get('timestamp')!r}."
        )
    c = cached.to(device=tok.device, dtype=tok.dtype)

    # Cached-vs-own difference at EVERY site, including the ones we do not substitute.
    # At the boundary K/V site (*.patch) this is the circularity meter: it is ~0 exactly
    # when the cache came from the same operating point, and grows with BC distance.
    with torch.no_grad():
        den = tok.detach().float().norm()
        rel = float((c.float() - tok.detach().float()).norm() / (den + 1e-12))
    _diff_sum[site] = _diff_sum.get(site, 0.0) + rel
    _diff_n[site] = _diff_n.get(site, 0) + 1

    if site not in _inject_sites:
        return tok
    _injected[site] = _injected.get(site, 0) + 1
    if _mix >= 1.0:
        return c
    return _mix * c + (1.0 - _mix) * tok


# ---------------------------------------------------------------------------
# guards and reporting
# ---------------------------------------------------------------------------

def assert_fired() -> None:
    """Call after the FIRST forward. Every site is visited exactly once per forward, so
    at that point the run's site set is complete and can be checked against the cache's.

    Without this, a hook wired into a branch the model does not take changes nothing, and
    "changed nothing" is delta-nRMSE = 0 — the same number as "the cached basis does not
    help". A mis-wire and a negative result must not be the same observation.
    """
    if not enabled():
        return
    if not _seen:
        raise RuntimeError(
            f"slice-basis is armed ("
            f"{ENV_CAPTURE if _capture_path else ENV_INJECT if _inject_path else ENV_ENTROPY}) but "
            f"NO site fired during the first forward. The pooling sites this build hooks "
            f"were never executed — this model takes a different branch (single_stream? a "
            f"different trunk?). Any delta measured from this run is 0 by construction."
        )
    if not _inject_path:
        return
    run_sites = set(_seen)
    cache_sites = set(_cache)
    if run_sites != cache_sites:
        raise RuntimeError(
            f"slice-basis site-set mismatch.\n"
            f"  this run has : {sorted(run_sites)}\n"
            f"  cache has    : {sorted(cache_sites)}\n"
            f"  only in run  : {sorted(run_sites - cache_sites)}\n"
            f"  only in cache: {sorted(cache_sites - run_sites)}\n"
            f"Refusing: layer indices must correspond one-to-one, or the trunk's layer L "
            f"would be handed layer L' 's basis."
        )
    missing = _inject_sites - set(_injected)
    if missing:
        raise RuntimeError(
            f"selected sites {sorted(missing)} were never substituted although they were "
            f"visited. Refusing: a partially applied injection is not the arm it claims."
        )
    logger.info(
        "🧬 slice-basis fired: %d sites visited, %d substituted (%s), mix=%.3f",
        len(run_sites), len(_injected), _sites_spec, _mix,
    )


def summary() -> dict:
    """Machine-readable receipt for the experiment driver's log."""
    if not enabled():
        return {"active": False}
    out = {
        "active": True,
        "mode": ("capture" if _capture_path else "inject" if _inject_path else "measure"),
        "path": _capture_path or _inject_path,
        "sites_seen": dict(sorted(_seen.items())),
        "n_forwards": max(_seen.values()) if _seen else 0,
    }
    if _inject_path:
        out["sites_substituted"] = dict(sorted(_injected.items()))
        out["sites_spec"] = _sites_spec
        out["reduce"] = _reduce
        out["mix"] = _mix
        out["cache_meta"] = _cache_meta
        out["cached_vs_own_rel_l2"] = {
            k: _diff_sum[k] / max(1, _diff_n[k]) for k in sorted(_diff_sum)
        }
    return out


def log_summary() -> None:
    if not enabled():
        return
    s = summary()
    logger.info("🧬 SLICE-BASIS RECEIPT %s", json.dumps(s, default=str)[:4000])
    if _inject_path:
        d = s["cached_vs_own_rel_l2"]
        patch = {k: v for k, v in d.items() if k.endswith(".patch")}
        if patch:
            v = list(patch.values())[0]
            # FLOOR, not zero. The trunk/patch tokens are computed under bfloat16 autocast
            # (relative precision 2^-8 = 3.9e-3) while the cache is accumulated in float64
            # and cast back on injection, so a SAME-operating-point reuse still reads
            # ~3.3e-3 — measured on case0 against a basis captured from case0 itself. The
            # threshold is therefore set above that quantisation floor, not at 0.
            verdict = ("SAME operating point (at the bf16 quantisation floor) — a result "
                       "from this run is CIRCULAR"
                       if v < 1e-2 else
                       "DIFFERENT operating point — genuine cross-point reuse")
            logger.info(
                "🧬 CIRCULARITY METER: boundary K/V cached-vs-own rel L2 = %.6f  ->  %s",
                v, verdict)


def entropy_report() -> dict:
    """Per-site mean entropies over the run's forwards, plus exp(H) effective slices."""
    out = {}
    for site, r in sorted(_ent.items()):
        n, n_occ, G = max(1, r["n"]), max(1, r["n_occ"]), r["G"]
        rec = {
            "n_forwards": r["n"],
            "G": G,
            "token_entropy_own": r["tok_own"] / n,
            "token_entropy_used": r["tok_out"] / n,
            "token_eff_slices_own": float(torch.tensor(r["tok_own"] / n).exp()),
            "token_eff_slices_used": float(torch.tensor(r["tok_out"] / n).exp()),
        }
        if r["n_occ"]:
            rec["occupancy_entropy"] = r["occ"] / n_occ
            rec["occupancy_eff_slices"] = float(torch.tensor(r["occ"] / n_occ).exp())
        rec["series_occ"] = list(r["series_occ"])
        rec["series_token_own"] = list(r["series_tok_own"])
        out[site] = rec
    return out


def _flush_entropy() -> None:
    if not _entropy_path or not _ent:
        return
    rep = entropy_report()
    trunk = [v for k, v in rep.items() if not k.endswith((".internal", ".patch"))]
    blob = {
        "format": "scano_slice_entropy/1",
        "label": os.environ.get(ENV_LABEL, ""),
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "mode": ("capture" if _capture_path else "inject" if _inject_path else "measure"),
        "inject_path": _inject_path,
        "inject_sites": sorted(_inject_sites),
        "pivot_env": {k: v for k, v in os.environ.items() if k.startswith("SCANO_PIVOT")},
        "sites": rep,
        # The headline: the 7 trunk AttnProcessor sites, which is where the diagnosis's
        # 2.699 -> 2.286 was measured and where the recovery was localised.
        "trunk_mean": {
            "token_entropy_own": sum(v["token_entropy_own"] for v in trunk) / max(1, len(trunk)),
            "token_entropy_used": sum(v["token_entropy_used"] for v in trunk) / max(1, len(trunk)),
            "occupancy_entropy": (sum(v["occupancy_entropy"] for v in trunk if "occupancy_entropy" in v)
                                  / max(1, sum("occupancy_entropy" in v for v in trunk))),
            "n_sites": len(trunk),
        },
    }
    with open(_entropy_path, "w") as f:
        json.dump(blob, f, indent=2)
    tm = blob["trunk_mean"]
    logger.info(
        "🧬 SLICE ENTROPY (trunk mean over %d sites, G=32): occupancy H=%.4f (%.2f eff "
        "slices) | token H own=%.4f -> used=%.4f (%.2f -> %.2f eff slices) -> %s",
        tm["n_sites"], tm["occupancy_entropy"], float(torch.tensor(tm["occupancy_entropy"]).exp()),
        tm["token_entropy_own"], tm["token_entropy_used"],
        float(torch.tensor(tm["token_entropy_own"]).exp()),
        float(torch.tensor(tm["token_entropy_used"]).exp()), _entropy_path,
    )


def flush() -> None:
    """Write the capture and/or the entropy readout. Registered with atexit AND callable
    explicitly; idempotent."""
    global _flushed
    if _flushed:
        return
    _flush_entropy()
    if not _capture_path or not _cap:
        _flushed = bool(_entropy_path)
        return
    _flushed = True
    meta = {
        "format": FORMAT,
        "label": os.environ.get(ENV_LABEL, ""),
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "argv": list(sys.argv),
        "cwd": os.getcwd(),
        "torch": torch.__version__,
        "n_forwards": max(r["count"] for r in _cap.values()),
        "n_sites": len(_cap),
        "pivot_env": {k: v for k, v in os.environ.items() if k.startswith("SCANO_PIVOT")},
        "dataset_path": next((sys.argv[i + 1] for i, a in enumerate(sys.argv)
                              if a == "--dataset-path" and i + 1 < len(sys.argv)), ""),
        "shapes": {k: list(r["shape"]) for k, r in _cap.items()},
        "dtypes": {k: r["dtype"] for k, r in _cap.items()},
    }
    counts = {k: r["count"] for k, r in _cap.items()}
    if len(set(counts.values())) != 1:
        raise RuntimeError(
            f"sites were visited an unequal number of times: {counts}. Every site is on "
            f"the same forward path, so this means some forwards took a different branch "
            f"and the per-site means would be over different draw sets."
        )
    torch.save({"format": FORMAT, "meta": meta, "sites": _cap}, _capture_path)
    logger.info(
        "🧬 slice-basis CAPTURED %d sites x %d forwards -> %s (%.1f KiB)\n     %s",
        len(_cap), meta["n_forwards"], _capture_path,
        os.path.getsize(_capture_path) / 1024.0, json.dumps(meta["shapes"]),
    )


def reset() -> None:
    """For tests: forget every resolved setting so env changes take effect."""
    global _ACTIVE, _capture_path, _inject_path, _entropy_path, _sites_spec, _reduce, _mix
    global _cache_meta, _inject_sites, _assigned, _flushed
    _ACTIVE = None
    _capture_path = _inject_path = _entropy_path = _sites_spec = ""
    _reduce, _mix = "mean", 1.0
    _ent.clear()
    _cap.clear()
    _cache.clear()
    _cache_meta = {}
    _inject_sites = set()
    _seen.clear()
    _injected.clear()
    _diff_sum.clear()
    _diff_n.clear()
    _assigned = 0
    _flushed = False
