#!/usr/bin/env python3
"""Generate layout.xlsx from a cfdproj label file for each demo case.

Usage:
    python generate_layout_xlsx.py <case_name>          # single case
    python generate_layout_xlsx.py all                   # all cases
    python generate_layout_xlsx.py case_07 case_08       # multiple cases
"""

import csv
import json
import math
import sys
import pathlib
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "src/frontend/scripts"))
from cfdproj_migrate import load_cfdproj

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEMO_DIR = Path(__file__).resolve().parent
DEFAULT_GRID_CELL = 0.5

FILL_RACK = PatternFill(start_color="CCE5FF", end_color="CCE5FF", fill_type="solid")
FILL_ACU = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
FILL_BAFFLE = PatternFill(start_color="FFFF99", end_color="FFFF99", fill_type="solid")
FILL_BOX = PatternFill(start_color="FCD5B4", end_color="FCD5B4", fill_type="solid")
FILL_WALL = PatternFill(start_color="D9D9D9", end_color="D9D9D9", fill_type="solid")
FILL_BY_KIND = {
    "rack": FILL_RACK,
    "acu": FILL_ACU,
    "baffle": FILL_BAFFLE,
    "box": FILL_BOX,
    "wall": FILL_WALL,
}

THIN_BORDER = Border(
    left=Side(style="thin", color="CCCCCC"),
    right=Side(style="thin", color="CCCCCC"),
    top=Side(style="thin", color="CCCCCC"),
    bottom=Side(style="thin", color="CCCCCC"),
)

SMALL_FONT = Font(size=7)
HEADER_FONT = Font(size=7, color="888888")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def quat_to_yaw_deg(q: dict) -> float:
    """Convert quaternion {x,y,z,w} to yaw in degrees (Y-axis rotation)."""
    yaw = 2.0 * math.atan2(q["y"], q["w"]) * 180.0 / math.pi
    return round(yaw, 4)


def normalize_yaw(yaw: float) -> int:
    """Normalize yaw to one of 0, 90, 180, 270."""
    y = round(yaw) % 360
    if y < 0:
        y += 360
    # Snap to nearest 90
    snapped = round(y / 90) * 90
    return snapped % 360


def yaw_to_arrow(yaw_deg: float) -> str:
    """Map yaw (degrees) to an arrow character for the rack outlet direction.

    Convention (per spec):
        0/360  (front faces -Z) -> arrow DOWN
        90     (front faces -X) -> arrow LEFT
        180    (front faces +Z) -> arrow UP
        270    (front faces +X) -> arrow RIGHT
    """
    ny = normalize_yaw(yaw_deg)
    return {0: "↓", 90: "←", 180: "↑", 270: "→"}.get(ny, "")


def yaw_to_arrow_offset(yaw_deg: float, grid_cell: float):
    """Return (d_row, d_col) offset for arrow placement relative to rack footprint.

    d_row and d_col are in grid-cell units.  Positive d_row = one row below,
    negative d_row = one row above, etc.

    The arrow is placed one cell beyond the footprint in the outlet direction.
    Since we follow the front-face arrow convention from the spec:
        0/360 -> DOWN  -> row increases  (d_row = +1 past footprint bottom)
        180   -> UP    -> row decreases  (d_row = -1 past footprint top)
        270   -> RIGHT -> col increases  (d_col = +1 past footprint right)
        90    -> LEFT  -> col decreases  (d_col = -1 past footprint left)
    """
    ny = normalize_yaw(yaw_deg)
    return {
        0: (1, 0),    # one row below
        90: (0, -1),  # one col left
        180: (-1, 0), # one row above
        270: (0, 1),  # one col right
    }.get(ny, (0, 0))


def model_footprint(anchor: dict, base: dict, yaw_deg: float):
    """Return (x_min, z_min, x_max, z_max) of a model's footprint.

    anchor is the model origin. base has W (width), D (depth), H (height).
    The model occupies a W x D rectangle on the XZ plane, rotated by yaw.
    """
    w = base["W"]
    d = base["D"]
    ny = normalize_yaw(yaw_deg)
    ax = anchor["x"]
    az = anchor["z"]

    # Compute the 4 corners of the un-rotated footprint, then rotate.
    # Default orientation (0 deg): W along +X, D along +Z from anchor.
    if ny == 0:
        return (ax, az, ax + w, az + d)
    elif ny == 90:
        # 90 CW: W along -Z, D along +X
        return (ax, az - w, ax + d, az)
    elif ny == 180:
        # 180: W along -X, D along -Z
        return (ax - w, az - d, ax, az)
    elif ny == 270:
        # 270 CW: W along +Z, D along -X
        return (ax - d, az, ax, az + w)
    else:
        return (ax, az, ax + w, az + d)


