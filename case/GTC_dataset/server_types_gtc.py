"""Server-size definitions and load-tier configuration for GTC dataset generation."""
from dataclasses import dataclass
from pathlib import Path as _Path
from typing import Dict, List, Tuple


@dataclass(frozen=True)
class ServerSizeType:
    name: str
    u_size: int
    kind: str = "fanServer"


# GTC key difference vs Slab: minimum server U-span is 4 (no 1U/2U).
U_SIZES = [4, 6, 8]
MIN_U_SPAN = 4
SERVER_SIZES: Dict[int, ServerSizeType] = {
    4: ServerSizeType("G4", 4),
    6: ServerSizeType("G6", 6),
    8: ServerSizeType("G8", 8),
}

RACK_TOTAL_U = 45                       # GTC rack geometry.slot
SAFE_MARGIN_U = 1
USABLE_SLOTS = RACK_TOTAL_U - 2 * SAFE_MARGIN_U  # 43

NUM_RACKS = 50
NUM_ACUS = 6


@dataclass(frozen=True)
class TierConfig:
    name: str
    lambda_range: Tuple[float, float]           # per-rack U-fill fraction
    server_power_kw_range: Tuple[float, float]  # per-server power
    server_flow_range: Tuple[float, float]      # per-server m3/s
    t_supply_range: Tuple[float, float]         # ACU supply temp C
    acu_flow_range: Tuple[float, float]         # ACU supply m3/s
    n_active_range: Tuple[int, int]             # active ACUs of 6
    sample_count: int


# Load tiers (decoupled from geometry — cabinet layout is now an archetype, below).
# acu_flow_range lower bounds are set low enough that _adjust_flow_for_balance
# can choose a flow yielding heat_balance_ratio >= HEAT_BALANCE_MIN (0.3) for
# all tiers (Light lo=0.3 allows ideal flows ~0.12-0.59 to be respected).
TIERS: List[TierConfig] = [
    TierConfig("Light",       (0.05, 0.20), (0.4, 0.9), (0.03, 0.08), (20, 24), (0.3, 4.5), (2, 3), 100),
    TierConfig("Medium",      (0.20, 0.40), (0.8, 1.4), (0.06, 0.13), (18, 22), (1.0, 5.5), (3, 4), 150),
    TierConfig("Heavy",       (0.40, 0.65), (1.3, 1.9), (0.10, 0.20), (16, 20), (2.5, 7.0), (4, 6), 150),
    TierConfig("Super-heavy", (0.60, 0.85), (1.7, 2.2), (0.15, 0.27), (15, 18), (5.0, 8.0), (6, 6), 100),
]


# Geometry layout archetypes — a SMALL, interpretable set of cabinet layouts that
# replaces the old per-case random subset (which produced ~500 distinct, hard-to-
# review geometries). Each archetype edits the 4 rack aisles (rows 1-4, sizes
# 13/11/13/13): 'remove' empties a whole aisle; 'halve' decommissions the far half
# of an aisle. The dataset now spans only these 6 distinct layouts.
GEOMETRY_ARCHETYPES: List[Tuple[str, list]] = [
    ("full",                      []),                              # 50 racks
    ("one_aisle_half",            [("halve", 1)]),                  # 44
    ("one_aisle_decommissioned",  [("remove", 1)]),                # 37
    ("two_aisles_half",           [("halve", 1), ("halve", 4)]),   # 38
    ("aisle_gone_plus_half",      [("remove", 1), ("halve", 4)]),  # 31
    ("two_aisles_decommissioned", [("remove", 1), ("remove", 4)]), # 24
]
ARCHETYPE_OPS: Dict[str, list] = dict(GEOMETRY_ARCHETYPES)

# Cases per archetype (sum == total tier sample_count == 500). 'full' dominates;
# the five reduced layouts are a structured minority of missing-rack cases.
ARCHETYPE_COUNTS: Dict[str, int] = {
    "full": 300,
    "one_aisle_half": 50,
    "one_aisle_decommissioned": 50,
    "two_aisles_half": 35,
    "aisle_gone_plus_half": 35,
    "two_aisles_decommissioned": 30,
}

ACU_COOLING_CAPACITY_KW = 200
ACU_COOLANT_TEMP_C = 7
ACU_SET_POINT_TEMP_C = 20

RHO = 1.2
CP = 1005
DELTA_T_ASSUMED = 10
HEAT_BALANCE_MIN = 0.3
HEAT_BALANCE_MAX = 1.3

_HERE = _Path(__file__).resolve().parent
_DCDTUNI_ROOT = _HERE.parents[1]

DATASET_ROOT = str(_HERE)
TEMPLATE_CFDPROJ = str(_HERE / "template" / "GTC_template.cfdproj")
DCDTUNI_ROOT = str(_DCDTUNI_ROOT)
STLGEN_SERVER_URL = "http://localhost:8005"
