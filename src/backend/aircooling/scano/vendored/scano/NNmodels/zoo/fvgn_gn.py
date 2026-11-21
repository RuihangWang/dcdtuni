"""FVGN graph-net (GN) blocks of the original SCANO trunk, ported read-only from the AAAI-27 code (Gen-FVGN-refine,
src/NNmodels/FVGN/EncoderProcesserDecoder.py: build_mlp_from_num_layer, OptimizedEdgeBlock, OptimizedNodeBlock,
OptimizedGnBlock, the two-way edge lift of OptimizedEncoder; src/NNmodels/TransFVGN/TransFVGN_v2.py: AttnProcessor, which
runs `message_passing_num` GN blocks and then the Transolver block on x + GN(x)). The JUPITER framework's slice trunk
(zoo/slice_operator.py, from c37's OlbcOperator) had dropped them; model.message_passing restores them (c76).

Port notes (arithmetic unchanged; the references are in the AAAI code):
  * one edge per mesh face, edge_index = [senders, receivers] (FVGN's undirected edges; the two-way logic is inside the
    blocks), edge features [E, H] = [forward lift (H / 2), reverse lift (H / 2)];
  * edge block: s_i = sum over the neighbours j of x_j (both directions), e' = MLP([s_send, s_recv, e]) (3H -> H, LN);
  * node block: a_i = sum of the half-edge features of the faces of i (first half where i sends, second where it
    receives), m_i = mean over the neighbours j of a_j, x' = MLP([m_i, x_i]) (1.5H -> H, LN);
  * GN block: x + x', e + e' (fp32);
  * edge lift (OptimizedEncoder / PatchCrossAttentionEncoder.lift_features): MLP(raw) on the edge and on its reverse
    (raw with every column but the last negated: offsets flip, the length stays), concatenated;
  * scatter sums and means with torch index_add_ (torch_scatter.scatter_add / scatter_mean: a mean over no entry is 0).
The raw edge features are the offset sender - receiver and its length / EDGE_REF of the dataset (Main/gn_common.py),
since the DCTS cache stores no internal-face geometry (the AAAI Slab arm used 13 FVGN face features).
"""
from __future__ import annotations

from contextlib import contextmanager

import torch
from torch import nn
from torch.utils.checkpoint import checkpoint as _checkpoint


def build_mlp_from_num_layer(in_size, hidden_size, out_size, lay_norm=True, num_layer=2):
    """FVGN's MLP (drop_out off): Linear, GELU, (Linear, GELU) x (num_layer - 1), Linear, optional LayerNorm."""
    layers = [nn.Linear(in_size, hidden_size), nn.GELU()]
    for _ in range(num_layer - 1):
        layers += [nn.Linear(hidden_size, hidden_size), nn.GELU()]
    layers.append(nn.Linear(hidden_size, out_size))
    if lay_norm:
        layers.append(nn.LayerNorm(normalized_shape=out_size))
    return nn.Sequential(*layers)


def scatter_add(src, index, size):
    return torch.zeros((size,) + tuple(src.shape[1:]), dtype=src.dtype, device=src.device).index_add_(0, index, src)


def scatter_mean(src, index, size):
    total = scatter_add(src, index, size)
    count = torch.zeros(size, dtype=src.dtype, device=src.device).index_add_(
        0, index, torch.ones(index.numel(), dtype=src.dtype, device=src.device))
    return total / count.clamp_min(1)[:, None]


class EdgeBlock(nn.Module):
    def __init__(self, hidden_size):
        super().__init__()
        self.net = build_mlp_from_num_layer(3 * hidden_size, hidden_size, hidden_size)

    def forward(self, x, edge_index, edge_attr):
        s, r = edge_index
        indeg, outdeg = torch.cat([s, r]), torch.cat([r, s])
        nbr_sum = scatter_add(x[outdeg], indeg, x.size(0))
        return self.net(torch.cat([nbr_sum[s], nbr_sum[r], edge_attr], dim=1))


