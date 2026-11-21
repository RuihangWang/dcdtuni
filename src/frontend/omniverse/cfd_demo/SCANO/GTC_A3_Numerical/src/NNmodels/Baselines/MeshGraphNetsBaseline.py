import torch
import torch.nn as nn
from torch.utils.checkpoint import checkpoint
from torch_scatter import scatter_add


def _mlp(in_dim, hidden, out_dim, layernorm=True):
    layers = [nn.Linear(in_dim, hidden), nn.GELU(),
              nn.Linear(hidden, hidden), nn.GELU(),
              nn.Linear(hidden, out_dim)]
    if layernorm:
        layers.append(nn.LayerNorm(out_dim))
    return nn.Sequential(*layers)


class GNBlock(nn.Module):
    """Canonical MeshGraphNets message-passing block with residuals (Pfaff et al. 2021)."""

    def __init__(self, hidden):
        super().__init__()
        self.edge_mlp = _mlp(3 * hidden, hidden, hidden)
        self.node_mlp = _mlp(2 * hidden, hidden, hidden)

    def forward(self, x, edge_index, e):
        s, r = edge_index[0], edge_index[1]
        e_new = self.edge_mlp(torch.cat([e, x[s], x[r]], dim=-1)) + e
        agg = scatter_add(e_new, r, dim=0, dim_size=x.size(0))
        x_new = self.node_mlp(torch.cat([x, agg], dim=-1)) + x
        return x_new, e_new


class MeshGraphNetsBaseline(nn.Module):
    def __init__(self, node_input_size=11, edge_input_size=4, node_output_size=5,
                 hidden_size=256, message_passing_num=15, use_grad_checkpoint=True, **kwargs):
        super().__init__()
        self.node_encoder = _mlp(node_input_size, hidden_size, hidden_size)
        self.edge_encoder = _mlp(edge_input_size, hidden_size, hidden_size)
        self.blocks = nn.ModuleList([GNBlock(hidden_size) for _ in range(message_passing_num)])
        self.decoder = _mlp(hidden_size, hidden_size, node_output_size, layernorm=False)
        self.node_output_size = node_output_size
        # The baseline graph keeps ALL boundary faces (~95K) + sampled internal (~88K at sr=0.05),
        # so 15 blocks of intra-block activations OOM a 24GB GPU even at bf16. Checkpoint each block
        # during training to recompute those activations in backward (memory O(1 block) not O(15)).
        self.use_grad_checkpoint = bool(use_grad_checkpoint)

    def forward(self, x, edge_index, edge_attr, batch=None, mask_output_cell=None):
        h = self.node_encoder(x)
        e = self.edge_encoder(edge_attr)
        for blk in self.blocks:
            if self.use_grad_checkpoint and self.training:
                h, e = checkpoint(blk, h, edge_index, e, use_reentrant=False)
            else:
                h, e = blk(h, edge_index, e)
        return self.decoder(h).to(torch.float32)
