#!/usr/bin/env python3
"""
OpenFOAM Case Inference Script

Workflow:
1. Phase 1: Prepare inference data (OpenFOAM simulation + H5 update)
2. Phase 2: Run distributed inference
3. Phase 3: Save results (VTU files)

Usage:
    python src/inference.py                          # Single GPU
    torchrun --nproc_per_node=4 src/inference.py    # Multi-GPU
"""
from typing import List
import os
import sys
import argparse
import yaml
import json
import logging
logger = logging.getLogger(__name__)
import torch
import torch.distributed as dist
# from pytorch_sparse_solver.module_a import cg_differentiable, bicgstab_differentiable
# Add src directory to path for imports
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_dir)))
src_dir = os.path.join(project_root, 'src')
sys.path.insert(0, src_dir)


from config import Config
from Main.Data_driven.inferencer import Inferencer
from Utils.key import Key
from Utils.local_optimizer import (
    disable_cuda_graphs,
    disable_torch_compile,
    enable_torch_compile_for_production,
)
from Utils.normalization import StandardDeviationNormalizer
from utils import get_boundary_patches


def setup_distributed(world_size: int):
    """Setup distributed inference environment

    注意：当使用torchrun时，环境变量已经被设置好了
    单GPU模式下，不初始化分布式环境
    """
    # 单GPU模式：跳过分布式初始化
    if world_size == 1:
        return

    # 多GPU模式：初始化进程组（如果还没有初始化）
    if not dist.is_initialized():
        dist.init_process_group(backend='nccl')

    # 获取rank
    rank = dist.get_rank() if dist.is_initialized() else 0

    # Non-main processes only show WARNING and above using standard logging
    if rank != 0:
        logging.basicConfig(level=logging.WARNING)
    else:
        logging.basicConfig(level=logging.INFO)


def cleanup_distributed():
    """Cleanup distributed inference environment"""
    if dist.is_initialized():
        dist.destroy_process_group()


def distributed_inference_worker(local_rank: int, config: Config, input_keys: List[Key], output_keys: List[Key], target_keys: List[Key], world_size: int = 1):
    """Distributed inference worker process

    Args:
        local_rank: 本地进程rank（对应GPU ID）
        config: 配置对象
        input_keys: 输入keys
        output_keys: 输出keys
        target_keys: 目标keys (用于输入归一化)
        world_size: 总进程数（单GPU模式传入1）
    """
    # 设置当前进程使用的GPU
    # torchrun模式：local_rank就是GPU ID（相对于CUDA_VISIBLE_DEVICES）
    # 单GPU模式：使用GPU 0（相对于CUDA_VISIBLE_DEVICES）
    gpu_id = local_rank

    torch.cuda.set_device(gpu_id)
    setup_distributed(world_size)

    # 获取全局rank
    rank = dist.get_rank() if dist.is_initialized() else 0
    world_size = dist.get_world_size() if dist.is_initialized() else 1

    inference_config = config.inference_config

    # 检查是否为 sensor-only 模式
    sensor_only = inference_config.get('sensor_only_inference', False)
    sensor_positions = inference_config.get('sensor_positions', {})
    
    if rank == 0:
        mode_str = "Sensor-only" if sensor_only else "Full"
        logger.info(f"🚀 Inference started (Rank {rank}/{world_size}, Mode: {mode_str})")

    # 🔧 FIX: Pass target_keys to enable input normalization
    inferencer = Inferencer(
        config=config,
        input_keys=input_keys,
        output_keys=output_keys,
        target_keys=target_keys,
    )

    # 解析边界patches（仅Rank 0）
    if rank == 0:
        logger.info(f"\n📋 解析边界patches...")
    boundary_patches = get_boundary_patches(data_root=config.training_config.get('dataset_path'))

    # Get resolved case paths from main process
    # These are full paths resolved in main() during data preparation
    resolved_case_paths = inference_config.get('_resolved_case_paths', [])

    if resolved_case_paths:
        # Use resolved full paths - extract case names for inference
        case_names = [os.path.basename(p) for p in resolved_case_paths]
        # Use the first case's parent directory as data_root for inference
        data_root = os.path.dirname(resolved_case_paths[0])
    else:
        # Fallback to old behavior
        case_list = inference_config.get('case', [])
        case_names = [os.path.basename(p) for p in case_list] if case_list else None
        data_root = inference_config.get('dataset_path')

    # ================================================================
    # 根据模式选择推理方式
    # ================================================================
    if sensor_only and sensor_positions:
        # Sensor-only 模式：仅推理 sensor 位置
        if rank == 0:
            logger.info(f"🎯 Running Sensor-only inference ({len(sensor_positions)} sensors)")
        
        results = inferencer.sensor_inference(
            data_root=data_root,
            case_names=case_names,
            sensor_positions=sensor_positions,
            interpolation_k=inference_config.get('sensor_interpolation_k', 4),
            interpolation_method="rbf",  # 使用 RBF 插值
            full_inference_time=inference_config.get('_full_inference_time'),  # 用于计算加速比
            benchmark_iterations=inference_config.get('sensor_benchmark_iterations', 0)  # Benchmark 迭代次数
        )
    else:
        # 全场推理模式
        # Phase-1 (feature-prep) time is measured in main(); pass it to the inferencer so
        # the 3-part timing breakdown (feature-prep / data-load / NN-forward) is complete.
        inferencer.feature_prep_seconds = inference_config.get('_feature_prep_seconds', 0.0)
        results = inferencer.inference(
            data_root=data_root,
            case_names=case_names
        )
        
        # 如果定义了 sensor_positions，在全场推理后插值输出 sensor 值
        if sensor_positions and rank == 0:
            logger.info(f"\n🎯 Interpolating at {len(sensor_positions)} sensor positions...")
            # 这里可以添加全场推理后的 sensor 插值逻辑
            # 但需要从 results 中获取全场预测结果
    
    # 🔧 FIX: All ranks should cleanup, not just rank 0
    cleanup_distributed()
    
    return results

