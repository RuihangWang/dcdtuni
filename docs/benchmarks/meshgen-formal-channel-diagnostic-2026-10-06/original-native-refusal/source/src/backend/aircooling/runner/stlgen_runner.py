"""
STLGenRunner — Direct STL pipeline for frontend.

This runner uses browser-exported STL files directly instead of re-generating
geometry from JSON parametric descriptions.  dcdtuni's internal STL generator is
completely bypassed.

Workflow
--------
1.  Read the .cfdrun manifest (model/<basename>.cfdrun — JSON).
2.  Find STL files in     Mesh/constant/triSurface/
3.  Parse solid names from ASCII STL headers
4.  Generate OpenFOAM mesh dictionaries:
      blockMeshDict, surfaceFeatureExtractDict, snappyHexMeshDict
    Leakage solids → faceType internal + cellZone (not walls)
5.  Run serial mesh pipeline:
      blockMesh → surfaceFeatureExtract → snappyHexMesh -overwrite
6.  Build in-memory BridgeBCRegistry from .cfdrun physics data
7.  Prepare a runnable OpenFOAM case directly from STLGen data
8.  Run solver directly from the STLGen-derived case
"""

import datetime
from contextlib import contextmanager
import functools
import glob as _glob
import hashlib
import copy
import json
import math
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, NamedTuple, Optional, Set, Tuple

if TYPE_CHECKING:
    from backend.aircooling.runner.bridge_bc import BridgeBCRegistry

from loguru import logger

from backend.aircooling.runner import cfdrun_reader
from backend.aircooling.runner.cfdrun_snapshot import CfdrunSnapshot, HybridCfdrunSnapshot
from backend.aircooling.runner.transient_time import time_events_from_registry
from backend.aircooling.runner.cfdrun_schema import CFDExport
from backend.aircooling.runner.fields import (
    FieldGenerator, STANDARD_FIELD_NAMES, ensure_table_write_precision, start_simulation_config,
)
# Editor-only export buckets (merged_server_interface_walls.stl — server
# left/right interface faces against rack-frame cavity walls, no fluid there)
# are never mesh inputs; the predicate lives in mesh_acceptance so the runner
# and the acceptance readers share ONE definition of the exclusion.
from backend.aircooling.runner.mesh_acceptance import is_editor_only_stl
from backend.aircooling.runner import floor_consistency
from backend.aircooling.runner.mpi_runtime import mesh_mpi_environment, prepare_hwloc_plugins
from backend.aircooling.utils.const import rho_air, air_specific_heat
from backend.aircooling.runner.openfoam_case import (
    copy_system_files_for_solver,
    fix_cyclic_match_tolerance,
    generate_control_dict,
    generate_decompose_par_dict,
    generate_fv_options,
    generate_fv_schemes,
    generate_fv_solution,
    read_boundary_patches,
)


def requires_hybrid_air_gate(raw: Dict[str, Any]) -> bool:
    """Fail closed for liquid-server heat flux even if a runId was edited."""
    if str(raw.get("runId", "")).startswith("hybrid_"):
        return True
    liquid = (raw.get("inputs") or {}).get("liquid") or {}
    if liquid.get("enabled") is not True:
        return False
    for binding in (raw.get("boundaryBindings") or {}).values():
        if (binding.get("bcType") == "nonfan_heat_flux" and
                re.fullmatch(r"constructions\.racks\.[^.]+\.servers\.[^.]+",
                             str(binding.get("objectPath", "")))):
            return True
    return False


def _case_locked(method):
    """Hold the same per-case lock as browser uploads for the entire operation.

    Unit fixtures that intentionally construct an incomplete runner with
    ``__new__`` have no lock flag; every normally constructed runner has it.
    """
    @functools.wraps(method)
    def run(self, *args, **kwargs):
        if not getattr(self, "_case_lock_enabled", False):
            return method(self, *args, **kwargs)
        from backend.aircooling.runner.case_execution_lock import case_execution_lock

        with case_execution_lock(self.case_dir, blocking=False):
            payload = self._source_cfdrun_bytes()
            actual = hashlib.sha256(payload).hexdigest()
            expected = self._expected_cfdrun_sha256
            if expected is not None and actual != expected:
                raise RuntimeError(
                    ".cfdrun input hash changed after upload; prepare the case again "
                    f"(expected {expected}, found {actual})"
                )
            snapshot = getattr(self, "_input_snapshot", None)
            if snapshot is not None:
                expected_batch = snapshot.stl_batch_sha256
                if expected_batch is None:
                    raise RuntimeError(
                        "RAM .cfdrun snapshot is missing physical STL batch identity; "
                        "prepare the case again"
                    )
                from backend.aircooling.runner.cfdrun_stl_identity import surface_batch_sha256
                current_batch = surface_batch_sha256(self.case_dir)
                if current_batch != expected_batch:
                    raise RuntimeError(
                        "physical STL batch changed after RAM input publication; "
                        f"prepare the case again (expected {expected_batch}, found {current_batch})"
                    )
            self._execution_cfdrun_sha256 = actual
            try:
                return method(self, *args, **kwargs)
            finally:
                self._execution_cfdrun_sha256 = None

    return run


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sanitize(name: str) -> str:
    """Same sanitization as HTML tool and bridge_bc.py."""
    return re.sub(r"[^A-Za-z0-9_]", "_", name)


def _compute_rack_min_u_span(rack_data: Dict[str, Any]) -> Optional[int]:
    """Read the frontend-computed `minUSpanForDoorBucket` field.

    The frontend's `_computeRackMinUSpan(rackModel)` in STL_generate.html
    is the single source of truth for the span rule (it walks
    equipments + merged-empty-slot ranges + through-slot groups using
    the front-end state shape). At export time the computed integer
    is written to each rack as `rack.minUSpanForDoorBucket`; this
    helper just reads it.

    Returns None when the field is absent or null — legacy cfdrun
    files and racks with no categorizable entries both land here,
    and `_door_level_for_rack` falls back to the unified door level.

    See docs/superpowers/specs/2026-04-17-rack-door-refinement-design.md
    §Algorithm §Resolve and §Risks (mirror-drift mitigation).
    """
    value = rack_data.get("minUSpanForDoorBucket")
    if value is None:
        return None
    return int(value)


def _door_level_for_rack(rack_data: Dict[str, Any], mesh_config: Dict[str, Any]) -> List[int]:
    """Unified door refinement level — no per-bucket variation.
    All doors share one level since they merge into a single STL file."""
    return list(mesh_config.get("rackDoorRefinementLevel") or [1, 1])


def _door_feature_level_for_rack(rack_data: Dict[str, Any],
                                 mesh_config: Dict[str, Any]) -> int:
    """Unified door feature-line level. Falls back to surface max."""
    explicit = mesh_config.get("rackDoorFeatureLevel")
    if explicit is not None:
        return int(explicit)
    rl = mesh_config.get("rackDoorRefinementLevel") or [1, 1]
    return int(rl[-1]) if rl else 1


_BLANKING_PANEL_SIGMA = 0.6401

# ---- Bucket file prefix → category mapping (for STL classification) ----

_BUCKET_PREFIXES: Dict[str, str] = {
    "merged_server_": "server",
    "merged_blanking_": "blanking",
    "merged_partition_": "partition",
    "merged_through_zone_": "through_zone",
    "merged_wallBaffle_": "wallBaffle",
    "merged_perforatedBlankingPanel_": "perforatedBlankingPanel",
    "merged_wallBlankingPanel_": "wallBlankingPanel",
}


def _parse_bucket_from_filename(filename: str) -> Optional[Tuple[str, str]]:
    """For per-bucket STL files, return (category, bucket_key). None for scene-merged.

    Recognises two formats:
      - ``{prefix}<N>U.stl``              — original multi-solid bucket file
      - ``{prefix}<N>U__<zone>.stl``      — per-zone file (through-zone only,
        2026-04-18+). One closed-surface per file so snappy's
        ``refinementRegions mode=inside`` closure test accepts the shell.
    """
    for prefix, cat in _BUCKET_PREFIXES.items():
        if not filename.startswith(prefix):
            continue
        m = re.match(rf"{re.escape(prefix)}(\d+)U__[A-Za-z0-9_]+\.stl$", filename)
        if m:
            return (cat, m.group(1))
        m = re.match(rf"{re.escape(prefix)}(\d+)U\.stl$", filename)
        if m:
            return (cat, m.group(1))
        m = re.match(rf"{re.escape(prefix)}[A-Za-z]+_(\d+)U\.stl$", filename)
        if m:
            return (cat, m.group(1))
    return None


_MESH_MODES = ("bodyFitted", "cartesianLocalized", "structured")


#: Sentinel for "no equipment seen yet" when reducing rack envelopes.
GREAT_Y = 1.0e30

#: Product ceiling for the whole mesh pipeline, including checkMesh and patch validation.
#: A project can select a shorter budget; legacy disabled/unbounded values use this ceiling.
DEFAULT_MESH_TIME_LIMIT_S = 300.0


def _mesh_time_limit_seconds(mc: Dict[str, Any], default: float) -> float:
    raw = mc.get("meshTimeLimitSeconds", default)
    try:
        value = float(raw) if raw is not None else default
    except (TypeError, ValueError):
        value = default
    if not math.isfinite(value) or value <= 0:
        value = default
    return min(value, DEFAULT_MESH_TIME_LIMIT_S)


# A solve that has lost conservation is not going to find it again: in the
# Shinaide divergence (2026-09-12) `sum local` climbed 1e0 -> 1e6 -> 1e27 over ~40
# iterations and then GAMG returned nan, which the solver wrote into
# system/data/solver/p_rgh and died reading back — an MPI abort whose only
# visible symptom was a FOAM IO error about the word 'nan'. Watching the
# continuity error lets the runner stop the job at the first catastrophic
# iteration and say WHAT diverged instead of how it crashed.
_RE_TIME_STEP = re.compile(r"Time = (\d+)")
_RE_CONTINUITY = re.compile(r"continuity errors : sum local = ([0-9.eE+\-]+)")
_RE_NAN_RESIDUAL = re.compile(r"residual = nan", re.IGNORECASE)
DIVERGENCE_CONTINUITY_THRESHOLD = 1e6


class MeshTimeoutError(RuntimeError):
    """Mesh generation exceeded its wall-clock budget and was aborted."""


class MeshMemoryError(RuntimeError):
    """The mesh would not fit in the memory available; refused before dcCartMesh started."""


# ---------------------------------------------------------------------------
# dcCart RAM law (provisional) — see DCCARTMESH.md "Memory" and
# docs/developer/dccart-memory-profile-2026-09-21.md.
#
# dcCartMesh keeps the whole mesh resident at once and every rank duplicates
# the octree + carve bookkeeping, so the peak RISES with the rank count:
#
#     total ≈ leaves × SHARED + ranks × (BASE + leaves × PER_RANK)
#
# fitted on Base604 (19.7M octree leaves, 16 ranks, 145.6 GB measured) and
# Slab (329k leaves: 0.80 / 1.60 / 4.56 GB at 1 / 4 / 16 ranks, 2026-09-21).
# ``maxGlobalCells`` is only a FLOOR on the leaf count (the refinement pass that
# crosses it and the 2:1 balance still run), so the runner refuses only on a
# LOWER-bound law (certain failure) and otherwise logs the estimate; dcCartMesh
# re-checks with the exact leaf count after its octree balance.
# dcCartMesh.C carries the SAME constants (kMem*); change both together.
# ---------------------------------------------------------------------------
DCCART_MEM_GUARD_ENV = "DCCART_MEM_GUARD"              # off|0|false|no disables both guards
DCCART_MEM_AVAILABLE_ENV = "DCCART_MEM_AVAILABLE_BYTES"  # overrides the measured number (test seam)
DCCART_MEM_SHARED_BYTES_PER_LEAF = 1600.0
DCCART_MEM_RANK_BYTES_PER_LEAF = 350.0
DCCART_MEM_RANK_BASE_BYTES = 100 * 1024 ** 2
DCCART_MEM_SAFETY = 1.15
DCCART_MEM_HEADROOM_BYTES = 4 * 1024 ** 3
# certain-failure bound: well under every measured point
DCCART_MEM_LOWER_SHARED_BYTES_PER_LEAF = 1024.0
DCCART_MEM_LOWER_RANK_BYTES_PER_LEAF = 150.0
DCCART_MEM_LOWER_RANK_BASE_BYTES = 50 * 1024 ** 2
DCCART_MAX_GLOBAL_CELLS_FALLBACK = 20_000_000

# ---------------------------------------------------------------------------
# dcCart first-layer floor (dcCartSurfaces::weldNearCoincident, 2026-09-22).
# Per axis-aligned surface class the near-coincidence weld band widens to
# ``firstLayerMinFraction`` x that class's own refinement-level spacing, capped
# at ``firstLayerMax`` metres, so no surface carves a slab cell thinner than
# that: the first cell off every wall is a full cell or a cut at least the
# floor thick (6Sigma's snap-to-grid guarantee at the cut-cell scale). A gap
# between two solids wider than ``firstLayerSealGap`` (< 0: the weld
# tolerance) is never sealed by it. 0 = classic weld only. The manifest's
# meshConfig keys override these defaults.
# Default 0.3 (A/B on 2026-09-22, np8, all through the web frontend): Base604
# min cell volume 1.6e-4 -> 2.1e-3 of its hex, max aspect 130 -> 41, no cell
# under 1 mm, and the steady solve's k residual stops freezing (9e-7 with 0
# solver iterations -> 2e-4, zero bounding events, the rack E15 jet hot spot
# k 14.8 -> 2.2); DAML residuals an order lower; Tri_angle unchanged. All
# three CONVERGED on the residual leg; every return-air probe within 0.11 K.
# ---------------------------------------------------------------------------
DCCART_FIRST_LAYER_MIN_FRACTION_DEFAULT = 0.3
DCCART_FIRST_LAYER_MAX_DEFAULT = 0.005
DCCART_FIRST_LAYER_SEAL_GAP_DEFAULT = -1.0


def _dcCart_float_key(mc: Dict[str, Any], key: str, default: float, lo: Optional[float] = None) -> float:
    """``meshConfig[key]`` as a float, ``default`` when absent / empty / unusable.

    ``lo`` clamps from below (the weld tolerance may not be negative; the seal
    gap may, meaning "use the weld tolerance")."""
    raw = mc.get(key)
    if raw is None or raw == "":
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning(f"meshConfig.{key}={raw!r} is not a number; using {default}")
        return default
    if lo is not None:
        value = max(lo, value)
    return value


def _dcCart_max_global_cells(mc: Dict[str, Any]) -> int:
    """The cap dcCartMeshDict gets — one fallback shared by the dict and the guard."""
    try:
        return int(mc.get("maxGlobalCells") or 0) or DCCART_MAX_GLOBAL_CELLS_FALLBACK
    except (TypeError, ValueError):
        return DCCART_MAX_GLOBAL_CELLS_FALLBACK


def _mem_available_bytes(meminfo: Path = Path("/proc/meminfo")) -> Optional[int]:
    """``MemAvailable`` in bytes — never ``MemFree``, which on any host with a
    page cache reads a few GB while hundreds are reclaimable."""
    try:
        for line in meminfo.read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def _cgroup_mem_remaining_bytes(
    cgroup_file: Path = Path("/proc/self/cgroup"),
    root: Path = Path("/sys/fs/cgroup"),
) -> Optional[int]:
    """``memory.max - memory.current`` of the tightest cgroup (v2) above us, or
    None when nothing limits us (every level reads ``max``)."""
    try:
        path = None
        for line in cgroup_file.read_text().splitlines():
            if line.startswith("0::"):
                path = line[3:].strip()
                break
    except OSError:
        return None
    if path is None:
        return None
    remaining: Optional[int] = None
    node = root / path.lstrip("/")
    while True:
        try:
            limit = (node / "memory.max").read_text().strip()
            if limit and limit != "max":
                try:
                    used = int((node / "memory.current").read_text().strip())
                except (OSError, ValueError):
                    used = 0
                left = int(limit) - used
                remaining = left if remaining is None else min(remaining, left)
        except (OSError, ValueError):
            pass
        if node == root or root not in node.parents:
            break
        node = node.parent
    return remaining


def _dcCart_mem_budget_bytes() -> Optional[int]:
    """What the mesh may use: the env override, else the smaller of MemAvailable
    and the cgroup remainder; None when neither is readable."""
    override = os.environ.get(DCCART_MEM_AVAILABLE_ENV, "").strip()
    if override:
        try:
            return int(float(override))
        except ValueError:
            logger.warning(f"{DCCART_MEM_AVAILABLE_ENV}={override!r} is not a number; ignored")
    candidates = [b for b in (_mem_available_bytes(), _cgroup_mem_remaining_bytes()) if b is not None]
    return min(candidates) if candidates else None


def _dcCart_mem_estimate_bytes(leaves: int, n_procs: int) -> int:
    """The RAM law at ``leaves`` octree leaves on ``n_procs`` ranks, with safety."""
    n = max(1, int(n_procs or 1))
    law = leaves * DCCART_MEM_SHARED_BYTES_PER_LEAF + n * (
        DCCART_MEM_RANK_BASE_BYTES + leaves * DCCART_MEM_RANK_BYTES_PER_LEAF
    )
    return int(law * DCCART_MEM_SAFETY)


def _dcCart_mem_lower_bound_bytes(leaves: int, n_procs: int) -> int:
    """A floor on the peak: below every measured point, so exceeding the budget
    here is a certain failure."""
    n = max(1, int(n_procs or 1))
    return int(
        leaves * DCCART_MEM_LOWER_SHARED_BYTES_PER_LEAF
        + n * (DCCART_MEM_LOWER_RANK_BASE_BYTES + leaves * DCCART_MEM_LOWER_RANK_BYTES_PER_LEAF)
    )


def _dcCart_mem_fit_leaves(budget: int, n_procs: int) -> int:
    """The largest leaf count (rounded down to 100k) whose estimate plus headroom fits."""
    n = max(1, int(n_procs or 1))
    per_leaf = (DCCART_MEM_SHARED_BYTES_PER_LEAF + n * DCCART_MEM_RANK_BYTES_PER_LEAF) * DCCART_MEM_SAFETY
    fixed = n * DCCART_MEM_RANK_BASE_BYTES * DCCART_MEM_SAFETY + DCCART_MEM_HEADROOM_BYTES
    leaves = (budget - fixed) / per_leaf
    return max(0, int(leaves // 100_000) * 100_000)


def _mem_guard_enabled() -> bool:
    return os.environ.get(DCCART_MEM_GUARD_ENV, "").strip().lower() not in ("off", "0", "false", "no")


def _resolve_mesh_mode(mc: Dict[str, Any]) -> str:
    """The mesh semantics follow the ENGINE; there is no separate mode switch.

    Two ways to mesh (2026-09-09, replacing the three-way ``meshMode``):

      * ``meshEngine: snappy``  -> ``bodyFitted``: blockMesh uniform background,
        snappyHexMesh with snapping ON (body-fitted).
      * ``meshEngine: dcCart``  -> ``cartesianLocalized``: uniform background
        plus per-object octree refinement, snapping OFF — the geometry is
        resolved by the cut-cell pass, which needs no grid-aligned breakpoints.

    ``meshMode`` / ``cartesianMode`` / ``structuredGrid`` in a manifest are
    ignored: the ``structured`` (grid-aligned, cross-hatch) background is
    retired, it was a stair-step work-around the cut cells make unnecessary.
    """
    return "cartesianLocalized" if _resolve_mesh_engine(mc) == "dcCart" else "bodyFitted"


_MESH_ENGINES = ("snappy", "dcCart", "meshgen")


def _resolve_mesh_engine(mc: Dict[str, Any]) -> str:
    """Which program builds the mesh: ``snappy`` (default), ``dcCart`` or ``meshgen``.

    An axis ORTHOGONAL to ``meshMode``: the mode describes the mesh semantics
    (snap on/off, background grid), the engine the executable. Absent or
    unrecognised means ``snappy`` — zero behaviour change for every existing
    manifest. ``dcCart`` runs the repo's own cut-cell Cartesian mesher
    (third_parties/dcCartMesh/DCCARTMESH.md) in place of blockMesh +
    snappyHexMesh; createBaffles and every clean-up step stay the same.
    """
    e = mc.get("meshEngine")
    return e if e in _MESH_ENGINES else "snappy"


def _mesh_mode_is_cartesian(mode: str) -> bool:
    """True for the snap-off Cartesian modes (localized octree and structured)."""
    return mode in ("cartesianLocalized", "structured")


def _min_server_slab_height(racks: Dict[str, Any]) -> Optional[float]:
    """Thinnest fan-server slab height (m) across all racks = min(unitHeight * spanSlots).

    This is the smallest geometry feature snap-off must resolve to keep every
    fan-server outlet/inlet patch: a server slab is marked solid only if a cell
    centre falls inside it, which is guaranteed only when the cell pitch is <=
    the slab height (otherwise thin 1U servers fall between cell centres and
    their flow patches drop). Returns None if there are no fan servers.
    """
    best: Optional[float] = None
    for rack in racks.values():
        unit_h = float(rack.get("geometry", {}).get("unitHeight", 0.04445) or 0.04445)
        for srv in rack.get("servers", []):
            if srv.get("type") != "fanServer":
                continue
            span = int(srv.get("spanSlots", 1) or 1)
            h = unit_h * span
            if h > 0 and (best is None or h < best):
                best = h
    return best


def _cartesian_rack_region_level(
    racks: Dict[str, Any],
    base_size: float,
    requested_level: int,
    min_cells: int = 1,
    min_cell_size: Optional[float] = None,
) -> int:
    """Rack-region refinement level for cartesian (snap-off) so the cell pitch
    resolves the thinnest fan-server slab.

    Returns ``max(requested_level, ceil(log2(base_size * min_cells / slab)))`` so
    the background grid + rack-region refinement guarantee ``cell <= slab/min_cells``.
    With ``min_cells=1`` a server cell centre always lands inside the slab; never
    coarsens an explicit higher request. No fan servers -> requested level unchanged.
    """
    slab = _min_server_slab_height(racks)
    if not slab or slab <= 0 or base_size <= 0:
        return requested_level
    need = max(0, math.ceil(math.log2(base_size * min_cells / slab)))
    level = max(requested_level, need)
    # ``min_cell_size`` (meshConfig.rackCellMinSize) caps the raise: the cell
    # never goes below it (6Sigma resolves 1U servers at 1/2U, never 12 mm).
    # An explicit request finer than the cap is the user's and stands.
    if min_cell_size and float(min_cell_size) > 0:
        cap = max(0, math.floor(math.log2(base_size / float(min_cell_size)) + 1e-9))
        if level > cap >= requested_level:
            level = cap
    return level


def _detect_thin_gap_regions(
    racks: Dict[str, Any],
    base_size: float,
    rack_region_level: int,
    min_cells: int = 2,
) -> List[Dict[str, Any]]:
    """Scan racks for thin unoccupied U-slot gaps needing higher refinement.

    Only racks with blankingPanelType == "typicalSmallGap" participate.
    """
    results: List[Dict[str, Any]] = []
    cell_size = base_size / (2 ** rack_region_level)

    for rack_name, rack in racks.items():
        if rack.get("blankingPanelType") != "typicalSmallGap":
            continue

        geom = rack.get("geometry") or {}
        unit_count = int(geom.get("unitCount", 0))
        unit_h = float(geom.get("unitHeight", 0.04445))
        if unit_count <= 0 or unit_h <= 0:
            continue

        size = geom.get("size") or {}
        W = float(size.get("W", 0.6))
        D = float(size.get("D", 1.0))
        slot_ratio = float(geom.get("slotWidthRatio", 0.8))
        inlet_off = float(geom.get("inletOffset", 0))
        outlet_off = float(geom.get("outletOffset", 0))

        occupied = set()
        for srv in rack.get("servers", []):
            start = int(srv.get("startSlot", 0))
            span = int(srv.get("spanSlots", 1))
            for u in range(start, start + span):
                occupied.add(u)

        unoccupied = sorted(set(range(unit_count)) - occupied)
        if not unoccupied:
            continue

        ranges: List[Tuple[int, int]] = []
        rng_start = unoccupied[0]
        prev = rng_start
        for u in unoccupied[1:]:
            if u == prev + 1:
                prev = u
            else:
                ranges.append((rng_start, prev))
                rng_start = u
                prev = u
        ranges.append((rng_start, prev))

        for start_u, end_u in ranges:
            span_units = end_u - start_u + 1
            if span_units > 2:
                continue
            gap_h = span_units * unit_h
            if gap_h / cell_size >= min_cells:
                continue
            safe_level = math.ceil(math.log2(base_size * min_cells / gap_h))
            box_level = max(safe_level, rack_region_level + 1)

            results.append({
                "rack_name": rack_name,
                "start_unit": start_u,
                "end_unit": end_u,
                "box_level": box_level,
                "gap_height": gap_h,
                "geom": {
                    "slot_width": W * slot_ratio,
                    "channel_depth": D - inlet_off - outlet_off,
                    "gap_start_h": start_u * unit_h,
                    "gap_height": gap_h,
                    "W": W, "D": D,
                    "inlet_offset": inlet_off,
                    "outlet_offset": outlet_off,
                    "slot_ratio": slot_ratio,
                    "anchor": geom.get("anchor") or {},
                    "quaternion": geom.get("quaternion"),
                    "scale": geom.get("scale"),
                },
            })

    return results


def _darcy_forchheimer(sigma: float, depth: float, min_depth: float = 0.1) -> Tuple[float, float]:
    """Compute Darcy (d) and Forchheimer (f) coefficients for rack leakage.

    Uses orifice/perforated-plate model (see AGENTS.md §7.3):
        f = (1/L) × ((1/(Cd×σ)) - 1)²
    where L = rack depth, Cd = 0.6 (discharge coefficient), σ = open area ratio.
    Darcy (viscous) term is set to zero — leakage flow is turbulent.
    """
    sigma = max(0.01, min(0.99, sigma))
    if depth < min_depth:
        logger.warning(
            f"Leakage depth={depth:.4f} is suspiciously small (<{min_depth}m), "
            f"clamping to {min_depth} — check rack geometry 'D' dimension"
        )
        depth = min_depth
    cd = 0.6
    f_coeff = (1.0 / depth) * ((1.0 / (cd * sigma)) - 1.0) ** 2
    d_coeff = 0.0
    return d_coeff, f_coeff


def _read_stl_solid_names(stl_path: Path) -> List[str]:
    """Parse `solid <name>` lines from an ASCII STL file and return solid names."""
    names: List[str] = []
    try:
        with open(stl_path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                stripped = line.strip()
                if stripped.startswith("solid "):
                    name = stripped[6:].strip()
                    if name and name not in names:
                        names.append(name)
    except Exception as exc:
        logger.warning(f"Could not read STL solid names from {stl_path}: {exc}")
    return names


Vec3 = Tuple[float, float, float]
Triangle = Tuple[Vec3, Vec3, Vec3]
SurfaceRef = Tuple[str, str]
SceneTriangle = Tuple[str, str, Triangle]

_GEOM_EPS = 1e-9


class PatchGeometry(NamedTuple):
    """Geometric summary of a flat STL patch, used by FieldGenerator
    for physically-grounded turbulence inlet BC computation.

    Fields:
        area:       total surface area in m²
        perimeter:  total boundary-polygon length in m (0.0 if extraction
                    yields no boundary edges — caller should fall back to
                    circular hydraulic-diameter approximation)
        bbox_dims:  axis-aligned bounding box (dx, dy, dz) in m
    """
    area: float
    perimeter: float
    bbox_dims: Tuple[float, float, float]


_PLANAR_TOL = 1e-5
_RAY_START_EPS = 1e-6
_SEED_DEDUPE_TOL = 1e-6


@dataclass(frozen=True)
class _PatchSeedSpec:
    zone_name: str
    solid_name: str
    stl_path: Path
    patch_kind: str
    seed_config: Optional[Dict[str, Any]] = None


def _vec_add(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _vec_sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _vec_scale(v: Vec3, scale: float) -> Vec3:
    return (v[0] * scale, v[1] * scale, v[2] * scale)


def _vec_dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _vec_cross(a: Vec3, b: Vec3) -> Vec3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _vec_norm(v: Vec3) -> float:
    return math.sqrt(_vec_dot(v, v))


def _vec_normalize(v: Vec3) -> Vec3:
    norm = _vec_norm(v)
    if norm <= _GEOM_EPS:
        raise RuntimeError("Cannot normalize zero-length vector")
    return (v[0] / norm, v[1] / norm, v[2] / norm)


def _quat_normalize(q: Dict[str, float]) -> Tuple[float, float, float, float]:
    qx = float(q.get("x", 0.0))
    qy = float(q.get("y", 0.0))
    qz = float(q.get("z", 0.0))
    qw = float(q.get("w", 1.0))
    norm = math.sqrt(qx * qx + qy * qy + qz * qz + qw * qw)
    if norm <= _GEOM_EPS:
        return (0.0, 0.0, 0.0, 1.0)
    return (qx / norm, qy / norm, qz / norm, qw / norm)


def _quat_rotate(vec: Vec3, quat: Dict[str, float]) -> Vec3:
    qx, qy, qz, qw = _quat_normalize(quat)
    vx, vy, vz = vec
    tx = 2.0 * (qy * vz - qz * vy)
    ty = 2.0 * (qz * vx - qx * vz)
    tz = 2.0 * (qx * vy - qy * vx)
    return (
        vx + qw * tx + (qy * tz - qz * ty),
        vy + qw * ty + (qz * tx - qx * tz),
        vz + qw * tz + (qx * ty - qy * tx),
    )


def _ensure_right_handed_box(
    origin: Vec3,
    e1: Vec3,
    e2: Vec3,
    e3: Vec3,
    span: Vec3,
) -> Tuple[Vec3, Vec3, Vec3, Vec3]:
    """Return an equivalent searchableRotatedBox basis that is right-handed.

    OpenFOAM's ``searchableRotatedBox`` reconstructs its second local axis from
    the cross product of the others and ignores the supplied ``e2``. When a rack
    is mirrored (a negative ``scale`` component), the ``(e1, e2, e3)`` basis is
    left-handed (determinant < 0); OpenFOAM's reconstructed axis then points the
    opposite way and flips the whole box — e.g. extending the level-3 region
    downward through the floor instead of up over the rack.

    A box is identical under reflection, so for a left-handed basis we flip
    ``e3`` and shift ``origin`` to the opposite face along ``e3``. The covered
    volume is unchanged but the basis becomes right-handed, so OpenFOAM
    interprets it as intended. A right-handed basis is returned unchanged.
    """
    det = _vec_dot(e1, _vec_cross(e2, e3))
    if det >= 0.0:
        return origin, e1, e2, e3
    origin = _vec_add(origin, _vec_scale(e3, span[2]))
    e3 = (-e3[0], -e3[1], -e3[2])
    return origin, e1, e2, e3


def _solid_bounding_boxes(
    stl_path: Path,
) -> Dict[str, Tuple[List[float], List[float]]]:
    """Read an ASCII STL file and return AABB per solid from actual vertices.

    Returns dict mapping solid_name → (min_xyz, max_xyz) as 3-element lists.

    This is the only reliable way to determine rack bounding boxes because:
    1. The ``orientation`` field in the cfdrun is extracted via
       ``atan2(q.z, q.w)`` (wrong axis) and always reads ~0° for Y-axis
       rotations, so anchor/size/orientation-based computation fails.
    2. Racks can have negative scale components (mirroring) not captured
       in the orientation field.
    """
    solids = _read_ascii_stl_triangles(stl_path)
    result: Dict[str, Tuple[List[float], List[float]]] = {}
    for name, triangles in solids.items():
        if not triangles:
            continue
        xs: List[float] = []
        ys: List[float] = []
        zs: List[float] = []
        for tri in triangles:
            for v in tri:
                xs.append(v[0])
                ys.append(v[1])
                zs.append(v[2])
        result[name] = ([min(xs), min(ys), min(zs)],
                        [max(xs), max(ys), max(zs)])
    return result


def _triangle_centroid(triangle: Triangle) -> Vec3:
    a, b, c = triangle
    return (
        (a[0] + b[0] + c[0]) / 3.0,
        (a[1] + b[1] + c[1]) / 3.0,
        (a[2] + b[2] + c[2]) / 3.0,
    )


def _triangle_area_and_normal(triangle: Triangle) -> Tuple[float, Vec3]:
    a, b, c = triangle
    cross = _vec_cross(_vec_sub(b, a), _vec_sub(c, a))
    area = 0.5 * _vec_norm(cross)
    return area, cross


def _compute_boundary_perimeter(triangles: Iterable[Triangle]) -> float:
    """Length of the boundary polygon of a triangle mesh patch.

    An edge (v_i, v_j) belongs to the patch boundary iff it appears in
    exactly one triangle. Shared edges (interior) appear in two; degenerate
    edges are ignored. Vertices are quantised to 1e-6 m so floating-point
    jitter from STL generation does not break edge identity.

    Returns 0.0 for an empty input or a closed-shell topology where every
    edge is shared (e.g. sphere, tetrahedron surface) — caller interprets
    this as 'fall back to circular D_h'.
    """
    edge_counts: Dict[Tuple[Tuple[int, int, int], Tuple[int, int, int]], int] = {}
    edge_lengths: Dict[Tuple[Tuple[int, int, int], Tuple[int, int, int]], float] = {}

    def _quantise(v: Vec3) -> Tuple[int, int, int]:
        return (
            int(round(v[0] * 1_000_000)),
            int(round(v[1] * 1_000_000)),
            int(round(v[2] * 1_000_000)),
        )

    for triangle in triangles:
        qa, qb, qc = _quantise(triangle[0]), _quantise(triangle[1]), _quantise(triangle[2])
        for (va, vb), (qa_k, qb_k) in (
            ((triangle[0], triangle[1]), (qa, qb)),
            ((triangle[1], triangle[2]), (qb, qc)),
            ((triangle[2], triangle[0]), (qc, qa)),
        ):
            if qa_k == qb_k:  # degenerate edge
                continue
            key = (qa_k, qb_k) if qa_k < qb_k else (qb_k, qa_k)
            edge_counts[key] = edge_counts.get(key, 0) + 1
            if key not in edge_lengths:
                edge_lengths[key] = _vec_norm(_vec_sub(vb, va))

    total = 0.0
    for key, count in edge_counts.items():
        if count == 1:
            total += edge_lengths[key]
    return total


def _format_vec(vec: Vec3) -> str:
    return f"({vec[0]:.6f}, {vec[1]:.6f}, {vec[2]:.6f})"


def _partition_hole_expected_map(data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Return expected partition-hole helper STLs for the current export."""
    expected: Dict[str, Dict[str, Any]] = {}
    for part_name, part in (data.get("partitionWalls", {}) or {}).items():
        for hole in part.get("holes", []):
            phys = hole.get("physics", {}) or {}
            # 2026-04-16 simplification: only leakageRate gates the source
            # term now. The legacy `resistanceEnabled` field is no longer
            # checked (still accepted for backward compatibility on load).
            try:
                leakage_rate = float(phys.get("leakageRate", 100.0))
            except (TypeError, ValueError):
                continue
            if leakage_rate >= 100.0:
                continue

            zone_name = hole.get("zoneName", "")
            zone_file = hole.get("zoneStlFile")
            if not zone_name or not zone_file:
                continue

            expected[zone_file] = {
                "part_name": part_name,
                "zone_name": zone_name,
                "zone_file": zone_file,
                "surface_name": _sanitize(Path(zone_file).stem),
                "zone_solid": _sanitize(zone_name),
            }
    return expected


def _read_ascii_stl_triangles(stl_path: Path) -> Dict[str, List[Triangle]]:
    """Parse triangles grouped by solid name from an ASCII STL file."""
    solids: Dict[str, List[Triangle]] = {}
    current_name: Optional[str] = None
    vertices: List[Vec3] = []

    with open(stl_path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            stripped = line.strip()
            if stripped.startswith("solid "):
                current_name = stripped[6:].strip()
                if current_name:
                    solids.setdefault(current_name, [])
                vertices = []
                continue
            if stripped.startswith("endsolid"):
                current_name = None
                vertices = []
                continue
            if current_name and stripped.startswith("vertex "):
                parts = stripped.split()
                if len(parts) >= 4:
                    vertices.append((float(parts[1]), float(parts[2]), float(parts[3])))
                    if len(vertices) == 3:
                        solids[current_name].append((vertices[0], vertices[1], vertices[2]))
                        vertices = []
    return solids


def _compute_triangle_surface_centroid(triangles: Iterable[Triangle]) -> Optional[Vec3]:
    total_area = 0.0
    weighted = (0.0, 0.0, 0.0)
    for triangle in triangles:
        area, _ = _triangle_area_and_normal(triangle)
        if area <= _GEOM_EPS:
            continue
        centroid = _triangle_centroid(triangle)
        weighted = _vec_add(weighted, _vec_scale(centroid, area))
        total_area += area
    if total_area <= _GEOM_EPS:
        return None
    return _vec_scale(weighted, 1.0 / total_area)


def _compute_planar_patch_geometry(
    triangles: Iterable[Triangle],
    planar_tol: float = _PLANAR_TOL,
) -> Tuple[Vec3, Vec3]:
    """Return an area-weighted center and unit normal for a near-planar patch."""
    tri_list = list(triangles)
    if not tri_list:
        raise RuntimeError("Patch STL contains no triangles")

    center = _compute_triangle_surface_centroid(tri_list)
    if center is None:
        raise RuntimeError("Patch STL contains only degenerate triangles")

    total_normal = (0.0, 0.0, 0.0)
    vertices: List[Vec3] = []
    for triangle in tri_list:
        area, cross = _triangle_area_and_normal(triangle)
        if area <= _GEOM_EPS:
            continue
        total_normal = _vec_add(total_normal, cross)
        vertices.extend(triangle)

    if _vec_norm(total_normal) <= _GEOM_EPS:
        raise RuntimeError("Patch STL has zero net normal and cannot define a seed plane")

    normal = _vec_normalize(total_normal)
    max_deviation = max(abs(_vec_dot(_vec_sub(vertex, center), normal)) for vertex in vertices)
    if max_deviation > planar_tol:
        raise RuntimeError(
            f"Patch STL is not planar enough for dual-side seed placement "
            f"(max deviation {max_deviation:.6g} > tolerance {planar_tol:.6g})"
        )
    return center, normal


def _ray_triangle_intersection(origin: Vec3, direction: Vec3, triangle: Triangle) -> Optional[float]:
    """Return ray distance to the triangle using Moller-Trumbore, or None if no hit."""
    v0, v1, v2 = triangle
    edge1 = _vec_sub(v1, v0)
    edge2 = _vec_sub(v2, v0)
    pvec = _vec_cross(direction, edge2)
    det = _vec_dot(edge1, pvec)
    if abs(det) <= _GEOM_EPS:
        return None
    inv_det = 1.0 / det
    tvec = _vec_sub(origin, v0)
    u = _vec_dot(tvec, pvec) * inv_det
    if u < 0.0 or u > 1.0:
        return None
    qvec = _vec_cross(tvec, edge1)
    v = _vec_dot(direction, qvec) * inv_det
    if v < 0.0 or u + v > 1.0:
        return None
    distance = _vec_dot(edge2, qvec) * inv_det
    if distance <= _GEOM_EPS:
        return None
    return distance


def _nearest_ray_clearance(
    center: Vec3,
    direction: Vec3,
    scene_triangles: Iterable[SceneTriangle],
    ignored_surfaces: Iterable[SurfaceRef],
) -> Optional[float]:
    origin = _vec_add(center, _vec_scale(direction, _RAY_START_EPS))
    nearest: Optional[float] = None
    ignored_surface_set = set(ignored_surfaces)
    for filename, solid_name, triangle in scene_triangles:
        if (filename, solid_name) in ignored_surface_set:
            continue
        hit = _ray_triangle_intersection(origin, direction, triangle)
        if hit is None:
            continue
        clearance = hit + _RAY_START_EPS
        if nearest is None or clearance < nearest:
            nearest = clearance
    return nearest


def _two_sided_clearance(
    anchor: Vec3,
    direction: Vec3,
    scene_triangles: Iterable[SceneTriangle],
    ignored_surfaces: Iterable[SurfaceRef],
) -> Tuple[Optional[float], Optional[float]]:
    ignored = set(ignored_surfaces)
    scene = list(scene_triangles)
    return (
        _nearest_ray_clearance(anchor, direction, scene, ignored),
        _nearest_ray_clearance(anchor, _vec_scale(direction, -1.0), scene, ignored),
    )


def _sheet_anchor_candidates(triangles: Iterable[Triangle]) -> List[Vec3]:
    """Points that lie ON a sheet: each triangle's centroid and its three
    mid-weighted interior points (1/2, 1/4, 1/4)."""
    out: List[Vec3] = []
    for tri in triangles:
        area, _ = _triangle_area_and_normal(tri)
        if area <= _GEOM_EPS:
            continue
        a, b, c = tri
        for wa, wb, wc in ((1 / 3, 1 / 3, 1 / 3), (0.5, 0.25, 0.25), (0.25, 0.5, 0.25), (0.25, 0.25, 0.5)):
            out.append(
                (
                    wa * a[0] + wb * b[0] + wc * c[0],
                    wa * a[1] + wb * b[1] + wc * c[1],
                    wa * a[2] + wb * b[2] + wc * c[2],
                )
            )
    return out


def _best_sheet_seed_anchor(
    triangles: Iterable[Triangle],
    direction: Vec3,
    offset: float,
    scene_triangles: Iterable[SceneTriangle],
    ignored_surfaces: Iterable[SurfaceRef],
) -> Optional[Tuple[Vec3, float, float]]:
    """The point on the sheet with the most room on BOTH sides.

    The default anchor is the sheet's area centroid, and a sheet with a
    cut-out — a wall baffle the exporter has opened around an ACU standing
    against it — has its centroid INSIDE the hole, on the ACU's front plane.
    The ray from there meets the ACU at 0.1 mm, the offset is clamped to
    0.08 mm, and the seed on that side lands inside the ACU: the carve floods
    it and its seven other patches vanish (STDCT PCU_3, 2026-09-22). A point
    on the sheet itself can never be inside a solid the sheet stands against.

    Returns (anchor, clearance_pos, clearance_neg) for the candidate whose
    smaller clearance is largest, or None when no candidate has clearance on
    both sides.
    """
    scene = list(scene_triangles)
    ignored = set(ignored_surfaces)
    best: Optional[Tuple[Vec3, float, float]] = None
    best_min = -1.0
    for cand in _sheet_anchor_candidates(triangles):
        pos, neg = _two_sided_clearance(cand, direction, scene, ignored)
        if pos is None or neg is None:
            continue
        m = min(pos, neg)
        if m > best_min:
            best_min = m
            best = (cand, pos, neg)
        if best_min >= offset * 4.0:
            break
    return best


def _flatten_scene_triangles(
    triangle_cache: Dict[str, Dict[str, List[Triangle]]]
) -> List[SceneTriangle]:
    return [
        (filename, solid_name, triangle)
        for filename, solids in triangle_cache.items()
        for solid_name, triangles in solids.items()
        for triangle in triangles
    ]


def _normalized_match_key(name: str) -> str:
    return _sanitize(name).strip("_").lower()


def _resolve_partition_wall_body_surfaces(
    triangle_cache: Dict[str, Dict[str, List[Triangle]]],
    part_name: str,
    wall_solid_names: Iterable[str],
) -> Tuple[Set[SurfaceRef], List[str]]:
    resolved_surfaces: Set[SurfaceRef] = set()
    resolved_names: Set[str] = set()
    missing_names: List[str] = []

    for solid_name in wall_solid_names:
        if not solid_name:
            continue
        matched = False
        for filename, solids in triangle_cache.items():
            if solid_name in solids:
                resolved_surfaces.add((filename, solid_name))
                resolved_names.add(solid_name)
                matched = True
        if not matched:
            missing_names.append(solid_name)

    if missing_names:
        quoted = ", ".join(repr(name) for name in missing_names)
        raise RuntimeError(
            f"Cannot validate partition wall seed placement for '{part_name}': "
            f"wall body solid(s) {quoted} were not parsed from any grouped STL file"
        )

    if resolved_surfaces:
        return resolved_surfaces, sorted(resolved_names)

    part_key = _normalized_match_key(part_name)
    expected_body_key = f"{part_key}_body"
    expected_body_prefix = f"{expected_body_key}_"
    preferred_filenames = [
        filename
        for filename in triangle_cache
        if "partition_wall" in _normalized_match_key(Path(filename).stem)
    ]
    def _collect_matches(filenames: Iterable[str]) -> None:
        for filename in filenames:
            for solid_name in triangle_cache.get(filename, {}):
                solid_key = _normalized_match_key(solid_name)
                if solid_key == expected_body_key or solid_key.startswith(expected_body_prefix):
                    resolved_surfaces.add((filename, solid_name))
                    resolved_names.add(solid_name)

    _collect_matches(preferred_filenames or list(triangle_cache.keys()))
    if not resolved_surfaces and preferred_filenames:
        _collect_matches(triangle_cache.keys())

    if resolved_surfaces:
        return resolved_surfaces, sorted(resolved_names)

    raise RuntimeError(
        f"Cannot validate partition wall seed placement for '{part_name}': "
        "wallSolidNames is missing/empty and the wall body solid cannot be inferred "
        f"from the partition wall name (expected naming like '{part_name}_body')"
    )


def _dedupe_seed_entries(entries: Iterable[Tuple[Vec3, str]], tol: float = _SEED_DEDUPE_TOL) -> List[Tuple[Vec3, str]]:
    deduped: List[Tuple[Vec3, str]] = []
    for point, zone_name in entries:
        if any(
            existing_zone == zone_name
            and math.isclose(point[0], existing_point[0], abs_tol=tol)
            and math.isclose(point[1], existing_point[1], abs_tol=tol)
            and math.isclose(point[2], existing_point[2], abs_tol=tol)
            for existing_point, existing_zone in deduped
        ):
            continue
        deduped.append((point, zone_name))
    return deduped


def _closest_point_on_triangle(point: Vec3, triangle: Triangle) -> Vec3:
    """Return the closest point on a triangle to an arbitrary point."""
    a, b, c = triangle
    ab = _vec_sub(b, a)
    ac = _vec_sub(c, a)
    ap = _vec_sub(point, a)

    d1 = _vec_dot(ab, ap)
    d2 = _vec_dot(ac, ap)
    if d1 <= 0.0 and d2 <= 0.0:
        return a

    bp = _vec_sub(point, b)
    d3 = _vec_dot(ab, bp)
    d4 = _vec_dot(ac, bp)
    if d3 >= 0.0 and d4 <= d3:
        return b

    vc = d1 * d4 - d3 * d2
    if vc <= 0.0 and d1 >= 0.0 and d3 <= 0.0:
        v = d1 / (d1 - d3)
        return _vec_add(a, _vec_scale(ab, v))

    cp = _vec_sub(point, c)
    d5 = _vec_dot(ab, cp)
    d6 = _vec_dot(ac, cp)
    if d6 >= 0.0 and d5 <= d6:
        return c

    vb = d5 * d2 - d1 * d6
    if vb <= 0.0 and d2 >= 0.0 and d6 <= 0.0:
        w = d2 / (d2 - d6)
        return _vec_add(a, _vec_scale(ac, w))

    va = d3 * d6 - d5 * d4
    if va <= 0.0 and (d4 - d3) >= 0.0 and (d5 - d6) >= 0.0:
        bc = _vec_sub(c, b)
        w = (d4 - d3) / ((d4 - d3) + (d5 - d6))
        return _vec_add(b, _vec_scale(bc, w))

    denom = 1.0 / (va + vb + vc)
    v = vb * denom
    w = vc * denom
    return _vec_add(a, _vec_add(_vec_scale(ab, v), _vec_scale(ac, w)))


def _project_point_onto_plane(point: Vec3, plane_point: Vec3, plane_normal: Vec3) -> Vec3:
    distance = _vec_dot(_vec_sub(point, plane_point), plane_normal)
    return _vec_sub(point, _vec_scale(plane_normal, distance))


def _project_point_onto_patch(point: Vec3, triangles: Iterable[Triangle]) -> Vec3:
    """Project a point onto the nearest point on the patch triangles."""
    nearest_point: Optional[Vec3] = None
    nearest_dist2: Optional[float] = None
    for triangle in triangles:
        candidate = _closest_point_on_triangle(point, triangle)
        delta = _vec_sub(candidate, point)
        dist2 = _vec_dot(delta, delta)
        if nearest_dist2 is None or dist2 < nearest_dist2:
            nearest_dist2 = dist2
            nearest_point = candidate
    if nearest_point is None:
        raise RuntimeError("Patch STL contains no valid triangles for seed anchor projection")
    return nearest_point


def _compute_stl_solid_centroid(stl_path: Path, solid_name: str) -> Optional[Tuple[float, float, float]]:
    """Compute an approximate geometric center from a named STL solid surface."""
    try:
        solids = _read_ascii_stl_triangles(stl_path)
    except Exception:
        return None
    triangles = solids.get(solid_name, [])
    return _compute_triangle_surface_centroid(triangles)


def _dict_to_vec3(value: Optional[Dict[str, Any]], fallback: Vec3) -> Vec3:
    if not isinstance(value, dict):
        return fallback
    try:
        return (
            float(value.get("x", fallback[0])),
            float(value.get("y", fallback[1])),
            float(value.get("z", fallback[2])),
        )
    except (TypeError, ValueError):
        return fallback


def _resolve_patch_seed_config(
    seed_config: Optional[Dict[str, Any]],
    center: Vec3,
    normal: Vec3,
    default_offset: float,
) -> Tuple[Vec3, Vec3, float]:
    anchor = center
    direction = normal
    offset = default_offset
    if isinstance(seed_config, dict) and seed_config.get("enabled", True) is not False:
        anchor = _dict_to_vec3(seed_config.get("anchor"), center)
        direction = _dict_to_vec3(seed_config.get("direction"), normal)
        try:
            offset = float(seed_config.get("offset", default_offset))
        except (TypeError, ValueError):
            offset = default_offset
    try:
        direction = _vec_normalize(direction)
    except RuntimeError:
        direction = normal
    if offset <= _GEOM_EPS:
        offset = default_offset
    return anchor, direction, offset



# ---------------------------------------------------------------------------
# BlockMesh grid-alignment optimisation
# ---------------------------------------------------------------------------

def optimize_blockmesh_cells(
    room_bounds: Dict[str, Tuple[float, float]],
    base_size: float,
    rack_configs: Optional[List[Dict[str, Any]]] = None,
    search_range: int = 12,
) -> Dict[str, Tuple[float, float, int]]:
    """Backward-compat wrapper — returns single-block result from multi-block.

    Returns ``{axis: (bm_min, bm_max, n_cells)}`` for each of x, y, z.
    For the actual multi-block generation, use ``compute_multiblock_grid``.
    """
    mb = compute_multiblock_grid(room_bounds, base_size)
    result: Dict[str, Tuple[float, float, int]] = {}
    for axis in ("x", "y", "z"):
        breaks = mb[axis]
        bm_min = breaks[0][0]
        bm_max = breaks[-1][1]
        n_total = sum(seg[2] for seg in breaks)
        result[axis] = (bm_min, bm_max, n_total)
    return result


def compute_multiblock_grid(
    room_bounds: Dict[str, Tuple[float, float]],
    base_size: float,
    rack_configs: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, List[Tuple[float, float, int]]]:
    """Compute uniform multi-block grid from room boundaries.

    Returns ``{axis: [(seg_min, seg_max, n_cells), ...]}`` per axis.
    Each axis gets one main segment from room_min to room_max with
    ``max(1, round(length / base_size))`` cells, plus 1-cell padding
    on each side.

    ``rack_configs`` is accepted for backward compatibility but ignored —
    rack alignment was removed in favour of searchableRotatedBox
    refinementRegions in snappyHexMeshDict.
    """
    result: Dict[str, List[Tuple[float, float, int]]] = {}

    for axis in ("x", "y", "z"):
        r_min, r_max = room_bounds[axis]
        length = r_max - r_min
        n = max(1, round(length / base_size))
        actual_cs = length / n if n > 0 else base_size

        segments: List[Tuple[float, float, int]] = []
        segments.append((r_min - actual_cs, r_min, 1))   # low padding
        segments.append((r_min, r_max, n))                # main segment
        segments.append((r_max, r_max + actual_cs, 1))    # high padding

        result[axis] = segments

    return result


def compute_aligned_grid(
    room_bounds: Dict[str, Tuple[float, float]],
    base_size: float,
    align_planes: Dict[str, List[float]],
) -> Dict[str, List[Tuple[float, float, int]]]:
    """Grid-ALIGNED multi-block grid (6Sigma 'grid lines on solid surfaces').

    Like :func:`compute_multiblock_grid` but inserts grid breakpoints exactly on
    object face coordinates (``align_planes`` per axis). With ``snap off`` the
    castellated mesh then keeps cells *exactly* up to those planes, so
    axis-aligned (right-angle) boundaries come out perfectly straight and
    orthogonal — no stair-step, no half-cell shift — while each inter-face
    segment is still divided at ~``base_size`` resolution.

    Near-coincident planes (closer than ``0.25*base_size``) are merged to avoid
    sliver cells; planes at/outside the room bounds are ignored. ``room_min`` and
    ``room_max`` are always anchored. Returns the same
    ``{axis: [(seg_min, seg_max, n_cells), ...]}`` structure, so the existing
    blockMeshDict emitter consumes it unchanged.
    """
    result: Dict[str, List[Tuple[float, float, int]]] = {}
    # Only planes essentially AT a room bound are dropped, so a face sitting just
    # inside the wall/ceiling keeps its grid line (a false ceiling 50 mm below the
    # slab used to be lost to a 0.25*base margin → bumpy ceiling).
    eps = 1.0e-3
    # Planes within `eps_merge` are the SAME physical surface (watertight-tolerance
    # duplicates) and collapse to one line. Distinct faces are NEVER dropped just
    # for being "close" — that 0.25*base greedy drop is exactly what recessed box
    # faces (e.g. a box right face 50 mm from a neighbour vanished → staircase).
    eps_merge = max(2.0e-3, 0.01 * base_size)
    # A distinct face is only suppressed if keeping it would carve a
    # domain-spanning cell thinner than this (a true sliver / extreme aspect
    # ratio). base/8 keeps the 50 mm box-face gap but drops genuine slivers.
    sliver_min = base_size / 8.0

    for axis in ("x", "y", "z"):
        r_min, r_max = room_bounds[axis]

        # interior face planes strictly inside the room (only AT-bound excluded)
        interior = sorted(
            p for p in align_planes.get(axis, [])
            if (r_min + eps) < p < (r_max - eps)
        )

        # 1) collapse watertight-coincident duplicates to one representative
        reps: List[float] = []
        for p in interior:
            if not reps or (p - reps[-1]) > eps_merge:
                reps.append(p)

        # 2) anchor the room bounds, then keep every distinct face unless it would
        #    create a sub-`sliver_min` cell against the previous kept break or
        #    r_max. No trailing snap-to-r_max (that absorbed real faces).
        breaks: List[float] = [r_min]
        for p in reps:
            if (p - breaks[-1]) >= sliver_min and (r_max - p) >= sliver_min:
                breaks.append(p)
            # else: sub-sliver gap → drop (residual recess < base/8), never the
            # 0.25*base over-merge that lost whole box faces.
        breaks.append(r_max)

        actual_cs = (r_max - r_min) / max(1, round((r_max - r_min) / base_size))
        segments: List[Tuple[float, float, int]] = []
        segments.append((breaks[0] - actual_cs, breaks[0], 1))   # low padding
        for a, b in zip(breaks[:-1], breaks[1:]):
            n = max(1, round((b - a) / base_size))
            segments.append((a, b, n))
        segments.append((breaks[-1], breaks[-1] + actual_cs, 1))  # high padding

        result[axis] = segments

    return result


# ---------------------------------------------------------------------------
# Main runner class
# ---------------------------------------------------------------------------

_DOMAIN_PATCH_RE = re.compile(r"^domain_(?:x|y|z)(?:Min|Max)$")


def _exported_box_y_range(geometry: Dict[str, Any]) -> Optional[Tuple[float, float]]:
    """World-Y extent (bottom, top) of an exported box geometry, or None when it has no height.

    The box spans [0, W] x [0, H] x [0, D] from ``anchor``, scaled and then
    rotated by ``quaternion``, as the scene places it (the frontend preflight
    uses the same rule). A wall turned only about Y spans anchor.y up to
    anchor.y + H * scale.y.
    """
    try:
        size = geometry.get("size") or {}
        if size.get("H") is None:
            return None
        anchor_y = float((geometry.get("anchor") or {}).get("y") or 0.0)
        q = geometry.get("quaternion") or {}
        sc = geometry.get("scale") or {}
        qx, qy, qz = (float(q.get(k) or 0.0) for k in ("x", "y", "z"))
        qw = float(q["w"]) if q.get("w") is not None else 1.0
        ext = [
            float(size.get(k) or 0.0) * (float(sc[a]) if sc.get(a) is not None else 1.0)
            for k, a in (("W", "x"), ("H", "y"), ("D", "z"))
        ]
    except (TypeError, ValueError, AttributeError):
        return None
    ys = []
    for vx in (0.0, ext[0]):
        for vy in (0.0, ext[1]):
            for vz in (0.0, ext[2]):
                # v' = v + w t + q.xyz x t with t = 2 (q.xyz x v); only y is needed
                tx = 2.0 * (qy * vz - qz * vy)
                ty = 2.0 * (qz * vx - qx * vz)
                tz = 2.0 * (qx * vy - qy * vx)
                ys.append(vy + qw * ty + (qz * tx - qx * tz))
    return anchor_y + min(ys), anchor_y + max(ys)


def _exported_box_top_y(geometry: Dict[str, Any]) -> Optional[float]:
    """World-Y top of an exported box geometry, or None when it has no height."""
    span = _exported_box_y_range(geometry)
    return None if span is None else span[1]


def _room_floor_level(floors: Any) -> float:
    """The room floor level of an exported manifest.

    The lowest floor bound as the room's bottom (``roomBoundaryType`` "bottom"),
    as the editors take room.height from the highest floor bound as its top.
    Without one it is y=0, the level ``_ensure_watertight_room_enclosure``
    extrudes a missing enclosure from.
    """
    levels = []
    for floor in (floors.values() if isinstance(floors, dict) else ()):
        if not isinstance(floor, dict) or floor.get("roomBoundaryType") != "bottom":
            continue
        y = ((floor.get("geometry") or {}).get("anchor") or {}).get("y")
        if isinstance(y, (int, float)) and not isinstance(y, bool) and float("-inf") < y < float("inf"):
            levels.append(float(y))
    return min(levels) if levels else 0.0


def _domain_patches_with_faces(boundary: Path) -> List[str]:
    """The dcCartMesh bounding-box patches (domain_*) that hold faces in *boundary*.

    dcCartMesh names a face after a domain side only when a live cell sits
    against the mesh bounding box, i.e. when the fluid reached it: inside a
    closed room shell none does.
    """
    try:
        text = Path(boundary).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    found = []
    for m in re.finditer(r"^\s*(domain_(?:x|y|z)(?:Min|Max))\s*\{([^}]*)\}", text, re.MULTILINE):
        n_faces = re.search(r"\bnFaces\s+(\d+)\s*;", m.group(2))
        if n_faces and int(n_faces.group(1)) > 0:
            found.append(m.group(1))
    return found


class STLGenRunner:
    """Run the stlgen (direct STL) pipeline for a CFD case."""

    def __init__(self, case_dir: Path, cfdrun_path: Optional[Path] = None,
                 expected_cfdrun_sha256: Optional[str] = None, *,
                 input_snapshot: Optional[CfdrunSnapshot] = None) -> None:
        """Construct a runner for `case_dir`.

        `cfdrun_path` — explicit path to the `.cfdrun` manifest (JSON).
        If omitted, the runner globs `<case_dir>/model/*.cfdrun`:

        - exactly one `.cfdrun` found → used;
        - zero `.cfdrun` found → `FileNotFoundError` (no silent fallback —
          deliberate per the 2026-04-18 no-silent-fallback policy);
        - two or more `.cfdrun` files → `RuntimeError` asking the user to
          pass `cfdrun_path=` explicitly.
        
        With an explicit input_snapshot, its owned bytes and case/model
        logical artifact are authoritative. The runner does not discover or
        read a source artifact; physical STL and mesh checks remain required.
        """
        self.case_dir = Path(case_dir).resolve()
        self.mesh_dir = self.case_dir / "Mesh"
        self.model_dir = self.case_dir / "model"
        self.tri_surface_dir = self.mesh_dir / "constant" / "triSurface"

        self._input_snapshot = input_snapshot
        if input_snapshot is not None:
            if type(input_snapshot) is not CfdrunSnapshot:
                raise TypeError("input_snapshot must be an original owned CfdrunSnapshot")
            input_snapshot.require_context(self.case_dir, cfdrun_path)
            cfdrun_path = input_snapshot.logical_path
        elif cfdrun_path is None:
            candidates = sorted(self.model_dir.glob("*.cfdrun"))
            if len(candidates) == 0:
                raise FileNotFoundError(
                    f"no *.cfdrun file found in {self.model_dir}. Re-export "
                    f"from the STL UI (or POST /api/browser/export-cfdrun "
                    f"for the manifest — STL files are a separate re-export "
                    f"via /api/browser/export-stl), or pass cfdrun_path= "
                    f"explicitly."
                )
            if len(candidates) > 1:
                raise RuntimeError(
                    f"multiple *.cfdrun files in {self.model_dir}: "
                    f"{[c.name for c in candidates]}. Pass cfdrun_path= "
                    f"explicitly to disambiguate."
                )
            cfdrun_path = candidates[0]
        self.cfdrun_path = Path(cfdrun_path).resolve()
        if expected_cfdrun_sha256 is not None and not re.fullmatch(
                r"[0-9a-f]{64}", expected_cfdrun_sha256):
            raise ValueError("expected .cfdrun SHA-256 must be 64 lowercase hex characters")
        self._expected_cfdrun_sha256 = expected_cfdrun_sha256
        self._case_lock_enabled = True

        self._data: Optional[Dict[str, Any]] = None   # parsed .cfdrun manifest
        self._stl_solids: Dict[str, List[str]] = {}   # {filename: [solidName, …]}
        self._baffle_physics: Dict[str, Dict] = {}   # {sanitized_solid: {baffleType, porosity}}
        self._wall_baffle_zone_names: set = set()     # populated by _generate_create_baffles_dict()
        # In-memory bridge BC registry; sourced from the V2 manifest via
        # cfdrun_reader.resolve_run() (the ResolvedRun.bridge_registry) during
        # _prepare_runtime_metadata(). Replaces the old model/bridge_bc.json file
        # AND the deleted _build_bridge_bc() in-runner derivation (P3 T6).
        self._bridge_registry: Optional["BridgeBCRegistry"] = None
        # Materialised V2 .cfdrun manifest (ResolvedRun); set by _load(). The BC
        # registry is read from here; the MESH pipeline keeps reading self._data
        # (= the transitional meshGeometry v1 blob).
        self._resolved: Optional["cfdrun_reader.ResolvedRun"] = None

    @staticmethod
    def _validate_v2_execution_parity(raw: Dict[str, Any]) -> None:
        """Refuse ambiguous v2 execution budgets before launching OpenFOAM.

        V2 duplicates these controls in its root contract and the transitional
        meshGeometry block still consumed by this runner. Normal editor exports
        agree. A one-sided edit otherwise silently runs the old MPI rank count
        or solve horizon. Older manifests missing a field on either side retain
        their existing compatibility behavior.
        """
        blob = raw.get("meshGeometry") or {}
        fields = {
            "meshConfig": ("parallelMesh", "meshProcess", "maxGlobalCells"),
            "simulationConfig": ("parallelSolve", "solveProcess", "endTime",
                                 "writeInterval", "startFrom"),
        }
        for group, names in fields.items():
            root_config = raw.get(group) or {}
            active_config = blob.get(group) or {}
            for name in names:
                if (name in root_config and name in active_config and
                        root_config[name] != active_config[name]):
                    raise ValueError(
                        f".cfdrun {group}.{name}={root_config[name]!r} disagrees "
                        f"with active meshGeometry.{group}.{name}="
                        f"{active_config[name]!r}; re-export from the editor "
                        "or update both v2 copies before running OpenFOAM"
                    )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate_geometry(self) -> None:
        """Pre-flight check: ensure room.height is consistent with floor y-coordinates
        and that the room enclosure walls span the room from its floor to that height.

        Raises RuntimeError if any floor's anchor y exceeds room.height, which would
        cause the room enclosure walls to be shorter than the geometry, leading to
        unclosed mesh domains and baffle seed placement failures. Raises too when a
        room enclosure wall ends below room.height or starts above the room floor:
        the room shell is then open.
        """
        if self._data is None:
            self._load_cfd_json_only()
        room_height = self._data.get("room", {}).get("height", 0)
        _TOLERANCE = 1e-3  # allow sub-mm floating-point rounding from JS export
        for name, floor in self._data.get("floors", {}).items():
            y = floor.get("geometry", {}).get("anchor", {}).get("y", 0)
            if y > room_height + _TOLERANCE:
                raise RuntimeError(
                    f"floor '{name}' y={y} exceeds room.height={room_height}. "
                    f"The room enclosure (walls) only extends to y={room_height}, "
                    f"but this floor is placed above it. "
                    f"Update the room height in the 2D floor plan (房间 2D 平面设计 → 房间高度) "
                    f"to at least {y}."
                )

        # Room enclosure walls must span the room, from the room floor up to
        # room.height. A wall that stops below the ceiling, or stands lifted off
        # the floor, leaves an open band, and the fluid region leaks through it
        # into the mesh bounding box. dcCartMesh then meshes the room walls,
        # floor and ceiling as interior surfaces with fluid on both sides: they
        # get no boundary face, createBaffles drops their empty patches and only
        # the domain_* sides remain (Base604: walls 3.5 m under a 4.4 m ceiling).
        # Nothing else closes the gap, because the browser's room_wall.stl is the
        # geometry source of truth and is no longer re-extruded to room.height.
        # The room floor is the lowest floor bound as the room's bottom, else
        # y=0 (_room_floor_level). Interior walls (isRoomBoundary false) may stop
        # anywhere. A manifest exported before that flag existed has no key; its
        # walls are checked.
        if isinstance(room_height, (int, float)) and room_height > 0:
            floor_y = _room_floor_level(self._data.get("floors"))
            open_walls = []
            for name, wall in (self._data.get("walls") or {}).items():
                if not isinstance(wall, dict) or wall.get("isRoomBoundary") is False:
                    continue
                span = _exported_box_y_range(wall.get("geometry") or {})
                if span is None:
                    continue
                bottom, top = span
                gaps = []
                if bottom > floor_y + _TOLERANCE:
                    gaps.append(f"bottom y={bottom:.6g}")
                if top < room_height - _TOLERANCE:
                    gaps.append(f"top y={top:.6g}")
                if gaps:
                    open_walls.append(f"'{name}' {' '.join(gaps)}")
            if open_walls:
                raise RuntimeError(
                    f"room walls do not span the room from its floor y={floor_y:.6g} to "
                    f"room.height={room_height}: {', '.join(open_walls)}. "
                    f"The room enclosure is open: fluid leaks through the gap above or "
                    f"below these walls to the mesh bounding box, and the room walls, "
                    f"floor and ceiling get no boundary faces. Extend these walls down "
                    f"to the room floor and up to the room height "
                    f"(房间围护墙未覆盖从地面到房间高度的范围，请把墙延伸到地面和房间高度)."
                )

    def _load_cfd_json_only(self) -> None:
        """Load and validate the .cfdrun manifest without requiring STL files.

        Requires a V2 manifest (schemaVersion=2). The MESH pipeline reads
        self._data from the transitional meshGeometry block (P3 T6 — known
        spec §12 deviation), validated separately via the existing CFDExport.
        """
        if getattr(self, "_input_snapshot", None) is None and not self.cfdrun_path.exists():
            raise FileNotFoundError(f"cfdrun input file not found: {self.cfdrun_path}")
        payload = self._read_cfdrun_bytes()
        raw = json.loads(payload)
        if (requires_hybrid_air_gate(raw) or
                bool((raw.get("geometryExport") or {}).get("sourceSurfaces"))):
            loaded_sha = hashlib.sha256(payload).hexdigest()
            prior_sha = getattr(self, "_verified_source_cfdrun_sha256", None)
            if prior_sha is not None and loaded_sha != prior_sha:
                raise RuntimeError("source manifest changed between STL preflight and load")
            self._verified_source_cfdrun_sha256 = loaded_sha
        # 3 = v2 + the transient block and binding schedules (cfdrun v3, E7);
        # 4 = one simulationConfig, the root (E8a).
        if raw.get("schemaVersion") not in (2, 3, 4):
            raise ValueError(
                f"unsupported .cfdrun schemaVersion {raw.get('schemaVersion')!r}; "
                "re-export from the editor (V2 required)"
            )
        self._validate_v2_execution_parity(raw)
        from backend.aircooling.runner.cfdrun_schema import CFDExport
        from backend.aircooling.runner.derived_config import execution_blob
        validated = CFDExport.model_validate(execution_blob(raw))
        self._data = validated.model_dump(mode="json")
        self._cfdrun_v3_shadow_check(raw)

    def _cfdrun_v3_shadow_check(self, raw) -> None:
        """cfdrun V3 Stage-1 shadow compare (non-behavioural). When
        DCDT_CFDRUN_V3_SHADOW=1, log whether the backend-derived config equals
        the meshGeometry blob config the runner currently consumes. Never raises.
        """
        if os.environ.get("DCDT_CFDRUN_V3_SHADOW") != "1":
            return
        try:
            from .derived_config import shadow_drift
            drift = shadow_drift(raw)
            if drift:
                logger.warning(
                    f"cfdrun-v3 shadow drift in {drift}: derived config != meshGeometry blob"
                )
            else:
                logger.info("cfdrun-v3 shadow: derived config == blob (parity OK)")
        except Exception as exc:  # never break the pipeline in Stage 1
            logger.warning(f"cfdrun-v3 shadow check skipped: {exc}")

    def _preflight_case_path(self) -> None:
        """Refuse a case directory OpenFOAM cannot open.

        Every OpenFOAM executable validates its working directory through
        ``fileName::stripInvalid`` and treats whitespace as fatal (debug level
        2), so a case copied as "Foo copy" fails in the first mesher call with
        nothing but "invalid fileName" in the log. Say so up front, in words.
        """
        path = str(self.case_dir)
        bad = [c for c in path if c.isspace() or c in '"\'\';']
        if bad:
            raise RuntimeError(
                f"Case directory path contains a character OpenFOAM refuses "
                f"({bad[0]!r}): {path}\n案例目录路径含有 OpenFOAM 不接受的字符"
                f"（空格/引号/分号），请重命名目录（例如用下划线代替空格）后重试。"
            )

    @_case_locked
    def run_mesh_only(self) -> Path:
        """Generate mesh + complete OpenFOAM case in Mesh/ directory."""
        logger.info("=== STLGen: Mesh-Only Mode ===")
        self._preflight_case_path()
        self._verify_hybrid_source_stls()
        self._load()
        self._preflight_floor_openings()
        mc0 = self._data.get("meshConfig", {}) or {}
        if _resolve_mesh_engine(mc0) == "dcCart":
            self._check_dcCart_memory(
                int(mc0.get("meshProcess", 1) or 1) if mc0.get("parallelMesh", False) else 1
            )
        self.validate_geometry()
        self._preflight_partition_wall_thickness()
        self._ensure_watertight_room_enclosure()
        self._prepare_runtime_metadata()
        self._generate_mesh_dicts()
        # Dispatch to serial or parallel mesh pipeline based on meshConfig
        mc = self._data.get("meshConfig", {})
        parallel_mesh = mc.get("parallelMesh", False)
        n_procs = mc.get("meshProcess", 1)
        if _resolve_mesh_engine(mc) == "dcCart":
            n_cart = n_procs if parallel_mesh else 1
            logger.info(
                "Mesh engine: dcCart (cut-cell Cartesian, "
                + (f"{n_cart} MPI ranks)" if n_cart > 1 else "1 rank)")
            )
            self._run_dcCart_mesh_pipeline(n_cart)
        elif _resolve_mesh_engine(mc) == "meshgen":
            logger.info(
                "Mesh engine: meshgen (MeshGenerator cut-cell Cartesian, "
                "single process)")
            self._run_meshgen_pipeline()
        elif parallel_mesh and n_procs > 1:
            logger.info(f"Parallel mesh enabled: {n_procs} processes")
            self._run_parallel_mesh_pipeline(n_procs)
        else:
            logger.info("Serial mesh mode")
            self._run_serial_mesh_pipeline()
        # Generate complete case files in Mesh/. Mesh-only mode is tolerant
        # of fan_outlet BC issues (missing inlet patches → placeholder BC +
        # warning); the solve-path call sites keep strict=True so the real
        # error surfaces when the user tries to solve.
        # Required case preparation and the final quality receipt are part of
        # the mesh API job and remain inside its whole-job time ceiling.
        self._setup_complete_case_in_mesh(strict=False)
        self._record_hybrid_mesh_source()
        self._record_source_surface_mesh_source()
        self._record_air_mesh_quality()
        self._check_mesh_budget("complete mesh case preparation and quality")
        self._clear_mesh_budget()
        foam_file = self.mesh_dir / "case.foam"
        if not foam_file.exists():
            foam_file.touch()
        logger.info(f"Mesh complete. Complete case in {self.mesh_dir}")
        poly_mesh = self.mesh_dir / "constant" / "polyMesh"
        # A distributed mesh is the collection under Mesh/, never one rank's
        # polyMesh and never the nonexistent reconstructed path.
        return poly_mesh if (poly_mesh / "faces").exists() else self.mesh_dir

    @_case_locked
    def run_solve_only(self) -> Path:
        """Run solver using complete case in Mesh/ directory."""
        logger.info("=== STLGen: Solve-Only Mode ===")
        self._preflight_case_path()
        self._verify_source_stl_snapshot_before_solve()
        self._load()
        self._prepare_runtime_metadata()
        if requires_hybrid_air_gate(self._read_cfdrun_manifest()):
            # Case setup may normalize polyMesh/boundary types. Audit that
            # normalized mesh, then regenerate 0/T using the corrected q.
            self._setup_complete_case_in_mesh()
            self._record_air_mesh_quality()
            self._require_air_mesh_quality()
        self._apply_hybrid_air_gate()
        # Always regenerate case files (user may have changed settings)
        self._setup_complete_case_in_mesh()
        self._verify_source_surface_mesh_source()
        self._record_air_mesh_quality()
        self._require_air_mesh_quality()
        return self._run_solve_from_mesh()

    @_case_locked
    def run_simulation(self) -> Path:
        """Run mesh + solve."""
        logger.info("=== STLGen: Full Simulation Mode ===")
        self._preflight_case_path()
        self._verify_hybrid_source_stls()
        self._load()
        self._preflight_floor_openings()
        mc0 = self._data.get("meshConfig", {}) or {}
        if _resolve_mesh_engine(mc0) == "dcCart":
            self._check_dcCart_memory(
                int(mc0.get("meshProcess", 1) or 1) if mc0.get("parallelMesh", False) else 1
            )
        self.validate_geometry()
        self._preflight_partition_wall_thickness()
        self._prepare_runtime_metadata()
        self._generate_mesh_dicts()
        mc = self._data.get("meshConfig", {})
        parallel_mesh = mc.get("parallelMesh", False)
        n_procs = mc.get("meshProcess", 1)
        if _resolve_mesh_engine(mc) == "dcCart":
            n_cart = n_procs if parallel_mesh else 1
            logger.info(
                "Mesh engine: dcCart (cut-cell Cartesian, "
                + (f"{n_cart} MPI ranks)" if n_cart > 1 else "1 rank)")
            )
            self._run_dcCart_mesh_pipeline(n_cart)
        elif _resolve_mesh_engine(mc) == "meshgen":
            logger.info(
                "Mesh engine: meshgen (MeshGenerator cut-cell Cartesian, "
                "single process)")
            self._run_meshgen_pipeline()
        elif parallel_mesh and n_procs > 1:
            logger.info(f"Parallel mesh enabled: {n_procs} processes")
            self._run_parallel_mesh_pipeline(n_procs)
        else:
            logger.info("Serial mesh mode")
            self._run_serial_mesh_pipeline()
        if requires_hybrid_air_gate(self._read_cfdrun_manifest()):
            self._setup_complete_case_in_mesh()
            self._record_hybrid_mesh_source()
            self._record_air_mesh_quality()
            self._require_air_mesh_quality()
        self._apply_hybrid_air_gate()
        self._setup_complete_case_in_mesh()
        self._record_source_surface_mesh_source()
        self._record_air_mesh_quality()
        self._require_air_mesh_quality()
        return self._run_solve_from_mesh()

    def _verify_hybrid_source_stls(self) -> None:
        """Freeze declared source STL bytes before mesh preparation rewrites files."""
        # A prior job's semantic permission must never survive this preflight.
        self._point_repair_source_receipt = None
        payload = self._read_cfdrun_bytes()
        raw = json.loads(payload)
        hybrid = requires_hybrid_air_gate(raw)
        has_source_surfaces = bool((raw.get("geometryExport") or {}).get("sourceSurfaces"))
        if not hybrid and not has_source_surfaces:
            return
        from backend.aircooling.runner.stl_geometry_index import verify_exported_stl_hashes
        receipt = verify_exported_stl_hashes(raw, self.tri_surface_dir)
        pinned = hashlib.sha256(payload).hexdigest()
        if hashlib.sha256(self._read_cfdrun_bytes()).hexdigest() != pinned:
            raise RuntimeError("source manifest changed during STL verification")
        self._verified_source_cfdrun_sha256 = pinned
        # verify_exported_stl_hashes has just checked every declared original
        # file against these exact bytes. Derived STL files may change during
        # normal split/regroup; their current hashes are bound separately.
        self._point_repair_source_receipt = {
            "version": 1,
            "rawSha256": pinned,
            "verifiedFiles": {
                key: {"path": record["path"], "contentHash": record["contentHash"]}
                for key, record in raw["geometryExport"]["stlFiles"].items()
            },
        }
        self._source_stls_verified = True
        if hybrid:
            self._hybrid_source_stls_verified = True
        logger.info(f"Source STL identity checked: {receipt['file_count']} grouped files")

    def _source_cfdrun_bytes(self) -> bytes:
        """Read the explicit owned source, or retain the original file entry."""
        snapshot = getattr(self, "_input_snapshot", None)
        if snapshot is not None:
            snapshot.require_context(self.case_dir, self.cfdrun_path)
            return snapshot.read_bytes()
        return self.cfdrun_path.read_bytes()

    def _snapshot_kwargs(self) -> Dict[str, Any]:
        # Preserve legacy call signatures for file-mode consumers and fixtures.
        snapshot = getattr(self, "_input_snapshot", None)
        return {"input_snapshot": snapshot} if snapshot is not None else {}

    @contextmanager
    def _cfdrun_command_path(self):
        """Hold sealed RAM bytes for a path-only native child without spooling."""
        if getattr(self, "_input_snapshot", None) is None:
            yield self.cfdrun_path
            return
        if not hasattr(os, "memfd_create"):
            raise RuntimeError("snapshot native input requires Linux sealed memfd")
        import fcntl

        fd = os.memfd_create("dcdtuni-cfdrun", os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
        try:
            view = memoryview(self._read_cfdrun_bytes())
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise OSError("failed to write owned cfdrun memfd")
                view = view[written:]
            os.lseek(fd, 0, os.SEEK_SET)
            fcntl.fcntl(fd, fcntl.F_ADD_SEALS,
                        fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW |
                        fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL)
            # /proc/self would refer to the shell/child. Keep the owning
            # runner alive and this descriptor open through normal completion.
            yield Path(f"/proc/{os.getpid()}/fd/{fd}")
        finally:
            os.close(fd)

    def _read_cfdrun_bytes(self) -> bytes:
        """Reject a manifest edit during one locked execution or STL preflight."""
        payload = self._source_cfdrun_bytes()
        actual = hashlib.sha256(payload).hexdigest()
        execution = getattr(self, "_execution_cfdrun_sha256", None)
        if execution and actual != execution:
            raise RuntimeError(".cfdrun input identity changed during case execution")
        source = getattr(self, "_verified_source_cfdrun_sha256", None)
        if source and actual != source:
            raise RuntimeError("source manifest input identity changed after verification")
        return payload

    def _read_cfdrun_manifest(self) -> Dict[str, Any]:
        return json.loads(self._read_cfdrun_bytes())

    def _pinned_source_manifest(self) -> Dict[str, Any]:
        """Read only the exact manifest revision used for source verification."""
        pinned = getattr(self, "_verified_source_cfdrun_sha256", None)
        if not pinned:
            raise RuntimeError("source manifest input identity was not pinned")
        payload = self._read_cfdrun_bytes()
        if hashlib.sha256(payload).hexdigest() != pinned:
            raise RuntimeError("source manifest input identity changed after verification")
        return json.loads(payload)

    def _stl_snapshot_sha256(self) -> str:
        """Hash the post-mesh STL set that solve-only would otherwise reread."""
        digest = hashlib.sha256()
        tri = self.tri_surface_dir
        if not tri.is_dir():
            raise RuntimeError(f"source STL snapshot directory is missing: {tri}")
        for path in sorted(tri.glob("*.stl")):
            if not path.is_file() or path.is_symlink():
                raise RuntimeError(f"source STL snapshot has invalid file: {path}")
            name = path.name.encode("utf-8")
            digest.update(len(name).to_bytes(8, "big"))
            digest.update(name)
            digest.update(path.stat().st_size.to_bytes(8, "big"))
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
        return digest.hexdigest()

    def _verify_source_stl_snapshot_before_solve(self) -> None:
        """Refuse an intervening STL upload before solve-only mutates it."""
        raw = self._read_cfdrun_manifest()
        source = bool((raw.get("geometryExport") or {}).get("sourceSurfaces"))
        hybrid = requires_hybrid_air_gate(raw)
        if not source and not hybrid:
            return
        path = self.case_dir / ("hybrid-mesh-source.json" if hybrid else
                                "source-surface-mesh-source.json")
        if not path.is_file():
            raise RuntimeError(f"source STL snapshot receipt is missing: {path}")
        try:
            receipt = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("source STL snapshot receipt is invalid") from exc
        expected_format = ("hybrid-mesh-source/v2" if hybrid else
                           "source-surface-mesh-source/v2")
        expected = receipt.get("tri_surface_sha256") if isinstance(receipt, dict) else None
        if (not isinstance(receipt, dict) or receipt.get("format") != expected_format or
                not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected)):
            raise RuntimeError("source STL snapshot receipt is stale or invalid; remesh")
        if self._stl_snapshot_sha256() != expected:
            raise RuntimeError("source STL snapshot changed after mesh generation; remesh")
        from backend.aircooling.runner.openfoam_patch_area import mesh_identity_sha256
        fields = self._hybrid_source_fields(raw) if hybrid else self._ordinary_source_fields(raw)
        if (any(receipt.get(key) != value for key, value in fields.items()) or
                receipt.get("mesh_sha256") != mesh_identity_sha256(self.mesh_dir)):
            raise RuntimeError("source mesh receipt identity does not match current input and mesh")

    @staticmethod
    def _hybrid_source_fields(raw: Dict[str, Any]) -> Dict[str, Any]:
        source = raw.get("sourceProject") or {}
        geometry = raw.get("geometryExport") or {}
        files = geometry.get("stlFiles") or {}
        source_surfaces = geometry.get("sourceSurfaces") or {}
        return {
            "project_content_hash": source.get("contentHash"),
            "air_run_id": raw.get("runId"),
            "exported_stl_hashes": {key: value.get("contentHash") for key, value in sorted(files.items())},
            "source_surfaces_sha256": hashlib.sha256(json.dumps(
                source_surfaces, sort_keys=True, separators=(",", ":"),
                ensure_ascii=False).encode("utf-8")).hexdigest(),
        }

    def _record_hybrid_mesh_source(self) -> None:
        """Bind a newly generated mesh to the checked React STL upload."""
        raw = self._read_cfdrun_manifest()
        if not requires_hybrid_air_gate(raw):
            return
        if getattr(self, "_hybrid_source_stls_verified", False) is not True:
            raise RuntimeError("hybrid mesh lacks verified export-time STL input")
        raw = self._pinned_source_manifest()
        from backend.aircooling.runner.openfoam_patch_area import mesh_identity_sha256
        receipt = {"format": "hybrid-mesh-source/v2",
                   **self._hybrid_source_fields(raw),
                   "tri_surface_sha256": self._stl_snapshot_sha256(),
                   "mesh_sha256": mesh_identity_sha256(self.mesh_dir)}
        self._pinned_source_manifest()
        path = self.case_dir / "hybrid-mesh-source.json"
        temporary = self.case_dir / f"hybrid-mesh-source-{os.getpid()}.tmp"
        temporary.write_text(json.dumps(receipt, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        os.replace(temporary, path)

    @staticmethod
    def _needs_source_surface_mesh_receipt(raw: Dict[str, Any]) -> bool:
        """Hybrid runs already bind these identities in hybrid-mesh-source.json."""
        return (not requires_hybrid_air_gate(raw) and
                bool((raw.get("geometryExport") or {}).get("sourceSurfaces")))

    @classmethod
    def _ordinary_source_fields(cls, raw: Dict[str, Any]) -> Dict[str, Any]:
        """Include the file path-to-hash map as well as source-face ownership."""
        files = ((raw.get("geometryExport") or {}).get("stlFiles") or {})
        return {**cls._hybrid_source_fields(raw),
                "exported_stl_manifest_sha256": hashlib.sha256(json.dumps(
                    files, sort_keys=True, separators=(",", ":"),
                    ensure_ascii=False).encode("utf-8")).hexdigest()}

    def _record_source_surface_mesh_source(self) -> None:
        """Bind an ordinary source-aware air mesh to its checked export."""
        raw = self._read_cfdrun_manifest()
        if not self._needs_source_surface_mesh_receipt(raw):
            return
        if getattr(self, "_source_stls_verified", False) is not True:
            raise RuntimeError("source-surface mesh lacks verified export-time STL input")
        raw = self._pinned_source_manifest()
        from backend.aircooling.runner.openfoam_patch_area import mesh_identity_sha256
        receipt = {"format": "source-surface-mesh-source/v2",
                   **self._ordinary_source_fields(raw),
                   "tri_surface_sha256": self._stl_snapshot_sha256(),
                   "mesh_sha256": mesh_identity_sha256(self.mesh_dir)}
        self._pinned_source_manifest()
        path = self.case_dir / "source-surface-mesh-source.json"
        temporary = self.case_dir / f"source-surface-mesh-source-{os.getpid()}.tmp"
        temporary.write_text(json.dumps(receipt, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        os.replace(temporary, path)

    def _verify_source_surface_mesh_source(self) -> None:
        """Reject solve-only reuse when source UIDs or the mesh have changed."""
        raw = self._read_cfdrun_manifest()
        if not self._needs_source_surface_mesh_receipt(raw):
            return
        raw = self._pinned_source_manifest()
        path = self.case_dir / "source-surface-mesh-source.json"
        if not path.is_file():
            raise RuntimeError("source-surface solve requires source-surface-mesh-source.json receipt")
        from backend.aircooling.runner.openfoam_patch_area import mesh_identity_sha256
        receipt = json.loads(path.read_text(encoding="utf-8"))
        if (receipt.get("format") != "source-surface-mesh-source/v2" or
                any(receipt.get(key) != value for key, value in self._ordinary_source_fields(raw).items()) or
                receipt.get("mesh_sha256") != mesh_identity_sha256(self.mesh_dir)):
            raise RuntimeError("source-surface mesh source identity does not match the .cfdrun and current mesh")

    def _record_air_mesh_quality(self) -> None:
        """Bind full topology/geometry quality for every air mesh and reuse."""
        raw = self._read_cfdrun_manifest()
        source_aware = bool((raw.get("geometryExport") or {}).get("sourceSurfaces")) or requires_hybrid_air_gate(raw)
        if source_aware:
            self._pinned_source_manifest()
        from backend.aircooling.runner.air_mesh_quality_receipt import (
            record_prechecked_air_mesh_quality, verify_air_mesh_quality_receipt,
        )
        try:
            receipt = verify_air_mesh_quality_receipt(
                self.case_dir / "mesh-quality-receipt.json", self.mesh_dir, self.cfdrun_path,
                **self._snapshot_kwargs(),
            )
        except ValueError:
            receipt = None
        if receipt is None:
            checked = getattr(self, "_prechecked_air_quality", None)
            if checked is not None:
                try:
                    receipt = record_prechecked_air_mesh_quality(
                        self.mesh_dir, self.cfdrun_path, **checked, **self._snapshot_kwargs(),
                    )
                except ValueError:
                    # Cleanup or a new export may have changed exact inputs.
                    # Such a stale capture cannot authorize the current mesh.
                    receipt = None
            if receipt is None:
                from backend.aircooling.runner.mesh_layout import mesh_directories
                roots = mesh_directories(self.mesh_dir)
                if not roots:
                    raise RuntimeError("air solve requires a complete OpenFOAM mesh")
                parallel = roots[0] != self.mesh_dir / "constant" / "polyMesh"
                command = "checkMesh -allTopology -allGeometry"
                if parallel:
                    command = f"mpirun -np {len(roots)} {command} -parallel"
                output = self._run_cmd_capture(command, self.mesh_dir)
                for line in output.splitlines():
                    if line.strip():
                        logger.info(line.rstrip())
                receipt = record_prechecked_air_mesh_quality(
                    self.mesh_dir, self.cfdrun_path, **self._prechecked_air_quality,
                    **self._snapshot_kwargs(),
                )
        if source_aware:
            try:
                self._pinned_source_manifest()
                if receipt["cfdrunSha256"] != self._verified_source_cfdrun_sha256:
                    raise RuntimeError("source manifest changed during strict mesh quality check")
            except RuntimeError:
                (self.case_dir / "mesh-quality-receipt.json").unlink(missing_ok=True)
                raise
        logger.info(
            f"Strict air mesh quality: {receipt['status']} "
            f"({receipt['failedChecks']} failed checks), mesh={receipt['meshSha256']}"
        )

    def _require_air_mesh_quality(self) -> None:
        """Reject every air solve unless this exact mesh/input was accepted."""
        raw = self._read_cfdrun_manifest()
        source_aware = bool((raw.get("geometryExport") or {}).get("sourceSurfaces")) or requires_hybrid_air_gate(raw)
        if source_aware:
            self._pinned_source_manifest()
        from backend.aircooling.runner.air_mesh_quality_receipt import verify_air_mesh_quality_receipt
        try:
            receipt = verify_air_mesh_quality_receipt(
                self.case_dir / "mesh-quality-receipt.json", self.mesh_dir,
                self.cfdrun_path, require_accepted=True, **self._snapshot_kwargs(),
            )
        except ValueError as exc:
            # Source-aware callers must have recorded quality after their
            # separate immutable STL/source receipt gate. Do not silently
            # replace that authorization following a source/mesh mutation.
            if source_aware:
                raise RuntimeError(f"source-aware air solve requires accepted strict mesh quality: {exc}") from exc
            self._record_air_mesh_quality()
            try:
                receipt = verify_air_mesh_quality_receipt(
                    self.case_dir / "mesh-quality-receipt.json", self.mesh_dir,
                    self.cfdrun_path, require_accepted=True, **self._snapshot_kwargs(),
                )
            except ValueError as exc:
                raise RuntimeError(f"air solve requires accepted strict mesh quality: {exc}") from exc
        self._accepted_air_mesh_sha = receipt["meshSha256"]
        self._accepted_air_cfdrun_sha = receipt["cfdrunSha256"]

    def _apply_hybrid_air_gate(self) -> None:
        """Block a hybrid solve until this exact mesh is checked and heat-balanced."""
        raw = self._read_cfdrun_manifest()
        ledger_path = self.case_dir / "heat-ledger.json"
        hybrid_run = requires_hybrid_air_gate(raw)
        if not hybrid_run and not ledger_path.exists():
            return
        if not hybrid_run or not ledger_path.is_file():
            raise RuntimeError("hybrid air solve requires a matching heat-ledger.json sidecar")
        from backend.aircooling.runner.hybrid_air_heat_handoff import finalize_hybrid_air_heat_for_mesh
        from backend.aircooling.runner import cfdrun_reader
        from backend.aircooling.runner.openfoam_patch_area import mesh_identity_sha256
        import sys

        source_path = self.case_dir / "hybrid-mesh-source.json"
        if not source_path.is_file():
            raise RuntimeError("hybrid solve requires hybrid-mesh-source.json from verified mesh generation")
        source_receipt = json.loads(source_path.read_text(encoding="utf-8"))
        if (source_receipt.get("format") != "hybrid-mesh-source/v2" or
                any(source_receipt.get(key) != value for key, value in self._hybrid_source_fields(raw).items()) or
                source_receipt.get("mesh_sha256") != mesh_identity_sha256(self.mesh_dir)):
            raise RuntimeError("hybrid mesh source identity does not match the .cfdrun and current mesh")

        prefix = Path(sys.prefix) / "bin"
        checkmesh = prefix / "checkMesh"
        postprocess = prefix / "postProcess"
        adjusted, verified = finalize_hybrid_air_heat_for_mesh(
            raw, json.loads(ledger_path.read_text(encoding="utf-8")), self.mesh_dir,
            checkmesh_binary=str(checkmesh) if checkmesh.is_file() else "checkMesh",
            postprocess_binary=str(postprocess) if postprocess.is_file() else "postProcess",
        )
        mesh_hash = verified["mesh_sha256"]
        final_manifest = self.case_dir / f"hybrid-mesh-{mesh_hash[:16]}.cfdrun"
        final_ledger = self.case_dir / f"hybrid-mesh-{mesh_hash[:16]}-ledger.json"
        for path, value in ((final_manifest, adjusted), (final_ledger, verified)):
            serialized = json.dumps(value, indent=2, allow_nan=False) + "\n"
            if path.exists():
                if path.read_text(encoding="utf-8") != serialized:
                    raise RuntimeError(f"hybrid mesh artifact already exists with different content: {path}")
            else:
                path.write_text(serialized, encoding="utf-8")
        original_snapshot = getattr(self, "_input_snapshot", None)
        derived_snapshot = None
        if original_snapshot is not None:
            derived_snapshot = HybridCfdrunSnapshot(
                (json.dumps(adjusted, indent=2, allow_nan=False) + "\n").encode("utf-8"),
                self.case_dir, final_manifest, parent_input=original_snapshot,
                mesh_sha256=mesh_hash,
                stl_batch_sha256=original_snapshot.stl_batch_sha256,
            )
        self._hybrid_input_snapshot = derived_snapshot
        self._resolved = cfdrun_reader.resolve_run(
            self.case_dir, cfdrun_path=final_manifest, resolve_stl=False,
            **({"input_snapshot": derived_snapshot} if derived_snapshot is not None else {}))
        self._bridge_registry = self._resolved.bridge_registry
        self._data["boundaryBindings"] = adjusted["boundaryBindings"]
        self._data["geometryExport"] = adjusted["geometryExport"]
        self._hybrid_mesh_sha = mesh_hash
        self._hybrid_final_manifest = final_manifest
        self._hybrid_final_ledger = final_ledger
        logger.info(f"Hybrid air gate passed: patch area receipt and exact mesh heat balance {mesh_hash}")

    def _prepare_runtime_metadata(self) -> None:
        """Refresh STLGen runtime metadata without generating config.toml/room.json."""
        self._cleanup_stale_compat_files()
        # P3 T6: the BC registry now comes from the V2 manifest (ResolvedRun),
        # NOT from the deleted in-runner _build_bridge_bc() derivation. _load()
        # populates self._resolved; guard defensively for any path reaching here
        # without a full _load().
        if self._resolved is None:
            self._resolved = cfdrun_reader.resolve_run(
                self.case_dir,
                cfdrun_path=self.cfdrun_path,
                resolve_stl=False, **self._snapshot_kwargs(),
            )
        self._bridge_registry = self._resolved.bridge_registry
        from collections import Counter
        counts = Counter(spec.bc_type for _, spec in self._bridge_registry.items())
        summary = " | ".join(f"{n} {bc}" for bc, n in sorted(counts.items()))
        logger.info(
            f"Loaded BridgeBCRegistry from V2 manifest: "
            f"{len(self._bridge_registry)} patches | {summary}"
        )

    def _cleanup_stale_compat_files(self) -> None:
        """Remove old STLGen-generated compatibility files from previous runs."""
        config_path = self.case_dir / "config.toml"
        if config_path.exists():
            try:
                config_text = config_path.read_text(encoding="utf-8")
            except Exception:
                config_text = ""
            if config_text.startswith("# Auto-generated by STLGenRunner from"):
                config_path.unlink()
                logger.info(f"Removed stale STLGen compatibility file: {config_path}")

        room_json_path = self.model_dir / "room.json"
        if room_json_path.exists():
            try:
                room_data = json.loads(room_json_path.read_text(encoding="utf-8"))
            except Exception:
                room_data = {}
            meta = room_data.get("meta", {}) if isinstance(room_data, dict) else {}
            if meta.get("name") == "stlgen_stub":
                room_json_path.unlink()
                logger.info(f"Removed stale STLGen compatibility file: {room_json_path}")

        # Delete legacy bridge_bc.json (now in-memory, never written to disk).
        # Safe to remove: if the user rolls back to pre-refactor main, the next
        # run regenerates it from scratch — it is a derived artifact, not an
        # input file.
        stale_bridge = self.model_dir / "bridge_bc.json"
        if stale_bridge.exists():
            stale_bridge.unlink()
            logger.info(f"Removed stale bridge_bc.json (now in-memory): {stale_bridge}")

    def _compute_patch_geometry(self) -> Dict[str, PatchGeometry]:
        """Compute area, boundary perimeter, and bbox for each STL solid.

        Returns dict mapping sanitized solid name → PatchGeometry.
        Used by FieldGenerator to derive physically-grounded k/epsilon
        inlet BCs (rectangular hydraulic diameter D_h = 4A/P).
        """
        geometries: Dict[str, PatchGeometry] = {}
        for stl_file in sorted(self.tri_surface_dir.glob("*.stl")):
            if is_editor_only_stl(stl_file.name):
                # Editor-only export bucket (server interface faces, no fluid
                # on either side): it yields no mesh patch, and its solids must
                # not shadow a real patch's geometry — this map is keyed by
                # solid name and later files win.
                continue
            try:
                solids = _read_ascii_stl_triangles(stl_file)
            except Exception as exc:
                logger.warning(
                    f"Could not read {stl_file.name} for patch geometry: {exc}"
                )
                continue
            for solid_name, triangles in solids.items():
                if not triangles:
                    continue
                total_area = 0.0
                xs: List[float] = []
                ys: List[float] = []
                zs: List[float] = []
                for tri in triangles:
                    a, _ = _triangle_area_and_normal(tri)
                    if a > _GEOM_EPS:
                        total_area += a
                    for v in tri:
                        xs.append(v[0])
                        ys.append(v[1])
                        zs.append(v[2])
                if total_area <= _GEOM_EPS:
                    continue
                perimeter = _compute_boundary_perimeter(triangles)
                bbox = (
                    max(xs) - min(xs),
                    max(ys) - min(ys),
                    max(zs) - min(zs),
                )
                geometries[_sanitize(solid_name)] = PatchGeometry(
                    area=total_area,
                    perimeter=perimeter,
                    bbox_dims=bbox,
                )
        return geometries

    def _build_field_generator(self) -> FieldGenerator:
        """Build a field generator for STLGen that does not depend on CaseConfig.

        Injects the in-memory bridge registry (built earlier in
        _prepare_runtime_metadata()) so FieldGenerator can render 0/ fields
        without touching disk for BC lookups.
        """
        resolved = getattr(self, "_resolved", None)
        return FieldGenerator(
            case_dir=self.case_dir,
            simulation_config=start_simulation_config(
                self._data.get("simulationConfig", {}),
                resolved.transient if resolved is not None else None),
            room_config={},
            patch_geometry=self._compute_patch_geometry(),
            bridge_registry=self._bridge_registry,
        )

    def _extract_sensor_points(self) -> List[Dict[str, float]]:
        """Return STLGen sensor points in controlDict probe format."""
        probes = []
        for sensor in self._data.get("sensorPoints", []):
            if not isinstance(sensor, dict):
                continue
            if "x" not in sensor or "y" not in sensor or "z" not in sensor:
                continue
            probes.append(
                {
                    "x": float(sensor["x"]),
                    "y": float(sensor["y"]),
                    "z": float(sensor["z"]),
                }
            )
        return probes

    def _setup_complete_case_in_mesh(
        self, *, strict: bool = True, prefer_partitions: bool = False,
    ) -> None:
        """Generate case files in Mesh/ EXCEPT controlDict and fvSolution.

        Args:
            strict: When True (default, solve-path), raise on BC conditions
                that cannot be built (e.g. fan_outlet's paired inlet dropped
                by snappy). When False (mesh-path), emit placeholder BCs so
                the mesh pipeline completes; the solve gate will re-raise.

            prefer_partitions: Generate the finalized MPI solver fields even
                when a serial mesh is retained for result reconstruction.

        controlDict and fvSolution are generated at solve-time in the run
        directory (log/<timestamp>/) so the user can adjust solver parameters
        between mesh generation and solve execution.

        After this, Mesh/ contains:
        - constant/polyMesh or processorN/constant/polyMesh (from mesh generation)
        - constant/g, transportProperties, turbulenceProperties, thermophysicalProperties
        - system/fvSchemes, fvOptions
        - 0/U, 0/T, 0/p_rgh, p, nut, k, epsilon in each mesh case
        """
        from backend.aircooling.runner.mesh_layout import mesh_directories, processor_mesh_dirs

        mesh_dir = self.mesh_dir
        meshes = (processor_mesh_dirs(mesh_dir) if prefer_partitions
                  else mesh_directories(mesh_dir))
        mesh_cases = [path.parents[1] for path in meshes]
        if not mesh_cases:
            raise RuntimeError(f"No complete serial or partitioned mesh found in {mesh_dir}")
        partitions = mesh_cases if mesh_cases != [mesh_dir] else []
        patch_lists = []
        global_physical_patches: Dict[str, str] = {}
        for mesh_case in mesh_cases:
            part_boundary = mesh_case / "constant" / "polyMesh" / "boundary"
            fix_cyclic_match_tolerance(part_boundary)
            self._ensure_wall_baffles_are_wall_type(part_boundary)
            patches = read_boundary_patches(part_boundary)
            patch_lists.append((mesh_case, part_boundary, patches))
            if partitions:
                # A patch may have no faces on this rank while its paired fan
                # inlet lives elsewhere. Only a globally absent inlet is an
                # error. Keep local zero-face BCs for MPI collective evaluation.
                face_counts = {
                    name: int(count)
                    for name, count in re.findall(
                        r"([A-Za-z0-9_]+)\s*\{[^{}]*?\bnFaces\s+(\d+)\s*;",
                        part_boundary.read_text(encoding="utf-8"),
                    )
                }
                for name, patch_type in patches:
                    if not patch_type.lower().startswith("processor") and face_counts.get(name, 0) > 0:
                        global_physical_patches[name] = patch_type

        field_generator = self._build_field_generator()
        global_patches = list(global_physical_patches.items()) if partitions else None
        wall_fn_patches: Optional[Set[str]] = None
        for mesh_case, part_boundary, patches in patch_lists:
            if partitions:
                field_generator.generate_fields(
                    mesh_case, patches, strict=strict, global_patches=global_patches,
                )
            else:
                field_generator.generate_fields(mesh_case, patches, strict=strict)
            wall_fn_patches = self._reconcile_boundary_wall_types(
                part_boundary, mesh_case, wall_fn_patches
            )
        logger.info(
            f"Generated initial fields for {len(partitions)} retained partitions"
            if partitions else f"Generated field files in {mesh_dir / '0'}"
        )

        # 3. Generate system/ files (fvOptions, fvSchemes, constant/ files)
        #    NOTE: controlDict and fvSolution are NOT generated here — they are
        #    generated at solve-time via _generate_solver_dicts()
        # 2026-04-17 architecture change: rack through-zones are no longer
        # modeled as cellZones + DarcyForchheimer porous source. They are pure
        # fluid regions; pressure drop is provided by the perforated rack doors
        # (createBaffles). Only partition-hole porous sources remain in fvOptions.
        fvoptions_zones = self._build_partition_hole_fvoptions_config()
        generate_fv_options(mesh_dir, fvoptions_zones)
        sc = self._data.get("simulationConfig", {})
        copy_system_files_for_solver(
            mesh_dir,
            physical_properties=sc.get("physical_properties", {}),
            gravity=sc.get("gravity", {}),
            room_temperature_c=float(sc.get("roomTemperature", None) or 22.0),
            schemes_config=sc.get("schemes", {}),
        )
        if prefer_partitions:
            # The processorCyclic fallback can return before decompose's copy.
            # Both stages must load the constants just generated for this run.
            for mesh_case in partitions:
                for name in ("g", "transportProperties", "turbulenceProperties", "thermophysicalProperties"):
                    source = mesh_dir / "constant" / name
                    if source.is_file():
                        shutil.copy2(source, mesh_case / "constant" / name)
        logger.info("Generated system files (fvSchemes, fvOptions, constant/)")

        # Serial meshes retain the legacy cleanup. Partitioned dcCart meshes
        # are the authoritative mesh and must survive setup for direct solve.
        if not partitions:
            self._clean_processor_dirs(mesh_dir)
        for mesh_case in [mesh_dir, *partitions]:
            for artifact in ["cellLevel", "pointLevel"]:
                (mesh_case / "0" / artifact).unlink(missing_ok=True)
            for entry in mesh_case.iterdir():
                if entry.is_dir() and entry.name.replace(".", "", 1).isdigit() and entry.name != "0":
                    shutil.rmtree(entry, ignore_errors=True)
                    logger.info(f"Removed stale time directory: {entry}")

    def _generate_solver_dicts(self, run_dir: Path) -> None:
        """Generate controlDict and fvSolution in the given run directory.

        Called at solve-time (after copying Mesh/ to log/<timestamp>/) so the
        user's latest solver settings are always used.
        """
        sc = self._data.get("simulationConfig", {})
        solver_type = sc.get("solverType", "steady") or "steady"
        # Tables are written back into every time directory; keep a (t − ε)
        # row distinct from t there (see fields.ensure_table_write_precision).
        registry = getattr(self, "_bridge_registry", None)
        has_schedules = bool(registry) and any(
            getattr(spec, "timeVarying", None) for _, spec in registry.items())
        sc = ensure_table_write_precision(sc, has_schedules=has_schedules)

        # Generate controlDict
        monitoring = sc.get("monitoring", {})
        monitor_patches = [p["patch"] for p in monitoring.get("serverTemperature", [])]
        monitor_patches += [p["patch"] for p in monitoring.get("acuReturnTemperature", [])]
        generate_control_dict(
            run_dir,
            sc,
            probes=self._extract_sensor_points(),
            monitor_patches=monitor_patches,
            transient_events=time_events_from_registry(registry),
        )

        # Generate fvSolution with user-specified residual thresholds
        residual_control = sc.get("residualControl", {})
        system_dir = run_dir / "system"
        system_dir.mkdir(parents=True, exist_ok=True)

        generate_fv_solution(
            system_dir,
            residual_control=residual_control,
            relaxation_factors=sc.get("relaxationFactors", {}),
            n_non_ortho_correctors=int(sc.get("nNonOrthogonalCorrectors", 0)),
            solver_type=solver_type,
            n_outer_correctors=int(sc.get("nOuterCorrectors", 1)),
            n_correctors=int(sc.get("nCorrectors", 2)),
            transient_residual_control=sc.get("transientResidualControl"),
            pressure_linear_solver=sc.get("pressureLinearSolver"),
        )

        # Regenerate fvSchemes at solve-time (user may have changed preset)
        schemes_config = dict(sc.get("schemes", {}))
        schemes_config.setdefault("ddtScheme", sc.get("ddtScheme", "Euler"))
        schemes_config.setdefault("crankNicolsonCoeff", sc.get("crankNicolsonCoeff", 0.9))
        generate_fv_schemes(system_dir, schemes_config, solver_type=solver_type)

        logger.info(f"Generated solver dicts (controlDict, fvSolution, fvSchemes) in {run_dir / 'system'}")

    @staticmethod
    def _reconcile_boundary_wall_types(
        boundary_path: Path,
        case_dir: Path,
        wall_fn_patches: Optional[Set[str]] = None,
    ) -> Set[str]:
        """Ensure boundary file patch types are consistent with field BCs.

        If a patch has wall-function BCs (nutkWallFunction, epsilonWallFunction,
        kqRWallFunction, omegaWallFunction) in ANY 0/ field file, the boundary
        file must declare it as ``type wall``.  Otherwise the solver crashes with
        "Invalid wall function specification — Current patch type is patch".

        This is a safety net: the primary fix is in FieldGenerator which now
        avoids assigning wall functions to flow patches.  But if for any reason
        a mismatch still occurs (e.g. custom patches, future code changes), this
        step corrects the boundary file.

        ``wall_fn_patches`` lets a partitioned run scan its field files once
        instead of once per rank. Every rank's 0/ files come from the same
        FieldGenerator call with the same ``global_patches``, so a physical
        patch carries the same BC on all of them; the only per-rank patches are
        ``processor*``, which are ``type processor`` and are never rewritten
        here. The set is returned so the caller can hand it to the next rank --
        on Base604 that is 0.58 s x 16 ranks of re-reading 7 MB of field files.
        """
        import re as _re

        WALL_FN_RE = _re.compile(
            r'(?:compressible::)?'
            r'(?:nutkWallFunction|nutk?Wall[Ff]unction|'
            r'epsilonWallFunction|kqRWallFunction|omegaWallFunction|'
            r'alphatWallFunction|alphatJayatillekeWallFunction)',
        )

        patch_name_re = _re.compile(r'^[ \t]+([^\s{};]+)[ \t]*$')
        patch_type_re = _re.compile(r'^[ \t]*type[ \t]+(?P<kind>[^\s;]+)[ \t]*;')

        def patch_type_lines(lines):
            """Yield first-entry patch types with one linear pass.

            Field and boundary writers place the name, opening brace and type
            on separate lines. Limit whitespace handling to each line: nested
            whitespace expressions over whole files can backtrack across thousands
            of patches, particularly when their first BC is not a wall function.
            """
            name = None
            opened = False
            for index, line in enumerate(lines):
                stripped = line.strip()
                if not stripped:
                    continue
                if name is not None:
                    if not opened and stripped == "{":
                        opened = True
                        continue
                    if opened:
                        match = patch_type_re.match(line)
                        if match:
                            yield name, index, match
                    name = None
                    opened = False
                match = patch_name_re.match(line.rstrip("\r\n"))
                if match:
                    name = match.group(1)

        # Collect all patches that receive wall-function BCs in any field file
        cached = wall_fn_patches
        wall_fn_patches = set() if cached is None else set(cached)
        field_dir = case_dir / "0"
        if cached is None:
            if not field_dir.is_dir():
                return wall_fn_patches

            for field_file in field_dir.iterdir():
                if not field_file.is_file():
                    continue
                try:
                    content = field_file.read_text(encoding="utf-8")
                except Exception:
                    continue

                for name, _, match in patch_type_lines(content.splitlines()):
                    if WALL_FN_RE.fullmatch(match.group("kind")):
                        wall_fn_patches.add(name)

        if not wall_fn_patches:
            return wall_fn_patches

        # Read the boundary file and fix patch types
        if not boundary_path.exists():
            return wall_fn_patches
        boundary_lines = boundary_path.read_text(encoding="utf-8").splitlines(keepends=True)

        fixed_count = 0
        for name, index, match in patch_type_lines(boundary_lines):
            if name in wall_fn_patches and match.group("kind") == "patch":
                line = boundary_lines[index]
                boundary_lines[index] = line[:match.start("kind")] + "wall" + line[match.end("kind"):]
                fixed_count += 1

        if fixed_count > 0:
            boundary_path.write_text("".join(boundary_lines), encoding="utf-8")
            logger.info(
                f"Reconciled boundary types: changed {fixed_count} patches "
                f"from 'type patch' to 'type wall' to match wall-function BCs"
            )

    @staticmethod
    def _partitions_have_processor_cyclic(case_dir: Path) -> bool:
        """True when any partition carries a processorCyclic patch.

        dcCartMesh writes those for baffle master/slave pairs a rank boundary
        separates: one side per rank, each referring back to its local half of
        the cyclic pair.
        """
        for boundary in sorted(case_dir.glob("processor*/constant/polyMesh/boundary")):
            try:
                if "processorCyclic" in boundary.read_text(encoding="utf-8", errors="ignore"):
                    return True
            except OSError:
                continue
        return False

    def _run_steady_start(self, run_dir: Path, parallel_solve: bool, n_procs: int,
                          retain_partition_results: bool) -> Dict[str, Any]:
        """Converge SIMPLE and seed PIMPLE on one finalized mesh layout.

        Determine the actual solver partition count before generating and
        backing up the transient initial fields. No repartitioning is allowed
        after those fields have been seeded with the conservative steady phi.
        """
        import shlex
        from backend.aircooling.runner.bridge_bc import BridgeBCRegistry
        from backend.aircooling.runner.mesh_layout import processor_mesh_dirs
        from backend.aircooling.runner.openfoam_patch_area import mesh_identity_sha256, require_nonempty_mesh
        from backend.aircooling.runner.transient_start import (
            latest_time_dir, require_steady_convergence, seed_internal_fields,
        )

        backups: Dict[Path, Path] = {}
        backup_root = run_dir / ".steady-start-backup"
        backup_root_owned = False
        saved_mesh_dir, saved_registry = self.mesh_dir, self._bridge_registry
        sc = self._data.setdefault("simulationConfig", {})
        saved_sc = copy.deepcopy(sc)
        iterations = int(saved_sc.get("endTime") or 1000)
        succeeded = False
        try:
            self.mesh_dir = run_dir
            requested_n_procs = n_procs if parallel_solve and n_procs > 1 else 1
            actual_n_procs = self._decompose_for_solve(run_dir, requested_n_procs)
            if actual_n_procs < 1:
                raise RuntimeError("steady start prepared an invalid solver process count")
            prefer_partitions = actual_n_procs > 1
            cases = ([mesh.parents[1] for mesh in processor_mesh_dirs(run_dir)]
                     if prefer_partitions else [run_dir])
            if len(cases) != actual_n_procs:
                raise RuntimeError("steady start did not prepare every final mesh partition")
            require_nonempty_mesh(run_dir, prefer_partitions=prefer_partitions)

            # A reconstructed root can remain alongside the final processors
            # for later reconstructPar. Generate transient BCs on the actual
            # solver meshes, without the normal serial-root preference.
            self._setup_complete_case_in_mesh(
                strict=True, prefer_partitions=prefer_partitions,
            )
            mesh_identities = {
                str(case.relative_to(run_dir)) or ".": mesh_identity_sha256(case)
                for case in cases
            }

            def require_same_layout() -> None:
                current = ([mesh.parents[1] for mesh in processor_mesh_dirs(run_dir)]
                           if prefer_partitions else [run_dir])
                if current != cases:
                    raise RuntimeError("steady start mesh partition layout changed")
                identities = {
                    str(case.relative_to(run_dir)) or ".": mesh_identity_sha256(case)
                    for case in current
                }
                if identities != mesh_identities:
                    raise RuntimeError("steady start mesh identity changed between solver stages")

            backup_root.mkdir()
            backup_root_owned = True
            for case in cases:
                if not all((case / "0" / name).is_file() for name in ("T", "U")):
                    raise RuntimeError(f"steady start initial fields are missing in {case}")
                backup = backup_root / case.relative_to(run_dir) / "0"
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(case / "0", backup)
                backups[case] = backup

            steady_registry = BridgeBCRegistry()
            for name, spec in saved_registry.items():
                steady_registry.register(name, spec.model_copy(update={"timeVarying": {}}))
            try:
                self._bridge_registry = steady_registry
                self._setup_complete_case_in_mesh(
                    strict=True, prefer_partitions=prefer_partitions,
                )
                require_same_layout()
                sc.update(solverType="steady", endTime=iterations, writeInterval=iterations)
                self._generate_solver_dicts(run_dir)
                steady_solver = self._resolve_solver_binary("buoyantBoussinesqSimpleFoam_new")
                steady_log = run_dir / "log.steady_start"
                steady_log.unlink(missing_ok=True)
                if prefer_partitions:
                    self._run_cmd(f"mpirun -np {actual_n_procs} {steady_solver} -parallel", run_dir)
                    shutil.copy2(run_dir / "log.mpi_solver", steady_log)
                else:
                    self._run_cmd(
                        f"set -o pipefail && {steady_solver} 2>&1 | tee {shlex.quote(str(steady_log))}",
                        run_dir,
                    )
                convergence = require_steady_convergence(steady_log)
                require_same_layout()
                if prefer_partitions and not retain_partition_results:
                    self._run_cmd("reconstructPar -latestTime", run_dir)
                    require_same_layout()
            finally:
                self._bridge_registry = saved_registry
                sc.clear()
                sc.update(saved_sc)

            steady_times = {case: latest_time_dir(case) for case in cases}
            if any(time_dir is None for time_dir in steady_times.values()):
                raise RuntimeError("the steady start stage wrote no result for a final mesh partition")
            if len({float(time_dir.name) for time_dir in steady_times.values()}) != 1:
                raise RuntimeError("steady start partitions do not share one result time")
            report: Dict[str, Any] = {
                "iterations_limit": iterations, "convergence": convergence,
                "actual_n_procs": actual_n_procs, "mesh_identities": mesh_identities,
                "mesh_layout_sha256": hashlib.sha256(
                    json.dumps(mesh_identities, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest(),
                "cases": {},
            }
            for case, backup in backups.items():
                steady = steady_times[case]
                # Retain rollback data until every rank has seeded successfully.
                shutil.rmtree(case / "0", ignore_errors=True)
                shutil.copytree(backup, case / "0")
                report["cases"][str(case.relative_to(run_dir)) or "."] = {
                    "steady_time": steady.name, "seeded": seed_internal_fields(steady, case / "0"),
                }
            archive = run_dir / "steadyStart"
            for case in [run_dir, *[case for case in cases if case != run_dir]]:
                for entry in list(case.iterdir()):
                    moving = entry.name == "postProcessing" and case == run_dir
                    if not moving and entry.is_dir():
                        try:
                            moving = float(entry.name) > 0
                        except ValueError:
                            moving = False
                    if moving:
                        destination = archive / entry.relative_to(run_dir)
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(entry), str(destination))
            self._generate_solver_dicts(run_dir)
            require_same_layout()
            succeeded = True
            return report
        finally:
            self.mesh_dir, self._bridge_registry = saved_mesh_dir, saved_registry
            sc.clear()
            sc.update(saved_sc)
            for case, backup in backups.items():
                if not succeeded:
                    shutil.rmtree(case / "0", ignore_errors=True)
                    case.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(backup), str(case / "0"))
            if backup_root_owned:
                shutil.rmtree(backup_root)

    def _decompose_for_solve(self, case_dir: Path, n_procs: int) -> int:
        """Reuse partitions or prepare the requested solver process count.

        n_procs=1 is the explicit serial compatibility path. Reconstruction is
        necessary only when changing an existing partition layout.

        Returns the process count to solve with: the one asked for, unless the
        mesh can only be used as partitioned (see the processorCyclic case
        below), in which case it is the partition count.
        """
        from backend.aircooling.runner.mesh_layout import processor_mesh_dirs

        boundary_path = case_dir / "constant" / "polyMesh" / "boundary"
        has_serial_mesh = (boundary_path.parent / "faces").is_file()
        partitions = processor_mesh_dirs(case_dir) if not has_serial_mesh else []
        reuse = bool(partitions) and len(partitions) == n_procs and n_procs > 1
        if reuse:
            logger.info(f"Reusing {n_procs} mesh partitions directly; no decomposePar required")
        else:
            if not has_serial_mesh:
                if not partitions:
                    raise RuntimeError(f"No complete mesh available for solving in {case_dir}")
                if n_procs > 1 and self._partitions_have_processor_cyclic(case_dir):
                    # reconstructParMesh segfaults on a processorCyclic patch:
                    # initOrder() exchanges the pair's face order with the
                    # neighbour rank, and a serial assembly has no ranks to
                    # talk to (sigSegv in UOPstreamBase::write from
                    # processorCyclicPolyPatch::initOrder). The partitions are
                    # then the only usable decomposition of this mesh, so solve
                    # on them rather than repartition -- which also skips the
                    # reconstruct + decomposePar round trip entirely.
                    logger.warning(
                        f"Mesh partitions carry processorCyclic baffle pairs, "
                        f"which reconstructParMesh cannot assemble into a "
                        f"serial mesh; solving on the existing "
                        f"{len(partitions)} partition(s) instead of the "
                        f"requested {n_procs}"
                    )
                    return len(partitions)
                reason = (
                    "serial solving was requested"
                    if n_procs == 1 else
                    f"solver process count changed from {len(partitions)} to {n_procs}"
                )
                logger.info(f"Reconstructing mesh for compatibility because {reason}")
                self._run_cmd("reconstructParMesh -constant", case_dir)
                if not boundary_path.exists():
                    raise RuntimeError("reconstructParMesh did not produce constant/polyMesh/boundary")
                fix_cyclic_match_tolerance(boundary_path)
                self._ensure_wall_baffles_are_wall_type(boundary_path)
                self._build_field_generator().generate_fields(
                    case_dir, read_boundary_patches(boundary_path), strict=True,
                )
                self._reconcile_boundary_wall_types(boundary_path, case_dir)
            elif not boundary_path.exists():
                raise RuntimeError(f"Serial mesh is missing its boundary file in {case_dir}")

            self._clean_processor_dirs(case_dir)
            if n_procs == 1:
                return n_procs
            generate_decompose_par_dict(case_dir, n_procs)
            logger.info(f"Running decomposePar with {n_procs} processes")
            self._run_cmd("decomposePar", case_dir)

        # Copy constant files to each processor dir
        const_files = ["g", "transportProperties", "turbulenceProperties", "thermophysicalProperties"]
        for pdir in sorted(case_dir.glob("processor*")):
            pconst = pdir / "constant"
            pconst.mkdir(parents=True, exist_ok=True)
            for fname in const_files:
                src = case_dir / "constant" / fname
                if src.exists():
                    import shutil
                    shutil.copy(src, pconst / fname)

        logger.info(f"Parallel solve case ready: {n_procs} processor directories")
        return n_procs

    def _run_solve_from_mesh(self) -> Path:
        """Copy Mesh/ to log/date/ and run solver.

        Clears the mesh time budget first: it bounds MESHING, and a full
        simulation run reaches here in the same process with the deadline
        still armed, which would otherwise abort the solver.
        """
        # Preserve the source-aware caller's already verified identity before
        # a direct entry can perform any re-audit. Legacy meshes additionally
        # receive an actual full-scope check when no exact receipt exists.
        direct_raw = self._read_cfdrun_manifest()
        if (direct_raw.get("geometryExport") or {}).get("sourceSurfaces") or requires_hybrid_air_gate(direct_raw):
            from backend.aircooling.runner.openfoam_patch_area import mesh_identity_sha256
            verified = getattr(self, "_accepted_air_mesh_sha", None)
            if not verified:
                raise RuntimeError("source-aware/hybrid air solve has no verified mesh quality identity")
            if mesh_identity_sha256(self.mesh_dir) != verified:
                raise RuntimeError("source-aware/hybrid air mesh changed after quality verification")
        # Even direct/internal callers must pass the full quality gate before
        # the mesh deadline is disarmed or a solver run directory is created.
        self._require_air_mesh_quality()
        self._clear_mesh_budget()
        import shutil
        import datetime

        mesh_dir = self.mesh_dir
        from backend.aircooling.runner.openfoam_patch_area import require_nonempty_mesh
        require_nonempty_mesh(mesh_dir)
        raw_input = self._read_cfdrun_manifest()
        source_aware = bool((raw_input.get("geometryExport") or {}).get("sourceSurfaces"))
        hybrid_run = requires_hybrid_air_gate(raw_input)
        if source_aware or hybrid_run:
            raw_input = self._pinned_source_manifest()
        accepted_source_hash = getattr(self, "_accepted_air_mesh_sha", None)
        from backend.aircooling.runner.openfoam_patch_area import mesh_identity_sha256
        import hashlib
        if not accepted_source_hash:
            raise RuntimeError("air solve has no verified strict mesh quality identity")
        if mesh_identity_sha256(mesh_dir) != accepted_source_hash:
            raise RuntimeError("air mesh changed after strict quality verification")
        if hashlib.sha256(self._read_cfdrun_bytes()).hexdigest() != self._accepted_air_cfdrun_sha:
            raise RuntimeError("air input changed after strict quality verification")
        hybrid_hash = getattr(self, "_hybrid_mesh_sha", None)
        if hybrid_run:
            if not hybrid_hash:
                raise RuntimeError("hybrid air solve bypassed the mesh-quality/area gate")
            from backend.aircooling.runner.openfoam_patch_area import mesh_identity_sha256
            if mesh_identity_sha256(mesh_dir) != hybrid_hash:
                raise RuntimeError("hybrid air mesh changed after quality/area verification")
        from backend.aircooling.runner.air_run_execution_receipt import (
            preflight_air_run_execution_source,
            publish_air_run_execution_receipt,
        )
        effective_cfdrun = self._hybrid_final_manifest if hybrid_run else self.cfdrun_path
        effective_snapshot = (getattr(self, "_hybrid_input_snapshot", None) if hybrid_run
                              else getattr(self, "_input_snapshot", None))
        if hybrid_run and getattr(self, "_input_snapshot", None) is not None:
            if (effective_snapshot is None or
                    effective_snapshot.parent_sha256 != self._accepted_air_cfdrun_sha):
                raise RuntimeError("hybrid execution snapshot does not match accepted original input")
        effective_provider = ({"input_snapshot": effective_snapshot}
                              if effective_snapshot is not None else {})
        preflight_air_run_execution_source(
            case_dir=self.case_dir, cfdrun_path=effective_cfdrun, **effective_provider,
        )
        log_dir = self.case_dir / "log"
        log_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
        case_name = self.case_dir.name
        run_dir = log_dir / f"{timestamp}_{case_name}"
        if run_dir.exists() or run_dir.is_symlink():
            raise FileExistsError(f"air run directory already exists: {run_dir}")

        # Copy only solver inputs. Geometry, mesh logs, visualization exports and
        # old result times can dominate the otherwise trivial solve startup.
        def _ignore_stale_time_dirs(directory, contents):
            relative = Path(directory).relative_to(mesh_dir)
            if relative == Path(".") or (
                len(relative.parts) == 1 and re.fullmatch(r"processor\d+", relative.name)
            ):
                ignored = []
                for name in contents:
                    # Keep unfamiliar files: a solver dictionary may include a
                    # user-supplied table, library or other input at case root.
                    artifact = (
                        name in {"VTK", "postProcessing", "log", "logs", ".meshgen_scratch"}
                        or name.startswith(("log.", "log_", ".mesh_preview"))
                        or name.endswith(".log")
                    )
                    if not artifact and (Path(directory) / name).is_dir():
                        try:
                            value = float(name)
                            artifact = math.isfinite(value) and value != 0
                        except ValueError:
                            pass
                    if artifact:
                        ignored.append(name)
                return ignored
            if relative.name == "constant":
                return [name for name in contents if name in {"triSurface", "extendedFeatureEdgeMesh"}]
            return []

        logger.info(f"Copying case from {mesh_dir} to {run_dir}")
        shutil.copytree(mesh_dir, run_dir, ignore=_ignore_stale_time_dirs)
        if source_aware or hybrid_run:
            self._pinned_source_manifest()
        if (source_aware or hybrid_run) and mesh_identity_sha256(run_dir) != accepted_source_hash:
            raise RuntimeError("source-aware/hybrid run mesh differs from the verified mesh copy")
        if hybrid_run:
            if mesh_identity_sha256(run_dir) != hybrid_hash:
                raise RuntimeError("hybrid air solve copy differs from the verified mesh")
            (run_dir / "model").mkdir(exist_ok=True)
            archived_manifest = run_dir / "model" / self._hybrid_final_manifest.name
            if effective_snapshot is not None:
                archived_manifest.write_bytes(effective_snapshot.read_bytes())
            else:
                shutil.copy2(self._hybrid_final_manifest, archived_manifest)
            shutil.copy2(self._hybrid_final_ledger,
                         run_dir / self._hybrid_final_ledger.name)
        elif effective_snapshot is not None:
            # Archive the accepted owned bytes, never a later logical artifact.
            (run_dir / "model").mkdir(exist_ok=True)
            archived_manifest = run_dir / "model" / effective_cfdrun.name
            with archived_manifest.open("xb") as archive:
                archive.write(effective_snapshot.read_bytes())

        # Create .foam file
        (run_dir / "case.foam").touch()

        # Generate controlDict and fvSolution at solve-time with latest settings
        self._generate_solver_dicts(run_dir)

        # Setup log file
        log_file = run_dir / f"{timestamp}_{case_name}.log"
        log_handler_id = logger.add(log_file, format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}",
                                     level="DEBUG", mode="w")

        logger.info(f"Created run directory: {run_dir}")
        logger.info(f"Log file: {log_file}")

        # Respect the user's solver process count, reusing a matching dcCart
        # partition layout or converting it only when the requested count differs.
        sc = self._data.get("simulationConfig", {})
        parallel_solve = bool(sc.get("parallelSolve", False))
        n_procs = int(sc.get("solveProcess", 1) or 1)
        retain_partition_results = _resolve_mesh_engine(self._data.get("meshConfig", {})) == "dcCart"
        solver_type = sc.get("solverType", "steady") or "steady"
        # Honor an explicit solverName override; otherwise use the per-solver-type
        # default. Steady defaults to buoyantBoussinesqSimpleFoam_new
        # (third_parties/foam), which re-writes every physical boundary patch
        # with its computed per-face values. Stock OpenFOAM emits
        # `type zeroGradient;` for T and `type noSlip;` for U with NO numbers,
        # so the raw result files have a hole exactly where the wall data
        # should be. controlDict's explicitBoundaryValues/mode picks how
        # (default preserveType — values added, BC type and restart preserved).
        # Transient uses our PIMPLE build with the same component gauges and
        # explicit physical boundary values.
        # DCDTUNI_SOLVER_OVERRIDE (env) takes top priority: the dataset-build
        # pipeline sets it to buoyantBoussinesqSimpleFoam_new because the
        # cfdproj->cfdrun->meshGeometry chain does NOT carry solverName through.
        try:
            solver = self._solver_for_simulation_config(sc)
            # cfdrun v3 steady-converged start (T4d): SIMPLE at the t = 0
            # boundaries in this same run directory, then its internal
            # fields seed the transient 0/ before PIMPLE below.
            resolved = getattr(self, "_resolved", None)
            timeline = resolved.transient if resolved is not None else None
            steady_layout_ready = False
            if (solver_type == "transient" and timeline
                    and (timeline.get("start") or {}).get("kind") == "steadyConverged"):
                logger.info("Steady-converged start: SIMPLE at the t = 0 boundaries first")
                report = self._run_steady_start(run_dir, parallel_solve, n_procs, retain_partition_results)
                (run_dir / "steady_start.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
                n_procs = report["actual_n_procs"]
                parallel_solve = n_procs > 1
                steady_layout_ready = True
            if parallel_solve and n_procs > 1:
                if not steady_layout_ready:
                    n_procs = self._decompose_for_solve(run_dir, n_procs)
                if retain_partition_results:
                    from backend.aircooling.runner.mesh_layout import processor_mesh_dirs
                    if processor_mesh_dirs(run_dir):
                        # A changed process count may have needed a temporary
                        # serial mesh. The partitions now own both mesh and
                        # results, including for the direct OpenFOAM reader.
                        shutil.rmtree(run_dir / "constant" / "polyMesh", ignore_errors=True)
                logger.info(f"Running {solver} in parallel with {n_procs} processes")
                solver_command = f"mpirun -np {n_procs} {solver} -parallel"
                self._run_cmd(solver_command, run_dir)
                if retain_partition_results:
                    logger.info("Keeping parallel results for direct OpenFOAM postprocessing")
                else:
                    logger.info("Reconstructing parallel results...")
                    self._run_cmd("reconstructPar -latestTime", run_dir)
                    self._rewrite_explicit_boundary_values(run_dir)
            else:
                if not steady_layout_ready and not (run_dir / "constant" / "polyMesh" / "faces").is_file():
                    self._decompose_for_solve(run_dir, 1)
                # No _rewrite_explicit_boundary_values here: with no
                # reconstructPar there is nothing to undo the solver's own
                # write. In serial, field.time().timePath() IS <case>/<time>,
                # so writeBCFields lands directly on the file that gets read.
                # Reasoned from the code, not measured — CLAUDE.md forbids
                # serial solving, so there is no supported run to measure.
                logger.info(f"Running {solver} in serial mode")
                solver_command = solver
                self._run_cmd(solver_command, run_dir)
        finally:
            try:
                logger.remove(log_handler_id)
            except Exception:
                pass

        # A zero solver exit alone does not establish a usable result. Publish
        # only after the native mesh and latest positive-time T/U pass checks.
        publish_air_run_execution_receipt(
            case_dir=self.case_dir, cfdrun_path=effective_cfdrun,
            run_dir=run_dir, solver_command=solver_command, **effective_provider,
        )
        logger.info(f"Solve complete")
        logger.info(f"Results in: {run_dir}")

        # 2026-04-19: auto-harvest sensor results for CLI parity with
        # the browser UI's /api/harvest-sensor-results flow. Failures
        # must not fail the run — the raw probes/ are already on disk.
        try:
            self._auto_harvest_sensors()
        except Exception as exc:
            logger.warning(
                f"Sensor harvest failed (non-fatal; raw probes remain on disk): {exc}"
            )

        # 2026-09-22: the dual convergence verdict (residuals decrease
        # smoothly AND every return-air sensor changed then plateaued) into
        # <run_dir>/convergence.json. Reporting only: a NOT_CONVERGED run
        # still returns its results, the verdict is what the operator reads.
        try:
            self._write_convergence_verdict(run_dir, log_file)
        except Exception as exc:
            logger.warning(f"Convergence verdict failed (non-fatal): {exc}")

        return run_dir

    def _sensor_names(self) -> List[str]:
        """Sensor names in ``probeLocations`` order (see ``_auto_harvest_sensors``)."""
        names: List[str] = []
        for i, sensor in enumerate(self._data.get("sensorPoints", []) or []):
            if not isinstance(sensor, dict):
                continue
            if "x" not in sensor or "y" not in sensor or "z" not in sensor:
                continue
            names.append(str(sensor.get("name") or f"sensor_{i + 1}"))
        return names

    def _write_convergence_verdict(self, run_dir: Path, log_file: Optional[Path] = None) -> Dict[str, Any]:
        """Run ``convergence.assess_run`` on the finished solve and log its one-line verdict."""
        from backend.aircooling.runner.convergence import assess_run, summary_line

        result = assess_run(run_dir, log_path=log_file, sensor_names=self._sensor_names())
        line = summary_line(result)
        if result.get("verdict") in ("CONVERGED", "TRANSIENT_COMPLETED"):
            logger.info(line)
        else:
            logger.warning(line)
        logger.info(f"Convergence verdict written to {run_dir / 'convergence.json'}")
        return result

    def _auto_harvest_sensors(self) -> None:
        """Write ``{run_dir}/sensorResults/{stem}_sensor_HH-MM-SS.json``.

        Pulls sensor names from ``self._data['sensorPoints']`` in the
        same order ``_extract_sensor_points`` emitted to ``probeLocations``
        so column indices align. Silently skips when probes weren't
        written (see sensor_harvest.harvest_sensor_results).
        """
        from backend.aircooling.runner.sensor_harvest import harvest_sensor_results

        sensor_names: List[str] = []
        for i, sensor in enumerate(self._data.get("sensorPoints", []) or []):
            if not isinstance(sensor, dict):
                continue
            if "x" not in sensor or "y" not in sensor or "z" not in sensor:
                continue
            sensor_names.append(str(sensor.get("name") or f"sensor_{i + 1}"))

        if not sensor_names:
            logger.info("Sensor harvest skipped: no sensorPoints in cfdrun")
            return

        unit_t = (self._data.get("simulationConfig", {}) or {}).get(
            "unitTemperature", "C"
        )
        case_name = self.cfdrun_path.stem
        harvest_sensor_results(
            self.case_dir,
            case_name=case_name,
            sensor_names=sensor_names,
            unit_temperature=unit_t,
        )

    # _build_leakage_fvoptions_config — REMOVED 2026-04-17.
    # Through-zones are no longer modeled as cellZones + DarcyForchheimer porous source.
    # They are pure fluid; pressure drop is provided by the perforated rack doors
    # (createBaffles porousBafflePressure). Only partition-hole porous sources remain
    # in fvOptions (see _build_partition_hole_fvoptions_config).
    # (formerly called _build_leakage_fvoptions_config)

    def _build_partition_hole_fvoptions_config(self) -> Dict[str, Any]:
        """Build fvOptions entries for partition-wall helper holes."""
        config: Dict[str, Any] = {}
        for part in self._data.get("partitionWalls", {}).values():
            geom = part.get("geometry", {}) or {}
            size = geom.get("size", {}) or {}
            quat = geom.get(
                "transformQuaternion",
                {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
            )
            try:
                depth_value = size.get("D")
                depth = max(0.001, float(depth_value))
            except (TypeError, ValueError):
                depth = 0.001

            # Y-up: D (thickness) is along local-Z; rotate local-Z to world.
            thickness_axis = _quat_rotate((0.0, 0.0, 1.0), quat)
            try:
                e1 = _vec_normalize(thickness_axis)
            except RuntimeError:
                e1 = (0.0, 0.0, 1.0)

            # Pick a fallback axis not parallel to e1 (e1 is horizontal under
            # Y-up, so world-Y is always a safe tangent).
            tmp_axis = (0.0, 1.0, 0.0) if abs(e1[1]) < 0.9 else (1.0, 0.0, 0.0)
            try:
                e2 = _vec_normalize(_vec_cross(tmp_axis, e1))
            except RuntimeError:
                fallback_axis = (1.0, 0.0, 0.0) if tmp_axis == (0.0, 0.0, 1.0) else (0.0, 0.0, 1.0)
                e2 = _vec_normalize(_vec_cross(fallback_axis, e1))

            for hole in part.get("holes", []):
                phys = hole.get("physics", {}) or {}
                # 2026-04-16: only leakageRate gates the source term.
                try:
                    leakage_rate = float(phys.get("leakageRate", 100.0))
                except (TypeError, ValueError):
                    continue
                if leakage_rate >= 100.0:
                    continue

                zone_name = hole.get("zoneName", "")
                zone_file = hole.get("zoneStlFile")
                if not zone_name or not zone_file:
                    continue

                zone_path = self.tri_surface_dir / zone_file
                if not zone_path.exists():
                    continue

                surface_name = _sanitize(Path(zone_file).stem)
                centroid = _compute_stl_solid_centroid(zone_path, surface_name)
                if centroid is None:
                    continue

                sigma = max(0.0, min(1.0, leakage_rate / 100.0))
                d_coeff, f_coeff = _darcy_forchheimer(sigma, depth, min_depth=0.001)
                cellzone_name = surface_name
                origin = f"({centroid[0]:.6f} {centroid[1]:.6f} {centroid[2]:.6f})"
                config[cellzone_name] = type("ZoneCfg", (), {
                    "cellzone_name": cellzone_name,
                    "sigma": sigma,
                    "d": f"({d_coeff:.6g} 0 0)",
                    "f": f"({f_coeff:.6g} 0 0)",
                    "origin": origin,
                    "e1": f"({e1[0]:.6g} {e1[1]:.6g} {e1[2]:.6g})",
                    "e2": f"({e2[0]:.6g} {e2[1]:.6g} {e2[2]:.6g})",
                })()
                logger.info(
                    f"Partition hole fvOptions: {cellzone_name}, sigma={sigma}, "
                    f"depth={depth}, f={f_coeff:.4g}"
                )
        return config

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def _ensure_watertight_room_enclosure(self) -> None:
        """Regenerate room_wall.stl and merged_floor_ceiling.stl from room data
        to ensure they form a watertight enclosure with shared vertices.

        Supports arbitrary polygon rooms (triangles, L-shapes, etc.) by
        extruding the polygon along Y (Y-up convention) to form a prismatic
        enclosure. Floor and ceiling are triangulated using ear-clipping.

        Y-up notes: room.plane[i] is a face-local floor-plan point; the
        frontend serializes it as {x, y, z=0} where `y` is actually the
        Z-axis (depth) under Y-up. We rename it to `pz` internally and
        extrude along Y.
        """
        import math

        data = self._data
        room = data.get("room", {})
        plane = room.get("plane", [])
        height = room.get("height", 0)
        if len(plane) < 3 or height <= 0:
            return

        # The browser-exported STL is the geometry source of truth (CLAUDE.md).
        # Regenerating over it threw away everything the sheets carried — floor
        # cutouts, ceiling return grilles, tile openings — and, because the
        # ceiling took the SECOND solid name in the file, a ceiling exported as
        # (Ceiling_patch_1, …, Ceiling_part1) came back as one 2-triangle slab
        # called Ceiling_patch_1: the whole ceiling became a pressure outlet.
        # Only a MISSING or empty enclosure file is synthesised here.
        wall_path = self.tri_surface_dir / "room_wall.stl"
        fc_path = self.tri_surface_dir / "merged_floor_ceiling.stl"
        need_walls = not (wall_path.exists() and self._stl_solids.get("room_wall.stl"))
        need_fc = not (fc_path.exists() and self._stl_solids.get("merged_floor_ceiling.stl"))
        if not need_walls and not need_fc:
            logger.info(
                "Room enclosure taken from the browser export: "
                f"room_wall.stl ({len(self._stl_solids.get('room_wall.stl', []))} solids), "
                f"merged_floor_ceiling.stl ({len(self._stl_solids.get('merged_floor_ceiling.stl', []))} solids)"
            )
            return

        # Extract polygon vertices in order. pts[i] = (world_x, world_z),
        # since plane[i].y holds the face-local 2nd coord (world Z under Y-up).
        pts = [(p["x"], p["y"]) for p in plane]
        n = len(pts)
        y0, y1 = 0.0, height  # vertical extrusion along Y

        def _tri(v0, v1, v2, nx, ny, nz):
            return (f"  facet normal {nx} {ny} {nz}\n"
                    f"    outer loop\n"
                    f"      vertex {v0[0]} {v0[1]} {v0[2]}\n"
                    f"      vertex {v1[0]} {v1[1]} {v1[2]}\n"
                    f"      vertex {v2[0]} {v2[1]} {v2[2]}\n"
                    f"    endloop\n"
                    f"  endfacet\n")

        wall_solids = self._stl_solids.get("room_wall.stl", [])
        fc_solids = self._stl_solids.get("merged_floor_ceiling.stl", [])

        # --- Walls: one quad (2 triangles) per polygon edge ---
        wall_stl = ""
        for i in range(n):
            j = (i + 1) % n
            ax, az = pts[i]
            bx, bz = pts[j]
            # Wall quad vertices in world (x, y_vertical, z_depth).
            bl = (ax, y0, az)
            br = (bx, y0, bz)
            tr = (bx, y1, bz)
            tl = (ax, y1, az)
            # Outward normal (pointing away from polygon interior) in XZ plane.
            dx, dz = bx - ax, bz - az
            length = math.sqrt(dx * dx + dz * dz)
            if length < 1e-10:
                continue
            # Normal = perpendicular to edge in the horizontal XZ plane.
            # For a CCW polygon (when viewed from above, +Y looking down),
            # the outward normal is (dz, 0, -dx) / length.
            nx, ny, nz = dz / length, 0.0, -dx / length

            name = wall_solids[i] if i < len(wall_solids) else f"Wall_{i+1}"
            wall_stl += f"solid {name}\n"
            wall_stl += _tri(bl, br, tr, nx, ny, nz)
            wall_stl += _tri(bl, tr, tl, nx, ny, nz)
            wall_stl += f"endsolid {name}\n"

        if need_walls:
            wall_path.write_text(wall_stl, encoding="utf-8")

        # --- Floor and Ceiling: triangulate polygon ---
        # Simple ear-clipping triangulation for arbitrary (convex/concave) polygons
        def _ear_clip(polygon):
            """Ear-clipping triangulation. Returns list of (i, j, k) index tuples."""
            indices = list(range(len(polygon)))
            triangles = []

            def _cross2d(o, a, b):
                return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

            def _point_in_tri(p, a, b, c):
                d1 = _cross2d(p, a, b)
                d2 = _cross2d(p, b, c)
                d3 = _cross2d(p, c, a)
                has_neg = (d1 < 0) or (d2 < 0) or (d3 < 0)
                has_pos = (d1 > 0) or (d2 > 0) or (d3 > 0)
                return not (has_neg and has_pos)

            # Ensure CCW winding
            area = sum(_cross2d(polygon[0], polygon[i], polygon[i + 1])
                       for i in range(1, len(polygon) - 1))
            if area < 0:
                indices.reverse()

            max_iter = len(indices) * len(indices)
            iter_count = 0
            while len(indices) > 2 and iter_count < max_iter:
                iter_count += 1
                ear_found = False
                for idx in range(len(indices)):
                    prev_i = indices[(idx - 1) % len(indices)]
                    curr_i = indices[idx]
                    next_i = indices[(idx + 1) % len(indices)]
                    prev_p = polygon[prev_i]
                    curr_p = polygon[curr_i]
                    next_p = polygon[next_i]
                    # Check convex
                    if _cross2d(prev_p, curr_p, next_p) <= 0:
                        continue
                    # Check no other point inside triangle
                    is_ear = True
                    for check_idx in indices:
                        if check_idx in (prev_i, curr_i, next_i):
                            continue
                        if _point_in_tri(polygon[check_idx], prev_p, curr_p, next_p):
                            is_ear = False
                            break
                    if is_ear:
                        triangles.append((prev_i, curr_i, next_i))
                        indices.remove(curr_i)
                        ear_found = True
                        break
                if not ear_found:
                    # Fallback: if stuck, add remaining as triangle (may be degenerate)
                    if len(indices) == 3:
                        triangles.append(tuple(indices))
                        indices.clear()
                    break
            return triangles

        tris = _ear_clip(pts)

        fc_stl = ""
        # A synthesised sheet is the bare room boundary; never borrow a patch or
        # part name from a browser file that is being kept.
        fc_solids = [] if not need_fc else fc_solids
        floor_name = fc_solids[0] if len(fc_solids) > 0 else "Ground_floor"
        fc_stl += f"solid {floor_name}\n"
        for i, j, k in tris:
            # Floor at Y=0 under Y-up; normal pointing down (-Y).
            fc_stl += _tri(
                (pts[i][0], y0, pts[i][1]),
                (pts[k][0], y0, pts[k][1]),  # reversed winding for -Y normal
                (pts[j][0], y0, pts[j][1]),
                0, -1, 0)
        fc_stl += f"endsolid {floor_name}\n"

        ceil_name = fc_solids[1] if len(fc_solids) > 1 else "Room_top"
        fc_stl += f"solid {ceil_name}\n"
        for i, j, k in tris:
            # Ceiling at Y=height under Y-up; normal pointing up (+Y).
            fc_stl += _tri(
                (pts[i][0], y1, pts[i][1]),
                (pts[j][0], y1, pts[j][1]),
                (pts[k][0], y1, pts[k][1]),
                0, 1, 0)
        fc_stl += f"endsolid {ceil_name}\n"

        if need_fc:
            fc_path.write_text(fc_stl, encoding="utf-8")

        if wall_path.exists():
            self._stl_solids["room_wall.stl"] = _read_stl_solid_names(wall_path)
        if fc_path.exists():
            self._stl_solids["merged_floor_ceiling.stl"] = _read_stl_solid_names(fc_path)

        xs = [p[0] for p in pts]
        zs = [p[1] for p in pts]
        logger.info(
            f"Regenerated watertight room enclosure (Y-up): "
            f"room_wall.stl ({len(self._stl_solids['room_wall.stl'])} walls), "
            f"merged_floor_ceiling.stl ({len(self._stl_solids['merged_floor_ceiling.stl'])} faces), "
            f"polygon={n} verts, y={y0:.2f}-{y1:.2f} (vertical), "
            f"floor-plan x=[{min(xs):.2f},{max(xs):.2f}] z=[{min(zs):.2f},{max(zs):.2f}]"
        )

    def _load(self) -> None:
        """Load the cfdrun/cfd_export JSON, discover STL solid names, and prepare
        STL files for snappyHexMesh (merge perforated into floor_ceiling,
        split wall vs internal solids into separate files)."""
        if getattr(self, "_input_snapshot", None) is None and not self.cfdrun_path.exists():
            raise FileNotFoundError(f"cfdrun input file not found: {self.cfdrun_path}")
        payload = self._read_cfdrun_bytes()
        raw = json.loads(payload)
        if (requires_hybrid_air_gate(raw) or
                bool((raw.get("geometryExport") or {}).get("sourceSurfaces"))):
            # Mesh-only pins this after original-STL verification. Solve-only
            # cannot repeat that preflight: mesh creation may regroup those
            # STL files, so it pins the manifest and verifies the saved
            # source-to-mesh receipt against the existing mesh instead.
            self._verified_source_cfdrun_sha256 = hashlib.sha256(payload).hexdigest()
        # P3 T6: require a V2 manifest. The MESH pipeline still reads the v1
        # geometry blob — carried in the transitional meshGeometry block (known
        # spec §12 deviation) and validated separately via CFDExport. The BC
        # registry is materialised below from the V2 boundaryBindings.
        # 3 = v2 + the transient block and binding schedules (cfdrun v3, E7);
        # 4 = one simulationConfig, the root (E8a).
        if raw.get("schemaVersion") not in (2, 3, 4):
            raise ValueError(
                f"unsupported .cfdrun schemaVersion {raw.get('schemaVersion')!r}; "
                "re-export from the editor (V2 required)"
            )
        self._validate_v2_execution_parity(raw)
        from backend.aircooling.runner.derived_config import execution_blob
        validated = CFDExport.model_validate(execution_blob(raw))
        self._data = validated.model_dump(mode="json")
        self._cfdrun_v3_shadow_check(raw)
        logger.info(f"Loaded cfdrun input: {self.cfdrun_path}")

        # Discover STL files and parse solid names
        # Only consider browser-exported grouped STL files (merged_*.stl + room_wall.stl).
        # Individual per-entity STL files from old pipeline runs are intentionally ignored.
        # 2026-04-17 redesign: also accept per-bucket files (merged_door_NU.stl, etc.),
        # merged_rack_frame.stl, and legacy leakage_*.stl files (pre-2026-04-17 naming).
        GROUPED_STL_NAMES = {
            "merged_rack.stl",
            "merged_rack_frame.stl",        # 2026-04-17 redesign: rack frame
            "merged_acu.stl",
            "merged_baffle.stl",
            "merged_all_baffles.stl",       # new browser export: all baffle/door/wallBaffle solids

            "merged_floor_ceiling.stl",
            "merged_perforated.stl",
            "merged_perforated_baffles.stl",  # new browser export: perforated subset
            "merged_wall_baffles.stl",      # new browser export: wall baffle subset
            "merged_box.stl",
            "merged_pipe.stl",              # air-side pipes / ducts (swept solids)
            "merged_partition_wall.stl",
            "merged_leakage.stl",   # legacy pre-2026-04-17 scene-merged through-zone file
            "room_wall.stl",
            "merged_rack_door.stl",         # baffle merge: all rack doors
            "merged_wall_baffle.stl",       # baffle merge: all wall baffles
        }
        self._stl_solids = {}
        if self.tri_surface_dir.exists():
            for stl_path in sorted(self.tri_surface_dir.glob("*.stl")):
                name = stl_path.name
                # Editor-only export artifacts (merged_server_interface_walls.stl
                # and any merged_server_*_left/_right.stl split) are NEVER mesh
                # inputs. The server left/right interface faces they carry sit
                # against rack-frame cavity walls with no fluid at the interface
                # — the closed fluid partition is maintained by the frame cavity
                # walls plus the servers' fluid-side faces — so these solids get
                # no patch, no BC and no refinement shell. The whitelist below
                # already rejects these names today; this explicit guard keeps
                # that contract independent of future whitelist/bucket-pattern
                # extensions.
                if is_editor_only_stl(name):
                    logger.info(
                        f"  {name}: editor-only export bucket, ignored by the "
                        "mesh path (no fluid at the server/frame interface)"
                    )
                    continue
                # Accept: static grouped names, partition-hole helpers,
                # per-bucket files (merged_server_NU / merged_blanking_NU /
                # merged_partition_NU / merged_through_zone_NU / merged_door_NU),
                # and legacy individual leakage_*.stl files (pre-2026-04-17 naming).
                if (name not in GROUPED_STL_NAMES
                        and not name.startswith("partition_hole_")
                        and not name.startswith("leakage_")
                        and not name.startswith("merged_door_")
                        and not name.startswith("perforated_baffle_")
                        and not name.startswith("perforated_floor_")
                        and _parse_bucket_from_filename(name) is None):
                    continue
                names = _read_stl_solid_names(stl_path)
                if names:
                    self._stl_solids[stl_path.name] = names
                    logger.info(f"  {stl_path.name}: {len(names)} solids")
        if not self._stl_solids:
            raise FileNotFoundError(
                f"No grouped STL files (merged_*.stl, room_wall.stl) found in {self.tri_surface_dir}. "
                "Start mesh generation from the browser (which uploads STL files first)."
            )

        expected_partition_helpers = _partition_hole_expected_map(self._data)
        for helper_name, expected in expected_partition_helpers.items():
            helper_path = self.tri_surface_dir / helper_name
            if not helper_path.exists():
                raise RuntimeError(
                    f"Referenced partition helper STL '{helper_name}' for zoneName "
                    f"'{expected['zone_name']}' is missing from {self.tri_surface_dir}.\n"
                    f"This usually means the cfdrun was edited (partition-wall hole "
                    f"toggled on) without a fresh mesh upload. Re-export STL files "
                    f"from the browser UI and re-mesh — the helper STL is generated "
                    f"only during the browser export step and cannot be reconstructed "
                    f"from the existing mesh."
                )

            helper_names = _read_stl_solid_names(helper_path)
            if not helper_names:
                raise RuntimeError(
                    f"Referenced partition helper STL '{helper_name}' for zoneName "
                    f"'{expected['zone_name']}' contains no solids"
                )
            if len(helper_names) != 1:
                raise RuntimeError(
                    f"Partition helper STL '{helper_name}' must contain exactly one solid "
                    f"for zoneName '{expected['zone_name']}', got {len(helper_names)}"
                )

            solid_name = helper_names[0]
            surface_name = expected["surface_name"]
            # Accept either the raw zone name (fresh from frontend export) or
            # the file-stem-normalized form written by a previous run-mesh
            # pass below. Without this dual check, solve-only after mesh
            # would always trip the equality test, since the first mesh run
            # rewrites the solid to surface_name.
            sanitized_solid = _sanitize(solid_name)
            if sanitized_solid not in (_sanitize(expected["zone_name"]),
                                       _sanitize(surface_name)):
                raise RuntimeError(
                    f"Partition helper STL '{helper_name}' solid '{solid_name}' does not match "
                    f"expected zoneName '{expected['zone_name']}' (or normalized surface name "
                    f"'{surface_name}') for partition wall '{expected['part_name']}'"
                )

            if solid_name != surface_name:
                text = helper_path.read_text(encoding="utf-8")
                text = re.sub(r"(?m)^solid\s+.*$", f"solid {surface_name}", text, count=1)
                text = re.sub(r"(?m)^endsolid\s+.*$", f"endsolid {surface_name}", text, count=1)
                helper_path.write_text(text, encoding="utf-8")
                logger.info(
                    f"Normalized partition helper surface name: {helper_name} "
                    f"{solid_name!r} -> {surface_name!r}"
                )
                solid_name = surface_name
            self._stl_solids[helper_name] = [solid_name]

        primary_geometry_found = any(name in GROUPED_STL_NAMES for name in self._stl_solids)
        if not primary_geometry_found:
            raise FileNotFoundError(
                f"No grouped STL files (merged_*.stl, room_wall.stl) found in {self.tri_surface_dir}. "
                "Start mesh generation from the browser (which uploads STL files first)."
            )

        helper_stl_names = [name for name in list(self._stl_solids) if name.startswith("partition_hole_")]
        for helper_name in helper_stl_names:
            expected = expected_partition_helpers.get(helper_name)
            if expected is None:
                # Drop from the in-memory map only — never unlink from disk.
                # _load runs in BOTH mesh-only and solve-only modes; deleting
                # a user-uploaded STL here makes a subsequent solve-only fail
                # with "Referenced partition helper STL ... is missing"
                # (the file is irrecoverable without re-meshing from the UI).
                # Mesh-phase re-upload from the browser overwrites triSurface
                # wholesale, which is the right place for cleanup.
                self._stl_solids.pop(helper_name, None)
                logger.info(
                    f"Ignored stale partition helper STL "
                    f"(left on disk for safety): {helper_name}"
                )
                continue

        # Clean up leftover split files from previous attempts
        for leftover in ("merged_floor_ceiling_internal.stl",
                         "merged_floor_ceiling_perforated.stl",
                         "merged_baffle_wall.stl",
                         "room_enclosure.stl",
                         "room_enclosure.eMesh"):
            lp = self.tri_surface_dir / leftover
            if lp.exists():
                lp.unlink()
                self._stl_solids.pop(leftover, None)

        # Restore merge-output files from _split_internal_face_solids() on
        # previous runs back to merged_baffle.stl so the split can re-categorize.
        # merged_wall_baffle.stl, merged_rack_door.stl → merged_baffle.stl
        # perforated_baffle_*.stl, perforated_floor_*.stl → merged_baffle.stl
        # (The actual source file doesn't matter; _split will re-extract.)
        _baffle_restore_target = self.tri_surface_dir / "merged_baffle.stl"
        for merge_leftover in ("merged_wall_baffle.stl", "merged_rack_door.stl"):
            lp = self.tri_surface_dir / merge_leftover
            if lp.exists():
                with open(lp, "r", encoding="utf-8") as f:
                    block_text = f.read().strip()
                if block_text:
                    with open(_baffle_restore_target, "a", encoding="utf-8") as f:
                        f.write("\n" + block_text + "\n")
                    logger.info(f"Restored {merge_leftover} → merged_baffle.stl")
                lp.unlink()
                self._stl_solids.pop(merge_leftover, None)
        if self.tri_surface_dir.exists():
            for pattern in ("perforated_baffle_*.stl", "perforated_floor_*.stl",
                            "wallBaffle_floor_*.stl",
                            "merged_door_*.stl", "merged_wallBaffle_*.stl"):
                for pf in sorted(self.tri_surface_dir.glob(pattern)):
                    with open(pf, "r", encoding="utf-8") as f:
                        block_text = f.read().strip()
                    if block_text:
                        with open(_baffle_restore_target, "a", encoding="utf-8") as f:
                            f.write("\n" + block_text + "\n")
                        logger.info(f"Restored {pf.name} → merged_baffle.stl")
                    pf.unlink()
                    self._stl_solids.pop(pf.name, None)
        # Re-read merged_baffle.stl solid names after restoration
        if _baffle_restore_target.exists():
            names = _read_stl_solid_names(_baffle_restore_target)
            if names:
                self._stl_solids["merged_baffle.stl"] = names

        # Restore individual baffle_*.stl files from previous runs back into
        # their source STL files, then delete the individual files.
        if self.tri_surface_dir.exists():
            for bf_file in sorted(self.tri_surface_dir.glob("baffle_*.stl")):
                with open(bf_file, "r", encoding="utf-8") as f:
                    block_text = f.read().strip()
                if not block_text:
                    bf_file.unlink()
                    self._stl_solids.pop(bf_file.name, None)
                    continue
                # Determine source file from solid name prefix
                san = bf_file.stem.replace("baffle_", "", 1)
                # Check if this solid belongs to a floor (merge back to floor_ceiling)
                # or a perforated opening (merge back to merged_perforated.stl)
                # or a rack wallBaffle (merge back to merged_rack.stl)
                # Default: merge back to merged_floor_ceiling.stl
                is_perf = any(san == _sanitize(op["solidName"])
                              for fl in self._data.get("floors", {}).values()
                              for op in fl.get("openings", [])
                              if op.get("physics", {}).get("type") == "perforated")
                # Check if this solid belongs to a baffle model (not a floor)
                is_baffle_model = any(
                    san.startswith(_sanitize(bn) + "_") or san == _sanitize(bn)
                    for bn in self._data.get("baffles", {}).keys()
                )
                # 2026-04-17: legacy is_rack_wall_baffle branch (restored to
                # merged_rack.stl) removed — wallBaffle solids are now handled
                # by the is_rack_thin_wallBaffle check below, which preserves
                # the individual baffle_*.stl file instead of merging back
                # into a legacy container.
                # Check if this solid is a rack thin-baffle (door / wallBaffle /
                # internal partition). These are NOT part of any merged_*.stl
                # at mesh-entry time — they arrive as single-solid baffle_*.stl
                # files, either freshly extracted by _split_internal_face_solids
                # this run OR left over from the previous run. Either way,
                # preserving them (rather than restoring into floor_ceiling)
                # is correct: _split will rewrite them from the corresponding
                # merged_<bucket>.stl file if present, and snappy picks them
                # up via the baffle_* refinementSurfaces branch.
                is_rack_door = any(
                    (rack.get("inletDoor", {}) or {}).get("solidName") == bf_file.stem.replace("baffle_", "", 1)
                    or (rack.get("outletDoor", {}) or {}).get("solidName") == bf_file.stem.replace("baffle_", "", 1)
                    or san == f"{_sanitize(rname)}_inlet_door"
                    or san == f"{_sanitize(rname)}_outlet_door"
                    for rname, rack in self._data.get("racks", {}).items()
                )
                is_rack_thin_wallBaffle = any(
                    san == _sanitize(wb)
                    for rack in self._data.get("racks", {}).values()
                    for wb in rack.get("wallBaffleSolids", [])
                )
                # Rack internal partition (name pattern `{rack}_u{idx+1}_partition`)
                # has its authoritative export in merged_partition_<N>U.stl —
                # never extracted to an individual baffle_*.stl. Any stale
                # baffle_*_partition.stl from a previous run is discarded:
                # dcCart regenerates these derived files below, while snappy
                # uses the grouped export directly.
                # Asymmetry with the partition-wall helper STL branch above
                # (which preserves stale files on disk): the rack partition's
                # geometry is in merged_partition_<N>U.stl, not in the stale
                # baffle_*.stl, so unlink here is safe for solve-only too —
                # triSurface/*.stl is a meshing input, not a solver input, and
                # solve-only reads the already-built constant/polyMesh/.
                m_part = re.search(r"^(.*)_u\d+_partition$", san)
                is_rack_partition = bool(m_part) and any(
                    m_part.group(1) == _sanitize(rn)
                    for rn in self._data.get("racks", {}).keys()
                )
                if is_rack_partition:
                    if not getattr(self, "_stale_partition_discarded_hint_emitted", False):
                        logger.info("Refreshing rack partition geometry from "
                                    "merged_partition_<N>U.stl; removing derived "
                                    "baffle files from the previous mesh.")
                        self._stale_partition_discarded_hint_emitted = True
                    logger.info(f"  discarded stale {bf_file.name}")
                    bf_file.unlink()
                    self._stl_solids.pop(bf_file.name, None)
                    continue
                if is_rack_door or is_rack_thin_wallBaffle:
                    # Restore door/wallBaffle solids to merged_baffle.stl so
                    # _split_internal_face_solids can categorise and re-merge
                    # them into merged_wall_baffle.stl / merged_rack_door.stl.
                    target = self.tri_surface_dir / "merged_baffle.stl"
                    with open(target, "a", encoding="utf-8") as f:
                        f.write("\n" + block_text + "\n")
                    names = _read_stl_solid_names(target)
                    if names:
                        self._stl_solids[target.name] = names
                    bf_file.unlink()
                    self._stl_solids.pop(bf_file.name, None)
                    kind = "door" if is_rack_door else "wallBaffle"
                    logger.info(f"Restored rack {kind} {bf_file.name} → {target.name}")
                    continue
                if is_perf:
                    target = self.tri_surface_dir / "merged_perforated.stl"
                elif is_baffle_model:
                    target = self.tri_surface_dir / "merged_baffle.stl"
                else:
                    target = self.tri_surface_dir / "merged_floor_ceiling.stl"
                with open(target, "a", encoding="utf-8") as f:
                    f.write("\n" + block_text + "\n")
                # Update stl_solids for target
                names = _read_stl_solid_names(target)
                if names:
                    self._stl_solids[target.name] = names
                bf_file.unlink()
                self._stl_solids.pop(bf_file.name, None)
                logger.info(f"Restored {bf_file.name} → {target.name}")

        # ── Keep room-wall and floor/ceiling STL files as room boundary surfaces ──
        # These STL files define the user-authored room enclosure. snappyHexMesh
        # uses the closed enclosure to remove cells outside the room. Any logic
        # that deletes these files would turn the background blockMesh container
        # into the effective room boundary, which is incorrect.
        if "room_wall.stl" in self._stl_solids:
            logger.info(
                f"room_wall.stl has {len(self._stl_solids['room_wall.stl'])} wall solids: "
                f"{', '.join(self._stl_solids['room_wall.stl'])}"
            )
        if "merged_floor_ceiling.stl" in self._stl_solids:
            logger.info(
                "Keeping merged_floor_ceiling.stl as part of the room enclosure: "
                f"{len(self._stl_solids['merged_floor_ceiling.stl'])} solids"
            )

        # Build baffle physics lookup from .cfdrun data
        self._build_baffle_physics_lookup()

        # ── Split solids that need faceType internal + faceZone into individual STL files ──
        # snappyHexMesh ignores faceType internal for surfaces with named regions
        # (it creates wall patches instead). The only reliable way to get internal faces
        # with per-solid faceZones is to put each solid in its own STL file (no named regions).
        #
        # Groups:
        # 1. merged_baffle.stl: porosity=0 baffle model solids → faceType internal, no faceZone
        # 2. Individual baffle_<solid>.stl: porosity>0 baffle solids → faceType internal + faceZone
        # 3. Individual baffle_<solid>.stl: wallBaffle floor solids → faceType internal + faceZone
        # 4. Individual baffle_<solid>.stl: perforated floor/baffle solids → faceType internal + faceZone
        # 5. Individual baffle_<solid>.stl: rack wallBaffle solids → faceType internal + faceZone
        self._split_internal_face_solids()
        self._prepare_dcCart_rack_partitions()

        # P3 T6: materialise the V2 manifest into a ResolvedRun. The BC registry
        # (resolved.bridge_registry) replaces the deleted _build_bridge_bc()
        # derivation. resolve_stl=False — the registry is the proven part; the
        # areaM2/STL cross-check is a future hardening, and enabling it now risks
        # false-failures on merged-file solid naming (the merge/split passes
        # above rename solids on disk relative to geometryExport).
        self._resolved = cfdrun_reader.resolve_run(
            self.case_dir,
            cfdrun_path=self.cfdrun_path,
            resolve_stl=False, **self._snapshot_kwargs(),
        )

    def _prepare_dcCart_rack_partitions(self) -> None:
        """Keep both fluid sides of zero-thickness rack shelves.

        Carving cannot represent a sheet reached by fluid from both sides.
        Give each exported shelf its own faceZone; dcCart splits its two wall
        sides before merging cells, so each side can coalesce independently.
        Retain the grouped source on
        disk so a CLI mesh rerun can recreate these derived files.
        """
        if _resolve_mesh_engine(self._data.get("meshConfig", {}) or {}) != "dcCart":
            return
        for filename, solids in list(self._stl_solids.items()):
            bucket = _parse_bucket_from_filename(filename)
            if not bucket or bucket[0] != "partition":
                continue
            text = (self.tri_surface_dir / filename).read_text(encoding="utf-8")
            blocks = {
                name.strip(): block
                for block, name in re.findall(
                    r"(^solid[ \t]+([^\n]+)\n.*?^endsolid[^\n]*)", text, re.MULTILINE | re.DOTALL
                )
            }
            for solid in solids:
                block = blocks.get(solid)
                if block is None:
                    raise RuntimeError(f"Rack partition {solid!r} is missing from {filename}")
                san = _sanitize(solid)
                derived = f"baffle_{san}.stl"
                (self.tri_surface_dir / derived).write_text(block + "\n", encoding="utf-8")
                self._stl_solids[derived] = [solid]
                self._baffle_physics[san] = {"baffleType": "wall", "porosity": 0, "directWall": True}
            del self._stl_solids[filename]

    def _build_baffle_physics_lookup(self) -> None:
        """Build lookup mapping sanitized STL solid names to their baffle physics."""
        data = self._data
        baffle_physics: Dict[str, Dict] = {}
        # Sort by name length descending to match longest prefix first
        # (avoids "Baffle_1" matching "Baffle_1_copy")
        sorted_baffles = sorted(
            data.get("baffles", {}).items(),
            key=lambda x: len(x[0]), reverse=True
        )

        # First pass: map exact solid names from patchPhysics
        for baffle_name, baffle in sorted_baffles:
            for solid_name, phys in baffle.get("patchPhysics", {}).items():
                baffle_physics[_sanitize(solid_name)] = phys

        # Second pass: remaining-area solids get model-level physics
        for solid in self._stl_solids.get("merged_baffle.stl", []):
            san_solid = _sanitize(solid)
            if san_solid in baffle_physics:
                continue
            for baffle_name, baffle in sorted_baffles:
                san_bn = _sanitize(baffle_name)
                if san_solid.startswith(san_bn + "_") or san_solid == san_bn:
                    model_phys = baffle.get("physics", {})
                    entry = {
                        "baffleType": model_phys.get("baffleType", "wall"),
                        "porosity": model_phys.get("porosity", 0),
                    }
                    if model_phys.get("perforated"):
                        entry["perforated"] = dict(model_phys["perforated"])
                    baffle_physics[san_solid] = entry
                    break

        # Third pass: rack inlet/outlet perforated doors.
        # Without this, _generate_createBaffles_dict step 3 would default to
        # {baffleType:"wall", porosity:0}, misclassifying every door as a wall
        # baffle regardless of the user's chosen perforated open-area pct.
        # 2026-04-16 unification: rack doors now carry the same `perforated`
        # block as baffles/floors (instead of a fraction-valued `porosity`).
        # We carry the block through here so downstream classifiers and
        # bridge_bc can derive sigma from openAreaPct identically.
        DEFAULT_DOOR_PERF = {"shape": "circle", "holeDiameterMm": 3.0, "openAreaPct": 64.0}
        for rack_name, rack in data.get("racks", {}).items():
            rack_san = _sanitize(rack_name)
            for side in ("inlet", "outlet"):
                door = rack.get(f"{side}Door")
                if not door or not door.get("enabled"):
                    continue
                solid = _sanitize(door.get("solidName", f"{rack_san}_{side}_door"))
                perf = door.get("perforated") or DEFAULT_DOOR_PERF
                baffle_physics[solid] = {
                    "baffleType": "perforated",
                    "perforated": dict(perf),
                }

        self._baffle_physics = baffle_physics

    def _split_internal_face_solids(self) -> None:
        """Categorize and merge baffle/door/wallBaffle solids into grouped STL files.

        Instead of splitting every baffle/door solid into individual baffle_<name>.stl
        files (which creates hundreds of files for large cases, killing snappyHexMesh
        performance), solids are merged into category-grouped files:

        - merged_wall_baffle.stl: all wall baffles (baffle models with baffleType=wall,
          rack wallBaffle solids, floor wallBaffle remaining-area)
        - merged_rack_door.stl: all rack perforated doors
        - perforated_baffle_<name>.stl: perforated baffle model solids (individual)
        - perforated_floor_<name>.stl: perforated floor opening patches (individual)

        Each merged/individual file uses surface-level faceType internal (no named
        regions in the geometry section) and per-solid faceZones.
        """
        data = self._data
        baffle_physics = self._baffle_physics

        # ── Collect all candidate solids with their source file + original name ──
        # {sanitized_name: (source_stl_filename, original_solid_name)}
        candidates: Dict[str, tuple] = {}

        def _register(san: str, src_file: str) -> None:
            """Register a solid for extraction, resolving original name."""
            if san in candidates:
                return
            orig = san
            for s in self._stl_solids.get(src_file, []):
                if _sanitize(s) == san:
                    orig = s
                    break
            candidates[san] = (src_file, orig)

        # 1. Floor solids: wallBaffle remaining-area + perforated/wallBaffle openings
        #    Search both merged_floor_ceiling.stl and merged_baffle.stl (on re-runs,
        #    floor solids may have been restored to merged_baffle.stl).
        # Source files that may contain baffle/door/wallBaffle/floor-patch solids.
        # Includes both old naming (merged_baffle.stl, merged_perforated.stl) and
        # new browser export naming (merged_all_baffles.stl, merged_wall_baffles.stl,
        # merged_perforated_baffles.stl).
        _baffle_source_keys = [
            "merged_baffle.stl", "merged_perforated.stl",
            "merged_all_baffles.stl", "merged_wall_baffles.stl",
            "merged_perforated_baffles.stl",
        ]
        fc_key = "merged_floor_ceiling.stl"
        _floor_search_keys = [fc_key] + _baffle_source_keys
        for floor_name, floor in data.get("floors", {}).items():
            default_bc = floor.get("defaultBC", "wall")
            san_fn = _sanitize(floor_name)
            op_names = {_sanitize(op["solidName"]) for op in floor.get("openings", [])}
            if default_bc == "wallBaffle":
                for search_key in _floor_search_keys:
                    for s in self._stl_solids.get(search_key, []):
                        san = _sanitize(s)
                        if san.startswith(san_fn) and san not in op_names:
                            _register(san, search_key)
            for op in floor.get("openings", []):
                op_type = op.get("physics", {}).get("type", "wall")
                if op_type in ("perforated", "wallBaffle"):
                    san = _sanitize(op["solidName"])
                    # Find which source file has this solid
                    for search_key in _floor_search_keys:
                        if san in {_sanitize(s) for s in self._stl_solids.get(search_key, [])}:
                            _register(san, search_key)
                            break
                    else:
                        # Fallback: register against fc_key even if not found yet
                        _register(san, fc_key)

        # 2. Rack wallBaffle solids — search multiple source files.
        #    Skip solids already in blankingpanel bucket files — the frontend
        #    has routed them correctly and they must not be extracted.
        _blankingpanel_sans: set = set()
        for fn, sols in self._stl_solids.items():
            bkt = _parse_bucket_from_filename(fn)
            if bkt and bkt[0] in ("perforatedBlankingPanel", "wallBlankingPanel"):
                for s in sols:
                    _blankingpanel_sans.add(_sanitize(s))

        rack_wall_baffle_sans: set = set()
        _rack_search_keys = ["merged_rack.stl"] + _baffle_source_keys
        for rack_name, rack in data.get("racks", {}).items():
            for wb_solid in rack.get("wallBaffleSolids", []):
                san = _sanitize(wb_solid)
                if san in _blankingpanel_sans:
                    logger.info(f"Rack wallBaffle {san} already in blankingpanel bucket — skipping extraction")
                    continue
                rack_wall_baffle_sans.add(san)
                found = False
                for candidate_key in _rack_search_keys:
                    if san in {_sanitize(s) for s in self._stl_solids.get(candidate_key, [])}:
                        _register(san, candidate_key)
                        logger.info(f"Rack wallBaffle {san} found in {candidate_key}")
                        found = True
                        break
                if not found:
                    logger.warning(f"Rack wallBaffle {san} (from {rack_name}) NOT found in any source STL")

        # 3. ALL baffle model solids (from merged_baffle.stl and browser exports)
        for bf_key in _baffle_source_keys:
            for solid in self._stl_solids.get(bf_key, []):
                san = _sanitize(solid)
                _register(san, bf_key)

        # 4. Perforated solids (from merged_perforated.stl and browser exports)
        # Already covered by step 3 via _baffle_source_keys

        # 5. Rack doors from bucket files (merged_door_<N>U.stl) — since "door"
        #    detect door bucket files by prefix
        for filename, solids in list(self._stl_solids.items()):
            if filename.startswith("merged_door_"):
                for solid in solids:
                    _register(_sanitize(solid), filename)

        # 6. wallBaffle bucket files (blankingpanel buckets are NOT registered —
        #    they're already in the correct file and must not be extracted)
        for filename, solids in list(self._stl_solids.items()):
            bucket = _parse_bucket_from_filename(filename)
            if bucket and bucket[0] == "wallBaffle":
                for solid in solids:
                    _register(_sanitize(solid), filename)

        if not candidates:
            self._discard_or_keep_legacy_through_zone_stl()
            return

        # ── Categorize each solid into one of 4 groups ──
        # Build sets for classification
        door_solid_sans: set = set()
        for rack_name, rack in data.get("racks", {}).items():
            rack_san = _sanitize(rack_name)
            for side in ("inlet", "outlet"):
                door = rack.get(f"{side}Door")
                if door and door.get("enabled"):
                    solid_name = door.get("solidName", f"{rack_san}_{side}_door")
                    door_solid_sans.add(_sanitize(solid_name))

        floor_opening_sans: set = set()
        # Map floor opening sanitized names to their physics type from cfdrun
        floor_opening_type: Dict[str, str] = {}
        floor_wallbaffle_remaining_sans: set = set()
        for floor_name, floor in data.get("floors", {}).items():
            default_bc = floor.get("defaultBC", "wall")
            san_fn = _sanitize(floor_name)
            op_names = {_sanitize(op["solidName"]) for op in floor.get("openings", [])}
            for op in floor.get("openings", []):
                op_type = op.get("physics", {}).get("type", "wall")
                if op_type in ("perforated", "wallBaffle"):
                    san_op = _sanitize(op["solidName"])
                    floor_opening_sans.add(san_op)
                    floor_opening_type[san_op] = op_type
            if default_bc == "wallBaffle":
                # Search all possible source files for floor solids
                for search_key in _floor_search_keys:
                    for s in self._stl_solids.get(search_key, []):
                        san = _sanitize(s)
                        if san.startswith(san_fn) and san not in op_names:
                            floor_wallbaffle_remaining_sans.add(san)

        # Classify each candidate
        wall_baffle_solids: Dict[str, tuple] = {}   # san → (src_file, orig_name)
        door_solids: Dict[str, tuple] = {}           # san → (src_file, orig_name)
        perforated_baffle_solids: Dict[str, tuple] = {}  # san → (src_file, orig_name)
        perforated_floor_solids: Dict[str, tuple] = {}   # san → (src_file, orig_name)
        # Floor wallBaffle remaining-area solids get INDIVIDUAL files (one per
        # solid) so each obtains its own surface-level faceZone and therefore
        # its own createBaffles <solid>_master/_slave pair — mirroring the
        # working perforated_floor_*.stl pattern. Merging them into
        # merged_wall_baffle.stl instead produces a single shared
        # merged_wall_baffle_master/_slave pair (per-region faceZone is ignored
        # by snappyHexMesh), so the per-solid patches the manifest declares
        # (e.g. raised_floor_part1_master/_slave) never exist in the mesh.
        floor_wallbaffle_solids: Dict[str, tuple] = {}   # san → (src_file, orig_name)

        for san, (src_file, orig_name) in candidates.items():
            if san in door_solid_sans:
                door_solids[san] = (src_file, orig_name)
            elif san in floor_opening_sans:
                # Floor openings: use physics type from cfdrun data, not _baffle_physics
                op_type = floor_opening_type.get(san, "wall")
                if op_type == "perforated":
                    perforated_floor_solids[san] = (src_file, orig_name)
                else:
                    # wallBaffle floor opening → individual file (own faceZone)
                    floor_wallbaffle_solids[san] = (src_file, orig_name)
            elif san in floor_wallbaffle_remaining_sans:
                floor_wallbaffle_solids[san] = (src_file, orig_name)
            elif san in rack_wall_baffle_sans:
                wall_baffle_solids[san] = (src_file, orig_name)
            else:
                # Baffle model solid — classify by physics
                phys = baffle_physics.get(san, {})
                if phys.get("baffleType") == "perforated" or phys.get("porosity", 0) > 0:
                    perforated_baffle_solids[san] = (src_file, orig_name)
                else:
                    wall_baffle_solids[san] = (src_file, orig_name)

        logger.info(f"Baffle merge categorization: "
                    f"{len(wall_baffle_solids)} wall_baffle, "
                    f"{len(door_solids)} door, "
                    f"{len(perforated_baffle_solids)} perforated_baffle, "
                    f"{len(perforated_floor_solids)} perforated_floor, "
                    f"{len(floor_wallbaffle_solids)} floor_wallbaffle")

        # ── Read STL blocks from all source files ──
        all_src_files = set()
        for san, (src, _) in {**wall_baffle_solids, **door_solids,
                               **perforated_baffle_solids, **perforated_floor_solids,
                               **floor_wallbaffle_solids}.items():
            all_src_files.add(src)

        stl_blocks: Dict[str, Dict[str, str]] = {}  # {filename: {sanitized: block_text}}
        for filename in all_src_files:
            fpath = self.tri_surface_dir / filename
            if not fpath.exists():
                continue
            with open(fpath, "r", encoding="utf-8") as f:
                stl_text = f.read()
            blocks = re.findall(r'(^solid\s+(\S+).*?endsolid\s+\S+)',
                                stl_text, re.MULTILINE | re.DOTALL)
            stl_blocks[filename] = {}
            for block, name in blocks:
                stl_blocks[filename][_sanitize(name)] = block

        def _has_facets(block: str) -> bool:
            return "facet normal" in block

        # Helper to extract block text for a solid
        def _get_block(san: str, src_file: str) -> Optional[str]:
            return stl_blocks.get(src_file, {}).get(san)

        # ── Write merged_wall_baffle.stl ──
        # All wallBaffle solids (floor, baffle models, rack fullBlock) go here.
        # typicalSmallGap rack blanking is now routed by the frontend to
        # merged_blankingpanel_<N>U.stl and never appears in wallBaffle buckets.
        extracted_from: Dict[str, set] = {}  # track which solids extracted from which file

        if wall_baffle_solids:
            wb_path = self.tri_surface_dir / "merged_wall_baffle.stl"
            wb_parts = []
            wb_names = []
            for san, (src_file, orig_name) in wall_baffle_solids.items():
                block = _get_block(san, src_file)
                if block is None:
                    continue
                if not _has_facets(block):
                    logger.info(f"Skipping empty solid '{orig_name}' from {src_file} (no facets)")
                    extracted_from.setdefault(src_file, set()).add(san)
                    continue
                wb_parts.append(block)
                wb_names.append(orig_name)
                extracted_from.setdefault(src_file, set()).add(san)
            if wb_parts:
                with open(wb_path, "w", encoding="utf-8") as f:
                    f.write("\n".join(wb_parts) + "\n")
                self._stl_solids["merged_wall_baffle.stl"] = wb_names
                logger.info(f"Wrote merged_wall_baffle.stl: {len(wb_names)} solids")

        # ── Write merged_rack_door.stl ──
        if door_solids:
            dr_path = self.tri_surface_dir / "merged_rack_door.stl"
            dr_text_parts = []
            dr_orig_names = []
            for san, (src_file, orig_name) in door_solids.items():
                block = _get_block(san, src_file)
                if block is None:
                    continue
                if not _has_facets(block):
                    logger.info(f"Skipping empty solid '{orig_name}' from {src_file} (no facets)")
                    extracted_from.setdefault(src_file, set()).add(san)
                    continue
                dr_text_parts.append(block)
                dr_orig_names.append(orig_name)
                extracted_from.setdefault(src_file, set()).add(san)
            if dr_text_parts:
                with open(dr_path, "w", encoding="utf-8") as f:
                    f.write("\n".join(dr_text_parts) + "\n")
                self._stl_solids["merged_rack_door.stl"] = dr_orig_names
                logger.info(f"Wrote merged_rack_door.stl: {len(dr_orig_names)} solids")

        # ── Write individual perforated_baffle_<name>.stl ──
        for san, (src_file, orig_name) in perforated_baffle_solids.items():
            block = _get_block(san, src_file)
            if block is None:
                continue
            if not _has_facets(block):
                logger.info(f"Skipping empty solid '{orig_name}' from {src_file} (no facets)")
                extracted_from.setdefault(src_file, set()).add(san)
                continue
            ind_file = f"perforated_baffle_{san}.stl"
            ind_path = self.tri_surface_dir / ind_file
            with open(ind_path, "w", encoding="utf-8") as f:
                f.write(block + "\n")
            self._stl_solids[ind_file] = [orig_name]
            extracted_from.setdefault(src_file, set()).add(san)
            logger.info(f"Wrote {ind_file}")

        # ── Write individual perforated_floor_<name>.stl ──
        for san, (src_file, orig_name) in perforated_floor_solids.items():
            block = _get_block(san, src_file)
            if block is None:
                continue
            if not _has_facets(block):
                logger.info(f"Skipping empty solid '{orig_name}' from {src_file} (no facets)")
                extracted_from.setdefault(src_file, set()).add(san)
                continue
            ind_file = f"perforated_floor_{san}.stl"
            ind_path = self.tri_surface_dir / ind_file
            with open(ind_path, "w", encoding="utf-8") as f:
                f.write(block + "\n")
            self._stl_solids[ind_file] = [orig_name]
            extracted_from.setdefault(src_file, set()).add(san)
            logger.info(f"Wrote {ind_file}")

        # ── Write individual wallBaffle_floor_<name>.stl ──
        # Each floor wallBaffle solid lives in its own file so it gets its own
        # surface-level faceZone → its own createBaffles <solid>_master/_slave
        # pair (matching the manifest's porousBindings). Mirrors the
        # perforated_floor_*.stl handling above.
        for san, (src_file, orig_name) in floor_wallbaffle_solids.items():
            block = _get_block(san, src_file)
            if block is None:
                continue
            if not _has_facets(block):
                logger.info(f"Skipping empty solid '{orig_name}' from {src_file} (no facets)")
                extracted_from.setdefault(src_file, set()).add(san)
                continue
            ind_file = f"wallBaffle_floor_{san}.stl"
            ind_path = self.tri_surface_dir / ind_file
            with open(ind_path, "w", encoding="utf-8") as f:
                f.write(block + "\n")
            self._stl_solids[ind_file] = [orig_name]
            extracted_from.setdefault(src_file, set()).add(san)
            logger.info(f"Wrote {ind_file}")

        # ── Remove extracted solids from source files ──
        for filename, extracted_names in extracted_from.items():
            fpath = self.tri_surface_dir / filename
            if not fpath.exists():
                continue
            with open(fpath, "r", encoding="utf-8") as f:
                stl_text = f.read()
            blocks = re.findall(r'(^solid\s+(\S+).*?endsolid\s+\S+)',
                                stl_text, re.MULTILINE | re.DOTALL)
            keep_text = ""
            keep_names = []
            for block, name in blocks:
                if _sanitize(name) not in extracted_names:
                    keep_text += block + "\n"
                    keep_names.append(name)
            with open(fpath, "w", encoding="utf-8") as f:
                f.write(keep_text)
            if keep_names:
                self._stl_solids[filename] = keep_names
            else:
                self._stl_solids.pop(filename, None)
                fpath.unlink()
            logger.info(f"Updated {filename}: {len(keep_names)} solids remaining")

        # Legacy through-zone processing — discards or keeps merged_leakage.stl.
        # Under the new architecture (2026-04-17), through-zones go to
        # refinementRegions rather than cellZones; no STL splitting needed.
        self._discard_or_keep_legacy_through_zone_stl()

    def _discard_or_keep_legacy_through_zone_stl(self) -> None:
        """Handle legacy merged_leakage.stl from pre-2026-04-17 frontend exports.

        The new frontend emits merged_through_zone_<N>U.stl (per-bucket).
        Old exports still produce merged_leakage.stl (scene-merged). Under
        the new architecture (through-zone = refinementRegions, not cellZone),
        we no longer split merged_leakage.stl into individual leakage_<san>.stl
        files. Instead we:
        - Discard it if no racks have through-zones enabled.
        - Keep it in _stl_solids as-is if through-zones are enabled; the geometry
          block emits it as type triSurfaceMesh and the refinementRegions block
          emits it with mode inside (see _generate_snappy_hex_mesh_dict, the
          leakage_ prefix branch).

        Also cleans up stale individual leakage_<san>.stl files that may still
        exist from a previous run under the old splitting architecture.
        (formerly called _split_leakage_solids)
        """
        data = self._data
        any_through_zone_enabled = any(
            rack.get("leakage", {}).get("enabled", False)
            or rack.get("leakage", {}).get("groups", [])
            for rack in data.get("racks", {}).values()
        )
        lk_key = "merged_leakage.stl"
        if lk_key in self._stl_solids:
            if not any_through_zone_enabled:
                lk_path = self.tri_surface_dir / lk_key
                if lk_path.exists():
                    lk_path.unlink()
                del self._stl_solids[lk_key]
                logger.info(f"Discarded {lk_key} — no racks have through-zones enabled")
            else:
                logger.info(
                    f"Kept {lk_key} (legacy through-zone STL) — "
                    "will be emitted as refinementRegions entry (mode inside)"
                )
        # Clean up stale per-solid leakage files from old splitting runs.
        # Under the new architecture these are no longer needed.
        if self.tri_surface_dir.exists():
            for orphan in sorted(self.tri_surface_dir.glob("leakage_*.stl")):
                orphan.unlink(missing_ok=True)
                self._stl_solids.pop(orphan.name, None)
                logger.info(f"Removed stale individual through-zone file (old cellZone split): {orphan.name}")

    def _resolve_single_solid_baffle_file(
        self,
        zone_name: str,
        patch_kind: str,
        seed_config: Optional[Dict[str, Any]] = None,
    ) -> _PatchSeedSpec:
        # Try multiple naming conventions (post-merge filenames first):
        candidates = [
            f"perforated_floor_{zone_name}.stl",
            f"wallBaffle_floor_{zone_name}.stl",  # floor wallBaffle solid (a272a06a)
            f"perforated_baffle_{zone_name}.stl",
            f"baffle_{zone_name}.stl",            # legacy
        ]
        for stl_name in candidates:
            stl_path = self.tri_surface_dir / stl_name
            if stl_path.exists():
                solid_names = self._stl_solids.get(stl_name, [])
                if len(solid_names) != 1:
                    raise RuntimeError(
                        f"Expected {stl_name} to contain exactly one solid for patch-driven seed placement, "
                        f"found {len(solid_names)}"
                    )
                return _PatchSeedSpec(
                    zone_name=zone_name,
                    solid_name=solid_names[0],
                    stl_path=stl_path,
                    patch_kind=patch_kind,
                    seed_config=seed_config,
                )
        # Check multi-solid merged files (merged_wall_baffle.stl, merged_rack_door.stl)
        for merge_file in ("merged_wall_baffle.stl", "merged_rack_door.stl"):
            merge_solids = self._stl_solids.get(merge_file, [])
            san_zone = _sanitize(zone_name)
            for solid in merge_solids:
                if _sanitize(solid) == san_zone:
                    stl_path = self.tri_surface_dir / merge_file
                    if stl_path.exists():
                        return _PatchSeedSpec(
                            zone_name=zone_name,
                            solid_name=solid,
                            stl_path=stl_path,
                            patch_kind=patch_kind,
                            seed_config=seed_config,
                        )
        raise RuntimeError(
            f"Expected {patch_kind} patch '{zone_name}' to exist as one of "
            f"{[c for c in candidates]}, but none found. Re-export the case geometry before meshing."
        )

    def _collect_create_baffle_seed_specs(self) -> List[_PatchSeedSpec]:
        """Collect every patch that should keep cells on both sides via createBaffles."""
        data = self._data
        specs: List[_PatchSeedSpec] = []
        added: set = set()

        def add_zone(zone_name: str, patch_kind: str, seed_config: Optional[Dict[str, Any]] = None) -> None:
            san = _sanitize(zone_name)
            if san in added:
                return
            specs.append(self._resolve_single_solid_baffle_file(san, patch_kind, seed_config=seed_config))
            added.add(san)

        for floor in data.get("floors", {}).values():
            for op in floor.get("openings", []):
                op_type = op.get("physics", {}).get("type", "wall")
                if op_type in ("perforated", "wallBaffle"):
                    add_zone(op["solidName"], f"floor_opening_{op_type}", op.get("seedConfig"))

        for floor_name, floor in data.get("floors", {}).items():
            default_bc = floor.get("defaultBC", "wall")
            if default_bc not in ("wallBaffle", "perforated"):
                continue
            san_floor_name = _sanitize(floor_name)
            opening_names = {_sanitize(op["solidName"]) for op in floor.get("openings", [])}
            default_seed_map = floor.get("defaultBCSeedConfigs", {}) or {}
            default_seed_cfg = floor.get("defaultBCSeedConfig")
            for filename, solids in self._stl_solids.items():
                if not (filename.startswith("baffle_")
                        or filename.startswith("perforated_floor_")
                        or filename.startswith("wallBaffle_floor_")
                        or filename == "merged_wall_baffle.stl"):
                    continue
                for solid in solids:
                    san = _sanitize(solid)
                    if san.startswith(san_floor_name) and san not in opening_names:
                        add_zone(
                            solid,
                            f"floor_surface_{default_bc}",
                            default_seed_map.get(solid) or default_seed_map.get(san) or default_seed_cfg,
                        )

        # 2026-04-17 redesign (Task 10): rack thin-baffle solids — perforated
        # doors AND single-face blanking panels (wallBaffle — the remaining
        # side of a half-deleted U-slot) — moved from per-solid
        # `baffle_<san>.stl` to bucket-merged `merged_door_<N>U.stl` /
        # `merged_blanking_<N>U.stl`. The legacy baffle_ filename resolver
        # above no longer matches them, so they stopped producing any
        # createBaffles seed spec. That in turn stopped
        # _build_patch_driven_none_seed_entries from placing the rack-cavity
        # seed pair that snappyHexMesh needs to recognise each rack interior
        # as fluid — cavity gets merged into the surrounding solid body,
        # every inner/cavity/fan-server/wallBaffle-neighbour face drops.
        #
        # Fix: scan the new merged_* bucket files directly and bind each
        # thin-baffle solid to its own spec.
        wall_baffle_solids_per_rack: Dict[str, str] = {}
        for rack_name, rack in data.get("racks", {}).items():
            for wb in rack.get("wallBaffleSolids", []):
                wall_baffle_solids_per_rack[_sanitize(wb)] = rack_name

        # Post-merge: merged_wall_baffle.stl / merged_rack_door.stl contain
        # multiple solids. Each solid needs its own seed spec.
        for merge_file, patch_kind in [
            ("merged_wall_baffle.stl", "rack_wallBaffle"),
            ("merged_rack_door.stl", "rack_door"),
        ]:
            if merge_file not in self._stl_solids:
                continue
            stl_path = self.tri_surface_dir / merge_file
            if not stl_path.exists():
                continue
            for solid in self._stl_solids[merge_file]:
                san = _sanitize(solid)
                if san in added:
                    continue
                specs.append(_PatchSeedSpec(
                    zone_name=san,
                    solid_name=solid,
                    stl_path=stl_path,
                    patch_kind=patch_kind,
                    seed_config=None,
                ))
                added.add(san)

        # Legacy bucket files (merged_door_<N>U.stl, merged_wallBaffle_<N>U.stl, etc.)
        for filename, file_solids in self._stl_solids.items():
            bucket = _parse_bucket_from_filename(filename)
            if not bucket:
                continue
            cat = bucket[0]
            if cat == "door":
                patch_kind = "rack_door"
                eligible_solids = {_sanitize(s): s for s in file_solids}
            elif cat == "wallBaffle":
                patch_kind = "rack_wallBaffle"
                eligible_solids = {_sanitize(s): s for s in file_solids}
            elif cat == "blanking":
                patch_kind = "rack_wallBaffle"
                eligible_solids = {
                    _sanitize(s): s for s in file_solids
                    if _sanitize(s) in wall_baffle_solids_per_rack
                }
            elif cat == "wallBlankingPanel":
                patch_kind = "rack_wallBaffle"
                eligible_solids = {_sanitize(s): s for s in file_solids}
            else:
                continue
            if not eligible_solids:
                continue
            stl_path = self.tri_surface_dir / filename
            if not stl_path.exists():
                continue
            for san, solid in eligible_solids.items():
                if san in added:
                    continue
                specs.append(_PatchSeedSpec(
                    zone_name=san,
                    solid_name=solid,
                    stl_path=stl_path,
                    patch_kind=patch_kind,
                    seed_config=None,
                ))
                added.add(san)

        baffle_seed_cfg_by_solid: Dict[str, Optional[Dict[str, Any]]] = {}
        sorted_baffles = sorted(
            data.get("baffles", {}).items(),
            key=lambda item: len(item[0]),
            reverse=True,
        )
        for baffle_name, baffle in sorted_baffles:
            for solid_name, phys in (baffle.get("patchPhysics", {}) or {}).items():
                baffle_seed_cfg_by_solid[_sanitize(solid_name)] = phys.get("seedConfig")
            for solid_name, seed_cfg in (baffle.get("wallRegionSeedConfigs", {}) or {}).items():
                baffle_seed_cfg_by_solid[_sanitize(solid_name)] = seed_cfg

        for filename, solids in self._stl_solids.items():
            if not (filename.startswith("baffle_")
                    or filename.startswith("perforated_baffle_")):
                continue
            for solid in solids:
                san = _sanitize(solid)
                if san in added:
                    continue
                phys = self._baffle_physics.get(san, {})
                if (_resolve_mesh_engine(data.get("meshConfig", {}) or {}) == "dcCart"
                        and re.fullmatch(r".+_u\d+_partition", san)):
                    # dcCart cuts these sheets after fluid connectivity is
                    # established; they do not block its initial flood fill.
                    # Per-slot seed offsets are unnecessary and can cross the
                    # neighbouring shelf in a tightly spaced rack.
                    continue
                if phys.get("baffleType") == "perforated" or phys.get("porosity", 0) > 0:
                    patch_kind = "baffle_model_perforated"
                else:
                    patch_kind = "baffle_model_wall"
                seed_cfg = baffle_seed_cfg_by_solid.get(san)
                if seed_cfg is None:
                    for baffle_name, baffle in sorted_baffles:
                        san_bn = _sanitize(baffle_name)
                        if san == san_bn or san.startswith(san_bn + "_"):
                            seed_cfg = baffle.get("seedConfig")
                            break
                add_zone(
                    solid,
                    patch_kind,
                    seed_cfg,
                )

        # Defensive regression guard: a rack u-slot partition must never end up
        # in the createBaffles seed-spec list. Partitions are single-face walls
        # (see design doc + _split_internal_face_solids comment). A future edit
        # that accidentally routes them here would hit RuntimeError placement
        # failures downstream (partition pitch < seed offset); surface the
        # architectural violation here instead.
        for spec in specs:
            assert not re.search(r"_u\d+_partition$", spec.zone_name), (
                f"Rack u-slot partition '{spec.zone_name}' must not enter "
                f"createBaffles seed specs — partitions are single-face walls "
                f"without dual-side seeds. Check _split_internal_face_solids "
                f"and the bucket-category dispatch above."
            )
        return specs

    def _build_stl_triangle_cache(self) -> Dict[str, Dict[str, List[Triangle]]]:
        cache: Dict[str, Dict[str, List[Triangle]]] = {}
        for filename in sorted(self._stl_solids):
            stl_path = self.tri_surface_dir / filename
            if not stl_path.exists():
                continue
            cache[filename] = _read_ascii_stl_triangles(stl_path)
        return cache

    def _build_patch_driven_none_seed_entries(self) -> List[Tuple[Vec3, str]]:
        """Generate dual-side none seeds only for createBaffles patches."""
        specs = self._collect_create_baffle_seed_specs()
        if not specs:
            return []

        mc = self._data.get("meshConfig", {})
        base_size = float(mc.get("baseSize", 0.25) or 0.25)
        if base_size <= 0:
            raise RuntimeError(f"meshConfig.baseSize must be positive, got {base_size}")
        rl_baffle = mc.get("baffleRefinementLevel", [1, 1])
        if not isinstance(rl_baffle, list):
            rl_baffle = [rl_baffle, rl_baffle]
        max_level = max(int(level) for level in rl_baffle) if rl_baffle else 0
        h_local = base_size / float(2 ** max_level)
        default_offset = 0.5 * h_local

        triangle_cache = self._build_stl_triangle_cache()
        scene_triangles = _flatten_scene_triangles(triangle_cache)

        seed_entries: List[Tuple[Vec3, str]] = []
        for spec in specs:
            triangles = triangle_cache.get(spec.stl_path.name, {}).get(spec.solid_name)
            if not triangles:
                raise RuntimeError(
                    f"Cannot generate seeds for {spec.patch_kind} patch '{spec.zone_name}': "
                    f"no triangles were parsed from {spec.stl_path.name}"
                )
            center, normal = _compute_planar_patch_geometry(triangles)
            anchor, direction, offset = _resolve_patch_seed_config(
                spec.seed_config,
                center=center,
                normal=normal,
                default_offset=default_offset,
            )

            ignored_surfaces = {(spec.stl_path.name, spec.solid_name)}
            if spec.stl_path.name in ("merged_wall_baffle.stl", "merged_rack_door.stl"):
                for sibling in self._stl_solids.get(spec.stl_path.name, []):
                    ignored_surfaces.add((spec.stl_path.name, sibling))

            clearance_pos, clearance_neg = _two_sided_clearance(
                anchor, direction, scene_triangles, ignored_surfaces
            )

            # The centroid is not on the sheet, or something stands right
            # against it: look for a point ON the sheet with room on both
            # sides before clamping the offset into a solid. A user-pinned
            # anchor (seedConfig.anchor) is kept as the user placed it.
            user_anchor = isinstance(spec.seed_config, dict) and spec.seed_config.get("anchor") is not None
            cur_min = (
                min(clearance_pos, clearance_neg)
                if clearance_pos is not None and clearance_neg is not None
                else -1.0
            )
            if not user_anchor and cur_min < offset:
                better = _best_sheet_seed_anchor(
                    triangles, direction, offset, scene_triangles, ignored_surfaces
                )
                if better is not None and min(better[1], better[2]) > cur_min:
                    logger.info(
                        f"Dual-side seed anchor for '{spec.zone_name}' moved from "
                        f"{_format_vec(anchor)} (clearance {max(cur_min, 0.0):.6f}) to "
                        f"{_format_vec(better[0])} on the sheet (clearance "
                        f"{min(better[1], better[2]):.6f}): the centroid lies in a "
                        "cut-out or against a solid"
                    )
                    anchor, clearance_pos, clearance_neg = better

            logger.info(
                "Dual-side seed patch="
                f"{spec.zone_name} kind={spec.patch_kind} center={_format_vec(center)} "
                f"anchor={_format_vec(anchor)} "
                f"direction={_format_vec(direction)} "
                f"normal={_format_vec(normal)} refinementLevel={max_level} "
                f"h_local={h_local:.6f} offset={offset:.6f}"
            )

            if clearance_pos is None or clearance_neg is None:
                raise RuntimeError(
                    "Cannot place dual-side seed for createBaffles patch "
                    f"'{spec.zone_name}' ({spec.patch_kind}). "
                    f"center={_format_vec(center)} anchor={_format_vec(anchor)} "
                    f"direction={_format_vec(direction)} "
                    f"normal={_format_vec(normal)} "
                    f"offset={offset:.6f} "
                    f"h_local={h_local:.6f} "
                    f"clearance_pos={'none' if clearance_pos is None else f'{clearance_pos:.6f}'} "
                    f"clearance_neg={'none' if clearance_neg is None else f'{clearance_neg:.6f}'}. "
                    "Reduce meshConfig.baseSize, increase baffleRefinementLevel, or adjust the geometry."
                )
            min_clearance = min(clearance_pos, clearance_neg)
            if min_clearance < offset:
                clamped = min_clearance * 0.8
                logger.warning(
                    f"Seed offset clamped for '{spec.zone_name}' ({spec.patch_kind}): "
                    f"{offset:.6f} → {clamped:.6f} (clearance {min_clearance:.6f} < offset)"
                )
                offset = clamped

            seed_entries.append((_vec_add(anchor, _vec_scale(direction, offset)), "none"))
            seed_entries.append((_vec_add(anchor, _vec_scale(direction, -offset)), "none"))

        return seed_entries

    # _build_leakage_cellzone_seed_entries — REMOVED 2026-04-17.
    # Through-zones (formerly called "leakage zones") are now modeled as
    # refinementRegions (mode inside), NOT as cellZones. No locationsInMesh
    # seed is needed — snappy's refinementRegions mode inside does not
    # require a seed point. See _build_locations_in_mesh_entries (above).
    # (formerly called _build_leakage_cellzone_seed_entries)

    def _build_partition_hole_cellzone_seed_entries(self) -> List[Tuple[Vec3, str]]:
        """Seed enabled partition-hole cellZones from helper STL centroids."""
        entries: List[Tuple[Vec3, str]] = []
        for part in self._data.get("partitionWalls", {}).values():
            for hole in part.get("holes", []):
                phys = hole.get("physics", {}) or {}
                # 2026-04-16: only leakageRate gates the source term.
                try:
                    rate = float(phys.get("leakageRate", 100.0))
                except (TypeError, ValueError):
                    continue
                if rate >= 100.0:
                    continue

                zone_name = hole.get("zoneName", "")
                zone_file = hole.get("zoneStlFile")
                if not zone_name or not zone_file:
                    continue

                zone_path = self.tri_surface_dir / zone_file
                if not zone_path.exists():
                    continue

                surface_name = _sanitize(Path(zone_file).stem)
                centroid = _compute_stl_solid_centroid(zone_path, surface_name)
                if centroid is None:
                    continue

                entries.append((centroid, surface_name))
                logger.info(
                    f"Partition hole seed: {surface_name} at {_format_vec(centroid)} "
                    "(cellZone seed, not a main fluid-domain seed)"
                )
        return entries

    def _check_dcCart_memory(self, n_procs: int = 1) -> None:
        """Refuse before dcCartMesh starts when ``maxGlobalCells`` cannot fit.

        Runs right after ``_load()`` — before geometry validation, STL
        preparation and dictionary generation — so a refusal costs seconds and
        leaves the previous mesh untouched. ``maxGlobalCells`` is a FLOOR on
        the octree's leaf count, so only a certain failure (the lower-bound
        law exceeds the budget) is refused; the estimate is logged, and when
        it does not fit either the mesher's own guard (exact count after the
        octree balance) has the last word. The peak grows with the rank
        count (every rank duplicates the octree and carve), so the number of
        ranks is part of the law, not just of the message.
        """
        mc = self._data.get("meshConfig", {}) or {}
        cap = _dcCart_max_global_cells(mc)
        n = max(1, int(n_procs or 1))
        if not _mem_guard_enabled():
            logger.warning(
                f"dcCart memory guard disabled ({DCCART_MEM_GUARD_ENV}=off): "
                f"maxGlobalCells {cap:,} on {n} rank(s) unchecked"
            )
            return
        budget = _dcCart_mem_budget_bytes()
        if budget is None:
            logger.warning(
                "dcCart memory guard: MemAvailable unreadable (/proc/meminfo); "
                "skipping the preflight check"
            )
            return
        est = _dcCart_mem_estimate_bytes(cap, n)
        low = _dcCart_mem_lower_bound_bytes(cap, n)
        gb = 1e9
        logger.info(
            f"dcCart memory guard: maxGlobalCells {cap:,} on {n} rank(s): estimate "
            f"{est / gb:.1f} GB at the cap (a floor: the octree usually overshoots it by "
            f"up to one level), {budget / gb:.1f} GB available (MemAvailable / cgroup)"
        )
        if low > budget:
            fit = _dcCart_mem_fit_leaves(budget, n)
            raise MeshMemoryError(
                f"dcCart memory guard: maxGlobalCells {cap:,} on {n} rank(s) needs at least "
                f"{low / gb:.0f} GB (estimate {est / gb:.0f} GB) but only {budget / gb:.0f} GB "
                f"is available on this host right now. Lower meshConfig.maxGlobalCells to at most "
                f"{fit:,} on {n} rank(s), reduce the refinement levels, or use FEWER ranks: every "
                f"rank duplicates the octree and carve, so more ranks need MORE memory. "
                f"{DCCART_MEM_GUARD_ENV}=off runs anyway at your own risk."
            )
        if est + DCCART_MEM_HEADROOM_BYTES > budget:
            logger.warning(
                f"dcCart memory guard: the estimate at the cap ({est / gb:.0f} GB + "
                f"{DCCART_MEM_HEADROOM_BYTES / gb:.0f} GB headroom) exceeds the {budget / gb:.0f} GB "
                f"available; dcCartMesh re-checks with the exact leaf count after its octree "
                f"balance and refuses there if it does not fit"
            )

    def _preflight_floor_openings(self) -> None:
        """Refuse a triSurface whose floor openings overhang their floor parts.

        The perforated / wall-baffle floor openings and the floor's remaining
        parts are exported together; an opening lying partly OVER a part means
        the two files come from different exports (Base604, 2026-09-21: a June
        patch_2 beside a fresh part1 whose hole had moved by 19 mm). The
        cut-cell mesher tags the overlap strip to the part's faceZone, and the
        baffle coverage gate refuses the mesh only after the full cut, with a
        ratio that does not say why. Measure it from the STL now and name the
        fix. ``DCDTUNI_FLOOR_OVERLAP_CHECK=off`` demotes the refusal to a
        warning. See floor_consistency.py.
        """
        floors = (self._data or {}).get("floors") or {}
        if not floors or not self.tri_surface_dir.is_dir():
            return
        found = floor_consistency.check_floor_openings(self.tri_surface_dir, floors, _sanitize)
        if not found:
            return
        msg = floor_consistency.format_floor_overlaps(found)
        if os.environ.get(floor_consistency.ENV_OVERRIDE, "").strip().lower() in ("off", "0", "false", "no"):
            logger.warning(msg + f"\n({floor_consistency.ENV_OVERRIDE}=off: meshing anyway)")
            return
        raise RuntimeError(msg)

    def _preflight_partition_wall_thickness(self) -> None:
        """Warn (but do NOT auto-bump) when partitionWallRefinementLevel is
        too low for thin partition walls.

        The frontend auto-detect button (``_autoDetectPartitionWall``) now
        owns the safe-level calculation and sets the meshConfig value.  The
        runner respects whatever the user chose — control is fully on the
        frontend side.
        """
        walls = self._data.get("partitionWalls") or {}
        if not walls:
            return
        mc = self._data.get("meshConfig") or self._data.setdefault("meshConfig", {})
        base_size = float(mc.get("baseSize", 0.25) or 0.25)
        if base_size <= 0:
            raise RuntimeError(
                f"meshConfig.baseSize must be positive, got {base_size}"
            )
        rl = mc.get("partitionWallRefinementLevel",
                    mc.get("refinementLevel", [1, 1]))
        if not isinstance(rl, (list, tuple)):
            rl = [rl, rl]
        current_max = max(int(level) for level in rl) if rl else 0

        for part_name, part in walls.items():
            size = (part.get("geometry", {}) or {}).get("size", {}) or {}
            try:
                d = float(size.get("D", 0.1))
            except (TypeError, ValueError):
                continue
            if d <= 0:
                continue
            h_local = base_size / float(2 ** current_max)
            if d < 2.0 * h_local - 1e-9:
                min_level = current_max
                while base_size / float(2 ** min_level) > d / 2.0:
                    min_level += 1
                logger.warning(
                    f"Partition wall '{part_name}' D={d:.4f} m is thinner "
                    f"than 2·h_local={2*h_local:.4f} m at level {current_max}. "
                    f"Recommended level ≥ {min_level}. Use the frontend "
                    f"auto-detect button to set a safe level."
                )

    def _build_partition_wall_seed_entries(self) -> List[Tuple[Vec3, str]]:
        entries: List[Tuple[Vec3, str]] = []
        if not self._data.get("partitionWalls"):
            return entries

        triangle_cache = self._build_stl_triangle_cache()
        scene_triangles = _flatten_scene_triangles(triangle_cache)

        for part_name, part in self._data.get("partitionWalls", {}).items():
            geom = part.get("geometry", {})
            anchor = _dict_to_vec3(geom.get("anchor"), (0.0, 0.0, 0.0))
            size = geom.get("size", {})
            quat = geom.get(
                "transformQuaternion",
                {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
            )

            w = float(size.get("W", 1.0))
            d = float(size.get("D", 0.1))
            h = float(size.get("H", 3.0))
            if d < 0.0:
                raise RuntimeError(
                    f"Cannot place partition wall seeds for '{part_name}': "
                    f"geometry.size.D must be non-negative, got {d}"
                )

            wall_solid_names = part.get("wallSolidNames", [])
            ignored_surfaces, ignored_solid_names = _resolve_partition_wall_body_surfaces(
                triangle_cache,
                part_name,
                wall_solid_names,
            )
            ignored_solid_label = ",".join(ignored_solid_names)

            # Y-up convention (matches frontend BoxGeometry(W, H, D) +
            # translate(W/2, H/2, D/2) at STL_generate.html:6958-6960):
            #   W → local-X, H → local-Y (vertical), D → local-Z (thin axis).
            local_center = (w * 0.5, h * 0.5, d * 0.5)
            direction = _vec_normalize(_quat_rotate((0.0, 0.0, 1.0), quat))
            center = _vec_add(anchor, _quat_rotate(local_center, quat))
            offset = d * 0.5 + 0.002

            clearance_pos = _nearest_ray_clearance(
                center,
                direction,
                scene_triangles,
                ignored_surfaces,
            )
            clearance_neg = _nearest_ray_clearance(
                center,
                _vec_scale(direction, -1.0),
                scene_triangles,
                ignored_surfaces,
            )
            failed = (
                clearance_pos is None
                or clearance_neg is None
                or clearance_pos < offset
                or clearance_neg < offset
            )
            if failed:
                raise RuntimeError(
                    "Cannot place dual-side seed for partition wall "
                    f"'{part_name}'. "
                    f"wall_solid={ignored_solid_label or 'unknown'} "
                    f"anchor={_format_vec(anchor)} "
                    f"center={_format_vec(center)} "
                    f"direction={_format_vec(direction)} "
                    f"quat=({float(quat.get('x', 0.0)):.6f}, {float(quat.get('y', 0.0)):.6f}, "
                    f"{float(quat.get('z', 0.0)):.6f}, {float(quat.get('w', 1.0)):.6f}) "
                    f"size=({w:.6f}, {d:.6f}, {h:.6f}) "
                    f"offset={offset:.6f} "
                    f"clearance_pos={'none' if clearance_pos is None else f'{clearance_pos:.6f}'} "
                    f"clearance_neg={'none' if clearance_neg is None else f'{clearance_neg:.6f}'}. "
                    "Adjust the partition wall placement/orientation or nearby geometry so both "
                    "seed points have at least thickness/2 + 0.002 m of clear space."
                )

            entries.append((_vec_add(center, _vec_scale(direction, offset)), "none"))
            entries.append((_vec_add(center, _vec_scale(direction, -offset)), "none"))
        return entries

    #: A seed is treated as "on" a grid plane when it is within this fraction
    #: of the local cell size of one. Exactly-on is the case that actually
    #: bites, but a near-miss is just as ambiguous to findCell's tet test.
    _SEED_PLANE_BAND = 1e-3
    #: How far off the plane to push it, as a fraction of the local cell size.
    _SEED_PLANE_NUDGE = 0.25

    def _nudge_seeds_off_grid_planes(
        self, entries: List[Tuple[Vec3, str]]
    ) -> List[Tuple[Vec3, str]]:
        """Move locationsInMesh seeds that sit exactly on a background grid plane.

        snappyHexMesh resolves every seed with polyMesh::findCell, whose tet
        test rejects a point lying ON a cell face: it belongs to neither
        neighbour. The run then dies at the very first step with
        "Point (...) is not inside the mesh or on a face or edge".

        This is not a rare coincidence in `structured` mode, it is the norm.
        Grid lines are deliberately placed on object faces, and a dual-side
        baffle seed is offset along the patch NORMAL only — its other two
        coordinates stay at the patch centroid, which is exactly where a grid
        line tends to be. Alibaba-hongchou died on z=7.25 for this reason: a
        horizontal patch, seed offset in y, z landing dead on a plane.

        The nudge is deliberately small (a quarter of the local cell) and goes
        toward the roomier neighbour, so a seed stays well inside the region it
        was placed to identify.
        """
        planes_by_axis = getattr(self, "_bg_grid_planes", None)
        if not planes_by_axis:
            return entries

        import bisect

        out: List[Tuple[Vec3, str]] = []
        for point, zone in entries:
            coords = list(point)
            moved = False
            for i, axis in enumerate("xyz"):
                planes = planes_by_axis.get(axis) or []
                if len(planes) < 2:
                    continue
                v = coords[i]
                j = bisect.bisect_left(planes, v)
                # nearest plane and the intervals either side of it
                cand = [k for k in (j - 1, j, j + 1) if 0 <= k < len(planes)]
                if not cand:
                    continue
                k = min(cand, key=lambda idx: abs(planes[idx] - v))
                plane = planes[k]
                below = planes[k] - planes[k - 1] if k > 0 else 0.0
                above = planes[k + 1] - planes[k] if k + 1 < len(planes) else 0.0
                local = max(below, above)
                if local <= 0:
                    continue
                if abs(v - plane) > self._SEED_PLANE_BAND * local:
                    continue
                # push into the roomier of the two adjacent cells
                if above >= below:
                    coords[i] = plane + self._SEED_PLANE_NUDGE * above
                else:
                    coords[i] = plane - self._SEED_PLANE_NUDGE * below
                moved = True
            if moved:
                logger.info(
                    f"locationsInMesh seed {_format_vec(tuple(point))} sat on a "
                    f"background grid plane; nudged to "
                    f"{_format_vec(tuple(coords))} (findCell rejects points on a face)"
                )
            out.append(((coords[0], coords[1], coords[2]), zone))
        return out

    def _build_locations_in_mesh_entries(self) -> List[Tuple[Vec3, str]]:
        room = self._data.get("room", {})
        room_height = float(room.get("height", 3.0) or 3.0)
        # Footprint bounds in runner space: plane.x -> X, plane.y -> Z (depth),
        # height -> Y (vertical). Mirrors _generate_block_mesh_dict().
        plane = room.get("plane", [])
        if plane:
            xs = [float(p["x"]) for p in plane]
            zs = [float(p["y"]) for p in plane]
            rx_min, rx_max = min(xs), max(xs)
            rz_min, rz_max = min(zs), max(zs)
        else:
            rx_min, rx_max, rz_min, rz_max = 0.0, 10.0, 0.0, 10.0
        # Y-up: y is vertical (1.5 m mid-height), z is floor-plan depth.
        loc = room.get("locationInMesh", {"x": 5.0, "y": 1.5, "z": 5.0})

        # The primary seed point MUST lie inside the domain or snappyHexMesh aborts
        # ("Point ... is not inside the mesh or on a face or edge"). An un-customized
        # default seed (e.g. 5,4,1.5) falls outside small rooms. Snap any out-of-bounds
        # coordinate to the room centre on that axis (best-effort interior point);
        # leave valid coordinates intact so user-positioned seeds are unaffected.
        def _snap(val: float, lo: float, hi: float, axis: str) -> float:
            if val < lo or val > hi:
                mid = 0.5 * (lo + hi)
                logger.warning(
                    f"Room locationInMesh {axis}={val} outside domain [{lo}, {hi}]; "
                    f"relocated to room centre {axis}={mid}"
                )
                return mid
            return val

        loc_x = _snap(float(loc["x"]), rx_min, rx_max, "X")
        loc_y = _snap(float(loc["y"]), 0.0, room_height, "Y")
        loc_z = _snap(float(loc["z"]), rz_min, rz_max, "Z")
        entries: List[Tuple[Vec3, str]] = [
            ((loc_x, loc_y, loc_z), "none")
        ]

        # User-specified extra seed points (all none mode)
        for eloc in room.get("extraLocationsInMesh", []):
            entries.append(((float(eloc["x"]), float(eloc["y"]), float(eloc["z"])), "none"))

        entries.extend(self._build_patch_driven_none_seed_entries())
        # 2026-04-17: through-zone seed entries removed — through-zones are now
        # refinementRegions (mode inside), not cellZones. No locationsInMesh seed needed.
        entries.extend(self._build_partition_hole_cellzone_seed_entries())
        entries.extend(self._build_partition_wall_seed_entries())
        return _dedupe_seed_entries(self._nudge_seeds_off_grid_planes(entries))

    def _ensure_config(self) -> None:
        """Backward-compatible no-op wrapper for the old compatibility layer."""
        self._prepare_runtime_metadata()

    # ------------------------------------------------------------------
    # Bridge BC registry
    # ------------------------------------------------------------------

    def _get_rack_width(self, rack_data: dict) -> float:
        """Get rack width in meters from rack data."""
        # Try geometry.size.W (.cfdrun from STLGenerator)
        geom = rack_data.get("geometry", {})
        size = geom.get("size", {})
        width = size.get("W") or size.get("w") or size.get("width")
        if width and float(width) > 0:
            return float(width)
        # Fallback: base.width (legacy format)
        base = rack_data.get("base", {})
        width = base.get("width") or base.get("w") or base.get("W")
        if width and float(width) > 0:
            return float(width)
        return 0.6  # default rack width 600mm

    def _collect_liquid_cooled_server_ids(self) -> set:
        """Return the set of server IDs that have liquid cooling enabled.

        A server is liquid-cooled if its `liquidCooling.enabled` flag is true
        in the .cfdrun manifest. Used by the liquid-results completeness check.
        """
        liquid_ids: set = set()
        data = self._data
        for rack in data.get("racks", {}).values():
            for srv in rack.get("servers", []):
                lc = srv.get("liquidCooling")
                if lc and lc.get("enabled", False):
                    sid = srv.get("id")
                    if sid:
                        liquid_ids.add(sid)
        return liquid_ids

    # ------------------------------------------------------------------
    # Mesh dict generation
    # ------------------------------------------------------------------

    def _generate_mesh_dicts(self) -> None:
        """Generate all OpenFOAM mesh dictionaries."""
        system_dir = self.mesh_dir / "system"
        system_dir.mkdir(parents=True, exist_ok=True)
        (self.mesh_dir / "constant" / "triSurface").mkdir(parents=True, exist_ok=True)

        self._generate_block_mesh_dict(system_dir)
        self._generate_surface_feature_extract_dict(system_dir)
        self._generate_snappy_hex_mesh_dict(system_dir)
        self._generate_create_baffles_dict()

    # STL files whose OUTER faces should become background grid lines (major
    # axis-aligned objects). Fine internal detail (servers, U-slots,
    # through-zones, blanking) is deliberately excluded so grid planes do not
    # propagate across the whole domain — that detail is resolved by octree
    # refinementSurfaces/Regions instead.
    _ALIGN_STL_FILES = (
        "merged_rack_frame.stl", "merged_rack.stl", "merged_acu.stl",
        "merged_baffle.stl", "merged_partition_wall.stl", "merged_box.stl",
        "merged_floor_ceiling.stl",
    )

    def _collect_alignment_planes(self) -> Dict[str, List[float]]:
        """Collect axis-aligned face coordinates from major-object STL AABBs.

        Used by :func:`compute_aligned_grid` to place background grid lines on
        solid surfaces (the 6Sigma method), so a snap-off Cartesian mesh has
        exact straight boundaries instead of half-cell-shifted / stepped ones.
        """
        planes: Dict[str, List[float]] = {"x": [], "y": [], "z": []}
        # Fixed major-object files PLUS variable-named baffles. Perforated/solid
        # raised-floor and false-ceiling slabs export to variable per-object names
        # (perforated_floor_*, wallBaffle_floor_*) that were missing from
        # _ALIGN_STL_FILES, so their Y plane never became a grid line and the slab
        # went bumpy under snap-off. Glob them in so the slab face is aligned too.
        #
        # The SAME gap existed for the VERTICAL containment baffles — in-room wall
        # baffles (merged_wall_baffle*) and the AHU/hot-aisle partition
        # (perforated_baffle_*). Their panel plane fell mid-cell, so snap-off
        # stair-stepped the panel edge. Glob them in too so an axis-aligned
        # containment panel gets a flush grid line on its plane (and outline).
        align_files: List[str] = list(self._ALIGN_STL_FILES)
        for pat in ("perforated_floor_*.stl", "wallBaffle_floor_*.stl",
                    "merged_wall_baffle*.stl", "perforated_baffle_*.stl"):
            align_files += sorted(p.name for p in self.tri_surface_dir.glob(pat))
        for fn in align_files:
            p = self.tri_surface_dir / fn
            if not p.exists():
                continue
            try:
                boxes = _solid_bounding_boxes(p)
            except Exception as exc:
                logger.warning(f"grid-align: could not read AABB from {fn}: {exc}")
                continue
            for lo, hi in boxes.values():
                planes["x"] += [lo[0], hi[0]]
                planes["y"] += [lo[1], hi[1]]
                planes["z"] += [lo[2], hi[2]]

        return planes

    # _rack_envelope_y_planes — TRIED AND REVERTED, 2026-08-26.
    #
    # The topmost slot's top face carries no mesh face, so its boundary
    # condition has nothing to attach to (28 regions on Base2-9, 71 on Base604,
    # 137 on Alibaba-sige). The obvious fix — put a background grid line on that
    # plane, one per rack, since server geometry is deliberately excluded from
    # _ALIGN_STL_FILES — was implemented and MEASURED on Base2-9:
    #
    #     cells   2,960,733 -> 6,292,687   (+113%)
    #     time          165s -> 270s
    #     patches      1,779 -> 1,779      (zero recovered)
    #
    # 110 racks reduced to 14 distinct heights, and 14 extra domain-spanning Y
    # planes is what doubled the mesh. It bought nothing because a grid line is
    # not what is missing. Probing a 4 cm column through the footprint of
    # `A1_A1_DH_S6500_U45_top` (plane y = 2.752174), the face planes run
    #
    #     ... 2.69239  2.70435  |  2.8  2.82104 ...
    #
    # with local spacing ~0.012 m. The plane sits in a ~0.096 m gap — eight
    # cells wide — where every cell has been carved away. The blockMeshDict DOES
    # carry a breakpoint at 2.752174; no face survives on it because the whole
    # band is solid. Adding grid lines cannot create a face in carved space.
    #
    # These surfaces are enclosed, not unresolved. mesh_acceptance now tells the
    # two apart by asking whether a CELL actually occupies the gap the plane
    # falls in, rather than whether faces merely exist somewhere either side.

    def _generate_block_mesh_dict(self, system_dir: Path) -> None:
        """Generate blockMeshDict from room boundaries, per mesh mode.

        A UNIFORM coarse background grid for ``bodyFitted`` (snap on) and
        ``cartesianLocalized`` (snap off); only ``structured`` inserts breakpoints
        on object faces (``compute_aligned_grid``) for perfectly straight snap-off
        boundaries at the cost of domain-spanning grid lines (cross-hatch).

        ``cartesianLocalized`` is a *pure* octree Cartesian grid: it shares the
        uniform background with snap-on but its fine detail comes from per-object
        local ``refinementRegions`` (in snappyHexMeshDict) — orthogonal, with no
        cross-hatch / 十字加密. See ``_resolve_mesh_mode``.
        """
        data = self._data
        mc = data.get("meshConfig", {})
        base_size = mc.get("baseSize", 0.25)

        plane = data.get("room", {}).get("plane", [])
        h = data.get("room", {}).get("height", 3.0)
        if plane:
            xs = [p["x"] for p in plane]
            zs = [p["y"] for p in plane]
            rx_min, rx_max = min(xs), max(xs)
            rz_min, rz_max = min(zs), max(zs)
        else:
            rx_min, rx_max, rz_min, rz_max = 0.0, 10.0, 0.0, 10.0
        ry_min, ry_max = 0.0, h

        room_bounds = {"x": (rx_min, rx_max), "y": (ry_min, ry_max), "z": (rz_min, rz_max)}
        mesh_mode = _resolve_mesh_mode(mc)
        if mesh_mode == "structured":
            # STRUCTURED (结构网格): 6Sigma-style grid-aligned Cartesian — place grid
            # lines on object faces so snap-off boundaries are exact + straight (no
            # stair-step / half-cell shift). The trade-off is that each grid line
            # spans the whole domain (the cross-hatch / 十字加密). Internal fine detail
            # still gets octree refinement.
            align_planes = self._collect_alignment_planes()
            mb = compute_aligned_grid(room_bounds, base_size, align_planes)
            n_breaks = {k: len({round(s[0], 4) for s in v} | {round(s[1], 4) for s in v}) - 2
                        for k, v in mb.items()}
            logger.info(
                f"mesh mode=structured: aligned background, interior breakpoints/axis={n_breaks} "
                f"(grid lines on object faces → straight snap-off boundaries; domain-spanning)"
            )
        elif (
            mesh_mode == "cartesianLocalized"
            and _resolve_mesh_engine(mc) == "dcCart"
            and os.environ.get("DCDTUNI_DCCART_ALIGNED_GRID", "0") == "1"
        ):
            # dcCart's own grid contract is the OPPOSITE of a uniform background:
            # "Breakpoints sit ON object faces, which is what lets an axis-aligned
            # rack or ACU mesh with zero stair-stepping — the cut lands exactly on
            # the geometry" (dcCartGrid/dcCartGrid.H). Feeding it the uniform grid
            # breaks that contract, and Base604 pays for it with a 3.26 mm sliver
            # column: x=11.400000 carries 448 exported vertices from files
            # _collect_alignment_planes already reads (merged_rack_frame 270,
            # wallBaffle_floor_* 62, merged_wall_baffle 48, perforated_floor_* 6),
            # yet the nearest level-4 plane of the uniform interval
            # [11.1913043, 11.5304348] is 11.4032609. Every cell of that column is
            # a sliver (volume fraction 0.0029), its four neighbours are slivers
            # too (0.0909 / 0.0070 / 0.0319 / 0.0450 — all under
            # minCutVolumeFraction, so merge passes 0-4 have no admissible host at
            # all), pass 5 can only merge ALONG the column which does not widen it,
            # and the two neighbours that would widen it in x both give
            # non-convex unions. So the 3.26 mm face survives to checkMesh as
            # skewness 21.4 (22 failing faces). Aligning the background removes
            # the column at its source: it moves no geometry and creates no
            # non-convex merge, both of which were measured and rejected.
            #
            # OFF BY DEFAULT: measured on Base604, the aligned background does
            # deliver on the mesh side (max skewness 21.3791 -> 4.92712, 22 ->
            # 16 failing faces, Shinaide max aspect 130.4 -> 11.6, all three
            # clean cases still Mesh OK with better skewness) but it BREAKS THE
            # SOLVE -- buoyantBoussinesqSimpleFoam_new diverged at iteration 93
            # with continuity error sum local 6.46473e+06 on the aligned
            # 4,227,537-cell mesh, while the uniform-background 3,550,198-cell
            # mesh of the same case ran 1000/1000 steps to `End`, rc=0, zero
            # NaN. A better checkMesh report is not a better mesh: alignment
            # costs +19% cells and +64% max aspect ratio, and whatever cell
            # that buys diverges the pressure equation. Requirement (2)
            # outranks (1), so this stays opt-in (DCDTUNI_DCCART_ALIGNED_GRID=1)
            # for experimentation and is not the shipped default.
            align_planes = self._collect_alignment_planes()
            mb = compute_aligned_grid(room_bounds, base_size, align_planes)
            n_breaks = {k: len({round(s[0], 4) for s in v} | {round(s[1], 4) for s in v}) - 2
                        for k, v in mb.items()}
            logger.info(
                "mesh mode=cartesianLocalized (dcCart): ALIGNED background, interior "
                f"breakpoints/axis={n_breaks} (grid lines on object faces, per the "
                "dcCartGrid contract) + per-object local refinementRegions"
            )
        else:
            # bodyFitted (snap on) uses a uniform coarse background: snappy's own
            # refinement plus the snap stage place the boundary, so background grid
            # lines would only add domain-spanning cross-hatch.
            # TARGETED alignment -- TRIED AND REVERTED, 2026-09-14. Inserting a
            # breakpoint only where a major-object face plane lands off the
            # level-4 lattice by <= 0.2*finest (19 breakpoints on Base604 vs
            # full alignment's 63, 20 on Shinaide) failed on BOTH cases, in two
            # different ways:
            #   Base604   redistributePar FATAL "face 2211 area does not match
            #             neighbour by 113.043%" on procBoundary2to1
            #             (6.136e-4 vs 1.705e-4 m2) -- dcCartMesh's ranks
            #             disagreed about a processor-interface face, i.e. the
            #             "one algorithm on any rank count" contract broken by
            #             a non-uniform background; dcCartMesh also slowed
            #             68 s -> 123.7 s.
            #   Shinaide  DCCART_MERGE_TIMEOUT after 240 s in the sliver merge
            #             -- a pathological sliver field. The design flaw: the
            #             off-lattice test is made against the ORIGINAL
            #             segment's lattice, but splitting it at p recomputes
            #             n1/n2 = round(len/base_size), so the lattice moves
            #             and every OTHER surface in that segment becomes
            #             newly off-lattice. It creates slivers, not fewer.
            mb = compute_multiblock_grid(room_bounds, base_size)
            if mesh_mode == "cartesianLocalized":
                logger.info(
                    "mesh mode=cartesianLocalized: uniform background + per-object local "
                    "refinementRegions (no domain-spanning grid lines / cross-hatch)"
                )

        x_segs = mb["x"]
        y_segs = mb["y"]
        z_segs = mb["z"]

        x_breaks = sorted({s[0] for s in x_segs} | {s[1] for s in x_segs})
        y_breaks = sorted({s[0] for s in y_segs} | {s[1] for s in y_segs})
        z_breaks = sorted({s[0] for s in z_segs} | {s[1] for s in z_segs})

        # Cache EVERY cell-face plane (each block segment is subdivided
        # uniformly into its own cell count), not just the block breakpoints.
        # _build_locations_in_mesh_entries needs them to keep locationsInMesh
        # seeds off the planes — see _nudge_seeds_off_grid_planes.
        def _cell_planes(segs) -> List[float]:
            planes: Set[float] = set()
            for lo, hi, n in segs:
                n = max(1, int(n))
                for i in range(n + 1):
                    planes.add(round(lo + (hi - lo) * i / n, 9))
            return sorted(planes)

        self._bg_grid_planes = {
            "x": _cell_planes(x_segs),
            "y": _cell_planes(y_segs),
            "z": _cell_planes(z_segs),
        }

        nx = len(x_breaks) - 1
        ny = len(y_breaks) - 1
        nz = len(z_breaks) - 1

        seg_nx = [seg[2] for seg in x_segs]
        seg_ny = [seg[2] for seg in y_segs]
        seg_nz = [seg[2] for seg in z_segs]

        def vidx(ix, iy, iz):
            return ix + iy * (nx + 1) + iz * (nx + 1) * (ny + 1)

        vertices = []
        for iz in range(nz + 1):
            for iy in range(ny + 1):
                for ix in range(nx + 1):
                    vertices.append(f"    ( {x_breaks[ix]} {y_breaks[iy]} {z_breaks[iz]} )")
        vert_str = "\n".join(vertices)

        blocks = []
        for iz in range(nz):
            for iy in range(ny):
                for ix in range(nx):
                    v = [vidx(ix, iy, iz), vidx(ix+1, iy, iz),
                         vidx(ix+1, iy+1, iz), vidx(ix, iy+1, iz),
                         vidx(ix, iy, iz+1), vidx(ix+1, iy, iz+1),
                         vidx(ix+1, iy+1, iz+1), vidx(ix, iy+1, iz+1)]
                    vstr = " ".join(str(i) for i in v)
                    blocks.append(
                        f"    hex ( {vstr} ) ( {seg_nx[ix]} {seg_ny[iy]} {seg_nz[iz]} ) "
                        f"simpleGrading ( 1 1 1 )")
        block_str = "\n".join(blocks)

        total_cells = sum(seg_nx) * sum(seg_ny) * sum(seg_nz)
        bm_min_x, bm_max_x = x_breaks[0], x_breaks[-1]
        bm_min_y, bm_max_y = y_breaks[0], y_breaks[-1]
        bm_min_z, bm_max_z = z_breaks[0], z_breaks[-1]

        content = f"""\
/*--------------------------------*- C++ -*----------------------------------*\\
| =========                 |                                                 |
| \\\\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox           |
|  \\\\    /   O peration     |                                                 |
|   \\\\  /    A nd           |                                                 |
|    \\\\/     M anipulation  |                                                 |
\\*---------------------------------------------------------------------------*/
FoamFile
{{
    version 2.0;
    format ascii;
    class dictionary;
    location system;
    object blockMeshDict;
}}

scale 1;
vertices
(
{vert_str}
);

blocks
(
{block_str}
);

edges
();

defaultPatch
{{
    name backgroundBoundary;
    type patch;
}}

patches
(
);

mergePatchPairs
();
"""
        (system_dir / "blockMeshDict").write_text(content, encoding="utf-8")
        logger.info(f"Generated multi-block blockMeshDict: "
                    f"({bm_min_x:.2f}…{bm_max_x:.2f}, {bm_min_y:.2f}…{bm_max_y:.2f}, "
                    f"{bm_min_z:.2f}…{bm_max_z:.2f}), "
                    f"blocks={nx}×{ny}×{nz}={nx*ny*nz}, cells≈{total_cells}")

    # Rack-internal STL prefixes whose eMesh generation is skipped.
    # implicitFeatureSnap detects their 90° edges from STL geometry.
    _EMESH_SKIP_PREFIXES = (
        "merged_server_", "merged_blanking_", "merged_door_",
        "merged_through_zone_", "merged_leakage",
        "leakage_", "merged_rack_door", "merged_wallBaffle_",
        "merged_perforatedBlankingPanel_", "merged_wallBlankingPanel_",
    )

    def _generate_surface_feature_extract_dict(self, system_dir: Path) -> None:
        """Generate surfaceFeatureExtractDict for all STL files.

        Rack-internal STL files are skipped — their simple box edges are
        captured by ``implicitFeatureSnap true`` in the snap phase.
        """
        entries = []
        skipped = 0
        for filename in sorted(self._stl_solids.keys()):
            if any(filename.startswith(p) for p in self._EMESH_SKIP_PREFIXES):
                skipped += 1
                continue
            entries.append(f"""    {filename}
    {{
        extractionMethod    extractFromSurface;
        extractFromSurfaceCoeffs {{ includedAngle 150; }}
        writeObj yes;
    }}""")
        if skipped:
            logger.info(f"Skipped eMesh generation for {skipped} rack-internal STL files")

        content = f"""\
/*--------------------------------*- C++ -*----------------------------------*\\
| =========                 |                                                 |
| \\\\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox           |
|  \\\\    /   O peration     |                                                 |
\\*---------------------------------------------------------------------------*/
FoamFile
{{
    version 2.0;
    format ascii;
    class dictionary;
    location system;
    object surfaceFeatureExtractDict;
}}

{chr(10).join(entries)}
"""
        (system_dir / "surfaceFeatureExtractDict").write_text(content, encoding="utf-8")
        logger.info(f"Generated surfaceFeatureExtractDict for {len(entries)} STL files (skipped {skipped} rack-internal)")

    def _source_surface_dict_block(self, mesh_config: Dict[str, Any]) -> str:
        """Forward source UID metadata only to dcCart, without changing BCs."""
        if _resolve_mesh_engine(mesh_config) != "dcCart":
            return ""
        from backend.aircooling.runner.source_surface_dict import render_source_surfaces_dict
        raw = self._read_cfdrun_manifest()
        if ((raw.get("geometryExport") or {}).get("sourceSurfaces") and
                getattr(self, "_source_stls_verified", False) is not True):
            raise RuntimeError("dcCart source surfaces require verified export-time STL inputs")
        return render_source_surfaces_dict(
            raw.get("geometryExport") or {}, getattr(self, "_stl_solids", None) or None)

    def _generate_snappy_hex_mesh_dict(self, system_dir: Path) -> None:
        """Generate snappyHexMeshDict with through-zones as refinementRegions."""
        data = self._data
        mc = data.get("meshConfig", {})

        # 2026-04-17 redesign: wallBaffleSolids is a deprecated schema slot.
        # Emit a warning if any rack populates it so the developer catches
        # schema drift early. Rack-internal geometry must go through the
        # 4 enumerated categories (server/blanking/partition/through_zone) —
        # not a bucket-less wallBaffleSolids dump.
        for rack_name, rack in (data.get("racks") or {}).items():
            wbs = rack.get("wallBaffleSolids") or []
            if wbs:
                logger.warning(
                    f"rack '{rack_name}' has non-empty wallBaffleSolids "
                    f"({len(wbs)} entries); dropping — these are not routed "
                    f"to any STL file. See "
                    f"docs/superpowers/specs/2026-04-17-refinement-"
                    f"architecture-redesign-design.md §Risks."
                )

        rl_default = mc.get("refinementLevel", [1, 1])
        rack_region_level = mc.get("rackRegionLevel", 3)
        base_size = float(mc.get("baseSize", 0.25) or 0.25)
        # Cartesian (snap-off) thin-server guarantee: without snapping, a fan-server
        # slab is captured only if the rack-region cell pitch resolves it (cell <=
        # slab height). Auto-raise rackRegionLevel to resolve the thinnest server so
        # 1U outlets never drop for being under-resolved. Propagates to the rack
        # refinementRegions, server-surface and feature levels below. Snap-on and
        # cases already fine enough are unchanged (never coarsens).
        mesh_mode = _resolve_mesh_mode(mc)
        if _mesh_mode_is_cartesian(mesh_mode):
            _rack_cell_min = mc.get("rackCellMinSize")
            _eff = _cartesian_rack_region_level(
                data.get("racks") or {}, base_size, rack_region_level, min_cells=1,
                min_cell_size=_rack_cell_min)
            if _eff != rack_region_level:
                _slab = _min_server_slab_height(data.get("racks") or {})
                logger.info(
                    f"cartesian ({mesh_mode}): raising rackRegionLevel {rack_region_level}->{_eff} "
                    f"to resolve thinnest server slab {(_slab or 0)*1000:.1f}mm "
                    f"(cell {base_size/2**_eff*1000:.1f}mm <= slab"
                    + (f"; capped by rackCellMinSize {float(_rack_cell_min)*1000:.1f}mm"
                       if _rack_cell_min and float(_rack_cell_min) > 0
                       and base_size / 2 ** _eff > (_slab or 0) else "")
                    + ")")
                rack_region_level = _eff
        # All rack-internal surface refinement synced to rackRegionLevel.
        rl_rack_internal = [rack_region_level, rack_region_level]
        rl_acu = mc.get("acuRefinementLevel", [1, 1])
        rl_box = mc.get("boxRefinementLevel", [1, 1])
        rl_partition = mc.get("partitionWallRefinementLevel", rl_box)
        rl_baffle = mc.get("baffleRefinementLevel", [1, 1])
        rl_floor = mc.get("floorRefinementLevel", [1, 1])

        # Approach A first-cell-size control (Ref_code/REPORT_Cartesian_Mesh_Generator.md).
        # With no prism layers the near-wall "first layer thickness" is the wall-adjacent
        # Cartesian cube: first-cell = baseSize / 2^L. Invert it to pick the room-wall /
        # floor surface refinement level so y+ is controllable. Only refines (max), never
        # coarsens an explicit level. Mirrors the frontend computeMinSafeLevel() math.
        first_cell = mc.get("firstCellSize")
        if first_cell and float(first_cell) > 0:
            fc_level = max(0, math.ceil(math.log2(base_size / float(first_cell))))
            rl_default = [max(rl_default[0], fc_level), max(rl_default[1], fc_level)]
            rl_floor = [max(rl_floor[0], fc_level), max(rl_floor[1], fc_level)]
            logger.info(
                f"cartesian firstCellSize={float(first_cell)*1000:.1f}mm "
                f"(baseSize={base_size*1000:.0f}mm) → wall/floor refinement level {fc_level} "
                f"(≈{base_size/(2**fc_level)*1000:.1f}mm near-wall cells)"
            )

        # Build solid-name lookup: which solids are server faces vs through-zone vs etc.
        server_inlet_solids: set = set()
        server_outlet_solids: set = set()
        nonfan_solids: set = set()
        through_zone_solids: Dict[str, float] = {}  # {solidName: sigma}  (formerly leakage_solids)
        acu_supply_solids: set = set()
        acu_return_solids: set = set()
        perforated_solids: Dict[str, float] = {}  # {solidName: porosity}

        # Build mapping: sanitized solid name → spanSlots for per-unit refinement lookup.
        # Per-unit level applies to ALL faces of a fanServer (inlet/outlet AND
        # top/bottom/left/right + patch wall remainders), not just flow faces.
        # Different U-height servers thus still get distinct refinement, but the
        # setting now governs the full server enclosure, not only the fan faces.
        solid_to_span: Dict[str, int] = {}
        for rack in data.get("racks", {}).values():
            for srv in rack.get("servers", []):
                span = srv.get("spanSlots", 1)
                is_fan = srv.get("type") == "fanServer"
                faces = srv.get("faces")
                if faces:
                    # New format: iterate faces dict
                    for face_id, face_info in faces.items():
                        patches = face_info.get("patches", [])
                        if patches:
                            for p in patches:
                                s = p.get("solid")
                                if s:
                                    san = _sanitize(s)
                                    if face_id in ("inlet", "outlet") and is_fan:
                                        (server_inlet_solids if face_id == "inlet"
                                         else server_outlet_solids).add(san)
                                    else:
                                        nonfan_solids.add(san)
                                    # All fanServer solids inherit per-U refinement.
                                    if is_fan:
                                        solid_to_span[san] = span
                            wall_s = face_info.get("wallSolid")
                            if wall_s:
                                san_w = _sanitize(wall_s)
                                nonfan_solids.add(san_w)
                                if is_fan:
                                    solid_to_span[san_w] = span
                        else:
                            s = face_info.get("solid")
                            if s:
                                san = _sanitize(s)
                                if is_fan:
                                    if face_id in ("inlet", "outlet"):
                                        (server_inlet_solids if face_id == "inlet"
                                         else server_outlet_solids).add(san)
                                    else:
                                        nonfan_solids.add(san)
                                    solid_to_span[san] = span
                                else:
                                    nonfan_solids.add(san)
                else:
                    # Legacy format — inletSolidName / outletSolidName
                    if is_fan:
                        _in = srv.get("inletSolidName")
                        _out = srv.get("outletSolidName")
                        if _in:
                            server_inlet_solids.add(_sanitize(_in))
                            solid_to_span[_sanitize(_in)] = span
                        if _out:
                            server_outlet_solids.add(_sanitize(_out))
                            solid_to_span[_sanitize(_out)] = span
                    else:
                        for k in ("inletSolidName", "outletSolidName", "topSolidName", "bottomSolidName"):
                            if srv.get(k):
                                nonfan_solids.add(_sanitize(srv[k]))
            for grp in rack.get("leakage", {}).get("groups", []):
                through_zone_solids[_sanitize(grp["stlSolidName"])] = grp.get("sigma", 0.05)

        # Rack frame and partition solids: wall BC with rack-level refinement.
        rack_frame_solids: set = set()
        partition_solids: set = set()
        for rack_name, rack in data.get("racks", {}).items():
            # Frame: new multi-solid format (frameSolidNames) + legacy singular fallback.
            frame_list = rack.get("frameSolidNames") or []
            if not frame_list and rack.get("frameSolidName"):
                frame_list = [rack["frameSolidName"]]
            for fs in frame_list:
                if fs:
                    rack_frame_solids.add(_sanitize(fs))
            for part in rack.get("partitions", []):
                solid = part.get("solidName")
                if solid:
                    partition_solids.add(_sanitize(solid))
        partition_wall_solids: set = set()
        for part in data.get("partitionWalls", {}).values():
            for solid in part.get("wallSolidNames", []):
                if solid:
                    partition_wall_solids.add(_sanitize(solid))

        # Rack wall-baffle solids: half-dissolved U-slots (this side wall, opposite void).
        # These become faceType internal → createBaffles cyclic → post-process to wall.
        rack_wall_baffle_solids: set = set()
        for rack in data.get("racks", {}).values():
            for wb_solid in rack.get("wallBaffleSolids", []):
                rack_wall_baffle_solids.add(_sanitize(wb_solid))

        # Rack perforated door solids are exported as standalone
        # baffle_*.stl files. Register them in dedicated containers so
        # the refinementSurfaces / feature-edge branches below can
        # detect each door's filename, reverse-map it to its rack, and
        # emit the bucket-resolved door refinement level via
        # _door_level_for_rack (spec 2026-04-17 §4). The rack_door_solids
        # SET is still read by the secondary per-solid branch near the
        # end of this method (Task 7 handles that branch).
        rack_door_solids: set = set()
        rack_door_filenames: set = set()
        # filename → rack_name so the refinement / feature-edge branches can
        # look up which rack a door belongs to and resolve its bucketed
        # refinement level via _door_level_for_rack. See spec §4.2.
        rack_door_filename_to_rack: Dict[str, str] = {}
        # solid → rack_name sibling map for the secondary per-solid branch
        # (doors appearing inside multi-solid STL files). Spec §4 Task 7.
        rack_door_solid_to_rack: Dict[str, str] = {}
        for rack_name, rack in data.get("racks", {}).items():
            rack_san = _sanitize(rack_name)
            for side in ("inlet", "outlet"):
                door = rack.get(f"{side}Door")
                if not door or not door.get("enabled"):
                    continue
                solid = _sanitize(door.get("solidName", f"{rack_san}_{side}_door"))
                rack_door_solids.add(solid)
                fn = f"baffle_{solid}.stl"
                rack_door_filenames.add(fn)
                rack_door_filename_to_rack[fn] = rack_name
                rack_door_solid_to_rack[solid] = rack_name

        # Structural inlet/outlet solids from rack geometry (safe margins, frames, U-ranges).
        # These are NOT in server_inlet/outlet_solids (which only contain server face names).
        # They must be type patch so snappyHexMesh creates boundary patches for them.
        _rack_io_pat = re.compile(r'_(inlet|outlet)_(safe_|frame_|u\d)')
        rack_io_solids: set = set()
        for solid in self._stl_solids.get("merged_rack.stl", []):
            san = _sanitize(solid)
            if (_rack_io_pat.search(san)
                    and san not in server_inlet_solids
                    and san not in server_outlet_solids
                    and san not in rack_wall_baffle_solids):
                rack_io_solids.add(san)

        # Build mapping: blanking plate solid name → spanUnits (for per-unit refinement)
        solid_to_blanking_span: Dict[str, int] = {}
        _u_range_pat = re.compile(r'_u(\d+)_u(\d+)')
        for solid_name in (rack_io_solids | rack_wall_baffle_solids):
            m = _u_range_pat.search(solid_name)
            if m:
                solid_to_blanking_span[solid_name] = int(m.group(2)) - int(m.group(1)) + 1

        # Build mapping: through-zone solid name → spanUnits (formerly solid_to_leakage_span)
        solid_to_through_zone_span: Dict[str, int] = {}
        for rack in data.get("racks", {}).values():
            for grp in rack.get("leakage", {}).get("groups", []):
                san = _sanitize(grp["stlSolidName"])
                solid_to_through_zone_span[san] = grp.get("endUnit", 0) - grp.get("startUnit", 0) + 1

        for acu in data.get("acus", {}).values():
            for sf in acu.get("supplyFaces", []):
                acu_supply_solids.add(_sanitize(sf["solidName"]))
            for rf in acu.get("returnFaces", []):
                acu_return_solids.add(_sanitize(rf["solidName"]))

        # Perforated baffles from baffle models. New format wins: prefer
        # `perforated.openAreaPct` over the legacy `porosity` scalar (pydantic
        # always emits `porosity: 0.0` even for perforated baffles, so a
        # porosity-only check would silently drop them).
        def _phys_sigma(p: dict) -> float:
            perf = p.get("perforated") if isinstance(p, dict) else None
            if perf and perf.get("openAreaPct") is not None:
                return float(perf["openAreaPct"]) / 100.0
            return float(p.get("porosity", 0) or 0) / 100.0

        for baffle_name, baffle in data.get("baffles", {}).items():
            phys = baffle.get("physics", {})
            porosity_pct = phys.get("porosity", 0)
            if phys.get("baffleType") == "perforated" or porosity_pct > 0:
                san_bn = _sanitize(baffle_name)
                for solid in self._stl_solids.get("merged_baffle.stl", []):
                    san = _sanitize(solid)
                    if san.startswith(san_bn + "_") or san == san_bn:
                        sol_phys = self._baffle_physics.get(san, phys)
                        sol_porosity = _phys_sigma(sol_phys)
                        if sol_porosity > 0 and san not in perforated_solids:
                            perforated_solids[san] = sol_porosity
            # Per-patch physics
            for solid_name, pp in baffle.get("patchPhysics", {}).items():
                san = _sanitize(solid_name)
                pp_porosity = _phys_sigma(pp)
                if pp_porosity > 0 and san not in perforated_solids:
                    perforated_solids[san] = pp_porosity

        # Collect floor solid types: wallBaffle (internal face, no-slip) and perforated (cyclic baffle)
        # Also includes rack wall-baffle solids (half-dissolved U-slots).
        wall_baffle_solids: set = set(rack_wall_baffle_solids)
        for floor_name, floor in data.get("floors", {}).items():
            default_bc = floor.get("defaultBC", "wall")
            san_floor_name = _sanitize(floor_name)
            # Identify which solids are patches (openings) vs remaining area
            opening_solid_names = set()
            for op in floor.get("openings", []):
                op_solid = _sanitize(op["solidName"])
                opening_solid_names.add(op_solid)
                op_type = op.get("physics", {}).get("type", "wall")
                if op_type == "perforated":
                    porosity = _phys_sigma(op["physics"])
                    if porosity <= 0:
                        # No usable physical params and porosity scalar is
                        # zero — treat as wall_baffle so the BC stack stays
                        # consistent (skip the porous classification).
                        wall_baffle_solids.add(op_solid)
                    else:
                        perforated_solids[op_solid] = porosity
                elif op_type == "wallBaffle":
                    wall_baffle_solids.add(op_solid)
            # Remaining area (non-opening) solids that belong to THIS floor get the defaultBC type
            # Filter by floor name prefix to avoid affecting solids from other floors in same STL
            # Check both original and internal-split STL files
            floor_stl = floor.get("stlFile", "merged_floor_ceiling.stl")
            floor_solids_in_stl = list(self._stl_solids.get(floor_stl, []))
            # Also check internal split file (created by _load split)
            fc_int_stl = "merged_floor_ceiling_internal.stl"
            if fc_int_stl in self._stl_solids:
                floor_solids_in_stl.extend(self._stl_solids[fc_int_stl])
            # Also check individual baffle/perforated/merged_wall files (floor solids are extracted there)
            for bf_name, bf_solids in self._stl_solids.items():
                if (bf_name.startswith("baffle_")
                        or bf_name.startswith("perforated_floor_")
                        or bf_name == "merged_wall_baffle.stl"):
                    floor_solids_in_stl.extend(bf_solids)
            if default_bc == "wallBaffle":
                for solid in floor_solids_in_stl:
                    s = _sanitize(solid)
                    if s.startswith(san_floor_name) and s not in opening_solid_names and s not in perforated_solids:
                        wall_baffle_solids.add(s)
            elif default_bc == "perforated":
                # 2026-04-16 unification: prefer the perforated block's
                # openAreaPct over the legacy defaultBCPorosity scalar.
                perf_block = floor.get("defaultBCPerforated")
                if perf_block and perf_block.get("openAreaPct") is not None:
                    default_porosity = float(perf_block["openAreaPct"]) / 100.0
                else:
                    default_porosity = floor.get(
                        "defaultBCPorosity", floor.get("defaultBC_porosity", 0.5))
                for solid in floor_solids_in_stl:
                    s = _sanitize(solid)
                    if s.startswith(san_floor_name) and s not in opening_solid_names:
                        perforated_solids[s] = default_porosity

        # --- Geometry section ---
        geom_lines = []
        for filename, solids in sorted(self._stl_solids.items()):
            stem = Path(filename).stem   # e.g. merged_rack
            if (filename in ("merged_perforated.stl",
                             "merged_baffle.stl",
                             "merged_wall_baffle.stl",
                             "merged_rack_door.stl")
                    or filename.startswith("baffle_")
                    or filename.startswith("perforated_baffle_")
                    or filename.startswith("perforated_floor_")):
                # These surfaces use faceType internal or baffle → no named regions in geometry.
                # Named regions force snappyHexMesh to create wall boundary patches
                # instead of internal faces, defeating faceType internal/baffle.
                geom_lines.append(f"""    {filename}
    {{
        type triSurfaceMesh;
        name {stem};
    }}""")
                continue
            elif filename.startswith("partition_hole_"):
                # Helper partition-hole surfaces behave like through-zone files: keep
                # them as standalone surfaces so snappyHexMesh can assign a cellZone.
                geom_lines.append(f"""    {filename}
    {{
        type triSurfaceMesh;
        name {stem};
    }}""")
                continue
            elif (filename == "merged_leakage.stl"
                  or filename.startswith("leakage_")
                  or re.match(r"merged_leakage_\d+U\.stl$", filename)):
                # Legacy through-zone files (pre-2026-04-17 naming):
                # "merged_leakage.stl" = old scene-merged export.
                # "leakage_<san>.stl" = old per-solid split files.
                # "merged_leakage_<N>U.stl" = old per-bucket name (renamed to
                #   merged_through_zone_<N>U.stl in 2026-04-17 refactor).
                # No named regions needed — declared as standalone closed surface
                # so snappyHexMesh can use it for refinementRegions mode inside.
                geom_lines.append(f"""    {filename}
    {{
        type triSurfaceMesh;
        name {stem};
    }}""")
                continue
            else:
                region_lines = "\n".join(f"            {s} {{ name {s}; }}" for s in solids)
                geom_lines.append(f"""    {filename}
    {{
        type triSurfaceMesh;
        name {stem};
        regions
        {{
{region_lines}
        }}
    }}""")

        # --- Features section (per-STL feature edge levels) ---
        def _feat_int(val, default=2):
            """Ensure feature level is a scalar integer (not a list)."""
            if isinstance(val, list): return int(val[0]) if val else default
            return int(val) if val is not None else default
        feat_default = _feat_int(mc.get("defaultFeatureLevel"), 2)
        feat_rack = rack_region_level  # synced to rackRegionLevel from mesh tab
        # PERF (snap-off): feature EDGE refinement on merged_rack_frame is the single
        # biggest castellation cost on large (100+ rack) cases. snappy's
        # markFeatureCellLevel particle-tracks EVERY rack-frame edge from the single
        # locationInMesh on EVERY feature iteration (≈28 s each × ~9 iters ≈ 275 s on
        # Alibaba-hongchou), and the last iterations refine ~0 cells yet still cost
        # full tracing. In snap-off modes there is no feature SNAPPING, so this only
        # adds cell density the rackRegion volume refinement + (structured) grid lines
        # already provide; the frame surface is still captured via refinementSurfaces.
        # Drop the frame from feature refinement when snapping is off.
        # See docs/developer/mesh-castellation-performance-findings.md.
        if _mesh_mode_is_cartesian(mesh_mode):
            feat_rack = 0
        feat_acu = _feat_int(mc.get("acuFeatureLevel"), 2)
        feat_box = _feat_int(mc.get("boxFeatureLevel"), 2)
        feat_partition = _feat_int(mc.get("partitionWallFeatureLevel"), feat_box)
        feat_baffle = _feat_int(mc.get("baffleFeatureLevel"), 2)
        feature_lines = []
        # Rack-internal STL prefixes — feature level kept at 1 (minimal).
        # The edge geometry is still captured (snappy snaps to it), but
        # feature *refinement* only runs 1 iteration instead of
        # rackRegionLevel iterations.  The refinementRegion supplies the
        # actual cell density inside the rack envelope.  This is critical
        # for 200-rack cases where O(cells × edges × iterations) in
        # feature refinement becomes the dominant cost.
        _FEAT_RACK_INTERNAL = 0   # skip explicit eMesh — rely on implicitFeatureSnap for simple box geometries
        for filename in sorted(self._stl_solids.keys()):
            stem = Path(filename).stem
            emesh_name = filename.replace(".stl", ".eMesh")

            if any(filename.startswith(p) for p in self._EMESH_SKIP_PREFIXES):
                feat_level = _FEAT_RACK_INTERNAL
            elif filename.startswith("merged_partition_") and filename != "merged_partition_wall.stl":
                feat_level = _FEAT_RACK_INTERNAL
            elif filename == "merged_rack_frame.stl":
                feat_level = feat_rack
            elif filename == "merged_wall_baffle.stl":
                feat_level = feat_baffle
            elif filename.startswith("perforated_baffle_"):
                feat_level = feat_baffle
            elif filename.startswith("perforated_floor_"):
                feat_level = _feat_int(mc.get("floorFeatureLevel"), feat_default)
            elif filename.startswith("wallBaffle_floor_"):
                feat_level = _feat_int(mc.get("floorFeatureLevel"), feat_default)
            elif filename == "merged_acu.stl":
                feat_level = feat_acu
            elif filename in ("merged_box.stl", "merged_pipe.stl"):
                feat_level = feat_box
            elif filename == "merged_partition_wall.stl":
                feat_level = feat_partition
            elif filename == "merged_baffle.stl":
                feat_level = feat_baffle
            elif filename == "merged_floor_ceiling.stl":
                feat_level = _feat_int(mc.get("floorFeatureLevel"), feat_default)
            elif filename == "room_wall.stl":
                feat_level = feat_default
            # Legacy fallbacks (transitional; old-style individual files
            # may still exist in partial rollouts until the frontend ships
            # Task 12's buildGroupedStlMap refactor):
            elif filename.startswith("partition_hole_"):
                feat_level = feat_partition
            elif filename == "merged_perforated.stl":
                feat_level = feat_baffle
            elif filename.startswith("baffle_"):
                feat_level = feat_baffle
            elif filename in rack_door_filenames:
                # Legacy individual door files — use rack level.
                feat_level = feat_rack
            else:
                feat_level = feat_default

            if feat_level > 0:
                feature_lines.append(f"""    {{
        file "{emesh_name}";
        level {feat_level};
    }}""")

        # --- Refinement surfaces section ---
        refine_lines = []
        for filename, solids in sorted(self._stl_solids.items()):
            stem = Path(filename).stem
            if filename.startswith("partition_hole_"):
                san = _sanitize(solids[0])
                refine_lines.append(f"""    {stem}
    {{
        level ({rl_partition[0]} {rl_partition[1]});
        faceType internal;
        cellZone {san};
        cellZoneInside inside;
    }}""")
                continue
            if (filename == "merged_leakage.stl"
                    or filename.startswith("leakage_")
                    or re.match(r"merged_leakage_\d+U\.stl$", filename)):
                continue  # covered by rack refinementRegion

            # 2026-04-17 redesign: per-bucket through-zone files go to
            # refinementRegions (mode inside) — NOT to refinementSurfaces.
            # Through-zone is pure fluid; no cellZone + porous source needed.
            # Server/blanking/partition bucket files fall through to the
            # per-region emission below — each solid is classified by the
            # big switch (server_inlet_solids, nonfan_solids, rack_io_solids,
            # …) so fan-server inlets become type=patch, channel walls
            # become type=wall, blanking openings become type=patch, etc.
            bucket = _parse_bucket_from_filename(filename)
            if bucket is not None and bucket[0] == "through_zone":
                continue  # covered by rack refinementRegion

            # 2026-04-17 fix: merged_door_<N>U.stl needs surface-level
            # faceType internal so snappy keeps the faces as internal mesh
            # faces (not boundary wall patches). Per-region faceType internal
            # is silently ignored by snappy — it creates wall patches anyway.
            # With surface-level faceType internal, snappy keeps ALL faces of
            # this surface internal, and createBaffles can then move them to
            # a cyclic master/slave pair. Each solid gets its own faceZone
            # (needed by createBaffles for zone-name matching).
            if bucket is not None and bucket[0] == "door":
                door_region_entries = []
                for solid in solids:
                    san = _sanitize(solid)
                    door_region_entries.append(f"""            {solid}
            {{
                level ({rl_rack_internal[0]} {rl_rack_internal[1]});
                faceZone {san};
            }}""")
                door_regions_str = "\n".join(door_region_entries)
                refine_lines.append(f"""    {stem}
    {{
        level ({rl_rack_internal[0]} {rl_rack_internal[1]});
        faceType internal;
        regions
        {{
{door_regions_str}
        }}
    }}""")
                continue

            # 2026-04-17 fix: merged_wallBaffle_<N>U.stl — same surface-level
            # faceType internal treatment as merged_door_<N>U.stl. wallBaffle
            # panels are half-dissolved U-slot blanking faces (double-sided);
            # createBaffles converts them to cyclic master/slave, then
            # _convert_wall_baffle_patches_to_wall() reclassifies to wall type.
            if bucket is not None and bucket[0] in ("wallBaffle", "perforatedBlankingPanel"):
                refine_lines.append(f"""    {stem}
    {{
        level ({rl_rack_internal[0]} {rl_rack_internal[1]});
        faceType internal;
        faceZone {stem};
    }}""")
                continue

            if bucket is not None and bucket[0] == "wallBlankingPanel":
                # fullBlock blanking panel = solid wall: refine only, NO faceType
                # internal / faceZone (it must NOT become a createBaffles baffle).
                # The sealed rack interior is simply left unmeshed.
                refine_lines.append(f"""    {stem}
    {{
        level ({rl_rack_internal[0]} {rl_rack_internal[1]});
    }}""")
                continue

            # ── merged_wall_baffle.stl: surface-level faceZone + faceType internal ──
            # Per-region faceZone does NOT work in snappyHexMesh — only
            # surface-level faceZone keeps faces internal. Uses ONE merged
            # faceZone; createBaffles creates merged_wall_baffle_master/_slave.
            if filename == "merged_wall_baffle.stl":
                floor_wb_sans = set()
                for floor_name, floor in data.get("floors", {}).items():
                    if floor.get("defaultBC") == "wallBaffle":
                        san_fn = _sanitize(floor_name)
                        op_names = {_sanitize(op["solidName"]) for op in floor.get("openings", [])}
                        for s in solids:
                            san_s = _sanitize(s)
                            if san_s.startswith(san_fn) and san_s not in op_names:
                                floor_wb_sans.add(san_s)
                wb_region_entries = []
                for solid in solids:
                    san = _sanitize(solid)
                    _rl_wb = rl_floor if san in floor_wb_sans else rl_baffle
                    wb_region_entries.append(f"""            {solid}
            {{
                level ({_rl_wb[0]} {_rl_wb[1]});
            }}""")
                wb_regions_str = "\n".join(wb_region_entries)
                refine_lines.append(f"""    {stem}
    {{
        level ({rl_baffle[0]} {rl_baffle[1]});
        faceType internal;
        faceZone merged_wall_baffle;
        regions
        {{
{wb_regions_str}
        }}
    }}""")
                continue

            # ── merged_rack_door.stl: faceType internal (createBaffles cyclic) ──
            if filename == "merged_rack_door.stl":
                refine_lines.append(f"""    {stem}
    {{
        level ({rl_rack_internal[0]} {rl_rack_internal[1]});
        faceType internal;
        faceZone merged_rack_door;
    }}""")
                continue

            # ── perforated_baffle_*.stl / perforated_floor_*.stl ──
            if filename.startswith("perforated_baffle_") or filename.startswith("perforated_floor_"):
                solid_san = _sanitize(solids[0])
                _rl_perf = rl_floor if filename.startswith("perforated_floor_") else rl_baffle
                refine_lines.append(f"""    {stem}
    {{
        level ({_rl_perf[0]} {_rl_perf[1]});
        faceType internal;
        faceZone {solid_san};
    }}""")
                continue

            # ── wallBaffle_floor_*.stl: individual floor wallBaffle solids ──
            # Same surface-level faceZone + faceType internal as perforated_floor.
            # createBaffles makes a cyclic <solid>_master/_slave pair, then
            # _convert_wall_baffle_patches_to_wall() reclassifies it to wall type.
            #
            # A raised-floor solid is a large flat horizontal baffle sitting in a
            # thin plenum (e.g. 0.2 m above the room floor). At the floor's own
            # refinement level the base cells are coarser than the plenum and no
            # cell face lands on the baffle plane, so snappy snaps nothing and the
            # faceZone gets 0 faces (the perforated opening only captures because
            # it borders the level-3-refined racks). Refine the surface itself to
            # at least level 2 so a cell layer resolves inside the plenum and
            # snapping produces real master/slave faces.
            if filename.startswith("wallBaffle_floor_"):
                solid_san = _sanitize(solids[0])
                _rl_wbf = [max(rl_floor[0], 2), max(rl_floor[1], 2)]
                refine_lines.append(f"""    {stem}
    {{
        level ({_rl_wbf[0]} {_rl_wbf[1]});
        faceType internal;
        faceZone {solid_san};
    }}""")
                continue

            if filename == "merged_perforated.stl":
                # Perforated solids should have been extracted to individual files.
                # If any remain (shouldn't happen), treat as surface-level faceZone.
                refine_lines.append(f"""    {stem}
    {{
        level ({rl_baffle[0]} {rl_baffle[1]});
        faceType internal;
        faceZone {_sanitize(solids[0])};
    }}""")
                continue

            if filename in rack_door_filenames:
                # Legacy: individual rack door files (baffle_<door>.stl).
                solid_san = _sanitize(solids[0])
                refine_lines.append(f"""    {stem}
    {{
        level ({rl_rack_internal[0]} {rl_rack_internal[1]});
        faceType internal;
        faceZone {solid_san};
    }}""")
                continue

            if filename.startswith("baffle_"):
                solid_san = _sanitize(solids[0])
                is_rack_partition = re.fullmatch(r".+_u\d+_partition", solid_san) is not None
                baffle_level = rl_rack_internal if is_rack_partition else rl_baffle
                direct_wall = "\n        wallBaffle true;" if self._baffle_physics.get(solid_san, {}).get("directWall") else ""
                refine_lines.append(f"""    {stem}
    {{
        level ({baffle_level[0]} {baffle_level[1]});
        faceType internal;
        faceZone {solid_san};{direct_wall}
    }}""")
                continue

            # 2026-04-17: Patches on faces (solids named `<parent>_p<N>`)
            # live in the same STL as their parent solid and implicitly
            # inherit the parent's surface level via the file-level `level`
            # fallback. No explicit per-patch regions block is emitted —
            # patches only get their own `regions {}` entry when they need
            # a DIFFERENT level from the parent, which never happens in the
            # current UI (which doesn't expose per-patch refinement controls).
            # See docs/superpowers/specs/2026-04-17-refinement-architecture-
            # redesign-design.md §Patch-on-face refinement inheritance.
            solid_entries = []
            for solid in solids:
                sanitized = _sanitize(solid)
                if sanitized in through_zone_solids:
                    # Through-zone solid in a non-through-zone STL — treat as regular wall
                    # (formerly: leakage solid in non-leakage STL)
                    solid_entries.append(f"""            {solid}
            {{
                level ({rl_default[0]} {rl_default[1]});
                patchInfo {{ type wall; inGroups (wall); }}
            }}""")
                elif sanitized in server_inlet_solids or sanitized in server_outlet_solids:
                    solid_entries.append(f"""            {solid}
            {{
                level ({rl_rack_internal[0]} {rl_rack_internal[1]});
                patchInfo {{ type patch; }}
            }}""")
                elif sanitized in nonfan_solids:
                    solid_entries.append(f"""            {solid}
            {{
                level ({rl_rack_internal[0]} {rl_rack_internal[1]});
                patchInfo {{ type wall; inGroups (wall); }}
            }}""")
                elif sanitized in rack_io_solids:
                    solid_entries.append(f"""            {solid}
            {{
                level ({rl_rack_internal[0]} {rl_rack_internal[1]});
                patchInfo {{ type patch; }}
            }}""")
                elif sanitized in acu_supply_solids or sanitized in acu_return_solids:
                    solid_entries.append(f"""            {solid}
            {{
                level ({rl_acu[0]} {rl_acu[1]});
                patchInfo {{ type patch; }}
            }}""")
                elif sanitized in rack_wall_baffle_solids:
                    solid_entries.append(f"""            {solid}
            {{
                level ({rl_rack_internal[0]} {rl_rack_internal[1]});
                faceType internal;
                faceZone {sanitized};
            }}""")
                elif sanitized in rack_frame_solids or sanitized in partition_solids:
                    # Rack frame / partition: wall with rack-level refinement
                    solid_entries.append(f"""            {solid}
            {{
                level ({rl_rack_internal[0]} {rl_rack_internal[1]});
                patchInfo {{ type wall; inGroups (wall); }}
            }}""")
                elif sanitized in partition_wall_solids:
                    solid_entries.append(f"""            {solid}
            {{
                level ({rl_partition[0]} {rl_partition[1]});
                patchInfo {{ type wall; inGroups (wall); }}
            }}""")
                elif sanitized in wall_baffle_solids:
                    # Standalone wall baffle (floor/baffle model): uses baffleRefinementLevel
                    solid_entries.append(f"""            {solid}
            {{
                level ({rl_baffle[0]} {rl_baffle[1]});
                faceType internal;
                faceZone {sanitized};
            }}""")
                elif sanitized in rack_door_solids:
                    solid_entries.append(f"""            {solid}
            {{
                level ({rl_rack_internal[0]} {rl_rack_internal[1]});
                faceType internal;
                faceZone {sanitized};
            }}""")
                elif sanitized in perforated_solids:
                    # Perforated floor opening: faceType internal + faceZone for createBaffles
                    solid_entries.append(f"""            {solid}
            {{
                level ({rl_baffle[0]} {rl_baffle[1]});
                faceType internal;
                faceZone {sanitized};
            }}""")
                elif filename == "merged_baffle.stl":
                    # All baffles use faceType internal + faceZone (both sides kept).
                    # snappyHexMesh requires faceZone for faceType internal to work.
                    # Porosity=0: faceZone exists but NO createBaffles entry →
                    #   stays as internal face (transparent) with noSlip semantics
                    # Porosity>0: faceZone + createBaffles cyclic master/slave
                    solid_entries.append(f"""            {solid}
            {{
                level ({rl_baffle[0]} {rl_baffle[1]});
                faceType internal;
                faceZone {sanitized};
            }}""")
                else:
                    # Per-STL-file refinement level
                    if filename in ("merged_rack.stl", "merged_rack_frame.stl"): rl = rl_rack_internal
                    elif filename == "merged_acu.stl": rl = rl_acu
                    elif filename in ("merged_box.stl", "merged_pipe.stl"): rl = rl_box
                    elif filename == "merged_partition_wall.stl": rl = rl_partition
                    elif filename == "merged_floor_ceiling.stl": rl = rl_floor
                    else: rl = rl_default
                    solid_entries.append(f"""            {solid}
            {{
                level ({rl[0]} {rl[1]});
                patchInfo {{ type wall; inGroups (wall); }}
            }}""")

            solid_block = "\n".join(solid_entries)
            # Surfaces with ONLY internal faces need faceType internal at
            # surface level (snappyHexMesh ignores per-region faceType).
            # Mixed surfaces (like merged_floor_ceiling with both wall and
            # internal regions) cannot use surface-level faceType internal
            # because it would override patchInfo type wall for solid walls.
            surface_facetype = ""  # per-region faceType handled by individual entries
            refine_lines.append(f"""    {stem}
    {{
        level ({rl_default[0]} {rl_default[1]});{surface_facetype}
        regions
        {{
{solid_block}
        }}
    }}""")

        # --- Locations in mesh ---
        location_entries = self._build_locations_in_mesh_entries()
        locations_str = "\n".join(
            f"        (( {point[0]:.6f} {point[1]:.6f} {point[2]:.6f} ) {zone_name})"
            for point, zone_name in location_entries
        )

        # ── Refinement zone searchableBox entries ──
        rz_list = mc.get("refinementZones", [])
        for rz in rz_list:
            rz_name = _sanitize(rz["name"])
            mn, mx = rz["min"], rz["max"]
            geom_lines.append(f"""    {rz_name}
    {{
        type searchableBox;
        min ({mn['x']} {mn['y']} {mn['z']});
        max ({mx['x']} {mx['y']} {mx['z']});
    }}""")

        # ── Rack refinementRegion entries ──
        # Use searchableRotatedBox when quaternion data is available (new
        # exports include quaternion + scale in rack geometry).  Fall back
        # to axis-aligned searchableBox from STL vertex AABB for legacy
        # cdfrun files that lack quaternion data.
        rack_frame_stl = self.tri_surface_dir / "merged_rack_frame.stl"
        frame_bboxes: Dict[str, Tuple[List[float], List[float]]] = {}
        if rack_frame_stl.exists():
            try:
                frame_bboxes = _solid_bounding_boxes(rack_frame_stl)
            except Exception as exc:
                logger.warning(f"Could not parse {rack_frame_stl.name} for "
                               f"rack AABB: {exc}")

        rack_region_names = []
        for rack_name, rack in (data.get("racks") or {}).items():
            geom = rack.get("geometry") or {}
            quat = geom.get("quaternion")
            scale = geom.get("scale")
            size = geom.get("size") or {}
            anchor = geom.get("anchor") or {}

            rr_name = f"rackRegion_{_sanitize(rack_name)}"

            if quat and size.get("W") and size.get("H") and size.get("D"):
                # ── New path: searchableRotatedBox from quaternion ──
                W = float(size["W"])
                H = float(size["H"])
                D = float(size["D"])
                ax = float(anchor.get("x", 0))
                ay = float(anchor.get("y", 0))
                az = float(anchor.get("z", 0))
                sx = float((scale or {}).get("x", 1))
                sy = float((scale or {}).get("y", 1))
                sz = float((scale or {}).get("z", 1))

                # Compute unit direction vectors (e1/e2/e3) and local
                # dimensions (span) for OpenFOAM searchableRotatedBox.
                # e1/e2/e3 are rotated unit axes, span is box extent.
                e1_raw = _quat_rotate((sx, 0.0, 0.0), quat)
                e2_raw = _quat_rotate((0.0, sy, 0.0), quat)
                e3_raw = _quat_rotate((0.0, 0.0, sz), quat)
                e1_len = math.sqrt(sum(v * v for v in e1_raw))
                e2_len = math.sqrt(sum(v * v for v in e2_raw))
                e3_len = math.sqrt(sum(v * v for v in e3_raw))
                e1 = tuple(v / e1_len for v in e1_raw) if e1_len > _GEOM_EPS else (1, 0, 0)
                e2 = tuple(v / e2_len for v in e2_raw) if e2_len > _GEOM_EPS else (0, 1, 0)
                e3 = tuple(v / e3_len for v in e3_raw) if e3_len > _GEOM_EPS else (0, 0, 1)
                span = (W * e1_len, H * e2_len, D * e3_len)

                # OpenFOAM rebuilds the box's 2nd axis from e3 x e1 (ignoring
                # e2); a mirrored rack (negative scale) yields a left-handed
                # basis that flips the box downward through the floor. Re-express
                # the identical box with a right-handed basis.
                (ax, ay, az), e1, e2, e3 = _ensure_right_handed_box(
                    (ax, ay, az), e1, e2, e3, span
                )

                rack_region_names.append(rr_name)
                geom_lines.append(f"""    {rr_name}
    {{
        type searchableRotatedBox;
        origin ({ax:.6f} {ay:.6f} {az:.6f});
        e1 ({e1[0]:.6f} {e1[1]:.6f} {e1[2]:.6f});
        e2 ({e2[0]:.6f} {e2[1]:.6f} {e2[2]:.6f});
        e3 ({e3[0]:.6f} {e3[1]:.6f} {e3[2]:.6f});
        span ({span[0]:.6f} {span[1]:.6f} {span[2]:.6f});
    }}""")
            else:
                # ── Fallback: AABB searchableBox from STL vertices ──
                frame_solids = rack.get("frameSolidNames") or []
                if not frame_solids and rack.get("frameSolidName"):
                    frame_solids = [rack["frameSolidName"]]

                mn: Optional[List[float]] = None
                mx: Optional[List[float]] = None
                for fs in frame_solids:
                    bbox = frame_bboxes.get(fs)
                    if bbox is None:
                        continue
                    if mn is None:
                        mn, mx = list(bbox[0]), list(bbox[1])
                    else:
                        for idx in range(3):
                            mn[idx] = min(mn[idx], bbox[0][idx])
                            mx[idx] = max(mx[idx], bbox[1][idx])

                if mn is None or mx is None:
                    logger.warning(
                        f"Rack '{rack_name}': no bounding data available; "
                        f"skipping refinement region."
                    )
                    continue

                rack_region_names.append(rr_name)
                geom_lines.append(f"""    {rr_name}
    {{
        type searchableBox;
        min ({mn[0]:.6f} {mn[1]:.6f} {mn[2]:.6f});
        max ({mx[0]:.6f} {mx[1]:.6f} {mx[2]:.6f});
    }}""")

        # ── Thin-gap auto-refinement regions ──
        thin_gap_regions = _detect_thin_gap_regions(
            data.get("racks") or {}, base_size, rack_region_level
        )
        thin_gap_rz_lines = []
        for tg in thin_gap_regions:
            tg_name = (f"rackThinGapRegion_{_sanitize(tg['rack_name'])}"
                       f"_u{tg['start_unit']}_u{tg['end_unit']}")
            tg_geom = tg["geom"]
            quat = tg_geom.get("quaternion")
            scale_data = tg_geom.get("scale")
            anchor = tg_geom.get("anchor") or {}
            ax = float(anchor.get("x", 0))
            ay = float(anchor.get("y", 0))
            az = float(anchor.get("z", 0))

            slot_margin = (tg_geom["W"] - tg_geom["slot_width"]) / 2

            if quat:
                # ── Rotated box from quaternion ──
                sx = float((scale_data or {}).get("x", 1))
                sy = float((scale_data or {}).get("y", 1))
                sz = float((scale_data or {}).get("z", 1))
                e1_raw = _quat_rotate((sx, 0.0, 0.0), quat)
                e2_raw = _quat_rotate((0.0, sy, 0.0), quat)
                e3_raw = _quat_rotate((0.0, 0.0, sz), quat)
                e1_len = math.sqrt(sum(v * v for v in e1_raw))
                e2_len = math.sqrt(sum(v * v for v in e2_raw))
                e3_len = math.sqrt(sum(v * v for v in e3_raw))
                e1 = tuple(v / e1_len for v in e1_raw) if e1_len > _GEOM_EPS else (1, 0, 0)
                e2 = tuple(v / e2_len for v in e2_raw) if e2_len > _GEOM_EPS else (0, 1, 0)
                e3 = tuple(v / e3_len for v in e3_raw) if e3_len > _GEOM_EPS else (0, 0, 1)

                off_e1 = slot_margin * e1_len
                off_e2 = tg_geom["gap_start_h"] * e2_len
                off_e3 = tg_geom["inlet_offset"] * e3_len

                ox = ax + off_e1 * e1[0] + off_e2 * e2[0] + off_e3 * e3[0]
                oy = ay + off_e1 * e1[1] + off_e2 * e2[1] + off_e3 * e3[1]
                oz = az + off_e1 * e1[2] + off_e2 * e2[2] + off_e3 * e3[2]

                span = (
                    tg_geom["slot_width"] * e1_len,
                    tg_geom["gap_height"] * e2_len,
                    tg_geom["channel_depth"] * e3_len,
                )

                # Same handedness fix as the rack region box above: a mirrored
                # rack would otherwise flip this thin-gap box downward too.
                (ox, oy, oz), e1, e2, e3 = _ensure_right_handed_box(
                    (ox, oy, oz), e1, e2, e3, span
                )

                geom_lines.append(f"""    {tg_name}
    {{
        type searchableRotatedBox;
        origin ({ox:.6f} {oy:.6f} {oz:.6f});
        e1 ({e1[0]:.6f} {e1[1]:.6f} {e1[2]:.6f});
        e2 ({e2[0]:.6f} {e2[1]:.6f} {e2[2]:.6f});
        e3 ({e3[0]:.6f} {e3[1]:.6f} {e3[2]:.6f});
        span ({span[0]:.6f} {span[1]:.6f} {span[2]:.6f});
    }}""")
            else:
                # ── Fallback: axis-aligned searchableBox (no quaternion) ──
                # Rack local axes: e1=X (width), e2=Y (height), e3=Z (depth)
                mn_x = ax + slot_margin
                mn_y = ay + tg_geom["gap_start_h"]
                mn_z = az + tg_geom["inlet_offset"]
                mx_x = mn_x + tg_geom["slot_width"]
                mx_y = mn_y + tg_geom["gap_height"]
                mx_z = mn_z + tg_geom["channel_depth"]

                geom_lines.append(f"""    {tg_name}
    {{
        type searchableBox;
        min ({mn_x:.6f} {mn_y:.6f} {mn_z:.6f});
        max ({mx_x:.6f} {mx_y:.6f} {mx_z:.6f});
    }}""")

            thin_gap_rz_lines.append(f"""        {tg_name}
        {{
            mode inside;
            levels ((1e15 {tg['box_level']}));
        }}""")

            cell_size = base_size / (2 ** rack_region_level)
            cell_at_box = base_size / (2 ** tg["box_level"])
            logger.info(
                f"Rack '{tg['rack_name']}': thin gap at units "
                f"{tg['start_unit']}-{tg['end_unit']} "
                f"({tg['gap_height']*1000:.1f}mm, "
                f"{tg['gap_height']/cell_size:.1f} cells at level {rack_region_level}) "
                f"→ auto-refinement box at level {tg['box_level']} "
                f"({cell_at_box*1000:.1f}mm cells, "
                f"{tg['gap_height']/cell_at_box:.1f} cells across gap)"
            )

        geom_str = "\n\n".join(geom_lines)
        source_surface_str = self._source_surface_dict_block(mc)
        features_str = "\n".join(feature_lines)
        refine_str = "\n\n".join(refine_lines)

        # ── Refinement regions ──
        # Combines user-defined searchableBox zones + rack refinement region entries.
        rz_region_lines = []
        for rz in rz_list:
            rz_name = _sanitize(rz["name"])
            lvl = rz.get("level", 2)
            rz_region_lines.append(f"""        {rz_name}
        {{
            mode inside;
            levels ((1e15 {lvl}));
        }}""")
        # Add rack refinementRegion entries
        for rr_name in rack_region_names:
            rz_region_lines.append(f"""        {rr_name}
        {{
            mode inside;
            levels ((1e15 {rack_region_level}));
        }}""")
        rz_region_lines.extend(thin_gap_rz_lines)
        rz_regions_str = "\n\n".join(rz_region_lines) if rz_region_lines else ""

        # Cartesian modes (cartesianLocalized / structured) run castellated-only
        # (snap off) → pure octree Cartesian / stair-step mesh. bodyFitted keeps snap on.
        snap_setting = "off" if _mesh_mode_is_cartesian(mesh_mode) else "on"

        # --- castellation speed knobs ---
        # maxLoadUnbalance: snappy re-partitions the mesh whenever a refinement
        # iteration pushes the per-processor cell imbalance past this.
        #
        # At the stock 0.10 it redistributed after nearly every iteration:
        # Base_leakage (607k cells, 8 procs) spent 20 s of a 34 s run doing it,
        # for a mesh whose cell count barely moved. Raising it to 0.50 took that
        # run to 16.2 s and turning rebalancing off entirely (10.0) to 11.3 s.
        #
        # But "off entirely" does not scale. Alibaba-hongchou (4.3M cells,
        # 24 procs) drifted to unbalance 2.64 — one processor carrying 3.6x the
        # average — and the whole run is gated by that processor. It blew the
        # 900 s budget.
        #
        # 0.50 is the compromise the two data points support: loose enough to
        # skip the churn on a case that stays balanced anyway, tight enough to
        # step in before an imbalance dominates the run. Override per case with
        # meshConfig.maxLoadUnbalance.
        max_load_unbalance = mc.get("maxLoadUnbalance")
        if max_load_unbalance is None:
            max_load_unbalance = 0.50 if _mesh_mode_is_cartesian(mesh_mode) else 0.10
        # handleSnapProblems detects and removes cells that WOULD be awkward to
        # snap. With `snap off` nothing ever snaps, so the whole phase is dead
        # work (~2.8 s here). Default `true` in refinementParameters.C.
        handle_snap_problems = "false" if _mesh_mode_is_cartesian(mesh_mode) else "true"

        content = f"""\
/*--------------------------------*- C++ -*----------------------------------*\\
| =========                 |                                                 |
| \\\\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox           |
|  \\\\    /   O peration     |                                                 |
\\*---------------------------------------------------------------------------*/
FoamFile
{{
    version 2.0;
    format ascii;
    class dictionary;
    location system;
    object snappyHexMeshDict;
}}

castellatedMesh on;
snap {snap_setting};
addLayers off;

// snappyHexMesh DELETES zero-sized patches when both snap and addLayers are
// off (snappyHexMesh.C:1833) — which is every Cartesian mode. A surface whose
// region ends up with no exposed faces (an ACU bottom flush with the floor, a
// pillar top against the ceiling, a rack face inside a containment) then
// vanishes from polyMesh/boundary entirely, and the boundary condition bound
// to it is silently lost. Keeping the empty patch costs nothing and keeps
// every declared STL region addressable.
keepPatches true;

geometry
{{
{geom_str}
}}

{source_surface_str}

castellatedMeshControls
{{
    maxLocalCells   {mc.get('maxGlobalCells', 20000000)};
    maxGlobalCells  {mc.get('maxGlobalCells', 20000000)};
    minRefinementCells 5;
    nCellsBetweenLevels 1;
    maxLoadUnbalance {max_load_unbalance};
    handleSnapProblems {handle_snap_problems};
    allowFreeStandingZoneFaces true;
    resolveFeatureAngle 30;

    features
    (
{features_str}
    );

    refinementSurfaces
    {{
{refine_str}
    }}

    refinementRegions
    {{
{rz_regions_str}
    }}

    // Use locationsInMesh with explicit cell zone assignment
    locationsInMesh
    (
{locations_str}
    );
}}

snapControls
{{
    nSmoothPatch 1;
    tolerance 1.5;
    nSolveIter 20;
    nRelaxIter 5;
    nFeatureSnapIter 10;
    implicitFeatureSnap true;
    explicitFeatureSnap true;
    multiRegionFeatureSnap true;
    nFaceSplitInterval 5;
    releasePoints true;
}}

addLayersControls
{{
    relativeSizes true;
    expansionRatio 1.0;
    finalLayerThickness 0.3;
    minThickness 0.1;
    nGrow 0;
    featureAngle 60;
    nRelaxIter 3;
    nSmoothSurfaceNormals 1;
    nSmoothNormals 3;
    nSmoothThickness 10;
    maxFaceThicknessRatio 0.5;
    maxThicknessToMedialRatio 0.3;
    minMedianAxisAngle 90;
    nBufferCellsNoExtrude 0;
    nLayerIter 50;
    layers
    {{
    }}
}}

meshQualityControls
{{
    maxNonOrtho 60;
    maxBoundarySkewness 20;
    maxInternalSkewness 4;
    maxConcave 80;
    minFlatness 0.5;
    minVol 1e-13;
    minTetQuality 1e-15;
    minArea -1;
    minTwist 0.02;
    minDeterminant 0.001;
    minFaceWeight 0.05;
    minVolRatio 0.01;
    minTriangleTwist -1;
    nSmoothScale 4;
    errorReduction 0.75;
    relaxed
    {{
        maxNonOrtho 70;
    }}
}}

writeFlags
(
    scalarLevels
);

mergeTolerance 1e-6;
"""
        (system_dir / "snappyHexMeshDict").write_text(content, encoding="utf-8")
        logger.info(f"Generated snappyHexMeshDict with {len(through_zone_solids)} through-zone solids (refinementRegions)")

    # ------------------------------------------------------------------
    # Mesh pipeline
    # ------------------------------------------------------------------

    def _get_uniform_door_sigma(self) -> float:
        """Return the shared sigma for all rack doors.

        All rack doors are merged into a single merged_rack_door.stl file
        and share one createBafflesDict entry, so they must have the same
        openAreaPct. Raises ValueError if multiple distinct values are found.
        """
        sigmas: set = set()
        for rack_name, rack in self._data.get("racks", {}).items():
            for side in ("inlet", "outlet"):
                door = rack.get(f"{side}Door")
                if not door or not door.get("enabled"):
                    continue
                perf = door.get("perforated") or {"openAreaPct": 64.0}
                sigmas.add(float(perf.get("openAreaPct", 64.0)) / 100.0)
        if len(sigmas) > 1:
            raise ValueError(f"All rack doors must have same openAreaPct, got: {sigmas}")
        return sigmas.pop() if sigmas else 0.64

    def _generate_create_baffles_dict(self) -> None:
        """Generate createBafflesDict for perforated floor openings and
        zero-thickness baffles (containment panels, partition walls).

        ALL baffles use type cyclic in createBafflesDict.
        Wall baffles are converted from cyclic to wall type in the boundary
        file AFTER createBaffles runs, via _convert_wall_baffle_patches_to_wall().
        """
        data = self._data
        baffle_configs = []
        wall_baffle_configs = []

        def _sigma_from_phys(p: dict, default_pct: float = 50.0) -> float:
            """Resolve porosity fraction from new perforated block or legacy
            porosity scalar (new format wins). Returns sigma in [0, 1]."""
            perf = p.get("perforated") if isinstance(p, dict) else None
            if perf and perf.get("openAreaPct") is not None:
                return max(0.0, min(1.0, float(perf["openAreaPct"]) / 100.0))
            porosity_pct = p.get("porosity", default_pct)
            try:
                return max(0.0, min(1.0, float(porosity_pct) / 100.0))
            except (TypeError, ValueError):
                return default_pct / 100.0

        for floor in data.get("floors", {}).values():
            for op in floor.get("openings", []):
                if op.get("physics", {}).get("type") == "perforated":
                    zone_name = _sanitize(op["solidName"])
                    porosity = _sigma_from_phys(op["physics"])
                    porosity_pct = porosity * 100.0

                    if porosity_pct <= 0:
                        # porosity=0 → fully blocked → degrade to no-slip wall
                        wall_baffle_configs.append({"zone_name": zone_name})
                    elif porosity_pct >= 100:
                        # porosity=100 → fully open → pure cyclic (no pressure drop)
                        baffle_configs.append({
                            "zone_name": zone_name,
                            "porosity": 1.0,
                            "d_coeff": 0.0,
                            "baffleType": "cyclic_only",  # no porousBafflePressure
                        })
                    else:
                        # 0 < porosity < 100 → cyclic + porousBafflePressure
                        d_coeff = (1.0 - porosity**2) / (2.0 * porosity**2)
                        baffle_configs.append({
                            "zone_name": zone_name,
                            "porosity": porosity,
                            "d_coeff": d_coeff,
                            "baffleType": "perforated",
                        })

        # ALL baffle model solids get createBaffles entries (type cyclic).
        # Wall baffles → cyclic (converted to wall in boundary post-processing).
        # Perforated → cyclic + porousBafflePressure BCs.
        # Skip solids already handled by the floor perforated loop above.
        #
        # Post-merge (Task 2): individual baffle files are:
        #   perforated_baffle_*.stl — perforated baffle models
        #   perforated_floor_*.stl  — perforated floor patches
        #   baffle_*.stl            — legacy (fallback)
        # merged_rack_door.stl   → ONE unified cyclic entry
        _already_added = set(bc["zone_name"] for bc in baffle_configs)

        # merged_wall_baffle.stl → ONE unified wall_baffle entry.
        # Surface-level faceZone "merged_wall_baffle" in snappyHexMeshDict
        # keeps all faces internal; createBaffles creates ONE master/slave
        # pair, then _convert_wall_baffle_patches_to_wall() makes them wall.
        if "merged_wall_baffle.stl" in self._stl_solids:
            if "merged_wall_baffle" not in _already_added:
                wall_baffle_configs.append({"zone_name": "merged_wall_baffle"})
                _already_added.add("merged_wall_baffle")

        # wallBaffle_floor_*.stl → ONE wall_baffle entry PER solid (own faceZone).
        # Gives each floor wallBaffle solid its own <solid>_master/_slave pair,
        # matching the manifest's porousBindings (e.g. raised_floor_part1).
        for filename, file_solids in self._stl_solids.items():
            if not filename.startswith("wallBaffle_floor_"):
                continue
            for solid in file_solids:
                zone_name = _sanitize(solid)
                if zone_name in _already_added:
                    continue
                wall_baffle_configs.append({"zone_name": zone_name})
                _already_added.add(zone_name)

        # Scan individual perforated/baffle files AND legacy baffle_ files
        for filename, file_solids in self._stl_solids.items():
            is_perforated_individual = (
                filename.startswith("perforated_baffle_")
                or filename.startswith("perforated_floor_")
            )
            is_legacy_baffle = filename.startswith("baffle_")
            if not is_perforated_individual and not is_legacy_baffle:
                continue
            # 2026-04-17: through-zone STLs must NEVER produce a baffle pair.
            bucket = _parse_bucket_from_filename(filename)
            if bucket and bucket[0] == "through_zone":
                continue
            for solid in file_solids:
                zone_name = _sanitize(solid)
                if zone_name in _already_added:
                    continue
                phys = self._baffle_physics.get(zone_name, {"baffleType": "wall", "porosity": 0})
                if phys.get("directWall"):
                    continue
                baffle_type = phys.get("baffleType", "wall")
                porosity = _sigma_from_phys(phys, default_pct=0.0)
                porosity_pct = porosity * 100.0

                if porosity_pct > 0 or baffle_type == "perforated":
                    if porosity <= 0 and baffle_type == "perforated":
                        porosity = 0.5
                    d_coeff = 0.0 if porosity >= 1.0 else (
                        (1.0 - porosity**2) / (2.0 * porosity**2) if porosity > 0 else 1e6
                    )
                    baffle_configs.append({
                        "zone_name": zone_name,
                        "porosity": min(porosity, 1.0),
                        "d_coeff": d_coeff,
                        "baffleType": "perforated",
                    })
                else:
                    wall_baffle_configs.append({"zone_name": zone_name})
                _already_added.add(zone_name)

        # merged_rack_door.stl → ONE unified cyclic entry for ALL door solids.
        # All doors share a single faceZone "merged_rack_door" so one
        # createBafflesDict entry suffices.
        if "merged_rack_door.stl" in self._stl_solids:
            if "merged_rack_door" not in _already_added:
                sigma = self._get_uniform_door_sigma()
                d_coeff = (1.0 - sigma**2) / (2.0 * sigma**2) if 0 < sigma < 1.0 else 0.0
                baffle_configs.append({
                    "zone_name": "merged_rack_door",
                    "porosity": sigma,
                    "d_coeff": d_coeff,
                    "baffleType": "perforated",
                })
                _already_added.add("merged_rack_door")
                logger.info(f"createBaffles: merged_rack_door → perforated σ={sigma:.2f}")

        # Legacy bucket files (merged_door_<N>U.stl, merged_wallBaffle_<N>U.stl)
        for filename, file_solids in self._stl_solids.items():
            bucket = _parse_bucket_from_filename(filename)
            if bucket is None:
                continue
            cat = bucket[0]

            if cat == "door":
                DEFAULT_DOOR_OPEN_AREA_PCT = 64.0
                rack_san_to_data: Dict[str, Any] = {
                    _sanitize(rn): rd for rn, rd in data.get("racks", {}).items()
                }
                for solid in file_solids:
                    zone_name = _sanitize(solid)
                    if zone_name in _already_added:
                        continue
                    sigma = DEFAULT_DOOR_OPEN_AREA_PCT / 100.0
                    for rack_san, rack in rack_san_to_data.items():
                        for side in ("inlet", "outlet"):
                            if zone_name == f"{rack_san}_{side}_door":
                                door = rack.get(f"{side}Door") or {}
                                perf = door.get("perforated")
                                if perf and perf.get("openAreaPct") is not None:
                                    sigma = float(perf["openAreaPct"]) / 100.0
                                elif door.get("porosity") is not None:
                                    sigma = float(door["porosity"]) / 100.0
                                break
                    sigma = max(0.0, min(1.0, sigma))
                    if sigma <= 0:
                        wall_baffle_configs.append({"zone_name": zone_name})
                    else:
                        d_coeff = (1.0 - sigma**2) / (2.0 * sigma**2) if sigma < 1.0 else 0.0
                        baffle_configs.append({
                            "zone_name": zone_name,
                            "porosity": sigma,
                            "d_coeff": d_coeff,
                            "baffleType": "perforated",
                        })
                    _already_added.add(zone_name)

            elif cat == "wallBaffle":
                for solid in file_solids:
                    zone_name = _sanitize(solid)
                    if zone_name in _already_added:
                        continue
                    wall_baffle_configs.append({"zone_name": zone_name})
                    _already_added.add(zone_name)

            elif cat == "perforatedBlankingPanel":
                bp_stem = Path(filename).stem
                if bp_stem in _already_added:
                    continue
                _d_bp = (1.0 - _BLANKING_PANEL_SIGMA**2) / (2.0 * _BLANKING_PANEL_SIGMA**2)
                baffle_configs.append({
                    "zone_name": bp_stem,
                    "porosity": _BLANKING_PANEL_SIGMA,
                    "d_coeff": _d_bp,
                    "baffleType": "perforated",
                })
                _already_added.add(bp_stem)
                logger.info(f"createBaffles: {bp_stem} → perforated σ={_BLANKING_PANEL_SIGMA}")

            elif cat == "wallBlankingPanel":
                # fullBlock = solid wall: no createBaffles entry (snappy emits no
                # faceZone for it), consistent with the snappyHexMeshDict branch.
                pass

        # Deduplicate: track zone names already added
        _added_zones = set(bc["zone_name"] for bc in baffle_configs)
        _added_zones.update(wbc["zone_name"] for wbc in wall_baffle_configs)

        # Wall-type baffles on floors (faceType internal + createBaffles cyclic → post-process to wall)
        for floor_name, floor in data.get("floors", {}).items():
            default_bc = floor.get("defaultBC", "wall")
            if default_bc != "wallBaffle":
                continue
            san_floor_name = _sanitize(floor_name)
            opening_solid_names = set()
            for op in floor.get("openings", []):
                opening_solid_names.add(_sanitize(op["solidName"]))
            for bf_name, bf_solids in self._stl_solids.items():
                if bf_name == "merged_wall_baffle.stl":
                    continue
                if (bf_name.startswith("baffle_")
                        or bf_name.startswith("perforated_floor_")):
                    for solid in bf_solids:
                        s = _sanitize(solid)
                        if s.startswith(san_floor_name) and s not in opening_solid_names and s not in _added_zones:
                            wall_baffle_configs.append({"zone_name": s})
                            _added_zones.add(s)

        # Store wall baffle zone names for post-createBaffles boundary conversion
        self._wall_baffle_zone_names = {wbc["zone_name"] for wbc in wall_baffle_configs}

        if not baffle_configs and not wall_baffle_configs:
            stale = self.mesh_dir / "system" / "createBafflesDict"
            if stale.exists():
                stale.unlink()
            return

        system_dir = self.mesh_dir / "system"
        entries = []
        # Perforated opening baffles → cyclic pairs
        for bc in baffle_configs:
            entries.append(f"""    {bc['zone_name']}
    {{
        type        faceZone;
        zoneName    {bc['zone_name']};

        patches
        {{
            master
            {{
                name            {bc['zone_name']}_master;
                type            cyclic;
                neighbourPatch  {bc['zone_name']}_slave;
            }}
            slave
            {{
                name            {bc['zone_name']}_slave;
                type            cyclic;
                neighbourPatch  {bc['zone_name']}_master;
            }}
        }}
    }}""")

        # Wall-type baffles → also cyclic in createBafflesDict (same as perforated).
        # After createBaffles, _convert_wall_baffle_patches_to_wall() converts
        # these cyclic patches to wall type in the boundary file.
        for wbc in wall_baffle_configs:
            zn = wbc['zone_name']
            entries.append(f"""    {zn}
    {{
        type        faceZone;
        zoneName    {zn};

        patches
        {{
            master
            {{
                name            {zn}_master;
                type            cyclic;
                neighbourPatch  {zn}_slave;
            }}
            slave
            {{
                name            {zn}_slave;
                type            cyclic;
                neighbourPatch  {zn}_master;
            }}
        }}
    }}""")

        content = f"""\
/*--------------------------------*- C++ -*----------------------------------*\\
| =========                 |                                                 |
| \\\\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox           |
|  \\\\    /   O peration     |                                                 |
\\*---------------------------------------------------------------------------*/
FoamFile
{{
    version 2.0;
    format ascii;
    class dictionary;
    location system;
    object createBafflesDict;
}}

internalFacesOnly true;

baffles
{{
{"".join(entries)}
}}
"""
        (system_dir / "createBafflesDict").write_text(content, encoding="utf-8")
        logger.info(f"Generated createBafflesDict with {len(baffle_configs)} perforated + {len(wall_baffle_configs)} wall baffles")

    def _ensure_mesh_system_files(self) -> None:
        """Write minimal controlDict + fvSchemes + fvSolution so blockMesh/snappyHexMesh can run."""
        system_dir = self.mesh_dir / "system"
        system_dir.mkdir(parents=True, exist_ok=True)

        sc = self._data.get("simulationConfig", {}) or {}
        wp_str = sc.get("writePrecision")
        if wp_str in ("single", "double"):
            double_prec = wp_str == "double"
        else:
            double_prec = bool(sc.get("doublePrecisionWrite", False))
        write_prec = 10 if double_prec else 6

        # Mesh-stage polyMesh I/O format. ASCII costs real time on every mesh
        # step that touches constant/polyMesh — measured on Base_leakage:
        # reconstructParMesh 10.3 s -> 1.8 s and checkMesh 10.4 s -> 8.9 s just
        # from switching to binary. The header of each file records its own
        # format, so downstream OpenFOAM tools and the solver read it either way;
        # `boundary` stays an ASCII dictionary, so the runner's regex patching of
        # it is unaffected. The SOLVER controlDict is a separate Jinja template
        # (foam/template/system/{steady,transient}/controlDict.j2) and still
        # governs field output format.
        _mc_io = self._data.get("meshConfig", {}) or {}
        mesh_write_format = "ascii" if _mc_io.get("asciiMeshIO") else "binary"

        stubs = {
            "controlDict": f"""\
FoamFile {{ version 2.0; format ascii; class dictionary; object controlDict; }}
application     snappyHexMesh;
startFrom       startTime;
startTime       0;
stopAt          endTime;
endTime         0;
deltaT          1;
writeControl    timeStep;
writeInterval   1;
writeFormat     {mesh_write_format};
writePrecision  {write_prec};
writeCompression off;
timeFormat      general;
timePrecision   6;
runTimeModifiable true;
""",
            "fvSchemes": """\
FoamFile { version 2.0; format ascii; class dictionary; object fvSchemes; }
ddtSchemes { default steadyState; }
gradSchemes { default Gauss linear; }
divSchemes { default none; }
laplacianSchemes { default Gauss linear corrected; }
interpolationSchemes { default linear; }
snGradSchemes { default corrected; }
""",
            "fvSolution": """\
FoamFile { version 2.0; format ascii; class dictionary; object fvSolution; }
solvers { }
""",
        }

        for name, content in stubs.items():
            path = system_dir / name
            # controlDict is always refreshed — it carries writeFormat, and a case
            # meshed before the binary switch would otherwise keep its ASCII stub
            # forever. fvSchemes/fvSolution are only seeded when missing.
            if name == "controlDict" or not path.exists():
                path.write_text(content, encoding="utf-8")
                logger.info(f"Written minimal {name} for mesh pipeline")

    def _ensure_wall_baffles_are_wall_type(self, boundary_path: Path) -> None:
        """Ensure wall_baffle patches have type=wall in boundary.

        Uses the in-memory bridge registry as the authoritative source for
        which patches are wall baffles. This works in both mesh and solve-only
        modes (unlike _convert_wall_baffle_patches_to_wall which depends on
        _wall_baffle_zone_names being populated during mesh dict generation).
        """
        if self._bridge_registry is None or not boundary_path.exists():
            return
        wall_patches = {
            name for name, spec in self._bridge_registry.items()
            if spec.bc_type == "wall_baffle"
        }
        if not wall_patches:
            return
        content = boundary_path.read_text(encoding="utf-8")
        modified = False
        for patch_name in wall_patches:
            pattern = re.compile(
                rf'(\s+{re.escape(patch_name)}\s*\n\s*\{{\s*\n)(\s*type\s+)cyclic(\s*;)',
                re.MULTILINE,
            )
            if pattern.search(content):
                content = pattern.sub(r'\1\2wall\3', content)
                content = re.sub(
                    rf'(\s+{re.escape(patch_name)}\s*\n(?:.*\n)*?\s*)neighbourPatch\s+\S+\s*;\n',
                    r'\1', content,
                )
                content = re.sub(
                    rf'(\s+{re.escape(patch_name)}\s*\n(?:.*\n)*?\s*)inGroups\s+\d+\((?:[^)]*cyclic[^)]*)\)\s*;\n',
                    r'\1', content,
                )
                modified = True
        if modified:
            boundary_path.write_text(content, encoding="utf-8")
            logger.info(f"Ensured wall baffle patches have type=wall (from bridge registry)")

    def _drop_empty_baffle_patches(
        self, case_dir: Path, mesh_root: Optional[Path] = None
    ) -> int:
        """Remove zero-face patches whose names collide with createBaffles output.

        `keepPatches true` stops snappyHexMesh deleting zero-sized patches, which
        is what keeps a flush-mounted ACU bottom addressable. But the branch it
        disables is literally "remove zero sized patches originating from
        faceZones" — so the `<zone>_master` / `<zone>_slave` patches snappy makes
        for a baffle faceZone now survive too, as type `wall`. createBaffles then
        finds the name taken, reuses the existing patch, and dies:

            Patch 'Room_Raising_FLoor_part1_slave' already exists.
            --> FOAM FATAL ERROR: Attempt to cast type wall to type cyclic

        snappy has no per-surface keepPatches, so the collision is resolved here
        instead: drop exactly the empty patches createBaffles is about to create.
        A zero-face patch owns no faces, so removing it needs no renumbering —
        only the leading count changes.
        """
        # createBafflesDict is always in the case; the boundary file is in the
        # case for a serial mesh and in each processor*/ for a decomposed one.
        boundary_path = (mesh_root or case_dir) / "constant" / "polyMesh" / "boundary"
        baffles_dict = case_dir / "system" / "createBafflesDict"
        if not boundary_path.exists() or not baffles_dict.exists():
            return 0

        targets = set(
            re.findall(r"\bname\s+(\S+?);", baffles_dict.read_text(encoding="utf-8"))
        )
        if not targets:
            return 0

        text = boundary_path.read_text(encoding="utf-8")
        block_re = re.compile(
            r"\n(\s{4})([A-Za-z_][\w.]*)\n\s{4}\{(.*?)\n\s{4}\}", re.DOTALL
        )
        removed: List[str] = []

        def _drop(match: "re.Match") -> str:
            name, body = match.group(2), match.group(3)
            if name not in targets:
                return match.group(0)
            n_faces = re.search(r"nFaces\s+(\d+)\s*;", body)
            if not n_faces or int(n_faces.group(1)) != 0:
                return match.group(0)
            removed.append(name)
            return ""

        new_text = block_re.sub(_drop, text)
        if not removed:
            return 0

        # the patch count sits on its own line just before the opening '('
        def _fix_count(m: "re.Match") -> str:
            return f"\n{int(m.group(1)) - len(removed)}\n("

        new_text = re.sub(r"\n(\d+)\n\(", _fix_count, new_text, count=1)
        boundary_path.write_text(new_text, encoding="utf-8")
        logger.info(
            f"Dropped {len(removed)} empty patch(es) that would collide with "
            f"createBaffles: {', '.join(sorted(removed)[:6])}"
            + ("…" if len(removed) > 6 else "")
        )
        return len(removed)

    def _convert_wall_baffle_patches_to_wall(self, case_dir: Path) -> None:
        """Convert wall baffle patches from cyclic to wall type in boundary file.

        createBaffles creates ALL baffles as cyclic. Wall baffles need to be
        converted to wall type afterwards so they get no-slip BCs.
        """
        boundary_path = case_dir / "constant" / "polyMesh" / "boundary"
        if not boundary_path.exists():
            return
        wall_patch_names = set()
        for zn in self._wall_baffle_zone_names:
            wall_patch_names.add(f"{zn}_master")
            wall_patch_names.add(f"{zn}_slave")
        # Also include perforated baffles with porosity=0. Use the new format
        # (`perforated.openAreaPct`) when present, falling back to the legacy
        # `porosity` scalar. Pydantic always emits `porosity: 0.0` even when
        # the user-authored cfdrun specified perforated; without this check
        # the patch would be degraded to `wall` in the boundary file while
        # fields.py still emits `porousBafflePressure` → solver crash.
        for floor in self._data.get("floors", {}).values():
            for op in floor.get("openings", []):
                phys = op.get("physics", {})
                if phys.get("type") == "perforated":
                    perf = phys.get("perforated") or {}
                    if perf.get("openAreaPct") is not None:
                        porosity_pct = float(perf["openAreaPct"])
                    else:
                        porosity_pct = float(phys.get("porosity", 50) or 0)
                    if porosity_pct <= 0:
                        zn = _sanitize(op["solidName"])
                        wall_patch_names.add(f"{zn}_master")
                        wall_patch_names.add(f"{zn}_slave")
        if not wall_patch_names:
            return
        # One linear pass. This used to run five
        # `\s+<name>\s*\n(?:.*\n)*?\s*<key>` substitutions per patch: the
        # unbounded `(?:.*\n)*?` prefix backtracks to end-of-file for every key
        # the block does not carry, so Base604's four wall baffles cost 0.62 s
        # on each of its 28,000-line boundary files -- 10 s of the mesh
        # pipeline for a four-line edit. Reading the block and rewriting it is
        # one O(file) pass and produces byte-identical output.
        lines = boundary_path.read_text(encoding="utf-8").split("\n")
        name_re = re.compile(r"^[ \t]+([A-Za-z0-9_.]+)[ \t]*$")
        cyclic_re = re.compile(r"^([ \t]*type[ \t]+)cyclic([ \t]*;[ \t]*)$")
        # neighbourPatch / matchTolerance / transform belong to a cyclic patch,
        # and an inGroups listing cyclic goes with them.
        drop_re = re.compile(
            r"^[ \t]*(?:neighbourPatch\s+\S+|matchTolerance\s+[\d.eE+-]+"
            r"|transform\s+\w+|inGroups\s+\d+\([^)]*cyclic[^)]*\))[ \t]*;[ \t]*$"
        )
        out: List[str] = []
        modified = False
        i, n = 0, len(lines)
        while i < n:
            line = lines[i]
            hit = name_re.match(line)
            if (
                hit
                and hit.group(1) in wall_patch_names
                and i + 1 < n
                and lines[i + 1].strip() == "{"
            ):
                end = i + 2
                while end < n and lines[end].strip() != "}":
                    end += 1
                body = lines[i + 2:end]
                if any(cyclic_re.match(b) for b in body):
                    modified = True
                    out.append(line)
                    out.append(lines[i + 1])
                    for b in body:
                        if cyclic_re.match(b):
                            out.append(cyclic_re.sub(r"\1wall\2", b))
                        elif not drop_re.match(b):
                            out.append(b)
                    if end < n:
                        out.append(lines[end])
                    i = end + 1
                    continue
            out.append(line)
            i += 1
        if modified:
            boundary_path.write_text("\n".join(out), encoding="utf-8")
            logger.info(f"Converted {len(wall_patch_names)} wall baffle patches from cyclic to wall")

    def _run_serial_mesh_pipeline(self) -> None:
        """Run blockMesh → surfaceFeatureExtract → snappyHexMesh → createBaffles → checkMesh."""
        self._start_mesh_budget()
        self._ensure_mesh_system_files()
        case_dir = self.mesh_dir
        total_steps = 3  # blockMesh + snappy + checkMesh (always); optional steps counted below
        baffles_dict = case_dir / "system" / "createBafflesDict"
        if baffles_dict.exists(): total_steps += 1
        feat_dict = case_dir / "system" / "surfaceFeatureExtractDict"
        if feat_dict.exists(): total_steps += 1
        step = 0

        self._clean_stale_polymesh_zones(case_dir)
        step += 1
        logger.info(f"Step {step}/{total_steps}: blockMesh")
        self._run_cmd("blockMesh", case_dir)

        if feat_dict.exists():
            step += 1
            logger.info(f"Step {step}/{total_steps}: surfaceFeatureExtract")
            self._run_cmd("surfaceFeatureExtract", case_dir)

        step += 1
        logger.info(f"Step {step}/{total_steps}: snappyHexMesh -overwrite")
        self._run_cmd("snappyHexMesh -overwrite", case_dir)
        self._diagnose_background_patch(case_dir)

        if baffles_dict.exists():
            step += 1
            logger.info(f"Step {step}/{total_steps}: createBaffles -overwrite")
            self._drop_empty_baffle_patches(case_dir)
            self._regenerate_fields_for_baffles(case_dir)
            self._run_cmd("createBaffles -overwrite", case_dir)
            self._convert_wall_baffle_patches_to_wall(case_dir)

        step += 1
        logger.info(f"Step {step}/{total_steps}: checkMesh (final quality check)")
        self._remove_disconnected_cells(case_dir)

    def _run_dcCart_mesh_pipeline(self, n_procs: int = 1) -> None:
        """dcCartMesh → createBaffles → checkMesh: the cut-cell Cartesian engine.

        dcCartMesh has ONE algorithm for any number of ranks; ``n_procs == 1``
        runs it without mpirun (OpenFOAM refuses -parallel on one rank) and
        that is the whole difference. With ``n_procs > 1`` every rank builds
        the same octree and carve, owns a slab of columns along x, cuts its
        own cells plus a halo of its neighbours' identically, merges slivers
        in rounds it resolves together with the other ranks (so the cells do
        not depend on the rank count), and writes processor<i>/constant/polyMesh
        with matched processor patches (same face order, same vertex 0).
        Baffle creation and quality checks run on those partitions, which are
        retained for the solver and direct OpenFOAM post-processing. A failed
        parallel run is an error: it
        does not fall back to one rank, which would only hide the failure.

        Replaces blockMesh + surfaceFeatureExtract + snappyHexMesh with ONE
        pass of the repo's own mesher. dcCartMesh reads the geometry,
        refinementSurfaces, refinementRegions and locationsInMesh from the
        snappyHexMeshDict this pipeline already wrote — so every patch comes
        out with the name the boundary conditions bind to — and takes its
        background grid from system/dcCartMeshDict, which carries the SAME
        cell planes blockMesh would have built (grid-aligned in ``structured``
        mode). It writes faceZones for the ``faceType internal`` surfaces, so
        createBaffles and the wall-baffle conversion run unchanged.
        """
        self._start_mesh_budget()
        self._ensure_mesh_system_files()
        case_dir = self.mesh_dir
        baffles_dict = case_dir / "system" / "createBafflesDict"
        total_steps = 2 + (1 if baffles_dict.exists() else 0)
        step = 0

        self._clean_stale_polymesh_zones(case_dir)
        self._write_dcCart_mesh_dict(case_dir / "system")

        # A previous serial mesh must never shadow the new processor meshes in
        # preview/readers. dcCart constructs its grid from dictionaries only.
        if n_procs > 1:
            (case_dir / "constant" / "dcCartPartitionInfo.json").unlink(missing_ok=True)
            serial_mesh = case_dir / "constant" / "polyMesh"
            if serial_mesh.exists():
                shutil.rmtree(serial_mesh)
            if (case_dir / "0").exists():
                shutil.rmtree(case_dir / "0")

        mesher = self._resolve_mesher_binary("dcCartMesh")
        remaining = self._mesh_time_remaining()
        budget = f" -timeLimit {max(1.0, remaining):.0f}" if remaining is not None else ""
        step += 1
        if n_procs > 1:
            total_steps += int(baffles_dict.exists())
            logger.info(f"Step {step}/{total_steps}: mpirun -np {n_procs} dcCartMesh -overwrite -parallel")
            if not self._run_dcCart_parallel(mesher, budget, n_procs, case_dir):
                raise RuntimeError(
                    f"dcCartMesh failed on {n_procs} MPI ranks (see the log above). "
                    "It is the same algorithm on any rank count, so a one-rank rerun "
                    "would hide the failure rather than fix it."
                )
        else:
            logger.info(f"Step {step}/{total_steps}: dcCartMesh -overwrite (1 rank)")
            self._run_cmd(f"{mesher} -overwrite{budget}", case_dir)
        if n_procs == 1:
            self._diagnose_background_patch(case_dir)

        if baffles_dict.exists() and self._dcCart_split_baffles(case_dir, n_procs):
            # dcCartMesh already wrote the pairs; neither pass has work left.
            total_steps -= 1                      # createBaffles
            if n_procs > 1:
                total_steps -= 1                  # redistributePar
            logger.info(
                "dcCart: baffle master/slave pairs written by the mesher; "
                "skipping redistributePar and createBaffles"
            )
        elif baffles_dict.exists():
            if n_procs > 1:
                # createBaffles cannot turn processor faces into cyclic faces.
                # Keep both sides of every baffle on one rank before splitting;
                # this redistributes the existing mesh without serial assembly.
                if self._dcCart_needs_redistribution(case_dir, n_procs):
                    generate_decompose_par_dict(
                        case_dir, n_procs, preserve_face_zones=True, method="hierarchical"
                    )
                    step += 1
                    logger.info(f"Step {step}/{total_steps}: redistributePar (preserveFaceZones, {n_procs} ranks)")
                    redistribute_cmd = f"mpirun -np {n_procs} redistributePar -parallel -overwrite"
                    try:
                        self._run_cmd(redistribute_cmd, case_dir)
                    except MeshTimeoutError:
                        raise
                    except RuntimeError as first:
                        # A rank that died mid-migration leaves processor dirs
                        # no second redistributePar can read, but dcCartMesh
                        # rebuilds them from dictionaries in seconds, so the
                        # recovery is: re-cut, re-decompose, migrate again. One
                        # retry only — a second death is a real defect and must
                        # surface with both attempts' logs preserved.
                        logger.error(
                            f"redistributePar failed ({first}); re-running "
                            f"dcCartMesh and retrying the migration once"
                        )
                        if not self._run_dcCart_parallel(mesher, budget, n_procs, case_dir):
                            raise
                        generate_decompose_par_dict(
                            case_dir, n_procs, preserve_face_zones=True, method="hierarchical"
                        )
                        self._run_cmd(redistribute_cmd, case_dir)
                else:
                    total_steps -= 1
                    logger.info("dcCart: all baffle zone faces are local; skipping redistributePar")
                for i in range(n_procs):
                    self._drop_empty_baffle_patches(case_dir, case_dir / f"processor{i}")
                step += 1
                logger.info(f"Step {step}/{total_steps}: createBaffles -overwrite -parallel ({n_procs} ranks)")
                self._run_cmd(f"mpirun -np {n_procs} createBaffles -overwrite -parallel", case_dir)
                for i in range(n_procs):
                    self._convert_wall_baffle_patches_to_wall(case_dir / f"processor{i}")
            else:
                step += 1
                logger.info(f"Step {step}/{total_steps}: createBaffles -overwrite")
                self._drop_empty_baffle_patches(case_dir)
                self._regenerate_fields_for_baffles(case_dir)
                self._run_cmd("createBaffles -overwrite", case_dir)
                self._convert_wall_baffle_patches_to_wall(case_dir)

        # An open room shell still meshes; say so while the patch names are final.
        self._warn_open_room_enclosure(case_dir)

        step += 1
        logger.info(f"Step {step}/{total_steps}: checkMesh (final quality check)")
        if n_procs > 1:
            self._remove_disconnected_cells(case_dir, n_procs=n_procs)
        else:
            self._remove_disconnected_cells(case_dir)

    @staticmethod
    def _dcCart_split_baffles(case_dir: Path, n_procs: int) -> bool:
        """True when dcCartMesh wrote the baffle master/slave pairs itself.

        The mesher only says so after it has split every zone face whose two
        cells it owns AND routed every face a rank boundary separates into a
        ``processorCyclic`` patch -- the two things redistributePar and
        createBaffles were run for. A missing or malformed flag (an older
        binary, a serial run that writes no partition info) keeps the old
        two-pass path, so this can only ever add a shortcut, never lose a
        baffle.
        """
        try:
            info = json.loads((case_dir / "constant" / "dcCartPartitionInfo.json").read_text())
        except (OSError, ValueError):
            return False
        return (
            isinstance(info, dict)
            and info.get("bafflesSplitByMesher") is True
            and type(info.get("nProcs")) is int
            and info["nProcs"] == n_procs
        )

    @staticmethod
    def _dcCart_needs_redistribution(case_dir: Path, n_procs: int) -> bool:
        """Skip migration only when this mesher proved every zone face is local.

        Older binaries omitted processor zone tags, so inferring this from
        their faceZones files could silently lose baffles. Missing or malformed
        capability metadata keeps the conservative redistribution path.
        """
        try:
            info = json.loads((case_dir / "constant" / "dcCartPartitionInfo.json").read_text())
        except (OSError, ValueError):
            return True
        return not (
            isinstance(info, dict)
            and type(info.get("nProcs")) is int
            and info["nProcs"] == n_procs
            and type(info.get("processorZoneFaceCopies")) is int
            and info["processorZoneFaceCopies"] == 0
        )

    def _room_boundary_region_names(self) -> List[str]:
        """The room enclosure in the mesh geometry: the room walls
        (isRoomBoundary not false) and the top / bottom room floors."""
        data = self._data or {}
        names = [
            n for n, w in (data.get("walls") or {}).items()
            if isinstance(w, dict) and w.get("isRoomBoundary") is not False
        ]
        names += [
            n for n, f in (data.get("floors") or {}).items()
            if isinstance(f, dict) and f.get("roomBoundaryType") in ("top", "bottom")
        ]
        return names

    def _warn_open_room_enclosure(self, case_dir: Path) -> None:
        """Log a WARNING when the mesh shows an open room enclosure.

        dcCartMesh meshes an open room shell without complaint: the fluid leaks
        to the mesh bounding box (domain_* patches) and the room walls, floor and
        ceiling end up with no patch, so the run looks successful. Only a mesh
        with a domain_* patch holding faces pays for the patch/solid
        correspondence check (about 20 s on Base604). The warning is advisory: a
        check that cannot run never fails the mesh.
        """
        from backend.aircooling.runner.mesh_layout import mesh_boundary_paths

        root_boundary = case_dir / "constant" / "polyMesh" / "boundary"
        boundaries = [root_boundary] if root_boundary.exists() else mesh_boundary_paths(case_dir)
        domain_names = {
            name for boundary in boundaries for name in _domain_patches_with_faces(boundary)
        }
        if not domain_names:
            return
        if len(boundaries) > 1:
            # The native acceptance geometry reader is serial. The domain faces
            # themselves prove that fluid reached outside the room, so report
            # them without reconstructing millions of cells just for a warning.
            logger.warning(
                f"Room enclosure may be open: fluid reached the mesh bounding box "
                f"({', '.join(sorted(domain_names))}). Check that the room walls "
                "reach room.height (房间外壳可能不封闭，请检查墙高和围护面)."
            )
            return
        from backend.aircooling.runner import mesh_acceptance

        try:
            result = mesh_acceptance.check_patch_solid_correspondence(
                case_dir, case_name=self.case_dir.name
            )
        except Exception as exc:
            logger.warning(f"Room enclosure check skipped: {exc}")
            return
        room = self._room_boundary_region_names()
        missing = [
            p for p in result.missing_patches
            if any(p == r or p.startswith(f"{r}_") for r in room)
        ]
        domain = [p for p in result.unexpected_patches if _DOMAIN_PATCH_RE.match(p)]
        if missing and domain:
            logger.warning(
                f"Room enclosure is open: {', '.join(missing)} got no mesh faces, and the "
                f"fluid reached the mesh bounding box ({', '.join(domain)}). Check that the "
                f"room walls reach room.height (房间外壳不封闭，请检查房间墙是否达到天花板高度)."
            )

    def _run_dcCart_parallel(self, mesher: str, budget: str, n_procs: int, case_dir: Path) -> bool:
        """mpirun dcCartMesh; True when every rank wrote its processor mesh.

        dcCartMesh reads only ``numberOfSubdomains`` from decomposeParDict
        (it partitions itself) and needs the processor<i> directories to
        exist. Leaves the processor meshes in place for baffles and solving.
        """
        generate_decompose_par_dict(case_dir, n_procs)
        self._clean_processor_dirs(case_dir)
        for i in range(n_procs):
            (case_dir / f"processor{i}").mkdir(parents=True, exist_ok=True)
        try:
            self._run_cmd(f"mpirun -np {n_procs} {mesher} -overwrite{budget} -parallel", case_dir)
        except RuntimeError as exc:
            logger.warning(f"dcCartMesh -parallel: {exc}")
            self._clean_processor_dirs(case_dir)
            return False
        missing = [
            i for i in range(n_procs)
            if not (case_dir / f"processor{i}" / "constant" / "polyMesh" / "faces").exists()
        ]
        if missing:
            logger.warning(f"dcCartMesh -parallel: ranks {missing} wrote no mesh")
            self._clean_processor_dirs(case_dir)
            return False
        return True

    def _write_dcCart_mesh_dict(self, system_dir: Path) -> None:
        """system/dcCartMeshDict: the background cell planes, nothing else.

        Geometry and refinement are NOT repeated here — dcCartMesh reads them
        from snappyHexMeshDict when this dict has no ``geometry`` block, which
        keeps one source of truth for patch names and levels. The planes are
        the ones ``_generate_block_mesh_dict`` cached (every cell face of the
        multi-block grid), so a ``structured`` run gets its breakpoints on the
        object faces exactly as the snappy path does.
        """
        planes = getattr(self, "_bg_grid_planes", None)
        if not planes:
            raise RuntimeError(
                "dcCartMeshDict needs the background grid planes; "
                "_generate_block_mesh_dict must run first"
            )
        mc = self._data.get("meshConfig", {}) or {}
        n_buffer = 1
        # Mirrors the snappyHexMeshDict line below (`maxGlobalCells` with the
        # same 20M fallback) so the two engines honour one setting. Base604
        # asks for 2,000,000 and dcCart used to hand back 3,550,304.
        max_global_cells = _dcCart_max_global_cells(mc)
        # Near-coincidence weld (dcCartMesh.C ``weldTolerance``, metres): fuse
        # surface vertices that sit within this distance of a grid plane / of
        # each other BEFORE the cut, so a slightly tilted or offset junction
        # between two solids no longer carves a wall wedge thinner than the
        # wall-function floor. Measured on DAML-Testbed (2026-09-21): OFF, the
        # steady solve's k collapses to 1e-7 by step 20 and the cumulative
        # continuity error grows past 7; at 1.2e-3 m (17 planes, 274 vertices
        # moved) every field decays 2-3 orders and the error stays at 3e-4 --
        # on both DAML project variants. Tri_angle (nothing to weld), Slab
        # (26 vertices) and Shinaide (146 vertices; wall-distance floor
        # 3.3e-4 -> 1.2e-3 m) keep every coverage-gate decision. The manifest's
        # meshConfig.weldTolerance overrides the default; 0 disables; the
        # environment (DCCART_WELD_TOL, DCCART_WELD=0) overrides the dict.
        weld_tolerance = 1.2e-3
        raw_weld = mc.get("weldTolerance")
        if raw_weld is not None and raw_weld != "":
            try:
                weld_tolerance = max(0.0, float(raw_weld))
            except (TypeError, ValueError):
                logger.warning(f"meshConfig.weldTolerance={raw_weld!r} is not a number; using {weld_tolerance}")
        # First-layer floor on top of the weld (see the module constants):
        # meshConfig.firstLayerMinFraction / firstLayerMax / firstLayerSealGap.
        first_layer_fraction = _dcCart_float_key(
            mc, "firstLayerMinFraction", DCCART_FIRST_LAYER_MIN_FRACTION_DEFAULT, lo=0.0
        )
        first_layer_max = _dcCart_float_key(mc, "firstLayerMax", DCCART_FIRST_LAYER_MAX_DEFAULT, lo=0.0)
        first_layer_seal_gap = _dcCart_float_key(mc, "firstLayerSealGap", DCCART_FIRST_LAYER_SEAL_GAP_DEFAULT)

        def fmt(vals: List[float]) -> str:
            # Full precision: these planes must coincide EXACTLY with the STL
            # faces they were aligned to, or the cut-cell pass sees a paper-
            # thin sliver between grid and surface on every rack and wall.
            return " ".join(repr(float(v)) for v in vals)

        # Zones this runner rewrites from cyclic to wall once the mesh exists
        # (see _generate_create_baffles_dict). With splitBaffles the mesher
        # writes the pairs itself, so it has to know which ones are walls: a
        # wall pair has nothing to couple, and each side of one that a rank
        # boundary separates is simply an independent wall on its own rank --
        # not a processorCyclic, which the later rewrite to type wall would
        # leave referring to a wall patch ("Attempt to cast type wall to type
        # coupled" the moment the solver builds the mesh).
        # Must be the SAME set _ensure_wall_baffles_are_wall_type rewrites from
        # cyclic to wall after meshing -- the bridge registry's wall_baffle
        # patches -- or the mesher seals a pair the post-pass leaves open (or
        # vice versa) and the flow topology changes: DAML's containment
        # baffles (merged_wall_baffle) are cyclic in the bridge registry, and
        # sealing them stalled the steady solve at a ~0.4 residual plateau.
        # _wall_baffle_zone_names is broader (it drives createBafflesDict), so
        # it is only the fallback when there is no registry.
        wall_pair_zones: List[str] = []
        if self._bridge_registry is not None:
            wall_pair_zones = sorted({
                re.sub(r"_(master|slave)$", "", name)
                for name, spec in self._bridge_registry.items()
                if spec.bc_type == "wall_baffle"
            })
        if not wall_pair_zones:
            wall_pair_zones = sorted(getattr(self, "_wall_baffle_zone_names", None) or ())
        if os.environ.get("DCCART_NO_WALL_PAIR_ZONES"):
            wall_pair_zones = []
        wall_pair_block = (
            "// Baffle zones that are walls, not flow-through pairs (see above).\n"
            "wallPairZones       (" + " ".join(wall_pair_zones) + ");\n"
            if wall_pair_zones else ""
        )

        # This authority belongs to the current locked raw manifest and actual
        # generated region tables, never a sticky launcher environment flag.
        from backend.aircooling.runner.point_repair_source_contract import foam_contract
        from backend.aircooling.runner.weight_union_source_contract import (
            per_run_contracts, foam_weight_contract, invalidate,
        )
        try:
            source_raw = self._read_cfdrun_bytes()
        except OSError:
            source_raw = b""  # Old writer-only callers have no manifest.
        source_sha = hashlib.sha256(source_raw).hexdigest()
        snappy_path = system_dir / "snappyHexMeshDict"
        try:
            snappy_bytes = snappy_path.read_bytes()
        except OSError:
            snappy_bytes = b""  # Missing generated input disables only singleton.
        source_contracts = per_run_contracts(
            source_raw,
            locked_raw_sha256=getattr(self, "_execution_cfdrun_sha256", None),
            derived_data=self._data,
            resolved_run=getattr(self, "_resolved", None),
            tri_surface_dir=self.tri_surface_dir,
            snappy_bytes=snappy_bytes,
            wall_pair_zones=set(wall_pair_zones),
            verified_original_receipt=getattr(
                self, "_point_repair_source_receipt", None,
            ),
        )
        # Preserve the existing raw identity guard; never render stale authority.
        try:
            source_raw_after = self._read_cfdrun_bytes()
        except OSError:
            source_raw_after = b""
        if source_raw and hashlib.sha256(source_raw_after).hexdigest() != source_sha:
            raise RuntimeError("source manifest changed during role contract")
        if not source_raw:
            invalidate(source_contracts, "missingRawManifest")
        try:
            snappy_unchanged = snappy_path.read_bytes() == snappy_bytes
        except OSError:
            snappy_unchanged = False
        if not snappy_unchanged:
            invalidate(source_contracts, "generatedDictionaryIdentityChanged")
        source_contract_block = foam_contract(source_contracts["singleton"])
        weight_contract_block = foam_weight_contract(source_contracts["weight"])

        text = f"""FoamFile
{{
    version     2.0;
    format      ascii;
    class       dictionary;
    object      dcCartMeshDict;
}}

// Written by STLGenRunner (meshEngine: dcCart). Geometry, refinementSurfaces,
// refinementRegions and locationsInMesh come from snappyHexMeshDict.
nCellsBetweenLevels {n_buffer};
timeLimitSeconds    {_mesh_time_limit_seconds(mc, DEFAULT_MESH_TIME_LIMIT_S):g};
// Write every flow-through baffle's <zone>_master/_slave pair in the mesher
// itself (and a processorCyclic patch for each pair a rank boundary
// separates). That is all redistributePar and createBaffles existed to do, so
// both passes are skipped below -- 29 s of the Base604 mesh pipeline.
splitBaffles        {"false" if os.environ.get("DCCART_NO_SPLIT_BAFFLES") else "true"};
// The same ceiling the snappy path writes into snappyHexMeshDict, and the same
// fallback. dcCartMesh stops refining once its octree reaches it, exactly where
// meshRefinement::refineCandidates stops in snappyHexMesh.
maxGlobalCells      {max_global_cells};
// Near-coincidence weld of the surfaces before the cut (see the runner comment
// above): 0 turns it off. DCCART_WELD_TOL / DCCART_WELD=0 in the environment win.
weldTolerance       {weld_tolerance:g};
// First-layer floor on top of the weld (dcCartSurfaces::weldNearCoincident):
// per surface class the band widens to firstLayerMinFraction x that class's
// refinement-level spacing, capped at firstLayerMax m, so no axis-aligned
// surface carves a slab thinner than that. 0 = classic weld only. A gap
// between two solids wider than firstLayerSealGap (< 0: weldTolerance) is
// never sealed. DCCART_FIRSTLAYER_FRACTION / DCCART_FIRSTLAYER_MAX win.
firstLayerMinFraction {first_layer_fraction:g};
firstLayerMax       {first_layer_max:g};
firstLayerSealGap   {first_layer_seal_gap:g};
{wall_pair_block}
{source_contract_block}
{weight_contract_block}
background
{{
    x ({fmt(planes["x"])});
    y ({fmt(planes["y"])});
    z ({fmt(planes["z"])});
}}
"""
        (system_dir / "dcCartMeshDict").write_text(text, encoding="utf-8")
        logger.info(
            "dcCartMeshDict: background planes "
            f"x={len(planes['x'])} y={len(planes['y'])} z={len(planes['z'])}"
        )

    #: third_parties/dcCartMesh build output: snappyHexMeshDC and dcCartMesh
    _CUSTOM_MESHER_DIR = Path(__file__).resolve().parents[1] / "third_parties" / "dcCartMesh" / "platforms"

    @classmethod
    def _resolve_mesher_binary(cls, name: str) -> str:
        """Absolute path of a repo-built mesher, or the bare name from PATH.

        Same rule as ``_resolve_solver_binary``: the copy this repo builds
        (third_parties/dcCartMesh/deploy.sh) wins over anything on PATH.
        """
        if cls._CUSTOM_MESHER_DIR.is_dir():
            for platform_dir in sorted(cls._CUSTOM_MESHER_DIR.iterdir()):
                candidate = platform_dir / "bin" / name
                if candidate.is_file() and os.access(candidate, os.X_OK):
                    logger.info(f"Using repo-built mesher: {candidate}")
                    return str(candidate)
        logger.warning(
            f"{name} is not built in {cls._CUSTOM_MESHER_DIR}; falling back to PATH. "
            f"Build it with third_parties/dcCartMesh/deploy.sh"
        )
        return name

    # ------------------------------------------------------------------
    # meshEngine: meshgen — the vendored MeshGenerator cut-cell engine
    # ------------------------------------------------------------------

    #: future vendor location (third_parties/meshgenerator/deploy.sh output),
    #: same non-destructive platforms/<tag>/bin/ convention as dcCartMesh/foam.
    _MESHGEN_VENDOR_DIR = (
        Path(__file__).resolve().parents[1] / "third_parties" / "meshgenerator" / "platforms"
    )
    #: dev fallback: the sibling checkout the dc17 campaign measures against
    #: (bench/mg --mg-root Ref_code/MeshGenerator).
    _MESHGEN_DEV_BIN = (
        Path(__file__).resolve().parents[4]
        / "Ref_code" / "MeshGenerator" / "build" / "cli" / "meshgen-cli"
    )

    #: Assembly contract shared with the dc17 campaign driver
    #: (bench/mg/mg_mesh_case.py header): keep ONLY these polyMesh files,
    #: never let MG's AMR extras reach a DC case tree.
    _MESHGEN_POLYMESH_KEEP = (
        "points", "faces", "owner", "neighbour", "boundary",
        "cellZones", "faceZones", "pointZones",
    )
    _MESHGEN_POLYMESH_REQUIRED = ("points", "faces", "owner", "neighbour", "boundary")
    _MESHGEN_AMR_EXTRAS = ("cellLevel", "pointLevel", "level0Edge", "refinementHistory")
    #: DEGENERATE floor (bench/mg): rc=0 with fewer fluid cells than this is a
    #: refusal dressed as success (dc-mode converts the S7 watertight refusal
    #: into a report and still exports a 1-cell mesh). Smallest legitimate
    #: fleet mesh is SingleRack (~17k cells), so 1000 is conservative.
    _MESHGEN_MIN_CELLS = 1000
    #: production posture (MG docs 31_dc17_mpi_hardening.md): single process,
    #: taskset + MESHGEN_THREADS double-capped; the host CPU reaper SIGTERMs
    #: at >=3500%, so the runner cap is 16 threads (1600%) and the default 8.
    _MESHGEN_DEFAULT_THREADS = 8
    _MESHGEN_MAX_THREADS = 16

    @classmethod
    def _resolve_meshgen_binary(cls) -> str:
        """Absolute path of the MeshGenerator CLI, md5-pinned per run.

        Resolution order (first hit wins):
          1. ``DCDTUNI_MESHGEN_BIN`` — explicit pin (the dc17 full-flow harness
             uses it to freeze one binary across an in-flight engine rebuild);
          2. ``third_parties/meshgenerator/platforms/*/bin/meshgen-cli`` —
             the future vendored build;
          3. ``Ref_code/MeshGenerator/build/cli/meshgen-cli`` — the dev
             checkout fallback.

        Unlike the OpenFOAM tools there is NO bare-name PATH fallback: a
        manifest that says ``meshEngine: meshgen`` gets the MeshGenerator or a
        loud failure, never a silently different mesher.
        """
        import hashlib

        env_pin = os.environ.get("DCDTUNI_MESHGEN_BIN", "").strip()
        candidates: List[Tuple[str, Path]] = []
        if env_pin:
            candidates.append(("env DCDTUNI_MESHGEN_BIN", Path(env_pin)))
        if cls._MESHGEN_VENDOR_DIR.is_dir():
            for platform_dir in sorted(cls._MESHGEN_VENDOR_DIR.iterdir()):
                candidates.append(
                    ("vendor build", platform_dir / "bin" / "meshgen-cli"))
        candidates.append(("dev checkout", cls._MESHGEN_DEV_BIN))

        for source, path in candidates:
            if path.is_file() and os.access(path, os.X_OK):
                md5 = hashlib.md5(path.read_bytes()).hexdigest()
                logger.info(
                    f"Using meshgen-cli ({source}): {path}  md5={md5}  "
                    f"mtime={datetime.datetime.fromtimestamp(path.stat().st_mtime)}"
                )
                return str(path)
        raise RuntimeError(
            "meshEngine=meshgen but no meshgen-cli binary was found. Looked at: "
            + "; ".join(f"{s}: {p}" for s, p in candidates)
            + ". Set DCDTUNI_MESHGEN_BIN, build the vendored copy "
              "(third_parties/meshgenerator), or build Ref_code/MeshGenerator."
        )

    def _meshgen_thread_prefix(self) -> str:
        """`MESHGEN_THREADS=N CART_*=1 taskset -c 0-(N-1)` production posture prefix.

        Also carries the sanctioned dc17 engine knobs (CART_CARVED_MERGE1=1
        CART_S9D_RESCUE=1 = the ON+S9D state every dc17 battery was greened
        under); DCDTUNI_MESHGEN_KNOBS=0 disables them for A/B comparisons.

        Double cap per MG docs 31_dc17_mpi_hardening.md: MESHGEN_THREADS only
        binds an MPI build's pool, taskset binds every build — and the host
        CPU reaper SIGTERMs any process group at >=3500%. Override the cap
        with DCDTUNI_MESHGEN_THREADS (1..16); set DCDTUNI_MESHGEN_TASKSET=0
        to run uncapped (not recommended on the shared host).
        """
        mc = (getattr(self, "_data", None) or {}).get("meshConfig", {}) or {}
        raw = os.environ.get("DCDTUNI_MESHGEN_THREADS") or mc.get("meshgenThreads")
        try:
            threads = int(raw) if raw else self._MESHGEN_DEFAULT_THREADS
        except (TypeError, ValueError):
            threads = self._MESHGEN_DEFAULT_THREADS
        threads = max(1, min(threads, self._MESHGEN_MAX_THREADS))
        prefix = f"env MESHGEN_THREADS={threads} "
        knobs_on = os.environ.get("DCDTUNI_MESHGEN_KNOBS", "1") != "0"
        if knobs_on:
            # Sanctioned dc17 production state: S9c bounded carved-merge + S9d
            # rescueSplit (MG docs 31_dc17_signoff_kb2_consult2/3.md; every dc17
            # battery — BL rc=0 announcement, dcmode_bm ctest leg — runs ON+S9D).
            # DCDTUNI_MESHGEN_KNOBS=0 restores engine defaults for A/B runs.
            #
            # CART_DUAL_SNAP=1 (dc17-align, 2026-09-13, measured on MG
            # d13481f825f8cbc130dc13f47a43b97a): snaps near-coincident surface
            # coordinates before cell cutting. This is the single largest measured
            # lever on the S10 "open cell" (Sigma-Sf != 0) refusal class --
            #   Slab     kind1 17 -> 0   (S10 backlog: survived=3  kind1=0 s10Pass=3)
            #   Base604  kind1 12 -> 0   (S10 backlog: survived=86 kind1=0 s10Pass=86)
            #   Tri      kind1 112 -> 38
            #   Shinaide kind1 3850 -> 198 (aligned cfdproj; shells 287 -> 77)
            # and Base_leakage-Mgen-aligned stays rc=0 and still exports. It costs
            # mesh bytes (points/faces/owner/neighbour/boundary/pointLevel differ;
            # cellLevel/level0Edge/refinementHistory are identical), so any frozen
            # meshgen regression anchor must be re-recorded once.
            # Scope: meshgen ONLY. This prefix is consumed solely by
            # _run_meshgen_pipeline(); _run_dcCart_mesh_pipeline() builds no env,
            # and dcCart's own tree (third_parties/dcCartMesh) has zero CART_ hits.
            # CART_S9D_EXH_BNDSKEW=1 (dc17-priority, 2026-09-13, measured on MG
            # 66389b4886abf7bbc171e3f80785e2d3): treat ANY S9d piece-skew rejection
            # as K2S(a) exhaustion evidence, so a cell whose split pieces are all
            # skew-rejected can fall through to the existing whole-cell kill-to-solid
            # path instead of dead-ending at the S10 face-cone centroid gate.
            # This is what takes Slab from rc=1 to rc=0 (3 residue cells, all
            # "wrapping two orthogonal thin sheets" at the containment junction;
            # S9d had already tried the quadrant split -- planes {x=2.07, y=2.402,
            # ...} into 7 pieces -- and every leaf failed skew 4.21764/4.09438 >
            # kS9dSkewMax=3.85, which also exceeds checkMesh's own 4.0 limit, so
            # the pieces were genuinely unexportable).
            # Measured, all with the knob ON:
            #   Slab      rc=1 -> rc=0   wholeKills=3/115507   = 0.00260%
            #   Base604   wholeKills=100/1264632 = 0.00791% (still rc=1, see below)
            #   Shinaide  wholeKills=9/242546    = 0.00371% (still rc=1: kind1)
            #   BL-aligned rc=0 -> rc=0, polyMesh 9/9 files BYTE-IDENTICAL
            #   (BL already whole-killed the very same ci=84536 without the knob)
            # Every case is below the campaign's R2 population trigger (0.01% of
            # cells / 1e-5 relative volume). No gate tolerance is touched: this
            # only widens which rejections count as "no option left".
            # CART_DUAL_SNAP=1 restored (dc17-priority, 2026-09-13).
            #
            # It was briefly made opt-in on the strength of a measurement that
            # turned out to be an ARTIFACT: two Slab runs that differed only by
            # CART_S9D_EXH_BNDSKEW appeared to give kind1=0 vs kind1=4314, which
            # looked like a snap x kill interaction. Re-running on a FROZEN input
            # snapshot (triSurface md5 4b1bca3d903415b4667e9cadec4f18d9) shows no
            # such interaction -- Slab is rc=0 with the kill knob under BOTH snap
            # settings, and [SNAP2] reports "dual-merged 0 vertex" either way.
            # The 4314 came from a different input epoch: the concurrent session
            # re-exported merged_wall_baffle.stl, wallBaffle_floor_Raising_floor_part1.stl
            # and perforated_floor_Raising_floor_patch.stl at 18:33:16, between the
            # two runs (solids onDisk 84 -> 83, R1_frame 267 -> 238 triangles).
            # Same story for a reported "Base604 kind1 0 -> 16046": on a frozen
            # snapshot Base604 measures kind1=0. Reading a triSurface directory
            # while another process is exporting it yields inconsistent input.
            #
            # Frozen-epoch matrix (MG 66389b4886abf7bbc171e3f80785e2d3), all runs
            # verified drift-free by re-hashing the input before and after:
            #   Slab      kill ON -> rc=0 with snap ON *and* snap OFF (kind1=0, 3 whole-kills)
            #   Base604   kill ON + snap ON -> kind1=0 (vs 8 with snap OFF)
            #   Tri_angle snap ON -> kind1=38 (vs 112 with snap OFF)
            #   Shinaide  snap ON -> kind1=7913 (vs 7860 snap OFF; a wash)
            # => snap ON is neutral-to-better on every case, so it stays on.
            # CART_DUPFACE_MERGE=1 (dc17-priority DUPF, 2026-09-13, measured on MG
            # binary c60b3e2deffacb9e9d0975d2773b0ea5, frozen inputs, every run
            # re-hashed before and after so drift=ok):
            #   Shinaide  kind1 7913 -> 172 (dup1 8080 groups / dup2 5628 / dup3 53;
            #             cured 7741, residual 7); leading refusal moves off the
            #             closure assertion onto the centroid gate.
            #   Base604   kind1 -> 0; leading refusal is the centroid gate.
            #   Tri_angle kind1 38 unchanged (its 3 dup1 groups are opposite-normal
            #             pairs, i.e. consult-6's refuted alpha1, so DUPF skips them).
            #   Slab      rc=0 with 9 polyMesh files, DUPF firing set EMPTY.
            #   Base_leakage rc=0 with 9 polyMesh files, DUPF firing set EMPTY
            #             (dup1Groups=0, skipOpposite=0) => no baffle master/slave
            #             parity re-base is needed this epoch.
            # The pass only ever DELETES a spurious face from one cell's own face
            # list, never moves a gate: DUPF-1 removes faces whose collinear-cleaned
            # point-id sets are identical and whose normals agree; DUPF-2 removes a
            # same-plane same-normal boundary face whose every grid sub-rectangle is
            # also covered by another such face; DUPF-3 removes a face that violates
            # the edge-closure invariant "the box of the other faces must contain all
            # of this face's vertices". consult-6's closure-neutrality law is enforced
            # at the landing gate: if |SigmaSf| is still above tolClose afterwards,
            # nothing is applied and S10 refuses aloud.
            # OFF redline re-verified on the same binary: basecheck TOTAL_6CASE =
            # 3d8c0a4a6cc8b7758b181a98cf007db0, cmpbase exh2-vs-dupf1 6/6 SAME,
            # tricheck PASS.
            #
            # NOT added: CART_S9D_ALLOW_NEWPTS (measured marginal -- it removes the
            # whole `newpts` S9d refusal class, which is 55 of Tri's 58 kind3 cells,
            # but the refusals then move to `half-admit`/`nonmanifold-seam` and Tri's
            # S9d cures go 1 -> 2 while Base604's stay at 76; kept as an ablation key).
            #
            # CART_S9D_PLAN_RETRY=1 (dc17-priority G2, 2026-09-13, measured on MG
            # binary 4b0eca0992f903f8e88858ef44f91160): S9d used to take the FIRST
            # split plan that passed the pool-level gates, and a landing-stage
            # rejection (piece_skew / piece_sliver / xface_unpaired / ...) threw the
            # whole cell away without trying another plane. So `piece_skew` proved
            # only "one plan was skew-bad", never "every plan was" -- which also left
            # the E5h.1 whole-cell-kill exhaustion argument incomplete for that reason
            # code. The retry excludes the failed plan's depth-0 plane and re-searches;
            # it is naturally bounded (each round removes at least one candidate plane)
            # so it adds no constant, and the whole-cell-kill path is never retried, so
            # the E5h.1 Slab cure is bit-preserved. Measured: S9d cures Base604
            # 76 -> 80 (130 retries), Shinaide 9 -> 10 (6 retries), Tri 1 -> 1 (0
            # retries -- Tri's refusals are plan-level `newpts`, not landing-level);
            # Slab and Base_leakage polyMesh aggregates byte-identical to knob-OFF
            # (540d75a77f4c4fcb / f58d1f5b8f94f2d0) and both still rc=0.
            # CART_PTWELD_MERGE=1 (dc17-priority family A, 2026-09-14, measured on MG
            # binary a2520ba4d4e73f13b51c20da76a3c429, frozen inputs, drift=ok).
            # Defect: two faces of one cell sharing an edge can reference two DIFFERENT
            # global point ids whose coordinates differ by ~1e-7, because the cut
            # machinery computed the same intersection twice from different triangle
            # pairs. Tri cell 458 reproduces it exactly: f13's legs use 3.44506719 /
            # 4.89550867 while f12's use 3.44506647 / 4.89550787, and the area
            # difference 1.9e-8 equals the measured SigmaSf to every digit. Neither
            # coordinate occurs anywhere in the input STL, so the surface-level S0.5
            # snap structurally cannot reach them. The weld only repoints ids to the
            # lowest-index member of a cluster; it never moves a coordinate.
            # Scale registration (kPtWeldTol = 1e-6 m, see the constant's comment):
            # every vertex of the frozen Tri/Slab/Base604 triSurface carries exactly 6
            # decimals (1872 / 4662 / 152073 values, no exception), and Shinaide's few
            # long values are full-double prints of 6-decimal numbers such as
            # 0.044500000000000005 and 2.002500000000001, so 1e-6 m is the input's own
            # coordinate quantum and two derived coordinates closer than that are
            # indistinguishable in it. Measured firing set at that scale: Tri 549
            # pairs / Shinaide 70 / Base604 ~35 clusters, and **Slab 0 / Base_leakage
            # 0**, whose smallest non-zero point distances are 9.46821036e-06 and
            # 9.0625e-06 -- a 9x margin, not a tuned edge. Cluster admission requires
            # every member to be within kPtWeldTol of its representative (measured
            # maxShift 9.99988215e-07), a cluster that would collapse a face ring
            # below 3 distinct points is reverted whole and counted, and the pass runs
            # before S9d on purpose: Tri's split halves were measured at
            # cl=2.78819e-08 against tolClose=6.87407e-09 while the parent pool closes
            # to 1.71216e-19, i.e. the 1e-7 duals enter the seam grid there.
            # Result: Tri kind1 38 -> 7. Slab and Base_leakage polyMesh aggregates stay
            # byte-identical to knob-OFF (540d75a77f4c4fcb / f58d1f5b8f94f2d0), both
            # still rc=0. Honest ledger: Tri's kind3 census goes 58 -> 80 because
            # welding restores conformity in cells that were already centroid-violating,
            # so their latent kind3 becomes visible; total blocking population 96 -> 87.
            # CART_S9D_SEAM_OFFGRID=1 (dc17-priority two-phase seam search, 2026-09-14,
            # measured on MG binary dca2f886326e90ea7ca74ab3f8517434).
            # Root cause this fixes, located in code: xvSplitFaces builds the seam ring on a
            # grid whose lines coords1/coords2 come only from the INPUT shell's vertices and
            # whose diagEdges come only from planePolys (faces lying in the split plane). A
            # point interpolated where the plane crosses a DIAGONAL edge is in neither, so the
            # seam ring cannot coincide with the clipped faces' boundary and both halves lose
            # closure -- measured cl=8.4e-07..1.6e-04 against tolClose=6.87407e-09, which is
            # why `nNew==0` is a structural precondition rather than a conservative filter.
            # The key augments the grid with the clipped halves' in-plane vertices and their
            # in-plane diagonal edges. That augmentation is provably inert when nNew==0 (an
            # interpolated point on an axis-aligned edge takes both in-plane coordinates from
            # that edge's endpoints, so it is already in the shell's coordinate set and
            # dedupCoords absorbs it), and Base_leakage confirms it: its four S9d counts read
            # 49/48/1/0 identically either way.
            # It is gated as a TWO-PHASE search, not a global relaxation, because the global
            # form was measured to regress Slab from rc=0 to rc=1: allowing nNew>0 everywhere
            # lets Slab's third residue cell travel deeper (coalesced 4 -> 9), end at
            # piece_skew instead of reaching the E5h.1 whole-cell-kill authorization, and
            # survive to S10 as a kind3 violator. Phase 0 is today's strict discipline
            # including the full kill-authorization path; phase 1 re-searches with the
            # augmented grid only if phase 0 neither landed nor marked the cell for kill.
            # Measured with the two-phase form: Slab and Base_leakage polyMesh aggregates
            # byte-identical to key-OFF (540d75a77f4c4fcb / f58d1f5b8f94f2d0) with
            # phase2Tries=0 on both, i.e. structurally untouched; Shinaide and Base604
            # dc_report, kind1 and leading refusal all identical; Tri phase2Tries=80,
            # phase2Cures=8, S9d cures 2 -> 10 and complete kind3 census 80 -> 69.
            # Net: strictly positive with zero regression, so it ships.
            # CART_S9D_RECT_MERGE=1 (dc17-priority, 2026-09-14, measured on MG binary
            # 2a8c96f93fb36abcf9d6ab42e5f599a6). Coplanar-adjacent-boundary-face rectangle
            # merge, tried ONLY inside the S9d green branch after the full-face skew
            # pre-check has already failed. Motivation, measured per face: on Shinaide's
            # leading kind3 cell the split box passes foamOut, closure, volume and the
            # pyramid centroid, and exactly two of its ten qualifying faces exceed
            # kS9dSkewMax=3.85 -- both narrow strips (5.87065 over
            # x[4.03612109,4.07388281] y[1.10700391,1.1125], and 4.29189 over
            # x[4.070737,4.07388281] y[1.1125,1.14476563]) -- each having a coplanar,
            # same-normal, adjacent sibling (5.88015e-14 and 0.140108) whose union with it
            # is exactly a full grid rectangle. Merging puts the face centre back on the
            # centreline. kS9dSkewMax=3.85 and the skew formula are untouched: the mesh
            # changes, the gate does not. Seam faces (patch==-2) are structurally excluded
            # because merging them would break the S8-A1c +- copy key identity, and every
            # merge is verified closure-neutral before it is applied (|Sf(merged) -
            # (SfA+SfB)| <= tolClose), which on its first flight caught this seat's own
            # off-by-one in the union's corner indices and stopped all 104 bad edits from
            # reaching the mesh.
            # It ships in TWO-PHASE form for the same reason CART_S9D_SEAM_OFFGRID does.
            # Bundled (applied in every phase) it was measured to move Slab off rc=0: the
            # merge lets a plan clear the pool gate, the plan then dies at landing as
            # xface_unpaired, and because xface_unpaired carries no E5h.1 kill authorization
            # the cell survives to S10 instead of being killed. Under two-phase, Slab's
            # residue cells are resolved by the kill path inside phase 0 and can never reach
            # phase 1. Measured: Slab and Base_leakage report phase2Tries=0 AND
            # rectMerge poolsTried=0, with polyMesh aggregates byte-identical to key-OFF
            # (540d75a77f4c4fcb / f58d1f5b8f94f2d0) and both still rc=0. Where it pays:
            # Shinaide's leading kind3 cell 31941 is CURED (its leading refusal moves off the
            # centroid gate onto the family-B closure gate at 43898) and its complete kind3
            # census goes 14 -> 12; Base604's goes 180 -> 170; Tri rescues 785 pools but its
            # census stays 69, recorded as no gain rather than claimed as one.
            prefix += ("CART_CARVED_MERGE1=1 CART_S9D_RESCUE=1 CART_DUAL_SNAP=1 "
                       "CART_S9D_EXH_BNDSKEW=1 CART_DUPFACE_MERGE=1 "
                       "CART_S9D_PLAN_RETRY=1 CART_PTWELD_MERGE=1 "
                       "CART_S9D_SEAM_OFFGRID=1 CART_S9D_RECT_MERGE=1 ")
        if os.environ.get("DCDTUNI_MESHGEN_TASKSET", "1") != "0" and shutil.which("taskset"):
            prefix += f"taskset -c 0-{threads - 1} "
        logger.info(
            f"meshgen production posture: single process, MESHGEN_THREADS={threads}"
            + (", knobs ON+S9D" if knobs_on else ", knobs engine-default")
            + (f", taskset -c 0-{threads - 1}" if "taskset" in prefix else ", UNCAPPED (no taskset)")
        )
        return prefix

    def _run_meshgen_pipeline(self) -> None:
        """meshgen-cli cfdrun → assemble → checkMesh: the MeshGenerator engine.

        One pass of the vendored cut-cell Cartesian mesher replaces blockMesh +
        surfaceFeatureExtract + snappyHexMesh + decomposePar-mesh +
        reconstructParMesh + createBaffles ENTIRELY: meshgen-cli reads the
        .cfdrun manifest this runner loaded and the browser-exported STLs in
        Mesh/constant/triSurface, and in --dc-mode it emits the final dc17
        export contract (MG docs 31_dc17_export_log.md) — golden-layout ASCII
        boundary with the baffle ``<zone>_master``/``<zone>_slave`` cyclic
        pairs, cyclic metadata (matchTolerance 0.0001, 16-column alignment),
        zero-face contract patches, and faceZones — so createBaffles must NOT
        run afterwards (it would collide with the emitted pair names, the same
        cast failure _drop_empty_baffle_patches exists to prevent).

        The mesh is written to a scratch dir and then assembled INSIDE Mesh/
        under the bench/mg campaign contract: keep triSurface/system/constant
        props, REPLACE constant/polyMesh with the keep-list (points faces owner
        neighbour boundary + zones when present), STRIP the MG AMR extras
        (cellLevel/pointLevel/level0Edge/refinementHistory + dynamicMeshDict),
        verify boundary is an ASCII dictionary. rc != 0 — or an rc=0 export
        below the degenerate floor — is a mesh failure surfaced with the
        stderr tail and the dc_report.json stage; a refused mesh is never
        shipped. The step runs through _run_cmd, so the mesh-budget watchdog
        (meshConfig.meshTimeLimitSeconds) bounds it like every other engine.
        """
        import shlex

        self._start_mesh_budget()
        self._ensure_mesh_system_files()
        case_dir = self.mesh_dir
        total_steps = 3  # meshgen-cli + assemble + checkMesh
        step = 0

        binary = self._resolve_meshgen_binary()
        # scratch lives UNDER Mesh/ (always gitignored via case/*/Mesh) so a
        # failed run never leaves an untracked dir at a golden case/<name>/ top
        # level; it is removed on success and kept for inspection on refusal.
        scratch = self.mesh_dir / ".meshgen_scratch"
        if scratch.exists():
            shutil.rmtree(scratch, ignore_errors=True)
        out_poly = scratch / "constant" / "polyMesh"
        out_poly.mkdir(parents=True, exist_ok=True)
        report = scratch / "dc_report.json"

        # Editor-only export buckets (is_editor_only_stl) do not count as
        # "STLs present" — meshgen would have nothing to mesh without them.
        if not self.tri_surface_dir.is_dir() or not any(
            not is_editor_only_stl(p.name) for p in self.tri_surface_dir.glob("*.stl")
        ):
            raise RuntimeError(
                f"meshgen needs the browser-exported STLs in {self.tri_surface_dir}; "
                "no *.stl found. Re-export from the frontend (Export STL) first."
            )

        step += 1
        logger.info(f"Step {step}/{total_steps}: meshgen-cli cfdrun (dc-mode, raw geometry, seeds pre)")
        try:
            with self._cfdrun_command_path() as native_input:
                cmd = (
                    self._meshgen_thread_prefix()
                    + f"{shlex.quote(binary)} cfdrun {shlex.quote(str(native_input))}"
                    + f" --surface-dir {shlex.quote(str(self.tri_surface_dir))}"
                    + " --dc-mode --dc-raw --dc-seeds pre"
                    + f" --export-foam {shlex.quote(str(out_poly))}"
                    + f" --report {shlex.quote(str(report))}"
                )
                self._run_cmd(cmd, scratch)
        except MeshTimeoutError:
            self._log_meshgen_report(report)
            raise
        except RuntimeError as exc:
            # _run_cmd already streamed the stderr tail into the job log; add
            # the engine's own verdict so the failure is legible at a glance.
            self._log_meshgen_report(report)
            raise RuntimeError(
                f"meshgen-cli refused the mesh ({exc}). dc_report: {report}; "
                f"scratch output kept at {scratch} for inspection."
            ) from exc

        step += 1
        logger.info(f"Step {step}/{total_steps}: assemble Mesh/ from meshgen output (dc17 contract)")
        self._assemble_meshgen_polymesh(case_dir, out_poly, scratch)

        # An open room shell still exports; say so while the patch names are final.
        self._warn_open_room_enclosure(case_dir)

        step += 1
        logger.info(f"Step {step}/{total_steps}: checkMesh (final quality check)")
        # MG output is cut-cell orthogonal by construction — follow the dcCart
        # branch's cleanup choice (no -allGeometry pass) even though
        # _resolve_mesh_mode only classifies dcCart as cartesian.
        self._remove_disconnected_cells(case_dir, snap_off=True)

        shutil.rmtree(scratch, ignore_errors=True)
        logger.info(f"meshgen scratch {scratch} cleaned up")

    def _log_meshgen_report(self, report: Path) -> None:
        """Surface the dc_report.json verdict (stage/mode/problem shells) in the job log."""
        if not report.is_file():
            logger.error("meshgen wrote no dc_report.json — the run died before the dc-mode report stage")
            return
        try:
            data = json.loads(report.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.error(f"dc_report.json is unreadable: {exc}")
            return
        stage = data.get("stage")
        mode = data.get("mode")
        problems = data.get("problemShells") or []
        logger.error(
            "dc_report.json: stage={stage} mode={mode} shells={shells} "
            "openEdgeShells={open} nonManifoldShells={nm} problemShells={np}"
            .format(stage=stage, mode=mode, shells=data.get("shells"),
                    open=data.get("openEdgeShells"), nm=data.get("nonManifoldShells"),
                    np=len(problems))
        )
        for shell in problems[:5]:
            logger.error(f"  problem shell: {json.dumps(shell, ensure_ascii=False)}")
        logger.error(f"  full report: {json.dumps(data, ensure_ascii=False)[:2000]}")

    def _assemble_meshgen_polymesh(self, case_dir: Path, out_poly: Path, scratch: Path) -> None:
        """REPLACE Mesh/constant/polyMesh with the MG scratch export (keep-list).

        bench/mg assembly contract, applied inside Mesh/: the surrounding tree
        (0/, system/, constant/triSurface, constant props) is the runner's own
        and stays; ONLY polyMesh is swapped, keeping points/faces/owner/
        neighbour/boundary (+ cellZones/faceZones/pointZones when MG wrote
        them) and STRIPPING the AMR extras (cellLevel/pointLevel/level0Edge/
        refinementHistory) plus constant/dynamicMeshDict — the DC solvers are
        static-mesh and a stray level file desynchronises any later subsetMesh
        (decomposePar FATAL). boundary MUST be an ASCII dictionary: the runner
        and the DC tooling regex-edit it in place, so a binary body would be
        corrupted silently.
        """
        missing = [n for n in self._MESHGEN_POLYMESH_REQUIRED
                   if not (out_poly / n).is_file() or (out_poly / n).stat().st_size == 0]
        if missing:
            self._log_meshgen_report(scratch / "dc_report.json")
            raise RuntimeError(
                f"meshgen exported no usable polyMesh (missing/empty: {missing}) — "
                "the mesh is refused, nothing was assembled into Mesh/"
            )

        # Degenerate-output floor: rc=0 is not proof of a mesh (dc-mode reports
        # refusals instead of failing; see bench/mg DEGENERATE class). MG's
        # owner header carries no nCells note (plain labelList), so count via
        # the same topology scanner the cleanup stage uses — it derives nCells
        # from the note or from max(owner, neighbour) + 1.
        from backend.aircooling.runner.polymesh_io import mesh_topology_summary

        summary = mesh_topology_summary(out_poly)
        if summary is not None:
            n_cells = summary["nCells"]
            if n_cells < self._MESHGEN_MIN_CELLS:
                self._log_meshgen_report(scratch / "dc_report.json")
                raise RuntimeError(
                    f"meshgen exported a DEGENERATE mesh ({n_cells} cells < "
                    f"{self._MESHGEN_MIN_CELLS} floor) — refused, nothing assembled"
                )
            logger.info(
                f"meshgen export: {n_cells} cells, {summary['nRegions']} region(s), "
                f"{summary['nIsolatedCells']} isolated cell(s)"
            )
        else:
            logger.warning(
                "meshgen export: topology scan could not read owner/neighbour — "
                "degenerate floor not applied, checkMesh is the gate"
            )

        boundary = out_poly / "boundary"
        # v2412 pushes 'FoamFile' to ~byte 560 and 'format ascii' to ~byte 596
        # in its standard banner (identical in golden snappy / dcCart / MG
        # exports) — the former 512-byte window spuriously refused EVERY
        # conformant ASCII boundary. 4096 covers any banner; 'format binary'
        # is rejected explicitly.
        head = boundary.read_text(encoding="utf-8", errors="replace")[:4096]
        if (
            "FoamFile" not in head
            or re.search(r"format\s+binary", head)
            or not re.search(r"format\s+ascii", head)
        ):
            raise RuntimeError(
                "meshgen wrote constant/polyMesh/boundary in BINARY (or it is not a "
                "FoamFile dictionary) — the DC pipeline regex-edits boundary in place "
                "and would corrupt it silently. Refusing to assemble."
            )

        dst = case_dir / "constant" / "polyMesh"
        if dst.exists():
            shutil.rmtree(dst)
        dst.mkdir(parents=True)
        copied, stripped = [], []
        for entry in sorted(out_poly.iterdir()):
            if entry.name in self._MESHGEN_POLYMESH_KEEP and entry.is_file():
                shutil.copy2(entry, dst / entry.name)
                copied.append(entry.name)
            elif entry.name in self._MESHGEN_AMR_EXTRAS:
                stripped.append(entry.name + " (AMR extra)")
            else:
                stripped.append(entry.name + ("/ (subdir)" if entry.is_dir() else ""))
        dyn = scratch / "constant" / "dynamicMeshDict"
        if dyn.exists():
            stripped.append("constant/dynamicMeshDict (AMR extra)")
        logger.info(
            f"meshgen assembly: replaced {dst} — kept [{', '.join(copied)}]"
            + (f", stripped [{', '.join(stripped)}]" if stripped else "")
        )

    _ORPHAN_REGION_FRACTION = 0.01  # regions < 1% of largest → auto-remove

    def _remove_disconnected_cells(
        self, case_dir: Path, *, snap_off: Optional[bool] = None, n_procs: int = 1,
        cleanup_pass: int = 0,
    ) -> int:
        """Run checkMesh and clean up mesh artifacts.

        Small orphan regions (< 1 % of the largest region) are auto-removed —
        they are snappyHexMesh artifacts that crash the solver (singular
        p_rgh matrix).  Larger secondary regions are kept and surfaced as
        ``disconnected_regions`` meshWarnings (could be legitimate Cold/Hot
        Aisle Containment).

        Isolated cells (zero/one non-boundary face) ARE auto-deleted since
        they are always tiny snappy artifacts that crash the solver.

        ``snap_off`` remains a compatibility argument for engine callers;
        final acceptance always checks extended geometry and topology.
        """
        from backend.aircooling.runner.openfoam_patch_area import require_nonempty_mesh
        require_nonempty_mesh(case_dir, prefer_partitions=n_procs > 1)
        if cleanup_pass >= 8:
            raise RuntimeError("checkMesh cleanup did not converge after 8 passes")
        from backend.aircooling.runner.mesh_quality import (
            parse_checkmesh_regions,
            parse_checkmesh_warnings,
        )

        # Cut-cell grids can contain oblique or concave polyhedra. Apply the
        # same full final quality scope to every engine (geometry/topology ADR).
        geometry_flag = " -allGeometry"

        # The serial scan gives a useful independent orphan/connectivity
        # summary; it never replaces the final distributed OpenFOAM checks.
        from backend.aircooling.runner.polymesh_io import mesh_topology_summary

        # Per-rank owner/neighbour scans would mistake MPI interfaces for
        # disconnected walls. Let OpenFOAM check global connectivity and write
        # distributed cellSets, including processor/cyclic coupling.
        summary = (mesh_topology_summary(case_dir / "constant" / "polyMesh")
                   if n_procs == 1 else None)
        needs_topology = True
        if summary is not None:
            sizes = summary.get("regionSizes") or []
            # Only the cases that actually run subsetMesh need the cellSets:
            # a region below _ORPHAN_REGION_FRACTION of the largest, or cells with
            # <=1 internal face. Several large regions is the normal shape of a
            # raised-floor / containment case and needs no cleanup at all.
            has_orphan_region = bool(
                len(sizes) > 1 and sizes[-1] < sizes[0] * self._ORPHAN_REGION_FRACTION
            )
            needs_topology = has_orphan_region or summary["nIsolatedCells"] > 0
            logger.info(
                f"polyMesh topology scan: {summary['nCells']} cells, "
                f"{summary['nRegions']} region(s) {sizes[:6]}, "
                f"{summary['nIsolatedCells']} cell(s) with <=1 internal face"
                " -> strict checkMesh -allTopology -allGeometry"
            )
            if summary["nRegions"] > 1 and not has_orphan_region:
                logger.warning(
                    f"Mesh has {summary['nRegions']} disconnected regions "
                    f"{sizes[:6]}; every secondary region is >= "
                    f"{self._ORPHAN_REGION_FRACTION:.0%} of the largest, so none is "
                    f"treated as a snappy orphan. Expected for raised-floor plenums "
                    f"and cold/hot aisle containment."
                )

        topology_flag = " -allTopology"
        check_cmd = f"checkMesh{topology_flag}{geometry_flag}"
        if n_procs > 1:
            # Interfaces that do not pair up across ranks make OpenFOAM segfault
            # while constructing the mesh (processorPolyPatch::updateMesh), so
            # checkMesh could only fail without saying why. Name the ranks first.
            from backend.aircooling.runner.processor_patches import assert_processor_patches_paired

            assert_processor_patches_paired(case_dir, n_procs)
            check_cmd = f"mpirun -np {n_procs} {check_cmd} -parallel"
        try:
            output = self._run_cmd_capture(check_cmd, case_dir)
        except Exception as exc:
            if n_procs > 1:
                raise RuntimeError("Partitioned mesh quality check could not complete") from exc
            raise RuntimeError("Mesh quality check could not complete") from exc

        # Stream checkMesh results to the frontend log
        for line in output.splitlines():
            stripped = line.rstrip()
            if stripped:
                logger.info(stripped)
        if n_procs > 1 and (
            "FOAM FATAL" in output
            or not re.search(r"Mesh OK\.|Failed\s+\d+\s+mesh checks", output)
        ):
            raise RuntimeError("Partitioned checkMesh did not produce a complete quality report")

        removed = 0

        def subset(actions, count):
            if n_procs > 1:
                return self._do_subset_mesh(case_dir, actions, count, n_procs=n_procs)
            return self._do_subset_mesh(case_dir, actions, count)

        # --- Strategy 1: multiple regions ---
        regions = parse_checkmesh_regions(output)

        if len(regions) > 1:
            largest = max(regions, key=lambda r: r["cells"])
            threshold = largest["cells"] * self._ORPHAN_REGION_FRACTION
            orphans = [
                r for r in regions
                if r["name"] != largest["name"] and r["cells"] < threshold
            ]
            kept = [
                r for r in regions
                if r["name"] != largest["name"] and r["cells"] >= threshold
            ]

            if orphans:
                orphan_total = sum(r["cells"] for r in orphans)
                orphan_names = ", ".join(
                    f"{r['name']}({r['cells']})" for r in orphans
                )
                logger.warning(
                    f"checkMesh detected {len(regions)} disconnected regions. "
                    f"Auto-removing {len(orphans)} small orphan region(s) "
                    f"[{orphan_names}] ({orphan_total} cells, "
                    f"< {self._ORPHAN_REGION_FRACTION:.0%} of largest "
                    f"{largest['name']} with {largest['cells']} cells)"
                )
                regions_to_keep = [largest] + kept
                actions = []
                first = regions_to_keep[0]
                actions.append(
                    '    {\n        name    keepCells;\n        type    cellSet;\n'
                    '        action  new;\n        source  cellToCell;\n'
                    f'        sourceInfo {{ set {first["name"]}; }}\n    }}'
                )
                for r in regions_to_keep[1:]:
                    actions.append(
                        '    {\n        name    keepCells;\n        type    cellSet;\n'
                        '        action  add;\n        source  cellToCell;\n'
                        f'        sourceInfo {{ set {r["name"]}; }}\n    }}'
                    )
                removed += subset(actions, orphan_total)
                if removed:
                    # A subset changes the delivered mesh in either serial or
                    # parallel mode. Report the repaired mesh's checkMesh
                    # verdict, not the failed input mesh's verdict.
                    return removed + self._remove_disconnected_cells(
                        case_dir, snap_off=snap_off, n_procs=n_procs,
                        cleanup_pass=cleanup_pass + 1,
                    )
            elif kept:
                total = sum(r["cells"] for r in regions)
                logger.warning(
                    f"checkMesh detected {len(regions)} disconnected regions "
                    f"(total {total} cells, largest: {largest['name']} "
                    f"with {largest['cells']} cells). NOT auto-removing — "
                    f"all secondary regions are large enough to be "
                    f"legitimate containment zones."
                )

        # --- Strategy 2: isolated cells (zero/one non-boundary face) ---
        warnings = parse_checkmesh_warnings(output)
        isolated = [w for w in warnings if w["code"] == "isolated_cells"]
        if isolated:
            n_isolated = isolated[0]["count"]
            logger.warning(
                f"Found {n_isolated} isolated cell(s) with zero/one non-boundary face "
                f"(point-connected but no flow coupling) — removing via oneInternalFaceCells"
            )

            mesh_roots = ([case_dir / f"processor{i}" for i in range(n_procs)]
                          if n_procs > 1 else [case_dir])
            if all((root / "constant" / "polyMesh" / "sets" / "oneInternalFaceCells").exists()
                   for root in mesh_roots):
                actions = [
                    '    {\n        name    keepCells;\n        type    cellSet;\n'
                    '        action  new;\n        source  cellToCell;\n'
                    '        sourceInfo { set oneInternalFaceCells; }\n    }',
                    '    {\n        name    keepCells;\n        type    cellSet;\n'
                    '        action  invert;\n    }',
                ]
                removed += subset(actions, n_isolated)
            else:
                if n_procs > 1:
                    raise RuntimeError("checkMesh reported isolated cells but distributed cellSets are missing")
                logger.warning(
                    "oneInternalFaceCells set not found on disk — "
                    "checkMesh may not have written it. Skipping removal."
                )

        # --- Underdetermined cells: warn but do NOT remove ---
        # Removing connected cells via subsetMesh exposes internal faces as
        # "oldInternalFaces" (type=empty), which breaks the momentum predictor
        # in buoyantBoussinesqSimpleFoam (U residuals disappear entirely).
        # A few bad-quality cells are far less harmful than empty-type patches.
        small_det = [w for w in warnings if w["code"] == "small_determinant"]
        if small_det:
            logger.warning(
                f"Found {small_det[0]['count']} under-determined cell(s) "
                f"(determinant < 0.001) — keeping in mesh (removal would "
                f"create oldInternalFaces empty patch)"
            )

        for root in ([case_dir / f"processor{i}" for i in range(n_procs)]
                     if n_procs > 1 else [case_dir]):
            self._fix_old_internal_faces_patch_type(root)
        if removed:
            # Validate the repaired mesh, not just the discarded input mesh.
            return removed + self._remove_disconnected_cells(
                case_dir, snap_off=snap_off, n_procs=n_procs,
                cleanup_pass=cleanup_pass + 1,
            )
        # checkMesh may return exit code zero even when geometry checks fail.
        # Only its complete Mesh OK verdict authorizes the delivered mesh.
        if "Mesh OK." not in output:
            failed = re.search(r"Failed\s+(\d+)\s+mesh checks", output)
            if failed:
                raise RuntimeError(f"Final checkMesh failed {failed.group(1)} mesh checks; see raw quality output")
            raise RuntimeError("Final checkMesh did not produce a complete Mesh OK quality verdict")
        return removed

    def _do_subset_mesh(
        self, case_dir: Path, topo_actions: list, n_removed: int, *, n_procs: int = 1
    ) -> int:
        """Run topoSet + subsetMesh to keep only the cells selected by *topo_actions*."""
        import shutil as _shutil

        topo_dict = (
            "FoamFile\n{\n    version 2.0;\n    format ascii;\n"
            "    class dictionary;\n    object topoSetDict;\n}\n\n"
            "actions\n(\n" + "\n".join(topo_actions) + "\n);\n"
        )
        topo_path = case_dir / "system" / "topoSetDict"
        topo_path.write_text(topo_dict, encoding="utf-8")

        if n_procs > 1:
            # dcCart's parallel quality pass precedes final field generation;
            # there are no processor fields to map or serial mesh to assemble.
            try:
                self._run_cmd(f"mpirun -np {n_procs} topoSet -parallel", case_dir)
                self._run_cmd(f"mpirun -np {n_procs} subsetMesh -overwrite keepCells -parallel", case_dir)
            finally:
                topo_path.unlink(missing_ok=True)
            for i in range(n_procs):
                self._fix_old_internal_faces_patch_type(case_dir / f"processor{i}")
            logger.info(f"Removed {n_removed} disconnected/isolated cell(s) from partitioned mesh")
            return n_removed

        zero_dir = case_dir / "0"
        zero_backup = case_dir / "0_pre_subset"
        try:
            self._run_cmd("topoSet", case_dir)
            if zero_dir.exists():
                if zero_backup.exists():
                    _shutil.rmtree(zero_backup)
                zero_dir.rename(zero_backup)
                zero_dir.mkdir()
            self._run_cmd("subsetMesh -overwrite keepCells", case_dir)
        except RuntimeError as exc:
            logger.error(f"Failed to remove disconnected cells: {exc}")
            if zero_backup.exists() and not any(zero_dir.iterdir()):
                _shutil.rmtree(zero_dir)
                zero_backup.rename(zero_dir)
            return 0
        finally:
            topo_path.unlink(missing_ok=True)
            if zero_backup.exists():
                _shutil.rmtree(zero_backup, ignore_errors=True)

        self._fix_old_internal_faces_patch_type(case_dir)
        logger.info(f"Removed {n_removed} disconnected/isolated cell(s) from mesh")
        return n_removed

    @staticmethod
    def _fix_old_internal_faces_patch_type(case_dir: Path) -> None:
        """Convert oldInternalFaces from 'empty' to 'wall' type if it exists.

        subsetMesh creates 'oldInternalFaces' (type=empty) for exposed
        internal faces.  'empty' type in a 3D case breaks the momentum
        predictor — buoyantBoussinesqSimpleFoam skips U solving entirely.
        Changing to 'wall' gives those faces a harmless noSlip BC.
        """
        boundary_path = case_dir / "constant" / "polyMesh" / "boundary"
        if not boundary_path.exists():
            return

        text = boundary_path.read_text(encoding="utf-8")
        if "oldInternalFaces" not in text:
            return

        import re as _re
        pattern = (
            r'(oldInternalFaces\s*\n\s*\{\s*\n'
            r'\s*type\s+)empty(;\s*\n'
            r'\s*inGroups\s+\d+\s*\()\s*empty\s*(\))'
        )
        new_text, n = _re.subn(pattern, r'\1wall\2wall\3', text)
        if n > 0:
            boundary_path.write_text(new_text, encoding="utf-8")
            logger.warning(
                "Converted oldInternalFaces patch type: empty → wall "
                "(empty type breaks momentum predictor in 3D cases)"
            )

    @staticmethod
    def _clean_processor_dirs(case_dir: Path) -> None:
        """Remove processor* directories from a case directory."""
        for pdir in sorted(_glob.glob(str(case_dir / "processor*"))):
            shutil.rmtree(pdir, ignore_errors=True)
        logger.info(f"Cleaned processor directories in {case_dir}")

    @staticmethod
    def _clean_stale_polymesh_zones(case_dir: Path) -> None:
        """Delete stale face/cell/point zone files in constant/polyMesh/ before blockMesh.

        blockMesh overwrites points/faces/cells/owner/neighbour/boundary but
        does NOT touch faceZones/cellZones/pointZones. Leftover zone files
        from a prior run's createBaffles output survive blockMesh and are
        picked up by decomposePar, which then distributes them to whichever
        processor scotch assigns — usually concentrated on one proc while
        other procs have 0 zones. snappyHexMesh parallel then fails with
        "faceZones are not synchronised on processors" in
        meshRefinement::checkCoupledFaceZones.
        """
        polymesh_dir = case_dir / "constant" / "polyMesh"
        for name in ("faceZones", "cellZones", "pointZones"):
            stale = polymesh_dir / name
            if stale.exists():
                stale.unlink()
                logger.info(f"Removed stale {name} from {polymesh_dir}")

    def _run_parallel_mesh_pipeline(self, n_procs: int) -> None:
        """Run blockMesh → decomposePar → snappyHexMesh → reconstruct → createBaffles → checkMesh."""
        self._start_mesh_budget()
        self._ensure_mesh_system_files()
        case_dir = self.mesh_dir

        baffles_dict = case_dir / "system" / "createBafflesDict"
        feat_dict = case_dir / "system" / "surfaceFeatureExtractDict"
        total_steps = 5  # blockMesh, decomposePar, snappy, reconstruct, checkMesh
        if feat_dict.exists():
            total_steps += 1
        if baffles_dict.exists():
            # parallel createBaffles (redistribute+split) + wall-baffle conversion
            total_steps += 2
        step = 0

        self._clean_stale_polymesh_zones(case_dir)
        step += 1
        logger.info(f"Step {step}/{total_steps}: blockMesh")
        self._run_cmd("blockMesh", case_dir)

        if feat_dict.exists():
            step += 1
            logger.info(f"Step {step}/{total_steps}: surfaceFeatureExtract")
            self._run_cmd("surfaceFeatureExtract", case_dir)

        step += 1
        logger.info(f"Step {step}/{total_steps}: decomposePar -force -no-fields ({n_procs} processors)")
        # Pre-snappy: the mesh is still the uniform background block, so a graph
        # partition buys nothing. hierarchical splits it into slabs instantly.
        generate_decompose_par_dict(case_dir, n_procs, method="hierarchical")
        self._clean_processor_dirs(case_dir)
        self._run_cmd("decomposePar -force -no-fields", case_dir)

        step += 1
        logger.info(f"Step {step}/{total_steps}: snappyHexMesh -overwrite -parallel ({n_procs} procs)")
        self._run_cmd(
            f"mpirun -np {n_procs} snappyHexMesh -overwrite -parallel",
            case_dir,
        )

        # Split baffles in parallel WHILE the mesh is still decomposed (snappy
        # left it in processor*/). createBaffles is otherwise a serial,
        # multi-minute pass on big meshes; in parallel it is a few seconds and
        # byte-identical (verified: same patches and per-baffle face counts).
        # The catch: parallel createBaffles silently drops faceZone faces that
        # straddle a processor boundary, punching holes in the porous/wall jump.
        # So first redistributePar with a preserveFaceZones constraint to pull
        # every baffle faceZone onto a single processor. createBaffles here is
        # mesh-only (the dict sets no fields), so field regen / wall conversion /
        # orphan cleanup all stay serial on the reconstructed mesh below. Any
        # failure falls back to the serial createBaffles path.
        baffles_done_in_parallel = False
        if baffles_dict.exists() and sorted(case_dir.glob("processor*")):
            step += 1
            try:
                # hierarchical here too: this re-decomposition exists only to pull
                # each baffle faceZone onto one processor before createBaffles, and
                # a graph partition of a 600k-cell mesh is the expensive half of it.
                generate_decompose_par_dict(
                    case_dir, n_procs, preserve_face_zones=True, method="hierarchical"
                )
                logger.info(
                    f"Step {step}/{total_steps}: redistributePar (preserveFaceZones) "
                    f"+ createBaffles -parallel ({n_procs} procs)"
                )
                self._run_cmd(f"mpirun -np {n_procs} redistributePar -parallel -overwrite", case_dir)
                # Parallel createBaffles reads processor*/constant/polyMesh, so
                # the colliding empty patches have to go from every one of them.
                dropped = 0
                for pdir in sorted(case_dir.glob("processor*")):
                    dropped += self._drop_empty_baffle_patches(case_dir, pdir)
                if dropped:
                    logger.info(
                        f"Removed {dropped} colliding empty baffle patch(es) across "
                        f"{n_procs} processor directories"
                    )
                self._run_cmd(f"mpirun -np {n_procs} createBaffles -overwrite -parallel", case_dir)
                baffles_done_in_parallel = True
            except RuntimeError as exc:
                logger.warning(
                    f"parallel createBaffles failed ({exc}); falling back to "
                    f"serial createBaffles after reconstruct"
                )
                generate_decompose_par_dict(case_dir, n_procs)  # restore plain dict

        step += 1
        proc_dirs = sorted(case_dir.glob("processor*"))
        if proc_dirs:
            logger.info(f"Step {step}/{total_steps}: reconstructParMesh -constant")
            try:
                self._run_cmd("reconstructParMesh -constant -mergeTol 1E-08 -noZero", case_dir)
            except RuntimeError:  # _run_cmd raises RuntimeError on non-zero exit
                poly = case_dir / "constant" / "polyMesh"
                if self._verify_reconstructed_mesh(poly):
                    logger.warning(
                        "reconstructParMesh crashed (likely during addressing "
                        "write) but the reconstructed mesh exists — continuing"
                    )
                else:
                    raise
            self._clean_processor_dirs(case_dir)
        else:
            logger.info(f"Step {step}/{total_steps}: reconstructParMesh -constant (skipped — snappy already reconstructed)")
        self._diagnose_background_patch(case_dir)

        if baffles_dict.exists():
            step += 1
            # Field regen runs on the reconstructed serial mesh regardless; for
            # the serial fallback, createBaffles still needs these fields present.
            self._regenerate_fields_for_baffles(case_dir)
            if baffles_done_in_parallel:
                logger.info(f"Step {step}/{total_steps}: baffles split in parallel — wall-baffle conversion")
            else:
                logger.info(f"Step {step}/{total_steps}: createBaffles -overwrite (serial)")
                self._run_cmd("createBaffles -overwrite", case_dir)
            self._convert_wall_baffle_patches_to_wall(case_dir)

        step += 1
        logger.info(f"Step {step}/{total_steps}: checkMesh (final quality check)")
        self._remove_disconnected_cells(case_dir)

    def _diagnose_background_patch(self, case_dir: Path) -> None:
        """Log the surviving face count of the generic background patch."""
        boundary_path = case_dir / "constant" / "polyMesh" / "boundary"
        if not boundary_path.exists():
            return

        text = boundary_path.read_text(encoding="utf-8")
        match = re.search(
            r'backgroundBoundary\s*\n\s*\{[^}]*?nFaces\s+(\d+)',
            text,
            re.MULTILINE,
        )
        if match:
            n_faces = int(match.group(1))
            if n_faces > 0:
                logger.warning(
                    "Background blockMesh patch still has faces after snappyHexMesh: "
                    f"backgroundBoundary(nFaces={n_faces})"
                )
            else:
                logger.info("Background blockMesh patch is empty after snappyHexMesh")
        elif "backgroundBoundary" not in text:
            logger.warning(
                "backgroundBoundary patch not found in boundary file after snappyHexMesh"
            )

    def _regenerate_fields_for_baffles(self, case_dir: Path) -> None:
        """Regenerate 0/ field files so createBaffles can read them.

        After snappyHexMesh the boundary patches may have changed, so the old
        0/ files (if any) won't match. We regenerate them directly from the
        STLGen bridge metadata.

        These fields are INTERMEDIATE — createBaffles only needs a file to
        exist + patch-structure to match. `_setup_complete_case_in_mesh`
        overwrites them afterward with the final (possibly strict) output.
        So we always pass strict=False here: a mesh run must not be blocked
        by BC issues at this stage; the solve gate in
        _setup_complete_case_in_mesh(strict=True) catches them if the user
        proceeds to solve.
        """
        # Remove stale field files (e.g. Cx, Cy, Cz, C, V from previous
        # postProcess runs) that don't match the current mesh cell count.
        zero_dir = case_dir / "0"
        if zero_dir.is_dir():
            for f in zero_dir.iterdir():
                if f.is_file() and f.name not in STANDARD_FIELD_NAMES:
                    logger.info(f"Removing stale field file: 0/{f.name}")
                    f.unlink()

        boundary_path = case_dir / "constant" / "polyMesh" / "boundary"
        fix_cyclic_match_tolerance(boundary_path)
        patches = read_boundary_patches(boundary_path)
        self._build_field_generator().generate_fields(case_dir, patches, strict=False)
        logger.info(f"Regenerated 0/ fields ({len(patches)} patches) for createBaffles")

        # Reconcile boundary types with wall-function BCs (safety net)
        self._reconcile_boundary_wall_types(boundary_path, case_dir)

    @staticmethod
    def _conda_prefix() -> str | None:
        """Return the conda env prefix if OpenFOAM lives inside it."""
        import shutil, sys
        bm = shutil.which("blockMesh")
        prefix = sys.prefix
        if bm and prefix and bm.startswith(prefix):
            return prefix
        return None

    #: third_parties/foam build output, relative to the backend package root
    _CUSTOM_SOLVER_DIR = Path(__file__).resolve().parents[1] / "third_parties" / "foam" / "platforms"

    @classmethod
    def _solver_for_simulation_config(cls, sc: dict) -> str:
        """Select the supported default while respecting explicit overrides."""
        solver = os.environ.get("DCDTUNI_SOLVER_OVERRIDE") or sc.get("solverName") or (
            "buoyantBoussinesqPimpleFoam_new"
            if (sc.get("solverType", "steady") or "steady") == "transient"
            else "buoyantBoussinesqSimpleFoam_new"
        )
        return cls._resolve_solver_binary(solver)

    @classmethod
    def _resolve_solver_binary(cls, solver: str) -> str:
        """Return an absolute path to *solver* when this repo builds it.

        third_parties/foam builds its solvers non-destructively into
        platforms/<WM_OPTIONS>/bin/. Preferring that copy makes a fresh clone
        self-contained (build it with third_parties/foam/deploy.sh) and stops a
        stale binary that someone once installed into the conda prefix from
        shadowing the source in this repo. Falls back to the bare name, so
        anything already on PATH still resolves.
        """
        if cls._CUSTOM_SOLVER_DIR.is_dir():
            for platform_dir in sorted(cls._CUSTOM_SOLVER_DIR.iterdir()):
                candidate = platform_dir / "bin" / solver
                if candidate.is_file() and os.access(candidate, os.X_OK):
                    logger.info(f"Using repo-built solver: {candidate}")
                    return str(candidate)
        if solver == "buoyantBoussinesqPimpleFoam_new":
            raise FileNotFoundError(
                f"{solver} is not built in {cls._CUSTOM_SOLVER_DIR}; "
                "build it with third_parties/foam/deploy.sh before transient solving"
            )
        if solver.endswith("_new"):
            # A *_new solver is ours; not finding it means the build is missing,
            # not that we should quietly use a stock solver of the same name.
            logger.warning(
                f"{solver} is not built in {cls._CUSTOM_SOLVER_DIR}; falling back "
                f"to PATH. Build it with third_parties/foam/deploy.sh"
            )
        return solver

    def _rewrite_explicit_boundary_values(self, run_dir: Path) -> None:
        """Put the explicit boundary values back after reconstructPar removed them.

        buoyantBoussinesqSimpleFoam_new writes every physical patch with its
        per-face values, but on a parallel run it writes them into
        `processorN/<time>/<field>`. reconstructPar then merges the processor
        fields into `<time>/<field>` by re-serialising them the STOCK way, which
        puts `type zeroGradient;` with no values back into the only file anyone
        downstream reads.

        Measured on a 100-iteration run of case/Base_leakage: processor0/100/T
        had 56 of 58 patches written nonuniform, while the reconstructed 100/T
        had 41 of 53 patches carrying nothing but a type. Running the same write
        again after reconstruction takes it to 51 of 53 — the remaining two are
        the cyclic baffle pair, which is left alone on purpose.

        Best-effort: a failure here costs the explicit values, not the solve, so
        it is logged and swallowed rather than failing a run whose results are
        otherwise complete.
        """
        binary = self._resolve_solver_binary("writeExplicitBC")
        if binary == "writeExplicitBC" and not shutil.which("writeExplicitBC"):
            logger.warning(
                "writeExplicitBC is not built; reconstructed fields keep "
                "OpenFOAM's default boundary serialisation (types without "
                "values). Build it with third_parties/foam/deploy.sh"
            )
            return
        try:
            logger.info("Re-writing explicit boundary values after reconstruction...")
            self._run_cmd(f"{binary} -latestTime", run_dir)
        except Exception as exc:
            logger.warning(f"writeExplicitBC failed ({exc}); boundary values stay implicit")

    def _foam_env_prefix(self) -> str:
        """Return shell preamble that isolates OpenFOAM to the conda env.

        Strips any system-installed OpenFOAM from PATH / LD_LIBRARY_PATH and
        sets FOAM_MPI so the real parallel Pstream library is found (the
        conda-forge package ships it under lib/mpich-<ver>/, not lib/sys-mpich/).
        """
        prefix = self._conda_prefix()
        # The repo-built meshers (third_parties/dcCartMesh/platforms/<tag>/bin)
        # link their own libdcCartMesh.so / libsnappyHexMeshDC.so by rpath, and
        # an rpath is an absolute path baked at link time: moving or renaming
        # the directory (snappyhex -> dcCartMesh, 2026-09-22) left every
        # binary unable to load its library until relinked. Putting the lib
        # dirs on LD_LIBRARY_PATH makes the launch independent of the rpath.
        mesher_libs = ":".join(
            str(d / "lib") for d in sorted(self._CUSTOM_MESHER_DIR.iterdir())
            if (d / "lib").is_dir()
        ) if self._CUSTOM_MESHER_DIR.is_dir() else ""
        mesher_env = f'export LD_LIBRARY_PATH="{mesher_libs}:$LD_LIBRARY_PATH" && ' if mesher_libs else ""
        if prefix:
            return (
                f'export WM_PROJECT_DIR="{prefix}" && '
                f'export FOAM_LIBBIN="{prefix}/lib" && '
                f'export FOAM_APPBIN="{prefix}/bin" && '
                f'export FOAM_ETC="{prefix}/etc" && '
                'export FOAM_MPI=sys-mpich && '
                'export FOAM_SIGFPE=false && '
                f'export PATH="{prefix}/bin:$(echo "$PATH" | tr ":" "\\n" '
                '| grep -iv openfoam | grep -v /openmpi/ | tr "\\n" ":")" && '
                'export LD_LIBRARY_PATH="$(echo "$LD_LIBRARY_PATH" | tr ":" "\\n" '
                '| grep -iv openfoam | grep -v /openmpi/ | tr "\\n" ":")" && '
                + mesher_env
            )
        return mesher_env + "export FOAM_SIGFPE=false && "

    @staticmethod
    def _verify_reconstructed_mesh(poly_dir: Path) -> bool:
        """Check that all essential polyMesh files exist and are non-empty."""
        required = ("points", "faces", "owner", "neighbour", "boundary")
        return all(
            (f := poly_dir / name).exists() and f.stat().st_size > 0
            for name in required
        )

    def _start_mesh_budget(self) -> None:
        """Arm the wall-clock budget for one whole mesh build."""
        mc = self._data.get("meshConfig", {}) or {}
        default = DEFAULT_MESH_TIME_LIMIT_S
        limit = _mesh_time_limit_seconds(mc, default)
        if limit > 0:
            self._mesh_deadline = time.monotonic() + limit
            self._mesh_limit_s = limit
            logger.info(f"Mesh time budget: {limit:.0f}s")
        else:
            self._mesh_deadline = None
            self._mesh_limit_s = 0.0
            logger.info("Mesh time budget: disabled")

    def _clear_mesh_budget(self) -> None:
        """Disarm the budget so a following SOLVE is never killed by it."""
        self._mesh_deadline = None
        self._mesh_limit_s = 0.0

    def _mesh_time_remaining(self) -> Optional[float]:
        """Seconds left in the budget, or None when no budget is armed."""
        deadline = getattr(self, "_mesh_deadline", None)
        if deadline is None:
            return None
        return deadline - time.monotonic()

    def _check_mesh_budget(self, step: str) -> None:
        """Raise if the budget is already spent before starting *step*."""
        remaining = self._mesh_time_remaining()
        if remaining is not None and remaining <= 0:
            raise MeshTimeoutError(
                f"mesh time budget of {self._mesh_limit_s:.0f}s exhausted "
                f"before '{step}'"
            )

    #: The mpirun step currently running, for the SIGTERM handler in run_cfd.
    _active_step_proc: "subprocess.Popen | None" = None

    @classmethod
    def active_step_process(cls) -> "subprocess.Popen | None":
        proc = cls._active_step_proc
        return proc if proc is not None and proc.poll() is None else None

    @staticmethod
    def _terminate_process_group(proc: "subprocess.Popen") -> None:
        """SIGTERM the whole group, then SIGKILL what survives.

        The step is a shell that forks mpirun which forks N solvers, so killing
        only `proc` orphans the real work. `start_new_session=True` at spawn
        puts them all in one group that can be signalled as a unit.
        """
        if proc.poll() is not None:
            return
        try:
            pgid = os.getpgid(proc.pid)
        except (ProcessLookupError, PermissionError):
            pgid = None
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                if pgid is not None:
                    os.killpg(pgid, sig)
                else:
                    proc.send_signal(sig)
            except (ProcessLookupError, PermissionError):
                return
            try:
                proc.wait(timeout=10)
                return
            except subprocess.TimeoutExpired:
                continue

    def _run_cmd(self, cmd: str, workdir: Path) -> None:
        """Run an OpenFOAM command, streaming output via loguru.

        Honours the mesh wall-clock budget when one is armed: a watchdog kills
        the whole process group at the deadline and the call raises
        MeshTimeoutError. A watchdog is used rather than a poll-loop check
        because the serial branch blocks in readline() and would not notice the
        deadline until the step produced its next line — which is exactly the
        case where a step has stopped making progress.
        """
        self._check_mesh_budget(cmd)
        logger.info(f"Running: {cmd}  (in {workdir})")
        shell_cmd = self._foam_env_prefix() + cmd

        divergence: Optional[str] = None
        time_step = ["?"]

        def _note_divergence(stripped: str) -> None:
            nonlocal divergence
            m = _RE_TIME_STEP.search(stripped)
            if m:
                time_step[0] = m.group(1)
            if divergence:
                return
            m = _RE_CONTINUITY.search(stripped)
            if m:
                try:
                    value = float(m.group(1))
                except ValueError:
                    return
                if value > DIVERGENCE_CONTINUITY_THRESHOLD:
                    divergence = (
                        f"continuity error sum local = {m.group(1)} at iteration "
                        f"{time_step[0]} (threshold "
                        f"{DIVERGENCE_CONTINUITY_THRESHOLD:.0e})"
                    )
                return
            if _RE_NAN_RESIDUAL.search(stripped):
                divergence = (
                    f"NaN residual at iteration {time_step[0]}: "
                    f"{stripped[:160]}"
                )

        is_mpi = "mpirun" in cmd
        # Workaround: hwloc's GL plugin scans X11 ports (6000-6006) on startup.
        # If a non-X11 service occupies one of these ports (e.g. TensorBoard on 6006),
        # mpirun hangs forever waiting for an X11 handshake response.
        # Fix: create a plugins dir without hwloc_gl.so and point HWLOC_PLUGINS_PATH to it.
        if is_mpi:
            _hwloc_nogl = prepare_hwloc_plugins()
            if _hwloc_nogl is not None:
                shell_cmd = f'export HWLOC_PLUGINS_PATH="{_hwloc_nogl}" && {shell_cmd}'
            # Disable FPE trapping to allow solver to recover from initial transients
            shell_cmd = f'export FOAM_SIGFPE=false && {shell_cmd}'
        mpi_env = mesh_mpi_environment(
            cmd, mesh_budget_active=self._mesh_time_remaining() is not None,
        ) if is_mpi else None
        if mpi_env is not None:
            logger.info(f"Mesh MPI binding: {mpi_env['HYDRA_BINDING']}")
        proc = None
        watchdog = None
        timed_out = threading.Event()

        def _arm_watchdog(p: "subprocess.Popen"):
            remaining = self._mesh_time_remaining()
            if remaining is None:
                return None

            def _fire():
                timed_out.set()
                logger.error(
                    f"Mesh time budget of {self._mesh_limit_s:.0f}s exhausted "
                    f"during '{cmd}' — aborting"
                )
                self._terminate_process_group(p)

            t = threading.Timer(max(0.0, remaining), _fire)
            t.daemon = True
            t.start()
            return t

        try:
            if is_mpi:
                # MPI: write output to log file to avoid pipe deadlock
                mpi_log = workdir / "log.mpi_solver"
                self._rotate_mpi_log(mpi_log, cmd)
                with open(mpi_log, "w") as log_fh:
                    proc = subprocess.Popen(
                        shell_cmd, shell=True, executable="/bin/bash",
                        cwd=str(workdir), stdout=log_fh, stderr=subprocess.STDOUT,
                        start_new_session=True, env=mpi_env,
                    )
                    watchdog = _arm_watchdog(proc)
                    # Tail the log file while process runs
                    import time
                    last_pos = 0
                    while proc.poll() is None:
                        # 2 s here used to add up to ~2 s of dead time to EVERY
                        # mpirun step (blockMesh-adjacent steps run in <1 s now).
                        time.sleep(0.2)
                        with open(mpi_log, "r") as rf:
                            rf.seek(last_pos)
                            new_lines = rf.readlines()
                            last_pos = rf.tell()
                            for line in new_lines:
                                stripped = line.rstrip()
                                if stripped:
                                    logger.info(stripped)
                                    _note_divergence(stripped)
                            if divergence:
                                break
                    if not divergence:
                        # Read remaining output
                        with open(mpi_log, "r") as rf:
                            rf.seek(last_pos)
                            for line in rf:
                                stripped = line.rstrip()
                                if stripped:
                                    logger.info(stripped)
                                    _note_divergence(stripped)
            else:
                proc = subprocess.Popen(
                    shell_cmd, shell=True, executable="/bin/bash",
                    cwd=str(workdir), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1, start_new_session=True,
                )
                watchdog = _arm_watchdog(proc)
                for line in iter(proc.stdout.readline, ""):
                    stripped = line.rstrip()
                    if stripped:
                        logger.info(stripped)
                        _note_divergence(stripped)
                    if divergence:
                        break
                proc.wait()
        except Exception as exc:
            logger.error(f"Failed to start '{cmd}': {exc}")
            raise
        finally:
            if watchdog is not None:
                watchdog.cancel()

        if timed_out.is_set():
            raise MeshTimeoutError(
                f"'{cmd}' was aborted after the {self._mesh_limit_s:.0f}s mesh "
                f"time budget ran out"
            )
        if divergence:
            # Stop the ranks now: a field that is already nan will never
            # recover, and every further iteration is budget and log noise.
            self._terminate_process_group(proc)
            raise RuntimeError(
                f"'{cmd}' diverged: {divergence}. The solve is numerically "
                "unstable for this case — lower "
                "solverConfig.relaxationFactors (T and U towards 0.3), reduce "
                "the heat/flow contrast of the fan-server feedback BCs, or "
                "refine where the continuity error blows up. The job was "
                "stopped at the first catastrophic iteration rather than "
                "running on a field that is already nan."
            )
        return_code = proc.returncode if proc else -1
        if return_code != 0:
            detail = f"'{cmd}' exited with code {return_code}"
            if is_mpi:
                detail += (
                    f"; the step's own output is preserved in {workdir / 'log.mpi_solver'}"
                    " and its rotated log.mpi_solver.* copies"
                )
            raise RuntimeError(detail)

    def _rotate_mpi_log(self, mpi_log: Path, cmd: str) -> None:
        """Keep the previous mpirun step's output instead of truncating it.

        `log.mpi_solver` is reopened with "w" by every mpirun step, so a step
        that died took its own evidence with it the moment the next step
        started: a `redistributePar` that exited 1 left the job log with nothing
        but the runner's traceback, and there was nothing for a fix to aim at.
        Rotate instead — a handful of files, named by step, pruned to the last
        few. Best effort: losing a log must never fail a mesh.

        The copy is named after the step that WROTE it — its OpenFOAM `Exec :`
        banner, else the command this runner launched before `cmd` — not after
        `cmd`, which is about to overwrite it.
        """
        tokens = [
            t for t in cmd.split()
            if not t.startswith("-") and t not in ("mpirun", "np") and not t.isdigit()
        ]
        previous = getattr(self, "_mpi_log_tag", None)
        self._mpi_log_tag = Path(tokens[0] if tokens else "mpi").name[:24]
        if not mpi_log.exists():
            return
        try:
            seq = getattr(self, "_mpi_log_seq", 0) + 1
            self._mpi_log_seq = seq
            tag = previous or "mpi"
            with open(mpi_log, encoding="utf-8", errors="replace") as fh:
                for _, line in zip(range(60), fh):
                    banner = re.match(r"Exec\s*:\s*(\S+)", line)
                    if banner:
                        tag = Path(banner.group(1)).name[:24]
                        break
            shutil.copy2(mpi_log, mpi_log.with_name(f"log.mpi_solver.{seq:02d}_{tag}"))
            for old in sorted(mpi_log.parent.glob("log.mpi_solver.*"))[:-6]:
                old.unlink(missing_ok=True)
        except OSError:
            pass

    def _run_cmd_capture(self, cmd: str, workdir: Path) -> str:
        """Run an OpenFOAM command and return its stdout+stderr as a string."""
        self._check_mesh_budget(cmd)
        checked = None
        if ("-allTopology" in cmd and "-allGeometry" in cmd
                and hasattr(self, "cfdrun_path")
                and (getattr(self, "_input_snapshot", None) is not None or Path(self.cfdrun_path).is_file())
                and Path(workdir).resolve() == Path(self.mesh_dir).resolve()):
            import hashlib
            import shlex
            from backend.aircooling.runner.openfoam_patch_area import mesh_identity_sha256
            checked = {
                "command": shlex.split(cmd), "workdir": Path(workdir),
                "mesh_sha256_before": mesh_identity_sha256(workdir),
                "cfdrun_sha256_before": hashlib.sha256(self._read_cfdrun_bytes()).hexdigest(),
            }
        logger.info(f"Running (capture): {cmd}  (in {workdir})")
        shell_cmd = self._foam_env_prefix() + cmd
        # Never outlive the mesh budget; 300 s stays the ceiling for a captured
        # step (checkMesh, topoSet) when no budget is armed.
        remaining = self._mesh_time_remaining()
        step_timeout = 300.0 if remaining is None else max(1.0, min(300.0, remaining))
        if "mpirun" in cmd:
            hwloc_plugins = prepare_hwloc_plugins()
            if hwloc_plugins is not None:
                shell_cmd = f'export HWLOC_PLUGINS_PATH="{hwloc_plugins}" && {shell_cmd}'
            mpi_env = mesh_mpi_environment(
                cmd, mesh_budget_active=remaining is not None,
            )
            if mpi_env is not None:
                logger.info(f"Mesh MPI binding: {mpi_env['HYDRA_BINDING']}")
            # subprocess.run(timeout=...) only kills the shell. All MPI ranks
            # must die too if a distributed check exceeds the mesh budget.
            proc = subprocess.Popen(
                shell_cmd, shell=True, executable="/bin/bash", cwd=str(workdir),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                start_new_session=True, env=mpi_env,
            )
            STLGenRunner._active_step_proc = proc
            try:
                stdout, stderr = proc.communicate(timeout=step_timeout)
            except subprocess.TimeoutExpired:
                self._terminate_process_group(proc)
                proc.communicate()
                raise
            finally:
                STLGenRunner._active_step_proc = None
            if checked is not None:
                self._prechecked_air_quality = dict(checked, returncode=proc.returncode, stdout=stdout, stderr=stderr)
            return stdout + stderr
        result = subprocess.run(
            shell_cmd, shell=True, executable="/bin/bash",
            cwd=str(workdir), capture_output=True, text=True, timeout=step_timeout,
        )
        if checked is not None:
            self._prechecked_air_quality = dict(checked, returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)
        return result.stdout + result.stderr
