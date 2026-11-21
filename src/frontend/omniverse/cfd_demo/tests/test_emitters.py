"""Emitter parity with the frontend's quad rules, on Base_leakage.

Each quad names the materials.json objectKind the editor gives that face and the
colour the editor hands the material (None for a renderer default); the manifest's
colour rules decide what shows (dc.cfd_bridge.material_library).
"""
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..",
                                "kit/extensions/dc.cfd_bridge"))
from dc.cfd_bridge.cfdproj import read_cfdproj, to_world          # noqa: E402
from dc.cfd_bridge import emitters as E                            # noqa: E402
from dc.cfd_bridge import material_library as lib                  # noqa: E402

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../.."))
BASE_LEAKAGE = os.path.join(REPO, "case/Base_leakage/Base_leakage.cfdproj")


@pytest.fixture(scope="module")
def scene():
    return read_cfdproj(BASE_LEAKAGE)


def _by_name(scene, n):
    return [p for p in scene.placements if p.name == n][0]


def test_deleted_face_means_open_not_panel(scene):
    """The sense that decides whether Base_leakage's racks look sealed or open."""
    r = _by_name(scene, "Default_Rack_1")
    # Server occupies 15..28; everything else is deletedFaces=True => void.
    assert E.slot_status(r, "front", 20) == "equipment"
    assert E.slot_status(r, "front", 0) == "void"
    assert E.slot_status(r, "front", 41) == "void"
    # So there are NO blank panels on this rack at all.
    assert E.merged_empty_ranges(r, "front") == []


def test_rack_shell_and_cavity_walls_are_all_present(scene):
    """The frame is 20 pieces, not 4 — buildRackFrameGeometry's full list.

    Emitting only the outer shell leaves the recess open-sided: with inletOffset 0.1 the
    rack has a 10 cm notch at the front whose left and right walls are the
    inlet_cavity_* pair. Missing them is what made the recess read as a hole.
    """
    r = _by_name(scene, "Default_Rack_1")
    names = {q.name.replace(r.name + "_", "") for q in E.emit_rack(r)}
    assert {"frame_outer_left", "frame_outer_right", "frame_outer_top",
            "frame_outer_bottom"} <= names
    assert {"frame_inner_left", "frame_inner_right",
            "frame_inner_floor", "frame_inner_ceiling"} <= names
    assert {"frame_inlet_cavity_left", "frame_inlet_cavity_right",
            "frame_outlet_cavity_left", "frame_outlet_cavity_right"} <= names
    # The rails flanking the slot column, front and back, are 19-inch mounting rails:
    # one rail section per rack unit, as the editor draws them.
    assert {"frame_front_strip_left", "frame_front_strip_right"} <= names
    by = {q.name.replace(r.name + "_", ""): q for q in E.emit_rack(r)}
    for rail in ("frame_front_strip_left", "frame_back_strip_right"):
        assert (by[rail].material, by[rail].uv_mode, by[rail].u_count) == ("rack-rail", "tile-per-u", r.slot)
    assert by["frame_inner_floor"].material == "rack-bottom"
    assert by["frame_inner_ceiling"].material == "rack-top"
    # No blank panels, because every non-server slot on this rack is an open hole.
    assert not any(q.material.startswith("blank-panel") for q in E.emit_rack(r))
    assert all(q.double_sided for q in E.emit_rack(r) if "_frame_" in q.name), \
        "a rack frame is hollow; every frame piece must draw from both sides"


def test_frame_colour_is_the_manifest_powder_coat(scene):
    """The editor's frame greys are renderer defaults its rack kinds ignore, so none
    travel: every frame piece shows its kind's base colour (matte black powder coat)."""
    r = _by_name(scene, "Default_Rack_1")
    frame = [q for q in E.emit_rack(r) if "_frame_" in q.name]
    assert frame and all(q.color is None and not q.keep_user_color for q in frame)
    for q in frame:
        assert lib.color_for(q.material, q.color, q.keep_user_color) == lib.material(q.material)["baseColor"]


