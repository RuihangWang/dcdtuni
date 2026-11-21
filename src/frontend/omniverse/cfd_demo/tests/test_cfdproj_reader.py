"""Reader parity against the two cases the plan names."""
import math
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..",
                                "kit/extensions/dc.cfd_bridge"))
from dc.cfd_bridge.cfdproj import read_cfdproj, room_bounds, rotate, to_world  # noqa: E402

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../.."))
BASE_LEAKAGE = os.path.join(REPO, "case/Base_leakage/Base_leakage.cfdproj")


def test_base_leakage_inventory():
    s = read_cfdproj(BASE_LEAKAGE)
    n = {}
    for p in s.placements:
        n[p.cls] = n.get(p.cls, 0) + 1
    assert n == {"floors": 3, "walls": 4, "baffles": 3, "acus": 2, "racks": 4}
    assert room_bounds(s) == (0.0, 4.4, 0.0, 6.0)
    assert s.room_height == pytest.approx(3.6)


def test_location_is_min_corner_not_centre():
    """Racks stand ON the raised floor at y=1.0 — the fact that pins the semantics."""
    s = read_cfdproj(BASE_LEAKAGE)
    raised = [p for p in s.placements if p.name == "Raised_Floor_copy"][0]
    racks = [p for p in s.placements if p.cls == "racks"]
    assert raised.origin[1] == pytest.approx(1.0)
    for r in racks:
        # 1 mm: the editor writes sub-mm placement noise (one rack lands at 0.99990),
        # which is float drift in real saved data, not a semantic difference.
        assert r.origin[1] == pytest.approx(1.0, abs=1e-3), "rack base sits on the raised floor"
        # top = 1.0 + 1.958 = 2.958, which is where Baffle_1 rests
        assert r.origin[1] + r.size[1] == pytest.approx(2.958, abs=1e-3)
    baffle1 = [p for p in s.placements if p.name == "Baffle_1"][0]
    assert baffle1.origin[1] == pytest.approx(2.958, abs=1e-3)


def test_rack_uslots_and_servers():
    s = read_cfdproj(BASE_LEAKAGE)
    r = [p for p in s.placements if p.name == "Default_Rack_1"][0]
    assert r.slot == 42 and r.slot_height == pytest.approx(0.0445)
    assert len(r.servers) == 1
    srv = r.servers[0]
    assert (srv.slot_position, srv.slot_occupation) == (15, 14)
    # deletedFaces marks a VOID (open hole), not a blank panel. The server occupies
    # 15..28, and exactly those slots are False on both faces.
    for side in ("front", "back"):
        df = r.deleted_faces[side]
        assert len(df) == 42
        assert all(df[i] is False for i in range(15, 29))
        assert df[0] is True and df[41] is True


def test_quaternion_double_cover_is_the_same_rotation():
    """The editor writes (y=+.7071,w=-.7071) and (y=-.7071,w=+.7071) for one rotation."""
    a = rotate((0, 0.7071067811865475, 0, -0.7071067811865476), (1, 0, 0))
    b = rotate((0, -0.7071067811865475, 0, 0.7071067811865476), (1, 0, 0))
    for u, v in zip(a, b):
        assert u == pytest.approx(v, abs=1e-9)
    assert rotate((0, 0, 0, 1), (1, 2, 3)) == pytest.approx((1, 2, 3))


def test_baffle_1_is_an_X_rotation_not_a_yaw():
    """The case that killed the yaw-only design: a HORIZONTAL containment lid.

    Baffle_1 is a flat panel (size.z == 0) whose local +Y must end up pointing along
    world -Z, i.e. the panel lies in the world XZ plane at the rack top. A yaw-only
    model would have left it standing upright.
    """
    s = read_cfdproj(BASE_LEAKAGE)
    b = [p for p in s.placements if p.name == "Baffle_1"][0]
    assert b.size[2] == pytest.approx(0.0), "a baffle is a zero-thickness panel"
    up = rotate(b.quat, (0, 1, 0))
    assert up == pytest.approx((0, 0, -1), abs=1e-6), "panel laid flat, not upright"
    # Its 4 corners must all share one Y (horizontal) at the rack top.
    W, H, _ = b.size
    ys = [to_world(b, c)[1] for c in ((0, 0, 0), (W, 0, 0), (0, H, 0), (W, H, 0))]
    for y in ys:
        assert y == pytest.approx(2.958, abs=1e-3)


