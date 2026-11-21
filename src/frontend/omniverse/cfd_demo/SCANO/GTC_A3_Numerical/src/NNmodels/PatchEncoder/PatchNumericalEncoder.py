"""
PatchNumericalEncoder: per-patch (u, v, w, p, T) numerical values -> patch embedding.

Used by TextCondTransFVGN_v1 when `patch_only_numerical=True`, replacing the
LLM-based text encoder. Keeps the downstream cross-attention path identical
to C1/C2, so A2 (numerical BC) vs C1/C2 (text BC) is a clean ablation.
"""

from __future__ import annotations

import torch
import torch.nn as nn


class PatchNumericalEncoder(nn.Module):
    """Encode per-patch (u, v, w, p, T) → hidden_size."""

    def __init__(self, hidden_size: int, numerical_dim: int = 5, dropout: float = 0.0):
        super().__init__()
        self.hidden_size = int(hidden_size)
        self.numerical_dim = int(numerical_dim)
        self.mlp = nn.Sequential(
            nn.Linear(numerical_dim, hidden_size),
            nn.GELU(),
            nn.Dropout(dropout) if dropout > 0 else nn.Identity(),
            nn.Linear(hidden_size, hidden_size),
        )
        self.norm = nn.LayerNorm(hidden_size)

    def forward(self, patch_numerical: torch.Tensor) -> torch.Tensor:
        """
        Args:
            patch_numerical: [N_patches, numerical_dim] — per-patch mean of (u, v, w, p, T)
        Returns:
            [N_patches, hidden_size]
        """
        if patch_numerical.dim() != 2 or patch_numerical.size(-1) != self.numerical_dim:
            raise ValueError(
                f"PatchNumericalEncoder expects [N, {self.numerical_dim}], got {tuple(patch_numerical.shape)}"
            )
        return self.norm(self.mlp(patch_numerical))