def test_server_is_a_six_faced_box(scene):
    """All six faces, all double-sided, as the editor now draws a server (WP-W6): a
    transparent server shows its far end and its sides. The inlet is the bezel, one
    bezel per U; the outlet the rear panel; the rest painted chassis."""
    r = _by_name(scene, "Default_Rack_1")
    s = r.servers[0]
    faces = {q.name.replace(f"{r.name}_{s.uid}_", "")
             for q in E.emit_rack(r) if f"_{s.uid}_" in q.name}
    assert {"inlet", "outlet", "top", "bottom", "left", "right"} <= faces
    by = {q.name.rsplit("_", 1)[-1]: q for q in E.emit_rack(r)
          if f"_{s.uid}_" in q.name}
    assert (by["inlet"].material, by["inlet"].uv_mode, by["inlet"].u_count) == \
        ("server-front", "tile-per-u", s.slot_occupation)
    assert (by["outlet"].material, by["outlet"].uv_mode) == ("server-rear-socket", "tile-per-u")
    assert {by[f].material for f in ("top", "bottom", "left", "right")} == {"server-chassis"}
    assert all(q.double_sided for q in by.values())
    # The authored face colour travels; it is one of the editor's defaults here, so the
    # bezel keeps the manifest colour.
    assert by["inlet"].color == s.faces["inlet"]["color"]
    assert lib.color_for("server-front", by["inlet"].color) == lib.material("server-front")["baseColor"]


def test_server_sits_at_the_recessed_depth(scene):
    """inletOffset/outletOffset move the server INTO the rack. Issue 3, pinned.

    Base_leakage recesses 0.1 m at the inlet and 0.3 m at the outlet on a 1.2 m rack, so
    a server flush with the shell would poke 10 cm out of the cold aisle and 30 cm out of
    the hot one.
    """
    r = _by_name(scene, "Default_Rack_1")
    s = r.servers[0]
    D = r.size[2]
    assert (r.inlet_offset, r.outlet_offset) == (0.1, 0.3)
    by = {q.name.rsplit("_", 1)[-1]: q for q in E.emit_rack(r)
          if f"_{s.uid}_" in q.name}
    assert [pt[2] for pt in by["inlet"].pts] == [pytest.approx(0.1)] * 4
    assert [pt[2] for pt in by["outlet"].pts] == [pytest.approx(D - 0.3)] * 4
    zs = [pt[2] for pt in by["top"].pts]
    assert (min(zs), max(zs)) == (pytest.approx(0.1), pytest.approx(D - 0.3))


def test_server_face_spans_its_u_slots(scene):
    r = _by_name(scene, "Default_Rack_1")
    srv = r.servers[0]
    q = [x for x in E.emit_rack(r) if x.material == "server-front"][0]
    ys = [pt[1] for pt in q.pts]
    lo_expected = r.margin_bottom + srv.slot_position * r.slot_height
    hi_expected = r.margin_bottom + (srv.slot_position + srv.slot_occupation) * r.slot_height
    assert min(ys) == pytest.approx(lo_expected, abs=1e-9)
    assert max(ys) == pytest.approx(hi_expected, abs=1e-9)


