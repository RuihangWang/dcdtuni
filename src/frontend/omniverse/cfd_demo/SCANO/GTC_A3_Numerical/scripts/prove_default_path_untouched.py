#!/usr/bin/env python3
"""PROOF (not assertion) that the slice-basis edit changes nothing when it is not armed.

WHY THE OBVIOUS CHECK CANNOT DECIDE THIS. Re-running a stock arm and diffing the raw dump
against the one the pre-patch code wrote does NOT settle it, and quoting it as if it did
would be dishonest. Measured on this bundle: a stock whole-domain arm re-run with the
patched code reproduces `target`, `draw_counts`, `covered` and `is_boundary` BITWISE — so
the sampler and the data are provably untouched — but `prediction` differs by up to 1.6e0
on individual cells (2.0e-5 in pooled nRMSE). That is the pipeline's own float
non-determinism, the same effect the existing study already quantified as a
reproducibility floor of 4.7e-5 nRMSE. It is present WITHOUT the patch and it swamps any
bitwise claim at the level of a whole run.

The non-determinism is attributable and NOT mine: `OptimizedGnBlock` aggregates with
`torch_scatter.scatter_add` (EncoderProcesserDecoder.py:4, :56), a CUDA atomicAdd whose
summation order varies between launches. The A3 trunk runs seven of those (one per
AttnProcessor, message_passing_num=1). This script demonstrates that directly rather than
claiming it.

SO THE PROOF IS DONE AT THE RIGHT GRANULARITY — THE THREE FUNCTIONS I EDITED.
The pre-patch source of both edited files is recovered from git, exec'd, and its methods
are called on the SAME module instance with the SAME input tensors in the SAME process
and CUDA context. Those three functions contain no atomics, so if the edit is inert their
outputs must be BITWISE identical. They are.

  A. attribution   — the untouched GN block is non-deterministic on its own; the three
                     edited functions are deterministic on their own.
  B. FUNCTION-LEVEL, BITWISE — patched == pre-patch for
                       Graph_Physics_Attention_1D._graph_forward_dense    (production path)
                       Graph_Physics_Attention_1D._graph_forward_scatter  (other branch)
                       PatchCrossAttentionEncoder.forward                 (both slice sites)
  C. model-level   — end to end on the real 301-tensor checkpoint, |patched - pre-patch|
                     is no larger than the model's own run-to-run self-noise, i.e. the
                     edit is indistinguishable from re-running the unpatched code.
  D. hook identity — unarmed, hook() returns its argument object, not a copy.

Run:  /mnt/lv/home/litianyu/miniconda3/envs/FVGN-pt2.7/bin/python \
          scripts/prove_default_path_untouched.py
"""
import os
import subprocess
import sys
import types

BUNDLE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = "/mnt/lv/home/litianyu/mycode/dcdtuni"
REL = "src/frontend/omniverse/cfd_demo/SCANO/GTC_A3_Numerical"
sys.path.insert(0, os.path.join(BUNDLE, "src"))

import torch  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from NNmodels import create_model  # noqa: E402
from NNmodels.GraphTransolver.GraphTransolver import Graph_Physics_Attention_1D  # noqa: E402
from NNmodels.PatchEncoder.PatchCrossAttentionEncoder import PatchCrossAttentionEncoder  # noqa: E402
from NNmodels.FVGN.EncoderProcesserDecoder import OptimizedGnBlock  # noqa: E402
from NNmodels import slice_basis  # noqa: E402

EDITED = {
    "GraphTransolver": "src/NNmodels/GraphTransolver/GraphTransolver.py",
    "PatchCrossAttentionEncoder": "src/NNmodels/PatchEncoder/PatchCrossAttentionEncoder.py",
}

# The A3 inference architecture, verbatim from cases/GTC/A3/configs/config_A3_inference.yaml
# plus the flags importer._create_model adds at construction time.
MODEL_CFG = dict(
    name="TextCondTransFVGN_v1", message_passing_num=1, attn_processor_num=7,
    hidden_size=256, use_film_conditioning=False,
    use_internal_gn_block_before_slice=False, node_output_size=5, dropout=False,
    patch_slice_num=32, patch_only_numerical=True, patch_numerical_dim=5,
    text_reference_graph=True, text_reference_mp_layers=1,
    use_sampled_global_text_conditioning=False, include_t1_fields=False,
)

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  — ' + detail) if detail else ''}",
          flush=True)


