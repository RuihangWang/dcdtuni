"""Scaled dot-product attention with independent query rows, computed in query chunks when the score matrix
would be too large to materialise.

torch's fused kernels (flash, memory-efficient, cuDNN) never materialise the scores, but they are not always
eligible: a head dimension that is not a multiple of 8 (for example width 148 with 4 heads) falls back to the math
path. On the R0-S 64^3 deployment mesh one per-record boundary attention (about 287k queries x 25k records x 4 heads)
then asks for about 105 GB. The rows of softmax(Q K^T * scale + bias) V are independent whenever the bias does not
depend on the query, so splitting the queries gives the same result; below the threshold the call is exactly
the one it replaces.

On CUDA the call is split only when no fused kernel is eligible (PLAN section 3.7 item 4): a fused kernel holds a
[B,1,1,M] bias without expanding it (GH200, torch 2.10: the memory-efficient kernel's peak is the same with and
without the bias, while the math path takes about 1 GB for 8192 x 2048 x 4 heads; tools/probe_sdpa.py), and many
query chunks only cost kernel launches. On the CPU, and when torch cannot tell, the size threshold alone decides.
"""
from __future__ import annotations

import torch
import torch.nn.functional as F

MAX_SCORE_ELEMENTS = 2 ** 30      # batch x heads x queries x keys above which the queries are split


def fused_kernel_eligible(q, k, v, attn_mask=None):
    """True when torch's dispatcher may take a fused CUDA kernel (flash, memory-efficient or cuDNN, each only if
    enabled) for this call; False on the CPU and when the check is not available in this torch."""
    if not q.is_cuda:
        return False
    try:
        from torch.backends import cuda as tbc
        try:
            params = tbc.SDPAParams(q, k, v, attn_mask, 0.0, False, False)     # torch >= 2.5 (enable_gqa)
        except TypeError:
            params = tbc.SDPAParams(q, k, v, attn_mask, 0.0, False)
        checks = [(tbc.flash_sdp_enabled, tbc.can_use_flash_attention),
                  (tbc.mem_efficient_sdp_enabled, tbc.can_use_efficient_attention)]
        if hasattr(tbc, 'can_use_cudnn_attention'):
            checks.append((tbc.cudnn_sdp_enabled, tbc.can_use_cudnn_attention))
        return any(enabled() and can_use(params, False) for enabled, can_use in checks)
    except (AttributeError, ImportError, RuntimeError, TypeError):
        return False


def sdpa(q, k, v, attn_mask=None, scale=None, max_elements=None):
    """F.scaled_dot_product_attention(q, k, v, attn_mask, scale) for q [B,H,N,D], k/v [B,H,M,D]; an attn_mask
    must broadcast over the queries (size 1 on dim -2) when the call is split."""
    limit = MAX_SCORE_ELEMENTS if max_elements is None else max_elements
    b, h, n, _ = q.shape
    m = k.shape[-2]
    if b * h * n * m <= limit or fused_kernel_eligible(q, k, v, attn_mask):
        return F.scaled_dot_product_attention(q, k, v, attn_mask=attn_mask, scale=scale)
    if attn_mask is not None and attn_mask.shape[-2] != 1:
        raise ValueError('chunked attention needs an attn_mask that does not depend on the query (size 1 on dim -2)')
    step = max(1, limit // (b * h * m))
    return torch.cat([F.scaled_dot_product_attention(q[:, :, i:i + step], k, v, attn_mask=attn_mask, scale=scale)
                      for i in range(0, n, step)], dim=2)
