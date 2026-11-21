"""Shinaide hybrid design -> build steps: a legal :mod:`agent.scenarios.hybrid.task` action as edits.

Replaces ``agent_legacy/harness/shinaide_design_compiler_v4.py``'s ``build_project``
(with ``shinaide_containment_compile_v2`` and ``shinaide_hot_frame_compile_v1``),
which wrote the reference ``.cfdproj`` dictionary directly. Every change here is an
automation-contract verb applied by the editor's own store
(:func:`agent.tools.project.edit_project` -> ``agent.scripted_api.build_project``), so the
saved project is the editor's v5 document. In it an ACU's supply temperature and
the pump speed live on the transient timeline.

The rules are the legacy compiler's:

- each row moves rigidly by its own (x, z) shift from the task's reference row;
  every rack, in-row ACU and utility in it moves with it (``setModelLocation``);
- OPEN and CAC: the liquid mains move with the rows they serve. A waypoint or
  valve between the rows takes a shift interpolated by its distance across the
  aisle (``setPipeSegmentPoints``). Ports, taps, bores and openings stay, so the
  liquid graph keeps its topology. Rows shifted independently along x need a
  re-routing compiler that does not exist yet (a pending dependency);
- HAC: every body in the two rows turns 180 degrees in place, so the hot sides
  face the contained aisle (``setRotationDegrees``). The mains are then re-routed
  from the turned equipment's own liquid ports: each tap goes under its rack's
  port, and each main runs one U through both banks to the CDU. The ports come
  from the editor (``equipmentPortFrames``) after the turn, so
  :func:`compile_design` builds HAC in two stages;
- CAC and HAC: the contained aisle gets two end sheets and a roof (``addModel``),
  copies of the Shinaide project's baffle. Each has a central open cutout of the
  design's leak fraction;
- each in-row ACU's supply and return ports carry the design's flow
  (``flow_fraction`` x the rated 2.8 m^3/s) and the supply its temperature, a
  50 kW capacity and a coil 4 K below. In a fault context the failed ACU is
  closed (zero flow and capacity) and every survivor runs at the rated flow;
- the CDU pump runs at ``pump_N`` (``setCduPumpSpeed``).

Server payloads and the liquid topology are fixed by the task. :func:`compile_design`
checks both on the built project and then runs the legacy's static admission on
the editor's own geometry: wall clearance, body overlaps, new pipe/body
intersections, ports resolved by the liquid graph, and the aisle width.
Compilation provenance is returned, not written into the project.
"""
from __future__ import annotations

import copy
import json
import math
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Sequence

from agent.scenarios.hybrid.task import (REGISTERED, RATED_FLOW_M3S, REPO_ROOT, TaskRef, digest, external_context,
                                         validate)
from agent.tools.edits import Edit, ProjectView, SetPipeRoute, step

VERSION = "shinaide-hybrid-build-compiler/v2"
ACTIVE_CAPACITY_KW = 50.0
COIL_APPROACH_K = 4.0
FAN_KW_AT_RATED = 2.4
FAN_EXPONENT = 2.8
PORTED_FAMILIES = ("SLAB_INROW_OPEN", "SLAB_INROW_CAC", "SLAB_INROW_HAC")
CONTAINMENT_HEIGHT_M = 2.047
CONTAINMENT_TEMPLATE = "case/Shinaide_Liquid/Shinaide_liquid.cfdproj"
# Heights of the re-routed hot-frame mains under the raised support (the legacy's).
MAIN_Y_M = {"supply": .5527157325845515, "return": .25011844193719657}


class DependencyPending(NotImplementedError):
    """The action is legal, but a compiler it needs is not in the trunk yet."""

    def __init__(self, dependencies):
        self.dependencies = list(dependencies)
        super().__init__("pending: " + "; ".join(self.dependencies))


def port_patch(acu: Mapping[str, Any], role: str) -> Mapping[str, Any]:
    """The single active ``role`` port patch of an ACU (its ``supplyFaces``/``returnFaces`` reference)."""
    refs = acu["geometry"][role + "Faces"]
    if len(refs) != 1:
        raise ValueError("one explicit active source port per role")
    ref = refs[0]
    rows = [p for p in acu["geometry"]["faces"][ref["faceId"]]["patches"] if p["id"] == ref["patchId"]]
    if len(rows) != 1 or rows[0]["physics"]["role"] != role:
        raise ValueError("source port role/UID mismatch")
    return rows[0]


