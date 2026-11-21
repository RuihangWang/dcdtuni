"""
Spatial Interpolation Utilities

Provides KNN-based interpolation functions for querying field values
at arbitrary spatial positions.

Supported methods:
- IDW (Inverse Distance Weighting): Simple, fast, works well for smooth fields
- RBF (Radial Basis Function): More accurate, passes through source points exactly
"""

import torch
from typing import Tuple, Optional, Dict, List, Literal
import logging
logger = logging.getLogger(__name__)


def find_knn_indices(
    query_pos: torch.Tensor,
    source_pos: torch.Tensor,
    k: int = 4
) -> Tuple[torch.Tensor, torch.Tensor]:
    """
    Find K nearest neighbors for each query point.
    
    Args:
        query_pos: [Q, 3] Query point coordinates
        source_pos: [N, 3] Source point coordinates
        k: Number of nearest neighbors
        
    Returns:
        indices: [Q, K] Indices of K nearest neighbors for each query
        distances: [Q, K] Distances to K nearest neighbors
    """
    # Compute pairwise distances: [Q, N]
    # Using broadcasting: (Q, 1, 3) - (1, N, 3) -> (Q, N, 3) -> (Q, N)
    diff = query_pos.unsqueeze(1) - source_pos.unsqueeze(0)  # [Q, N, 3]
    distances_all = torch.norm(diff, dim=-1)  # [Q, N]
    
    # Find K nearest neighbors
    # topk returns (values, indices), we want smallest so use negative or just topk with largest=False
    distances, indices = torch.topk(distances_all, k=k, dim=-1, largest=False)
    
    return indices, distances


def rbf_interpolate(
    query_pos: torch.Tensor,
    source_pos: torch.Tensor,
    source_values: torch.Tensor,
    k: int = 4,
    shape_param: float = 0.23,
    epsilon: float = 1e-8
) -> torch.Tensor:
    """
    Radial Basis Function (RBF) interpolation using multiquadric kernel.
    
    This method solves a local RBF system for each query point using its
    K nearest neighbors. More accurate than IDW as it passes exactly through
    source points and provides smoother interpolation.
    
    RBF kernel: φ(r) = √(r² + c²) where c is shape_param (multiquadric)
    
    Args:
        query_pos: [Q, 3] Query point coordinates
        source_pos: [N, 3] Source point coordinates
        source_values: [N, C] Source point values (C channels)
        k: Number of nearest neighbors (default: 4)
        shape_param: RBF shape parameter (default: 0.23)
        epsilon: Small value for numerical stability
        
    Returns:
        interpolated_values: [Q, C] Interpolated values at query positions
        
    Note:
        Adapted from FVInterpolation.rbf_interpolate for standalone use.
    """
    Q = query_pos.shape[0]
    C = source_values.shape[1]
    device = query_pos.device
    dtype = source_values.dtype
    
    # Find K nearest neighbors
    indices, distances = find_knn_indices(query_pos, source_pos, k)  # [Q, K], [Q, K]
    
    # Get neighbor positions and values: [Q, K, 3], [Q, K, C]
    neighbor_pos = source_pos[indices]  # [Q, K, 3]
    neighbor_values = source_values[indices]  # [Q, K, C]
    
    # Compute inter-source distance matrix: [Q, K, K]
    # Distance between each pair of neighbors for each query point
    neighbors_diff = neighbor_pos.unsqueeze(2) - neighbor_pos.unsqueeze(1)  # [Q, K, 1, 3] - [Q, 1, K, 3] -> [Q, K, K, 3]
    distances_squared = torch.sum(neighbors_diff * neighbors_diff, dim=-1)  # [Q, K, K]
    
    # Compute RBF kernel matrix using multiquadric basis function
    shape_param_sq = shape_param * shape_param
    kernel = torch.sqrt(distances_squared + shape_param_sq)  # [Q, K, K]
    
    # Add small regularization for numerical stability
    kernel = kernel + epsilon * torch.eye(k, device=device, dtype=dtype).unsqueeze(0)
    
    # Solve RBF system: kernel @ coeffs = neighbor_values
    # coeffs: [Q, K, C]
    try:
        coeffs = torch.linalg.solve(kernel, neighbor_values)
    except RuntimeError:
        # Fallback to pseudo-inverse if system is singular
        logger.warning("RBF system singular, using pseudo-inverse")
        coeffs = torch.linalg.lstsq(kernel, neighbor_values).solution
    
    # Compute query-to-neighbor distances: [Q, K]
    query_to_neighbor = query_pos.unsqueeze(1) - neighbor_pos  # [Q, 1, 3] - [Q, K, 3] -> [Q, K, 3]
    query_distances_squared = torch.sum(query_to_neighbor * query_to_neighbor, dim=-1)  # [Q, K]
    
    # Evaluate RBF at query points
    kernel_query = torch.sqrt(query_distances_squared + shape_param_sq)  # [Q, K]
    
    # Compute final interpolated values: [Q, K] @ [Q, K, C] -> [Q, C]
    # Using einsum for batch matrix-vector multiplication
    result = torch.einsum('qk,qkc->qc', kernel_query, coeffs)
    
    return result


