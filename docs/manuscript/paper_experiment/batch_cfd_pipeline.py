#!/usr/bin/env python3
"""Paper-experiment CFD driver: a Builder's dc-params.json -> .cfdproj -> mesh (and solve), through agent.scripted_api.

    conda activate dcdtuni          # from the repository root
    python docs/manuscript/paper_experiment/batch_cfd_pipeline.py docs/manuscript/paper_experiment/case_02_leakage \
        --mesh-processes 4 --solve-processes 4 --max-processes 8 --end-time 10
    ... --build-only                # stop after the .cfdproj
    ... --mesh-only                 # export and mesh, no solve

``--end-time 10`` (the default) is a pipeline smoke run: the return-air verdict
needs at least 1000 iterations, so the solve reads INCONCLUSIVE (too-short).
Pass ``--end-time 1500`` for a run you want judged.

1. ``params_to_steps``: dc-params.json (normalised from the spellings different
   subagents write) becomes automation-contract steps -- ``setProjectMeta``,
   ``applyRoom`` (walls, floor and ceiling as ROOM BOUNDARIES, the 2D editor's
   Apply), racks with their servers (``addRackServerAtRange`` +
   ``updateRackServerCfd``), ACUs each with a supply and a return patch
   (``addPatch`` -> ``fillFacePatch`` -> ``updateAcuPatchPhysics``), baffles and
   the run configuration.
2. ``build_from_params``: ``agent.scripted_api.build_project`` runs those steps on the
   editor's own contract in JSDOM (no browser, no server) and writes the save
   button's bytes, then self-checks them with the editor's air export.
3. ``agent.scripted_api.run_air_case`` meshes (and solves) it on a private server that
   ``start_server.sh`` starts on its own port pair with per-run credentials and
   that is stopped with ``fuser`` on those two ports only.

Every run writes a NEW round directory ``<case>/workdir/cfd/<YYYY-MM-DD_HHMMSS>/``
holding the built ``.cfdproj`` and, under its ``log/``, the run's ``result.json``.
Nothing is deleted, and the Builder's own ``workdir/<case>.cfdproj`` (the file
the Judge scores) is never touched. The topview is the Builder's deliverable
(/dc-builder Phase 3). Several cases at once: ``run_all_cases.py``.
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from agent.scripted_api import identity as ident  # noqa: E402
from agent.scripted_api.air import run_air_case, run_settings_from_options  # noqa: E402
from agent.scripted_api.build import build_project  # noqa: E402
from backend.aircooling.runner.convergence import minimum_run_iterations as _minimum_run_iterations  # noqa: E402


def _normalize_params(params):
    """Normalize dc-params.json from various subagent output formats."""
    # Normalize room keys: sizeX/sizeY/sizeZ → width/depth/height
    room = params.get("room", {})
    if "width" not in room and "sizeX" in room:
        room["width"] = room.pop("sizeX")
        room["height"] = room.pop("sizeY")
        room["depth"] = room.pop("sizeZ")
        params["room"] = room
    if "polygon" not in room and "width" in room:
        W, D = room["width"], room["depth"]
        room["polygon"] = [[0, 0], [W, 0], [W, D], [0, D]]

    # Convert 'racks' flat list → 'rackColumns' with count=1
    if "racks" in params and "rackColumns" not in params:
        columns = []
        for r in params["racks"]:
            col = {
                "name": r.get("name", f"Rack_{len(columns)+1}"),
                "count": 1,
                "startPosition": [r.get("pos_x", 0), r.get("pos_y", 0), r.get("pos_z", 0)],
                "spacing": 0.8,
                "orientation": r.get("orientation_deg", 0),
                "rackSize": {
                    "W": r.get("sizeW", 0.6),
                    "D": r.get("sizeD", 1.2),
                    "H": r.get("sizeH", 2.2),
                },
                "equipments": [
                    {
                        "kind": s.get("kind", "fanServer"),
                        "name": s.get("name", "server"),
                        "startUnit": s.get("start_U", s.get("startUnit", 29)),
                        "spanUnits": s.get("span_U", s.get("spanUnits", 5)),
                        "power_W": s.get("power_W", 150),
                        "flowRate_m3s": s.get("flowRate_m3s"),
                    }
                    for s in r.get("servers", r.get("equipments", []))
                ],
            }
            columns.append(col)
        params["rackColumns"] = columns

    # Normalize cfdSettings from various key names
    if "cfdSettings" not in params:
        solver = params.get("solver", {})
        mesh = params.get("mesh", {})
        params["cfdSettings"] = {
            "solverType": solver.get("solverType", solver.get("type", "steady")),
            # None = keep the project's own value: a new project starts on the
            # recommended cell-size rule, which a fixed default would override.
            "meshBaseSize": mesh.get("baseCellSize", mesh.get("meshBaseSize")),
            "maxGlobalCells": mesh.get("maxGlobalCells"),
            "parallelCores": solver.get("nCores", solver.get("parallelCores", 8)),
            "endTime": solver.get("endTime", solver.get("maxIter", 10)),
            "roomTemperature_C": solver.get("ambientTemp", solver.get("roomTemperature_C", 22)),
        }

    # Normalize ACU fields
    for acu in params.get("acus", []):
        if "position" not in acu and "pos_x" in acu:
            acu["position"] = [acu["pos_x"], acu.get("pos_y", 0), acu["pos_z"]]
        if "size" not in acu and "sizeW" in acu:
            acu["size"] = {"W": acu["sizeW"], "D": acu["sizeD"], "H": acu["sizeH"]}
        if "orientation" not in acu and "orientation_deg" in acu:
            acu["orientation"] = acu["orientation_deg"]
        if "supply" not in acu and "supply_temp_C" in acu:
            acu["supply"] = {
                "face": "front",
                "temperature_C": acu.get("supply_temp_C", 15),
                "flowRate_m3s": acu.get("supply_flowRate_m3s", 4.0),
                "velocity_ms": acu.get("supply_velocity_ms"),
                "direction": acu.get("supply_direction", [1, 0, 0]),
            }
        if "return" not in acu and "return_face" in acu:
            acu["return"] = {
                "face": acu.get("return_face", "top"),
                "direction": acu.get("return_direction", [0, 1, 0]),
            }

    # Normalize baffle fields
    for b in params.get("baffles", []):
        if "position" not in b and "pos_x" in b:
            b["position"] = [b["pos_x"], b.get("pos_y", 0), b["pos_z"]]
        if "size" not in b and "sizeW" in b:
            b["size"] = {"W": b["sizeW"], "D": b.get("sizeD", 0.01), "H": b["sizeH"]}
        if "orientation" not in b and "orientation_deg" in b:
            b["orientation"] = b["orientation_deg"]

    return params


class _Steps(list):
    """A build spec under construction: ``add`` appends one step and returns its ``"$N"`` reference."""

    def add(self, verb, *args):
        self.append({"verb": verb, "args": list(args)})
        return f"${len(self) - 1}"


def _seed(polygon, height):
    """A locationInMesh guess: the footprint's vertex centroid, just under the ceiling (above the racks)."""
    xs, zs = [v[0] for v in polygon], [v[1] for v in polygon]
    return {"x": sum(xs) / len(xs), "y": height - min(0.3, 0.1 * height), "z": sum(zs) / len(zs)}


