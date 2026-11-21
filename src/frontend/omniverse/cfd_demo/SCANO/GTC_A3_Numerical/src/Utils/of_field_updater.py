"""
OpenFOAM Field Updater

Comprehensive manager for inference data preparation:
1. Generates 1 time step OpenFOAM output using process_single_case
2. Optionally creates new H5 file using DataProcessor
3. Updates field data in existing H5 file

Exposes a single run() API for all operations.
"""

import os
import json
import h5py
import torch
import numpy as np
from typing import Dict, Optional, Sequence, Union
import logging
logger = logging.getLogger(__name__)


class OpenFOAMFieldUpdater:
    """
    Comprehensive manager for inference data preparation.

    Workflow:
    1. Generate 1 time step OpenFOAM output using process_single_case
    2. Optionally create new H5 file using DataProcessor (if use_data_processor=True)
    3. Update field data in existing H5 file (if use_data_processor=False)

    Key feature: Correctly places field data into CPD positions using patch_list

    Usage:
        updater = OpenFOAMFieldUpdater(
            case_path="dataset/Alibaba-Shanghai-Inference/base",
            case_name="base",
            h5_path="dataset/Alibaba-Shanghai-Inference/base/h5/base.h5",
            use_data_processor=False  # Only update fields, don't regenerate H5
        )
        success = updater.run()
    """

    def __init__(
        self,
        case_path: str,
        case_name: str,
        h5_path: str,
        use_data_processor: bool = False,
        time_value: float = 1.0,
        run_openfoam: bool = True,
        run_data_updater: bool = True
    ):
        """
        Initialize InferenceFieldUpdater.

        Args:
            case_path: Path to OpenFOAM case
            case_name: Name of the case
            h5_path: Path to H5 file (to create or update)
            use_data_processor: If True, use DataProcessor to create new H5 file
                               If False, only update field data in existing H5
            time_value: Time step to read from VTK (default: 1.0)
            run_openfoam: Whether to run OpenFOAM simulation to generate output
            run_data_updater: Whether to run the H5 data updater step
        """
        self.case_path = case_path
        self.case_name = case_name
        self.h5_path = h5_path
        self.use_data_processor = use_data_processor
        self.time_value = time_value
        self.run_openfoam = run_openfoam
        self.run_data_updater = run_data_updater

    def run(self) -> bool:
        """
        Main entry point: Execute the complete inference data preparation workflow.

        Workflow:
        1. Generate 1 time step OpenFOAM output (auto-detect if needed)
        2. Create new H5 (if use_data_processor=True) or update existing H5
        3. Verify the result

        Auto-detection logic:
        - If 1 time step folder does not exist, force run_openfoam=True
        - If H5 file does not exist and use_data_processor=False, auto switch to True
        - If VTK folder does not exist but 1 time step exists, regenerate VTK

        Returns:
            bool: True if successful, False otherwise
        """
        try:
            logger.info("📊 OpenFOAMFieldUpdater: Starting data preparation workflow")
            logger.info(f"   Case: {self.case_name}")
            logger.info(f"   Path: {self.case_path}")
            logger.info(f"   H5 file: {self.h5_path}")
            logger.info(f"   Config: run_openfoam={self.run_openfoam}, run_data_updater={self.run_data_updater}, use_data_processor={self.use_data_processor}")

            # ================================================================
            # Step 1: Auto-detect and generate 1 time step OpenFOAM output
            # ================================================================
            time_1_path = os.path.join(self.case_path, "1")
            vtk_path = os.path.join(self.case_path, "VTK")

            # Auto-detect: If 1 time step folder does not exist, force OpenFOAM simulation
            if not os.path.exists(time_1_path):
                logger.warning(f"   ⚠️  1 time step folder not found, auto-enabling OpenFOAM simulation")
                self.run_openfoam = True
            elif not os.path.exists(vtk_path):
                # 1 time step exists but VTK doesn't - need to regenerate
                logger.warning(f"   ⚠️  VTK folder not found, auto-enabling OpenFOAM simulation to regenerate VTK")
                self.run_openfoam = True
            else:
                logger.info(f"   ✅ 1 time step and VTK folders exist")

            # Run OpenFOAM simulation if needed
            if self.run_openfoam:
                logger.info("📖 Step 1: Running OpenFOAM simulation and VTK conversion...")
                if not self._run_openfoam_simulation():
                    logger.error("❌ Failed to generate OpenFOAM output")
                    return False
            else:
                logger.info("📖 Step 1: Skipping OpenFOAM simulation (data exists)")

            # ================================================================
            # Step 2: Auto-detect and create/update H5 file
            # ================================================================
            # Auto-detect: If H5 file does not exist, force full mode
            if not os.path.exists(self.h5_path):
                if not self.use_data_processor:
                    logger.warning(f"   ⚠️  H5 file not found at {self.h5_path}")
                    logger.warning(f"   ⚠️  Auto-switching to full mode (use_data_processor=True)")
                    self.use_data_processor = True
                if not self.run_data_updater:
                    logger.warning(f"   ⚠️  H5 file required but run_data_updater=False, auto-enabling")
                    self.run_data_updater = True

            if self.run_data_updater:
                if self.use_data_processor:
                    logger.info("📖 Step 2: Creating new H5 file using DataProcessor (full mode)...")
                    if not self._create_h5_with_data_processor():
                        logger.error("❌ Failed to create H5 file")
                        return False
                else:
                    logger.info("📖 Step 2: Updating field data in existing H5 file (optimized mode)...")
                    if not self._update_existing_h5():
                        logger.error("❌ Failed to update H5 file")
                        return False
            else:
                logger.info("📖 Step 2: Skipping H5 data update (dry run mode)")

            # ================================================================
            # Step 3: Verify the result
            # ================================================================
            logger.info("📖 Step 3: Verifying H5 structure...")
            if not self.verify_h5_structure(self.h5_path):
                logger.error("❌ H5 file verification failed")
                return False

            logger.info("🎉 OpenFOAMFieldUpdater: Workflow completed successfully!")
            return True

        except Exception as e:
            logger.error(f"❌ OpenFOAMFieldUpdater failed: {e}")
            import traceback
            traceback.print_exc()
            return False

    def _run_openfoam_simulation(self) -> bool:
        """
        Generate 1 time step OpenFOAM output using FieldDataPreprocessor.

        Returns:
            bool: True if successful
        """
        try:
            from Parse_mesh.parse_fielddata import FieldDataPreprocessor

            logger.info(f"   Running OpenFOAM simulation for {self.case_name}...")
            logger.info(f"   This will generate 1 time step output and convert to VTK")

            preprocessor = FieldDataPreprocessor(self.case_path)
            success = preprocessor.process(run_simulation=True)

            if success:
                logger.info(f"   ✅ OpenFOAM simulation completed")
                return True
            else:
                logger.error(f"   ❌ OpenFOAM simulation failed")
                return False

        except Exception as e:
            logger.error(f"   ❌ Failed to run OpenFOAM simulation: {e}")
            import traceback
            traceback.print_exc()
            return False

    def _create_h5_with_data_processor(self) -> bool:
        """
        Create new H5 file using DataProcessor (full mode).

        This method processes the OpenFOAM case from scratch:
        1. Parse mesh geometry
        2. Build CPD graph
        3. Read field data
        4. Export to H5

        Returns:
            bool: True if successful
        """
        try:
            from Parse_mesh.parse_openfoam_refactored import OpenFOAMManager

            logger.info(f"   Creating new H5 file for {self.case_name}...")

            # Process mesh and convert to H5 format
            mesh = OpenFOAMManager(case_path=self.case_path, plt_edge_vis=False)
            mesh._process_mesh_pipeline()

            # Create output directory
            h5_output_dir = os.path.dirname(self.h5_path)
            os.makedirs(h5_output_dir, exist_ok=True)

            # Export to HDF5
            mesh.export_to_hdf5(output_path=self.h5_path, case_name=self.case_name)

            logger.info(f"   ✅ H5 file created successfully: {self.h5_path}")
            return True

        except Exception as e:
            logger.error(f"   ❌ Failed to create H5 file: {e}")
            import traceback
            traceback.print_exc()
            return False

    def _update_existing_h5(self) -> bool:
        """
        Update field data in existing H5 file.

        Returns:
            bool: True if successful
        """
        try:
            # Verify H5 structure first
            if not OpenFOAMFieldUpdater.verify_h5_structure(self.h5_path):
                logger.error(f"   ❌ H5 file structure invalid: {self.h5_path}")
                return False

            # Update field data
            success = OpenFOAMFieldUpdater.update_h5_field_data(
                h5_path=self.h5_path,
                case_path=self.case_path,
                time_value=self.time_value
            )

            if success:
                logger.info(f"   ✅ H5 field data updated successfully")
                return True
            else:
                logger.error(f"   ❌ Failed to update H5 field data")
                return False

        except Exception as e:
            logger.error(f"   ❌ Failed to update existing H5: {e}")
            import traceback
            traceback.print_exc()
            return False

    @staticmethod
    def _place_field_data_to_cpd(
        field_result: Dict,
        num_cpd_cells: int,
        num_internal_cells: int,
        patch_list: Dict,
        field_dim: int
    ) -> torch.Tensor:
        """
        Place field data into CPD positions correctly.

        Args:
            field_result: Dict with 'internal' and 'boundary' keys
            num_cpd_cells: Total number of CPD cells
            num_internal_cells: Number of internal cells
            patch_list: Dict mapping patch names to CPD index ranges
            field_dim: Dimension of the field (1 for scalar, 3 for vector)

        Returns:
            torch.Tensor: Field data with shape [num_cpd_cells, field_dim]
        """
        # Initialize CPD field tensor
        cpd_field = torch.zeros((num_cpd_cells, field_dim))

        # Place internal field data
        internal_data = field_result.get('internal', torch.zeros((0, field_dim)))
        if internal_data.shape[0] > 0:
            cpd_field[:num_internal_cells] = internal_data

        # Place boundary field data using patch_list
        boundary_data = field_result.get('boundary', {})
        for patch_name, patch_tensor in boundary_data.items():
            if patch_name in patch_list:
                patch_info = patch_list[patch_name]
                start_idx = patch_info.get('start_idx_cpd_cell')
                end_idx = patch_info.get('end_idx_cpd_cell')

                if start_idx is not None and end_idx is not None:
                    if patch_tensor.shape[0] == (end_idx - start_idx):
                        cpd_field[start_idx:end_idx] = patch_tensor
                    else:
                        logger.warning(
                            f"Patch {patch_name} size mismatch: "
                            f"expected {end_idx - start_idx}, got {patch_tensor.shape[0]}"
                        )

        return cpd_field

    @staticmethod
    def update_h5_field_data(
        h5_path: str,
        case_path: str,
        time_value: Union[float, Sequence[float]] = 1.0,
        fields_to_update: Optional[Dict[str, str]] = None,
        use_direct_reader: bool = False
    ) -> bool:
        """
        Update field data in H5 file from new OpenFOAM VTK output.

        Correctly places field data into CPD positions using patch_list.

        Args:
            h5_path: Path to existing H5 file
            case_path: Path to OpenFOAM case (with new VTK output)
            time_value: Time step to read from VTK, or an ordered sequence of time steps
            fields_to_update: Dict mapping OpenFOAM names to H5 keys
                             Default: {'U': 'uvw_cpd_cell', 'p': 'press_cpd_cell', 'T': 'temp_cpd_cell'}

        Returns:
            bool: True if successful, False otherwise
        """
        if fields_to_update is None:
            fields_to_update = {
                'U': 'uvw_cpd_cell',
                'p': 'press_cpd_cell',
                'T': 'temp_cpd_cell',
                'rho': 'rho_cpd_cell',
                'p_rgh': 'p_rgh_cpd_cell'
            }

        try:
            if isinstance(time_value, (list, tuple, np.ndarray)):
                time_values = [float(t) for t in time_value]
            else:
                time_values = [float(time_value)]

            if not time_values:
                raise ValueError("time_value must contain at least one time step")

            # Step 1: Read patch_list and CPD info from existing H5
            with h5py.File(h5_path, 'r') as hf:
                if 'patch_list' not in hf.attrs:
                    logger.warning("patch_list not found in H5 attributes")
                    patch_list = None
                else:
                    patch_list_str = hf.attrs['patch_list']
                    if isinstance(patch_list_str, bytes):
                        patch_list_str = patch_list_str.decode('utf-8')
                    patch_list = json.loads(patch_list_str)

                # Get CPD info
                num_cpd_cells = hf['cpd|centroid'].shape[0]
                cpd_cell_type = torch.from_numpy(hf['cpd|cell_type'][()]).long()
                num_internal_cells = (cpd_cell_type == 0).sum().item()

            logger.info(
                "📊 CPD info: %s total cells, %s internal cells | target_times=%s",
                num_cpd_cells,
                num_internal_cells,
                time_values,
            )

            # Step 2: Use FieldReader to read new field data
            from Parse_mesh.parse_fielddata import FieldReader

            reader = FieldReader(case_path)
            all_fields_by_time = []
            for current_time in time_values:
                logger.info(
                    "📖 Reading fields from %s at time %s (%s)",
                    case_path, current_time,
                    "direct pyvista" if use_direct_reader else "VTK"
                )
                if use_direct_reader:
                    fields_at_time = reader.read_all_fields_direct(current_time, patch_list)
                else:
                    fields_at_time = reader.read_all_fields_at_time(current_time, patch_list)
                if not fields_at_time:
                    raise RuntimeError(
                        f"No fields read at time {current_time} "
                        f"({'direct' if use_direct_reader else 'VTK'})"
                    )
                all_fields_by_time.append(fields_at_time)

            # Step 3: Update H5 file with new field data
            with h5py.File(h5_path, 'r+') as hf:
                updated_field_count = 0
                for foam_name, h5_key in fields_to_update.items():
                    if not all(foam_name in all_fields for all_fields in all_fields_by_time):
                        logger.warning(f"Field {foam_name} not found in VTK output for all requested times")
                        continue

                    field_steps = []
                    for all_fields in all_fields_by_time:
                        field_result = all_fields[foam_name]

                        # Determine field dimension
                        internal_data = field_result.get('internal', torch.zeros((0, 1)))
                        field_dim = internal_data.shape[1] if internal_data.shape[0] > 0 else 1

                        # Place field data into CPD positions correctly
                        cpd_field_data = OpenFOAMFieldUpdater._place_field_data_to_cpd(
                            field_result=field_result,
                            num_cpd_cells=num_cpd_cells,
                            num_internal_cells=num_internal_cells,
                            patch_list=patch_list,
                            field_dim=field_dim
                        )
                        field_steps.append(cpd_field_data)

                    # Delete old dataset if exists
                    if h5_key in hf:
                        del hf[h5_key]

                    # Add time dimension to match original H5 format [time_steps, num_cells, dim]
                    field_data_3d = torch.stack(field_steps, dim=0)

                    # Create new dataset
                    hf.create_dataset(
                        h5_key,
                        data=field_data_3d.cpu().numpy().astype(np.float32)
                    )
                    logger.info(f"   ✅ Updated {h5_key}: shape {field_data_3d.shape}")
                    updated_field_count += 1

                if updated_field_count == 0:
                    raise RuntimeError("No H5 field datasets were updated")

                if 'time_list' in hf:
                    del hf['time_list']
                time_array = np.asarray(time_values, dtype=np.float32)
                hf.create_dataset('time_list', data=time_array)
                hf.attrs['time_list'] = time_array
                logger.info("   ✅ Updated time_list: %s", time_values)

            logger.info(f"✅ H5 field data updated successfully: {h5_path}")
            return True

        except Exception as e:
            logger.error(f"❌ Failed to update H5 field data: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    @staticmethod
    def verify_h5_structure(h5_path: str) -> bool:
        """
        Verify that H5 file has required mesh data for inference.
        
        Args:
            h5_path: Path to H5 file
        
        Returns:
            bool: True if all required datasets exist
        """
        required_datasets = [
            'node|pos',
            'pv_cells_node',
            'pv_cells_type',
            'cpd|centroid',
            'cpd|neighbor_cell'
        ]
        
        try:
            with h5py.File(h5_path, 'r') as hf:
                missing = []
                for dataset in required_datasets:
                    if dataset not in hf:
                        missing.append(dataset)
                
                if missing:
                    logger.warning(f"Missing datasets in H5: {missing}")
                    return False
                
                logger.info(f"✅ H5 structure verified: {h5_path}")
                return True
        except Exception as e:
            logger.error(f"❌ Failed to verify H5 structure: {e}")
            return False
    
    @staticmethod
    def get_h5_field_info(h5_path: str) -> Dict[str, tuple]:
        """
        Get information about fields in H5 file.

        Args:
            h5_path: Path to H5 file

        Returns:
            Dict mapping field names to (shape, dtype)
        """
        field_info = {}

        try:
            with h5py.File(h5_path, 'r') as hf:
                for key in hf.keys():
                    item = hf[key]
                    # Only process Datasets, skip Groups
                    if isinstance(item, h5py.Dataset):
                        field_info[key] = (item.shape, str(item.dtype))

            return field_info
        except Exception as e:
            logger.error(f"❌ Failed to get H5 field info: {e}")
            return {}