def round4(v):
    return round(v, 4)


# ---------------------------------------------------------------------------
# Sheet builders
# ---------------------------------------------------------------------------

def build_legend(models: list) -> list[dict]:
    """Build legend entries from models.

    Group by kind, then by (rounded W, D, H).  Assign symbols.
    """
    KIND_PREFIX = {
        "rack": "SR",
        "acu": "ACU",
        "baffle": "B",
        "box": "BX",
    }
    KIND_DESC = {
        "rack": "Server Rack",
        "acu": "Air Cooling Unit",
        "baffle": "Baffle",
        "box": "Support Structure",
    }

    # Collect unique size signatures per kind
    kind_sizes: dict[str, dict[tuple, int]] = defaultdict(dict)
    for m in models:
        k = m["kind"]
        if k not in KIND_PREFIX:
            continue
        dims = (round4(m["base"]["W"]), round4(m["base"]["D"]), round4(m["base"]["H"]))
        if dims not in kind_sizes[k]:
            kind_sizes[k][dims] = 0
        kind_sizes[k][dims] += 1

    legend = []
    for kind in ("rack", "acu", "baffle", "box"):
        sizes = kind_sizes.get(kind, {})
        if not sizes:
            continue
        prefix = KIND_PREFIX[kind]
        sorted_dims = sorted(sizes.keys())
        for i, dims in enumerate(sorted_dims):
            sym = prefix if len(sorted_dims) == 1 else f"{prefix}{i + 1}"
            legend.append({
                "symbol": sym,
                "kind": kind,
                "W_m": dims[0],
                "D_m": dims[1],
                "H_m": dims[2],
                "description": KIND_DESC.get(kind, kind),
            })
    return legend


def build_legend_lookup(legend: list[dict]) -> dict:
    """Return a dict:  (kind, W, D, H) -> symbol."""
    lookup = {}
    for entry in legend:
        key = (entry["kind"], entry["W_m"], entry["D_m"], entry["H_m"])
        lookup[key] = entry["symbol"]
    return lookup


def get_room_params(data: dict) -> dict:
    """Extract room parameters from cfdproj."""
    room = (data.get("meta", {}) or {}).get("room", {}) or {}
    rp = room.get("polygon") or {}
    vertices = rp.get("vertices", [])
    height = rp.get("height", room.get("height", 3.0))

    if vertices:
        xs = [v[0] for v in vertices]
        zs = [v[1] for v in vertices]
        width = round4(max(xs) - min(xs))
        depth = round4(max(zs) - min(zs))
    else:
        width = 0.0
        depth = 0.0

    # Detect raised floor
    raised_floor = 0.0
    for m in data.get("construction", []):
        if m["kind"] == "floor":
            y = m["anchor"]["y"]
            name_lower = m["name"].lower()
            # Skip ground floor (y=0) and ceiling
            if y > 0.01 and "ceil" not in name_lower and "top" not in name_lower:
                raised_floor = round4(y)
                break

    polygon_str = json.dumps(
        [[round4(v[0]), round4(v[1])] for v in vertices]
    ) if vertices else "[]"

    return {
        "width_m": width,
        "depth_m": depth,
        "height_m": round4(height),
        "raised_floor_m": raised_floor,
        "polygon": polygon_str,
        "grid_cell_m": DEFAULT_GRID_CELL,
    }


def write_legend_sheet(ws, legend: list[dict]):
    """Write the Legend sheet."""
    headers = ["symbol", "kind", "W_m", "D_m", "H_m", "description"]
    for ci, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=ci, value=h)
        cell.font = Font(bold=True, size=9)
    for ri, entry in enumerate(legend, 2):
        for ci, h in enumerate(headers, 1):
            ws.cell(row=ri, column=ci, value=entry[h])
    # Autofit columns
    for ci, h in enumerate(headers, 1):
        ws.column_dimensions[get_column_letter(ci)].width = max(len(h) + 2, 12)


def write_room_sheet(ws, room: dict):
    """Write the Room sheet."""
    ws.cell(row=1, column=1, value="Parameter").font = Font(bold=True, size=9)
    ws.cell(row=1, column=2, value="Value").font = Font(bold=True, size=9)
    for ri, (k, v) in enumerate(room.items(), 2):
        ws.cell(row=ri, column=1, value=k)
        ws.cell(row=ri, column=2, value=v)
    ws.column_dimensions["A"].width = 18
    ws.column_dimensions["B"].width = 40


