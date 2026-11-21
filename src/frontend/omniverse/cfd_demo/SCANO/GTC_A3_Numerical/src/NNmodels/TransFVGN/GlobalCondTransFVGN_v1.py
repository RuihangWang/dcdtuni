"""
GlobalCondTransFVGN_v1 — Method D: Global text embedding broadcast.

No PatchCrossAttentionEncoder. All nodes receive the same global BC signal.

Note: some text_conditioning config keys (e.g. normalize_lift_features,
text_reference_mp_layers, use_film_conditioning) are accepted by the upstream
config builder but do NOT apply here — this model has no patch cross-attention,
no patch reference graph, and always normalizes lifted features via
node_lift_norm/edge_lift_norm. Those keys are silently ignored.
"""

import torch
import torch.nn as nn
from typing import Optional

from .TextCondTransFVGN_v1 import QwenTextConditionEncoder
from .TransFVGN_v2 import AttnProcessor


class Simulator(nn.Module):
    def __init__(
        self,
        message_passing_num,
        node_output_size,
        drop_out=False,
        hidden_size=128,
        params=None,
    ):
        super().__init__()
        self.hidden_size = hidden_size
        self.node_output_size = node_output_size

        # Text encoder (for on-the-fly encoding — LoRA variant)
        self.text_encoder = QwenTextConditionEncoder(
            model_name=getattr(params, "text_encoder_name", "sentence-transformers/all-MiniLM-L6-v2"),
            output_dim=hidden_size,
            lora_rank=getattr(params, "text_lora_rank", 16),
            lora_alpha=getattr(params, "text_lora_alpha", 32),
            lora_dropout=getattr(params, "text_lora_dropout", 0.05),
            use_gradient_checkpointing=getattr(params, "text_use_gradient_checkpointing", True),
            encode_micro_batch_size=getattr(params, "patch_encode_micro_batch_size", 64),
        )

        # Node feature lift: [global_emb(hidden) + pos(3) + unv(3)] → hidden
        self.node_lift = nn.Sequential(
            nn.Linear(hidden_size + 6, hidden_size),
            nn.GELU(),
            nn.Linear(hidden_size, hidden_size),
        )
        self.node_lift_norm = nn.LayerNorm(hidden_size)

        # Edge feature: [node_feat_diff(hidden) + rel_pos(3) + dist(1)] → hidden
        edge_input_dim = hidden_size + 3 + 1
        self.edge_lift = nn.Sequential(
            nn.Linear(edge_input_dim, hidden_size),
            nn.GELU(),
            nn.Linear(hidden_size, hidden_size),
        )
        self.edge_lift_norm = nn.LayerNorm(hidden_size)

        # Attention processors (same as v1/v2)
        assume_single_graph_batch = getattr(params, "assume_single_graph_batch", False)
        attn_processor_num = getattr(params, "attn_processor_num", 7)

        self.processpr_list = nn.ModuleList()
        for _ in range(attn_processor_num):
            self.processpr_list.append(
                AttnProcessor(
                    message_passing_num=message_passing_num,
                    hidden_size=hidden_size,
                    drop_out=drop_out,
                    assume_single_graph_batch=assume_single_graph_batch,
                )
            )

        # Decoder
        self.decoder = nn.Sequential(
            nn.Linear(hidden_size, hidden_size),
            nn.GELU(),
            nn.Linear(hidden_size, node_output_size),
        )

    def _build_edge_attr(self, x, pos, edge_index):
        """Build edge attributes: [feat_diff, rel_pos, dist_norm]."""
        src, dst = edge_index[0], edge_index[1]
        feat_diff = x[dst] - x[src]
        rel_pos = pos[dst] - pos[src]
        dist = rel_pos.norm(dim=-1, keepdim=True)
        return torch.cat([feat_diff, rel_pos, dist], dim=-1)

    def forward(
        self,
        sampled_pos: torch.Tensor,                     # [N, 3]
        sampled_unv: torch.Tensor,                     # [N, 3]
        edge_index: torch.Tensor,                      # [2, E]
        batch: torch.Tensor,                           # [N]
        global_input_ids: Optional[torch.Tensor] = None,
        global_attention_mask: Optional[torch.Tensor] = None,
        precomputed_global_embeddings: Optional[torch.Tensor] = None,
    ) -> torch.Tensor:
        # Step 1: Get global embedding
        if precomputed_global_embeddings is not None:
            global_emb = precomputed_global_embeddings  # [1, hidden] or [B, hidden]
        else:
            global_emb = self.text_encoder(global_input_ids, global_attention_mask)

        # Step 2: Broadcast to all nodes
        node_global = global_emb.index_select(0, batch.long())  # [N, hidden]

        # Step 3: Concat geometry + lift
        node_input = torch.cat([node_global, sampled_pos, sampled_unv], dim=-1)  # [N, hidden+6]
        x = self.node_lift_norm(self.node_lift(node_input))  # [N, hidden]

        # Step 4: Edge attributes
        edge_attr = self._build_edge_attr(x, sampled_pos, edge_index)
        edge_attr = self.edge_lift_norm(self.edge_lift(edge_attr))  # [E, hidden]

        # Step 5: Attention processors
        for processor in self.processpr_list:
            x, edge_attr = processor(x, edge_index, edge_attr, batch)

        # Step 6: Decode
        return self.decoder(x).to(torch.float32)  # [N, 5]
