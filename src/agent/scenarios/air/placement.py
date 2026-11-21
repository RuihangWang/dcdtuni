"""Placement math for the air-v1 compiler (:mod:`agent.scenarios.air.compile`).

cfdproj convention (verified against the exported STLs, and the anchor the
``placeModel`` contract verb takes): ``geometry.location`` is the MIN corner in
the object's own frame, the world box is ``anchor + R_y(yaw) . [0,W] x [0,H] x [0,D]``, the
intake is the local -z face and the exhaust the local +z face. The scene is
y-up; the DSL floor coordinates (X, Y) map to scene (x, z).
"""
from __future__ import annotations

import itertools
import math
from typing import Dict, Tuple

from agent.scenarios.air.spec import Footprint, Row


def yaw_quaternion(deg: float) -> Dict[str, float]:
    h = math.radians(deg) / 2.0
    return {"x": 0.0, "y": math.sin(h), "z": 0.0, "w": math.cos(h)}


def rotate_y(v: Tuple[float, float, float], deg: float) -> Tuple[float, float, float]:
    r = math.radians(deg)
    c, s = math.cos(r), math.sin(r)
    x, y, z = v
    return (x * c + z * s, y, -x * s + z * c)


# Yaw that makes the local -z intake face the row's front side.
_YAW = {("x", "-"): 0.0, ("x", "+"): 180.0, ("y", "+"): 270.0, ("y", "-"): 90.0}


def slot_pose(row: Row, offset_m: float, fp: Footprint, floor_y: float) -> Tuple[Dict[str, float], Dict[str, float]]:
    """Anchor (location) and quaternion of the object whose slot starts ``offset_m`` along the row."""
    yaw = _YAW[(row.axis, row.front)]
    a0, a1 = row.start_m + offset_m, row.start_m + offset_m + fp.width_m
    c0, c1 = row.center_m - fp.depth_m / 2.0, row.center_m + fp.depth_m / 2.0
    if row.axis == "x":
        x_lo, x_hi, z_lo, z_hi = a0, a1, c0, c1
    else:
        x_lo, x_hi, z_lo, z_hi = c0, c1, a0, a1
    # pick the corner that R_y(yaw) maps the local origin onto
    corners = {0.0: (x_lo, z_lo), 180.0: (x_hi, z_hi), 270.0: (x_hi, z_lo), 90.0: (x_lo, z_hi)}
    ax, az = corners[yaw]
    return {"x": ax, "y": floor_y, "z": az}, yaw_quaternion(yaw)


def world_box(location: Dict[str, float], quaternion: Dict[str, float], size: Tuple[float, float, float]):
    """Axis-aligned world bounds ((x0, x1), (y0, y1), (z0, z1)) of a yawed box."""
    yaw = math.degrees(2.0 * math.atan2(quaternion["y"], quaternion["w"])) % 360.0
    W, H, D = size
    pts = [rotate_y(c, yaw) for c in itertools.product((0.0, W), (0.0, H), (0.0, D))]
    xs = [location["x"] + p[0] for p in pts]
    ys = [location["y"] + p[1] for p in pts]
    zs = [location["z"] + p[2] for p in pts]
    return (min(xs), max(xs)), (min(ys), max(ys)), (min(zs), max(zs))


def intake_normal(quaternion: Dict[str, float]) -> Tuple[float, float, float]:
    """World direction the intake face looks towards (local -z)."""
    yaw = math.degrees(2.0 * math.atan2(quaternion["y"], quaternion["w"])) % 360.0
    return rotate_y((0.0, 0.0, -1.0), yaw)


def rewrite_uids(obj, new_uid):
    """Clone uid definitions, dictionary keys and exact uid/path references."""
    mapping={}
    def collect(value):
        if isinstance(value,dict):
            if isinstance(value.get("uid"),str):
                old=value["uid"]
                if old not in mapping:mapping[old]=new_uid(old)
            for v in value.values():collect(v)
        elif isinstance(value,list):
            for v in value:collect(v)
    collect(obj)
    if len(set(mapping.values()))!=len(mapping):raise ValueError("new uid mapping collides")
    def transform(value):
        if isinstance(value,dict):return {mapping.get(k,k):transform(v) for k,v in value.items()}
        if isinstance(value,list):return [transform(v) for v in value]
        if isinstance(value,str):
            if value in mapping:return mapping[value]
            return ".".join(mapping.get(v,v) for v in value.split("."))
        return value
    return transform(obj)
