"""
Calculate Unsigned Distance Field (UDF) for OpenFOAM mesh

This module computes the unsigned distance from query points (cpd_cell positions)
to the surface mesh (boundary patches excluding "room" patches).
"""

import logging
import numpy as np
import pyvista as pv
import torch
from typing import Dict, Optional

try:
    from vtk import vtkImplicitPolyDataDistance, vtkDoubleArray
    VTK_AVAILABLE = True
except ImportError:
    VTK_AVAILABLE = False
    logging.warning("VTK modules not available, UDF computation may be slower")


def extract_surface_mesh_from_boundary(
    boundary_block: pv.MultiBlock,
    patch_list: Dict,
    exclude_prefix: str = "room"
) -> Optional[pv.PolyData]:
    """
    Extract surface mesh from boundary MultiBlock by excluding patches with specific prefix.

    Args:
        boundary_block: PyVista MultiBlock containing all boundary patches
        patch_list: Dictionary containing patch information with patch names as keys
        exclude_prefix: Prefix of patches to exclude (default: "room")

    Returns:
        Combined surface mesh as PyVista PolyData, or None if no valid patches found
    """
    if boundary_block is None or not patch_list:
        logging.warning("No boundary block or patch_list provided")
        return None

    surface_meshes = []
    excluded_patches = []
    included_patches = []

    # Iterate through all patches in the boundary MultiBlock
    for i in range(boundary_block.n_blocks):
        block = boundary_block[i]
        block_name = boundary_block.get_block_name(i)

        # Check if this patch is in our patch_list
        if block_name not in patch_list:
            logging.debug(f"Patch '{block_name}' found in VTK but not in patch_list, skipping")
            continue

        # Exclude patches starting with the specified prefix
        if block_name.startswith(exclude_prefix):
            excluded_patches.append(block_name)
            logging.debug(f"Excluding patch '{block_name}' (starts with '{exclude_prefix}')")
            continue

        # Add this patch to surface mesh
        if block is not None and block.n_points > 0:
            # Convert to PolyData if needed
            if isinstance(block, pv.PolyData):
                surface_meshes.append(block)
            else:
                # Try to extract surface
                try:
                    surface = block.extract_surface()
                    surface_meshes.append(surface)
                except Exception as e:
                    logging.warning(f"Failed to extract surface from patch '{block_name}': {e}")
                    continue

            included_patches.append(block_name)
            logging.debug(f"Including patch '{block_name}' ({block.n_points} points, {block.n_cells} cells)")

    # Combine all surface meshes
    if not surface_meshes:
        logging.warning("No valid surface patches found after filtering")
        return None

    if len(surface_meshes) == 1:
        combined_surface = surface_meshes[0]
    else:
        # Merge all surface meshes
        combined_surface = surface_meshes[0]
        for mesh in surface_meshes[1:]:
            combined_surface = combined_surface.merge(mesh)

    logging.info(f"Combined surface mesh: {combined_surface.n_points} points, {combined_surface.n_cells} cells")

    return combined_surface


def compute_udf_field(
    query_points: torch.Tensor,
    surface_mesh: pv.PolyData
) -> torch.Tensor:
    """
    Compute unsigned distance field from query points to surface mesh.

    Args:
        query_points: Query point positions [N, 3] as torch.Tensor
        surface_mesh: Surface mesh as PyVista PolyData

    Returns:
        Unsigned distances [N, 1] as torch.Tensor
    """
    if surface_mesh is None or surface_mesh.n_points == 0:
        logging.error("Invalid surface mesh provided")
        return torch.zeros((query_points.shape[0], 1), dtype=query_points.dtype)

    # Convert query points to numpy
    query_points_np = query_points.detach().cpu().numpy()

    logging.info(f"Computing UDF for {query_points_np.shape[0]} query points...")

    distances = None

    # Use VTK's vtkImplicitPolyDataDistance for efficient computation
    if VTK_AVAILABLE:
        try:
            # Create VTK implicit function
            implicit_func = vtkImplicitPolyDataDistance()
            implicit_func.SetInput(surface_mesh)

            # Convert points to VTK format
            points_vtk = pv.convert_array(query_points_np)

            # Create output array
            dists_vtk = vtkDoubleArray()

            # Compute signed distances (negative inside, positive outside)
            implicit_func.FunctionValue(points_vtk, dists_vtk)

            # Convert to numpy and take absolute value for UDF
            signed_distances = pv.convert_array(dists_vtk)
            distances = np.abs(signed_distances).reshape(-1, 1)

            logging.info(f"UDF computed using VTK vtkImplicitPolyDataDistance")
        except Exception as e:
            logging.warning(f"VTK method failed: {e}, falling back to PyVista method")
            distances = None

    if distances is None:
        # Fallback: use PyVista's built-in method
        try:
            # Create a temporary point cloud from query points
            point_cloud = pv.PolyData(query_points_np)
            # Compute implicit distance
            point_cloud = point_cloud.compute_implicit_distance(surface_mesh, inplace=True)
            # Extract distances and take absolute value
            signed_distances = point_cloud['implicit_distance']
            distances = np.abs(signed_distances).reshape(-1, 1)
            logging.info(f"UDF computed using PyVista compute_implicit_distance")
        except Exception as e:
            logging.error(f"All methods failed: {e}")
            # Last resort: return zeros
            distances = np.zeros((query_points_np.shape[0], 1))

    # Convert back to torch tensor
    udf_field = torch.from_numpy(distances).to(query_points.dtype)

    # Log statistics
    logging.info(f"UDF statistics:")
    logging.info(f"  - Min distance: {udf_field.min().item():.6e}")
    logging.info(f"  - Max distance: {udf_field.max().item():.6e}")
    logging.info(f"  - Mean distance: {udf_field.mean().item():.6e}")
    logging.info(f"  - Std distance: {udf_field.std().item():.6e}")

    return udf_field
