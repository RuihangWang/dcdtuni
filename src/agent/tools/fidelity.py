"""Multi-fidelity dispatch: one ``evaluate(candidate, fidelity, ...)`` with a budget, a cost ledger and a run log.

=====================  ====================================================  ======================
Fidelity               tool                                                  default information
=====================  ====================================================  ======================
``ADMISSION``          :func:`~agent.tools.evaluate.admit_air`               ``("admission",)``
``LF``                 the caller's own ``lf(candidate, **options)``         declared by the caller
``LIQUID_STEADY``      :func:`~agent.tools.evaluate.evaluate_liquid` steady  ``("I0", "I1", "I3")``
``LIQUID_TRANSIENT``   :func:`~agent.tools.evaluate.evaluate_liquid`         ``("I0", "I1")``
``AIR_MESH``           :func:`~agent.tools.evaluate.mesh_air`                ``("mesh",)``
``AIR_FULL``           :func:`~agent.tools.evaluate.evaluate_air`            ``("I0", "I1")``
``HYBRID``             :func:`~agent.tools.evaluate.evaluate_hybrid`         ``("I0", "I1")``
=====================  ====================================================  ======================

Order of one call: the :class:`Budget` is checked *before* the tool runs
(an exhausted envelope raises :class:`BudgetExhausted` and buys nothing); the
tool runs inside a :class:`~agent.records.CostLedger` attempt; whatever
happens -- success, a failed run, an exception -- the attempt is charged to
the budget and, with a ``logger``, appended to its ``physics_calls.jsonl``
(evaluator = the result's own ``identity`` plus the tool and agent versions;
costs = the result's stage seconds plus the metered process costs). An
exception is re-raised after it is booked. :class:`EvaluationRecord` is what
the caller gets back.
"""
from __future__ import annotations

import enum
import functools
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Tuple, Union

from agent.records.costs import CostLedger
from agent.tools import evaluate as tools
from agent.tools.results import ResultView, view

PathLike = Union[str, os.PathLike]


class Fidelity(str, enum.Enum):
    ADMISSION = "admission"
    LF = "lf"
    LIQUID_STEADY = "liquid_steady"
    LIQUID_TRANSIENT = "liquid_transient"
    AIR_MESH = "air_mesh"
    AIR_FULL = "air_full"
    HYBRID = "hybrid"
    DCRANS_L0 = "dcrans_l0"
    DCRANS_L1 = "dcrans_l1"

    def __str__(self) -> str:
        return self.value


DISPATCH: Dict[Fidelity, Callable[..., Any]] = {
    Fidelity.ADMISSION: tools.admit_air,
    Fidelity.LIQUID_STEADY: tools.evaluate_liquid,
    Fidelity.LIQUID_TRANSIENT: functools.partial(tools.evaluate_liquid, mode="transient"),
    Fidelity.AIR_MESH: tools.mesh_air,
    Fidelity.AIR_FULL: tools.evaluate_air,
    Fidelity.HYBRID: tools.evaluate_hybrid,
    Fidelity.DCRANS_L0: tools.evaluate_dcrans_l0,
    Fidelity.DCRANS_L1: tools.evaluate_dcrans_l1,
}
INFORMATION: Dict[Fidelity, Tuple[str, ...]] = {
    Fidelity.ADMISSION: ("admission",), Fidelity.LF: (), Fidelity.LIQUID_STEADY: ("I0", "I1", "I3"),
    Fidelity.LIQUID_TRANSIENT: ("I0", "I1"), Fidelity.AIR_MESH: ("mesh",), Fidelity.AIR_FULL: ("I0", "I1"),
    Fidelity.HYBRID: ("I0", "I1"),
    Fidelity.DCRANS_L0: ("I0", "I1"),
    Fidelity.DCRANS_L1: ("I0", "I1"),
}


class BudgetExhausted(RuntimeError):
    """The registered envelope is spent; the next attempt is not bought."""


