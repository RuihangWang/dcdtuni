"""Cartesian (snap-off) must auto-resolve the thinnest fan-server slab.

Snap-off marks a server slab solid (so its outlet/inlet patch forms) only if a
cell centre falls inside it — guaranteed only when the rack-region cell pitch is
<= the slab height. Reproduced on Base_coldAisleIso: at rackRegionLevel=2 (cell
0.085 m > 1U 0.049 m) 2 of 4 outlets drop; at level 3 (cell 0.0425 m < slab) all
4 capture. `_cartesian_rack_region_level` raises rackRegionLevel so the pitch
always resolves the thinnest fan server. See
Ref_code/VALIDATION_AllCases_Cartesian_vs_Snapping.md §2.
"""
from backend.aircooling.runner.stlgen_runner import (
    _min_server_slab_height,
    _cartesian_rack_region_level,
)


def _rack(unit_h, servers):
    return {"geometry": {"unitHeight": unit_h}, "servers": servers}


def test_min_slab_height_picks_thinnest_fan_server():
    racks = {
        "R1": _rack(0.05, [{"type": "fanServer", "spanSlots": 4},
                           {"type": "fanServer", "spanSlots": 1}]),
        "R2": _rack(0.045, [{"type": "fanServer", "spanSlots": 2}]),
    }
    # thinnest = 1U of R1 = 0.05 (R2's 2U=0.09 is thicker)
    assert abs(_min_server_slab_height(racks) - 0.05) < 1e-9


def test_min_slab_ignores_nonfan_and_empty():
    assert _min_server_slab_height({}) is None
    racks = {"R1": _rack(0.05, [{"type": "blanking", "spanSlots": 1}])}
    assert _min_server_slab_height(racks) is None  # no fan servers


def test_region_level_raised_when_pitch_too_coarse():
    # Base_coldAisleIso: base 0.34, 1U 0.0489. level 2 -> cell 0.085 > slab (BAD).
    racks = {"R": _rack(0.0489, [{"type": "fanServer", "spanSlots": 1}])}
    # requested level 2 must be raised to 3 (cell 0.0425 <= 0.0489)
    assert _cartesian_rack_region_level(racks, 0.34, 2, min_cells=1) == 3


def test_region_level_not_lowered_when_already_fine():
    # Alibaba-hongchou: base 0.3, 1U 0.0522. level 3 -> cell 0.0375 < slab (OK).
    racks = {"R": _rack(0.0522, [{"type": "fanServer", "spanSlots": 1}])}
    need = _cartesian_rack_region_level(racks, 0.3, 3, min_cells=1)
    assert need == 3  # ceil(log2(0.3/0.0522))=3, already met, no change


def test_region_level_keeps_user_higher_request():
    racks = {"R": _rack(0.0489, [{"type": "fanServer", "spanSlots": 1}])}
    # user asked level 5 (finer than needed 3) -> keep 5
    assert _cartesian_rack_region_level(racks, 0.34, 5, min_cells=1) == 5


def test_region_level_no_servers_unchanged():
    assert _cartesian_rack_region_level({}, 0.34, 3) == 3


def test_region_level_raise_is_capped_by_rack_cell_min_size():
    # DAML: base 0.2, 1U slab 0.045 -> need level 3 (25 mm). A 30 mm floor
    # caps the raise at level 2 (50 mm); a 17.5 mm floor (6Sigma) lets 3 through.
    racks = {"R": _rack(0.045, [{"type": "fanServer", "spanSlots": 1}])}
    assert _cartesian_rack_region_level(racks, 0.2, 2, min_cells=1) == 3
    assert _cartesian_rack_region_level(racks, 0.2, 2, min_cells=1, min_cell_size=0.030) == 2
    assert _cartesian_rack_region_level(racks, 0.2, 2, min_cells=1, min_cell_size=0.0175) == 3
    # the cap never lowers an explicit request finer than itself
    assert _cartesian_rack_region_level(racks, 0.2, 4, min_cells=1, min_cell_size=0.030) == 4
    # None / 0 = uncapped
    assert _cartesian_rack_region_level(racks, 0.2, 2, min_cells=1, min_cell_size=0) == 3


def test_cell_exactly_one_unit_is_not_raised():
    # base 4U = 0.1778 m: level 2 is exactly the 1U slab; a unitHeight that is
    # 0.04445 up to rounding (0.044449999999999996 from 0.1778 / 4 in the
    # editor) must not push the rack to level 3 (2.2 mm cells, 2.9x the cells)
    racks = {"r": {"geometry": {"unitHeight": 0.044449999999999996},
                   "servers": [{"type": "fanServer", "spanSlots": 1}]}}
    assert _cartesian_rack_region_level(racks, 0.1778, 2) == 2
    assert _cartesian_rack_region_level(racks, 0.1780, 2) == 3   # a real excess still raises
