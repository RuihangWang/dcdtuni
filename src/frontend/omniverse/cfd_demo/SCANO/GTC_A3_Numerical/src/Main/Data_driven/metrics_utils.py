"""Pure, side-effect-free metric helpers for inference evaluation.

Kept separate from inferencer.py so they can be unit-tested without building a
model, dataset, or distributed context.
"""
from typing import Dict, Optional, Sequence

import torch

FIELD_NAMES = ("u", "v", "w", "p", "T")


def nrmse_per_field(
    prediction: torch.Tensor,
    target: torch.Tensor,
    field_std: torch.Tensor,
    eps: float = 1e-8,
    field_names: Optional[Sequence[str]] = None,
) -> Dict[str, float]:
    """Normalized RMSE per channel: sqrt(mean((pred-gt)^2)) / std_c.

    Args:
        prediction: [N, C] physical-space predictions.
        target:     [N, C] physical-space ground truth.
        field_std:  [C] (or broadcastable) per-channel std — the dataset-wide std
            used to z-score the training target. nRMSE is then the RMS of the
            z-scored error.
        eps: denominator guard.

    Returns:
        {"nrmse_u": .., "nrmse_v": .., ..., "nrmse_overall": mean over channels}.
        nrmse_overall is the simple arithmetic mean over channels (channels are NOT reweighted).
    """
    assert prediction.shape == target.shape, (prediction.shape, target.shape)
    C = prediction.shape[1]
    fstd_full = field_std.to(prediction.device).reshape(-1)
    assert fstd_full.numel() >= C, (
        f"field_std has {fstd_full.numel()} elements but prediction has {C} channels"
    )
    fstd = fstd_full[:C]
    names = tuple(field_names) if field_names is not None else FIELD_NAMES
    out: Dict[str, float] = {}
    vals = []
    for i in range(C):
        rmse = torch.sqrt(torch.mean((prediction[:, i] - target[:, i]) ** 2))
        nrmse = (rmse / (fstd[i] + eps)).item()
        name = names[i] if i < len(names) else str(i)
        out[f"nrmse_{name}"] = nrmse
        vals.append(nrmse)
    out["nrmse_overall"] = float(sum(vals) / len(vals)) if vals else 0.0
    return out


def draw_spread_per_field(
    draw_spread: torch.Tensor,
    field_std: torch.Tensor,
    eps: float = 1e-8,
    field_names: Optional[Sequence[str]] = None,
) -> Dict[str, float]:
    """Normalised disagreement between pivot draws — the direct test of well-posedness.

    Inference predicts each node several times, once per pivot draw that happened to cover
    it, and averages the results. `draw_spread` is the per-channel standard deviation of
    those predictions, in physical units, over the nodes covered at least twice
    (runner.py::_finalize_predictions).

    Dividing by the SAME dataset-wide `field_std` that nRMSE divides by puts the two on one
    scale, so `dspread_u = 0.08` and `nrmse_u = 0.31` can be read together: a third of this
    model's error is not error at all, it is the model disagreeing with itself depending on
    which pivots were drawn.

    The thesis this measures. If the complete boundary condition determined the interior
    solution, then (subgraph, boundary) -> field would be a function, every draw would land
    on the same answer, and dspread would be 0. It is not 0 for a model that only ever sees
    the sampled fraction of the boundary, because many different complete boundaries are
    consistent with the fraction it drew. So dspread is not a proxy for the pathology — it
    IS the pathology, measured, and it needs no ground truth.

    The one way to cheat it: ignore the boundary entirely, and the answer stops depending on
    which boundary faces were drawn. A boundary-blind model scores a perfect 0. That is not
    a hypothetical — `DNE-SS-MGN` is boundary-blind by construction. So dspread MUST be read
    against a boundary-response measurement (see Main/Data_driven/bc_intervention.py); low
    dspread is only good news when the model demonstrably uses the boundary.

    Returns {"dspread_u": .., ..., "dspread_overall": mean over channels}.
    """
    C = draw_spread.numel()
    fstd = field_std.to(draw_spread.device).reshape(-1)[:C]
    names = tuple(field_names) if field_names is not None else FIELD_NAMES
    out: Dict[str, float] = {}
    vals = []
    for i in range(C):
        v = (draw_spread[i] / (fstd[i] + eps)).item()
        out[f"dspread_{names[i] if i < len(names) else i}"] = v
        vals.append(v)
    out["dspread_overall"] = float(sum(vals) / len(vals)) if vals else 0.0
    return out


def aggregate_case_metrics(case_results: Dict[str, dict]) -> Dict[str, float]:
    """Mean every numeric metric key across cases.

    Args:
        case_results: {case_name: {"metrics": {metric_key: value, ...}, ...}}.

    Returns:
        {metric_key: mean over all cases that reported it}.
    """
    sums: Dict[str, float] = {}
    counts: Dict[str, int] = {}
    for cr in case_results.values():
        for k, v in cr.get("metrics", {}).items():
            if isinstance(v, (int, float)):
                sums[k] = sums.get(k, 0.0) + float(v)
                counts[k] = counts.get(k, 0) + 1
    return {k: sums[k] / counts[k] for k in sums}
