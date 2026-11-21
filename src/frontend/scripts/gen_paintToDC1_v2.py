#!/usr/bin/env python3
"""Generate paintToDC1.cfdproj matching the hand-drawn data center layout.

Layout from drawing:
  - Room with 4 walls
  - 4 ACUs: 2 left (supply→+X), 2 right (supply←-X)
  - 4 rack columns × 6 racks each = 24 racks
  - Col1(→) | Hot Aisle 1 | Col2(←) | Cold Aisle | Col3(→) | Hot Aisle 2 | Col4(←)
  - 4 baffles: 2 per hot aisle (south + north side panels, no top lid)
  - Floor, Ceiling matching room dimensions (no raised floor)
"""

import json, copy, uuid

# ── Reference templates (from Base_coldAisleIso.cfdproj) ──────────────
with open("Base_coldAisleIso.cfdproj") as f:
    ref = json.load(f)

def find_ref(kind):
    for m in ref["models"]:
        if m["kind"] == kind:
            return m
    raise KeyError(kind)

REF_RACK   = find_ref("rack")
REF_ACU    = find_ref("acu")
REF_WALL   = find_ref("wall")
REF_FLOOR  = find_ref("floor")
REF_BAFFLE = find_ref("baffle")

# ── Room parameters ───────────────────────────────────────────────────
ROOM_X     = 14.0    # width (m)
ROOM_Z     = 7.0     # depth (m)
ROOM_H     = 3.0     # ceiling height (m)
RAISED_Y   = 0.0     # no raised floor in this design

# Rack parameters (default)
RACK_W = 0.8   # width  (along column, Z direction after rotation)
RACK_D = 1.2   # depth  (perpendicular to column, X direction after rotation)
RACK_H = 2.2   # height
RACKS_PER_COL = 6

# Quaternions
QUAT_POS90Y = {"x": 0, "y": 0.7071067811865476, "z": 0, "w":  0.7071067811865476}  # +90°Y → front→-X (→)
QUAT_NEG90Y = {"x": 0, "y": 0.7071067811865476, "z": 0, "w": -0.7071067811865476}  # -90°Y → front→+X (←)
QUAT_0      = {"x": 0, "y": 0, "z": 0, "w": 1}
QUAT_180Y   = {"x": 0, "y": 1, "z": 0, "w": 0}  # 180° Y
QUAT_NEG90Y_WALL = {"x": 0, "y": -0.7071067811865475, "z": 0, "w": 0.7071067811865476}  # for wall facing +X

# ── Layout X positions ────────────────────────────────────────────────
# ACU depth=1.0m, rack depth=1.2m
# Wall gaps, aisle widths:
ACU_D        = 1.0
COLD_AISLE   = 1.2
HOT_AISLE    = 1.2
CENTER_AISLE = 1.8
WALL_GAP     = 0.3

# X positions (left to right):
X_LEFT_WALL   = 0.0
X_ACU_L       = WALL_GAP                                 # 0.3
X_COLD1_START = X_ACU_L + ACU_D                           # 1.3
X_COL1_START  = X_COLD1_START + COLD_AISLE                # 2.5
X_COL1_END    = X_COL1_START + RACK_D                     # 3.7
X_HA1_END     = X_COL1_END + HOT_AISLE                    # 4.9
X_COL2_ANCHOR = X_HA1_END + RACK_D                        # 6.1
X_CENTER_END  = X_COL2_ANCHOR + CENTER_AISLE              # 7.9
X_COL3_START  = X_CENTER_END                              # 7.9
X_COL3_END    = X_COL3_START + RACK_D                     # 9.1
X_HA2_END     = X_COL3_END + HOT_AISLE                    # 10.3
X_COL4_ANCHOR = X_HA2_END + RACK_D                        # 11.5
X_COLD4_END   = X_COL4_ANCHOR + COLD_AISLE                # 12.7
X_ACU_R       = X_COLD4_END                               # 12.7
X_RIGHT_WALL  = X_ACU_R + ACU_D + WALL_GAP               # 14.0

assert abs(X_RIGHT_WALL - ROOM_X) < 0.01, f"X mismatch: {X_RIGHT_WALL} != {ROOM_X}"

