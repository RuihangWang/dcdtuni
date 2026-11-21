#!/usr/bin/env python3
"""
Batch-generate server_power.csv, ACU.csv, and prompt.md for all paper_experiment directories.
Reads *.cfdproj files from each case's label/ directory.
Skips case_10_dense_A (already done).
"""

import json
import math
import csv
import os
import glob
import io
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "src/frontend/scripts"))
from cfdproj_migrate import load_cfdproj as load_cfdproj_v3

DEMO_DIR = os.path.dirname(os.path.abspath(__file__))
SKIP_CASES = {"case_10_dense_A"}


def load_cfdproj(case_dir):
    """Load the first .cfdproj file from case_dir/label/ (auto-migrated to v3)."""
    label_dir = os.path.join(case_dir, "label")
    projs = glob.glob(os.path.join(label_dir, "*.cfdproj"))
    if not projs:
        return None
    return load_cfdproj_v3(projs[0])


def _room(data):
    """v3 meta.room (always a dict)."""
    return (data.get("meta", {}) or {}).get("room", {}) or {}


def _sim_flat(data):
    """Flat cfdRoom-equivalent: simulation merged with the two keys
    (height, roomTemperature) that v3 relocates to meta.room."""
    flat = dict(data.get("simulation", {}) or {})
    room = _room(data)
    if room.get("height") is not None:
        flat["height"] = room["height"]
    if room.get("temperature") is not None:
        flat["roomTemperature"] = room["temperature"]
    return flat


def quat_to_y_rotation_deg(q):
    """Convert quaternion {x,y,z,w} to Y-axis rotation in degrees."""
    qy = q.get("y", 0)
    qw = q.get("w", 1)
    return 2 * math.atan2(qy, qw) * 180 / math.pi


def round_val(v, decimals=4):
    """Round a numeric value, return empty string if None."""
    if v is None:
        return ""
    return round(v, decimals)


# ---------------------------------------------------------------------------
# 1. server_power.csv
# ---------------------------------------------------------------------------
def generate_server_power_csv(data, case_name):
    """Generate server_power.csv content from cfdproj data."""
    rows = []
    for m in data.get("construction", []):
        if m.get("kind") != "rack":
            continue
        rack_name = m["name"]
        rack_obj = m.get("rack", {})
        cfd = m.get("cfd", {})
        equipments = rack_obj.get("equipments", [])
        servers = cfd.get("servers", [])

        eq_map = {e["id"]: e for e in equipments}

        for srv in servers:
            eq_id = srv.get("equipmentId")
            eq = eq_map.get(eq_id, {})
            power = srv.get("power", 0)
            flow_rate = srv.get("volumeFlowRate")

            rows.append({
                "rack_name": rack_name,
                "equipment_name": eq.get("name", ""),
                "equipment_kind": eq.get("kind", ""),
                "start_U": eq.get("startUnit", ""),
                "span_U": eq.get("spanUnits", ""),
                "power_W": power if power is not None else "",
                "flowRate_m3s": round_val(flow_rate) if flow_rate else "",
            })

    # Sort by rack_name then start_U
    def sort_key(r):
        su = r["start_U"]
        return (r["rack_name"], su if isinstance(su, int) else 0)

    rows.sort(key=sort_key)
    return rows


