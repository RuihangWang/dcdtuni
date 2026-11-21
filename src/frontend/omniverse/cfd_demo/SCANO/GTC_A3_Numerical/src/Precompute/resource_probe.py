"""
System resource probe for adaptive multiprocessing worker count selection.

Detects CPU count, available RAM, and disk type (SSD vs HDD), then computes
an appropriate number of workers for parallel H5 preprocessing pipelines.
"""

import logging
import os
import subprocess

import psutil

logger = logging.getLogger(__name__)


def _check_ssd(path: str) -> bool:
    """Check whether the filesystem hosting *path* resides on an SSD.

    Algorithm:
      1. Use ``df --output=source <path>`` to find the backing block device.
      2. Strip partition numbers to get the raw device name (e.g. ``sda1`` -> ``sda``).
      3. Read ``/sys/block/<dev>/queue/rotational``:
         - ``"0"`` means non-rotational (SSD/NVMe) -> return True
         - ``"1"`` means rotational (HDD)           -> return False
      4. Return True (assume SSD) if any step fails.

    Parameters
    ----------
    path:
        Filesystem path to probe.  An empty string or non-existent path falls
        back to True (assume SSD).

    Returns
    -------
    bool
        True if the device is an SSD (or detection failed), False for HDD.
    """
    try:
        probe_path = path if path else "."
        result = subprocess.run(
            ["df", "--output=source", probe_path],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return True

        lines = result.stdout.strip().splitlines()
        if len(lines) < 2:
            return True

        source = lines[1].strip()  # e.g. /dev/sda1 or /dev/nvme0n1p2
        # Extract just the device basename
        dev = os.path.basename(source)

        # Strip trailing digits to get the block device (sda1 -> sda, nvme0n1p2 -> nvme0n1)
        # NVMe devices have the form nvme<ctrl>n<ns>p<part> — strip only the partition suffix
        import re

        # For nvme devices: strip trailing 'p<digits>'
        if re.match(r"nvme\d+n\d+p\d+", dev):
            dev = re.sub(r"p\d+$", "", dev)
        else:
            # For sd* / vd* / hd* devices: strip trailing digits
            dev = re.sub(r"\d+$", "", dev)

        rotational_path = f"/sys/block/{dev}/queue/rotational"
        if not os.path.exists(rotational_path):
            return True

        with open(rotational_path) as f:
            value = f.read().strip()

        # "0" = SSD/NVMe (non-rotational), "1" = HDD (rotational)
        return value == "0"

    except Exception:
        # Any failure -> assume SSD (optimistic default)
        return True


def probe_system_resources(dataset_path: str = "") -> dict:
    """Probe key system resources relevant to multiprocessing workloads.

    Parameters
    ----------
    dataset_path:
        Path to the dataset directory.  Used to determine the disk type of the
        storage device that hosts the dataset.  Falls back to the current
        working directory if empty.

    Returns
    -------
    dict with keys:
        - ``cpu_count`` (int): logical CPU count from ``os.cpu_count()``.
        - ``available_ram_gb`` (float): currently available RAM in GiB.
        - ``total_ram_gb`` (float): total installed RAM in GiB.
        - ``disk_is_ssd`` (bool): True if the dataset lives on an SSD.
    """
    mem = psutil.virtual_memory()
    cpu_count = os.cpu_count() or 1
    available_ram_gb = mem.available / (1024 ** 3)
    total_ram_gb = mem.total / (1024 ** 3)
    disk_is_ssd = _check_ssd(dataset_path)

    info = {
        "cpu_count": cpu_count,
        "available_ram_gb": available_ram_gb,
        "total_ram_gb": total_ram_gb,
        "disk_is_ssd": disk_is_ssd,
    }

    logger.debug(
        "Resource probe: cpu=%d, available_ram=%.1f GiB / %.1f GiB total, disk_is_ssd=%s",
        cpu_count,
        available_ram_gb,
        total_ram_gb,
        disk_is_ssd,
    )

    return info


def compute_worker_count(
    user_override: int | None = None,
    dataset_path: str = "",
    ram_per_worker_gb: float = 4.0,
    force_hdd: bool = False,
) -> int:
    """Compute an adaptive worker count for multiprocessing data loading.

    Decision logic:
      1. If *user_override* is set and > 0, return it directly without probing.
      2. Otherwise probe resources and compute:
         ``workers = min(cpu_count // 2, available_ram_gb // ram_per_worker_gb, 16)``
         clamped to >= 1.
      3. If the dataset disk is HDD (or *force_hdd* is True), cap workers at 4
         to avoid excessive random-access thrashing.

    Parameters
    ----------
    user_override:
        Explicit worker count requested by the user.  Skips all heuristics when
        provided and > 0.
    dataset_path:
        Path used for disk-type detection (passed to :func:`probe_system_resources`).
    ram_per_worker_gb:
        Estimated RAM each worker will need.  Default 4.0 GiB.
    force_hdd:
        If True, apply the HDD cap of 4 workers regardless of detected disk type.
        Useful in tests or when the user knows IO is constrained.

    Returns
    -------
    int
        Recommended worker count, always >= 1.
    """
    if user_override is not None and user_override > 0:
        logger.info("Worker count: using user override = %d", user_override)
        return user_override

    info = probe_system_resources(dataset_path=dataset_path)
    cpu_count = info["cpu_count"]
    available_ram_gb = info["available_ram_gb"]
    disk_is_ssd = info["disk_is_ssd"]

    cpu_based = cpu_count // 2
    ram_based = int(available_ram_gb // ram_per_worker_gb)
    raw = min(cpu_based, ram_based, 16)
    workers = max(raw, 1)

    is_hdd = force_hdd or not disk_is_ssd
    if is_hdd and workers > 4:
        logger.info(
            "Worker count: HDD detected (force_hdd=%s, disk_is_ssd=%s) — "
            "capping at 4 (heuristic gave %d). "
            "Reasoning: cpu_based=%d, ram_based=%d.",
            force_hdd,
            disk_is_ssd,
            workers,
            cpu_based,
            ram_based,
        )
        workers = 4
    else:
        logger.info(
            "Worker count: %d. Reasoning: cpu_based=%d, ram_based=%d (%.1f GiB / %.1f GiB each), "
            "disk_is_ssd=%s.",
            workers,
            cpu_based,
            ram_based,
            available_ram_gb,
            ram_per_worker_gb,
            disk_is_ssd,
        )

    return workers
