"""
Model Importer and Simulator Wrapper

This module provides the Simulator class that wraps a neural network model
with a loss function for training and inference.
"""

import torch
import torch.nn as nn
import torch.nn.functional as F
from typing import List, Optional, Dict, Any
from types import SimpleNamespace
import logging
logger = logging.getLogger(__name__)


def vicreg_variance_covariance_loss(z: torch.Tensor, gamma: float = 1.0) -> torch.Tensor:
    """VICReg variance + covariance regularization (no invariance term).

    Prevents representation collapse by maintaining per-dimension variance
    and decorrelating dimensions.

    Args:
        z: embeddings [N, D], must have grad
        gamma: target std threshold (default 1.0)
    Returns:
        scalar loss
    """
    z = z.float()
    N, D = z.shape
    if N < 2:
        return z.new_zeros(())

    # Variance: push std of each dimension above gamma
    std = torch.sqrt(z.var(dim=0) + 1e-4)
    var_loss = F.relu(gamma - std).mean()

    # Covariance: penalize off-diagonal correlations
    z_centered = z - z.mean(dim=0, keepdim=True)
    cov = (z_centered.T @ z_centered) / (N - 1)
    off_diag = cov.clone()
    off_diag.fill_diagonal_(0)
    cov_loss = off_diag.pow(2).sum() / D

    return var_loss + cov_loss


def compute_embedding_health(z: torch.Tensor) -> Dict[str, Any]:
    """Compute health metrics for text embeddings to detect representation collapse.

    Args:
        z: embedding tensor [N, D]
    Returns:
        dict with metrics: mean_cos_sim, max_cos_sim, std_mean, std_min,
        l2_mean, active_dims, total_dims, eff_rank, status
    """
    z = z.detach().float()
    N, D = z.shape

    if N < 2:
        return {
            "mean_cos_sim": 0.0, "max_cos_sim": 0.0,
            "std_mean": 0.0, "std_min": 0.0,
            "l2_mean": z.norm(dim=1).mean().item() if N == 1 else 0.0,
            "active_dims": 0, "total_dims": D,
            "eff_rank": 1.0, "status": "insufficient_data",
        }

    std_per_dim = z.std(dim=0)
    std_mean = std_per_dim.mean().item()
    std_min = std_per_dim.min().item()
    active_dims = int((std_per_dim > 0.01).sum().item())
    l2_mean = z.norm(dim=1).mean().item()

    sample_n = min(50, N)
    indices = torch.randperm(N)[:sample_n]
    z_sample = F.normalize(z[indices], dim=1)
    cos_matrix = z_sample @ z_sample.T
    mask = ~torch.eye(sample_n, dtype=torch.bool, device=cos_matrix.device)
    mean_cos = cos_matrix[mask].mean().item()
    max_cos = cos_matrix[mask].max().item()

    centered = z - z.mean(dim=0, keepdim=True)
    s = torch.linalg.svdvals(centered)
    s_norm = s / s.sum().clamp(min=1e-10)
    eff_rank = torch.exp(-(s_norm * (s_norm + 1e-10).log()).sum()).item()

    if mean_cos > 0.99:
        status = "collapsed"
    elif mean_cos > 0.9:
        status = "warning"
    else:
        status = "healthy"

    return {
        "mean_cos_sim": mean_cos,
        "max_cos_sim": max_cos,
        "std_mean": std_mean,
        "std_min": std_min,
        "l2_mean": l2_mean,
        "active_dims": active_dims,
        "total_dims": D,
        "eff_rank": eff_rank,
        "status": status,
    }


# Import required modules
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from config import Config
from Utils.key import Key
from NNmodels import create_model
from FVsolver.FVscheme.dataloss import LpLoss
from Main.Data_driven import bc_intervention


from Utils.normalization import KeyBasedNormalizer, PatchNumericalNormalizer
from NNmodels.debug_utils import orthogonality_error, summarize_parameter, summarize_tensor

