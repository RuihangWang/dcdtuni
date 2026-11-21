#!/usr/bin/env python3
"""
OpenFOAM Field Data Preprocessor

This module provides a unified class for preprocessing OpenFOAM field data.
It combines the functionality of:
1. Initial field generation (from generate_initial_fields.py)
2. VTK conversion (from foam_to_vtk.py)

The FieldDataPreprocessor class handles the complete workflow:
- Check if initial field (time=1) exists, generate if needed
- Convert OpenFOAM case to VTK format for specified time steps
- Provide a clean interface for use in the mesh parsing pipeline

Usage:
    # As a module
    from Parse_mesh.parse_fielddata import FieldDataPreprocessor

    preprocessor = FieldDataPreprocessor(case_path)
    preprocessor.process()  # Full pipeline

    # As a standalone script
    python parse_fielddata.py --case-path /path/to/openfoam/case
"""

import os
import sys
import re
import shutil
import subprocess
import argparse
from pathlib import Path
from typing import List, Optional, Dict
import logging
import json
import glob
import numpy as np
import torch
import pyvista as pv

logger = logging.getLogger(__name__)


def _is_ignored_vtk_dir_name(name: str) -> bool:
    lowered = name.lower()
    return (
        "backup" in lowered
        or "pre_rebuild" in lowered
        or "pre_fix" in lowered
        or "disabled" in lowered
    )


