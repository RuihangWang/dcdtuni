"""Families, equipment catalog, task and design of the data-hall design language (v1).

Every number in ``CATALOG`` is a declared research assumption (synthetic
equipment), recorded in each compiled project's ``meta.dcdesign``.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Optional, Tuple

DSL_VERSION = "dc-design/v1-2026-10-21"

# units: in-row (in the rack rows, front supply into the cold aisle, back return)
#        perimeter (against the long walls, downflow into the raised-floor plenum,
#        perforated tiles in the cold aisle, back return)
# containment: none | cold (cold-aisle roof and end doors)
#              | hot (each row's rear zone up to the wall gets a roof and end panels)
FAMILIES = {
    "INROW_OPEN": {"units": "inrow", "containment": "none"},
    "INROW_CAC": {"units": "inrow", "containment": "cold"},
    "INROW_HAC": {"units": "inrow", "containment": "hot"},
    "PERIM_OPEN": {"units": "perimeter", "containment": "none"},
    "PERIM_CAC": {"units": "perimeter", "containment": "cold"},
    "PERIM_HAC": {"units": "perimeter", "containment": "hot"},
}
TILE_COUNTS = (8, 12, 16, 20)   # perforated tiles in the cold aisle (perimeter families)
N_UNITS = 4                     # cooling units in every family

CATALOG = {
    "acu_rated_flow_m3s": 4.2,
    "acu_fan_kw_at_rated": 3.0,
    "acu_fan_exponent": 2.8,
    "acu_min_flow_fraction": 0.22,
    "acu_capacity_kw": 50.0,
    "acu_coolant_c": 14.0,
    "perimeter_body_m": {"width": 1.2, "depth": 0.9, "height": 1.95},
    "perimeter_wall_gap_m": 0.1,
    "tile_m": 0.6,
    "tile_open_area_pct": 25.0,
    "tile_hole_mm": 3.0,
    "provenance": "declared synthetic equipment for the ISPO design task; not vendor data",
}

BOUNDS = {
    "acu_supply_c": (16.0, 27.0),
    "acu_flow_fraction": (CATALOG["acu_min_flow_fraction"], 1.0),
    "cdu_primary_inlet_c": (15.0, 32.0),     # facility water delivered to the CDU heat exchanger
    "cdu_primary_flow_kg_s": (0.5, 2.0),
    "cdu_pump_ratio": (0.5, 1.0),            # secondary pump speed / reference speed
}

# ASHRAE thermal guideline envelopes for server inlet air (recommended range is
# 18-27 degC for all classes; the allowable upper limits differ).
THERMAL_CLASSES = {
    "A1": {"inlet_min_c": 15.0, "inlet_max_c": 32.0},
    "A2": {"inlet_min_c": 10.0, "inlet_max_c": 35.0},
    "recommended": {"inlet_min_c": 18.0, "inlet_max_c": 27.0},
}


@dataclass(frozen=True)
class Task:
    """A design requirement on the fixed hall."""
    it_scale: float = 1.0                     # multiplies every server's total IT power
    thermal_class: str = "recommended"
    allowed_families: Tuple[str, ...] = tuple(FAMILIES)

    def check(self):
        if not (math.isfinite(self.it_scale) and 0.25 <= self.it_scale <= 2.0):
            raise ValueError("it_scale must be in [0.25, 2]")
        if self.thermal_class not in THERMAL_CLASSES:
            raise ValueError(f"thermal_class must be one of {tuple(THERMAL_CLASSES)}")
        if not self.allowed_families or any(f not in FAMILIES for f in self.allowed_families):
            raise ValueError("allowed_families must be a nonempty subset of FAMILIES")


@dataclass(frozen=True)
class Design:
    family: str
    acu_supply_c: Tuple[float, ...]           # delivered supply temperature per unit, in unit order
    acu_flow_fraction: Tuple[float, ...]      # of the rated flow, per unit
    cdu_primary_inlet_c: float = 20.0         # facility (primary) water inlet of the CDU heat exchanger
    cdu_primary_flow_kg_s: float = 1.0
    cdu_pump_ratio: float = 1.0               # secondary pump speed / reference speed
    tile_count: Optional[int] = None          # perimeter families only
    closed_units: Tuple[int, ...] = field(default=())   # operating context: failed units (indices)

    def to_json(self) -> dict:
        return asdict(self)


def unit_ids(family: str) -> Tuple[str, ...]:
    """Stable unit names in the compiled project, in the order of the design vectors."""
    kind = FAMILIES[family]["units"]
    return tuple(f"{'irc' if kind == 'inrow' else 'crah'}_{i + 1:02d}" for i in range(N_UNITS))


def validate(task: Task, design: Design) -> list[str]:
    """Reasons the design is not admissible for the task (empty list = admissible)."""
    issues = []
    task.check()
    if design.family not in FAMILIES:
        return [f"unknown family {design.family!r}"]
    if design.family not in task.allowed_families:
        issues.append(f"family {design.family} is not allowed by the task")
    perimeter = FAMILIES[design.family]["units"] == "perimeter"
    if perimeter and design.tile_count not in TILE_COUNTS:
        issues.append(f"a perimeter family needs tile_count in {TILE_COUNTS}")
    if not perimeter and design.tile_count is not None:
        issues.append("in-row families have no floor tiles")
    for name, values in (("acu_supply_c", design.acu_supply_c), ("acu_flow_fraction", design.acu_flow_fraction)):
        lo, hi = BOUNDS[name]
        if len(values) != N_UNITS:
            issues.append(f"{name} needs {N_UNITS} values")
        elif any(not math.isfinite(v) or v < lo - 1e-12 or v > hi + 1e-12 for v in values):
            issues.append(f"{name} outside [{lo}, {hi}]")
    for name in ("cdu_primary_inlet_c", "cdu_primary_flow_kg_s", "cdu_pump_ratio"):
        lo, hi = BOUNDS[name]
        v = getattr(design, name)
        if not math.isfinite(v) or v < lo - 1e-12 or v > hi + 1e-12:
            issues.append(f"{name} outside [{lo}, {hi}]")
    if len(set(design.closed_units)) != len(design.closed_units) or \
            any(not 0 <= i < N_UNITS for i in design.closed_units) or len(design.closed_units) >= N_UNITS:
        issues.append("closed_units must be distinct unit indices and leave at least one unit running")
    return issues
