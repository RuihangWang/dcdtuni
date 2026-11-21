#!/usr/bin/env python3
"""Shared CLI logic for H5 precompute entry scripts.

Both ``cases/GTC/precompute_features.py`` and
``cases/Slab/precompute_features.py`` are thin wrappers that call
``main(script_dir)`` from this module.

Stages
------
1  --migrate-layout         : v1 -> v2 H5 layout migration
2a --converged-t1           : generate converged t=1 field (original fvSolution)
2b --unconverged-t1         : generate unconverged t=1 field (minimal iteration)
3  --text-embeddings         : precompute MiniLM text embeddings (pretrained)
4  --random-init-embeddings  : precompute random-init embeddings (ablation)
5  --patch-stats             : compute and inject per-channel patch statistics
   --all                    : run all stages in order (1 -> 5)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("precompute_features")

# ---------------------------------------------------------------------------
# Imports from src/Precompute  (deferred so --help works without heavy deps)
# ---------------------------------------------------------------------------


def _import_precompute():
    from src.Precompute.resource_probe import compute_worker_count
    from src.Precompute.h5_layout import migrate_v1_to_v2
    from src.Precompute.stage_embeddings import precompute_embeddings
    from src.Precompute.stage_unconverged import (
        generate_unconverged_t1_single,
        generate_converged_t1_single,
    )
    from src.Precompute.stage_patch_stats import compute_patch_stats, inject_patch_stats

    return (
        compute_worker_count,
        migrate_v1_to_v2,
        precompute_embeddings,
        generate_unconverged_t1_single,
        generate_converged_t1_single,
        compute_patch_stats,
        inject_patch_stats,
    )


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


def _load_config(script_dir: Path) -> dict:
    config_path = script_dir / "precompute_config.yaml"
    if not config_path.is_file():
        raise FileNotFoundError(f"Config not found: {config_path}")
    with open(config_path) as fh:
        cfg = yaml.safe_load(fh)
    logger.info("Loaded config from %s", config_path)
    return cfg


# ---------------------------------------------------------------------------
# H5 discovery and train/test split
# ---------------------------------------------------------------------------


def _discover_and_split(dataset_path: str, train_ratio: float):
    root = Path(dataset_path)
    if not root.is_dir():
        raise NotADirectoryError(f"dataset_path does not exist: {dataset_path}")

    all_h5 = sorted(str(p) for p in root.rglob("*.h5"))
    if not all_h5:
        raise RuntimeError(f"No .h5 files found under {dataset_path}")

    n_train = max(1, int(len(all_h5) * train_ratio))
    train_paths = all_h5[:n_train]
    test_paths = all_h5[n_train:]

    logger.info(
        "Discovered %d H5 files — train=%d, test=%d (ratio=%.2f)",
        len(all_h5),
        len(train_paths),
        len(test_paths),
        train_ratio,
    )
    return all_h5, train_paths, test_paths


# ---------------------------------------------------------------------------
# Stage helpers
# ---------------------------------------------------------------------------


def _stage_migrate_layout(all_h5, force, num_workers, migrate_v1_to_v2):
    """Stage 1: Migrate H5 files from v1 to v2 layout using multiprocessing."""
    import multiprocessing
    import functools

    logger.info("Stage 1 — migrate-layout: %d files, workers=%d", len(all_h5), num_workers)
    t0 = time.perf_counter()

    worker_fn = functools.partial(migrate_v1_to_v2, force=force)

    with multiprocessing.Pool(num_workers) as pool:
        results = pool.map(worker_fn, all_h5)

    migrated = sum(bool(r) for r in results)
    skipped = len(results) - migrated
    elapsed = time.perf_counter() - t0
    logger.info(
        "Stage 1 done: migrated=%d, skipped=%d in %.1f s", migrated, skipped, elapsed
    )


def _safe_generate_t1(generate_fn, h5_path, dataset_root, force):
    """Wrapper that catches per-case errors (e.g. mesh mismatch) and logs them."""
    try:
        return generate_fn(h5_path=h5_path, dataset_root=dataset_root, force=force)
    except Exception:
        logger.error("Failed to process %s:", h5_path, exc_info=True)
        return False


def _stage_converged_t1(all_h5, dataset_path, force, num_workers, generate_converged_t1_single):
    """Stage 2a: Generate converged t=1 field data (original fvSolution).

    Runs the OF solver for 1 time step with the original fvSolution settings
    (tight tolerances, high maxIter) so inner iterations properly solve each
    equation.  Only controlDict is patched.
    """
    import multiprocessing
    import functools

    logger.info(
        "Stage 2a — converged-t1: %d files (all), workers=%d", len(all_h5), num_workers
    )
    t0 = time.perf_counter()

    worker_fn = functools.partial(
        _safe_generate_t1, generate_converged_t1_single,
        dataset_root=dataset_path, force=force,
    )

    with multiprocessing.Pool(num_workers) as pool:
        results = pool.map(worker_fn, all_h5)

    processed = sum(bool(r) for r in results)
    failed = sum(1 for r in results if r is False)
    elapsed = time.perf_counter() - t0
    logger.info("Stage 2a done: processed=%d, failed=%d in %.1f s", processed, failed, elapsed)


def _stage_unconverged_t1(all_h5, dataset_path, force, num_workers, generate_unconverged_t1_single):
    """Stage 2b: Generate unconverged t=1 field data (minimal iteration).

    Uses all_h5 (not train_h5) so that A2/A3 inference on test-set cases also
    gets unconverged data.
    """
    import multiprocessing
    import functools

    logger.info(
        "Stage 2b — unconverged-t1: %d files (all), workers=%d", len(all_h5), num_workers
    )
    t0 = time.perf_counter()

    worker_fn = functools.partial(
        _safe_generate_t1, generate_unconverged_t1_single,
        dataset_root=dataset_path, force=force,
    )

    with multiprocessing.Pool(num_workers) as pool:
        results = pool.map(worker_fn, all_h5)

    processed = sum(bool(r) for r in results)
    failed = sum(1 for r in results if r is False)
    elapsed = time.perf_counter() - t0
    logger.info("Stage 2b done: processed=%d, failed=%d in %.1f s", processed, failed, elapsed)


def _stage_text_embeddings(all_h5, encoder_name, device, force, precompute_embeddings):
    """Stage 3: Precompute pretrained MiniLM text embeddings (GPU, single-process)."""
    logger.info(
        "Stage 3 — text-embeddings: %d files, encoder=%s, device=%s",
        len(all_h5),
        encoder_name,
        device,
    )
    t0 = time.perf_counter()

    stats = precompute_embeddings(
        h5_paths=all_h5,
        encoder_name=encoder_name,
        random_init=False,
        output_dataset_name="pretrained_patch_embeddings",
        device=device,
        force=force,
    )

    elapsed = time.perf_counter() - t0
    logger.info(
        "Stage 3 done: processed=%d, skipped=%d in %.1f s",
        stats["processed"],
        stats["skipped"],
        elapsed,
    )


def _stage_random_init_embeddings(all_h5, encoder_name, random_seed, device, force, precompute_embeddings):
    """Stage 4: Precompute random-init embeddings (GPU, single-process, ablation)."""
    logger.info(
        "Stage 4 — random-init-embeddings: %d files, device=%s, seed=%d",
        len(all_h5),
        device,
        random_seed,
    )
    t0 = time.perf_counter()

    stats = precompute_embeddings(
        h5_paths=all_h5,
        encoder_name=encoder_name,
        random_init=True,
        random_seed=random_seed,
        output_dataset_name="random_init_patch_embeddings",
        device=device,
        force=force,
    )

    elapsed = time.perf_counter() - t0
    logger.info(
        "Stage 4 done: processed=%d, skipped=%d in %.1f s",
        stats["processed"],
        stats["skipped"],
        elapsed,
    )


def _stage_patch_stats(
    all_h5,
    train_h5,
    dataset_path,
    force,
    num_workers,
    compute_patch_stats,
    inject_patch_stats,
):
    """Stage 5: Compute patch statistics and inject into all H5 files."""
    logger.info(
        "Stage 5 — patch-stats: computing from %d train files", len(train_h5)
    )
    t0 = time.perf_counter()

    stats = compute_patch_stats(h5_paths_train=train_h5, force=force)

    # Inject into every H5 (train + test)
    logger.info("Stage 5 — injecting stats into %d files", len(all_h5))
    inject_patch_stats(h5_paths_all=all_h5, stats=stats, force=force)

    # Save JSON sidecar for backward compatibility
    json_path = Path(dataset_path) / "patch_statistics.json"
    with open(json_path, "w") as fh:
        json.dump(
            {"mean": stats["mean"].tolist(), "std": stats["std"].tolist()},
            fh,
            indent=2,
        )
    logger.info("Stage 5 — saved patch_statistics.json to %s", json_path)

    elapsed = time.perf_counter() - t0
    logger.info("Stage 5 done in %.1f s", elapsed)


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Precompute H5 features for a Gen-FVGN dataset.\n"
            "Config is read from precompute_config.yaml in the calling script's directory.\n\n"
            "Stages:\n"
            "  1  --migrate-layout\n"
            "  2  --unconverged-t1\n"
            "  3  --text-embeddings\n"
            "  4  --random-init-embeddings\n"
            "  5  --patch-stats\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--migrate-layout",
        action="store_true",
        help="Stage 1: migrate H5 layout from v1 to v2",
    )
    parser.add_argument(
        "--converged-t1",
        action="store_true",
        help="Stage 2a: generate converged t=1 field (original fvSolution, all cases)",
    )
    parser.add_argument(
        "--unconverged-t1",
        action="store_true",
        help="Stage 2b: generate unconverged t=1 field (minimal iteration, all cases)",
    )
    parser.add_argument(
        "--text-embeddings",
        action="store_true",
        help="Stage 3: precompute pretrained MiniLM text embeddings",
    )
    parser.add_argument(
        "--random-init-embeddings",
        action="store_true",
        help="Stage 4: precompute random-init embeddings (ablation)",
    )
    parser.add_argument(
        "--patch-stats",
        action="store_true",
        help="Stage 5: compute and inject per-channel patch statistics",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run all stages in order (1 -> 5)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing data instead of skipping",
    )
    parser.add_argument(
        "--num-workers",
        type=int,
        default=None,
        metavar="N",
        help="Override adaptive worker count for CPU-bound stages",
    )
    parser.add_argument(
        "--gpu",
        type=int,
        default=0,
        metavar="N",
        help="GPU device index for GPU stages (default: 0)",
    )
    return parser


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def main(script_dir: str | Path):
    """Run the precompute CLI.

    Parameters
    ----------
    script_dir:
        Directory that contains ``precompute_config.yaml`` (i.e. the directory
        of the calling entry script, e.g. ``cases/GTC`` or ``cases/Slab``).
    """
    script_dir = Path(script_dir).resolve()

    parser = _build_parser()
    args = parser.parse_args()

    # Expand --all into individual flags
    if args.all:
        args.migrate_layout = True
        args.converged_t1 = True
        args.unconverged_t1 = True
        args.text_embeddings = True
        args.random_init_embeddings = True
        args.patch_stats = True

    run_any = any([
        args.migrate_layout,
        args.converged_t1,
        args.unconverged_t1,
        args.text_embeddings,
        args.random_init_embeddings,
        args.patch_stats,
    ])

    if not run_any:
        parser.print_help()
        sys.exit(0)

    # Load config from the calling script's directory
    cfg = _load_config(script_dir)
    dataset_path = cfg["dataset_path"]
    train_ratio = float(cfg.get("train_ratio", 0.90))
    encoder_name = cfg.get("text_encoder_name", "sentence-transformers/all-MiniLM-L6-v2")
    random_seed = int(cfg.get("random_init_seed", 42))

    # Import heavy modules only when actually running
    (
        compute_worker_count,
        migrate_v1_to_v2,
        precompute_embeddings,
        generate_unconverged_t1_single,
        generate_converged_t1_single,
        compute_patch_stats,
        inject_patch_stats,
    ) = _import_precompute()

    # Adaptive worker count for CPU-bound stages
    num_workers = compute_worker_count(
        user_override=args.num_workers,
        dataset_path=dataset_path,
    )
    logger.info("Using %d worker(s) for CPU-bound stages", num_workers)

    # GPU device string
    device = f"cuda:{args.gpu}"
    logger.info("Using device=%s for GPU stages", device)

    # Discover H5 files and split
    all_h5, train_h5, test_h5 = _discover_and_split(dataset_path, train_ratio)

    # ------------------------------------------------------------------
    # Stage dispatch
    # ------------------------------------------------------------------

    if args.migrate_layout:
        _stage_migrate_layout(all_h5, args.force, num_workers, migrate_v1_to_v2)

    if args.converged_t1:
        _stage_converged_t1(
            all_h5, dataset_path, args.force, num_workers, generate_converged_t1_single
        )

    if args.unconverged_t1:
        _stage_unconverged_t1(
            all_h5, dataset_path, args.force, num_workers, generate_unconverged_t1_single
        )

    if args.text_embeddings:
        _stage_text_embeddings(all_h5, encoder_name, device, args.force, precompute_embeddings)

    if args.random_init_embeddings:
        _stage_random_init_embeddings(
            all_h5, encoder_name, random_seed, device, args.force, precompute_embeddings
        )

    if args.patch_stats:
        _stage_patch_stats(
            all_h5,
            train_h5,
            dataset_path,
            args.force,
            num_workers,
            compute_patch_stats,
            inject_patch_stats,
        )

    logger.info("All requested stages complete.")
