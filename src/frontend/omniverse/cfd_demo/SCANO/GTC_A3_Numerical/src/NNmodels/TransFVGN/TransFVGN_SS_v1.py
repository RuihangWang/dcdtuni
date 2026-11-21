"""Single-stream TransFVGN — the `DNE-SS-TransFVGN` control arm.

WHY THIS EXISTS. The `{trunk} x {single-stream, +SCA}` table had no single-stream row for the
TransFVGN trunk: `TextCondTransFVGN_v1` hard-requires a boundary stream (`TransFVGN_v2.forward`
raises without `init_patch_phi_cpd_cell`), so the SCA-TransFVGN arms had nothing to be compared
against. This class is that row.

WHAT IT IS. Exactly `TextCondTransFVGN_v1`'s trunk with the complete-boundary K/V stream deleted:

    lift (nb_encoder + twoway eb_encoder) -> GN block -> AttnProcessor x N -> OptimizedDecoder

It reuses the SAME `PatchCrossAttentionEncoder` (with `single_stream=True`), the SAME `AttnProcessor`
and the SAME `OptimizedDecoder` as the two-stream arm rather than reimplementing them, so the two
arms' internal path is the same code, not merely a similar one. The only difference is the boundary
route, which is the whole point of the comparison.

THE CONTRAST IT ENCODES. Under k-hop subgraph sampling the boundary can reach a model two ways:

    single-stream (this class): only through whichever boundary `cpd_cell`s the sampler drew,
                                carried in the 11D node feature's `bc` slice -> INCOMPLETE.
    two-stream (SCA):           the whole boundary, as cross-attention K/V -> COMPLETE.

So this arm is the boundary-incomplete condition. Per the project's thesis it is the one expected to
collapse toward the conditional mean — and therefore the one expected to look BEST on nRMSE, since
the conditional mean is the L2-optimal answer to an ill-posed relation. Read its nRMSE only beside
the degeneracy diagnostics (climatology floor, draw-spread, interior/boundary split); a win here is
not evidence against the boundary-completeness claim.

INPUT CONTRACT. `x` = `assemble_baseline_node_features` = `[pos(3), unv(3), bc(5)]` = 11D, with `bc`
zeroed on interior rows (`unv` is the implicit interior/boundary marker). Set `base_node_dim: 11` in
the config; the edge width follows as `base_node_dim + 4 = 15` and must not be set independently.
"""
import torch
import torch.nn as nn
import torch.utils.checkpoint  # not implied by `import torch`

from ..FVGN.EncoderProcesserDecoder import OptimizedDecoder
from ..PatchEncoder.PatchCrossAttentionEncoder import PatchCrossAttentionEncoder
from .TransFVGN_v2 import AttnProcessor


