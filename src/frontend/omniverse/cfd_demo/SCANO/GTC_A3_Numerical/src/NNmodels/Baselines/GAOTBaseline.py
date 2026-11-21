"""GAOT (Geometry-Aware Operator Transformer, NeurIPS 2025) as a DNE single-stream baseline.

Faithful port of camlab-ethz/GAOT (src/model/**, vendored under src/NNmodels/GAOT_src/ with the
unused omegaconf import stripped and rotary_embedding_torch made lazy). GAOT is edge-free: a
multiscale attentional GNO (MAGNO) encoder scatters the point cloud onto a regular latent grid, a
patch ViT/UViT processes the grid tokens, and a MAGNO decoder gathers the grid back to the query
points. It builds its own physical<->latent radius graph internally via torch_cluster.

Coordinates are per-subgraph min-max normalized to [-1,1] (the radius and latent grid assume that
box); the latent grid is built on [-1,1]^3 in 'ij' order to match the ViT patch reshape, and the
radius is set to the grid spacing so every query node is covered. The shape-keyed neighbor cache is
cleared each forward because the sampled subgraph size N varies step to step.
"""

from types import SimpleNamespace

import torch
import torch.nn as nn

from ..GAOT_src.gaot import GAOT
from ..GAOT_src.layers.magno import MAGNOConfig
from ..GAOT_src.layers.attn import TransformerConfig, AttentionConfig


class GAOTBaseline(nn.Module):
    """GAOT trunk (MAGNO enc -> ViT -> MAGNO dec) over sampled cpd_cell. Edge-free."""

    def __init__(self, node_input_size=11, node_output_size=5, latent_grid=(16, 16, 16),
                 patch_size=2, lifting_channels=36, magno_hidden=64, transformer_hidden=256,
                 radius=0.14, scales=(1.0, 2.0), n_layers=5, n_head=8,
                 transform_type="linear", **kwargs):
        super().__init__()
        self.node_output_size = node_output_size
        self.H, self.W, self.D = (int(v) for v in latent_grid)
        assert all(n % int(patch_size) == 0 for n in (self.H, self.W, self.D)), \
            "latent_grid dims must be divisible by patch_size"
        magno = MAGNOConfig(
            coord_dim=3, radius=float(radius), hidden_size=int(magno_hidden), mlp_layers=3,
            lifting_channels=int(lifting_channels), scales=list(scales),
            use_attention=True, attention_type="cosine", use_geoembed=True,
            embedding_method="statistical", transform_type=transform_type,
            neighbor_search_method="torch_cluster", neighbor_strategy="radius",
            use_torch_scatter=True, precompute_edges=False,
        )
        trans = TransformerConfig(
            patch_size=int(patch_size), hidden_size=int(transformer_hidden),
            num_layers=int(n_layers), positional_embedding="absolute",
            use_long_range_skip=True,
            attn_config=AttentionConfig(num_heads=int(n_head), num_kv_heads=int(n_head)),
        )
        cfg = SimpleNamespace(latent_tokens_size=[self.H, self.W, self.D],
                              args=SimpleNamespace(magno=magno, transformer=trans))
        self.trunk = GAOT(input_size=node_input_size, output_size=node_output_size, config=cfg)

    def _latent(self, device, dtype):
        ax = [torch.linspace(-1, 1, n, device=device, dtype=dtype) for n in (self.H, self.W, self.D)]
        return torch.stack(torch.meshgrid(*ax, indexing="ij"), dim=-1).reshape(-1, 3)  # [H*W*D, 3]

    def _run_one(self, xi):
        pos = xi[:, :3]
        lo, hi = pos.amin(0), pos.amax(0)
        coord = 2 * (pos - lo) / (hi - lo).clamp_min(1e-8) - 1        # [-1,1] -> radius/grid valid
        # N varies per step -> shape-keyed neighbor cache would grow unbounded; clear it.
        self.trunk.encoder.neighbor_cache.clear()
        self.trunk.decoder.neighbor_cache.clear()
        pred = self.trunk(latent_tokens_coord=self._latent(xi.device, xi.dtype),
                          xcoord=coord, pndata=xi.unsqueeze(0), query_coord=coord)  # [1,N,5]
        return pred.squeeze(0)

    def forward(self, x, batch, edge_index=None, edge_attr=None, mask_output_cell=None):
        b = batch.squeeze(-1) if (batch is not None and batch.dim() > 1) else batch
        if b is None or int(b.max()) == 0:
            return self._run_one(x).to(torch.float32)
        outs = []
        for g in b.unique(sorted=True):                              # batch_size>1: per-graph pass
            outs.append(self._run_one(x[b == g]))
        return torch.cat(outs, dim=0).to(torch.float32)