# Z positions: 6 racks centered in room
RACK_COLUMN_LEN = RACKS_PER_COL * RACK_W  # 4.8m
Z_RACK_START = (ROOM_Z - RACK_COLUMN_LEN) / 2  # 1.1m
Z_RACK_END   = Z_RACK_START + RACK_COLUMN_LEN  # 5.9m

# ── Model ID counter ─────────────────────────────────────────────────
_mid = [0]
def new_id():
    _mid[0] += 1
    return f"m_{_mid[0]}"

# ── Model builders ───────────────────────────────────────────────────

def make_rack(name, anchor_x, anchor_y, anchor_z, quat):
    m = copy.deepcopy(REF_RACK)
    m["id"]   = new_id()
    m["name"] = name
    m["anchor"] = {"x": anchor_x, "y": anchor_y, "z": anchor_z}
    m["base"]   = {"W": RACK_W, "D": RACK_D, "H": RACK_H}
    m["transform"]["quaternion"] = dict(quat)
    m["enabled"] = True
    return m

def make_acu(name, anchor_x, anchor_y, anchor_z, quat):
    m = copy.deepcopy(REF_ACU)
    m["id"]   = new_id()
    m["name"] = name
    m["anchor"] = {"x": anchor_x, "y": anchor_y, "z": anchor_z}
    m["base"]   = {"W": 1.8, "D": 1.0, "H": 2.0}
    m["transform"]["quaternion"] = dict(quat)
    m["enabled"] = True
    # Fix: Supply should be on the FRONT (side-blow toward cold aisle), not bottom
    faces = m.get("faces", {})
    # Move Supply patches from bottom to front
    if "bottom" in faces and faces["bottom"].get("patches"):
        supply_patches = faces["bottom"]["patches"]
        faces["front"]["patches"] = supply_patches
        faces["bottom"]["patches"] = []
    return m

def make_wall(name, anchor_x, anchor_y, anchor_z, w, h, quat):
    m = copy.deepcopy(REF_WALL)
    m["id"]   = new_id()
    m["name"] = name
    m["anchor"]  = {"x": anchor_x, "y": anchor_y, "z": anchor_z}
    m["base"]    = {"W": w, "D": 0, "H": h}
    m["transform"]["quaternion"] = dict(quat)
    m["enabled"] = True
    return m

def make_floor(name, anchor_x, anchor_y, anchor_z, w, d, enabled=True, opacity=0.6):
    m = copy.deepcopy(REF_FLOOR)
    m["id"]   = new_id()
    m["name"] = name
    m["anchor"]  = {"x": anchor_x, "y": anchor_y, "z": anchor_z}
    m["base"]    = {"W": w, "D": d, "H": 0}
    m["transform"]["quaternion"] = dict(QUAT_0)
    m["enabled"] = enabled
    m["opacity"] = opacity
    # Clear ALL stale patches inherited from REF_FLOOR (raised_floor template)
    for face in m.get("faces", {}).values():
        face["patches"] = []
        face["opacity"] = opacity
        if "wallOpacities" in face:
            face["wallOpacities"] = [opacity]
    return m

def make_baffle(name, anchor_x, anchor_y, anchor_z, w, h, quat):
    m = copy.deepcopy(REF_BAFFLE)
    m["id"]   = new_id()
    m["name"] = name
    m["anchor"]  = {"x": anchor_x, "y": anchor_y, "z": anchor_z}
    m["base"]    = {"W": w, "D": 0, "H": h}
    m["transform"]["quaternion"] = dict(quat)
    m["enabled"] = True
    return m

# ── Build model list ─────────────────────────────────────────────────
models = []

# ── 24 Racks (4 columns × 6 racks) ──────────────────────────────────

# Col1 (→ direction): quat +90°Y, front faces -X
# With +90°Y: anchor is at the -X edge of rack, D extends in +X, W extends in -Z
# So anchor.z should be at the TOP of each rack slot
for i in range(RACKS_PER_COL):
    z = Z_RACK_START + (i + 1) * RACK_W   # anchor at Z_top of each slot
    models.append(make_rack(
        f"Rack_Col1_{i+1}", X_COL1_START, RAISED_Y, z, QUAT_POS90Y
    ))