class Simulator(nn.Module):
    def __init__(
        self,
        message_passing_num,
        node_output_size,
        drop_out=False,
        hidden_size=128,
        params=None,
        patch_slice_num=32,
    ):
        super().__init__()
        self.hidden_size = int(hidden_size)

        # 11D = assemble_baseline_node_features. Defaulting to 11 (not the two-stream arms' 6)
        # because a single-stream model has no K/V: strip the bc slice and the boundary condition
        # would not reach it at all, and the arm would stop being a model of the PDE.
        base_node_dim = int(getattr(params, "base_node_dim", 11))
        # Mirrors TextCondTransFVGN_v1: the edge feature is [node_diff, pos_diff(3), |pos_diff|(1)],
        # so its width is a consequence of the node width, never an independent config knob.
        base_edge_dim = base_node_dim + 4
        self.base_node_dim = base_node_dim
        self.base_edge_dim = base_edge_dim
        assume_single_graph_batch = getattr(params, "assume_single_graph_batch", False)

        self.encoder = PatchCrossAttentionEncoder(
            hidden_dim=hidden_size,
            num_heads=8,
            slice_num=patch_slice_num,
            edge_input_size=base_edge_dim,
            dropout=0.0 if not drop_out else 0.1,
            node_input_size=base_node_dim,
            boundary_input_dim=None,  # single_stream never constructs the patch encoder
            assume_single_graph_batch=assume_single_graph_batch,
            use_internal_gn_block=getattr(params, "use_internal_gn_block_before_slice", True),
            use_patch_pre_norm=getattr(params, "use_patch_pre_norm", True),
            single_stream=True,
        )

        self.processpr_list = nn.ModuleList([
            AttnProcessor(
                message_passing_num=message_passing_num,
                hidden_size=hidden_size,
                drop_out=drop_out,
                assume_single_graph_batch=assume_single_graph_batch,
            )
            for _ in range(params.attn_processor_num)
        ])

        self.decoder = OptimizedDecoder(
            hidden_size=hidden_size,
            node_output_size=node_output_size,
        )
        # Measured 2026-07-16: forward+backward on a real Slab subgraph (113,193 nodes / 205,759
        # edges at sample_ratio=0.05) peaks at 16.8 GiB for the trunk alone, and the training loop's
        # resident tensors push it past a 24 GiB 4090 -> OOM on batch 1. The SCA arm this row is the
        # control for never hit that because it trained on the pre-fix FROZEN sampler, which drew the
        # same pivot chunk every epoch (its subgraphs topped out ~96-106K nodes); unfrozen sampling
        # draws varied chunks and reaches wider k-hop expansions.
        #
        # Checkpointing is the ONLY memory fix here that is mathematically a no-op — it recomputes
        # activations rather than approximating them, so the trained weights are identical to an
        # un-checkpointed run. Lowering sample_ratio or shrinking the trunk would instead change the
        # experiment, and this arm's whole purpose is to be trunk-identical to the SCA row.
        self.use_grad_ckpt = bool(getattr(params, "use_gradient_checkpointing", True))
        self.node_output_size = node_output_size

    @staticmethod
    def _build_edge_attr(sampled_x, sampled_pos, edge_index):
        # Byte-identical to TextCondTransFVGN_v1._build_edge_attr. The last column must stay the
        # (sign-invariant) distance norm: the encoder's twoway lift negates every column but the
        # last to synthesise the reversed edge.
        senders, receivers = edge_index[0], edge_index[1]
        x_diff = sampled_x[senders] - sampled_x[receivers]
        pos_diff = sampled_pos[senders] - sampled_pos[receivers]
        pos_norm = torch.linalg.vector_norm(pos_diff, dim=-1, keepdim=True)
        return torch.cat([x_diff, pos_diff, pos_norm], dim=-1)

    def forward(self, x, batch=None, edge_index=None, edge_attr=None, **unused_kwargs):
        if edge_index is None:
            raise ValueError(
                "TransFVGN_SS_v1 needs edge_index: its trunk is a GN block plus graph slice "
                "attention. Add the model to importer.py's _baseline_needs_edges set."
            )
        if x.shape[1] != self.base_node_dim:
            raise ValueError(
                f"node feature is {x.shape[1]}D but base_node_dim={self.base_node_dim}. The "
                f"single-stream contract is assemble_baseline_node_features = 11D "
                f"[pos(3), unv(3), bc(5)]; a silent mismatch here would train the arm on a "
                f"different input than the one its ARM.md claims."
            )

        pos = x[:, :3]  # assemble_baseline_node_features puts cpd_centroid in cols 0:3, raw.
        # importer.py's baseline branch hands every edge-consuming baseline a 4D
        # [rel_pos(3), |rel_pos|(1)] edge_attr. Ignore it: the SCA-TransFVGN arm this row is
        # compared against builds node-feature-diff edges, and the trunks must match.
        edge_attr = self._build_edge_attr(x, pos, edge_index)

        if batch is None:
            batch = torch.zeros(x.shape[0], dtype=torch.long, device=x.device)
        internal_batch = batch.squeeze(-1) if batch.dim() > 1 else batch

        x, edge_attr = self.encoder(
            internal_x=x,
            internal_edge_index=edge_index,
            internal_edge_attr=edge_attr,
            internal_batch=internal_batch,
            patch_x=None,
            patch_batch=None,
        )

        for model in self.processpr_list:
            if self.use_grad_ckpt and self.training and torch.is_grad_enabled():
                # use_reentrant=False is required: the reentrant autograd path does not compose with
                # autocast here and silently drops the recomputed cast, and it cannot handle a module
                # returning a tuple.
                x, edge_attr = torch.utils.checkpoint.checkpoint(
                    model, x, edge_index, edge_attr, internal_batch, use_reentrant=False
                )
            else:
                x, edge_attr = model(x, edge_index, edge_attr, internal_batch)

        pred_cell = self.decoder(x)
        return pred_cell.to(torch.float32)
