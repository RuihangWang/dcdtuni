import torch
import torch.nn as nn
from ..FVGN.EncoderProcesserDecoder import OptimizedGnBlock, OptimizedDecoder
from ..GraphTransolver.GraphTransolver import Graph_Transolver_block
from ..PatchEncoder.PatchCrossAttentionEncoder import PatchCrossAttentionEncoder, _memprobe


class AttnProcessor(nn.Module):
    def __init__(self, message_passing_num=0, hidden_size=128, drop_out=False, dtype=torch.float32, assume_single_graph_batch=False):
        super(AttnProcessor, self).__init__()

        if message_passing_num < 0:
            raise ValueError("message_passing_num must be >= 0")

        # message_passing_num == 0 → PURE Transolver trunk (no per-layer GN message passing).
        # This matches the standalone Transolver baseline's trunk (node-only slice attention) and
        # removes the GN activations that dominate memory (~1.08 GiB/layer on the largest GTC mesh),
        # letting the two-stream SCA model run at a much higher sample ratio.
        self.use_gn = message_passing_num >= 1
        self.gn_blocks_list = nn.ModuleList([
            OptimizedGnBlock(
                hidden_size=hidden_size,
                drop_out=drop_out,
            ) for _ in range(message_passing_num)
        ])

        self.TransBlock = Graph_Transolver_block(
            num_heads=8,
            hidden_dim=hidden_size,
            dropout=0,
            act="gelu",
            mlp_ratio=2,
            slice_num=32,
            assume_single_graph_batch=assume_single_graph_batch,
        )

    def forward_GN_block(self, x, edge_index, edge_attr):

        # Process through GN blocks (optimized - no Data object creation)
        for  gn_block in self.gn_blocks_list:
                x, edge_attr = gn_block(x, edge_index, edge_attr)

        return x, edge_attr

    def forward(self, x, edge_index, edge_attr, batch):
        if self.use_gn:
            # GN + Transolver hybrid trunk (message passing then slice attention, residual add).
            fx, f_edge_attr = self.forward_GN_block(x, edge_index, edge_attr)
            _memprobe("  proc.after_GN", f"E={edge_index.size(1)}")
            fx = self.TransBlock((x + fx), batch)
        else:
            # Pure Transolver trunk: node-only slice attention, edges passed through unused.
            f_edge_attr = edge_attr
            fx = self.TransBlock(x, batch)
        _memprobe("  proc.after_TransBlock")

        return fx, f_edge_attr


