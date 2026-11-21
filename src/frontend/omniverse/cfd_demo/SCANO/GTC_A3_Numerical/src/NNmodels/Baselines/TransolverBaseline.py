import os
import torch
import torch.nn as nn
import torch.utils.checkpoint
from ..GraphTransolver.GraphTransolver import Transolver_block, MLP


class TransolverBaseline(nn.Module):
    """Faithful graph Transolver (ICML 2024) over the combined cpd_cell node set. No edges."""

    def __init__(self, node_input_size=11, node_output_size=5, hidden_size=256,
                 n_layers=8, n_head=8, slice_num=32, mlp_ratio=2, dropout=False,
                 assume_single_graph_batch=False, **kwargs):
        super().__init__()
        drop = 0.1 if dropout else 0.0
        self.preprocess = MLP(node_input_size, hidden_size * 2, hidden_size,
                              n_layers=0, res=False, act="gelu")
        self.placeholder = nn.Parameter((1.0 / hidden_size) * torch.rand(hidden_size))
        self.blocks = nn.ModuleList([
            Transolver_block(num_heads=n_head, hidden_dim=hidden_size, dropout=drop,
                             act="gelu", mlp_ratio=mlp_ratio, slice_num=slice_num,
                             assume_single_graph_batch=assume_single_graph_batch)
            for _ in range(n_layers)
        ])
        self.decoder = nn.Sequential(nn.LayerNorm(hidden_size),
                                     nn.Linear(hidden_size, node_output_size))
        self.node_output_size = node_output_size
        # Config-gated trunk gradient checkpointing (also overridable via env GTC_GRAD_CKPT=1).
        self.use_gradient_checkpointing = bool(kwargs.get("use_gradient_checkpointing", False))

    def forward(self, x, batch, edge_index=None, edge_attr=None, mask_output_cell=None):
        b = batch.squeeze(-1) if batch.dim() > 1 else batch
        fx = self.preprocess(x) + self.placeholder[None, :]
        use_ckpt = self.use_gradient_checkpointing or bool(os.environ.get("GTC_GRAD_CKPT", ""))
        for blk in self.blocks:
            if use_ckpt and self.training and torch.is_grad_enabled():
                fx = torch.utils.checkpoint.checkpoint(blk, fx, b, use_reentrant=False)
            else:
                fx = blk(fx, b)
        return self.decoder(fx).to(torch.float32)
