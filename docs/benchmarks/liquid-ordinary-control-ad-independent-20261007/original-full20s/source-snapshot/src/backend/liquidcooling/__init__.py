"""Liquid cooling network solver.

Two solver strategies:
- hybrid_solve: Newton GGA hydraulics + direct thermal march (production)
- solve: pure Adam ODIL optimisation (research/comparison)
"""

from backend.liquidcooling.graphcompiling.compiler import compile_graph
from backend.liquidcooling.schema import LiquidrunConfig
from backend.liquidcooling.graphcompiling.compiled_graph import CompiledGraph
from backend.liquidcooling.solver.model import LiquidODILModel
from backend.liquidcooling.solver.adam_odil import solve
from backend.liquidcooling.solver.hybrid import hybrid_solve

__all__ = [
    "compile_graph", "LiquidrunConfig", "CompiledGraph",
    "LiquidODILModel", "solve", "hybrid_solve",
]