class Simulator(nn.Module):
    """
    Simulator wrapper that combines a neural network model with a loss function.

    This class wraps:
    - Neural network model (created from model_config)
    - Loss function (LpLoss for data-driven training)

    Args:
        config: Unified configuration object
        input_keys: List of input keys
        output_keys: List of output keys
        target_keys: List of target keys (optional, for training)
    """

    def __init__(
        self,
        config: Config,
        input_keys: List[Key],
        output_keys: List[Key],
        target_keys: Optional[List[Key]] = None,
        mode: str = "train",
    ):
        super().__init__()

        self.config = config
        self.input_keys = input_keys
        self.output_keys = output_keys
        self.target_keys = target_keys
        self.mode = mode
        self.use_text_conditioning = bool(self._get_run_config().get("use_text_conditioning", False))
        self.patch_only_numerical = bool(self._get_run_config().get("patch_only_numerical", False))
        self.patch_numerical_normalize = bool(self._get_run_config().get("patch_numerical_normalize", False))
        self.include_t1_fields = bool(self._get_run_config().get("include_t1_fields", False))
        self.use_precomputed_features = bool(self._get_run_config().get("use_precomputed_features", True))
        active_model_name = self._get_run_config().get("model_name", "")
        # "Baseline" here means single-stream: the model gets the 11D assemble_baseline_node_features
        # vector and NO complete-boundary K/V. TransFVGN_SS_v1 is single-stream, so it belongs on
        # this path — which is also what wires it to the `single_stream_node_bc` intervention site
        # the knockout guardrail needs.
        BASELINE_MODELS = {"Transolver_baseline", "TransolverPP_baseline", "MeshGraphNets_baseline",
                           "AeroGTO_baseline", "GINO_baseline", "GAOT_baseline", "TransFVGN_SS_v1"}
        self._is_baseline = active_model_name in BASELINE_MODELS
        # Edge-consuming baselines get edge_index + edge_attr=[rel(3),|rel|] built by the importer;
        # edge-free operator trunks (GINO/GAOT build their own radius graph) do not.
        # TransFVGN_SS_v1 needs edge_index but rebuilds its own node-diff edge_attr internally (to
        # match the SCA-TransFVGN trunk it is the control for) and ignores the 4D one passed here.
        self._baseline_needs_edges = active_model_name in (
            "MeshGraphNets_baseline", "AeroGTO_baseline", "TransFVGN_SS_v1")
        self.baseline_normalize_inputs = bool(self._get_run_config().get("baseline_normalize_inputs", False))
        self._is_global_cond = (active_model_name == "GlobalCondTransFVGN_v1")
        if self.use_text_conditioning and active_model_name not in (
            "TextCondTransFVGN_v1", "GlobalCondTransFVGN_v1",
            # SCA two-stream trunks also accept a TEXT KV (architecture-matched TE-SCA): the
            # importer feeds the per-face text embedding as patch_numerical_values into the KV.
            # TransSSCA_v1 (DNE-SSCA, concat-self-attn) shares the same two-stream input contract.
            "TextCondTrans_v2", "TextCondTransFVGN_v2", "TextCondTransFVGN_v3", "TransSSCA_v1",
            "AeroGTOSCA_v1",
        ):
            raise ValueError(
                "use_text_conditioning=true requires model_name in "
                "{'TextCondTransFVGN_v1', 'GlobalCondTransFVGN_v1', "
                "'TextCondTrans_v2', 'TextCondTransFVGN_v3'}, "
                f"but got {active_model_name!r}"
            )
        # v2 = SCA + Transolver trunk (renamed TextCondTrans_v2, no GNN); v3 = SCA + FVGN GNN trunk.
        # TransSSCA_v1 (A5, DNE-SSCA) shares v2's pure-Transolver, edge-free two-stream contract —
        # identical forward signature and inputs — so it routes through the same importer branch;
        # only the model-internal conditioning attention (concat-self vs cross) differs.
        self._is_textcond_v2 = active_model_name in (
            "TextCondTrans_v2", "TextCondTransFVGN_v2", "TransSSCA_v1", "AeroGTOSCA_v1")
        self._is_textcond_v3 = (active_model_name == "TextCondTransFVGN_v3")
        _is_aligned_sca = self._is_textcond_v2 or self._is_textcond_v3
        # Which SCA trunks consume the graph. TextCondTransFVGN_v3 has an FVGN message-passing
        # processor; AeroGTOSCA_v1 wraps AeroGTO's Mixer, whose every block does GNN message
        # passing. TextCondTrans_v2 / TransSSCA_v1 are pure attention and take no edges.
        self._sca_needs_edges = active_model_name in ("TextCondTransFVGN_v3", "AeroGTOSCA_v1")
        if self.patch_only_numerical and active_model_name not in (
            "TextCondTransFVGN_v1", "TextCondTrans_v2", "TextCondTransFVGN_v2",
            "TextCondTransFVGN_v3", "TransSSCA_v1", "AeroGTOSCA_v1",
        ):
            raise ValueError(
                "patch_only_numerical=true requires model_name in "
                "{'TextCondTransFVGN_v1', 'TextCondTrans_v2', 'TextCondTransFVGN_v3', "
                "'TransSSCA_v1'}, "
                f"but got {active_model_name!r}"
            )
        if _is_aligned_sca and not (self.patch_only_numerical or self.use_text_conditioning):
            raise ValueError(
                f"{active_model_name} (SCA two-stream) requires patch_only_numerical=true "
                "(numeric KV) or use_text_conditioning=true (text KV) to drive the boundary stream"
            )
        if _is_aligned_sca and bool(
            self._get_run_config().get("baseline_full_boundary", False)
        ):
            if not bool(self._get_run_config().get("sca_full_boundary_ablation", False)):
                raise ValueError(
                    f"{active_model_name} (two-stream SCA) must use baseline_full_boundary=false. "
                    "The prediction stream is a plain k-hop sample of the ORIGINAL cpd_cell array "
                    "(internal + naturally-sampled boundary cpd_cells; still cpd_cell-dim, so the "
                    "decoder output and loss are over a cpd_cell target). "
                    "baseline_full_boundary=true (sampled_internal + ALL boundary) is the SINGLE-STREAM "
                    "Transolver baseline's regime — in two-stream SCA the full boundary enters as the "
                    "cross-attention KV conditioning (all boundary patch, per-face), NOT the prediction set. "
                    "To deliberately run the SCA-EPD fairness ablation (prediction stream = sampled_internal "
                    "+ ALL boundary, matching the single-stream baseline's supervision, with full boundary "
                    "ALSO entering as cross-attn KV), set sca_full_boundary_ablation=true to opt in."
                )
            logger.warning(
                f"⚠️ SCA-EPD ablation ENABLED ({active_model_name}): baseline_full_boundary=true on a "
                "two-stream SCA model. The prediction stream (Encoder→Processor→Decoder + loss target) is "
                "sampled_internal + ALL boundary (full-boundary supervision, baseline-matched); the full "
                "boundary ALSO enters as cross-attention KV. This is the deliberate conditioning-fairness "
                "variant (SCA-vs-concat at equal full-boundary supervision) — it FORGOES the SCA memory "
                "advantage because the trunk/decoder now hold all boundary, like the single-stream baseline."
            )
        if self.patch_only_numerical and self.use_text_conditioning:
            raise ValueError(
                "patch_only_numerical=true and use_text_conditioning=true are mutually exclusive"
            )

        # Create model from config
        self.model = self._create_model()

        # Create loss function from config
        self.loss = self._create_loss()
        
        # Initialize KeyBasedNormalizers
        # These will handle registration of sub-normalizers and application logic
        self.target_normalizer = KeyBasedNormalizer(target_keys) if target_keys else None
        self.output_denormalizer = KeyBasedNormalizer(output_keys) if output_keys else None

        # Input normalizer: use target_keys normalizers for input physical fields
        # This ensures input and output are in the same normalized space
        # Important for generalization: model learns mapping in normalized space
        self.input_normalizer = KeyBasedNormalizer(target_keys) if target_keys else None

        # Patch-specific normalizer: uses boundary patch statistics instead of
        # dataset-wide stats. Loaded from patch_statistics.json in dataset dir.
        self.patch_normalizer = None
        if self.patch_numerical_normalize:
            dataset_path = self._get_run_config().get("dataset_path", "")
            patch_stats_path = os.path.join(dataset_path, "patch_statistics.json")
            if os.path.exists(patch_stats_path):
                self.patch_normalizer = PatchNumericalNormalizer.from_json(patch_stats_path)
                logger.info(f"Loaded patch-specific normalizer from {patch_stats_path}")
            else:
                logger.warning(
                    f"patch_numerical_normalize=true but {patch_stats_path} not found. "
                    "Falling back to dataset-wide input_normalizer."
                )

        # Number of physical field channels to normalize. Field-set aware: 5 for the default
        # GTC/Slab [u,v,w,p,T], 4 for AirfRANS [u,v,p,nut].
        from FVdomain.HomoGraph_dataset import resolve_field_set
        self.field_set = str(self._get_run_config().get("field_set", "uvwpT"))
        self.num_physical_channels = resolve_field_set(self.field_set)["output_size"]
        self.debug_enabled = False
        self._debug_state = {}
        
        self.initialize_weights()
        self._maybe_compile_model()

    def initialize_weights(self):
        # Skip text encoder - it has pretrained weights that should not be re-initialized
        text_encoder = None
        if self.use_text_conditioning and hasattr(self.model, 'text_encoder'):
            text_encoder = self.model.text_encoder
        for name, module in self.named_modules():
            if text_encoder is not None and module is text_encoder:
                continue
            # Skip any child of text_encoder
            if text_encoder is not None and any(
                module is child for child in text_encoder.modules()
            ):
                continue
            self._init_weights(module)
        self._restore_custom_initializations()

    def _init_weights(self, m):
        if isinstance(m, nn.Linear):
            nn.init.trunc_normal_(m.weight, std=0.02)
            if isinstance(m, nn.Linear) and m.bias is not None:
                nn.init.constant_(m.bias, 0)
        elif isinstance(m, (nn.LayerNorm, nn.BatchNorm1d)):
            nn.init.constant_(m.bias, 0)
            nn.init.constant_(m.weight, 1.0)

    def _restore_custom_initializations(self):
        for module in self.modules():
            if module is self:
                continue
            if hasattr(module, 'reset_custom_initialization'):
                module.reset_custom_initialization()

    def set_debug_enabled(self, enabled: bool = True):
        self.debug_enabled = enabled
        if hasattr(self.model, 'set_debug_enabled'):
            self.model.set_debug_enabled(enabled)
        elif hasattr(self.model, 'encoder') and hasattr(self.model.encoder, 'set_debug_enabled'):
            self.model.encoder.set_debug_enabled(enabled)

    def _record_debug(self, name: str, tensor: torch.Tensor):
        if self.debug_enabled:
            self._debug_state[name] = summarize_tensor(tensor)

    def peek_debug_state(self):
        state = dict(self._debug_state)
        if hasattr(self.model, 'peek_debug_state'):
            state["model"] = self.model.peek_debug_state()
        elif hasattr(self.model, 'encoder') and hasattr(self.model.encoder, 'peek_debug_state'):
            state["patch_encoder"] = self.model.encoder.peek_debug_state()
        return state

    def consume_debug_state(self):
        state = self.peek_debug_state()
        self._debug_state = {}
        if hasattr(self.model, 'consume_debug_state'):
            state["model"] = self.model.consume_debug_state()
        elif hasattr(self.model, 'encoder') and hasattr(self.model.encoder, 'consume_debug_state'):
            state["patch_encoder"] = self.model.encoder.consume_debug_state()
        return state

    def describe_initialization(self):
        summaries = {}
        named_params = dict(self.named_parameters())
        for name in [
            "model.encoder.patch_in_project_slice.weight",
            "model.encoder.internal_in_project_slice.weight",
            "model.processpr_list.0.TransBlock.Attn.in_project_slice.weight",
        ]:
            if name in named_params:
                summaries[name] = summarize_parameter(named_params[name])
                summaries[name]["orthogonality_error"] = orthogonality_error(named_params[name])
        return summaries
            
    def _create_model(self) -> nn.Module:
        """Create neural network model from configuration"""
        # Get model config directly from config (already resolved in Config.__post_init__)
        model_config = self.config.model_config

        if model_config is None:
            raise ValueError("No model configuration found in config.model_config")

        effective_batch_size = 1
        if self.mode == "train":
            effective_batch_size = int(self.config.training_config.get("batch_size", 1))
        elif self.mode == "inference":
            effective_batch_size = int(self.config.inference_config.get("tta_batch_size", 1))

        assume_single_graph_batch = (effective_batch_size == 1)
        if isinstance(model_config, dict):
            model_config = dict(model_config)
            model_config["assume_single_graph_batch"] = assume_single_graph_batch
            model_config["include_t1_fields"] = self.include_t1_fields
            model_config["patch_only_numerical"] = self.patch_only_numerical
            if self.use_text_conditioning:
                run_config = self._get_run_config()
                model_config["text_encoder_name"] = run_config.get("text_encoder_name", "sentence-transformers/all-MiniLM-L6-v2")
                model_config["text_lora_rank"] = int(run_config.get("text_lora_rank", 16))
                model_config["text_lora_alpha"] = int(run_config.get("text_lora_alpha", 32))
                model_config["text_lora_dropout"] = float(run_config.get("text_lora_dropout", 0.05))
                model_config["text_use_gradient_checkpointing"] = bool(
                    run_config.get("text_use_gradient_checkpointing", True)
                )
                model_config["text_reference_graph"] = bool(
                    run_config.get("text_reference_graph", True)
                )
                model_config["text_reference_mp_layers"] = int(
                    run_config.get("text_reference_mp_layers", 1)
                )
                model_config["use_sampled_global_text_conditioning"] = bool(
                    run_config.get("use_sampled_global_text_conditioning", False)
                )
                dataset_config = getattr(self.config, "dataset_config", {})
                if isinstance(dataset_config, dict):
                    bc_fields = list(dataset_config.get("text_bc_fields", ["U", "p_rgh", "T"]))
                else:
                    bc_fields = ["U", "p_rgh", "T"]
                model_config["text_reference_type_count"] = max(1, len(bc_fields) + 1)
                model_config["patch_encode_micro_batch_size"] = int(
                    run_config.get("patch_encode_micro_batch_size", 64)
                )
                model_config["normalize_lift_features"] = bool(
                    run_config.get("normalize_lift_features", True)
                )
                model_config["use_patch_pre_norm"] = bool(
                    run_config.get("use_patch_pre_norm", True)
                )
                model_config["text_use_output_proj"] = bool(
                    run_config.get("text_use_output_proj", False)
                )
                model_config["legacy_text_output_projection"] = bool(
                    run_config.get("legacy_text_output_projection", False)
                )
        else:
            config_dict = vars(model_config).copy()
            config_dict["assume_single_graph_batch"] = assume_single_graph_batch
            config_dict["include_t1_fields"] = self.include_t1_fields
            config_dict["patch_only_numerical"] = self.patch_only_numerical
            if self.use_text_conditioning:
                run_config = self._get_run_config()
                config_dict["text_encoder_name"] = run_config.get("text_encoder_name", "sentence-transformers/all-MiniLM-L6-v2")
                config_dict["text_lora_rank"] = int(run_config.get("text_lora_rank", 16))
                config_dict["text_lora_alpha"] = int(run_config.get("text_lora_alpha", 32))
                config_dict["text_lora_dropout"] = float(run_config.get("text_lora_dropout", 0.05))
                config_dict["text_use_gradient_checkpointing"] = bool(
                    run_config.get("text_use_gradient_checkpointing", True)
                )
                config_dict["text_reference_graph"] = bool(
                    run_config.get("text_reference_graph", True)
                )
                config_dict["text_reference_mp_layers"] = int(
                    run_config.get("text_reference_mp_layers", 1)
                )
                config_dict["use_sampled_global_text_conditioning"] = bool(
                    run_config.get("use_sampled_global_text_conditioning", False)
                )
                dataset_config = getattr(self.config, "dataset_config", {})
                if isinstance(dataset_config, dict):
                    bc_fields = list(dataset_config.get("text_bc_fields", ["U", "p_rgh", "T"]))
                else:
                    bc_fields = ["U", "p_rgh", "T"]
                config_dict["text_reference_type_count"] = max(1, len(bc_fields) + 1)
                config_dict["patch_encode_micro_batch_size"] = int(
                    run_config.get("patch_encode_micro_batch_size", 64)
                )
                config_dict["normalize_lift_features"] = bool(
                    run_config.get("normalize_lift_features", True)
                )
                config_dict["use_patch_pre_norm"] = bool(
                    run_config.get("use_patch_pre_norm", True)
                )
                config_dict["text_use_output_proj"] = bool(
                    run_config.get("text_use_output_proj", False)
                )
                config_dict["legacy_text_output_projection"] = bool(
                    run_config.get("legacy_text_output_projection", False)
                )
            model_config = SimpleNamespace(**config_dict)

        logger.info(
            "🧩 Model batching policy: batch_size=%s -> assume_single_graph_batch=%s",
            effective_batch_size,
            assume_single_graph_batch,
        )

        # Create model using the factory function
        model = create_model(model_config)

        # Tag the slice-pooling modules with their qualified names, so a captured basis is
        # keyed layer-by-layer and can only be injected back into the same layer. Done on
        # the UNWRAPPED model, before any DDP/compile wrapper renames anything. No-op (and
        # no module walk at all) unless SCANO_BASIS_CAPTURE/INJECT is set.
        try:
            from . import slice_basis
        except ImportError:
            import slice_basis
        slice_basis.assign_site_ids(model)

        return model

    def _get_run_config(self):
        if self.mode == "train":
            return self.config.training_config
        if self.mode == "inference":
            return self.config.inference_config
        raise ValueError(f"Unknown simulator mode: {self.mode}")

    def _maybe_compile_model(self):
        run_config = self._get_run_config()
        if not bool(run_config.get("enable_compile", False)):
            return

        if not hasattr(torch, "compile"):
            raise RuntimeError("torch.compile is not available in this PyTorch build")

        compile_kwargs = {}
        backend = run_config.get("compile_backend", "inductor")
        if backend:
            compile_kwargs["backend"] = backend

        mode = run_config.get("compile_mode", "default")
        if mode:
            compile_kwargs["mode"] = mode

        if "compile_dynamic" in run_config:
            compile_kwargs["dynamic"] = bool(run_config.get("compile_dynamic"))

        if "compile_fullgraph" in run_config:
            compile_kwargs["fullgraph"] = bool(run_config.get("compile_fullgraph"))

        logger.info("🚀 Wrapping model with torch.compile(%s)", compile_kwargs)
        self.model = torch.compile(self.model, **compile_kwargs)

    @staticmethod
    def _unwrap_model(model: nn.Module) -> nn.Module:
        from torch.nn.parallel import DistributedDataParallel as DDP

        unwrapped = model
        while True:
            if isinstance(unwrapped, DDP):
                unwrapped = unwrapped.module
                continue
            if hasattr(unwrapped, "_orig_mod"):
                unwrapped = unwrapped._orig_mod
                continue
            return unwrapped

    @staticmethod
    def _normalize_state_dict_keys(state_dict):
        normalized = {}
        for key, value in state_dict.items():
            normalized_key = key
            while normalized_key.startswith("module.") or normalized_key.startswith("_orig_mod."):
                if normalized_key.startswith("module."):
                    normalized_key = normalized_key[len("module."):]
                    continue
                if normalized_key.startswith("_orig_mod."):
                    normalized_key = normalized_key[len("_orig_mod."):]
                    continue
            normalized[normalized_key] = value
        return normalized

    def _create_loss(self) -> nn.Module:
        """
        Create loss function from configuration.

        For data-driven mode, we use LpLoss (Relative L2 Loss) directly.
        """
        # Create LpLoss (uses default size_average=True, reduction=True)
        loss = LpLoss()

        logger.info(f"✅ Created LpLoss - Relative L2 Loss for data-driven training")

        return loss

    def update_edge_attr(self, data):
        """
        Update edge attributes with relative features and positions.

        Computes edge attributes as: [rel_node_features, rel_pos, norm(rel_pos)]

        Args:
            data: Data object containing node features (x), positions (cpd_centroid), and connectivity (edge_index)

        Returns:
            Updated data object with edge_attr computed
        """
        # Extract edge connectivity
        edge_index = data.edge_index  # [2, num_edges]
        senders, receivers = edge_index[0], edge_index[1]

        # Compute relative node features (e.g., rel_u, rel_v, rel_w, rel_p, rel_T)
        x_diff = data.x[senders] - data.x[receivers]  # [num_edges, num_channels]

        # Compute relative positions
        pos_diff = data.cpd_centroid[senders] - data.cpd_centroid[receivers]  # [num_edges, 3]

        # Compute distance norm (L2 norm of relative position)
        pos_norm = torch.linalg.vector_norm(pos_diff, dim=-1, keepdim=True)  # [num_edges, 1]

        # Concatenate all edge attributes: [rel_features, rel_pos(3D), norm(rel_pos)(1D)]
        # Total dimensions: node_features_dim + 4
        data.edge_attr = torch.cat([x_diff, pos_diff, pos_norm], dim=-1)

        return data

    def forward(self, data):
        """
        Forward pass through the model.

        Args:
            data: Input data object

        Returns:
            Model prediction
        """
        self._debug_state = {}
        self._record_debug("input_x_raw", data.x)
        if hasattr(data, 'init_patch_phi_cpd_cell') and data.init_patch_phi_cpd_cell is not None:
            self._record_debug("input_patch_raw", data.init_patch_phi_cpd_cell)

        if self._is_baseline:
            if self.field_set == "airfrans":
                # AirfRANS single-stream baseline: 9D = [pos(3), SDF(1), unv(3)] (data.x, the same
                # 7D geometry stream the SCA arms predict) + freestream(2) broadcast to every node.
                # The freestream is the official AirfRANS inlet-velocity input (a per-case global,
                # known at inference); broadcasting it to all nodes is the standard baseline encoding.
                # NO field values enter the input (the field is the target). node_input_size=9.
                fs = data.freestream_vec.to(data.x.dtype)  # [B, 2]
                batch = data.batch if getattr(data, "batch", None) is not None else \
                    torch.zeros(data.x.shape[0], dtype=torch.long, device=data.x.device)
                x_node = torch.cat([data.x, fs[batch]], dim=-1)  # [N, 9]
            else:
                from NNmodels.baseline_features import assemble_baseline_node_features
                normalizer = self.input_normalizer if (self.baseline_normalize_inputs and self.input_normalizer is not None) else None
                x_node = assemble_baseline_node_features(
                    cpd_centroid=data.cpd_centroid,
                    unv_cpd_cell=data.unv_cpd_cell,
                    phi=data.x[:, :self.num_physical_channels],
                    mask_internal_cell=data.mask_internal_cell,
                    normalizer=normalizer,
                )
                # The single-stream node feature is [pos(3), unv(3), bc(5)] with bc zeroed on
                # interior rows, so the boundary condition lives in cols 6:11 of the boundary
                # rows and nowhere else. Intervene there only: touching pos/unv would ablate
                # the GEOMETRY, and geometry alone already explains most of this dataset.
                x_node = bc_intervention.apply(
                    x_node,
                    site="single_stream_node_bc",
                    rows=~data.mask_internal_cell.reshape(-1).bool(),
                    cols=slice(6, 11),
                )
            self._record_debug("baseline_x_node", x_node)
            model_inputs = {"x": x_node, "batch": data.batch}
            if self._baseline_needs_edges:
                pos = data.cpd_centroid
                s, r = data.edge_index[0], data.edge_index[1]
                rel = pos[s] - pos[r]
                model_inputs["edge_index"] = data.edge_index
                model_inputs["edge_attr"] = torch.cat(
                    [rel, torch.linalg.vector_norm(rel, dim=-1, keepdim=True)], dim=-1)
            return self.model(**model_inputs)

        if self._is_textcond_v2 or self._is_textcond_v3:
            # Input-aligned SCA models: per-FACE raw BC values [N_boundary_faces, 5]
            # (patch_numerical_per_face=true); the model concatenates
            # [boundary_pos, boundary_unv, values] = 11D — value-identical to the Transolver
            # baseline's boundary rows. Deliberately NO normalizer here.
            # v2 (TextCondTrans_v2): pure Transolver trunk, consumes NO edges.
            # v3 (TextCondTransFVGN_v3): FVGN GNN+attention processor trunk, needs edge_index.
            # TE-SCA on the Transolver two-stream trunk (architecture-matched to numerical SCA):
            # replace the per-face NUMERIC BC values with the per-face TEXT embedding, so the ONLY
            # difference from numerical SCA is the KV content (text vs freestream), not the trunk.
            # The case/patch embedding is broadcast to each boundary face via boundary_patch_indices.
            # Requires boundary_input_size = 6 + D_text in the config (e.g. 390 for MiniLM-384).
            if self.use_text_conditioning and getattr(data, "precomputed_patch_embeddings", None) is not None:
                _emb = data.precomputed_patch_embeddings
                _idx = data.boundary_patch_indices.long().view(-1)
                patch_kv_values = _emb.index_select(0, _idx).to(data.cpd_centroid.dtype)
            else:
                patch_kv_values = data.patch_numerical_values
                # z-score the NUMERIC boundary K/V field with the SAME input_normalizer used for
                # the query field below. Cross-attention scores Q·K, so if Q's field is z-scored
                # (unit scale) and K/V's field is raw (T~300, u~0.1, three orders apart) the dot
                # product is dominated by the raw T dimension and the attention is meaningless —
                # and measured to be INERT: an un-normalised numeric K/V trained to be ignored
                # entirely (knockout delta +0.01%), while a z-scored one is live (+136%). Text
                # K/V (the branch above) is already a learned embedding, not a physical field, so
                # it is left alone.
                if self.baseline_normalize_inputs and self.input_normalizer is not None:
                    patch_kv_values = self.input_normalizer(patch_kv_values)
            # DIAGNOSTIC (env-gated, default OFF). GTC_ABLATE_KV is the legacy zero-only switch,
            # kept so old commands keep working; GTC_BC_INTERVENTION is the one to use — it also
            # covers shuffle/scale, reaches the other three boundary branches, and refuses to
            # finish silently if it never fired.
            # Truthy-string check (not bare get): with bare truthiness GTC_ABLATE_KV=0 would be
            # TRUTHY and turn the ablation ON — the opposite of the sibling flags (GTC_SMOKE_MAX_STEPS,
            # GTC_MAX_CASES, GTC_BC_INTERVENTION) which all treat "0"/"off"/"none"/"false" as OFF.
            if (os.environ.get("GTC_ABLATE_KV") or "").strip().lower() not in ("", "0", "off", "none", "false"):
                patch_kv_values = torch.zeros_like(patch_kv_values)
            # Every row of the K/V tensor IS a boundary face, so no row mask is needed. The
            # boundary geometry (pos/unv) travels separately and is left intact by 'zero' —
            # 'zero' asks "does the model use the boundary VALUES", and wiping pos/unv would
            # instead be asking "does it use the boundary at all". Both questions matter, and
            # on this dataset they have different answers, so they get different modes.
            patch_kv_values = bc_intervention.apply(patch_kv_values, site="sca_kv_values")
            _b_pos, _b_unv = bc_intervention.apply_geometry(
                data.boundary_pos, data.boundary_unv, site="sca_kv_geometry")
            # Completeness test: restrict the K/V to a fraction of the boundary. Must subset
            # values, pos, unv and batch with ONE index set, so a surviving face keeps its own
            # data; hence it happens here rather than inside apply().
            _b_batch = data.boundary_batch
            patch_kv_values, _b_pos, _b_unv, _b_batch = bc_intervention.subset_boundary(
                patch_kv_values, _b_pos, _b_unv, _b_batch, site="sca_kv_subset",
                patch_index=getattr(data, "boundary_patch_indices", None))
            model_inputs = {
                'sampled_pos': data.cpd_centroid,
                'sampled_unv': data.unv_cpd_cell,
                'batch': data.batch,
                'boundary_pos': _b_pos,
                'boundary_unv': _b_unv,
                'boundary_batch': _b_batch,
                'patch_numerical_values': patch_kv_values,
            }
            if self._sca_needs_edges:
                model_inputs['edge_index'] = data.edge_index
            # Baseline-matched-input ablation (include_t1_fields=true): feed the SCA
            # prediction stream the EXACT 11D feature the single-stream Transolver baseline
            # consumes — assemble_baseline_node_features = [cpd_centroid(3), unv(3), bc(5)]
            # where bc is the raw unconverged-t1 field with INTERNAL rows ZEROED (only
            # boundary rows carry BC values in-stream). This is byte-identical to the
            # baseline's data.x re-assembly (importer baseline branch above), RAW (no
            # normalization — baseline_normalize_inputs is false). The full boundary STILL
            # ALSO enters as the cross-attention KV, so SCA-EPD-matched differs from
            # baseline_FULL ONLY by the added KV bridge: a clean conditioning-route isolation.
            # NOTE: feeding raw data.x (=[init_phi, unv, udf], 9D) here would be WRONG — it
            # leaks the t1 field to internal nodes (baseline zeroes them) and drops positions.
            # Requires internal_input_size=11 in the model config.
            if self.include_t1_fields:
                from NNmodels.baseline_features import assemble_baseline_node_features
                # Same input_normalizer as the K/V above, so the query field and the K/V field
                # share ONE normalized space — the precondition for Q·K to be meaningful. Under
                # WithNonConvergedT1 the two streams' 11D tensors are the same physical field, so
                # normalizing them identically is the correct, and the only consistent, choice.
                _q_norm = self.input_normalizer if self.baseline_normalize_inputs else None
                model_inputs['sampled_x_override'] = assemble_baseline_node_features(
                    cpd_centroid=data.cpd_centroid,
                    unv_cpd_cell=data.unv_cpd_cell,
                    phi=data.x[:, :self.num_physical_channels],
                    mask_internal_cell=data.mask_internal_cell,
                    normalizer=_q_norm,
                )
            elif self.field_set in ("airfrans", "olbc"):
                # AirfRANS prediction stream = 7D geometry [pos(3), SDF(1), unv(3)]; olbc = 5D
                # [pos(3), SDF(1), source_f(1)] (data.x from HomoGraphDataset's per-field branch).
                # SDF keeps the SCA stream geometry-matched to the baseline; the BC condition is
                # carried ONLY by the boundary KV. internal_input_size = 7 (airfrans) / 5 (olbc).
                model_inputs['sampled_x_override'] = data.x
            self._record_debug("sca_patch_numerical_values", patch_kv_values)
            # One-shot pre-flight (GTC_ZSCORE_SMOKE=1): prove the field is actually z-scored to
            # unit scale in BOTH streams before committing to a 30h run. A raw K/V shows a T-column
            # std ~12 (physical Kelvin); a correctly z-scored one shows ~1. This is the check that
            # would have caught the dead-bridge run before it burned 30 hours.
            if os.environ.get("GTC_ZSCORE_SMOKE") and not getattr(self, "_zscore_smoke_done", False):
                self._zscore_smoke_done = True
                kv = patch_kv_values.detach().float()
                qf = model_inputs.get('sampled_x_override')
                logger.info("🧪 ZSCORE SMOKE (one-shot):")
                logger.info(f"    K/V field per-channel std : {kv.std(dim=0).tolist()}")
                if qf is not None and qf.shape[1] >= 11:
                    bnd = ~data.mask_internal_cell.reshape(-1).bool()
                    if bool(bnd.any()):
                        qbf = qf[bnd, 6:11].detach().float()
                        logger.info(f"    Q field (boundary rows) std: {qbf.std(dim=0).tolist()}")
                logger.info("    (raw would show the T column ~12; z-scored ~1)")
            return self.model(**model_inputs)

        if self.patch_only_numerical:
            # A2: TextCondTransFVGN_v1 framework with patch numerical encoder instead of text.
            # Same cross-attn path as C1/C2, only the patch→embedding source differs.
            patch_vals = data.patch_numerical_values
            if self.patch_numerical_normalize:
                if self.patch_normalizer is not None:
                    patch_vals = self.patch_normalizer(patch_vals)
                elif self.input_normalizer is not None:
                    patch_vals = self.input_normalizer(patch_vals)
            # Applied AFTER normalization, so 'zero' means "the model is handed no boundary
            # value", not "the model is handed the value that happens to z-score to zero"
            # (which is the dataset mean — a very different, and much weaker, ablation).
            patch_vals = bc_intervention.apply(patch_vals, site="patch_only_numerical_kv")
            _b_pos, _b_unv = bc_intervention.apply_geometry(
                data.boundary_pos, data.boundary_unv, site="patch_only_numerical_geometry")
            model_inputs = {
                'sampled_pos': data.cpd_centroid,
                'sampled_unv': data.unv_cpd_cell,
                'edge_index': data.edge_index,
                'batch': data.batch,
                'boundary_pos': _b_pos,
                'boundary_unv': _b_unv,
                'boundary_batch': data.boundary_batch,
                'boundary_patch_indices': data.boundary_patch_indices,
                'patch_numerical_values': patch_vals,
            }
            if hasattr(data, 'patch_ref_edge_index') and data.patch_ref_edge_index is not None:
                model_inputs['patch_ref_edge_index'] = data.patch_ref_edge_index
                model_inputs['patch_ref_edge_type'] = data.patch_ref_edge_type
            if self.field_set == "airfrans":
                # AirfRANS prediction stream = 7D geometry [pos(3), SDF(1), unv(3)] (data.x),
                # geometry-matched to the v1 TEXT arm and the v2 numerical SCA. Without this the
                # v1 model builds a 6D [pos, unv] stream (drops SDF), shape-mismatching
                # base_node_dim=7 and making the v1-numeric-vs-v1-text comparison geometry-unfair.
                model_inputs['sampled_x_override'] = data.x
            # SCANO_BND_PROBE=1 — per-DRAW audit of the boundary (K/V) stream against the
            # prediction (Q) stream. Answers, with numbers rather than reading, whether
            # restricting the pivot pool to a box also truncated the complete-boundary stream
            # the two-stream design depends on. Default off -> no cost, no behaviour change.
            if os.environ.get("SCANO_BND_PROBE"):
                _n = getattr(self, "_bnd_probe_n", 0) + 1
                self._bnd_probe_n = _n
                _bpi = model_inputs['boundary_patch_indices'].reshape(-1)
                _bp = model_inputs['boundary_pos']
                _pv = model_inputs['patch_numerical_values']
                _piv = getattr(data, 'sampled_pivot_count', None)
                _piv = int(_piv.sum()) if _piv is not None else -1
                logger.info(
                    f"[BNDPROBE] draw={_n:04d} pivots={_piv} subgraph_nodes={data.cpd_centroid.shape[0]} "
                    f"bnd_rows={_bp.shape[0]} patches={int(_bpi.max())+1 if _bpi.numel() else 0} "
                    f"patch_ids_present={int(torch.unique(_bpi).numel())} "
                    f"kv_shape={tuple(_pv.shape)} "
                    f"bnd_pos_sum={_bp.double().sum().item():.6f} "
                    f"kv_sum={_pv.double().sum().item():.6f} "
                    f"bnd_bbox=[{_bp[:,0].min():.3f},{_bp[:,1].min():.3f},{_bp[:,2].min():.3f} -> "
                    f"{_bp[:,0].max():.3f},{_bp[:,1].max():.3f},{_bp[:,2].max():.3f}] "
                    f"q_bbox=[{data.cpd_centroid[:,0].min():.3f},{data.cpd_centroid[:,1].min():.3f},"
                    f"{data.cpd_centroid[:,2].min():.3f} -> {data.cpd_centroid[:,0].max():.3f},"
                    f"{data.cpd_centroid[:,1].max():.3f},{data.cpd_centroid[:,2].max():.3f}]"
                )
            self._record_debug("sampled_pos", data.cpd_centroid)
            self._record_debug("sampled_unv", data.unv_cpd_cell)
            self._record_debug("boundary_pos", data.boundary_pos)
            self._record_debug("boundary_unv", data.boundary_unv)
            self._record_debug("patch_numerical_values", patch_vals)
            return self.model(**model_inputs)

        if self.use_text_conditioning:
            if self._is_global_cond:
                # Method D: Global embedding broadcast — no boundary geometry needed
                model_inputs = {
                    'sampled_pos': data.cpd_centroid,
                    'sampled_unv': data.unv_cpd_cell,
                    'edge_index': data.edge_index,
                    'batch': data.batch,
                }
                if hasattr(data, 'precomputed_global_embeddings') and data.precomputed_global_embeddings is not None:
                    model_inputs['precomputed_global_embeddings'] = data.precomputed_global_embeddings
                else:
                    model_inputs['global_input_ids'] = data.global_input_ids
                    model_inputs['global_attention_mask'] = data.global_attention_mask
                self._record_debug("sampled_pos", data.cpd_centroid)
                self._record_debug("sampled_unv", data.unv_cpd_cell)
                return self.model(**model_inputs)

            # Existing TextCond path (unchanged below)
            model_inputs = {
                'sampled_pos': data.cpd_centroid,
                'sampled_unv': data.unv_cpd_cell,
                'edge_index': data.edge_index,
                'batch': data.batch,
                'boundary_pos': data.boundary_pos,
                'boundary_unv': data.boundary_unv,
                'boundary_batch': data.boundary_batch,
                'boundary_patch_indices': data.boundary_patch_indices,
            }
            # Cold-start: ignore precomputed embeddings to force online text encoder forward
            if not self.use_precomputed_features:
                data.precomputed_patch_embeddings = None
            # Pre-computed text embeddings path (training with precompute=true)
            if hasattr(data, 'precomputed_patch_embeddings') and data.precomputed_patch_embeddings is not None:
                # The text arms' boundary K/V is the MiniLM embedding of each patch's OpenFOAM
                # boundary text. That embedding IS the boundary condition here, so it is the
                # thing to intervene on — there is no separate numeric channel to reach.
                data.precomputed_patch_embeddings = bc_intervention.apply(
                    data.precomputed_patch_embeddings, site="text_patch_embeddings_kv")
                model_inputs['precomputed_patch_embeddings'] = data.precomputed_patch_embeddings
                if hasattr(data, 'precomputed_global_embeddings') and data.precomputed_global_embeddings is not None:
                    model_inputs['precomputed_global_embeddings'] = data.precomputed_global_embeddings
                # Still pass ref graph data for trainable PatchReferenceGraphEncoder
                if hasattr(data, 'patch_ref_edge_index') and data.patch_ref_edge_index is not None:
                    model_inputs['patch_ref_edge_index'] = data.patch_ref_edge_index
                    model_inputs['patch_ref_edge_type'] = data.patch_ref_edge_type
            else:
                # On-the-fly text encoding path (inference or legacy)
                model_inputs['patch_unique_input_ids'] = data.patch_unique_input_ids
                model_inputs['patch_unique_attention_mask'] = data.patch_unique_attention_mask
                model_inputs['patch_text_inverse_indices'] = data.patch_text_inverse_indices
                model_inputs['patch_ref_edge_index'] = data.patch_ref_edge_index
                model_inputs['patch_ref_edge_type'] = data.patch_ref_edge_type
                model_inputs['global_input_ids'] = data.global_input_ids
                model_inputs['global_attention_mask'] = data.global_attention_mask
            # D1/D2: pass pre-assembled node features [init_phi, unv, udf]=9D as override
            if self.include_t1_fields:
                x = data.x
                if self.input_normalizer is not None:
                    x_physical = x[:, :self.num_physical_channels]
                    x_geometric = x[:, self.num_physical_channels:]
                    x = torch.cat([self.input_normalizer(x_physical), x_geometric], dim=-1)
                model_inputs['sampled_x_override'] = x
            elif self.field_set == "airfrans":
                # TE-SCA prediction stream = 7D geometry [pos(3), SDF(1), unv(3)] (data.x), matching
                # the numerical-SCA stream so Claim 2 (TE-SCA vs numerical-SCA) is geometry-fair;
                # the condition enters only via the boundary text-embedding KV. sampled_input_dim=7.
                model_inputs['sampled_x_override'] = data.x
            self._record_debug("sampled_pos", data.cpd_centroid)
            self._record_debug("sampled_unv", data.unv_cpd_cell)
            self._record_debug("boundary_pos", data.boundary_pos)
            self._record_debug("boundary_unv", data.boundary_unv)
            self._record_debug("boundary_patch_indices", data.boundary_patch_indices)
            return self.model(**model_inputs)

        # Normalize input physical fields (u, v, w, p, T) for better generalization
        # The input x has shape [N, node_input_size] where first 5 channels are [u, v, w, p, T]
        # Remaining channels are [unv (3), udf (1)] which should not be normalized
        if self.input_normalizer is not None:
            # Separate physical fields from geometric features
            x_physical = data.x[:, :self.num_physical_channels]  # [u, v, w, p, T]
            x_geometric = data.x[:, self.num_physical_channels:]  # [unv, udf]
            self._record_debug("input_x_physical_raw", x_physical)
            self._record_debug("input_x_geometric_raw", x_geometric)

            # Normalize physical fields
            x_physical_norm = self.input_normalizer(x_physical)
            self._record_debug("input_x_physical_norm", x_physical_norm)

            # Concatenate back
            data.x = torch.cat([x_physical_norm, x_geometric], dim=-1)
            self._record_debug("input_x_after_norm", data.x)

            # Also normalize patch physical fields (init_patch_phi_cpd_cell)
            # Shape: [N_patch, boundary_input_dim] where first 5 are [u, v, w, p, T]
            if hasattr(data, 'init_patch_phi_cpd_cell') and data.init_patch_phi_cpd_cell is not None:
                patch_physical = data.init_patch_phi_cpd_cell[:, :self.num_physical_channels]
                patch_other = data.init_patch_phi_cpd_cell[:, self.num_physical_channels:]
                self._record_debug("input_patch_physical_raw", patch_physical)
                self._record_debug("input_patch_other_raw", patch_other)
                patch_physical_norm = self.input_normalizer(patch_physical)
                data.init_patch_phi_cpd_cell = torch.cat([patch_physical_norm, patch_other], dim=-1)
                self._record_debug("input_patch_physical_norm", patch_physical_norm)
                self._record_debug("input_patch_after_norm", data.init_patch_phi_cpd_cell)

        # Compute edge attributes dynamically
        data = self.update_edge_attr(data)
        self._record_debug("edge_attr", data.edge_attr)

        # Prepare model inputs
        model_inputs = {
            'x': data.x,
            'edge_attr': data.edge_attr,
            'edge_index': data.edge_index,
            'batch': data.batch,
            'init_patch_phi_cpd_cell': data.init_patch_phi_cpd_cell,
            'init_patch_phi_batch_cpd_cell': data.init_patch_phi_batch_cpd_cell,
        }

        return self.model(**model_inputs)

    def compute_loss(self, data):
        """
        Compute loss for training.

        Args:
            data: Input data object with predictions and targets

        Returns:
            Loss value
        """
        # Forward pass
        prediction = self.forward(data)

        prediction = prediction.float()
        target = data.y.float()
        self._record_debug("prediction", prediction)
        self._record_debug("target_raw", target)
        
        # Normalize target using KeyBasedNormalizer
        if self.target_normalizer:
            target = self.target_normalizer(target)
            self._record_debug("target_norm", target)

        # Compute loss
        loss_value = self.loss(prediction, target, data.batch)
        if self.debug_enabled:
            self._debug_state["loss"] = float(loss_value.detach().float().item())

        # VICReg regularization on text embeddings to prevent LoRA collapse
        vicreg_weight = getattr(self, '_vicreg_weight', 0.0)
        if vicreg_weight > 0:
            raw_model = self._unwrap_model(self.model)
            unique_emb = getattr(raw_model, '_last_unique_patch_embeddings', None)
            if unique_emb is not None and unique_emb.requires_grad:
                vicreg = vicreg_variance_covariance_loss(unique_emb)
                loss_value = loss_value + vicreg_weight * vicreg
                if self.debug_enabled:
                    self._debug_state["vicreg_loss"] = float(vicreg.detach().float().item())

        return loss_value

    def inference(self, data):
        """
        Inference mode - forward pass without loss computation.

        Args:
            data: Input data object

        Returns:
            Data object with predictions stored
        """
        # Forward pass
        prediction = self.forward(data)
        
        # Denormalize prediction using KeyBasedNormalizer
        if self.output_denormalizer:
            prediction = self.output_denormalizer.inverse(prediction)

        # Store prediction in data object
        data.prediction = prediction

        return data

    def save_model(self, checkpoint_path: str, **kwargs):
        """
        Save model checkpoint.

        Args:
            checkpoint_path: Path to save checkpoint
            **kwargs: Additional state to save (optimizer, scheduler, epoch, etc.)
        """
        # Prepare checkpoint state
        model_to_save = self._unwrap_model(self.model)
        checkpoint = {
            'model_state_dict': model_to_save.state_dict(),
            'config': self.config.to_dict() if hasattr(self.config, 'to_dict') else None,
        }

        # Add any additional state from kwargs
        checkpoint.update(kwargs)

        # Save checkpoint
        torch.save(checkpoint, checkpoint_path)

    def load_model(self, checkpoint_path: str, strict: bool = True):
        """
        Load model from checkpoint.

        Args:
            checkpoint_path: Path to checkpoint file
            strict: Whether to strictly enforce that the keys in state_dict match

        Returns:
            dict: Checkpoint data (optimizer state, epoch, etc.)
        """
        # PyTorch 2.6+ requires weights_only=False for checkpoints with optimizer state
        checkpoint = torch.load(checkpoint_path, map_location='cpu', weights_only=False)

        # Load model state
        if 'model_state_dict' in checkpoint:
            state_dict = checkpoint['model_state_dict']
        else:
            # Legacy format: entire checkpoint is state_dict
            state_dict = checkpoint

        # A benign OrderedDict `_metadata` can serialize as a plain key on some saves; it is never a
        # real parameter and trips strict load_state_dict (seen on the GINO/GAOT baseline checkpoints).
        # Drop it (no-op when absent).
        if isinstance(state_dict, dict):
            state_dict.pop('_metadata', None)

        state_dict = self._normalize_state_dict_keys(state_dict)
        target_model = self._unwrap_model(self.model)
        target_model.load_state_dict(state_dict, strict=strict)

        return checkpoint


# Legacy ModelImporter class for backward compatibility
class ModelImporter:
    def __init__(self, model_name):
        self.model_name = model_name

    def import_model(self):
        if self.model_name == 'FVGN':
            from .FVGN import FVGN
            return FVGN
        elif self.model_name == 'TransFVGN_v2':
            from .TransFVGN import TransFVGN
            return TransFVGN
        else:
            raise ValueError(f"Unknown model: {self.model_name}")
