import torch
import torch.nn as nn

from ..FVGN.EncoderProcesserDecoder import OptimizedDecoder
from ..debug_utils import summarize_tensor
from ..PatchEncoder.PatchCrossAttentionEncoder import PatchCrossAttentionEncoder
from ..PatchEncoder.PatchNumericalEncoder import PatchNumericalEncoder
from .TransFVGN_v2 import AttnProcessor


def _build_small_mlp(input_dim: int, hidden_dim: int, output_dim: int) -> nn.Sequential:
    return nn.Sequential(
        nn.Linear(int(input_dim), int(hidden_dim)),
        nn.SiLU(),
        nn.Linear(int(hidden_dim), int(output_dim)),
    )


class QwenTextConditionEncoder(nn.Module):
    def __init__(
        self,
        model_name: str,
        lora_rank: int = 16,
        lora_alpha: int = 32,
        lora_dropout: float = 0.05,
        use_gradient_checkpointing: bool = True,
        encode_micro_batch_size: int = 64,
        random_init: bool = False,
        output_dim: int = None,
        legacy_output_projection: bool = False,
    ):
        super().__init__()

        from transformers import AutoModel, AutoConfig
        from peft import LoraConfig, TaskType, get_peft_model

        self.encode_micro_batch_size = max(1, int(encode_micro_batch_size))
        backbone_dtype = torch.bfloat16 if torch.cuda.is_available() else torch.float32
        if random_init:
            config = AutoConfig.from_pretrained(model_name, trust_remote_code=False)
            self.backbone = AutoModel.from_config(config).to(backbone_dtype)
        else:
            self.backbone = AutoModel.from_pretrained(
                model_name,
                trust_remote_code=False,
                torch_dtype=backbone_dtype,
                low_cpu_mem_usage=True,
            )
        if hasattr(self.backbone.config, "use_cache"):
            self.backbone.config.use_cache = False
        if use_gradient_checkpointing and hasattr(self.backbone, "gradient_checkpointing_enable"):
            self.backbone.gradient_checkpointing_enable()

        lora_config = LoraConfig(
            task_type=TaskType.FEATURE_EXTRACTION,
            r=int(lora_rank),
            lora_alpha=int(lora_alpha),
            lora_dropout=float(lora_dropout),
            target_modules="all-linear",
        )
        self.backbone = get_peft_model(self.backbone, lora_config)

        backbone_hidden = getattr(self.backbone.config, "hidden_size", None)
        if backbone_hidden is None:
            raise ValueError(f"Unable to infer hidden_size from text encoder config for {model_name}")

        if output_dim is not None and int(output_dim) != backbone_hidden:
            if legacy_output_projection:
                # Legacy v1 checkpoints (e.g. GTC/C2 2026-05-01): a single bare
                # Linear projection with NO pre-LayerNorm. Retained solely so those
                # historical checkpoints reload exactly; not used for new training.
                self.output_ln = None
                self.output_proj = nn.Linear(int(backbone_hidden), int(output_dim))
            else:
                self.output_ln = nn.LayerNorm(int(backbone_hidden))
                self.output_proj = nn.Sequential(
                    nn.Linear(int(backbone_hidden), int(backbone_hidden)),
                    nn.GELU(),
                    nn.Linear(int(backbone_hidden), int(output_dim)),
                )
            self.output_dim = int(output_dim)
        else:
            self.output_proj = None
            self.output_ln = None
            self.output_dim = backbone_hidden

    @staticmethod
    def _masked_mean_pool(last_hidden_state: torch.Tensor, attention_mask: torch.Tensor) -> torch.Tensor:
        mask = attention_mask.unsqueeze(-1).to(last_hidden_state.dtype)
        masked_hidden = last_hidden_state * mask
        denom = mask.sum(dim=1).clamp_min(1.0)
        return masked_hidden.sum(dim=1) / denom

    def forward(self, input_ids: torch.Tensor, attention_mask: torch.Tensor) -> torch.Tensor:
        chunks = []
        total = int(input_ids.size(0))
        for start in range(0, total, self.encode_micro_batch_size):
            end = min(start + self.encode_micro_batch_size, total)
            outputs = self.backbone(
                input_ids=input_ids[start:end],
                attention_mask=attention_mask[start:end],
            )
            pooled = self._masked_mean_pool(outputs.last_hidden_state, attention_mask[start:end])
            if self.output_proj is not None:
                if self.output_ln is not None:
                    pooled = self.output_ln(pooled.to(self.output_ln.weight.dtype))
                else:
                    # Legacy bare-Linear projection: align dtype to the proj weights.
                    pooled = pooled.to(self.output_proj.weight.dtype)
                pooled = self.output_proj(pooled)
            chunks.append(pooled)
        return torch.cat(chunks, dim=0)