def row_shifts(action: Mapping[str, Any], task: Mapping[str, Any]) -> Dict[str, Dict[str, float]]:
    """Each row's (x, z) shift from its reference row."""
    return {row["id"]: {"x": row["start_m"] - old["start_m"], "z": row["center_m"] - old["center_m"]}
            for row, old in zip(action["layout"]["rows"], task["geometry"]["reference_rows"])}


def acu_states(action: Mapping[str, Any], task: Mapping[str, Any], failed_uid: Optional[str]) -> Dict[str, Any]:
    """The effective control of every in-row ACU in this context (the fault recourse applied)."""
    states = {}
    for uid in task["inventory"]["inrow_acu_uids"]:
        active = uid != failed_uid
        requested = action["setpoints"][uid]
        flow = (RATED_FLOW_M3S * requested["flow_fraction"] if failed_uid is None else RATED_FLOW_M3S) if active else 0.
        states[uid] = {"active": active, "supply_C": requested["supply_C"], "flow_m3s": flow,
                       "capacity_kw": ACTIVE_CAPACITY_KW if active else 0.,
                       "fan_kw": FAN_KW_AT_RATED * (flow / RATED_FLOW_M3S) ** FAN_EXPONENT if active else 0.,
                       "body_and_ports_retained": True}
    return states


