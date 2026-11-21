"""Semantic edits: small dataclasses that compile to automation-contract build steps.

``compile_edits(edits, view)`` turns a list of edits into the ``{"verb",
"args"}`` steps :func:`agent.scripted_api.build_project` runs through the editor's own
contract (``window.__dcdtuni`` in JSDOM, every step through
``projectStore.edit`` -- so in a v5 project a time-variable field becomes a
keyframe at t = 0, exactly as a click). Python never writes the ``.cfdproj``.

Rules fixed here (learnt by the frozen ``projcomp``/``pipeline``):

- switching an object off **deletes** it (``removeModels``); ``setEnabled``
  only changes viewport visibility and leaves the exported geometry unchanged;
- an ACU's supply temperature/flow is set on its supply patch through
  ``updateAcuPatchPhysics`` (the one writer the export reads);
- a step that needs an earlier step's return value refers to it as ``"$N"``,
  N counted over the whole build; a literal string starting with ``$`` is
  escaped as ``"$$..."``.

Edits that need to look at the project read a :class:`ProjectView`: the saved
document (structure: ids, patches, valves) and, only when an edit needs
time-variable values (for example rack power), the project as the editor
materialises it at t = 0, fetched once through ``getProject``.

The liquid-network edits :class:`SetPumpSpeed`, :class:`SetPipeDiameter` and
:class:`SetValveOpening` emit their own contract verb as soon as it is one of
``BUILD_VERBS`` and raise :class:`ContractVerbMissing` until then;
:class:`SetValveOpening` meanwhile uses the open ``setModelCooling`` on the
same field (``cooling.valves[i].opening``), with the panel's own checks.
"""
from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass, field
from typing import Any, Callable, ClassVar, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

Step = Dict[str, Any]


class ContractVerbMissing(NotImplementedError):
    """The automation contract does not (yet) expose the verb an edit needs as a build verb."""

    def __init__(self, verb: str, edit: str, hint: str = ""):
        self.verb = verb
        super().__init__(f"{edit} needs the build verb {verb!r}, which the automation contract does not expose "
                         f"yet (src/frontend/react/src/headless/projectBuilder.ts BUILD_VERBS){': ' + hint if hint else ''}")


@dataclass(frozen=True)
class Ref:
    """``"$N"``: the value returned by step ``N`` of the build."""
    index: int


def _arg(value: Any) -> Any:
    if isinstance(value, Ref):
        return f"${value.index}"
    if isinstance(value, str):
        return "$" + value if value.startswith("$") else value
    if isinstance(value, (list, tuple)):
        return [_arg(v) for v in value]
    if isinstance(value, dict):
        return {k: _arg(v) for k, v in value.items()}
    return value


def step(verb: str, *args: Any) -> Step:
    return {"verb": verb, "args": [_arg(a) for a in args]}