@dataclass
class Budget:
    """An evaluation envelope, checked before each attempt and charged after it (failures included).

    Limits left as ``None`` are not enforced. Wall and CPU are the metered
    process costs of the attempts (``process_wall_seconds`` and
    ``total_process_tree_cpu_seconds`` of the ledger delta).
    """
    max_calls: Optional[int] = None
    max_calls_by_fidelity: Mapping[Any, int] = field(default_factory=dict)
    max_wall_seconds: Optional[float] = None
    max_cpu_seconds: Optional[float] = None
    calls: int = 0
    calls_by_fidelity: Dict[str, int] = field(default_factory=dict)
    wall_seconds: float = 0.
    cpu_seconds: float = 0.

    def __post_init__(self):
        self.max_calls_by_fidelity = {str(Fidelity(k)): int(v) for k, v in dict(self.max_calls_by_fidelity).items()}

    def check(self, fidelity: Fidelity) -> None:
        name = str(Fidelity(fidelity))
        if self.max_calls is not None and self.calls >= self.max_calls:
            raise BudgetExhausted(f"call budget spent ({self.calls}/{self.max_calls})")
        limit = self.max_calls_by_fidelity.get(name)
        if limit is not None and self.calls_by_fidelity.get(name, 0) >= limit:
            raise BudgetExhausted(f"{name} call budget spent ({self.calls_by_fidelity.get(name, 0)}/{limit})")
        if self.max_wall_seconds is not None and self.wall_seconds >= self.max_wall_seconds:
            raise BudgetExhausted(f"wall budget spent ({self.wall_seconds:.3f}/{self.max_wall_seconds} s)")
        if self.max_cpu_seconds is not None and self.cpu_seconds >= self.max_cpu_seconds:
            raise BudgetExhausted(f"CPU budget spent ({self.cpu_seconds:.3f}/{self.max_cpu_seconds} s)")

    def charge(self, fidelity: Fidelity, costs: Mapping[str, Any]) -> None:
        name = str(Fidelity(fidelity))
        self.calls += 1
        self.calls_by_fidelity[name] = self.calls_by_fidelity.get(name, 0) + 1
        self.wall_seconds += float(costs.get("process_wall_seconds") or 0.)
        self.cpu_seconds += float(costs.get("total_process_tree_cpu_seconds") or 0.)

    def remaining(self) -> Dict[str, Any]:
        def left(limit, used):
            return None if limit is None else max(0., float(limit) - float(used))
        return {"calls": left(self.max_calls, self.calls),
                "calls_by_fidelity": {k: left(v, self.calls_by_fidelity.get(k, 0))
                                      for k, v in self.max_calls_by_fidelity.items()},
                "wall_seconds": left(self.max_wall_seconds, self.wall_seconds),
                "cpu_seconds": left(self.max_cpu_seconds, self.cpu_seconds)}


@dataclass(frozen=True)
class EvaluationRecord:
    """One evaluation: what was asked, by whom, at what cost, and the platform's own result."""
    fidelity: Fidelity
    status: str
    validity: str
    information: Tuple[str, ...]
    derivatives: Tuple[str, ...]
    costs: Dict[str, Any]
    evaluator: Dict[str, Any]
    design: Dict[str, Any]
    result_identity: Dict[str, Any]
    result: Any = field(repr=False, compare=False)

    def as_record(self) -> Dict[str, Any]:
        """JSON-able summary (the raw result itself stays in its ``result.json``)."""
        return {"fidelity": str(self.fidelity), "status": self.status, "validity": self.validity,
                "information": list(self.information), "derivatives": list(self.derivatives),
                "costs": self.costs, "evaluator": self.evaluator, "design": self.design,
                "result_identity": self.result_identity}


def _callable_name(tool: Callable[..., Any]) -> str:
    target = tool.func if isinstance(tool, functools.partial) else tool
    return f"{getattr(target, '__module__', '?')}.{getattr(target, '__qualname__', repr(target))}"


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _derivatives(fidelity: Fidelity, result: Mapping[str, Any]) -> Tuple[str, ...]:
    if fidelity is Fidelity.LIQUID_STEADY:
        inputs = result.get("inputs") or {}
        return tuple(inputs.get("gradientParameters") or ())
    if fidelity is Fidelity.LF:
        return tuple(result.get("derivatives") or ())
    return ()


def _validity(result: Mapping[str, Any]) -> str:
    status = result.get("status")
    if status == "succeeded":
        return "valid" if result.get("admitted", True) else "blocked"
    stage = (result.get("failure") or {}).get("stage")
    return f"failed:{stage}" if stage else f"failed:{status}"