def params_to_steps(params, project_name=None):
    """dc-params.json -> automation-contract steps for ``agent.scripted_api.build_project``."""
    params = _normalize_params(params)
    room = params["room"]
    W, D, H = room["width"], room["depth"], room["height"]
    cfd = params.get("cfdSettings", {})
    polygon = room.get("polygon") or [[0, 0], [W, 0], [W, D], [0, D]]
    steps = _Steps()

    # The project, then the room shell. applyRoom is the 2D editor's Apply: four
    # walls, a floor and a ceiling that are ROOM BOUNDARIES. A floor from
    # createDefaultFloorModel is an interior sheet (a createBaffles wall baffle)
    # with no air under it at y = 0, so the runner cannot seed it and the room
    # does not mesh.
    steps.add("setProjectMeta", {"name": project_name or params.get("projectName") or "project",
                                 "inputUnit": "m", "roomTemperatureC": cfd.get("roomTemperature_C", 22)})
    steps.add("applyRoom", polygon, H)

    # Racks, one column at a time, and the servers in each: an explicit 0-based
    # start unit and span, then the server's own power and airflow. Zero power
    # AND zero flow on a fan server is a solve failure, not a default.
    # `startPosition` is the first rack's anchor; the next anchors step by
    # `spacing` along +Z, or along +X when the column says "axis": "x" (a row of
    # racks facing -Z or +Z, yaw 0 or 180).
    for col in params.get("rackColumns", []):
        sx, sy, sz = col["startPosition"]
        size = col["rackSize"]
        along_x = str(col.get("axis", "z")).lower() == "x"
        for i in range(int(col["count"])):
            step = i * col["spacing"]
            rack = steps.add("createDefaultRackModel")
            steps.add("placeModel", rack, {
                "name": f"Rack_{col['name']}_{i + 1}",
                "location": {"x": sx + step if along_x else sx, "y": sy, "z": sz if along_x else sz + step},
                "size": {"W": size["W"], "D": size["D"], "H": size["H"]},
                "rotationDeg": {"x": 0, "y": col["orientation"], "z": 0}})
            for eq in col.get("equipments", []):
                server = steps.add("addRackServerAtRange", rack, int(eq["startUnit"]),
                                   int(eq.get("spanUnits") or 1), eq.get("kind", "fanServer"))
                steps.add("updateRackServerCfd", rack, server, "power", eq.get("power_W", 150))
                if eq.get("flowRate_m3s"):
                    steps.add("updateRackServerCfd", rack, server, "volumeFlowRate", eq["flowRate_m3s"])

    # ACUs: a supply and a return patch stretched over their faces. The default
    # ACU has no patches, so without these it has no boundary condition at all.
    for acu in params.get("acus", []):
        px, py, pz = acu["position"]
        size = acu["size"]
        sup, ret = acu["supply"], acu["return"]
        uid = steps.add("createDefaultAcuModel")
        steps.add("placeModel", uid, {"name": acu["name"], "location": {"x": px, "y": py, "z": pz},
                                      "size": {"W": size["W"], "D": size["D"], "H": size["H"]},
                                      "rotationDeg": {"x": 0, "y": acu["orientation"], "z": 0}})
        supply = steps.add("addPatch", uid, sup.get("face", "front"), "rect")
        steps.add("fillFacePatch", uid, supply)
        steps.add("updateAcuPatchPhysics", uid, supply, "role", "supply")
        steps.add("updateAcuPatchPhysics", uid, supply, "temperature", sup.get("temperature_C", 15))
        steps.add("updateAcuPatchPhysics", uid, supply, "volumeFlowRate", sup.get("flowRate_m3s", 4.0))
        if sup.get("velocity_ms"):
            steps.add("updateAcuPatchPhysics", uid, supply, "velocity", sup["velocity_ms"])
        back = steps.add("addPatch", uid, ret.get("face", "top"), "rect")
        steps.add("fillFacePatch", uid, back)
        steps.add("updateAcuPatchPhysics", uid, back, "role", "return")

    # Baffles: thin surfaces, like the walls.
    for baffle in params.get("baffles", []):
        bx, by, bz = baffle["position"]
        uid = steps.add("createDefaultBaffleModel")
        steps.add("placeModel", uid, {"name": baffle["name"], "location": {"x": bx, "y": by, "z": bz},
                                      "size": {"W": baffle["size"]["W"], "D": 0, "H": baffle["size"]["H"]},
                                      "rotationDeg": {"x": 0, "y": baffle.get("orientation", 0), "z": 0}})

    # Run configuration: solver fields to simulationConfig, mesh fields to
    # meshConfig -- separate buckets; the runner reads each from its own.
    end_time = cfd.get("endTime", 1000)
    steps.add("setSolverType", cfd.get("solverType", "steady"))
    for key, value in (("endTime", end_time), ("writeInterval", end_time), ("parallelSolve", True),
                       ("solveProcess", cfd.get("parallelCores", 8))):
        steps.add("setSolverConfig", key, value)
    # Cell size and cell budget only when dc-params states them: a new project
    # already starts on the recommended cell-size rule.
    for key in ("meshBaseSize", "maxGlobalCells"):
        if cfd.get(key) is not None:
            steps.add("setMeshConfig", key, cfd[key])
    steps.add("setMeshConfig", "locationInMesh", cfd.get("locationInMesh") or _seed(polygon, H))
    return list(steps)


