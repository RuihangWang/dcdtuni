"""
src/Precompute/stage_patch_stats.py
====================================
Compute per-channel mean/std of boundary patch field values across training-set
cases and inject the result into every case's H5 file.

Algorithm
---------
1. For each training case H5:
   - Load field data from ``field_unconverged_t1/`` group (v2 layout) OR from
     root-level ``uvw_cpd_cell`` shape ``(2, N, 3)`` (legacy layout, use index 0).
     Concatenate to form ``init_phi (N, 5)`` = [u, v, w, p, T].
   - Load ``text_conditioning/patch_name_to_patch_index_json`` — JSON mapping
     patch_name → {"start": int, "end": int}  OR  [start, end]
   - Load ``text_conditioning/patch_order`` — ordered array of patch names.
   - For each patch in patch_order: slice ``init_phi[start:end]``,
     compute ``mean(axis=0)`` → 5D vector.

2. Stack all per-patch means → ``(total_patches, 5)``.
   Compute global ``mean(axis=0)`` and ``std(axis=0)``.
   Clamp std: where std < 1e-8, set to 1.0.

3. Inject resulting mean/std into EVERY case's H5 (both train and test)
   using ``write_patch_stats_to_h5`` from h5_layout.
"""

from __future__ import annotations

import json
import logging
from typing import List

import h5py
import numpy as np

from src.Precompute.h5_layout import write_patch_stats_to_h5

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_patch_stats(
    h5_paths_train: List[str],
    field_group: str = "field_unconverged_t1",
    force: bool = False,
) -> dict:
    """Compute per-channel mean/std of boundary patch field values.

    Iterates over *h5_paths_train*, computes per-patch mean [u,v,w,p,T] from
    the t=1 field, then aggregates across all patches and cases.

    Parameters
    ----------
    h5_paths_train:
        List of H5 file paths for the training split.
    field_group:
        Group name for v2-layout field data (default ``"field_unconverged_t1"``).
    force:
        Unused here (reserved for future caching). Present for API symmetry.

    Returns
    -------
    dict with keys:
        ``"mean"`` — np.ndarray of shape (5,)
        ``"std"``  — np.ndarray of shape (5,), clamped so values < 1e-8 → 1.0
    """
    all_patch_means: list[np.ndarray] = []
    n_cases_processed = 0

    for h5_path in h5_paths_train:
        try:
            init_phi = _load_init_phi(h5_path, field_group)
        except Exception as exc:
            logger.warning("stage_patch_stats: cannot load field from %s — %s", h5_path, exc)
            continue

        try:
            patch_index, patch_order = _load_patch_meta(h5_path)
        except KeyError:
            logger.warning(
                "stage_patch_stats: text_conditioning group missing in %s — skipping",
                h5_path,
            )
            continue

        case_patch_count = 0
        for patch_name in patch_order:
            entry = patch_index.get(patch_name)
            if entry is None:
                continue
            start, end = _parse_patch_entry(entry)
            if end <= start:
                continue
            patch_data = init_phi[start:end]  # (k, 5)
            patch_mean = patch_data.mean(axis=0)  # (5,)
            all_patch_means.append(patch_mean)
            case_patch_count += 1

        n_cases_processed += 1

    if not all_patch_means:
        raise RuntimeError(
            "stage_patch_stats: no patch data collected from any training case"
        )

    stacked = np.stack(all_patch_means, axis=0)  # (total_patches, 5)
    mean = stacked.mean(axis=0)
    std = stacked.std(axis=0)
    std = np.where(std < 1e-8, 1.0, std)

    logger.info(
        "stage_patch_stats: processed %d training cases, aggregated %d patch means",
        n_cases_processed,
        len(all_patch_means),
    )

    return {"mean": mean, "std": std}


