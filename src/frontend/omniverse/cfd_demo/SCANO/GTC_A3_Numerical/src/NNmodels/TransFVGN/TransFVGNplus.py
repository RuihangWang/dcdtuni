import torch
import torch.nn as nn
from ..FVGN.EncoderProcesserDecoder import OptimizedGnBlock, OptimizedDecoder
from ..GraphTransolverplus.GraphTransolverplus import Graph_Transolver_plus_block
from ..PatchEncoder.PatchCrossAttentionEncoder import PatchCrossAttentionEncoder
from ..debug_utils import summarize_tensor

class AttnProcessorPlus(nn.Module):
    """
    Attention Processor using GraphTransolver++ blocks
    Improved memory efficiency compared to original AttnProcessor
    """
    def __init__(
        self,
        message_passing_num=0,
        hidden_size=128,
        drop_out=False,
        dtype=torch.float32,
        use_gumbel_softmax=False,
        assume_single_graph_batch=False,
    ):
        super(AttnProcessorPlus, self).__init__()
        self.debug_enabled = False
        self._debug_state = {}

        if message_passing_num < 1:
            raise ValueError("message_passing_num must be greater than 0")

        self.gn_blocks_list = nn.ModuleList([
            OptimizedGnBlock(
                hidden_size=hidden_size,
                drop_out=drop_out,
            ) for _ in range(message_passing_num)
        ])

        # Use GraphTransolver++ block for improved memory efficiency
        self.TransBlock = Graph_Transolver_plus_block(
            num_heads=8,
            hidden_dim=hidden_size,
            dropout=0,
            act="gelu",
            mlp_ratio=2,
            slice_num=32,
            use_gumbel_softmax=use_gumbel_softmax,
            assume_single_graph_batch=assume_single_graph_batch,
        )

    def set_debug_enabled(self, enabled: bool = True):
        self.debug_enabled = enabled
        if hasattr(self.TransBlock, "set_debug_enabled"):
            self.TransBlock.set_debug_enabled(enabled)

    def _record_debug(self, name: str, tensor: torch.Tensor):
        if self.debug_enabled:
            self._debug_state[name] = summarize_tensor(tensor)

    def peek_debug_state(self):
        return dict(self._debug_state)

    def consume_debug_state(self):
        state = self.peek_debug_state()
        self._debug_state = {}
        return state
        
    def forward_GN_block(self, x, edge_index, edge_attr):
        
        # Process through GN blocks (optimized - no Data object creation)
        for  gn_block in self.gn_blocks_list:
                x, edge_attr = gn_block(x, edge_index, edge_attr)
                
        return x, edge_attr
    
    def forward(self, x, edge_index, edge_attr, batch):
        self._debug_state = {}

        # Process through GN blocks (optimized - no Data object creation)
        fx, f_edge_attr = self.forward_GN_block(x, edge_index, edge_attr)
        self._record_debug("after_gn_x", fx)
        self._record_debug("after_gn_edge_attr", f_edge_attr)

        # Apply GraphTransolver++ block with residual connection
        # x (GN前) + fx (GN后) 作为输入送入 TransBlock
        fx = self.TransBlock(x + fx, batch)
        self._record_debug("after_transblock_x", fx)
        if self.debug_enabled and hasattr(self.TransBlock, "peek_debug_state"):
            self._debug_state["transblock_detail"] = self.TransBlock.peek_debug_state()

        return fx, f_edge_attr


class Simulator(nn.Module):
    """
    TransFVGN++ Simulator using GraphTransolver++ blocks
    Improved memory efficiency for handling larger point clouds
    """
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
        self.debug_enabled = False
        self._debug_state = {}
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
        )

        processpr_list = []
        use_gumbel_softmax = getattr(params, "use_gumbel_softmax", False)
        for _ in range(params.attn_processor_num):
            processpr_list.append(
                AttnProcessorPlus(
                    message_passing_num=message_passing_num,
                    hidden_size=hidden_size,
                    drop_out=drop_out,
                    use_gumbel_softmax=use_gumbel_softmax,
                    assume_single_graph_batch=assume_single_graph_batch,
                )
            )
        self.processpr_list = nn.ModuleList(processpr_list)

        self.decoder = OptimizedDecoder(
            hidden_size=hidden_size,
            node_output_size=node_output_size,
        )

        self.node_output_size = node_output_size

    def set_debug_enabled(self, enabled: bool = True):
        self.debug_enabled = enabled
        if hasattr(self.encoder, "set_debug_enabled"):
            self.encoder.set_debug_enabled(enabled)
        for model in self.processpr_list:
            if hasattr(model, "set_debug_enabled"):
                model.set_debug_enabled(enabled)

    def _record_debug(self, name: str, tensor: torch.Tensor):
        if self.debug_enabled:
            self._debug_state[name] = summarize_tensor(tensor)

    def peek_debug_state(self):
        state = dict(self._debug_state)
        if hasattr(self.encoder, "peek_debug_state"):
            state["patch_encoder"] = self.encoder.peek_debug_state()
        return state

    def consume_debug_state(self):
        state = self.peek_debug_state()
        self._debug_state = {}
        if hasattr(self.encoder, "consume_debug_state"):
            state["patch_encoder"] = self.encoder.consume_debug_state()
        return state

    # Note: torch.compile disabled due to incompatibility with torch_scatter operations
    # The scatter_add operation has data-dependent output sizes which torch.compile cannot handle
    # @torch.compile(mode="default", dynamic=True)
    def forward(
        self,
        x,  # [N_internal, node_input_size] - Sampled internal nodes
        edge_attr,  # [E_internal, edge_input_size] - Sampled edges
        edge_index,  # [2, E_internal] - Sampled edge indices
        batch,  # [N_internal] - Batch indices for internal nodes
        # Patch encoder inputs (required)
        init_patch_phi_cpd_cell,  # [N_patch, boundary_input_dim] - All boundary nodes
        init_patch_phi_batch_cpd_cell,  # [N_patch, 1] - Batch indices for patches
        mask_output_cell=None, # 可以排除循环面的ghost cell的解码，以节省显存
    ):
        self._debug_state = {}

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
        self._record_debug("encoder_output_x", x)
        self._record_debug("encoder_output_edge_attr", edge_attr)

        # Process through attention processors (using GraphTransolver++)
        for idx, model in enumerate(self.processpr_list):
            x, edge_attr = model(x, edge_index, edge_attr, internal_batch)
            self._record_debug(f"processor_{idx}_x", x)
            self._record_debug(f"processor_{idx}_edge_attr", edge_attr)
            if self.debug_enabled and hasattr(model, "peek_debug_state"):
                self._debug_state[f"processor_{idx}_detail"] = model.peek_debug_state()

        ''' collected cell scheme'''
        if mask_output_cell is None:
            pred_cell = self.decoder(x)
        else:
            pred_cell = torch.zeros((x.shape[0],self.node_output_size), device=x.device, dtype=torch.float32)
            decoder_output = self.decoder(x[mask_output_cell])
            # Ensure dtype matches for mixed precision training
            pred_cell[mask_output_cell] = decoder_output.to(pred_cell.dtype)
        self._record_debug("decoder_output", pred_cell)

        return pred_cell.to(torch.float32)
