"""Abstract base class for component batch residual handlers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import torch.nn as nn
from torch import Tensor


class BaseHandler(nn.Module, ABC):
    """
    Base class for per-type batch residual handlers.

    Each handler processes ALL instances of one component type simultaneously.
    Subclasses implement compute_residuals() returning a dict of named residual
    tensors. Keys can include 'momentum', 'energy', 'ic'.
    """

    @abstractmethod
    def compute_residuals(
        self,
        state: dict[str, Tensor],
        indices: Any,
        dt: float,
        bdf_order: int,
    ) -> dict[str, Tensor]:
        """
        Compute batch residuals for all instances of this component type.

        Args:
            state: dict with keys 'P', 'm', 'T_w', 'T_s' -- the global state tensors.
            indices: type-specific index dataclass for gather/scatter.
            dt: time step size.
            bdf_order: BDF order for ODE residuals.

        Returns:
            Dict of named residual tensors. Each value shape: [N_instances, N_t]
            or [N_instances]. Not all keys are required.
        """
