"""Boundary records per patch (boundary.records = "patch"; J2 of docs/dcts_jupiter_design_2026-10-01.md).

On DCTS a case has about half a million boundary faces but only hundreds of boundary patches, and per-layer
per-record attention over every face is not affordable at full-mesh deployment. A patch record is the area integral
of the face records over the patch divided by the patch area, and its weight is the patch area:

    feat_p = sum_{f in p} w_f feat_f / sum_{f in p} w_f,        w_p = sum_{f in p} w_f,

so the weights of the patch records integrate the boundary exactly as the face weights did (the "area-integrated
aggregation" of the story), whatever the face resolution of the mesh.

Which columns are means: ALL of them, with no special treatment of any column: every column of the patch record
is the area mean of that column of the face records, as stored. For the record layouts in use:
  position (x, y, z)          the area-weighted centroid of the patch;
  unit normal (nx, ny, nz)    the area-weighted mean normal, NOT renormalised: the normal itself on a planar patch,
                              shorter than 1 on a curved one;
  one-hot BC type             the type when all faces of the patch share it (the usual case), else the area
                              fraction of each type;
  alpha                       constant on an R0-S patch, so the patch value is that constant;
  BC value                    the area mean of the STORED (normalised) value column. On R0-S the raw value is linear
                              in the position on a patch (r0s_common.record_arrays), and the column holds g / d_std
                              for Dirichlet and u_inf / r_std for Robin records (linear, so their area mean is the
                              value at the area centroid) but slog1p(q) / n_std for Neumann records: a nonlinear
                              transform of the flux q, so the patch value is the area mean of slog1p(q) / n_std,
                              which is neither slog1p of the patch-mean flux nor the value at the centroid (insulated
                              faces are Neumann records with value 0). On DCTS it will be the area mean of whatever
                              the cache stores per face.
The routes then weight the patch records by w_p exactly as they weighted face records by w_f (arc/area measure).

The sums run in float64 and the result is cast back to the input dtype. The framework (cfg_common.patch_records)
calls this once per case on the CPU and caches the result on the device, so the records do not depend on the
summation order of CUDA atomics.
"""
from __future__ import annotations

import torch


def aggregate_records_by_patch(feat, weight, patch_id):
    """feat [NB, F], weight [NB] > 0, patch_id [NB] (integers; records with the same id form one patch).

    Returns (feat_p [P, F], weight_p [P], ids [P]) with one row per distinct id in increasing id order: the
    weight-averaged feature columns and the summed weight (see the module docstring)."""
    if feat.dim() != 2 or weight.shape != feat.shape[:1] or patch_id.shape != feat.shape[:1]:
        raise ValueError(f'expected feat [NB, F], weight [NB], patch_id [NB]; got {tuple(feat.shape)}, '
                         f'{tuple(weight.shape)}, {tuple(patch_id.shape)}')
    if patch_id.dtype.is_floating_point or patch_id.dtype == torch.bool:
        raise ValueError(f'patch ids must be integers, got {patch_id.dtype}')
    if weight.numel() == 0:
        raise ValueError('no records to aggregate')
    if not bool(torch.isfinite(weight).all()) or not bool((weight > 0).all()):
        raise ValueError('record weights must be positive and finite')
    ids, inv = torch.unique(patch_id, sorted=True, return_inverse=True)
    w64 = weight.to(torch.float64)
    wsum = torch.zeros(ids.numel(), dtype=torch.float64, device=feat.device).index_add_(0, inv, w64)
    fsum = torch.zeros(ids.numel(), feat.shape[1], dtype=torch.float64, device=feat.device)
    fsum.index_add_(0, inv, feat.to(torch.float64) * w64[:, None])
    return (fsum / wsum[:, None]).to(feat.dtype), wsum.to(weight.dtype), ids
