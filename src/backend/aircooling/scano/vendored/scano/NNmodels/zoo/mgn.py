"""MeshGraphNets (Pfaff et al., ICLR 2021) as in Gen-FVGN-refine's single-stream baseline
(src/NNmodels/Baselines/MeshGraphNetsBaseline.py at roi-sampling@7d4a2b3, our lab's code): encoder MLPs for
nodes and edges, residual message-passing blocks (edge update from [e, x_s, x_r], sum aggregation at the
receiver, node update from [x, agg]), MLP decoder. Module names and creation order are refine's, so the same
seed gives the same parameters and the forward matches refine's class (tests/test_zoo_mgn.py, run where the
refine repository is available).

Framework interface: model(node_feat, ctx_w, bnd_feat, bnd_w, edges) with `edges` the mesh edges induced on the
passed points (cfg_common passes them to backbones with needs_edges = True). The edge features are built here
as refine's importer does ([relative position, distance]), from the coordinates in the first space_dim columns.
There is no global state: ctx_w and the boundary records are not used (boundary values reach the model only
through the node features of the boundary points that are passed, i.e. the single-stream route), and
predictions depend on the sample only through truncated neighbourhoods near the subgraph border.
Optional SCANO channel as a plug-in (zoo/channel_plugin.py; c95, d63): after every channel_every-th GN block the
node states read the boundary records, x += Read(LN x, lift(records), log w), the only route by which a point away
from the sampled boundary points sees the boundary. Created after every MGN parameter (the native parameters are
unchanged at the same seed). MGN has no global state, so the sample-independent state of SCANO does not apply.
Changes to refine: no gradient checkpointing, no bf16 path; batch of one case.
"""
from __future__ import annotations

import torch
import torch.nn as nn


def _mlp(in_dim, hidden, out_dim, layernorm=True):
    layers = [nn.Linear(in_dim, hidden), nn.GELU(), nn.Linear(hidden, hidden), nn.GELU(), nn.Linear(hidden, out_dim)]
    if layernorm:
        layers.append(nn.LayerNorm(out_dim))
    return nn.Sequential(*layers)


class GNBlock(nn.Module):
    def __init__(self, hidden):
        super().__init__()
        self.edge_mlp = _mlp(3 * hidden, hidden, hidden)
        self.node_mlp = _mlp(2 * hidden, hidden, hidden)

    def forward(self, x, edge_index, e):
        s, r = edge_index[0], edge_index[1]
        e_new = self.edge_mlp(torch.cat([e, x[s], x[r]], dim=-1)) + e
        agg = torch.zeros_like(x).index_add_(0, r, e_new)
        x_new = self.node_mlp(torch.cat([x, agg], dim=-1)) + x
        return x_new, e_new


def edge_features(pos, edge_index):
    """refine importer's MGN edge attributes (src/NNmodels/importer.py): pos[sender] - pos[receiver] and its length."""
    rel = pos[edge_index[0]] - pos[edge_index[1]]
    return torch.cat([rel, rel.norm(dim=-1, keepdim=True)], -1)


class MGN(nn.Module):
    needs_edges = True

    def __init__(self, node_in, bnd_in, width=64, depth=15, space_dim=2, out_dim=1, channel='none',
                 channel_measure='arc', heads=4, channel_every=1, record_slices=None):
        super().__init__()
        self.space_dim = space_dim
        self.node_encoder = _mlp(node_in, width, width)
        self.edge_encoder = _mlp(space_dim + 1, width, width)
        self.blocks = nn.ModuleList([GNBlock(width) for _ in range(depth)])
        self.decoder = _mlp(width, width, out_dim, layernorm=False)
        self.plugin = None                     # SCANO channel (c95), created after every native parameter
        if channel != 'none':
            from .channel_plugin import ChannelPlugin, plugin_layers
            self.plugin = ChannelPlugin(width, heads, bnd_in, plugin_layers(channel, depth, channel_every),
                                        channel_measure, record_slices)

    @property
    def needs_record_area(self):
        return self.plugin is not None and self.plugin.needs_record_area

    def forward(self, node_feat, ctx_w, bnd_feat=None, bnd_w=None, edges=None, bnd_area=None):
        if node_feat.shape[0] != 1:
            raise ValueError('MGN runs one case per forward')
        x = node_feat[0]
        h = self.node_encoder(x)
        e = self.edge_encoder(edge_features(x[:, :self.space_dim], edges))
        bt = log_w = None
        if self.plugin is not None:
            bt, log_w = self.plugin.tokens(bnd_feat, bnd_w, bnd_area)
        for i, blk in enumerate(self.blocks):
            h, e = blk(h, edges, e)
            if self.plugin is not None:
                h = self.plugin.read(i, h[None], bt, log_w)[0]
        out = self.decoder(h)
        return (out.squeeze(-1) if out.shape[-1] == 1 else out)[None]    # [1,N] for out_dim 1, else [1,N,out_dim]
