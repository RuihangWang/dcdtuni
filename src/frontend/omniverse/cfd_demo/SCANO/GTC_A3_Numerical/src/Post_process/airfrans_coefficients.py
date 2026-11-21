"""AirfRANS evaluation metrics: volume/surface rel-L2 split, official C_L/C_D via the
`airfrans` library, and Spearman rank correlation of predicted vs reference coefficients.

The model predicts 4 fields per node in order [u, v, p, nut], with `nut` stored under an
asinh transform (matching the dataset/normalizer). Coefficient computation inverts that
transform (sinh) before handing fields to `airfrans.simulation.Simulation`.

Official force coefficients follow the benchmark's own definition:
    sim = Simulation(root, name)
    sim.velocity = pred[:, 0:2]; sim.pressure = pred[:, 2:3]; sim.nu_t = sinh(pred[:, 3:4])
    (cd_pred, ..), (cl_pred, ..) = sim.force_coefficient(reference=False)
    (cd_ref,  ..), (cl_ref,  ..) = sim.force_coefficient(reference=True)
Node rows of `pred` must be aligned to `Simulation.position` (the .vtu internal-mesh order),
which is the same row order the parser preserves from `airfrans.dataset.load`.
"""
from typing import Dict, Sequence

import numpy as np


def invert_nut_transform(nut_asinh):
    """Invert the asinh transform applied to nut at the dataset layer: nut = sinh(x)."""
    return np.sinh(np.asarray(nut_asinh, dtype=np.float64))


def _rel_l2(pred: np.ndarray, target: np.ndarray) -> float:
    """Relative L2 over a flattened (nodes x fields) block: ||pred-tgt|| / ||tgt||."""
    num = np.linalg.norm(pred.reshape(-1) - target.reshape(-1))
    den = np.linalg.norm(target.reshape(-1))
    return float(num / (den + 1e-12))


def vol_surf_relL2(pred: np.ndarray, target: np.ndarray, surf_mask: np.ndarray) -> Dict[str, float]:
    """Relative-L2 error split into volume (off-surface) and surface (airfoil) nodes.

    Args:
        pred:   [N, C] physical-space predictions (nut already inverse-transformed or not —
                the split is transform-agnostic since pred and target share the same space).
        target: [N, C] physical-space ground truth.
        surf_mask: [N] boolean; True on airfoil-surface nodes.
    Returns:
        {"volume_relL2", "surface_relL2", "overall_relL2"}.
    """
    pred = np.asarray(pred, dtype=np.float64)
    target = np.asarray(target, dtype=np.float64)
    surf_mask = np.asarray(surf_mask, dtype=bool)
    vol = ~surf_mask
    out = {"overall_relL2": _rel_l2(pred, target)}
    out["volume_relL2"] = _rel_l2(pred[vol], target[vol]) if vol.any() else 0.0
    out["surface_relL2"] = _rel_l2(pred[surf_mask], target[surf_mask]) if surf_mask.any() else 0.0
    return out


def spearman_coeffs(
    cd_pred: Sequence[float],
    cd_ref: Sequence[float],
    cl_pred: Sequence[float],
    cl_ref: Sequence[float],
) -> Dict[str, float]:
    """Spearman rank correlation of predicted vs reference C_D and C_L across the test set."""
    from scipy.stats import spearmanr

    rho_cd = float(spearmanr(cd_pred, cd_ref).correlation)
    rho_cl = float(spearmanr(cl_pred, cl_ref).correlation)
    return {"rho_cd": rho_cd, "rho_cl": rho_cl}


def compute_case_coefficients(
    name: str,
    root: str,
    pred_uvpnut: np.ndarray,
    pos: np.ndarray = None,
    nut_is_asinh: bool = True,
    pos_atol: float = 1e-4,
) -> Dict[str, float]:
    """Official drag/lift coefficients for one case, predicted vs reference.

    Args:
        name: AirfRANS case name (e.g. "airFoil2D_SST_..._<Uinf>_<aoa>_...").
        root: AirfRANS dataset root (the dir holding `Dataset/`).
        pred_uvpnut: [N, 4] predictions in order [u, v, p, nut]; nut under asinh if
            `nut_is_asinh` (inverted before use).
        pos: optional [N, 2] node positions, asserted row-aligned to Simulation.position.
        nut_is_asinh: whether the nut column is asinh-transformed (default True).
    Returns:
        {cd_pred, cl_pred, cd_ref, cl_ref, cd_relerr, cl_relerr}.
    """
    from airfrans.simulation import Simulation

    pred = np.asarray(pred_uvpnut, dtype=np.float64)
    if pred.ndim != 2 or pred.shape[1] != 4:
        raise ValueError(f"pred_uvpnut must be [N,4] [u,v,p,nut], got {pred.shape}")

    sim = Simulation(root=root, name=name)
    if pred.shape[0] != sim.position.shape[0]:
        raise ValueError(
            f"prediction has {pred.shape[0]} nodes but Simulation '{name}' has "
            f"{sim.position.shape[0]} — row alignment to the .vtu mesh is required."
        )
    if pos is not None:
        pos = np.asarray(pos, dtype=np.float64)
        max_dpos = float(np.abs(pos[:, :2] - sim.position[:, :2]).max())
        if max_dpos > pos_atol:
            raise ValueError(
                f"node positions differ from Simulation order by up to {max_dpos:.3e} "
                f"(> {pos_atol}); prediction rows are not aligned to the .vtu mesh."
            )

    # Reference (ground-truth) coefficients from the loaded .vtu fields.
    (cd_ref, _, _), (cl_ref, _, _) = sim.force_coefficient(reference=True)

    # Inject predictions and recompute.
    nut = invert_nut_transform(pred[:, 3:4]) if nut_is_asinh else pred[:, 3:4]
    sim.velocity = pred[:, 0:2].astype(np.float64)
    sim.pressure = pred[:, 2:3].astype(np.float64)
    sim.nu_t = nut.astype(np.float64)
    (cd_pred, _, _), (cl_pred, _, _) = sim.force_coefficient(reference=False)

    cd_ref, cl_ref = float(cd_ref), float(cl_ref)
    cd_pred, cl_pred = float(cd_pred), float(cl_pred)
    return {
        "cd_pred": cd_pred,
        "cl_pred": cl_pred,
        "cd_ref": cd_ref,
        "cl_ref": cl_ref,
        "cd_relerr": abs(cd_pred - cd_ref) / (abs(cd_ref) + 1e-12),
        "cl_relerr": abs(cl_pred - cl_ref) / (abs(cl_ref) + 1e-12),
    }
