import os
import re
import sys
import logging
from typing import Dict, List, Optional
import tomlkit

import torch

# Ensure repo src is on sys.path
file_dir = os.path.dirname(os.path.dirname(__file__))
sys.path.append(file_dir)

from Utils.utilities import FaceType, UBoundaryType, PBoundaryType, TBoundaryType, RhoBoundaryType, PrghBoundaryType

''' support value type '''
support_val_type=["uniform","constant","nonuniform","file","expression","parametric"] # 后续还会持续引入额外的支持

class BoundaryParser:
    """
    Parse OpenFOAM boundary information and build:
      - face_type: [N_faces, 1] general categories purely from polyMesh/boundary
      - boundary_type: [N_faces, N_fields] per-field boundary categories from 0/ field files
      - neighbor relationships and cyclic pairs
    """

    def __init__(self, case_path: str, faces_raw: List[List[int]], owner_raw: torch.Tensor,
                 neighbour_raw: torch.Tensor, boundary_patch: Dict, valid_bc_file_list: List[str] = None,
                 num_cells: int = None, num_internal_faces: int=None):
        self.case_path = case_path
        self.faces_raw = faces_raw
        self.owner_raw = owner_raw
        self.neighbour_raw = neighbour_raw
        self.boundary_patch = boundary_patch
        self.valid_bc_file_list = valid_bc_file_list or ["rho", "U", "T", "p", "p_rgh"]
        self.num_cells = num_cells  # Add num_cells attribute
        self.num_internal_faces = num_internal_faces  # Add num_internal_faces attribute
        self.num_faces = len(self.faces_raw)

        # Cache for boundary file parsing to avoid repeated file I/O
        self._boundary_polymesh_cache = None

        # Validate that mesh files exist
        self._validate_mesh_files()

    def _validate_mesh_files(self):
        """
        Validate that essential OpenFOAM mesh files exist.
        """
        polymesh_dir = os.path.join(self.case_path, 'constant', 'polyMesh')
        if not os.path.isdir(polymesh_dir):
            raise FileNotFoundError(f"polyMesh directory not found: {polymesh_dir}")

        essential_files = ['boundary', 'points', 'faces', 'owner', 'neighbour']
        missing_files = []

        for filename in essential_files:
            filepath = os.path.join(polymesh_dir, filename)
            if not os.path.isfile(filepath):
                missing_files.append(filename)

        if missing_files:
            raise FileNotFoundError(f"Essential mesh files missing in {polymesh_dir}: {missing_files}")

        logging.info(f"Mesh validation passed for case: {self.case_path}")

    @staticmethod
    def validate_case_structure(case_path: str):
        """
        Static method to validate OpenFOAM case structure before creating BoundaryParser instance.
        Used for standalone validation when mesh data is not yet available.
        """
        if not os.path.isdir(case_path):
            raise FileNotFoundError(f"Case directory not found: {case_path}")

        # Check for 0/ directory
        zero_dir = os.path.join(case_path, '0')
        if not os.path.isdir(zero_dir):
            raise FileNotFoundError(f"0/ directory not found: {zero_dir}")

        # Check for constant/polyMesh directory
        polymesh_dir = os.path.join(case_path, 'constant', 'polyMesh')
        if not os.path.isdir(polymesh_dir):
            raise FileNotFoundError(f"polyMesh directory not found: {polymesh_dir}")

        # Check for essential mesh files
        essential_files = ['boundary', 'points', 'faces', 'owner', 'neighbour']
        missing_files = []

        for filename in essential_files:
            filepath = os.path.join(polymesh_dir, filename)
            if not os.path.isfile(filepath):
                missing_files.append(filename)

        if missing_files:
            raise FileNotFoundError(f"Essential mesh files missing in {polymesh_dir}: {missing_files}")

        logging.info(f"Case structure validation passed for: {case_path}")

    def process(self) -> Dict:
        # Parse BCs from 0/ directory
        boundary_0 = self._parse_boundary_conditions_from_0()
        # Parse detailed boundary info from polyMesh/boundary (for neighbourPatch and type)
        detailed = self._parse_boundary_file_from_polymesh()

        # Build boundary faces set and patch info from Ofpp boundary_patch
        boundary_faces_set = set()
        patch_info = {}
        for patch_name, props in self.boundary_patch.items():
            if isinstance(patch_name, bytes):
                patch_name = patch_name.decode('utf-8')
            if isinstance(props, dict):
                start_face = int(props.get('startFace', 0))
                n_faces = int(props.get('nFaces', 0))
                geom_type = props.get('type', 'patch')
            else:
                # namedtuple-like
                start_face = int(getattr(props, 'start', 0))
                n_faces = int(getattr(props, 'num', 0))
                geom_type = getattr(props, 'type', 'patch')
            if isinstance(geom_type, bytes):
                geom_type = geom_type.decode('utf-8')

            # merge detailed info
            neighbour_patch = None
            if detailed and patch_name in detailed:
                geom_type = detailed[patch_name].get('type', geom_type)
                neighbour_patch = detailed[patch_name].get('neighbourPatch', None)

            patch_info[patch_name] = {
                'start_face': start_face,
                'n_faces': n_faces,
                'geom_type': geom_type,
                'neighbour_patch': neighbour_patch,
            }
            boundary_faces_set.update(range(start_face, start_face + n_faces))

        # Face type from polyMesh/boundary only
        face_type = torch.full((self.num_faces, 1), 0, dtype=torch.long)  # Default: NORMAL
        for patch_name, info in patch_info.items():
            if info['n_faces'] <= 0:
                continue
            ft_val = self._map_geom_to_face_type(info['geom_type'])
            rng = torch.arange(info['start_face'], info['start_face'] + info['n_faces'], dtype=torch.long)
            face_type[rng, 0] = int(ft_val)

        # Boundary type per field
        fields_order = list(sorted(boundary_0.keys()))
        n_fields = max(1, len(fields_order))
        boundary_type = torch.full((self.num_faces, n_fields), 0, dtype=torch.long)  # Default: NORMAL
        boundary_type_codebook = {f: self._get_field_codebook(f) for f in fields_order}

        for patch_name, info in patch_info.items():
            if info['n_faces'] <= 0:
                continue
            rng = torch.arange(info['start_face'], info['start_face'] + info['n_faces'], dtype=torch.long)
            geom_type = info.get('geom_type', 'patch')
            for c_idx, field in enumerate(fields_order):
                bc_type = None
                try:
                    bc_type = boundary_0.get(field, {}).get(patch_name, {}).get('type', None)
                except Exception:
                    bc_type = None
                boundary_type[rng, c_idx] = self._map_bc_to_boundary_code(field, bc_type, geom_type)

        # Neighbor relationships
        neighbor_cell = torch.zeros((2, self.num_faces), dtype=torch.long)
        neighbor_cell[0, :] = self.owner_raw

        # Process internal faces first - determine which faces are internal
        internal_faces = set(range(self.num_faces)) - boundary_faces_set
        internal_face_indices = list(internal_faces)

        # Set neighbor relationships for internal faces using original logic
        # But first filter out invalid cell indices from neighbour_raw
        max_valid_cell_idx = self.num_cells - 1  # Based on actual cell count from openfoamparser

        # Create a mask for valid neighbour indices
        valid_neighbour_mask = (self.neighbour_raw >= 0) & (self.neighbour_raw <= max_valid_cell_idx)

        if len(internal_face_indices) == len(self.neighbour_raw):
            # If internal faces count matches neighbour_raw length, use internal face indices
            internal_tensor = torch.tensor(internal_face_indices, dtype=torch.long)
            # Only assign valid neighbour values, invalid ones will remain as owner (boundary-like)
            valid_internal_mask = valid_neighbour_mask
            neighbor_cell[1, internal_tensor[valid_internal_mask]] = self.neighbour_raw[valid_internal_mask]
            # For invalid neighbour indices, duplicate the owner (treat as boundary)
            invalid_internal_indices = internal_tensor[~valid_internal_mask]
            if len(invalid_internal_indices) > 0:
                neighbor_cell[1, invalid_internal_indices] = neighbor_cell[0, invalid_internal_indices]
        else:
            # Otherwise, assign neighbour_raw to first N faces (original OpenFOAM convention)
            valid_indices = torch.where(valid_neighbour_mask)[0]
            if len(valid_indices) > 0:
                neighbor_cell[1, valid_indices] = self.neighbour_raw[valid_indices]
            # For invalid indices, duplicate owner
            invalid_indices = torch.where(~valid_neighbour_mask)[0]
            if len(invalid_indices) > 0:
                neighbor_cell[1, invalid_indices] = neighbor_cell[0, invalid_indices]

        # # Set boundary face neighbors (duplicate owner)
        # boundary_tensor = torch.tensor(list(boundary_faces_set), dtype=torch.long)
        # neighbor_cell[1, boundary_tensor] = neighbor_cell[0, boundary_tensor]

        # Cyclic pairs using neighbourPatch
        cyclic_pairs = []
        processed = set()
        for name, info in patch_info.items():
            if info.get('geom_type', '').strip().lower() not in ['cyclic', 'cyclicami']:
                continue
            if name in processed:
                continue
            neigh = info.get('neighbour_patch')
            if not neigh or neigh not in patch_info:
                continue
            info2 = patch_info[neigh]
            if info2.get('geom_type', '').strip().lower() not in ['cyclic', 'cyclicami']:
                continue
            n1, n2 = info['n_faces'], info2['n_faces']
            if n1 != n2:
                logging.warning(f"Cyclic patches {name} and {neigh} have different nFaces: {n1} vs {n2}")
                continue
            f1 = torch.arange(info['start_face'], info['start_face'] + n1, dtype=torch.long)
            f2 = torch.arange(info2['start_face'], info2['start_face'] + n2, dtype=torch.long)
            cyclic_pairs.append(torch.stack([f1, f2], dim=0))
            processed.add(name)
            processed.add(neigh)
        if cyclic_pairs:
            cyclic_face = torch.cat(cyclic_pairs, dim=1)
        else:
            cyclic_face = torch.full((2, 1), -1, dtype=torch.long)

        # Create patch_list for h5 storage - convert patch_info to the format expected by Load_mesh.py
        patch_list = {}
        for patch_name, info in patch_info.items():
            patch_list[patch_name] = {
                'patch_name': patch_name,
                'start_face': info['start_face'],
                'n_faces': info['n_faces'],
                'geom_type': info['geom_type'],
                'neighbour_patch': info.get('neighbour_patch', None),
                # Calculate start and end indices for cpd cells (boundary cells)
                'start_idx_face': info['start_face'],
                'end_idx_face': info['start_face'] + info['n_faces'],
                # For boundary cells, we use the owner cells of the boundary faces
                'start_idx_cpd_cell': info['start_face'] - self.num_internal_faces + self.num_cells,  # Will be adjusted based on actual boundary cell mapping
                'end_idx_cpd_cell': info['start_face'] + info['n_faces'] - self.num_internal_faces + self.num_cells,  # Will be adjusted based on actual boundary cell mapping
            }

        return {
            'fields_order': fields_order,
            'boundary_0': boundary_0,
            'patch_info': patch_info,
            'patch_list': patch_list,  # Add patch_list to the return dictionary
            'face_type': face_type,
            'boundary_type': boundary_type,
            'boundary_type_codebook': boundary_type_codebook,
            'neighbor_cell': neighbor_cell,
            'cyclic_face': cyclic_face,
        }

    def _map_geom_to_face_type(self, geom_type: str) -> FaceType:
        """Map OpenFOAM geometry type to FaceType enum."""
        if not geom_type:
            return FaceType.NORMAL

        geom_lower = geom_type.lower().strip()

        if geom_lower in ['cyclic', 'cyclicami']:
            return FaceType.CYCLIC
        elif geom_lower == 'empty':
            return FaceType.EMPTY
        elif geom_lower in ['wall']:
            return FaceType.WALL
        elif geom_lower in ['patch', 'inlet', 'outlet']:
            return FaceType.PATCH
        else:
            return FaceType.PATCH  # Default to patch

    def _map_bc_to_boundary_code(self, field: str, bc_type: str, geom_type: str = None) -> int:
        """Map OpenFOAM boundary condition to field-specific boundary code."""
        if not bc_type:
            return 0  # NORMAL

        bc_lower = bc_type.lower().strip()
        geom_lower = (geom_type or '').lower().strip()
        field_lower = field.lower().strip()

        # Handle geometry-based types first
        if geom_lower in ['cyclic', 'cyclicami'] or bc_lower == 'cyclic':
            return 99  # CYCLIC
        elif geom_lower == 'empty' or bc_lower == 'empty':
            return -1  # EMPTY

        # Field-specific mapping
        if field_lower in ['u', 'velocity']:
            if bc_lower == 'fixedvalue':
                return int(UBoundaryType.FIXEDVALUE)
            elif bc_lower == 'uniformfixedvalue':
                return int(UBoundaryType.UNIFORMFIXEDVALUE)
            elif bc_lower == 'zerogradient':
                return int(UBoundaryType.ZEROGRADIENT)
            elif bc_lower == 'slip':
                return int(UBoundaryType.SLIP)
            elif bc_lower == 'noslip':
                return int(UBoundaryType.NOSLIP)
            elif bc_lower == 'freestream':
                return int(UBoundaryType.FREESTREAM)

        elif field_lower in ['p', 'pressure']:
            if bc_lower == 'fixedvalue':
                return int(PBoundaryType.FIXEDVALUE)
            elif bc_lower == 'zerogradient':
                return int(PBoundaryType.ZEROGRADIENT)
            elif bc_lower == 'freestreampressure':
                return int(PBoundaryType.FREESTREAMPRESSURE)
            elif bc_lower == 'freestream':
                return int(PBoundaryType.FREESTREAM)
            elif bc_lower == 'fanpressure':
                return int(PBoundaryType.FANPRESSURE)

        elif field_lower in ['t', 'temperature']:
            if bc_lower == 'fixedvalue':
                return int(TBoundaryType.FIXEDVALUE)
            elif bc_lower == 'zerogradient':
                return int(TBoundaryType.ZEROGRADIENT)
            elif bc_lower == 'freestream':
                return int(TBoundaryType.FREESTREAM)

        elif field_lower in ['rho', 'density']:
            if bc_lower == 'fixedvalue':
                return int(RhoBoundaryType.FIXEDVALUE)
            elif bc_lower == 'zerogradient':
                return int(RhoBoundaryType.ZEROGRADIENT)
            elif bc_lower == 'freestream':
                return int(RhoBoundaryType.FREESTREAM)

        elif field_lower in ['p_rgh', 'prgh']:
            if bc_lower == 'fixedvalue':
                return int(PrghBoundaryType.FIXEDVALUE)
            elif bc_lower == 'uniformfixedvalue':
                return int(PrghBoundaryType.UNIFORMFIXEDVALUE)
            elif bc_lower == 'zerogradient':
                return int(PrghBoundaryType.ZEROGRADIENT)
            elif bc_lower == 'fixedfluxpressure':
                return int(PrghBoundaryType.FIXEDFLUXPRESSURE)
            elif bc_lower == 'totalpressure':
                return int(PrghBoundaryType.TOTALPRESSURE)
            elif bc_lower == 'porousbafflepressure':
                return int(PrghBoundaryType.POROUSBAFFLEPRESSURE)
            elif bc_lower == 'freestreampressure':
                return int(PrghBoundaryType.FREESTREAMPRESSURE)
            elif bc_lower == 'freestream':
                return int(PrghBoundaryType.FREESTREAM)
            elif bc_lower == 'calculated':
                return int(PrghBoundaryType.CALCULATED)

        return 0  # Default to NORMAL

    def _get_field_codebook(self, field: str) -> dict:
        """Get the boundary type codebook for a specific field."""
        field_lower = field.lower().strip()

        if field_lower in ['u', 'velocity']:
            return {int(v): v.name for v in UBoundaryType}
        elif field_lower in ['p', 'pressure']:
            return {int(v): v.name for v in PBoundaryType}
        elif field_lower in ['t', 'temperature']:
            return {int(v): v.name for v in TBoundaryType}
        elif field_lower in ['rho', 'density']:
            return {int(v): v.name for v in RhoBoundaryType}
        elif field_lower in ['p_rgh', 'prgh']:
            return {int(v): v.name for v in PrghBoundaryType}
        else:
            # Generic codebook for unknown fields
            return {
                0: 'NORMAL',
                99: 'CYCLIC',
                -1: 'EMPTY'
            }

    def _parse_boundary_conditions_from_0(self) -> Dict:
        boundary_conditions_0: Dict[str, Dict] = {}
        zero_dir = os.path.join(self.case_path, '0')
        if not os.path.isdir(zero_dir):
            logging.warning("0 directory not found at %s", zero_dir)
            return boundary_conditions_0
        try:
            for fname in sorted(os.listdir(zero_dir)):
                fpath = os.path.join(zero_dir, fname)
                if not os.path.isfile(fpath) or fname.startswith('.'):
                    continue
                field_name = fname
                # Only parse files in the valid_bc_file_list
                if field_name not in self.valid_bc_file_list:
                    continue
                try:
                    field_bcs = self._parse_field_file(fpath)
                    if field_bcs:
                        boundary_conditions_0[field_name] = field_bcs
                except Exception as e:
                    logging.warning("Failed to parse field file %s: %s", fpath, e)
        except Exception as e:
            logging.error("Error listing 0 directory: %s", e)
        return boundary_conditions_0

    def _parse_field_file(self, field_file_path: str) -> Dict:
        boundary_conditions = {}
        try:
            with open(field_file_path, 'r') as f:
                content = f.read()

            # Find boundaryField section using a more robust approach
            boundary_start = content.find('boundaryField')
            if boundary_start == -1:
                logging.warning(f"No boundaryField found in {field_file_path}")
                return boundary_conditions

            # Find the opening brace after boundaryField
            brace_start = content.find('{', boundary_start)
            if brace_start == -1:
                logging.warning(f"No opening brace found after boundaryField in {field_file_path}")
                return boundary_conditions

            # Find the matching closing brace
            brace_count = 0
            boundary_end = brace_start
            for i, char in enumerate(content[brace_start:], brace_start):
                if char == '{':
                    brace_count += 1
                elif char == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        boundary_end = i
                        break

            if brace_count != 0:
                logging.warning(f"Unmatched braces in boundaryField section of {field_file_path}")
                return boundary_conditions

            # Extract boundary content
            boundary_content = content[brace_start + 1:boundary_end]

            # Parse patches using a more robust approach for nested structures
            boundary_conditions = self._parse_patches_with_nesting(boundary_content)

        except Exception as e:
            logging.error("Failed to parse boundary conditions from %s: %s", field_file_path, e)
        return boundary_conditions

    def _parse_patches_with_nesting(self, boundary_content: str) -> Dict:
        """
        Parse patches that may contain nested structures like uniformFixedValue with expressions.
        Only parse top-level patch definitions, not nested structures.
        """
        boundary_conditions = {}

        # Split content into lines and find patch definitions
        lines = boundary_content.split('\n')
        i = 0

        while i < len(lines):
            line = lines[i].strip()

            # Skip empty lines and comments
            if not line or line.startswith('//') or line.startswith('/*'):
                i += 1
                continue

            # Look for patch definition: patch_name followed by opening brace
            # This should be at the beginning of a line (top-level)
            patch_match = re.match(r'^(?:"([^"]+)"|(\w+))\s*$', line)
            if patch_match and i + 1 < len(lines) and lines[i + 1].strip() == '{':
                # Found a patch definition
                patch_name = patch_match.group(1) if patch_match.group(1) else patch_match.group(2)

                # Find the complete patch content by counting braces
                brace_count = 0
                patch_lines = []
                j = i + 1  # Start from the opening brace line

                while j < len(lines):
                    current_line = lines[j]
                    patch_lines.append(current_line)

                    # Count braces
                    brace_count += current_line.count('{')
                    brace_count -= current_line.count('}')

                    if brace_count == 0:
                        # Found the closing brace
                        break
                    j += 1

                if brace_count == 0:
                    # Successfully extracted patch content
                    patch_content = '\n'.join(patch_lines[1:-1])  # Exclude opening and closing brace lines

                    # Parse the patch content
                    try:
                        patch_bc = self._parse_single_patch(patch_content, patch_name)
                        boundary_conditions[patch_name] = patch_bc
                    except Exception as e:
                        logging.warning(f"Failed to parse patch {patch_name}: {e}")
                        # Provide fallback
                        boundary_conditions[patch_name] = {'type': 'unknown', 'value': None}

                    # Move to the line after this patch
                    i = j + 1
                else:
                    logging.warning(f"Unmatched braces for patch {patch_name}")
                    i += 1
            else:
                i += 1

        return boundary_conditions

    def _extract_balanced_braces(self, content: str, start_pos: int) -> Optional[str]:
        """
        Extract content between balanced braces starting from start_pos.
        """
        brace_count = 0
        i = start_pos

        while i < len(content):
            char = content[i]
            if char == '{':
                brace_count += 1
            elif char == '}':
                brace_count -= 1
                if brace_count == 0:
                    # Return content without the outer braces
                    return content[start_pos + 1:i]
            i += 1

        return None  # Unmatched braces

    def _clean_openfoam_content(self, content: str) -> str:
        """
        Clean OpenFOAM content by removing comments and normalizing whitespace.

        Args:
            content: Raw OpenFOAM content

        Returns:
            Cleaned content
        """
        lines = []
        for line in content.split('\n'):
            # Remove C++ style comments
            if '//' in line:
                line = line[:line.index('//')]
            # Remove C style comments (single line)
            line = re.sub(r'/\*.*?\*/', '', line)
            # Strip whitespace and add if not empty
            line = line.strip()
            if line:
                lines.append(line)

        return '\n'.join(lines)

    def _extract_parameters_generic(self, content: str) -> Dict[str, str]:
        """
        Generic parameter extraction from OpenFOAM content.

        This method extracts all parameter-value pairs in the format:
        parameterName value;

        Args:
            content: Cleaned OpenFOAM content

        Returns:
            Dictionary of parameter names to values
        """
        result = {}

        # Clean content first
        content = self._clean_openfoam_content(content)

        # Split into lines and process each line
        lines = content.split('\n')
        i = 0

        # Track brace depth to skip nested structures
        brace_depth = 0

        while i < len(lines):
            line = lines[i].strip()

            # Skip empty lines
            if not line:
                i += 1
                continue

            # Track brace depth
            if line == '{':
                brace_depth += 1
                i += 1
                continue
            elif line == '}':
                brace_depth -= 1
                i += 1
                continue

            # Only process parameters at top level (brace_depth == 0)
            if brace_depth == 0:
                # Look for parameter-value pairs ending with semicolon
                if ';' in line:
                    # Extract everything before the semicolon
                    param_line = line[:line.index(';')].strip()

                    # Split into parameter name and value
                    parts = param_line.split(None, 1)  # Split on first whitespace
                    if len(parts) == 2:
                        param_name, param_value = parts
                        result[param_name] = param_value.strip()
                    elif len(parts) == 1:
                        # Handle cases like "type fixedValue;"
                        param_name = parts[0]
                        result[param_name] = ""

                # Handle multi-line values (like nonuniform lists and nested structures)
                elif line.endswith('(') or 'nonuniform' in line or ('{' in line and not ';' in line):
                    # This is a multi-line value, collect until closing
                    param_parts = line.split(None, 1)
                    if len(param_parts) >= 1:
                        param_name = param_parts[0]
                        value_parts = [line]

                        # Collect lines until we find the closing semicolon or brace
                        i += 1
                        paren_count = line.count('(') - line.count(')')
                        local_brace_count = line.count('{') - line.count('}')

                        while i < len(lines):
                            current_line = lines[i].strip()
                            value_parts.append(current_line)

                            paren_count += current_line.count('(') - current_line.count(')')
                            local_brace_count += current_line.count('{') - current_line.count('}')

                            # Stop when all braces/parentheses are closed
                            if paren_count <= 0 and local_brace_count <= 0:
                                break
                            i += 1

                        # Join and extract value
                        full_value = ' '.join(value_parts)

                        # Handle nested structures (like fanCurveCoeffs { file "path"; })
                        if '{' in full_value and '}' in full_value:
                            # This is a nested structure - store as raw content
                            brace_start = full_value.find('{')
                            brace_end = full_value.rfind('}')
                            if brace_start != -1 and brace_end != -1:
                                nested_content = full_value[brace_start+1:brace_end].strip()
                                result[param_name] = nested_content
                        else:
                            # Handle other multi-line values
                            if ';' in full_value:
                                value_part = full_value[:full_value.index(';')].strip()
                                # Remove parameter name from the beginning
                                if value_part.startswith(param_name):
                                    value_part = value_part[len(param_name):].strip()
                                result[param_name] = value_part
                            else:
                                # Remove parameter name from the beginning
                                value_part = full_value
                                if value_part.startswith(param_name):
                                    value_part = value_part[len(param_name):].strip()
                                result[param_name] = value_part

            i += 1

        return result

    def _parse_single_patch(self, patch_content: str, patch_name: str) -> Dict:
        """
        Parse a single patch using generic parameter extraction.

        This new approach extracts ALL parameters generically instead of
        hardcoding specific boundary condition types.
        """
        # First extract type to determine boundary condition type
        # Look for the first type declaration (not nested ones)
        type_match = re.search(r'^\s*type\s+(\w+)\s*;', patch_content, re.MULTILINE)
        bc_type = type_match.group(1) if type_match else 'unknown'

        # Use generic parameter extraction
        result = self._extract_parameters_generic(patch_content)

        # Ensure we have the correct type (don't let generic parser override it)
        result['type'] = bc_type



        # Handle special nested structures that need custom parsing
        if bc_type == 'uniformFixedValue':
            # Look for uniformValue nested structure
            uniform_value_match = re.search(r'uniformValue\s*\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}', patch_content)
            if uniform_value_match:
                uniform_content = uniform_value_match.group(1).strip()
                # Parse the nested uniformValue structure
                nested_result = self._parse_nested_structure(uniform_content, patch_name)
                if nested_result:
                    result['uniformValue'] = nested_result
                    # Check if it contains expressions
                    # if nested_result.get('type') == 'expression':
                    #     logging.warning(f"Found expression in patch '{patch_name}'. Manual configuration may be required.")

                    # Remove any nested parameters that were extracted to top level by generic parser
                    # to avoid conflicts with the nested structure
                    # But preserve the top-level type (uniformFixedValue)
                    for nested_key in nested_result.keys():
                        if nested_key in result and nested_key != 'uniformValue' and nested_key != 'type':
                            del result[nested_key]

        # Handle special nested structures that need custom formatting
        if bc_type == 'fanPressure':
            # Handle fanCurveCoeffs nested structure
            fan_curve_coeffs_match = re.search(r'fanCurveCoeffs\s*\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}', patch_content)
            if fan_curve_coeffs_match:
                coeffs_content = fan_curve_coeffs_match.group(1).strip()
                # Parse file path from fanCurveCoeffs and convert to inline table format
                file_match = re.search(r'file\s+"([^"]+)"|file\s+<([^>]+)>', coeffs_content)
                if file_match:
                    file_path = file_match.group(1) if file_match.group(1) else file_match.group(2)
                    # Create proper inline table for fanCurveCoeffs
                    import tomlkit
                    inline_table = tomlkit.inline_table()
                    inline_table['value_type'] = 'file'
                    inline_table['val'] = file_path
                    result['fanCurveCoeffs'] = inline_table

        # Note: All other parameters are now extracted generically by _extract_parameters_generic()
        # This includes: p0, rho, psi, gamma, D, I, length, uniformJump, jump, inletValue,
        # gradient, volumetricFlowRate, massFlowRate, rhoInlet, freestreamValue, direction, etc.

        return result

    def _parse_nested_structure(self, content: str, patch_name: str) -> Optional[Dict]:
        """
        Parse nested structure content (like uniformValue block).

        Args:
            content: Content inside the nested block
            patch_name: Name of the patch for logging

        Returns:
            Dictionary representing the nested structure, or None if parsing fails
        """
        try:
            nested_result = {}

            # Extract type
            type_match = re.search(r'type\s+(\w+)\s*;', content)
            if type_match:
                nested_type = type_match.group(1)
                nested_result['type'] = nested_type

                # Warn about expressions
                # if nested_type == 'expression':
                #     logging.warning(f"Expression type detected in patch '{patch_name}'. This requires manual configuration.")

            # Extract expression if present
            expr_match = re.search(r'expression\s+"([^"]+)"\s*;', content)
            if expr_match:
                nested_result['expression'] = expr_match.group(1)

            # Extract variables if present
            var_match = re.search(r'variables\s+\(\s*"([^"]+)"\s*\)\s*;', content)
            if var_match:
                nested_result['variables'] = var_match.group(1)

            # Extract other key-value pairs
            kv_pattern = r'(\w+)\s+([^;]+);'
            for key, value in re.findall(kv_pattern, content):
                if key not in ['type', 'expression', 'variables']:
                    nested_result[key] = value.strip()

            return nested_result if nested_result else None

        except Exception as e:
            logging.warning(f"Failed to parse nested structure in patch '{patch_name}': {e}")
            return None

    def _parse_boundary_file_from_polymesh(self) -> Dict:
        """
        Parse boundary file from polyMesh/boundary with caching to avoid repeated file I/O.

        Returns:
            Dict containing boundary patch information with geometry types
        """
        # Return cached result if available
        if self._boundary_polymesh_cache is not None:
            return self._boundary_polymesh_cache

        boundary_file = os.path.join(self.case_path, 'constant', 'polyMesh', 'boundary')
        detailed_info: Dict[str, Dict] = {}

        if not os.path.exists(boundary_file):
            # Cache empty result
            self._boundary_polymesh_cache = detailed_info
            return detailed_info

        try:
            with open(boundary_file, 'r') as f:
                content = f.read()
            patch_pattern = r'(\w+)\s*\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}'
            patches = re.findall(patch_pattern, content)
            for patch_name, patch_content in patches:
                if patch_name in ['FoamFile', 'boundaryField']:
                    continue
                patch_info = {}
                kv_pattern = r'(\w+)\s+([^;]+);'
                for key, value in re.findall(kv_pattern, patch_content):
                    value = value.strip()
                    if value.startswith('"') and value.endswith('"'):
                        value = value[1:-1]
                    patch_info[key] = value
                detailed_info[patch_name] = patch_info
        except Exception as e:
            logging.error("Failed to parse polyMesh/boundary: %s", e)

        # Cache the result for future calls
        self._boundary_polymesh_cache = detailed_info
        return detailed_info

    def _clear_boundary_cache(self):
        """
        Clear the boundary file cache. Useful if the boundary file has been modified.
        """
        self._boundary_polymesh_cache = None

    def export_boundary_conditions_to_toml(self, output_dir: str):
        """
        Export boundary conditions from 0/ directory to TOML format.
        Creates separate TOML files for each field in the specified output directory.

        Args:
            output_dir: Directory where TOML files will be saved (e.g., case_path/h5/bc)
        """
        # Parse boundary conditions from 0/ directory
        boundary_0 = self._parse_boundary_conditions_from_0()

        if not boundary_0:
            logging.warning("No boundary conditions found in 0/ directory")
            return

        # Parse boundary geometry types from polyMesh/boundary
        boundary_geom_types = self._parse_boundary_file_from_polymesh()

        # Create output directory
        os.makedirs(output_dir, exist_ok=True)

        # Export each field to a separate TOML file
        for field_name, field_bcs in boundary_0.items():
            # Parse the complete field file to get dimensions and internalField
            field_file_path = os.path.join(self.case_path, '0', field_name)
            field_info = self._parse_complete_field_file(field_file_path)

            # Start with field info
            toml_data = {
                'field_info': {
                    'name': field_name,
                    'case_path': self.case_path,
                    'description': f'Boundary conditions for field {field_name}'
                }
            }

            # Add dimensions if available
            if field_info.get('dimensions'):
                toml_data['dimensions'] = {
                    'value': field_info['dimensions']
                }

            # Add internalField as a section if available
            if field_info.get('internalField'):
                internal_value = self._convert_to_toml_value_format(field_info['internalField'])
                if internal_value is not None:
                    toml_data['internalField'] = {'value': internal_value}

            # Note: We no longer use a fixed 'boundary' section,
            # instead we use geometry type as section prefix

            # Process each patch for this field
            for patch_name, bc_info in field_bcs.items():
                bc_type = bc_info.get('type', 'unknown')
                bc_value = bc_info.get('value', None)
                freestream_value = bc_info.get('freestreamValue', None)
                is_complex = bc_info.get('complex', False)
                note = bc_info.get('note', None)

                # Get geometry type from polyMesh/boundary file
                geom_type = 'boundary'  # Default fallback
                if patch_name in boundary_geom_types:
                    geom_type = boundary_geom_types[patch_name].get('type', 'boundary')

                # Create patch section with geometry type prefix
                patch_data = {'type': bc_type}

                # Handle complex boundary conditions
                if is_complex:
                    patch_data['note'] = note
                    patch_data['manual_config_required'] = True
                    # logging.warning(f"Patch '{patch_name}' contains complex expressions. "
                    #               f"Manual configuration required in TOML file.")
                else:
                    # Handle special boundary condition parameters
                    if bc_type == 'fanPressure':
                        # Add fanPressure-specific parameters
                        for param in ['p0', 'patchType', 'rho', 'psi', 'gamma', 'fanCurve', 'direction']:
                            if param in bc_info:
                                if param == 'p0':
                                    # Parse p0 as a value
                                    parsed_p0 = self._convert_to_toml_value_format(bc_info[param])
                                    if parsed_p0 is not None:
                                        patch_data[param] = parsed_p0
                                elif param == 'fanCurve':
                                    # Check if fanCurve is already in inline table format
                                    fan_curve_value = bc_info[param]
                                    if fan_curve_value.startswith('{') and fan_curve_value.endswith('}'):
                                        # It's already an inline table string, parse it
                                        try:
                                            # Convert string representation to actual inline table
                                            inline_table = tomlkit.inline_table()
                                            # Parse the inline table string manually
                                            content = fan_curve_value[1:-1]  # Remove { }
                                            pairs = [pair.strip() for pair in content.split(',')]
                                            for pair in pairs:
                                                if '=' in pair:
                                                    key, value = pair.split('=', 1)
                                                    key = key.strip().strip('"')
                                                    value = value.strip().strip('"')
                                                    inline_table[key] = value
                                            patch_data[param] = inline_table
                                        except Exception as e:
                                            # Fallback to string if parsing fails
                                            logging.warning(f"Failed to parse fanCurve inline table: {e}")
                                            patch_data[param] = fan_curve_value
                                    else:
                                        # Store as string
                                        patch_data[param] = fan_curve_value
                                else:
                                    # Store other parameters as strings
                                    patch_data[param] = bc_info[param]

                    # Generic parameter processing - handle ALL parameters automatically
                    # Define which parameters need special value format parsing vs simple conversion
                    value_format_params = {
                        'value', 'inletValue', 'gradient', 'jump', 'p0', 'freestreamValue',
                        'volumetricFlowRate', 'massFlowRate'
                    }

                    for param_name, param_value in bc_info.items():
                        # Skip type as it's already handled
                        if param_name == 'type':
                            continue

                        # Skip nested structures (handled separately)
                        if param_name in ['uniformValue']:
                            continue

                        # Handle special inline table formats
                        if param_name in ['fanCurveCoeffs'] and isinstance(param_value, str) and param_value.startswith('{'):
                            # This is already in inline table format, parse it
                            try:
                                # Extract the inline table content
                                if 'value_type="file"' in param_value:
                                    # Parse the file path from the inline table format
                                    file_match = re.search(r'val="([^"]+)"', param_value)
                                    if file_match:
                                        file_path = file_match.group(1)
                                        inline_table = tomlkit.inline_table()
                                        inline_table['value_type'] = 'file'
                                        inline_table['val'] = file_path
                                        patch_data[param_name] = inline_table
                                        continue
                            except Exception as e:
                                logging.warning(f"Failed to parse inline table for {param_name}: {e}")
                                # Fall back to string
                                patch_data[param_name] = str(param_value)
                                continue

                        try:
                            if param_name in value_format_params:
                                # These parameters might have complex formats (uniform, constant, etc.)
                                parsed_param = self._convert_to_toml_value_format(param_value)
                                if parsed_param is not None:
                                    patch_data[param_name] = parsed_param
                            else:
                                # Simple parameters - use smart type conversion
                                converted_value = self._convert_simple_value(param_value)
                                if converted_value is not None:
                                    patch_data[param_name] = converted_value
                                # Note: if converted_value is None (from "none"), we skip adding this parameter
                        except Exception:
                            # If parsing fails, store as string
                            # logging.warning(f"Failed to parse parameter '{param_name}' with value '{param_value}': {e}")
                            patch_data[param_name] = str(param_value)

                    # Handle nested structures (like uniformValue)
                    uniform_value = bc_info.get('uniformValue', None)
                    if uniform_value:
                        nested_inline_table = self._convert_nested_to_inline_table(uniform_value)
                        if nested_inline_table is not None:
                            patch_data['uniformValue'] = nested_inline_table

                # Use geometry type as section prefix instead of 'boundary'
                # Create nested structure: geom_type -> patch_name
                if geom_type not in toml_data:
                    toml_data[geom_type] = {}
                toml_data[geom_type][patch_name] = patch_data

            # Write TOML file using tomlkit
            toml_file_path = os.path.join(output_dir, f"{field_name}.toml")
            try:
                with open(toml_file_path, 'w') as f:
                    tomlkit.dump(toml_data, f)
                logging.info(f"Exported boundary conditions for {field_name} to {toml_file_path}")
            except Exception as e:
                logging.error(f"Failed to write TOML file for {field_name}: {e}")

    def _convert_simple_value(self, value_str):
        """
        Convert a simple OpenFOAM value to appropriate Python type.

        Args:
            value_str: String value from OpenFOAM file

        Returns:
            Converted value (bool, int, float, None, or str)
        """
        if not isinstance(value_str, str):
            return value_str

        value_str = value_str.strip()

        # Handle boolean values
        if value_str.lower() == 'true':
            return True
        elif value_str.lower() == 'false':
            return False

        # Handle none/null values - keep as string for OpenFOAM compatibility
        # In OpenFOAM, "none" is a meaningful string value, not null
        if value_str.lower() in ['none', 'null']:
            return value_str  # Keep as string, don't convert to None

        # Handle numeric values
        try:
            # Try integer first
            if '.' not in value_str and 'e' not in value_str.lower():
                return int(value_str)
            else:
                return float(value_str)
        except ValueError:
            # Return as string if not numeric
            return value_str

    def _convert_to_toml_value_format(self, value_input):
        """
        Convert boundary condition value to TOML inline table format.

        Args:
            value_input: Raw value string from OpenFOAM file or parsed value list

        Returns:
            tomlkit.InlineTable with value_type and val, or None if no value
        """
        if not value_input:
            return None

        # Handle already parsed values (from _parse_boundary_value_simple)
        if isinstance(value_input, list) and len(value_input) == 2:
            value_type, val = value_input
            # Validate value_type
            if value_type not in support_val_type:
                if value_type == "raw":
                    # Try to parse raw value
                    return self._convert_to_toml_value_format(val)
                elif value_type == "$internalField":
                    return None
                else:
                    raise ValueError(f"Unsupported value_type '{value_type}'. Supported types: {support_val_type}")

            # Create inline table
            inline_table = tomlkit.inline_table()
            inline_table['value_type'] = value_type
            inline_table['val'] = val
            return inline_table

        # Handle raw string input
        if isinstance(value_input, str):
            value_str = value_input.strip()

            # Handle uniform values: uniform (1.0 0 0) or uniform 0
            uniform_match = re.match(r'uniform\s+(.+)', value_str)
            if uniform_match:
                uniform_value = uniform_match.group(1).strip()

                # Vector values: (1.0 0 0)
                vector_match = re.match(r'\(([^)]+)\)', uniform_value)
                if vector_match:
                    components = vector_match.group(1).split()
                    try:
                        val = [float(c) for c in components]
                    except ValueError:
                        val = components  # Keep as strings if not numeric
                else:
                    # Scalar value
                    try:
                        val = float(uniform_value)
                    except ValueError:
                        val = uniform_value  # Keep as string if not numeric

                # Create inline table
                inline_table = tomlkit.inline_table()
                inline_table['value_type'] = 'uniform'
                inline_table['val'] = val
                return inline_table

            # Handle constant values: constant (1.0 0 0) or constant 0
            constant_match = re.match(r'constant\s+(.+)', value_str)
            if constant_match:
                constant_value = constant_match.group(1).strip()

                # Vector values: (1.0 0 0)
                vector_match = re.match(r'\(([^)]+)\)', constant_value)
                if vector_match:
                    components = vector_match.group(1).split()
                    try:
                        val = [float(c) for c in components]
                    except ValueError:
                        val = components  # Keep as strings if not numeric
                else:
                    # Scalar value
                    try:
                        val = float(constant_value)
                    except ValueError:
                        val = constant_value  # Keep as string if not numeric

                # Create inline table
                inline_table = tomlkit.inline_table()
                inline_table['value_type'] = 'constant'
                inline_table['val'] = val
                return inline_table

            # Handle nonuniform values: nonuniform List<vector> or nonuniform List<scalar>
            nonuniform_match = re.match(r'nonuniform\s+List<(\w+)>\s*(\d+)\s*\((.*)\)', value_str, re.DOTALL)
            if nonuniform_match:
                data_type = nonuniform_match.group(1)  # vector, scalar, etc.
                count = int(nonuniform_match.group(2))
                data_content = nonuniform_match.group(3).strip()

                # Parse the data content
                val = self._parse_nonuniform_data(data_content, data_type, count)

                # Create inline table
                inline_table = tomlkit.inline_table()
                inline_table['value_type'] = 'nonuniform'
                inline_table['data_type'] = data_type
                inline_table['count'] = count
                inline_table['val'] = val
                return inline_table

            # Handle simple numeric values (like "2", "1.5", etc.)
            try:
                # Try to parse as a simple number
                if '.' in value_str or 'e' in value_str.lower():
                    val = float(value_str)
                else:
                    val = int(value_str)

                # Create inline table for constant value
                inline_table = tomlkit.inline_table()
                inline_table['value_type'] = 'constant'
                inline_table['val'] = val
                return inline_table
            except ValueError:
                pass

            # Handle other formats - raise error for unsupported types
            raise ValueError(f"Unsupported value format '{value_str}'. Supported formats: {support_val_type}")

        return None

    def _parse_nonuniform_data(self, data_content: str, data_type: str, count: int) -> List:
        """
        Parse nonuniform data content from OpenFOAM format.

        Args:
            data_content: The content inside the parentheses
            data_type: Type of data (vector, scalar, etc.)
            count: Expected number of entries

        Returns:
            List of parsed values
        """
        values = []

        if data_type.lower() == 'vector':
            # Parse vector data: (1.0 0 0) (0.5 0.2 0) ...
            vector_pattern = r'\(([^)]+)\)'
            vector_matches = re.findall(vector_pattern, data_content)

            for vector_str in vector_matches:
                components = vector_str.split()
                try:
                    vector_val = [float(c) for c in components]
                    values.append(vector_val)
                except ValueError:
                    # Keep as strings if not numeric
                    values.append(components)

        elif data_type.lower() == 'scalar':
            # Parse scalar data: 101325 101330 101335 ...
            # Remove any parentheses and split by whitespace
            scalar_content = re.sub(r'[()]', '', data_content)
            scalar_strings = scalar_content.split()

            for scalar_str in scalar_strings:
                scalar_str = scalar_str.strip()
                if scalar_str:  # Skip empty strings
                    try:
                        scalar_val = float(scalar_str)
                        values.append(scalar_val)
                    except ValueError:
                        # Keep as string if not numeric
                        values.append(scalar_str)
        else:
            # For other data types, try to parse as generic values
            logging.warning(f"Unknown nonuniform data type '{data_type}', treating as generic")
            # Split by whitespace and try to parse
            parts = data_content.split()
            for part in parts:
                part = part.strip('()')
                if part:
                    try:
                        values.append(float(part))
                    except ValueError:
                        values.append(part)

        # Validate count
        if len(values) != count:
            logging.warning(f"Expected {count} values for nonuniform data, but got {len(values)}")

        return values

    def _convert_nested_to_inline_table(self, nested_dict: Dict):
        """
        Convert nested structure (like uniformValue) to TOML inline table.

        Args:
            nested_dict: Dictionary containing nested structure data

        Returns:
            tomlkit.InlineTable representing the nested structure
        """
        if not isinstance(nested_dict, dict):
            return None

        try:
            inline_table = tomlkit.inline_table()

            # Handle special case for expression type
            if nested_dict.get('type') == 'expression':
                inline_table['value_type'] = 'expression'
                if 'expression' in nested_dict:
                    inline_table['expression'] = nested_dict['expression']
                if 'variables' in nested_dict:
                    # Format variables properly for TOML
                    variables = nested_dict['variables']
                    # Clean up the variables format
                    if variables.startswith('(') and variables.endswith(')'):
                        variables = variables[1:-1].strip()
                    if variables.startswith('"') and variables.endswith('"'):
                        variables = variables[1:-1]
                    inline_table['variables'] = f'( "{variables}" )'
            else:
                # Add all key-value pairs to the inline table
                for key, value in nested_dict.items():
                    if isinstance(value, str):
                        inline_table[key] = value
                    elif isinstance(value, (int, float, bool)):
                        inline_table[key] = value
                    elif isinstance(value, list):
                        inline_table[key] = value
                    else:
                        # Convert other types to string
                        inline_table[key] = str(value)

            return inline_table

        except Exception as e:
            logging.warning(f"Failed to convert nested structure to inline table: {e}")
            return None

    def _parse_complete_field_file(self, field_file_path: str) -> Dict:
        """
        Parse complete OpenFOAM field file to extract dimensions, internalField, and boundary conditions.

        Args:
            field_file_path: Path to the field file

        Returns:
            Dictionary containing dimensions, internalField, and other field info
        """
        field_info = {}

        try:
            with open(field_file_path, 'r') as f:
                content = f.read()

            # Parse dimensions
            dimensions_match = re.search(r'dimensions\s+\[([^\]]+)\]', content)
            if dimensions_match:
                dim_str = dimensions_match.group(1).strip()
                try:
                    # Parse dimensions as list of numbers
                    dimensions = [float(x) for x in dim_str.split()]
                    field_info['dimensions'] = dimensions
                except ValueError:
                    # Keep as string if parsing fails
                    field_info['dimensions'] = dim_str.split()

            # Parse internalField
            internal_match = re.search(r'internalField\s+(.+?);', content, re.DOTALL)
            if internal_match:
                internal_str = internal_match.group(1).strip()
                # Store as raw string for later conversion to TOML format
                field_info['internalField'] = internal_str

        except Exception as e:
            logging.warning(f"Failed to parse complete field file {field_file_path}: {e}")

        return field_info


