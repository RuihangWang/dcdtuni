"""Warp kernels of the conjugate heat transfer pieces (docs/research/2026-10-15-cht-heatsink.md section 4).

A separate Warp module, like ``buoyantBoussinesqpicoext_warp_transient``: the W1 module then compiles exactly as
before, so every one-region case keeps its bit-for-bit path (W3a found that a kernel added to the W1 unit changed the
CUDA compiler's inlining of the shared functions).

Two-source ghosts.  A conjugate interface ghost holds T_Gamma = (a_1 d_1 T_own + a_2 d_2 T_partner) / (a_1 d_1 +
a_2 d_2): it reads its own owner cell AND the partner side's owner cell, and a ghost whose other conditions read the
temperature ghost (fixedFluxPressure) reads both as well.  The ghost map of ``BuoyantWarpResidual`` is then
``q_g = A_g q_src + A2_g q_src2 + b_g``; ``ghost_kernel`` applies the first part and ``ghost2_kernel`` adds the second on
the two-source ghosts only, to every dual component (the offset b rides on the value, in ``ghost_kernel``).
"""
from __future__ import annotations

from typing import Any

import warp as wp

wp.config.quiet = True
wp.set_module_options({"enable_backward": False})


@wp.kernel
def ghost2_kernel(ghost: wp.array(dtype=wp.int32), src2: wp.array(dtype=wp.int32), A2: wp.array3d(dtype=wp.float64),
                  nf: int, q: wp.array2d(dtype=Any)):
    """q[g] += A2_g q[src2_g] on the ghosts that read a second interior cell (after ``ghost_kernel``)."""
    j = wp.tid()
    gi = ghost[j]
    si = src2[j]
    for c in range(nf):
        acc = q[si, 0] * A2[j, c, 0]
        for c2 in range(1, nf):
            acc = acc + q[si, c2] * A2[j, c, c2]
        q[gi, c] = q[gi, c] + acc
