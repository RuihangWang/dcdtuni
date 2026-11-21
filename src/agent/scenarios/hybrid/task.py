"""The Shinaide hybrid design task and its strict design grammar; no physics.

A port of the frozen ``agent_legacy/training/shinaide_design_codec_v4.py`` over the
task format of ``agent_legacy/scenario_hybrid/training_task_spec_v4.py``, unchanged
in meaning: the registered task file accepts and refuses the same actions with
the same messages, and the named continuous head has the same fields in the same
order (``test_hybrid_task`` compares the two codecs action by action).

- :class:`TaskRef` pins a task file and, through it, the reference project, by
  SHA-256; :data:`REGISTERED` is the six-family Shinaide task.
- :func:`validate` checks an action against the task and reports whether it can
  be compiled now or which compiler it still needs (``dependencies``).
- :func:`continuous_fields` / :func:`with_continuous_values` are the named numeric
  head: row centres and starts, ACU supply temperatures and flow fractions, the
  pump speed ratio.
- :func:`external_context` is the fault context. It comes from the task, never
  from the action.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[4]
TASK_FORMAT = "ispo-shinaide-training-task/v4"
DESIGN_FORMAT = "ispo-shinaide-design/v1"
ACTION_KEYS = ("format", "architecture", "layout", "acu_form", "containment", "perimeter_positions", "tiles",
               "setpoints", "liquid")
BODY_WIDTH_M = 0.6          # the registered common rack / in-row body footprint
BODY_DEPTH_M = 1.2
WALL_CLEARANCE_M = 1.2
COLD_AISLE_MIN_M = 1.22
OTHER_AISLE_MIN_M = 0.91
RATED_FLOW_M3S = 2.8
ROOM_FLOW_REQUIRED_M3S = 13.9216


@dataclass(frozen=True)
class TaskRef:
    """A task file (repository-relative) and the SHA-256 it must have."""
    path: str
    sha256: str

    def load(self) -> Dict[str, Any]:
        path = (REPO_ROOT / self.path).resolve()
        path.relative_to(REPO_ROOT)
        if hashlib.sha256(path.read_bytes()).hexdigest() != self.sha256:
            raise ValueError("trusted TaskSpec bytes changed")
        task = json.loads(path.read_text(encoding="utf-8"))
        if task["format"] != TASK_FORMAT:
            raise ValueError("unknown task format")
        source = (REPO_ROOT / task["source_reference"]["project"]).resolve()
        source.relative_to(REPO_ROOT)
        if hashlib.sha256(source.read_bytes()).hexdigest() != task["source_reference"]["sha256"]:
            raise ValueError("reference project changed")
        return task

    @property
    def project(self) -> Path:
        """The reference ``.cfdproj`` the task names (checked by :meth:`load`)."""
        return REPO_ROOT / self.load()["source_reference"]["project"]


REGISTERED = TaskRef("case/agent/shinaide-design-compile-20261011/TaskSpec_training_v4.json",
                     "7f1075fbb5324a46ff184fc685711c31edd74b47538d5cc7b259903fda5c7509")


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("finite numeric " + name + " required")
    return float(value)


def _strict_keys(value: Any, keys: Sequence[str], name: str) -> None:
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError(name + " keys differ from the registered design grammar")


def reference_action(task: Mapping[str, Any]) -> Dict[str, Any]:
    """The reference operating design: the task's rows, OPEN, every ACU at 20 °C and an equal share of the room flow."""
    uids = task["inventory"]["inrow_acu_uids"]
    return {"format": DESIGN_FORMAT, "architecture": "SLAB_INROW_OPEN",
            "layout": {"axis": "x", "rows": copy.deepcopy(task["geometry"]["reference_rows"])},
            "acu_form": "inrow", "containment": {"pairs": [], "leakage_open_area_fraction": .02},
            "perimeter_positions": [], "tiles": [],
            "setpoints": {uid: {"supply_C": 20.0, "flow_fraction": ROOM_FLOW_REQUIRED_M3S / 6 / RATED_FLOW_M3S}
                          for uid in uids},
            "liquid": {"pump_N": 1.0}}