class Simulator(nn.Module):
    def __init__(
        self,
        message_passing_num,
        edge_input_size,
        node_input_size,
        node_output_size,
        drop_out=False,
        hidden_size=128,
        params=None,
        # Patch cross-attention encoder config
        patch_slice_num=32,
        boundary_input_dim=11,
    ):
        super(Simulator, self).__init__()
        self.hidden_size = hidden_size
        assume_single_graph_batch = getattr(params, "assume_single_graph_batch", False)

        # ============================================================
        # Patch Cross-Attention Encoder Architecture
        # ============================================================
        # Two-stream architecture:
        #   - Patch stream: All boundary patches → GraphTransolver graph_forward → slice_token [B, H, G, D]
        #   - Internal stream: Sampled k-hop internal nodes → GN Block (edge+node) →
        #                      GraphTransolver graph_forward with Cross-Attention (K from patch) → Deslice
        #
        # This provides cross-attention between internal nodes and ALL boundary conditions
        # via slice tokens, preserving complete boundary information.
        # ============================================================

        # Patch cross-attention encoder with FVGN-style lift
        self.encoder = PatchCrossAttentionEncoder(
            hidden_dim=hidden_size,
            num_heads=8,
            slice_num=patch_slice_num,
            edge_input_size=edge_input_size,  # Raw edge input size
            dropout=0.0 if not drop_out else 0.1,
            node_input_size=node_input_size,  # Raw node input size
            boundary_input_dim=boundary_input_dim,  # Raw boundary input size
            assume_single_graph_batch=assume_single_graph_batch,
            use_internal_gn_block=getattr(params, "use_internal_gn_block_before_slice", True),
            use_patch_pre_norm=getattr(params, "use_patch_pre_norm", True),
        )

        # Attention processors (unchanged)
        processpr_list = []
        for _ in range(params.attn_processor_num):
            processpr_list.append(
                AttnProcessor(
                    message_passing_num=message_passing_num,
                    hidden_size=hidden_size,
                    drop_out=drop_out,
                    assume_single_graph_batch=assume_single_graph_batch,
                )
            )
        self.processpr_list = nn.ModuleList(processpr_list)

        # Decoder (unchanged)
        self.decoder = OptimizedDecoder(
            hidden_size=hidden_size,
            node_output_size=node_output_size,
        )

        self.node_output_size = node_output_size

    def forward(
        self,
        x,  # [N_internal, node_input_size] - Sampled internal nodes
        edge_attr,  # [E_internal, edge_input_size] - Sampled edges
        edge_index,  # [2, E_internal] - Sampled edge indices
        batch,  # [N_internal] - Batch indices for internal nodes
        # Patch encoder inputs (required)
        init_patch_phi_cpd_cell,  # [N_patch, boundary_input_dim] - All boundary nodes
        init_patch_phi_batch_cpd_cell,  # [N_patch, 1] - Batch indices for patches
        mask_output_cell=None,  # [N_internal] - Mask for output cells
    ):
        # ============================================================
        # Patch Cross-Attention Encoding
        # ============================================================
        if init_patch_phi_cpd_cell is None:
            raise ValueError("init_patch_phi_cpd_cell must be provided")

        # Pass raw features to encoder - it will handle FVGN-style lift internally
        # The encoder's lift_features() will:
        # 1. Encode internal nodes with FVGN-style nb_encoder
        # 2. Encode edges with FVGN-style twoway edge logic
        # 3. Encode patches with FVGN-style patch_encoder

        # Ensure batch indices are 1D
        internal_batch = batch.squeeze(-1) if batch.dim() > 1 else batch
        patch_batch = init_patch_phi_batch_cpd_cell.squeeze(-1) if init_patch_phi_batch_cpd_cell.dim() > 1 else init_patch_phi_batch_cpd_cell

        # Pass through patch cross-attention encoder
        # This performs:
        #   0. Lift: FVGN-style encoding for nodes, edges, and patches
        #   1. Patch stream: patch_x → GraphTransolver graph_forward → slice_token [B, H, G, D]
        #   2. Internal stream: x → GN Block → GraphTransolver graph_forward with Cross-Attention
        #                       (Q from internal, K/V from patch) → Deslice
        x, edge_attr = self.encoder(
            internal_x=x,
            internal_edge_index=edge_index,
            internal_edge_attr=edge_attr,
            internal_batch=internal_batch,
            patch_x=init_patch_phi_cpd_cell,  # Pass raw patch features
            patch_batch=patch_batch,
        )

        _memprobe("trunk.after_encoder")

        # ============================================================
        # Attention Processors (unchanged)
        # ============================================================
        for i, model in enumerate(self.processpr_list):
            x, edge_attr = model(x, edge_index, edge_attr, internal_batch)
            _memprobe(f"trunk.after_processor_{i}")

        # ============================================================
        # Decoder (unchanged)
        # ============================================================
        # Collected cell scheme: only decode output cells (exclude cyclic ghost cells)
        if mask_output_cell is None:
            pred_cell = self.decoder(x)
        else:
            pred_cell = torch.zeros((x.shape[0],self.node_output_size), device=x.device, dtype=torch.float32)
            decoder_output = self.decoder(x[mask_output_cell])
            # Ensure dtype matches for mixed precision training
            pred_cell[mask_output_cell] = decoder_output.to(pred_cell.dtype)

        return pred_cell.to(torch.float32)
