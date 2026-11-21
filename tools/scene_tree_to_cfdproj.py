#!/usr/bin/env python3
"""Convert one data room of a scene-tree export (``scene_tree.json``) into a .cfdproj.

The scene tree is the facility model of another data-centre modelling tool:
``DataCenter > Building > Floor > DataRoom-* > {RackRow > Rack > servers/blindPlate,
CRAH, RowPowerPanels, sensors}, hotAisle-*, buliding21, sensors``, sizes in mm,
``position`` the bottom centre of a cuboid, z up, ``rotation`` in degrees about z.

The project is BUILT, not written: the converter turns the room into contract
steps (``window.__dcdtuni`` verbs) and hands them to ``agent.scripted_api.build_project``,
so the saved bytes are the editor's own and the editor's air export checks them.

Frame: X = source x, Y = source z (up), Z = room depth - source y; metres (a proper
rotation, not a mirror). A source rotation r about z is the editor's yaw r + 180.

What becomes what:
  Rack (48U, 10 mm bottom offset)      rack, U layout from ``uSlot``; every server
                                        a fanServer at its ``startU``/``totalU`` on the
                                        rack's slot plane (100 mm setback, 720 mm deep),
                                        exhausting through the editor's fan strip on its
                                        rear face (``addServerPatch``), not the whole face;
                                        empty U blanked on that same plane (the source's
                                        blindPlate; --blanking: typicalSmallGap, the
                                        editor default, or fullBlock, solid)
  CRAH-WD / CRAH-MD (in-row)            ACU; supply grille on the front (cold aisle),
                                        return grille on the back (hot aisle), each inside
                                        a 50 mm side / 150 mm top-bottom frame (assumed)
  RowPowerPanels, buliding21            boxes (columns at the room edge clipped to it)
  hotAisle-*                            containment: a horizontal baffle at the rack top over
                                        the aisle, an end door at each end, and a
                                        panel over every stretch of the aisle's sides
                                        that no rack, ACU or box closes
  *TemperatureSensor                    sensor probes at their positions
  AC-Distribution-Panel (10 mm stubs)   skipped (placeholders without geometry)

The source has no measured loads. Server power is the room's ``ratedPowerKw``
shared out per occupied U (design load); server airflow follows from a 12 K
server temperature rise. Each hot aisle's ACUs together draw what the servers
blowing into it supply. Both are synthetic and named so in the project
description.

Usage (repository root, dcdtuni environment):
  python tools/scene_tree_to_cfdproj.py case/Base201/Ref_data/scene_tree.json \
      --room DataRoom201 --out case/Base201/Base201.cfdproj --name Base201
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

RHO_AIR = 1.2          # kg/m3
CP_AIR = 1005.0        # J/(kg K)
SERVER_DT_K = 12.0     # server temperature rise used to size airflow
CRAH_CODES = ("CRAH-WD", "CRAH-MD")
BOX_CODES = ("RowPowerPanels", "buliding21")
SENSOR_SUFFIX = "TemperatureSensor"
MM = 1e-3
SLOT_INLET_SETBACK = 0.10   # m: rail plane behind the rack front (blanking panels and server fronts)
ACU_GRILLE_SIDE = 0.05      # m: frame left/right of an in-row unit's supply and return grilles (assumed)
ACU_GRILLE_END = 0.15       # m: frame below and above them (assumed; the source gives no grille size)
SERVER_DEPTH = 0.72         # m: the source's servers are 720 mm deep (its 1U units 750 mm, taken as 720)


def ft(node):
    return (node.get("facilityType") or {}).get("code", "")


def find_room(tree, room_code):
    stack = list(tree.get("DataCenter") or [])
    while stack:
        n = stack.pop()
        if n.get("code") == room_code and ft(n).startswith("DataRoom"):
            return n
        stack.extend(n.get("children") or [])
    raise SystemExit(f"no data room with code {room_code!r}")


ROOM_DEPTH = [0.0]   # the room's source y extent in metres, set by convert()


def pose(node):
    """(cx, cz, y0, W, D, H, yaw) in editor metres/degrees; W along the model's own x.

    Source (x, y, z up) -> editor (X, Y up, Z) = (x, z, depth - y): a proper rotation (no
    mirror), so the editor's plan shows the room as the source tool does. A source
    rotation r about z becomes the yaw r + 180: the source's local front is -y, which is
    +Z here, and the editor's yaw 0 faces -Z.
    """
    g = node["geometry"]["size"]
    t = node.get("transform") or {}
    p = t.get("position") or {}
    r = t.get("rotation") or {}
    yaw = (round(float(r.get("z", 0) or 0)) + 180) % 360
    return (p.get("x", 0) * MM, ROOM_DEPTH[0] - p.get("y", 0) * MM, p.get("z", 0) * MM,
            g["x"] * MM, g["y"] * MM, g["z"] * MM, yaw)


def footprint(cx, cz, w, d, yaw):
    """World box [x0, x1, z0, z1] of a centred footprint."""
    if yaw in (90, 270):
        w, d = d, w
    return (cx - w / 2, cx + w / 2, cz - d / 2, cz + d / 2)


def anchor_for(cx, cz, w, d, yaw):
    """The editor's anchor (corner of the front face) for a centred footprint.

    Footprint table (dc-builder): yaw 0 -> x [ax, ax+W], z [az, az+D];
    180 -> x [ax-W, ax], z [az-D, az]; 90 -> x [ax, ax+D], z [az-W, az];
    270 -> x [ax-D, ax], z [az, az+W].
    """
    if yaw == 0:
        return cx - w / 2, cz - d / 2
    if yaw == 180:
        return cx + w / 2, cz + d / 2
    if yaw == 90:
        return cx - d / 2, cz + w / 2
    if yaw == 270:
        return cx + d / 2, cz - w / 2
    raise ValueError(f"yaw {yaw} is not a multiple of 90")


def r6(v):
    return round(float(v), 6)


class Steps:
    def __init__(self):
        self.steps = []

    def __call__(self, verb, *args):
        self.steps.append({"verb": verb, "args": list(args)})
        return f"${len(self.steps) - 1}"


def convert(tree, room_code, name, rows_filter=None, room_height=None, case_path="", blanking="typicalSmallGap"):
    room = find_room(tree, room_code)
    rs = room["geometry"]["size"]
    room_w, room_d = rs["x"] * MM, rs["y"] * MM
    ROOM_DEPTH[0] = room_d
    room_h = room_height if room_height else rs["z"] * MM
    rated_w = float((room.get("commonFields") or {}).get("ratedPowerKw") or 0) * 1000.0

    rows = [c for c in room["children"] if ft(c) == "RackRow"]
    if rows_filter:
        rows = [r for r in rows if r["name"].split("-")[-1] in rows_filter]
    others = [c for c in room["children"] if ft(c) != "RackRow"]

    # ---- inventory ------------------------------------------------------
    racks, crahs, boxes, sensors = [], [], [], []
    row_yaw, crah_flipped = {}, []
    for row in rows:
        yaws = [pose(e)[6] for e in row["children"] if ft(e) == "Rack"]
        row_yaw[row["id"]] = max(set(yaws), key=yaws.count) if yaws else None
        for e in row["children"]:
            code = ft(e)
            if code == "Rack":
                racks.append((row, e))
            elif code in CRAH_CODES:
                crahs.append((row, e))
                if row_yaw[row["id"]] is not None and (pose(e)[6] + 180) % 360 != row_yaw[row["id"]]:
                    crah_flipped.append(e["name"])
            elif code in BOX_CODES:
                boxes.append(e)
            elif code.endswith(SENSOR_SUFFIX):
                sensors.append(e)
    aisles = [c for c in others if ft(c).startswith("hotAisle")]
    for c in others:
        code = ft(c)
        if code in BOX_CODES:
            boxes.append(c)
        elif code.endswith(SENSOR_SUFFIX):
            sensors.append(c)
    if rows_filter:   # keep only what lies near the kept rows
        zs = [pose(e)[1] for _, e in racks + crahs]
        lo, hi = min(zs) - 1.5, max(zs) + 1.5
        aisles = [a for a in aisles if lo <= pose(a)[1] <= hi]
        boxes = [b for b in boxes if lo <= pose(b)[1] <= hi]
        sensors = [s for s in sensors if lo <= pose(s)[1] <= hi]

    def servers_of(rack):
        return [c for c in rack.get("children") or []
                if ft(c) not in ("blindPlate",) and not ft(c).endswith(SENSOR_SUFFIX)
                and (c.get("uSlot") or {}).get("totalU")]

    total_u = sum(s["uSlot"]["totalU"] for _, r in racks for s in servers_of(r))
    w_per_u = rated_w / total_u if total_u else 0.0
    q_per_w = 1.0 / (RHO_AIR * CP_AIR * SERVER_DT_K)

    # ---- hot aisle membership: which aisle each rack/CRAH backs onto ----
    def crah_pose(row, e):
        """An in-row unit stands like its row's racks: supply to the cold aisle, return from the hot one.

        The source's CRAH model faces the other way from its racks (its ``front`` texture is
        the unit's face, at local +y), so a unit set 180 deg from its row's racks is the
        regular case; the few set like their racks are listed in the report.
        """
        cx, cz, y0, w, d, h, _ = pose(e)
        yaw = row_yaw[row["id"]]
        return cx, cz, y0, w, d, h, (pose(e)[6] + 180) % 360 if yaw is None else yaw

    def back_face_z(e, p=None):
        cx, cz, _, w, d, _, yaw = p or pose(e)
        return cz + d / 2 if yaw == 0 else cz - d / 2

    def aisle_of(e, p=None):
        cx, cz, *_ = p or pose(e)
        bz = back_face_z(e, p)
        for i, a in enumerate(aisles):
            ax, az, _, aw, ad, _, _ = pose(a)
            if abs(abs(bz - az) - ad / 2) < 0.02 and ax - aw / 2 - 1e-3 <= cx <= ax + aw / 2 + 1e-3:
                return i
        return None

    aisle_flow = defaultdict(float)
    aisle_crahs = defaultdict(list)
    for _, r in racks:
        i = aisle_of(r)
        u = sum(s["uSlot"]["totalU"] for s in servers_of(r))
        if i is not None:
            aisle_flow[i] += u * w_per_u * q_per_w
    for row, c in crahs:
        aisle_crahs[aisle_of(c, crah_pose(row, c))].append(c["id"])
    unassigned = aisle_crahs.pop(None, [])
    crah_flow = {}
    for i, ids in aisle_crahs.items():
        for cid in ids:
            crah_flow[cid] = aisle_flow[i] / len(ids)
    default_crah_flow = (sum(aisle_flow.values()) / max(1, sum(len(v) for v in aisle_crahs.values()))) or 2.0
    for cid in unassigned:
        crah_flow[cid] = default_crah_flow

    # ---- steps ------------------------------------------------------------
    S = Steps()
    meta = {"name": name, "inputUnit": "m", "roomTemperatureC": 24}
    if case_path:
        meta["casePath"] = case_path
    S("setProjectMeta", meta)
    S("applyRoom", [[0, 0], [r6(room_w), 0], [r6(room_w), r6(room_d)], [0, r6(room_d)]], r6(room_h))

    report = {"room": room_code, "blanking": blanking, "roomSize_m": [room_w, room_d, room_h], "racks": 0, "servers": 0,
              "occupiedU": total_u, "W_per_U": w_per_u, "serverDeltaT_K": SERVER_DT_K,
              "totalPower_W": total_u * w_per_u, "acus": 0, "boxes": 0, "baffles": 0, "sensors": 0,
              "aisleFlow_m3s": {}, "aisleAcus": {f"HotAisle{i + 1}": len(v) for i, v in aisle_crahs.items()},
              "acusWithoutAisle": len(unassigned), "acusSetLikeTheirRacksInSource": crah_flipped,
              "skipped": []}

    # racks
    for row, r in racks:
        cx, cz, y0, w, d, h, yaw = pose(r)
        us = r.get("uSlot") or {}
        n_u = int(us.get("totalU") or 42)
        bottom = float(us.get("firstSlotOffset") or 0) * MM
        unit = 0.04445
        top = max(0.0, h - bottom - n_u * unit)
        ax, az = anchor_for(cx, cz, w, d, yaw)
        uid = S("createDefaultRackModel")
        S("placeModel", uid, {"name": r["name"], "location": {"x": r6(ax), "y": r6(y0), "z": r6(az)},
                              "size": {"W": r6(w), "D": r6(d), "H": r6(h)},
                              "rotationDeg": {"x": 0, "y": yaw, "z": 0}})
        S("setRackHeightMode", uid, "totalHeight")
        S("setRackLayout", uid, "unitCount", n_u)
        S("setRackLayout", uid, "safeMarginBottom", r6(bottom))
        S("setRackLayout", uid, "safeMarginTop", r6(top))
        # The slot plane: where the blanking panels of empty U stand and where every server
        # sits, so panels and server faces are flush on both sides.
        # The source fills every empty U with a blindPlate but gives no leakage:
        # the editor's typicalSmallGap (64 % open, the default) or fullBlock (solid).
        # Base201 with typicalSmallGap: exhaust looped back through the empty U to
        # the inlets (76 C in N14's rear plenum); which is nearer the room is for
        # the site's sensors to say.
        if blanking != "typicalSmallGap":
            S("setModelCooling", uid, "blankingPanelType", blanking)
        S("setRackDoorOffset", uid, "inlet", r6(SLOT_INLET_SETBACK))
        S("setRackDoorOffset", uid, "outlet", r6(max(0.0, d - SLOT_INLET_SETBACK - SERVER_DEPTH)))
        report["racks"] += 1
        for s in sorted(servers_of(r), key=lambda s: s["uSlot"]["startU"]):
            su = s["uSlot"]
            span = int(su["totalU"])
            sv = S("addRackServerAtRange", uid, int(su["startU"]) - 1, span, "fanServer")
            p = span * w_per_u
            S("updateRackServerCfd", uid, sv, "power", round(p, 3))
            S("updateRackServerCfd", uid, sv, "volumeFlowRate", r6(p * q_per_w))
            # the fans blow through a strip of the rear face (the jet into the hot aisle)
            S("addServerPatch", uid, sv, "outlet")
            report["servers"] += 1

    # in-row ACUs
    for row, c in crahs:
        cx, cz, y0, w, d, h, yaw = crah_pose(row, c)
        ax, az = anchor_for(cx, cz, w, d, yaw)
        uid = S("createDefaultAcuModel")
        S("placeModel", uid, {"name": c["name"], "location": {"x": r6(ax), "y": r6(y0), "z": r6(az)},
                              "size": {"W": r6(w), "D": r6(d), "H": r6(h)},
                              "rotationDeg": {"x": 0, "y": yaw, "z": 0}})
        def grille(face):
            # a grille inside the face's frame, not the whole face
            pid = S("addPatch", uid, face, "rect")
            for field, value in (("w", w - 2 * ACU_GRILLE_SIDE), ("h", h - 2 * ACU_GRILLE_END),
                                 ("x", ACU_GRILLE_SIDE), ("y", ACU_GRILLE_END)):
                S("setPatchGeometry", uid, pid, field, r6(value))
            return pid

        sup = grille("front")
        S("updateAcuPatchPhysics", uid, sup, "role", "supply")
        S("updateAcuPatchPhysics", uid, sup, "temperature", 20)
        S("updateAcuPatchPhysics", uid, sup, "volumeFlowRate", r6(crah_flow[c["id"]]))
        ret = grille("back")
        S("updateAcuPatchPhysics", uid, ret, "role", "return")
        report["acus"] += 1

    # boxes (row power panels, columns); clip to the room
    for b in boxes:
        cx, cz, y0, w, d, h, yaw = pose(b)
        x0, x1, z0, z1 = footprint(cx, cz, w, d, yaw)
        x0, x1, z0, z1 = max(0, x0), min(room_w, x1), max(0, z0), min(room_d, z1)
        b0, b1 = max(0.0, y0), min(room_h, y0 + h)
        if x1 - x0 < 0.01 or z1 - z0 < 0.01 or b1 - b0 < 0.01:
            report["skipped"].append(b["name"])
            continue
        uid = S("createDefaultBaseModel")
        S("placeModel", uid, {"name": f'{b["name"]}_{b["code"]}', "location": {"x": r6(x0), "y": r6(b0), "z": r6(z0)},
                              "size": {"W": r6(x1 - x0), "D": r6(z1 - z0), "H": r6(b1 - b0)},
                              "rotationDeg": {"x": 0, "y": 0, "z": 0}})
        report["boxes"] += 1

    # hot-aisle containment
    closers = []   # x-intervals at a z plane closed by a body
    for _, e in racks + crahs:
        cx, cz, _, w, d, _, yaw = pose(e)
        x0, x1, z0, z1 = footprint(cx, cz, w, d, yaw)
        closers.append((x0, x1, z0, z1))
    for b in boxes:
        cx, cz, _, w, d, _, yaw = pose(b)
        closers.append(footprint(cx, cz, w, d, yaw))

    def baffle(name, x0, z0, length, height, along_x, y0=0.0):
        uid = S("createDefaultBaffleModel")
        S("placeModel", uid, {"name": name, "location": {"x": r6(x0), "y": r6(y0), "z": r6(z0)},
                              "size": {"W": r6(length), "D": 0, "H": r6(height)},
                              "rotationDeg": {"x": 0, "y": 0 if along_x else -90, "z": 0}})
        report["baffles"] += 1

    for i, a in enumerate(aisles):
        ax, az, ay0, aw, ad, ah, _ = pose(a)
        x0, x1, z0, z1 = ax - aw / 2, ax + aw / 2, az - ad / 2, az + ad / 2
        tag = f"HotAisle{i + 1}"
        # roof: the aisle's rectangle minus the footprint of any box (column) standing through it
        roof_y = ay0 + ah
        cuts = []
        for b in boxes:
            bx, bz, by0, bw, bd, bh, byaw = pose(b)
            c = footprint(bx, bz, bw, bd, byaw)
            if by0 < roof_y < by0 + bh and c[0] < x1 and c[1] > x0 and c[2] < z1 and c[3] > z0:
                cuts.append((max(x0, c[0]), min(x1, c[1]), max(z0, c[2]), min(z1, c[3])))
        xs = sorted({x0, x1, *[c[0] for c in cuts], *[c[1] for c in cuts]})
        pieces = []
        for xa, xb in zip(xs, xs[1:]):
            covered = sorted((c[2], c[3]) for c in cuts if c[0] <= xa + 1e-6 and c[1] >= xb - 1e-6)
            cur = z0
            for c0, c1 in covered + [(z1, z1)]:
                if c0 > cur + 1e-6:
                    if pieces and abs(pieces[-1][1] - xa) < 1e-6 and pieces[-1][2:] == (cur, c0):
                        pieces[-1] = (pieces[-1][0], xb, cur, c0)   # merge with the previous x strip
                    else:
                        pieces.append((xa, xb, cur, c0))
                cur = max(cur, c1)
        for k, (xa, xb, za, zb) in enumerate(pieces):
            # a horizontal baffle: W along x, its H turned by +90 deg about x onto +z
            roof = S("createDefaultBaffleModel")
            S("placeModel", roof, {"name": f"{tag}_Roof" + (f"_{k + 1}" if len(pieces) > 1 else ""),
                                   "location": {"x": r6(xa), "y": r6(roof_y), "z": r6(za)},
                                   "size": {"W": r6(xb - xa), "D": 0, "H": r6(zb - za)},
                                   "rotationDeg": {"x": 90, "y": 0, "z": 0}})
            report["baffles"] += 1
        # end doors: a sheet across the aisle at each end (along z)
        baffle(f"{tag}_Door_W", x0, z0, ad, ah, along_x=False)
        baffle(f"{tag}_Door_E", x1, z0, ad, ah, along_x=False)
        # sides: what no body closes along z = z0 and z = z1
        for side, zp in (("S", z0), ("N", z1)):
            cover = sorted((max(x0, c[0]), min(x1, c[1])) for c in closers
                           if c[2] - 1e-3 <= zp <= c[3] + 1e-3 and c[1] > x0 and c[0] < x1)
            cur = x0
            gaps = []
            for c0, c1 in cover:
                if c0 > cur + 1e-3:
                    gaps.append((cur, c0))
                cur = max(cur, c1)
            if cur < x1 - 1e-3:
                gaps.append((cur, x1))
            for k, (g0, g1) in enumerate(gaps):
                baffle(f"{tag}_Side{side}_{k + 1}", g0, zp, g1 - g0, ah, along_x=True)
        report["aisleFlow_m3s"][tag] = aisle_flow.get(i, 0.0)

    # sensors
    for s in sensors:
        cx, cz, y0, *_ = pose(s)
        x = min(max(cx, 0.05), room_w - 0.05)
        z = min(max(cz, 0.05), room_d - 0.05)
        pid = S("addSensorProbe", r6(x), r6(min(y0, room_h - 0.05)), r6(z))
        S("setSensorProbe", pid, {"name": f'{s["code"]}'})
        report["sensors"] += 1
    for c in others:
        if ft(c) == "AC-Distribution-Panel":
            report["skipped"].append(c["name"])

    # run configuration
    S("setMeshConfig", "meshEngine", "dcCart")
    S("setMeshConfig", "locationInMesh", {"x": r6(room_w / 2 + 0.13), "y": r6(room_h - 0.4), "z": r6(0.6)})
    S("setSolverType", "steady")
    S("setSolverConfig", "endTime", 3000)
    S("setSolverConfig", "writeInterval", 500)
    S("setProjectDescription",
      f"Converted from scene_tree.json ({room['name']}, {room_code}) by tools/scene_tree_to_cfdproj.py. "
      f"No measured loads in the source: server power = room ratedPowerKw {rated_w / 1000:.0f} kW shared per "
      f"occupied U ({w_per_u:.1f} W/U, synthetic design load); server airflow from a {SERVER_DT_K:.0f} K rise; "
      f"in-row ACUs supply 20 C through 0.50 x 1.95 m grilles (50/150 mm frame, assumed) and each hot "
      f"aisle's ACUs draw its servers' airflow (synthetic).")
    S("setInputUnit", "mm")
    return S.steps, report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scene_tree")
    ap.add_argument("--room", required=True, help="the data room's code, e.g. DataRoom201")
    ap.add_argument("--out", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--rows", default="", help="comma-separated row letters to keep (testing)")
    ap.add_argument("--steps-out", default="", help="also write the step list here")
    ap.add_argument("--extra-steps", default="", help="JSON list of steps appended after the conversion")
    ap.add_argument("--bounds-out", default="", help="write the built models' world boxes (modelBounds) here")
    ap.add_argument("--blanking", default="typicalSmallGap", choices=("typicalSmallGap", "fullBlock", "fullyOpen"),
                    help="blanking panels of empty U (the source gives none of their leakage)")
    args = ap.parse_args(argv)

    tree = json.loads(Path(args.scene_tree).read_text(encoding="utf-8"))
    rows = {r.strip() for r in args.rows.split(",") if r.strip()} or None
    out = Path(args.out).resolve()
    try:
        case_path = str(out.parent.relative_to(REPO))
    except ValueError:
        case_path = str(out.parent)
    steps, report = convert(tree, args.room, args.name, rows, case_path=case_path, blanking=args.blanking)
    if args.extra_steps:
        steps += json.loads(Path(args.extra_steps).read_text())
    steps.append({"verb": "modelBounds", "args": []})
    if args.steps_out:
        Path(args.steps_out).write_text(json.dumps(steps, ensure_ascii=False, indent=1))
    print(json.dumps(report, ensure_ascii=False, indent=1))

    from agent.scripted_api import build_project
    out.parent.mkdir(parents=True, exist_ok=True)
    result = build_project(steps, out=str(out), overwrite=True, timeout=3600.0)
    keep = {k: result.get(k) for k in ("status", "failure", "out", "modelCount", "selfCheck")}
    print(json.dumps(keep, ensure_ascii=False, indent=1, default=str))
    bounds = (result.get("steps") or [{}])[-1].get("result") if result.get("steps") else None
    if bounds and args.bounds_out:
        Path(args.bounds_out).write_text(json.dumps(bounds, indent=1))
    return 0 if result.get("status") == "succeeded" and (result.get("selfCheck") or {}).get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
