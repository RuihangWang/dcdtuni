from dataclasses import replace

from agent.scenarios.air.rules import aisles, validate
from agent.scenarios.air.spec import Design, PerimeterUnit, Row, Setpoint, Task, TileGroup

TASK = Task(room_x_m=12.0, room_y_m=9.0, room_h_m=3.6, n_racks=8, n_perimeter_units=2, n_inrow_units=4)
INROW_SLOTS = ("RACK", "RACK", "IRC", "RACK", "RACK", "IRC")


def _inrow(arch="SLAB_INROW_HAC", front_a="-", front_b="+", pairs=(("R1", "R2"),)):
    rows = (
        Row("R1", "x", 3.0, 2.0, front_a, INROW_SLOTS),
        Row("R2", "x", 5.4, 2.0, front_b, INROW_SLOTS),
    )
    sp = tuple(Setpoint(u, 0.7, 22.0) for u in ("R1:2", "R1:5", "R2:2", "R2:5"))
    return Design(arch, rows, containment_pairs=pairs, setpoints=sp)


def _raised(arch="RF_PERIM_CAC", tiles=(TileGroup("R1", "R2", 6),), center_b=5.6):
    rows = (
        Row("R1", "x", 3.0, 2.0, "+", ("RACK",) * 4),
        Row("R2", "x", center_b, 2.0, "-", ("RACK",) * 4),
    )
    units = (PerimeterUnit("C1", "x0", 3.0), PerimeterUnit("C2", "x0", 6.0))
    sp = (Setpoint("C1", 0.8, 20.0), Setpoint("C2", 0.8, 20.0))
    pairs = (("R1", "R2"),) if arch.endswith("CAC") else ()
    return Design(arch, rows, containment_pairs=pairs, perimeter_units=units, tiles=tiles, setpoints=sp)


def _codes(design, task=TASK):
    return {i.code for i in validate(design, task)}


def test_valid_inrow_hot_aisle_design_passes():
    assert validate(_inrow(), TASK) == []
    assert [a.kind for a in aisles(_inrow(), TASK)] == ["hot"]


def test_valid_raised_floor_cold_aisle_design_passes():
    assert validate(_raised(), TASK) == []


def test_containment_must_match_aisle_type():
    assert _codes(_inrow(arch="SLAB_INROW_CAC")) == {"CONTAINMENT_WRONG_AISLE"}


def test_cold_aisle_containment_needs_a_supply_path():
    issues = validate(replace(_inrow(), architecture="SLAB_PERIM_CAC"), TASK)
    assert [i.code for i in issues] == ["ARCH_FORBIDDEN"]
    assert "supply path" in issues[0].message


def test_later_stage_architecture_is_rejected_until_enabled():
    assert _codes(_raised(arch="RF_PERIM_HAC")) == {"ARCH_NOT_SUPPORTED_YET"}
    assert "ARCH_NOT_SUPPORTED_YET" not in _codes(_raised(arch="RF_PERIM_HAC"), replace(TASK, enable_later_stage=True))


def test_rows_facing_the_same_way_are_rejected():
    assert "FRONT_TO_BACK" in _codes(_inrow(front_a="+", front_b="+", pairs=()))


def test_narrow_cold_aisle_is_rejected():
    assert "AISLE_TOO_NARROW" in _codes(_raised(center_b=5.2))  # 2.2 m between centres -> 1.0 m aisle


def test_raised_floor_cold_aisle_needs_tiles():
    assert "COLD_AISLE_WITHOUT_TILES" in _codes(_raised(tiles=()))


def test_tiles_on_slab_and_tile_capacity():
    assert "TILES_ON_SLAB" in _codes(replace(_inrow(), tiles=(TileGroup("R1", "R2", 2),)))
    assert "TILES_COUNT" in _codes(_raised(tiles=(TileGroup("R1", "R2", 99),)))


def test_counts_setpoints_and_room_bounds():
    assert "RACK_COUNT" in _codes(_inrow(), replace(TASK, n_racks=10))
    d = _inrow()
    assert "SETPOINT_MISSING" in _codes(replace(d, setpoints=d.setpoints[:-1]))
    assert "SETPOINT_RANGE" in _codes(replace(d, setpoints=(Setpoint("R1:2", 0.1, 30.0),) + d.setpoints[1:]))
    far = replace(d, rows=(replace(d.rows[0], start_m=9.0), d.rows[1]))
    assert "OUT_OF_ROOM" in _codes(far)


def test_nonfinite_geometry_invalid_axis_and_duplicate_rows_reject_before_compile():
    d=_inrow()
    assert "ROW_NONFINITE" in _codes(replace(d,rows=(replace(d.rows[0],start_m=float("nan")),d.rows[1])))
    assert "ROW_AXIS" in _codes(replace(d,rows=(replace(d.rows[0],axis="z"),d.rows[1])))
    assert "ROW_ID_DUPLICATE" in _codes(replace(d,rows=(d.rows[0],replace(d.rows[1],id=d.rows[0].id))))

def test_containment_and_tile_references_cannot_escape_the_inventory():
    d=_inrow()
    assert "CONTAINMENT_PAIR" in _codes(replace(d,containment_pairs=(("R1","missing"),)))
    assert "CONTAINMENT_DUPLICATE" in _codes(replace(d,containment_pairs=(("R1","R2"),("R2","R1"))))
    assert "TILES_COUNT" in _codes(_raised(tiles=(TileGroup("R1","R2",-1),)))
