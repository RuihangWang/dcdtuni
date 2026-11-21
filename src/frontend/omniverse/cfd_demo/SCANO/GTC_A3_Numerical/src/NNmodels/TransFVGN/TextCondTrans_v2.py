"""TextCondTrans_v2 — input-aligned, message-passing-free A2 architecture (no FVGN/GNN component).

Renamed from TextCondTransFVGN_v2 (2026-06-10): the name dropped "FVGN" because this variant
contains NO graph network — pure Transolver trunk + SCA conditioning. The sibling
TextCondTransFVGN_v3 keeps the FVGN GNN+attention processor trunk.


Two streams, NO message passing, NO edges consumed anywhere:
  - BC stream: ALL boundary faces, raw 11D [pos(3), unv(3), bc(5)] per face (value-identical to the
    Transolver baseline's boundary rows; bc = raw unconverged-t1, no normalization, no patch-mean,
    no PatchReferenceGraphEncoder) -> MLP lift -> slice-aggregated into BC slice tokens.
  - Internal stream: sampled subgraph nodes, raw 6D [pos(3), unv(3)] -> MLP lift + placeholder.
Conditioning happens ONCE at input: internal slice tokens cross-attend (Q) to BC slice tokens (K/V),
deslice back onto internal nodes, residual + LayerNorm. Then a plain Transolver trunk
(n_layers x Transolver_block, baseline-matched) and an LN+Linear decoder over the sampled nodes.

vs the single-stream Transolver baseline this is a single-variable ablation: same trunk family,
same information at the same per-face granularity; only the conditioning route differs
(cross-attention two-stream here vs in-stream concatenation in the baseline).
"""
import os
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.utils.checkpoint
from torch_scatter import scatter_add
from einops import rearrange

from ..GraphTransolver.GraphTransolver import Transolver_block, MLP


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


