import copy
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import generate_cfdproj as g
import server_types_gtc as st


def _tmpl():
    return json.load(open(st.TEMPLATE_CFDPROJ))


def _supply_patch(acu):
    for f in acu["geometry"]["faces"].values():
        for p in f.get("patches", []):
            if p.get("physics", {}).get("role") == "supply":
                return p
    return None


def test_active_acu_supply_set():
    tmpl = _tmpl()
    acu_uids = list(tmpl["constructions"]["acus"].keys())
    plan = {"acu": {"supply_temp_C": 17.5, "flow_rate_m3s": 5.5,
                    "n_active": 1, "active_uids": [acu_uids[0]]}}
    proj = copy.deepcopy(tmpl)
    g.apply_acus(proj, plan)
    sp = _supply_patch(proj["constructions"]["acus"][acu_uids[0]])
    assert sp["physics"]["temperature"] == 17.5
    assert sp["physics"]["volumeFlowRate"] == 5.5


def test_inactive_acu_patches_stripped():
    tmpl = _tmpl()
    acu_uids = list(tmpl["constructions"]["acus"].keys())
    plan = {"acu": {"supply_temp_C": 17.5, "flow_rate_m3s": 5.5,
                    "n_active": 1, "active_uids": [acu_uids[0]]}}
    proj = copy.deepcopy(tmpl)
    g.apply_acus(proj, plan)
    off = proj["constructions"]["acus"][acu_uids[1]]
    assert _supply_patch(off) is None
    assert all(f.get("patches", []) == [] for f in off["geometry"]["faces"].values())


def test_paths_rewritten():
    tmpl = _tmpl()
    proj = copy.deepcopy(tmpl)
    g.apply_paths(proj, "case_0007", "/scratch/x/GTC_dataset/case_0007")
    assert proj["meta"]["casePath"] == "/scratch/x/GTC_dataset/case_0007"
    assert proj["meta"]["projectName"] == "GTC_dataset_0007"
    assert proj["meta"]["exportFileName"] == "GTC_dataset_0007"


def test_runDefaults_untouched():
    tmpl = _tmpl()
    before = json.dumps(tmpl["runDefaults"])
    proj = g.modify_cfdproj(tmpl, "case_0000",
                            {"present_rack_uids": [], "racks": {},
                             "acu": {"supply_temp_C": 20, "flow_rate_m3s": 5,
                                     "n_active": 1, "active_uids": [
                                         list(tmpl["constructions"]["acus"].keys())[0]]}},
                            output_dir="/tmp/gtc")
    assert json.dumps(proj["runDefaults"]) == before