class FieldReader:
    """
    Read OpenFOAM simulation results using PyVista.

    Supports:
    - Reading internal field data
    - Reading boundary field data
    - Converting to PyTorch tensors
    - Multi-threaded extraction for performance

    This class is designed to be independent and only requires case_path.
    All other parameters are optional and can be inferred from the VTK data.
    """

    def __init__(self, case_path: str):
        """
        Initialize FieldReader.

        Args:
            case_path: Path to OpenFOAM case directory (containing VTK/ or vtk_output/)
        """
        self.case_path = case_path

        # These will be inferred from VTK data if not provided
        self.num_cells = None
        self.num_cpd_cells = None
        self.cpd_centroid = None

        # Field name mapping: OpenFOAM name -> internal data key
        self.field_mapping = {
            'U': 'uvw_cpd_cell',
            'p': 'press_cpd_cell',
            'T': 'temp_cpd_cell',
            'rho': 'rho_cpd_cell',
            'p_rgh': 'p_rgh_cpd_cell'
        }

        # Field dimensions
        self.field_dims = {
            'uvw_cpd_cell': 3,      # Vector field
            'press_cpd_cell': 1,    # Scalar field
            'temp_cpd_cell': 1,
            'rho_cpd_cell': 1,
            'p_rgh_cpd_cell': 1
        }
    
    def read_field_at_time(
        self, 
        field_name: str, 
        time_value: float,
        patch_list: Optional[Dict] = None
    ) -> Dict[str, torch.Tensor]:
        """
        Read a specific field at a specific time step.

        Args:
            field_name: OpenFOAM field name (e.g., 'U', 'p', 'T')
            time_value: Time step value
            patch_list: Optional dictionary with patch information.
                       If None, only internal field is returned.

        Returns:
            Dictionary with keys:
            - 'internal': Internal field tensor [num_cells, dim]
            - 'boundary': Dict of boundary fields {patch_name: tensor}
            - 'all': Combined tensor [num_cpd_cells, dim]
        """
        # Find VTK directory
        vtk_dir = self._find_vtk_directory()
        if vtk_dir is None:
            raise FileNotFoundError(f"No VTK directory found in {self.case_path}")

        # Find VTM file for this time step
        vtm_file = self._find_vtm_file(vtk_dir, time_value)
        if vtm_file is None:
            raise FileNotFoundError(f"No VTM file found for time {time_value}")

        # Read mesh data
        mesh_data = pv.read(vtm_file)

        # Extract field data
        result = self._extract_field_from_mesh(mesh_data, field_name, patch_list)

        return result
    
    def read_all_fields_at_time(
        self,
        time_value: float,
        patch_list: Optional[Dict] = None
    ) -> Dict[str, torch.Tensor]:
        """
        Read all available fields at a specific time step.

        Args:
            time_value: Time step value
            patch_list: Optional dictionary with patch information.
                       If None, only internal fields are returned.

        Returns:
            Dictionary with all fields:
            {
                'U': {'internal': ..., 'boundary': ..., 'all': ...},
                'p': {...},
                ...
            }
        """
        result = {}
        for foam_name in self.field_mapping.keys():
            try:
                result[foam_name] = self.read_field_at_time(foam_name, time_value, patch_list)
            except Exception as e:
                logger.warning(f"Failed to read field '{foam_name}': {e}")

        return result

    def read_all_fields_direct(
        self,
        time_value: float,
        patch_list: Optional[Dict] = None
    ) -> Dict[str, torch.Tensor]:
        """
        Read all fields directly from OpenFOAM case using pyvista native reader.

        Skips foamToVTK entirely — reads the .foam file directly.
        Requires that boundary values are already written in the field files
        (i.e., produced by buoyantBoussinesqSimpleFoam_new or foamToVTK).

        Args:
            time_value: Time step value
            patch_list: Optional dict with patch info (same format as read_all_fields_at_time)

        Returns:
            Same format as read_all_fields_at_time.
        """
        # Find .foam file
        foam_files = list(Path(self.case_path).glob("*.foam"))
        if not foam_files:
            raise FileNotFoundError(f"No .foam file found in {self.case_path}")
        foam_file = str(foam_files[0])

        reader = pv.OpenFOAMReader(foam_file)
        available_times = reader.time_values

        # Pick closest available time
        closest = min(available_times, key=lambda t: abs(t - time_value))
        if abs(closest - time_value) > 0.5:
            raise ValueError(
                f"Requested time {time_value} not found in {available_times}"
            )
        reader.set_active_time_value(closest)
        mesh_data = reader.read()

        result = {}
        for foam_name in self.field_mapping.keys():
            try:
                result[foam_name] = self._extract_field_from_foam_native(
                    mesh_data, foam_name, patch_list
                )
            except Exception as e:
                logger.warning(f"Direct read failed for field '{foam_name}': {e}")

        return result

    def _extract_field_from_foam_native(
        self,
        mesh_data: pv.MultiBlock,
        field_name: str,
        patch_list: Optional[Dict]
    ) -> Dict[str, torch.Tensor]:
        """Extract field from pyvista native OpenFOAM reader output.

        The native reader names blocks 'internalMesh' and 'boundary'
        (vs VTK which uses 'internal' and 'boundary').
        """
        data_key = self.field_mapping[field_name]
        dim = self.field_dims[data_key]

        internal_tensor = torch.zeros((0, dim))
        boundary_tensors = {}

        internal_block = mesh_data.get("internalMesh")
        boundary_block = mesh_data.get("boundary")

        if internal_block is not None and field_name in internal_block.array_names:
            internal_tensor = self._extract_field_from_block(internal_block, field_name, dim)

        if boundary_block is not None and patch_list is not None:
            boundary_tensors = self._extract_boundary_fields(
                boundary_block, field_name, dim, patch_list
            )

        return {"internal": internal_tensor, "boundary": boundary_tensors}
    
    def _find_vtk_directory(self) -> Optional[str]:
        """Find VTK directory in case path."""
        possible_dirs = [
            os.path.join(self.case_path, "VTK"),
            os.path.join(self.case_path, "vtk_output"),
        ]

        for vdir in possible_dirs:
            if os.path.exists(vdir):
                return vdir

        # Fallback: search for any directory containing .vtm.series files.
        # This is intentionally lower priority than the standard VTK/vtk_output
        # directories so stale backup folders do not override the active dataset.
        try:
            for item in sorted(os.listdir(self.case_path)):
                item_path = os.path.join(self.case_path, item)
                if os.path.isdir(item_path):
                    if _is_ignored_vtk_dir_name(item):
                        continue
                    series_files = glob.glob(os.path.join(item_path, "*.vtm.series"))
                    if series_files:
                        return item_path
        except Exception as e:
            logger.warning(f"Error searching for VTK directory: {e}")

        return None
    
    def _find_vtm_file(
        self, 
        vtk_dir: str, 
        time_value: float
    ) -> Optional[str]:
        """Find VTM file for a specific time step."""
        series_file = glob.glob(os.path.join(vtk_dir, "*.vtm.series"))
        if not series_file:
            return None
        
        try:
            with open(series_file[0], 'r') as f:
                series_data = json.load(f)
            
            files_list = series_data.get("files", [])
            for entry in files_list:
                if float(entry.get("time", 0.0)) == time_value:
                    return os.path.join(vtk_dir, entry.get("name", ""))
        except Exception as e:
            logger.warning(f"Error reading series file: {e}")
        
        return None
    
    def _extract_field_from_mesh(
        self, 
        mesh_data: pv.MultiBlock,
        field_name: str,
        patch_list: Optional[Dict]
    ) -> Dict[str, torch.Tensor]:
        """Extract field data from mesh."""
        # Initialize result tensors
        data_key = self.field_mapping[field_name]
        dim = self.field_dims[data_key]

        internal_tensor = None
        boundary_tensors = {}

        # Find internal and boundary blocks
        internal_block = None
        boundary_block = None

        for i in range(mesh_data.n_blocks):
            block_name = mesh_data.get_block_name(i)
            if block_name == "internal":
                internal_block = mesh_data[i]
            elif block_name == "boundary":
                boundary_block = mesh_data[i]

        # Extract internal field
        if internal_block is not None and field_name in internal_block.array_names:
            internal_tensor = self._extract_field_from_block(
                internal_block, field_name, dim
            )
        else:
            internal_tensor = torch.zeros((0, dim))

        # Extract boundary fields
        if boundary_block is not None and patch_list is not None:
            boundary_tensors = self._extract_boundary_fields(
                boundary_block, field_name, dim, patch_list
            )

        return {
            'internal': internal_tensor,
            'boundary': boundary_tensors
        }
    
    def _extract_field_from_block(
        self, 
        block: pv.UnstructuredGrid,
        field_name: str, dim: int
    ) -> torch.Tensor:
        """Extract field from a single block."""
        if field_name not in block.array_names:
            return torch.zeros((block.n_cells, dim))
        
        field_data = block[field_name]
        field_array = np.asarray(field_data)
        
        # Handle different field dimensions
        if dim == 3 and field_array.ndim == 2 and field_array.shape[1] == 3:
            # Vector field
            return torch.from_numpy(field_array).float()
        elif dim == 1 and field_array.ndim == 1:
            # Scalar field
            return torch.from_numpy(field_array).float().unsqueeze(1)
        else:
            logger.warning(f"Unexpected shape for field '{field_name}': {field_array.shape}")
            return torch.zeros((block.n_cells, dim))
    
    def _extract_boundary_fields(
        self, 
        boundary_block: pv.MultiBlock,
        field_name: str, dim: int,
        patch_list: Dict
    ) -> Dict[str, torch.Tensor]:
        """Extract boundary fields from all patches."""
        boundary_tensors = {}

        for i in range(boundary_block.n_blocks):
            block = boundary_block[i]
            block_name = boundary_block.get_block_name(i)

            # If patch_list is provided, only extract patches in it
            if block_name not in patch_list:
                continue

            # Extract field from this patch
            patch_tensor = self._extract_field_from_block(block, field_name, dim)
            boundary_tensors[block_name] = patch_tensor

        return boundary_tensors


