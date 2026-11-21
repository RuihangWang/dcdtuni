import math
from typing import Any, Dict, Optional

import torch


def summarize_tensor(tensor: Optional[torch.Tensor]) -> Optional[Dict[str, Any]]:
    if tensor is None:
        return None

    if not isinstance(tensor, torch.Tensor):
        return {"type": str(type(tensor))}

    data = tensor.detach()
    stats: Dict[str, Any] = {
        "shape": tuple(data.shape),
        "dtype": str(data.dtype),
        "device": str(data.device),
        "numel": int(data.numel()),
    }

    if data.numel() == 0:
        stats.update(
            {
                "finite_ratio": 1.0,
                "nan_count": 0,
                "posinf_count": 0,
                "neginf_count": 0,
            }
        )
        return stats

    if not torch.is_floating_point(data):
        data = data.to(torch.float32)
    else:
        data = data.float()

    finite_mask = torch.isfinite(data)
    finite_ratio = float(finite_mask.float().mean().item())
    stats["finite_ratio"] = finite_ratio
    stats["nan_count"] = int(torch.isnan(data).sum().item())
    stats["posinf_count"] = int(torch.isposinf(data).sum().item())
    stats["neginf_count"] = int(torch.isneginf(data).sum().item())

    if finite_mask.any():
        finite = data[finite_mask]
        stats.update(
            {
                "min": float(finite.min().item()),
                "max": float(finite.max().item()),
                "mean": float(finite.mean().item()),
                "std": float(finite.std(unbiased=False).item()),
                "abs_max": float(finite.abs().max().item()),
                "l2": float(torch.linalg.vector_norm(finite).item()),
            }
        )

    return stats


def summarize_parameter(param: Optional[torch.Tensor]) -> Optional[Dict[str, Any]]:
    return summarize_tensor(param)


def orthogonality_error(weight: Optional[torch.Tensor]) -> Optional[float]:
    if weight is None or not isinstance(weight, torch.Tensor) or weight.ndim != 2:
        return None

    w = weight.detach().float()
    rows, cols = w.shape
    if rows >= cols:
        gram = w.transpose(0, 1) @ w
        eye = torch.eye(cols, device=gram.device, dtype=gram.dtype)
    else:
        gram = w @ w.transpose(0, 1)
        eye = torch.eye(rows, device=gram.device, dtype=gram.dtype)

    diag_mean = torch.diagonal(gram).mean()
    if torch.isfinite(diag_mean) and torch.abs(diag_mean) > 0:
        gram = gram / diag_mean

    diff = gram - eye
    return float(torch.linalg.matrix_norm(diff).item() / math.sqrt(diff.numel()))
