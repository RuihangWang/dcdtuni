"""Optimisation and HF-acquisition maths: estimators, ISPO controllers, controls, optimizers, the acquisition ledger.

Submodules are imported on first use, so ``import agent.optim`` needs neither
torch nor scipy: :mod:`~agent.optim.estimators` needs torch,
:mod:`~agent.optim.optimizers` needs scipy for CMA-ES and MFBO.
"""
from __future__ import annotations

import importlib

_SUBMODULES = ("estimators", "ispo_controller", "predictable_controls", "optimizers", "acquisition")
_NAMES = {
    "allocation_probabilities": "estimators", "ht_peer_baseline": "estimators", "aipw_updates": "estimators",
    "nested_control_variate": "estimators", "lax_continuous_update": "estimators", "rloo_advantages": "estimators",
    "score_updates": "estimators", "fit_control_coefficients": "estimators",
    "optimize": "optimizers", "Space": "optimizers", "Point": "optimizers", "METHODS": "optimizers",
    "Acquisition": "acquisition", "AcquisitionError": "acquisition",
}
__all__ = sorted(_NAMES) + list(_SUBMODULES)


def __getattr__(name):
    if name in _SUBMODULES:
        return importlib.import_module(f"agent.optim.{name}")
    if name in _NAMES:
        return getattr(importlib.import_module(f"agent.optim.{_NAMES[name]}"), name)
    raise AttributeError(f"module 'agent.optim' has no attribute {name!r}")