def export_boundary_conditions_standalone(case_path: str):
    """
    Export all boundary conditions from an OpenFOAM case to TOML format.
    Creates case_path/h5/bc/ directory and saves TOML files there.

    Args:
        case_path: Path to OpenFOAM case directory
    """
    # Validate case structure first
    try:
        BoundaryParser.validate_case_structure(case_path)
    except FileNotFoundError as e:
        logging.error(f"Case validation failed: {e}")
        raise

    # Create output directory
    output_dir = os.path.join(case_path, 'h5', 'bc')

    # Create BoundaryParser with dummy data (we only need the parsing functionality)
    parser = BoundaryParser(
        case_path=case_path,
        faces_raw=[],
        owner_raw=torch.zeros(0, dtype=torch.long),
        neighbour_raw=torch.zeros(0, dtype=torch.long),
        boundary_patch={}
    )

    # Export boundary conditions
    parser.export_boundary_conditions_to_toml(output_dir)

    logging.info(f"Boundary conditions exported to {output_dir}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Test OpenFOAM boundary parsing and TOML export")
    parser.add_argument('--case', type=str, default='mesh_example/STDCT', help='Path to OpenFOAM case dir')
    parser.add_argument('--export-toml', default=True, action='store_true', help='Export boundary conditions to TOML format')
    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

    # Validate case structure first
    try:
        BoundaryParser.validate_case_structure(args.case)
    except FileNotFoundError as e:
        logging.error(f"❌ Case validation failed: {e}")
        logging.error(f"❌ Cannot proceed without valid OpenFOAM mesh files")
        exit(1)
    except Exception as e:
        logging.error(f"❌ Unexpected error during validation: {e}")
        exit(1)

    # Lightweight test: we only parse files here, not full mesh
    try:
        # Dummy placeholders for faces_raw/owner/neighbour; for CLI test we only check the file parsing parts
        # So we guard against missing mesh by printing available patch info and fields
        bp = BoundaryParser(args.case, faces_raw=[], owner_raw=torch.zeros(0, dtype=torch.long),
                            neighbour_raw=torch.zeros(0, dtype=torch.long), boundary_patch={})
        b0 = bp._parse_boundary_conditions_from_0()
        bpoly = bp._parse_boundary_file_from_polymesh()
        print("Parsed fields under 0/:", sorted(list(b0.keys())))
        print("Parsed patches from polyMesh/boundary:", sorted(list(bpoly.keys())))

        # Export to TOML if requested
        if args.export_toml:
            print(f"\nExporting boundary conditions to TOML format...")
            export_boundary_conditions_standalone(args.case)
            print(f"TOML files saved to {args.case}/h5/bc/")

    except Exception as e:
        logging.error(f"❌ BoundaryParser test failed: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