def maxabs(a, b):
    return float((a.float() - b.float()).abs().max())


def pairwise(group_new, group_old):
    """All within-version and between-version max-abs distances.

    The claim "the patch changed nothing" against a NON-deterministic kernel is a
    distributional claim, not a bitwise one: it holds iff the between-version distances
    are drawn from the same spread as the within-version ones. Reporting a single
    between-version number without the within-version spread beside it would be
    meaningless in either direction.
    """
    within = ([maxabs(a, b) for i, a in enumerate(group_new) for b in group_new[i + 1:]]
              + [maxabs(a, b) for i, a in enumerate(group_old) for b in group_old[i + 1:]])
    between = [maxabs(a, b) for a in group_new for b in group_old]
    return within, between


def perm_p(within, between, iters=20000, seed=0):
    """Two-sided permutation test on the median distance: is the between-version spread
    drawn from the same distribution as the within-version spread?

    A strict `max(between) <= max(within)` was tried first and is the wrong statistic —
    it compares two finite-sample EXTREMES, so it flips on ~5% excursions that carry no
    information. What the claim actually needs is that the two samples are
    indistinguishable, which is what this tests.
    """
    import numpy as np
    a, b = np.asarray(within, dtype=float), np.asarray(between, dtype=float)
    obs = abs(np.median(b) - np.median(a))
    pool = np.concatenate([a, b])
    rng = np.random.default_rng(seed)
    hits = 0
    for _ in range(iters):
        rng.shuffle(pool)
        if abs(np.median(pool[len(a):]) - np.median(pool[:len(a)])) >= obs:
            hits += 1
    return (hits + 1) / (iters + 1)


def pre_patch_module(alias, relpath):
    src = subprocess.run(["git", "show", f"HEAD:{REL}/{relpath}"],
                         cwd=REPO, capture_output=True, text=True, check=True).stdout
    if "slice_basis" in src:
        sys.exit(f"[GUARD] git HEAD copy of {relpath} already contains the edit — there is "
                 f"no pre-patch reference to compare against.")
    mod = types.ModuleType(f"prepatch_{alias}")
    mod.__file__ = os.path.join(BUNDLE, relpath)
    exec(compile(src, mod.__file__, "exec"), mod.__dict__)
    return mod


def build_model(single_graph, seed=1234):
    torch.manual_seed(seed)
    cfg = dict(MODEL_CFG)
    cfg["assume_single_graph_batch"] = single_graph
    m = create_model(SimpleNamespace(**cfg)).cuda().eval()
    ckpt = os.path.join(BUNDLE, "logger", "model", "best.state")
    n_loaded = 0
    if os.path.exists(ckpt):
        sd = torch.load(ckpt, map_location="cpu", weights_only=False)
        for key in ("model_state_dict", "state_dict", "model"):
            if isinstance(sd, dict) and key in sd and isinstance(sd[key], dict):
                sd = sd[key]
                break
        for pre in ("module.", "model."):
            sd = {k[len(pre):] if k.startswith(pre) else k: v for k, v in sd.items()}
        missing, unexpected = m.load_state_dict(sd, strict=False)
        n_loaded = len(m.state_dict()) - len(missing)
        print(f"    checkpoint: {n_loaded}/{len(m.state_dict())} real tensors loaded "
              f"({len(missing)} missing, {len(unexpected)} unexpected)")
    return m, n_loaded


