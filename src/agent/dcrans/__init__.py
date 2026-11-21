"""Lazy front-end for coupled JFNK/JFGNK dcRANS forward, inverse, and patch-audit services."""
from __future__ import annotations

__all__ = ["solve_dcrans", "invert_dcrans", "verify_dcrans_patches"]


def __getattr__(name: str):
    if name in __all__:
        from agent.dcrans import service
        return getattr(service, name)
    raise AttributeError(f"module 'agent.dcrans' has no attribute {name!r}")
