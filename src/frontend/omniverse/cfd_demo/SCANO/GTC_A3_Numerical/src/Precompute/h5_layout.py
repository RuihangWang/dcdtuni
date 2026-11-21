"""
src/Precompute/h5_layout.py
===========================
H5 layout detection, v1→v2 migration, and field read/write helpers.

Layout versions
---------------
v1 (legacy):  field data stored as flat root-level datasets with shape (2, N, C)
              where axis-0 index 0 = t=1 (init) field, index -1 = converged target.
              `layout_version` attribute absent.

v2 (current): field data stored in named groups:
              - field_converged_t1/   ← was index 0 of the (2,N,C) arrays
              - field_target/         ← was index -1 of the (2,N,C) arrays
              Each group contains datasets: uvw_cpd_cell, press_cpd_cell,
              p_rgh_cpd_cell, temp_cpd_cell  (each shape (N, C)).
              `layout_version` = 2 stored as H5 root attribute.
"""

from __future__ import annotations

import logging
from typing import Dict

import h5py
import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

FIELD_KEYS: list[str] = [
    "uvw_cpd_cell",
    "press_cpd_cell",
    "p_rgh_cpd_cell",
    "temp_cpd_cell",
]

LAYOUT_VERSION_ATTR: str = "layout_version"

# Group names used in v2
_GROUP_CONVERGED_T1 = "field_converged_t1"
_GROUP_TARGET       = "field_target"

# ---------------------------------------------------------------------------
# detect_layout_version
# ---------------------------------------------------------------------------


def detect_layout_version(h5_path: str) -> int:
    """Return the layout version stored in *h5_path*.

    Returns
    -------
    int
        1 if the file has no ``layout_version`` attribute (legacy), otherwise
        the integer value of that attribute.
    """
    with h5py.File(h5_path, "r") as f:
        return int(f.attrs.get(LAYOUT_VERSION_ATTR, 1))


# ---------------------------------------------------------------------------
# migrate_v1_to_v2
# ---------------------------------------------------------------------------


def migrate_v1_to_v2(h5_path: str, force: bool = False) -> bool:
    """Migrate an H5 file from layout v1 to v2 in-place.

    Parameters
    ----------
    h5_path:
        Path to the H5 file to migrate.
    force:
        If True, re-migrate even if the file is already v2.

    Returns
    -------
    bool
        True if migration was performed; False if skipped (already v2 and
        *force* is False).
    """
    current_version = detect_layout_version(h5_path)
    if current_version >= 2:
        if not force:
            logger.info("h5_layout: %s is already v%d — skipping migration", h5_path, current_version)
            return False
        # force=True on a v2 file: legacy root-level datasets no longer exist,
        # so there is nothing to migrate from. Return False rather than creating
        # empty groups from missing data.
        logger.info(
            "h5_layout: %s is already v%d and force=True — legacy data not available, "
            "nothing to re-migrate",
            h5_path,
            current_version,
        )
        return False

    logger.info("h5_layout: migrating %s v%d → v2 (force=%s)", h5_path, current_version, force)

    with h5py.File(h5_path, "r+") as f:
        # ------------------------------------------------------------------
        # 1. Read all legacy FIELD_KEYS (2, N, C) → split into two slabs
        # ------------------------------------------------------------------
        converged_t1_data: Dict[str, np.ndarray] = {}
        target_data:       Dict[str, np.ndarray] = {}

        for key in FIELD_KEYS:
            if key not in f:
                logger.warning("h5_layout: expected field '%s' not found in %s", key, h5_path)
                continue
            arr = f[key][:]  # shape (2, N, C)
            converged_t1_data[key] = arr[0]   # index 0 → t=1 init / converged
            target_data[key]       = arr[-1]  # index -1 → converged target

        # ------------------------------------------------------------------
        # 2. Handle rho_cpd_cell: keep only index 0 as root-level (N, 1)
        # ------------------------------------------------------------------
        if "rho_cpd_cell" in f:
            rho_arr = f["rho_cpd_cell"][:]
            del f["rho_cpd_cell"]
            if rho_arr.ndim == 3 and rho_arr.shape[0] >= 1:
                # Shape was (2, N, 1) or similar — keep index 0
                f.create_dataset("rho_cpd_cell", data=rho_arr[0], compression="gzip")
            else:
                # Already (N, C) — just re-store as-is
                f.create_dataset("rho_cpd_cell", data=rho_arr, compression="gzip")

        # ------------------------------------------------------------------
        # 3. Remove legacy flat field datasets
        # ------------------------------------------------------------------
        for key in FIELD_KEYS:
            if key in f:
                del f[key]
                logger.debug("h5_layout: removed legacy dataset '%s'", key)

        # ------------------------------------------------------------------
        # 4. Create named groups and write split data
        # ------------------------------------------------------------------
        for group_name, data_dict in [
            (_GROUP_CONVERGED_T1, converged_t1_data),
            (_GROUP_TARGET,       target_data),
        ]:
            if group_name in f:
                # force re-migration: delete existing group first
                del f[group_name]

            if not data_dict:
                logger.warning("h5_layout: no data for group '%s' — skipping", group_name)
                continue

            grp = f.require_group(group_name)
            for key, arr in data_dict.items():
                grp.create_dataset(key, data=arr, compression="gzip")
            logger.info("h5_layout: created group '%s' with %d datasets", group_name, len(data_dict))

        # ------------------------------------------------------------------
        # 5. Stamp version attribute
        # ------------------------------------------------------------------
        f.attrs[LAYOUT_VERSION_ATTR] = 2

    logger.info("h5_layout: migration complete for %s", h5_path)
    return True