class FieldDataPreprocessor:
    """
    Unified preprocessor for OpenFOAM field data.

    This class combines initial field generation and VTK conversion into a single,
    clean interface. It's designed to be used both as a module in the mesh processing
    pipeline and as a standalone tool for testing.
    """

    def __init__(self, case_path: str, fvsolution_template_path: str = None):
        """
        Initialize the field data preprocessor.

        Args:
            case_path: Path to OpenFOAM case directory
            fvsolution_template_path: Optional explicit path to fvSolution_minimal.j2 template.
                If not provided, searches case_path.parent.parent (dataset root) first.
        """
        self.case_path = Path(case_path).resolve()

        if not self.case_path.exists():
            raise ValueError(f"Case path does not exist: {case_path}")

        self.controldict_path = self.case_path / "system" / "controlDict"
        if not self.controldict_path.exists():
            raise ValueError(f"controlDict not found: {self.controldict_path}")

        # fvSolution path for minimal iteration template
        self.fvsolution_path = self.case_path / "system" / "fvSolution"
        if not self.fvsolution_path.exists():
            logger.warning(f"fvSolution not found: {self.fvsolution_path}")

        # VTK output directory (foamToVTK always creates VTK/ by default)
        self.vtk_dir = self.case_path / "VTK"

        # Path to minimal iteration template — resolve in priority order:
        # 1. Explicit argument
        # 2. dataset root (case_path.parent.parent/fvSolution_minimal.j2)
        if fvsolution_template_path:
            self.fvsolution_template_path = Path(fvsolution_template_path)
        else:
            candidate = self.case_path.parent.parent / "fvSolution_minimal.j2"
            self.fvsolution_template_path = candidate

        logger.info(f"Initialized FieldDataPreprocessor for case: {self.case_path}")

    def process(self, run_simulation: bool = True, force_compute_1st_field: bool = False,
                skip_vtk: bool = True) -> bool:
        """
        Execute the complete field data preprocessing pipeline.

        Args:
            run_simulation: Whether to run OpenFOAM simulation for initial field
            force_compute_1st_field: Force regeneration of 1st time step field even if it exists
            skip_vtk: Skip foamToVTK conversion (default True — use direct pyvista reader instead)

        Returns:
            bool: True if processing succeeded, False otherwise
        """
        logger.info("="*80)
        logger.info("Starting Field Data Preprocessing Pipeline")
        logger.info("="*80)

        # Step 1: Check and generate initial field (time=1)
        if force_compute_1st_field:
            logger.info("force_compute_1st_field=True, regenerating initial field...")
            if run_simulation:
                success = self.generate_initial_field()
                if not success:
                    logger.error("Failed to generate initial field")
                    return False
            else:
                logger.warning("run_simulation=False, skipping initial field generation")
        elif not self.check_initial_field_exists():
            logger.info("Initial field not found, generating...")
            if run_simulation:
                success = self.generate_initial_field()
                if not success:
                    logger.error("Failed to generate initial field")
                    return False
            else:
                logger.warning("run_simulation=False, skipping initial field generation")
        else:
            logger.info("Initial field already exists, skipping generation")

        # Step 2: Convert to VTK format (skipped when using direct reader)
        if not skip_vtk:
            logger.info("Converting to VTK format...")
            success = self.convert_to_vtk()
            if not success:
                logger.error("Failed to convert to VTK")
                return False
        else:
            logger.info("Skipping foamToVTK (skip_vtk=True) — using direct pyvista reader")

        logger.info("="*80)
        logger.info("Field Data Preprocessing Completed Successfully")
        logger.info("="*80)
        return True

    def check_initial_field_exists(self) -> bool:
        """
        Check if initial field (time=1) directory exists.

        Returns:
            bool: True if initial field exists
        """
        # Parse controlDict to get startTime and deltaT
        params = self._parse_controldict()
        start_time = params.get('startTime', 0)
        delta_t = params.get('deltaT', 1)

        # Expected initial time = startTime + deltaT
        initial_time = start_time + delta_t

        # Check for time directory (handle both integer and float formats)
        if initial_time == int(initial_time):
            time_dir = self.case_path / str(int(initial_time))
        else:
            time_dir = self.case_path / str(initial_time)

        exists = time_dir.exists()
        if exists:
            logger.info(f"Found initial field directory: {time_dir}")
        else:
            logger.info(f"Initial field directory not found: {time_dir}")

        return exists

    def generate_initial_field(self) -> bool:
        """
        Generate initial field by modifying controlDict/fvSolution and running simulation.

        This method:
        1. Backs up original controlDict and fvSolution
        2. Applies minimal iteration fvSolution (all tolerances = 1.0)
        3. Modifies controlDict to run only 1 time step
        4. Runs OpenFOAM solver
        5. Restores original controlDict and fvSolution

        Returns:
            bool: True if generation succeeded
        """
        logger.info("Generating initial field (1 time step with minimal iteration)...")

        # Backup controlDict and fvSolution
        controldict_backup = self._backup_controldict()
        fvsolution_backup = self._backup_fvsolution()

        try:
            # Apply minimal iteration fvSolution template
            logger.info("Applying minimal iteration fvSolution...")
            if not self._apply_minimal_fvsolution():
                logger.warning("Failed to apply minimal fvSolution, proceeding with original")

            # Modify controlDict for 1 time step
            self._modify_controldict_for_initial_field()

            # Run OpenFOAM simulation
            success = self._run_openfoam_simulation()

            if success:
                logger.info("Initial field generated successfully")
            else:
                logger.error("OpenFOAM simulation failed")

            return success

        finally:
            # Always restore original controlDict and fvSolution
            self._restore_controldict(controldict_backup)
            self._restore_fvsolution(fvsolution_backup)
            logger.info("Restored original controlDict and fvSolution")

    def convert_to_vtk(self) -> bool:
        """
        Convert OpenFOAM case to VTK format using foamToVTK.

        This method converts all non-zero time steps to VTK format,
        excluding time=0 to save space and processing time.

        Returns:
            bool: True if conversion succeeded
        """
        logger.info("Converting OpenFOAM case to VTK format...")

        # Find available time directories (excluding 0)
        time_dirs = self._find_time_directories()
        non_zero_times = [t for t in time_dirs if t != 0.0]

        if not non_zero_times:
            logger.error("No non-zero time directories found!")
            return False

        # Build time selection string
        time_str = ','.join(str(int(t) if t == int(t) else t) for t in non_zero_times)
        logger.info(f"Converting time steps: {time_str} (excluding time=0)")

        # Find OpenFOAM environment
        openfoam_source = self._find_openfoam_environment()

        # Export only the physical fields actually consumed by the H5 pipeline.
        # This avoids auxiliary/debug fields (for example `node_type`) breaking
        # foamToVTK when they have incomplete boundaryField definitions.
        export_fields = "(U p T p_rgh)"
        logger.info(f"Restricting foamToVTK export fields to: {export_fields}")

        # Run foamToVTK
        try:
            if openfoam_source:
                cmd = (
                    f"source {openfoam_source} && "
                    f"foamToVTK -case {self.case_path} -time '{time_str}' -fields '{export_fields}'"
                )
                logger.info(f"Running: {cmd}")

                result = subprocess.run(
                    cmd,
                    shell=True,
                    executable='/bin/bash',
                    cwd=str(self.case_path),
                    capture_output=True,
                    text=True,
                    timeout=600  # 10 minute timeout
                )
            else:
                # Try direct execution
                logger.info("OpenFOAM environment not found, trying direct execution...")
                cmd = [
                    'foamToVTK',
                    '-case',
                    str(self.case_path),
                    '-time',
                    time_str,
                    '-fields',
                    export_fields,
                ]

                result = subprocess.run(
                    cmd,
                    cwd=str(self.case_path),
                    capture_output=True,
                    text=True,
                    timeout=600
                )

            if result.returncode != 0:
                logger.error(f"foamToVTK failed: {result.stderr}")
                return False

            logger.info(f"VTK conversion completed successfully")
            logger.info(f"Converted {len(non_zero_times)} time steps")
            logger.info(f"VTK output directory: {self.vtk_dir}")

            return True

        except subprocess.TimeoutExpired:
            logger.error("foamToVTK timed out (>10 minutes)")
            return False
        except Exception as e:
            logger.error(f"Error running foamToVTK: {e}")
            return False

    def _parse_controldict(self) -> dict:
        """Parse controlDict file and extract key parameters."""
        params = {}

        with open(self.controldict_path, 'r') as f:
            content = f.read()

        # Extract parameters using regex
        patterns = {
            'startFrom': r'startFrom\s+([^;]+);',
            'startTime': r'startTime\s+([^;]+);',
            'endTime': r'endTime\s+([^;]+);',
            'deltaT': r'deltaT\s+([^;]+);',
            'writeInterval': r'writeInterval\s+([^;]+);',
            'writeControl': r'writeControl\s+([^;]+);',
            'application': r'application\s+([^;]+);'
        }

        for param, pattern in patterns.items():
            match = re.search(pattern, content)
            if match:
                value = match.group(1).strip()
                try:
                    # Try to convert to number
                    if '.' in value:
                        params[param] = float(value)
                    else:
                        params[param] = int(value)
                except ValueError:
                    # Keep as string if not a number
                    params[param] = value

        return params

    def _backup_controldict(self) -> Path:
        """Create a backup of controlDict."""
        backup_path = self.controldict_path.with_suffix('.backup')
        if not backup_path.exists():
            shutil.copy2(self.controldict_path, backup_path)
            logger.info(f"Created backup: {backup_path}")
        return backup_path

    def _restore_controldict(self, backup_path: Path):
        """Restore controlDict from backup."""
        if backup_path.exists():
            # Read backup content and write to controlDict
            # This avoids permission issues with shutil.copy/copy2
            with open(backup_path, 'r') as src:
                content = src.read()
            with open(self.controldict_path, 'w') as dst:
                dst.write(content)
            logger.info(f"Restored controlDict from backup")

    def _backup_fvsolution(self) -> Path:
        """
        Create a backup of fvSolution.

        Returns:
            Path to backup file
        """
        backup_path = self.fvsolution_path.with_suffix('.backup')
        if self.fvsolution_path.exists():
            if not backup_path.exists():
                shutil.copy2(self.fvsolution_path, backup_path)
                logger.info(f"Created fvSolution backup: {backup_path}")
        else:
            logger.warning(f"fvSolution not found, cannot create backup: {self.fvsolution_path}")
        return backup_path

    def _restore_fvsolution(self, backup_path: Path):
        """
        Restore fvSolution from backup.

        Args:
            backup_path: Path to backup file
        """
        if backup_path.exists():
            # Read backup content and write to fvSolution
            with open(backup_path, 'r') as src:
                content = src.read()
            with open(self.fvsolution_path, 'w') as dst:
                dst.write(content)
            logger.info(f"Restored fvSolution from backup")
        else:
            logger.warning(f"fvSolution backup not found: {backup_path}")

    def _apply_minimal_fvsolution(self):
        """
        Replace fvSolution with minimal iteration template.

        This template sets all tolerances to 1.0 to ensure OpenFOAM
        applies boundary conditions and exits immediately without solving.
        """
        if not self.fvsolution_template_path.exists():
            logger.error(f"fvSolution template not found: {self.fvsolution_template_path}")
            return False

        try:
            # Read template content
            with open(self.fvsolution_template_path, 'r') as f:
                template_content = f.read()

            # Write to fvSolution
            with open(self.fvsolution_path, 'w') as f:
                f.write(template_content)

            logger.info("Applied minimal iteration fvSolution template")
            logger.info("   All tolerances set to 1.0 for immediate convergence")
            return True

        except Exception as e:
            logger.error(f"Failed to apply minimal fvSolution template: {e}")
            return False


    def _modify_controldict_for_initial_field(self):
        """Modify controlDict to run only 1 time step with buoyantBoussinesqSimpleFoam_new."""
        params = self._parse_controldict()

        start_time = params.get('startTime', 0)
        delta_t = params.get('deltaT', 1)
        new_end_time = start_time + delta_t

        logger.info(f"Modifying controlDict: startTime={start_time}, endTime={new_end_time}")

        # Read file content
        with open(self.controldict_path, 'r') as f:
            lines = f.readlines()

        # Modify specific lines
        modified_lines = []
        for line in lines:
            stripped = line.strip()
            if re.match(r'\s*application\s+', line):
                modified_line = re.sub(
                    r'(\s*application\s+)[^;]+;',
                    r'\g<1>buoyantBoussinesqSimpleFoam_new;',
                    line
                )
                modified_lines.append(modified_line)
            elif re.match(r'\s*startFrom\s+', line):
                modified_line = re.sub(r'(\s*startFrom\s+)[^;]+;', r'\g<1>startTime;', line)
                modified_lines.append(modified_line)
            elif re.match(r'\s*stopAt\s+', line):
                modified_line = re.sub(r'(\s*stopAt\s+)[^;]+;', r'\g<1>endTime;', line)
                modified_lines.append(modified_line)
            elif re.match(r'\s*endTime\s+', line):
                modified_line = re.sub(r'(\s*endTime\s+)[^;]+;', f'\\g<1>{new_end_time};', line)
                modified_lines.append(modified_line)
            elif re.match(r'\s*writeInterval\s+', line):
                modified_line = re.sub(r'(\s*writeInterval\s+)[^;]+;', r'\g<1>1;', line)
                modified_lines.append(modified_line)
            elif re.match(r'\s*writeControl\s+', line):
                modified_line = re.sub(r'(\s*writeControl\s+)[^;]+;', r'\g<1>timeStep;', line)
                modified_lines.append(modified_line)
            elif re.match(r'\s*libs\s+', line) and 'petscFoam' in stripped:
                # Comment out petscFoam which is not available
                modified_lines.append('// ' + line.lstrip())
            else:
                modified_lines.append(line)

        # Write modified content
        with open(self.controldict_path, 'w') as f:
            f.writelines(modified_lines)

        logger.info("controlDict modified: buoyantBoussinesqSimpleFoam_new, 1 time step")

    def _run_openfoam_simulation(self) -> bool:
        """Run OpenFOAM simulation."""
        # Get solver name from controlDict
        params = self._parse_controldict()
        solver = params.get('application', None)

        if not solver:
            logger.error("Could not find 'application' in controlDict")
            return False

        logger.info(f"Running OpenFOAM solver: {solver}")

        # Find OpenFOAM environment
        openfoam_source = self._find_openfoam_environment()

        try:
            if openfoam_source:
                cmd = f"source {openfoam_source} && {solver}"
                logger.info(f"Command: {cmd}")

                result = subprocess.run(
                    cmd,
                    shell=True,
                    executable='/bin/bash',
                    cwd=str(self.case_path),
                    capture_output=True,
                    text=True,
                    timeout=300  # 5 minute timeout
                )
            else:
                # Try direct execution
                logger.info("OpenFOAM environment not found, trying direct execution...")
                result = subprocess.run(
                    [solver],
                    cwd=str(self.case_path),
                    capture_output=True,
                    text=True,
                    timeout=300
                )

            if result.returncode == 0:
                logger.info("OpenFOAM simulation completed successfully")
                return True
            else:
                logger.error(f"OpenFOAM simulation failed:")
                logger.error(f"STDERR: {result.stderr}")
                return False

        except subprocess.TimeoutExpired:
            logger.error("OpenFOAM simulation timed out (>5 minutes)")
            return False
        except Exception as e:
            logger.error(f"Error running OpenFOAM: {e}")
            return False

    def _find_time_directories(self) -> List[float]:
        """Find all time directories in the case."""
        time_dirs = []

        for item in self.case_path.iterdir():
            if item.is_dir():
                # Match numeric directories
                if re.match(r'^[0-9]+\.?[0-9]*$', item.name):
                    try:
                        time_val = float(item.name)
                        time_dirs.append(time_val)
                    except ValueError:
                        continue

        time_dirs.sort()
        return time_dirs

    def _find_openfoam_environment(self) -> Optional[str]:
        """Find OpenFOAM environment script (conda v2412 first; never system).

        Delegates to the single canonical resolver in ``Utils.foam_env`` so the
        whole codebase shares one conda-first, env-driven source of truth.
        """
        from Utils.foam_env import resolve_openfoam_bashrc
        bashrc = resolve_openfoam_bashrc()
        if bashrc:
            logger.info(f"Found OpenFOAM environment: {bashrc}")
        else:
            logger.warning(
                "No OpenFOAM environment found. Set FOAM_BASHRC to the conda "
                "OpenFOAM etc/bashrc (system OpenFOAM is intentionally not searched)."
            )
        return bashrc


def main():
    """Main function for standalone execution."""
    parser = argparse.ArgumentParser(
        description='OpenFOAM Field Data Preprocessor',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Process a single case (full pipeline)
  python parse_fielddata.py --case-path /path/to/case

  # Only modify controlDict (no simulation)
  python parse_fielddata.py --case-path /path/to/case --no-run

  # Process with custom logging level
  python parse_fielddata.py --case-path /path/to/case --verbose
        """
    )

    parser.add_argument(
        '--case-path',
        type=str,
        required=True,
        help='Path to OpenFOAM case directory'
    )

    parser.add_argument(
        '--no-run',
        action='store_true',
        help='Only modify files, do not run simulation'
    )

    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Enable verbose logging'
    )

    args = parser.parse_args()

    # Configure logging using standard logging
    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    try:
        # Initialize preprocessor
        preprocessor = FieldDataPreprocessor(args.case_path)

        # Run processing pipeline
        success = preprocessor.process(run_simulation=not args.no_run)

        if success:
            logger.info(" Processing completed successfully!")
            sys.exit(0)
        else:
            logger.error("L Processing failed!")
            sys.exit(1)

    except Exception as e:
        logger.error(f"L Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
