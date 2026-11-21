"""Utility functions for reading the datasets."""
import os
import sys
from typing import Optional
import torch
from torch.utils.data import Dataset
import numpy as np
import h5py
import copy
import logging
import importlib
import importlib.util
import hashlib

from Utils.utilities import FaceType
from FVsolver.FVdiscretization.FVgrad import compute_normal_matrix
from FVdomain.Graph_pool import case_seed_for_global_slot
from FVdomain.SetBC.onthefly.Parafunction import Functionbase, ParametricManager
from FVdomain.SetBC.onthefly.BCbase import BCType, VARType, VAR_CONFIG, SOLVER_VARS
from FVdomain.SetBC.onthefly.setCyclic import prepare_cyclic_boundary_geometry
import tomlkit
try:
    import tomllib
except ModuleNotFoundError:                 # Python 3.10: the same parser under its earlier name
    import tomli as tomllib
from tomlkit.items import Item
from Utils.toml_utils import toml_to_native
from Utils import mesh_schema
from Utils import geometry_precision
from FVdomain import region_physics
from FVsolver.applications import PLANE_MIRROR_SOLVERS

logger = logging.getLogger(__name__)

# Each dynamic sidecar is compiled from the exact source bytes recorded here.
# SourceFileLoader may otherwise accept a same-mtime/size stale .pyc while the
# certificate hashes a different .py file. Callers bind these digests to their
# input manifest; a set retains every import attempt if a file changes mid-run.
CUSTOM_BC_SOURCE_SHA256 = {}
CUSTOM_BC_SOURCE_FAILURES = {}


def validate_patch_table(
    patch_dict: dict,
    num_cells: int,
    num_faces: int,
    num_cpd_cells: int,
    patch_table_path: str = "<patchinfo.toml>",
    h5_path: str = "<mesh.h5>",
) -> None:
    """Refuse a ``patchinfo.toml`` that does not describe the mesh being loaded.

    Nothing downstream re-derives the boundary from the mesh: the patch table IS
    the definition of which faces are boundary faces and which compound cells are
    ghosts.  A table belonging to another grid is therefore not detected, it is
    obeyed -- in one case the 153,600-cell periodic-hills table was served to the
    1,228,800-cell grid and turned 12,664 interior cells into ghosts, overwrote
    8,951 interior centroids in ``prepare_cyclic_boundary_geometry``, zeroed 7,759
    continuity diagonals and put 1.9M out-of-pattern nonzeros in the assembled
    Jacobian, with no error and no warning
    (``docs/research/2026-08-16-mwi-refinement-singularity.md``).

    Two independent assertions of the table are checked against the mesh:

    1. ``[metadata]`` ``num_cells`` / ``num_faces`` / ``num_internal_faces``.
    2. the patch spans themselves, which must TILE the mesh's boundary ranges:
       ``[start_idx_face, end_idx_face)`` exactly covers
       ``[num_internal_faces, num_faces)`` with no gap and no overlap, and
       ``[start_idx_cpd_cell, end_idx_cpd_cell)`` exactly covers
       ``[num_cells, num_cpd_cells)``.

    (2) is the general check and (1) is the cheap one: a table with correct counts
    but shifted offsets (a measured control,
    which still produced 5,574 zero diagonals on the coarse grid) passes (1) and
    fails (2).  It is the FACE half of (2) that makes the damaging write safe, not
    the ghost half: ``setCyclic.py:288`` writes
    ``cpd_cell_pos[neighbor_cpd_cell[1, start_idx_face:end_idx_face]]``, so the
    cells it touches are chosen by the FACE span through the mesh's own neighbour
    array, and ``start_idx_cpd_cell`` never enters.  On a parsed mesh that array
    maps ``[num_internal_faces, num_faces)`` bijectively onto ``[num_cells,
    num_cpd_cells)`` (verified on the hills, cylinder and flat-plate
    meshes: every boundary face's neighbour is a ghost, every interior face's is
    not), hence a table whose face spans tile that range cannot address an
    interior cell.  The ghost half is checked because the BC classes write
    through it.

    The mesh-side counts are taken from dataset SHAPES, not from the ``.h5`` root
    attributes, so a stale attribute cannot certify itself; no dataset is read.

    Args:
        patch_dict: the parsed ``patchinfo.toml``, before ``fvPatch().register()``.
        num_cells: interior cell count of the loaded mesh (``cell|cell_pos``).
        num_faces: total face count of the loaded mesh (``face|face_pos``).
        num_cpd_cells: compound cell count (``cpd|cell_pos``); interior + ghosts.
        patch_table_path, h5_path: named in the error message, both sides.

    Raises:
        ValueError: with both paths and both sides of every mismatch.
    """
    num_boundary_faces = num_cpd_cells - num_cells
    num_internal_faces = num_faces - num_boundary_faces
    problems: list = []

    meta = patch_dict.get("metadata", {}) or {}
    expected_meta = {
        "num_cells": num_cells,
        "num_faces": num_faces,
        "num_internal_faces": num_internal_faces,
    }
    for key, mesh_value in expected_meta.items():
        if key not in meta:
            problems.append(
                f"[metadata] has no '{key}'; the mesh has {key} = {mesh_value}. "
                f"Re-parse the case so the table states which mesh it describes."
            )
        elif int(meta[key]) != int(mesh_value):
            problems.append(
                f"[metadata] {key} = {int(meta[key])}, mesh has {mesh_value}"
            )

    patches = patch_dict.get("patch", {}) or {}
    if not patches:
        problems.append("no [patch.*] sections at all")

    spans_face, spans_cpd = [], []
    for name, patch in patches.items():
        try:
            fs, fe = int(patch["start_idx_face"]), int(patch["end_idx_face"])
            cs, ce = int(patch["start_idx_cpd_cell"]), int(patch["end_idx_cpd_cell"])
            n_faces_patch = int(patch["n_faces"])
        except (KeyError, TypeError, ValueError):
            problems.append(f"patch '{name}' is missing its face/cpd_cell index range")
            continue
        if fe - fs != n_faces_patch:
            problems.append(
                f"patch '{name}': face span [{fs}, {fe}) is {fe - fs} faces "
                f"but n_faces = {n_faces_patch}"
            )
        if ce - cs != n_faces_patch:
            problems.append(
                f"patch '{name}': ghost span [{cs}, {ce}) is {ce - cs} cells "
                f"but n_faces = {n_faces_patch}"
            )
        spans_face.append((fs, fe, name))
        spans_cpd.append((cs, ce, name))

    for label, spans, lo, hi in (
        ("boundary face", spans_face, num_internal_faces, num_faces),
        ("ghost cell", spans_cpd, num_cells, num_cpd_cells),
    ):
        cursor = lo
        for start, end, name in sorted(spans):
            if start != cursor:
                if start > cursor:
                    kind = "leaves a gap"
                elif cursor == lo:
                    kind = f"starts below the mesh's {label} range"
                else:
                    kind = "overlaps the previous patch"
                problems.append(
                    f"{label} spans do not tile the mesh: patch '{name}' starts at "
                    f"{start} but the table has covered up to {cursor}; it {kind}. "
                    f"The mesh's {label} range is [{lo}, {hi})."
                )
                break
            cursor = end
        else:
            if cursor != hi:
                problems.append(
                    f"{label} spans cover [{lo}, {cursor}) but the mesh's "
                    f"{label} range is [{lo}, {hi})"
                )

    if problems:
        raise ValueError(
            f"Patch table does not describe this mesh.\n"
            f"  table: {patch_table_path}\n"
            f"  mesh:  {h5_path}\n"
            f"  mesh has num_cells = {num_cells}, num_faces = {num_faces}, "
            f"num_internal_faces = {num_internal_faces}, "
            f"num_cpd_cells = {num_cpd_cells}\n"
            + "".join(f"  - {p}\n" for p in problems)
            + "A table from another grid is obeyed, not detected: it decides which "
            "cells are ghosts, so a wrong one silently corrupts interior cell "
            "geometry and the assembled Jacobian. Point --dataset_dir at the "
            "directory holding this mesh's own patchinfo.toml, or re-parse the case."
        )


def build_wide_stencil_neighbor(
    neighbor_cpd_cell: torch.Tensor,
    neighbor_cpd_cell_x: torch.Tensor,
) -> torch.Tensor:
    """Extract pure 2-hop (wide-stencil) neighbors by removing 1-hop from extended.

    Fully vectorized (device-native) set-difference: each (l, r) edge is encoded as
    a single int64 key ``l * N + r`` and ``torch.isin`` selects the extended-stencil
    edges absent from the 1-hop set. This runs in ~1 ms on GPU for ~1.8M edges,
    versus ~1.2 s for the previous pure-Python loop (which also forced a GPU->CPU
    sync via ``.tolist()``). Critical because the caller re-runs this every forward:
    ``register_properties(force_register=True)`` resets the build guard each step
    (see FVInterpolation.register_geometrics / NNmodels.importer.forward), so a slow
    build dominates the FVM-loss step time. Output is identical to the old loop.

    Args:
        neighbor_cpd_cell: [2, F1] unidirectional 1-hop face pairs
        neighbor_cpd_cell_x: [2, Fx] unidirectional 1-hop + 2-hop pairs

    Returns:
        [2, F2] unidirectional pure 2-hop pairs (wide stencil), on the input device
    """
    device = neighbor_cpd_cell_x.device
    nc = neighbor_cpd_cell.to(device=device, dtype=torch.long)
    ncx = neighbor_cpd_cell_x.to(torch.long)
    # Empty-input guard BEFORE any .max() (Tensor.max() on a 0-element tensor raises).
    # Degenerate/tiny meshes (single cell, all-boundary) can have an empty 1-hop or
    # extended-stencil edge list; return an empty wide stencil so the caller's
    # `.shape[1] > 0` check cleanly skips the checker loss.
    if nc.numel() == 0 or ncx.numel() == 0:
        return torch.zeros(2, 0, dtype=torch.long, device=device)
    # int64 key per edge; N bounds both index ranges so keys are collision-free
    N = int(torch.max(torch.stack([nc.max(), ncx.max()]))) + 1
    key1 = nc[0] * N + nc[1]
    keyx = ncx[0] * N + ncx[1]
    mask = ~torch.isin(keyx, key1)
    L2, R2 = ncx[0][mask], ncx[1][mask]
    if L2.numel() == 0:
        return torch.zeros(2, 0, dtype=torch.long, device=device)
    return torch.stack([L2, R2], dim=0)