def setup_logging(log_level: str = "INFO", rank: int = 0):
    """
    Setup global logging configuration before any logger is used.

    Unified format: [Rank X] YYYY-MM-DD HH:MM:SS - module_name - LEVEL - message
    For single GPU (rank=0), still shows [Rank 0] for consistency.

    Args:
        log_level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        rank: Process rank for distributed training (default 0)
    """
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)

    # Clear existing handlers
    if root_logger.handlers:
        root_logger.handlers.clear()

    # Unified format: [Rank X] date - module - level - message
    formatter = logging.Formatter(
        f'[Rank {rank}] %(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(numeric_level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # Suppress verbose third-party library logs
    for lib in ['matplotlib', 'PIL', 'h5py', 'urllib3', 'numba']:
        logging.getLogger(lib).setLevel(logging.WARNING)


def configure_runtime_optimization(inference_config: dict):
    """Configure torch.compile/CUDA Graphs according to YAML settings."""
    enable_compile = bool(inference_config.get("enable_compile", False))

    if enable_compile:
        enable_torch_compile_for_production()
    else:
        disable_torch_compile()
        disable_cuda_graphs()


def main():
    """Main inference function"""
    parser = argparse.ArgumentParser(description="GTC inference entrypoint")
    parser.add_argument(
        "--config",
        default=None,
        help="Path to YAML config file. Defaults to cases/GTC/configs/config.yaml",
    )
    parser.add_argument(
        "--checkpoint",
        default=None,
        help="Optional checkpoint override. Supports a direct path or 'best'/'latest'.",
    )
    parser.add_argument(
        "--dataset-path",
        default=None,
        help="Optional inference dataset path override.",
    )
    args = parser.parse_args()

    # Setup logging FIRST before any logger.info() calls
    setup_logging("INFO")

    logger.info("🚀 Inference Script Started")
    logger.info("=" * 60)

    # Load configuration - 使用相对于当前脚本的路径
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = args.config or os.path.join(script_dir, 'configs/config.yaml')
    if not os.path.isabs(config_path):
        config_path = os.path.abspath(os.path.join(project_root, config_path))
    logger.info(f"📁 加载配置文件: {config_path}")

    with open(config_path, 'r') as f:
        config_data = yaml.safe_load(f)
    config = Config.from_dict(config_data)
    inference_config = config.inference_config
    configure_runtime_optimization(inference_config)

    if args.checkpoint is not None:
        inference_config['checkpoint_path'] = args.checkpoint
    if args.dataset_path is not None:
        inference_config['dataset_path'] = args.dataset_path

    # 转换相对路径为绝对路径
    training_config = config.training_config
    if not os.path.isabs(inference_config.get('dataset_path', '')):
        inference_config['dataset_path'] = os.path.join(project_root, inference_config['dataset_path'])

    if not os.path.isabs(training_config.get('dataset_path', '')):
        training_config['dataset_path'] = os.path.join(project_root, training_config['dataset_path'])

    checkpoint_value = inference_config.get('checkpoint_path')
    if (
        checkpoint_value
        and checkpoint_value not in {"best", "latest"}
        and not os.path.isabs(checkpoint_value)
    ):
        inference_config['checkpoint_path'] = os.path.join(project_root, inference_config['checkpoint_path'])

    logger.info(f"📊 Config: {inference_config.get('case')} | Mode: {'Optimized' if inference_config.get('is_optimized_mode') else 'Full'}")

    # ================================================================
    # Load dataset statistics from TRAINING dataset meta.json
    # IMPORTANT: Always use training dataset's meta.json for normalization
    # This ensures consistent normalization between training and inference
    # ================================================================
    training_dataset_path = training_config.get('dataset_path')
    meta_json_path = os.path.join(training_dataset_path, 'meta.json')

    if not os.path.exists(meta_json_path):
        raise FileNotFoundError(
            f"Training dataset meta.json not found at {meta_json_path}\n"
            f"Please ensure training_config.dataset_path points to the training dataset with meta.json"
        )

    logger.info(f"📊 Loading normalization statistics from training dataset: {meta_json_path}")

    with open(meta_json_path, "r") as f:
        dataset_statistics = json.load(f)["dataset_statistics"]

    # Create input/output keys
    input_keys = [
        Key(name="u_cpd_cell_init", dimension=1),
        Key(name="v_cpd_cell_init", dimension=1),
        Key(name="w_cpd_cell_init", dimension=1),
        Key(name="p_cpd_cell_init", dimension=1),
        Key(name="T_cpd_cell_init", dimension=1),
    ]

    output_keys = [
        Key(
            name="u_cpd_cell_pred", dimension=1,
            normalizer=StandardDeviationNormalizer(
                mean=torch.tensor(dataset_statistics["mean"]["uvw_cpd_cell_u"], dtype=torch.float32),
                std=torch.tensor(dataset_statistics["std"]["uvw_cpd_cell_u"], dtype=torch.float32),
            )
        ),
        Key(
            name="v_cpd_cell_pred", dimension=1,
            normalizer=StandardDeviationNormalizer(
                mean=torch.tensor(dataset_statistics["mean"]["uvw_cpd_cell_v"], dtype=torch.float32),
                std=torch.tensor(dataset_statistics["std"]["uvw_cpd_cell_v"], dtype=torch.float32),
            )
        ),
        Key(
            name="w_cpd_cell_pred", dimension=1,
            normalizer=StandardDeviationNormalizer(
                mean=torch.tensor(dataset_statistics["mean"]["uvw_cpd_cell_w"], dtype=torch.float32),
                std=torch.tensor(dataset_statistics["std"]["uvw_cpd_cell_w"], dtype=torch.float32),
            )
        ),
        Key(
            name="p_cpd_cell_pred", dimension=1,
            normalizer=StandardDeviationNormalizer(
                mean=torch.tensor(dataset_statistics["mean"]["press_cpd_cell"], dtype=torch.float32),
                std=torch.tensor(dataset_statistics["std"]["press_cpd_cell"], dtype=torch.float32),
            )
        ),
        Key(
            name="T_cpd_cell_pred", dimension=1,
            normalizer=StandardDeviationNormalizer(
                mean=torch.tensor(dataset_statistics["mean"]["temp_cpd_cell"], dtype=torch.float32),
                std=torch.tensor(dataset_statistics["std"]["temp_cpd_cell"], dtype=torch.float32),
            )
        ),
    ]

    # 🔧 FIX: Create target_keys with normalizers for input normalization
    # This ensures inference uses the same input normalization as training
    # The normalizers here are used by Simulator.input_normalizer to normalize inputs
    target_keys = [
        Key(
            name="u_cpd_cell_true",
            dimension=1,
            normalizer=StandardDeviationNormalizer(
                mean=torch.tensor(dataset_statistics["mean"]["uvw_cpd_cell_u"], dtype=torch.float32),
                std=torch.tensor(dataset_statistics["std"]["uvw_cpd_cell_u"], dtype=torch.float32),
            )
        ),
        Key(
            name="v_cpd_cell_true",
            dimension=1,
            normalizer=StandardDeviationNormalizer(
                mean=torch.tensor(dataset_statistics["mean"]["uvw_cpd_cell_v"], dtype=torch.float32),
                std=torch.tensor(dataset_statistics["std"]["uvw_cpd_cell_v"], dtype=torch.float32),
            )
        ),
        Key(
            name="w_cpd_cell_true",
            dimension=1,
            normalizer=StandardDeviationNormalizer(
                mean=torch.tensor(dataset_statistics["mean"]["uvw_cpd_cell_w"], dtype=torch.float32),
                std=torch.tensor(dataset_statistics["std"]["uvw_cpd_cell_w"], dtype=torch.float32),
            )
        ),
        Key(
            name="p_cpd_cell_true",
            dimension=1,
            normalizer=StandardDeviationNormalizer(
                mean=torch.tensor(dataset_statistics["mean"]["press_cpd_cell"], dtype=torch.float32),
                std=torch.tensor(dataset_statistics["std"]["press_cpd_cell"], dtype=torch.float32),
            )
        ),
        Key(
            name="T_cpd_cell_true",
            dimension=1,
            normalizer=StandardDeviationNormalizer(
                mean=torch.tensor(dataset_statistics["mean"]["temp_cpd_cell"], dtype=torch.float32),
                std=torch.tensor(dataset_statistics["std"]["temp_cpd_cell"], dtype=torch.float32),
            )
        )
    ]

    # Phase 1: Prepare inference data
    logger.info("📊 Phase 1: Data Preparation")

    # Time the full Phase-1 data-preparation span (cold-start OpenFOAM t=1 generation
    # dominates; ~0 on warm/precomputed runs). Handed to the inferencer below as the
    # "feature-prep" component of the 3-part timing breakdown.
    import time as _time
    _t_feature_prep0 = _time.time()

    # ── helper imports ────────────────────────────────────────────────────────
    import re as _re
    import shutil as _shutil
    import subprocess as _subprocess
    import h5py as _h5py
    from pathlib import Path as _Path

    # ── case path resolution ──────────────────────────────────────────────────
    case_list = inference_config.get('case', [])
    dataset_path = inference_config.get('dataset_path')

    resolved_case_paths = []
    if case_list:
        for case_entry in case_list:
            if not os.path.isabs(case_entry):
                case_entry = os.path.join(project_root, case_entry)
            if os.path.exists(case_entry):
                resolved_case_paths.append(case_entry)
                logger.info(f"   📂 Case: {case_entry}")
            elif dataset_path:
                full_path = os.path.join(dataset_path, os.path.basename(case_entry))
                if os.path.exists(full_path):
                    resolved_case_paths.append(full_path)
                    logger.info(f"   📂 Case: {full_path}")
                else:
                    logger.warning(f"   ⚠️  Case not found: {case_entry}")
            else:
                logger.warning(f"   ⚠️  No dataset_path to resolve: {case_entry}")
    elif dataset_path:
        from Parse_mesh.parse_openfoam_refactored import find_valid_openfoam_cases
        resolved_case_paths = find_valid_openfoam_cases(dataset_path)
        logger.info(f"   📂 Scan-all: {len(resolved_case_paths)} cases found")
    else:
        raise ValueError("Either 'case' list or 'dataset_path' must be specified")

    if not resolved_case_paths:
        raise ValueError("No valid cases found for inference")

    logger.info(f"   📊 Total cases to check: {len(resolved_case_paths)}")

    # ── data-readiness helpers ────────────────────────────────────────────────

    def _check_bc_changed(case_path: str, h5_path=None) -> bool:
        """Return True iff 0/ dir contents hash differs from the hash stored in H5.

        Also True when no H5 is present or no baseline hash is stored, so the
        caller treats that as "needs rebuild". See Utils.deployment_helpers.
        """
        from Utils.deployment_helpers import bc_changed as _bc_changed_fn
        return _bc_changed_fn(case_path, h5_path)

    def _h5_has_t1_data(h5_path: str) -> bool:
        """Return True if H5 already has a t=1 field cached at index 0."""
        try:
            with _h5py.File(h5_path, 'r') as f:
                time_list = f.attrs.get('time_list', None)
                if time_list is None:
                    return False
                times = [float(t) for t in time_list]
                return len(times) >= 1 and abs(times[0] - 1.0) < 1e-6
        except Exception:
            return False

    def _run_solver_phase_b(case_path: str, h5_path=None) -> None:
        """
        Run buoyantBoussinesqSimpleFoam_new for exactly 1 relaxed step,
        then (if ``h5_path`` is given and the H5 exists) update/merge t=1 data
        into that H5. Pass ``h5_path=None`` to only run the solver — useful
        for the cold-deploy bootstrap path where the H5 has not been built
        yet and the caller will invoke
        ``Utils.deployment_helpers.ensure_h5_from_mesh`` after this returns.

        Mirrors Phase B from rerun_all_cases.py. Raises RuntimeError on
        failure; restores original config files on exit.
        """
        from Utils.foam_env import require_openfoam_bashrc
        OPENFOAM_SOURCE = require_openfoam_bashrc()  # conda v2412; never system OpenFOAM
        NEW_SOLVER = "buoyantBoussinesqSimpleFoam_new"

        cp = _Path(case_path)
        cd_path = cp / "system" / "controlDict"
        fv_path = cp / "system" / "fvSolution"

        # Template resolution with fallbacks so arbitrary deployment case dirs work.
        _script_dir = _Path(__file__).resolve().parent
        _template_candidates = [
            cp.parent.parent / "fvSolution_minimal.j2",           # legacy: adjacent to processed_dataset_*
            _script_dir / "fvSolution.jinja2",                    # repo: co-located with this script
            _Path("/mnt/lv/home/Public/GTC/fvSolution_minimal.j2"),  # public dataset root
        ]
        template_path = next((p for p in _template_candidates if p.is_file()), None)

        if template_path is None:
            raise FileNotFoundError(
                "fvSolution template not found. Looked at: "
                + ", ".join(str(p) for p in _template_candidates)
            )

        orig_cd = cd_path.read_text()
        orig_fv = fv_path.read_text()

        def _sub(pattern, repl, s):
            return _re.sub(pattern, repl, s, flags=_re.MULTILINE)

        def _patch_cd_for_1(content: str) -> str:
            content = _sub(r'^(\s*application\s+)[^;]+;',    r'\g<1>buoyantBoussinesqSimpleFoam_new;', content)
            content = _sub(r'^(\s*startFrom\s+)[^;]+;',       r'\g<1>startTime;',  content)
            content = _sub(r'^(\s*stopAt\s+)[^;]+;',          r'\g<1>endTime;',    content)
            content = _sub(r'^(\s*endTime\s+)[^;]+;',         r'\g<1>1;',          content)
            content = _sub(r'^(\s*writeInterval\s+)[^;]+;',   r'\g<1>1;',          content)
            content = _sub(r'^(\s*writeControl\s+)[^;]+;',    r'\g<1>timeStep;',   content)
            content = _re.sub(r'^(\s*libs\s+\([^)]*petscFoam[^)]*\);)', r'// \1',
                              content, flags=_re.MULTILINE)
            return content

        try:
            cd_path.write_text(_patch_cd_for_1(orig_cd))
            fv_path.write_text(template_path.read_text())

            # Remove existing 1/ directory (handle permission errors via rename)
            one_dir = cp / "1"
            if one_dir.exists():
                try:
                    _shutil.rmtree(one_dir)
                except PermissionError:
                    backup = cp / "_1_bak_infer"
                    if backup.exists():
                        _shutil.rmtree(backup, ignore_errors=True)
                    one_dir.rename(backup)

            # Run solver
            result = _subprocess.run(
                f"source {OPENFOAM_SOURCE} && {NEW_SOLVER}",
                shell=True, executable='/bin/bash', cwd=str(cp),
                capture_output=True, text=True, timeout=180
            )
            if result.returncode != 0:
                raise RuntimeError(f"Solver failed: {result.stderr[-1000:]}")
            if not one_dir.exists():
                raise RuntimeError("Solver ran but 1/ directory was not produced")

            # ── Preserve existing H5 time steps, then merge with new t=1 ──
            # (Skipped when h5_path is None: cold-deploy path uses
            # ensure_h5_from_mesh separately after this returns.)
            if h5_path and os.path.exists(h5_path):
                import numpy as _np
                _FIELD_KEYS = ['uvw_cpd_cell', 'press_cpd_cell', 'temp_cpd_cell',
                               'rho_cpd_cell', 'p_rgh_cpd_cell']

                # 1. Backup existing fields from H5 (e.g. t=500)
                existing_fields = {}
                existing_times = []
                with _h5py.File(h5_path, 'r') as _f:
                    existing_times = [float(t) for t in _f.attrs.get('time_list', [])]
                    for _k in _FIELD_KEYS:
                        if _k in _f:
                            existing_fields[_k] = _f[_k][()]

                # 2. Write t=1 data via direct pyvista reader (overwrites datasets)
                from Utils.of_field_updater import OpenFOAMFieldUpdater
                ok = OpenFOAMFieldUpdater.update_h5_field_data(
                    h5_path=h5_path,
                    case_path=str(cp),
                    time_value=[1.0],
                    use_direct_reader=True
                )
                if not ok:
                    raise RuntimeError("H5 update failed after Phase B solver run")

                # 3. Merge: prepend new t=1 before existing time steps
                #    Result: time_list=[1.0, 500.0, ...], fields shape (N+1, cells, dim)
                other_times = [t for t in existing_times if abs(t - 1.0) > 1e-6]
                if other_times and existing_fields:
                    with _h5py.File(h5_path, 'r+') as _f:
                        for _k, _old in existing_fields.items():
                            if _k in _f:
                                _new_t1 = _f[_k][()]          # (1, cells, dim)
                                # Keep only non-t=1 slices from old data
                                _old_mask = [i for i, t in enumerate(existing_times) if abs(t - 1.0) > 1e-6]
                                _old_kept = _old[_old_mask] if _old_mask else _old[:0]
                                _merged = _np.concatenate([_new_t1, _old_kept], axis=0)
                                del _f[_k]
                                _f.create_dataset(_k, data=_merged)
                        merged_times = _np.array([1.0] + other_times, dtype=_np.float32)
                        if 'time_list' in _f:
                            del _f['time_list']
                        _f.create_dataset('time_list', data=merged_times)
                        _f.attrs['time_list'] = merged_times
                    logger.info(f"   ✅ H5 merged: time_list={[1.0]+other_times}")

        finally:
            cd_path.write_text(orig_cd)
            fv_path.write_text(orig_fv)

    # ── per-case readiness loop ───────────────────────────────────────────────
    # Three deployment states are handled here:
    #   (a) cache-hit   : H5 exists, BC hash matches, t=1 present  →  skip OF
    #   (b) warm+change : H5 exists but BC hash stale (or t=1 missing)  →  run OF
    #                     + merge t=1 into existing H5
    #   (c) cold        : H5 absent  →  run OF, then build H5 from mesh via
    #                     Utils.deployment_helpers.ensure_h5_from_mesh
    # After any refresh, we write the fresh 0/-dir hash into H5 so the next
    # inference can short-circuit on cache-hit.
    from Utils.deployment_helpers import (
        ensure_h5_from_mesh as _ensure_h5_from_mesh,
        write_stored_bc_hash as _write_stored_bc_hash,
        compute_bc_hash as _compute_bc_hash,
    )
    n_ready = 0
    n_computed = 0
    n_bootstrap = 0
    for _cp in resolved_case_paths:
        _case_name = os.path.basename(_cp)

        # Locate (or plan) H5 file
        _h5_dir = os.path.join(_cp, "h5")
        _h5_files = sorted(_Path(_h5_dir).glob("*.h5")) if os.path.isdir(_h5_dir) else []
        _h5_path = str(_h5_files[0]) if _h5_files else os.path.join(_h5_dir, f"{_case_name}.h5")
        _h5_exists = os.path.exists(_h5_path)

        _bc_changed = _check_bc_changed(_cp, _h5_path if _h5_exists else None)

        if _h5_exists and not _bc_changed and _h5_has_t1_data(_h5_path):
            logger.info(f"   ✅ {_case_name}: H5 t=1 cache valid — skipping solver")
            n_ready += 1
            continue

        try:
            if _h5_exists:
                reason = "BC changed" if _bc_changed else "missing t=1 cache"
                logger.info(f"   🔄 {_case_name}: {reason} — running buoyantBoussinesqSimpleFoam_new for t=1")
                _run_solver_phase_b(_cp, _h5_path)
                n_computed += 1
                logger.info(f"   ✅ {_case_name}: t=1 computed and H5 updated")
            else:
                logger.info(f"   🔨 {_case_name}: no H5 — cold bootstrap (run OF + build H5 from mesh)")
                _run_solver_phase_b(_cp, None)
                _ensure_h5_from_mesh(_cp, _h5_path, case_name=_case_name)
                n_bootstrap += 1
                logger.info(f"   ✅ {_case_name}: H5 bootstrapped from mesh")
            # Stamp the new BC hash so the next inference can cache-hit.
            _write_stored_bc_hash(_h5_path, _compute_bc_hash(_cp))
        except Exception as _e:
            logger.error(f"   ❌ {_case_name}: data preparation failed: {_e}")
            raise RuntimeError(f"Data preparation failed for {_case_name}: {_e}")

    logger.info(
        f"   📊 Phase 1 complete: {n_ready} cached, {n_computed} recomputed, {n_bootstrap} cold-bootstrapped"
    )

    feature_prep_seconds = _time.time() - _t_feature_prep0
    inference_config['_feature_prep_seconds'] = feature_prep_seconds
    logger.info(f"   ⏱️  Feature-prep (Phase 1) time: {feature_prep_seconds*1000:.1f} ms")

    # Store resolved case names for inference worker.
    # When the original `case` list was empty (scan-all), do NOT pass resolved_case_paths
    # to the worker — that would make HomoGraphDataset bypass the meta.json split and infer
    # on ALL cases instead of the test split only.
    original_case_list = inference_config.get('case', [])
    if original_case_list:
        inference_config['_resolved_case_paths'] = resolved_case_paths
    else:
        inference_config['_resolved_case_paths'] = []  # let worker use case_names=None → meta.json split

    # Phase 2: Distributed Inference
    logger.info("🚀 Phase 2: Inference")

    # 检查是否由torchrun启动（多GPU模式）
    if 'RANK' in os.environ and 'WORLD_SIZE' in os.environ:
        # 由torchrun启动，直接运行worker
        local_rank = int(os.environ.get('LOCAL_RANK', 0))
        world_size = int(os.environ.get('WORLD_SIZE', 1))
        distributed_inference_worker(local_rank, config, input_keys, output_keys, target_keys, world_size)
    else:
        # 单GPU模式：直接运行
        distributed_inference_worker(0, config, input_keys, output_keys, target_keys, world_size=1)

    logger.info("✅ Completed")

if __name__ == "__main__":
    main()
