"""air-v1 design -> build steps: a validated in-row/slab design as an :class:`agent.tools.edits.Edit`.

Replaces the legacy template-clone compiler (``agent_legacy/harness/air_compiler.py``),
which wrote ``.cfdproj`` fields directly. That cannot follow a v5 project, where an
ACU's supply temperature and a server's power live on the transient timeline, so
every change here is an automation-contract verb and the editor's own store writes
it (:func:`agent.tools.project.edit_project` -> ``agent.scripted_api.build_project``).

The geometry rules are the legacy compiler's:

- the template project's first rack with servers is duplicated into every RACK
  slot, so racks keep the template's declared physical inventory; the template
  rack itself and every other template model are removed, and ``applyRoom``
  builds the task's room shell;
- an in-row cooler (IRC) is a default ACU with two full-face patches: supply on
  its front (the row's front side), return on its back, at the design's
  set-point (``flow_fraction * rated_flow_m3s``);
- a contained aisle is real planar baffles -- a roof at the taller row's height and
  two end doors, porous at ``leak_open_area_pct`` -- plus solid strips closing the
  gap above any shorter enclosure or GAP slot.

Slot poses come from :func:`agent.scenarios.air.placement.slot_pose` (anchor = a
corner of the front face, yaw about it; the footprint table in dc-builder).
Compilation provenance (design, task, template identity) is returned by
:func:`compile_design`, not written into the project.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Tuple

from agent.scenarios.air.placement import slot_pose
from agent.scenarios.air.rules import aisles, row_depth, validate
from agent.scenarios.air.spec import ARCHITECTURES, Design, Task
from agent.tools.edits import Edit, ProjectView, step

VERSION = "air-inrow-build-compiler/v1"
DEFAULT_ACU_HEIGHT_M = 2.0


class IllegalDesign(ValueError):
    """The design breaks an air-v1 rule; ``issues`` are the rule violations."""

    def __init__(self, issues):
        self.issues = issues
        super().__init__("; ".join(issue.message for issue in issues))


def quaternion_to_euler_yxz(q: Tuple[float, float, float, float]) -> Dict[str, float]:
    """``(x, y, z, w)`` -> Euler degrees in Three.js order YXZ (what ``placeModel``'s ``rotationDeg`` reads)."""
    x, y, z, w = q
    m11 = 1 - 2 * (y * y + z * z)
    m13 = 2 * (x * z + y * w)
    m21 = 2 * (x * y + z * w)
    m22 = 1 - 2 * (x * x + z * z)
    m23 = 2 * (y * z - x * w)
    m31 = 2 * (x * z - y * w)
    m33 = 1 - 2 * (x * x + y * y)
    ex = math.asin(-max(-1.0, min(1.0, m23)))
    if abs(m23) < 0.9999999:
        ey, ez = math.atan2(m13, m33), math.atan2(m21, m22)
    else:
        ey, ez = math.atan2(-m31, m11), 0.0
    return {"x": math.degrees(ex), "y": math.degrees(ey), "z": math.degrees(ez)}


def euler_yxz_to_quaternion(deg: Mapping[str, float]) -> Tuple[float, float, float, float]:
    """The inverse, as Three.js ``Quaternion.setFromEuler`` computes it for order YXZ."""
    hx, hy, hz = (math.radians(deg[k]) / 2 for k in ("x", "y", "z"))
    c1, c2, c3 = math.cos(hx), math.cos(hy), math.cos(hz)
    s1, s2, s3 = math.sin(hx), math.sin(hy), math.sin(hz)
    return (s1 * c2 * c3 + c1 * s2 * s3, c1 * s2 * c3 - s1 * c2 * s3,
            c1 * c2 * s3 - s1 * s2 * c3, c1 * c2 * c3 + s1 * s2 * s3)


def _yaw_deg(quaternion: Mapping[str, float]) -> float:
    return math.degrees(2.0 * math.atan2(quaternion["y"], quaternion["w"])) % 360.0


def template_inventory(project: Mapping[str, Any]) -> Dict[str, Any]:
    """The template's rack (the first one with servers), its height, the ACU height and every model uid."""
    constructions = project.get("constructions") or {}
    if (project.get("runDefaults") or {}).get("liquid", {}).get("enabled") or constructions.get("cdus") \
            or constructions.get("liquidFlowNetworks"):
        raise NotImplementedError("the air-only compiler cannot discard a liquid topology; use a hybrid workflow")
    racks = constructions.get("racks") or {}
    rack_uid = next((uid for uid, rack in racks.items()
                     if (rack.get("constructions") or {}).get("servers")), None)
    if rack_uid is None:
        raise ValueError("the template needs a rack with actual server inventory")
    acus = list((constructions.get("acus") or {}).values())
    acu_height = float(acus[0]["geometry"]["size"]["y"]) if acus else DEFAULT_ACU_HEIGHT_M
    every = [uid for models in constructions.values() if isinstance(models, dict) for uid in models]
    return {"rackUid": rack_uid, "rackHeightM": float(racks[rack_uid]["geometry"]["size"]["y"]),
            "acuHeightM": acu_height, "modelUids": every,
            "serverCount": len(racks[rack_uid]["constructions"]["servers"])}


@dataclass(frozen=True)
class DesignToProject(Edit):
    """Build the room, racks, in-row coolers and containment of ``design`` on a template project."""
    design: Design
    task: Task
    rated_flow_m3s: float = 2.8
    leak_open_area_pct: float = 2.0
    name: Optional[str] = None
    op: ClassVar[str] = "DesignToProject"

    def __post_init__(self):
        issues = validate(self.design, self.task)
        if issues:
            raise IllegalDesign(issues)
        arch = ARCHITECTURES[self.design.architecture]
        if arch["unit"] != "inrow" or arch["floor"] != "slab":
            raise NotImplementedError("raised-floor and perimeter geometry need their own validated compiler")
        if not 0 < self.leak_open_area_pct < 100:
            raise ValueError("containment porosity must be a percentage strictly between 0 and 100")
        if self.task.rack.depth_m != self.task.irc.depth_m:
            raise NotImplementedError("containment requires flush, equal-depth rack and in-row cooler faces")

    def to_dict(self) -> Dict[str, Any]:
        return {"op": self.op, "design": asdict(self.design), "task": asdict(self.task),
                "rated_flow_m3s": self.rated_flow_m3s, "leak_open_area_pct": self.leak_open_area_pct,
                "name": self.name, "compiler": VERSION}

    def steps(self, view: ProjectView) -> List[Dict[str, Any]]:
        inv = template_inventory(view.document)
        task, design = self.task, self.design
        out: List[Dict[str, Any]] = []

        def add(verb: str, *args: Any):
            out.append(step(verb, *args))
            return view.ref(len(out) - 1)

        others = [uid for uid in inv["modelUids"] if uid != inv["rackUid"]]
        if others:
            add("removeModels", others)
        plane = [[0.0, 0.0], [task.room_x_m, 0.0], [task.room_x_m, task.room_y_m], [0.0, task.room_y_m]]
        add("applyRoom", plane, task.room_h_m)
        if self.name:
            add("setProjectMeta", {"name": self.name})

        declared = {sp.unit_id: sp for sp in design.setpoints}
        slot_heights: Dict[str, List[Tuple[float, float, float, str]]] = {}
        for row in design.rows:
            offset, row_slots = 0.0, []
            for index, kind in enumerate(row.slots):
                fp = task.irc if kind == "IRC" else task.rack
                if kind == "GAP":
                    row_slots.append((offset, fp.width_m, 0.0, kind))
                    offset += fp.width_m
                    continue
                label = f"{row.id}_{kind}_{index:02d}"
                location, quaternion = slot_pose(row, offset, fp, 0.0)
                height = inv["rackHeightM"] if kind == "RACK" else inv["acuHeightM"]
                if height > task.room_h_m:
                    raise ValueError(f"{label} is {height} m tall, above the {task.room_h_m} m room")
                uid = add("duplicateModel", inv["rackUid"]) if kind == "RACK" else add("createDefaultAcuModel")
                add("placeModel", uid, {"name": label, "location": location,
                                        "size": {"W": fp.width_m, "D": fp.depth_m, "H": height},
                                        "rotationDeg": {"x": 0.0, "y": _yaw_deg(quaternion), "z": 0.0}})
                if kind == "IRC":
                    point = declared[f"{row.id}:{index}"]
                    for face, role in (("front", "supply"), ("back", "return")):
                        patch = add("addPatch", uid, face, "rect")
                        add("fillFacePatch", uid, patch)
                        add("updateAcuPatchPhysics", uid, patch, "role", role)
                        if role == "supply":
                            add("updateAcuPatchPhysics", uid, patch, "temperature", float(point.t_supply_c))
                            add("updateAcuPatchPhysics", uid, patch, "volumeFlowRate",
                                float(point.flow_fraction) * float(self.rated_flow_m3s))
                row_slots.append((offset, fp.width_m, height, kind))
                offset += fp.width_m
            slot_heights[row.id] = row_slots
        add("removeModels", [inv["rackUid"]])
        self._containment(add, slot_heights)
        # A corner column the rows keep clear of (wall clearance), half way up.
        seed = (task.wall_clear_m / 2.0, task.room_h_m / 2.0, task.wall_clear_m / 2.0)
        for axis, value in zip("xyz", seed):
            add("setLocationInMesh", axis, value)
        return out

    def _containment(self, add, slot_heights) -> None:
        task, design = self.task, self.design
        rows = {row.id: row for row in design.rows}
        lookup = {frozenset((a.row_a, a.row_b)): a for a in aisles(design, task)}
        sq = math.sqrt(0.5)
        for number, pair in enumerate(design.containment_pairs):
            aisle = lookup[frozenset(pair)]
            a, b = rows[aisle.row_a], rows[aisle.row_b]
            along0 = max(a.start_m, b.start_m)
            across0 = a.center_m + row_depth(task) / 2.0
            height = max(h for row in (a, b) for _, _, h, _ in slot_heights[row.id])
            length, width = aisle.length_m, aisle.width_m

            def xyz(along, y, across, axis=a.axis):
                return {"x": along, "y": y, "z": across} if axis == "x" else {"x": across, "y": y, "z": along}

            roof_q = (sq, 0.0, 0.0, sq) if a.axis == "x" else (-0.5, -0.5, -0.5, 0.5)
            door_q = (0.0, -sq, 0.0, sq) if a.axis == "x" else (0.0, 0.0, 0.0, 1.0)
            side_q = (0.0, 0.0, 0.0, 1.0) if a.axis == "x" else (0.0, -sq, 0.0, sq)
            label = f"contain_{number:02d}"
            panels = [(f"{label}_roof", xyz(along0, height, across0), length, width, roof_q),
                      (f"{label}_end0", xyz(along0, 0.0, across0), width, height, door_q),
                      (f"{label}_end1", xyz(along0 + length, 0.0, across0), width, height, door_q)]
            for name, origin, w, h, q in panels:
                self._baffle(add, name, origin, w, h, q, self.leak_open_area_pct)
            for row, edge in ((a, across0), (b, across0 + width)):
                for index, (offset, w, h, _kind) in enumerate(slot_heights[row.id]):
                    lo = max(along0, row.start_m + offset)
                    hi = min(along0 + length, row.start_m + offset + w)
                    if hi <= lo or height - h < 1e-8:
                        continue
                    self._baffle(add, f"{label}_{row.id}_fill_{index:02d}", xyz(lo, h, edge),
                                 hi - lo, height - h, side_q, 0.0)

    @staticmethod
    def _baffle(add, name, origin, width, height, quaternion, leak_pct) -> None:
        uid = add("createDefaultBaffleModel")
        add("placeModel", uid, {"name": name, "location": origin, "size": {"W": width, "D": 0.0, "H": height},
                                "rotationDeg": quaternion_to_euler_yxz(quaternion)})
        if leak_pct > 0:
            add("setModelCooling", uid, "type", "perforated")
            add("setModelCooling", uid, "perforation",
                {"shape": "circle", "holeDiameterMm": 3.0, "openAreaPct": float(leak_pct)})
        else:
            add("setModelCooling", uid, "type", "wall")


def compile_design(design: Design, task: Task, template, *, out, rated_flow_m3s: float = 2.8,
                   leak_open_area_pct: float = 2.0, name: Optional[str] = None, overwrite: bool = False,
                   timeout: float = 600.0) -> Dict[str, Any]:
    """Build ``design`` on ``template`` into ``out``; the ``dcdtuni.api.build/v1`` report plus ``provenance``."""
    from agent.tools.project import edit_project, project_identity
    edit = DesignToProject(design, task, rated_flow_m3s=rated_flow_m3s,
                           leak_open_area_pct=leak_open_area_pct, name=name)
    report = edit_project(template, [edit], out=out, overwrite=overwrite, timeout=timeout)
    report["provenance"] = {"compiler": VERSION, "design": asdict(design), "task": asdict(task),
                            "template": str(template), "templateIdentity": project_identity(template),
                            "ratedFlowM3s": rated_flow_m3s, "leakOpenAreaPct": leak_open_area_pct}
    return report
