"""GINO (Geometry-Informed Neural Operator, NeurIPS 2023) as a DNE single-stream baseline.

Uses the local neuraloperator clone (Ref_code/neuraloperator) via sys.path. GINO is edge-free:
a GNO encoder (radius-graph kernel integral) scatters the point cloud onto a fixed regular latent
grid, a 3D FNO mixes on the grid, and a GNO decoder gathers the grid back to the query points.

The stock neuralop neighbor search materializes a dense torch.cdist(queries, data) matrix, which
OOMs at high sampling ratios (full-mesh cdist(R^3, ~370k) ~ 24 GB). We monkeypatch it with a
torch_cluster.radius implementation (O(E) memory) that returns the identical CSR structure, so GINO
trains and infers at every sampling ratio. Coordinates are bbox-normalized to the unit cube each
forward (GINO's default radii and latent grid live on [0,1]^3).
"""

import glob
import os
import sys
import torch
import torch.nn as nn

# neuraloperator is a vendored checkout that lives OUTSIDE this bundle, so an absolute
# literal here only ever worked on one machine. Resolve it instead: explicit override
# first, then the conventional sibling-repo layout under $HOME. Only the GINO baseline
# needs it — the demo's inference path never reaches this module (NNmodels/__init__.py
# imports it lazily, inside the branch for config.name == "GINO_baseline").
def _find_neuralop():
    p = os.environ.get("NEURALOP_PATH")
    if p:
        return p
    for c in glob.glob(os.path.expanduser("~/mycode/*/Ref_code/neuraloperator")):
        return c
    return ""


_NEURALOP = _find_neuralop()
if _NEURALOP and _NEURALOP not in sys.path:
    sys.path.insert(0, _NEURALOP)

import neuralop.layers.neighbor_search as _ns  # noqa: E402


# Neighbor cap bounds the GNO kernel-integral pair count (memory). Tunable by the wrapper.
_MAX_NUM_NEIGHBORS = 96


def _torch_cluster_neighbor_search(data, queries, radius, return_norm=False, max_num_neighbors=None):
    """Drop-in for neuralop.layers.neighbor_search.native_neighbor_search using torch_cluster.

    For each point in `queries`, return the indices (into `data`) of all points within `radius`,
    in CRS format: {neighbors_index [E], neighbors_row_splits [m+1]} (+ squared-distance weights).
    """
    from torch_cluster import radius as tc_radius
    cap = _MAX_NUM_NEIGHBORS if max_num_neighbors is None else int(max_num_neighbors)
    # tc_radius(x, y, r): for each point in y, neighbors in x. row0 -> y(query), row1 -> x(data).
    pair = tc_radius(data, queries, r=float(radius), max_num_neighbors=cap)
    q_idx, d_idx = pair[0], pair[1]
    order = torch.argsort(q_idx, stable=True)
    q_sorted = q_idx[order]
    nbr_index = d_idx[order].long()
    m = queries.shape[0]
    counts = torch.bincount(q_sorted, minlength=m)
    splits = torch.zeros(m + 1, dtype=torch.long, device=queries.device)
    splits[1:] = torch.cumsum(counts, dim=0)
    out = {"neighbors_index": nbr_index.to(queries.device),
           "neighbors_row_splits": splits}
    if return_norm:
        diff = data[nbr_index] - queries[q_sorted]
        out["weights"] = (diff ** 2).sum(-1)  # squared L2, matching native impl
    return out


# Patch the module global BEFORE any GINO instance builds its NeighborSearch modules.
_ns.native_neighbor_search = _torch_cluster_neighbor_search

from neuralop.models import GINO  # noqa: E402


class GINOBaseline(nn.Module):
    """GINO trunk (GNO-enc -> FNO latent grid -> GNO-dec) over sampled cpd_cell. Edge-free."""

    def __init__(self, node_input_size=11, node_output_size=5, latent_grid_res=20,
                 fno_n_modes=(12, 12, 12), fno_hidden_channels=48, fno_n_layers=4,
                 in_gno_radius=0.07, out_gno_radius=0.07, gno_transform_type="nonlinear",
                 projection_channel_ratio=2, max_num_neighbors=96,
                 infer_query_chunk=60000, **kwargs):
        super().__init__()
        global _MAX_NUM_NEIGHBORS
        _MAX_NUM_NEIGHBORS = int(max_num_neighbors)
        self.node_output_size = node_output_size
        self.infer_query_chunk = int(infer_query_chunk)
        R = int(latent_grid_res)
        raw_modes = fno_n_modes if isinstance(fno_n_modes, (list, tuple)) else (fno_n_modes,) * 3
        modes = tuple(int(min(m, R // 2)) for m in raw_modes)  # Nyquist guard: modes < R/2
        self.gino = GINO(
            in_channels=node_input_size, out_channels=node_output_size, gno_coord_dim=3,
            in_gno_radius=in_gno_radius, out_gno_radius=out_gno_radius,
            in_gno_transform_type=gno_transform_type, out_gno_transform_type=gno_transform_type,
            fno_n_modes=modes, fno_hidden_channels=fno_hidden_channels, fno_n_layers=fno_n_layers,
            projection_channel_ratio=projection_channel_ratio,
            fno_norm="group_norm", gno_use_open3d=False, gno_use_torch_scatter=True,
        )
        lin = torch.linspace(0, 1, R)
        grid = torch.stack(torch.meshgrid(lin, lin, lin, indexing="ij"), dim=-1)  # [R,R,R,3]
        self.register_buffer("latent_grid", grid.unsqueeze(0), persistent=False)   # [1,R,R,R,3]

    def _gino_call(self, feat, c, out_c):
        return self.gino(
            x=feat.unsqueeze(0),                                    # [1,n,node_input_size]
            input_geom=c.unsqueeze(0),                              # [1,n,3] scatter source (full cloud)
            latent_queries=self.latent_grid.to(c),                 # [1,R,R,R,3]
            output_queries=out_c.unsqueeze(0),                     # [1,q,3] read points
        ).squeeze(0)                                                # [q, node_output_size]

    def _run_one(self, feat, coords):
        lo, hi = coords.amin(0), coords.amax(0)
        c = (coords - lo) / (hi - lo).clamp_min(1e-6)               # bbox-normalize -> unit cube
        n = c.shape[0]
        chunk = self.infer_query_chunk
        if self.training or n <= chunk:
            return self._gino_call(feat, c, c)
        # Inference at large N: chunk the output-GNO queries (its integral is the memory driver).
        # input-GNO + FNO recompute per chunk from the FULL cloud (bounded, cheap) so the latent
        # grid still sees every input point; only the per-query decode is chunked.
        outs = [self._gino_call(feat, c, c[i:i + chunk]) for i in range(0, n, chunk)]
        return torch.cat(outs, dim=0)                               # [n, node_output_size]

    def forward(self, x, batch, edge_index=None, edge_attr=None, mask_output_cell=None):
        coords = x[:, :3]
        b = batch.squeeze(-1) if (batch is not None and batch.dim() > 1) else batch
        if b is None or int(b.max()) == 0:                          # single graph (common case)
            return self._run_one(x, coords).to(torch.float32)
        y = x.new_zeros(x.shape[0], self.node_output_size)          # multi-graph: per-graph GINO
        for g in b.unique(sorted=True):
            m = b == g
            y[m] = self._run_one(x[m], coords[m])
        return y.to(torch.float32)
