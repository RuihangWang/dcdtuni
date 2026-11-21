#!/usr/bin/env python3
"""Unit tests for the slice-basis capture/injection hook — CPU only, no dataset, no GPU.

Covers the four things that must be true before any number from a real arm is worth
reading:
  1. Default path untouched. Neither env var set -> hook() returns the SAME OBJECT.
  2. Capture is non-perturbing, and the reduction is the mean of what was pooled.
  3. Injection substitutes the pooled TOKEN and nothing else — the per-node assignment
     weights and the scatter-back stay the run's own (checked structurally by running a
     real Graph_Physics_Attention_1D with a node count the cache could not possibly have
     come from: a broadcast-happy implementation would crash or silently agree).
  4. Every mismatch REFUSES loudly: wrong slice count, wrong head count, wrong feature
     dim, wrong batch, unknown site, missing site, missing file, both vars set.

Run:  /mnt/lv/home/litianyu/miniconda3/envs/FVGN-pt2.7/bin/python scripts/test_slice_basis.py
"""
import os
import sys
import tempfile
import traceback

BUNDLE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BUNDLE, "src"))

import torch  # noqa: E402
import torch.nn as nn  # noqa: E402

from NNmodels import slice_basis  # noqa: E402
from NNmodels.GraphTransolver.GraphTransolver import Graph_Physics_Attention_1D  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  — ' + detail) if detail else ''}")


def expect_raises(name, fn, needle=""):
    try:
        fn()
    except Exception as e:
        ok = needle.lower() in str(e).lower() if needle else True
        check(name, ok, f"raised {type(e).__name__}: {str(e)[:110]}")
        return
    check(name, False, "did NOT raise")


def clear_env():
    for k in (slice_basis.ENV_CAPTURE, slice_basis.ENV_INJECT, slice_basis.ENV_SITES,
              slice_basis.ENV_REDUCE, slice_basis.ENV_MIX, slice_basis.ENV_LABEL,
              slice_basis.ENV_ENTROPY):
        os.environ.pop(k, None)
    slice_basis.reset()


class TinyModel(nn.Module):
    """One trunk attention layer, named exactly as it is in the real model."""

    def __init__(self, dim=64, heads=8, slices=32):
        super().__init__()
        self.processpr_list = nn.ModuleList([nn.Module()])
        self.processpr_list[0].TransBlock = nn.Module()
        self.processpr_list[0].TransBlock.Attn = Graph_Physics_Attention_1D(
            dim, heads=heads, dim_head=dim // heads, slice_num=slices,
            assume_single_graph_batch=True)

    def forward(self, x):
        attn = self.processpr_list[0].TransBlock.Attn
        return attn.graph_forward(x, torch.zeros(x.size(0), 1, dtype=torch.long))


SITE = "processpr_list.0.TransBlock.Attn"


