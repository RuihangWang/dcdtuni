import copy
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import generate_cfdproj as g
import server_types_gtc as st


def _tmpl():
    return json.load(open(st.TEMPLATE_CFDPROJ))


def test_absent_racks_deleted():
    tmpl = _tmpl()
    all_uids = list(tmpl["constructions"]["racks"].keys())
    present = all_uids[:10]
    plan = {"present_rack_uids": present,
            "racks": {u: {"servers": []} for u in present}}
    proj = copy.deepcopy(tmpl)
    g.apply_racks(proj, plan)
    assert set(proj["constructions"]["racks"].keys()) == set(present)


def test_present_rack_servers_rebuilt():
    tmpl = _tmpl()
    present = list(tmpl["constructions"]["racks"].keys())[:1]
    uid = present[0]
    plan = {"present_rack_uids": present, "racks": {uid: {"servers": [
        {"start_u": 5, "span_u": 4, "power_W": 1500.0, "flow_m3s": 0.12},
        {"start_u": 12, "span_u": 6, "power_W": 1800.0, "flow_m3s": 0.15},
    ]}}}
    proj = copy.deepcopy(tmpl)
    g.apply_racks(proj, plan)
    servers = proj["constructions"]["racks"][uid]["constructions"]["servers"]
    assert len(servers) == 2
    vals = list(servers.values())
    assert vals[0]["geometry"]["slotPosition"] == 5
    assert vals[0]["geometry"]["slotOccupation"] == 4
    assert vals[0]["cooling"]["power"] == 1500.0
    assert vals[0]["cooling"]["volumeFlowRate"] == 0.12
    # outlet patch ids are unique across the rebuilt servers
    pids = [p["id"] for sv in vals for p in sv["faces"]["outlet"]["patches"]]
    assert len(pids) == len(set(pids))


def test_empty_rack_has_no_servers():
    tmpl = _tmpl()
    present = list(tmpl["constructions"]["racks"].keys())[:1]
    uid = present[0]
    plan = {"present_rack_uids": present, "racks": {uid: {"servers": []}}}
    proj = copy.deepcopy(tmpl)
    g.apply_racks(proj, plan)
    assert proj["constructions"]["racks"][uid]["constructions"]["servers"] == {}
