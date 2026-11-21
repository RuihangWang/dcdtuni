import torch
import torch.nn as nn
from torch_scatter import scatter_add
from einops import rearrange
import numpy as np
import torch.nn as nn
import torch.distributed.nn as dist_nn
import torch.nn.functional as F

from einops import rearrange

try:
    # Slice-basis capture/injection. Default-off: with neither SCANO_BASIS_CAPTURE nor
    # SCANO_BASIS_INJECT set, _slice_basis_hook returns its argument unchanged.
    from ..slice_basis import hook as _slice_basis_hook
except ImportError:  # this file imported standalone, outside the NNmodels package
    from slice_basis import hook as _slice_basis_hook


ACTIVATION = {
    "gelu": nn.GELU,
    "tanh": nn.Tanh,
    "sigmoid": nn.Sigmoid,
    "relu": nn.ReLU,
    "leaky_relu": nn.LeakyReLU(0.1),
    "softplus": nn.Softplus,
    "ELU": nn.ELU,
    "silu": nn.SiLU,
}


def _get_dense_batch_info(batch, total_nodes, assume_single_graph_batch=False):
    batch = batch.reshape(-1).long()
    if batch.numel() == 0:
        return batch, 0, None
    if assume_single_graph_batch:
        return batch, 1, total_nodes

    node_counts = torch.bincount(batch)
    if node_counts.numel() == 0:
        return batch, 0, None

    batch_size = node_counts.size(0)
    if batch_size < 1:
        return batch, batch_size, None

    nodes_per_graph = total_nodes // batch_size
    if nodes_per_graph < 1:
        return batch, batch_size, None
    if batch_size * nodes_per_graph != total_nodes:
        return batch, batch_size, None
    if not torch.all(node_counts == nodes_per_graph):
        return batch, batch_size, None

    return batch, batch_size, nodes_per_graph


class Graph_Physics_Attention_1D(nn.Module):
    def __init__(self, dim, heads=8, dim_head=64, dropout=0.0, slice_num=64, assume_single_graph_batch=False):
        super().__init__()
        inner_dim = dim_head * heads
        self.dim_head = dim_head
        self.heads = heads
        self.scale = dim_head**-0.5
        self.softmax = nn.Softmax(dim=-1)
        self.dropout = nn.Dropout(dropout)
        self.assume_single_graph_batch = assume_single_graph_batch
        
        self.graph_temperature = nn.Parameter(torch.ones([1, heads, 1]) * 0.5)

        self.in_project_x = nn.Linear(dim, inner_dim)
        self.in_project_fx = nn.Linear(dim, inner_dim)
        self.in_project_slice = nn.Linear(dim_head, slice_num)
        for l in [self.in_project_slice]:
            torch.nn.init.orthogonal_(l.weight)  # use a principled initialization
        # 优化：合并QKV线性层为单个线性层，提升计算效率
        self.to_qkv = nn.Linear(dim_head, dim_head * 3, bias=False)
        self.to_out = nn.Sequential(nn.Linear(inner_dim, dim), nn.Dropout(dropout))

    def reset_custom_initialization(self):
        gain = 0.02 * (max(self.in_project_slice.weight.shape) ** 0.5)
        torch.nn.init.orthogonal_(self.in_project_slice.weight, gain=gain)
        if self.in_project_slice.bias is not None:
            nn.init.constant_(self.in_project_slice.bias, 0)

    def _graph_forward_dense(self, x, batch_size, nodes_per_graph):
        x_dtype = x.dtype
        x_dense = x.reshape(batch_size, nodes_per_graph, -1)

        fx_mid = self.in_project_fx(x_dense).reshape(
            batch_size, nodes_per_graph, self.heads, self.dim_head
        )
        fx_mid = fx_mid.permute(0, 2, 1, 3).contiguous()

        x_mid = self.in_project_x(x_dense).reshape(
            batch_size, nodes_per_graph, self.heads, self.dim_head
        )
        x_mid = x_mid.permute(0, 2, 1, 3).contiguous()

        temp = F.softplus(self.graph_temperature).unsqueeze(-2) + 1e-4
        slice_weights = self.softmax(self.in_project_slice(x_mid) / temp)
        slice_norm = slice_weights.sum(dim=2)

        slice_token = torch.einsum("bhnd,bhng->bhgd", fx_mid, slice_weights)
        slice_token = slice_token / (slice_norm.unsqueeze(-1) + 1e-5)

        # SLICE-BASIS SITE (trunk, dense path). The token is a mean over exactly the nodes
        # handed in, so a spatially confined sample gives a confined basis. Capture dumps
        # it; inject replaces it with one pooled from a whole-domain pass. `slice_weights`
        # below is NOT touched: it is per-node ([N,H,G]) and is what routes the answer back
        # to THESE nodes. Borrow what the slices mean, keep which slice each node is in.
        slice_token = _slice_basis_hook(self, "", slice_token, slice_weights)

        qkv = self.to_qkv(slice_token)
        q_slice_token, k_slice_token, v_slice_token = qkv.chunk(3, dim=-1)

        dots = torch.matmul(q_slice_token, k_slice_token.transpose(-1, -2)) * self.scale
        attn = self.softmax(dots)
        out_slice_token = torch.matmul(attn, v_slice_token)

        out_x = torch.einsum("bhgd,bhng->bhnd", out_slice_token, slice_weights)
        out_x = rearrange(out_x, "b h n d -> (b n) (h d)").to(x_dtype)
        return self.to_out(out_x)

    def _graph_forward_scatter(self, x, batch, batch_size):
        total_nodes = x.size(0)

        fx_mid = self.in_project_fx(x).view(
            total_nodes, self.heads, self.dim_head
        )
        x_mid = self.in_project_x(x).view(
            total_nodes, self.heads, self.dim_head
        )

        temp = F.softplus(self.graph_temperature) + 1e-4
        slice_weights = self.softmax(
            self.in_project_slice(x_mid) / temp
        )
        slice_norm = scatter_add(
            slice_weights, batch, dim=0, dim_size=batch_size
        )

        slice_token = scatter_add(
            fx_mid.unsqueeze(-2) * slice_weights.unsqueeze(-1),
            batch,
            dim=0,
            dim_size=batch_size
        )
        slice_token = slice_token / (slice_norm.unsqueeze(-1) + 1e-5)

        # SLICE-BASIS SITE (trunk, scatter path) — same site name as the dense path above,
        # because it is the same layer: whichever branch a run takes, layer L's token is
        # keyed to layer L. See the dense-path comment for why only the token moves.
        slice_token = _slice_basis_hook(self, "", slice_token, slice_weights)

        qkv = self.to_qkv(slice_token)
        q_slice_token, k_slice_token, v_slice_token = qkv.chunk(3, dim=-1)

        dots = torch.matmul(q_slice_token, k_slice_token.transpose(-1, -2)) * self.scale
        attn = self.softmax(dots)
        out_slice_token = torch.matmul(attn, v_slice_token)

        out_slice_token_expanded = out_slice_token[batch.squeeze()]
        out_x = torch.sum(
            out_slice_token_expanded * slice_weights.unsqueeze(-1), dim=-2
        )
        out_x = rearrange(out_x, "n h d -> n (h d)")
        return self.to_out(out_x)

    def graph_forward(self, x, batch):
        # x: [B*N, C], batch: [B*N, 1]
        total_nodes = x.size(0)
        batch, batch_size, nodes_per_graph = _get_dense_batch_info(
            batch,
            total_nodes,
            assume_single_graph_batch=self.assume_single_graph_batch,
        )

        if nodes_per_graph is not None:
            return self._graph_forward_dense(x, batch_size, nodes_per_graph)

        return self._graph_forward_scatter(x, batch, batch_size)