def test_server_outlet_patch_is_a_fraction_of_the_face(scene):
    """Server patches are NORMALISED, unlike a model's own metre-rect face patches."""
    r = _by_name(scene, "Default_Rack_1")
    s = r.servers[0]
    patch = (s.faces or {})["outlet"]["patches"][0]
    assert (patch["x"], patch["w"]) == (0.75, 0.25)
    face = [q for q in E.emit_rack(r) if q.name.endswith(f"{s.uid}_outlet")][0]
    isl = [q for q in E.emit_rack(r) if q.name.endswith(f"{s.uid}_outlet_p0")][0]
    fw = max(pt[0] for pt in face.pts) - min(pt[0] for pt in face.pts)
    iw = max(pt[0] for pt in isl.pts) - min(pt[0] for pt in isl.pts)
    assert iw == pytest.approx(0.25 * fw), "0.25 is a quarter of the face, not 25 cm"
    # The exhaust mesh keeps its material colour and tiles every 0.1 m, in the face's
    # own coordinates (the patch starts three quarters of the way along).
    assert (isl.material, isl.color, isl.keep_user_color) == ("server-outlet-mesh", None, False)
    assert (isl.uv_mode, isl.tile_m) == ("physical", 0.1)
    assert min(u for u, _ in isl.uv) == pytest.approx(0.75 * fw)


def test_quads_stay_inside_the_local_box(scene):
    """No emitted quad may escape its own [0,W]x[0,H]x[0,D] — catches sign errors.

    The slack is 2 mm on every axis, not zero: ACU supply/return patches are authored
    1 mm proud of their host face on purpose, so they win the depth test instead of
    z-fighting. A real sign error is off by a whole dimension, not by a millimetre.
    """
    SLACK = 2e-3
    for p in scene.placements:
        W, H, D = p.size
        for q in E.emit(p):
            for (x, y, z) in q.pts:
                assert -SLACK <= x <= W + SLACK, f"{p.name}/{q.name} x={x} vs W={W}"
                assert -SLACK <= y <= H + SLACK, f"{p.name}/{q.name} y={y} vs H={H}"
                assert -SLACK <= z <= D + SLACK, f"{p.name}/{q.name} z={z} vs D={D}"


def test_acu_supply_and_return_patches_are_emitted(scene):
    """_buildSingleBoxFace: a supply island is the louvre, a return the perforated
    grille. Each quad carries the PATCH's colour, but both grilles are photographed
    sets that show their own colour, not the patch's (b4576d584)."""
    a = _by_name(scene, "ACU_1")
    quads = E.emit(a)
    mats = [q.material for q in quads]
    assert mats.count("acu-supply") == 1, "bottom downflow supply"
    assert mats.count("acu-return") == 1, "top return"
    for q in quads:
        if q.material in ("acu-supply", "acu-return"):
            patch_id = q.name.rsplit("_", 2)[-2] + "_" + q.name.rsplit("_", 1)[-1]
            face = q.name[len(a.name) + 1:].split("_")[0]
            patch = [x for x in a.faces[face]["patches"] if x["id"] == patch_id][0]
            assert (q.color, q.keep_user_color) == (patch["color"], True)
            assert lib.color_for(q.material, q.color, True) == lib.material(q.material)["baseColor"]


def test_acu_shell_is_off_white_sheet_metal(scene):
    """The cabinet is 'acu-body' on all six faces, in the manifest's colour."""
    a = _by_name(scene, "ACU_1")
    shell = [q for q in E.emit(a) if q.material == "acu-body"]
    assert len(shell) == 6
    assert all(q.color is None for q in shell)


def test_raised_floor_is_hpl_tiles_with_a_vent_tile(scene):
    """_floorBodyStyle: a non-boundary floor is the raised floor (HPL tiles); a patch in
    it is a perforated vent tile. This is the largest object in frame for this case."""
    raised = _by_name(scene, "Raised_Floor_copy")
    room = _by_name(scene, "Room_Floor")
    assert not raised.is_room_boundary and room.is_room_boundary
    rq = E.emit(raised, scene.placements)
    patches = [q for q in rq if q.name.startswith(raised.name + "_top_")]
    body = [q for q in rq if q not in patches]
    assert body and {q.material for q in body} == {"raised-floor"}
    assert patches and {q.material for q in patches} == {"floor-vent"}
    # The room floor and ceiling are the boundary pair: lower = slab, upper = ceiling tiles.
    fq = E.emit(room, scene.placements)
    assert {q.material for q in fq} == {"floor"}
    cq = E.emit(_by_name(scene, "Room_Ceiling"), scene.placements)
    assert {q.material for q in cq} == {"ceiling"}


