"""The model-tree manifest: the web UI's only source of tree rows.

The bug this replaces: the tree was a hardcoded list of the GTC scene's groups, so a
.cfdproj build showed a "Floor" row whose checkbox sent setObjectVisible("Floor") — a
group a .cfdproj build never creates. The backend logged "unknown group" and did
nothing, which is what "the Floor checkbox has no effect" was.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..",
                                "kit/extensions/dc.cfd_bridge"))
from dc.cfd_bridge.cfdproj import read_cfdproj                    # noqa: E402
from dc.cfd_bridge import scenetree as T                          # noqa: E402
from dc.cfd_bridge import usdgen                                  # noqa: E402

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../.."))


@pytest.fixture(scope="module")
def scene():
    return read_cfdproj(os.path.join(REPO, "case/Base_leakage/Base_leakage.cfdproj"))


def _fake_groups(scene):
    """The keys build_scene would produce, without needing a USD stage.

    Mirrors usdgen.build_scene's naming: a per-class Scope key plus one key per object,
    de-duplicated with a global counter.
    """
    groups, counts = {}, {}
    for p in scene.placements:
        group = p.cls.capitalize()
        safe = usdgen._safe_name(p.name) or p.uid
        n = counts.get(safe, 0)
        counts[safe] = n + 1
        if n:
            safe = f"{safe}_{n}"
        groups.setdefault(group, []).append(f"/World/Geometry/{group}/{safe}")
        groups[safe] = [f"/World/Geometry/{group}/{safe}"]
    return groups


def test_every_tree_id_is_a_real_group_key(scene):
    """The invariant the whole manifest exists for: no row without a prim behind it."""
    groups = _fake_groups(scene)
    m = T.build_manifest(scene, groups)
    for c in m["categories"]:
        assert c["id"] in groups, f"category {c['id']} has no prims"
        for it in c["items"]:
            assert it["id"] in groups, f"object {it['id']} has no prims"


def test_no_gtc_only_rows_leak_into_a_cfdproj_tree(scene):
    """'Floor' (GTC) vs 'Floors' (cfdproj) — the exact confusion being fixed."""
    m = T.build_manifest(scene, _fake_groups(scene))
    ids = {c["id"] for c in m["categories"]}
    assert "Floors" in ids
    assert not (ids & {"Floor", "Room", "Containment", "Ducts", "Partitions", "ACUs"})


def test_categories_follow_the_editor_order_and_carry_counts(scene):
    m = T.build_manifest(scene, _fake_groups(scene))
    got = [(c["id"], c["count"]) for c in m["categories"]]
    order = [c[0] for c in got]
    assert order == [g for g in ["Boxes", "Racks", "Acus", "Walls", "Baffles", "Floors"]
                     if g in order], "editor CATEGORY_ORDER, not alphabetical"
    assert dict(got)["Racks"] == 4 and dict(got)["Acus"] == 2
    assert m["objects"] == sum(c for _, c in got)


def test_disabled_objects_open_unticked(scene):
    """meta.enabled=false must reach the checkbox as its INITIAL state.

    GTC_2026 disables all four of its walls. The old UI showed them ticked and relied on
    scraping a `hiddenGroups=` line out of the Kit log to correct itself afterwards.
    """
    gtc = read_cfdproj(os.path.join(REPO, "case/GTC2026/GTC_2026.cfdproj"))
    m = T.build_manifest(gtc, _fake_groups(gtc))
    walls = [c for c in m["categories"] if c["id"] == "Walls"][0]
    assert walls["items"], "GTC_2026 has walls"
    assert all(it["enabled"] is False for it in walls["items"])
    # …and a project that enables its objects must NOT come up unticked.
    m2 = T.build_manifest(scene, _fake_groups(scene))
    racks = [c for c in m2["categories"] if c["id"] == "Racks"][0]
    assert all(it["enabled"] is True for it in racks["items"])


def test_rack_children_mirror_the_editor(scene):
    """Equipments + Partitions — NOT six face nodes.

    A rack's `geometry.faces` is an empty array in every tracked project, so the
    editor's face loop emits nothing and these two groups are the rack's real children.
    Reading getTreeHtmlForModel without checking the data produced a plausible
    six-face tree the editor does not draw; the screenshot is what caught it.
    """
    m = T.build_manifest(scene, _fake_groups(scene))
    rack = [c for c in m["categories"] if c["id"] == "Racks"][0]["items"][0]
    assert [c["label"] for c in rack["children"]] == ["Equipments", "Partitions"]
    eq, part = rack["children"]
    assert eq["badge"] == "1" and part["badge"] == "41"
    assert part["children"][0]["label"] == "partition_1 (deleted)"

    srv = eq["children"][0]
    assert srv["badge"] == "fan-server · 14U"
    assert srv["color"] == "#d14b4b", "the dot takes the equipment's inlet colour"
    faces = srv["children"]
    assert [f["label"] for f in faces] == ["Inlet", "Outlet", "Top", "Bottom",
                                           "Left", "Right"]
    assert faces[0]["badge"] == "" and faces[1]["badge"] == "1 patch"
    patch = faces[1]["children"][0]
    assert patch["label"] == "Patch 1" and patch["badge"] == "25×100%"
    # None of these ids is a group key — they are prototype-internal, and the Inspector
    # must not offer to toggle one.
    assert all("/" in n["id"] for n in (eq, part, srv, faces[1], patch))


def test_acu_children_are_faces_with_patch_counts(scene):
    """The editor's generic branch: one node per face, badged with its patch count."""
    m = T.build_manifest(scene, _fake_groups(scene))
    acu = [c for c in m["categories"] if c["id"] == "Acus"][0]["items"][0]
    got = {f["label"]: f["badge"] for f in acu["children"]}
    assert set(got) >= {"Front", "Back", "Top", "Bottom"}
    assert sum(int(v) for v in got.values()) == 2, "one supply + one return"