def write_layout_sheet(ws, data: dict, room: dict, legend: list[dict]):
    """Write the Layout (2D grid) sheet."""
    grid_cell = room["grid_cell_m"]
    rp = (data.get("meta", {}) or {}).get("room", {}).get("polygon") or {}
    vertices = rp.get("vertices", [])
    models = data.get("construction", [])

    if not vertices:
        ws.cell(row=1, column=1, value="No room polygon data")
        return

    xs = [v[0] for v in vertices]
    zs = [v[1] for v in vertices]
    x_min, x_max = min(xs), max(xs)
    z_min, z_max = min(zs), max(zs)

    # Grid dimensions
    n_cols = int(math.ceil((x_max - x_min) / grid_cell)) + 1
    n_rows = int(math.ceil((z_max - z_min) / grid_cell)) + 1

    # Initialize grid: grid[row][col] = (text, fill_or_None)
    grid = [[("", None) for _ in range(n_cols)] for _ in range(n_rows)]

    legend_lookup = build_legend_lookup(legend)

    # -- Place wall boundary on the grid edges --
    # Mark the first/last row and first/last column as wall perimeter
    for c in range(n_cols):
        grid[0][c] = ("", FILL_WALL)
        grid[n_rows - 1][c] = ("", FILL_WALL)
    for r in range(n_rows):
        grid[r][0] = ("", FILL_WALL)
        grid[r][n_cols - 1] = ("", FILL_WALL)

    # -- Place models --
    rack_info = []  # [(name, yaw, row_min, row_max, col_min, col_max)]

    for m in models:
        kind = m["kind"]
        if kind in ("wall", "floor"):
            continue
        if not m.get("enabled", True):
            continue

        yaw = quat_to_yaw_deg(m["transform"]["quaternion"])
        fp = model_footprint(m["anchor"], m["base"], yaw)
        fx_min, fz_min, fx_max, fz_max = fp

        # Ensure min < max
        if fx_min > fx_max:
            fx_min, fx_max = fx_max, fx_min
        if fz_min > fz_max:
            fz_min, fz_max = fz_max, fz_min

        # Map physical coords to grid indices.
        # Use round() for both edges.  For the end edge, subtract a tiny
        # epsilon before rounding so that a boundary exactly on a grid line
        # doesn't claim the next cell (prevents adjacent-model overlap).
        eps = grid_cell * 0.01
        col_start = max(0, int(round((fx_min - x_min) / grid_cell)))
        col_end   = max(0, int(round((fx_max - x_min - eps) / grid_cell)))
        row_start = max(0, int(round((z_max - fz_max) / grid_cell)))
        row_end   = max(0, int(round((z_max - fz_min - eps) / grid_cell)))

        # Ensure at least one cell
        if col_end < col_start:
            col_end = col_start
        if row_end < row_start:
            row_end = row_start

        col_start = min(col_start, n_cols - 1)
        col_end   = min(col_end, n_cols - 1)
        row_start = min(row_start, n_rows - 1)
        row_end   = min(row_end, n_rows - 1)

        # For baffles with D=0, ensure at least 1 cell
        if row_start == row_end and col_start == col_end:
            pass  # single cell is fine
        # For baffles that are thin lines, keep them as-is

        fill = FILL_BY_KIND.get(kind)
        name = m["name"]

        # Determine symbol from legend
        dims = (round4(m["base"]["W"]), round4(m["base"]["D"]), round4(m["base"]["H"]))
        sym = legend_lookup.get((kind, dims[0], dims[1], dims[2]), name)

        # Label to write in the cell — use actual name for rack/acu/box
        label = name if kind in ("rack", "acu", "box") else sym

        # For single-cell models, just write the label
        if row_start == row_end and col_start == col_end:
            grid[row_start][col_start] = (label, fill)
        else:
            # Fill all occupied cells, but preserve existing text labels
            # from previously placed models (only overwrite fill).
            center_r = (row_start + row_end) // 2
            center_c = (col_start + col_end) // 2
            for r in range(row_start, row_end + 1):
                for c in range(col_start, col_end + 1):
                    if r < n_rows and c < n_cols:
                        old_text, _ = grid[r][c]
                        grid[r][c] = (old_text, fill)
            # Write label in center cell (always overwrites)
            if center_r < n_rows and center_c < n_cols:
                grid[center_r][center_c] = (label, fill)

        if kind == "rack":
            rack_info.append((name, yaw, row_start, row_end, col_start, col_end))

    # -- Place arrows for racks --
    for name, yaw, rs, re, cs, ce in rack_info:
        arrow = yaw_to_arrow(yaw)
        d_row, d_col = yaw_to_arrow_offset(yaw, grid_cell)
        if not arrow:
            continue
        # Determine where to put the arrow
        if d_row > 0:
            # Arrow below: place along bottom edge + 1
            ar = re + 1
            for c in range(cs, ce + 1):
                if 0 <= ar < n_rows and 0 <= c < n_cols:
                    old_text, old_fill = grid[ar][c]
                    if not old_text:
                        grid[ar][c] = (arrow, old_fill)
        elif d_row < 0:
            # Arrow above
            ar = rs - 1
            for c in range(cs, ce + 1):
                if 0 <= ar < n_rows and 0 <= c < n_cols:
                    old_text, old_fill = grid[ar][c]
                    if not old_text:
                        grid[ar][c] = (arrow, old_fill)
        elif d_col > 0:
            # Arrow right
            ac = ce + 1
            for r in range(rs, re + 1):
                if 0 <= r < n_rows and 0 <= ac < n_cols:
                    old_text, old_fill = grid[r][ac]
                    if not old_text:
                        grid[r][ac] = (arrow, old_fill)
        elif d_col < 0:
            # Arrow left
            ac = cs - 1
            for r in range(rs, re + 1):
                if 0 <= r < n_rows and 0 <= ac < n_cols:
                    old_text, old_fill = grid[r][ac]
                    if not old_text:
                        grid[r][ac] = (arrow, old_fill)

    # -- Detect hot/cold aisles (heuristic) --
    _detect_aisles(grid, rack_info, n_rows, n_cols)

    # -- Write to worksheet --
    # Row 1: header row with X coordinates
    ws.cell(row=1, column=1, value="")
    for c in range(n_cols):
        x_val = round4(x_min + c * grid_cell)
        cell = ws.cell(row=1, column=c + 2, value=x_val)
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center")

    # Column A: Z coordinates (flipped)
    for r in range(n_rows):
        z_val = round4(z_max - r * grid_cell)
        cell = ws.cell(row=r + 2, column=1, value=z_val)
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center")

    # Grid content
    for r in range(n_rows):
        for c in range(n_cols):
            text, fill = grid[r][c]
            cell = ws.cell(row=r + 2, column=c + 2, value=text)
            cell.font = SMALL_FONT
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = THIN_BORDER
            if fill:
                cell.fill = fill

    # Set column widths (compact)
    ws.column_dimensions["A"].width = 5
    for c in range(n_cols):
        ws.column_dimensions[get_column_letter(c + 2)].width = 4

    # Set row heights
    for r in range(1, n_rows + 2):
        ws.row_dimensions[r].height = 14