def test_wall_and_baffle_kinds(scene):
    assert E.emit(_by_name(scene, "Room_Wall_1"))[0].material == "wall"
    assert E.emit(_by_name(scene, "Baffle_2"))[0].material == "baffle"
    import copy
    perforated = copy.deepcopy(_by_name(scene, "Baffle_2"))
    perforated.cooling_type = "perforated"
    assert E.emit(perforated)[0].material == "baffle-perforated"


def test_polygon_floor_triangulates_and_covers_the_room(scene):
    f = _by_name(scene, "Room_Floor")
    quads = E.emit(f)
    assert len(quads) == 2, "a 4-gon fans into 2 triangles"
    xs = [pt[0] for q in quads for pt in q.pts]
    zs = [pt[2] for q in quads for pt in q.pts]
    assert (min(xs), max(xs)) == (0.0, 4.4)
    assert (min(zs), max(zs)) == (0.0, 6.0)
    # One tile grid for the whole floor: every triangle's UVs are the floor's top-face
    # frame (u = x, v = D - z), so the 600 mm joints line up across the fan.
    D = f.size[2]
    for q in quads:
        assert q.uv == tuple((x, D - z) for x, _, z in q.pts)


def test_horizontal_baffle_stays_horizontal_in_world(scene):
    """Baffle_1's X-rotation must survive local->world."""
    b = _by_name(scene, "Baffle_1")
    q = E.emit(b)[0]
    ys = [to_world(b, pt)[1] for pt in q.pts]
    assert max(ys) - min(ys) < 1e-9, "lid must be flat in world space"
    assert ys[0] == pytest.approx(2.958, abs=1e-3)


def test_whole_scene_quad_budget(scene):
    """16 objects must not explode into thousands of prims.

    The budget went 88 -> 135 when the rack internals landed: a rack is 25 quads, not 8.
    That is the cost of the cavity walls and the server boxes, and it is bounded — the
    ceiling is here so a future emitter cannot quietly go per-slot again.
    """
    total = sum(len(E.emit(p, scene.placements)) for p in scene.placements)
    assert total < 200, f"{total} quads for 16 objects is too many"


# ── blanking panels, which Base_leakage cannot show (it has none) ────────────────────

@pytest.fixture(scope="module")
def slab():
    return read_cfdproj(os.path.join(REPO, "case/Slab/Slab.cfdproj"))


def test_blanking_runs_are_one_panel_and_the_texture_draws_the_u_seams(slab):
    """A merged run is ONE quad. The editor's U-division guide lines are view helpers
    the render snapshot leaves out; the blanking-panel texture carries one seam per U,
    so neither Kit path draws seam quads."""
    r = [p for p in slab.placements if p.cls == "racks"][0]
    quads = E.emit_rack(r)
    runs = E.merged_empty_ranges(r, "front")
    assert runs, "this rack must have blank runs for the test to mean anything"
    blanks = [q for q in quads if "_front_blank_" in q.name]
    assert len(blanks) == len(runs)
    assert not any("_uline_" in q.name for q in quads)
    for (a, b, baffle), q in zip(runs, blanks):
        assert q.material == ("blank-panel-baffle" if baffle else "blank-panel")