def idw_interpolate(
    query_pos: torch.Tensor,
    source_pos: torch.Tensor,
    source_values: torch.Tensor,
    k: int = 4,
    power: float = 2.0,
    epsilon: float = 1e-8
) -> torch.Tensor:
    """
    Inverse Distance Weighting (IDW) interpolation.
    
    For each query point, finds K nearest neighbors and computes weighted average:
        value = sum(w_i * v_i) / sum(w_i)
        where w_i = 1 / (distance_i ^ power)
    
    If a query point coincides with a source point (distance ≈ 0),
    the source value is used directly.
    
    Args:
        query_pos: [Q, 3] Query point coordinates
        source_pos: [N, 3] Source point coordinates
        source_values: [N, C] Source point values (C channels, e.g., [u, v, w, p, T])
        k: Number of nearest neighbors (default: 4)
        power: IDW power parameter (default: 2.0, higher = more local)
        epsilon: Small value to avoid division by zero
        
    Returns:
        interpolated_values: [Q, C] Interpolated values at query positions
    """
    Q = query_pos.shape[0]
    C = source_values.shape[1]
    device = query_pos.device
    dtype = source_values.dtype
    
    # Find K nearest neighbors
    indices, distances = find_knn_indices(query_pos, source_pos, k)  # [Q, K], [Q, K]
    
    # Get neighbor values: [Q, K, C]
    neighbor_values = source_values[indices]
    
    # Compute IDW weights: w_i = 1 / (d_i ^ power)
    # Handle zero distances (exact matches)
    weights = 1.0 / (distances.pow(power) + epsilon)  # [Q, K]
    
    # Check for exact matches (distance very small)
    exact_match_mask = distances[:, 0] < epsilon  # [Q]
    
    # Normalize weights
    weights_sum = weights.sum(dim=-1, keepdim=True)  # [Q, 1]
    weights_normalized = weights / (weights_sum + epsilon)  # [Q, K]
    
    # Weighted sum: [Q, K, 1] * [Q, K, C] -> [Q, K, C] -> sum -> [Q, C]
    interpolated = (weights_normalized.unsqueeze(-1) * neighbor_values).sum(dim=1)
    
    # For exact matches, use the nearest neighbor value directly
    if exact_match_mask.any():
        interpolated[exact_match_mask] = neighbor_values[exact_match_mask, 0, :]
    
    return interpolated