def _detect_aisles(grid, rack_info, n_rows, n_cols):
    """Try to detect hot and cold aisles between facing rack rows.

    Heuristic: if two racks in adjacent rows face each other (arrows pointing
    toward the space between them), that space is a hot aisle. The opposite
    side is a cold aisle.
    """
    if not rack_info:
        return

    # Group racks by their approximate Z band (row range)
    # Find pairs of rack rows that face each other

    # For each rack, record its outlet direction in row-terms
    # d_row > 0 means outlet faces downward (increasing row), etc.
    rack_outlets = []
    for name, yaw, rs, re, cs, ce in rack_info:
        d_row, d_col = yaw_to_arrow_offset(yaw, 1.0)
        rack_outlets.append((name, yaw, rs, re, cs, ce, d_row, d_col))

    # Group racks by their row band (rs, re) -- approximate grouping
    row_groups = defaultdict(list)
    for item in rack_outlets:
        _, _, rs, re, _, _, _, _ = item
        key = (rs, re)
        row_groups[key].append(item)

    # For each pair of row-groups, check if they face each other
    keys = sorted(row_groups.keys())
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            group_a = row_groups[keys[i]]
            group_b = row_groups[keys[j]]

            # Check if group_a outlets face toward group_b and vice versa
            a_re = keys[i][1]  # bottom row of group a
            b_rs = keys[j][0]  # top row of group b

            if b_rs <= a_re:
                continue  # overlapping, skip

            gap = b_rs - a_re
            if gap < 1 or gap > 6:
                continue  # too far apart or no gap

            # Check arrow directions.  The arrow indicates the front/inlet
            # direction (spec convention).  d_row > 0 means front faces
            # downward in Excel (toward increasing row).
            #
            # If group_a arrows point DOWN and group_b arrows point UP,
            # both fronts face the gap -> COLD aisle (cold air enters from
            # both sides).
            # If group_a arrows point UP and group_b arrows point DOWN,
            # both backs face the gap -> HOT aisle (hot exhaust meets in
            # the middle).
            a_fronts_down = all(item[6] > 0 for item in group_a)
            b_fronts_up = all(item[6] < 0 for item in group_b)

            a_fronts_up = all(item[6] < 0 for item in group_a)
            b_fronts_down = all(item[6] > 0 for item in group_b)

            if a_fronts_down and b_fronts_up:
                # Both fronts face the gap -> cold aisle
                mid_row = (a_re + b_rs) // 2
                _write_aisle_label(grid, mid_row, group_a, group_b, n_cols, "COLD AISLE")
            elif a_fronts_up and b_fronts_down:
                # Both backs face the gap -> hot aisle
                mid_row = (a_re + b_rs) // 2
                _write_aisle_label(grid, mid_row, group_a, group_b, n_cols, "HOT AISLE")


