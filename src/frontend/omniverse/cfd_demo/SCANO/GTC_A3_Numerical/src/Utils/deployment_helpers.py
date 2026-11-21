"""
deployment_helpers.py — inference Phase 1 utilities for true-deployment support.

Two responsibilities:

  1. BC-change detection (``bc_changed``). Hash every file under the OpenFOAM
     ``0/`` dict on each inference, compare against a hash stored as an H5 attr.
     If they differ (or no hash is stored yet), the BC has effectively changed
     and downstream stages (OF + H5 update) should re-run.

  2. H5 bootstrap (``ensure_h5_from_mesh``). When a case has no H5 yet, build
     one from the OpenFOAM case using ``OpenFOAMManager``. This is the step
     that turns a raw user-provided case folder (mesh + BC dicts only) into
     the graph-level H5 that the GNN dataset loader consumes.

These helpers are *timing-friendly*: every function returns or is easily
wrapped with monotonic() measurements so a deployment-latency bench can
attribute wall time to specific stages.

Nothing here touches the solver — ``_run_solver_phase_b`` in each case's
inference.py remains responsible for actually invoking OpenFOAM.
"""
from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from typing import Dict, Optional

import h5py


BC_HASH_ATTR = "bc_hash_0_dir"


# ---------------------------------------------------------------------------
# BC hash
# ---------------------------------------------------------------------------
def compute_bc_hash(case_path: str) -> str:
    """Return sha256 hex over all files in ``case_path/0/``.

    Produces a deterministic digest by walking files in sorted-path order and
    hashing ``<relpath>\0<content>\0``.

    Returns "" if ``0/`` does not exist or is empty.
    """
    bc_dir = Path(case_path) / "0"
    if not bc_dir.is_dir():
        return ""
    h = hashlib.sha256()
    found = False
    for fpath in sorted(bc_dir.rglob("*")):
        if fpath.is_file():
            h.update(str(fpath.relative_to(bc_dir)).encode())
            h.update(b"\0")
            h.update(fpath.read_bytes())
            h.update(b"\0")
            found = True
    return h.hexdigest() if found else ""


def read_stored_bc_hash(h5_path: Optional[str]) -> str:
    """Read H5 attr ``bc_hash_0_dir``. Returns "" if missing/unreadable."""
    if not h5_path or not os.path.exists(h5_path):
        return ""
    try:
        with h5py.File(h5_path, "r") as f:
            val = f.attrs.get(BC_HASH_ATTR, "")
            if isinstance(val, bytes):
                val = val.decode("ascii", errors="replace")
            return str(val) if val else ""
    except Exception:
        return ""


def write_stored_bc_hash(h5_path: str, bc_hash: str) -> None:
    """Write the hash into H5 as attr ``bc_hash_0_dir``."""
    with h5py.File(h5_path, "a") as f:
        f.attrs[BC_HASH_ATTR] = bc_hash


def bc_changed(case_path: str, h5_path: Optional[str]) -> bool:
    """Return True iff the current ``0/`` hash differs from the one stored in H5.

    Also returns True when no H5 exists, no hash is stored, or no ``0/`` dir
    is present — these are all cases where the cached t=1 field cannot be
    trusted.
    """
    current = compute_bc_hash(case_path)
    stored = read_stored_bc_hash(h5_path)
    if not current or not stored:
        return True
    return current != stored


# ---------------------------------------------------------------------------
# H5 bootstrap (cold deploy: case folder → H5)
# ---------------------------------------------------------------------------
def ensure_h5_from_mesh(
    case_path: str,
    h5_path: str,
    case_name: Optional[str] = None,
) -> Dict[str, float]:
    """Build an H5 file from an OpenFOAM case using ``OpenFOAMManager``.

    Assumes the OpenFOAM case already has a ``1/`` time directory — the
    mesh-to-graph pipeline reads that for field data alongside geometry.
    If ``1/`` is missing, the caller must run ``_run_solver_phase_b`` first.

    Args:
        case_path: path to the OpenFOAM case dir (has 0/, 1/, constant/, system/).
        h5_path:   absolute path where the new H5 will be written.
        case_name: H5 internal case name (defaults to ``basename(case_path)``).

    Returns a timing dict:
        {
          't_mesh_parse_s': time spent in OpenFOAMManager._process_mesh_pipeline,
          't_h5_export_s': time spent in OpenFOAMManager.export_to_hdf5,
          't_total_s':     total wall time including object construction,
        }
    """
    from Parse_mesh.parse_openfoam_refactored import OpenFOAMManager  # local import: heavy

    if not case_name:
        case_name = Path(case_path).name
    os.makedirs(os.path.dirname(h5_path), exist_ok=True)

    t_all0 = time.monotonic()
    t_m0 = time.monotonic()
    mesh = OpenFOAMManager(case_path=case_path, plt_edge_vis=False)
    mesh._process_mesh_pipeline()
    t_mesh = time.monotonic() - t_m0

    t_x0 = time.monotonic()
    mesh.export_to_hdf5(output_path=h5_path, case_name=case_name)
    t_export = time.monotonic() - t_x0

    return {
        "t_mesh_parse_s": t_mesh,
        "t_h5_export_s": t_export,
        "t_total_s": time.monotonic() - t_all0,
    }
