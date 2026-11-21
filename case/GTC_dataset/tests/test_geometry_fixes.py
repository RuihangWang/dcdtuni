import copy
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import generate_cfdproj as g
import server_types_gtc as st


def _tmpl():
    return json.load(open(st.TEMPLATE_CFDPROJ))


def _all_opacities(proj):
    out = []
    for grp in proj["constructions"].values():
        if not isinstance(grp, dict):
            continue
        for obj in grp.values():
            if isinstance(obj, dict):
                disp = obj.get("meta", {}).get("display")
                if isinstance(disp, dict) and "opacity" in disp:
                    out.append(disp["opacity"])
    return out


def test_template_starts_invisible():
    # guard: the template really does default to opacity 0
    assert set(_all_opacities(_tmpl())) == {0}


def _group_face_opacities(proj, grp):
    out = []
    for obj in proj["constructions"].get(grp, {}).values():
        faces = obj.get("geometry", {}).get("faces")
        if isinstance(faces, dict):
            for f in faces.values():
                if isinstance(f, dict) and "opacity" in f:
                    out.append(f["opacity"])
    return out


def test_visibility_scheme_equipment_opaque_enclosure_translucent():
    # Equipment (racks/ACUs) opaque; enclosure/structure (baffles/floors/walls/boxes) 50%.
    proj = _tmpl()
    g.apply_visibility(proj)
    for grp in ("racks", "acus"):
        assert set(o["meta"]["display"]["opacity"]
                   for o in proj["constructions"][grp].values()) == {1.0}
    for grp in ("baffles", "floors", "walls", "boxes"):
        assert set(o["meta"]["display"]["opacity"]
                   for o in proj["constructions"][grp].values()) == {0.5}


def test_planar_faces_get_perface_opacity():
    # baffles/floors render from per-face opacity, not meta.display.opacity; the
    # template bakes face.opacity=0 (invisible) -> must be lifted to 0.5, wallOpacities cleared.
    proj = _tmpl()
    assert set(_group_face_opacities(proj, "baffles")) == {0}  # guard: starts invisible
    g.apply_visibility(proj)
    assert set(_group_face_opacities(proj, "baffles")) == {0.5}
    assert set(_group_face_opacities(proj, "floors")) == {0.5}
    for grp in ("baffles", "floors", "walls"):
        for obj in proj["constructions"][grp].values():
            for f in obj.get("geometry", {}).get("faces", {}).values():
                assert f["wallOpacities"] == []


def test_modify_cfdproj_makes_visible():
    tmpl = _tmpl()
    acu0 = list(tmpl["constructions"]["acus"].keys())[0]
    present = list(tmpl["constructions"]["racks"].keys())[:5]
    plan = {"present_rack_uids": present,
            "racks": {u: {"servers": []} for u in present},
            "acu": {"supply_temp_C": 20, "flow_rate_m3s": 5,
                    "n_active": 1, "active_uids": [acu0]}}
    proj = g.modify_cfdproj(tmpl, "case_0000", plan, "/tmp/gtc")
    # nothing invisible anymore (no display.opacity or face.opacity == 0)
    assert 0 not in _all_opacities(proj)
    assert set(_all_opacities(proj)) == {1.0, 0.5}
    assert 0 not in _group_face_opacities(proj, "baffles")
    assert 0 not in _group_face_opacities(proj, "floors")


def test_outlet_blanking_voids_empty_slots_only():
    # Empty outlet (back) slots must not export blanking baffles (back[i]=True), but a
    # server-occupied slot keeps back[i]=False so its outlet patch (airflow BC) survives.
    # Inlet (front) panels are left untouched.
    tmpl = _tmpl()
    u = list(tmpl["constructions"]["racks"].keys())[0]
    front_before = list(tmpl["constructions"]["racks"][u]["deletedFaces"]["front"])
    # one 4U server at slot index 10..13
    plan = {"present_rack_uids": [u],
            "racks": {u: {"servers": [{"start_u": 10, "span_u": 4,
                                       "power_W": 500.0, "flow_m3s": 0.05}]}}}
    proj = copy.deepcopy(tmpl)
    g.apply_racks(proj, plan)
    df = proj["constructions"]["racks"][u]["deletedFaces"]
    # server slots 10..13 -> back face kept (False); every empty slot voided (True)
    for i in range(len(df["back"])):
        if 10 <= i < 14:
            assert df["back"][i] is False, f"slot {i} (server) back face deleted -> patch lost"
        else:
            assert df["back"][i] is True, f"empty slot {i} still has outlet blanking"
    assert df["front"] == front_before, "inlet panels must be untouched"