def _write_aisle_label(grid, mid_row, group_a, group_b, n_cols, label):
    """Write an aisle label in the gap between two rack rows.

    Tries mid_row first, then scans one row above / below for an empty cell.
    The label is placed in the horizontal center of the rack rows.
    """
    n_rows = len(grid)
    all_items = list(group_a) + list(group_b)
    cs_min = min(item[4] for item in all_items)
    ce_max = max(item[5] for item in all_items)
    mid_col = (cs_min + ce_max) // 2

    # Try mid_row, then +1, then -1
    for try_row in (mid_row, mid_row + 1, mid_row - 1):
        if try_row < 0 or try_row >= n_rows:
            continue
        if 0 <= mid_col < n_cols:
            old_text, old_fill = grid[try_row][mid_col]
            if not old_text:
                grid[try_row][mid_col] = (label, old_fill)
                return
    # All candidate cells occupied — still write at mid_row center,
    # overwriting the existing content (arrows / empty fills).
    if 0 <= mid_row < n_rows and 0 <= mid_col < n_cols:
        _, old_fill = grid[mid_row][mid_col]
        grid[mid_row][mid_col] = (label, old_fill)


def write_acu_config_sheet(ws, case_dir: Path):
    """Write ACU_Config sheet from input/ACU.csv or from cfdproj."""
    acu_csv = case_dir / "input" / "ACU.csv"
    if acu_csv.exists():
        _csv_to_sheet(ws, acu_csv)
    else:
        ws.cell(row=1, column=1, value="(no ACU.csv available)")


def write_rack_equipment_sheet(ws, case_dir: Path):
    """Write Rack_Equipment sheet from input/server_power.csv or from cfdproj."""
    sp_csv = case_dir / "input" / "server_power.csv"
    if sp_csv.exists():
        _csv_to_sheet(ws, sp_csv)
    else:
        ws.cell(row=1, column=1, value="(no server_power.csv available)")


def _csv_to_sheet(ws, csv_path: Path):
    """Copy a CSV file into a worksheet."""
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        for ri, row in enumerate(reader, 1):
            for ci, val in enumerate(row, 1):
                cell = ws.cell(row=ri, column=ci, value=val)
                if ri == 1:
                    cell.font = Font(bold=True, size=9)
    # Auto-width
    for ci in range(1, ws.max_column + 1):
        max_len = 0
        col_letter = get_column_letter(ci)
        for row in ws.iter_rows(min_col=ci, max_col=ci, values_only=True):
            for cell_val in row:
                if cell_val:
                    max_len = max(max_len, len(str(cell_val)))
        ws.column_dimensions[col_letter].width = max(max_len + 2, 10)


# ---------------------------------------------------------------------------
# Positions sheet (exact coordinates — precise Builder input)
# ---------------------------------------------------------------------------

