"""Harness-level validation for scenario air-v1.

This is where prior knowledge lives that RL should not have to rediscover:
which cooling architectures make physical sense, how containment pairs with
aisles, clearances and counts. Every message tells the agent what is wrong
and what to change.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List

from agent.scenarios.air.spec import ARCHITECTURES, FORBIDDEN, LATER_STAGE, Design, Row, Task, unit_ids

SLOT_TYPES = ("RACK", "IRC", "GAP")


@dataclass(frozen=True)
class Issue:
    code: str
    message: str


@dataclass(frozen=True)
class Aisle:
    row_a: str  # row with the smaller centre coordinate
    row_b: str
    kind: str  # "cold" | "hot" | "front_to_back"
    width_m: float
    length_m: float  # overlap of the two rows along the row axis


def row_length(row: Row, task: Task) -> float:
    widths = {"RACK": task.rack.width_m, "IRC": task.irc.width_m, "GAP": task.rack.width_m}
    return sum(widths.get(s, task.rack.width_m) for s in row.slots)


def row_depth(task: Task) -> float:
    return max(task.rack.depth_m, task.irc.depth_m)


def aisles(design: Design, task: Task) -> List[Aisle]:
    """Aisles between consecutive parallel rows that overlap along the row axis."""
    rows = sorted(design.rows, key=lambda r: r.center_m)
    out: List[Aisle] = []
    for a, b in zip(rows, rows[1:]):
        lo = max(a.start_m, b.start_m)
        hi = min(a.start_m + row_length(a, task), b.start_m + row_length(b, task))
        if hi <= lo:
            continue
        if a.front == "+" and b.front == "-":
            kind = "cold"
        elif a.front == "-" and b.front == "+":
            kind = "hot"
        else:
            kind = "front_to_back"
        out.append(Aisle(a.id, b.id, kind, (b.center_m - a.center_m) - row_depth(task), hi - lo))
    return out


def _fits(x: float, unit: float) -> int:
    return int(math.floor(x / unit + 1e-9))


def _bounds(row: Row, task: Task) -> List[Issue]:
    length, half, c = row_length(row, task), row_depth(task) / 2, task.wall_clear_m
    along = task.room_x_m if row.axis == "x" else task.room_y_m
    across = task.room_y_m if row.axis == "x" else task.room_x_m
    if row.start_m < c or row.start_m + length > along - c or row.center_m - half < c or row.center_m + half > across - c:
        return [
            Issue(
                "OUT_OF_ROOM",
                f"row {row.id} must stay {c} m from every wall; it spans "
                f"[{row.start_m:.2f}, {row.start_m + length:.2f}] m along {row.axis} and "
                f"[{row.center_m - half:.2f}, {row.center_m + half:.2f}] m across",
            )
        ]
    return []


def _perimeter(design: Design, task: Task) -> List[Issue]:
    issues: List[Issue] = []
    wall_len = {"x0": task.room_y_m, "x1": task.room_y_m, "y0": task.room_x_m, "y1": task.room_x_m}
    half = task.perimeter.width_m / 2
    by_wall: Dict[str, list] = {}
    for u in design.perimeter_units:
        if u.wall not in wall_len:
            issues.append(Issue("UNIT_WALL", f"unit {u.id}: wall must be one of x0, x1, y0, y1"))
            continue
        if u.position_m - half < 0 or u.position_m + half > wall_len[u.wall]:
            issues.append(Issue("UNIT_OFF_WALL", f"unit {u.id} does not fit on wall {u.wall} at {u.position_m:.2f} m"))
        by_wall.setdefault(u.wall, []).append(u)
    for wall, units in by_wall.items():
        units.sort(key=lambda u: u.position_m)
        for p, q in zip(units, units[1:]):
            if q.position_m - p.position_m < task.perimeter.width_m:
                issues.append(Issue("UNIT_OVERLAP", f"units {p.id} and {q.id} overlap on wall {wall}"))
    return issues


def validate(design: Design, task: Task) -> List[Issue]:
    # Reject malformed numerical/identity input before geometry arithmetic.
    issues = []
    numbers = (task.room_x_m, task.room_y_m, task.room_h_m, task.tile_m,
               task.min_aisle_m, task.min_cold_aisle_m,
               task.rack.width_m, task.rack.depth_m, task.irc.width_m,
               task.irc.depth_m, task.perimeter.width_m, task.perimeter.depth_m)
    counts = (task.n_racks, task.n_inrow_units, task.n_perimeter_units)
    if any(not math.isfinite(float(v)) or v <= 0 for v in numbers) or not math.isfinite(task.wall_clear_m) or task.wall_clear_m < 0:
        return [Issue("TASK_DOMAIN", "task dimensions must be finite and positive; wall clearance must be nonnegative")]
    if any(isinstance(v,bool) or not isinstance(v,int) or v < 0 for v in counts) or task.n_racks == 0:
        return [Issue("TASK_COUNTS", "fixed equipment counts must be nonnegative integers and include at least one rack")]
    for bounds in (task.t_supply_range_c,task.flow_fraction_range):
        if len(bounds)!=2 or any(not math.isfinite(v) for v in bounds) or not bounds[0]<bounds[1]:
            return [Issue("TASK_RANGE", "each control range must contain two finite ordered values")]
    if len({r.id for r in design.rows}) != len(design.rows):
        issues.append(Issue("ROW_ID_DUPLICATE", "each physical row needs a unique id"))
    if len({u.id for u in design.perimeter_units}) != len(design.perimeter_units):
        issues.append(Issue("UNIT_ID_DUPLICATE", "each perimeter unit needs a unique id"))
    for row in design.rows:
        if row.axis not in ("x","y"):issues.append(Issue("ROW_AXIS", f"row {row.id}: axis must be x or y"))
        if not row.id or not row.slots:issues.append(Issue("ROW_EMPTY", "a row needs a nonempty id and slot sequence"))
        if not math.isfinite(row.center_m) or not math.isfinite(row.start_m):
            issues.append(Issue("ROW_NONFINITE", f"row {row.id}: location must be finite"))
    for unit in design.perimeter_units:
        if not math.isfinite(unit.position_m):issues.append(Issue("UNIT_NONFINITE", f"unit {unit.id}: position must be finite"))
    row_ids={r.id for r in design.rows}
    for pair in design.containment_pairs:
        if len(pair)!=2 or pair[0]==pair[1] or not set(pair)<=row_ids:
            issues.append(Issue("CONTAINMENT_PAIR", "containment must name two distinct existing rows"))
    if len({frozenset(p) for p in design.containment_pairs})!=len(design.containment_pairs):
        issues.append(Issue("CONTAINMENT_DUPLICATE", "list each contained aisle exactly once"))
    for tile in design.tiles:
        if tile.row_a not in row_ids or (tile.row_b and tile.row_b not in row_ids):
            issues.append(Issue("TILE_ROW", "tile groups must name existing rows"))
        if isinstance(tile.count,bool) or not isinstance(tile.count,int) or tile.count<1:
            issues.append(Issue("TILES_COUNT", "tile count must be a positive integer"))
    if issues:return issues
    arch = design.architecture
    if arch in FORBIDDEN:
        return [Issue("ARCH_FORBIDDEN", f"{arch} is not a valid cooling architecture: {FORBIDDEN[arch]}")]
    if arch not in ARCHITECTURES:
        return [Issue("ARCH_UNKNOWN", f"unknown architecture {arch}; choose one of {sorted(ARCHITECTURES)}")]
    if arch not in task.allowed_architectures:
        return [Issue("ARCH_NOT_ALLOWED", f"the brief allows only {sorted(task.allowed_architectures)}")]
    if arch in LATER_STAGE and not task.enable_later_stage:
        return [Issue("ARCH_NOT_SUPPORTED_YET", f"{arch} is not supported yet: {LATER_STAGE[arch]}")]
    spec = ARCHITECTURES[arch]
    issues: List[Issue] = []

    # Slots, axes and counts.
    for row in design.rows:
        bad = sorted({s for s in row.slots if s not in SLOT_TYPES})
        if bad:
            issues.append(Issue("SLOT_UNKNOWN", f"row {row.id} has unknown slots {bad}; use RACK, IRC or GAP"))
        if row.front not in ("+", "-"):
            issues.append(Issue("ROW_FRONT", f"row {row.id}: front must be '+' or '-'"))
    if len({r.axis for r in design.rows}) > 1:
        issues.append(Issue("MIXED_ROW_AXES", "all rows must run along the same axis"))
    n_racks = sum(s == "RACK" for r in design.rows for s in r.slots)
    if n_racks != task.n_racks:
        issues.append(Issue("RACK_COUNT", f"the brief fixes {task.n_racks} racks; the design places {n_racks}"))
    n_irc = sum(s == "IRC" for r in design.rows for s in r.slots)
    if spec["unit"] == "inrow":
        if design.perimeter_units:
            issues.append(Issue("UNIT_TYPE", f"{arch} uses in-row coolers only; remove the perimeter units"))
        if n_irc != task.n_inrow_units:
            issues.append(Issue("UNIT_COUNT", f"{arch} needs exactly {task.n_inrow_units} IRC slots; the design has {n_irc}"))
    else:
        if n_irc:
            issues.append(Issue("UNIT_TYPE", f"{arch} uses perimeter units; remove the IRC slots"))
        if len(design.perimeter_units) != task.n_perimeter_units:
            issues.append(
                Issue("UNIT_COUNT", f"{arch} needs exactly {task.n_perimeter_units} perimeter units; the design has {len(design.perimeter_units)}")
            )
        issues += _perimeter(design, task)
    if spec["floor"] == "slab" and design.tiles:
        issues.append(Issue("TILES_ON_SLAB", f"{arch} has no raised floor; remove the perforated tiles"))

    # Geometry and aisles.
    for row in design.rows:
        issues += _bounds(row, task)
    found = aisles(design, task)
    kinds = {frozenset((a.row_a, a.row_b)): a for a in found}
    for a in found:
        if a.width_m < 0:
            issues.append(Issue("ROW_OVERLAP", f"rows {a.row_a} and {a.row_b} overlap"))
            continue
        if a.kind == "front_to_back":
            issues.append(
                Issue(
                    "FRONT_TO_BACK",
                    f"rows {a.row_a} and {a.row_b} face the same way, so one row breathes the other's exhaust; "
                    "flip one row so fronts face fronts (cold aisle) and backs face backs (hot aisle)",
                )
            )
            continue
        need = task.min_cold_aisle_m if a.kind == "cold" else task.min_aisle_m
        if a.width_m < need:
            issues.append(Issue("AISLE_TOO_NARROW", f"the {a.kind} aisle between {a.row_a} and {a.row_b} is {a.width_m:.2f} m; it must be at least {need} m"))

    # Containment.
    pairs = {frozenset(p) for p in design.containment_pairs}
    want = spec["containment"]
    if want == "none" and pairs:
        issues.append(Issue("CONTAINMENT_UNEXPECTED", f"{arch} has no containment; remove containment_pairs or pick a *_CAC / *_HAC architecture"))
    if want != "none":
        if not pairs:
            issues.append(Issue("CONTAINMENT_MISSING", f"{arch} needs at least one contained {want} aisle in containment_pairs"))
        for p in sorted(pairs, key=sorted):
            a = kinds.get(p)
            if a is None:
                issues.append(Issue("CONTAINMENT_NOT_AISLE", f"containment pair {sorted(p)} is not two adjacent facing rows"))
            elif a.kind != want:
                issues.append(Issue("CONTAINMENT_WRONG_AISLE", f"{arch} contains {want} aisles, but {sorted(p)} bound a {a.kind} aisle"))

    # Perforated tiles (raised floor only).
    if spec["floor"] == "raised":
        tiled = set()
        for t in design.tiles:
            key = frozenset((t.row_a, t.row_b)) if t.row_b else frozenset((t.row_a,))
            tiled.add(key)
            a = kinds.get(key)
            if t.row_b and (a is None or a.kind != "cold"):
                issues.append(Issue("TILES_NOT_IN_COLD_AISLE", f"tiles between {t.row_a} and {t.row_b} are not in a cold aisle; tiles go in front of rack intakes"))
            elif a is not None:
                cap = _fits(a.length_m, task.tile_m) * _fits(a.width_m, task.tile_m)
                if not 1 <= t.count <= cap:
                    issues.append(Issue("TILES_COUNT", f"the cold aisle between {t.row_a} and {t.row_b} holds 1 to {cap} tiles; got {t.count}"))
        for a in found:
            if a.kind == "cold" and frozenset((a.row_a, a.row_b)) not in tiled:
                issues.append(Issue("COLD_AISLE_WITHOUT_TILES", f"the cold aisle between {a.row_a} and {a.row_b} has no perforated tiles, so its racks get no supply air"))

    # Setpoints.
    ids = unit_ids(design)
    given = [s.unit_id for s in design.setpoints]
    missing, unknown = sorted(set(ids) - set(given)), sorted(set(given) - set(ids))
    duplicate = sorted({u for u in given if given.count(u) > 1})
    if missing:
        issues.append(Issue("SETPOINT_MISSING", f"no setpoint for units {missing}"))
    if unknown:
        issues.append(Issue("SETPOINT_UNKNOWN", f"setpoints name unknown units {unknown}"))
    if duplicate:
        issues.append(Issue("SETPOINT_DUPLICATE", f"units {duplicate} have more than one setpoint"))
    (t_lo, t_hi), (f_lo, f_hi) = task.t_supply_range_c, task.flow_fraction_range
    for s in design.setpoints:
        if not (t_lo <= s.t_supply_c <= t_hi and f_lo <= s.flow_fraction <= f_hi):
            issues.append(
                Issue("SETPOINT_RANGE", f"unit {s.unit_id}: supply temperature must lie in [{t_lo}, {t_hi}] C and flow fraction in [{f_lo}, {f_hi}]")
            )
    return issues
