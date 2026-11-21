"""TextCondTransFVGN_v3 — SCA conditioning (input-aligned, per-face) + FVGN GNN+attention trunk.

The third corner of the 2-factor study:

  - Transolver_baseline : in-stream concat conditioning + Transolver trunk
  - TextCondTrans_v2    : SCA conditioning              + Transolver trunk   (isolates SCA)
  - TextCondTransFVGN_v3: SCA conditioning              + FVGN GNN+attn trunk (isolates trunk vs v2;
                          full method vs baseline)

Front-end follows TextCondTrans_v2: BC stream = ALL boundary faces, per-face [pos,unv,bc].
NOTE: the widths and the prediction set below are the module defaults, NOT the reported
configuration. The arm config governs, and the reported main-line run sets
`include_t1_fields=true` (11D query, matching its single-stream control) with
`baseline_full_boundary=false` (prediction set = the sampled k-hop subgraph). The
full-boundary prediction set is a separate ablation, never the headline arm. Conditioning once at input via PatchCrossAttentionEncoder
(slice cross-attention; its optional internal GN block stays OFF).

Trunk: v1's processor — attn_processor_num x AttnProcessor(message_passing_num GN blocks +
attention) over the induced subgraph edges — i.e. the finite-volume graph component of Li et al.,
J. Comput. Phys. 530:113919 (2025). Decoder: OptimizedDecoder (v1-matched).
"""
import torch
import torch.nn as nn
from torch.utils.checkpoint import checkpoint

from ..FVGN.EncoderProcesserDecoder import OptimizedDecoder
from ..PatchEncoder.PatchCrossAttentionEncoder import PatchCrossAttentionEncoder
from .TransFVGN_v2 import AttnProcessor


class TextCondTransFVGN_v3(nn.Module):
    def __init__(self, hidden_size=256, message_passing_num=1, attn_processor_num=7,
                 patch_slice_num=32, node_output_size=5, dropout=False,
                 assume_single_graph_batch=False,
                 use_internal_gn_block_before_slice=False,
                 normalize_lift_features=False, use_patch_pre_norm=False,
                 use_grad_checkpoint=True, base_node_dim=6, **kwargs):
        super().__init__()
        drop_out = bool(dropout)
        # The [sampled_internal + ALL boundary] node set (~184K) OOMs 24GB without checkpointing
        # the 7 GN+attention processors (same fix as MeshGraphNetsBaseline). Training-only.
        self.use_grad_checkpoint = bool(use_grad_checkpoint)
        # Query width. Default 6 = [pos(3), unv(3)] — the original v3 / DNEface contract, kept so
        # existing 6D checkpoints still load. Set base_node_dim=11 (with include_t1_fields=true in the
        # run config) to feed the SAME 11D [pos(3), unv(3), bc(5)] query the single-stream arm gets;
        # that makes DNE-SCA-TransFVGN a single-variable (boundary-K/V-bridge-only) ablation against
        # DNE-SS-TransFVGN, which is the whole point of this arm.
        self.sampled_input_dim = int(base_node_dim)
        self.boundary_input_dim = 11    # [pos(3), unv(3), bc(5)] per face — symmetric with the 11D query
        self.edge_input_dim = self.sampled_input_dim + 4  # x_diff + pos_diff(3) + norm(1)
        self.node_output_size = node_output_size

        self.encoder = PatchCrossAttentionEncoder(
            hidden_dim=hidden_size,
            num_heads=8,
            slice_num=patch_slice_num,
            edge_input_size=self.edge_input_dim,
            dropout=0.1 if drop_out else 0.0,
            node_input_size=self.sampled_input_dim,
            boundary_input_dim=self.boundary_input_dim,
            assume_single_graph_batch=assume_single_graph_batch,
            use_internal_gn_block=bool(use_internal_gn_block_before_slice),
            normalize_lift_features=bool(normalize_lift_features),
            use_patch_pre_norm=bool(use_patch_pre_norm),
        )
        self.processpr_list = nn.ModuleList([
            AttnProcessor(
                message_passing_num=message_passing_num,
                hidden_size=hidden_size,
                drop_out=drop_out,
                assume_single_graph_batch=assume_single_graph_batch,
            )
            for _ in range(attn_processor_num)
        ])
        self.decoder = OptimizedDecoder(
            hidden_size=hidden_size,
            node_output_size=node_output_size,
        )

    @staticmethod
    def _build_edge_attr(sampled_x, sampled_pos, edge_index):
        senders, receivers = edge_index[0], edge_index[1]
        x_diff = sampled_x[senders] - sampled_x[receivers]
        pos_diff = sampled_pos[senders] - sampled_pos[receivers]
        pos_norm = torch.linalg.vector_norm(pos_diff, dim=-1, keepdim=True)
        return torch.cat([x_diff, pos_diff, pos_norm], dim=-1)

    def forward(self, sampled_pos, sampled_unv, batch, edge_index,
                boundary_pos, boundary_unv, boundary_batch,
                patch_numerical_values, sampled_x_override=None, **unused_kwargs):
        batch = batch.squeeze(-1) if batch.dim() > 1 else batch
        boundary_batch = boundary_batch.squeeze(-1) if boundary_batch.dim() > 1 else boundary_batch

        # Query stream. When include_t1_fields=true the importer routes its 11D
        # assemble_baseline_node_features here as sampled_x_override — byte-identical to the
        # single-stream arm's query — so the ONLY thing separating this arm from DNE-SS-TransFVGN is
        # the boundary K/V bridge built below. Without the override, fall back to the original 6D
        # [pos, unv] geometry query.
        if sampled_x_override is not None:
            if sampled_x_override.shape[-1] != self.sampled_input_dim:
                raise ValueError(
                    f"sampled_x_override is {sampled_x_override.shape[-1]}D but the encoder was built "
                    f"for sampled_input_dim={self.sampled_input_dim}. Set base_node_dim="
                    f"{sampled_x_override.shape[-1]} in this model's config so the query lift matches."
                )
            sampled_x = sampled_x_override
        else:
            sampled_x = torch.cat([sampled_pos, sampled_unv], dim=-1)
        # K/V = the COMPLETE boundary, per face: [pos(3), unv(3), bc(5)] = 11D. patch_numerical_values
        # arrives already z-scored (importer, when baseline_normalize_inputs=true) with the SAME
        # normalizer as the query's bc slice, so Q and K/V share one scale.
        boundary_x = torch.cat([boundary_pos, boundary_unv, patch_numerical_values], dim=-1)
        edge_attr = self._build_edge_attr(sampled_x, sampled_pos, edge_index)

        x, edge_attr = self.encoder(
            internal_x=sampled_x,
            internal_edge_index=edge_index,
            internal_edge_attr=edge_attr,
            internal_batch=batch,
            patch_x=boundary_x,
            patch_batch=boundary_batch,
        )
        for model in self.processpr_list:
            if self.use_grad_checkpoint and self.training:
                x, edge_attr = checkpoint(model, x, edge_index, edge_attr, batch,
                                          use_reentrant=False)
            else:
                x, edge_attr = model(x, edge_index, edge_attr, batch)
        return self.decoder(x).to(torch.float32)