def _validate_rows(action, task, family, names, seen, pairs) -> None:
    if action["acu_form"] == "inrow":
        expected = set(task["inventory"]["rack_uids"] + task["inventory"]["inrow_acu_uids"]
                       + task["inventory"]["utility_uids"])
        if set(seen) != expected:
            raise ValueError("fixed rack/ACU/utility UID inventory must occur exactly once")
        if action["perimeter_positions"] or action["tiles"]:
            raise ValueError("in-row architecture has no perimeter/tile actions")
        if set(action["setpoints"]) != set(task["inventory"]["inrow_acu_uids"]):
            raise ValueError("one setpoint per fixed in-row ACU")
        return
    if set(seen) != set(task["inventory"]["rack_uids"] + task["inventory"]["utility_uids"]):
        raise ValueError("perimeter rows contain fixed racks/utilities; no in-row ACUs or invented equipment")
    if not isinstance(action["perimeter_positions"], list) or not isinstance(action["tiles"], list):
        raise ValueError("perimeter and tile action lists required")
    for placement in action["perimeter_positions"]:
        _strict_keys(placement, ["unit_id", "wall", "position_m"], "perimeter placement")
        if placement["wall"] not in ("x0", "x1", "y0", "y1"):
            raise ValueError("perimeter wall")
        _number(placement["position_m"], "perimeter position")
    for tile in action["tiles"]:
        _strict_keys(tile, ["row_a", "row_b", "count"], "tile group")
        if (tile["row_a"] not in names or tile["row_b"] not in names or isinstance(tile["count"], bool)
                or not isinstance(tile["count"], int) or tile["count"] <= 0):
            raise ValueError("tile aisle/count")


