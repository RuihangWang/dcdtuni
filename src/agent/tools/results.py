"""The raw ``result.json`` of a ``agent.scripted_api`` run, and light typed views over it.

A view *is* the raw dictionary (it is a read-only ``Mapping`` over it, and
``view.raw`` is the very object the API returned); its properties only read
fields the platform already reported -- nothing is recomputed here.
:func:`read_result` loads a ``result.json`` (or its run directory) and checks
``format == "dcdtuni.api.result/v1"``.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Union

RESULT_FORMAT = "dcdtuni.api.result/v1"
PathLike = Union[str, "os.PathLike[str]"]


class ResultView(Mapping):
    """Read-only access to one result dictionary; common fields as properties."""

    def __init__(self, raw: Dict[str, Any], path: Optional[Path] = None):
        if not isinstance(raw, dict):
            raise TypeError("a result view wraps the result dictionary")
        self.raw = raw
        self.path = Path(path) if path is not None else None

    # Mapping -----------------------------------------------------------------
    def __getitem__(self, key: str) -> Any:
        return self.raw[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.raw)

    def __len__(self) -> int:
        return len(self.raw)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(kind={self.kind!r}, status={self.status!r}, path={str(self.path)!r})"

    # common ------------------------------------------------------------------
    @property
    def format(self) -> Optional[str]:
        return self.raw.get("format")

    @property
    def kind(self) -> Optional[str]:
        return self.raw.get("kind")

    @property
    def status(self) -> Optional[str]:
        return self.raw.get("status")

    @property
    def succeeded(self) -> bool:
        return self.status == "succeeded"

    @property
    def failure(self) -> Optional[Dict[str, Any]]:
        return self.raw.get("failure")

    @property
    def failure_stage(self) -> Optional[str]:
        return (self.failure or {}).get("stage")

    @property
    def identity(self) -> Dict[str, Any]:
        return self.raw.get("identity") or {}

    @property
    def inputs(self) -> Dict[str, Any]:
        return self.raw.get("inputs") or {}

    @property
    def stages(self) -> Dict[str, Any]:
        return self.raw.get("stages") or {}

    @property
    def metrics(self) -> Dict[str, Any]:
        return self.raw.get("metrics") or {}

    @property
    def run_dir(self) -> Optional[Path]:
        value = self.raw.get("runDir")
        return Path(value) if value else None

    @property
    def result_json(self) -> Optional[Path]:
        if self.path is not None:
            return self.path
        value = (self.raw.get("artifacts") or {}).get("resultJson")
        return Path(value) if value else None

    @property
    def elapsed_seconds(self) -> Optional[float]:
        return self.raw.get("elapsedSeconds")

    def stage_seconds(self) -> Dict[str, float]:
        """``{stage: seconds}`` for every stage that reports its own ``seconds``."""
        return {name: float(stage["seconds"]) for name, stage in self.stages.items()
                if isinstance(stage, dict) and isinstance(stage.get("seconds"), (int, float))}

    def design_identity(self) -> Dict[str, Any]:
        """The input hashes the platform recorded (``*Sha256`` fields of ``inputs``)."""
        return {key: value for key, value in self.inputs.items() if key.endswith("Sha256")}


class AirView(ResultView):
    @property
    def convergence(self) -> Dict[str, Any]:
        return self.metrics.get("convergence") or {}

    @property
    def convergence_verdict(self) -> Optional[str]:
        return self.convergence.get("verdict")

    @property
    def mesh(self) -> Dict[str, Any]:
        return self.stages.get("mesh") or {}

    @property
    def mesh_cells(self) -> Optional[int]:
        return self.mesh.get("cells")

    @property
    def check_mesh_ok(self) -> Optional[bool]:
        return self.mesh.get("checkMeshOk")

    @property
    def mesh_receipt_status(self) -> Optional[str]:
        return (self.mesh.get("qualityReceipt") or {}).get("status")

    @property
    def fields(self) -> Dict[str, Any]:
        return self.metrics.get("fields") or {}

    @property
    def rack_inlet_summary(self) -> Dict[str, Any]:
        return (self.fields.get("rackInlet") or {}).get("summary") or {}

    @property
    def acus(self) -> List[Dict[str, Any]]:
        return list(self.fields.get("acus") or [])

    @property
    def energy_balance(self) -> Dict[str, Any]:
        return self.fields.get("energyBalance") or {}

    @property
    def sensors(self) -> Any:
        return self.metrics.get("sensors")


class HybridView(AirView):
    @property
    def heat_ledger(self) -> Any:
        return self.metrics.get("heatLedger")

    @property
    def liquid(self) -> Dict[str, Any]:
        return self.metrics.get("liquid") or {}


class LiquidView(ResultView):
    @property
    def mode(self) -> Optional[str]:
        return self.inputs.get("mode")

    @property
    def objectives(self) -> Dict[str, float]:
        return self.metrics.get("objectives") or {}

    @property
    def gradients(self) -> Dict[str, Dict[str, Dict[str, float]]]:
        return self.metrics.get("gradients") or {}

    def gradient(self, objective: str, parameters: Optional[Sequence[str]] = None) -> Dict[str, Dict[str, float]]:
        """``{parameter: {edge/node id: d objective / d theta}}``; ``KeyError`` for a name not reported."""
        table = self.gradients[objective]
        if parameters is None:
            return {name: dict(values) for name, values in table.items()}
        return {name: dict(table[name]) for name in parameters}

    @property
    def finite_difference_checks(self) -> List[Dict[str, Any]]:
        return list(self.metrics.get("finiteDifferenceCheck") or [])

    @property
    def network(self) -> Dict[str, Any]:
        """The steady ``metrics.metrics`` block, or the transient metrics themselves."""
        inner = self.metrics.get("metrics")
        return inner if isinstance(inner, dict) else self.metrics

    @property
    def edges(self) -> List[Dict[str, Any]]:
        return list(self.network.get("edges") or [])

    @property
    def nodes(self) -> List[Dict[str, Any]]:
        return list(self.network.get("nodes") or [])

    @property
    def max_chip_temperature_K(self) -> Optional[float]:
        return self.network.get("maxChipTemperatureK")


class AdmissionView(ResultView):
    """``admit_air``'s record (format ``agent.tools.admission/v1``): an export, no server."""

    @property
    def admitted(self) -> bool:
        return bool(self.raw.get("admitted"))

    @property
    def blockers(self) -> List[str]:
        return list(self.raw.get("blockers") or [])


_VIEWS = {"air": AirView, "hybrid": HybridView, "liquid": LiquidView, "admission": AdmissionView}


def view(raw: Dict[str, Any], path: Optional[Path] = None) -> ResultView:
    """The typed view for ``raw["kind"]`` (a plain :class:`ResultView` for anything else)."""
    return _VIEWS.get(raw.get("kind"), ResultView)(raw, path)


def read_result(path: PathLike) -> ResultView:
    """Load ``result.json`` (or ``<run dir>/result.json``) and check its format."""
    path = Path(path)
    if path.is_dir():
        path = path / "result.json"
    if not path.is_file():
        raise FileNotFoundError(f"no result.json at {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("format") != RESULT_FORMAT:
        found = raw.get("format") if isinstance(raw, dict) else type(raw).__name__
        raise ValueError(f"{path} is not a {RESULT_FORMAT} document (format {found!r})")
    return view(raw, path)
