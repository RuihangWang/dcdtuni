"""Prototype cache keying. Pure — prototype_key never touches pxr."""
import os
import sys
import copy

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..",
                                "kit/extensions/dc.cfd_bridge"))
from dc.cfd_bridge.cfdproj import read_cfdproj                    # noqa: E402
from dc.cfd_bridge.usdgen import prototype_key, GEN_VERSION       # noqa: E402
import dc.cfd_bridge.usdgen as usdgen                             # noqa: E402

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../.."))
BASE_LEAKAGE = os.path.join(REPO, "case/Base_leakage/Base_leakage.cfdproj")


def test_position_and_rotation_do_not_change_the_key():
    """The property that makes sharing possible at all."""
    s = read_cfdproj(BASE_LEAKAGE)
    r = [p for p in s.placements if p.cls == "racks"][0]
    moved = copy.deepcopy(r)
    moved.origin = (99.0, 5.0, -3.0)
    moved.quat = (0.0, 1.0, 0.0, 0.0)
    moved.name = "somewhere_else"
    assert prototype_key(moved) == prototype_key(r)


def test_size_change_does_change_the_key():
    s = read_cfdproj(BASE_LEAKAGE)
    r = [p for p in s.placements if p.cls == "racks"][0]
    taller = copy.deepcopy(r)
    taller.size = (r.size[0], r.size[1] + 0.5, r.size[2])
    assert prototype_key(taller) != prototype_key(r)


def test_server_layout_changes_the_key():
    """Two same-size racks with different equipment are different geometry."""
    s = read_cfdproj(BASE_LEAKAGE)
    r = [p for p in s.placements if p.cls == "racks"][0]
    other = copy.deepcopy(r)
    other.servers[0].slot_position += 3
    assert prototype_key(other) != prototype_key(r)


def test_gen_version_busts_every_key():
    s = read_cfdproj(BASE_LEAKAGE)
    r = s.placements[0]
    before = prototype_key(r)
    usdgen.GEN_VERSION = GEN_VERSION + "-next"
    try:
        assert prototype_key(r) != before
    finally:
        usdgen.GEN_VERSION = GEN_VERSION


def test_base_leakage_shares_prototypes():
    """16 objects, but far fewer distinct prototypes — the point of content addressing."""
    s = read_cfdproj(BASE_LEAKAGE)
    keys = {prototype_key(p, s.placements) for p in s.placements}
    assert len(s.placements) == 16
    assert len(keys) < len(s.placements), "no sharing at all means the key is too specific"
    # The 3 floors are the same 4.4 x 6 panel, but each is a different surface: the
    # raised floor (with the leakage tile this case is named for), the slab and the
    # ceiling. The old hand-listed key shared one prototype between the slab and the
    # ceiling, so one of them was drawn in the other's material.
    floors = {prototype_key(p, s.placements) for p in s.placements if p.cls == "floors"}
    assert len(floors) == 3
    # Rack_3 and Rack_4 hold the same servers at the same size (Rack_4 is a mirrored
    # copy): one prototype. Racks 1 and 2 differ in height or equipment.
    racks = {p.name: prototype_key(p, s.placements) for p in s.placements if p.cls == "racks"}
    assert racks["Default_Rack_3"] == racks["Default_Rack_4"]
    assert len(set(racks.values())) == 3


def test_a_surface_change_changes_the_key():
    """The key is computed from the emitted quads, so a material input cannot be left
    out: a patch colour, a face picture or a face opacity is a new prototype."""
    s = read_cfdproj(BASE_LEAKAGE)
    acu = [p for p in s.placements if p.cls == "acus"][0]
    base = prototype_key(acu, s.placements)
    recoloured = copy.deepcopy(acu)
    recoloured.faces["top"]["patches"][0]["color"] = "#00ff00"
    assert prototype_key(recoloured, s.placements) != base
    pictured = copy.deepcopy(acu)
    pictured.faces["front"]["textureUrl"] = "/textures/electric-box/panel_lamps.jpg"
    assert prototype_key(pictured, s.placements) != base
    faded = copy.deepcopy(acu)
    faded.faces["left"]["opacity"] = 0.3
    assert prototype_key(faded, s.placements) != base
