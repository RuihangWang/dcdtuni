"""
Patch Cross-Attention Encoder

Two-stream architecture:
1. Patch stream: All boundary patches → GraphTransolver graph_forward → slice_token [B, H, G, D] (no deslice)
2. Internal stream: K-hop sampled internal nodes → Message Passing (edge+node block) →
   GraphTransolver graph_forward with Cross-Attention (K from patch slice_token) → Deslice

This implements cross-attention between internal nodes and boundary conditions via slice tokens.
"""
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch_scatter import scatter_add
from einops import rearrange

# Import necessary components
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))
from NNmodels.FVGN.EncoderProcesserDecoder import OptimizedGnBlock, build_mlp_from_num_layer
from NNmodels.debug_utils import summarize_tensor

try:
    # Slice-basis capture/injection. Default-off: with neither SCANO_BASIS_CAPTURE nor
    # SCANO_BASIS_INJECT set, _slice_basis_hook returns its argument unchanged.
    from ..slice_basis import hook as _slice_basis_hook
except ImportError:  # this file imported standalone, outside the NNmodels package
    from NNmodels.slice_basis import hook as _slice_basis_hook


def _memprobe(tag, extra=""):
    """Env-gated CUDA memory probe (GTC_MEM_PROBE=1). Default off → no overhead."""
    if os.environ.get("GTC_MEM_PROBE") and torch.cuda.is_available():
        torch.cuda.synchronize()
        a = torch.cuda.memory_allocated() / 1024**3
        m = torch.cuda.max_memory_allocated() / 1024**3
        print(f"[MEMPROBE] {tag:30s} alloc={a:6.2f} GiB peak={m:6.2f} GiB {extra}", flush=True)


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