def test_recessed_blank_panels_face_into_the_cavity(slab):
    """The frontend's own winding, kept rather than "fixed".

    With a depth offset the blind panels move inward and are FrontSide with the normal
    pointing INTO the rack, so looking into an open front you see the BACK panels. That
    is the editor's picture; forcing them double-sided would seal a rack it draws open.
    """
    r = [p for p in slab.placements if p.cls == "racks"][0]
    assert r.inlet_offset > 1e-6
    blanks = [q for q in E.emit_rack(r) if q.material.startswith("blank-panel")]
    assert blanks
    # The emitter does not ask for both sides (the editor does not either); the
    # manifest's blanking-panel material is double-sided, so the mesh is.
    assert not any(q.double_sided for q in blanks)
    assert all(lib.render_material(q.material)["doubleSided"] for q in blanks)
    front = [q for q in blanks if "_front_blank" in q.name][0]
    assert [pt[2] for pt in front.pts] == [pytest.approx(r.inlet_offset)] * 4


def test_partitions_are_read_and_gated_on_deleted(slab):
    """Every tracked case deletes all of them, so the emitter must draw none."""
    r = [p for p in slab.placements if p.cls == "racks"][0]
    assert r.partitions and all(pt.deleted for pt in r.partitions)
    assert not any("_partition_" in q.name for q in E.emit_rack(r))
    import copy
    r2 = copy.deepcopy(r)
    r2.partitions[3].deleted = False
    got = [q for q in E.emit_rack(r2) if "_partition_" in q.name]
    assert len(got) == 1
    assert [pt[1] for pt in got[0].pts] == \
        [pytest.approx(r.margin_bottom + 4 * r.slot_height)] * 4


def test_model_opacity_reaches_every_quad(scene):
    """meta.display.opacity 0 must make every quad transparent, not just some.

    GTC_2026 shipped with all 75 objects at opacity 0 — the editor drew nothing while OV
    drew everything solid, because nothing downstream read the value.
    """
    import copy
    r = copy.deepcopy(_by_name(scene, "Default_Rack_1"))
    r.opacity = 0.0
    assert all(q.opacity == 0.0 for q in E.emit(r))
    r.opacity = 0.35
    assert all(q.opacity == pytest.approx(0.35) for q in E.emit(r))


def test_face_opacity_overrides_the_model(scene):
    """faces[id].opacity wins over meta.display.opacity, per createMaterialForType."""
    import copy
    a = copy.deepcopy(_by_name(scene, "ACU_1"))
    a.opacity = 1.0
    a.faces = copy.deepcopy(a.faces)
    a.faces["top"]["opacity"] = 0.2
    q = {x.name.rsplit("_", 1)[-1]: x.opacity
         for x in E.emit(a) if x.material == "acu-body"}
    assert q["top"] == pytest.approx(0.2), "the faded face must fade"
    assert q["front"] == pytest.approx(1.0), "its siblings must not"


# ── pictures on faces and patches (DMAL_Testbed-patched) ────────────────────────────

@pytest.fixture(scope="module")
def daml():
    return read_cfdproj(os.path.join(REPO, "case/DAML-Testbed/DMAL_Testbed-patched.cfdproj"))


def test_a_face_texture_url_becomes_a_pool_picture(daml):
    """face.textureUrl (the electric-box door) draws as the picture, stretched once
    over the face, falling back to the cabinet kind if it cannot be bound."""
    box = [p for p in daml.placements if p.name == "electric_box"][0]
    front = [q for q in E.emit(box) if q.name == "electric_box_front"][0]
    assert (front.material, front.picture, front.fallback, front.uv_mode) == \
        ("face-image", "/textures/electric-box/panel_lamps.jpg", "box", "stretch")
    assert lib.pool_ref_for_url(front.picture) == lib.pool_ref("electric-box/panel_lamps.jpg")


def test_a_patch_texture_preset_resolves_through_the_manifest(daml):
    ceiling = [p for p in daml.placements if p.name == "Ceiling"][0]
    pics = [q for q in E.emit(ceiling, daml.placements) if q.picture]
    assert pics, "the ceiling's return diffusers carry a texturePreset"
    preset = [x for x in lib.manifest()["patchPresets"] if x["id"] == "four_way_diffuser"][0]
    assert {q.picture for q in pics} == {"/textures/" + preset["image"]["file"]}
