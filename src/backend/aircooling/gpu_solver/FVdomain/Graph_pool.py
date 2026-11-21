"""
HeteroData-based loader for FV domain.
Defines loaders and compatibility wrapper for existing FVGraph interface.
"""

import sys
import os
file_dir = os.path.dirname(os.path.dirname(__file__))
sys.path.append(file_dir)

import json
import logging
from collections import deque
from pathlib import Path

import torch
from Post_process.to_vtk import export_mesh_with_patches
from FVdomain.MmapManager import (
    SharedMeshStore, CaseSlot, MeshCacheManager,
    AsyncSnapshotWriter, STATIC_TENSOR_KEYS, _atomic_json_write,
)

logger = logging.getLogger(__name__)


def global_slot_index(local_slot_idx, rank, world_size):
    """Global slot g lives on rank g % world_size at local g // world_size.

    Interleaved, not blocked: spreads the per-epoch reset (a full CFDmesh
    rebuild between two barriers) over the ranks, and keeps each rank's slots
    spread in rollout age.
    """
    return local_slot_idx * world_size + rank


def reset_owner_rank(reset_counter, dataset_size, world_size):
    """Owner of global reset number ``reset_counter``.

    The counter is cumulative and must never be re-derived per epoch: at 7
    resets/epoch over 8 ranks, only the carry keeps every rank participating.
    """
    return (reset_counter % dataset_size) % world_size


def case_seed_for_global_slot(global_idx, base=777):
    """Parametric seed keyed on the GLOBAL slot index, so case k is case k at
    any world size.  Replaces base + rank*1000 + local_counter, which also
    collided outright past 1000 slots/rank (D=2400, W=2: rank1/local0 and
    rank0/local1000 both got 1777).
    """
    return base + int(global_idx)


def build_rank_case_list(case_names, total_dataset_size, rank, world_size,
                         seed=42, return_global=False):
    """Build a deterministically-shuffled, rank-specific slice of the global case list.

    1. Repeat *case_names* enough times to fill *total_dataset_size*.
    2. Shuffle the global list with a fixed *seed* (identical across ranks).
    3. Return the slice ``[rank * per_rank : (rank+1) * per_rank]``.

    If *return_global* is True, return the full shuffled global list instead
    of the per-rank slice (used by rank 0 to save the assignment).
    """
    import random as _random
    if total_dataset_size % world_size != 0:
        # Raise, not warn: unequal per-rank slot counts give unequal loader
        # lengths, and with find_unused_parameters=False the short rank leaves
        # the others on an allreduce that never comes -- a hang, not an error.
        raise ValueError(
            f"dataset_size ({total_dataset_size}) must be divisible by "
            f"world_size ({world_size}): dataset_size is a GLOBAL slot count, "
            f"so each rank takes dataset_size // world_size slots."
        )
    n_cases = len(case_names)
    full_repeats = total_dataset_size // n_cases
    remainder = total_dataset_size % n_cases
    global_list = list(case_names) * full_repeats + list(case_names[:remainder])

    rng = _random.Random(seed)
    rng.shuffle(global_list)

    if return_global:
        return global_list

    # Stride, not block: rank r's local slot l must hold global slot
    # l*world_size + r, else "case k" names a different case at each world size.
    return global_list[rank::world_size]


def save_case_assignment(save_dir, global_case_list, dataset_size, world_size, seed):
    """Save the global case assignment to ``case_assignment.json``.

    Stores: dataset_size, world_size, seed, global_case_list, and
    pre-computed rank_slices for easy inspection.
    """
    save_dir = Path(save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)

    # Stride, matching build_rank_case_list; a block slice would record an
    # assignment the loader does not use.
    rank_slices = [global_case_list[r::world_size] for r in range(world_size)]

    data = {
        "dataset_size": dataset_size,
        "world_size": world_size,
        "seed": seed,
        "global_case_list": global_case_list,
        "rank_slices": rank_slices,
    }
    with open(save_dir / "case_assignment.json", "w") as f:
        json.dump(data, f, indent=2)


def load_case_assignment(save_dir):
    """Load a previously saved case assignment. Raises FileNotFoundError."""
    path = Path(save_dir) / "case_assignment.json"
    if not path.is_file():
        raise FileNotFoundError(f"case_assignment.json not found in {save_dir}")
    with open(path) as f:
        return json.load(f)



def _sampled_parameters_of(mesh_dict):
    """{param_name: current_value} drawn by this transform, or None.

    Persisted per slot so a resume re-imposes the slot's CURRENT operating
    point.  The deterministic global-index seeding only reproduces the
    CREATION draw; after any reset_env the slot's Re comes from the evolving
    RNG stream, and reseeding scrambles it (measured: resuming a
    300-epoch pool redrew all 1200 inlets and continuity fell from -3.9 to
    -2.1, below even a cold start).
    """
    pm = mesh_dict.get("parametric_manager")
    if pm is None:
        return None
    try:
        # Only samplers that actually HOLD a value: get_status() reports
        # current_value=None for local-scope samplers (they draw per element,
        # not once per case).  Persisting a None and re-imposing it as a
        # constant crashes the BC evaluation with "must be real number, not
        # NoneType" (it kills a resumed run).
        return {k: v["current_value"] for k, v in pm.get_status().items()
                if isinstance(v.get("current_value"), (int, float))}
    except Exception:
        return None

def validate_resume_params(saved, dataset_size, world_size):
    """Raise ValueError if saved params don't match current run."""
    if saved["dataset_size"] != dataset_size:
        raise ValueError(
            f"dataset_size mismatch: saved={saved['dataset_size']}, "
            f"current={dataset_size}. Cannot resume mmap state."
        )
    if saved["world_size"] != world_size:
        raise ValueError(
            f"world_size mismatch: saved={saved['world_size']}, "
            f"current={world_size}. Cannot resume mmap state."
        )


def _strip_static_tensors(mesh_dict):
    """Return a shallow copy of mesh_dict with STATIC_TENSOR_KEYS removed.

    The removed tensors live in SharedMeshStore, so we don't need them
    duplicated in per-slot meta_objects.  This is critical for memory
    savings: without stripping, each slot would keep a full in-memory
    copy of static mesh tensors (~140 MB per case for Cylinder 172K cells).
    """
    return {k: v for k, v in mesh_dict.items() if k not in STATIC_TENSOR_KEYS}


def _build_merged_mesh(slot):
    """Build a mesh dict merging per-case meta_objects with static tensors.

    The plot/reset helpers expect to access keys like ``face|face_area`` or
    ``node|node_pos`` directly on the mesh dict.  Because we strip those
    static keys from ``slot.meta_objects`` to save memory, we must
    re-inject them (as clones from the shared mmap) before calling the
    helpers.
    """
    merged = dict(slot.meta_objects)
    shared = slot._shared_mesh
    if shared is not None and shared.manifest is not None:
        for key in STATIC_TENSOR_KEYS:
            if shared.has_tensor(key):
                merged[key] = shared.get_tensor(key).clone()
    return merged


