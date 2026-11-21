"""Back-compat shim — model renamed to TextCondTrans_v2 (2026-06-10).

The architecture has no FVGN/GNN component (pure Transolver trunk + SCA), so "FVGN" was dropped
from the name. Kept so already-running torchrun jobs can respawn workers and old configs/imports
keep resolving. New code must import TextCondTrans_v2.
"""
from .TextCondTrans_v2 import TextCondTrans_v2 as TextCondTransFVGN_v2

__all__ = ["TextCondTransFVGN_v2"]