def write_server_power_csv(rows, output_path):
    cols = ["rack_name", "equipment_name", "equipment_kind",
            "start_U", "span_U", "power_W", "flowRate_m3s"]
    with open(output_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow(r)


# ---------------------------------------------------------------------------
# 2. ACU.csv
# ---------------------------------------------------------------------------
def generate_acu_csv(data, case_name):
    """Generate ACU.csv rows from cfdproj data."""
    rows = []
    for m in data.get("construction", []):
        cfd = m.get("cfd", {})
        if cfd.get("role") != "acu":
            continue

        name = m["name"]
        anchor = m.get("anchor", {})
        base = m.get("base", {})
        quat = m.get("transform", {}).get("quaternion", {"x": 0, "y": 0, "z": 0, "w": 1})

        orientation_deg = round(quat_to_y_rotation_deg(quat), 1)

        # Extract supply/return info from patchPhysics
        pp = cfd.get("patchPhysics", {})
        supply_temp = ""
        supply_flow = ""
        supply_vel = ""
        return_face = ""

        # Find supply and return patch IDs
        supply_patch_id = None
        return_patch_id = None
        for pid, pinfo in pp.items():
            role = pinfo.get("role", "")
            if "supply" in role or "inlet" in role:
                supply_patch_id = pid
                supply_temp = pinfo.get("temperature", "")
                supply_flow = round_val(pinfo.get("volumeFlowRate"))
                supply_vel = round_val(pinfo.get("velocity"))
            elif "return" in role or "outlet" in role:
                return_patch_id = pid

        # Map patch IDs to faces to find return face
        faces = m.get("faces", {})
        for fname, fdata in faces.items():
            patches = fdata.get("patches", [])
            for p in patches:
                if p.get("id") == return_patch_id:
                    return_face = fname
                    break

        rows.append({
            "acu_name": name,
            "pos_x": round_val(anchor.get("x", 0)),
            "pos_y": round_val(anchor.get("y", 0)),
            "pos_z": round_val(anchor.get("z", 0)),
            "size_W": round_val(base.get("W", 0)),
            "size_D": round_val(base.get("D", 0)),
            "size_H": round_val(base.get("H", 0)),
            "orientation_deg": orientation_deg,
            "supply_temp_C": supply_temp,
            "supply_flowRate_m3s": supply_flow,
            "supply_velocity_ms": supply_vel,
            "return_face": return_face,
        })

    return rows


def write_acu_csv(rows, output_path):
    cols = ["acu_name", "pos_x", "pos_y", "pos_z",
            "size_W", "size_D", "size_H", "orientation_deg",
            "supply_temp_C", "supply_flowRate_m3s", "supply_velocity_ms",
            "return_face"]
    with open(output_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow(r)


# ---------------------------------------------------------------------------
# 3. prompt.md
# ---------------------------------------------------------------------------
def compute_room_dims(data):
    """Compute room dimensions from roomPolygon vertices."""
    rp = _room(data).get("polygon") or {}
    verts = rp.get("vertices", [])
    height = rp.get("height", _room(data).get("height", 3.0))

    if not verts:
        return 0, 0, height

    xs = [v[0] for v in verts]
    zs = [v[1] for v in verts]
    width_x = round(max(xs) - min(xs), 1)
    depth_z = round(max(zs) - min(zs), 1)
    return width_x, depth_z, height


def detect_raised_floor(data):
    """Detect raised floor and return its height (0 if none)."""
    floor_models = [m for m in data.get("construction", []) if m.get("kind") == "floor"]
    if not floor_models:
        return 0

    # Find the maximum anchor.y among floor models — that's the ceiling
    max_y = max(m.get("anchor", {}).get("y", 0) for m in floor_models)

    for m in floor_models:
        ay = m.get("anchor", {}).get("y", 0)
        name = m.get("name", "").lower()
        # A floor with anchor.y > 0 that is NOT the ceiling (by name or by being the highest)
        is_ceiling = (
            "ceiling" in name
            or "top" in name
            or (ay > 0 and abs(ay - max_y) < 0.01)
        )
        if ay > 0.05 and not is_ceiling:
            return round(ay, 2)
    return 0


def analyze_racks(data, server_rows):
    """Analyze rack layout: count, rows, orientation, dimensions."""
    racks = [m for m in data.get("construction", []) if m.get("kind") == "rack"]
    if not racks:
        return {}

    # Rack dimensions (take first)
    r0 = racks[0]
    base = r0.get("base", {})
    rack_w = round(base.get("W", 0.6), 2)
    rack_d = round(base.get("D", 1.2), 2)
    rack_h = round(base.get("H", 2.2), 2)

    # Group racks by Z position to identify rows
    rack_positions = []
    for r in racks:
        a = r.get("anchor", {})
        q = r.get("transform", {}).get("quaternion", {"x": 0, "y": 0, "z": 0, "w": 1})
        deg = quat_to_y_rotation_deg(q)
        rack_positions.append({
            "name": r["name"],
            "x": a.get("x", 0),
            "z": a.get("z", 0),
            "deg": round(deg, 1),
        })

    # Group by Z (with tolerance)
    rack_positions.sort(key=lambda r: r["z"])
    z_groups = []
    current_z = None
    current_group = []
    for rp in rack_positions:
        if current_z is None or abs(rp["z"] - current_z) > 0.3:
            if current_group:
                z_groups.append(current_group)
            current_group = [rp]
            current_z = rp["z"]
        else:
            current_group.append(rp)
    if current_group:
        z_groups.append(current_group)

    num_rows = len(z_groups)

    # Determine facing direction pattern
    orientations = set()
    for rp in rack_positions:
        orientations.add(rp["deg"])

    facing_desc = ""
    orientation_list = sorted(orientations)
    if len(orientation_list) == 1:
        deg = orientation_list[0]
        facing_desc = _deg_to_facing(deg)
        facing_pattern = f"所有排出风方向相同：{facing_desc}"
    else:
        facing_pattern = "排间交替面对面排布"

    # Server stats from CSV rows
    powers = [r["power_W"] for r in server_rows if r["power_W"] != "" and r["power_W"] != 0]
    if powers:
        powers = [float(p) for p in powers]
        total_power_w = sum(powers)
        min_power = min(powers)
        max_power = max(powers)
        n_servers = len(powers)
    else:
        total_power_w = 0
        min_power = 0
        max_power = 0
        n_servers = 0

    # Detect U size from span_U
    span_set = set()
    for r in server_rows:
        su = r.get("span_U", "")
        if su != "":
            span_set.add(int(su))

    return {
        "count": len(racks),
        "num_rows": num_rows,
        "facing_pattern": facing_pattern,
        "rack_w": rack_w,
        "rack_d": rack_d,
        "rack_h": rack_h,
        "total_power_w": total_power_w,
        "min_power": min_power,
        "max_power": max_power,
        "n_servers": n_servers,
        "span_units": sorted(span_set) if span_set else [4],
        "racks_per_row": [len(g) for g in z_groups],
    }


def _deg_to_facing(deg):
    """Convert Y-rotation degrees to facing direction string."""
    # Normalize to [0, 360)
    deg = deg % 360
    if deg < 0:
        deg += 360
    # Map degrees to direction
    if 315 <= deg or deg < 45:
        return "朝前(-Z)"
    elif 45 <= deg < 135:
        return "朝左(-X)"
    elif 135 <= deg < 225:
        return "朝后(+Z)"
    elif 225 <= deg < 315:
        return "朝右(+X)"
    return f"{deg}°"


def analyze_acus(acu_rows, data):
    """Analyze ACU placement."""
    if not acu_rows:
        return {}

    count = len(acu_rows)

    # Determine placement description
    room_w, room_z, _ = compute_room_dims(data)

    # Get room polygon bounds
    rp = _room(data).get("polygon") or {}
    verts = rp.get("vertices", [])
    if verts:
        min_x = min(v[0] for v in verts)
        max_x = max(v[0] for v in verts)
        min_z = min(v[1] for v in verts)
        max_z = max(v[1] for v in verts)
    else:
        min_x, max_x = 0, room_w
        min_z, max_z = 0, room_z

    xs = []
    zs = []
    for row in acu_rows:
        x = float(row["pos_x"]) if row["pos_x"] != "" else 0
        z = float(row["pos_z"]) if row["pos_z"] != "" else 0
        xs.append(x)
        zs.append(z)

    # Assign each ACU to its closest wall (no double-counting)
    wall_counts = {"left": 0, "right": 0, "front": 0, "back": 0, "interior": 0}
    wall_thresh = min(min(room_w, room_z) * 0.25, 3.0) if room_w > 0 and room_z > 0 else 3.0

    for x, z in zip(xs, zs):
        dists = {
            "left": abs(x - min_x),
            "right": abs(x - max_x),
            "front": abs(z - min_z),
            "back": abs(z - max_z),
        }
        closest_wall = min(dists, key=dists.get)
        closest_dist = dists[closest_wall]
        if closest_dist < wall_thresh:
            wall_counts[closest_wall] += 1
        else:
            wall_counts["interior"] += 1

    n_wall_acus = sum(v for k, v in wall_counts.items() if k != "interior")
    placement_parts = []

    if wall_counts["interior"] > count * 0.4:
        # Many ACUs are in the interior — distributed layout
        acu_z_sorted = sorted(set(round(z, 0) for z in zs))
        z_groups_acu = []
        cur = None
        for zv in acu_z_sorted:
            if cur is None or zv - cur > 1.5:
                z_groups_acu.append(zv)
                cur = zv
        n_acu_rows = len(z_groups_acu)
        placement_parts.append(f"分 {n_acu_rows} 排均布于机房内")
    else:
        wall_cn = {"left": "左侧", "right": "右侧", "front": "前侧", "back": "后侧"}
        for w in ["left", "right", "front", "back"]:
            if wall_counts[w] > 0:
                placement_parts.append(f"{wall_cn[w]} {wall_counts[w]} 台")

    if not placement_parts:
        placement_parts.append(f"{count} 台")

    # For single ACU or trivial fallback, simplify
    if count <= 1:
        placement_str = ""
    else:
        placement_str = "（" + "、".join(placement_parts) + "）"

    # Supply temp and flow rate
    temps = []
    flows = []
    for row in acu_rows:
        t = row.get("supply_temp_C", "")
        f = row.get("supply_flowRate_m3s", "")
        if t != "":
            temps.append(float(t))
        if f != "":
            flows.append(float(f))

    supply_temp = temps[0] if temps else 15
    total_flow = sum(flows) if flows else 0
    per_flow = flows[0] if flows else 0

    # Return face
    return_faces = set()
    for row in acu_rows:
        rf = row.get("return_face", "")
        if rf:
            return_faces.add(rf)

    face_cn_map = {
        "top": "顶部",
        "bottom": "底部",
        "front": "前方",
        "back": "后方",
        "left": "左侧",
        "right": "右侧",
    }

    # Supply face — collect from all ACUs
    supply_faces = set()
    for m2 in data.get("construction", []):
        cfd2 = m2.get("cfd", {})
        if cfd2.get("role") != "acu":
            continue
        pp = cfd2.get("patchPhysics", {})
        faces = m2.get("faces", {})
        for pid, pinfo in pp.items():
            if "supply" in pinfo.get("role", ""):
                for fname, fdata in faces.items():
                    for p in fdata.get("patches", []):
                        if p.get("id") == pid:
                            supply_faces.add(fname)

    # Simplify face descriptions when patterns are complex
    if supply_faces == return_faces and len(supply_faces) > 1:
        # Same faces for supply and return (alternating ACU pattern)
        combined = "、".join(face_cn_map.get(f, f) for f in sorted(supply_faces))
        sf_desc = f"{combined}交替送风"
        rf_desc = f"{combined}交替回风"
    elif len(supply_faces) > 2 or len(return_faces) > 2:
        # Too many unique faces across ACUs — use generic description
        sf_desc = "侧面送风"
        rf_desc = "侧面回风"
    else:
        rf_parts = [face_cn_map.get(f, f) for f in sorted(return_faces)] if return_faces else ["顶部"]
        rf_desc = "、".join(rf_parts) + "回风"
        sf_parts = [face_cn_map.get(f, f) for f in sorted(supply_faces)] if supply_faces else ["底部"]
        sf_desc = "、".join(sf_parts) + "送风"

    return {
        "count": count,
        "placement": placement_str,
        "supply_temp": supply_temp,
        "per_flow": round(per_flow, 2),
        "total_flow": round(total_flow, 1),
        "return_face_desc": rf_desc,
        "supply_face_desc": sf_desc,
    }


def analyze_baffles(data):
    """Analyze baffle/containment structures."""
    baffles = [m for m in data.get("construction", []) if m.get("kind") == "baffle"]
    if not baffles:
        return {"count": 0, "has_containment": False, "has_partition_wall": False}

    room_w, room_z, room_h = compute_room_dims(data)

    # Check for partition walls (baffles spanning ~full room width)
    partition_walls = []
    containment_baffles = []
    regular_baffles = []

    for b in baffles:
        bw = b.get("base", {}).get("W", 0)
        bh = b.get("base", {}).get("H", 0)
        name = b.get("name", "").lower()

        # A partition wall spans close to the full room dimension
        if bw > max(room_w, room_z) * 0.7 and bh > room_h * 0.7:
            partition_walls.append(b)
        elif "containment" in name or "cold" in name or "hot" in name:
            containment_baffles.append(b)
        else:
            regular_baffles.append(b)

    # Detect containment type from names
    has_cold_aisle = any("cold" in b.get("name", "").lower() for b in baffles)
    has_containment = any("containment" in b.get("name", "").lower() for b in baffles)

    # If no explicit containment naming, check structure
    # Containment baffles are typically shorter than room height
    containment_type = ""
    if has_cold_aisle:
        containment_type = "冷通道隔离（Cold Aisle Containment）"
    elif has_containment:
        containment_type = "通道隔离"

    return {
        "count": len(baffles),
        "has_containment": has_cold_aisle or has_containment,
        "containment_type": containment_type,
        "containment_count": len(containment_baffles),
        "has_partition_wall": len(partition_walls) > 0,
        "partition_walls": partition_walls,
        "regular_baffles": regular_baffles,
    }


def generate_prompt_md(data, case_name, server_rows, acu_rows):
    """Generate the 3-part prompt.md content."""
    lines = []

    # ==================== Part 1: Geometry & Attributes ====================
    lines.append("## Part 1: 几何与属性\n")

    # Room dimensions
    room_w, room_z, room_h = compute_room_dims(data)
    raised_floor_h = detect_raised_floor(data)

    # Round room height for display
    room_h_disp = round(room_h, 1) if room_h == round(room_h, 1) else round(room_h, 2)
    room_desc = f"数据中心机房，{room_w}m × {room_z}m × {room_h_disp}m（层高）"
    if raised_floor_h > 0:
        room_desc += f"，架高地板 {raised_floor_h}m"
    room_desc += "。"
    lines.append(room_desc)
    lines.append("")

    # Rack info
    rack_info = analyze_racks(data, server_rows)
    if rack_info.get("count", 0) > 0:
        n_racks = rack_info["count"]
        n_rows = rack_info["num_rows"]
        facing = rack_info["facing_pattern"]
        rw = rack_info["rack_w"]
        rd = rack_info["rack_d"]
        rh = rack_info["rack_h"]

        # Racks per row description
        racks_per_row = rack_info.get("racks_per_row", [])
        if racks_per_row and len(set(racks_per_row)) == 1 and racks_per_row[0] > 1:
            lines.append(
                f"{n_racks} 台服务器机柜，分为 {n_rows} 排（沿 Z 方向排列），{facing}。"
            )
        elif n_rows > 1:
            lines.append(
                f"{n_racks} 台服务器机柜，分为 {n_rows} 排，{facing}。"
            )
        else:
            lines.append(
                f"{n_racks} 台服务器机柜，{n_rows} 排，{facing}。"
            )

        lines.append(f"机柜尺寸 {rw}m × {rd}m × {rh}m（W×D×H）")

        # Server summary
        n_servers = rack_info["n_servers"]
        total_power_w = rack_info["total_power_w"]
        min_p = rack_info["min_power"]
        max_p = rack_info["max_power"]
        span_units = rack_info["span_units"]
        if len(span_units) <= 3:
            span_str = "/".join(f"{s}U" for s in span_units)
        else:
            span_str = f"{min(span_units)}U-{max(span_units)}U"

        if n_servers > 0:
            # Servers per rack range
            servers_per_rack = {}
            for r in server_rows:
                rn = r["rack_name"]
                servers_per_rack[rn] = servers_per_rack.get(rn, 0) + 1
            spr_values = list(servers_per_rack.values())
            spr_min = min(spr_values) if spr_values else 0
            spr_max = max(spr_values) if spr_values else 0

            if spr_min == spr_max:
                spr_desc = f"每台安装 {spr_min} 台"
            else:
                spr_desc = f"每台安装 {spr_min}-{spr_max} 台"

            total_power_kw = round(total_power_w / 1000, 1)

            if min_p == max_p:
                power_desc = f"单台功耗 {int(min_p)}W"
            else:
                power_desc = f"单台功耗 {int(min_p)}-{int(max_p)}W 不等"

            lines[-1] += f"，{spr_desc} {span_str} 风扇服务器，{power_desc}，总功耗约 {total_power_kw}kW。"
        else:
            lines[-1] += "。"

        lines.append(
            "各机柜的服务器安装位置、功耗及流率详见 [server_power.csv](server_power.csv)。"
        )
        lines.append("")
    else:
        lines.append("无服务器机柜。")
        lines.append("")

    # ACU info
    acu_info = analyze_acus(acu_rows, data)
    if acu_info.get("count", 0) > 0:
        n_acus = acu_info["count"]
        placement = acu_info["placement"]
        supply_temp = acu_info["supply_temp"]
        per_flow = acu_info["per_flow"]
        rf_desc = acu_info["return_face_desc"]
        sf_desc = acu_info["supply_face_desc"]

        lines.append(
            f"{n_acus} 台 ACU{placement}，{sf_desc}、{rf_desc}。"
            f"各 ACU 的位置、尺寸、送风温度及风量详见 [ACU.csv](ACU.csv)。"
        )
        lines.append("")

    # Baffles/containment
    baffle_info = analyze_baffles(data)
    if baffle_info["count"] > 0:
        parts = []
        if baffle_info["has_containment"]:
            n_containment = baffle_info["containment_count"]
            parts.append(
                f"**{baffle_info['containment_type']}**："
                f"挡板 + 隔板围封冷/热通道（共 {baffle_info['count']} 块挡板/隔板）。"
            )
        if baffle_info["has_partition_wall"]:
            pw = baffle_info["partition_walls"]
            n_pw = len(pw)
            # Use dimensions from first partition wall
            pw_w = round(pw[0]["base"]["W"], 1)
            pw_h = round(pw[0]["base"]["H"], 1)
            if n_pw == 1:
                parts.append(f"一面全宽隔墙（{pw_w}m × {pw_h}m）将机房分隔为前后两个区域。")
            else:
                parts.append(
                    f"{n_pw} 面全宽隔墙（{pw_w}m × {pw_h}m）将机房分隔为多个区域。"
                )
        if not baffle_info["has_containment"] and not baffle_info["has_partition_wall"]:
            parts.append(f"共 {baffle_info['count']} 块挡板/隔板。")

        for p in parts:
            lines.append(p)
        lines.append("")

    # ==================== Part 2: Mesh Refinement ====================
    lines.append("## Part 2: 网格加密\n")

    cfd_room = _sim_flat(data)
    mesh_base = cfd_room.get("meshBaseSize", 0.2)
    max_cells = cfd_room.get("maxGlobalCells", 5000000)
    ref_level = cfd_room.get("refinementLevel", [1, 1])
    rack_ref = cfd_room.get("rackRefinementLevel", [2, 2])
    server_ref = cfd_room.get("serverFaceRefinementLevel", [2, 3])
    acu_ref = cfd_room.get("acuRefinementLevel", [1, 2])
    wall_baffle_ref = cfd_room.get("wallBafflesRefinementLevel", [1, 2])
    leakage_ref = cfd_room.get("leakageRefinementLevel", [2, 3])
    non_ortho = cfd_room.get("nonOrthoCorrection", True)
    n_non_ortho = cfd_room.get("nNonOrthogonalCorrectors", 1)

    lines.append(f"- 基础网格尺寸：{mesh_base}m")
    lines.append(f"- 最大全局单元数：{max_cells:,}")
    lines.append(f"- Rack 表面加密：level {rack_ref}")
    lines.append(f"- Server 出风面加密：level {server_ref}（更精细，捕捉射流）")
    lines.append(f"- ACU 表面加密：level {acu_ref}")
    lines.append(f"- 挡板/隔墙表面加密：level {wall_baffle_ref}")
    lines.append(f"- 泄漏区域加密：level {leakage_ref}")
    lines.append(f"- 无额外区域加密（box refinement {ref_level}）")
    if non_ortho:
        lines.append(f"- 非正交校正：开启，{n_non_ortho} 层校正")
    else:
        lines.append("- 非正交校正：关闭")
    lines.append("")

    # ==================== Part 3: Solver Control ====================
    lines.append("## Part 3: 求解控制与输出\n")

    solver_type = cfd_room.get("solverType", "steady")
    solver_type_cn = "稳态（steady）" if solver_type == "steady" else "瞬态（transient）"
    end_time = cfd_room.get("endTime", 1000)
    parallel = cfd_room.get("parallelSolve", True)
    n_cores = cfd_room.get("solveProcess", 4)
    preset = cfd_room.get("solverPreset", "fast")

    residual = cfd_room.get("residualControl", {})
    relaxation = cfd_room.get("relaxationFactors", {})
    write_interval = cfd_room.get("writeInterval", end_time)
    room_temp = cfd_room.get("roomTemperature", 22)

    lines.append(f"- 求解器类型：{solver_type_cn}")

    if solver_type == "steady":
        lines.append(f"- 最大迭代步数：{end_time}")
    else:
        end_time_s = cfd_room.get("endTimeSeconds", 300)
        delta_t = cfd_room.get("deltaT", 0.1)
        lines.append(f"- 终止时间：{end_time_s}s")
        lines.append(f"- 时间步长：{delta_t}s")

    lines.append(f"- 并行核数：{n_cores}")
    lines.append(f"- 求解器预设：{preset}（upwind 格式）")
    lines.append("- 收敛残差阈值：")
    lines.append(f"  - p_rgh: {residual.get('p_rgh', 1e-4)}")
    lines.append(f"  - U: {residual.get('U', 1e-5)}")
    lines.append(f"  - T: {residual.get('T', 1e-5)}")
    lines.append(f"  - k/epsilon: {residual.get('k_epsilon', 1e-4)}")

    lines.append("- 松弛因子：")
    relax_parts = []
    for k in ["p_rgh", "U", "T", "k_epsilon"]:
        v = relaxation.get(k, "")
        if v != "":
            relax_parts.append(f"{k}={v}")
    lines.append(f"  {', '.join(relax_parts)}")

    lines.append(f"- 输出间隔：每 {write_interval} 步写出一次")
    lines.append(f"- 环境温度：{room_temp}°C")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def process_case(case_dir):
    case_name = os.path.basename(case_dir)
    if case_name in SKIP_CASES:
        print(f"  SKIP: {case_name} (already done)")
        return False

    data = load_cfdproj(case_dir)
    if data is None:
        print(f"  SKIP: {case_name} (no cfdproj found)")
        return False

    input_dir = os.path.join(case_dir, "input")
    os.makedirs(input_dir, exist_ok=True)

    # 1. server_power.csv
    server_rows = generate_server_power_csv(data, case_name)
    sp_path = os.path.join(input_dir, "server_power.csv")
    write_server_power_csv(server_rows, sp_path)

    # 2. ACU.csv
    acu_rows = generate_acu_csv(data, case_name)
    acu_path = os.path.join(input_dir, "ACU.csv")
    write_acu_csv(acu_rows, acu_path)

    # 3. prompt.md
    prompt_text = generate_prompt_md(data, case_name, server_rows, acu_rows)
    prompt_path = os.path.join(input_dir, "prompt.md")
    with open(prompt_path, "w") as f:
        f.write(prompt_text)

    # Summary
    n_racks = sum(1 for m in data.get("construction", []) if m.get("kind") == "rack")
    n_acus = len(acu_rows)
    n_servers = len(server_rows)
    total_power = sum(float(r["power_W"]) for r in server_rows if r["power_W"] != "")
    print(
        f"  OK: {case_name} — "
        f"{n_racks} racks, {n_servers} servers ({total_power/1000:.1f}kW), "
        f"{n_acus} ACUs"
    )
    return True


def main():
    case_dirs = sorted(glob.glob(os.path.join(DEMO_DIR, "case_*")))
    print(f"Found {len(case_dirs)} case directories\n")

    processed = 0
    for case_dir in case_dirs:
        if os.path.isdir(case_dir):
            if process_case(case_dir):
                processed += 1

    print(f"\nDone: {processed} cases processed.")


if __name__ == "__main__":
    main()