class CFDdatasetBase:

    def __init__(self,):
        self.params = None
        self.dynimic_params = None # 用于存放参数化变量的取值

    def _get_bc_class(self, bc_type: str, var_name: str, sidecar_dir: str = None):
        """
        Get boundary condition class from bc_type string and variable name.

        This method supports loading custom boundary conditions from the case's bc directory.
        Priority: <sidecar_dir>/bc/set{var_name}.py > FVdomain/SetBC/onthefly/set{var_name}.py

        Args:
            bc_type: Boundary condition type string (e.g., "fixedValue", "zeroGradient", "Parabolicinlet")
            var_name: Variable name (e.g., "U", "p", "T", "rho", "p_rgh")
            sidecar_dir: Directory holding this mesh's patchinfo.toml/fvconfig.toml/bc/,
                i.e. the one --dataset_dir named (e.g. "experiments/DataCenter/PCNO/sweep300/h5").

        Returns:
            Boundary condition class
        """
        # Normalize bc_type: convert to PascalCase for class name
        # e.g., "fixedValue" -> "Fixedvalue", "zeroGradient" -> "Zerogradient"
        # "noSlip" -> "Noslip", "cyclic" -> "Cyclic", "Parabolicinlet" -> "Parabolicinlet"
        class_name = bc_type[0].upper() + bc_type[1:].lower()

        # 1. First, try to load from case's bc directory (custom boundary condition)
        if sidecar_dir is not None:
            # Custom BC files sit beside the tomls they belong to: <sidecar_dir>/bc/
            custom_bc_path = os.path.join(sidecar_dir, "bc", f"set{var_name}.py")
            if os.path.exists(custom_bc_path):
                try:
                    # Preserve module metadata/relative-import context, but
                    # compile the bytes we actually hash rather than read pyc.
                    with open(custom_bc_path, 'rb') as source_handle:
                        source_bytes = source_handle.read()
                    source_path = os.path.realpath(custom_bc_path)
                    digest = hashlib.sha256(source_bytes).hexdigest()
                    spec = importlib.util.spec_from_file_location(
                        f"custom_bc_{var_name}_{os.path.basename(sidecar_dir)}",
                        custom_bc_path
                    )
                    custom_module = importlib.util.module_from_spec(spec)
                    exec(compile(source_bytes, custom_bc_path, 'exec',
                                 dont_inherit=True), custom_module.__dict__)
                    CUSTOM_BC_SOURCE_SHA256.setdefault(source_path, set()).add(digest)

                    # Try to get the class from the custom module
                    bc_class = getattr(custom_module, class_name, None)
                    if bc_class is not None:
                        logger.info(f"Loaded custom BC class '{class_name}' from {custom_bc_path}")
                        return bc_class
                    # If class not found in custom module, fall through to standard modules
                except Exception as e:
                    failed_path = os.path.realpath(custom_bc_path)
                    CUSTOM_BC_SOURCE_FAILURES.setdefault(failed_path, []).append(
                        f'{type(e).__name__}: {e}')
                    logger.warning(f"Failed to load custom BC from {custom_bc_path}: {e}")
                    # Fall through to standard modules

        # 2. Fall back to standard BC modules
        # Map variable names to their corresponding BC modules
        var_to_module = {
            "rho": "FVdomain.SetBC.onthefly.setRho",
            "U": "FVdomain.SetBC.onthefly.setU",
            "T": "FVdomain.SetBC.onthefly.setT",
            "p": "FVdomain.SetBC.onthefly.setP",
            "p_rgh": "FVdomain.SetBC.onthefly.setP_rgh",
            "phi": "FVdomain.SetBC.onthefly.setPhi",
            # Transported turbulence scalars (bc tomls speak OpenFOAM values;
            # setOmega applies the omega -> ln(omega) transform at the ghost
            # write -- turbulence design doc section 3.1).
            "k": "FVdomain.SetBC.onthefly.setK",
            "omega": "FVdomain.SetBC.onthefly.setOmega",
            # epsilon is PRIMAL (design A.4): setEpsilon binds setTurbScalar
            # with no value transform.
            "epsilon": "FVdomain.SetBC.onthefly.setEpsilon",
        }

        # Get the module name for this variable
        module_name = var_to_module.get(var_name)
        if module_name is None:
            raise ValueError(f"Unknown variable name: {var_name}. Supported: {list(var_to_module.keys())}")

        # Dynamically import the module
        try:
            module = importlib.import_module(module_name)
        except ImportError as e:
            raise ImportError(f"Failed to import module {module_name}: {e}")

        # Get the class from the module
        bc_class = getattr(module, class_name, None)
        if bc_class is None:
            raise AttributeError(
                f"Boundary condition class '{class_name}' not found in module {module_name}. "
                f"Available classes: {[name for name in dir(module) if not name.startswith('_')]}"
            )

        return bc_class