def test_floor_patches_hang_straight_off_the_model(scene):
    """wall/baffle/floor take the OTHER branch — no face node in between."""
    m = T.build_manifest(scene, _fake_groups(scene))
    floors = [c for c in m["categories"] if c["id"] == "Floors"][0]["items"]
    raised = [f for f in floors if f["label"] == "Raised_Floor_copy"][0]
    assert raised["children"], "the leakage opening is a patch on the raised floor"
    assert all(not k["children"] for k in raised["children"])


def test_duplicate_names_get_the_same_suffix_build_scene_uses():
    """Two objects with one name must not collapse onto one tree row."""
    import copy
    sc = read_cfdproj(os.path.join(REPO, "case/Base_leakage/Base_leakage.cfdproj"))
    sc = copy.deepcopy(sc)
    for p in sc.placements:
        if p.cls == "racks":
            p.name = "Rack"
    m = T.build_manifest(sc, _fake_groups(sc))
    racks = [c for c in m["categories"] if c["id"] == "Racks"][0]
    assert [it["id"] for it in racks["items"]] == ["Rack", "Rack_1", "Rack_2", "Rack_3"]


def test_manifest_round_trips_through_the_file(scene, tmp_path):
    import json
    m = T.build_manifest(scene, _fake_groups(scene))
    p = T.write_manifest(m, str(tmp_path / "tree.json"))
    with open(p) as f:
        assert json.load(f) == m


def test_gtc_manifest_only_lists_groups_that_exist():
    groups = {"Room": ["/a"], "Racks": ["/b"], "r1": ["/b/r1"]}
    m = T.gtc_manifest(groups, ["r1", "r_missing"])
    assert [c["id"] for c in m["categories"]] == ["Room", "Racks"]
    assert [it["id"] for it in m["categories"][1]["items"]] == ["r1"]
