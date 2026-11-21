"""Task and design types for scenario air-v1 (DSL v1 draft).

The agent writes a compact Design; the compiler turns a validated Design into
a real .cfdproj. Rows are straight lines of slots ("RACK", "IRC" = in-row
cooler, "GAP"), all rows share one axis, and ``front`` is the side the rack
intakes face along the perpendicular axis.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple

# floor: raised | slab; unit: perimeter (downflow CRAH on the walls) | inrow; containment: none | cold | hot
ARCHITECTURES: Dict[str, Dict[str, str]] = {
    "RF_PERIM_OPEN": {"floor": "raised", "unit": "perimeter", "containment": "none"},
    "RF_PERIM_CAC": {"floor": "raised", "unit": "perimeter", "containment": "cold"},
    "RF_PERIM_HAC": {"floor": "raised", "unit": "perimeter", "containment": "hot"},
    "SLAB_INROW_OPEN": {"floor": "slab", "unit": "inrow", "containment": "none"},
    "SLAB_INROW_HAC": {"floor": "slab", "unit": "inrow", "containment": "hot"},
    "SLAB_INROW_CAC": {"floor": "slab", "unit": "inrow", "containment": "cold"},
}

# Architectures whose geometry the compiler does not build yet.
LATER_STAGE: Dict[str, str] = {
    "RF_PERIM_HAC": "needs a false-ceiling return plenum connected to the CRAH returns",
}

# Combinations the agent might try, with the reason they are rejected.
FORBIDDEN: Dict[str, str] = {
    "SLAB_PERIM_CAC": "a contained cold aisle needs a supply path: perforated tiles over a raised floor, or in-row coolers; wall units on a slab floor cannot feed it",
    "RF_INROW_OPEN": "in-row coolers do not use the raised floor; pick SLAB_INROW_* or a perimeter architecture",
    "RF_INROW_HAC": "in-row coolers do not use the raised floor; pick SLAB_INROW_HAC",
    "RF_INROW_CAC": "in-row coolers do not use the raised floor; pick SLAB_INROW_CAC",
}


@dataclass(frozen=True)
class Footprint:
    width_m: float  # along the row
    depth_m: float  # front to back


@dataclass(frozen=True)
class Row:
    id: str
    axis: str  # "x" or "y": the direction the row runs
    center_m: float  # row centre line on the perpendicular axis
    start_m: float  # first slot edge on the row axis
    front: str  # "+" or "-": intakes face the + or - side of the perpendicular axis
    slots: Tuple[str, ...]  # "RACK" | "IRC" | "GAP"


@dataclass(frozen=True)
class PerimeterUnit:
    id: str
    wall: str  # "x0" | "x1" | "y0" | "y1"
    position_m: float  # unit centre along the wall


@dataclass(frozen=True)
class TileGroup:
    row_a: str
    row_b: str  # "" when row_a faces open floor instead of another row
    count: int


@dataclass(frozen=True)
class Setpoint:
    unit_id: str  # perimeter id, or "<row id>:<slot index>" for an in-row cooler
    flow_fraction: float
    t_supply_c: float


@dataclass(frozen=True)
class Design:
    architecture: str
    rows: Tuple[Row, ...]
    containment_pairs: Tuple[Tuple[str, str], ...] = ()
    perimeter_units: Tuple[PerimeterUnit, ...] = ()
    tiles: Tuple[TileGroup, ...] = ()
    setpoints: Tuple[Setpoint, ...] = ()


@dataclass(frozen=True)
class Task:
    room_x_m: float
    room_y_m: float
    room_h_m: float
    n_racks: int
    n_perimeter_units: int = 0  # unit count when a perimeter architecture is chosen
    n_inrow_units: int = 0  # unit count when an in-row architecture is chosen
    rack: Footprint = Footprint(0.6, 1.2)
    irc: Footprint = Footprint(0.6, 1.2)
    perimeter: Footprint = Footprint(1.8, 1.0)
    tile_m: float = 0.6
    min_aisle_m: float = 0.91
    min_cold_aisle_m: float = 1.22
    wall_clear_m: float = 1.2
    # 16 C keeps chilled water >= 12 C (dry coil, sensible-only model); 30 C lies above every inlet limit,
    # so the thermal constraint, not this range, decides how warm the supply can be.
    t_supply_range_c: Tuple[float, float] = (16.0, 30.0)
    flow_fraction_range: Tuple[float, float] = (0.22, 1.0)
    allowed_architectures: Tuple[str, ...] = tuple(ARCHITECTURES)
    enable_later_stage: bool = False


def unit_ids(design: Design) -> Tuple[str, ...]:
    """Cooling-unit ids in a fixed order: perimeter units, then in-row slots row by row."""
    ids = [u.id for u in design.perimeter_units]
    for row in design.rows:
        ids += [f"{row.id}:{i}" for i, s in enumerate(row.slots) if s == "IRC"]
    return tuple(ids)
