"""Code-verification utilities for the liquid-cooling solver.

Verification asks "are we solving the equations right?" — distinct from
validation, which asks "are we solving the right equations?".  The Method of
Manufactured Solutions (MMS) is the strongest available answer to the first
question, because it supplies an exact solution against which the observed
order of accuracy can be measured.
"""

from backend.liquidcooling.verification.mms import (
    MMSCase,
    ObservedOrder,
    build_mms_graph,
    observed_order,
    run_mms,
    steady_case,
    temporal_case,
    transient_case,
)

__all__ = [
    "MMSCase",
    "ObservedOrder",
    "build_mms_graph",
    "observed_order",
    "run_mms",
    "steady_case",
    "temporal_case",
    "transient_case",
]