def _finite(name: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number, got {value!r}")
    return float(value)


def _uid(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a nonempty id string, got {value!r}")
    return value


class ProjectView:
    """What an edit may read: the saved document, the t = 0 materialised project, the open build verbs."""

    def __init__(self, *, document: Optional[Mapping[str, Any]] = None,
                 materialized: Optional[Mapping[str, Any]] = None,
                 materialize: Optional[Callable[[], Mapping[str, Any]]] = None,
                 build_verbs: Optional[Iterable[str]] = None):
        self._document = document
        self._materialized = materialized
        self._materialize = materialize
        self._build_verbs = None if build_verbs is None else frozenset(build_verbs)
        self.offset = 0  # index of the next step in the whole build

    @property
    def document(self) -> Mapping[str, Any]:
        if self._document is None:
            raise ValueError("this edit needs the base project; pass a base .cfdproj")
        return self._document

    @property
    def materialized(self) -> Mapping[str, Any]:
        if self._materialized is None:
            if self._materialize is None:
                return self.document
            self._materialized = self._materialize()
        return self._materialized

    @property
    def build_verbs(self) -> frozenset:
        if self._build_verbs is None:
            from agent.scripted_api.contract_verbs import build_verbs
            self._build_verbs = frozenset(build_verbs())
        return self._build_verbs

    def ref(self, local_index: int) -> Ref:
        """The reference to step ``local_index`` of the edit being compiled."""
        return Ref(self.offset + local_index)

    @staticmethod
    def model(project: Mapping[str, Any], uid: str, bucket: Optional[str] = None) -> Optional[Mapping[str, Any]]:
        for name, models in (project.get("constructions") or {}).items():
            if bucket is not None and name != bucket:
                continue
            if isinstance(models, dict) and isinstance(models.get(uid), dict):
                return models[uid]
        return None


@dataclass(frozen=True)
class Edit:
    """One semantic change; ``steps(view)`` returns its build steps."""
    op: ClassVar[str] = "Edit"

    def steps(self, view: ProjectView) -> List[Step]:  # pragma: no cover - abstract
        raise NotImplementedError

    def to_dict(self) -> Dict[str, Any]:
        return {"op": type(self).__name__, **{f.name: getattr(self, f.name) for f in dataclasses.fields(self)}}


@dataclass(frozen=True)
class MoveModel(Edit):
    """Absolute location of a model (``setModelLocation``; refused when it leaves the project bound)."""
    uid: str
    x: float
    y: float
    z: float

    def __post_init__(self):
        _uid("uid", self.uid)
        for name in ("x", "y", "z"):
            _finite(name, getattr(self, name))

    def steps(self, view):
        return [step("setModelLocation", self.uid, {"x": float(self.x), "y": float(self.y), "z": float(self.z)})]


@dataclass(frozen=True)
class RotateModel(Edit):
    """Absolute rotation in degrees (YXZ Euler; ``yaw_deg`` is about the vertical y axis)."""
    uid: str
    yaw_deg: float = 0.
    pitch_deg: float = 0.
    roll_deg: float = 0.

    def __post_init__(self):
        _uid("uid", self.uid)
        for name in ("yaw_deg", "pitch_deg", "roll_deg"):
            _finite(name, getattr(self, name))

    def steps(self, view):
        return [step("setRotationDegrees", self.uid,
                     {"x": float(self.pitch_deg), "y": float(self.yaw_deg), "z": float(self.roll_deg)})]


@dataclass(frozen=True)
class RemoveModels(Edit):
    """Delete models (the way to switch an object off: ``setEnabled`` does not reach the geometry)."""
    uids: Tuple[str, ...]

    def __post_init__(self):
        uids = tuple(self.uids)
        if not uids:
            raise ValueError("RemoveModels needs at least one uid")
        for uid in uids:
            _uid("uid", uid)
        object.__setattr__(self, "uids", uids)

    def steps(self, view):
        return [step("removeModels", list(self.uids))]

    def to_dict(self):
        return {"op": "RemoveModels", "uids": list(self.uids)}


@dataclass(frozen=True)
class SetServerLoad(Edit):
    """One rack server's heat (W) and/or air flow (m³/s), through ``updateRackServerCfd``."""
    rack_uid: str
    server_uid: str
    power_w: Optional[float] = None
    flow_m3s: Optional[float] = None

    def __post_init__(self):
        _uid("rack_uid", self.rack_uid)
        _uid("server_uid", self.server_uid)
        if self.power_w is None and self.flow_m3s is None:
            raise ValueError("SetServerLoad needs power_w and/or flow_m3s")
        if self.power_w is not None and _finite("power_w", self.power_w) < 0:
            raise ValueError("power_w must be nonnegative")
        if self.flow_m3s is not None and _finite("flow_m3s", self.flow_m3s) < 0:
            raise ValueError("flow_m3s must be nonnegative")

    def steps(self, view):
        out = []
        if self.power_w is not None:
            out.append(step("updateRackServerCfd", self.rack_uid, self.server_uid, "power", float(self.power_w)))
        if self.flow_m3s is not None:
            out.append(step("updateRackServerCfd", self.rack_uid, self.server_uid, "volumeFlowRate",
                            float(self.flow_m3s)))
        return out


@dataclass(frozen=True)
class SetRackPower(Edit):
    """A rack's total server heat (W): ``proportional`` to the current powers, or ``equal`` per server."""
    rack_uid: str
    power_w: float
    split: str = "proportional"

    def __post_init__(self):
        _uid("rack_uid", self.rack_uid)
        if _finite("power_w", self.power_w) < 0:
            raise ValueError("power_w must be nonnegative")
        if self.split not in ("proportional", "equal"):
            raise ValueError("split must be 'proportional' or 'equal'")

    def steps(self, view):
        rack = view.model(view.materialized, self.rack_uid, "racks")
        if rack is None:
            raise ValueError(f"no rack {self.rack_uid!r} in the project")
        servers = ((rack.get("constructions") or {}).get("servers") or {})
        if not servers:
            raise ValueError(f"rack {self.rack_uid!r} has no servers")
        ids = sorted(servers)
        if self.split == "equal":
            shares = {sid: float(self.power_w) / len(ids) for sid in ids}
        else:
            current = {sid: ((servers[sid] or {}).get("cooling") or {}).get("power") for sid in ids}
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0
                   for v in current.values()) or sum(current.values()) <= 0:
                raise ValueError(f"rack {self.rack_uid!r}: not every server states a power (or the total is 0); "
                                 "use split='equal'")
            total = float(sum(current.values()))
            shares = {sid: float(self.power_w) * float(current[sid]) / total for sid in ids}
        return [step("updateRackServerCfd", self.rack_uid, sid, "power", shares[sid]) for sid in ids]


def _supply_patches(acu: Mapping[str, Any]) -> List[str]:
    found = []
    for face in ((acu.get("geometry") or {}).get("faces") or {}).values():
        for patch in (face or {}).get("patches") or []:
            if ((patch or {}).get("physics") or {}).get("role") == "supply":
                found.append(patch["id"])
    return found


@dataclass(frozen=True)
class SetAcuSupply(Edit):
    """An ACU's supply temperature (°C, every supply patch) and/or flow (m³/s, one supply patch)."""
    acu_uid: str
    temperature_c: Optional[float] = None
    flow_m3s: Optional[float] = None
    patch_id: Optional[str] = None

    def __post_init__(self):
        _uid("acu_uid", self.acu_uid)
        if self.temperature_c is None and self.flow_m3s is None:
            raise ValueError("SetAcuSupply needs temperature_c and/or flow_m3s")
        if self.temperature_c is not None:
            _finite("temperature_c", self.temperature_c)
        if self.flow_m3s is not None and _finite("flow_m3s", self.flow_m3s) < 0:
            raise ValueError("flow_m3s must be nonnegative")

    def steps(self, view):
        acu = view.model(view.document, self.acu_uid, "acus")
        patches = _supply_patches(acu) if acu is not None else []
        if not patches:
            raise ValueError(f"ACU {self.acu_uid!r} has no supply patch")
        if self.patch_id is not None:
            if self.patch_id not in patches:
                raise ValueError(f"{self.patch_id!r} is not a supply patch of {self.acu_uid!r} ({patches})")
            patches = [self.patch_id]
        out = []
        if self.temperature_c is not None:
            out += [step("updateAcuPatchPhysics", self.acu_uid, pid, "temperature", float(self.temperature_c))
                    for pid in patches]
        if self.flow_m3s is not None:
            if len(patches) != 1:
                raise ValueError(f"ACU {self.acu_uid!r} has {len(patches)} supply patches; name one with patch_id "
                                 "to set its flow")
            out.append(step("updateAcuPatchPhysics", self.acu_uid, patches[0], "volumeFlowRate", float(self.flow_m3s)))
        return out


@dataclass(frozen=True)
class AddProbe(Edit):
    """A named temperature probe at (x, y, z) m: ``addSensorProbe`` then ``setSensorProbe`` on its id."""
    name: str
    x: float
    y: float
    z: float

    def __post_init__(self):
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("probe name must be a nonempty string")
        for axis in ("x", "y", "z"):
            _finite(axis, getattr(self, axis))

    def steps(self, view):
        return [step("addSensorProbe", float(self.x), float(self.y), float(self.z)),
                step("setSensorProbe", view.ref(0), {"name": self.name})]


@dataclass(frozen=True)
class SetCduSupply(Edit):
    """A CDU's secondary supply temperature (°C), pressure (kPa) and/or total flow, via ``setCduLiquidParam``."""
    cdu_uid: str
    temperature_c: Optional[float] = None
    pressure_kpa: Optional[float] = None
    total_flow: Optional[float] = None

    def __post_init__(self):
        _uid("cdu_uid", self.cdu_uid)
        given = [(n, getattr(self, n)) for n in ("temperature_c", "pressure_kpa", "total_flow")
                 if getattr(self, n) is not None]
        if not given:
            raise ValueError("SetCduSupply needs temperature_c, pressure_kpa and/or total_flow")
        for name, value in given:
            _finite(name, value)

    def steps(self, view):
        out = []
        for attr, key in (("temperature_c", "supplyTemperature"), ("pressure_kpa", "supplyPressure"),
                          ("total_flow", "totalFlowRate")):
            value = getattr(self, attr)
            if value is not None:
                out.append(step("setCduLiquidParam", self.cdu_uid, key, float(value)))
        return out


@dataclass(frozen=True)
class SetPumpSpeed(Edit):
    """A CDU pump's speed ratio (0..2, the CDU panel's range); verb ``setCduPumpSpeed``.

    In a v5 project the value is the pump's timeline track at t = 0, which is
    what the liquid compiler reads.
    """
    verb: ClassVar[str] = "setCduPumpSpeed"
    cdu_uid: str
    rpm_ratio: float

    def __post_init__(self):
        _uid("cdu_uid", self.cdu_uid)
        if not 0 <= _finite("rpm_ratio", self.rpm_ratio) <= 2:
            raise ValueError("rpm_ratio must lie in [0, 2] (the CDU panel's range)")

    def steps(self, view):
        if self.verb not in view.build_verbs:
            raise ContractVerbMissing(self.verb, "SetPumpSpeed", "to run at another speed without editing the "
                                      "project, pass inputs={'pump_rpm_ratios': {<pump edge>: ratio}} to "
                                      "evaluate_liquid")
        return [step(self.verb, self.cdu_uid, float(self.rpm_ratio))]


@dataclass(frozen=True)
class SetPipeDiameter(Edit):
    """A liquid pipe's bore in metres (``geometry.diameterM``); verb ``setPipeDiameter``."""
    verb: ClassVar[str] = "setPipeDiameter"
    segment_uid: str
    diameter_m: float

    def __post_init__(self):
        _uid("segment_uid", self.segment_uid)
        if _finite("diameter_m", self.diameter_m) <= 0:
            raise ValueError("diameter_m must be positive (a zero bore is an infinite resistance)")

    def steps(self, view):
        if self.verb not in view.build_verbs:
            raise ContractVerbMissing(self.verb, "SetPipeDiameter")
        return [step(self.verb, self.segment_uid, float(self.diameter_m))]


@dataclass(frozen=True)
class SetPipeRoute(Edit):
    """Re-route a liquid pipe segment: world waypoints and named valve positions; verb ``setPipeSegmentPoints``.

    Ports, taps, bore and openings stay, so the liquid graph keeps its topology
    and changes only the route lengths, heights and tap places it measures.
    """
    verb: ClassVar[str] = "setPipeSegmentPoints"
    segment_uid: str
    waypoints: Tuple[Tuple[float, float, float], ...]
    valve_positions: Tuple[Tuple[str, Tuple[float, float, float]], ...] = ()

    def __post_init__(self):
        _uid("segment_uid", self.segment_uid)
        if len(self.waypoints) < 2:
            raise ValueError("a pipe route needs at least two waypoints")
        for point in (*self.waypoints, *(p for _, p in self.valve_positions)):
            if len(point) != 3:
                raise ValueError("points are (x, y, z) in metres")
            for value in point:
                _finite("point", value)
        for valve_id, _ in self.valve_positions:
            _uid("valve_id", valve_id)

    def steps(self, view):
        if self.verb not in view.build_verbs:
            raise ContractVerbMissing(self.verb, "SetPipeRoute")
        xyz = lambda p: {"x": float(p[0]), "y": float(p[1]), "z": float(p[2])}  # noqa: E731
        return [step(self.verb, self.segment_uid, [xyz(p) for p in self.waypoints],
                     {valve_id: xyz(p) for valve_id, p in self.valve_positions})]


VALVE_ADJUSTABLE = ("flow_control", "isolation")


@dataclass(frozen=True)
class SetValveOpening(Edit):
    """A flow-control/isolation valve's opening (0.001..1) on a pipe segment; verb ``setValveOpening``."""
    verb: ClassVar[str] = "setValveOpening"
    segment_uid: str
    valve_id: str
    opening: float

    def __post_init__(self):
        _uid("segment_uid", self.segment_uid)
        _uid("valve_id", self.valve_id)
        if not .001 <= _finite("opening", self.opening) <= 1:
            raise ValueError("opening must lie in [0.001, 1] (refused, not clamped, as in the pipe panel)")

    def steps(self, view):
        if self.verb in view.build_verbs:
            return [step(self.verb, self.segment_uid, self.valve_id, float(self.opening))]
        if "setModelCooling" not in view.build_verbs:
            raise ContractVerbMissing(self.verb, "SetValveOpening")
        segment = view.model(view.document, self.segment_uid, "pipeSegments")
        valves = ((segment or {}).get("cooling") or {}).get("valves") or []
        index = next((i for i, valve in enumerate(valves) if (valve or {}).get("id") == self.valve_id), None)
        if index is None:
            raise ValueError(f"no valve {self.valve_id!r} on pipe segment {self.segment_uid!r}")
        if valves[index].get("type") not in VALVE_ADJUSTABLE:
            raise ValueError(f"valve {self.valve_id!r} ({valves[index].get('type')}) is not adjustable; "
                             f"only {VALVE_ADJUSTABLE} have an opening")
        return [step("setModelCooling", self.segment_uid, f"valves.{index}.opening", float(self.opening))]


EDITS: Dict[str, type] = {cls.__name__: cls for cls in (
    MoveModel, RotateModel, RemoveModels, SetServerLoad, SetRackPower, SetAcuSupply, AddProbe, SetCduSupply,
    SetPumpSpeed, SetValveOpening, SetPipeDiameter, SetPipeRoute)}


def edit_from_dict(row: Mapping[str, Any]) -> Edit:
    """``{"op": "MoveModel", "uid": ..., ...}`` -> the edit (the inverse of ``Edit.to_dict``)."""
    row = dict(row)
    name = row.pop("op", None)
    if name not in EDITS:
        raise ValueError(f"unknown edit {name!r}; choose from {sorted(EDITS)}")
    try:
        return EDITS[name](**row)
    except TypeError as exc:
        raise ValueError(f"{name}: {exc}") from exc


def needs_materialized(edits: Sequence[Edit]) -> bool:
    """Whether any edit reads time-variable values (the t = 0 materialised project)."""
    return any(isinstance(edit, SetRackPower) for edit in edits)


def compile_edits(edits: Sequence[Edit], view: ProjectView, *, start: int = 0) -> List[Step]:
    """The build steps of ``edits`` in order; ``start`` is the index of the first one in the build."""
    out: List[Step] = []
    for edit in edits:
        if not isinstance(edit, Edit):
            raise TypeError(f"expected an Edit, got {type(edit).__name__}")
        view.offset = start + len(out)
        out.extend(edit.steps(view))
    return out
