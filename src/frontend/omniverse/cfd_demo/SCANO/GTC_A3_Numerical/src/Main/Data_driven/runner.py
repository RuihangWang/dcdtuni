#!/usr/bin/env python3
"""
Runner Base Class

Unified base class for Trainer and Inferencer.
Provides common infrastructure for:
- Distributed training/inference
- Device management
- Simulator creation and checkpoint loading
- DataLoader creation
- Inference core methods
- Mixed precision support
"""

import os
import json
from contextlib import nullcontext
import torch
import torch.nn as nn
import torch.distributed as dist
from typing import List, Dict, Tuple, Optional, Any
from pathlib import Path
import logging
logger = logging.getLogger(__name__)
from tqdm import tqdm
from torch.utils.data import DataLoader

# Fallback imports for standalone usage
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from config import Config
from Utils.key import Key
from NNmodels.importer import Simulator
from NNmodels.debug_utils import summarize_tensor
from NNmodels import slice_basis
from Main.Data_driven import bc_intervention


class Runner:
    """
    Unified Runner base class

    Provides common infrastructure for training and inference:
    - Distributed setup and management
    - Device management
    - Simulator creation and checkpoint loading
    - DataLoader creation
    - Inference core methods
    - Mixed precision support
    """
    
    # ========== Initialization ==========
    
    def __init__(
        self,
        config: Config,
        input_keys: List[Key],
        output_keys: List[Key],
        target_keys: Optional[List[Key]] = None,
        mode: str = "train"
    ):
        """
        Initialize Runner
        
        Args:
            config: Unified configuration object
            input_keys: Input keys
            output_keys: Output keys
            target_keys: Target keys
            mode: Running mode ("train", "inference")
        """
        # Basic configuration
        self.config = config
        self.input_keys = input_keys
        self.output_keys = output_keys
        self.target_keys = target_keys
        self.mode = mode
        
        # Get run configuration based on mode
        self.run_config = self._get_run_config()

        # Distributed configuration
        # run_config is a dict, use .get() instead of getattr()
        self.gpu_ids = self.run_config.get('gpu_ids', None)
        self.distributed = dist.is_initialized()
        
        # Initialize distributed environment
        if self.distributed:
            self._init_distributed()
        else:
            self.rank = 0
            self.world_size = 1
            self.is_main_process = True
            self.actual_gpu_id = self.gpu_ids[0] if self.gpu_ids else 0

        self.memory_debug_enabled = bool(int(os.environ.get("GTC_MEMORY_DEBUG", "0") or "0"))
        self.memory_debug_batches = int(os.environ.get("GTC_MEMORY_DEBUG_BATCHES", "2") or "2")
        self.memory_debug_reset_peaks = bool(int(os.environ.get("GTC_MEMORY_DEBUG_RESET_PEAKS", "0") or "0"))
        memory_debug_ranks_raw = os.environ.get("GTC_MEMORY_DEBUG_RANKS", "").strip()
        self.memory_debug_all_ranks = memory_debug_ranks_raw.lower() in {"all", "*"}
        self.memory_debug_ranks = {
            int(rank.strip())
            for rank in memory_debug_ranks_raw.split(",")
            if rank.strip() and rank.strip().lower() not in {"all", "*"}
        }
        
        # Setup device
        self.device = self._setup_device()
        self._log_cuda_memory("runner_device_ready", force=True)
        
        # Create Simulator
        self.simulator = self._create_simulator()
        vicreg_w = float(self.config.training_config.get("vicreg_weight", 0.0))
        if vicreg_w > 0:
            self.simulator._vicreg_weight = vicreg_w
            print(f"[VICReg] regularization enabled: weight={vicreg_w}")
        self._log_cuda_memory("simulator_created", force=True)

        # Pre-compute text embeddings BEFORE DDP wrapping.
        # This encodes all cases, stores embeddings, then removes text encoder
        # from GPU so that DDP only wraps the graph backbone.
        self._precomputed_text_embeddings = None
        self._text_encoder_state_dict = None
        # Offline-precomputed embeddings (AirfRANS TE-SCA): a {case: {patch_embeddings:[1,D]}}
        # sidecar built once by cases/Airfrans/text_sidecar_airfrans.py, loaded directly so no
        # text encoder runs online. Works in BOTH train and inference; takes precedence.
        _precomp_path = self.run_config.get("precomputed_text_embeddings_path")
        if _precomp_path and bool(self.run_config.get("use_text_conditioning", False)):
            if not os.path.isabs(_precomp_path):
                _precomp_path = os.path.abspath(_precomp_path)
            self._precomputed_text_embeddings = torch.load(_precomp_path, map_location="cpu", weights_only=False)
            if self.is_main_process:
                logger.info(f"📝 Loaded {len(self._precomputed_text_embeddings)} precomputed text "
                            f"embeddings from {_precomp_path}")
        if (
            self._precomputed_text_embeddings is None
            and self.mode == "train"
            and bool(self.run_config.get("use_text_conditioning", False))
            and bool(self.run_config.get("precompute_text_embeddings", False))
        ):
            if self.run_config.get("text_embedding_mode") == "random_typed":
                self._precomputed_text_embeddings = self._load_random_typed_embeddings()
            elif self._dataset_has_h5_embeddings():
                self._precomputed_text_embeddings = None
                if self.is_main_process:
                    logger.info("Embeddings found in H5 -- skipping runtime precomputation")
            else:
                self._precomputed_text_embeddings = self._run_text_precomputation()

        # Wrap model with DDP if running distributed
        # 🔧 FIX: 推理模式下不使用DDP，因为每个GPU处理不同数据，不需要梯度同步
        if self.distributed and self.world_size > 1 and self.mode == "train":
            self._wrap_model_with_ddp()
            self._log_cuda_memory("ddp_wrapped", force=True)

        # Load checkpoint if provided (skip for train mode - handled in Trainer)
        if self.mode != "train":
            checkpoint_path = self.run_config.get('checkpoint_path', None)
            if checkpoint_path:
                self._load_checkpoint(checkpoint_path)
            # Pre-compute text embeddings for inference AFTER checkpoint loading.
            # The _load_checkpoint restores text_encoder weights from the checkpoint
            # (if saved), ensuring the same embeddings as training.
            if (
                bool(self.run_config.get("use_text_conditioning", False))
                and bool(self.run_config.get("precompute_text_embeddings", False))
            ):
                if self.run_config.get("text_embedding_mode") == "random_typed":
                    self._precomputed_text_embeddings = self._load_random_typed_embeddings()
                elif self._dataset_has_h5_embeddings():
                    self._precomputed_text_embeddings = None
                    if self.is_main_process:
                        logger.info("Embeddings found in H5 -- skipping runtime precomputation")
                else:
                    self._precomputed_text_embeddings = self._run_text_precomputation()

        # Logger (will be initialized when checkpoint is loaded or in Trainer)
        self.logger = None

        # Mixed precision
        self.use_amp = self.run_config.get('mixed_precision', False)
        self.amp_dtype = self._get_amp_dtype()
        self.scaler = self._setup_mixed_precision() if self.mode == "train" else None
        self._log_cuda_memory("amp_ready", force=True)
        numeric_debug_max_batches_env = os.environ.get("GTC_NUMERIC_DEBUG_MAX_BATCHES", "").strip()
        numeric_debug_max_epochs_env = os.environ.get("GTC_NUMERIC_DEBUG_MAX_EPOCHS", "").strip()
        numeric_debug_deep_env = os.environ.get("GTC_NUMERIC_DEBUG_DEEP", "").strip()
        debug_ranks_raw = os.environ.get("GTC_NUMERIC_DEBUG_RANKS", "").strip()
        self.numeric_debug_max_batches = int(
            numeric_debug_max_batches_env
            or self.run_config.get("numeric_debug_max_batches", 0)
            or 0
        )
        self.numeric_debug_max_epochs = int(
            numeric_debug_max_epochs_env
            or self.run_config.get("numeric_debug_max_epochs", 1)
            or 1
        )
        self.numeric_debug_deep = bool(
            int(
                numeric_debug_deep_env
                or int(bool(self.run_config.get("numeric_debug_deep", False)))
            )
        )
        numeric_debug_ranks_cfg = self.run_config.get("numeric_debug_ranks", [])
        if debug_ranks_raw:
            self.numeric_debug_ranks = {
                int(rank.strip())
                for rank in debug_ranks_raw.split(",")
                if rank.strip()
            }
        else:
            self.numeric_debug_ranks = {int(rank) for rank in numeric_debug_ranks_cfg}
        self.sample_debug_batches = int(os.environ.get("GTC_SAMPLE_DEBUG_BATCHES", "3") or "3")
    
    def _get_run_config(self):
        """Get configuration based on mode"""
        if self.mode == "train":
            return self.config.training_config
        elif self.mode == "inference":
            return self.config.inference_config
        else:
            raise ValueError(f"Unknown mode: {self.mode}")

    def _get_effective_batch_size(self) -> int:
        """Return the configured graph batch size for the current mode."""
        if self.mode == "train":
            return int(self.config.training_config.get('batch_size', 1))
        if self.mode == "inference":
            return int(self.config.inference_config.get('tta_batch_size', 1))
        return 1

    def _resolve_fixed_size_policy(self, batch_size: int) -> Tuple[str, Optional[int]]:
        """
        Resolve fixed-size sampling policy from the effective graph batch size.

        Policy:
        - batch_size == 1: disable truncation completely
        - batch_size > 1: scan the current split under the current sample ratio
          and use that minimum as the common fixed node count
        """
        explicit_single_graph_policy = str(
            self.run_config.get("single_graph_fixed_size_policy", "none")
        ).strip().lower()
        if explicit_single_graph_policy not in ("none", "scan_split_min"):
            raise ValueError(
                f"Unsupported single_graph_fixed_size_policy: {explicit_single_graph_policy}"
            )
        if batch_size <= 1:
            return explicit_single_graph_policy, None
        return "scan_split_min", None

    def _log_batch_oom(self, stage: str, batch: Any, current_epoch: int, batch_idx: int):
        if self.device.type != 'cuda':
            return

        payload = {
            "stage": stage,
            "rank": self.rank,
            "epoch": current_epoch + 1,
            "batch": batch_idx + 1,
            "case_name": self._normalize_case_name(getattr(batch, "case_name", None)),
            "sample_ratio": self.run_config.get("sample_ratio", self.run_config.get("tta_sample_ratio")),
            "compile_enabled": bool(self.run_config.get("enable_compile", False)),
            "batch_size": self._get_effective_batch_size(),
            "x_shape": tuple(batch.x.shape) if getattr(batch, "x", None) is not None else None,
            "edge_index_shape": tuple(batch.edge_index.shape) if getattr(batch, "edge_index", None) is not None else None,
            "patch_shape": (
                tuple(getattr(batch, "init_patch_phi_cpd_cell").shape)
                if getattr(batch, "init_patch_phi_cpd_cell", None) is not None
                else None
            ),
            "sampled_pivot_count": self._extract_batch_debug_scalar(batch, "sampled_pivot_count"),
            "sampled_nodes_before_trunc": self._extract_batch_debug_scalar(batch, "sampled_nodes_before_trunc"),
            "sampled_nodes_after_trunc": self._extract_batch_debug_scalar(batch, "sampled_nodes_after_trunc"),
            "sampled_edge_count": self._extract_batch_debug_scalar(batch, "sampled_edge_count"),
            "sample_was_truncated": self._extract_batch_debug_scalar(batch, "sample_was_truncated"),
            "num_cpd_cells_total": self._extract_batch_debug_scalar(batch, "num_cpd_cells_total"),
            "alloc_gib": round(torch.cuda.memory_allocated(self.device) / 1024**3, 3),
            "reserved_gib": round(torch.cuda.memory_reserved(self.device) / 1024**3, 3),
        }
        logger.error("OOM_BATCH_CONTEXT %s", json.dumps(payload, ensure_ascii=False, sort_keys=True))

    def _should_log_memory_debug(self) -> bool:
        if not self.memory_debug_enabled or self.device.type != "cuda":
            return False
        if self.memory_debug_all_ranks:
            return True
        if not self.memory_debug_ranks:
            return self.is_main_process
        return self.rank in self.memory_debug_ranks

    def _should_log_sample_debug(self) -> bool:
        if self._should_log_memory_debug():
            return True
        return self.is_main_process

    def _log_lora_gradient_check(self, step: int):
        """Log LoRA weight norms after first few optimizer steps to verify gradient flow."""
        model = self.simulator._unwrap_model(self.simulator.model)
        if not hasattr(model, 'text_encoder') or model.text_encoder is None:
            return
        lora_a_norms = []
        lora_b_norms = []
        for name, param in model.text_encoder.named_parameters():
            if 'lora_A' in name:
                lora_a_norms.append((name, param.data.abs().max().item()))
            elif 'lora_B' in name:
                lora_b_norms.append((name, param.data.abs().max().item()))
        if lora_a_norms or lora_b_norms:
            a_max = max(v for _, v in lora_a_norms) if lora_a_norms else 0
            b_max = max(v for _, v in lora_b_norms) if lora_b_norms else 0
            status = "✅ LoRA training" if b_max > 1e-6 else "❌ LoRA NOT training (lora_B still zero)"
            logger.info(f"   🔍 [Step {step}] {status} | lora_A max={a_max:.6f} | lora_B max={b_max:.6f}")

    def _log_embedding_health(self, epoch: int):
        """Log text embedding health at end of epoch (every 10 epochs)."""
        if epoch % 10 != 0 and epoch != 1:
            return
        model = self.simulator._unwrap_model(self.simulator.model)
        emb = getattr(model, '_last_unique_patch_embeddings', None)
        if emb is None:
            return
        from NNmodels.importer import compute_embedding_health
        stats = compute_embedding_health(emb)
        icon = {"healthy": "🟢", "warning": "🟡", "collapsed": "🔴"}.get(stats["status"], "⚪")
        logger.info(
            f"   📊 [Epoch {epoch}] Embedding {icon} {stats['status']} | "
            f"cos={stats['mean_cos_sim']:.4f} | "
            f"std={stats['std_mean']:.4f} | "
            f"L2={stats['l2_mean']:.2f} | "
            f"rank={stats['eff_rank']:.1f} | "
            f"active={stats['active_dims']}/{stats['total_dims']}"
        )

    def _log_cuda_memory(
        self,
        stage: str = "",
        batch: Any = None,
        batch_idx: Optional[int] = None,
        force: bool = False,
    ):
        if not torch.cuda.is_available():
            # NaN, not 0.0: peak memory is a smaller-is-better column, so a 0 fallback would publish
            # a CPU-fallback run as the most memory-efficient model in the table.
            return (float("nan"), float("nan"))
        alloc_gib = torch.cuda.max_memory_allocated() / (1024**3)
        reserved_gib = torch.cuda.max_memory_reserved() / (1024**3)
        if self.is_main_process:
            label = f" [{stage}]" if stage else ""
            logger.info(f"GPU peak memory{label}: allocated={alloc_gib:.2f} GiB, reserved={reserved_gib:.2f} GiB")
        return (alloc_gib, reserved_gib)

    def _reset_cuda_peak_memory_stats(self):
        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()

    def _measure_forward_flops(self, batch_data) -> float:
        """Best-effort per-forward GFLOPs, measured on a CLONE. Returns 0.0 on any failure.

        Deliberately a SEPARATE forward rather than wrapping the live inference call:
        FlopCounterMode raises on some trunks (AeroGTO -> AssertionError), which would otherwise
        abort the whole sweep. Run in fp32 (no autocast) because FLOP counts are arithmetic and
        GINO's complex FNO spectral convolutions reject bfloat16.

        Resets the CUDA peak-memory counter afterwards so this probe never inflates the reported
        inference peak_mem_gib.

        Root cause of the AeroGTO break: FlopCounterMode's ModuleTracker registers a backward
        grad-hook on every module-INPUT tensor whose `.requires_grad` is True (torch
        module_tracker.py: `[a for a in args if a.requires_grad]` -> register_multi_grad_hook).
        AeroGTO feeds an expanded nn.Parameter (`self.Q.expand(...)`) as a submodule input; that
        view keeps requires_grad=True but has no grad node, so register_multi_grad_hook asserts.
        Forcing grad on (old fix) silenced the assert but RETAINED a full autograd graph that
        scales with subgraph size and OOMs the card at sampling ratios >= 0.05.
        Fix: temporarily set every parameter's requires_grad=False for the probe. The tracker then
        skips all of them, so the probe runs under no_grad -- no retained graph, no OOM at ANY
        ratio -- and the FLOP count (pure arithmetic) is byte-for-byte identical (verified). The
        original requires_grad flags are restored in `finally`, so training state is untouched.
        """
        gflops = 0.0
        saved_rg = None
        try:
            from torch.utils.flop_counter import FlopCounterMode

            saved_rg = [(p, p.requires_grad) for p in self.simulator.parameters()]
            for p, _ in saved_rg:
                p.requires_grad_(False)

            probe = batch_data.clone()
            try:
                with FlopCounterMode(display=False) as fc:
                    with torch.no_grad():
                        self.simulator.inference(probe)
                gflops = float(fc.get_total_flops()) / 1e9
            finally:
                del probe
        except Exception as exc:
            # NaN, never 0.0. A cost column is a column where SMALLER IS BETTER, so a 0 fallback
            # publishes a model that could not be traced as infinitely efficient -- 0 GFLOPs. That
            # has already happened once (the AeroGTO baseline). NaN propagates visibly through the
            # aggregator and refuses to be mistaken for a result.
            logger.warning(f"FLOPs measurement FAILED ({type(exc).__name__}: {exc}); reporting NaN")
            gflops = float("nan")
        finally:
            if saved_rg is not None:
                for p, rg in saved_rg:
                    p.requires_grad_(rg)
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                torch.cuda.reset_peak_memory_stats()
        return gflops

    # ========== Distributed Infrastructure ==========
    
    def _init_distributed(self):
        """Initialize distributed environment and setup device"""
        if not dist.is_initialized():
            if self.mode == "train":
                raise RuntimeError(
                    f"Distributed {self.mode} requires torch.distributed to be initialized. "
                    f"Please use torchrun to launch the script."
                )

        self.rank = dist.get_rank()
        self.world_size = dist.get_world_size()
        self.is_main_process = (self.rank == 0)

        # Calculate actual GPU ID based on rank
        if 'LOCAL_RANK' in os.environ:
            self.actual_gpu_id = int(os.environ['LOCAL_RANK'])
        elif torch.cuda.is_available() and self.gpu_ids:
            if self.rank >= len(self.gpu_ids):
                raise ValueError(f"Rank {self.rank} exceeds available GPUs {len(self.gpu_ids)}")
            self.actual_gpu_id = self.gpu_ids[self.rank]
        else:
            self.actual_gpu_id = 0

        if self.is_main_process:
            logger.info(f"🌐 Distributed {self.mode}: Rank {self.rank}/{self.world_size}")
            logger.info(f"   Rank {self.rank} -> GPU {self.actual_gpu_id}")
    
    def _wrap_model_with_ddp(self):
        """Wrap model with DistributedDataParallel"""
        from torch.nn.parallel import DistributedDataParallel as DDP

        if self.is_main_process:
            logger.info("🌐 Wrapping model with DistributedDataParallel...")

        model_name = str(self.run_config.get("model_name", ""))
        use_text_conditioning = bool(self.run_config.get("use_text_conditioning", False))
        precompute_mode = bool(self.run_config.get("precompute_text_embeddings", False))

        if precompute_mode and use_text_conditioning:
            find_unused_default = True
        else:
            # v1/v3 use PatchCrossAttentionEncoder, whose gn_block is instantiated even when
            # use_internal_gn_block=False -> unused params -> DDP needs find_unused=True.
            # TextCondTrans_v2 (pure Transolver) has every param used -> default False.
            find_unused_default = use_text_conditioning or model_name in (
                "TextCondTransFVGN_v1", "TextCondTransFVGN_v3"
            )
        find_unused_parameters = bool(
            self.run_config.get("find_unused_parameters", find_unused_default)
        )

        # static_graph is REQUIRED for any model using activation checkpointing under DDP: the
        # recompute fires each parameter's gradient hook a second time, and without static_graph the
        # reducer raises "Expected to have finished reduction in the prior iteration". Default False
        # so no existing arm changes behaviour. TransFVGN_SS_v1 checkpoints its processors, so it
        # opts in by default.
        static_graph_default = model_name in ("TransFVGN_SS_v1",)
        static_graph = bool(self.run_config.get("static_graph", static_graph_default))
        if static_graph and find_unused_parameters:
            # DDP rejects the combination outright. Fail loudly rather than silently dropping one:
            # dropping static_graph would resurrect the reduction error, and dropping
            # find_unused_parameters would hang on the first backward with unused params.
            raise ValueError(
                f"model_name={model_name!r} resolved to BOTH static_graph=True and "
                f"find_unused_parameters=True, which DDP does not allow. static_graph is needed for "
                f"activation checkpointing; find_unused_parameters is needed when the module builds "
                f"parameters it never uses. Fix the model so every parameter is used, then leave "
                f"find_unused_parameters=False."
            )

        self.simulator.model = DDP(
            self.simulator.model,
            device_ids=[self.actual_gpu_id] if torch.cuda.is_available() else None,
            find_unused_parameters=find_unused_parameters,
            static_graph=static_graph,
        )

        if self.is_main_process:
            logger.info(f"   ✅ DDP model created on device: cuda:{self.actual_gpu_id}")
            logger.info(f"   🔧 DDP find_unused_parameters={find_unused_parameters} static_graph={static_graph}")

    # ========== Device Management ==========
    
    def _setup_device(self) -> torch.device:
        """Setup device and configure CUDA settings"""
        # Configure torch.compile() for dynamic shapes (graph neural networks)
        # Disable CUDA Graph dynamic shape warnings since GNN node counts vary
        import torch._inductor.config as inductor_config
        inductor_config.triton.cudagraph_dynamic_shape_warn_limit = None

        if self.distributed:
            device_id = self.actual_gpu_id
        else:
            device_id = self.gpu_ids[0] if self.gpu_ids else 0
        device = torch.device(f"cuda:{device_id}")
        torch.cuda.set_device(device)

        # Enable cuDNN benchmark for faster training
        # This is safe for distributed training with fixed input sizes
        # For dynamic GNN graphs, the benchmark overhead may not be worth it,
        # but it's generally safe to enable
        if self.mode == "train":
            torch.backends.cudnn.benchmark = True
        else:
            torch.backends.cudnn.benchmark = False

        if self.is_main_process:
            logger.info(f"🖥️  Device: {device} ({torch.cuda.get_device_name(device_id)})")
            logger.info(f"   cuDNN benchmark: {'Enabled' if torch.backends.cudnn.benchmark else 'Disabled'}")
            logger.info(f"   CUDA Graph dynamic shapes: Enabled (warnings suppressed)")

        return device
    
    # ========== Simulator Management ==========
    
    def _create_simulator(self) -> Simulator:
        """Create Simulator"""
        if self.is_main_process:
            logger.info("🧠 Creating Simulator...")
        
        simulator = Simulator(
            config=self.config,
            input_keys=self.input_keys,
            output_keys=self.output_keys,
            target_keys=self.target_keys,
            mode=self.mode,
        )

        simulator.to(self.device)

        if self.is_main_process:
            self._log_design_contract(simulator)

        return simulator

    def _log_design_contract(self, simulator: Simulator) -> None:
        """Dump every switch that decides WHAT this run is, so the log is self-describing.

        Reconstructing a finished run used to mean solving the model constructor against the printed
        parameter count and reading the first Linear's weight shape out of best.state, because none of
        these keys was ever logged and the YAML on disk may have been edited since. A run that cannot
        be identified from its own log is not reproducible.

        The input dims are read off the live weights rather than echoed from the config: the config
        says what was requested, the weights say what was built.
        """
        rc = self.run_config
        model = getattr(simulator, "model", None)

        total = sum(p.numel() for p in simulator.parameters())
        trainable = sum(p.numel() for p in simulator.parameters() if p.requires_grad)

        # First Linear of each named encoder path -> the real per-stream input width.
        dims = {}
        if model is not None:
            for name, p in model.named_parameters():
                if p.dim() == 2 and name.endswith(".weight"):
                    stem = name.rsplit(".weight", 1)[0]
                    head = stem.split(".")[0]
                    if head not in dims and any(
                        tag in stem.lower()
                        for tag in ("encoder", "lift", "preprocess", "embed", "in_proj")
                    ):
                        dims[head] = f"{stem}{tuple(p.shape)}"

        keys = [
            "model_name", "dataset_path", "field_source", "field_set",
            "use_text_conditioning", "precompute_text_embeddings", "text_source",
            "text_lora_rank", "vicreg_weight",
            "patch_only_numerical", "patch_numerical_normalize", "patch_numerical_per_face",
            "include_t1_fields", "baseline_full_boundary", "sca_full_boundary_ablation",
            "baseline_normalize_inputs", "sample_ratio", "khop", "batch_size", "num_epochs",
        ]
        logger.info("=" * 78)
        logger.info("📐 DESIGN CONTRACT (what this run actually is)")
        for k in keys:
            logger.info(f"      {k:30s} = {rc.get(k, '‹unset → code default›')!r}")
        logger.info(f"      {'world_size':30s} = {self.world_size}")
        fs = rc.get("field_source")
        logger.info(f"      {'h5 field group':30s} = "
                    f"{f'field_{fs}/' if fs else '‹flat keys (layout v1)›'}")
        for k, v in dims.items():
            logger.info(f"      input dim  {k:19s} = {v}")
        logger.info(f"      {'params total / trainable':30s} = {total:,} / {trainable:,}")
        # A diagnostic run and a production run must never be confusable in the logs. An
        # intervened run's nRMSE is deliberately damaged; if that number were later quoted as
        # this arm's accuracy, the arm would look far worse than it is.
        bci = bc_intervention.mode()
        if bci is not None:
            logger.info("      " + "!" * 62)
            logger.info(f"      {'BC INTERVENTION':30s} = {bci!r}  ← DIAGNOSTIC RUN")
            logger.info("      the boundary values are deliberately corrupted. The nRMSE from")
            logger.info("      this run is NOT this arm's accuracy. Do not quote it as such.")
            logger.info("      " + "!" * 62)
        logger.info("=" * 78)

    def _should_collect_numeric_debug(self, current_epoch: int, batch_idx: int) -> bool:
        if self.numeric_debug_max_batches <= 0:
            return False
        if current_epoch >= self.numeric_debug_max_epochs:
            return False
        if batch_idx >= self.numeric_debug_max_batches:
            return False
        if not self.numeric_debug_ranks:
            return True
        return self.rank in self.numeric_debug_ranks

    @staticmethod
    def _normalize_case_name(case_name):
        if isinstance(case_name, (list, tuple)) and len(case_name) == 1:
            return case_name[0]
        return case_name

    def _emit_numeric_debug(self, batch, current_epoch: int, batch_idx: int, loss=None, prefix: str = "debug", extra: Optional[Dict[str, Any]] = None):
        case_name = self._normalize_case_name(getattr(batch, "case_name", None))
        payload = {
            "rank": self.rank,
            "epoch": current_epoch + 1,
            "batch": batch_idx + 1,
            "case_name": case_name,
            "loss": None if loss is None else float(loss.detach().float().item()),
            "x": summarize_tensor(getattr(batch, "x", None)),
            "y": summarize_tensor(getattr(batch, "y", None)),
            "edge_index": summarize_tensor(getattr(batch, "edge_index", None)),
            "edge_attr": summarize_tensor(getattr(batch, "edge_attr", None)),
            "patch": summarize_tensor(getattr(batch, "init_patch_phi_cpd_cell", None)),
            "patch_batch": summarize_tensor(getattr(batch, "init_patch_phi_batch_cpd_cell", None)),
        }

        if self.numeric_debug_deep and hasattr(self.simulator, "peek_debug_state"):
            payload["simulator_debug"] = self.simulator.peek_debug_state()
        if extra:
            payload.update(extra)

        logger.info(
            "NUMERIC_%s %s",
            prefix.upper(),
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
        )
    
    def _setup_logger_from_checkpoint(self, checkpoint_path: str):
        """Setup Logger using the same directory as the checkpoint"""
        from Utils.Logger import Logger

        results_root = None
        if self.mode == "inference":
            results_root = self.run_config.get('results_root')

        # Extract checkpoint directory
        checkpoint_path = Path(checkpoint_path)
        if results_root:
            logger_dir = str(Path(results_root).expanduser().resolve())
            if self.is_main_process:
                logger.info(f" Using inference results_root override: {logger_dir}")
        elif checkpoint_path.is_file():
            # Go up two levels: states -> timestamp -> model_name
            logger_dir = str(checkpoint_path.parent.parent)
        else:
            logging_config = self.config.training_config.get('logging_config', {})
            logger_root = logging_config.get('logger_root')
            logger_dir = str(logger_root) if logger_root else "Logger"

        # Create Logger instance with existing directory
        _logger = Logger(
            name=self.config.training_config.get('experiment_name'),
            use_tensorboard=False,
            params=self.config.to_dict(),
            saving_path=logger_dir
        )

        if self.is_main_process:
            logger.info(f" Logger initialized: {logger_dir}")
        return _logger

    def _find_checkpoint(self, checkpoint_path: str) -> str:
        """Find actual checkpoint file path"""
        if checkpoint_path in ["best", "latest"]:
            logger_roots = []

            top_logging_config = self.config.logging_config or {}
            nested_logging_config = self.config.training_config.get('logging_config', {})
            for candidate in (
                nested_logging_config.get('logger_root'),
                top_logging_config.get('logger_root'),
                "Logger",
            ):
                if not candidate:
                    continue
                candidate_path = os.path.abspath(candidate)
                if os.path.isdir(candidate_path) and candidate_path not in logger_roots:
                    logger_roots.append(candidate_path)

            logger_dirs = []
            for logger_root in logger_roots:
                for root, dirs, files in os.walk(logger_root):
                    if "states" in dirs:
                        logger_dirs.append(os.path.join(root, "states"))

            if not logger_dirs:
                raise FileNotFoundError(
                    f"No logger directories with states found under: {logger_roots}"
                )

            # Use the most recent Logger directory
            latest_logger_dir = max(logger_dirs, key=os.path.getmtime)

            if checkpoint_path == "best":
                best_path = os.path.join(latest_logger_dir, "best.state")
                if os.path.exists(best_path):
                    return best_path
                else:
                    checkpoint_path = "latest"

            if checkpoint_path == "latest":
                state_files = [f for f in os.listdir(latest_logger_dir) if f.endswith('.state')]
                if not state_files:
                    raise FileNotFoundError(f"No .state files found in {latest_logger_dir}")

                def extract_epoch(filename):
                    return int(filename.split('_')[1].split('.')[0])

                latest_file = max(state_files, key=extract_epoch)
                return os.path.join(latest_logger_dir, latest_file)

        # Direct path
        if not os.path.exists(checkpoint_path):
            if self.is_main_process:
                logger.warning(f"Checkpoint not found: {checkpoint_path}")

        return checkpoint_path

    def _load_checkpoint(self, checkpoint_path: str):
        """Load checkpoint and setup Logger"""
        # Find actual checkpoint path
        actual_path = self._find_checkpoint(checkpoint_path)

        # Setup Logger (for inference mode)
        if self.mode == "inference":
            self.logger = self._setup_logger_from_checkpoint(actual_path)

        if self.is_main_process:
            logger.info(f"📥 Loading checkpoint: {actual_path}")

        # FixedEmb (precompute_text_embeddings=true) saves checkpoint WITHOUT
        # text_encoder weights in model_state_dict (replaced with stub during
        # training).  Use strict=False so the GNN weights load correctly.
        # Then restore the text_encoder from the separately-saved state_dict
        # so that on-the-fly encoding at inference time matches training.
        strict = True
        precompute = bool(self.run_config.get("precompute_text_embeddings", False))
        if precompute:
            strict = False
            if self.is_main_process:
                logger.info("   ℹ️  precompute_text_embeddings=true → loading with strict=False")

        # Load model state through simulator helper (handles DDP internally)
        checkpoint = self.simulator.load_model(actual_path, strict=strict) if actual_path else None
        self.simulator.eval()

        # Restore text_encoder weights from checkpoint if available.
        # This fixes the train/inference mismatch where output_proj and LoRA_A
        # had different random initialisations.
        if (
            precompute
            and checkpoint is not None
            and 'text_encoder_state_dict' in checkpoint
        ):
            target_model = self.simulator._unwrap_model(self.simulator.model)
            if hasattr(target_model, 'text_encoder') and target_model.text_encoder is not None:
                # Only restore if the text_encoder is still a real encoder (not a stub).
                # After _run_text_precomputation it's replaced with _TextEncoderStub
                # which has no parameters.
                te_sd = checkpoint['text_encoder_state_dict']
                try:
                    target_model.text_encoder.load_state_dict(te_sd, strict=True)
                    if self.is_main_process:
                        logger.info(f"   ✅ Restored text_encoder weights from checkpoint ({len(te_sd)} keys)")
                except (RuntimeError, AttributeError):
                    # text_encoder is already a stub (e.g. second _load_checkpoint call)
                    if self.is_main_process:
                        logger.info("   ℹ️  text_encoder already replaced with stub, skipping weight restore")
            else:
                if self.is_main_process:
                    logger.warning("   ⚠️  text_encoder_state_dict in checkpoint but model has no text_encoder")
        elif precompute and self.is_main_process:
            logger.warning(
                "   ⚠️  No text_encoder_state_dict in checkpoint — text_encoder uses fresh "
                "random weights. This may cause inference quality degradation if the "
                "output_proj / LoRA weights differ from training."
            )

        if self.is_main_process:
            logger.info("   ✅ Checkpoint loaded successfully")
    
    # ========== Text Embedding Pre-computation ==========

    def _run_text_precomputation(self):
        """Pre-compute text embeddings for ALL dataset cases.

        Called BEFORE DDP wrapping. Encodes text for every case,
        then removes the text encoder from GPU to free VRAM.

        Returns:
            dict mapping case_name -> {"patch_embeddings": Tensor, "global_embeddings": Tensor|None}
        """
        import glob
        from Parse_mesh.text_sidecar import load_text_conditioning_from_h5
        from pathlib import Path

        run_config = self.run_config
        dataset_path = run_config.get("dataset_path", "")

        if self.is_main_process:
            logger.info("📝 Pre-computing text embeddings (text encoder will be freed after)...")

        # Discover all case H5 files
        meta_path = os.path.join(dataset_path, "meta.json")
        if os.path.exists(meta_path):
            import json as _json
            with open(meta_path) as f:
                meta = _json.load(f)
            all_case_names = (
                meta.get("case_splits", {}).get("train_cases", [])
                + meta.get("case_splits", {}).get("test_cases", [])
            )
        else:
            all_case_names = sorted(
                d for d in os.listdir(dataset_path)
                if os.path.isdir(os.path.join(dataset_path, d)) and d.startswith("case")
            )

        # Build case_name -> h5_path mapping (handles both flat and nested layouts)
        case_h5_map = {}
        h5_index = None  # lazy-built fallback index
        for case_name in all_case_names:
            cn = os.path.dirname(case_name) if '/' in case_name else case_name
            h5_dir = os.path.join(dataset_path, case_name, "h5")
            h5_path = os.path.join(h5_dir, f"{cn}.h5")
            if os.path.exists(h5_path):
                case_h5_map[case_name] = h5_path
                continue
            if os.path.isdir(h5_dir):
                h5_files = sorted(glob.glob(os.path.join(h5_dir, "**", "*.h5"), recursive=True))
                if h5_files:
                    case_h5_map[case_name] = h5_files[0]
                    continue
            # Nested layout fallback: index all H5 files once
            if h5_index is None:
                h5_index = {}
                for p in Path(dataset_path).rglob("*.h5"):
                    h5_index.setdefault(p.stem, str(p))
                    for part in p.relative_to(dataset_path).parts:
                        if part.startswith("20") and "_case_" in part:
                            h5_index.setdefault(part, str(p))
                            break
            if cn in h5_index:
                case_h5_map[case_name] = h5_index[cn]

        if self.is_main_process:
            logger.info(f"   Found {len(case_h5_map)} cases with H5 files")

        # Get text encoder from model
        model = self.simulator._unwrap_model(self.simulator.model)
        text_encoder = model.text_encoder
        device = self.device

        # Text encoder is already on device (created by _create_simulator -> model.cuda())
        text_encoder.eval()

        text_sidecar_name = str(run_config.get("text_sidecar_name", "text_inputs.pt"))
        embeddings_dict = {}
        # GlobalCondTransFVGN_v1 only consumes global embeddings — skip expensive
        # per-patch encoding to save startup time and GPU memory.
        is_global_cond = (run_config.get("model_name", "") == "GlobalCondTransFVGN_v1")

        with torch.no_grad():
            for case_name, h5_path in case_h5_map.items():
                try:
                    sidecar = load_text_conditioning_from_h5(h5_path)
                except (KeyError, Exception):
                    sidecar_path = Path(h5_path).parent / text_sidecar_name
                    if sidecar_path.exists():
                        sidecar = torch.load(sidecar_path, map_location="cpu")
                    else:
                        continue

                if is_global_cond:
                    patch_embeddings = None
                else:
                    # Encode unique patches
                    unique_ids = sidecar["patch_unique_input_ids"].to(torch.long).to(device)
                    unique_mask = sidecar["patch_unique_attention_mask"].to(torch.long).to(device)
                    unique_embeddings = text_encoder(unique_ids, unique_mask)

                    # Expand unique -> per-patch
                    inverse_idx = sidecar["patch_text_inverse_indices"].to(torch.long).to(device)
                    patch_embeddings = unique_embeddings.index_select(0, inverse_idx).cpu()

                # Encode global text
                global_embeddings = None
                if "global_input_ids" in sidecar and sidecar["global_input_ids"] is not None:
                    g_ids = sidecar["global_input_ids"].to(torch.long).to(device)
                    g_mask = sidecar["global_attention_mask"].to(torch.long).to(device)
                    global_embeddings = text_encoder(g_ids, g_mask).cpu()

                embeddings_dict[case_name] = {
                    "patch_embeddings": patch_embeddings,
                    "global_embeddings": global_embeddings,
                }

        if self.is_main_process:
            logger.info(f"   ✅ Pre-computed {len(embeddings_dict)} cases")

        # Save the text encoder state_dict BEFORE replacing it with a stub.
        # This is critical for inference: the checkpoint must carry these weights
        # so that on-the-fly text encoding produces the same embeddings as
        # precomputation did during training.  Without this, the randomly-
        # initialised output_proj and LoRA_A matrices will differ between
        # training and inference, causing catastrophic prediction errors.
        self._text_encoder_state_dict = {
            k: v.cpu() for k, v in text_encoder.state_dict().items()
        }
        if self.is_main_process:
            n_params = sum(v.numel() for v in self._text_encoder_state_dict.values())
            logger.info(f"   💾 Saved text_encoder state_dict ({n_params:,} params) for checkpoint")

        # Replace text encoder with lightweight stub to free GPU VRAM
        class _TextEncoderStub(nn.Module):
            """Dummy module replacing the real text encoder after pre-computation."""
            def forward(self, *args, **kwargs):
                raise RuntimeError(
                    "Text encoder was removed after pre-computation. "
                    "Use precomputed_patch_embeddings in data instead."
                )

        model.text_encoder = _TextEncoderStub()
        del text_encoder
        torch.cuda.empty_cache()

        if self.is_main_process:
            freed_mb = 22.7 * 4  # rough estimate: 22.7M params * 4 bytes
            logger.info(f"   🗑️ Text encoder replaced with stub (freed ~{freed_mb:.0f}MB GPU VRAM)")

        return embeddings_dict

    def _load_random_typed_embeddings(self):
        """Load precomputed random-typed embeddings from file.

        Used by A4 experiment: replaces MiniLM text embeddings with
        random unit vectors per BC type combination.
        """
        emb_path = self.run_config.get("random_typed_embeddings_path", "")
        if not emb_path or not os.path.exists(emb_path):
            raise FileNotFoundError(
                f"random_typed_embeddings_path not found: {emb_path!r}"
            )

        if self.is_main_process:
            logger.info(f"📝 Loading random-typed embeddings from {emb_path}")

        data = torch.load(emb_path, map_location="cpu", weights_only=False)
        embeddings_dict = data["embeddings_dict"]

        if self.is_main_process:
            logger.info(
                f"   ✅ Loaded {len(embeddings_dict)} cases, "
                f"{len(data.get('type_vocab', []))} BC types"
            )

        self._text_encoder_state_dict = {}

        model = self.simulator._unwrap_model(self.simulator.model)
        if hasattr(model, 'text_encoder') and model.text_encoder is not None:
            model.text_encoder = None
            torch.cuda.empty_cache()
            if self.is_main_process:
                logger.info("   🗑️ Text encoder removed (random_typed mode)")

        return embeddings_dict

    def _dataset_has_h5_embeddings(self) -> bool:
        """Check if the first H5 file has precomputed embeddings."""
        import h5py
        dataset_path = self.run_config.get("dataset_path", "")
        if not dataset_path:
            return False
        _model_name = self.run_config.get("model_name", "")
        _model_cfg = self.config.model_configs.get(_model_name, {})
        _random_init = bool(_model_cfg.get("text_encoder_random_init", False))
        key = "random_init_patch_embeddings" if _random_init else "pretrained_patch_embeddings"
        h5_paths = sorted(Path(dataset_path).rglob("*.h5"))
        if h5_paths:
            with h5py.File(str(h5_paths[0]), "r") as hf:
                return f"text_conditioning/{key}" in hf
        return False

    # ========== DataLoader Creation ==========
    def _create_dataloader(
        self,
        data_root: str,
        split: str = "train",
        case_names: Optional[List[str]] = None,
    ) -> Optional[DataLoader]:
        """
        Create DataLoader based on mode (unified for train/inference)

        Args:
            data_root: Data root directory
            split: Dataset split ("train", "test")
            case_names: Optional list of case names to filter

        Returns:
            DataLoader or None if no data found
        """
        from FVdomain.HomoGraph_dataset import HomoGraphDataset
        from torch_geometric.loader import DataLoader as PyGDataLoader

        # Get configuration based on mode
        if self.mode == "train":
            run_config = self.config.training_config
            sample_ratio = run_config.get('sample_ratio', 1.0)
            khop = run_config.get('khop', 2)
            batch_size = run_config.get('batch_size', 4)
            fixed_subgraph_size_scale = float(
                run_config.get('fixed_subgraph_size_scale', 1.0)
            )
            deprecated_fixed_mode = run_config.get('fixed_subgraph_size_mode', None)
            deprecated_fixed_size = run_config.get('fixed_subgraph_size', None)
            case_names = case_names or run_config.get('case_names')
        elif self.mode == "inference":
            run_config = self.config.inference_config
            sample_ratio = run_config.get('tta_sample_ratio', 1.0)

            # 🔧 多GPU自适应采样比例调整
            if self.world_size > 1:
                # 优先使用多GPU专用配置
                multi_gpu_ratio = run_config.get('tta_sample_ratio_multi_gpu', None)
                if multi_gpu_ratio is not None:
                    sample_ratio = multi_gpu_ratio
                    if self.is_main_process:
                        logger.info(f"🔧 Multi-GPU mode: Using tta_sample_ratio_multi_gpu={sample_ratio}")
                else:
                    # 自动调整: 保持每个GPU的采样节点数与单GPU相近
                    adjusted_ratio = min(1.0, sample_ratio * self.world_size)
                    if self.is_main_process:
                        logger.info(f"🔧 Multi-GPU mode: Auto-adjusting sample_ratio from {sample_ratio} to {adjusted_ratio}")
                    sample_ratio = adjusted_ratio

            khop = run_config.get('tta_khop', 2)
            batch_size = run_config.get('tta_batch_size', 4)
            fixed_subgraph_size_scale = float(
                run_config.get('fixed_subgraph_size_scale', 1.0)
            )
            deprecated_fixed_mode = run_config.get('fixed_subgraph_size_mode', None)
            deprecated_fixed_size = run_config.get('tta_fixed_subgraph_size', None)
            case_names = case_names or run_config.get('case_names')
        else:
            raise ValueError(f"Unsupported mode for _create_dataloader: {self.mode}")

        effective_fixed_mode, effective_fixed_size = self._resolve_fixed_size_policy(int(batch_size))

        if self.is_main_process:
            logger.info(f"📊 Creating DataLoader for {self.mode} mode...")
            if deprecated_fixed_mode is not None or deprecated_fixed_size is not None:
                logger.warning(
                    "⚠️  Manual fixed_subgraph_size configuration is deprecated and ignored. "
                    "Runtime now always derives fixed size by scanning the current split when batch_size>1."
                )
            logger.info(
                f"🎯 Sampling: ratio={sample_ratio}, khop={khop}, batch_size={batch_size}"
            )
            if int(batch_size) <= 1 and effective_fixed_mode == "none":
                logger.info("   ✅ batch_size=1 -> disable fixed-size truncation, keep variable-size subgraphs")
            elif int(batch_size) <= 1:
                logger.info(
                    "   ✅ batch_size=1 -> explicit safety mode enabled: %s (scale=%.4f)",
                    effective_fixed_mode,
                    fixed_subgraph_size_scale,
                )
            else:
                logger.info("   ✅ batch_size>1 -> always scan current split and use its min k-hop size for truncation")

        # Set random seed for reproducible sampling.
        # Default 42 (backward compatible); override via FVGN_SEED env var for multi-seed runs.
        _seed = int(os.environ.get("FVGN_SEED", "42"))
        torch.manual_seed(_seed)
        torch.cuda.manual_seed_all(_seed)
        import numpy as np
        import random
        np.random.seed(_seed)
        random.seed(_seed)

        if self.is_main_process:
            logger.info(f"   🎲 Random seed set to {_seed} for reproducible sampling (FVGN_SEED env, default 42)")
        self._log_cuda_memory(f"{self.mode}_dataloader_before_dataset", force=True)

        # Create dataset
        # 🔧 FIX: 多GPU模式下总是需要subsampling来进行chunk分配
        # 即使sample_ratio=1.0,多GPU也需要将节点分配给不同GPU
        needs_subsampling = (sample_ratio < 1.0) or (self.world_size > 1)

        dataset_fixed_mode = effective_fixed_mode
        dataset_fixed_size = effective_fixed_size
        if (
            self.distributed
            and self.world_size > 1
            and effective_fixed_mode == "scan_split_min"
            and not self.is_main_process
        ):
            dataset_fixed_mode = "none"
            dataset_fixed_size = None

        field_source = run_config.get("field_source", None)
        # Derive embedding_key: A4 uses random_init encoder -> different H5 key
        _active_model_name = run_config.get("model_name", "")
        _active_model_cfg = self.config.model_configs.get(_active_model_name, {})
        _random_init = bool(_active_model_cfg.get("text_encoder_random_init", False))
        embedding_key = "random_init_patch_embeddings" if _random_init else "pretrained_patch_embeddings"
        dataset = HomoGraphDataset(
            root=data_root,
            subsampling=needs_subsampling,
            khop=khop,
            sample_ratio=sample_ratio,
            fixed_subgraph_size=dataset_fixed_size,
            fixed_subgraph_size_mode=dataset_fixed_mode,
            fixed_subgraph_size_scale=fixed_subgraph_size_scale,
            split=split,
            case_names=case_names,
            rank=self.rank,
            world_size=self.world_size,
            use_text_conditioning=bool(run_config.get("use_text_conditioning", False)),
            text_sidecar_name=str(run_config.get("text_sidecar_name", "text_inputs.pt")),
            global_cond_only=(_active_model_name == "GlobalCondTransFVGN_v1"),
            patch_only_numerical=bool(run_config.get("patch_only_numerical", False)),
            patch_numerical_per_face=bool(run_config.get("patch_numerical_per_face", False)),
            include_t1_fields=bool(run_config.get("include_t1_fields", False)),
            field_source=field_source,
            embedding_key=embedding_key,
            baseline_full_boundary=bool(run_config.get("baseline_full_boundary", False)),
            field_set=str(run_config.get("field_set", "uvwpT")),
            olbc_cond_mode=str(run_config.get("olbc_cond_mode", "numeric")),
            olbc_type_embeddings_path=run_config.get("olbc_type_embeddings_path", None),
        )

        # Inject pre-computed text embeddings into dataset (if available)
        if self._precomputed_text_embeddings is not None:
            dataset.set_precomputed_text_embeddings(self._precomputed_text_embeddings)
            if self.is_main_process:
                logger.info("   📝 Injected pre-computed text embeddings into dataset")

        self._log_cuda_memory(f"{self.mode}_dataloader_after_dataset", force=True)

        if (
            self.distributed
            and self.world_size > 1
            and effective_fixed_mode == "scan_split_min"
        ):
            fixed_size_tensor = torch.tensor(
                [int(dataset.fixed_subgraph_size or 0)],
                dtype=torch.int64,
                device=self.device,
            )
            dist.broadcast(fixed_size_tensor, src=0)
            if int(fixed_size_tensor.item()) <= 0:
                raise RuntimeError("Rank 0 failed to derive a valid fixed subgraph size during scan.")
            dataset.fixed_subgraph_size = int(fixed_size_tensor.item())
            if self.is_main_process:
                logger.info(
                    "   🌐 Distributed fixed-size sync complete: global fixed_subgraph_size=%s",
                    dataset.fixed_subgraph_size,
                )
            self._log_cuda_memory(f"{self.mode}_dataloader_after_fixed_size_sync", force=True)

        if self.is_main_process and hasattr(dataset, "get_sampling_summary"):
            summary = dataset.get_sampling_summary()
            logger.info(
                "   📐 Sampling summary: cells[min/mean/max]=%s/%.1f/%s | "
                "budget[min/mean/max]=%s/%.1f/%s",
                summary.get("num_cells_min"),
                summary.get("num_cells_mean", 0.0),
                summary.get("num_cells_max"),
                summary.get("budget_min"),
                summary.get("budget_mean", 0.0),
                summary.get("budget_max"),
            )
            if summary.get("num_cells_max_case") is not None:
                logger.info(
                    "   📐 Sampling extremes: min_case=%s (%s cells, budget=%s) | "
                    "max_case=%s (%s cells, budget=%s)",
                    summary.get("num_cells_min_case"),
                    summary.get("num_cells_min"),
                    summary.get("budget_min"),
                    summary.get("num_cells_max_case"),
                    summary.get("num_cells_max"),
                    summary.get("budget_max"),
                )
                num_cells_min = summary.get("num_cells_min")
                num_cells_max = summary.get("num_cells_max")
                if (
                    int(batch_size) <= 1
                    and num_cells_min
                    and num_cells_max
                    and float(num_cells_max) / float(num_cells_min) > 1.5
                ):
                    logger.warning(
                        "   ⚠️  Large per-case graph-size skew detected under batch_size=1 without truncation. "
                        "Expect visible GPU memory spikes when the largest case is sampled."
                    )
            logger.info(
                "   📐 Effective fixed-size policy: mode=%s | fixed_size=%s",
                effective_fixed_mode,
                dataset.fixed_subgraph_size,
            )

        if len(dataset) > 0:
            # Get num_workers based on mode
            if self.mode == "train":
                num_workers = self.config.training_config.get('num_workers', 4)
                persistent_workers = bool(self.config.training_config.get('persistent_workers', num_workers > 0))
                prefetch_factor = self.config.training_config.get('prefetch_factor', None)
                # Epoch-driven sampling (dataset.set_epoch) needs workers to RE-FORK each epoch so
                # they inherit the updated _current_epoch. Persistent workers keep a stale copy and
                # silently re-freeze every case to one subgraph (the exact bug set_epoch fixes). The
                # default above resolves to True whenever persistent_workers is omitted and
                # num_workers>0, so force it off for such a dataset regardless of the config default.
                if persistent_workers and hasattr(dataset, 'set_epoch'):
                    logger.warning(
                        "⚠️  Forcing persistent_workers=False for training: the dataset uses "
                        "epoch-driven sampling (set_epoch), which needs workers re-forked each epoch. "
                        "persistent_workers=True would freeze every case to a single subgraph."
                    )
                    persistent_workers = False
            else:
                # For inference, default to 0 workers (safer for distributed). For single-GPU TTA
                # sweeps on large meshes (e.g. AirfRANS ~180K nodes) the per-case subgraph budget
                # forces thousands of subgraphs at low sample_ratio; building them single-threaded
                # leaves the GPU idle. Set inference_config.tta_num_workers>0 to parallelize.
                num_workers = int(self.config.inference_config.get('tta_num_workers', 0))
                persistent_workers = bool(num_workers > 0)
                prefetch_factor = (2 if num_workers > 0 else None)

            # For inference mode with subsampling, use custom collate_fn to filter None
            collate_fn = None
            if self.mode == "inference" and dataset.subsampling:
                from FVdomain.HomoGraph_dataset import collate_fn_filter_none
                collate_fn = collate_fn_filter_none

            # Determine shuffle and sampler based on mode
            # Training mode:
            #   - Use DistributedSampler to distribute cases across GPUs
            #   - shuffle=False (DistributedSampler handles shuffling)
            # Inference mode:
            #   - No DistributedSampler (HomoGraphDataset handles distribution internally via rank/world_size)
            #   - shuffle=False for deterministic results
            sampler = None
            shuffle = False

            if self.mode == "train" and self.distributed:
                # Training with multiple GPUs: use DistributedSampler to distribute cases
                from torch.utils.data.distributed import DistributedSampler
                sampler = DistributedSampler(
                    dataset,
                    num_replicas=self.world_size,
                    rank=self.rank,
                    shuffle=True,  # DistributedSampler handles shuffling
                    drop_last=False
                )
                shuffle = False  # Must be False when using sampler
            elif self.mode == "train":
                # Training with single GPU: use shuffle
                shuffle = True
            else:
                # Inference mode: no shuffling
                shuffle = False
                
            # Use PyG's DataLoader which handles graph batching automatically
            data_loader = PyGDataLoader(
                dataset=dataset,
                batch_size=batch_size,
                shuffle=shuffle,
                sampler=sampler,
                num_workers=num_workers,
                persistent_workers=(persistent_workers and num_workers > 0),
                prefetch_factor=(int(prefetch_factor) if (prefetch_factor is not None and num_workers > 0) else None),
                collate_fn=collate_fn,
            )

            if self.is_main_process:
                logger.info(f"   ✅ DataLoader: {len(data_loader)} batches")
                logger.info(
                    "   ⚙️  DataLoader workers: num_workers=%s persistent_workers=%s prefetch_factor=%s",
                    num_workers,
                    bool(persistent_workers and num_workers > 0),
                    (int(prefetch_factor) if (prefetch_factor is not None and num_workers > 0) else None),
                )
                if self.distributed:
                    logger.info(f"   📊 Total samples: {len(dataset)}")
                    logger.info(f"   📊 Samples per GPU: ~{len(dataset) // self.world_size}")
        else:
            data_loader = None
            if self.is_main_process:
                logger.info(f"   ⚠️  No {split} cases found")

        return data_loader

    # ========== Output Directory ==========
    def _get_output_dir(self) -> Path:
        """Get output directory based on mode"""
        if not self.logger:
            raise RuntimeError("Logger not initialized. Call _load_checkpoint first.")

        if self.mode == "inference":
            output_dir = Path(self.logger.saving_path, 'inference')
        else:
            output_dir = Path(self.logger.saving_path)

        os.makedirs(output_dir, exist_ok=True)
        return output_dir

    # ========== Inference Core Methods ==========
    def _preinitialize_accumulator(
        self,
        data_loader: DataLoader
    ) -> Tuple[Dict[str, Dict[str, Any]], int]:
        """
        Pre-initialize accumulator for inference mode

        Args:
            data_loader: DataLoader

        Returns:
            accumulator: Accumulator dictionary
            pred_dim: Prediction dimension
        """
        import h5py

        if self.is_main_process:
            logger.info(f"🔧 Pre-initializing accumulator on GPU {self.rank}...")

        # Get H5 file list
        h5_file_list = data_loader.dataset.h5_list

        # Get prediction dimension; rely on configured output keys
        pred_dim = sum(key.dimension for key in self.output_keys)

        # Pre-initialize accumulator
        accumulator = {}
        for idx, h5_path in enumerate(h5_file_list):
            # Use the original case name from dataset.cases (preserves format like 'sample_XXX/base')
            case_name = data_loader.dataset.cases[idx]

            # Read number of cells and mesh data
            with h5py.File(h5_path, 'r') as f:
                if 'cpd|centroid' not in f:
                    raise KeyError(f"'cpd|centroid' dataset not found in {h5_path}")
                num_cpd_cells = len(f['cpd|centroid'])

                # Load mesh data for VTU export
                mesh_pos = torch.from_numpy(f["node|pos"][()]).to(torch.float32)
                pv_cells_node = torch.from_numpy(f["pv_cells_node"][()]).long()
                pv_cells_type = torch.from_numpy(f["pv_cells_type"][()]).long()
                # Which scored nodes are boundary faces. nRMSE is computed over ALL cpd_cells,
                # and 26% of them ARE the boundary — so a pooled score mixes two different
                # questions: how well the model reconstructs the interior FROM the boundary,
                # and how well it reproduces the boundary values it was handed. Those must be
                # read apart. Without the split, zeroing the boundary damages the boundary
                # rows' own predictions and the pooled number moves, which reads as "the model
                # uses the boundary" even for a model whose interior provably cannot see it.
                is_boundary = torch.from_numpy(f["cpd|cell_type"][()]).long().reshape(-1) != 0

            # Pre-allocate accumulation tensors on GPU
            case_dict = {
                # GPU tensors: accumulation data
                'predictions_sum': torch.zeros(num_cpd_cells, pred_dim, dtype=torch.float32, device=self.device),
                # Sum of SQUARED predictions, so that a node predicted by several different
                # pivot draws yields the spread of those predictions, not just their mean.
                # That spread is the direct measurement of whether sampling made the answer
                # non-unique: if the map (subgraph, sampled boundary) -> field were a function,
                # every draw would agree and the spread would be identically zero.
                # float64 (not float32): the spread is recovered as E[x^2]-E[x]^2, and for a
                # physical field like T~300 K the two terms are ~9e4 while their difference (the
                # variance) is O(0.01). In float32 (7 digits) that subtraction loses the signal to
                # catastrophic cancellation exactly in the low-spread regime the thesis leans on;
                # float64 (16 digits) resolves it. Only this accumulator needs it (~15 MB extra).
                'predictions_sq_sum': torch.zeros(num_cpd_cells, pred_dim, dtype=torch.float64, device=self.device),
                'counts': torch.zeros(num_cpd_cells, dtype=torch.float32, device=self.device),
                # Target tensor (will be filled from first batch)
                'target': None,
                # Scalar: sample count
                'num_samples': 0,
                # String: H5 file path
                'h5_path': h5_path,
                # Mesh data for VTU export (CPU tensors)
                'mesh_pos': mesh_pos,
                'pv_cells_node': pv_cells_node,
                'pv_cells_type': pv_cells_type,
                'is_boundary': is_boundary,
            }

            accumulator[case_name] = case_dict

        if self.is_main_process:
            logger.info(f"   📊 Initialized {len(accumulator)} cases on GPU {self.rank}")

        return accumulator, pred_dim

    def _accumulate_predictions(
        self,
        accumulator: Dict,
        batch_data
    ):
        """
        Accumulate predictions for inference mode

        Args:
            accumulator: Accumulator dictionary
            batch_data: Batch data (contains predictions)
        """
        # Get batch information
        batch_indices = batch_data.batch
        # Handle both case_name (single) and case_names (batched)
        if hasattr(batch_data, 'case_names'):
            case_names = batch_data.case_names
        elif hasattr(batch_data, 'case_name'):
            # Single graph or PyG didn't batch the string attribute
            case_name_attr = batch_data.case_name
            if isinstance(case_name_attr, list):
                case_names = case_name_attr
            else:
                case_names = [case_name_attr]
        else:
            raise AttributeError("Batch data has neither 'case_name' nor 'case_names'")

        original_indices = batch_data.original_node_indices
        predictions = batch_data.prediction

        # Get target if available (for metric computation)
        targets = batch_data.y if hasattr(batch_data, 'y') else None

        # Get number of graphs in batch
        num_graphs = len(case_names)

        # Accumulate for each graph
        for graph_idx in range(num_graphs):
            # Find nodes belonging to current graph
            node_mask = (batch_indices == graph_idx)

            # Extract current graph data
            case_name = case_names[graph_idx]
            graph_original_indices = original_indices[node_mask]
            graph_predictions = predictions[node_mask]

            # Get accumulator data
            case_data = accumulator[case_name]

            # Accumulate predictions on GPU
            case_data['predictions_sum'].index_add_(0, graph_original_indices, graph_predictions)
            case_data['predictions_sq_sum'].index_add_(
                0, graph_original_indices, graph_predictions.double() ** 2
            )
            case_data['counts'].index_add_(
                0, graph_original_indices,
                torch.ones(len(graph_original_indices), dtype=torch.float32, device=self.device)
            )

            # Store target values (accumulate from all batches to get complete target)
            # 🔧 FIX: 不能只从第一个batch提取target,因为采样模式下每个batch只包含部分节点
            if targets is not None:
                graph_targets = targets[node_mask]
                # Initialize target tensor on first batch
                if case_data['target'] is None:
                    case_data['target'] = torch.zeros_like(case_data['predictions_sum'])
                    num_cells = case_data['predictions_sum'].shape[0]
                    case_data['target_counts'] = torch.zeros(num_cells, dtype=torch.float32, device=self.device)
                
                # Store target values for current batch nodes
                case_data['target'].index_copy_(0, graph_original_indices, graph_targets)
                
                # Mark these nodes as having target (set count to 1)
                # We use index_copy_ with ones to ensure it's 1 regardless of how many times it's visited locally
                ones = torch.ones(len(graph_original_indices), dtype=torch.float32, device=self.device)
                case_data['target_counts'].index_copy_(0, graph_original_indices, ones)            # Increment sample count
            case_data['num_samples'] += 1

    def _finalize_predictions(
        self,
        case_name: str,
        case_data: Dict
    ):
        """
        Finalize predictions: compute average for inference mode

        Args:
            case_name: Case name
            case_data: Case data (contains predictions_sum and counts)

        Returns:
            finalized_data: Finalized prediction data (Data object)
        """
        from torch_geometric.data import Data

        # Get counts and sums (on GPU)
        counts = case_data['counts']
        predictions_sum = case_data['predictions_sum']

        # Check for unsampled nodes
        zero_count_mask = counts == 0
        num_zero_counts = zero_count_mask.sum().item()

        if num_zero_counts > 0:
            if self.is_main_process:
                logger.warning(f"   ⚠️  {case_name}: {num_zero_counts} nodes were not sampled!")
            # Set count to 1 for unsampled nodes to avoid division by zero
            counts = counts.clone()
            counts[zero_count_mask] = 1.0

        # Compute average predictions on GPU
        averaged_predictions = predictions_sum / counts.unsqueeze(1)

        # Draw spread: the per-node standard deviation of the predictions this node received
        # from DIFFERENT pivot draws. Only nodes seen at least twice carry any information —
        # a node predicted once has no spread, and folding its 0 into the mean would dilute
        # the statistic toward "everything agrees" exactly in the low-rho regime where the
        # disagreement is largest. So the mask is >= 2, and the number of qualifying nodes is
        # reported alongside, because a spread averaged over 12 nodes is not a measurement.
        multi = counts >= 2
        n_multi = int(multi.sum().item())
        if n_multi > 0:
            # Do the whole E[x^2]-E[x]^2 in float64 (sq_sum is already float64); a float32 mean^2 at
            # ~9e4 would re-introduce the cancellation the float64 accumulator was chosen to avoid.
            c = counts[multi].unsqueeze(1).double()
            mean = predictions_sum[multi].double() / c
            var = (case_data['predictions_sq_sum'][multi] / c - mean ** 2).clamp_min(0.0)
            result_draw_spread = var.sqrt().mean(dim=0).float()  # [C], physical units
        else:
            result_draw_spread = None

        # Create Data object
        result_data = Data(
            prediction=averaged_predictions.cpu(),
            case_name=case_name
        )
        result_data.draw_spread = None if result_draw_spread is None else result_draw_spread.cpu()
        result_data.draw_spread_nodes = n_multi
        result_data.draw_spread_total_nodes = int(counts.numel())
        result_data.is_boundary = case_data['is_boundary']
        # Which rows any draw actually reached. With a whole-domain pivot pool the permutation
        # slices tile every cpd id, so this is all-True and every metric downstream is
        # unchanged. With a RESTRICTED pool (a dragged ROI) it is not: the rows above got
        # prediction 0.0 forced through counts=1, and their target was never written either
        # (it stays at the zeros_like() initialisation in _accumulate_predictions), so an
        # unmasked nRMSE would silently average in ~|uncovered| exact zeros and report the ROI
        # arm as several times BETTER than the full-domain baseline. Export the mask so
        # _compute_metrics can score only what was predicted.
        result_data.covered = (~zero_count_mask).cpu()
        # Raw per-node draw count (BEFORE the zero->1 clamp above). The offline analysis needs
        # it to prove the two arms scored the same cells with a known redundancy; counts==0
        # rows must never enter a metric, and counts tells you how much draw-averaging each
        # scored cell actually received.
        result_data.draw_counts = case_data['counts'].cpu()

        # Add target if available
        if case_data['target'] is not None:
            target = case_data['target']
            if 'target_counts' in case_data:
                target_counts = case_data['target_counts']
                # Avoid division by zero
                target_counts = target_counts.clone()
                target_counts[target_counts == 0] = 1.0
                target = target / target_counts.unsqueeze(1)
            
            result_data.target = target.cpu()

        # Store h5_path for later use in VTU export
        result_data.h5_path = case_data['h5_path']

        # Load mesh data from accumulator for VTU export
        save_vtu = self.run_config.get('save_vtu', False)
        if save_vtu:
            result_data.mesh_pos = case_data['mesh_pos']
            result_data.pv_cells_node = case_data['pv_cells_node']
            result_data.pv_cells_type = case_data['pv_cells_type']

        return result_data

    def _all_reduce_accumulator(self, accumulator: Dict):
        """
        All-Reduce accumulator across all processes for inference mode

        Args:
            accumulator: Accumulator dictionary
        """
        if not self.distributed or self.world_size == 1:
            return

        for case_name, case_data in accumulator.items():
            # All-Reduce predictions_sum and counts
            dist.all_reduce(case_data['predictions_sum'], op=dist.ReduceOp.SUM)
            # The squared sum reduces the same way. It must, or the draw-spread would be
            # computed from one rank's draws only, and every rank would report a different
            # (and smaller) number.
            dist.all_reduce(case_data['predictions_sq_sum'], op=dist.ReduceOp.SUM)
            dist.all_reduce(case_data['counts'], op=dist.ReduceOp.SUM)

            # 🔧 FIX: All-Reduce target values
            # 使用SUM聚合target和target_counts，然后归一化
            if case_data['target'] is not None:
                dist.all_reduce(case_data['target'], op=dist.ReduceOp.SUM)
                if 'target_counts' in case_data:
                    dist.all_reduce(case_data['target_counts'], op=dist.ReduceOp.SUM)

            # All-Reduce num_samples
            num_samples_tensor = torch.tensor(
                [case_data['num_samples']],
                dtype=torch.int64,
                device=self.device
            )
            dist.all_reduce(num_samples_tensor, op=dist.ReduceOp.SUM)
            case_data['num_samples'] = num_samples_tensor.item()

        if self.is_main_process:
            logger.info(f"   ✅ All-reduce complete for {len(accumulator)} cases")

    def _run_inference_loop(self, data_loader: DataLoader, accumulator: Dict) -> Dict:
        """
        Unified inference loop for inference mode.

        Args:
            data_loader: DataLoader for the data
            accumulator: Pre-initialized accumulator dictionary

        Returns:
            Dictionary of results for each case
        """
        import time
        self._reset_cuda_peak_memory_stats()
        self.simulator.eval()

        # Determine progress bar label based on mode
        desc = "Inference"

        if self.is_main_process:
            logger.info("📊 Phase 1: Sampling and accumulation...")

        # 详细计时
        forward_times = []
        data_load_times = []
        accumulate_times = []

        with torch.no_grad():
            if self.is_main_process:
                iterator = tqdm(data_loader, desc=desc, ncols=100)
            else:
                iterator = data_loader

            t_prev = time.time()
            for batch_idx, batch_data in enumerate(iterator):
                # Skip None batches (all samples filtered out)
                if batch_data is None:
                    continue

                data_load_times.append(time.time() - t_prev)

                t0 = time.time()
                batch_data = batch_data.to(self.device)
                torch.cuda.synchronize()

                # FLOPs: measured once per ratio on a clone, fully guarded (see _measure_forward_flops).
                if batch_idx == 0 and self.is_main_process:
                    self._inference_flops_g = self._measure_forward_flops(batch_data)

                t1 = time.time()
                if self.use_amp:
                    with self._autocast_context():
                        batch_data = self.simulator.inference(batch_data)
                else:
                    batch_data = self.simulator.inference(batch_data)
                torch.cuda.synchronize()
                forward_times.append(time.time() - t1)

                # Fail on the FIRST forward, not after an hour of inference, if a requested
                # boundary intervention never reached the model. A hook wired into the wrong
                # branch changes nothing, and "changed nothing" comes out as delta-nRMSE = 0 —
                # which is the exact signature of the finding "this model ignores the
                # boundary". The two are indistinguishable downstream, so they must be
                # distinguished here.
                if batch_idx == 0:
                    bc_intervention.assert_fired()
                    # Same contract for the slice-basis capture/injection: every pooling
                    # site is visited exactly once per forward, so after the first forward
                    # the run's site set is complete and can be checked against the
                    # cache's, layer index by layer index. A mis-wired injection changes
                    # nothing, and "changed nothing" is delta = 0 — indistinguishable from
                    # "the cached basis does not help".
                    slice_basis.assert_fired()

                t2 = time.time()
                self._accumulate_predictions(accumulator, batch_data)
                accumulate_times.append(time.time() - t2)

                t_prev = time.time()

        # 输出详细时间
        if self.is_main_process and forward_times:
            logger.info(f"   📊 Per-batch forward times (ms): {[f'{t*1000:.1f}' for t in forward_times]}")
            logger.info(f"   📊 Total forward: {sum(forward_times)*1000:.1f} ms")
            logger.info(f"   📊 Total data load: {sum(data_load_times)*1000:.1f} ms")
            logger.info(f"   📊 Total accumulate: {sum(accumulate_times)*1000:.1f} ms")

        # Persist loop timing + peak memory so Inferencer.inference() can report them.
        self._inference_loop_timing = {
            "data_load_s": float(sum(data_load_times)),
            "nn_forward_s": float(sum(forward_times)),
            "accumulate_s": float(sum(accumulate_times)),
        }
        peak_alloc, _peak_reserved = self._log_cuda_memory("inference_loop_done")
        self._peak_memory_gib = float(peak_alloc)

        # Write the captured basis (and, on an inject run, the receipt including the
        # circularity meter) explicitly here rather than trusting atexit alone.
        if self.is_main_process:
            slice_basis.flush()
            slice_basis.log_summary()

        # Cost columns for the AeroGTO-style comparison table. Per-forward latency = MEDIAN of the
        # CUDA-synced per-batch forward times (median, not mean: the first batch carries cuDNN
        # warm-up + the FLOP-counter overhead). #params is constant across sampling ratios.
        if forward_times:
            _sorted = sorted(forward_times)
            self._inference_latency_ms = float(_sorted[len(_sorted) // 2]) * 1000.0
        _model = getattr(self.simulator.model, "module", self.simulator.model)  # unwrap DDP
        self._model_params_m = float(sum(p.numel() for p in _model.parameters())) / 1e6

        if self.distributed and self.world_size > 1:
            if self.is_main_process:
                logger.info("Phase 2: All-Reduce aggregation...")
            self._all_reduce_accumulator(accumulator)
            dist.barrier()

        if self.is_main_process:
            logger.info("Phase 3: Finalizing predictions...")

        results = {}
        for case_name, case_data in accumulator.items():
            results[case_name] = self._finalize_predictions(case_name, case_data)

        return results

    # ========== Mixed Precision Support ==========

    def _get_amp_dtype(self):
        if not self.use_amp or self.device.type != 'cuda':
            return None

        amp_dtype = str(self.run_config.get('amp_dtype', 'float16')).strip().lower()
        if amp_dtype in {'float16', 'fp16', 'half'}:
            return torch.float16
        if amp_dtype in {'bfloat16', 'bf16'}:
            return torch.bfloat16
        raise ValueError(f"Unsupported amp_dtype: {amp_dtype}")

    def _autocast_context(self):
        if self.use_amp and self.device.type == 'cuda':
            return torch.amp.autocast("cuda", dtype=self.amp_dtype)
        return nullcontext()

    def _setup_mixed_precision(self):
        """Setup mixed precision"""
        if self.use_amp and self.device.type == 'cuda':
            if self.amp_dtype == torch.bfloat16:
                if self.is_main_process:
                    logger.info("🚀 Enabling mixed precision (AMP)")
                    logger.info("   AMP autocast dtype: bfloat16")
                    logger.info("   GradScaler disabled for bfloat16")
                return None

            amp_kwargs = {}
            if 'amp_init_scale' in self.run_config:
                amp_kwargs['init_scale'] = float(self.run_config['amp_init_scale'])
            if 'amp_growth_factor' in self.run_config:
                amp_kwargs['growth_factor'] = float(self.run_config['amp_growth_factor'])
            if 'amp_backoff_factor' in self.run_config:
                amp_kwargs['backoff_factor'] = float(self.run_config['amp_backoff_factor'])
            if 'amp_growth_interval' in self.run_config:
                amp_kwargs['growth_interval'] = int(self.run_config['amp_growth_interval'])
            if self.is_main_process:
                logger.info("🚀 Enabling mixed precision (AMP)")
                logger.info(f"   AMP autocast dtype: {self.amp_dtype}")
                if amp_kwargs:
                    logger.info(f"   AMP GradScaler kwargs: {amp_kwargs}")
            return torch.amp.GradScaler('cuda', **amp_kwargs)
        return None

    # ========== Process Synchronization ==========

    def _sync_processes(self):
        """Synchronize all processes in distributed mode"""
        if self.distributed:
            dist.barrier()

    def _aggregate_metrics(self, metrics: Dict[str, Any]) -> Dict[str, Any]:
        """
        Aggregate metrics across all processes in distributed mode

        Args:
            metrics: Dictionary of metrics to aggregate

        Returns:
            Aggregated metrics dictionary
        """
        if not self.distributed or self.world_size == 1:
            return metrics

        aggregated = {}
        for key, value in metrics.items():
            if isinstance(value, (int, float)):
                tensor = torch.tensor(float(value), device=self.device)
                dist.all_reduce(tensor, op=dist.ReduceOp.SUM)
                aggregated[key] = tensor.item() / self.world_size
            else:
                aggregated[key] = value

        return aggregated

    # ========== Checkpoint Management ==========

    def _save_checkpoint(self, checkpoint_path: str, **kwargs):
        """
        Save checkpoint (unified API for all modes)

        Args:
            checkpoint_path: Path to save checkpoint
            **kwargs: Additional state to save (optimizer, scheduler, epoch, etc.)
        """
        os.makedirs(os.path.dirname(checkpoint_path), exist_ok=True)
        # Include text_encoder state_dict if it was saved during precomputation.
        # This ensures inference can restore the exact text encoder weights that
        # were used to produce the pre-computed embeddings during training.
        if hasattr(self, '_text_encoder_state_dict') and self._text_encoder_state_dict is not None:
            kwargs['text_encoder_state_dict'] = self._text_encoder_state_dict
        self.simulator.save_model(checkpoint_path, **kwargs)

        if self.is_main_process:
            logger.info(f"💾 Checkpoint saved: {checkpoint_path}")

    def _extract_batch_debug_scalar(self, batch: Any, attr_name: str) -> Optional[int]:
        """Read a scalar debug attribute from a PyG batch without assuming a specific container type."""
        value = getattr(batch, attr_name, None)
        if value is None:
            return None
        if isinstance(value, torch.Tensor):
            if value.numel() == 0:
                return None
            return int(value.reshape(-1)[0].item())
        if isinstance(value, (list, tuple)):
            if not value:
                return None
            return int(value[0])
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    # ========== Training Loop ==========

    def _run_train_loop(
        self,
        train_loader: DataLoader,
        optimizer: torch.optim.Optimizer,
        scheduler: Optional[torch.optim.lr_scheduler._LRScheduler] = None,
        current_epoch: int = 0,
        global_step: int = 0,
        log_metrics_fn: Optional[callable] = None
    ) -> Dict[str, Any]:
        """
        Run one epoch of training loop (unified for all trainers)

        Args:
            train_loader: Training data loader
            optimizer: Optimizer instance
            scheduler: Optional learning rate scheduler
            current_epoch: Current epoch number (for distributed sampler)
            global_step: Current global step (for logging)
            log_metrics_fn: Optional function to log metrics (signature: fn(metrics: Dict, step: int))

        Returns:
            Dictionary containing training metrics
        """
        # Set epoch for distributed sampler
        if self.distributed and hasattr(train_loader.sampler, 'set_epoch'):
            train_loader.sampler.set_epoch(current_epoch)

        # Advance the training subgraph draw to this epoch. Without this, every case is trained on
        # ONE frozen k-hop subgraph for the entire run (the pivot cursor lived in the DataLoader
        # worker and was lost each time the non-persistent worker died — verified). Done for BOTH
        # single-GPU and DDP, before the epoch's iteration forks its workers, so the fresh workers
        # inherit the new epoch. Guarded so datasets without set_epoch (e.g. sensor-only) are fine.
        _ds = getattr(train_loader, 'dataset', None)
        if _ds is not None and hasattr(_ds, 'set_epoch'):
            _ds.set_epoch(current_epoch)

        self.simulator.train()
        total_loss = 0.0
        num_batches = 0
        batch_times = []

        # Gradient accumulation setup
        accumulation_steps = self.config.training_config.get('gradient_accumulation_steps', 1)
        accumulated_batches = 0
        optimizer_steps = 0
        sampled_pivots_total = 0
        sampled_nodes_before_total = 0
        sampled_nodes_after_total = 0
        truncated_batches = 0
        sample_stat_batches = 0

        # Match the validated one-batch training path as closely as possible.
        optimizer.zero_grad(set_to_none=True)
        self._reset_cuda_peak_memory_stats()
        self._log_cuda_memory(f"epoch_{current_epoch + 1}_start", force=True)

        for batch_idx, batch in enumerate(train_loader):

            # 🧪 Opt-in smoke cap (default 0 = disabled → no behavior change). When
            # GTC_SMOKE_MAX_STEPS>0, stop the epoch after that many steps so a sample-ratio /
            # memory probe self-terminates without needing to kill the process. Peak memory is
            # logged at the cap; an OOM before the cap self-terminates via the existing handler.
            _smoke_cap = int(os.environ.get("GTC_SMOKE_MAX_STEPS", "0") or "0")
            if _smoke_cap and batch_idx >= _smoke_cap:
                self._log_cuda_memory(f"smoke_cap_{batch_idx}", force=True)
                logger.info(f"🧪 SMOKE cap reached: stopping epoch after {batch_idx} steps")
                break

            # Only track timing on main process to reduce memory overhead
            batch_start_time = torch.cuda.Event(enable_timing=True) if (self.device.type == 'cuda' and self.is_main_process) else None
            batch_end_time = torch.cuda.Event(enable_timing=True) if (self.device.type == 'cuda' and self.is_main_process) else None

            if batch_start_time:
                batch_start_time.record()

            # Move batch to device
            batch = batch.to(self.device)
            if batch_idx < self.memory_debug_batches:
                self._log_cuda_memory("batch_to_device", batch=batch, batch_idx=batch_idx)
            sampled_pivots = self._extract_batch_debug_scalar(batch, "sampled_pivot_count")
            sampled_nodes_before = self._extract_batch_debug_scalar(batch, "sampled_nodes_before_trunc")
            sampled_nodes_after = self._extract_batch_debug_scalar(batch, "sampled_nodes_after_trunc")
            sample_was_truncated = self._extract_batch_debug_scalar(batch, "sample_was_truncated")
            if sampled_pivots is not None:
                sampled_pivots_total += sampled_pivots
            if sampled_nodes_before is not None:
                sampled_nodes_before_total += sampled_nodes_before
            if sampled_nodes_after is not None:
                sampled_nodes_after_total += sampled_nodes_after
            if sample_was_truncated is not None:
                truncated_batches += int(sample_was_truncated > 0)
            if sampled_nodes_after is not None:
                sample_stat_batches += 1

            if (
                self._should_log_sample_debug()
                and current_epoch == 0
                and batch_idx < self.sample_debug_batches
            ):
                logger.info(
                    "   🔎 Batch %d sampling: rank=%s case=%s pivots=%s nodes_before=%s nodes_after=%s edges=%s truncated=%s total_cells=%s",
                    batch_idx + 1,
                    self.rank,
                    getattr(batch, "case_name", None),
                    sampled_pivots,
                    sampled_nodes_before,
                    sampled_nodes_after,
                    self._extract_batch_debug_scalar(batch, "sampled_edge_count"),
                    bool(sample_was_truncated) if sample_was_truncated is not None else None,
                    self._extract_batch_debug_scalar(batch, "num_cpd_cells_total"),
                )
            collect_numeric_debug = self._should_collect_numeric_debug(current_epoch, batch_idx)
            if hasattr(self.simulator, "set_debug_enabled"):
                self.simulator.set_debug_enabled(collect_numeric_debug and self.numeric_debug_deep)

            # Forward pass and loss computation using Simulator
            if self.use_amp:
                try:
                    with self._autocast_context():
                        loss = self.simulator.compute_loss(batch)
                except torch.OutOfMemoryError as exc:
                    self._log_batch_oom("forward", batch, current_epoch, batch_idx)
                    raise exc
                if batch_idx < self.memory_debug_batches:
                    self._log_cuda_memory("forward_loss_ready", batch=batch, batch_idx=batch_idx)

                if collect_numeric_debug:
                    self._emit_numeric_debug(
                        batch=batch,
                        current_epoch=current_epoch,
                        batch_idx=batch_idx,
                        loss=loss,
                        prefix="train_batch",
                    )

                if not torch.isfinite(loss):
                    self._emit_numeric_debug(
                        batch=batch,
                        current_epoch=current_epoch,
                        batch_idx=batch_idx,
                        loss=loss,
                        prefix="nonfinite_loss",
                    )
                    raise FloatingPointError(
                        f"Non-finite loss detected at epoch {current_epoch + 1}, batch {batch_idx + 1}: {loss.detach().float().item()}"
                    )



                # Scale loss for gradient accumulation
                loss = loss / accumulation_steps

                # Backward pass
                try:
                    if self.scaler:
                        self.scaler.scale(loss).backward()
                    else:
                        loss.backward()
                    if batch_idx < self.memory_debug_batches:
                        self._log_cuda_memory("backward_done", batch=batch, batch_idx=batch_idx)
                except torch.OutOfMemoryError as exc:
                    self._log_batch_oom("backward", batch, current_epoch, batch_idx)
                    raise exc
            else:
                # Standard precision training
                try:
                    loss = self.simulator.compute_loss(batch)
                except torch.OutOfMemoryError as exc:
                    self._log_batch_oom("forward", batch, current_epoch, batch_idx)
                    raise exc

                if collect_numeric_debug:
                    self._emit_numeric_debug(
                        batch=batch,
                        current_epoch=current_epoch,
                        batch_idx=batch_idx,
                        loss=loss,
                        prefix="train_batch",
                    )

                if not torch.isfinite(loss):
                    self._emit_numeric_debug(
                        batch=batch,
                        current_epoch=current_epoch,
                        batch_idx=batch_idx,
                        loss=loss,
                        prefix="nonfinite_loss",
                    )
                    raise FloatingPointError(
                        f"Non-finite loss detected at epoch {current_epoch + 1}, batch {batch_idx + 1}: {loss.detach().float().item()}"
                    )



                # Scale loss for gradient accumulation
                loss = loss / accumulation_steps

                loss.backward()
                if batch_idx < self.memory_debug_batches:
                    self._log_cuda_memory("backward_done", batch=batch, batch_idx=batch_idx)

            # Update accumulation counter
            accumulated_batches += 1

            # Only update parameters when we've accumulated enough gradients
            if accumulated_batches == accumulation_steps or batch_idx == len(train_loader) - 1:
                if self.scaler:
                    # Gradient clipping
                    max_grad_norm = self.config.training_config.get('max_grad_norm')
                    if max_grad_norm:
                        self.scaler.unscale_(optimizer)
                        nn.utils.clip_grad_norm_(
                            self.simulator.parameters(),
                            max_grad_norm
                        )

                    # Optimizer step
                    prev_scale = self.scaler.get_scale()
                    self.scaler.step(optimizer)
                    self.scaler.update()
                    current_scale = self.scaler.get_scale()
                    optimizer_step_ran = current_scale >= prev_scale
                    if collect_numeric_debug:
                        self._emit_numeric_debug(
                            batch=batch,
                            current_epoch=current_epoch,
                            batch_idx=batch_idx,
                            loss=loss * accumulation_steps,
                            prefix="scaler",
                            extra={
                                "prev_scale": float(prev_scale),
                                "current_scale": float(current_scale),
                                "optimizer_step_ran": bool(optimizer_step_ran),
                            },
                        )
                    if optimizer_step_ran:
                        setattr(optimizer, "_opt_called", True)
                    if batch_idx < self.memory_debug_batches:
                        self._log_cuda_memory("optimizer_step", batch=batch, batch_idx=batch_idx)
                else:
                    # Gradient clipping
                    max_grad_norm = self.config.training_config.get('max_grad_norm')
                    if max_grad_norm:
                        nn.utils.clip_grad_norm_(
                            self.simulator.parameters(),
                            max_grad_norm
                        )

                    optimizer.step()
                    optimizer_step_ran = True
                    if batch_idx < self.memory_debug_batches:
                        self._log_cuda_memory("optimizer_step", batch=batch, batch_idx=batch_idx)

                # Learning rate scheduling (per-step for most schedulers)
                # Note: ReduceLROnPlateau should be called per-epoch, not per-step
                # 🔧 DISABLED: Per-step scheduling causes very slow LR decay with small batches_per_epoch
                # Moved to per-epoch scheduling in trainer.py
                # if scheduler and not isinstance(scheduler, torch.optim.lr_scheduler.ReduceLROnPlateau):
                #     scheduler.step()

                if optimizer_step_ran:
                    optimizer_steps += 1

                    if optimizer_steps <= 3 and self.is_main_process:
                        self._log_lora_gradient_check(optimizer_steps)

                # Zero gradients after update (set_to_none=True is faster)
                optimizer.zero_grad(set_to_none=True)
                accumulated_batches = 0

            # Update metrics (accumulate loss as tensor to avoid CPU-GPU sync)
            # Note: loss is already scaled by accumulation_steps
            total_loss += loss.item() * accumulation_steps
            num_batches += 1
            global_step += 1

            # Record batch time
            if batch_end_time:
                batch_end_time.record()
                torch.cuda.synchronize()
                batch_time = batch_start_time.elapsed_time(batch_end_time) / 1000.0
                batch_times.append(batch_time)

            # Log batch metrics (every 10 steps, only on main process)
            # Only call .item() when actually logging to reduce CPU-GPU sync
            if batch_idx % 10 == 0 and self.is_main_process and log_metrics_fn:
                batch_metrics = {
                    'train/batch_loss': (loss.item() * accumulation_steps),  # Unscale for logging
                    'train/learning_rate': optimizer.param_groups[0]['lr']
                }
                # Log GradScaler scale if using mixed precision
                if self.scaler:
                    batch_metrics['train/grad_scaler_scale'] = self.scaler.get_scale()
                # Log effective batch size
                if accumulation_steps > 1:
                    batch_metrics['train/accumulation_steps'] = accumulation_steps
                log_metrics_fn(batch_metrics, step=global_step)

        # Calculate epoch metrics
        avg_loss = total_loss / num_batches if num_batches > 0 else 0.0
        avg_batch_time = sum(batch_times) / len(batch_times) if batch_times else 0.0

        metrics = {
            'train_loss': avg_loss,
            'train/avg_batch_time': avg_batch_time,
            'train/batches_per_epoch': num_batches,
            'optimizer_steps': optimizer_steps,
            'global_step': global_step
        }

        if sample_stat_batches > 0:
            metrics.update(
                {
                    'train/avg_sampled_pivots': sampled_pivots_total / sample_stat_batches,
                    'train/avg_nodes_before_trunc': sampled_nodes_before_total / sample_stat_batches,
                    'train/avg_nodes_after_trunc': sampled_nodes_after_total / sample_stat_batches,
                    'train/truncated_batch_ratio': truncated_batches / sample_stat_batches,
                }
            )

        return metrics

    # ========== Resource Cleanup ==========

    def cleanup(self):
        """Cleanup resources"""
        if hasattr(self, 'logger') and self.logger is not None:
            self.logger.cleanup()

        # Empty CUDA cache if configured
        if self.device.type == 'cuda':
            torch.cuda.empty_cache()