def interpolate_at_sensors(
    sensor_positions: Dict[str, Dict],
    source_pos: torch.Tensor,
    source_values: torch.Tensor,
    k: int = 4,
    power: float = 2.0,
    method: Literal["idw", "rbf"] = "rbf",
    shape_param: float = 0.23,
    field_names: List[str] = None
) -> Dict[str, Dict]:
    """
    Interpolate field values at sensor positions.
    
    Args:
        sensor_positions: Dict of sensor configs, e.g.:
            {"Sensor_1": {"x": 7.4, "y": 3.0, "z": 2.6, "aisle": "cold"}, ...}
        source_pos: [N, 3] Source point coordinates (cpd_centroid)
        source_values: [N, C] Source point values (e.g., [u, v, w, p, T])
        k: Number of nearest neighbors for interpolation
        power: IDW power parameter (only used when method="idw")
        method: Interpolation method ("idw" or "rbf")
            - "idw": Inverse Distance Weighting, simple and fast
            - "rbf": Radial Basis Function, more accurate
        shape_param: RBF shape parameter (only used when method="rbf")
        field_names: List of field names (default: ["u", "v", "w", "p", "T"])
        
    Returns:
        Dict of interpolated values per sensor:
            {"Sensor_1": {"u": 0.1, "v": 0.2, ...}, ...}
    """
    if field_names is None:
        field_names = ["u", "v", "w", "p", "T"]
    
    device = source_pos.device
    dtype = source_pos.dtype
    
    # Build query positions tensor
    sensor_names = list(sensor_positions.keys())
    query_coords = []
    for name in sensor_names:
        pos = sensor_positions[name]
        query_coords.append([pos["x"], pos["y"], pos["z"]])
    
    query_pos = torch.tensor(query_coords, dtype=dtype, device=device)  # [Q, 3]
    
    # Interpolate using selected method
    if method == "rbf":
        interpolated = rbf_interpolate(
            query_pos=query_pos,
            source_pos=source_pos,
            source_values=source_values,
            k=k,
            shape_param=shape_param
        )
    else:  # idw
        interpolated = idw_interpolate(
            query_pos=query_pos,
            source_pos=source_pos,
            source_values=source_values,
            k=k,
            power=power
        )
    
    # Build result dict
    result = {}
    for i, name in enumerate(sensor_names):
        sensor_result = {}
        for j, field in enumerate(field_names):
            sensor_result[field] = interpolated[i, j].item()
        result[name] = sensor_result
    
    return result


def find_nearest_cell_indices(
    sensor_positions: Dict[str, Dict],
    cpd_centroid: torch.Tensor,
    return_distances: bool = False
) -> Tuple[torch.Tensor, List[str], Optional[torch.Tensor]]:
    """
    Find the nearest cpd_cell index for each sensor position.
    
    This is used to determine pivot nodes for k-hop subgraph construction
    in sensor-only inference mode.
    
    Args:
        sensor_positions: Dict of sensor configs
        cpd_centroid: [N, 3] CPD cell centroid coordinates
        return_distances: Whether to return distances
        
    Returns:
        indices: [Q] Indices of nearest cpd_cell for each sensor
        sensor_names: List of sensor names (in same order as indices)
        distances: [Q] Distances to nearest cell (if return_distances=True)
    """
    device = cpd_centroid.device
    dtype = cpd_centroid.dtype
    
    sensor_names = list(sensor_positions.keys())
    query_coords = []
    for name in sensor_names:
        pos = sensor_positions[name]
        query_coords.append([pos["x"], pos["y"], pos["z"]])
    
    query_pos = torch.tensor(query_coords, dtype=dtype, device=device)  # [Q, 3]
    
    # Find nearest neighbor (k=1)
    indices, distances = find_knn_indices(query_pos, cpd_centroid, k=1)
    indices = indices.squeeze(-1)  # [Q]
    distances = distances.squeeze(-1)  # [Q]
    
    if return_distances:
        return indices, sensor_names, distances
    else:
        return indices, sensor_names, None


def compute_sensor_metrics(
    predictions: Dict[str, Dict],
    ground_truth: Dict[str, Dict],
    field_names: List[str] = None
) -> Dict[str, Dict]:
    """
    Compute per-sensor and per-field metrics (MAE, relative error).
    
    Args:
        predictions: Dict of predicted values per sensor
        ground_truth: Dict of ground truth values per sensor
        field_names: List of field names
        
    Returns:
        Dict of metrics per sensor
    """
    if field_names is None:
        field_names = ["u", "v", "w", "p", "T"]
    
    metrics = {}
    
    for sensor_name in predictions.keys():
        pred = predictions[sensor_name]
        gt = ground_truth.get(sensor_name, {})
        
        sensor_metrics = {"mae": {}, "relative_error": {}}
        
        for field in field_names:
            if field in pred and field in gt:
                p = pred[field]
                g = gt[field]
                mae = abs(p - g)
                rel_err = mae / (abs(g) + 1e-8)
                sensor_metrics["mae"][field] = mae
                sensor_metrics["relative_error"][field] = rel_err
        
        metrics[sensor_name] = sensor_metrics
    
    return metrics