# Col2 (← direction): quat -90°Y, front faces +X
# With -90°Y: D extends in -X, W extends in +Z
# anchor.z at the BOTTOM of each slot
for i in range(RACKS_PER_COL):
    z = Z_RACK_START + i * RACK_W
    models.append(make_rack(
        f"Rack_Col2_{i+1}", X_COL2_ANCHOR, RAISED_Y, z, QUAT_NEG90Y
    ))

# Col3 (→ direction): same as Col1
for i in range(RACKS_PER_COL):
    z = Z_RACK_START + (i + 1) * RACK_W
    models.append(make_rack(
        f"Rack_Col3_{i+1}", X_COL3_START, RAISED_Y, z, QUAT_POS90Y
    ))

# Col4 (← direction): same as Col2
for i in range(RACKS_PER_COL):
    z = Z_RACK_START + i * RACK_W
    models.append(make_rack(
        f"Rack_Col4_{i+1}", X_COL4_ANCHOR, RAISED_Y, z, QUAT_NEG90Y
    ))

# ── 4 ACUs ───────────────────────────────────────────────────────────
# Left ACUs: supply toward +X, use -90°Y quaternion
# With -90°Y: D(1.0) extends in -X from anchor, W(1.8) extends in +Z
ACU_Z_TOP = 1.5   # top ACU Z position
ACU_Z_BOT = 4.0   # bottom ACU Z position

models.append(make_acu("ACU_Left_1", X_ACU_L + ACU_D, RAISED_Y, ACU_Z_TOP, QUAT_NEG90Y))
models.append(make_acu("ACU_Left_2", X_ACU_L + ACU_D, RAISED_Y, ACU_Z_BOT, QUAT_NEG90Y))

# Right ACUs: supply toward -X, use +90°Y quaternion
# With +90°Y: D(1.0) extends in +X from anchor, W(1.8) extends in -Z
ACU_R_Z_TOP = 3.3   # adjusted to avoid overlap
ACU_R_Z_BOT = 5.8
models.append(make_acu("ACU_Right_1", X_ACU_R, RAISED_Y, ACU_R_Z_TOP, QUAT_POS90Y))
models.append(make_acu("ACU_Right_2", X_ACU_R, RAISED_Y, ACU_R_Z_BOT, QUAT_POS90Y))

# ── 4 Walls ──────────────────────────────────────────────────────────
WALL_H = ROOM_H  # walls go full height

# South wall (Z=0, along X): W=ROOM_X, facing +Z (default orientation)
models.append(make_wall("Room_Wall_S", 0, 0, 0, ROOM_X, WALL_H, QUAT_0))

# East wall (X=ROOM_X, along Z): W=ROOM_Z, rotated -90°Y
models.append(make_wall("Room_Wall_E", ROOM_X, 0, 0, ROOM_Z, WALL_H, QUAT_NEG90Y_WALL))

# North wall (X=ROOM_X, Z=ROOM_Z, along -X): W=ROOM_X, rotated 180°Y
models.append(make_wall("Room_Wall_N", ROOM_X, 0, ROOM_Z, ROOM_X, WALL_H, QUAT_180Y))

# West wall (X=0, Z=ROOM_Z, along -Z): W=ROOM_Z, rotated +90°Y
QUAT_POS90Y_WALL = {"x": 0, "y": 0.7071067811865475, "z": 0, "w": 0.7071067811865476}
models.append(make_wall("Room_Wall_W", 0, 0, ROOM_Z, ROOM_Z, WALL_H, QUAT_POS90Y_WALL))

# ── 2 Floors (no raised floor in this design) ────────────────────────
# Floor at Y=0
models.append(make_floor("Room_Floor", 0, 0, 0, ROOM_X, ROOM_Z, enabled=True))

# Ceiling at Y=ROOM_H
models.append(make_floor("Room_Ceiling", 0, ROOM_H, 0, ROOM_X, ROOM_Z, enabled=True))

# ── 4 Baffles (hot aisle side panels, no top lid) ────────────────────
# Hot Aisle 1: between Col1 end (X=3.7) and Col2 start (X=4.9)
# Baffles at Z=Z_RACK_START and Z=Z_RACK_END, spanning X from 3.7 to 4.9

# HA1 South baffle (at Z=Z_RACK_START, facing +Z, no rotation)
models.append(make_baffle(
    "Baffle_HA1_South", X_COL1_END, RAISED_Y, Z_RACK_START,
    HOT_AISLE, RACK_H, QUAT_0
))

