#!/usr/bin/env python3
"""
Inferencer Class for OpenFOAM Case Inference

Refactored to inherit from Runner base class.
Assumes data has been pre-processed (H5 files exist).
Uses the same inference logic as Validator.

Supports two modes:
1. Full inference: Infer entire field, optionally interpolate at sensor positions
2. Sensor-only inference: Only infer at sensor positions (fast mode)
"""

import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# Third-party imports
import torch
import torch.distributed as dist
from typing import Dict, List, Any, Optional
import logging
logger = logging.getLogger(__name__)
import h5py
import json
import time
import numpy as np

# Library imports
from config import Config
from Utils.key import Key
from Utils.utilities import FaceType
from Utils.spatial_interpolation import (
    interpolate_at_sensors, 
    compute_sensor_metrics,
    idw_interpolate,
    rbf_interpolate
)
from Main.Data_driven.runner import Runner
from Main.Data_driven.metrics_utils import nrmse_per_field, draw_spread_per_field, aggregate_case_metrics
from Post_process.to_vtk import export_full_mesh_vtu, export_cell_subset_vtu
from Post_process import export_roi as _roi


class Inferencer(Runner):
    """
    Inferencer class for OpenFOAM case inference.

    Inherits from Runner base class.
    Assumes data has been pre-processed (H5 files exist).
    Uses the same inference logic as Validator.

    Note: This class should be instantiated within a distributed worker process
    (see inference.py for the distributed launcher).
    """

    def __init__(
        self,
        config: Config,
        input_keys: List[Key],
        output_keys: List[Key],
        target_keys: List[Key] = None
    ):
        """
        Initialize the Inferencer.

        Args:
            config: Configuration object
            input_keys: List of input keys
            output_keys: List of output keys
            target_keys: List of target keys (optional, used for input normalization)
        """
        # Initialize base class with mode="inference"
        # 🔧 FIX: Pass target_keys to enable input normalization in inference mode
        super().__init__(config, input_keys, output_keys, target_keys=target_keys, mode="inference")

        if self.is_main_process:
            logger.info("🔍 Inferencer initialized")
            logger.info(f"   Sample ratio: {self.config.inference_config.get('tta_sample_ratio', 1.0)}")
            logger.info(f"   Distributed: {self.distributed}")
            logger.info(f"   Device: {self.device}")
            logger.info(f"   Mixed Precision: {self.use_amp}")
            if self.distributed:
                logger.info(f"   Rank: {self.rank}/{self.world_size}")

    def _resolve_field_names(self):
        """Per-field output names from the active field_set config.

        Defaults to the original 5-field [u,v,w,p,T] when no field_set is set,
        so GTC/Slab behavior is unchanged; AirfRANS configs set field_set="airfrans".
        """
        from FVdomain.HomoGraph_dataset import resolve_field_set
        field_set = self._get_run_config().get("field_set", "uvwpT")
        return resolve_field_set(field_set)["names"]


    # ========== Inference Entry Point ==========

    def _export_case_vtu(self, case_name: str, result_data, data_root: str, output_dir: str = None) -> str:
        """
        Export VTU file for a single case.

        Args:
            case_name: Name of the case
            result_data: Result data from inference
            data_root: Root directory containing the dataset
            output_dir: Directory to save VTU output (if None, uses case_path/VTK)

        Returns:
            Path to the saved VTU file
        """
        import time
        vtu_start = time.time()

        # Determine output directory
        if output_dir is None:
            # Default: save to case_path/VTK
            case_path = os.path.join(data_root, case_name)
            case_output_dir = os.path.join(case_path, "VTK")
        else:
            # Custom output_dir: create subdirectory for this case
            # Replace '/' with '_' in case_name for directory name
            case_dir_name = case_name.replace('/', '_')
            case_output_dir = os.path.join(output_dir, case_dir_name)

        vtu_path = os.path.join(case_output_dir, f"{case_name.replace('/', '_')}.vtu")
        os.makedirs(case_output_dir, exist_ok=True)

        logger.info(f"💾 Rank {self.rank}: Exporting VTU for {case_name}")

        try:
            # An ROI restricts what is WRITTEN, never what is inferred. The forward pass
            # above already ran over the whole domain and every value below is the value
            # the unrestricted export would have written for the same cell — this is a
            # selection, not a re-run. Restricting the model's own pivot pool to the box
            # was measured and FAILS: commit 8da9599 records a 3% ROI costing +0.406 nRMSE
            # against a 0.077 fail threshold, with T error 1.03 K — larger than the 0.94 K
            # ACU signal the demo exists to show. See Post_process/export_roi.py.
            roi = _roi.parse_export_roi(os.environ.get(_roi.ENV_EXPORT_ROI))

            # Load CPD cell type to identify interior cells
            h5_path = getattr(result_data, 'h5_path', None)
            if not h5_path:
                sample_name = os.path.dirname(case_name) if '/' in case_name else case_name
                h5_path = os.path.join(data_root, case_name, "h5", f"{sample_name}.h5")
            with h5py.File(h5_path, 'r') as hf:
                cpd_cell_type = torch.from_numpy(hf['cpd|cell_type'][:]).long().squeeze()
                # cpd-cell centroids in the SOLVER frame; only needed to select an ROI.
                # The runner already hard-requires this key (it sizes the accumulator from
                # it), so an ROI run cannot reach here without it.
                cpd_centroid = np.asarray(hf['cpd|centroid'][()]) if roi else None

            # Create mask for interior cells (FaceType.NORMAL = 0)
            mask_interior_cell = (cpd_cell_type == FaceType.NORMAL)

            # Prepare data dictionary for VTU export (only interior cells)
            data_dict = {}

            prediction = getattr(result_data, 'prediction', None)
            target = getattr(result_data, 'target', None)

            if prediction is not None:
                pred_interior = prediction[mask_interior_cell].cpu().numpy()
                if pred_interior.shape[1] >= 3:
                    data_dict["pred_velocity"] = pred_interior[:, 0:3]
                if pred_interior.shape[1] >= 4:
                    data_dict["pred_pressure"] = pred_interior[:, 3]
                if pred_interior.shape[1] >= 5:
                    data_dict["pred_Temperature"] = pred_interior[:, 4]

            if target is not None:
                target_interior = target[mask_interior_cell].cpu().numpy()
                if target_interior.shape[1] >= 3:
                    data_dict["GT_velocity"] = target_interior[:, 0:3]
                if target_interior.shape[1] >= 4:
                    data_dict["GT_pressure"] = target_interior[:, 3]
                if target_interior.shape[1] >= 5:
                    data_dict["GT_Temperature"] = target_interior[:, 4]

            # Export VTU file
            if roi is None:
                # Unchanged full-field path. A receipt from an earlier ROI run beside this
                # VTU would make the adapter believe this file is a subset, so clear it and
                # keep the invariant "a receipt exists iff this VTU is an ROI subset".
                stale = _roi.receipt_path(vtu_path)
                if os.path.exists(stale):
                    os.remove(stale)
                export_full_mesh_vtu(
                    mesh_pos=result_data.mesh_pos,
                    pv_cells_node=result_data.pv_cells_node,
                    pv_cells_type=result_data.pv_cells_type,
                    save_file_path=vtu_path,
                    data_dict=data_dict if data_dict else None
                )
            else:
                self._export_case_vtu_roi(
                    case_name=case_name, roi=roi, vtu_path=vtu_path,
                    result_data=result_data, data_dict=data_dict,
                    cpd_centroid=cpd_centroid, mask_interior_cell=mask_interior_cell)

            vtu_time = time.time() - vtu_start
            logger.info(f"   ✅ VTU saved: {vtu_path} ({vtu_time:.2f}s)")

        except Exception as e:
            logger.error(f"   ❌ Failed to export VTU for {case_name}: {e}")
            import traceback
            traceback.print_exc()
            vtu_path = ""

        return vtu_path

    def _export_case_vtu_roi(self, *, case_name, roi, vtu_path, result_data, data_dict,
                             cpd_centroid, mask_interior_cell) -> None:
        """Write only the cells whose cpd centroid lies inside the ROI box.

        The identity that makes this exact is closed-form: VTK cell j of the full export is
        interior-cpd id ``interior_ids[j]``, because ``data_dict`` rows and the pv cell
        array are both indexed by the same ``mask_interior_cell``. Boundary-face cpd rows
        have no VTK cell at all, so they can never be exported; the receipt reports the
        cpd-in-box count separately so a box that mostly covers patches is visible as such
        rather than looking like a small selection.

        ``depth`` is taken from the FULL mesh here and written into the receipt. Everything
        downstream must reuse that number: a subset's own y_max is smaller, and deriving
        the demo-frame translation from it silently places the subset at the wrong depth.
        """
        mesh_pos = result_data.mesh_pos
        pts = mesh_pos.cpu().numpy() if hasattr(mesh_pos, "cpu") else np.asarray(mesh_pos)
        full_bounds = [float(pts[:, 0].min()), float(pts[:, 0].max()),
                       float(pts[:, 1].min()), float(pts[:, 1].max()),
                       float(pts[:, 2].min()), float(pts[:, 2].max())]
        depth = full_bounds[3]  # solver y_max — the demo-frame depth translation

        s_lo, s_hi, d_lo, d_hi = _roi.resolve_box(roi, depth)

        interior_ids = np.flatnonzero(
            mask_interior_cell.cpu().numpy() if hasattr(mask_interior_cell, "cpu")
            else np.asarray(mask_interior_cell))
        n_cells_total = int(interior_ids.size)

        in_box_cpd = _roi.select_in_box(cpd_centroid, s_lo, s_hi)
        keep_local = _roi.select_in_box(cpd_centroid[interior_ids], s_lo, s_hi)
        keep_cpd = interior_ids[keep_local]

        if keep_local.size == 0:
            # Writing an empty VTU would render as a blank viewport with a healthy log —
            # the exact failure mode this work is meant to remove. Fail loudly instead.
            raise ValueError(
                f"ROI selects 0 of {n_cells_total} exportable cells. box_solver="
                f"{s_lo}-{s_hi} (from box_demo={d_lo}-{d_hi}, depth={depth:.3f}); the "
                f"mesh spans {full_bounds} in the solver frame.")

        export_cell_subset_vtu(
            mesh_pos=result_data.mesh_pos,
            pv_cells_node=result_data.pv_cells_node,
            pv_cells_type=result_data.pv_cells_type,
            cell_ids=keep_local,
            save_file_path=vtu_path,
            data_dict=data_dict if data_dict else None,
            # Provenance that survives the subset: which cpd cell each row came from.
            extra_cell_data={"cpd_id": keep_cpd.astype(np.int64)},
        )

        receipt = _roi.build_receipt(
            case_name=case_name, vtu_path=vtu_path, cpd_ids=keep_cpd,
            n_cells_total=n_cells_total, n_cpd_total=int(cpd_centroid.shape[0]),
            n_cpd_in_box=int(in_box_cpd.size), demo_lo=d_lo, demo_hi=d_hi,
            solver_lo=s_lo, solver_hi=s_hi, full_bounds=full_bounds, depth=depth)
        _roi.write_receipt(vtu_path, receipt)

        logger.info(_roi.receipt_line(
            case_name, int(keep_cpd.size), n_cells_total, d_lo, d_hi, s_lo, s_hi,
            depth, vtu_path))

    def _compute_metrics(self, result_data) -> Dict[str, float]:
        """
        Compute evaluation metrics (Relative L2 + MAE) for each field.

        IMPORTANT: result_data.prediction and result_data.target are ALREADY in
        physical (denormalized) space by the time they reach this function:
          * prediction: Simulator.inference() calls output_denormalizer.inverse()
            on the raw normalized model output before storing it.
          * target: the dataset stores targets as raw physical values; nothing
            in the accumulator denormalizes them.

        Previous versions of this function applied `normalizer.denormalize()`
        a second time here, effectively computing
          denorm(denorm(x)) = x * std^2 + std*mean + mean
        which inflated absolute errors by a factor of the per-field std
        (e.g. T std = 5.46 K, so reported T MAE was ~5.5x the real value).
        Fixed 2026-04-11 by removing the extra denormalize call.

        Args:
            result_data: PyG Data object containing 'prediction' and 'target'
                tensors, both in physical space.

        Returns:
            Dictionary of metrics for each field (physical-space units).
        """
        prediction = result_data.prediction  # [num_nodes, num_fields], physical
        target = result_data.target          # [num_nodes, num_fields], physical

        # Score only the rows some pivot draw actually reached. Uncovered rows carry
        # prediction == 0 AND target == 0 (runner._finalize_predictions / _accumulate_
        # predictions never write them), so they contribute an exact-zero error and DILUTE
        # every metric below by sqrt(coverage). With a whole-domain pivot pool coverage is
        # 1.0 and this mask is a no-op — the published baseline numbers are unchanged. With a
        # restricted (ROI) pool it is the difference between a measurement and an artifact.
        covered = getattr(result_data, 'covered', None)
        is_bnd_full = getattr(result_data, 'is_boundary', None)
        coverage_frac = 1.0
        if covered is not None:
            covered = covered.reshape(-1).bool()
            coverage_frac = float(covered.float().mean())
            if coverage_frac < 1.0:
                prediction = prediction[covered]
                target = target[covered]
                if is_bnd_full is not None:
                    is_bnd_full = is_bnd_full.reshape(-1)[covered]
                if self.is_main_process:
                    logger.info(
                        f"   🎯 {result_data.case_name}: scoring "
                        f"{int(covered.sum())}/{covered.numel()} covered cpd cells "
                        f"(coverage={coverage_frac:.4f}); uncovered rows excluded."
                    )

        # Per-field names from the active field_set (default "uvwpT" -> [u,v,w,p,T];
        # "airfrans" -> [u,v,p,nut]). Keeps per-field metrics aligned with output columns.
        field_names = self._resolve_field_names()

        metrics = {'coverage_frac': coverage_frac}

        # Log physical-space statistics for debugging
        if self.is_main_process:
            logger.info(f"   📊 Statistics for {result_data.case_name}:")
            logger.info(f"      Prediction (Phys): Mean={prediction.mean(0).tolist()}, Std={prediction.std(0).tolist()}")
            logger.info(f"      Target (Phys):     Mean={target.mean(0).tolist()}, Std={target.std(0).tolist()}")

        # Compute per-field metrics directly on the already-physical tensors
        for i, field_name in enumerate(field_names):
            pred_field = prediction[:, i]
            target_field = target[:, i]

            # Relative L2: ||pred - target||_2 / ||target||_2
            numerator = torch.norm(pred_field - target_field, p=2)
            denominator = torch.norm(target_field, p=2)
            relative_l2 = (numerator / (denominator + 1e-8)).item()
            metrics[f'relative_l2_{field_name}'] = relative_l2

            # Mean Absolute Error (MAE): mean(|pred - target|)
            mae = torch.mean(torch.abs(pred_field - target_field)).item()
            metrics[f'mae_{field_name}'] = mae

        # Overall Relative L2 (across all fields)
        numerator_all = torch.norm(prediction - target, p=2)
        denominator_all = torch.norm(target, p=2)
        metrics['relative_l2_overall'] = (numerator_all / (denominator_all + 1e-8)).item()

        # Overall MAE (across all fields)
        metrics['mae_overall'] = torch.mean(torch.abs(prediction - target)).item()

        # nRMSE per field = RMSE / dataset-wide per-channel std.
        # Equivalent to the RMS of the z-scored error; "nrmse_overall" (mean over
        # the 5 channels) is the paper's headline metric.
        field_std = None
        tn = getattr(self.simulator, "target_normalizer", None)
        if tn is not None and getattr(tn, "std", None) is not None:
            field_std = tn.std.detach().reshape(-1)
        if field_std is None:
            # Fallback: per-case GT std. NOTE this makes nRMSE case-relative rather than
            # dataset-wide — only expected when no normalizer is attached (e.g. unit tests);
            # at real inference target_normalizer is always present, so this path is inert.
            field_std = target.std(dim=0)
        metrics.update(nrmse_per_field(prediction, target, field_std, field_names=field_names))

        # nRMSE split by region. The scored node set is interior cells PLUS boundary patch
        # faces, and the boundary is 26% of it — so the pooled number answers two questions at
        # once and lets them be mistaken for each other:
        #
        #   interior : did the model reconstruct the FIELD from the boundary condition?
        #              This is the question the method is about.
        #   boundary : did the model reproduce the boundary values it was handed?
        #              A single-stream model has those values in its own input and can copy
        #              them; a two-stream model, whose sampled query is [pos, unv] with no
        #              field at all, must reconstruct them through the cross-attention.
        #
        # The two also react completely differently to a boundary knockout: zeroing the
        # boundary wrecks the boundary rows' own predictions in EVERY model, including one
        # whose interior provably cannot see the boundary. Pooled, that reads as "the model
        # uses the boundary". Split, it does not.
        is_bnd = is_bnd_full
        if is_bnd is not None:
            is_bnd = is_bnd.reshape(-1).bool().to(prediction.device)
            for region, mask in (("interior", ~is_bnd), ("boundary", is_bnd)):
                if not bool(mask.any()):
                    continue
                sub = nrmse_per_field(
                    prediction[mask], target[mask], field_std, field_names=field_names)
                for k, v in sub.items():
                    metrics[k.replace("nrmse_", f"nrmse_{region}_")] = v
            metrics['boundary_node_frac'] = float(is_bnd.float().mean())

        # Draw spread: how much the model disagrees with ITSELF across pivot draws, on the
        # same normalisation as nRMSE. Absent when every node was covered by a single draw
        # (rho >= 1/world_size), in which case there is nothing to disagree about and we
        # record nothing rather than recording a 0 that would read as "perfectly determined".
        draw_spread = getattr(result_data, 'draw_spread', None)
        if draw_spread is not None:
            metrics.update(draw_spread_per_field(draw_spread, field_std, field_names=field_names))
            metrics['dspread_nodes'] = float(getattr(result_data, 'draw_spread_nodes', 0))
            metrics['dspread_node_frac'] = (
                float(getattr(result_data, 'draw_spread_nodes', 0))
                / max(1.0, float(getattr(result_data, 'draw_spread_total_nodes', 1)))
            )

        # AirfRANS benchmark metrics: volume/surface rel-L2 split (always) and, when a sim root is
        # configured, the official drag/lift coefficients. Spearman ρ is aggregated across cases
        # in inference() (it needs the whole test set), not here.
        if self._get_run_config().get("field_set", "uvwpT") == "airfrans":
            metrics.update(self._airfrans_extra_metrics(prediction, target, result_data))

        return metrics

    def _airfrans_extra_metrics(self, prediction, target, result_data) -> Dict[str, float]:
        """AirfRANS-specific per-case metrics, read straight from the case H5 (rows of the
        accumulated full-case prediction align with the H5 cpd_cell ordering).

        Always returns the volume/surface rel-L2 split. When run_config enables
        ``airfrans_compute_coefficients`` and provides ``airfrans_sim_root`` (the dir holding the
        official AirfRANS ``Dataset/``), also returns the official C_D/C_L (pred vs reference) so
        inference() can rank-correlate them across the test set.
        """
        from Post_process.airfrans_coefficients import vol_surf_relL2, compute_case_coefficients

        out: Dict[str, float] = {}
        h5_path = getattr(result_data, "h5_path", None)
        pred_np = prediction.detach().cpu().numpy()
        targ_np = target.detach().cpu().numpy()

        surf_mask = None
        pos = None
        if h5_path and os.path.exists(h5_path):
            with h5py.File(h5_path, "r") as f:
                cell_type = np.asarray(f["cpd|cell_type"][()]).reshape(-1)
                surf_mask = cell_type != int(FaceType.NORMAL)  # WALL(!=0) == airfoil surface
                pos = np.asarray(f["cpd|centroid"][()])[:, :2]
        if surf_mask is not None and surf_mask.shape[0] == pred_np.shape[0]:
            vs = vol_surf_relL2(pred_np, targ_np, surf_mask)
            out["volume_relL2"] = vs["volume_relL2"]
            out["surface_relL2"] = vs["surface_relL2"]
        elif self.is_main_process:
            logger.warning(f"   ⚠️  airfrans vol/surf split skipped for {result_data.case_name}: "
                           f"surf mask unavailable or misaligned (h5={h5_path}).")

        rc = self._get_run_config()
        if bool(rc.get("airfrans_compute_coefficients", False)):
            sim_root = rc.get("airfrans_sim_root")
            try:
                coef = compute_case_coefficients(
                    name=result_data.case_name, root=sim_root,
                    pred_uvpnut=pred_np, pos=pos, nut_is_asinh=True,
                )
                out.update({f"coef_{k}": v for k, v in coef.items()})
            except Exception as e:  # airfrans lib / sim load / alignment failure — skip this case
                if self.is_main_process:
                    logger.warning(f"   ⚠️  C_L/C_D skipped for {result_data.case_name}: {e}")

        return out

    def inference(self, data_root: str, case_names: List[str] = None, output_dir: str = None) -> Dict[str, Any]:
        """
        Main inference entry point.

        Assumes:
        - Data has been pre-processed (H5 files exist)
        - Checkpoint has been loaded
        - Distributed environment is initialized (if distributed)

        Args:
            data_root: Root directory containing the processed dataset
            case_names: List of case names to infer
            output_dir: Directory to save VTU output (if None, uses case_path/VTK for each case)

        Returns:
            Dictionary containing inference results and metadata
        """
        start_time = time.time()
        timing_breakdown = {}  # 详细计时

        if self.is_main_process:
            logger.info("🚀 Starting inference...")
            logger.info(f"   Cases: {case_names}")
            logger.info(f"   Sample Ratio: {self.config.inference_config.get('tta_sample_ratio', 1.0)}")

        # Load checkpoint
        t0 = time.time()
        self._load_checkpoint(self.config.inference_config.get('checkpoint_path'))
        timing_breakdown['checkpoint_load'] = time.time() - t0

        # Enable VTU export (default on). Set inference_config.save_vtu: false to skip the per-case
        # field dump — cost/nRMSE sweeps don't need VTUs, and skipping saves ~7 s/case of CPU+disk
        # per ratio (and the disk contention that comes with it). Default True preserves prior behavior.
        self.run_config['save_vtu'] = bool(self.config.inference_config.get('save_vtu', True))

        # Use Logger's save_path as output_dir if not specified
        if output_dir is None and hasattr(self, 'logger') and self.logger is not None:
            output_dir = os.path.join(self.logger.saving_path, "vtu_output")
            if self.is_main_process:
                logger.info(f"   VTU output directory: {output_dir}")

        # Create DataLoader (assumes H5 files exist)
        t0 = time.time()
        data_loader = self._create_dataloader(
            data_root=data_root,
            case_names=case_names,
            split="test"
        )
        timing_breakdown['dataloader_create'] = time.time() - t0

        # Pre-initialize accumulator
        t0 = time.time()
        accumulator, _ = self._preinitialize_accumulator(data_loader)
        timing_breakdown['accumulator_init'] = time.time() - t0

        # Run inference
        if self.is_main_process:
            logger.info(f"📊 Phase 2: Running Inference")

        t0 = time.time()
        results = self._run_inference_loop(data_loader, accumulator)
        timing_breakdown['inference_loop'] = time.time() - t0
        loop_timing = getattr(self, "_inference_loop_timing", {})
        timing_breakdown["data_load"] = loop_timing.get("data_load_s", 0.0)
        timing_breakdown["nn_forward"] = loop_timing.get("nn_forward_s", 0.0)
        timing_breakdown["feature_prep"] = float(getattr(self, "feature_prep_seconds", 0.0))

        # Synchronize all processes
        if self.distributed and self.world_size > 1:
            dist.barrier()

        total_inference_time = time.time() - start_time

        # Process results - distribute VTU export among processes
        if self.is_main_process:
            logger.info(f"Phase 3: Exporting VTU Files")

        case_results = {}
        all_case_names = sorted(results.keys())

        # Distribute cases among processes for VTU export
        if self.distributed:
            cases_per_rank = len(all_case_names) // self.world_size
            remainder = len(all_case_names) % self.world_size

            if self.rank < remainder:
                start_idx = self.rank * (cases_per_rank + 1)
                end_idx = start_idx + cases_per_rank + 1
            else:
                start_idx = remainder * (cases_per_rank + 1) + (self.rank - remainder) * cases_per_rank
                end_idx = start_idx + cases_per_rank

            my_case_names = all_case_names[start_idx:end_idx]
            logger.info(f"   Rank {self.rank}: Assigned {len(my_case_names)}/{len(all_case_names)} cases")
        else:
            my_case_names = all_case_names

        # Export VTU files and compute metrics for assigned cases
        t0 = time.time()
        # Honor save_vtu here (this is the actual VTU call site; the flag set earlier was never checked).
        # cost/nRMSE sweeps set save_vtu:false to skip the ~7 s/case field dump. Metrics are always computed.
        _save_vtu = bool(self.run_config.get('save_vtu', True))
        # SCANO_DUMP_RAW=<dir>: additionally dump the raw per-cpd-cell arrays as .npz, so an
        # offline analysis can score ANY cell subset (e.g. a user's ROI box) identically across
        # runs, without going through the VTU's float32 round-trip or its interior-only mask,
        # and without trusting the pooled metric below. Purely additive; unset -> no-op.
        _dump_raw = (os.environ.get("SCANO_DUMP_RAW") or "").strip()
        for case_name in my_case_names:
            result_data = results[case_name]
            vtu_path = self._export_case_vtu(case_name, result_data, data_root, output_dir) if _save_vtu else ""

            if _dump_raw:
                try:
                    import numpy as _np
                    os.makedirs(_dump_raw, exist_ok=True)
                    _p = os.path.join(_dump_raw, f"{case_name.replace('/', '_')}_raw.npz")
                    _arr = {'prediction': result_data.prediction.numpy()}
                    for _k in ('target', 'draw_counts', 'covered', 'is_boundary'):
                        _v = getattr(result_data, _k, None)
                        if _v is not None:
                            _arr[_k] = _v.cpu().numpy() if hasattr(_v, 'cpu') else _np.asarray(_v)
                    _np.savez_compressed(_p, **_arr)
                    logger.info(f"   🧪 raw dump: {_p}")
                except Exception as _e:
                    logger.error(f"   ❌ raw dump failed for {case_name}: {_e}")

            # Compute metrics (Relative L2 for each field)
            metrics = self._compute_metrics(result_data)

            case_results[case_name] = {
                'vtu_path': vtu_path,
                'case_name': case_name,
                'metrics': metrics
            }
        timing_breakdown['vtu_export'] = time.time() - t0

        # Synchronize all processes after VTU export
        if self.distributed and self.world_size > 1:
            dist.barrier()
            gathered_case_results = [None for _ in range(self.world_size)]
            dist.all_gather_object(gathered_case_results, case_results)

            merged_case_results = {}
            for rank_case_results in gathered_case_results:
                if rank_case_results:
                    merged_case_results.update(rank_case_results)
            case_results = merged_case_results

        mean_metrics = aggregate_case_metrics(case_results)

        # AirfRANS: Spearman rank-correlation of predicted vs reference C_D/C_L across the WHOLE
        # test set (needs every case's coefficients, so it cannot live in per-case _compute_metrics).
        # Only fires when coefficients were computed (airfrans_compute_coefficients=true).
        if self._get_run_config().get("field_set", "uvwpT") == "airfrans":
            cd_p, cd_r, cl_p, cl_r = [], [], [], []
            for cr in case_results.values():
                m = cr.get("metrics", {})
                if "coef_cd_pred" in m and "coef_cd_ref" in m:
                    cd_p.append(m["coef_cd_pred"]); cd_r.append(m["coef_cd_ref"])
                    cl_p.append(m["coef_cl_pred"]); cl_r.append(m["coef_cl_ref"])
            if len(cd_p) >= 2:
                from Post_process.airfrans_coefficients import spearman_coeffs
                rho = spearman_coeffs(cd_p, cd_r, cl_p, cl_r)
                mean_metrics["spearman_rho_cd"] = rho["rho_cd"]
                mean_metrics["spearman_rho_cl"] = rho["rho_cl"]
                if self.is_main_process:
                    logger.info(f"   📈 AirfRANS Spearman over {len(cd_p)} cases: "
                                f"ρ(C_D)={rho['rho_cd']:.4f}, ρ(C_L)={rho['rho_cl']:.4f}")

        total_time = time.time() - start_time

        # Build results dictionary
        inference_results = {
            'case_results': case_results,
            'total_cases': len(all_case_names),
            'total_time_seconds': total_time,
            'inference_time_seconds': total_inference_time,
            'checkpoint_path': self.config.inference_config.get('checkpoint_path'),
            'sample_ratio': self.config.inference_config.get('tta_sample_ratio', 1.0),
            'num_gpus': self.world_size,
            'timing_breakdown': timing_breakdown,
            'peak_memory_gib': float(getattr(self, "_peak_memory_gib", 0.0)),
            # Cost columns for the AeroGTO-style table (set by Runner._run_inference_loop).
            'params_m': float(getattr(self, "_model_params_m", 0.0)),
            'flops_g': float(getattr(self, "_inference_flops_g", 0.0)),
            'latency_ms': float(getattr(self, "_inference_latency_ms", 0.0)),
            'mean_metrics': mean_metrics,
        }

        if self.is_main_process:
            logger.info("✅ Inference completed successfully!")
            logger.info(f"   Total cases: {len(all_case_names)}")
            logger.info(f"   Inference time: {total_inference_time:.2f}s")
            logger.info(f"   Total time: {total_time:.2f}s")
            
            # 详细时间分解
            logger.info("")
            logger.info("⏱️  TIME BREAKDOWN (Full Inference)")
            logger.info("-" * 60)
            logger.info(f"   Checkpoint loading:   {timing_breakdown.get('checkpoint_load', 0)*1000:.1f} ms")
            logger.info(f"   DataLoader creation:  {timing_breakdown.get('dataloader_create', 0)*1000:.1f} ms")
            logger.info(f"   Accumulator init:     {timing_breakdown.get('accumulator_init', 0)*1000:.1f} ms")
            logger.info(f"   Inference loop:       {timing_breakdown.get('inference_loop', 0)*1000:.1f} ms")
            logger.info(f"   VTU export:           {timing_breakdown.get('vtu_export', 0)*1000:.1f} ms")
            logger.info(f"   Total:                {total_time*1000:.1f} ms")
            logger.info("-" * 60)

            peak_mem_gib = inference_results['peak_memory_gib']
            sr = float(inference_results['sample_ratio'])
            # Field-aware per-field tokens: for the default field_set="uvwpT" this is byte-identical
            # to the legacy [u,v,w,p,T] line (so aggregate_sweep.py is unaffected); for AirfRANS it
            # emits [u,v,p,nut] and appends the volume/surface rel-L2 split and Spearman ρ.
            sweep_field_names = self._resolve_field_names()
            # A metric that was never computed must print `nan`, NOT 0. These are error metrics --
            # 0 is the BEST possible value, so a `.get(k, 0)` default silently promotes a missing
            # measurement to a perfect score, and the downstream parser has no way to tell the two
            # apart. (aggregate_sweep.py was already fixed to preserve nan; this is the source side
            # of the same bug.) For a run where every metric is present, the output is unchanged.
            _NAN = float("nan")
            mae_tokens = " ".join(f"mae_{n}={mean_metrics.get('mae_'+n,_NAN):.6f}" for n in sweep_field_names)
            nrmse_tokens = " ".join(f"nrmse_{n}={mean_metrics.get('nrmse_'+n,_NAN):.6f}" for n in sweep_field_names)
            airfrans_tokens = ""
            if self._get_run_config().get("field_set", "uvwpT") == "airfrans":
                airfrans_tokens = (
                    f" volume_relL2={mean_metrics.get('volume_relL2',_NAN):.6f} "
                    f"surface_relL2={mean_metrics.get('surface_relL2',_NAN):.6f}"
                )
                if "spearman_rho_cd" in mean_metrics:
                    airfrans_tokens += (
                        f" spearman_rho_cd={mean_metrics['spearman_rho_cd']:.6f} "
                        f"spearman_rho_cl={mean_metrics['spearman_rho_cl']:.6f}"
                    )
            logger.info(
                "SWEEP_SUMMARY "
                f"sample_ratio={sr:.6g} "
                f"{mae_tokens} "
                f"{nrmse_tokens} nrmse_overall={mean_metrics.get('nrmse_overall',_NAN):.6f} "
                # Thesis diagnostics on the SAME machine-readable line, so aggregate_sweep.py's
                # sweep_table.md carries them beside pooled nRMSE. Without these the mandated table
                # shows pooled nRMSE alone, and a single-stream arm's lower pooled number reads as
                # "winning" when it may just be conditional-mean degeneracy on the boundary rows it
                # copies from its own input. nan when the split/spread was not computed for this run.
                f"nrmse_interior_overall={mean_metrics.get('nrmse_interior_overall',_NAN):.6f} "
                f"nrmse_boundary_overall={mean_metrics.get('nrmse_boundary_overall',_NAN):.6f} "
                f"boundary_node_frac={mean_metrics.get('boundary_node_frac',_NAN):.6f} "
                f"dspread_overall={mean_metrics.get('dspread_overall',_NAN):.6f} "
                f"dspread_node_frac={mean_metrics.get('dspread_node_frac',_NAN):.6f} "
                f"feature_prep_s={timing_breakdown.get('feature_prep',0):.4f} "
                f"data_load_s={timing_breakdown.get('data_load',0):.4f} "
                f"nn_forward_s={timing_breakdown.get('nn_forward',0):.4f} "
                f"peak_mem_gib={peak_mem_gib:.3f} "
                f"params_m={inference_results['params_m']:.4f} "
                f"flops_g={inference_results['flops_g']:.4f} "
                f"latency_ms={inference_results['latency_ms']:.4f} "
                f"num_cases={inference_results['total_cases']}"
                f"{airfrans_tokens}"
            )

            # Print metrics for each case — explicitly labeled PER-CASE so these lines are never
            # mistaken for the across-case aggregate (that is SWEEP_SUMMARY / the banner below).
            logger.info("\n📊 PER-CASE Evaluation Metrics (one block per test case — NOT the aggregate):")
            logger.info("=" * 80)
            for case_name, case_result in case_results.items():
                if 'metrics' in case_result:
                    metrics = case_result['metrics']
                    logger.info(f"\n🔹 Case: {case_name} (per-case)")
                    logger.info(f"   Overall: RelL2={metrics.get('relative_l2_overall', 0.0):.6f} | MAE={metrics.get('mae_overall', 0.0):.6f} | nRMSE={metrics.get('nrmse_overall', 0.0):.6f}")
                    logger.info(f"   u:       RelL2={metrics.get('relative_l2_u', 0.0):.6f} | MAE={metrics.get('mae_u', 0.0):.6f} | nRMSE={metrics.get('nrmse_u', 0.0):.6f}")
                    logger.info(f"   v:       RelL2={metrics.get('relative_l2_v', 0.0):.6f} | MAE={metrics.get('mae_v', 0.0):.6f} | nRMSE={metrics.get('nrmse_v', 0.0):.6f}")
                    logger.info(f"   w:       RelL2={metrics.get('relative_l2_w', 0.0):.6f} | MAE={metrics.get('mae_w', 0.0):.6f} | nRMSE={metrics.get('nrmse_w', 0.0):.6f}")
                    logger.info(f"   p:       RelL2={metrics.get('relative_l2_p', 0.0):.6f} | MAE={metrics.get('mae_p', 0.0):.6f} | nRMSE={metrics.get('nrmse_p', 0.0):.6f}")
                    logger.info(f"   T:       RelL2={metrics.get('relative_l2_T', 0.0):.6f} | MAE={metrics.get('mae_T', 0.0):.6f} | nRMSE={metrics.get('nrmse_T', 0.0):.6f}")
            logger.info("=" * 80)
            n_agg = inference_results['total_cases']
            logger.info("█" * 80)
            logger.info(f"█ AGGREGATE (mean over ALL {n_agg} test cases) — THE headline numbers:")
            logger.info(f"█   nRMSE_avg(5-field mean) = {mean_metrics.get('nrmse_overall', 0.0):.6f}")
            logger.info(f"█   per-field nRMSE: u={mean_metrics.get('nrmse_u',0):.4f} v={mean_metrics.get('nrmse_v',0):.4f} "
                        f"w={mean_metrics.get('nrmse_w',0):.4f} p={mean_metrics.get('nrmse_p',0):.4f} T={mean_metrics.get('nrmse_T',0):.4f}")
            if 'nrmse_interior_overall' in mean_metrics:
                bf = mean_metrics.get('boundary_node_frac', 0.0)
                logger.info(
                    f"█   nRMSE_INTERIOR = {mean_metrics['nrmse_interior_overall']:.6f}   "
                    f"nRMSE_BOUNDARY = {mean_metrics.get('nrmse_boundary_overall', 0.0):.6f}   "
                    f"(boundary = {bf*100:.1f}% of scored nodes)")
                logger.info("█   INTERIOR is the one the method is about: reconstructing the field")
                logger.info("█   FROM the boundary. BOUNDARY is reproducing the values it was handed.")
            if 'dspread_overall' in mean_metrics:
                logger.info(
                    f"█   DRAW SPREAD = {mean_metrics['dspread_overall']:.6f}  (same scale as nRMSE; "
                    f"over {mean_metrics.get('dspread_node_frac', 0.0)*100:.1f}% of nodes seen 2+ times)")
                logger.info("█   how much the model disagrees with ITSELF depending on which pivots")
                logger.info("█   were drawn. 0 would mean the sampled problem has a unique answer.")
            logger.info("█   (machine-readable: the SWEEP_SUMMARY line above; tables: aggregate_sweep.py)")
            logger.info("█" * 80)

            # Save inference results to JSON file
            if hasattr(self, 'logger') and self.logger is not None:
                import json
                # An intervention run must never land in the canonical filename. On 2026-07-25 a
                # GTC_BC_INTERVENTION=shuffle run overwrote this arm's clean inference_results.json
                # with corrupted-boundary numbers and NO marker, so 0.280 sat there looking like a
                # normal result. Route interventions to their own file and stamp the mode inside it.
                from Main.Data_driven import bc_intervention as _bci
                _iv = _bci.mode()
                _suffix = f"_BCINTERVENTION_{_iv.replace(':', '')}" if _iv else ""
                results_path = os.path.join(
                    self.logger.saving_path, f"inference_results{_suffix}.json")

                # Convert results to JSON-serializable format
                json_results = {
                    'total_cases': inference_results['total_cases'],
                    'total_time_seconds': inference_results['total_time_seconds'],
                    'inference_time_seconds': inference_results['inference_time_seconds'],
                    'checkpoint_path': inference_results['checkpoint_path'],
                    'sample_ratio': inference_results['sample_ratio'],
                    'num_gpus': inference_results['num_gpus'],
                    'timing_breakdown': inference_results['timing_breakdown'],
                    'peak_memory_gib': inference_results['peak_memory_gib'],
                    'mean_metrics': inference_results['mean_metrics'],
                    'cases': {}
                }
                if _iv:
                    json_results['bc_intervention'] = {
                        'mode': _iv,
                        'fired': _bci.fired_sites() if hasattr(_bci, 'fired_sites') else None,
                        'warning': 'NOT a clean run — the boundary input was deliberately corrupted.',
                    }

                for case_name, case_result in case_results.items():
                    json_results['cases'][case_name] = {
                        'vtu_path': case_result.get('vtu_path', ''),
                        'metrics': case_result.get('metrics', {})
                    }

                with open(results_path, 'w') as f:
                    json.dump(json_results, f, indent=2)

                logger.info(f"\n💾 Results saved: {results_path}")

        return inference_results

    def sensor_inference(
        self, 
        data_root: str, 
        case_names: List[str] = None,
        sensor_positions: Optional[Dict[str, Dict]] = None,
        interpolation_k: int = 4,
        interpolation_method: str = "rbf",
        full_inference_time: float = None,
        benchmark_iterations: int = 0
    ) -> Dict[str, Any]:
        """
        Sensor-only inference mode.
        
        Only infers at sensor positions using k-hop subgraph centered at sensor nodes.
        Much faster than full inference as it only processes a small subgraph.
        
        Args:
            data_root: Root directory containing the processed dataset
            case_names: List of case names to infer
            sensor_positions: Dict of sensor positions
            interpolation_k: K for KNN interpolation
            interpolation_method: "rbf" or "idw"
            full_inference_time: Time for full inference (for speedup calculation)
            benchmark_iterations: If > 0, run forward pass this many times and report timing
            
        Returns:
            Dictionary containing sensor inference results
        """
        start_time = time.time()
        timing_breakdown = {}  # 详细计时
        
        if sensor_positions is None:
            sensor_positions = self.config.inference_config.get('sensor_positions', {})
        
        if not sensor_positions:
            raise ValueError("No sensor positions provided for sensor-only inference")
        
        benchmark_mode = benchmark_iterations > 0
        
        if self.is_main_process:
            logger.info("🎯 Starting Sensor-only Inference...")
            logger.info(f"   Sensors: {len(sensor_positions)}")
            logger.info(f"   Interpolation: {interpolation_method.upper()}, K={interpolation_k}")
            if benchmark_mode:
                logger.info(f"   🔬 Benchmark mode: {benchmark_iterations} iterations")
        
        # Load checkpoint
        t0 = time.time()
        self._load_checkpoint(self.config.inference_config.get('checkpoint_path'))
        timing_breakdown['checkpoint_load'] = time.time() - t0
        
        # Disable VTU export for sensor-only mode
        self.run_config['save_vtu'] = False
        
        # Create DataLoader with sensor_only mode
        t0 = time.time()
        data_loader = self._create_sensor_dataloader(
            data_root=data_root,
            case_names=case_names,
            sensor_positions=sensor_positions
        )
        timing_breakdown['dataloader_create'] = time.time() - t0
        
        # Run sensor inference
        sensor_results = {}
        self.simulator.eval()
        
        # Benchmark timing storage
        benchmark_times = []
        timing_breakdown['data_loading'] = 0
        timing_breakdown['data_to_gpu'] = 0
        timing_breakdown['forward_pass'] = 0
        timing_breakdown['warmup'] = 0
        timing_breakdown['interpolation'] = 0
        
        is_first_batch = True  # 只在第一个 batch 做 warmup
        
        t_loop_start = time.time()
        with torch.no_grad():
            for batch_idx, batch_data in enumerate(data_loader):
                t_batch_start = time.time()
                if batch_data is None:
                    continue
                
                timing_breakdown['data_loading'] += time.time() - t_batch_start
                
                t0 = time.time()
                batch_data = batch_data.to(self.device)
                torch.cuda.synchronize()
                timing_breakdown['data_to_gpu'] += time.time() - t0
                
                # case_name 可能是 list（batch 模式）或 string
                case_name_attr = batch_data.case_name if hasattr(batch_data, 'case_name') else f"case_{batch_idx}"
                case_name = case_name_attr[0] if isinstance(case_name_attr, list) else case_name_attr
                
                # ================================================================
                # Benchmark mode: run forward pass multiple times
                # ================================================================
                if benchmark_mode:
                    # Warmup runs - 只在第一个 batch 做，避免重复编译开销
                    if is_first_batch:
                        t0 = time.time()
                        warmup_runs = max(10, benchmark_iterations // 10)
                        if self.is_main_process:
                            logger.info(f"   🔥 Warming up ({warmup_runs} runs)...")
                        for _ in range(warmup_runs):
                            if self.use_amp:
                                with self._autocast_context():
                                    _ = self.simulator.inference(batch_data.clone())
                            else:
                                _ = self.simulator.inference(batch_data.clone())
                        
                        torch.cuda.synchronize()
                        timing_breakdown['warmup'] += time.time() - t0
                        is_first_batch = False
                    
                    # Timed runs
                    iteration_times = []
                    for i in range(benchmark_iterations):
                        torch.cuda.synchronize()
                        t_start = time.perf_counter()
                        
                        if self.use_amp:
                            with self._autocast_context():
                                result = self.simulator.inference(batch_data.clone())
                        else:
                            result = self.simulator.inference(batch_data.clone())
                        
                        torch.cuda.synchronize()
                        t_end = time.perf_counter()
                        iteration_times.append(t_end - t_start)
                    
                    # 取后 10 次的平均（排除可能的波动）
                    last_n = min(10, benchmark_iterations)
                    avg_time = sum(iteration_times[-last_n:]) / last_n
                    min_time = min(iteration_times[-last_n:])
                    max_time = max(iteration_times[-last_n:])
                    
                    benchmark_times.append({
                        'case_name': case_name,
                        'iterations': benchmark_iterations,
                        'warmup_runs': warmup_runs,
                        'avg_time_ms': avg_time * 1000,
                        'min_time_ms': min_time * 1000,
                        'max_time_ms': max_time * 1000,
                        'all_times_ms': [t * 1000 for t in iteration_times]
                    })
                    
                    if self.is_main_process:
                        logger.info(f"   🔬 {case_name}: Forward time = {avg_time*1000:.2f}ms (min={min_time*1000:.2f}ms, max={max_time*1000:.2f}ms)")
                    
                    batch_data = result
                else:
                    # Normal forward pass
                    torch.cuda.synchronize()
                    t0 = time.time()
                    if self.use_amp:
                        with self._autocast_context():
                            batch_data = self.simulator.inference(batch_data)
                    else:
                        batch_data = self.simulator.inference(batch_data)
                    torch.cuda.synchronize()
                    timing_breakdown['forward_pass'] += time.time() - t0
                
                # Extract predictions and targets
                prediction = batch_data.prediction  # [N_subgraph, 5]
                target = batch_data.y if hasattr(batch_data, 'y') else None
                cpd_centroid = batch_data.cpd_centroid  # [N_subgraph, 3]
                
                # Get sensor metadata
                sensor_names = batch_data.sensor_names
                sensor_local_indices = batch_data.sensor_local_indices
                
                # Interpolate at sensor positions
                t0 = time.time()
                pred_at_sensors = interpolate_at_sensors(
                    sensor_positions=sensor_positions,
                    source_pos=cpd_centroid,
                    source_values=prediction,
                    k=interpolation_k,
                    method=interpolation_method,
                    field_names=self._resolve_field_names()
                )
                
                # Interpolate ground truth if available
                gt_at_sensors = None
                if target is not None:
                    gt_at_sensors = interpolate_at_sensors(
                        sensor_positions=sensor_positions,
                        source_pos=cpd_centroid,
                        source_values=target,
                        k=interpolation_k,
                        method=interpolation_method,
                        field_names=self._resolve_field_names()
                    )
                
                timing_breakdown['interpolation'] += time.time() - t0
                
                # Compute metrics
                metrics = None
                if gt_at_sensors is not None:
                    metrics = compute_sensor_metrics(pred_at_sensors, gt_at_sensors)
                
                # Store results for this case
                sensor_results[case_name] = {
                    'predictions': pred_at_sensors,
                    'ground_truth': gt_at_sensors,
                    'metrics': metrics,
                    'subgraph_size': prediction.shape[0]
                }
                
                if self.is_main_process:
                    logger.info(f"   ✅ {case_name}: subgraph_size={prediction.shape[0]}")
        
        timing_breakdown['inference_loop'] = time.time() - t_loop_start
        total_time = time.time() - start_time
        
        # Calculate speedup if full inference time is provided
        speedup = full_inference_time / total_time if full_inference_time else None
        
        # Build final results
        final_results = {
            'inference_mode': 'sensor_only',
            'inference_time_seconds': total_time,
            'full_inference_time_seconds': full_inference_time,
            'speedup': speedup,
            'num_sensors': len(sensor_positions),
            'interpolation_method': interpolation_method,
            'interpolation_k': interpolation_k,
            'cases': {}
        }
        
        # Add benchmark results if available
        if benchmark_mode and benchmark_times:
            avg_forward_time_ms = sum(b['avg_time_ms'] for b in benchmark_times) / len(benchmark_times)
            final_results['benchmark'] = {
                'iterations': benchmark_iterations,
                'avg_forward_time_ms': avg_forward_time_ms,
                'per_case': benchmark_times
            }
        
        for case_name, case_data in sensor_results.items():
            final_results['cases'][case_name] = {
                'subgraph_size': case_data['subgraph_size'],
                'sensors': {}
            }
            
            for sensor_name, pos in sensor_positions.items():
                sensor_entry = {
                    'position': {'x': pos['x'], 'y': pos['y'], 'z': pos['z']},
                    'aisle': pos.get('aisle', 'unknown'),
                    'prediction': case_data['predictions'].get(sensor_name, {}),
                }
                
                if case_data['ground_truth']:
                    sensor_entry['ground_truth'] = case_data['ground_truth'].get(sensor_name, {})
                
                if case_data['metrics']:
                    sensor_entry['mae'] = case_data['metrics'].get(sensor_name, {}).get('mae', {})
                    sensor_entry['relative_error'] = case_data['metrics'].get(sensor_name, {}).get('relative_error', {})
                
                final_results['cases'][case_name]['sensors'][sensor_name] = sensor_entry
        
        # Log results
        if self.is_main_process:
            logger.info("=" * 80)
            logger.info("🎯 SENSOR INFERENCE RESULTS")
            logger.info("=" * 80)
            logger.info(f"   Total time: {total_time:.3f}s")
            if speedup:
                logger.info(f"   Speedup vs full inference: {speedup:.1f}x")
            
            # 详细时间分解
            logger.info("")
            logger.info("⏱️  TIME BREAKDOWN")
            logger.info("-" * 60)
            logger.info(f"   Checkpoint loading:  {timing_breakdown.get('checkpoint_load', 0)*1000:.1f} ms")
            logger.info(f"   DataLoader creation: {timing_breakdown.get('dataloader_create', 0)*1000:.1f} ms")
            logger.info(f"   Data loading (H5):   {timing_breakdown.get('data_loading', 0)*1000:.1f} ms")
            logger.info(f"   Data to GPU:         {timing_breakdown.get('data_to_gpu', 0)*1000:.1f} ms")
            logger.info(f"   Warmup runs:         {timing_breakdown.get('warmup', 0)*1000:.1f} ms")
            logger.info(f"   Forward passes:      {timing_breakdown.get('forward_pass', 0)*1000:.1f} ms")
            logger.info(f"   Interpolation:       {timing_breakdown.get('interpolation', 0)*1000:.1f} ms")
            logger.info(f"   Inference loop total:{timing_breakdown.get('inference_loop', 0)*1000:.1f} ms")
            
            # Benchmark summary
            if benchmark_mode and benchmark_times:
                avg_forward_ms = sum(b['avg_time_ms'] for b in benchmark_times) / len(benchmark_times)
                min_forward_ms = min(b['min_time_ms'] for b in benchmark_times)
                max_forward_ms = max(b['max_time_ms'] for b in benchmark_times)
                
                logger.info("")
                logger.info("🔬 BENCHMARK RESULTS (Pure Forward Pass)")
                logger.info("-" * 60)
                logger.info(f"   Iterations per case: {benchmark_iterations}")
                logger.info(f"   Average forward time: {avg_forward_ms:.2f} ms")
                logger.info(f"   Min forward time: {min_forward_ms:.2f} ms")
                logger.info(f"   Max forward time: {max_forward_ms:.2f} ms")
                logger.info(f"   Throughput: {1000/avg_forward_ms:.1f} inferences/sec")
            
            # Print per-case sensor results
            for case_name, case_data in sensor_results.items():
                logger.info(f"\n📦 Case: {case_name}")
                if case_data['metrics']:
                    # Calculate average MAE per field
                    avg_mae = {'u': 0, 'v': 0, 'w': 0, 'p': 0, 'T': 0}
                    for sensor_name, sensor_metrics in case_data['metrics'].items():
                        for field, value in sensor_metrics.get('mae', {}).items():
                            avg_mae[field] += value
                    
                    n_sensors = len(case_data['metrics'])
                    for field in avg_mae:
                        avg_mae[field] /= n_sensors
                    
                    logger.info(f"   Average MAE: u={avg_mae['u']:.4f}, v={avg_mae['v']:.4f}, w={avg_mae['w']:.4f}, p={avg_mae['p']:.2f}, T={avg_mae['T']:.2f}")
            
            logger.info("=" * 80)
            
            # Save results to JSON
            if hasattr(self, 'logger') and self.logger is not None:
                results_path = os.path.join(self.logger.saving_path, "sensor_inference_results.json")
                with open(results_path, 'w') as f:
                    json.dump(final_results, f, indent=2)
                logger.info(f"\n💾 Sensor results saved: {results_path}")
        
        return final_results
    
    def _create_sensor_dataloader(
        self,
        data_root: str,
        case_names: Optional[List[str]] = None,
        sensor_positions: Optional[Dict[str, Dict]] = None
    ):
        """
        Create DataLoader for sensor-only inference mode.
        
        Args:
            data_root: Data root directory
            case_names: Optional list of case names
            sensor_positions: Sensor position configurations
            
        Returns:
            DataLoader configured for sensor-only mode
        """
        from FVdomain.HomoGraph_dataset import HomoGraphDataset
        from torch_geometric.loader import DataLoader as PyGDataLoader
        
        khop = self.config.inference_config.get('tta_khop', 2)
        
        if self.is_main_process:
            logger.info(f"📊 Creating Sensor-only DataLoader...")
            logger.info(f"   K-hop: {khop}")
        
        # Create dataset with sensor_only mode
        dataset = HomoGraphDataset(
            root=data_root,
            subsampling=True,  # Enable subsampling for k-hop
            khop=khop,
            sample_ratio=1.0,  # Not used in sensor_only mode
            split="test",
            case_names=case_names,
            rank=self.rank,
            world_size=self.world_size,
            sensor_positions=sensor_positions,
            sensor_only=True
        )
        
        # Create DataLoader
        data_loader = PyGDataLoader(
            dataset=dataset,
            batch_size=1,  # Process one case at a time for sensor mode
            shuffle=False,
            num_workers=0
        )
        
        if self.is_main_process:
            logger.info(f"   ✅ DataLoader: {len(data_loader)} cases")
        
        return data_loader
