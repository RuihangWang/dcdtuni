"""Domain decomposition of the coupled collocated Newton solver on several GPUs (docs/development/multi-gpu.md).

  partition  cuts an LPCNS case into per-part sub-cases (own cells + ``halo`` layers; cut faces as a last patch;
             geometry sliced bit for bit), by the OpenFOAM decomposition (``cell|proc``), METIS or RCB
  depth      measures the halo depth a residual needs (value / frozen / implicit JVP hop counts)
  comm       halo exchange, its transpose, reductions and gather (NCCL, or gloo through host memory)
  part       one part as a Newton problem on its own unknowns (residual, JVP, VJP, assembly, gauge, BC fix-ups)
  krylov     distributed restarted FGMRES (CGS2, one all-reduce per pass)
  newton     partitioned Newton-PTC with restricted additive Schwarz around the local camg / SIMPLE / LU
  adjoint    implicit differentiation of a partitioned solution
  launch     one process per GPU (``spawn``), card binding by index or UUID
  solve      the entry's ``--nproc`` path (host side) and one rank of it

Ported from, and adapted to the collocated residual of, Gen-FVGN-3D ``dev/odil:src/Parallel/``.
"""