class HeteroDataPool:
    """
    Data pool for heterogeneous graphs with DDP support.
    """

    def __init__(self, params=None, device=None, state_save_dir=None, export_patches=True,
                 rank=0, ddp_mode=False):
        self.params = params
        self.device = device
        self.export_patches = export_patches  # Control whether to export VTM with patches or single VTU
        self.state_save_dir = state_save_dir

        # meta里面存字典格式的h5文件信息+fvconfig+boundaryinfo+patch_info
        self.meta_pool = []
        self.phi_cpd_cell_pool = []
        self.time_step_pool = [] # 最终读取完之后要变为tensor

        self._plot_env = True
        self.reset_env_flag = False  # 初始化reset_env_flag
        self.rst_time = 1  # 初始化rst_time

        # === DDP info ===
        self.rank = rank
        self.ddp_mode = ddp_mode  # When True, reset_env is controlled externally

    def load_mesh_to_cpu(self, dataset_dir=None):
        """Loads the mesh dataset to CPU."""
        from FVdomain.Load_mesh import H5CFDdataset

        if dataset_dir is None:
            dataset_dir = self.params.dataset_dir
        
        valid_h5file_paths = []
        if dataset_dir.endswith(".h5"):
            valid_h5file_paths.append(dataset_dir)
        else:
            for subdir, _, files in os.walk(dataset_dir, followlinks=True):
                for data_name in files:
                    if data_name.endswith(".h5"):
                        valid_h5file_paths.append(os.path.join(subdir, data_name))
                    
        if not valid_h5file_paths:
            raise ValueError(f".h5 file not found in dataset directory: {dataset_dir}")

        logger.info("Loading whole dataset to cpu")

        mesh_dataset = H5CFDdataset(params=self.params, case_list=valid_h5file_paths)
        readround = 0
        start_idx = 0
        while True:
            
            for i_data in range(len(valid_h5file_paths)):
                meta_data, init_phi_cpd_cell = mesh_dataset[i_data]
                meta_data["global_idx"] = torch.arange(start_idx, start_idx + init_phi_cpd_cell.shape[0])
                
                # check num_channels: 要求所有求解的case属于同一个physics场景，不支持不同physics场景的case统一求解训练
                if readround == 0:
                    num_channels = meta_data["num_channels"]
                    phi_num_channels = meta_data["phi_num_channels"]
                elif readround > 0 and meta_data["num_channels"] != num_channels:
                    raise ValueError(f"Number of channels mismatch: {meta_data['num_channels']} != {num_channels}, please make sure all case in the dataset dir are the")
                
                self.meta_pool.append(meta_data)
                self.phi_cpd_cell_pool.append(init_phi_cpd_cell)
                start_idx += init_phi_cpd_cell.shape[0]

                if len(self.meta_pool) >= self.params.dataset_size:
                    break

            if len(self.meta_pool) >= self.params.dataset_size:
                break
            
            readround += 1

        logger.info(f"Successfully loaded {len(self.meta_pool)} cases to cpu")
        logger.info(f"Number of channels: {num_channels}")
        logger.info(f"Number of phi channels: {phi_num_channels}")
        
        self.phi_cpd_cell_pool = torch.cat(self.phi_cpd_cell_pool, dim=0)
        self.time_step_pool = torch.zeros(len(self.meta_pool))
        self.params.num_channels = num_channels
        self.params.phi_num_channels = phi_num_channels
        self.params.dataset_size = len(self.meta_pool)
        self.params.fvconfig = self.meta_pool[0]["fvconfig"]
        self.params.fvschemes = self.meta_pool[0]["fvconfig"]["fvSchemes"] # TODO:注意这里没有强制检查所有case的fvconfig.toml中的fvSchemes是否一致，而是仅取第一个case的fvSchemes
        # Always sync loss weights from fvconfig to params to avoid script-side overrides
        fvsolution = self.params.fvconfig.get("fvSolution", {})
        for key in ["loss_cont", "loss_mom", "loss_energy", "loss_phi_jump", "loss_pRef"]:
            if key in fvsolution:
                setattr(self.params, key, fvsolution[key])

        # logging the first mesh's fvconfig
        logger.info("First Mesh has fvconfig:")
        for section, content in self.params.fvconfig.items():
            logger.info(f"  [{section}]")
            if isinstance(content, dict):
                for key, value in content.items():
                    logger.info(f"    {key} = {value}")
            else:
                logger.info(f"    {content}")
        
        # --- DEBUGGING START ---
        if __debug__:
            logger.debug("--- Running Debug Check on meta_pool ---")
            if self.meta_pool:
                # Take the first sample as reference
                ref_sample = self.meta_pool[0]
                ref_shapes = {key: value.shape for key, value in ref_sample.items() if torch.is_tensor(value)}

                for i, sample in enumerate(self.meta_pool[1:]):
                    for key, value in sample.items():
                        if torch.is_tensor(value):
                            ref_shape = ref_shapes.get(key)
                            if ref_shape is None:
                                logger.warning(f"DEBUG WARNING: Key '{key}' in sample {i+1} not in reference sample 0.")
                                continue

                            # Check number of dimensions
                            if value.ndim != len(ref_shape):
                                logger.error(f"--- DEBUG ALERT: Dimension Mismatch Found! ---")
                                logger.error(f"  Sample Index: {i+1}")
                                logger.error(f"  Key: '{key}'")
                                logger.error(f"  Reference Shape (from sample 0): {ref_shape} (ndim={len(ref_shape)})")
                                logger.error(f"  Current Shape: {value.shape} (ndim={value.ndim})")
                                logger.error(f"  Case name: {sample.get('case_name')}, File name: {sample.get('file_name')}")
                                logger.error(f"---------------------------------------------")

            logger.debug("--- Debug Check Finished ---")
        # --- DEBUGGING END ---

        # Control the folder grouping for the number of plots
        self.plot_count = 0

        logger.info(f"Loaded {len(self.meta_pool)} cases")

        return self.params
    
    def _set_reset_env_flag(self, flag=False, rst_time=1):
        """
        Sets the flag for resetting the environment.

        Args:
            flag (bool): The flag to indicate whether to reset the environment.
            rst_time (int): The number of times to reset.
        """
        self.reset_env_flag = flag
        self.rst_time = rst_time

    def refresh_pool(self, phi_new_cpd_cell, global_idx, graph_index):
        """
        Updates the phi_cpd_cell_pool with new values.

        NOTE: reset_env is NEVER triggered here. It must be called explicitly
        from the training script AFTER the batch loop completes, to avoid
        corrupting the loader's index mapping mid-iteration.
        """
        self.phi_cpd_cell_pool[global_idx] = phi_new_cpd_cell.data
        self.time_step_pool[graph_index] += 1

    def _update_re_from_patch_velocity(self, mesh, phi_cpd_cell):
        """
        Update Re in fvconfig based on actual velocity at the specified boundary patch.

        If fvconfig["theta_pde"]["Re_config"] exists with {vel="boundaryfield.inlet"},
        this method calculates the area-weighted average normal velocity at the inlet patch
        and updates Re = rho * V_avg * L / mu.

        The average velocity is computed using the flux-based formula:
            V_avg = sum(v_i · S_i) / sum(|S_i|)
        where v_i is the velocity at face i and S_i is the face area vector (normal * area).

        Args:
            mesh: Mesh dictionary containing fvconfig, patch_dict, face_area, and face_normal
            phi_cpd_cell: Field values tensor [num_cpd_cells, channels], velocity in first 3 columns
        """
        fvconfig = mesh.get("fvconfig", {})
        theta_pde = fvconfig.get("theta_pde", {})

        # Check for saved Re_config (preserved from original dict config)
        re_config = theta_pde.get("Re_config", None)
        if re_config is None or not isinstance(re_config, dict) or "vel" not in re_config:
            return  # Not a parametric Re config, use existing value

        # Parse patch name from "boundaryfield.inlet" -> "inlet"
        vel_spec = re_config["vel"]
        if vel_spec.startswith("boundaryfield."):
            patch_name = vel_spec.split(".", 1)[1]
        else:
            patch_name = vel_spec

        # Get patch info
        patch_dict = mesh.get("patch_dict", {})
        if patch_name not in patch_dict:
            logger.warning(f"Patch '{patch_name}' not found for Re calculation. Available: {list(patch_dict.keys())}")
            return

        patch_info = patch_dict[patch_name]

        # Get face indices for this patch
        start_idx_face = int(patch_info["start_idx_face"])
        end_idx_face = int(patch_info["end_idx_face"])

        # Get cpd_cell indices for velocity extraction
        start_idx_cpd = int(patch_info["start_idx_cpd_cell"])
        end_idx_cpd = int(patch_info["end_idx_cpd_cell"])

        # Extract velocity at patch ghost cells (first 3 columns are u, v, w)
        u_patch = phi_cpd_cell[start_idx_cpd:end_idx_cpd, 0:3]  # [N_faces, 3]

        # Get face area and normal from mesh
        face_area = mesh.get("face|face_area", None)
        face_normal = mesh.get("face|face_normal", None)

        if face_area is None or face_normal is None:
            # Fallback: use simple velocity magnitude average
            velocity_magnitude = torch.norm(u_patch, dim=1).mean().item()
            logger.warning(f"face_area or face_normal not found in mesh, using simple velocity magnitude average")
        else:
            # Get face properties for this patch
            patch_face_area = face_area[start_idx_face:end_idx_face]  # [N_faces, 1]
            patch_face_normal = face_normal[start_idx_face:end_idx_face]  # [N_faces, 3]

            logger.debug(f"Re calc - patch_face_area shape: {patch_face_area.shape}, sum: {patch_face_area.sum().item()}")
            logger.debug(f"Re calc - num ghost cells: {end_idx_cpd - start_idx_cpd}, num faces: {end_idx_face - start_idx_face}")

            # Compute area-weighted average normal velocity:
            # V_avg = sum(v_i · S_i) / sum(|S_i|)
            # where S_i = face_normal * face_area (face area vector)

            # Ensure face_area has correct shape for broadcasting
            if patch_face_area.dim() == 1:
                patch_face_area = patch_face_area.unsqueeze(-1)  # [N] -> [N, 1]

            # v_i · n_i (dot product of velocity and unit normal)
            v_dot_n = (u_patch * patch_face_normal).sum(dim=1, keepdim=True)  # [N_faces, 1]

            # sum(v_i · S_i) = sum(v_i · n_i * |S_i|)
            flux_sum = (v_dot_n * patch_face_area).sum().item()

            # sum(|S_i|)
            total_area = patch_face_area.sum().item()

            # V_avg = flux / total_area
            velocity_magnitude = abs(flux_sum / total_area) if total_area > 0 else 0.0

            logger.debug(f"DEBUG Re calc - flux_sum: {flux_sum}, total_area: {total_area}, V_avg: {velocity_magnitude}")

        # Calculate Re = rho * V_avg * L / mu
        rho = theta_pde["rho"]["val"]
        mu = theta_pde["mu"]["val"]
        L = theta_pde["L"]

        Re = rho * velocity_magnitude * L / mu
        mesh["fvconfig"]["theta_pde"]["Re"] = Re

        logger.info(f"Updated Re from patch '{patch_name}': Re = {Re:.2f} (V_avg={velocity_magnitude:.4f})")

    def reset_env(self, plot=False):
        """
        Resets the environment by removing the oldest mesh and adding a new one.

        This method:
        1. Removes the oldest case from the pool
        2. Optionally exports it for visualization (including boundary patches)
        3. Re-initializes it to time=0 using the transform function
        4. Appends it back to the end of the pool

        IMPORTANT: The transform function (CFDmesh.__call__) will:
        - Deep copy boundary_info to ensure clean BC definitions
        - Re-create all BC instances from scratch
        - Re-initialize all field values

        This ensures that each reset starts from a clean state without any
        accumulated modifications from training.

        Args:
            plot: Whether to export visualization files
        """
        # Import here to avoid circular imports
        from Utils.utilities import FaceType

        # Pop the mesh data of the 0-th grid
        old_mesh = self.meta_pool.pop(0)
        old_global_idx = old_mesh["global_idx"]

        # DEBUG: Print plot decision
        logger.info(f"[reset_env] rank={self.rank}, plot={plot}, plot_count={self.plot_count}")

        # Plotting
        if plot:

            if not (self.state_save_dir.find("traing_results") != -1):
                os.makedirs(f"{self.state_save_dir}/traing_results", exist_ok=True)
                self.state_save_dir = f"{self.state_save_dir}/traing_results"

            # Fetch data from pool using old_global_idx
            plot_phi_cpd_cell = self.phi_cpd_cell_pool[old_global_idx]  # [num_cpd_cells_in_old_mesh, C]

            mask_interior_cell = (old_mesh["cpd|cell_type"].long()==FaceType.NORMAL).squeeze()

            ''' >>> Export mesh visualization >>> '''
            max_u = plot_phi_cpd_cell[~mask_interior_cell,0:3].max().item()
            min_u = plot_phi_cpd_cell[~mask_interior_cell,0:3].min().item()
            logger.debug(f"case: {old_mesh['case_name']}, boundary:max_u: {max_u}, min_u: {min_u}")

            # Calculate Re from actual boundary velocity if configured
            self._update_re_from_patch_velocity(old_mesh, plot_phi_cpd_cell)

            self.export_mesh_visualization(old_mesh, plot_phi_cpd_cell, mask_interior_cell, export_patches=self.export_patches)
            self.plot_count += 1  # Increment plot counter after exporting
            ''' <<< Export mesh visualization <<< '''

            self._plot_env = False

        # Remove uvwp data belonging to the 0-th grid
        self.phi_cpd_cell_pool = self.phi_cpd_cell_pool[old_global_idx.shape[0]:]

        # Update global_idx for all remaining meshes
        for iidx in range(len(self.meta_pool)):
            cur_meta_data = self.meta_pool[iidx]
            cur_meta_data["global_idx"] -= old_global_idx.shape[0]

        # Generate new mesh data and insert at the end of the pool
        # The transform function will:
        # 1. Deep copy boundary_info (防止BC实例修改原始定义)
        # 2. Re-create all BC instances from scratch (确保BC状态干净)
        # 3. Re-initialize field values (重置到初始条件)
        # 4. Re-compute WLSQ matrices (确保梯度重构矩阵正确)
        new_mesh, init_phi_cpd_cell = old_mesh["transform"](
            old_mesh,
            self.params
        )

        # This ensures global_idx points to valid indices in the pool
        new_mesh["global_idx"] = torch.arange(
            self.phi_cpd_cell_pool.shape[0], self.phi_cpd_cell_pool.shape[0]+init_phi_cpd_cell.shape[0]
        )
        # Add new data to pool
        self.phi_cpd_cell_pool = torch.cat((self.phi_cpd_cell_pool, init_phi_cpd_cell), dim=0)
        self.meta_pool.append(new_mesh)
        # Remove the first time counter and append a zero at the end
        self.time_step_pool = torch.cat([self.time_step_pool[1:], torch.zeros(1, device=self.time_step_pool.device)])

    def export_mesh_visualization(self, mesh, phi_cpd_cell, mask_interior_cell, file_name=None, export_patches=False):
        """
        Export mesh data to VTK format with optional boundary patches.

        Uses the unified API from to_vtk.py to export either:
        - VTM multiblock format with interior + boundary patches (if self.export_patches=True)
        - Single VTU file with interior mesh only (if self.export_patches=False)

        Args:
            mesh (dict): Mesh data containing geometry, patch information, and boundary_info
            phi_cpd_cell (torch.Tensor): Field values for all cpd cells (interior + boundary ghost cells)
                                         Shape: [num_cpd_cells, total_channels]
                                         Channels ordered according to boundary_info.keys()
            mask_interior_cell (torch.Tensor): Boolean mask for interior cells
                                               Shape: [num_cpd_cells]
            file_name (str, optional): Output file name. Defaults to None.
        """
        # Get basic info
        case_name = mesh["case_name"]
        dt = mesh["fvconfig"]["theta_pde"]["dt"]["val"]
        Re = mesh["fvconfig"]["theta_pde"]["Re"]

        # Determine output path
        if file_name is None:
            save_dir_num = self.plot_count // 50
            saving_dir = f"{self.state_save_dir}/{save_dir_num*50}-{(save_dir_num+1)*50}"
            os.makedirs(saving_dir, exist_ok=True)

            if self.export_patches:
                # Create subdirectory for VTM output
                case_dir = f"{saving_dir}/NO.{self.plot_count}_{case_name}_Re={Re:.4f}_dt={dt:.3f}"
                os.makedirs(case_dir, exist_ok=True)
                save_path = f"{case_dir}/NO.{self.plot_count}_{case_name}_Re={Re:.4f}_dt={dt:.3f}"  # Extension added by export function
            else:
                # Single VTU file
                save_path = f"{saving_dir}/NO.{self.plot_count}_{case_name}_Re={Re:.4f}_dt={dt:.3f}"  # Extension added by export function
        else:
            save_path = file_name

        # Call unified export API
        export_mesh_with_patches(
            mesh=mesh,
            phi_cpd_cell=phi_cpd_cell,
            mask_interior_cell=mask_interior_cell,
            save_path=save_path,
            export_patches=export_patches
        )