# HA1 North baffle (at Z=Z_RACK_END, facing -Z, 180° rotation)
models.append(make_baffle(
    "Baffle_HA1_North", X_HA1_END, RAISED_Y, Z_RACK_END,
    HOT_AISLE, RACK_H, QUAT_180Y
))

# Hot Aisle 2: between Col3 end (X=9.1) and Col4 start (X=10.3)
models.append(make_baffle(
    "Baffle_HA2_South", X_COL3_END, RAISED_Y, Z_RACK_START,
    HOT_AISLE, RACK_H, QUAT_0
))

models.append(make_baffle(
    "Baffle_HA2_North", X_HA2_END, RAISED_Y, Z_RACK_END,
    HOT_AISLE, RACK_H, QUAT_180Y
))

# ── Assemble project ────────────────────────────────────────────────
project = copy.deepcopy(ref)
project["models"] = models

# Update room settings
project["cfdRoom"]["height"] = ROOM_H
project["cfdRoom"]["polygon"] = [
    {"x": 0, "z": 0},
    {"x": ROOM_X, "z": 0},
    {"x": ROOM_X, "z": ROOM_Z},
    {"x": 0, "z": ROOM_Z},
]
project["cfdRoom"]["locationInMesh"] = {
    "x": 2.0,
    "y": 0.5,
    "z": ROOM_Z / 2,
}

# Fix roomPolygon (2D editor uses this, not cfdRoom.polygon)
project["roomPolygon"] = {
    "vertices": [[0, 0], [ROOM_X, 0], [ROOM_X, ROOM_Z], [0, ROOM_Z]],
    "height": ROOM_H,
}

# Fix bounds to match room dimensions
project["bounds"] = {
    "xMin": {"enabled": False, "value": 0},
    "xMax": {"enabled": False, "value": ROOM_X},
    "yMin": {"enabled": True,  "value": 0},
    "yMax": {"enabled": False, "value": ROOM_H + 0.6},
    "zMin": {"enabled": False, "value": 0},
    "zMax": {"enabled": False, "value": ROOM_Z},
}

out_path = "paintToDC1.cfdproj"
with open(out_path, "w") as f:
    json.dump(project, f, indent=2, ensure_ascii=False)

print(f"Generated {out_path}")
print(f"  Room: {ROOM_X}m × {ROOM_Z}m × {ROOM_H}m")
print(f"  No raised floor (all equipment at Y=0)")
print(f"  Models: {len(models)} total")
print(f"    Racks: {sum(1 for m in models if m['kind']=='rack')} (4 cols × {RACKS_PER_COL})")
print(f"    ACUs:  {sum(1 for m in models if m['kind']=='acu')}")
print(f"    Walls: {sum(1 for m in models if m['kind']=='wall')}")
print(f"    Floors:{sum(1 for m in models if m['kind']=='floor')}")
print(f"    Baffles:{sum(1 for m in models if m['kind']=='baffle')}")
print(f"\n  X layout:")
print(f"    Left wall:  0")
print(f"    ACU_L:      {X_ACU_L:.1f} - {X_ACU_L+ACU_D:.1f}")
print(f"    Col1:       {X_COL1_START:.1f} - {X_COL1_END:.1f}")
print(f"    Hot Aisle1: {X_COL1_END:.1f} - {X_HA1_END:.1f}")
print(f"    Col2:       {X_HA1_END:.1f} - {X_COL2_ANCHOR:.1f}")
print(f"    Center:     {X_COL2_ANCHOR:.1f} - {X_CENTER_END:.1f}")
print(f"    Col3:       {X_COL3_START:.1f} - {X_COL3_END:.1f}")
print(f"    Hot Aisle2: {X_COL3_END:.1f} - {X_HA2_END:.1f}")
print(f"    Col4:       {X_HA2_END:.1f} - {X_COL4_ANCHOR:.1f}")
print(f"    ACU_R:      {X_ACU_R:.1f} - {X_ACU_R+ACU_D:.1f}")
print(f"    Right wall: {ROOM_X}")
print(f"\n  Z layout: racks from {Z_RACK_START:.1f} to {Z_RACK_END:.1f}")