class PatchCrossAttentionEncoder(nn.Module):
    """
    Optimized Patch Cross-Attention Encoder with fused operations.
    """
    def __init__(
        self,
        hidden_dim=128,
        num_heads=8,
        slice_num=32,
        edge_input_size=12,
        dropout=0.0,
        node_input_size=None,
        boundary_input_dim=None,
        assume_single_graph_batch=False,
        use_internal_gn_block=True,
        normalize_lift_features=False,
        use_patch_pre_norm=True,
        single_stream=False,
    ):
        super().__init__()

        self.hidden_dim = hidden_dim
        self.num_heads = num_heads
        self.dim_head = hidden_dim // num_heads
        self.slice_num = slice_num
        self.scale = self.dim_head ** -0.5
        self.assume_single_graph_batch = assume_single_graph_batch
        self.use_internal_gn_block = bool(use_internal_gn_block)
        self.normalize_lift_features = bool(normalize_lift_features)
        self.use_patch_pre_norm = bool(use_patch_pre_norm)
        # single_stream: the DNE-SS control arm. The complete-boundary K/V stream is REMOVED, so the
        # boundary can only reach the model through whichever boundary cpd_cells the k-hop sampler
        # happened to draw (carried in the 11D node feature's bc slice). That is the boundary-
        # INCOMPLETE condition the two-stream arm is meant to be compared against.
        #
        # The patch and cross-attention submodules are NOT CONSTRUCTED here rather than merely
        # skipped in forward(): a built-but-ungradiented parameter makes DDP raise
        # ("Expected to have finished reduction in the prior iteration") unless
        # find_unused_parameters=True, which silently costs a full extra reduction pass. Not
        # building them also makes the logged parameter count honest — the SS arm must not be
        # credited with the bridge's weights it never uses.
        self.single_stream = bool(single_stream)

        if hidden_dim % 2 != 0:
            raise ValueError("hidden_dim must be even number for FVGN-style encoder")

        self.nb_encoder = build_mlp_from_num_layer(
            node_input_size, hidden_dim, int(hidden_dim), drop_out=False, lay_norm=False,
        )
        self.eb_encoder = build_mlp_from_num_layer(
            edge_input_size, hidden_dim, int(hidden_dim/2), drop_out=False, lay_norm=False,
        )
        if not self.single_stream:
            self.patch_pre_norm = nn.LayerNorm(boundary_input_dim) if self.use_patch_pre_norm else nn.Identity()
            self.patch_encoder = build_mlp_from_num_layer(
                boundary_input_dim, hidden_dim, int(hidden_dim), drop_out=False, lay_norm=False,
            )
        if self.normalize_lift_features:
            self.internal_lift_norm = nn.LayerNorm(hidden_dim)
            self.edge_lift_norm = nn.LayerNorm(hidden_dim)
            self.patch_lift_norm = nn.Identity() if self.single_stream else nn.LayerNorm(hidden_dim)
        else:
            self.internal_lift_norm = nn.Identity()
            self.edge_lift_norm = nn.Identity()
            self.patch_lift_norm = nn.Identity()

        # ============ Patch Stream ============
        if not self.single_stream:
            self.patch_graph_temperature = nn.Parameter(torch.ones([1, num_heads, 1]) * 0.5)

            # Fused projection: [fx, x] -> 2 * hidden_dim
            self.patch_in_project_merged = nn.Linear(hidden_dim, hidden_dim * 2)
            self.patch_in_project_slice = nn.Linear(self.dim_head, slice_num)
            torch.nn.init.orthogonal_(self.patch_in_project_slice.weight)

        # ============ Internal Stream ============
        # The two-stream path builds gn_block unconditionally even when use_internal_gn_block=False,
        # which leaves unused parameters and is why runner.py forces find_unused_parameters=True for
        # TextCondTransFVGN_v1/v3. Do NOT "fix" that for the two-stream path: those arms' shipped
        # best.state files contain encoder.gn_block.* weights, and dropping the module would break
        # checkpoint loading for every SCA arm on disk.
        #
        # single_stream is new, has no checkpoints to stay compatible with, and NEEDS a clean
        # parameter set: it uses activation checkpointing, which requires DDP static_graph=True,
        # and static_graph is mutually exclusive with find_unused_parameters=True.
        if self.use_internal_gn_block or not self.single_stream:
            self.gn_block = OptimizedGnBlock(
                hidden_size=hidden_dim,
                drop_out=dropout > 0,
            )
        # The internal slice tokens exist ONLY to supply Q to the cross-attention. With no K/V to
        # attend to, they feed nothing — the trunk's own Graph_Transolver_blocks do their own
        # slicing downstream. So single_stream drops this whole projection too.
        if not self.single_stream:
            self.internal_graph_temperature = nn.Parameter(torch.ones([1, num_heads, 1]) * 0.5)

            # Fused projection: [fx, x] -> 2 * hidden_dim
            self.internal_in_project_merged = nn.Linear(hidden_dim, hidden_dim * 2)
            self.internal_in_project_slice = nn.Linear(self.dim_head, slice_num)
            torch.nn.init.orthogonal_(self.internal_in_project_slice.weight)

            # ============ Cross-Attention ============
            # Q from internal (separate)
            self.to_q = nn.Linear(self.dim_head, self.dim_head, bias=False)
            # Fused K, V from patch
            self.to_kv = nn.Linear(self.dim_head, self.dim_head * 2, bias=False)

            self.to_out = nn.Sequential(
                nn.Linear(hidden_dim, hidden_dim),
                nn.Dropout(dropout)
            )
            self.norm = nn.LayerNorm(hidden_dim)
            self.softmax = nn.Softmax(dim=-1)
        self.debug_enabled = False
        self._debug_state = {}

    def set_debug_enabled(self, enabled: bool = True):
        self.debug_enabled = enabled

    def reset_custom_initialization(self):
        if self.single_stream:
            # Nothing to re-init: single_stream builds neither slice projector (both exist only to
            # serve the cross-attention). Returning early keeps this a no-op instead of an
            # AttributeError on a module that was deliberately never constructed.
            return
        gain_patch = 0.02 * (max(self.patch_in_project_slice.weight.shape) ** 0.5)
        gain_internal = 0.02 * (max(self.internal_in_project_slice.weight.shape) ** 0.5)
        torch.nn.init.orthogonal_(self.patch_in_project_slice.weight, gain=gain_patch)
        torch.nn.init.orthogonal_(self.internal_in_project_slice.weight, gain=gain_internal)
        if self.patch_in_project_slice.bias is not None:
            nn.init.constant_(self.patch_in_project_slice.bias, 0)
        if self.internal_in_project_slice.bias is not None:
            nn.init.constant_(self.internal_in_project_slice.bias, 0)

    def _record_debug(self, name: str, tensor: torch.Tensor):
        if self.debug_enabled:
            self._debug_state[name] = summarize_tensor(tensor)

    def peek_debug_state(self):
        return dict(self._debug_state)

    def consume_debug_state(self):
        state = self.peek_debug_state()
        self._debug_state = {}
        return state

    def _compute_slice_tokens_dense(
        self,
        fx: torch.Tensor,
        x_proj: torch.Tensor,
        batch_size: int,
        nodes_per_graph: int,
        temperature: torch.Tensor,
        slice_projector: nn.Linear,
    ):
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

    def _compute_slice_tokens_scatter(
        self,
        fx: torch.Tensor,
        x_proj: torch.Tensor,
        batch: torch.Tensor,
        batch_size: int,
        temperature: torch.Tensor,
        slice_projector: nn.Linear,
    ):
        total_nodes = fx.size(0)
        fx_mid = fx.view(total_nodes, self.num_heads, self.dim_head)
        x_mid = x_proj.view(total_nodes, self.num_heads, self.dim_head)

        slice_logits = slice_projector(x_mid.float())
        slice_scores = (slice_logits / temperature).clamp_(min=-30.0, max=30.0)
        slice_weights = F.softmax(slice_scores, dim=-1).to(fx_mid.dtype)
        slice_norm = scatter_add(slice_weights, batch, dim=0, dim_size=batch_size)
        slice_token = scatter_add(
            fx_mid.unsqueeze(-2) * slice_weights.unsqueeze(-1),
            batch,
            dim=0,
            dim_size=batch_size,
        )
        slice_token = slice_token / (slice_norm.unsqueeze(-1) + 1e-5)
        return slice_weights, slice_token

    def lift_features(self, internal_x, internal_edge_attr, patch_x):
        # Same as original
        if self.nb_encoder is not None:
            lifted_internal_x = self.nb_encoder(internal_x)
        else:
            lifted_internal_x = internal_x

        reverse_edge_features = torch.cat(
            (-internal_edge_attr[:, :-1], internal_edge_attr[:, -1:]), dim=1
        )
        twoway_edge_attr = torch.cat((internal_edge_attr, reverse_edge_features), dim=0)
        twoway_edge_features = self.eb_encoder(twoway_edge_attr)
        lifted_edge_attr = torch.cat(torch.chunk(twoway_edge_features, 2, dim=0), dim=1)
        lifted_internal_x = self.internal_lift_norm(lifted_internal_x)
        lifted_edge_attr = self.edge_lift_norm(lifted_edge_attr)
        if self.single_stream:
            # No patch stream to lift. The internal lift above is bit-identical to the two-stream
            # arm's, which is the point: the two arms must differ ONLY by the boundary route.
            return lifted_internal_x, lifted_edge_attr, None
        lifted_patch_x = self.patch_encoder(self.patch_pre_norm(patch_x))
        lifted_patch_x = self.patch_lift_norm(lifted_patch_x)
        return lifted_internal_x, lifted_edge_attr, lifted_patch_x

    def forward(
        self,
        internal_x, # [1245421, 5]
        internal_edge_index,
        internal_edge_attr,
        internal_batch,
        patch_x,  # [2500, 5]
        patch_batch,
    ):
        # patch_x is None under single_stream, so the probe's size() must not be evaluated
        # unconditionally — an f-string argument is built before _memprobe can gate on its env var.
        _memprobe("enc.entry", f"N_int={internal_x.size(0)} "
                               f"N_patch={'none(single_stream)' if patch_x is None else patch_x.size(0)}")
        if (patch_x is None) != self.single_stream:
            raise ValueError(
                f"patch_x is {'None' if patch_x is None else 'provided'} but single_stream="
                f"{self.single_stream}. These must agree: a two-stream encoder silently fed no "
                f"boundary would train as a single-stream model while still being reported as SCA, "
                f"and a single-stream encoder handed a boundary would ignore it without saying so."
            )
        internal_x, internal_edge_attr, patch_x = self.lift_features(
            internal_x, internal_edge_attr, patch_x
        ) # [1245421, 5]
        _memprobe("enc.after_lift")
        self._debug_state = {}
        self._record_debug("lifted_internal_x", internal_x)
        self._record_debug("lifted_internal_edge_attr", internal_edge_attr)

        if self.single_stream:
            # DNE-SS control: lift -> GN block -> out. No patch slice, no cross-attention, no
            # deslice. Everything downstream (the trunk's Graph_Transolver_blocks, the decoder) is
            # shared with the two-stream arm, so the ONLY difference between the arms is that the
            # complete boundary never reaches the model.
            if self.use_internal_gn_block:
                internal_x, internal_edge_attr = self.gn_block(
                    internal_x, internal_edge_index, internal_edge_attr
                )
            self._record_debug("internal_x_after_gn", internal_x)
            _memprobe("enc.after_internal_gn(single_stream)")
            return internal_x, internal_edge_attr

        self._record_debug("lifted_patch_x", patch_x)
        internal_batch, internal_batch_size, internal_nodes_per_graph = _get_dense_batch_info(
            internal_batch,
            internal_x.size(0),
            assume_single_graph_batch=self.assume_single_graph_batch,
        )
        patch_batch, patch_batch_size, patch_nodes_per_graph = _get_dense_batch_info(
            patch_batch,
            patch_x.size(0),
            assume_single_graph_batch=self.assume_single_graph_batch,
        )
        batch_size = internal_batch_size
        use_dense_path = (
            internal_nodes_per_graph is not None
            and patch_nodes_per_graph is not None
            and internal_batch_size == patch_batch_size
        )

        # ============ Patch Stream ============
        N_patch = patch_x.size(0)
        
        # Fused projection
        patch_merged = self.patch_in_project_merged(patch_x)
        patch_fx, patch_x_proj = patch_merged.chunk(2, dim=-1)
        self._record_debug("patch_fx", patch_fx)
        self._record_debug("patch_x_proj", patch_x_proj)

        with torch.amp.autocast(device_type=internal_x.device.type, enabled=False):
            patch_temp = F.softplus(self.patch_graph_temperature.float()) + 1e-4
            self._record_debug("patch_temperature", patch_temp)
            if use_dense_path:
                patch_slice_weights, patch_slice_token = self._compute_slice_tokens_dense(
                    patch_fx,
                    patch_x_proj,
                    batch_size=patch_batch_size,
                    nodes_per_graph=patch_nodes_per_graph,
                    temperature=patch_temp,
                    slice_projector=self.patch_in_project_slice,
                )
            else:
                patch_slice_weights, patch_slice_token = self._compute_slice_tokens_scatter(
                    patch_fx,
                    patch_x_proj,
                    batch=patch_batch,
                    batch_size=batch_size,
                    temperature=patch_temp,
                    slice_projector=self.patch_in_project_slice,
                )
        # SLICE-BASIS SITE (boundary K/V) — the CIRCULARITY METER, captured but NOT
        # injected by default (SCANO_BASIS_SITES excludes '*.patch' unless '+patch').
        # This token is pooled over ALL 856 patches regardless of pivot sampling, so it is
        # byte-identical between an ROI arm and a whole-domain arm AT THE SAME OPERATING
        # POINT, and differs only when the boundary condition itself differs. Its
        # cached-vs-own relative L2 therefore reads ~0 for a same-point (circular) reuse
        # and >0 for genuine cross-operating-point reuse. It is reported on every inject
        # run so a same-point number can never be quoted as a cross-point one.
        patch_slice_token = _slice_basis_hook(self, "patch", patch_slice_token, patch_slice_weights)
        self._record_debug("patch_slice_weights", patch_slice_weights)
        self._record_debug("patch_slice_token", patch_slice_token)
        _memprobe("enc.after_patch_slice", f"N_patch={N_patch} dense={use_dense_path}")

        # ============ Internal Stream ============
        self._record_debug("internal_x_before_gn", internal_x)
        self._record_debug("internal_edge_attr_before_gn", internal_edge_attr)
        if self.use_internal_gn_block:
            internal_x, internal_edge_attr = self.gn_block(
                internal_x, internal_edge_index, internal_edge_attr
            )
        internal_x_before_cross_attn = internal_x
        self._record_debug("internal_x_after_gn", internal_x)
        self._record_debug("internal_edge_attr_after_gn", internal_edge_attr)
        _memprobe("enc.after_internal_gn")

        N_internal = internal_x.size(0)
        
        # Fused projection
        internal_merged = self.internal_in_project_merged(internal_x)
        internal_fx, internal_x_proj = internal_merged.chunk(2, dim=-1)
        self._record_debug("internal_fx", internal_fx)
        self._record_debug("internal_x_proj", internal_x_proj)

        with torch.amp.autocast(device_type=internal_x.device.type, enabled=False):
            internal_temp = F.softplus(self.internal_graph_temperature.float()) + 1e-4
            self._record_debug("internal_temperature", internal_temp)
            if use_dense_path:
                internal_slice_weights, internal_slice_token = self._compute_slice_tokens_dense(
                    internal_fx,
                    internal_x_proj,
                    batch_size=internal_batch_size,
                    nodes_per_graph=internal_nodes_per_graph,
                    temperature=internal_temp,
                    slice_projector=self.internal_in_project_slice,
                )
            else:
                internal_slice_weights, internal_slice_token = self._compute_slice_tokens_scatter(
                    internal_fx,
                    internal_x_proj,
                    batch=internal_batch,
                    batch_size=batch_size,
                    temperature=internal_temp,
                    slice_projector=self.internal_in_project_slice,
                )
        # SLICE-BASIS SITE (cross-attention Q) — THE site the diagnosis names. This token
        # is the only way the sampled interior addresses the complete boundary sitting in
        # K/V, and it is a mean over exactly the nodes this draw was handed. Substituting
        # it hands the ROI a query basis pooled from a whole-domain pass.
        # `internal_slice_weights` is deliberately left alone: it is [N_roi, H, G] and is
        # both the assignment (which slice each ROI node queries through) and the deslice
        # (line below, "out_x = einsum(out_slice_token, internal_slice_weights)") that
        # writes the attention result back onto THIS pass's nodes. Replacing it is not
        # possible (wrong node count) and not meaningful (it would paste another node
        # set's answers onto ROI cells). Borrow the basis, keep the addressing.
        internal_slice_token = _slice_basis_hook(self, "internal", internal_slice_token, internal_slice_weights)
        self._record_debug("internal_slice_weights", internal_slice_weights)
        self._record_debug("internal_slice_token", internal_slice_token)
        _memprobe("enc.after_internal_slice")

        # ============ Cross-Attention ============
        # Q from internal
        Q = self.to_q(internal_slice_token)

        # Fused K, V from patch
        KV = self.to_kv(patch_slice_token)
        K, V = KV.chunk(2, dim=-1)
        self._record_debug("cross_q", Q)
        self._record_debug("cross_k", K)
        self._record_debug("cross_v", V)

        with torch.amp.autocast(device_type=internal_x.device.type, enabled=False):
            dots = torch.matmul(Q.float(), K.float().transpose(-1, -2)) * self.scale
            dots = dots.clamp_(min=-50.0, max=50.0)
            attn = F.softmax(dots, dim=-1).to(Q.dtype)
        self._record_debug("cross_dots", dots)
        self._record_debug("cross_attn", attn)

        out_slice_token = torch.matmul(attn, V)
        self._record_debug("out_slice_token", out_slice_token)

        # Deslice
        if use_dense_path:
            out_x = torch.einsum("bhgd,bhng->bhnd", out_slice_token, internal_slice_weights)
            out_x = rearrange(out_x, "b h n d -> (b n) (h d)")
        else:
            out_slice_token_expanded = out_slice_token[internal_batch]
            out_x = torch.sum(
                out_slice_token_expanded * internal_slice_weights.unsqueeze(-1),
                dim=-2
            )
            out_x = rearrange(out_x, "n h d -> n (h d)")
        out_x = self.to_out(out_x)
        self._record_debug("out_x_before_norm", out_x)
        out_x = self.norm(internal_x_before_cross_attn + out_x)
        self._record_debug("out_x_after_norm", out_x)
        _memprobe("enc.after_cross_attn_deslice")

        return out_x, internal_edge_attr