def build_from_params(params, out, *, project_name=None):
    """Build ``out`` (a new .cfdproj) from dc-params; returns the ``agent.scripted_api.build`` report.

    A built file also gets ``report["layoutCheck"]`` (``layout_check``): the
    editor's export, and the mesh after it, accept a model outside the room or a
    baffle through a rack, so a wrong anchor would otherwise surface only as a
    wrong result.
    """
    out = Path(out)
    report = build_project(params_to_steps(params, project_name=project_name or out.stem), out=out)
    if report.get("status") == "succeeded" and out.is_file():
        try:
            report["layoutCheck"] = layout_check(out)
        except Exception as exc:  # the check is advisory; never lose the build report over it
            report["layoutCheck"] = {"ok": None, "problems": [], "error": f"{type(exc).__name__}: {exc}"}
    return report


# ── layout check: what the exported geometry says about where models stand ──

# Buckets whose entries are not bodies of their own (rows group racks, servers
# live inside racks, sensors and the liquid network export no air solid).
_NOT_BODIES = {"rows", "servers", "sensors", "liquidFlowNetworks", "heatGains", "pipeJunctions"}
_TOL = 1e-6


def _load_project(cfdproj):
    """The project as a v4+ document: a v2/v3 file (the dataset labels) is migrated the way the Judge loads it."""
    project = json.loads(Path(cfdproj).read_text(encoding="utf-8"))
    if isinstance(project.get("constructions"), dict):
        return project
    scripts = str(REPO / "src" / "frontend" / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    from cfdproj_migrate import migrate_cfdproj_to_v4
    return migrate_cfdproj_to_v4(project)


def _project_models(project):
    """Name of every top-level model of a V4/V5 .cfdproj."""
    names = []
    for bucket, value in (project.get("constructions") or {}).items():
        if bucket in _NOT_BODIES or not isinstance(value, dict):
            continue
        items = [value] if isinstance(value.get("meta"), dict) else value.values()
        for model in items:
            name = ((model or {}).get("meta") or {}).get("name") if isinstance(model, dict) else None
            if name and name not in names:
                names.append(name)
    return names


def _solid_boxes(stl_files):
    """``{solid name: [[xmin, ymin, zmin], [xmax, ymax, zmax]]}`` over the ASCII STL texts."""
    boxes = {}
    for text in stl_files.values():
        solid = None
        for line in text.splitlines():
            words = line.split()
            if not words:
                continue
            if words[0] == "solid":
                solid = " ".join(words[1:])
            elif words[0] == "vertex" and solid is not None and len(words) >= 4:
                point = [float(v) for v in words[1:4]]
                box = boxes.setdefault(solid, [list(point), list(point)])
                for axis in range(3):
                    box[0][axis] = min(box[0][axis], point[axis])
                    box[1][axis] = max(box[1][axis], point[axis])
    return boxes


def _owner(solid, keys):
    """The model a solid belongs to: the longest model name that is the solid or a ``name_`` prefix of it."""
    best = None
    for key, name in keys:
        if (solid == key or solid.startswith(key + "_")) and (best is None or len(key) > len(best[0])):
            best = (key, name)
    return best[1] if best else None


def model_boxes(cfdproj, *, stl_files=None):
    """Axis-aligned box of every model, from the STL the editor's export writes for ``cfdproj``.

    ``{model name: {"x": [lo, hi], "y": [lo, hi], "z": [lo, hi]}}``; a model that
    exports no solid (disabled, a sensor, ...) is absent.
    """
    project = _load_project(cfdproj)
    if stl_files is None:
        from agent.scripted_api.air import export_air_case
        stl_files = export_air_case(cfdproj)["stlFiles"]
    keys = []
    for name in _project_models(project):
        keys.append((name, name))
        safe = re.sub(r"[^A-Za-z0-9_]+", "_", name)
        if safe != name:
            keys.append((safe, name))
    boxes = {}
    for solid, (lo, hi) in _solid_boxes(stl_files).items():
        name = _owner(solid, keys)
        if name is None:
            continue
        box = boxes.setdefault(name, [list(lo), list(hi)])
        for axis in range(3):
            box[0][axis] = min(box[0][axis], lo[axis])
            box[1][axis] = max(box[1][axis], hi[axis])
    return {name: {axis: [lo[i], hi[i]] for i, axis in enumerate("xyz")} for name, (lo, hi) in boxes.items()}


def _inside_polygon(x, z, plane):
    """Point in polygon (``[[x, z], ...]``), a point on an edge counting as inside."""
    n, inside = len(plane), False
    for i in range(n):
        (x1, z1), (x2, z2) = plane[i], plane[(i + 1) % n]
        cross = (x2 - x1) * (z - z1) - (z2 - z1) * (x - x1)
        if abs(cross) <= 1e-6 * max(1.0, abs(x2 - x1) + abs(z2 - z1)) and \
                min(x1, x2) - 1e-6 <= x <= max(x1, x2) + 1e-6 and min(z1, z2) - 1e-6 <= z <= max(z1, z2) + 1e-6:
            return True
        if (z1 > z) != (z2 > z) and x < x1 + (z - z1) * (x2 - x1) / (z2 - z1):
            inside = not inside
    return inside


def _is_room_shell(box, plane, height):
    """A sheet on the room boundary: the floor, the ceiling, or a wall along a footprint edge."""
    x, y, z = box["x"], box["y"], box["z"]
    flat_y = y[1] - y[0] <= _TOL
    if flat_y and (abs(y[0]) <= _TOL or abs(y[0] - height) <= _TOL):
        return True
    if y[1] - y[0] < height - 1e-3:
        return False
    for i in range(len(plane)):
        (x1, z1), (x2, z2) = plane[i], plane[(i + 1) % len(plane)]
        ends = {(round(min(x1, x2), 6), round(min(z1, z2), 6)), (round(max(x1, x2), 6), round(max(z1, z2), 6))}
        if ends == {(round(x[0], 6), round(z[0], 6)), (round(x[1], 6), round(z[1], 6))}:
            return True
    return False


def _interpenetrate(a, b):
    """Two boxes whose bodies cut into each other (touching faces do not count).

    On an axis where a box is a sheet (no thickness), its plane must lie strictly
    inside the other box; elsewhere the two ranges must overlap by more than the
    tolerance. Two sheets in one plane are left alone.
    """
    for axis in "xyz":
        (a0, a1), (b0, b1) = a[axis], b[axis]
        flat_a, flat_b = a1 - a0 <= _TOL, b1 - b0 <= _TOL
        if flat_a and flat_b:
            return False
        if flat_a:
            if not b0 + _TOL < a0 < b1 - _TOL:
                return False
        elif flat_b:
            if not a0 + _TOL < b0 < a1 - _TOL:
                return False
        elif min(a1, b1) - max(a0, b0) <= _TOL:
            return False
    return True


def layout_check(cfdproj, *, stl_files=None):
    """Models outside the room and models cutting into each other, read from the exported STL.

    Advisory and bounding-box based: a model turned by a yaw that is not a
    multiple of 90 degrees is checked by its box. Some overlaps are intended
    (a downflow ACU standing through a raised floor); name them in the report.
    Returns ``{ok, problems: [{kind: "outside-room", model, box} |
    {kind: "interpenetrate", models, boxes}], room, checkedModels}``.
    """
    project = _load_project(cfdproj)
    geometry = project.get("geometry") or {}
    plane = geometry.get("plane") or []
    height = float(geometry.get("height") or 0)
    boxes = model_boxes(cfdproj, stl_files=stl_files)
    bodies = {name: box for name, box in boxes.items() if not (plane and _is_room_shell(box, plane, height))}
    problems = []
    if plane and height > 0:
        for name, box in bodies.items():
            corners = [(box["x"][i], box["z"][j]) for i in (0, 1) for j in (0, 1)]
            if (box["y"][0] < -_TOL or box["y"][1] > height + _TOL
                    or not all(_inside_polygon(x, z, plane) for x, z in corners)):
                problems.append({"kind": "outside-room", "model": name, "box": box})
    names = sorted(bodies)
    for i, first in enumerate(names):
        for second in names[i + 1:]:
            if _interpenetrate(bodies[first], bodies[second]):
                problems.append({"kind": "interpenetrate", "models": [first, second],
                                 "boxes": {first: bodies[first], second: bodies[second]}})
    xs, zs = [p[0] for p in plane], [p[1] for p in plane]
    room = {"x": [min(xs), max(xs)], "y": [0.0, height], "z": [min(zs), max(zs)]} if plane else None
    return {"ok": not problems, "problems": problems, "room": room, "checkedModels": len(bodies)}


def new_round_dir(workdir, stamp=None):
    """``<workdir>/cfd/<YYYY-MM-DD_HHMMSS>/``, created new (never an existing one)."""
    stamp = stamp or ident.now_sgt().strftime("%Y-%m-%d_%H%M%S")
    path = Path(workdir) / "cfd" / stamp
    path.mkdir(parents=True, exist_ok=False)
    return path


def summarize(case_name, result, build=None, round_dir=None, elapsed=None):
    """The per-case line the paper tables use, from a agent.scripted_api result."""
    stages = result.get("stages") or {}
    mesh, solve = stages.get("mesh") or {}, stages.get("solve") or {}
    failure = result.get("failure") or {}
    status = "SUCCESS" if result.get("status") == "succeeded" else f"FAIL_{str(failure.get('stage')).upper()}"
    return {
        "case": case_name, "status": status,
        "build_ok": build is None or build.get("status") == "succeeded",
        "model_count": (build or {}).get("modelCount"),
        "mesh_ok": mesh.get("returncode") == 0, "cell_count": mesh.get("cells"),
        "solve_ok": solve.get("returncode") == 0 if solve else None,
        "convergence": ((result.get("metrics") or {}).get("convergence") or {}).get("verdict"),
        "round_dir": str(round_dir) if round_dir else None,
        "result_json": (result.get("artifacts") or {}).get("resultJson"),
        "error": failure.get("reason"),
        "time_total_s": round(elapsed, 1) if elapsed is not None else result.get("elapsedSeconds"),
    }


def run_pipeline(case_dir, *, end_time=10, mesh_only=False, build_only=False, mesh_processes=None,
                 solve_processes=None, max_processes=8):
    """dc-params.json -> new round directory -> .cfdproj -> mesh/solve. Returns the summary row."""
    case_dir = Path(case_dir).resolve()
    workdir = case_dir / "workdir"
    case_name = case_dir.name
    started = time.time()
    params_path = workdir / "dc-params.json"
    if not params_path.is_file():
        return {"case": case_name, "status": "FAIL_INTERPRET", "error": f"{params_path} not found"}
    params = json.loads(params_path.read_text(encoding="utf-8"))
    params = _normalize_params(params)
    params.setdefault("cfdSettings", {})["endTime"] = end_time
    round_dir = new_round_dir(workdir)
    cfdproj = round_dir / f"{case_name}.cfdproj"
    build = build_from_params(params, cfdproj, project_name=case_name)
    if build.get("status") != "succeeded" or not (build.get("selfCheck") or {}).get("ok"):
        return {"case": case_name, "status": "FAIL_BUILD", "build_ok": build.get("status") == "succeeded",
                "round_dir": str(round_dir), "error": (build.get("failure") or {}).get("reason")
                or json.dumps(build.get("selfCheck"), default=str)[:500],
                "time_total_s": round(time.time() - started, 1)}
    if build_only:
        return {"case": case_name, "status": "BUILT", "build_ok": True, "model_count": build.get("modelCount"),
                "cfdproj": str(cfdproj), "round_dir": str(round_dir), "time_total_s": round(time.time() - started, 1)}
    per_job = max_processes
    settings = run_settings_from_options(None, mesh_processes=mesh_processes or per_job,
                                         solve_processes=solve_processes or per_job, end_time=end_time)
    result = run_air_case(cfdproj, run_settings=settings, stages=("mesh",) if mesh_only else ("mesh", "solve"),
                          max_processes=max_processes, label=case_name)
    return summarize(case_name, result, build=build, round_dir=round_dir, elapsed=time.time() - started)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("case_dir", help="a paper_experiment case directory holding workdir/dc-params.json")
    parser.add_argument("--end-time", type=int, default=10,
                        help="solver iterations (default 10: a pipeline smoke run whose verdict is INCONCLUSIVE; "
                             f"the return-air verdict needs at least {_minimum_run_iterations()} iterations)")
    parser.add_argument("--mesh-only", action="store_true", help="export and mesh; no solve")
    parser.add_argument("--build-only", action="store_true", help="stop after building the .cfdproj")
    parser.add_argument("--mesh-processes", type=int, help="mesh MPI ranks (default --max-processes)")
    parser.add_argument("--solve-processes", type=int, help="solve MPI ranks (default --max-processes)")
    parser.add_argument("--max-processes", type=int, default=8, help="MPI rank budget (default 8)")
    args = parser.parse_args(argv)
    result = run_pipeline(args.case_dir, end_time=args.end_time, mesh_only=args.mesh_only,
                          build_only=args.build_only, mesh_processes=args.mesh_processes,
                          solve_processes=args.solve_processes, max_processes=args.max_processes)
    print(f"Result: {json.dumps(result, indent=2, default=str)}")
    return 0 if result["status"] in ("SUCCESS", "BUILT") else 1


if __name__ == "__main__":
    sys.exit(main())