def containment_sheets(action: Mapping[str, Any], task: Mapping[str, Any],
                       template: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """The contained aisle's two end sheets and roof as baffle models, and their record (none for OPEN)."""
    architecture = action["architecture"]
    if architecture == "SLAB_INROW_OPEN":
        return {"record": {"kind": "none", "sheets": []}, "models": []}
    rows = sorted(action["layout"]["rows"], key=lambda r: r["center_m"])
    low, high = rows
    cold = architecture == "SLAB_INROW_CAC"
    if (low["front"], high["front"]) != (("+", "-") if cold else ("-", "+")):
        raise ValueError("actual opposed bank orientation required")
    prefix = "ispo_cac_" if cold else "ispo_hac_"
    xmin = max(r["start_m"] for r in rows)
    xmax = min(r["start_m"] + .6 * len(r["slots"]) for r in rows)
    zmin, zmax = low["center_m"] + .6, high["center_m"] - .6
    floor = task["geometry"]["support_y_m"]
    height = CONTAINMENT_HEIGHT_M
    top = floor + height
    if xmax <= xmin or zmax - zmin < (1.22 if cold else .91) - 1e-9:
        raise ValueError("positive legal CAC footprint")
    if template is None:
        template = next(iter(json.loads((REPO_ROOT / CONTAINMENT_TEMPLATE).read_text())["constructions"]["baffles"]
                             .values()))
    root = math.sqrt(.5)
    definitions = [("end-left", {"x": xmin, "y": floor, "z": zmax}, zmax - zmin, height,
                    {"x": 0, "y": root, "z": 0, "w": root}),
                   ("end-right", {"x": xmax, "y": floor, "z": zmax}, zmax - zmin, height,
                    {"x": 0, "y": root, "z": 0, "w": root}),
                   ("roof", {"x": xmin, "y": top, "z": zmax}, xmax - xmin, zmax - zmin,
                    {"x": -root, "y": 0, "z": 0, "w": root})]
    leak = action["containment"]["leakage_open_area_fraction"]
    models, sheets = [], []
    for name, location, width, sheet_height, quaternion in definitions:
        uid = prefix + name.replace("-", "_")
        baffle = copy.deepcopy(dict(template))
        baffle["uid"] = uid
        baffle["meta"].update(name=uid, enabled=True,
                              description="Synthetic task-owned CAC wall sheet with explicit open leak cutout")
        baffle["geometry"].update(location=location, size={"x": width, "y": sheet_height, "z": 0},
                                  quaternion=quaternion, scale={"x": 1, "y": 1, "z": 1}, cutouts=[])
        for face in baffle["geometry"]["faces"].values():
            face["patches"] = []
        if leak > 0:
            w, h = width * math.sqrt(leak), sheet_height * math.sqrt(leak)
            x, y = (width - w) / 2, (sheet_height - h) / 2
            ring = [[x, y], [x + w, y], [x + w, y + h], [x, y + h], [x, y]]
            baffle["geometry"]["cutouts"] = [{"faceId": "front", "polygon": [[ring]],
                                              "sourceName": "task-owned-CAC-open-leak"}]
        models.append(baffle)
        sheets.append({"uid": uid, "role": name, "gross_area_m2": width * sheet_height,
                       "open_cutout_area_m2": leak * width * sheet_height, "leak_fraction": leak,
                       "one_sided_STL_material_area_m2_expected": width * sheet_height * (1 - leak)})
    record = {"format": "actual-leaky-contained-aisle-geometry/v2", "kind": "cold" if cold else "hot",
              "row_pair": [low["id"], high["id"]], "aisle_floor_rectangle_m": [xmin, xmax, zmin, zmax],
              "floor_y_m": floor, "roof_y_m": top, "rows_act_as_side_boundaries": True,
              "closed_end_and_roof_geometry": True,
              "leak_semantics": "explicit central open rectangle on each sheet; shared production geometry "
                                "removes its triangles",
              "sheets": sheets, "physical_acceptance": False}
    return {"record": record, "models": models}


def hot_frame_routes(action: Mapping[str, Any], constructions: Mapping[str, Any],
                     ports: Mapping[str, Mapping[str, Any]]) -> Dict[str, Any]:
    """HAC mains from the turned equipment's ports: ``{pipe uid: (waypoints, {valve id: position})}`` and records.

    ``ports`` is ``equipmentPortFrames`` of every tapped rack and of the CDU,
    read from the project after the rows turned.
    """
    row_for = {uid: row["id"] for row in action["layout"]["rows"] for uid in row["slots"]}
    cdu = _cdu_uid(constructions)
    routes, records = {}, []
    for uid, pipe in constructions["pipeSegments"].items():
        role = pipe["cooling"]["loopRole"]
        y = MAIN_Y_M["supply" if role == "supply" else "return"]
        valves, groups = {}, {}
        for valve in pipe["cooling"]["valves"]:
            rack = valve["connectedRackId"]
            p = ports[rack][role + "_bottom"]["position"]
            valves[valve["id"]] = {"x": p["x"], "y": y, "z": p["z"]}
            groups.setdefault(row_for[rack], []).append(valves[valve["id"]])
        if set(groups) != {"A", "B"}:
            raise ValueError("both original seven-rack banks must retain main-pipe taps")
        bank_a, bank_b = groups["A"], groups["B"]
        z_a = sum(p["z"] for p in bank_a) / len(bank_a)
        z_b = sum(p["z"] for p in bank_b) / len(bank_b)
        if max(abs(p["z"] - z_a) for p in bank_a) > 1e-8 or max(abs(p["z"] - z_b) for p in bank_b) > 1e-8:
            raise ValueError("cardinal same-bank manifold ports required")
        left_b = min(p["x"] for p in bank_b) - .2
        left_a = min(p["x"] for p in bank_a) - .2
        right = max(p["x"] for p in bank_a + bank_b) + .2
        end = ports[cdu][role + "_top"]["position"]
        # One non-overlapping U through B and A, then a CDU service corridor offset
        # from bank A so no main segment runs twice.
        corridor = z_a + (.25 if z_a > z_b else -.25)
        waypoints = [{"x": left_b, "y": y, "z": z_b}, {"x": right, "y": y, "z": z_b}, {"x": right, "y": y, "z": z_a},
                     {"x": left_a, "y": y, "z": z_a}, {"x": left_a, "y": y, "z": corridor},
                     {"x": end["x"], "y": y, "z": corridor}, {"x": end["x"], "y": y, "z": end["z"]},
                     {"x": end["x"], "y": end["y"], "z": end["z"]}]
        if role == "return":
            waypoints = list(reversed(waypoints))
        routes[uid] = (waypoints, valves)
        records.append({"pipe_uid": uid, "loop_role": role, "tap_count": len(valves),
                        "tap_UIDs": [v["connectedRackId"] for v in pipe["cooling"]["valves"]],
                        "main_waypoints_m": copy.deepcopy(waypoints),
                        "ports_authority": "equipmentPortFrames after the true row body rotations"})
    record = {"format": "hot-facing-UID-coherent-liquid-routes/v1",
              "body_rotations": "each device rotates180deg in place at its retained body center",
              "main_pipe_and_valve_ids_preserved": True, "native_pipe_material_control_parameters_preserved": True,
              "records": records, "physical_acceptance": False}
    return {"routes": routes, "record": record}


@dataclass(frozen=True)
class HybridDesignToProject(Edit):
    """Move (and for HAC turn) the rows, route the mains, contain the aisle, set the ACU ports and the pump.

    For HAC the mains follow the turned ports, which only the built project
    knows: these steps leave them, and :func:`compile_design` routes them in a
    second build.
    """
    action: Mapping[str, Any]
    task_ref: TaskRef = REGISTERED
    fault_id: str = "normal"
    task: Mapping[str, Any] = field(default=None, compare=False, repr=False)
    op: ClassVar[str] = "HybridDesignToProject"

    def __post_init__(self):
        task = self.task_ref.load()
        object.__setattr__(self, "task", task)
        decision = validate(self.action, task)
        pending = list(decision["dependencies"])
        if self.action["architecture"] not in PORTED_FAMILIES:
            pending.append(f"{self.action['architecture']} compiler")
        shift = row_shifts(self.action, task)
        if not pending and abs(shift["A"]["x"] - shift["B"]["x"]) > 1e-12:
            pending.append("independent per-row X shift pipe-routing compiler")
        if pending:
            raise DependencyPending(sorted(set(pending)))
        external_context(self.task_ref, self.fault_id)  # refuses an unregistered context

    @property
    def hot_frame(self) -> bool:
        return self.action["architecture"] == "SLAB_INROW_HAC"

    def to_dict(self) -> Dict[str, Any]:
        return {"op": self.op, "action": copy.deepcopy(dict(self.action)),
                "task_ref": {"path": self.task_ref.path, "sha256": self.task_ref.sha256},
                "fault_id": self.fault_id, "compiler": VERSION}

    def steps(self, view: ProjectView) -> List[Dict[str, Any]]:
        project, task, action = view.document, self.task, self.action
        constructions = project["constructions"]
        out: List[Dict[str, Any]] = []
        shift = row_shifts(action, task)
        by_uid = {uid: model for kind in ("racks", "acus", "cdus", "boxes")
                  for uid, model in constructions[kind].items()}
        for row, original in zip(action["layout"]["rows"], task["geometry"]["reference_rows"]):
            if self.hot_frame and row["front"] == original["front"]:
                raise ValueError("HAC must really rotate row ports, not relabel cold geometry")
            for uid in row["slots"]:
                geometry = by_uid[uid]["geometry"]
                location = {"x": geometry["location"]["x"] + shift[row["id"]]["x"], "y": geometry["location"]["y"],
                            "z": geometry["location"]["z"] + shift[row["id"]]["z"]}
                if self.hot_frame:
                    # Turn 180 degrees in place: the anchor is a corner, so it moves by the size.
                    sign = 1 if original["front"] == "-" else -1
                    location["x"] += sign * geometry["size"]["x"]
                    location["z"] += sign * geometry["size"]["z"]
                out.append(step("setModelLocation", uid, location))
                if self.hot_frame:
                    out.append(step("setRotationDegrees", uid, {"x": 0., "y": 180. if row["front"] == "+" else 0.,
                                                                "z": 0.}))
        if not self.hot_frame:
            reference = task["geometry"]["reference_rows"]
            low, high = reference[1]["center_m"] + .6, reference[0]["center_m"] - .6

            def move(point):
                weight = min(1.0, max(0.0, (point["z"] - low) / (high - low)))
                return {"x": point["x"] + shift["A"]["x"], "y": point["y"],
                        "z": point["z"] + (1 - weight) * shift["B"]["z"] + weight * shift["A"]["z"]}

            for uid, pipe in constructions["pipeSegments"].items():
                valves = {v["id"]: move(v["position"]) for v in pipe["cooling"]["valves"] if "position" in v}
                out.append(step("setPipeSegmentPoints", uid, [move(p) for p in pipe["geometry"]["waypoints"]], valves))
        failed = external_context(self.task_ref, self.fault_id)["failed_uid"]
        for uid, state in acu_states(action, task, failed).items():
            acu = constructions["acus"][uid]
            for role in ("supply", "return"):
                patch = port_patch(acu, role)
                flow = state["flow_m3s"]
                values = {"volumeFlowRate": flow, "velocity": flow / (patch["w"] * patch["h"])}
                if role == "supply":
                    values.update(temperature=state["supply_C"], setPointTemperature=state["supply_C"],
                                  coolingCapacity=state["capacity_kw"],
                                  coolantTemperature=state["supply_C"] - COIL_APPROACH_K)
                for path, value in values.items():
                    out.append(step("updateAcuPatchPhysics", uid, patch["id"], path, float(value)))
        out.append(step("setCduPumpSpeed", _cdu_uid(constructions), float(action["liquid"]["pump_N"])))
        if action["architecture"] == "SLAB_INROW_OPEN" and constructions["baffles"]:
            raise ValueError("OPEN must remove physical containment")
        for baffle in containment_sheets(action, task)["models"]:
            out.append(step("addModel", "baffles", baffle))
        return out


def _cdu_uid(constructions: Mapping[str, Any]) -> str:
    cdus = list(constructions["cdus"])
    if len(cdus) != 1:
        raise ValueError(f"the hybrid task has one CDU; the reference project has {len(cdus)}")
    return cdus[0]


def operation(action: Mapping[str, Any], task_ref: TaskRef, fault_id: str) -> Dict[str, Any]:
    """What the compiled candidate is: the record the legacy wrote into ``meta.provenance``, returned instead."""
    task = task_ref.load()
    context = external_context(task_ref, fault_id)
    states = acu_states(action, task, context["failed_uid"])
    uids = task["inventory"]["inrow_acu_uids"]
    return {"format": "task-owned-hybrid-candidate-context/v1", "task_path": task_ref.path,
            "task_sha256": task_ref.sha256, "action_sha256": digest(action), "architecture": action["architecture"],
            "containment_geometry": containment_sheets(action, task)["record"],
            "fault_id": fault_id, "failed_uid": context["failed_uid"], "all_ACU_UIDs": uids,
            "active_ACU_UIDs": [u for u in uids if states[u]["active"]], "effective_ACU_controls": states,
            "pump_N": action["liquid"]["pump_N"],
            "IT_budget_W": {uid: float(s["cooling"]["power"]) for uid, s in task["inventory"]["server_records"].items()},
            "plant_design_load_kw": 98.4, "thresholds": task["thresholds"],
            "fault_mask_source": "external trusted TaskRef/context, never policy action"}


def _fixed_parts(document: Mapping[str, Any]) -> Dict[str, Any]:
    constructions = document["constructions"]
    return {"racks": {uid: {"constructions": rack.get("constructions"), "cooling": rack.get("cooling")}
                      for uid, rack in constructions["racks"].items()},
            "liquidFlowNetworks": constructions.get("liquidFlowNetworks"),
            "pipeJunctions": constructions.get("pipeJunctions"),
            "pipeTopology": {uid: {"connection": p["cooling"].get("connection"),
                                   "taps": [(v.get("id"), v.get("connectedRackId")) for v in p["cooling"]["valves"]]}
                             for uid, p in constructions["pipeSegments"].items()}}


def segment_box_distance(a: Sequence[float], b: Sequence[float], lo: Sequence[float], hi: Sequence[float]) -> float:
    """Exact Euclidean distance between a straight segment ``a``-``b`` and an axis-aligned box."""
    d = [b[i] - a[i] for i in range(3)]
    cuts = {0.0, 1.0}
    for i in range(3):
        if abs(d[i]) > 1e-15:
            for bound in (lo[i], hi[i]):
                t = (bound - a[i]) / d[i]
                if 0 < t < 1:
                    cuts.add(t)
    cuts = sorted(cuts)
    candidates = list(cuts)
    for left, right in zip(cuts, cuts[1:]):
        mid = (left + right) / 2
        quadratic = linear = 0.0
        for i in range(3):
            x = a[i] + mid * d[i]
            target = lo[i] if x < lo[i] else hi[i] if x > hi[i] else None
            if target is not None:
                quadratic += d[i] ** 2
                linear += d[i] * (a[i] - target)
        if quadratic:
            candidates.append(min(right, max(left, -linear / quadratic)))
    return math.sqrt(min(sum(max(lo[i] - (a[i] + t * d[i]), 0, (a[i] + t * d[i]) - hi[i]) ** 2 for i in range(3))
                         for t in candidates))


def static_admission(project: Mapping[str, Any], bounds: Mapping[str, Any], ports: Mapping[str, Any],
                     liquidrun: Mapping[str, Any], action: Mapping[str, Any],
                     task: Mapping[str, Any]) -> Dict[str, Any]:
    """The legacy compiler's static gates on the editor's geometry; ``ValueError`` names the first one broken.

    ``bounds`` is ``modelBounds``, ``ports`` ``equipmentPortFrames`` of every rack
    and the CDU, ``liquidrun`` the exported ``.liquidrun`` of the same project.
    """
    constructions = project["constructions"]
    inventory = task["inventory"]
    uids = inventory["rack_uids"] + inventory["inrow_acu_uids"] + inventory["utility_uids"]
    plane = project["geometry"]["plane"]
    xmin, xmax = min(p[0] for p in plane), max(p[0] for p in plane)
    zmin, zmax = min(p[1] for p in plane), max(p[1] for p in plane)
    wall = {u: min(bounds[u]["min"][0] - xmin, xmax - bounds[u]["max"][0],
                   bounds[u]["min"][2] - zmin, zmax - bounds[u]["max"][2]) for u in uids}
    if min(wall.values()) < 1.2 - 1e-9:
        raise ValueError("actual compiled body violates wall clearance")
    collisions = []
    for i, u in enumerate(uids):
        for v in uids[i + 1:]:
            overlap = [min(bounds[u]["max"][k], bounds[v]["max"][k]) - max(bounds[u]["min"][k], bounds[v]["min"][k])
                       for k in range(3)]
            if min(overlap) > 1e-9:
                collisions.append([u, v])
    if collisions:
        raise ValueError("actual compiled equipment overlap")
    routes = [{"id": e["id"], "from": e["from"], "to": e["to"], "diameter_m": e["params"]["D_m"],
               "waypoints_m": e["params"]["waypoints_m"]} for e in liquidrun["edges"] if e["type"] == "pipe"]
    # The CDU's original port intersections are intentional; new ones with any other body are refused.
    allowed = {tuple(x) for x in task["geometry"]["existing_CDU_pipe_contacts"]}
    cdu = _cdu_uid(constructions)
    pipe_pairs = []
    for u in uids:
        box = bounds[u]
        for route in routes:
            distance = min(segment_box_distance(a, b, box["min"], box["max"])
                           for a, b in zip(route["waypoints_m"], route["waypoints_m"][1:])) - route["diameter_m"] / 2
            endpoint = u == cdu and (route["from"] in (cdu + "_supply", cdu + "_return")
                                     or route["to"] in (cdu + "_supply", cdu + "_return"))
            if distance < -1e-7 and (u, route["id"]) not in allowed and not endpoint:
                pipe_pairs.append([u, route["id"], distance])
    if pipe_pairs:
        raise ValueError("route/body intersection in compiled candidate: " + str(pipe_pairs[:3]))
    nodes = {n["id"]: n for n in liquidrun["nodes"]}
    errors = []
    for uid, frames in ports.items():
        for role in ("supply", "return"):
            key = uid + ("_mf_" if uid in constructions["racks"] else "_") + role
            position = nodes[key]["position_m"]
            expected = frames[role + "_top"]["position"]
            errors.append(max(abs(position[j] - expected[k]) for j, k in enumerate("xyz")))
    if max(errors) > 1e-9:
        raise ValueError("actual liquid graph does not resolve production equipment ports")
    aisles = []
    ordered = sorted(task["geometry"]["reference_rows"], key=lambda r: r["center_m"])
    minimum = .91 if action["architecture"] == "SLAB_INROW_HAC" else 1.22
    for lower, upper in zip(ordered, ordered[1:]):
        gap = min(bounds[u]["min"][2] for u in upper["slots"]) - max(bounds[u]["max"][2] for u in lower["slots"])
        if gap < minimum - 1e-9:
            raise ValueError("actual body aisle violates registered minimum")
        aisles.append({"rows": [lower["id"], upper["id"]], "width_m": gap, "minimum_m": minimum})
    return {"aisles": aisles, "port_to_liquid_graph_max_error_m": max(errors), "port_checks": len(errors),
            "wall_clearances_m": wall, "body_collisions": collisions, "new_pipe_body_intersections": pipe_pairs,
            "allowed_existing_contacts": task["geometry"]["existing_CDU_pipe_contacts"],
            "static_passed": True, "physics_solved": False}


def _scratch() -> Path:
    from agent.tools.project import SCRATCH_ROOT
    root = Path(SCRATCH_ROOT)
    root.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="hybrid-compile-", dir=root))