def validate(action: Mapping[str, Any], task: Mapping[str, Any]) -> Dict[str, Any]:
    """Refuse an illegal action (``ValueError``); for a legal one, whether it compiles now or what it still needs."""
    _strict_keys(action, ACTION_KEYS, "action")
    if action["format"] != DESIGN_FORMAT:
        raise ValueError("unknown design format")
    if action["architecture"] not in task["architectures"]:
        raise ValueError("unregistered/forbidden architecture")
    family = task["architectures"][action["architecture"]]
    if action["acu_form"] != family["acu_form"]:
        raise ValueError("architecture and ACU form conflict")
    _strict_keys(action["layout"], ["axis", "rows"], "layout")
    if action["layout"]["axis"] not in ("x", "y"):
        raise ValueError("registered floor row axis")
    rows = action["layout"]["rows"]
    if not isinstance(rows, list) or not rows:
        raise ValueError("nonempty row family")
    names: List[str] = []
    seen: List[str] = []
    for row in rows:
        _strict_keys(row, ["id", "front", "start_m", "center_m", "slots"], "row")
        if not isinstance(row["id"], str) or not row["id"] or row["front"] not in ("+", "-"):
            raise ValueError("row identity/orientation")
        _number(row["start_m"], "row start")
        _number(row["center_m"], "row center")
        if not isinstance(row["slots"], list) or not row["slots"]:
            raise ValueError("row slot sequence required")
        if any(not isinstance(slot, str) for slot in row["slots"]):
            raise ValueError("UID slots or GAP tokens required")
        names.append(row["id"])
        seen += [slot for slot in row["slots"] if slot != "GAP"]
    if len(set(names)) != len(names) or len(set(seen)) != len(seen):
        raise ValueError("duplicate row or equipment UID")
    _strict_keys(action["containment"], ["pairs", "leakage_open_area_fraction"], "containment")
    leak = _number(action["containment"]["leakage_open_area_fraction"], "leak fraction")
    if not 0 <= leak < 1:
        raise ValueError("leak area fraction outside [0,1)")
    pairs = action["containment"]["pairs"]
    if not isinstance(pairs, list) or any(not isinstance(p, list) or len(p) != 2 or p[0] == p[1]
                                          or not set(p) <= set(names) for p in pairs):
        raise ValueError("contained aisle row-pair identity")
    if family["containment"] == "none" and pairs:
        raise ValueError("OPEN cannot contain a sealed aisle")
    if family["containment"] != "none" and not pairs:
        raise ValueError("contained architecture must declare its aisles")
    _validate_rows(action, task, family, names, seen, pairs)
    if action["acu_form"] == "perimeter":
        placement_ids = [p["unit_id"] for p in action["perimeter_positions"]]
        if len(set(placement_ids)) != len(placement_ids) or set(action["setpoints"]) != set(placement_ids):
            raise ValueError("one setpoint per unique perimeter placeholder; inventory binding remains external")
    for setpoint in action["setpoints"].values():
        _strict_keys(setpoint, ["supply_C", "flow_fraction"], "setpoint")
        for key in ("supply_C", "flow_fraction"):
            value = _number(setpoint[key], key)
            low, high = task["bounds"][key]
            if not low <= value <= high:
                raise ValueError("setpoint outside task bounds")
    _strict_keys(action["liquid"], ["pump_N"], "liquid control")
    pump = _number(action["liquid"]["pump_N"], "pump ratio")
    if not task["bounds"]["pump_N"][0] <= pump <= task["bounds"]["pump_N"][1]:
        raise ValueError("pump ratio outside declared synthetic domain")
    # Geometry rules use the registered common rack/in-row body footprint;
    # perimeter-body placement is separately pending its external catalog.
    along, cross = _room_axes(action, task)
    for row in rows:
        if (row["start_m"] < min(along) + WALL_CLEARANCE_M - 1e-9
                or row["start_m"] + BODY_WIDTH_M * len(row["slots"]) > max(along) - WALL_CLEARANCE_M + 1e-9):
            raise ValueError("row endpoint violates wall clearance")
        if (row["center_m"] - BODY_WIDTH_M < min(cross) + WALL_CLEARANCE_M - 1e-9
                or row["center_m"] + BODY_WIDTH_M > max(cross) - WALL_CLEARANCE_M + 1e-9):
            raise ValueError("row side violates wall clearance")
    ordered = sorted(rows, key=lambda r: r["center_m"])
    for lower, upper in zip(ordered, ordered[1:]):
        if lower["front"] == upper["front"]:
            raise ValueError("front-to-back adjacent rows are forbidden")
        cold = lower["front"] == "+" and upper["front"] == "-"
        minimum = COLD_AISLE_MIN_M if cold else OTHER_AISLE_MIN_M
        if upper["center_m"] - lower["center_m"] - BODY_DEPTH_M < minimum - 1e-9:
            raise ValueError("aisle below registered width")
    for pair in pairs:
        positions = sorted(ordered.index(next(r for r in ordered if r["id"] == name)) for name in pair)
        if positions[1] != positions[0] + 1:
            raise ValueError("contained rows must be adjacent")
        lower, upper = ordered[positions[0]], ordered[positions[1]]
        cold = lower["front"] == "+" and upper["front"] == "-"
        if (family["containment"] == "cold") != cold:
            raise ValueError("containment kind does not match aisle orientation")
    if (action["acu_form"] == "inrow"
            and sum(s["flow_fraction"] * RATED_FLOW_M3S for s in action["setpoints"].values())
            < ROOM_FLOW_REQUIRED_M3S - 1e-9):
        raise ValueError("normal operating airflow below registered global requirement")
    return {"syntax_and_task_bounds_valid": True, **_dependencies(action, task, family, rows, pairs)}


def _room_axes(action, task):
    axis = action["layout"]["axis"]
    plane = task["geometry"]["room_plane"]
    along = [v[0 if axis == "x" else 1] for v in plane]
    cross = [v[1 if axis == "x" else 0] for v in plane]
    return along, cross


def _dependencies(action, task, family, rows, pairs) -> Dict[str, Any]:
    dependencies = list(family["pending"]) if family["compiler"] is None else []
    if family["containment"] != "none" and pairs != [["B", "A"]] and pairs != [["A", "B"]]:
        dependencies += ["multi-aisle containment compiler"]
    if action["acu_form"] == "perimeter":
        dependencies += ["external perimeter catalog and fixed UID inventory registration"]
    if family["compiler"] is not None:
        reference = task["geometry"]["reference_rows"]
        hot = family["containment"] == "hot"
        if action["layout"]["axis"] != "x" or len(rows) != 2:
            dependencies += ["general row axis/count route compiler and LF topology"]
        elif any(a["id"] != b["id"] or a["front"] != ((("+" if b["front"] == "-" else "-") if hot else b["front"]))
                 or a["slots"] != b["slots"] for a, b in zip(rows, reference)):
            dependencies += ["UID-slot permutation/orientation liquid-route compiler"]
    return {"executable_now": not dependencies, "dependencies": sorted(set(dependencies))}


