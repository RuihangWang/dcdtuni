import json
import random
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sampling_gtc as s
import server_types_gtc as st


def _tmpl():
    return json.load(open(st.TEMPLATE_CFDPROJ))


def test_rack_inventory_has_50():
    racks = s.load_rack_inventory(_tmpl())
    assert len(racks) == st.NUM_RACKS
    assert all(r.uid.startswith("m_") for r in racks)
    # rows are 1-indexed and contiguous
    rows = sorted({r.row for r in racks})
    assert rows[0] == 1


def test_acu_inventory_has_6():
    acus = s.load_acu_inventory(_tmpl())
    assert len(acus) == st.NUM_ACUS


def test_fill_rack_min_span_4():
    rng = random.Random(0)
    rc = s.fill_rack("m_1", "r1", 0.8, (1.0, 2.0), (0.1, 0.2), rng)
    assert rc.servers, "expected servers placed at high load"
    assert all(sv.span_u >= st.MIN_U_SPAN for sv in rc.servers)


def test_fill_rack_no_overlap_and_in_bounds():
    rng = random.Random(1)
    rc = s.fill_rack("m_1", "r1", 0.9, (1.0, 2.0), (0.1, 0.2), rng)
    occupied = []
    for sv in rc.servers:
        occupied += list(range(sv.start_u, sv.start_u + sv.span_u))
        assert sv.start_u >= st.SAFE_MARGIN_U + 1
        assert sv.start_u + sv.span_u - 1 <= st.SAFE_MARGIN_U + st.USABLE_SLOTS
    assert len(occupied) == len(set(occupied)), "servers overlap"


def test_fill_rack_power_flow_in_range():
    rng = random.Random(2)
    rc = s.fill_rack("m_1", "r1", 0.5, (1.3, 1.9), (0.10, 0.20), rng)
    for sv in rc.servers:
        assert 1300.0 <= sv.power_w <= 1900.0
        assert 0.10 <= sv.flow_m3s <= 0.20


def test_fill_rack_empty_at_zero_load():
    rng = random.Random(3)
    rc = s.fill_rack("m_1", "r1", 0.0, (1.0, 2.0), (0.1, 0.2), rng)
    assert rc.servers == []