class MLP(nn.Module):
    def __init__(self, n_input, hidden_size, n_output, n_layers=1, act="gelu", res=True):
        super(MLP, self).__init__()

        if act in ACTIVATION.keys():
            act = ACTIVATION[act]
        else:
            raise NotImplementedError
        self.n_input = n_input
        self.hidden_size = hidden_size
        self.n_output = n_output
        self.n_layers = n_layers
        self.res = res
        self.linear_pre = nn.Sequential(nn.Linear(n_input, hidden_size), act())
        self.linear_post = nn.Linear(hidden_size, n_output)
        self.linears = nn.ModuleList(
            [
                nn.Sequential(nn.Linear(hidden_size, hidden_size), act())
                for _ in range(n_layers)
            ]
        )

    def forward(self, x):
        x = self.linear_pre(x)
        for i in range(self.n_layers):
            if self.res:
                x = self.linears[i](x) + x
            else:
                x = self.linears[i](x)
        x = self.linear_post(x)
        return x


class Graph_Transolver_block(nn.Module):
    """Transformer encoder block."""

    def __init__(
        self,
        num_heads: int,
        hidden_dim: int,
        dropout: float,
        act="gelu",
        mlp_ratio=4,
        slice_num=32,
        assume_single_graph_batch=False,
    ):
        super().__init__()

        self.Attn = Graph_Physics_Attention_1D(
            hidden_dim,
            heads=num_heads,
            dim_head=hidden_dim // num_heads,
            dropout=dropout,
            slice_num=slice_num,
            assume_single_graph_batch=assume_single_graph_batch,
        )
        self.ln_2 = nn.LayerNorm(hidden_dim)
        self.mlp = MLP(
            hidden_dim,
            hidden_dim * mlp_ratio,
            hidden_dim,
            n_layers=0,
            res=False,
            act=act,
        )

    def forward(self, fx, batch):
        fx = self.Attn.graph_forward(fx, batch) + fx
        fx = self.mlp(self.ln_2(fx)) + fx
        return fx