def main():
    torch.manual_seed(0)
    tmp = tempfile.mkdtemp(prefix="slice_basis_test_")

    # ---------------------------------------------------------------- 1. default path
    print("\n[1] default path: neither env var set")
    clear_env()
    m = TinyModel()
    n_tagged = slice_basis.assign_site_ids(m)
    check("assign_site_ids is a no-op when disabled", n_tagged == 0)
    check("no _scano_site attribute was written",
          not hasattr(m.processpr_list[0].TransBlock.Attn, "_scano_site"))
    tok = torch.randn(1, 8, 32, 8)
    check("hook() returns the SAME OBJECT (identity, not equality)",
          slice_basis.hook(m.processpr_list[0].TransBlock.Attn, "", tok) is tok)
    x = torch.randn(500, 64)
    torch.manual_seed(1)
    y_ref = m(x)
    torch.manual_seed(1)
    y_again = m(x)
    check("stock forward is deterministic (baseline for the bitwise checks)",
          torch.equal(y_ref, y_again))

    # ---------------------------------------------------------------- 2. capture
    print("\n[2] capture: non-perturbing, and the mean is the mean")
    clear_env()
    cap = os.path.join(tmp, "basis.pt")
    os.environ[slice_basis.ENV_CAPTURE] = cap
    os.environ[slice_basis.ENV_LABEL] = "unit-test"
    slice_basis.reset()
    check("capturing() is True", slice_basis.capturing())
    n_tagged = slice_basis.assign_site_ids(m)
    check("assign_site_ids tagged the one attention layer", n_tagged == 1)
    check("site key is the qualified module name",
          m.processpr_list[0].TransBlock.Attn._scano_site == SITE)

    y_cap = m(x)
    check("capture does not change the forward (bitwise)", torch.equal(y_cap, y_ref))
    x2 = torch.randn(700, 64)
    m(x2)
    slice_basis.assert_fired()
    slice_basis.flush()
    check("cache file written", os.path.exists(cap))
    blob = torch.load(cap, map_location="cpu", weights_only=False)
    rec = blob["sites"][SITE]
    check("captured 2 forwards at 1 site",
          rec["count"] == 2 and len(blob["sites"]) == 1 and blob["meta"]["n_forwards"] == 2)
    check("token shape is [B, heads, slices, dim_head] = [1, 8, 32, 8]",
          tuple(rec["shape"]) == (1, 8, 32, 8), str(rec["shape"]))
    mean = (rec["sum"] / rec["count"]).float()
    check("mean == (first + last) / 2",
          torch.allclose(mean, (rec["first"].float() + rec["last"].float()) / 2, atol=1e-6))
    check("first != last (the two draws really did pool different node sets)",
          not torch.allclose(rec["first"].float(), rec["last"].float()))
    check("provenance stamped", blob["meta"]["label"] == "unit-test")

    # ---------------------------------------------------------------- 3. injection
    print("\n[3] injection: the token is substituted, the addressing is not")
    clear_env()
    os.environ[slice_basis.ENV_INJECT] = cap
    slice_basis.reset()
    check("injecting() is True", slice_basis.injecting())
    slice_basis.assign_site_ids(m)
    # A node count neither capture used. The cached token has NO node axis, so this must
    # run; if anything node-shaped were being reused it would blow up here.
    x3 = torch.randn(123, 64)
    y_inj = m(x3)
    slice_basis.assert_fired()
    check("injected forward runs at an unseen node count (token has no node axis)",
          y_inj.shape == (123, 64))
    clear_env()
    slice_basis.reset()
    y_stock = m(x3)
    check("injection actually changed the output", not torch.allclose(y_inj, y_stock))

    # mix=0 must be an exact no-op, which proves the substitution is the ONLY difference
    clear_env()
    os.environ[slice_basis.ENV_INJECT] = cap
    os.environ[slice_basis.ENV_MIX] = "0"
    slice_basis.reset()
    slice_basis.assign_site_ids(m)
    y_mix0 = m(x3)
    check("mix=0 reproduces the stock forward bitwise", torch.equal(y_mix0, y_stock))
    s = slice_basis.summary()
    check("summary reports the cached-vs-own relative L2",
          s["cached_vs_own_rel_l2"][SITE] > 0.0,
          f"{s['cached_vs_own_rel_l2'][SITE]:.4f}")

    # the substituted token really is the cached one
    clear_env()
    os.environ[slice_basis.ENV_INJECT] = cap
    slice_basis.reset()
    slice_basis.assign_site_ids(m)
    seen = {}
    attn = m.processpr_list[0].TransBlock.Attn

    def _spy(_mod, inp):
        seen["token"] = inp[0].detach().clone()

    h = attn.to_qkv.register_forward_pre_hook(_spy)
    m(x3)
    h.remove()
    check("the token handed to to_qkv IS the cached mean",
          torch.allclose(seen["token"].float(), mean, atol=1e-6))

    # ---------------------------------------------------------------- 4. refusals
    print("\n[4] guards: every mismatch refuses loudly")
    for label, kw in (("slice count", dict(slices=64)),
                      ("head count", dict(heads=4)),
                      ("feature dim", dict(dim=128))):
        clear_env()
        os.environ[slice_basis.ENV_INJECT] = cap
        slice_basis.reset()
        m2 = TinyModel(**{**dict(dim=64, heads=8, slices=32), **kw})
        slice_basis.assign_site_ids(m2)
        expect_raises(f"refuses a cache with the wrong {label}",
                      lambda mm=m2: mm(torch.randn(200, mm.processpr_list[0]
                                                   .TransBlock.Attn.in_project_x.in_features)),
                      "mismatch")

    clear_env()
    os.environ[slice_basis.ENV_INJECT] = cap
    slice_basis.reset()

    class Renamed(TinyModel):
        pass

    m3 = Renamed()
    # rename the layer -> a site key the cache has never heard of
    m3.processpr_list[0].TransBlock.Attn._scano_site = "processpr_list.9.TransBlock.Attn"
    slice_basis._ACTIVE = True
    expect_raises("refuses an unknown site key", lambda: m3(torch.randn(200, 64)), "absent")

    clear_env()
    os.environ[slice_basis.ENV_INJECT] = cap
    slice_basis.reset()
    slice_basis.assign_site_ids(m)
    expect_raises("assert_fired refuses before any forward",
                  slice_basis.assert_fired, "no site fired")

    clear_env()
    os.environ[slice_basis.ENV_INJECT] = os.path.join(tmp, "nope.pt")
    slice_basis.reset()
    expect_raises("refuses a missing cache file",
                  lambda: slice_basis.hook(m.processpr_list[0].TransBlock.Attn, "", tok),
                  "does not exist")

    clear_env()
    os.environ[slice_basis.ENV_CAPTURE] = cap
    os.environ[slice_basis.ENV_INJECT] = cap
    slice_basis.reset()
    expect_raises("refuses capture and inject in the same process",
                  slice_basis.enabled, "both set")

    clear_env()
    os.environ[slice_basis.ENV_INJECT] = cap
    os.environ[slice_basis.ENV_SITES] = "no.such.site"
    slice_basis.reset()
    expect_raises("refuses an unknown name in SCANO_BASIS_SITES",
                  slice_basis.enabled, "not in the cache")

    clear_env()
    os.environ[slice_basis.ENV_INJECT] = cap
    os.environ[slice_basis.ENV_MIX] = "1.5"
    slice_basis.reset()
    expect_raises("refuses an out-of-range mix", slice_basis.enabled, "[0, 1]")

    clear_env()
    os.environ[slice_basis.ENV_CAPTURE] = "off"
    slice_basis.reset()
    expect_raises("refuses 'off' as a path (it is a filename, not a switch)",
                  slice_basis.enabled, "file path")

    # site-set mismatch: cache carries a layer this model does not have
    clear_env()
    two = os.path.join(tmp, "two.pt")
    blob2 = torch.load(cap, map_location="cpu", weights_only=False)
    blob2["sites"]["processpr_list.1.TransBlock.Attn"] = dict(blob2["sites"][SITE])
    torch.save(blob2, two)
    os.environ[slice_basis.ENV_INJECT] = two
    slice_basis.reset()
    slice_basis.assign_site_ids(m)
    m(x3)
    expect_raises("refuses a cache with MORE layers than the model",
                  slice_basis.assert_fired, "site-set mismatch")

    clear_env()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED: " + ", ".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
