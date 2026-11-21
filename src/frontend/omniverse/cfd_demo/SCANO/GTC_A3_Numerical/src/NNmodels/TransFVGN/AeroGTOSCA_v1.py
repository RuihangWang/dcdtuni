"""AeroGTOSCA_v1 — the AeroGTO trunk with our two-stream SCA conditioning bridge.

WHY THIS EXISTS
    Under the fixed multi-GPU protocol, the single-stream AeroGTO baseline (AAAI'25) beats every
    two-stream SCA arm we have, at less memory and fewer parameters. That is a confound, not a
    verdict: our SCA arms also differ from it in TRUNK (Transolver / TransFVGN vs AeroGTO's
    GNN+projection-attention mixer). This model removes the confound. It is plain AeroGTO with
    exactly ONE thing added — the complete-boundary cross-attention bridge — so:

        AeroGTOSCA_v1  <  AeroGTO_baseline   =>  the SCA bridge helps, on a trunk that can use it.
        AeroGTOSCA_v1  >= AeroGTO_baseline   =>  the bridge does not help. That is the answer.

    It is deliberately built so the null result is reachable: everything outside the bridge is
    byte-identical to the baseline.

WHAT IS IDENTICAL TO AeroGTO_baseline (verbatim, same classes, same hyper-parameters)
    * the internal input:      11D [pos(3), unv(3), bc(5)] with bc ZEROED on interior cells,
                               assembled by the importer's `assemble_baseline_node_features`
                               (include_t1_fields=true) over the k-hop sample of cpd_cell.
                               NOT the full-boundary prediction set: baseline_full_boundary=false.
    * the bbox normalisation:  pos min-max normalised over the SAMPLED subgraph, exactly as the
                               baseline does (its Fourier frequencies 2^i*pi assume coords ~[0,1]).
    * the internal encoder:    AeroGTO's own `Encoder11D` — Fourier(pos, 9 freqs) + the 8 extra
                               channels -> 65 -> MLP -> s_enc(128) -> V(hidden). Reusing it, rather
                               than a fresh MLP lift, is what makes the trunk's input byte-identical.
    * the trunk:               AeroGTO's `Mixer` (N_block x [GNN message passing + 256-token
                               projection attention + gated residuals]), edge-consuming.
    * the decoder:             AeroGTO's `Decoder`.

WHAT IS ADDED (the single variable under test)
    The SCA conditioning head, copied from TextCondTrans_v2 so the conditioning route is IDENTICAL
    to the DNEface-SCA-Transolver arm (only the trunk differs between those two):
        BC stream:   ALL boundary faces, raw 11D [pos(3), unv(3), bc(5)] per face -> Encoder11D
                     -> slice-aggregated into `slice_num` BC slice tokens.
        Internal:    the encoded sampled nodes -> slice-aggregated into `slice_num` slice tokens.
        Bridge:      ONCE at input. Internal slice tokens are Q, BC slice tokens are K/V; the
                     cross-attention output is de-sliced back onto the internal nodes, added
                     residually and LayerNorm'd. Then the AeroGTO trunk runs unchanged.

    The two streams stay SEPARATE: the query shrinks with the sampling ratio, the K/V (the complete
    boundary) never does. That separation IS the claim being tested.

DELIBERATE CHOICE — the boundary stream also goes through `Encoder11D`
    TextCondTrans_v2 lifts both streams with a plain MLP. Here the internal stream must go through
    AeroGTO's Fourier encoder (to keep the trunk input byte-identical), so lifting the boundary with
    a plain MLP would leave the two streams in DIFFERENT positional representations and force the
    cross-attention to bridge that gap as well. Both streams therefore use `Encoder11D` (separate
    weights) and — this matters — both are normalised by the SAME bbox, the one computed from the
    sampled subgraph. Boundary faces slightly outside that box are fine: the Fourier features are
    periodic in the coordinate.
"""