class CFDmesh(CFDdatasetBase):
    # Base class for CFDdataset with process_trajectory method

    _case_counter = 0

    def __init__(self, params):
        self.params = params
        self.dynimic_params = {} # 用于存放参数化变量的取值
        super().__init__()

    def init_env(self, mesh):
        """
        Initialize internal field and boundary conditions for all variables.

        This method is called both during initial loading and during reset_env.
        For parametric boundary conditions:
        1. On reset_env, we reset the parametric_manager to allow new sampling
        2. BC classes resolve $variable_name references in their __init__
        3. The new boundary conditions are applied

        Args:
            mesh: Mesh dictionary containing boundary_info and other mesh data

        Returns:
            mesh: Updated mesh dictionary with bc_instances
            phi_cpd_cell: Concatenated field tensor for all variables
        """
        # Get necessary data from mesh
        patch_dict = mesh["patch_dict"]
        fvpatch = mesh["fvpatch"]
        fvconfig = mesh["fvconfig"]

        num_cpd_cell = mesh["cpd|cell_pos"].shape[0]

        # Get parametric_manager (may be None if no parameters defined)
        parametric_manager = mesh.get("parametric_manager", None)

        # Check if this is a reset - if so, reset all parameters for new sampling
        if parametric_manager is not None and "boundary_info_raw" in mesh:
            parametric_manager.reset_all()
            # Use raw boundary_info for re-parsing (BC classes will resolve $refs)
            boundary_info = copy.deepcopy(mesh["boundary_info_raw"])
        else:
            # First load: use boundary_info as-is (already loaded from TOML)
            boundary_info = copy.deepcopy(mesh["boundary_info"])

        # Store field tensors and BC instances
        phi_cpd_cell = []
        bcpatch = {
            BCType.FIRST:[],
            # BCType.SECOND:[],
            BCType.MIXED:[],
            BCType.CYCLIC:[],
            BCType.COUPLED:[],
            BCType.EMPTY:[],
            "patch_idx_offset":0 # 这里不会动这个参数，其会在src/FVdomain/SetBC/onthefly/BCbase.py这里，拼batch的时候被修改
        }
        current_col = 0

        # Fields stored as the LOG of their OpenFOAM bc-toml value (kOmega's
        # omega-tilde, turbulence design section 2.1).  The BC classes handle
        # the ghost write themselves (setOmega.py); the internalField is
        # transformed HERE so the whole toml keeps OpenFOAM meaning.
        from FVsolver.Turbulence import log_transformed_bc_fields
        _log_bc_fields = set(log_transformed_bc_fields(
            fvconfig.get("solver", {}).get("turbulence", "laminar")))

        # Process each variable in boundary_info
        for var, bc_info in boundary_info.items():

            # 为每个变量创建一个bcpatch
            bcpatch[var] = []

            # Get number of channels for this variable
            num_channels = VAR_CONFIG.get(var)
            if num_channels is None:
                raise ValueError(f"Unknown field: {var}. Supported: {list(VAR_CONFIG.keys())}")

            # 1. Initialize internal field
            # Resolve any $variable_name references in internalField
            internal_field_info = bc_info.get("internalField")
            if internal_field_info is None:
                raise ValueError(f"Missing internalField for variable {var}")

            # Resolve parameter references in internalField
            if parametric_manager is not None:
                resolved_internal = parametric_manager.resolve_value(internal_field_info)
            else:
                resolved_internal = internal_field_info

            if var in _log_bc_fields:
                from FVdomain.SetBC.onthefly.setTurbScalar import (
                    log_transform_scalar_value,
                )
                resolved_internal = log_transform_scalar_value(resolved_internal)

            # Get cpd_cell_pos for position-dependent initial fields (e.g., parabolic)
            cpd_cell_pos = mesh.get("cpd|cell_pos", None)

            var_cpd_cell = Functionbase(
                value_type=resolved_internal["value_type"],
                value=resolved_internal["val"],
                num_element=num_cpd_cell,
                channel_dim=num_channels,
            ).eval(device="cpu", cpd_cell_pos=cpd_cell_pos, fvconfig=fvconfig)

            # 2. Initialize boundary conditions for this variable
            for patch_name, patch_bc in bc_info["boundaryfield"].items():
                bc_type = patch_bc.get("type")
                if bc_type is None:
                    raise ValueError(f"Missing BC type for patch {patch_name} in variable {var}")

                patch_idx = patch_dict[patch_name]["patch_idx"]

                # Get the boundary condition class
                # Pass the sidecar dir to support loading custom BC from <dataset_dir>/bc/
                sidecar_dir = mesh.get("sidecar_dir", None)
                bc_class = self._get_bc_class(bc_type, var, sidecar_dir=sidecar_dir)

                # Prepare boundary_info dict for this patch (excluding 'type')
                patch_boundary_info = {k: v for k, v in patch_bc.items() if k != "type"}
                patch_boundary_info["fvconfig"] = fvconfig
                patch_boundary_info["patch_dict"] = patch_dict
                # schema v2: interface conditions take their face pairing from the mesh
                patch_boundary_info["region_schema"] = mesh.get("region_schema")

                # Instantiate the boundary condition with parametric_manager
                # BC classes that support parameters will resolve $variable_name in their __init__
                bc_instance = bc_class(
                    patch_name=patch_name,
                    patch_idx=patch_idx,
                    boundary_info=patch_boundary_info,
                    parametric_manager=parametric_manager
                )

                bcpatch[bc_instance.bcType].append(bc_instance)

                # Initialize the field for this boundary (if the BC has init_field method)
                if hasattr(bc_instance, 'init_field'):
                    var_cpd_cell = bc_instance.init_field(
                        var_cpd_cell,
                        fvpatch,
                        neighbor_cpd_cell=mesh["cpd|neighbor_cell"],
                        fvconfig=fvconfig,
                        cpd_cell_pos=mesh.get("cpd|cell_pos", None),  # Pass cell positions for position-dependent BCs
                    )
                else:
                    raise NotImplementedError(f"BC type {bc_type} init_field() function not implemented")

                # Store BC instances for this variable
                bcpatch[var].append(bc_instance)

            # Store the field tensor
            phi_cpd_cell.append(var_cpd_cell)

            # Store the column mapping
            current_col += num_channels

        # Concatenate all field tensors
        phi_cpd_cell = torch.cat(phi_cpd_cell, dim=-1)

        # Optional Re-continuation warm start: an H5 may carry a per-interior-cell
        # field under "warm|phi_interior" (written by an external data-prep step,
        # e.g. experiments/LDC/odil-jfnk/ Re-sweep warm start from the
        # Re=1000 converged time-mean). When present, it overwrites ONLY the
        # interior cpd rows; ghost/boundary rows keep the BC-toml-derived values
        # computed above, since runtime BC enforcement (Dirichlet writes /
        # Neumann mirrors) re-derives them every step regardless of the seed.
        # Absent key = no-op, so every other case's H5 is unaffected.
        warm_phi_interior = mesh.get("warm|phi_interior")
        if warm_phi_interior is not None:
            mask_interior_cell = (
                mesh["cpd|cell_type"].reshape(-1) == FaceType.NORMAL
            )
            num_interior = int(mask_interior_cell.sum())
            warm_phi_interior = warm_phi_interior.to(
                device=phi_cpd_cell.device, dtype=phi_cpd_cell.dtype
            )
            expected_shape = (num_interior, phi_cpd_cell.shape[-1])
            if tuple(warm_phi_interior.shape) != expected_shape:
                raise ValueError(
                    f"warm|phi_interior has shape {tuple(warm_phi_interior.shape)}, "
                    f"expected {expected_shape} (num_interior_cells, phi_num_channels)"
                )
            phi_cpd_cell = phi_cpd_cell.clone()
            phi_cpd_cell[mask_interior_cell] = warm_phi_interior

        # Store metadata in mesh
        mesh["bcpatch"] = bcpatch
        mesh["phi_num_channels"] = current_col
        mesh["num_channels"] = current_col * 2 + len(FaceType)  # phi + init_x + cell_type(one-hot, 6类)
        mesh["init_phi_cpd_cell"] = phi_cpd_cell.clone()

        # Auto-detect if pRef constraint is needed based on pressure BC types
        # Following OpenFOAM logic: if p or p_rgh has FIRST (Dirichlet) or MIXED boundary conditions,
        # pressure is already constrained and we don't need pRef. Only enable pRef for pure
        # cyclic/periodic cases where pressure is undetermined up to an arbitrary constant.
        mesh = self._auto_detect_pRef_need(mesh, bcpatch, fvconfig)

        return mesh, phi_cpd_cell

    def _auto_detect_pRef_need(self, mesh, bcpatch, fvconfig):
        """
        Auto-detect whether pRef constraint is needed based on pressure boundary conditions.

        Following OpenFOAM's needReference() logic:
        - If ANY boundary patch for p or p_rgh has BCTYPE.FIRST (Dirichlet),
          then the pressure is already fixed and we DON'T need pRef constraint.
        - If ANY MIXED pressure BC has ``is_pressure_anchor = True``
          (e.g. naturalOutlet), the pressure is already anchored.
        - If ALL pressure boundaries are CYCLIC, EMPTY, or MIXED (Neumann-type like zeroGradient),
          then we NEED pRef constraint to make the pressure equation well-posed.

        IMPORTANT (reset_env safety):
        This method is called on the **same** mesh dict during both the initial load
        and every subsequent ``reset_env`` cycle.  Because the mesh dict is reused,
        ``mesh["fvconfig"]["fvSolution"]["pRefCell_valid_mask"]`` may carry a stale
        value from the previous round.  To be safe we **always** reset it to a
        known default at the top of this function before re-evaluating the BCs.

        Args:
            mesh: Mesh dictionary containing fvconfig and bcpatch
            bcpatch: Dictionary containing BC instances by type and variable
            fvconfig: Configuration dictionary

        Returns:
            Updated mesh dictionary with pRefCell_valid_mask set appropriately
        """
        solver_type = fvconfig["solver"]["type"]

        # --- Defensive reset ---------------------------------------------------
        # Clear any stale value left over from a previous init_env / reset_env
        # cycle.  This guarantees that even if we hit an early-return path the
        # mask is in a safe (disabled) state rather than a leftover from last time.
        mesh["fvconfig"]["fvSolution"]["pRefCell_valid_mask"] = False
        # -----------------------------------------------------------------------

        # Determine which pressure variable to check based on solver type
        if solver_type in ("buoyantboussinesqpicoext", "buoyantboussinesqpicocmpt"):
            pressure_var = "p_rgh"
        elif solver_type in ["picoext", "pico_old", "picocmpt"]:
            pressure_var = "p"
        else:
            # For other solvers (rhocoext, diffusionext), no pRef constraint needed.
            # pRefCell_valid_mask is already False from the defensive reset above.
            return mesh

        # Check if pressure has any FIRST (Dirichlet) BC
        has_dirichlet_pressure_bc = False

        # Check FIRST type BCs only
        for bc_instance in bcpatch.get(BCType.FIRST, []):
            if bc_instance.varType in [VARType.PRESSURE, VARType.PRESSURE_RGH]:
                has_dirichlet_pressure_bc = True
                logger.info(f"Found FIRST (Dirichlet) BC for pressure at patch '{bc_instance.patch_name}' - pRef constraint NOT needed")
                break

        # NOTE: The MIXED-BC pressure-anchor scan (is_pressure_anchor attribute) has
        # been removed. No BC class currently sets is_pressure_anchor — the only
        # candidate (Naturaloutlet) raises NotImplementedError at __init__. If a
        # future MIXED BC needs to act as a pressure anchor, add the attribute and
        # restore this loop.

        # Update pRefCell_valid_mask based on detection result
        if has_dirichlet_pressure_bc:
            # Pressure is already constrained — keep pRefCell_valid_mask = False
            # (already set by the defensive reset above, but explicit for clarity)
            mesh["fvconfig"]["fvSolution"]["pRefCell_valid_mask"] = False
            logger.info("Auto-detected: Pressure has Dirichlet BC, disabling pRef constraint")
        else:
            # No Dirichlet / anchor pressure BC found — enable pRef if configured
            if mesh["fvconfig"]["fvSolution"].get("pRefCell", -1) >= 0:
                mesh["fvconfig"]["fvSolution"]["pRefCell_valid_mask"] = True
                logger.info(f"Auto-detected: No Dirichlet pressure BC found, enabling pRef constraint (mean pressure mode)")
            else:
                # pRefCell not configured, keep it disabled but warn
                mesh["fvconfig"]["fvSolution"]["pRefCell_valid_mask"] = False
                logger.warning(f"Auto-detected: No Dirichlet pressure BC found, but pRefCell not configured in fvSolution. "
                             f"Pressure may drift. Consider setting pRefCell >= 0 in fvconfig.toml.")

        return mesh
    
    def _resolve_param_value(self, mesh, value, default=None, key=None):
        """
        Resolve a theta_pde entry that may be:
          - a parametric reference "$name" (requires parametric_manager)
          - a dict {'value_type': 'constant'/'arithmetic'/'choices', 'val': ...}
          - a plain scalar (int/float/list)

        Returns the concrete numeric value (the same type the solver
        branches currently read via `theta_pde[key]["val"]`).

        `key` (the theta_pde key name, e.g. "mu") gives sweep-type dict specs
        a STABLE sampler name in the parametric_manager, so the sampler and
        its rotation pool survive across reset_env invocations even though
        theta_pde is re-derived (deep-copied) each time.
        """
        # Missing key -> fall back to the caller-supplied default immediately,
        # so downstream arithmetic doesn't receive None.
        if value is None:
            return default

        pm = mesh.get("parametric_manager", None)

        # String reference: "$nu_sweep"
        if isinstance(value, str):
            if pm is not None:
                return pm.resolve_value(value)
            raise ValueError(
                f"theta_pde contains parametric reference {value!r} but mesh has "
                f"no parametric_manager. Make sure fvconfig.toml [parameter] section "
                f"is registered before set_theta_PDE runs."
            )

        # Dict with value_type
        if isinstance(value, dict):
            vt = value.get("value_type", "constant")
            if vt in ("constant", "uniform"):
                return value.get("val", default)
            # arithmetic / choices — delegate to parametric_manager via a synthetic name.
            # The name must NOT depend on id(value): theta_pde dicts are fresh
            # deep copies on every reset, and an id-keyed name would register a
            # brand-new same-seeded sampler each time (identical shuffle ->
            # identical first pop -> the sweep silently pins to one value).
            if pm is not None:
                synth_name = f"__theta_pde_{key}" if key else f"__theta_pde_{id(value)}"
                if not pm.has_parameter(synth_name):
                    pm.register_from_toml({synth_name: value})
                return pm.get_value(synth_name)
            raise ValueError(
                f"theta_pde entry with value_type={vt!r} requires a parametric_manager. "
                f"Current value: {value}"
            )

        # Plain scalar (legacy path — some fvconfig files store `L = 1.0` directly)
        return value

    @staticmethod
    def _estimate_inlet_velocity(mesh):
        """Extract characteristic velocity from BC config for Re calculation.

        Searches parametric_manager for 'inlet_velocity', then falls back to
        scanning U boundary patches for a fixedValue with nonzero magnitude.
        Returns velocity magnitude (scalar) or 1.0 if nothing found.
        """
        pm = mesh.get("parametric_manager", None)
        if pm is not None:
            try:
                v = pm.get_value("inlet_velocity")
                if isinstance(v, (int, float)):
                    return abs(float(v))
                if isinstance(v, (list, tuple)):
                    return float(sum(x**2 for x in v) ** 0.5)
            except (KeyError, ValueError):
                pass

        bc_info = mesh.get("boundary_info", {})
        u_bc = bc_info.get("U", {})
        for patch_name, patch_cfg in u_bc.items():
            if not isinstance(patch_cfg, dict):
                continue
            if patch_cfg.get("type", "") != "fixedValue":
                continue
            val_cfg = patch_cfg.get("value", {})
            if isinstance(val_cfg, dict):
                val = val_cfg.get("val", None)
            else:
                val = val_cfg
            if val is None:
                continue
            if isinstance(val, (list, tuple)):
                mag = float(sum(x**2 for x in val if isinstance(x, (int, float))) ** 0.5)
                if mag > 0:
                    return mag
            elif isinstance(val, (int, float)) and abs(val) > 0:
                return abs(float(val))
        return 1.0

    def set_theta_PDE(self,mesh):
        """
        Set the theta_PDE tensor based on the solver type and PDE parameters.

        Args:
            mesh: Mesh dictionary containing fvconfig and other mesh data

        Returns:
            mesh: Updated mesh dictionary with theta_PDE tensor
        """
        fvconfig = mesh["fvconfig"]
        solver = fvconfig["solver"]
        solver_type = solver["type"]

        # Re-derive theta_pde from the pristine user spec on every invocation.
        # This function writes computed values (Re, Re_config, Ra, Pr, ...)
        # back into theta_pde; without the reset below, a second invocation
        # (reset_env) reads its own previous output as if it were user input.
        # Concretely: the numeric Re computed at the startup draw satisfied
        # the isinstance(re_config, (int, float)) branch on every reset, so
        # the exported Re stayed frozen at the startup value while mu kept
        # being re-sampled (VTM filename-Re bug).
        theta_pde_raw = fvconfig.get("theta_pde_raw")
        if theta_pde_raw is None:
            # Mesh produced by an older load path without the pristine
            # snapshot: take one now, dropping the keys only this function
            # ever writes (none of them are legitimate user spec keys).
            theta_pde_raw = copy.deepcopy(fvconfig["theta_pde"])
            for _k in ("Re", "Re_config", "Ra", "_resolved_mu", "_resolved_rho"):
                theta_pde_raw.pop(_k, None)
            fvconfig["theta_pde_raw"] = theta_pde_raw
        # clear()+update() keeps the dict object identity so references held
        # elsewhere (BC instances re-fetch fvconfig["theta_pde"] lazily, but
        # belt-and-suspenders) always see the refreshed content.
        theta_pde = fvconfig["theta_pde"]
        theta_pde.clear()
        theta_pde.update(copy.deepcopy(theta_pde_raw))

        if solver_type in ("picoext", "pico_old", "picocmpt"):
            rho = self._resolve_param_value(mesh, theta_pde.get("rho"), default=1.0, key="rho")
            mu = self._resolve_param_value(mesh, theta_pde.get("mu"), default=1e-3, key="mu")
            dt = self._resolve_param_value(mesh, theta_pde.get("dt"), default=0.01, key="dt")
            L = self._resolve_param_value(mesh, theta_pde.get("L"), default=1.0, key="L")
            source = self._resolve_param_value(mesh, theta_pde.get("source"), default=0.0, key="source")
            # Resolved values for downstream diagnostics (refreshed every invocation).
            mesh["fvconfig"]["theta_pde"]["_resolved_mu"] = mu
            mesh["fvconfig"]["theta_pde"]["_resolved_rho"] = rho

            # Handle Re configuration: if Re is a dict (e.g., {vel="boundaryfield.inlet"}),
            # preserve the config for later update in _update_re_from_patch_velocity
            re_config = theta_pde.get("Re", None)
            if isinstance(re_config, dict) and "vel" in re_config:
                mesh["fvconfig"]["theta_pde"]["Re_config"] = re_config
                U_char = self._estimate_inlet_velocity(mesh)
                Re = rho * U_char * L / mu if mu != 0 else float('inf')
            elif isinstance(re_config, (int, float)):
                Re = re_config
            else:
                U_char = self._estimate_inlet_velocity(mesh)
                Re = rho * U_char * L / mu if mu != 0 else float('inf')
            mesh["fvconfig"]["theta_pde"]["Re"] = Re

            # Default PDE coefficients
            unsteady_coeff = 1.0 if theta_pde["unsteady"] else 0.0
            continuity_eq_coeff = 1.0
            convection_coeff = 1.0
            grad_p_coeff = 1.0 / rho if rho != 0 else 1.0
            nu_coeff = mu / rho if rho != 0 and convection_coeff != 0 else mu
            source_term = source / rho if rho != 0 else source

            # Create theta_PDE tensor, 注意这里的维度，需与src/FVsolver/applications/picoext.py（及picocmpt.py）中的theta_PDE展开部分对应
            theta_PDE = torch.tensor(
                [
                    unsteady_coeff,      # 0
                    continuity_eq_coeff, # 1
                    convection_coeff,    # 2
                    grad_p_coeff,        # 3
                    nu_coeff,            # 4
                    source_term,         # 5
                    dt,                  # 6
                ],
                dtype=torch.float32,
            ).view(1, -1)

            mesh["num_channels"] = mesh.get("num_channels", 0) + theta_PDE.shape[1]

        elif solver_type in ("buoyantboussinesqpicoext", "buoyantboussinesqpicocmpt"):
            # Boussinesq approximation parameters
            rho = self._resolve_param_value(mesh, theta_pde.get("rho"), default=1.0, key="rho")  # Reference density ρ_0
            mu = self._resolve_param_value(mesh, theta_pde.get("mu"), default=1e-3, key="mu")   # Dynamic viscosity μ
            dt = self._resolve_param_value(mesh, theta_pde.get("dt"), default=0.01, key="dt")   # Time step
            L = self._resolve_param_value(mesh, theta_pde.get("L"), default=1.0, key="L")      # Characteristic length
            source = self._resolve_param_value(mesh, theta_pde.get("source"), default=0.0, key="source")  # Momentum source term
            mesh["fvconfig"]["theta_pde"]["_resolved_mu"] = mu
            mesh["fvconfig"]["theta_pde"]["_resolved_rho"] = rho

            # Boussinesq-specific parameters — resolved via _resolve_param_value
            # so that $ref parametric strings (e.g., "$alpha_param") are handled.
            beta = self._resolve_param_value(mesh, theta_pde.get("beta"), default=0.0, key="beta")
            TRef = self._resolve_param_value(mesh, theta_pde.get("TRef"), default=300.0, key="TRef")
            Pr = self._resolve_param_value(mesh, theta_pde.get("Pr"), default=0.71, key="Pr")
            Cp = self._resolve_param_value(mesh, theta_pde.get("Cp"), default=1005.0, key="Cp")
            g = self._resolve_param_value(mesh, theta_pde.get("g"), default=[0.0, 0.0, -9.81], key="g")
            heat_source = self._resolve_param_value(mesh, theta_pde.get("heat_source"), default=0.0, key="heat_source")
            alpha_cfg = self._resolve_param_value(mesh, theta_pde.get("alpha"), default=None, key="alpha")
            hRef = self._resolve_param_value(mesh, theta_pde.get("hRef"), default=0.0, key="hRef")

            # Compute dimensionless numbers
            nu = mu / rho  # Kinematic viscosity [m²/s]
            if alpha_cfg is None:
                alpha = nu / Pr if Pr != 0 else 0.0  # Thermal diffusivity [m²/s]
            else:
                alpha = alpha_cfg
            # Handle Re configuration: if Re is a dict (e.g., {vel="boundaryfield.in"}),
            # preserve the config for later update in _update_re_from_patch_velocity
            re_config = theta_pde.get("Re", None)
            if isinstance(re_config, dict) and "vel" in re_config:
                mesh["fvconfig"]["theta_pde"]["Re_config"] = re_config
                U_char = self._estimate_inlet_velocity(mesh)
                Re = rho * U_char * L / mu if mu != 0 else float('inf')
            elif isinstance(re_config, (int, float)):
                Re = re_config
            else:
                U_char = self._estimate_inlet_velocity(mesh)
                Re = rho * U_char * L / mu if mu != 0 else float('inf')

            g_mag = (g[0]**2 + g[1]**2 + g[2]**2) ** 0.5
            Ra = g_mag * beta * L**3 / (nu * alpha) if beta != 0 and alpha != 0 else 0.0  # Rayleigh number (ΔT=1K reference)

            mesh["fvconfig"]["theta_pde"]["Re"] = Re
            mesh["fvconfig"]["theta_pde"]["Ra"] = Ra
            mesh["fvconfig"]["theta_pde"]["Pr"] = Pr

            # PDE coefficients
            unsteady_coeff = 1.0 if theta_pde["unsteady"] else 0.0
            continuity_eq_coeff = 1.0
            convection_coeff = 1.0
            grad_p_coeff = 1.0 # in Boussinesq, pressure gradient not divided by density
            nu_coeff = mu / rho if rho != 0 else mu
            source_term = source / rho if rho != 0 else source

            # Temperature equation coefficients
            alpha_coeff = alpha  # Thermal diffusivity
            heat_source_term = heat_source / (rho * Cp) if rho != 0 else heat_source  # Using Cp from fvconfig

            # Create theta_PDE tensor
            # 注意这里的维度，需与src/FVsolver/applications/buoyantBoussinesqpicoext.py中的theta_PDE展开部分对应
            theta_PDE = torch.tensor(
                [
                    unsteady_coeff,      # 0: Unsteady coefficient for momentum
                    continuity_eq_coeff, # 1: Continuity equation coefficient
                    convection_coeff,    # 2: Convection coefficient
                    grad_p_coeff,        # 3: Pressure gradient coefficient
                    nu_coeff,            # 4: Kinematic viscosity
                    source_term,         # 5: Momentum source term
                    dt,                  # 6: Time step
                    alpha_coeff,         # 7: Thermal diffusivity
                    heat_source_term,    # 8: Heat source term
                    TRef,                # 9: Reference temperature
                    beta,                # 10: Thermal expansion coefficient
                    g[0],                # 11: gravity in x-direction
                    g[1],                # 12: gravity in y-direction
                    g[2],                # 13: gravity in z-direction
                    hRef,                # 14: Reference height for gh calculation
                ],
                dtype=torch.float32,
            ).view(1, -1)

            mesh["num_channels"] = mesh.get("num_channels", 0) + theta_PDE.shape[1]

        elif solver_type == "rhocoext" or str(solver_type).lower() in PLANE_MIRROR_SOLVERS:
            # Compressible Euler/Navier-Stokes parameters
            rho = theta_pde["rho"]["val"]  # Density (for initialization)
            mu = theta_pde["mu"]["val"]    # Dynamic viscosity (0 for Euler)
            dt = theta_pde["dt"]["val"]    # Time step
            L = theta_pde["L"]             # Characteristic length
            source = theta_pde["source"]["val"]  # Source term

            # For compressible flow, we use the actual viscosity
            # For Euler equations, set mu = 0
            nu = mu / rho if rho != 0 else 0.0  # Kinematic viscosity
            U_char = self._estimate_inlet_velocity(mesh)
            Re = rho * U_char * L / mu if mu != 0 else float('inf')

            mesh["fvconfig"]["theta_pde"]["Re"] = Re

            # PDE coefficients
            unsteady_coeff = 1.0 if theta_pde["unsteady"] else 0.0
            continuity_eq_coeff = 1.0
            convection_coeff = 1.0
            grad_p_coeff = 1.0  # For compressible flow, pressure gradient is not divided by density
            nu_coeff = mu  # Use dynamic viscosity directly (set to 0 for Euler)
            source_term = source

            # Create theta_PDE tensor
            # Note: Structure must match the theta_PDE unpacking in the compressible solver
            theta_PDE = torch.tensor(
                [
                    unsteady_coeff,      # 0: Unsteady coefficient
                    continuity_eq_coeff, # 1: Continuity equation coefficient
                    convection_coeff,    # 2: Convection coefficient
                    grad_p_coeff,        # 3: Pressure gradient coefficient
                    nu_coeff,            # 4: Dynamic viscosity (0 for Euler)
                    source_term,         # 5: Source term
                    dt,                  # 6: Time step
                ],
                dtype=torch.float32,
            ).view(1, -1)

            mesh["num_channels"] = mesh.get("num_channels", 0) + theta_PDE.shape[1]

        elif solver_type == "diffusionext":
            # Diffusion/Poisson equation parameters
            mu = theta_pde["mu"]["val"]  # Diffusion coefficient
            dt = theta_pde["dt"]["val"]  # Time step
            L = theta_pde["L"]  # Characteristic length
            source = theta_pde["source"]["val"]  # Source term

            mesh["fvconfig"]["theta_pde"]["Re"] = 0.0  # Not applicable for diffusion

            # PDE coefficients for diffusion equation
            # ∂φ/∂t = μ∇²φ + S
            unsteady_coeff = 1.0 if theta_pde["unsteady"] else 0.0
            diffusion_coeff = mu  # Diffusion coefficient μ
            source_term = source  # Source term S

            # Create theta_PDE tensor
            # Note: Structure must match the theta_PDE unpacking in the diffusion-only solver
            theta_PDE = torch.tensor(
                [
                    unsteady_coeff,      # 0: Unsteady coefficient
                    diffusion_coeff,     # 1: Diffusion coefficient μ
                    source_term,         # 2: Source term S
                    dt,                  # 3: Time step
                ],
                dtype=torch.float32,
            ).view(1, -1)

            mesh["num_channels"] = mesh.get("num_channels", 0) + theta_PDE.shape[1]

        else:
            raise NotImplementedError(f"Solver_type {solver_type} not implemented")

        mesh["theta_PDE"] = theta_PDE

        return mesh

    def _prepare_cyclic_boundary_geometry(self, mesh):
        return prepare_cyclic_boundary_geometry(mesh)

    def _generate_ghost_positions_for_rhoco(self, mesh):
        """
        Generate ghost cell positions for all boundary patches when using the
        compressible solver (rhoco).

        For non-cyclic boundaries of ``rhocoext``, ghost cell centers are mirrored
        across the face center: x_g = 2*x_f - x_owner.  For the solvers of
        ``PLANE_MIRROR_SOLVERS`` (rhocoinviscompt, rhocoviscompt) they are the owner
        centre mirrored across the plane of the face, x_g = x_o + 2 ((x_f - x_o) . n) n,
        so that the owner-to-ghost direction is the face normal (the Gen-FVGN-3D
        construction these ports were written against).  Cyclic patches already have
        their ghost positions computed during mesh parsing and are left untouched.
        """
        solver_type = mesh["fvconfig"]["solver"].get("type", "")
        plane = str(solver_type).lower() in PLANE_MIRROR_SOLVERS
        if solver_type != "rhocoext" and not plane:
            return mesh

        cpd_cell_pos = mesh["cpd|cell_pos"].clone()
        neighbor_cpd_cell = mesh["cpd|neighbor_cell"].long()
        face_pos = mesh["face|face_pos"]
        face_normal = mesh["face|face_normal"] if plane else None
        face_type = mesh.get("face|face_type", None)
        patch_dict = mesh.get("patch_dict", {})

        for patch_name, patch_info in patch_dict.items():
            start_idx_face = int(patch_info["start_idx_face"])
            end_idx_face = int(patch_info["end_idx_face"])
            if start_idx_face >= end_idx_face:
                continue

            # Skip explicitly empty patches (e.g., 2D extruded planes)
            geom_type = str(patch_info.get("geom_type", "")).lower()
            if geom_type == "empty":
                continue

            face_slice = slice(start_idx_face, end_idx_face)
            mask = None
            if face_type is not None:
                cur_type = face_type[face_slice]
                mask = (cur_type != FaceType.CYCLIC) & (cur_type != FaceType.EMPTY)
                mask = mask.view(-1)

            owner_cells = neighbor_cpd_cell[0, face_slice]
            ghost_cells = neighbor_cpd_cell[1, face_slice]
            patch_face_pos = face_pos[face_slice]
            patch_normal = face_normal[face_slice] if plane else None

            if mask is not None:
                if not torch.any(mask):
                    continue
                owner_cells = owner_cells[mask]
                ghost_cells = ghost_cells[mask]
                patch_face_pos = patch_face_pos[mask]
                if plane:
                    patch_normal = patch_normal[mask]

            owner_pos = cpd_cell_pos[owner_cells]
            if plane:
                nrm = patch_normal.to(owner_pos.dtype)
                nrm = nrm / nrm.norm(dim=-1, keepdim=True).clamp_min(1e-30)
                dist = ((patch_face_pos.to(owner_pos.dtype) - owner_pos) * nrm).sum(-1, keepdim=True)
                ghost_pos = owner_pos + 2.0 * dist * nrm
            else:
                ghost_pos = 2.0 * patch_face_pos - owner_pos
            cpd_cell_pos[ghost_cells] = ghost_pos

        mesh["cpd|cell_pos"] = cpd_cell_pos
        logger.info("Generated mirrored ghost cell positions for all non-cyclic patches (rhoco solver)")
        return mesh

    def _update_gnn_csr_format(self, mesh, neighbor_cpd_cell, num_cpd_cells):
        """
        Update CSR format for GNN message passing after EMPTY edge filtering.
        
        This rebuilds the CSR format data structures after edges connected to EMPTY
        cells have been removed. The CSR format enables efficient segment_csr operations.
        
        NOTE: We store edge_counts instead of indptr for easier batching.
        At runtime: indptr = F.pad(counts.cumsum(0), (1, 0))
        
        Args:
            mesh: Mesh dictionary
            neighbor_cpd_cell: Filtered neighbor_cpd_cell [2, num_edges]
            num_cpd_cells: Total number of compound cells
            
        Returns:
            Updated mesh dictionary with new CSR format data
        """
        num_edges = neighbor_cpd_cell.shape[1]
        
        # Build two-way edges: forward + reverse
        twoway_edges = torch.cat([
            neighbor_cpd_cell,              # [2, num_edges] - forward
            neighbor_cpd_cell.flip(0)       # [2, num_edges] - reverse
        ], dim=1)  # [2, 2*num_edges]
        
        sources = twoway_edges[0]
        receivers = twoway_edges[1]
        
        # Sort edges by receiver (target) node for CSR format
        sort_idx_raw = receivers.argsort(stable=True)
        sorted_sources = sources[sort_idx_raw]
        sorted_receivers = receivers[sort_idx_raw]
        
        # Map sort_idx to interleaved twoway_edge_attr structure
        # In forward(), twoway_edge_attr is created as [e0_f, e0_r, e1_f, e1_r, ...]
        # Mapping: old_idx -> new_idx
        # - if old_idx < num_edges: new_idx = 2 * old_idx (forward edge)
        # - if old_idx >= num_edges: new_idx = 2 * (old_idx - num_edges) + 1 (reverse edge)
        is_forward = sort_idx_raw < num_edges
        sort_idx = torch.where(
            is_forward,
            2 * sort_idx_raw,
            2 * (sort_idx_raw - num_edges) + 1
        )
        
        # Count edges per receiver node (store counts instead of indptr for easier batching)
        receiver_counts = torch.bincount(sorted_receivers, minlength=num_cpd_cells)
        
        # Store counts instead of indptr - enables simple concatenation during batching
        mesh["gnn|edge_counts"] = receiver_counts
        mesh["gnn|edge_indices"] = sorted_sources
        mesh["gnn|edge_sort_idx"] = sort_idx
        
        logger.debug(f"Updated GNN CSR format: {num_edges} edges -> {twoway_edges.shape[1]} two-way edges")
        
        return mesh

    def calc_WLSQ_A_B_normal_matrix(self, mesh, order):

        if order == "GG":
            order = "1st" # 临时替换
            logger.warning("Green-Gauss gradient method is not supported, fallback to WLSQ and order is set to 1st")

        if not "A_cell_to_cell" in mesh.keys():

            """>>> compute WLSQ cell to cell left A matrix >>>"""
            cpd_cell_pos = mesh["cpd|cell_pos"]
            neighbor_cpd_cell = mesh["cpd|neighbor_cell"].long() # 紧致模板
            neighbor_cpd_cell_x = mesh["cpd|neighbor_cell_x"].long() # 扩展模板
            cell_type = mesh["cpd|cell_type"].long().squeeze()

            ''' >>> [stencil] khops — 加载期扩展模板选择 (2026-07-18) >>> '''
            # khops 在加载期生效: khops==1(默认)把扩展模板直接重建为 1-hop 面
            # 邻接(与解析器 khop=1 的 torch.unique(sort) 输出一致), 因此任何
            # 既有 H5(无论解析时烘焙的 khop)都能免重解析地跑 khops=1;
            # khops>=2 保留 H5 存储的解析期宽模板。守卫: 高阶 gradSchemes 的
            # WLSQ 拟合需要宽模板; diffusionext 的 Dirichlet 边界扩散通量消费
            # ghost 梯度(边界面梯度插值取 ghost 侧), 与统一梯度的 ghost 零梯度
            # 语义不兼容 — 两者均回退到存储模板并警告。
            _fvconfig = mesh.get("fvconfig", {}) or {}
            _khops = int(_fvconfig.get("stencil", {}).get("khops", 1))
            _solver_type = str(_fvconfig.get("solver", {}).get("type", "")).lower()
            if _khops == 1:
                if order in ("2nd", "3rd", "4th"):
                    logger.warning(
                        f"[stencil] khops=1 cannot support gradSchemes='{order}' "
                        "(higher-order WLSQ fit needs a wider stencil); keeping "
                        "the H5's stored parse-time stencil"
                    )
                elif _solver_type == "diffusionext":
                    logger.warning(
                        "[stencil] khops=1 is unsupported for diffusionext (its "
                        "boundary diffusion flux consumes ghost WLSQ gradients); "
                        "keeping the H5's stored parse-time stencil"
                    )
                else:
                    # torch.unique(dim=1) on the (2, E) pairs, by a 1-D unique of the key a * N + b (the same
                    # lexicographic order and result; dim=1 unique costs ~5.6 s on 7e5 cells on the CPU)
                    _srt = neighbor_cpd_cell.sort(0)[0].long()
                    _N = int(_srt.max()) + 1 if _srt.numel() else 1
                    _key = torch.unique(_srt[0] * _N + _srt[1])
                    _nb1 = torch.stack([_key // _N, _key % _N]).to(neighbor_cpd_cell.dtype)
                    if _nb1.shape[1] != neighbor_cpd_cell_x.shape[1]:
                        logger.info(
                            f"[stencil] khops=1: extended WLSQ stencil rebuilt as "
                            f"1-hop face adjacency ({neighbor_cpd_cell_x.shape[1]} "
                            f"-> {_nb1.shape[1]} edges; stored parse-time stencil "
                            "ignored)"
                        )
                    neighbor_cpd_cell_x = _nb1
            ''' <<< [stencil] khops <<< '''

            ''' >>> 排除边界面元与边界面元的连接边 >>> '''
            # # 注意后续在WLSQ梯度重构的时候，不要让边界面元与边界面元出现连接边: 这里是打补丁，后续需要优化
            # mask_cell_interior = (cell_type == FaceType.NORMAL)  # NORMAL
            # mask_cell_boundary = ~mask_cell_interior
            # mask_valid_nb_cell_x = ~(mask_cell_boundary[neighbor_cpd_cell_x[0]] & mask_cell_boundary[neighbor_cpd_cell_x[1]])
            # neighbor_cpd_cell_x = neighbor_cpd_cell_x[:,mask_valid_nb_cell_x]
            ''' <<< 排除边界面元与边界面元的连接边 <<< '''
            
            ''' >>> 排除EMPTY面元的连接边（仅对扩展模板neighbor_cpd_cell_x） >>> '''
            # 注意：不能过滤紧致模板 neighbor_cpd_cell，因为它必须与 face|face_pos 等面数据保持 1:1 对齐。
            # EMPTY face 的贡献在 FVInterpolation 中通过 mask_empty_face 掩码排斥。
            # 仅对扩展模板 neighbor_cpd_cell_x 进行过滤（用于 WLSQ 梯度重构，不需要与面数据对齐）。
            mask_empty_cpd_cell = (cell_type == FaceType.EMPTY)
            mask_neighbor_cpd_cell_x = ~(mask_empty_cpd_cell[neighbor_cpd_cell_x[0]] | mask_empty_cpd_cell[neighbor_cpd_cell_x[1]])
            neighbor_cpd_cell_x = neighbor_cpd_cell_x[:,mask_neighbor_cpd_cell_x]
            mesh["cpd|neighbor_cell_x"] = neighbor_cpd_cell_x
            ''' <<< 排除EMPTY面元的连接边 <<< '''
            
            # # 尝试排斥出CYCLIC的extend模板, 然后拼接紧致的Cyclic patch邻居模板
            # mask_cyclic_face = (mesh["face|face_type"] == FaceType.CYCLIC)
            # mask_cyclic_cpd_cell = torch.zeros(cpd_cell_pos.shape[0], dtype=torch.bool)
            # mask_valid_neighbor_cpd_x = ~(mask_cyclic_cpd_cell[neighbor_cpd_cell_x[0]] | mask_cyclic_cpd_cell[neighbor_cpd_cell_x[1]])
            # neighbor_cpd_cell_x = neighbor_cpd_cell_x[:,mask_valid_neighbor_cpd_x]
            # # 只有当存在cyclic face时才拼接cyclic邻居模板
            # # 注意：neighbor_cpd_cell的第二维是edge数量，需要用对应的cyclic edge mask而不是face mask
            # if mask_cyclic_face.any():
            #     # 获取cyclic cell的邻居关系（基于cell type而不是face type）
            #     mask_cyclic_cpd_cell_type = (cell_type == FaceType.CYCLIC)
            #     mask_cyclic_neighbor = (mask_cyclic_cpd_cell_type[neighbor_cpd_cell[0]] | mask_cyclic_cpd_cell_type[neighbor_cpd_cell[1]])
            #     if mask_cyclic_neighbor.any():
            #         neighbor_cpd_cell_x = torch.cat((neighbor_cpd_cell_x, neighbor_cpd_cell[:,mask_cyclic_neighbor]), dim=1)
            
            # # 在这里组装紧致的neighbor_cpd_cell_x模板=内部紧致模板+边界面元扩展模板
            # mask_empty_cpd_cell = (cell_type == FaceType.EMPTY)
            # mask_boundary_cpd_cell = ~(cell_type == FaceType.NORMAL)
            
            # # 内部的紧致模板
            # mask_inerior_neighbor_cpd_cell = ~(mask_boundary_cpd_cell[neighbor_cpd_cell[0]] | mask_boundary_cpd_cell[neighbor_cpd_cell[1]]) 
            # interior_neighbor_cpd_cell = neighbor_cpd_cell[:,mask_inerior_neighbor_cpd_cell]
        
            # # 只保留边界面元中非EMPTY的扩展模板
            # mask_boundary_non_empty_cpd_cell = (mask_boundary_cpd_cell&(~mask_empty_cpd_cell))
            # mask_boundary_neighbor_cpd_cell_x = (mask_boundary_non_empty_cpd_cell[neighbor_cpd_cell_x[0]] | mask_boundary_non_empty_cpd_cell[neighbor_cpd_cell_x[1]])
            # boundary_neighbor_cpd_cell_x = neighbor_cpd_cell_x[:,mask_boundary_neighbor_cpd_cell_x]
            
            # compact_neighbor_cpd_cell_x = torch.cat((interior_neighbor_cpd_cell, boundary_neighbor_cpd_cell_x), dim=1)
            # # compact_neighbor_cpd_cell_x = torch.unique(compact_neighbor_cpd_cell_x.sort(dim=0)[0],dim=1) # 去重并排序
            # mesh["cpd|neighbor_cell_x"] = compact_neighbor_cpd_cell_x
             
            """ >>> compute WLSQ cell to cell left A matrix >>> """
            (A_cell_to_cell, two_way_B_cell_to_cell) = compute_normal_matrix(
                order=order,
                node_pos=cpd_cell_pos,
                edge_index=neighbor_cpd_cell_x, # khops=1(默认)时为重建后的1-hop面邻接; khops>=2时为H5存储的解析期宽模板(1阶邻居点+构成共点的单元的所有点)
            )

            gdt = geometry_precision.torch_dtype(mesh)
            mesh["A_cell_to_cell"] = A_cell_to_cell.to(gdt)
            mesh["single_B_cell_to_cell"] = (
                torch.chunk(two_way_B_cell_to_cell, 2, dim=0)[0]
            ).to(gdt)
            """ <<< compute WLSQ cell to cell right B matrix<<< """

        return mesh

    def __call__(
        self,
        mesh,
        params=None
    ):

        mesh = self._prepare_cyclic_boundary_geometry(mesh)

        # Initialize fields and fvpatch
        mesh, phi_cpd_cell = self.init_env(
            mesh,
        )

        # Update num_channels to include Fourier positional encoding
        fourier_num_freqs = getattr(params, 'fourier_num_freqs', 0) if params else 0
        fourier_dim = 3 * 2 * fourier_num_freqs if fourier_num_freqs > 0 else 0
        mesh["num_channels"] += fourier_dim
        mesh["fourier_num_freqs"] = fourier_num_freqs

        mesh = self.set_theta_PDE(
            mesh,
        )

        # Regions / zones / sources of a multi-region case (schema v2), resolved per query like theta_PDE
        mesh = region_physics.apply(mesh, self._resolve_param_value)

        mesh = self._generate_ghost_positions_for_rhoco(mesh)

        mesh = self.calc_WLSQ_A_B_normal_matrix(mesh, order = mesh["fvconfig"]["fvSchemes"]["gradSchemes"])

        # Update CSR format for GNN message passing.
        # Note: neighbor_cpd_cell is NOT filtered for EMPTY edges — it must stay
        # aligned 1:1 with face data (face_pos, face_type, etc.).
        # EMPTY contributions are excluded at runtime via mask_empty_face in FVInterpolation.
        mesh = self._update_gnn_csr_format(
            mesh,
            mesh["cpd|neighbor_cell"],
            mesh["cpd|cell_pos"].shape[0]
        )

        return mesh, phi_cpd_cell

class H5CFDdataset(Dataset):
    def __init__(self, params, case_list):
        super().__init__()

        self.case_list = case_list
        self.params = params
        # Pending per-case parameter overrides, set via set_pending_overrides()
        # and consumed once per __getitem__ call.  Used by the multi-slot pool
        # loader to inject per-case explicit parametric values (e.g., Re-derived
        # inlet velocity) so each slot reflects its own case_name.
        self._pending_param_overrides: Optional[dict] = None
        # Global slot index for the next __getitem__, injected by the mmap
        # pool.  None = no injection, keeping direct callers on the old rule.
        self._pending_global_slot_index: Optional[int] = None

    def set_pending_overrides(self, overrides: Optional[dict]) -> None:
        """Inject per-case parameter overrides for the next __getitem__ call.

        Overrides are consumed (cleared) after being applied once so they
        never leak to subsequent calls.
        """
        self._pending_param_overrides = overrides

    def set_pending_global_slot_index(self, global_idx: Optional[int]) -> None:
        """Name the global slot the next __getitem__ builds; consumed once."""
        self._pending_global_slot_index = global_idx

    def __getitem__(self, index):
        h5_path = self.case_list[index]
        # The sidecars (patchinfo.toml, fvconfig.toml, bc/) describe THIS mesh, so
        # they are read from the directory that holds it -- the one --dataset_dir
        # names, not from <case_root>/h5/ with the
        # directory name 'h5' hardcoded, so any case parsed into a differently named
        # directory was silently served a sibling's table (the periodic-hills fine
        # grid got the coarse grid's; see validate_patch_table and
        # docs/research/2026-08-16-mwi-refinement-singularity.md).
        data_dir = os.path.dirname(h5_path)
        case_path = os.path.dirname(data_dir)
        # Legacy layout: a directory that holds only the .h5 (e.g.
        # experiments/LDC/ODIL/jfnk/warm_start/) relied on the old detour to reach
        # <case_root>/h5/. Keep it loadable, but say so -- and validate_patch_table
        # below now decides whether the table it reaches actually fits this mesh.
        sidecar_dir = data_dir
        if not os.path.exists(os.path.join(data_dir, "patchinfo.toml")):
            legacy_dir = os.path.join(case_path, "h5")
            if os.path.exists(os.path.join(legacy_dir, "patchinfo.toml")):
                logger.warning(
                    f"No patchinfo.toml next to {h5_path}; falling back to the "
                    f"sibling directory {legacy_dir}. Give this directory its own "
                    f"patchinfo.toml/fvconfig.toml/bc (symlinks are fine) -- the "
                    f"fallback serves whatever mesh that directory was parsed for."
                )
                sidecar_dir = legacy_dir
        h5_file = h5py.File(h5_path, "r")

        # Validate case_name: it propagates into VTK filenames, slot meta.json,
        # and the data-driven labels manifest lookup key, so a garbage value
        # silently corrupts traceability and breaks data_driven training.
        # Known-bad patterns seen in the wild:
        #   - missing attr entirely (old / foreign h5 files)
        #   - empty string
        #   - "_tmp_parse_case" (generated when parse_openfoam was run against
        #     a staging copy of the OpenFOAM case placed in a _tmp* dir).
        # Raise loudly rather than fall back to the h5 filename — silent
        # recovery hides a real bug in whatever produced the h5.
        if "case_name" not in h5_file.attrs:
            raise ValueError(
                f"H5 file {h5_path} has no 'case_name' root attribute. "
                f"Re-run parse_openfoam_refactored.py with a proper --case_name, "
                f"or patch the attr in place with h5py."
            )
        case_name = h5_file.attrs["case_name"]
        if isinstance(case_name, bytes):
            case_name = case_name.decode("utf-8")
        if not isinstance(case_name, str) or not case_name.strip():
            raise ValueError(
                f"H5 file {h5_path} has invalid case_name={case_name!r} "
                f"(must be a non-empty string). Re-parse the mesh with a "
                f"proper case name."
            )
        if case_name.startswith("_tmp"):
            raise ValueError(
                f"H5 file {h5_path} has placeholder case_name={case_name!r} "
                f"— this was produced by parse_openfoam running on a temporary "
                f"staging directory. Patch the attribute with h5py, for example:\n"
                f"  import h5py\n"
                f"  with h5py.File({h5_path!r}, 'r+') as f:\n"
                f"      f.attrs['case_name'] = 'cylinder'  # or the real case id\n"
                f"or re-run parse_openfoam_refactored.py against the real case "
                f"directory (not a _tmp copy)."
            )

        mesh = {"case_name": case_name}
        mesh["case_path"] = case_path
        mesh["sidecar_dir"] = sidecar_dir
        # A file parsed with float64 geometry (root attribute geometry_dtype = "float64") keeps it float64 through
        # the loader (Utils/geometry_precision.py); without the attribute the mesh dictionary has no such key.
        if geometry_precision.of_file(h5_file) == "float64":
            mesh[geometry_precision.MESH_KEY] = "float64"

        # 实例化网格transform
        transform = CFDmesh(self.params)
        mesh["transform"] = transform
        
        ''' >>> Load fvpatch_dict  >>> '''
        from FVdomain.SetBC.FvPatch import fvPatch
        patch_dict_path = os.path.join(sidecar_dir, "patchinfo.toml")
        if os.path.exists(patch_dict_path):
            with open(patch_dict_path, "rb") as f:
                patch_dict = tomllib.load(f)        # native types (tomlkit was ~14x slower; same result)
            # Convert tomlkit objects to native Python types
            patch_dict = toml_to_native(patch_dict)
            # Refuse a table belonging to another grid BEFORE anything consumes it:
            # every mesh in this process passes through here, and the patch spans
            # are what define the ghost cells.
            validate_patch_table(
                patch_dict,
                num_cells=h5_file["cell|cell_pos"].shape[0],
                num_faces=h5_file["face|face_pos"].shape[0],
                num_cpd_cells=h5_file["cpd|cell_pos"].shape[0],
                patch_table_path=patch_dict_path,
                h5_path=h5_path,
            )
            mesh["fvpatch"] = fvPatch().register(patch_dict["patch"])
            mesh["patch_dict"] = mesh["fvpatch"].patch_dict # register()函数会添加patch_idx属性
        else:
            raise FileNotFoundError(f"Patch list not found at {patch_dict_path}")
        ''' <<< Load fvpatch_dict <<< '''
        
        ''' >>> load fvconfig >>> '''
        # 将全局的fvconfig附加到当前mesh
        fvconfig_path = os.path.join(sidecar_dir, "fvconfig.toml")
        with open(fvconfig_path, "rb") as f:
            fvconfig = tomllib.load(f)

        # Convert tomlkit objects to native Python types for torch.compile compatibility
        fvconfig = toml_to_native(fvconfig)
        mesh["fvconfig"] = fvconfig
        # Pristine snapshot of the user's [theta_pde] spec. set_theta_PDE
        # re-derives the runtime theta_pde from this copy on EVERY invocation
        # (initial load and each reset_env), so values it computes and writes
        # back (Re, Re_config, Ra, ...) can never be mistaken for user input
        # on a later invocation. Mirrors the boundary_info_raw idiom below.
        fvconfig["theta_pde_raw"] = copy.deepcopy(fvconfig["theta_pde"])
        # Set pRefCell to -1 if not defined (sentinel value for "no reference pressure")
        if mesh["fvconfig"]["fvSolution"].get("pRefCell", -99) == -1:
            mesh["fvconfig"]["fvSolution"]["pRefCell_valid_mask"] = False
        elif mesh["fvconfig"]["fvSolution"].get("pRefCell", -99) >= 0:
            mesh["fvconfig"]["fvSolution"]["pRefCell_valid_mask"] = True
        else:
            mesh["fvconfig"]["fvSolution"]["pRefCell_valid_mask"] = False
            mesh["fvconfig"]["fvSolution"]["pRefCell"] = -1
            mesh["fvconfig"]["fvSolution"]["pRefValue"] = 0.0
        ''' <<< load fvconfig <<< '''

        ''' >>> Load boundary condition information >>> '''
        bc_dir = os.path.join(sidecar_dir, "bc")
        boundary_info = {}
        boundary_info_raw = {}

        # Seed keyed on the GLOBAL slot index, so case k draws the same
        # operating point at any world size (test_reset_schedule_invariance).
        # Ranks still differ: interleaving gives rank r the indices r, r+W, ...
        _pending_global = getattr(self, "_pending_global_slot_index", None)
        if _pending_global is not None:
            case_seed = case_seed_for_global_slot(_pending_global)
            self._pending_global_slot_index = None
        else:
            # ODIL / coupled Newton / rollout build H5CFDdataset directly and
            # inject nothing; unchanged from the old formula at RANK=0.
            _ddp_rank = int(os.environ.get("RANK", 0))
            case_seed = 777 + _ddp_rank * 1000 + CFDmesh._case_counter
        CFDmesh._case_counter += 1
        parametric_manager = ParametricManager(seed=case_seed)

        # Register fvconfig-level parameters (e.g., parametric mu/nu for Re sweeps).
        # These are sampled at set_theta_PDE time via _resolve_param_value.
        if isinstance(fvconfig.get("parameter"), dict):
            parametric_manager.register_from_toml(fvconfig["parameter"])
            logger.info(
                f"Registered fvconfig-level parameters: {list(fvconfig['parameter'].keys())}"
            )

        # The summary log below runs even when bc/ is absent, so the names it
        # references must always be bound.
        loaded_files = set()
        solver_type = fvconfig.get("solver", {}).get("type", "<unset>")
        required_vars = SOLVER_VARS.get(solver_type)

        if os.path.exists(bc_dir):
            # Get solver type to determine required variables
            solver_type = fvconfig["solver"]["type"]
            required_vars = SOLVER_VARS.get(solver_type, None)

            if required_vars is None:
                raise ValueError(f"Unknown solver type: {solver_type}. Supported: {list(SOLVER_VARS.keys())}")

            # Transported turbulence scalars extend the required BC set
            # (turbulence design doc section 7): [solver] turbulence names
            # the model, the model declares its transported fields, and each
            # needs its own bc/<field>.toml in OpenFOAM-meaning values.
            # Deferred import: FVdomain stays import-light for laminar cases.
            from FVsolver.Turbulence import transported_bc_fields
            required_vars = list(required_vars) + list(transported_bc_fields(
                fvconfig.get("solver", {}).get("turbulence", "laminar")))

            # Collect all available TOML files
            available_files = {}
            for filename in os.listdir(bc_dir):
                if filename.endswith('.toml'):
                    field_name = filename[:-5]  # Remove .toml extension
                    if field_name in required_vars:
                        available_files[field_name] = os.path.join(bc_dir, filename)
                    else:
                        logger.warning(f"Unrecognized boundary condition file found: {filename}, it will be ignored.")

            # Load only the required variables for this solver
            loaded_files = set()
            for var_name in required_vars:
                if var_name in available_files:
                    toml_path = available_files[var_name]
                    try:
                        with open(toml_path, 'rb') as f:
                            raw_bc = tomllib.load(f)

                        # Convert tomlkit objects to native Python types
                        raw_bc = toml_to_native(raw_bc)

                        # Register parameters from [parameter] section
                        # Parameters will be sampled when BC classes call resolve_value()
                        if 'parameter' in raw_bc:
                            parametric_manager.register_from_toml(raw_bc['parameter'])
                            logger.info(f"Registered parametric BC for {var_name}: {list(raw_bc['parameter'].keys())}")

                        # Store raw BC (will be parsed in init_env by BC classes)
                        boundary_info[var_name] = raw_bc
                        boundary_info_raw[var_name] = raw_bc  # Keep a copy for reset_env
                        loaded_files.add(var_name)
                        logger.debug(f"Loaded BC for {var_name} (required by solver '{solver_type}')")
                    except Exception as e:
                        logger.error(f"Failed to load {var_name}.toml: {e}")
                        raise
                else:
                    raise FileNotFoundError(
                        f"Missing required boundary condition file for solver '{solver_type}': {var_name}.toml\n"
                        f"Required variables: {required_vars}\n"
                        f"Available files: {list(available_files.keys())}"
                    )
        else:
            logger.warning(
                f"No bc/ directory found at {bc_dir} for case {mesh['case_name']}; no boundary condition TOMLs loaded."
            )
        # log loaded boundary file names
        logger.info(f"Loaded boundary files: {loaded_files} for case {mesh['case_name']} for solver {solver_type}")
        logger.debug(f"Required variables: {required_vars} for case {mesh['case_name']} for solver {solver_type}")

        # Store parametric manager and boundary info in mesh
        mesh["parametric_manager"] = parametric_manager
        mesh["boundary_info"] = boundary_info
        mesh["boundary_info_raw"] = boundary_info_raw  # For re-sampling during reset_env

        ''' <<< Load boundary condition information <<< '''

        # Apply any per-case parameter overrides BEFORE the transform runs.
        # The multi-slot pool loader sets these via set_pending_overrides() so
        # each slot gets its own per-case explicit values (e.g., a Reynolds-
        # derived inlet velocity) rather than a shared random draw from the
        # arithmetic pool.  Overrides are consumed (cleared) after this call
        # so they cannot leak into the next __getitem__ invocation.
        if self._pending_param_overrides:
            for _pname, _pvalue in self._pending_param_overrides.items():
                parametric_manager.override_value(_pname, _pvalue)
            logger.info(
                f"[H5CFDdataset] Applied {len(self._pending_param_overrides)} "
                f"per-case parameter override(s) for case {mesh['case_name']}: "
                f"{list(self._pending_param_overrides.keys())}"
            )
            self._pending_param_overrides = None

        # convert to tensors
        for key in h5_file.keys():
            value = h5_file[key][()]
            if isinstance(value, np.ndarray):
                # convert numpy array to torch tensor
                mesh[key] = torch.from_numpy(value)
            else:
                mesh[key] = value

        # Regions, cell zones and interfaces (mesh schema v2; a v1 file reads as one fluid region), checked against
        # the patch table and the face geometry before anything consumes them.
        region_schema = mesh_schema.read(h5_file, num_cells=mesh["cell|cell_pos"].shape[0])
        if not region_schema.is_trivial:
            mesh_schema.validate(
                region_schema,
                num_faces=mesh["face|face_pos"].shape[0],
                patch_dict=mesh["patch_dict"],
                owner=mesh["cpd|neighbor_cell"][0].numpy(),
                face_pos=mesh["face|face_pos"].numpy(),
                where=h5_path,
            )
        mesh["region_schema"] = region_schema

        # start to calculate other attributes like stencil, WLSQ matrix, etc.
        mesh_transformed, phi_cpd_cell = transform(
            mesh,
            self.params
        )

        # return to CPU!
        return mesh_transformed, phi_cpd_cell

    def __len__(self):
        return len(self.case_list)
