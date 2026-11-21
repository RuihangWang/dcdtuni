"""
Utilities for cyclic boundary handling.

This module centralizes cyclic-patch indexing, face-value reconstruction, and
geometry transform inference so that BCbase remains focused on abstract BC
interfaces only.
"""

import logging
import numpy as np
import torch

from FVdomain.SetBC.onthefly.tensor_layout import field_get, field_set_, gradient_get, gradient_set_


logger = logging.getLogger(__name__)


def _patch_scalar_to_int(value) -> int:
    if torch.is_tensor(value):
        return int(value.view(-1)[0].item())
    return int(value)


def get_cyclic_patch_pair_indices(fvpatch_like, neighbor_cpd_cell: torch.Tensor, patch_idx: int):
    """
    Return owner/boundary indices for one cyclic patch and its paired patch.

    Works for both single-case `fvPatch` (`neighbour_patch_idx`) and batched
    `fvPatchBatch` (`neighbor_patch_idx`).
    """
    source_patch_idx = _patch_scalar_to_int(patch_idx)

    if hasattr(fvpatch_like, "neighbor_patch_idx"):
        neighbor_patch_idx = fvpatch_like.neighbor_patch_idx
    elif hasattr(fvpatch_like, "neighbour_patch_idx"):
        neighbor_patch_idx = fvpatch_like.neighbour_patch_idx
    else:
        raise AttributeError("fvpatch_like has no cyclic neighbour patch index attribute")

    target_patch_idx = _patch_scalar_to_int(neighbor_patch_idx[source_patch_idx])
    if target_patch_idx < 0:
        raise ValueError(f"Patch {source_patch_idx} is not paired with a cyclic neighbour")

    source_start = _patch_scalar_to_int(fvpatch_like.start_idx_face[source_patch_idx])
    source_end = _patch_scalar_to_int(fvpatch_like.end_idx_face[source_patch_idx])
    target_start = _patch_scalar_to_int(fvpatch_like.start_idx_face[target_patch_idx])
    target_end = _patch_scalar_to_int(fvpatch_like.end_idx_face[target_patch_idx])

    pair_index = neighbor_cpd_cell.long()
    source_owner, source_boundary = pair_index[:, source_start:source_end]
    target_owner, target_boundary = pair_index[:, target_start:target_end]

    if source_owner.numel() != target_owner.numel():
        raise ValueError(
            f"Cyclic patch face count mismatch: patch {source_patch_idx} has {source_owner.numel()} faces, "
            f"paired patch {target_patch_idx} has {target_owner.numel()} faces"
        )

    return source_owner, source_boundary, target_owner, target_boundary


def cyclic_paired_owner_average(
    field: torch.Tensor,
    source_owner: torch.Tensor,
    target_owner: torch.Tensor,
    *,
    gradient: bool = False,
):
    """Copy paired patch's owner values to this patch's ghost cells."""
    getter = gradient_get if gradient else field_get
    return getter(field, target_owner)


def apply_cyclic_face_values(
    field: torch.Tensor,
    fvpatch_like,
    neighbor_cpd_cell: torch.Tensor,
    patch_idx: int,
    *,
    gradient: bool = False,
):
    """
    Update cyclic boundary-face CPD values from paired owner values.

    The cyclic boundary CPD cell remains the face-value carrier. Its value is
    copied from the owner cells of the paired cyclic patch, rather than being
    treated as a mirrored ghost cell.
    """
    if field is None:
        return None

    source_owner, source_boundary, target_owner, _ = get_cyclic_patch_pair_indices(
        fvpatch_like,
        neighbor_cpd_cell,
        patch_idx,
    )
    setter = gradient_set_ if gradient else field_set_
    setter(field, source_boundary, cyclic_paired_owner_average(
        field,
        source_owner,
        target_owner,
        gradient=gradient,
    ))
    return field


def rotation_matrix_to_axis_angle(rotation_matrix: np.ndarray):
    trace_val = np.trace(rotation_matrix)
    cos_theta = np.clip((trace_val - 1.0) * 0.5, -1.0, 1.0)
    angle_rad = float(np.arccos(cos_theta))

    if angle_rad < 1e-10:
        return np.zeros(3, dtype=np.float64), angle_rad

    axis = np.array([
        rotation_matrix[2, 1] - rotation_matrix[1, 2],
        rotation_matrix[0, 2] - rotation_matrix[2, 0],
        rotation_matrix[1, 0] - rotation_matrix[0, 1],
    ], dtype=np.float64)
    axis_norm = np.linalg.norm(axis)

    if axis_norm < 1e-10:
        eigvals, eigvecs = np.linalg.eig(rotation_matrix)
        axis = np.real(eigvecs[:, np.argmin(np.abs(eigvals - 1.0))])
        axis_norm = np.linalg.norm(axis)

    if axis_norm < 1e-10:
        return np.zeros(3, dtype=np.float64), angle_rad

    return axis / axis_norm, angle_rad