class PatchReferenceGraphEncoder(nn.Module):
    def __init__(
        self,
        hidden_size: int,
        num_edge_types: int,
        num_layers: int = 1,
    ):
        super().__init__()
        self.hidden_size = int(hidden_size)
        self.num_layers = max(0, int(num_layers))
        if self.num_layers == 0:
            self.edge_type_embedding = None
            self.layers = nn.ModuleList()
            self.norms = nn.ModuleList()
            return

        self.edge_type_embedding = nn.Embedding(max(1, int(num_edge_types)), self.hidden_size)
        self.layers = nn.ModuleList(
            [
                _build_small_mlp(self.hidden_size * 2, self.hidden_size, self.hidden_size)
                for _ in range(self.num_layers)
            ]
        )
        self.norms = nn.ModuleList([nn.LayerNorm(self.hidden_size) for _ in range(self.num_layers)])

    def forward(
        self,
        patch_embeddings: torch.Tensor,
        patch_ref_edge_index: torch.Tensor,
        patch_ref_edge_type: torch.Tensor,
    ) -> torch.Tensor:
        if self.num_layers == 0 or patch_ref_edge_index.numel() == 0:
            return patch_embeddings

        input_dtype = patch_embeddings.dtype
        x = patch_embeddings.float()
        src = patch_ref_edge_index[0].long()
        dst = patch_ref_edge_index[1].long()
        edge_type = patch_ref_edge_type.long()

        for update_mlp, norm in zip(self.layers, self.norms):
            message = x.index_select(0, src) + self.edge_type_embedding(edge_type).float()
            aggregated = torch.zeros_like(x)
            aggregated.index_add_(0, dst, message)

            degree = torch.zeros((x.size(0), 1), device=x.device, dtype=x.dtype)
            degree.index_add_(
                0,
                dst,
                torch.ones((dst.size(0), 1), device=x.device, dtype=x.dtype),
            )
            aggregated = aggregated / degree.clamp_min(1.0)
            updated = update_mlp(torch.cat([x, aggregated], dim=-1))
            x = norm(x + updated)
        return x.to(input_dtype)


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
        self.use_film_conditioning = bool(getattr(params, "use_film_conditioning", False))
        self.use_patch_reference_graph = bool(getattr(params, "text_reference_graph", True))
        self.use_sampled_text_conditioning = bool(
            getattr(params, "use_sampled_global_text_conditioning", False)
        )
        self.patch_only_numerical = bool(getattr(params, "patch_only_numerical", False))
        if self.patch_only_numerical and self.use_sampled_text_conditioning:
            raise ValueError(
                "patch_only_numerical=True is incompatible with use_sampled_global_text_conditioning=True"
            )
        self.include_t1_fields = bool(getattr(params, "include_t1_fields", False))
        self.debug_enabled = False
        self._debug_state = {}
        # D experiments: internal node features include t=1 converged field [u,v,w,p,T,unv,udf]=9D
        # Geometry width of the sampled prediction stream. Default 6 [pos(3),unv(3)] or 9 with
        # t1 fields; AirfRANS overrides to 7 [pos(3),SDF(1),unv(3)] via config base_node_dim, so
        # the sampled_x_override (data.x) is accepted and TE-SCA is geometry-matched to numerical SCA.
        base_node_dim = int(getattr(params, "base_node_dim", 9 if self.include_t1_fields else 6))
        base_edge_dim = base_node_dim + 4  # node_diff + pos_diff(3) + norm(1)
        assume_single_graph_batch = getattr(params, "assume_single_graph_batch", False)

        if self.patch_only_numerical:
            self.text_encoder = None
            self.patch_numerical_encoder = PatchNumericalEncoder(
                hidden_size=hidden_size,
                numerical_dim=int(getattr(params, "patch_numerical_dim", 5)),
                dropout=0.0,
            )
            text_output_dim = int(hidden_size)
        else:
            self.patch_numerical_encoder = None
            _use_output_proj = bool(getattr(params, "text_use_output_proj", False))
            _legacy_output_proj = bool(getattr(params, "legacy_text_output_projection", False))
            self.text_encoder = QwenTextConditionEncoder(
                model_name=getattr(params, "text_encoder_name", "sentence-transformers/all-MiniLM-L6-v2"),
                lora_rank=getattr(params, "text_lora_rank", 16),
                lora_alpha=getattr(params, "text_lora_alpha", 32),
                lora_dropout=getattr(params, "text_lora_dropout", 0.05),
                use_gradient_checkpointing=getattr(params, "text_use_gradient_checkpointing", True),
                encode_micro_batch_size=getattr(params, "patch_encode_micro_batch_size", 64),
                random_init=getattr(params, "text_encoder_random_init", False),
                output_dim=int(hidden_size) if _use_output_proj else None,
                legacy_output_projection=_legacy_output_proj,
            )
            text_output_dim = self.text_encoder.output_dim

        if self.use_film_conditioning:
            self.sampled_input_dim = int(hidden_size)
            self.boundary_input_dim = int(hidden_size)
            self.edge_input_dim = int(hidden_size) + 4
        else:
            self.sampled_input_dim = text_output_dim + base_node_dim if self.use_sampled_text_conditioning else base_node_dim
            self.boundary_input_dim = text_output_dim + 6
            self.edge_input_dim = (text_output_dim + base_edge_dim) if self.use_sampled_text_conditioning else base_edge_dim

        self.patch_reference_encoder = PatchReferenceGraphEncoder(
            hidden_size=text_output_dim,
            num_edge_types=getattr(params, "text_reference_type_count", 4),
            num_layers=getattr(params, "text_reference_mp_layers", 1)
            if self.use_patch_reference_graph
            else 0,
        )
        if self.use_film_conditioning:
            film_hidden = max(int(hidden_size), 64)
            self.boundary_geom_proj = _build_small_mlp(6, film_hidden, hidden_size)
            self.sampled_geom_proj = _build_small_mlp(6, film_hidden, hidden_size)
            self.boundary_film = _build_small_mlp(hidden_size, film_hidden, hidden_size * 2)
            self.sampled_film = _build_small_mlp(hidden_size, film_hidden, hidden_size * 2)
            self.boundary_geom_norm = nn.LayerNorm(hidden_size)
            self.sampled_geom_norm = nn.LayerNorm(hidden_size)

        self.encoder = PatchCrossAttentionEncoder(
            hidden_dim=hidden_size,
            num_heads=8,
            slice_num=patch_slice_num,
            edge_input_size=self.edge_input_dim,
            dropout=0.0 if not drop_out else 0.1,
            node_input_size=self.sampled_input_dim,
            boundary_input_dim=self.boundary_input_dim,
            assume_single_graph_batch=assume_single_graph_batch,
            use_internal_gn_block=getattr(params, "use_internal_gn_block_before_slice", False),
            normalize_lift_features=bool(getattr(params, "normalize_lift_features", True)),
            use_patch_pre_norm=bool(getattr(params, "use_patch_pre_norm", True)),
        )

        processor_list = []
        for _ in range(params.attn_processor_num):
            processor_list.append(
                AttnProcessor(
                    message_passing_num=message_passing_num,
                    hidden_size=hidden_size,
                    drop_out=drop_out,
                    assume_single_graph_batch=assume_single_graph_batch,
                )
            )
        self.processpr_list = nn.ModuleList(processor_list)
        self.decoder = OptimizedDecoder(
            hidden_size=hidden_size,
            node_output_size=node_output_size,
        )
        self.node_output_size = node_output_size

    def set_debug_enabled(self, enabled: bool = True):
        self.debug_enabled = enabled
        if hasattr(self.encoder, "set_debug_enabled"):
            self.encoder.set_debug_enabled(enabled)

    def _record_debug(self, name: str, tensor: torch.Tensor):
        if self.debug_enabled:
            self._debug_state[name] = summarize_tensor(tensor)

    def _record_scalar(self, name: str, value):
        if not self.debug_enabled:
            return
        if isinstance(value, torch.Tensor):
            value = value.detach().float().item()
        self._debug_state[name] = float(value)

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

    @staticmethod
    def _build_edge_attr(
        sampled_x: torch.Tensor,
        sampled_pos: torch.Tensor,
        edge_index: torch.Tensor,
    ) -> torch.Tensor:
        senders, receivers = edge_index[0], edge_index[1]
        x_diff = sampled_x[senders] - sampled_x[receivers]
        pos_diff = sampled_pos[senders] - sampled_pos[receivers]
        pos_norm = torch.linalg.vector_norm(pos_diff, dim=-1, keepdim=True)
        return torch.cat([x_diff, pos_diff, pos_norm], dim=-1)

    def _apply_film(
        self,
        text_embeddings: torch.Tensor,
        geom_features: torch.Tensor,
        geom_proj: nn.Module,
        film_module: nn.Module,
        geom_norm: nn.Module,
        prefix: str,
    ) -> torch.Tensor:
        geom_hidden = geom_norm(geom_proj(geom_features))
        gamma_beta = film_module(text_embeddings)
        gamma_raw, beta_raw = gamma_beta.chunk(2, dim=-1)
        gamma = 1.0 + 0.1 * torch.tanh(gamma_raw)
        beta = 0.1 * beta_raw
        out = gamma * geom_hidden + beta

        self._record_debug(f"{prefix}_geom_features", geom_features)
        self._record_debug(f"{prefix}_geom_hidden", geom_hidden)
        self._record_debug(f"{prefix}_gamma", gamma)
        self._record_debug(f"{prefix}_beta", beta)
        self._record_debug(f"{prefix}_film_out", out)
        self._record_scalar(f"{prefix}_gamma_delta_abs_mean", (gamma - 1.0).abs().mean())
        self._record_scalar(f"{prefix}_beta_abs_mean", beta.abs().mean())
        self._record_scalar(f"{prefix}_film_shift_abs_mean", (out - geom_hidden).abs().mean())
        return out

    def forward(
        self,
        sampled_pos,
        sampled_unv,
        edge_index,
        batch,
        boundary_pos,
        boundary_unv,
        boundary_batch,
        boundary_patch_indices,
        patch_unique_input_ids=None,
        patch_unique_attention_mask=None,
        patch_text_inverse_indices=None,
        patch_ref_edge_index=None,
        patch_ref_edge_type=None,
        global_input_ids=None,
        global_attention_mask=None,
        precomputed_patch_embeddings=None,
        precomputed_global_embeddings=None,
        patch_numerical_values=None,
        sampled_x_override=None,
    ):
        self._debug_state = {}
        self._last_unique_patch_embeddings = None
        batch = batch.squeeze(-1) if batch.dim() > 1 else batch
        boundary_batch = (
            boundary_batch.squeeze(-1)
            if boundary_batch.dim() > 1
            else boundary_batch
        )
        boundary_patch_indices = (
            boundary_patch_indices.squeeze(-1)
            if boundary_patch_indices.dim() > 1
            else boundary_patch_indices
        )

        # --- Patch embedding source: numerical OR text (pre-computed OR on-the-fly) ---
        if self.patch_only_numerical:
            if patch_numerical_values is None:
                raise ValueError(
                    "patch_only_numerical=True but patch_numerical_values not provided"
                )
            patch_embeddings = self.patch_numerical_encoder(patch_numerical_values)
            self._record_debug("patch_numerical_values", patch_numerical_values)
            self._record_debug("patch_embeddings_from_numerical", patch_embeddings)
        elif precomputed_patch_embeddings is not None:
            # Pre-computed path: skip text_encoder entirely
            patch_embeddings = precomputed_patch_embeddings
            self._record_debug("precomputed_patch_embeddings", patch_embeddings)
        else:
            # On-the-fly path: run text_encoder (used for inference or legacy mode)
            unique_patch_embeddings = self.text_encoder(
                patch_unique_input_ids,
                patch_unique_attention_mask,
            )
            self._record_debug("unique_patch_embeddings", unique_patch_embeddings)
            self._last_unique_patch_embeddings = unique_patch_embeddings
            patch_embeddings = unique_patch_embeddings.index_select(
                0, patch_text_inverse_indices.reshape(-1).long()
            )

        self._record_debug("patch_embeddings_before_ref_graph", patch_embeddings)
        if patch_embeddings.size(0) > 1:
            self._record_scalar(
                "patch_embedding_cross_patch_std_mean",
                patch_embeddings.float().std(dim=0, unbiased=False).mean(),
            )
        if self.use_patch_reference_graph and patch_ref_edge_index is not None:
            patch_embeddings_before_ref = patch_embeddings
            patch_embeddings = self.patch_reference_encoder(
                patch_embeddings=patch_embeddings,
                patch_ref_edge_index=patch_ref_edge_index,
                patch_ref_edge_type=patch_ref_edge_type,
            )
            self._record_debug("patch_embeddings_after_ref_graph", patch_embeddings)
            self._record_scalar(
                "patch_ref_update_abs_mean",
                (patch_embeddings - patch_embeddings_before_ref).abs().mean(),
            )
        global_embeddings = None
        sampled_global_embeddings = None
        if self.use_sampled_text_conditioning:
            if precomputed_global_embeddings is not None:
                global_embeddings = precomputed_global_embeddings
            else:
                global_embeddings = self.text_encoder(global_input_ids, global_attention_mask)
            self._record_debug("global_embeddings", global_embeddings)
            sampled_global_embeddings = global_embeddings.index_select(0, batch.long())
        boundary_text_embeddings = patch_embeddings.index_select(0, boundary_patch_indices.long())
        self._record_scalar(
            "sampled_text_conditioning_enabled",
            1.0 if self.use_sampled_text_conditioning else 0.0,
        )
        self._record_debug("sampled_global_embeddings", sampled_global_embeddings)
        self._record_debug("boundary_text_embeddings", boundary_text_embeddings)

        if self.use_film_conditioning:
            boundary_geom = torch.cat([boundary_pos, boundary_unv], dim=-1)
            sampled_geom = torch.cat([sampled_pos, sampled_unv], dim=-1)
            boundary_x = self._apply_film(
                text_embeddings=boundary_text_embeddings,
                geom_features=boundary_geom,
                geom_proj=self.boundary_geom_proj,
                film_module=self.boundary_film,
                geom_norm=self.boundary_geom_norm,
                prefix="boundary",
            )
            if self.use_sampled_text_conditioning:
                sampled_x = self._apply_film(
                    text_embeddings=sampled_global_embeddings,
                    geom_features=sampled_geom,
                    geom_proj=self.sampled_geom_proj,
                    film_module=self.sampled_film,
                    geom_norm=self.sampled_geom_norm,
                    prefix="sampled",
                )
            else:
                sampled_x = self.sampled_geom_norm(self.sampled_geom_proj(sampled_geom))
                self._record_debug("sampled_geom_features", sampled_geom)
                self._record_debug("sampled_geom_hidden", sampled_x)
        else:
            self._record_debug("boundary_geom_features", torch.cat([boundary_pos, boundary_unv], dim=-1))
            self._record_debug("sampled_geom_features", torch.cat([sampled_pos, sampled_unv], dim=-1))
            boundary_x = torch.cat(
                [boundary_text_embeddings, boundary_pos, boundary_unv],
                dim=-1,
            )
            if self.use_sampled_text_conditioning:
                sampled_x = torch.cat(
                    [sampled_global_embeddings, sampled_pos, sampled_unv],
                    dim=-1,
                )
            elif sampled_x_override is not None:
                sampled_x = sampled_x_override
            else:
                sampled_x = torch.cat([sampled_pos, sampled_unv], dim=-1)
        self._record_debug("boundary_x", boundary_x)
        self._record_debug("sampled_x", sampled_x)
        edge_attr = self._build_edge_attr(sampled_x, sampled_pos, edge_index)
        self._record_debug("edge_attr", edge_attr)

        x, edge_attr = self.encoder(
            internal_x=sampled_x,
            internal_edge_index=edge_index,
            internal_edge_attr=edge_attr,
            internal_batch=batch,
            patch_x=boundary_x,
            patch_batch=boundary_batch,
        )
        self._record_debug("encoder_out_x", x)
        self._record_debug("encoder_out_edge_attr", edge_attr)

        for idx, model in enumerate(self.processpr_list):
            x, edge_attr = model(x, edge_index, edge_attr, batch)
            if idx == 0 or idx == len(self.processpr_list) - 1:
                self._record_debug(f"processor_{idx}_x", x)
                self._record_debug(f"processor_{idx}_edge_attr", edge_attr)

        pred_cell = self.decoder(x)
        self._record_debug("pred_cell", pred_cell)
        return pred_cell.to(torch.float32)