def evaluate(candidate: PathLike, fidelity: Union[Fidelity, str], *, budget: Optional[Budget] = None,
             logger: Any = None, ledger: Optional[CostLedger] = None, information: Optional[Sequence[str]] = None,
             lf: Optional[Callable[..., Mapping[str, Any]]] = None, tool: Optional[Callable[..., Any]] = None,
             **options: Any) -> EvaluationRecord:
    """Evaluate ``candidate`` at ``fidelity``; see the module docstring for the order of events.

    ``options`` go to the tool (e.g. ``case_dir``, ``mesh_processes``,
    ``max_processes`` for the air fidelities; ``fd_check`` for liquid).
    ``tool`` replaces the dispatched function for this call; ``lf`` is the
    LF model and must return a mapping with ``status`` (and may give
    ``evaluator`` and ``derivatives``).
    """
    fidelity = Fidelity(fidelity)
    if fidelity is Fidelity.LF:
        if tool is None and lf is None:
            raise ValueError("Fidelity.LF evaluates the caller's own LF model: pass lf=callable")
        tool = tool or lf
    tool = tool or DISPATCH[fidelity]
    info = tuple(information) if information is not None else INFORMATION[fidelity]
    ledger = ledger if ledger is not None else CostLedger()
    if budget is not None:
        budget.check(fidelity)
    evaluator: Dict[str, Any] = {"tool": _callable_name(tool), "fidelity": str(fidelity)}
    from agent import AGENT_API_VERSION, __version__
    evaluator.update(agentVersion=__version__, agentApiVersion=AGENT_API_VERSION)
    error: Optional[BaseException] = None
    raw: Any = None
    with_record = None
    try:
        with ledger.attempt(f"{fidelity}:{Path(str(candidate)).name}", fidelity=str(fidelity),
                            candidate=str(candidate)) as attempt:
            with_record = attempt
            raw = tool(candidate, **options)
            attempt["status"] = (raw or {}).get("status", "unknown") if isinstance(raw, Mapping) else "unknown"
    except BaseException as exc:  # booked below, then re-raised
        error = exc
    metered = dict((with_record or {}).get("costs") or {})
    if budget is not None:
        budget.charge(fidelity, metered)
    if error is not None:
        if logger is not None:
            logger.physics_call(evaluator=evaluator, project=candidate if Path(str(candidate)).is_file() else None,
                                fidelity=str(fidelity), information=list(info), costs={"process": metered},
                                status="error", error=f"{type(error).__name__}: {error}")
        raise error
    result = raw if isinstance(raw, ResultView) else (view(dict(raw)) if isinstance(raw, Mapping) else raw)
    if not isinstance(result, Mapping):
        raise TypeError(f"{evaluator['tool']} returned {type(raw).__name__}, not a result mapping")
    identity = result.get("identity") if fidelity is not Fidelity.LF else result.get("evaluator")
    evaluator["identity"] = _jsonable(identity or {})
    stage_seconds = result.stage_seconds() if isinstance(result, ResultView) else {}
    costs = {"stage_seconds": stage_seconds, "elapsedSeconds": result.get("elapsedSeconds"), "process": metered}
    design = (result.design_identity() if isinstance(result, ResultView) else {}) or {}
    result_path = result.result_json if isinstance(result, ResultView) else None
    result_identity = {"format": result.get("format"), "kind": result.get("kind"),
                       "path": None if result_path is None else str(result_path)}
    record = EvaluationRecord(fidelity=fidelity, status=str(result.get("status")), validity=_validity(result),
                              information=info, derivatives=_derivatives(fidelity, result), costs=costs,
                              evaluator=evaluator, design=_jsonable(design), result_identity=result_identity,
                              result=result)
    if logger is not None:
        project = candidate if Path(str(candidate)).is_file() else None
        logger.physics_call(evaluator=evaluator, project=project, fidelity=str(fidelity), information=list(info),
                            costs=costs, status=record.status,
                            result=result_path if result_path is not None and Path(result_path).is_file() else None,
                            design=record.design, validity=record.validity, derivatives=list(record.derivatives),
                            result_format=result_identity["format"])
    return record