def vector_direction_angles_deg(vector: np.ndarray):
    magnitude = float(np.linalg.norm(vector))
    if magnitude < 1e-12:
        return {"x": 0.0, "y": 0.0, "z": 0.0}

    direction = vector / magnitude
    return {
        axis: float(np.degrees(np.arccos(np.clip(component, -1.0, 1.0))))
        for axis, component in zip(("x", "y", "z"), direction)
    }


def infer_cyclic_transform(
    source_face_pos: torch.Tensor,
    target_face_pos: torch.Tensor,
    source_face_normal: torch.Tensor = None,
    target_face_normal: torch.Tensor = None,
):
    source_np = source_face_pos.detach().cpu().numpy().astype(np.float64)
    target_np = target_face_pos.detach().cpu().numpy().astype(np.float64)

    source_centroid = source_np.mean(axis=0)
    target_centroid = target_np.mean(axis=0)

    source_centered = source_np - source_centroid
    target_centered = target_np - target_centroid

    covariance = source_centered.T @ target_centered
    u_mat, _, vt_mat = np.linalg.svd(covariance)
    rotation_matrix = vt_mat.T @ u_mat.T
    if np.linalg.det(rotation_matrix) < 0.0:
        vt_mat[-1, :] *= -1.0
        rotation_matrix = vt_mat.T @ u_mat.T

    rigid_translation = target_centroid - rotation_matrix @ source_centroid
    mapped_source = source_np @ rotation_matrix.T + rigid_translation
    fit_error = np.linalg.norm(mapped_source - target_np, axis=1)

    translation_samples = target_np - source_np
    translation_vector = translation_samples.mean(axis=0)
    translation_spread = np.linalg.norm(translation_samples - translation_vector, axis=1)

    rotation_axis, rotation_angle_rad = rotation_matrix_to_axis_angle(rotation_matrix)
    rotation_angle_deg = float(np.degrees(rotation_angle_rad))

    if rotation_angle_rad < 1e-10:
        rotation_center = source_centroid
    else:
        rotation_center, *_ = np.linalg.lstsq(
            np.eye(3, dtype=np.float64) - rotation_matrix,
            rigid_translation,
            rcond=None,
        )

    patch_normal_angle_deg = 0.0
    if source_face_normal is not None and target_face_normal is not None:
        source_normal_np = source_face_normal.detach().cpu().numpy().astype(np.float64)
        target_normal_np = target_face_normal.detach().cpu().numpy().astype(np.float64)
        source_mean_normal = source_normal_np.mean(axis=0)
        target_mean_normal = target_normal_np.mean(axis=0)
        source_norm = np.linalg.norm(source_mean_normal)
        target_norm = np.linalg.norm(target_mean_normal)
        if source_norm > 1e-12 and target_norm > 1e-12:
            source_mean_normal /= source_norm
            target_mean_normal /= target_norm
            patch_normal_angle_deg = float(
                np.degrees(
                    np.arccos(
                        np.clip(np.dot(source_mean_normal, -target_mean_normal), -1.0, 1.0)
                    )
                )
            )

    transform_type = "translational" if rotation_angle_deg < 1e-3 else "rotational"

    return {
        "transform_type": transform_type,
        "translation_vector": translation_vector.tolist(),
        "translation_distance": float(np.linalg.norm(translation_vector)),
        "translation_direction_angles_deg": vector_direction_angles_deg(translation_vector),
        "translation_fit_rms": float(np.sqrt(np.mean(translation_spread**2))) if translation_spread.size > 0 else 0.0,
        "translation_fit_max": float(translation_spread.max()) if translation_spread.size > 0 else 0.0,
        "rotation_matrix": rotation_matrix.tolist(),
        "rotation_axis": rotation_axis.tolist(),
        "rotation_angle_rad": rotation_angle_rad,
        "rotation_angle_deg": rotation_angle_deg,
        "rotation_center": rotation_center.tolist(),
        "rigid_translation": rigid_translation.tolist(),
        "rigid_fit_rms": float(np.sqrt(np.mean(fit_error**2))) if fit_error.size > 0 else 0.0,
        "rigid_fit_max": float(fit_error.max()) if fit_error.size > 0 else 0.0,
        "patch_normal_angle_deg": patch_normal_angle_deg,
    }