# ---------------------------------------------------------------------------
# read_field_group
# ---------------------------------------------------------------------------


def read_field_group(h5_path: str, group_name: str) -> Dict[str, np.ndarray]:
    """Read all datasets from a named group and return as a dict of arrays.

    Parameters
    ----------
    h5_path:
        Path to the H5 file.
    group_name:
        Name of the group inside the file (e.g. ``"field_converged_t1"``).

    Returns
    -------
    dict[str, np.ndarray]

    Raises
    ------
    KeyError
        If *group_name* does not exist in the file.
    """
    with h5py.File(h5_path, "r") as f:
        if group_name not in f:
            raise KeyError(f"Group '{group_name}' not found in {h5_path}")
        grp = f[group_name]
        return {key: grp[key][:] for key in grp}


# ---------------------------------------------------------------------------
# write_field_group
# ---------------------------------------------------------------------------


def write_field_group(
    h5_path: str,
    group_name: str,
    fields: Dict[str, np.ndarray],
    force: bool = False,
) -> None:
    """Write *fields* dict into a named group inside an H5 file.

    Parameters
    ----------
    h5_path:
        Path to the H5 file (must already exist).
    group_name:
        Destination group name (created if absent).
    fields:
        Mapping of dataset name → numpy array.
    force:
        If True, delete and recreate the group when it already exists.
        If False (default), skip silently when the group already exists.
    """
    with h5py.File(h5_path, "r+") as f:
        if group_name in f:
            if not force:
                logger.debug("h5_layout: group '%s' already exists — skipping (force=False)", group_name)
                return
            del f[group_name]
            logger.debug("h5_layout: deleted existing group '%s' (force=True)", group_name)

        grp = f.require_group(group_name)
        for key, arr in fields.items():
            grp.create_dataset(key, data=arr, compression="gzip")
        logger.debug("h5_layout: wrote %d datasets to group '%s'", len(fields), group_name)


# ---------------------------------------------------------------------------
# write_embeddings_to_h5
# ---------------------------------------------------------------------------


def write_embeddings_to_h5(
    h5_path: str,
    dataset_name: str,
    embeddings: np.ndarray,
    force: bool = False,
) -> None:
    """Write an embedding array into the ``text_conditioning/`` group.

    Parameters
    ----------
    h5_path:
        Path to the H5 file (must already exist).
    dataset_name:
        Dataset name inside ``text_conditioning/`` (e.g. ``"patch_embeddings"``).
    embeddings:
        Array to store.
    force:
        Overwrite existing dataset if True.
    """
    with h5py.File(h5_path, "r+") as f:
        grp = f.require_group("text_conditioning")
        if dataset_name in grp:
            if not force:
                logger.debug(
                    "h5_layout: text_conditioning/%s already exists — skipping",
                    dataset_name,
                )
                return
            del grp[dataset_name]

        grp.create_dataset(dataset_name, data=embeddings, compression="gzip")
        logger.debug("h5_layout: wrote text_conditioning/%s shape=%s", dataset_name, embeddings.shape)


# ---------------------------------------------------------------------------
# write_patch_stats_to_h5
# ---------------------------------------------------------------------------


def write_patch_stats_to_h5(
    h5_path: str,
    mean: np.ndarray,
    std: np.ndarray,
    force: bool = False,
) -> None:
    """Write mean/std arrays into ``statistics/patch_statistics/`` group.

    Parameters
    ----------
    h5_path:
        Path to the H5 file (must already exist).
    mean, std:
        Statistics arrays to store.
    force:
        Overwrite existing data if True.
    """
    with h5py.File(h5_path, "r+") as f:
        grp = f.require_group("statistics/patch_statistics")

        for name, arr in [("mean", mean), ("std", std)]:
            if name in grp:
                if not force:
                    logger.debug(
                        "h5_layout: statistics/patch_statistics/%s exists — skipping",
                        name,
                    )
                    continue
                del grp[name]
            grp.create_dataset(name, data=arr, compression="gzip")

        logger.debug("h5_layout: wrote statistics/patch_statistics mean/std")