import os
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch_scatter import scatter_add
from einops import rearrange

from ..Baselines.AeroGTOBaseline import Encoder11D, Mixer, Decoder


def _get_dense_batch_info(batch, total_nodes, assume_single_graph_batch=False):
    """Verbatim from TextCondTrans_v2 — decides dense vs scatter slicing."""
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


class AeroGTOSCA_v1(nn.Module):
    def __init__(self, internal_input_size=11, boundary_input_size=11, hidden_size=128,
                 att_embedding_dim=None, N_block=8, n_head=8, n_token=256, slice_num=32,
                 dropout=False, node_output_size=5, assume_single_graph_batch=False,
                 use_gradient_checkpointing=False, grad_checkpoint_every=1, **kwargs):
        super().__init__()
        drop = 0.1 if dropout else 0.0
        att = att_embedding_dim or hidden_size
        self.hidden_dim = hidden_size
        self.num_heads = n_head
        self.dim_head = hidden_size // n_head
        self.slice_num = slice_num
        self.scale = self.dim_head ** -0.5
        self.assume_single_graph_batch = bool(assume_single_graph_batch)
        self.node_output_size = node_output_size
        self.use_gradient_checkpointing = bool(use_gradient_checkpointing)
        self.grad_checkpoint_every = int(grad_checkpoint_every)

        # --- AeroGTO, verbatim -------------------------------------------------------------
        self.encoder = Encoder11D(node_input_size=internal_input_size,
                                  state_embedding_dim=hidden_size)
        self.boundary_encoder = Encoder11D(node_input_size=boundary_input_size,
                                           state_embedding_dim=hidden_size)
        self.mixer = Mixer(N=N_block, state_embedding_dim=hidden_size, att_embedding_dim=att,
                           n_head=n_head, n_token=n_token)
        self.decoder = Decoder(state_embedding_dim=hidden_size, state_size=node_output_size)

        # --- SCA conditioning head, copied from TextCondTrans_v2 ----------------------------
        self.boundary_graph_temperature = nn.Parameter(torch.ones([1, n_head, 1]) * 0.5)
        self.boundary_in_project_merged = nn.Linear(hidden_size, hidden_size * 2)
        self.boundary_in_project_slice = nn.Linear(self.dim_head, slice_num)
        torch.nn.init.orthogonal_(self.boundary_in_project_slice.weight)

        self.internal_graph_temperature = nn.Parameter(torch.ones([1, n_head, 1]) * 0.5)
        self.internal_in_project_merged = nn.Linear(hidden_size, hidden_size * 2)
        self.internal_in_project_slice = nn.Linear(self.dim_head, slice_num)
        torch.nn.init.orthogonal_(self.internal_in_project_slice.weight)

        self.to_q = nn.Linear(self.dim_head, self.dim_head, bias=False)
        self.to_kv = nn.Linear(self.dim_head, self.dim_head * 2, bias=False)
        self.to_out = nn.Sequential(nn.Linear(hidden_size, hidden_size), nn.Dropout(drop))
        self.norm = nn.LayerNorm(hidden_size)

    # Both slicing helpers are verbatim from TextCondTrans_v2.
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

    def forward(self, sampled_pos, sampled_unv, batch, edge_index,
                boundary_pos, boundary_unv, boundary_batch,
                patch_numerical_values, sampled_x_override=None, **unused_kwargs):
        batch = batch.squeeze(-1) if batch.dim() > 1 else batch
        boundary_batch = boundary_batch.squeeze(-1) if boundary_batch.dim() > 1 else boundary_batch

        # Internal stream. sampled_x_override (include_t1_fields=true) is the 11D vector the
        # single-stream AeroGTO baseline consumes, byte-for-byte. Falling back to 6D geometry would
        # silently break the single-variable contract, so refuse instead of degrading quietly.
        if sampled_x_override is None:
            raise ValueError(
                "AeroGTOSCA_v1 requires include_t1_fields=true so the internal stream is the same "
                "11D [pos(3), unv(3), bc(5)] the AeroGTO baseline consumes. Without it the trunk "
                "would see a different input than the baseline and the comparison would not isolate "
                "the cross-attention bridge."
            )
        sampled_x = sampled_x_override
        boundary_x = torch.cat([boundary_pos, boundary_unv, patch_numerical_values], dim=-1)

        # ONE bbox for BOTH streams, taken from the sampled subgraph — identical to the baseline's
        # own normalisation, so the trunk sees the same coordinates it always did. The boundary is
        # placed in that same frame (it may fall slightly outside [0,1]; the Fourier features are
        # periodic, so that is harmless) — without a shared frame the cross-attention would have to
        # reconcile two different coordinate systems on top of the physics.
        pos = sampled_x[:, :3]
        pmin, pmax = pos.amin(0, keepdim=True), pos.amax(0, keepdim=True)
        span = (pmax - pmin + 1e-8)
        int_feats = torch.cat([(pos - pmin) / span, sampled_x[:, 3:]], dim=-1).unsqueeze(0)
        bnd_feats = torch.cat([(boundary_x[:, :3] - pmin) / span, boundary_x[:, 3:]], dim=-1).unsqueeze(0)

        V, s_enc = self.encoder(int_feats)                # [1,N,hidden], [1,N,128]
        B, _ = self.boundary_encoder(bnd_feats)           # [1,Nb,hidden]
        h_int = V.squeeze(0)
        h_bnd = B.squeeze(0)

        # --- SCA bridge (TextCondTrans_v2, verbatim) ---------------------------------------
        batch, int_bs, int_npg = _get_dense_batch_info(
            batch, h_int.size(0), self.assume_single_graph_batch)
        boundary_batch, bnd_bs, bnd_npg = _get_dense_batch_info(
            boundary_batch, h_bnd.size(0), self.assume_single_graph_batch)
        use_dense = int_npg is not None and bnd_npg is not None and int_bs == bnd_bs
        batch_size = int_bs

        bnd_fx, bnd_xp = self.boundary_in_project_merged(h_bnd).chunk(2, dim=-1)
        int_fx, int_xp = self.internal_in_project_merged(h_int).chunk(2, dim=-1)

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
        K, Vv = self.to_kv(bnd_slice_token).chunk(2, dim=-1)
        with torch.amp.autocast(device_type=h_int.device.type, enabled=False):
            dots = (torch.matmul(Q.float(), K.float().transpose(-1, -2)) * self.scale)
            dots = dots.clamp_(min=-50.0, max=50.0)
            attn = F.softmax(dots, dim=-1).to(Q.dtype)
        out_slice_token = torch.matmul(attn, Vv)

        if use_dense:
            out_x = torch.einsum("bhgd,bhng->bhnd", out_slice_token, int_slice_weights)
            out_x = rearrange(out_x, "b h n d -> (b n) (h d)")
        else:
            out_x = torch.sum(out_slice_token[batch] * int_slice_weights.unsqueeze(-1), dim=-2)
            out_x = rearrange(out_x, "n h d -> n (h d)")
        h = self.norm(h_int + self.to_out(out_x))

        # --- AeroGTO trunk (verbatim) ------------------------------------------------------
        edges = edge_index.t().contiguous().unsqueeze(0)          # [1,E,2] = [sender, receiver]
        node_pos = int_feats[..., :3]                             # [1,N,3], already normalised
        use_ckpt = (self.use_gradient_checkpointing or bool(os.environ.get("GTC_GRAD_CKPT", ""))) \
            and self.training and torch.is_grad_enabled()
        V = self.mixer(h.unsqueeze(0), edges, node_pos, s_enc,
                       use_ckpt=use_ckpt, ckpt_every=self.grad_checkpoint_every)
        return self.decoder(V).squeeze(0).to(torch.float32)