class NodeBlock(nn.Module):
    def __init__(self, hidden_size):
        super().__init__()
        self.net = build_mlp_from_num_layer(hidden_size + hidden_size // 2, hidden_size, hidden_size)

    def forward(self, x, edge_index, edge_attr):
        s, r = edge_index
        indeg, outdeg = torch.cat([s, r]), torch.cat([r, s])
        twoway = torch.cat(torch.chunk(edge_attr, 2, dim=-1), dim=0)
        agg = scatter_add(twoway, indeg, x.size(0))
        avg = scatter_mean(agg[outdeg], indeg, x.size(0))
        return self.net(torch.cat([avg, x], dim=1))


class GnBlock(nn.Module):
    """OptimizedGnBlock: node block (nb_module) and edge block (eb_module), created in that order as in FVGN."""

    def __init__(self, hidden_size):
        super().__init__()
        if hidden_size % 2:
            raise ValueError('the GN block needs an even width (two-way edge features)')
        self.nb_module = NodeBlock(hidden_size)
        self.eb_module = EdgeBlock(hidden_size)

    def forward(self, x, edge_index, edge_attr):
        e = self.eb_module(x, edge_index, edge_attr)
        return x + self.nb_module(x, edge_index, e), edge_attr + e


class EdgeLift(nn.Module):
    """The two-way edge lift: MLP(raw) and MLP(reverse raw) -> [E, hidden] (H / 2 each)."""

    def __init__(self, edge_in, hidden_size):
        super().__init__()
        self.eb_encoder = build_mlp_from_num_layer(edge_in, hidden_size, hidden_size // 2)

    def forward(self, raw):
        rev = torch.cat([-raw[:, :-1], raw[:, -1:]], 1)
        both = self.eb_encoder(torch.cat([raw, rev], 0))
        return torch.cat(torch.chunk(both, 2, dim=0), dim=1)


# Receptive field of one GN block: 3 mesh hops (the edge block reads the neighbour sums of both ends, the node block
# the mean over the neighbours of their incident edges), so m blocks before each of `depth` layers reach 3 m depth
# hops (tests/test_c76_trunk.py); a single-subgraph deployment needs that halo (gn_common.gn_halo, eval_*).
HOPS_PER_BLOCK = 3


class TrunkMessagePassing(nn.Module):
    """model.message_passing = m >= 1: the edge lift and m GN blocks before each of the trunk's `depth` layers
    (AttnProcessor). Applied to the query rows only (SliceOperator.forward): under a decoupled context the global
    state is computed by the context rows without GN, the same estimator in training (anchors) and at deployment."""

    EDGE_IN = 4                                    # offset (3) and length, / EDGE_REF of the dataset

    def __init__(self, width, depth, m):
        super().__init__()
        self.m, self.hops = m, HOPS_PER_BLOCK * m * depth          # receptive field of the GN path in mesh hops
        self.lift = EdgeLift(self.EDGE_IN, width)
        self.blocks = nn.ModuleList(nn.ModuleList(GnBlock(width) for _ in range(m)) for _ in range(depth))
        # activation checkpointing of the GN path (gn_checkpointing; c74 paired steps): a plain attribute, not a
        # parameter or buffer, so the state dict and the parameter creation are unchanged; False = as always
        self.checkpoint = False

    def lift_edges(self, raw):
        """The edge lift of the raw edge features (checkpointed under gn_checkpointing when gradients are recorded)."""
        if self.checkpoint and torch.is_grad_enabled():
            return _checkpoint(self.lift, raw, use_reentrant=False)
        return self.lift(raw)

    def layer(self, i, x, edge_index, e):
        """GN(x) of layer i on [Q, W] query states; returns (GN(x), e) with the blocks' own residuals. Under
        gn_checkpointing (with gradients recorded) the layer's activations are not kept: torch.utils.checkpoint
        stores its inputs and recomputes the blocks in the backward (the same arithmetic; no random numbers inside)."""
        if self.checkpoint and torch.is_grad_enabled():
            return _checkpoint(self._layer, i, x, edge_index, e, use_reentrant=False)
        return self._layer(i, x, edge_index, e)

    def _layer(self, i, x, edge_index, e):
        for blk in self.blocks[i]:
            x, e = blk(x, edge_index, e)
        return x, e


@contextmanager
def gn_checkpointing(model, on=True, layers=False):
    """Within the block, the GN path of `model` (SliceOperator with model.message_passing: the edge lift and the GN
    blocks of every layer) is activation-checkpointed: the forward keeps only the inputs of each checkpointed piece
    (per layer the query states [Q, W] and the edge features [E, W]) instead of its MLP activations over the E mesh
    edges, and the backward recomputes them. Used by train_cfg on the paired steps of dataset.pairs_dir runs (c74):
    the room's and its pair's graphs are alive together until the step's one backward, and at the c76 subgraph size
    one GN graph alone peaks near 49 GiB. Hosts with a boolean `checkpoint` attribute of their own (AeroGTO's mixer
    blocks, c86: an AeroGTO + channel run ran out of memory on a paired DCTS step) are checkpointed the same way.
    layers=True (with on; train.act_checkpoint = "always", c120) also checkpoints the slice tokens and every layer of a
    SliceOperator (its boolean `checkpoint_layers`). A no-op for on=False and for models with none of these (the
    attributes are restored on exit, also after an exception)."""
    targets = []
    if on:
        gn = getattr(model, 'gn', None)
        if gn is not None:
            targets.append((gn, 'checkpoint'))
        if isinstance(getattr(model, 'checkpoint', None), bool):
            targets.append((model, 'checkpoint'))
        if layers and isinstance(getattr(model, 'checkpoint_layers', None), bool):
            targets.append((model, 'checkpoint_layers'))
    prev = [getattr(t, k) for t, k in targets]
    for t, k in targets:
        setattr(t, k, True)
    try:
        yield
    finally:
        for (t, k), p in zip(targets, prev):
            setattr(t, k, p)
