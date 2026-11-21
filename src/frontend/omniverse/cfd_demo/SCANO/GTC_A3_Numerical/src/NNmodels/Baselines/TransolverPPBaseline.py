import torch
import torch.nn as nn
import torch.nn.functional as F
from torch_scatter import scatter_add
from einops import rearrange
from ..GraphTransolver.GraphTransolver import _get_dense_batch_info, MLP


def gumbel_softmax(logits, tau=1.0):
    # NOTE: torch.rand is fine at runtime.
    u = torch.rand_like(logits)
    g = -torch.log(-torch.log(u + 1e-8) + 1e-8)
    return F.softmax((logits + g) / tau, dim=-1)


class Graph_Physics_Attention_1D_Eidetic(nn.Module):
    """Transolver++ Eidetic slice attention adapted to graphs. Cross-GPU all_reduce dropped."""

    def __init__(self, dim, heads=8, dim_head=64, dropout=0.0, slice_num=64,
                 assume_single_graph_batch=False):
        super().__init__()
        inner = dim_head * heads
        self.dim_head, self.heads, self.scale = dim_head, heads, dim_head ** -0.5
        self.slice_num = slice_num
        self.assume_single_graph_batch = assume_single_graph_batch
        self.softmax = nn.Softmax(dim=-1)
        self.bias = nn.Parameter(torch.ones([1, heads, 1, 1]) * 0.5)
        self.proj_temperature = nn.Sequential(
            nn.Linear(dim_head, slice_num), nn.GELU(), nn.Linear(slice_num, 1), nn.GELU())
        self.in_project_x = nn.Linear(dim, inner)
        self.in_project_slice = nn.Linear(dim_head, slice_num)
        torch.nn.init.orthogonal_(self.in_project_slice.weight)
        self.to_q = nn.Linear(dim_head, dim_head, bias=False)
        self.to_k = nn.Linear(dim_head, dim_head, bias=False)
        self.to_v = nn.Linear(dim_head, dim_head, bias=False)
        self.to_out = nn.Sequential(nn.Linear(inner, dim), nn.Dropout(dropout))

    def _attend(self, slice_token):
        q, k, v = self.to_q(slice_token), self.to_k(slice_token), self.to_v(slice_token)
        return F.scaled_dot_product_attention(q, k, v)

    def _dense(self, x, B, N):
        x_dense = x.reshape(B, N, -1)
        x_mid = self.in_project_x(x_dense).reshape(B, N, self.heads, self.dim_head)
        x_mid = x_mid.permute(0, 2, 1, 3).contiguous()                     # B H N C
        temp = torch.clamp(self.proj_temperature(x_mid) + self.bias, min=0.01)  # B H N 1
        slice_weights = gumbel_softmax(self.in_project_slice(x_mid), temp)      # B H N G
        slice_norm = slice_weights.sum(2)                                       # B H G
        slice_token = torch.einsum("bhnc,bhng->bhgc", x_mid, slice_weights)     # B H G C
        slice_token = slice_token / ((slice_norm + 1e-5)[..., None])
        out_slice = self._attend(slice_token)                                   # B H G C
        out_x = torch.einsum("bhgc,bhng->bhnc", out_slice, slice_weights)
        out_x = rearrange(out_x, "b h n c -> (b n) (h c)")
        return self.to_out(out_x)

    def _scatter(self, x, batch, batch_size):
        total = x.size(0)
        x_mid = self.in_project_x(x).view(total, self.heads, self.dim_head)      # N H C
        bias = self.bias.view(1, self.heads, 1)                                  # broadcast to N H 1
        temp = torch.clamp(self.proj_temperature(x_mid) + bias, min=0.01)        # N H 1
        slice_weights = gumbel_softmax(self.in_project_slice(x_mid), temp)       # N H G
        slice_norm = scatter_add(slice_weights, batch, dim=0, dim_size=batch_size)   # B H G
        slice_token = scatter_add(x_mid.unsqueeze(-2) * slice_weights.unsqueeze(-1),
                                  batch, dim=0, dim_size=batch_size)             # B H G C
        slice_token = slice_token / ((slice_norm + 1e-5).unsqueeze(-1))
        out_slice = self._attend(slice_token)                                   # B H G C
        out_slice_n = out_slice[batch]                                          # N H G C
        out_x = torch.sum(out_slice_n * slice_weights.unsqueeze(-1), dim=-2)    # N H C
        out_x = rearrange(out_x, "n h c -> n (h c)")
        return self.to_out(out_x)

    def graph_forward(self, x, batch):
        total = x.size(0)
        batch, batch_size, npg = _get_dense_batch_info(
            batch, total, assume_single_graph_batch=self.assume_single_graph_batch)
        if npg is not None:
            return self._dense(x, batch_size, npg)
        return self._scatter(x, batch, batch_size)


class TransolverPP_block(nn.Module):
    def __init__(self, num_heads, hidden_dim, dropout, act="gelu", mlp_ratio=2,
                 slice_num=32, assume_single_graph_batch=False):
        super().__init__()
        self.ln_1 = nn.LayerNorm(hidden_dim)
        self.Attn = Graph_Physics_Attention_1D_Eidetic(
            hidden_dim, heads=num_heads, dim_head=hidden_dim // num_heads, dropout=dropout,
            slice_num=slice_num, assume_single_graph_batch=assume_single_graph_batch)
        self.ln_2 = nn.LayerNorm(hidden_dim)
        self.mlp = MLP(hidden_dim, hidden_dim * mlp_ratio, hidden_dim, n_layers=0, res=False, act=act)

    def forward(self, fx, batch):
        fx = self.Attn.graph_forward(self.ln_1(fx), batch) + fx
        fx = self.mlp(self.ln_2(fx)) + fx
        return fx


class TransolverPPBaseline(nn.Module):
    def __init__(self, node_input_size=11, node_output_size=5, hidden_size=256,
                 n_layers=8, n_head=8, slice_num=32, mlp_ratio=2, dropout=False,
                 assume_single_graph_batch=False, **kwargs):
        super().__init__()
        drop = 0.1 if dropout else 0.0
        self.preprocess = MLP(node_input_size, hidden_size * 2, hidden_size,
                              n_layers=0, res=False, act="gelu")
        self.placeholder = nn.Parameter((1.0 / hidden_size) * torch.rand(hidden_size))
        self.blocks = nn.ModuleList([
            TransolverPP_block(num_heads=n_head, hidden_dim=hidden_size, dropout=drop,
                               act="gelu", mlp_ratio=mlp_ratio, slice_num=slice_num,
                               assume_single_graph_batch=assume_single_graph_batch)
            for _ in range(n_layers)
        ])
        self.decoder = nn.Sequential(nn.LayerNorm(hidden_size),
                                     nn.Linear(hidden_size, node_output_size))
        self.node_output_size = node_output_size

    def forward(self, x, batch, edge_index=None, edge_attr=None, mask_output_cell=None):
        b = batch.squeeze(-1) if batch.dim() > 1 else batch
        fx = self.preprocess(x) + self.placeholder[None, :]
        for blk in self.blocks:
            fx = blk(fx, b)
        return self.decoder(fx).to(torch.float32)