def _port_frames(path: Path, constructions: Mapping[str, Any], timeout: float) -> Dict[str, Any]:
    from agent.tools.project import query_project
    uids = sorted(constructions["racks"]) + sorted(constructions["cdus"])
    frames = query_project(path, [{"verb": "equipmentPortFrames", "args": [uid]} for uid in uids], timeout=timeout)
    return dict(zip(uids, frames))


def compile_design(action: Mapping[str, Any], *, out, task_ref: TaskRef = REGISTERED, fault_id: str = "normal",
                   overwrite: bool = False, timeout: float = 600.0) -> Dict[str, Any]:
    """Build ``action`` on the task's reference project into ``out`` and check it.

    Returns ``{"status": "dependency_pending", "dependencies": [...]}`` for a legal
    action the trunk cannot compile yet. Otherwise the ``dcdtuni.api.build/v1``
    report (of the last build) plus ``status: "compiled"``, ``provenance``,
    ``static_admission`` and ``checks``:

    - the servers, cold plates and manifolds and the liquid topology (networks,
      junctions, pipe ports and taps) equal the reference project's;
    - the ``.liquidrun`` the liquid panel would submit runs the pump at ``pump_N``;
    - the static gates of :func:`static_admission` pass.
    """
    from agent.tools.project import edit_project, export_liquid, project_identity, query_project
    try:
        edit = HybridDesignToProject(action, task_ref=task_ref, fault_id=fault_id)
    except DependencyPending as pending:
        return {"status": "dependency_pending", "dependencies": pending.dependencies, "compiler": VERSION,
                "action_sha256": digest(action), "context": external_context(task_ref, fault_id)}
    out = Path(out)
    reference = task_ref.project
    source_document = json.loads(reference.read_text(encoding="utf-8"))
    provenance_extra: Dict[str, Any] = {}
    scratch = _scratch()
    try:
        if edit.hot_frame:
            turned = scratch / "turned.cfdproj"
            edit_project(reference, [edit], out=turned, timeout=timeout)
            ports = _port_frames(turned, source_document["constructions"], timeout)
            hot = hot_frame_routes(action, source_document["constructions"], ports)
            routes = [SetPipeRoute(uid, tuple((p["x"], p["y"], p["z"]) for p in waypoints),
                                   tuple((vid, (p["x"], p["y"], p["z"])) for vid, p in valves.items()))
                      for uid, (waypoints, valves) in hot["routes"].items()]
            report = edit_project(turned, routes, out=out, overwrite=overwrite, timeout=timeout)
            provenance_extra["hot_frame_routes"] = hot["record"]
        else:
            report = edit_project(reference, [edit], out=out, overwrite=overwrite, timeout=timeout)
        # Fixed parts compared with the reference saved by the same editor (a v4
        # file becomes v5, with time-variable values on the timeline).
        edit_project(reference, [], out=scratch / "reference.cfdproj", timeout=timeout)
        source = json.loads((scratch / "reference.cfdproj").read_text(encoding="utf-8"))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    built = json.loads(out.read_text(encoding="utf-8"))
    if _fixed_parts(built) != _fixed_parts(source):
        raise ValueError("the compiled candidate changed fixed server/cold-plate/manifold data or the liquid topology")
    liquidrun = export_liquid(out, mode="steady")["liquidrun"]
    pumps = [e["params"].get("rpm_ratio") for e in liquidrun["edges"] if e["type"] == "pump"]
    if pumps != [float(action["liquid"]["pump_N"])]:
        raise ValueError(f"the liquid run lost the pump speed: {pumps} != [{action['liquid']['pump_N']}]")
    task = task_ref.load()
    bounds = query_project(out, [{"verb": "modelBounds", "args": []}], timeout=timeout)[0]
    ports = _port_frames(out, built["constructions"], timeout)
    admission = static_admission(built, bounds, ports, liquidrun, action, task)
    report.update(status="compiled", static_admission=admission,
                  checks={"fixed_parts_equal_reference": True, "liquidrun_pump_rpm_ratio": pumps},
                  provenance={"compiler": VERSION, "reference": project_identity(reference),
                              "operation": operation(action, task_ref, fault_id), **provenance_extra})
    return report