def test_server_uids_globally_unique():
    # cfdrun export rejects duplicate server uids across racks; uids must be project-wide unique.
    tmpl = _tmpl()
    present = list(tmpl["constructions"]["racks"].keys())[:8]
    plan = {"present_rack_uids": present,
            "racks": {u: {"servers": [
                {"start_u": 5, "span_u": 4, "power_W": 500.0, "flow_m3s": 0.05},
                {"start_u": 12, "span_u": 4, "power_W": 600.0, "flow_m3s": 0.06},
            ]} for u in present},
            "acu": {"supply_temp_C": 20, "flow_rate_m3s": 5, "n_active": 1,
                    "active_uids": [list(tmpl["constructions"]["acus"].keys())[0]]}}
    proj = g.modify_cfdproj(tmpl, "case_0000", plan, "/tmp/gtc")
    uids = [s["uid"]
            for r in proj["constructions"]["racks"].values()
            for s in r.get("constructions", {}).get("servers", {}).values()]
    assert uids, "expected servers"
    assert len(uids) == len(set(uids)), "server uids must be globally unique"
    # also the dict key matches the uid
    for r in proj["constructions"]["racks"].values():
        for key, s in r.get("constructions", {}).get("servers", {}).items():
            assert key == s["uid"]


def test_empty_rack_has_no_outlet_blanking():
    # a rack with no servers: every back slot voided (no blanking baffle at the outlet)
    tmpl = _tmpl()
    u = list(tmpl["constructions"]["racks"].keys())[0]
    plan = {"present_rack_uids": [u], "racks": {u: {"servers": []}}}
    proj = copy.deepcopy(tmpl)
    g.apply_racks(proj, plan)
    assert all(proj["constructions"]["racks"][u]["deletedFaces"]["back"])


def _cutout_sources(proj):
    srcs = set()
    for grp in proj["constructions"].values():
        if not isinstance(grp, dict):
            continue
        for obj in grp.values():
            if isinstance(obj, dict):
                for c in obj.get("geometry", {}).get("cutouts", []) or []:
                    if c.get("sourceName"):
                        srcs.add(c["sourceName"])
    return srcs


def test_deleted_rack_cutouts_removed():
    tmpl = _tmpl()
    all_uids = list(tmpl["constructions"]["racks"].keys())
    present = all_uids[:5]
    removed_names = {tmpl["constructions"]["racks"][u]["meta"]["name"] for u in all_uids[5:]}
    present_names = {tmpl["constructions"]["racks"][u]["meta"]["name"] for u in present}
    plan = {"present_rack_uids": present,
            "racks": {u: {"servers": []} for u in present}}
    proj = copy.deepcopy(tmpl)
    g.apply_racks(proj, plan)
    srcs = _cutout_sources(proj)
    # no cutout references a removed rack
    assert srcs.isdisjoint(removed_names)
    # present racks that had cutouts keep them; non-rack cutouts (crac/pillar) survive
    assert any(n in srcs for n in present_names)
    assert "pillar1" in srcs or "left_fan_wall_room_crac_1" in srcs


def test_floor_cutouts_pruned_for_removed_racks():
    tmpl = _tmpl()
    floor = next(o for o in tmpl["constructions"]["floors"].values()
                 if o["meta"]["name"] == "Room_Floor")
    before = len(floor["geometry"]["cutouts"])
    all_uids = list(tmpl["constructions"]["racks"].keys())
    present = all_uids[:10]
    plan = {"present_rack_uids": present, "racks": {u: {"servers": []} for u in present}}
    proj = copy.deepcopy(tmpl)
    g.apply_racks(proj, plan)
    floor2 = next(o for o in proj["constructions"]["floors"].values()
                  if o["meta"]["name"] == "Room_Floor")
    after = len(floor2["geometry"]["cutouts"])
    assert after < before  # 40 rack cutouts pruned (50 racks -> 10 present)