def encode(action: Mapping[str, Any], task: Mapping[str, Any]) -> str:
    validate(action, task)
    return json.dumps(action, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def decode(text: str, task: Mapping[str, Any]) -> Dict[str, Any]:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON action key")
            result[key] = value
        return result
    action = json.loads(text, object_pairs_hook=unique)
    validate(action, task)
    return action


def continuous_fields(action: Mapping[str, Any], task: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """The named numeric head of a legal action, in a fixed order, with each field's bounds."""
    validate(action, task)
    fields: List[Dict[str, Any]] = []
    along, cross = _room_axes(action, task)
    for row in action["layout"]["rows"]:
        low = min(cross) + WALL_CLEARANCE_M + BODY_WIDTH_M
        high = max(cross) - WALL_CLEARANCE_M - BODY_WIDTH_M
        start_low = min(along) + WALL_CLEARANCE_M
        start_high = max(along) - WALL_CLEARANCE_M - BODY_WIDTH_M * len(row["slots"])
        if abs(start_high - start_low) < 1e-9:
            start_high = start_low
        fields += [{"name": "row:" + row["id"] + ":center_m", "value": row["center_m"], "unit": "m",
                    "bounds": [low, high], "path_derivative_status": "geometry tensorization pending"},
                   {"name": "row:" + row["id"] + ":start_m", "value": row["start_m"], "unit": "m",
                    "bounds": [start_low, start_high], "path_derivative_status": "geometry tensorization pending"}]
    for key, unit in (("supply_C", "degC"), ("flow_fraction", "1")):
        for uid in sorted(action["setpoints"]):
            fields.append({"name": uid + ":" + key, "value": action["setpoints"][uid][key], "unit": unit,
                           "bounds": task["bounds"][key],
                           "path_derivative_status": "state/energy interface, point-specific validation required"})
    fields.append({"name": "m_30:pump_N", "value": action["liquid"]["pump_N"], "unit": "1",
                   "bounds": task["bounds"]["pump_N"],
                   "path_derivative_status": "native nominal-affinity interface; sampling-domain coverage pending"})
    for field in fields:
        field["sampled_in_this_task"] = (field["bounds"][1] - field["bounds"][0]) > 1e-9
    return fields


def with_continuous_values(action: Mapping[str, Any], task: Mapping[str, Any],
                           values: Sequence[float]) -> Dict[str, Any]:
    """``action`` with its numeric head replaced by ``values`` (same order as :func:`continuous_fields`)."""
    fields = continuous_fields(action, task)
    if len(values) != len(fields):
        raise ValueError("named numeric-head length mismatch")
    result = copy.deepcopy(dict(action))
    for field, value in zip(fields, values):
        value = _number(value, field["name"])
        parts = field["name"].split(":")
        if parts[0] == "row":
            row = next(r for r in result["layout"]["rows"] if r["id"] == parts[1])
            row[parts[2]] = value
        elif parts[1] == "pump_N":
            result["liquid"]["pump_N"] = value
        else:
            result["setpoints"][parts[0]][parts[1]] = value
    validate(result, task)
    return result


def external_context(task_ref: TaskRef, fault_id: str) -> Dict[str, Any]:
    """The fault context ``fault_id`` of the task (``normal`` or ``single-off/<ACU uid>``)."""
    task = task_ref.load()
    if fault_id not in task["fault_policy"]["contexts"]:
        raise ValueError("unregistered external fault context")
    return {"task_id": task["task_id"], "task_sha256": task_ref.sha256, "fault_id": fault_id,
            "failed_uid": None if fault_id == "normal" else fault_id.split("/", 1)[1],
            "fault_authority": "external task, not design action"}