class Transolver_block(nn.Module):
    """Transformer encoder block."""

    def __init__(
        self,
        num_heads: int,
        hidden_dim: int,
        dropout: float,
        act="gelu",
        mlp_ratio=4,
        slice_num=32,
        assume_single_graph_batch=False,
    ):
        super().__init__()

        self.ln_1 = nn.LayerNorm(hidden_dim)
        self.Attn = Graph_Physics_Attention_1D(
            hidden_dim,
            heads=num_heads,
            dim_head=hidden_dim // num_heads,
            dropout=dropout,
            slice_num=slice_num,
            assume_single_graph_batch=assume_single_graph_batch,
        )
        self.ln_2 = nn.LayerNorm(hidden_dim)
        self.mlp = MLP(
            hidden_dim,
            hidden_dim * mlp_ratio,
            hidden_dim,
            n_layers=0,
            res=False,
            act=act,
        )

    def forward(self, fx, batch):
        fx = self.Attn.graph_forward(self.ln_1(fx), batch) + fx
        fx = self.mlp(self.ln_2(fx)) + fx
        return fx
    
class Transolver(nn.Module):
    def __init__(
        self,
        space_dim=1,
        n_layers=5,
        hidden_size=256,
        dropout=0,
        n_head=8,
        act="gelu",
        mlp_ratio=1,
        fun_dim=1,
        out_dim=1,
        slice_num=32,
        ref=8,
        unified_pos=False,
    ):
        super(Transolver, self).__init__()
        self.__name__ = "UniPDE_3D"
        self.ref = ref
        self.unified_pos = unified_pos
        if self.unified_pos:
            self.preprocess = MLP(
                fun_dim + self.ref * self.ref * self.ref,
                hidden_size * 2,
                hidden_size,
                n_layers=0,
                res=False,
                act=act,
            )
        else:
            self.preprocess = MLP(
                fun_dim + space_dim,
                hidden_size * 2,
                hidden_size,
                n_layers=0,
                res=False,
                act=act,
            )

        self.hidden_size = hidden_size
        self.space_dim = space_dim

        self.blocks = nn.ModuleList(
            [
                Transolver_block(
                    num_heads=n_head,
                    hidden_dim=hidden_size,
                    dropout=dropout,
                    act=act,
                    mlp_ratio=mlp_ratio,
                    out_dim=out_dim,
                    slice_num=slice_num,
                    last_layer=False,
                )
                for _ in range(n_layers)
            ]
        )

        self.decoder = nn.Sequential(
            nn.LayerNorm(hidden_size), nn.Linear(hidden_size, out_dim)
        )

        self.initialize_weights()
        self.placeholder = nn.Parameter(
            (1 / (hidden_size)) * torch.rand(hidden_size, dtype=torch.float)
        )

    def initialize_weights(self):
        self.apply(self._init_weights)

    def _init_weights(self, m):
        if isinstance(m, nn.Linear):
            nn.init.trunc_normal_(m.weight, std=0.02)
            if isinstance(m, nn.Linear) and m.bias is not None:
                nn.init.constant_(m.bias, 0)
        elif isinstance(m, (nn.LayerNorm, nn.BatchNorm1d)):
            nn.init.constant_(m.bias, 0)
            nn.init.constant_(m.weight, 1.0)

    def get_grid(self, my_pos):
        # my_pos 1 N 3
        batchsize = my_pos.shape[0]

        gridx = torch.tensor(np.linspace(-1.5, 1.5, self.ref), dtype=torch.float)
        gridx = gridx.reshape(1, self.ref, 1, 1, 1).repeat(
            [batchsize, 1, self.ref, self.ref, 1]
        )
        gridy = torch.tensor(np.linspace(0, 2, self.ref), dtype=torch.float)
        gridy = gridy.reshape(1, 1, self.ref, 1, 1).repeat(
            [batchsize, self.ref, 1, self.ref, 1]
        )
        gridz = torch.tensor(np.linspace(-4, 4, self.ref), dtype=torch.float)
        gridz = gridz.reshape(1, 1, 1, self.ref, 1).repeat(
            [batchsize, self.ref, self.ref, 1, 1]
        )
        grid_ref = (
            torch.cat((gridx, gridy, gridz), dim=-1)
            .cuda()
            .reshape(batchsize, self.ref**3, 3)
        )  # B 4 4 4 3

        pos = (
            torch.sqrt(
                torch.sum(
                    (my_pos[:, :, None, :] - grid_ref[:, None, :, :]) ** 2, dim=-1
                )
            )
            .reshape(batchsize, my_pos.shape[1], self.ref * self.ref * self.ref)
            .contiguous()
        )
        return pos

    def forward(self, x, batch, params=None):


        # cfd_data, geom_data = data
        # x, fx, T = cfd_data.x, None, None
        # x  : node features
        # pos: node pos

        if self.unified_pos:
            new_pos = self.get_grid(pos[None, :, :])
            x = torch.cat((x, new_pos), dim=-1)

        pos, fx = None, None

        if fx is not None:
            fx = torch.cat((x, fx), -1)
            fx = self.preprocess(fx)
        else:
            fx = self.preprocess(x)
            fx = fx + self.placeholder[None, None, :]

        for block in self.blocks:
            fx = block(fx, batch, in_layernorm=True)
            
        fx = self.decoder(fx)

        return fx