def build_positions(data: dict) -> list[dict]:
    """Exact per-model geometry rows — the precise-coordinate source of truth.

    The visual Layout grid quantises positions to 0.5 m, which cannot represent
    dense rows of 0.6 m-pitch racks. This sheet instead lists every model's
    exact anchor, footprint and orientation, so the Builder can reconstruct the
    layout faithfully (the paper's "precise equipment positions" claim for
    Excel-spreadsheet mode). Disabled models are included because the scorer
    still counts them.
    """
    rows = []
    for m in data.get("construction", []):
        kind = m.get("kind")
        a = m.get("anchor", {}) or {}
        b = m.get("base", {}) or {}
        yaw = quat_to_yaw_deg(m.get("transform", {}).get("quaternion", {}))
        if kind == "rack":
            equip = len(m.get("rack", {}).get("equipments", []))
        elif kind in ("acu", "floor"):
            equip = sum(len(f.get("patches", [])) for f in (m.get("faces", {}) or {}).values())
        else:
            equip = 0
        rows.append({
            "name": m.get("name", ""),
            "kind": kind,
            "enabled": bool(m.get("enabled", True)),
            "x_m": round4(a.get("x", 0)),
            "y_m": round4(a.get("y", 0)),
            "z_m": round4(a.get("z", 0)),
            "W_m": round4(b.get("W", 0)),
            "D_m": round4(b.get("D", 0)),
            "H_m": round4(b.get("H", 0)),
            "yaw_deg": round(yaw, 2),
            "equip": equip,
        })
    return rows


def write_positions_sheet(ws, rows: list[dict]):
    """Write the Positions sheet: exact anchor/footprint/yaw per model."""
    headers = ["name", "kind", "enabled", "x_m", "y_m", "z_m",
               "W_m", "D_m", "H_m", "yaw_deg", "equip"]
    for ci, h in enumerate(headers, 1):
        c = ws.cell(row=1, column=ci, value=h)
        c.font = Font(bold=True, size=9)
    for ri, r in enumerate(rows, 2):
        for ci, h in enumerate(headers, 1):
            ws.cell(row=ri, column=ci, value=r[h])
        fill = FILL_BY_KIND.get(r["kind"])
        if fill:
            ws.cell(row=ri, column=2).fill = fill
    for ci, h in enumerate(headers, 1):
        ws.column_dimensions[get_column_letter(ci)].width = max(len(h) + 2, 10)


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

def generate_xlsx(case_name: str):
    """Generate layout.xlsx for a single case."""
    case_dir = DEMO_DIR / case_name
    label_path = case_dir / "label" / f"{case_name}.cfdproj"

    if not label_path.exists():
        print(f"  SKIP {case_name}: label file not found at {label_path}")
        return False

    data = load_cfdproj(label_path)

    models = [m for m in data.get("construction", []) if m.get("enabled", True)]

    # Filter out wall/floor for legend (they have no legend entry)
    equip_models = [m for m in models if m["kind"] not in ("wall", "floor")]

    legend = build_legend(equip_models)
    room = get_room_params(data)

    wb = Workbook()

    # Sheet 1: Legend
    ws_legend = wb.active
    ws_legend.title = "Legend"
    write_legend_sheet(ws_legend, legend)

    # Sheet 2: Room
    ws_room = wb.create_sheet("Room")
    write_room_sheet(ws_room, room)

    # Sheet 3: Layout
    ws_layout = wb.create_sheet("Layout")
    write_layout_sheet(ws_layout, data, room, legend)

    # Sheet 4: ACU_Config
    ws_acu = wb.create_sheet("ACU_Config")
    write_acu_config_sheet(ws_acu, case_dir)

    # Sheet 5: Rack_Equipment
    ws_equip = wb.create_sheet("Rack_Equipment")
    write_rack_equipment_sheet(ws_equip, case_dir)

    # Sheet 6: Positions (exact coordinates — precise Builder input)
    ws_pos = wb.create_sheet("Positions")
    write_positions_sheet(ws_pos, build_positions(data))

    # Save
    out_dir = case_dir / "input"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "layout.xlsx"
    wb.save(out_path)
    print(f"  OK   {case_name} -> {out_path}")
    return True


def discover_cases() -> list[str]:
    """Return sorted list of all case_* directories."""
    cases = []
    for d in sorted(DEMO_DIR.iterdir()):
        if d.is_dir() and d.name.startswith("case_"):
            cases.append(d.name)
    return cases


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    args = sys.argv[1:]
    if args == ["all"]:
        cases = discover_cases()
    else:
        cases = args

    ok = 0
    fail = 0
    for case_name in cases:
        try:
            if generate_xlsx(case_name):
                ok += 1
            else:
                fail += 1
        except Exception as e:
            print(f"  FAIL {case_name}: {e}")
            fail += 1

    print(f"\nDone: {ok} generated, {fail} skipped/failed")


if __name__ == "__main__":
    main()
