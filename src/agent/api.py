"""The stable public surface of the agent layer (``AGENT_API_VERSION = "1.0"``, semantic versioning).

An external research repository (for example ISPO-AgentForDesign) depends on
these names only, pinned to a dcdtuni commit. Everything below reaches the
platform through ``agent.scripted_api``; nothing imports a frozen ``agent.<old>``
module, and importing this module changes no environment variable and loads
neither torch nor scipy (:func:`allocation_probabilities` imports torch on
first use, the CMA-ES/MFBO optimizers scipy).

=================  ==========================================================================
Protocols          :class:`Task`, :class:`Design`, :class:`Scenario`, :class:`Codec`,
                   :class:`Evaluator`; :func:`canonical_identity` for their ``identity()``
Evaluation         :class:`Fidelity`, :class:`Budget`, :class:`BudgetExhausted`,
                   :class:`EvaluationRecord`, :func:`evaluate`
Records and cost   :class:`RunStore`, :class:`RunLogger`, :func:`new_run_id`, :class:`CostLedger`
Acquisition        :class:`Acquisition` ("register, then buy"), :func:`allocation_probabilities`
Optimizers         :class:`Space`, :func:`optimize` (heuristic, CMA-ES, MFBO, adjoint descent)
Tools              :func:`edit_project`, :func:`query_project`, :func:`project_identity`,
                   :func:`validate_project`, :func:`admit_air`, :func:`mesh_air`, :func:`solve_air`,
                   :func:`evaluate_air`, :func:`evaluate_liquid`, :func:`liquid_gradient`,
                   :func:`evaluate_hybrid`, :func:`evaluate_dcrans_l0`, :func:`evaluate_dcrans_l1`,
                   :func:`solve_dcrans`, :func:`invert_dcrans`, :func:`verify_dcrans_patches`,
                   :func:`evaluate_many`, :func:`read_result`, :func:`identity`
Edits              :class:`Edit` and :class:`MoveModel`, :class:`RotateModel`, :class:`RemoveModels`,
                   :class:`SetServerLoad`, :class:`SetRackPower`, :class:`SetAcuSupply`,
                   :class:`AddProbe`, :class:`SetCduSupply`, :class:`SetPumpSpeed`,
                   :class:`SetValveOpening`, :class:`SetPipeDiameter`, :class:`SetPipeRoute`; :class:`ContractVerbMissing`
=================  ==========================================================================
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, List, Mapping, Protocol, Sequence, Tuple, runtime_checkable

from agent import AGENT_API_VERSION
from agent.optim.acquisition import Acquisition
from agent.optim.optimizers import Space, optimize
from agent.records import CostLedger, RunLogger, RunStore, new_run_id
from agent.tools.edits import (AddProbe, ContractVerbMissing, Edit, MoveModel, RemoveModels, RotateModel,
                               SetAcuSupply, SetCduSupply, SetPipeDiameter, SetPipeRoute, SetPumpSpeed, SetRackPower,
                               SetServerLoad, SetValveOpening)
from agent.tools.evaluate import (admit_air, evaluate_air, evaluate_dcrans_l0, evaluate_dcrans_l1, evaluate_hybrid,
                                  evaluate_liquid, evaluate_many, identity, liquid_gradient, mesh_air, solve_air)
from agent.tools.fidelity import Budget, BudgetExhausted, EvaluationRecord, Fidelity, evaluate
from agent.tools.project import edit_project, project_identity, query_project, validate_project
from agent.tools.results import read_result

__all__ = [
    "AGENT_API_VERSION",
    "Task", "Design", "Scenario", "Codec", "Evaluator", "canonical_identity",
    "Fidelity", "Budget", "BudgetExhausted", "EvaluationRecord", "evaluate",
    "CostLedger", "RunStore", "RunLogger", "new_run_id",
    "Acquisition", "allocation_probabilities", "Space", "optimize",
    "edit_project", "query_project", "project_identity", "validate_project",
    "admit_air", "mesh_air", "solve_air", "evaluate_air", "evaluate_liquid", "liquid_gradient", "evaluate_hybrid",
    "evaluate_dcrans_l0", "evaluate_dcrans_l1", "solve_dcrans", "invert_dcrans", "verify_dcrans_patches",
    "evaluate_many", "read_result", "identity",
    "Edit", "MoveModel", "RotateModel", "RemoveModels", "SetServerLoad", "SetRackPower", "SetAcuSupply", "AddProbe",
    "SetCduSupply", "SetPumpSpeed", "SetValveOpening", "SetPipeDiameter", "SetPipeRoute", "ContractVerbMissing",
]


def canonical_identity(value: Any) -> str:
    """SHA-256 of the sorted, compact, finite JSON of ``value`` (the same rule as ``agent.scripted_api``)."""
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@runtime_checkable
class Task(Protocol):
    """A fixed design task (room, equipment, loads, goal); ``identity()`` is a SHA-256 of its definition."""

    def identity(self) -> str: ...


@runtime_checkable
class Design(Protocol):
    """One candidate design for a task; ``identity()`` is a SHA-256 of its canonical form."""

    def identity(self) -> str: ...


@runtime_checkable
class Scenario(Protocol):
    """A task family: legality rules and the compiler from a design to automation-contract build steps.

    ``validate`` returns the list of violated rules (empty = legal);
    ``compile`` returns the ``{"verb", "args"}`` steps
    :func:`agent.scripted_api.build_project` runs (no hand-written ``.cfdproj`` JSON).
    """
    name: str
    version: str

    def identity(self) -> str: ...

    def validate(self, task: Task, design: Design) -> List[str]: ...

    def compile(self, task: Task, design: Design) -> List[dict]: ...


@runtime_checkable
class Codec(Protocol):
    """Grammar text <-> (discrete structure s, continuous values x), with its numeric domain and schema version."""
    schema_version: str

    def encode(self, discrete: Any, continuous: Sequence[float]) -> str: ...

    def decode(self, text: str) -> Tuple[Any, Sequence[float]]: ...

    def domain(self) -> Mapping[str, Any]: ...


@runtime_checkable
class Evaluator(Protocol):
    """``evaluator(candidate, fidelity, **options) -> EvaluationRecord``; :func:`evaluate` is the reference one."""

    def __call__(self, candidate: Any, fidelity: Any, **options: Any) -> EvaluationRecord: ...


def __getattr__(name: str):
    if name == "allocation_probabilities":
        from agent.optim.estimators import allocation_probabilities
        return allocation_probabilities
    if name in ("solve_dcrans", "invert_dcrans", "verify_dcrans_patches"):
        from agent.dcrans import service as dcrans_service
        return getattr(dcrans_service, name)
    raise AttributeError(f"module 'agent.api' has no attribute {name!r}")