def test_wall_rotation_places_it_on_the_room_edge():
    """Wall_2 is 6 m long, anchored at (4.4,0,0), rotated so it runs to (4.4,0,6)."""
    s = read_cfdproj(BASE_LEAKAGE)
    w = [p for p in s.placements if p.name == "Room_Wall_2"][0]
    end = to_world(w, (w.size[0], 0.0, 0.0))
    assert end[0] == pytest.approx(4.4, abs=1e-6)
    assert end[2] == pytest.approx(6.0, abs=1e-6)


def test_every_object_lands_inside_the_room():
    """Whole-scene sanity: no object may fall outside the room box."""
    s = read_cfdproj(BASE_LEAKAGE)
    x0, x1, z0, z1 = room_bounds(s)
    for p in s.placements:
        W, H, D = p.size
        for c in ((0, 0, 0), (W, 0, 0), (0, H, 0), (0, 0, D),
                  (W, H, 0), (W, 0, D), (0, H, D), (W, H, D)):
            wx, wy, wz = to_world(p, c)
            assert x0 - 1e-6 <= wx <= x1 + 1e-6, f"{p.name} x={wx} outside [{x0},{x1}]"
            assert z0 - 1e-6 <= wz <= z1 + 1e-6, f"{p.name} z={wz} outside [{z0},{z1}]"
            assert -1e-6 <= wy <= s.room_height + 0.05, f"{p.name} y={wy} outside room"


def test_mirrored_racks_land_where_the_editor_puts_them():
    """geometry.scale can be NEGATIVE, and dropping it closes the cold aisle.

    Ground truth read out of the STLGen editor itself (Box3.setFromObject on each
    model's render root) for Base_leakage:

        Default_Rack_1/2   x 0.60 .. 1.67
        Default_Rack_3/4   x 2.73 .. 3.80

    Rack_3 and Rack_4 carry scale (1, 1, -1) while their quaternion is only the
    NEGATIVE of Rack_1's — the same rotation. Ignoring the scale mirrored them about
    their own origin to x 1.80..2.80, which turns a 1.2 m cold aisle into 0.2 m and
    leaves the containment baffles spanning the racks instead of the aisle.
    """
    s = read_cfdproj(BASE_LEAKAGE)
    def span_x(name):
        p = [q for q in s.placements if q.name == name][0]
        W, H, D = p.size
        xs = [to_world(p, c)[0] for c in
              ((0, 0, 0), (W, 0, 0), (0, 0, D), (W, 0, D),
               (0, H, 0), (W, H, 0), (0, H, D), (W, H, D))]
        return (min(xs), max(xs))

    for n in ("Default_Rack_1", "Default_Rack_2"):
        lo, hi = span_x(n)
        assert lo == pytest.approx(0.60, abs=0.02) and hi == pytest.approx(1.60, abs=0.02)
    for n in ("Default_Rack_3", "Default_Rack_4"):
        lo, hi = span_x(n)
        assert lo == pytest.approx(2.80, abs=0.02), f"{n} mirrored to the wrong side"
        assert hi == pytest.approx(3.80, abs=0.02)

    # The aisle the containment encloses: between the two rows, and 1.2 m wide, which
    # is exactly the baffles' own width.
    aisle = span_x("Default_Rack_3")[0] - span_x("Default_Rack_1")[1]
    assert aisle == pytest.approx(1.2, abs=0.03), f"cold aisle is {aisle:.2f} m, not 1.2"
    baffle = [q for q in s.placements if q.name == "Baffle_2"][0]
    assert baffle.size[0] == pytest.approx(1.2, abs=0.01)