class TextCondTrans_v2(nn.Module):
    def __init__(self, internal_input_size=6, boundary_input_size=11, hidden_size=256,
                 n_layers=8, n_head=8, slice_num=32, mlp_ratio=2, dropout=False,
                 node_output_size=5, assume_single_graph_batch=False, **kwargs):
        super().__init__()
        drop = 0.1 if dropout else 0.0
        self.hidden_dim = hidden_size
        self.num_heads = n_head
        self.dim_head = hidden_size // n_head
        self.slice_num = slice_num
        self.scale = self.dim_head ** -0.5
        self.assume_single_graph_batch = bool(assume_single_graph_batch)
        self.node_output_size = node_output_size
        # Config-gated trunk gradient checkpointing (also overridable via env GTC_GRAD_CKPT=1).
        self.use_gradient_checkpointing = bool(kwargs.get("use_gradient_checkpointing", False))

        # Lifts: raw features -> hidden, no pre-norm (parity with TransolverBaseline.preprocess)
        self.internal_lift = MLP(internal_input_size, hidden_size * 2, hidden_size,
                                 n_layers=0, res=False, act="gelu")
        self.boundary_lift = MLP(boundary_input_size, hidden_size * 2, hidden_size,
                                 n_layers=0, res=False, act="gelu")
        self.placeholder = nn.Parameter((1.0 / hidden_size) * torch.rand(hidden_size))

        # Slice projections (fused [fx, x_proj]) + per-head temperatures, per stream
        self.boundary_graph_temperature = nn.Parameter(torch.ones([1, n_head, 1]) * 0.5)
        self.boundary_in_project_merged = nn.Linear(hidden_size, hidden_size * 2)
        self.boundary_in_project_slice = nn.Linear(self.dim_head, slice_num)
        torch.nn.init.orthogonal_(self.boundary_in_project_slice.weight)

        self.internal_graph_temperature = nn.Parameter(torch.ones([1, n_head, 1]) * 0.5)
        self.internal_in_project_merged = nn.Linear(hidden_size, hidden_size * 2)
        self.internal_in_project_slice = nn.Linear(self.dim_head, slice_num)
        torch.nn.init.orthogonal_(self.internal_in_project_slice.weight)

        # Cross-attention at slice level: Q from internal, K/V from boundary
        self.to_q = nn.Linear(self.dim_head, self.dim_head, bias=False)
        self.to_kv = nn.Linear(self.dim_head, self.dim_head * 2, bias=False)
        self.to_out = nn.Sequential(nn.Linear(hidden_size, hidden_size), nn.Dropout(drop))
        self.norm = nn.LayerNorm(hidden_size)

        # Trunk: plain Transolver blocks (baseline-matched), NO edges / message passing
        self.blocks = nn.ModuleList([
            Transolver_block(num_heads=n_head, hidden_dim=hidden_size, dropout=drop,
                             act="gelu", mlp_ratio=mlp_ratio, slice_num=slice_num,
                             assume_single_graph_batch=assume_single_graph_batch)
            for _ in range(n_layers)
        ])
        self.decoder = nn.Sequential(nn.LayerNorm(hidden_size),
                                     nn.Linear(hidden_size, node_output_size))

    def _compute_slice_tokens_dense(self, fx, x_proj, batch_size, nodes_per_graph,
                                    temperature, slice_projector):
        total_nodes = fx.size(0)
        fx_mid = fx.view(total_nodes, self.num_heads, self.dim_head)
        x_mid = x_proj.view(total_nodes, self.num_heads, self.dim_head)

        fx_mid = fx_mid.view(batch_size, nodes_per_graph, self.num_heads, self.dim_head)
        x_mid = x_mid.view(batch_size, nodes_per_graph, self.num_heads, self.dim_head)
        fx_mid = fx_mid.permute(0, 2, 1, 3).contiguous()
        x_mid = x_mid.permute(0, 2, 1, 3).contiguous()

        slice_logits = slice_projector(x_mid.float())
        slice_scores = (slice_logits / temperature.unsqueeze(-2)).clamp_(min=-30.0, max=30.0)
        slice_weights = F.softmax(slice_scores, dim=-1).to(fx_mid.dtype)
        slice_norm = slice_weights.sum(dim=2)
        slice_token = torch.einsum("bhnd,bhng->bhgd", fx_mid, slice_weights)
        slice_token = slice_token / (slice_norm.unsqueeze(-1) + 1e-5)
        return slice_weights, slice_token

    def _compute_slice_tokens_scatter(self, fx, x_proj, batch, batch_size,
                                      temperature, slice_projector):
        total_nodes = fx.size(0)
        fx_mid = fx.view(total_nodes, self.num_heads, self.dim_head)
        x_mid = x_proj.view(total_nodes, self.num_heads, self.dim_head)

        slice_logits = slice_projector(x_mid.float())
        slice_scores = (slice_logits / temperature).clamp_(min=-30.0, max=30.0)
        slice_weights = F.softmax(slice_scores, dim=-1).to(fx_mid.dtype)
        slice_norm = scatter_add(slice_weights, batch, dim=0, dim_size=batch_size)
        slice_token = scatter_add(
            fx_mid.unsqueeze(-2) * slice_weights.unsqueeze(-1),
            batch, dim=0, dim_size=batch_size,
        )
        slice_token = slice_token / (slice_norm.unsqueeze(-1) + 1e-5)
        return slice_weights, slice_token

    def forward(self, sampled_pos, sampled_unv, batch,
                boundary_pos, boundary_unv, boundary_batch,
                patch_numerical_values, sampled_x_override=None, **unused_kwargs):
        batch = batch.squeeze(-1) if batch.dim() > 1 else batch
        boundary_batch = boundary_batch.squeeze(-1) if boundary_batch.dim() > 1 else boundary_batch

        # Raw inputs, no normalization (baseline parity).
        # sampled_x_override (include_t1_fields=true): the prediction-stream input is the
        # baseline-IDENTICAL 11D vector [cpd_centroid(3), unv(3), bc(5)] (internal bc zeroed),
        # produced by the importer via assemble_baseline_node_features — instead of the
        # geometry-only [pos, unv]=6D — so SCA-EPD differs from the single-stream baseline
        # ONLY by the added cross-attention KV bridge (boundary_x below). internal_input_size
        # must be set to 11 in the config to match.
        if sampled_x_override is not None:
            sampled_x = sampled_x_override
        else:
            sampled_x = torch.cat([sampled_pos, sampled_unv], dim=-1)
        boundary_x = torch.cat([boundary_pos, boundary_unv, patch_numerical_values], dim=-1)

        h_int = self.internal_lift(sampled_x) + self.placeholder[None, :]
        h_bnd = self.boundary_lift(boundary_x)

        batch, int_bs, int_npg = _get_dense_batch_info(
            batch, h_int.size(0), self.assume_single_graph_batch)
        boundary_batch, bnd_bs, bnd_npg = _get_dense_batch_info(
            boundary_batch, h_bnd.size(0), self.assume_single_graph_batch)
        use_dense = int_npg is not None and bnd_npg is not None and int_bs == bnd_bs
        batch_size = int_bs

        bnd_merged = self.boundary_in_project_merged(h_bnd)
        bnd_fx, bnd_xp = bnd_merged.chunk(2, dim=-1)
        int_merged = self.internal_in_project_merged(h_int)
        int_fx, int_xp = int_merged.chunk(2, dim=-1)

        with torch.amp.autocast(device_type=h_int.device.type, enabled=False):
            bnd_temp = F.softplus(self.boundary_graph_temperature.float()) + 1e-4
            int_temp = F.softplus(self.internal_graph_temperature.float()) + 1e-4
            if use_dense:
                _, bnd_slice_token = self._compute_slice_tokens_dense(
                    bnd_fx, bnd_xp, bnd_bs, bnd_npg, bnd_temp, self.boundary_in_project_slice)
                int_slice_weights, int_slice_token = self._compute_slice_tokens_dense(
                    int_fx, int_xp, int_bs, int_npg, int_temp, self.internal_in_project_slice)
            else:
                _, bnd_slice_token = self._compute_slice_tokens_scatter(
                    bnd_fx, bnd_xp, boundary_batch, batch_size, bnd_temp,
                    self.boundary_in_project_slice)
                int_slice_weights, int_slice_token = self._compute_slice_tokens_scatter(
                    int_fx, int_xp, batch, batch_size, int_temp,
                    self.internal_in_project_slice)

        Q = self.to_q(int_slice_token)
        K, V = self.to_kv(bnd_slice_token).chunk(2, dim=-1)
        with torch.amp.autocast(device_type=h_int.device.type, enabled=False):
            dots = torch.matmul(Q.float(), K.float().transpose(-1, -2)) * self.scale
            dots = dots.clamp_(min=-50.0, max=50.0)
            attn = F.softmax(dots, dim=-1).to(Q.dtype)
        out_slice_token = torch.matmul(attn, V)

        if use_dense:
            out_x = torch.einsum("bhgd,bhng->bhnd", out_slice_token, int_slice_weights)
            out_x = rearrange(out_x, "b h n d -> (b n) (h d)")
        else:
            out_x = torch.sum(
                out_slice_token[batch] * int_slice_weights.unsqueeze(-1), dim=-2)
            out_x = rearrange(out_x, "n h d -> n (h d)")
        h = self.norm(h_int + self.to_out(out_x))

        # Optional gradient checkpointing over the plain Transolver trunk: recompute each block's
        # activations in backward instead of storing them (~0.8 GiB/block saved), trading ~15-25%
        # compute for the memory needed to raise the feasible sample ratio on the larger GTC mesh.
        # Enabled by config (use_gradient_checkpointing) or env GTC_GRAD_CKPT=1.
        use_ckpt = getattr(self, "use_gradient_checkpointing", False) or bool(
            os.environ.get("GTC_GRAD_CKPT", "")
        )
        for blk in self.blocks:
            if use_ckpt and self.training and torch.is_grad_enabled():
                h = torch.utils.checkpoint.checkpoint(blk, h, batch, use_reentrant=False)
            else:
                h = blk(h, batch)
        return self.decoder(h).to(torch.float32)