def inject_patch_stats(
    h5_paths_all: List[str],
    stats: dict,
    force: bool = False,
) -> None:
    """Write patch_statistics into every case's H5.

    Parameters
    ----------
    h5_paths_all:
        All H5 file paths (train + test) to receive the statistics.
    stats:
        Dict with ``"mean"`` and ``"std"`` np.ndarray of shape (5,).
    force:
        If True, overwrite existing statistics. If False (default), skip
        files that already have statistics written.
    """
    mean = np.asarray(stats["mean"], dtype=np.float32)
    std = np.asarray(stats["std"], dtype=np.float32)

    for h5_path in h5_paths_all:
        try:
            write_patch_stats_to_h5(h5_path, mean, std, force=force)
            logger.debug("stage_patch_stats: injected stats into %s", h5_path)
        except Exception as exc:
            logger.warning(
                "stage_patch_stats: failed to inject stats into %s — %s", h5_path, exc
            )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _load_init_phi(h5_path: str, field_group: str) -> np.ndarray:
    """Load and concatenate [u, v, w, p, T] → shape (N, 5).

    Handles:
    - v2 layout: field in ``field_group/uvw_cpd_cell`` (N, 3),
      ``field_group/press_cpd_cell`` (N, 1),  ``field_group/temp_cpd_cell`` (N, 1).
    - Legacy layout: ``uvw_cpd_cell`` (2, N, 3) at root — uses index 0.
    """
    with h5py.File(h5_path, "r") as f:
        if field_group in f:
            grp = f[field_group]
            uvw = grp["uvw_cpd_cell"][:]          # (N, 3)
            press = grp["press_cpd_cell"][:]       # (N, 1)
            temp = grp["temp_cpd_cell"][:]         # (N, 1)
        elif "field_converged_t1" in f:
            grp = f["field_converged_t1"]
            uvw = grp["uvw_cpd_cell"][:]
            press = grp["press_cpd_cell"][:]
            temp = grp["temp_cpd_cell"][:]
        else:
            # Legacy: root-level datasets, shape (2, N, C) — use index 0
            uvw_raw = f["uvw_cpd_cell"][:]         # (2, N, 3)
            press_raw = f["press_cpd_cell"][:]     # (2, N, 1)
            temp_raw = f["temp_cpd_cell"][:]       # (2, N, 1)
            uvw = uvw_raw[0]                       # (N, 3)
            press = press_raw[0]                   # (N, 1)
            temp = temp_raw[0]                     # (N, 1)

    init_phi = np.concatenate([uvw, press, temp], axis=1).astype(np.float64)  # (N, 5)
    return init_phi


def _load_patch_meta(h5_path: str):
    """Load patch_list (with start/end cpd_cell indices) and patch_order.

    Uses the ``patch_list`` root attribute (written at H5 build time) which
    maps each patch name to
    ``{"start_idx_cpd_cell": int, "end_idx_cpd_cell": int, ...}``.

    Falls back to ``text_conditioning/patch_name_to_patch_index_json`` if
    ``patch_list`` is not available (handles both ``{"start":, "end":}``
    and ``[start, end]`` formats).

    Returns
    -------
    tuple[dict, list]
        (patch_list_dict, patch_order)
    """
    with h5py.File(h5_path, "r") as f:
        # Primary source: root attribute patch_list
        if "patch_list" in f.attrs:
            raw = f.attrs["patch_list"]
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            patch_list = json.loads(raw)
        else:
            patch_list = None

        # Load patch_order from text_conditioning
        if "text_conditioning" not in f:
            raise KeyError("text_conditioning group missing")
        tc = f["text_conditioning"]
        patch_order_raw = tc["patch_order"][:]
        patch_order = [
            p.decode("utf-8") if isinstance(p, bytes) else str(p)
            for p in patch_order_raw
        ]

        # Fallback: if no patch_list attr, try text_conditioning JSON
        if patch_list is None:
            raw_json = tc["patch_name_to_patch_index_json"][()]
            if isinstance(raw_json, bytes):
                raw_json = raw_json.decode("utf-8")
            patch_list = json.loads(raw_json)

    return patch_list, patch_order


def _parse_patch_entry(entry) -> tuple[int, int]:
    """Parse a patch index entry into (start, end) cpd_cell indices.

    Accepted formats:
    - ``{"start_idx_cpd_cell": int, "end_idx_cpd_cell": int}`` (from patch_list attr)
    - ``{"start": int, "end": int}``
    - ``[start, end]``
    """
    if isinstance(entry, dict):
        if "start_idx_cpd_cell" in entry:
            return int(entry["start_idx_cpd_cell"]), int(entry["end_idx_cpd_cell"])
        return int(entry["start"]), int(entry["end"])
    elif isinstance(entry, (list, tuple)) and len(entry) == 2:
        return int(entry[0]), int(entry[1])
    else:
        raise ValueError(f"Unrecognised patch entry format: {entry!r}")