def prepare_cyclic_boundary_geometry(mesh):
    """
    Extrapolate cyclic ghost cell positions to mirror the owner cell about
    the face center (ghost = 2·face_center − owner), and infer transform
    metadata from paired cyclic face geometry.
    """
    patch_dict = mesh.get("patch_dict", {})
    if not patch_dict:
        raise ValueError("patch_dict not found in mesh")

    cpd_cell_pos = mesh["cpd|cell_pos"].clone()
    neighbor_cpd_cell = mesh["cpd|neighbor_cell"].long()
    face_pos = mesh["face|face_pos"]
    face_normal = mesh.get("face|face_normal", None)

    processed_pairs = set()
    total_cyclic_faces = 0
    cyclic_patch_transforms = {}

    for patch_name, patch_info in patch_dict.items():
        patch_type = str(patch_info.get("type", "")).lower()
        geom_type = str(patch_info.get("geom_type", "")).lower()

        is_cyclic = ("cyclic" in patch_type) or ("cyclic" in geom_type)
        if not is_cyclic:
            continue

        neighbour_patch_name = patch_info.get("neighbour_patch", None)
        if neighbour_patch_name is None:
            logger.warning(f"Cyclic patch '{patch_name}' has no neighbour_patch defined, skipping")
            continue

        pair_key = tuple(sorted([patch_name, neighbour_patch_name]))
        if pair_key in processed_pairs:
            continue
        processed_pairs.add(pair_key)

        neighbour_patch_info = patch_dict.get(neighbour_patch_name, None)
        if neighbour_patch_info is None:
            logger.warning(f"Neighbour patch '{neighbour_patch_name}' not found for cyclic patch '{patch_name}'")
            continue

        patch1_start = int(patch_info["start_idx_face"])
        patch1_end = int(patch_info["end_idx_face"])
        patch2_start = int(neighbour_patch_info["start_idx_face"])
        patch2_end = int(neighbour_patch_info["end_idx_face"])

        if (patch1_end - patch1_start) != (patch2_end - patch2_start):
            raise ValueError(
                f"Cyclic patch pair '{patch_name}'-'{neighbour_patch_name}' has mismatched face counts: "
                f"{patch1_end - patch1_start} vs {patch2_end - patch2_start}"
            )

        patch1_boundary_cells = neighbor_cpd_cell[1, patch1_start:patch1_end]
        patch2_boundary_cells = neighbor_cpd_cell[1, patch2_start:patch2_end]
        patch1_owner_cells = neighbor_cpd_cell[0, patch1_start:patch1_end]
        patch2_owner_cells = neighbor_cpd_cell[0, patch2_start:patch2_end]

        patch1_face_pos = face_pos[patch1_start:patch1_end]
        patch2_face_pos = face_pos[patch2_start:patch2_end]
        cpd_cell_pos[patch1_boundary_cells] = 2 * patch1_face_pos - cpd_cell_pos[patch1_owner_cells]
        cpd_cell_pos[patch2_boundary_cells] = 2 * patch2_face_pos - cpd_cell_pos[patch2_owner_cells]

        patch1_face_normal = face_normal[patch1_start:patch1_end] if face_normal is not None else None
        patch2_face_normal = face_normal[patch2_start:patch2_end] if face_normal is not None else None

        forward_transform = infer_cyclic_transform(
            patch1_face_pos,
            patch2_face_pos,
            patch1_face_normal,
            patch2_face_normal,
        )
        reverse_transform = infer_cyclic_transform(
            patch2_face_pos,
            patch1_face_pos,
            patch2_face_normal,
            patch1_face_normal,
        )

        forward_transform["paired_patch_name"] = neighbour_patch_name
        reverse_transform["paired_patch_name"] = patch_name

        patch_info["cyclic_transform"] = forward_transform
        neighbour_patch_info["cyclic_transform"] = reverse_transform
        cyclic_patch_transforms[patch_name] = forward_transform
        cyclic_patch_transforms[neighbour_patch_name] = reverse_transform

        total_cyclic_faces += patch1_end - patch1_start
        logger.debug(
            f"Processed cyclic pair '{patch_name}'-'{neighbour_patch_name}': "
            f"{patch1_end - patch1_start} faces, translation={forward_transform['translation_vector']}, "
            f"rotation_deg={forward_transform['rotation_angle_deg']:.6f}"
        )

    if total_cyclic_faces > 0:
        mesh["cpd|cell_pos"] = cpd_cell_pos
        mesh["patch_dict"] = patch_dict
        mesh["cyclic_patch_transforms"] = cyclic_patch_transforms
        logger.info(f"Prepared extrapolated cyclic ghost geometry for {total_cyclic_faces} face pairs")
    else:
        logger.debug("No cyclic boundaries found in patch_dict")

    return mesh
