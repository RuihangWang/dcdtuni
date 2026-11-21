# case/GTC_dataset/tests/test_select_racks.py
import json
import random
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sampling_gtc as s
import server_types_gtc as st


def _inv():
    return s.load_rack_inventory(json.load(open(st.TEMPLATE_CFDPROJ)))


def _row_sizes(inv):
    sizes = {}
    for r in inv:
        sizes[r.row] = sizes.get(r.row, 0) + 1
    return sizes  # {1:13, 2:11, 3:13, 4:13}


def test_full_archetype_keeps_all():
    inv = _inv()
    present = s.apply_archetype(st.ARCHETYPE_OPS["full"], inv)
    assert len(present) == len(inv) == 50
    assert set(present) == {r.uid for r in inv}


def test_remove_aisle_drops_whole_row():
    inv = _inv()
    sizes = _row_sizes(inv)
    present = set(s.apply_archetype([("remove", 1)], inv))
    rows_left = {r.row for r in inv if r.uid in present}
    assert 1 not in rows_left
    assert len(present) == len(inv) - sizes[1]  # row 1 = 13 -> 37 present


def test_halve_aisle_keeps_near_half():
    inv = _inv()
    sizes = _row_sizes(inv)
    present = set(s.apply_archetype([("halve", 1)], inv))
    kept_in_row1 = [r for r in inv if r.row == 1 and r.uid in present]
    assert len(kept_in_row1) == (sizes[1] + 1) // 2  # 13 -> keep 7
    # the kept racks are the near-x half (smaller x), far half decommissioned
    row1_sorted = sorted((r for r in inv if r.row == 1), key=lambda r: r.x)
    assert {r.uid for r in kept_in_row1} == {r.uid for r in row1_sorted[: (sizes[1] + 1) // 2]}


def test_archetype_is_deterministic():
    inv = _inv()
    a = s.apply_archetype(st.ARCHETYPE_OPS["aisle_gone_plus_half"], inv)
    b = s.apply_archetype(st.ARCHETYPE_OPS["aisle_gone_plus_half"], inv)
    assert a == b


def test_expected_cabinet_counts():
    inv = _inv()
    counts = {name: len(s.apply_archetype(ops, inv)) for name, ops in st.ARCHETYPE_OPS.items()}
    assert counts == {
        "full": 50, "one_aisle_half": 44, "one_aisle_decommissioned": 37,
        "two_aisles_half": 38, "aisle_gone_plus_half": 31,
        "two_aisles_decommissioned": 24,
    }


def test_assignment_covers_500_and_matches_counts():
    assign = s.build_archetype_assignment(random.Random(42))
    assert len(assign) == 500
    got = {name: assign.count(name) for name in st.ARCHETYPE_COUNTS}
    assert got == dict(st.ARCHETYPE_COUNTS)