class MmapDataPool:
    """
    Drop-in replacement for HeteroDataPool that uses mmap-backed CaseSlots
    instead of in-memory tensors.  Each case owns a CaseSlot with its own
    phi.bin mmap.  Static mesh tensors are shared across ranks via
    MeshCacheManager / SharedMeshStore.
    """

    def __init__(self, params=None, device=None, state_save_dir=None,
                 export_patches=True, rank=0, ddp_mode=False):
        self.params = params
        self.device = device
        self.export_patches = export_patches
        self.state_save_dir = state_save_dir

        # Slot-based storage (replaces meta_pool + phi_cpd_cell_pool)
        self.slots: list[CaseSlot] = []
        self.slot_queue: deque[int] = deque()  # FIFO order for reset_env

        self._plot_env = True
        self.reset_env_flag = False
        self.rst_time = 1

        self.rank = rank
        self.ddp_mode = ddp_mode
        # Local slot -> global identity.  From the environment so existing
        # call sites keep working; non-DDP gets 1, where global == local.
        self._world_size = int(os.environ.get("WORLD_SIZE", 1))

        # Mmap infrastructure
        self._rank_dir: Path | None = None
        self._mesh_cache: MeshCacheManager | None = None
        self._snapshot_writer = AsyncSnapshotWriter()
        self._snapshots_root: Path | None = None

        # Data-driven training: label attachment state
        self.loss_mode: str = "fvm"
        self._has_labels: bool = False

        self.plot_count = 0

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _find_meta_json(self, dataset_dir: str):
        """Search for a ``meta.json`` describing multi-case splits.

        The Cylinder/DC 300-case experiments ship a ``meta.json`` alongside
        the (single) h5 mesh describing per-case metadata and
        train/val/test splits.  It typically lives next to the OpenFOAM
        results rather than the gnn dataset, so we look in a few places.

        Search order:

          1. ``<dataset_dir>/meta.json``
          2. ``<dataset_dir>/../openfoam/meta.json``
          3. ``<dataset_dir>/../../openfoam/meta.json``
          4. ``<dataset_dir>/../../../openfoam/meta.json``

        Returns the ``Path`` if found, else ``None``.
        """
        # Relative paths break the upward search: Path("h5").parent.parent is
        # still "." (e.g. --dataset_dir h5).
        dataset_dir = str(Path(dataset_dir).resolve())
        candidates = [
            Path(dataset_dir) / "meta.json",
            Path(dataset_dir).parent / "openfoam" / "meta.json",
            Path(dataset_dir).parent.parent / "openfoam" / "meta.json",
            Path(dataset_dir).parent.parent.parent / "openfoam" / "meta.json",
        ]
        for p in candidates:
            if p.is_file():
                return p
        return None

    def _find_labels_manifest(self, dataset_dir: str):
        """Search for the ``labels_manifest.json`` written by the label-preparation
        script that converts reference trajectories into per-case label mmaps.

        Search order (preferred first; later paths are legacy fallbacks):

          1. ``<dataset_dir>/data_driven/labels/labels_manifest.json``
             (new preferred layout: labels live under the data_driven
             training-mode directory, next to the other training modes)
          2. ``<dataset_dir>/../data_driven/labels/labels_manifest.json``
          3. ``<dataset_dir>/labels/labels_manifest.json``  (legacy)
          4. ``<dataset_dir>/../labels/labels_manifest.json``  (legacy)

        Returns the ``Path`` if found, else ``None``.
        """
        # --labels_dir was declared in get_param but never read here, so passing it
        # did nothing and the store had to be reachable by one of the four fixed
        # paths below.  An explicit path wins, and a wrong
        # one raises instead of silently falling through to a different store.
        explicit = getattr(self.params, "labels_dir", None)
        if explicit:
            p = Path(explicit)
            if p.name != "labels_manifest.json":
                p = p / "labels_manifest.json"
            if not p.is_file():
                raise FileNotFoundError(f"--labels_dir {explicit}: no labels_manifest.json there")
            return p

        # Relative paths break the upward search: Path("h5").parent.parent is
        # still "." (e.g. --dataset_dir h5).
        dataset_dir = str(Path(dataset_dir).resolve())
        candidates = [
            # Preferred: <gnn>/data_driven/labels/
            Path(dataset_dir) / "data_driven" / "labels" / "labels_manifest.json",
            Path(dataset_dir).parent / "data_driven" / "labels" / "labels_manifest.json",
            # Legacy: <gnn>/labels/
            Path(dataset_dir) / "labels" / "labels_manifest.json",
            Path(dataset_dir).parent / "labels" / "labels_manifest.json",
        ]
        for p in candidates:
            if p.is_file():
                return p
        return None

    def _attach_labels_to_slots(self, manifest_path: Path) -> None:
        """Attach per-case labels mmap to each slot by matching ``case_name``.

        The manifest lists one entry per case (written by the label-preparation
        script) with a ``case_name`` that must match ``slot._meta['case_name']`` as set
        during slot creation.  Slots whose ``case_name`` is absent from the
        manifest are left without labels; if *no* slot matches at all we raise,
        since that almost certainly indicates a fixture/path mismatch that
        would silently break data-driven training downstream.
        """
        with open(manifest_path) as f:
            manifest = json.load(f)

        labels_dir = manifest_path.parent
        mmap_by_name = {}
        for case in manifest["cases"]:
            mmap_by_name[case["case_name"]] = {
                "file": labels_dir / case["mmap_file"],
                "shape": tuple(case["shape"]),
            }

        dtype = manifest.get("dtype", "float32")
        t_offset = manifest.get("T_offset", None)
        n_attached = 0
        unmatched_slots = []
        for slot in self.slots:
            name = slot._meta.get("case_name", "")
            if name in mmap_by_name:
                info = mmap_by_name[name]
                slot.attach_labels(
                    labels_file=info["file"],
                    shape=info["shape"],
                    dtype=dtype,
                )
                if t_offset is not None:
                    slot._label_T_offset = float(t_offset)
                n_attached += 1
            else:
                unmatched_slots.append(name)

        # Fail loud if ANY slot is missing a label.  A partial-attach silently
        # breaks data-driven training the first time the unlabelled slot is
        # sampled by HeteroGraphDataset, so we prefer an early crash here.
        if n_attached != len(self.slots):
            slot_names = [s._meta.get("case_name", "") for s in self.slots[:5]]
            mf_names = list(mmap_by_name.keys())[:5]
            raise RuntimeError(
                f"Labels manifest found at {manifest_path} but only "
                f"{n_attached}/{len(self.slots)} slots matched.\n"
                f"  Unmatched slot case_names (first 5): {unmatched_slots[:5]}\n"
                f"  Slot names sampled (first 5): {slot_names}\n"
                f"  Manifest case_names (first 5): {mf_names}\n"
                f"Re-run prepare_labels.py for the missing cases, or check "
                f"that slot.case_name matches manifest.cases[].case_name."
            )
        logger.info(
            "[MmapDataPool] Attached labels to %d/%d slots from %s",
            n_attached, len(self.slots), manifest_path,
        )
        print(
            f"[MmapDataPool] Attached labels to {n_attached}/{len(self.slots)} "
            f"slots from {manifest_path}"
        )

    def load_mesh_to_mmap(self, dataset_dir=None, resume=False, resume_from=None,
                          split: str = "all", loss_mode: str = "fvm",
                          total_dataset_size: int | None = None,
                          world_size: int = 1,
        param_overrides=None,
    ):
        """Load H5 files, create mesh cache entries, create CaseSlots.

        Args:
            dataset_dir: Path to H5 dataset directory.
            resume: Whether to attempt resuming from existing mmap state.
            resume_from: Path to OLD mmap_state directory from a previous
                Logger run.  When ``resume=True`` and the current Logger dir
                is freshly created, this allows reopening mmap files that
                were written under the previous run's Logger path.
            split: One of ``"all"`` (legacy: 1 h5 = 1 slot), ``"train"``,
                ``"val"``, or ``"test"``.  When ``split != "all"`` and a
                sibling ``meta.json`` is discovered, the pool materialises
                one slot per case name listed in ``meta["split"][split]``,
                all sharing a single ``SharedMeshStore`` instance.  This is
                how the 300-case parametric sweeps (shared geometry, one
                Reynolds per case) are mapped onto the slot API.
            loss_mode: ``"fvm"`` (default) runs the physics-informed FV path
                with no labels loaded.  ``"data_driven"`` additionally
                attaches a per-case labels mmap to each slot by looking up
                a sibling ``labels_manifest.json`` (written by the
                label-preparation script); if the manifest is missing a
                ``RuntimeError`` is raised rather than silently falling back.
        """
        from FVdomain.Load_mesh import H5CFDdataset

        if dataset_dir is None:
            dataset_dir = self.params.dataset_dir

        self.loss_mode = loss_mode

        # Determine mmap root directory for THIS run (snapshots will go here)
        mmap_root = Path(self.state_save_dir) / "mmap_state"
        mmap_root.mkdir(parents=True, exist_ok=True)
        # MMAP_LIVE_ROOT relocates the LIVE state (per-slot phi.bin, mesh
        # cache) -- the files refresh_pool writes through to on every step --
        # onto fast local storage, while snapshots stay under the Logger so a
        # relay or crash can still restore.  Measured: the
        # same run did 594 s/epoch with the live mmap on GPFS home and
        # 650-1550 s/epoch on Lustre scratch; the compute nodes expose a
        # 252 GB tmpfs at /tmp (no local NVMe), and the live state is ~4 GB.
        live_root = Path(os.environ["MMAP_LIVE_ROOT"]) if os.environ.get("MMAP_LIVE_ROOT") else mmap_root
        if live_root != mmap_root:
            live_root.mkdir(parents=True, exist_ok=True)
            logger.info("Live mmap state on %s (snapshots stay on %s)", live_root, mmap_root)
        self._rank_dir = live_root / f"rank_{self.rank}"
        self._rank_dir.mkdir(parents=True, exist_ok=True)
        self._mesh_cache = MeshCacheManager(live_root / "mesh_cache")
        self._snapshots_root = mmap_root / "snapshots"
        self._snapshots_root.mkdir(parents=True, exist_ok=True)

        # If resume requested, try the OLD mmap_state dir first, then current.
        # Either way, the resumed slots must live under the CURRENT Logger's
        # rank_dir so that subsequent refresh_pool/save_snapshot calls mutate
        # the new experiment's files, not the old one.
        if resume:
            if resume_from:
                old_mmap_root = Path(resume_from)
                old_rank_dir = old_mmap_root / f"rank_{self.rank}"
                if old_rank_dir.exists() and (old_rank_dir / "slot_0").exists():
                    logger.info("Resuming from old mmap state at %s", old_mmap_root)
                    old_snapshots_root = old_mmap_root / "snapshots"
                    if not old_snapshots_root.exists():
                        old_snapshots_root = None
                    return self._resume_from_mmap(old_rank_dir, old_snapshots_root)
                else:
                    logger.warning(
                        "resume_from path has no rank_%d/slot_0: %s, "
                        "trying current dir", self.rank, old_mmap_root
                    )

            # Fall back to current dir (in case mmap_state is already here)
            if (self._rank_dir / "slot_0").exists():
                logger.info("Resuming from existing mmap state at %s", self._rank_dir)
                return self._resume_from_mmap(self._rank_dir, self._snapshots_root)

        # Collect H5 paths
        valid_h5file_paths = []
        if dataset_dir.endswith(".h5"):
            valid_h5file_paths.append(dataset_dir)
        else:
            for subdir, _, files in os.walk(dataset_dir, followlinks=True):
                for data_name in files:
                    if data_name.endswith(".h5"):
                        valid_h5file_paths.append(os.path.join(subdir, data_name))

        if not valid_h5file_paths:
            raise ValueError(f".h5 file not found in dataset directory: {dataset_dir}")

        logger.info("Loading dataset to mmap (rank %d)", self.rank)

        # Resolve multi-slot split (if any).  When split == "all" we stick
        # with the legacy 1-h5 = 1-slot behaviour; otherwise we require a
        # sibling ``meta.json`` that enumerates the case names per split.
        case_names_from_meta = None
        case_parameters_from_meta: dict = {}
        if split != "all":
            meta_json_path = self._find_meta_json(dataset_dir)
            if meta_json_path is None:
                raise FileNotFoundError(
                    f"split='{split}' requested but no meta.json found near "
                    f"{dataset_dir}. Searched dataset_dir, ../openfoam, "
                    f"../../openfoam."
                )
            with open(meta_json_path) as f:
                meta = json.load(f)
            if split not in meta.get("split", {}):
                raise ValueError(
                    f"Split '{split}' not found in {meta_json_path}. "
                    f"Available: {list(meta.get('split', {}).keys())}"
                )
            case_names_from_meta = list(meta["split"][split])
            if len(case_names_from_meta) == 0:
                raise ValueError(
                    f"Split '{split}' in {meta_json_path} is empty; "
                    f"cannot create any slots."
                )
            # Optional per-case parameter overrides: emitted by generate_meta.py
            # so each slot can be conditioned on its own case-specific values
            # (e.g., Cylinder inlet velocity derived from the Re in case_name).
            # When absent, slots fall back to the parametric pool defined in
            # the h5/bc/*.toml files — which for sweep datasets samples the
            # *same* random value across all slots and is therefore wrong.
            case_parameters_from_meta = meta.get("case_parameters", {}) or {}
            if not case_parameters_from_meta:
                logger.warning(
                    "[MmapDataPool] meta.json at %s does NOT carry "
                    "'case_parameters'. Every slot will share a single "
                    "parametric draw, which is almost certainly wrong for "
                    "a sweep dataset. Re-run generate_meta.py to emit "
                    "per-case parameters.",
                    meta_json_path,
                )
            logger.info(
                "[MmapDataPool] Using meta.json split='%s': %d cases (from %s)"
                "%s",
                split, len(case_names_from_meta), meta_json_path,
                " WITH per-case parameter overrides"
                if case_parameters_from_meta else " WITHOUT per-case overrides",
            )
            print(
                f"[MmapDataPool] Using meta.json split='{split}': "
                f"{len(case_names_from_meta)} cases"
                + (" + case_parameters"
                   if case_parameters_from_meta else " (no per-case overrides)")
            )

        mesh_dataset = H5CFDdataset(params=self.params, case_list=valid_h5file_paths)

        # DDP rank-based slicing: build global case list, shuffle, slice.
        # When total_dataset_size is provided (DDP), each rank gets a
        # non-overlapping slice of the shuffled global list.  When not
        # provided (single-GPU), fall back to the original round-robin
        # fill logic using params.dataset_size directly.
        rank_case_list = None
        if case_names_from_meta is not None and total_dataset_size is not None:
            rank_case_list = build_rank_case_list(
                case_names=case_names_from_meta,
                total_dataset_size=total_dataset_size,
                rank=self.rank,
                world_size=world_size,
                seed=42,
            )
            if self.rank == 0:
                global_list = build_rank_case_list(
                    case_names=case_names_from_meta,
                    total_dataset_size=total_dataset_size,
                    rank=0, world_size=world_size,
                    seed=42, return_global=True,
                )
                save_case_assignment(
                    self.state_save_dir, global_list,
                    dataset_size=total_dataset_size,
                    world_size=world_size, seed=42,
                )
                logger.info(
                    "[MmapDataPool] Saved case_assignment.json to %s "
                    "(%d cases, %d ranks)",
                    self.state_save_dir, total_dataset_size, world_size,
                )
            logger.info(
                "[MmapDataPool] Rank %d: sliced %d cases from global %d "
                "(world_size=%d)",
                self.rank, len(rank_case_list), total_dataset_size, world_size,
            )

        readround = 0
        while True:
            for i_data in range(len(valid_h5file_paths)):
                h5_path = valid_h5file_paths[i_data]
                h5_hash = self._mesh_cache._hash_h5(h5_path)

                if case_names_from_meta is not None:
                    # Use rank-specific case list when available (DDP),
                    # otherwise fall back to full case list (single-GPU).
                    iter_cases = rank_case_list if rank_case_list is not None else case_names_from_meta
                    shared_mesh = None
                    for case_name in iter_cases:
                        if len(self.slots) >= self.params.dataset_size:
                            break

                        per_case_overrides = case_parameters_from_meta.get(case_name)
                        if param_overrides:
                            # Caller-chosen values for every slot (a rollout at one
                            # Reynolds number) win over meta.json's per-case values.
                            # They go through the sampler, so the BC ghost carries
                            # them every step -- overwriting phi's inlet cells instead
                            # leaves the ghost on a random draw and the interior
                            # relaxes toward it (measured: inlet
                            # 1.80 -> 1.46 over 500 steps).
                            per_case_overrides = {**(per_case_overrides or {}), **param_overrides}
                        if per_case_overrides:
                            mesh_dataset.set_pending_overrides(per_case_overrides)

                        # Seed on global identity (test_reset_schedule_invariance).
                        mesh_dataset.set_pending_global_slot_index(
                            global_slot_index(len(self.slots), self.rank, self._world_size)
                        )
                        per_case_mesh_dict, per_case_init_phi = mesh_dataset[i_data]

                        if readround == 0 and len(self.slots) == 0:
                            num_channels = per_case_mesh_dict["num_channels"]
                            phi_num_channels = per_case_mesh_dict["phi_num_channels"]
                        elif per_case_mesh_dict["num_channels"] != num_channels:
                            raise ValueError(
                                f"Number of channels mismatch: "
                                f"{per_case_mesh_dict['num_channels']} != {num_channels}"
                            )

                        if shared_mesh is None:
                            shared_mesh = self._mesh_cache.get_or_create(
                                h5_path, per_case_mesh_dict,
                            )

                        slot_idx = len(self.slots)
                        slot_dir = self._rank_dir / f"slot_{slot_idx}"
                        meta_info = {
                            "h5_hash": h5_hash,
                            "h5_path": h5_path,
                            "case_name": case_name,
                            "case_parameters": per_case_overrides,
                        }
                        slot = CaseSlot.create(
                            slot_dir=slot_dir,
                            shared_mesh=shared_mesh,
                            num_cpd_cells=per_case_init_phi.shape[0],
                            phi_channels=per_case_init_phi.shape[1],
                            init_phi=per_case_init_phi,
                            meta_info=meta_info,
                        )
                        slot.meta_objects = _strip_static_tensors(per_case_mesh_dict)
                        slot.meta_objects["case_name"] = case_name
                        if per_case_overrides:
                            slot.meta_objects["case_parameters"] = per_case_overrides
                        _sp = _sampled_parameters_of(per_case_mesh_dict)
                        if _sp:
                            slot.set_meta_entry("sampled_parameters", _sp)
                        self.slots.append(slot)
                        self.slot_queue.append(slot_idx)
                else:
                    # Legacy single-slot path: load once, create one slot.
                    if param_overrides:
                        # caller-chosen values, e.g. a rollout at one Re; see the
                        # meta branch above for why this must go through the sampler
                        mesh_dataset.set_pending_overrides(dict(param_overrides))
                    mesh_dataset.set_pending_global_slot_index(
                        global_slot_index(len(self.slots), self.rank, self._world_size)
                    )
                    mesh_dict, init_phi_cpd_cell = mesh_dataset[i_data]

                    # Create / reuse shared mesh store via cache manager
                    shared_mesh = self._mesh_cache.get_or_create(h5_path, mesh_dict)

                    # Validate num_channels consistency
                    if readround == 0 and len(self.slots) == 0:
                        num_channels = mesh_dict["num_channels"]
                        phi_num_channels = mesh_dict["phi_num_channels"]
                    elif mesh_dict["num_channels"] != num_channels:
                        raise ValueError(
                            f"Number of channels mismatch: {mesh_dict['num_channels']} != {num_channels}"
                        )

                    slot_idx = len(self.slots)
                    slot_dir = self._rank_dir / f"slot_{slot_idx}"
                    meta_info = {
                        "h5_hash": h5_hash,
                        "h5_path": h5_path,
                        "case_name": mesh_dict["case_name"],
                        "case_parameters": None,
                    }

                    slot = CaseSlot.create(
                        slot_dir=slot_dir,
                        shared_mesh=shared_mesh,
                        num_cpd_cells=init_phi_cpd_cell.shape[0],
                        phi_channels=init_phi_cpd_cell.shape[1],
                        init_phi=init_phi_cpd_cell,
                        meta_info=meta_info,
                    )

                    # Store non-static keys from mesh_dict in meta_objects.
                    # Static tensors live in SharedMeshStore — stripping them
                    # here is what actually realises the mmap memory savings.
                    slot.meta_objects = _strip_static_tensors(mesh_dict)

                    _sp = _sampled_parameters_of(mesh_dict)
                    if _sp:
                        slot.set_meta_entry("sampled_parameters", _sp)
                    self.slots.append(slot)
                    self.slot_queue.append(slot_idx)

                if len(self.slots) >= self.params.dataset_size:
                    break

            if len(self.slots) >= self.params.dataset_size:
                break
            # Multi-slot mode: if dataset_size > len(case_names), loop
            # again to create additional copies (each copy gets its own
            # independent mmap phi.bin so it can sit at a different
            # rollout timestep).
            if case_names_from_meta is not None:
                if len(self.slots) >= self.params.dataset_size:
                    break
                logger.info(
                    "[MmapDataPool] Round %d filled %d/%d slots, "
                    "looping for more copies",
                    readround, len(self.slots), self.params.dataset_size,
                )
            readround += 1

        logger.info("Successfully loaded %d cases to mmap (rank %d)", len(self.slots), self.rank)
        logger.info("Number of channels: %d", num_channels)
        logger.info("Number of phi channels: %d", phi_num_channels)

        # Attach per-case labels mmap when data-driven training is requested.
        # FVM (physics-informed) mode never loads labels, even if a manifest
        # happens to be on disk — this keeps the default training path
        # memory-identical to how it behaved before label support was added.
        if loss_mode == "data_driven":
            manifest_path = self._find_labels_manifest(dataset_dir)
            if manifest_path is None:
                raise RuntimeError(
                    f"loss_mode='data_driven' requires labels_manifest.json. "
                    f"Searched under {dataset_dir}/labels/ and "
                    f"{Path(dataset_dir).parent}/labels/. "
                    f"Run prepare_labels.py first."
                )
            self._attach_labels_to_slots(manifest_path)
            self._has_labels = True

        # Sync params (mirror HeteroDataPool.load_mesh_to_cpu behaviour)
        self.params.num_channels = num_channels
        self.params.phi_num_channels = phi_num_channels
        self.params.dataset_size = len(self.slots)
        self.params.fvconfig = self.slots[0].meta_objects["fvconfig"]
        self.params.fvschemes = self.slots[0].meta_objects["fvconfig"]["fvSchemes"]

        # Sync loss weights from fvconfig to params
        fvsolution = self.params.fvconfig.get("fvSolution", {})
        for key in ["loss_cont", "loss_mom", "loss_energy", "loss_phi_jump", "loss_pRef"]:
            if key in fvsolution:
                setattr(self.params, key, fvsolution[key])

        # Log fvconfig
        logger.info("First Mesh has fvconfig:")
        for section, content in self.params.fvconfig.items():
            logger.info("  [%s]", section)
            if isinstance(content, dict):
                for key, value in content.items():
                    logger.info("    %s = %s", key, value)
            else:
                logger.info("    %s", content)

        return self.params

    # ------------------------------------------------------------------
    # Resume helpers
    # ------------------------------------------------------------------

    def _resume_from_mmap(self, old_rank_dir: Path, old_snapshots_root: Path | None = None):
        """Copy phi state from an old Logger dir into the current Logger dir,
        then reopen slots pointing at the NEW locations.

        Why copy?  Subsequent ``refresh_pool`` / ``save_snapshot`` calls must
        mutate the current experiment's files — not the previous run's — so
        that a crash-and-resume cycle can recover its own state.

        Args:
            old_rank_dir: Path like ``.../old_logger/mmap_state/rank_N``.  If
                this equals ``self._rank_dir`` the copy is a no-op.
            old_snapshots_root: Old snapshots dir to try restore from.  When
                None, only the current snapshots dir is consulted.
        """
        import json
        import shutil
        from FVdomain.Load_mesh import H5CFDdataset

        old_rank_dir = Path(old_rank_dir)
        self._rank_dir.mkdir(parents=True, exist_ok=True)

        slot_idx = 0
        while (old_rank_dir / f"slot_{slot_idx}").exists():
            old_slot_dir = old_rank_dir / f"slot_{slot_idx}"
            new_slot_dir = self._rank_dir / f"slot_{slot_idx}"

            with open(old_slot_dir / "meta.json") as f:
                old_meta = json.load(f)
            h5_hash = old_meta["h5_hash"]

            # Ensure the static mesh is available in the CURRENT mesh_cache.
            # All DDP ranks share this dir, so the copy MUST be race-safe:
            # only rank 0 copies, atomically (per-pid temp + os.replace, so a
            # partially written static.bin never appears at the real path), and
            # ready.flag is written LAST as the completion signal. Every rank
            # then WAITS for ready.flag + a size-complete static.bin before
            # opening, so no rank mmaps a half-copied file (which raises
            # "ValueError: mmap length is greater than file size" on resume).
            import time as _time
            current_mesh_dir = self._mesh_cache._cache_dir / h5_hash
            if not (current_mesh_dir / "ready.flag").exists():
                old_mesh_cache_dir = old_rank_dir.parent / "mesh_cache" / h5_hash
                if not (old_mesh_cache_dir.exists() and (old_mesh_cache_dir / "ready.flag").exists()):
                    raise RuntimeError(
                        f"Cannot resume: neither current nor old mesh_cache "
                        f"has static mesh {h5_hash}"
                    )
                if self.rank == 0:
                    current_mesh_dir.mkdir(parents=True, exist_ok=True)
                    for fname in ["manifest.json", "static.bin"]:
                        src = old_mesh_cache_dir / fname
                        dst = current_mesh_dir / fname
                        if not (dst.exists() and dst.stat().st_size == src.stat().st_size):
                            tmp = current_mesh_dir / f"{fname}.tmp.{os.getpid()}"
                            shutil.copy2(str(src), str(tmp))
                            os.replace(str(tmp), str(dst))
                    flag_tmp = current_mesh_dir / f"ready.flag.tmp.{os.getpid()}"
                    flag_tmp.touch()
                    os.replace(str(flag_tmp), str(current_mesh_dir / "ready.flag"))

            # Barrier: wait until the shared mesh is fully present on disk
            # (rank 0 may still be mid-copy). Gated on ready.flag AND an exact
            # static.bin size match against the manifest.
            _manifest_path = current_mesh_dir / "manifest.json"
            _static_path = current_mesh_dir / "static.bin"
            for _ in range(3600):  # up to ~30 min under EDR I/O throttle
                if (current_mesh_dir / "ready.flag").exists() and \
                        _manifest_path.exists() and _static_path.exists():
                    try:
                        _need = json.load(open(_manifest_path))["total_bytes"]
                        if _static_path.stat().st_size == _need:
                            break
                    except (json.JSONDecodeError, KeyError, OSError):
                        pass
                _time.sleep(0.5)
            else:
                raise RuntimeError(
                    f"Timed out waiting for shared mesh {h5_hash} to finish "
                    f"copying into {current_mesh_dir} during resume"
                )

            shared_mesh = SharedMeshStore.open(current_mesh_dir)
            self._mesh_cache._stores[h5_hash] = shared_mesh

            # Copy phi.bin + meta.json into the NEW slot dir (if different).
            new_slot_dir.mkdir(parents=True, exist_ok=True)
            if old_slot_dir.resolve() != new_slot_dir.resolve():
                shutil.copy2(str(old_slot_dir / "phi.bin"), str(new_slot_dir / "phi.bin"))
                with open(new_slot_dir / "meta.json", "w") as f:
                    json.dump(dict(old_meta), f, indent=2)

            slot = CaseSlot.open(new_slot_dir, shared_mesh)

            # Rebuild meta_objects from H5 (strip static tensors — they live
            # in SharedMeshStore).
            h5_path = old_meta["h5_path"]
            temp_dataset = H5CFDdataset(params=self.params, case_list=[h5_path])
            # Same global-index seeding as a fresh load.  Without it a resume
            # falls back to 777 + RANK*1000 + counter, so every slot redraws a
            # DIFFERENT operating point while its phi.bin is restored -- the run
            # would continue on a flow field developed at one Reynolds number
            # against boundary conditions at another, silently.
            temp_dataset.set_pending_global_slot_index(
                global_slot_index(slot_idx, self.rank, self._world_size)
            )
            _ovr = dict(old_meta.get("case_parameters") or {})
            _ovr.update(old_meta.get("sampled_parameters") or {})
            if _ovr:
                temp_dataset.set_pending_overrides(_ovr)
            mesh_dict, _ = temp_dataset[0]
            # The override pins each sampler to a constant.  That is right for
            # the rebuild (this slot's phi WAS developed at that draw) but must
            # not outlive it: a later reset_env has to draw fresh operating
            # points again.  Re-register the ORIGINAL specs for the purely
            # sampled keys; case_parameters (data-driven case identity) stay
            # pinned by design.
            _pm = mesh_dict.get("parametric_manager")
            _orig = (mesh_dict.get("fvconfig") or {}).get("parameter") or {}
            _unpin = {k: _orig[k] for k in (old_meta.get("sampled_parameters") or {})
                      if k in _orig and k not in (old_meta.get("case_parameters") or {})}
            if _pm is not None and _unpin:
                _pm.register_from_toml(_unpin)
            slot.meta_objects = _strip_static_tensors(mesh_dict)
            # Restore the sweep identity the fresh path sets; rebuilding from a
            # bare h5 read loses both, which collapses a multi-case sweep onto
            # one case name and breaks per-case parameter injection.
            for key in ("case_name", "case_parameters"):
                if old_meta.get(key) is not None:
                    slot.meta_objects[key] = old_meta[key]

            self.slots.append(slot)
            self.slot_queue.append(slot_idx)
            slot_idx += 1

        if not self.slots:
            raise RuntimeError(f"No slots found under {old_rank_dir}")

        logger.info("Resumed %d slots from mmap (rank %d)", len(self.slots), self.rank)

        # Try restore phi from snapshots — prefer the OLD snapshot dir
        # (aligned with the checkpoint we're resuming), fall back to current.
        for snap_root in [old_snapshots_root, self._snapshots_root]:
            if snap_root is None:
                continue
            if self._try_restore_snapshot_from(snap_root):
                break

        # Sync params
        self.params.num_channels = self.slots[0].meta_objects["num_channels"]
        self.params.phi_num_channels = self.slots[0].meta_objects["phi_num_channels"]
        self.params.dataset_size = len(self.slots)
        self.params.fvconfig = self.slots[0].meta_objects["fvconfig"]
        self.params.fvschemes = self.slots[0].meta_objects["fvconfig"]["fvSchemes"]

        fvsolution = self.params.fvconfig.get("fvSolution", {})
        for key in ["loss_cont", "loss_mom", "loss_energy", "loss_phi_jump", "loss_pRef"]:
            if key in fvsolution:
                setattr(self.params, key, fvsolution[key])

        # Attach per-case labels mmap on RESUME too (data-driven mode). The
        # non-resume path attaches labels in load_mesh_to_mmap, but the resume
        # path returns here early — without this, _has_labels stays False and
        # the trainer aborts at "data_driven mode requires labels_manifest.json".
        if self.loss_mode == "data_driven":
            manifest_path = self._find_labels_manifest(self.params.dataset_dir)
            if manifest_path is None:
                raise RuntimeError(
                    f"loss_mode='data_driven' requires labels_manifest.json on "
                    f"resume. Searched under {self.params.dataset_dir}/labels/ "
                    f"and parent. Run prepare_labels.py first."
                )
            self._attach_labels_to_slots(manifest_path)
            self._has_labels = True

        return self.params

    def _try_restore_snapshot(self):
        """Convenience wrapper that restores from the current snapshots dir."""
        return self._try_restore_snapshot_from(self._snapshots_root)

    def _try_restore_snapshot_from(self, snap_root) -> bool:
        """Find the latest valid snapshot under ``snap_root`` and restore
        phi data into the slots.

        Returns True on successful restore, False otherwise.

        Reads per-rank meta files ``snapshot_meta_rank_N.json`` (preferred),
        falling back to the legacy shared ``snapshot_meta.json`` for
        snapshots written before the rank-split fix.
        """
        import json as _json
        import numpy as np

        if snap_root is None:
            return False
        snap_root = Path(snap_root)
        if not snap_root.exists():
            return False

        rank_key = f"rank_{self.rank}"

        def _load_rank_meta(snap_dir: Path):
            """Return ``{slot_i: {time_step: ts}}`` for ``self.rank`` from a
            snapshot dir, or ``None`` if neither format is readable.

            Preferred: ``snapshot_meta_rank_{self.rank}.json`` (per-rank).
            Fallback: ``snapshot_meta.json`` (legacy shared file, where
            each rank clobbered the previous rank's atomic rename — so
            only the last writer's data was preserved; we still try it
            in case this rank happened to win the race).
            """
            preferred = snap_dir / f"snapshot_meta_rank_{self.rank}.json"
            if preferred.exists():
                try:
                    data = _json.loads(preferred.read_text())
                    return data.get(rank_key, {})
                except Exception:
                    pass
            legacy = snap_dir / "snapshot_meta.json"
            if legacy.exists():
                try:
                    data = _json.loads(legacy.read_text())
                    return data.get(rank_key, {})
                except Exception:
                    pass
            return None

        snapshot_dirs = sorted(
            [d for d in snap_root.iterdir() if d.is_dir()],
            key=lambda d: d.stat().st_mtime,
            reverse=True,
        )
        for snap_dir in snapshot_dirs:
            rank_meta = _load_rank_meta(snap_dir)
            if rank_meta is None:
                continue

            rank_dir = snap_dir / rank_key
            if not rank_dir.exists():
                continue

            success = True
            for i, slot in enumerate(self.slots):
                phi_file = rank_dir / f"slot_{i}_phi.bin"
                if not phi_file.exists():
                    success = False
                    break
                phi_np = np.fromfile(str(phi_file), dtype=np.float32)
                expected_size = slot._meta["num_cpd_cells"] * slot._meta["phi_channels"]
                if phi_np.size != expected_size:
                    success = False
                    break
                phi_np = phi_np.reshape(slot._meta["num_cpd_cells"], slot._meta["phi_channels"])
                slot._phi_mmap[:] = phi_np
                slot._phi_mmap.flush()

                # Restore time_step from snapshot meta (may be missing if
                # the legacy snapshot_meta.json lost this rank's entry in
                # the atomic-rename race; in that case we keep the slot's
                # existing time_step from its own meta.json).
                slot_key = f"slot_{i}"
                if slot_key in rank_meta:
                    slot._meta["time_step"] = rank_meta[slot_key]["time_step"]
                    _atomic_json_write(slot._slot_dir / "meta.json", slot._meta)

            if success:
                logger.info("Restored phi from snapshot: %s", snap_dir)
                return True

        logger.info("No valid snapshot found under %s; using phi from mmap as-is", snap_root)
        return False

    # ------------------------------------------------------------------
    # Properties for legacy compatibility
    # ------------------------------------------------------------------

    @property
    def meta_pool(self):
        """Return list of meta_objects dicts (one per slot) for legacy compat."""
        return [slot.meta_objects for slot in self.slots]

    @property
    def time_step_pool(self):
        """Return tensor of time steps (one per slot) for legacy compat."""
        return torch.tensor([slot.time_step for slot in self.slots], dtype=torch.float32)

    # ------------------------------------------------------------------
    # Training-loop API
    # ------------------------------------------------------------------

    def _set_reset_env_flag(self, flag=False, rst_time=1):
        self.reset_env_flag = flag
        self.rst_time = rst_time

    def refresh_pool(self, phi_new_cpd_cell, slot_idx):
        """
        Update phi for the given slot(s).

        Args:
            phi_new_cpd_cell: Tensor of new phi values (already CPU, detached).
            slot_idx: Tensor of slot indices (shape [num_graphs_in_batch]).
        """
        if slot_idx.dim() == 0:
            slot_idx = slot_idx.unsqueeze(0)

        # When batched, phi_new_cpd_cell is concatenated for multiple graphs.
        # We need to split by per-graph cpd_cell counts.
        unique_slots = slot_idx.unique()
        if len(unique_slots) == 1:
            idx = unique_slots.item()
            self.slots[idx].update_phi(phi_new_cpd_cell)
            self.slots[idx].increment_time_step()
            self._auto_reset_if_past_labels(idx)
        else:
            # Multiple graphs in batch — need to split phi
            # Determine per-slot sizes from meta_objects
            offset = 0
            for s_idx in slot_idx.tolist():
                # cpd|cell_pos is a static tensor and is not stored
                # in meta_objects — read the cell count from the JSON meta.
                n_cpd = self.slots[s_idx]._meta["num_cpd_cells"]
                self.slots[s_idx].update_phi(phi_new_cpd_cell[offset:offset + n_cpd])
                self.slots[s_idx].increment_time_step()
                self._auto_reset_if_past_labels(s_idx)
                offset += n_cpd

    def _auto_reset_if_past_labels(self, slot_idx):
        """Auto-reset slot to IC when time_step reaches label boundary."""
        slot = self.slots[slot_idx]
        if self.loss_mode == "data_driven" and slot.has_labels:
            if slot.time_step >= slot.n_timesteps:
                init_phi = slot.meta_objects["init_phi_cpd_cell"].to(
                    torch.float32
                )
                slot.reset_phi(init_phi)

    # Reuse helpers from HeteroDataPool (bound as own methods to decouple)
    _update_re_from_patch_velocity = HeteroDataPool._update_re_from_patch_velocity
    _export_mesh_visualization = HeteroDataPool.export_mesh_visualization

    def reset_env(self, plot=False):
        """
        Pop the oldest slot from the queue, optionally export VTK,
        reinit via transform, overwrite phi in-place.
        """
        from Utils.utilities import FaceType

        if not self.slot_queue:
            return

        oldest_idx = self.slot_queue.popleft()
        slot = self.slots[oldest_idx]
        old_mesh = slot.meta_objects

        logger.info("[reset_env] rank=%d, slot=%d, plot=%s, plot_count=%d",
                     self.rank, oldest_idx, plot, self.plot_count)

        # Plot / transform helpers expect static mesh tensors (face_area,
        # face_normal, node_pos, cpd|cell_type, ...) on the mesh dict.  We
        # strip those from slot.meta_objects to save memory, so rebuild a
        # merged dict that re-injects them from SharedMeshStore.
        merged_mesh = _build_merged_mesh(slot)

        if plot:
            if not (self.state_save_dir.find("traing_results") != -1):
                os.makedirs(f"{self.state_save_dir}/traing_results", exist_ok=True)
                self.state_save_dir = f"{self.state_save_dir}/traing_results"

            plot_phi_cpd_cell = slot.phi_tensor.clone()
            mask_interior_cell = (
                slot.get_mesh_tensor("cpd|cell_type").clone().long() == FaceType.NORMAL
            ).squeeze()

            max_u = plot_phi_cpd_cell[~mask_interior_cell, 0:3].max().item()
            min_u = plot_phi_cpd_cell[~mask_interior_cell, 0:3].min().item()
            logger.debug("case: %s, boundary:max_u: %s, min_u: %s",
                          old_mesh['case_name'], max_u, min_u)

            # Re-use helpers from HeteroDataPool (same interface via self)
            self._update_re_from_patch_velocity(merged_mesh, plot_phi_cpd_cell)
            self._export_mesh_visualization(
                merged_mesh, plot_phi_cpd_cell, mask_interior_cell,
                export_patches=self.export_patches,
            )
            # Side-channel diagnostic VTU: if a solver reference has been
            # attached via self._solver_ref (wired in train_with_fvm.py), emit
            # a `_diag.vtu` alongside the main VTM so ParaView can co-render
            # the WLSQ gradient fields. All failures are caught and logged —
            # the diagnostic must never crash training.
            solver_ref = getattr(self, "_solver_ref", None)
            if solver_ref is not None:
                try:
                    from Post_process.to_vtk import export_diagnostic_interior_vtu
                    diag_dict = {}
                    g = getattr(solver_ref, "_grad_uvwpt_hat_cpd_cell", None)
                    if g is not None and g.shape[0] == plot_phi_cpd_cell.shape[0]:
                        # Column 6 = p_rgh. Keep as vec3 at each interior cell.
                        diag_dict["grad_p_rgh_interior"] = g[:, 6, :]
                    if diag_dict:
                        # Derive diag path mirroring the output-path logic in
                        # HeteroDataPool.export_mesh_visualization: nested
                        # case_dir when export_patches is True, flat saving_dir
                        # when False.
                        save_dir_num = self.plot_count // 50
                        saving_dir = f"{self.state_save_dir}/{save_dir_num*50}-{(save_dir_num+1)*50}"
                        case_name = merged_mesh["case_name"]
                        dt = merged_mesh["fvconfig"]["theta_pde"]["dt"]["val"]
                        Re = merged_mesh["fvconfig"]["theta_pde"]["Re"]
                        case_prefix = f"NO.{self.plot_count}_{case_name}_Re={Re:.4f}_dt={dt:.3f}"
                        if self.export_patches:
                            diag_path = f"{saving_dir}/{case_prefix}/{case_prefix}_diag"
                        else:
                            diag_path = f"{saving_dir}/{case_prefix}_diag"
                        export_diagnostic_interior_vtu(
                            mesh=merged_mesh,
                            diag_dict=diag_dict,
                            mask_interior_cell=mask_interior_cell,
                            save_path=diag_path,
                        )
                except Exception as exc:
                    logger.warning(
                        "[reset_env] diagnostic VTU export failed (case=%s): %s",
                        getattr(merged_mesh, "get", lambda *_: None)("case_name", "?"),
                        exc,
                    )
            self.plot_count += 1
            self._plot_env = False

        # Re-init: data-driven rollout restarts from the stored t=0 initial
        # condition (same as what transform produced for this case at load).
        # NOT label[0] — labels exclude t=0 so label[0] is actually t=dt.
        # FVM-loss uses transform to regenerate BCs and initial fields.
        if self.loss_mode == "data_driven":
            init_phi_cpd_cell = slot.meta_objects["init_phi_cpd_cell"].to(torch.float32)
            slot.reset_phi(init_phi_cpd_cell)
        else:
            new_mesh, init_phi_cpd_cell = merged_mesh["transform"](merged_mesh, self.params)
            slot.reset_phi(init_phi_cpd_cell)
            slot.meta_objects = _strip_static_tensors(new_mesh)
            _sp = _sampled_parameters_of(new_mesh)
            if _sp:
                slot.set_meta_entry("sampled_parameters", _sp)

        # Put slot back at end of queue
        self.slot_queue.append(oldest_idx)

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------

    def save_snapshot(self, checkpoint_idx, epoch):
        """Trigger an async phi snapshot."""
        snapshot_dir = self._snapshots_root / f"epoch_{epoch}"
        extra_meta = {"epoch": epoch, "checkpoint_idx": checkpoint_idx}
        # All ranks write their own rank_N/slot_*_phi.bin inside snapshot_dir
        # (each rank has its own slice of the pool), so snapshot() is called
        # on every rank.
        self._snapshot_writer.snapshot(
            self.slots, snapshot_dir, self.rank, extra_meta,
        )
        # Clean up old snapshots.  This deletes SHARED epoch_N directories
        # (rmtree of paths all ranks see), so only rank 0 may run it —
        # otherwise 6 ranks concurrently rmtree the same dirs and whoever
        # loses the race crashes in shutil._rmtree_safe_fd when os.rmdir
        # sees files already gone (on a Cylinder run this
        # killed the whole DDP group via NCCL watchdog).
        if self.rank == 0:
            max_keep = getattr(self.params, "max_snapshots", 3)
            self._snapshot_writer.cleanup(self._snapshots_root, max_keep)

    def close(self):
        """Flush any pending snapshot writes — call before process exit."""
        if self._snapshot_writer is not None:
            self._snapshot_writer.wait()