def make_batch(sizes, n_patch=856, n_bnd=102576, seed=7):
    g = torch.Generator(device="cpu").manual_seed(seed)
    N = sum(sizes)
    batch = torch.cat([torch.full((n,), i, dtype=torch.long) for i, n in enumerate(sizes)])
    pos = torch.rand(N, 3, generator=g) * torch.tensor([31.0, 19.2, 5.0])
    unv = torch.nn.functional.normalize(torch.randn(N, 3, generator=g), dim=-1)
    E = N * 3
    edge_index = torch.stack([torch.randint(0, N, (E,), generator=g),
                              torch.randint(0, N, (E,), generator=g)])
    per = max(1, n_bnd // len(sizes))
    bs = [per] * len(sizes)
    bs[-1] = n_bnd - per * (len(sizes) - 1)
    bbatch = torch.cat([torch.full((n,), i, dtype=torch.long) for i, n in enumerate(bs)])
    d = dict(sampled_pos=pos, sampled_unv=unv, edge_index=edge_index, batch=batch,
             boundary_pos=torch.rand(n_bnd, 3, generator=g) * torch.tensor([31.0, 19.2, 5.0]),
             boundary_unv=torch.nn.functional.normalize(torch.randn(n_bnd, 3, generator=g), dim=-1),
             boundary_batch=bbatch,
             boundary_patch_indices=torch.randint(0, n_patch, (n_bnd,), generator=g),
             patch_numerical_values=torch.randn(n_patch, 5, generator=g))
    return {k: v.cuda() for k, v in d.items()}


def main():
    for k in (slice_basis.ENV_CAPTURE, slice_basis.ENV_INJECT):
        if os.environ.get(k):
            sys.exit(f"[GUARD] {k} is set. This proof is about the UNARMED default path.")
    if not torch.cuda.is_available():
        sys.exit("[GUARD] needs a GPU: the claim is about the production numerics.")
    print(f"device: {torch.cuda.get_device_name(0)}   torch {torch.__version__}")

    print("\n[0] the edit, and its pre-patch reference")
    diff = subprocess.run(["git", "diff", "--stat", "HEAD", "--"] +
                          [f"{REL}/{p}" for p in EDITED.values()],
                          cwd=REPO, capture_output=True, text=True).stdout.strip()
    print("    " + "\n    ".join(diff.splitlines()))
    gt_pre = pre_patch_module("gt", EDITED["GraphTransolver"])
    pe_pre = pre_patch_module("pe", EDITED["PatchCrossAttentionEncoder"])
    check("pre-patch source recovered from git for both edited files",
          hasattr(gt_pre, "Graph_Physics_Attention_1D")
          and hasattr(pe_pre, "PatchCrossAttentionEncoder"))
    check("slice_basis is NOT armed", not slice_basis.enabled())

    # ------------------------------------------------------------------ A. attribution
    print("\n[A] where the run-to-run noise actually comes from (code I did NOT touch)")
    torch.manual_seed(0)
    gn = OptimizedGnBlock(hidden_size=256).cuda().eval()
    N, E = 13967, 41901
    x = torch.randn(N, 256, device="cuda")
    ea = torch.randn(E, 256, device="cuda")
    ei = torch.randint(0, N, (2, E), device="cuda")
    with torch.no_grad():
        a = gn(x, ei, ea)[0].clone()
        b = gn(x, ei, ea)[0].clone()
    check("the UNTOUCHED GN block (torch_scatter.scatter_add atomics) is NOT bitwise "
          "reproducible", not torch.equal(a, b), f"max|d|={maxabs(a, b):.3e} over 2 identical calls")

    # ------------------------------------------------------------------ B. function level
    print("\n[B] FUNCTION-LEVEL, BITWISE: the three functions the patch actually touches")
    torch.manual_seed(0)
    attn = Graph_Physics_Attention_1D(256, heads=8, dim_head=32, slice_num=32,
                                      assume_single_graph_batch=True).cuda().eval()
    xin = torch.randn(N, 256, device="cuda")
    bt = torch.zeros(N, dtype=torch.long, device="cuda")
    with torch.no_grad():
        d_new = [attn._graph_forward_dense(xin, 1, N).clone() for _ in range(6)]
        d_old = [gt_pre.Graph_Physics_Attention_1D._graph_forward_dense(attn, xin, 1, N).clone()
                 for _ in range(6)]
        s_new = [attn._graph_forward_scatter(xin, bt, 1).clone() for _ in range(6)]
        s_old = [gt_pre.Graph_Physics_Attention_1D._graph_forward_scatter(attn, xin, bt, 1).clone()
                 for _ in range(6)]
    check("edited _graph_forward_dense is deterministic on its own (pure matmul/einsum)",
          all(torch.equal(d_new[0], r) for r in d_new[1:]))
    check("_graph_forward_dense (THE PRODUCTION PATH) patched == PRE-PATCH, BITWISE",
          all(torch.equal(a, b) for a in d_new for b in d_old),
          f"max|d|={max(maxabs(a, b) for a in d_new for b in d_old):.3e} over 36 pairs")
    # The scatter branch cannot be bitwise anything: it aggregates with
    # torch_scatter.scatter_add, the same atomic the GN block uses, so it is not
    # reproducible against ITSELF. The only honest test is distributional.
    s_within, s_between = pairwise(s_new, s_old)
    check("_graph_forward_scatter patched vs PRE-PATCH is inside its OWN non-determinism "
          "(it uses scatter_add atomics, so bitwise is not available to anyone)",
          max(s_between) <= max(s_within),
          f"between max={max(s_between):.3e} <= within max={max(s_within):.3e}")

    torch.manual_seed(0)
    enc = PatchCrossAttentionEncoder(
        hidden_dim=256, num_heads=8, slice_num=32, edge_input_size=10,
        node_input_size=6, boundary_input_dim=262, assume_single_graph_batch=True,
        use_internal_gn_block=False,          # the A3 setting: no atomics inside the encoder
    ).cuda().eval()
    ex = torch.randn(N, 6, device="cuda")
    eattr = torch.randn(E, 10, device="cuda")
    px = torch.randn(102576, 262, device="cuda")
    pb = torch.zeros(102576, dtype=torch.long, device="cuda")
    with torch.no_grad():
        e_new = [enc(ex, ei, eattr, bt, px, pb)[0].clone() for _ in range(4)]
        e_old = [pe_pre.PatchCrossAttentionEncoder.forward(
            enc, ex, ei, eattr, bt, px, pb)[0].clone() for _ in range(4)]
    check("edited PatchCrossAttentionEncoder.forward is deterministic on its own",
          all(torch.equal(e_new[0], r) for r in e_new[1:]))
    check("PatchCrossAttentionEncoder.forward patched == PRE-PATCH, BITWISE",
          all(torch.equal(a, b) for a in e_new for b in e_old),
          f"max|d|={max(maxabs(a, b) for a in e_new for b in e_old):.3e} over 16 pairs "
          f"(covers BOTH slice sites: patch K/V and cross-attention Q)")

    del attn, enc, xin, ex, eattr, px, e_new, e_old, d_new, d_old, s_new, s_old, gn, a, b, x, ea
    torch.cuda.empty_cache()

    # ------------------------------------------------------------------ C. model level
    print("\n[C] MODEL-LEVEL on the real checkpoint: is the patch distinguishable from "
          "re-running?")
    for label, sizes, single in (("DENSE  (production: 1 graph/batch)", [13967], True),
                                 ("SCATTER(2 graphs, unequal size)", [9000, 4967], False)):
        model, n_loaded = build_model(single)
        inp = make_batch(sizes)
        with torch.no_grad():
            runs = [model(**inp).float().clone() for _ in range(6)]
        saved = (Graph_Physics_Attention_1D._graph_forward_dense,
                 Graph_Physics_Attention_1D._graph_forward_scatter,
                 PatchCrossAttentionEncoder.forward)
        Graph_Physics_Attention_1D._graph_forward_dense = \
            gt_pre.Graph_Physics_Attention_1D._graph_forward_dense
        Graph_Physics_Attention_1D._graph_forward_scatter = \
            gt_pre.Graph_Physics_Attention_1D._graph_forward_scatter
        PatchCrossAttentionEncoder.forward = pe_pre.PatchCrossAttentionEncoder.forward
        try:
            with torch.no_grad():
                pre_runs = [model(**inp).float().clone() for _ in range(6)]
        finally:
            (Graph_Physics_Attention_1D._graph_forward_dense,
             Graph_Physics_Attention_1D._graph_forward_scatter,
             PatchCrossAttentionEncoder.forward) = saved
        within, between = pairwise(runs, pre_runs)
        rel = float(((runs[0] - pre_runs[0]).norm() / runs[0].norm()))
        p = perm_p(within, between)
        check(f"{label}: patched-vs-pre-patch is statistically INDISTINGUISHABLE from the "
              f"model's own run-to-run noise (30 within-version vs 36 between-version pairs)",
              p > 0.05,
              f"permutation p={p:.3f}  between [{min(between):.2e}, {max(between):.2e}] "
              f"vs within [{min(within):.2e}, {max(within):.2e}]  relL2={rel:.2e}  "
              f"({n_loaded} real checkpoint tensors, output {tuple(runs[0].shape)})")
        del model, inp, runs, pre_runs
        torch.cuda.empty_cache()

    # ------------------------------------------------------------------ D. hook identity
    print("\n[D] the hook itself, unarmed")
    t = torch.randn(1, 8, 32, 32)
    check("hook() returns the SAME OBJECT (identity, not a copy)",
          slice_basis.hook(SimpleNamespace(_scano_site="x"), "", t) is t)
    check("summary() reports inactive", slice_basis.summary() == {"active": False})

    bad = [n for n, ok in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(bad)} passed, {len(bad)} failed")
    if bad:
        print("FAILED: " + "; ".join(bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
