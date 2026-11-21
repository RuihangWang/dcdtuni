"""Native OpenFOAM regression for the rotated racks, using a frozen UI export.

Run with DCDTUNI_RUN_OPENFOAM_TESTS=1 after building dcCartMesh/Allwmake.
"""
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys

import numpy as np
import pytest

from backend.aircooling.runner.postprocess import read_openfoam_case

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = Path(__file__).parent / "fixtures/dccart_tri_angle"
ENV = ROOT / "src/backend/aircooling/third_parties/dcCartMesh/env.sh"
pytestmark = pytest.mark.skipif(
    os.environ.get("DCDTUNI_RUN_OPENFOAM_TESTS") != "1",
    reason="requires a built dcCartMesh and OpenFOAM runtime",
)


# Where each case of the `cases` fixture was meshed, for the checks that read
# the per-rank meshes rather than the reconstructed one.
CASE_DIRS = {}

# 17 ranks put a rank boundary through BOTH kinds of rotated panel: Rack_1's
# 6U/8U outlet rows, where the lower rank holds the master cell, and one face
# of Rack_1's 6U inlet, where the HIGHER rank does (measured 2026-09-23 with
# the export's 0.25 m sizing; none of 1-16 and 18 has the second kind except
# 19). See test_a_rank_boundary_through_a_panel_follows_the_sheet.
ROUTING_RANKS = 17


@pytest.fixture(scope="module")
def cases(tmp_path_factory):
    root = tmp_path_factory.mktemp("tri-angle-regression")
    result = {}
    # None keeps the export's own sizing (0.25 m, the recommended rule)
    for key, ranks, base_size in ((1, 1, None), (4, 4, None), ("fine", 4, .17),
                                  (ROUTING_RANKS, ROUTING_RANKS, None)):
        case = root / f"rank{key}"
        shutil.copytree(FIXTURE / "triSurface", case / "Mesh/constant/triSurface")
        (case / "model").mkdir()
        data = json.loads((FIXTURE / "model/Tri_angle.cfdrun").read_text())
        data["meshConfig"].update(parallelMesh=ranks > 1, meshProcess=ranks)
        data["meshGeometry"]["meshConfig"].update(parallelMesh=ranks > 1, meshProcess=ranks)
        if base_size is not None:
            data["meshConfig"]["meshBaseSize"] = base_size
            data["meshGeometry"]["meshConfig"].update(baseSize=base_size, blockMeshBounds=None)
        manifest = case / "model/Tri_angle.cfdrun"
        manifest.write_text(json.dumps(data))
        command = (f"source {shlex.quote(str(ENV))} >/dev/null\n"
                   f"exec {shlex.quote(sys.executable)} -m backend.aircooling.runner.run_cfd "
                   f"--from-cfdrun {shlex.quote(str(manifest))} --mesh-only")
        run = subprocess.run(["bash", "-c", command], cwd=ROOT,
                             env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
                             capture_output=True, text=True, timeout=300)
        log = run.stdout + run.stderr
        (case / "mesh.log").write_text(log)
        assert run.returncode == 0, log[-10000:]
        # mpirun interleaves the ranks' output mid-line, so the mesher's own
        # "over N rank(s)" line can arrive split; the launch command cannot
        if ranks > 1:
            assert f"mpirun -np {ranks} " in log
        else:
            assert "mpirun" not in log
        mesh, _ = read_openfoam_case(case / "Mesh", 0)
        result[key] = (mesh, log)
        CASE_DIRS[key] = case / "Mesh"
    return result


def source_triangles(name):
    for path in (FIXTURE / "triSurface").glob("*.stl"):
        match = re.search(r"^solid " + re.escape(name) + r"\n(.*?)^endsolid", path.read_text(), re.M | re.S)
        if match:
            return np.array([[float(v) for v in row.split()]
                             for row in re.findall(r"vertex\s+([^\n]+)", match[1])]).reshape(-1, 3, 3)
    raise AssertionError(f"missing fixture solid {name}")


def assert_rectangle_covered(surface, source, max_shift=0.0, snap=0.0):
    """Every interior sample of the exported rectangle has mesh under it.

    The first-layer floor may carry the whole sheet onto a grid plane up to
    firstLayerMax (5 mm) away; `max_shift` allows that ONE rigid offset along
    the normal and nothing else, so a missing corner still fails. `snap`
    lets single points of a TILTED sheet sit off its plane by the carve's
    grid snap (SNAP); coverage is then measured in the plane.
    """
    if snap:
        tri = source_triangles(source)
        n = np.cross(tri[0, 1] - tri[0, 0], tri[0, 2] - tri[0, 0])
        n /= np.linalg.norm(n)
        offsets = (np.asarray(surface.points, float) - tri[0, 0]) @ n
        assert np.max(np.abs(offsets)) <= max_shift + snap, (source, offsets.min(), offsets.max())
        flat = surface.copy()
        flat.points = np.asarray(surface.points, float) - np.outer(offsets, n)
        return assert_rectangle_covered(flat, source)
    tri = source_triangles(source)
    p = tri.reshape(-1, 3)
    origin = p[0]
    u = tri[0, 1] - tri[0, 0]
    u /= np.linalg.norm(u)
    n = np.cross(u, tri[0, 2] - tri[0, 0])
    n /= np.linalg.norm(n)
    v = np.cross(n, u)
    xy = np.column_stack(((p - origin) @ u, (p - origin) @ v))
    lo, hi = xy.min(0), xy.max(0)
    # Interior samples close to all four corners catch the former step-shaped
    # missing regions without mistaking STL rounding at a shared rim for a gap.
    t = np.linspace(.02, .98, 21)
    x, y = np.meshgrid(lo[0] + t * (hi[0] - lo[0]), lo[1] + t * (hi[1] - lo[1]))
    samples = origin + x.ravel()[:, None] * u + y.ravel()[:, None] * v
    offsets = (np.asarray(surface.points, float) - origin) @ n
    assert np.ptp(offsets) < 2e-6, (source, np.ptp(offsets))
    assert abs(offsets.mean()) <= max_shift + 2e-6, (source, offsets.mean())
    samples = samples + offsets.mean() * n
    _, closest = surface.find_closest_cell(samples, return_closest_point=True)
    assert np.max(np.linalg.norm(closest - samples, axis=1)) < 2e-6


@pytest.mark.parametrize("ranks", [1, 4, "fine"])
def test_fan_server_bottom_has_no_missing_corners(cases, ranks):
    mesh, _ = cases[ranks]
    name = "Default_Rack_2_fan_server_2_bottom"
    assert_rectangle_covered(mesh["boundary"][name], name, max_shift=5e-3)


@pytest.mark.parametrize("ranks", [1, 4, "fine"])
def test_both_shelf_sides_cover_the_exported_surface(cases, ranks):
    mesh, log = cases[ranks]
    # The rim may fall short by the carve's ON band (a hundredth of the
    # finest cell, a tenth of the logged ON tolerance) where the shelf meets
    # the rail; nothing may lie past the outline (u23, carried onto the grid
    # plane y = 1.0625, overhung its rotated rear edge by 22 mm: +0.0053 m2).
    rim = 2 * (.48 + .6) * on_tolerance(log) / 10
    for slot in (17, 23):
        name = f"Default_Rack_1_u{slot}_partition"
        for side in ("master", "slave"):
            surface = mesh["boundary"][f"{name}_{side}"]
            assert_rectangle_covered(surface, name, max_shift=5e-3)
            assert .288 - rim <= surface.area <= .288 + 2e-6, (name, side, surface.area, rim)


@pytest.mark.parametrize("ranks", [1, 4, "fine"])
def test_blanking_panel_sheets_keep_their_exported_plane(cases, ranks):
    mesh, log = cases[ranks]
    shift = first_layer_max(log)
    for path in sorted((FIXTURE / "triSurface").glob("merged_perforatedBlankingPanel_*.stl")):
        rows = re.findall(r"vertex\s+([^\n]+)", path.read_text())
        tri = np.array([[float(x) for x in row.split()] for row in rows]).reshape(-1, 3, 3)
        sheet_area = float(np.sum(0.5 * np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1)))
        sheet_normal = np.cross(tri[0, 1] - tri[0, 0], tri[0, 2] - tri[0, 0])
        sheet_normal /= np.linalg.norm(sheet_normal)
        for side in ("master", "slave"):
            surface = mesh["boundary"][f"{path.stem}_{side}"]
            area = 0.0
            for cell in surface.cell:
                p = np.asarray(cell.points, float)
                nrm = np.zeros(3)
                for i in range(len(p)):
                    nrm += np.cross(p[i], p[(i + 1) % len(p)])
                ln = np.linalg.norm(nrm)
                area += 0.5 * ln
                nrm /= ln
                # a stair-step saw on the sheet's rim shows as axis-aligned
                # faces; a planar sheet's caps stay on the sheet's diagonal
                assert np.max(np.abs(nrm)) < 0.99, (path.stem, side, nrm)
                # and every face lies in the sheet's plane (which way round
                # is test_every_baffle_side_faces_one_way's business: 6U's
                # file holds two panels facing opposite ways)
                assert abs(nrm @ sheet_normal) > np.cos(np.radians(.5)), (path.stem, side, nrm)
            # the first-layer floor may carry the panel's top or bottom edge
            # onto a grid plane (11U's top: 1.46685 -> 1.46875 at 0.25 m);
            # the panel is a rectangle with vertical sides, so its area then
            # scales with its height and is otherwise exact
            y_stl = tri[..., 1]
            y_mesh = np.asarray(surface.points, float)[:, 1]
            for a, b in ((y_mesh.min(), y_stl.min()), (y_mesh.max(), y_stl.max())):
                assert abs(a - b) <= shift + 1e-9, (path.stem, side, a, b)
            expected = sheet_area * np.ptp(y_mesh) / np.ptp(y_stl)
            # its end on the rail may fall short by the carve's ON band, as a
            # shelf's rim does (6U at 0.17 m: 0.11 mm)
            rim = 2 * (sheet_area / np.ptp(y_stl) + np.ptp(y_mesh)) * on_tolerance(log) / 10
            assert expected - rim <= area <= expected + 2e-6, (path.stem, side, area, expected, rim)


@pytest.mark.parametrize("ranks", [1, 4, "fine"])
def test_outlet_columns_do_not_alternate_between_rows(cases, ranks):
    mesh, _ = cases[ranks]
    for prefix in ("Default_Rack_1_fan_server_1", "Default_Rack_2_fan_server_1", "Default_Rack_2_fan_server_2"):
        for side in ("inlet_patch", "outlet_p0", "outlet_wall"):
            name = f"{prefix}_{side}"
            surface = mesh["boundary"][name]
            triangles = source_triangles(name)
            tri = triangles[0]
            normal = np.cross(tri[1] - tri[0], tri[2] - tri[0])
            normal /= np.linalg.norm(normal)
            across = np.cross([0., 1., 0.], normal)
            across /= np.linalg.norm(across)
            ymin, ymax = triangles[:, :, 1].min(), triangles[:, :, 1].max()
            rows = []
            for y in ymin + np.linspace(.2, .8, 7) * (ymax - ymin):
                intersections = []
                for cell in surface.cell:
                    p = cell.points
                    for a, b in zip(p, np.roll(p, -1, axis=0)):
                        if (a[1] <= y < b[1]) or (b[1] <= y < a[1]):
                            q = a + (y - a[1]) / (b[1] - a[1]) * (b - a)
                            intersections.append(round(float(q @ across), 6))
                rows.append(sorted(set(intersections)))
            assert len(rows[0]) >= 2, name
            assert all(row == rows[0] for row in rows[1:]), (name, rows)



def solids_matching(pattern):
    """(name, triangles) of every exported solid whose name matches."""
    out = []
    for path in sorted((FIXTURE / "triSurface").glob("*.stl")):
        for name, body in re.findall(r"^solid (\S+)\n(.*?)^endsolid", path.read_text(), re.M | re.S):
            if re.fullmatch(pattern, name):
                rows = re.findall(r"vertex\s+([^\n]+)", body)
                out.append((name, np.array([[float(v) for v in r.split()] for r in rows]).reshape(-1, 3, 3)))
    return out


def distance_to_triangles(points, tri):
    """Distance from each point to the nearest of the triangles (Ericson)."""
    return np.min(triangle_distances(points, tri), axis=1)


def triangle_distances(points, tri):
    """Distance from every point to every triangle, (points, triangles)."""
    p = np.asarray(points, float)[:, None, :]
    a, b, c = tri[None, :, 0], tri[None, :, 1], tri[None, :, 2]
    ab, ac, ap = b - a, c - a, p - a
    d1, d2 = np.sum(ab * ap, -1), np.sum(ac * ap, -1)
    bp = p - b
    d3, d4 = np.sum(ab * bp, -1), np.sum(ac * bp, -1)
    cp = p - c
    d5, d6 = np.sum(ab * cp, -1), np.sum(ac * cp, -1)
    va, vb, vc = d3 * d6 - d5 * d4, d5 * d2 - d1 * d6, d1 * d4 - d3 * d2
    denom = np.where(np.abs(va + vb + vc) < 1e-300, 1e-300, va + vb + vc)
    q = a + ab * (vb / denom)[..., None] + ac * (vc / denom)[..., None]

    def edge(m, x0, x1, t):
        t = np.clip(np.nan_to_num(t), 0, 1)[..., None]
        return np.where(m[..., None], x0 + (x1 - x0) * t, q)

    with np.errstate(divide="ignore", invalid="ignore"):
        q = edge((vc <= 0) & (d1 >= 0) & (d3 <= 0), a, b, d1 / (d1 - d3))
        q = edge((vb <= 0) & (d2 >= 0) & (d6 <= 0), a, c, d2 / (d2 - d6))
        q = edge((va <= 0) & (d4 - d3 >= 0) & (d5 - d6 >= 0), b, c, (d4 - d3) / ((d4 - d3) + (d5 - d6)))
    q = np.where(((d1 <= 0) & (d2 <= 0))[..., None], a, q)
    q = np.where(((d3 >= 0) & (d4 <= d3))[..., None], b, q)
    q = np.where(((d6 >= 0) & (d5 <= d6))[..., None], c, q)
    return np.linalg.norm(p - q, axis=-1)


def on_tolerance(log):
    return float(re.search(r"ON tolerance ([0-9.eE+-]+) m", log)[1])


def first_layer_max(log):
    return float(re.search(r"first-layer floor ON, .*?at most ([0-9.eE+-]+) m", log)[1])


def distance_with_vertical_slack(points, tri, slack):
    """distance_to_triangles, where a point may also be carried by up to
    `slack` along y: the weld and the first-layer floor move a horizontal
    plane of the rack (a server's bottom, the 1U slot above the floor) onto a
    grid plane up to firstLayerMax away, and nothing else. What is left is
    the off-plane (horizontal) deviation, to the 0.1 mm step of the search."""
    points = np.asarray(points, float)
    d = np.concatenate([distance_to_triangles(points[i:i + 4000], tri) for i in range(0, len(points), 4000)])
    far = np.where(d > 1e-6)[0]
    for dy in np.linspace(-slack, slack, 101):
        if not len(far):
            break
        moved = points[far] + [0, dy, 0]
        d[far] = np.minimum(d[far], np.concatenate(
            [distance_to_triangles(moved[i:i + 4000], tri) for i in range(0, len(moved), 4000)]))
        far = far[d[far] > 1e-6]
    return d


# Faces of a rotated rack may sit off the exported surface by the carve's own
# snap (a surface within 1% of a cell of a grid plane is put on it: 0.29 mm on
# Rack_1's outlet) and by the first-layer floor's move of a horizontal plane
# (along y only, up to firstLayerMax), and nothing more. Before 2026-09-23 the
# wedge a server's outlet makes with the rail was lost wherever it poked past
# a grid plane (Rack_2 outlet 6.5 mm, Rack_1 inlet 3.7 mm, Rack_1 frame
# 17.6 mm off), and floor faces beside Rack_1's foot were named after the
# frame (20 mm off it).
SNAP = 5e-4


@pytest.mark.parametrize("ranks", [1, 4, "fine"])
def test_rack_inlets_and_outlets_lie_on_the_exported_faces(cases, ranks):
    mesh, log = cases[ranks]
    slack = first_layer_max(log)
    for server in ("Default_Rack_1_fan_server_1", "Default_Rack_2_fan_server_1", "Default_Rack_2_fan_server_2"):
        tri = np.concatenate([t for _, t in solids_matching(server + r"_(inlet_patch|outlet_p0|outlet_wall)")])
        for side in ("inlet_patch", "outlet_p0", "outlet_wall"):
            surface = mesh["boundary"][f"{server}_{side}"]
            worst = distance_with_vertical_slack(surface.points, tri, slack).max()
            assert worst <= SNAP, (server, side, worst)


@pytest.mark.parametrize("ranks", [1, 4, "fine"])
def test_outlet_regions_keep_their_own_faces(cases, ranks):
    # A fan opening (outlet_p0) and the wall around it (outlet_wall) share a
    # plane, and each keeps exactly its own part of it. Before 2026-09-23 a
    # whole strip went to the other one: a flat cap holding one ON corner
    # 0.29 mm off the plane read as folded and went unsplit (outlet_wall took
    # 12.8 mm of Rack_1's opening), and a face the feature pre-split had cut
    # along the outlet plane was named whole by its vertices' votes
    # (outlet_wall took 2.47 mm of Rack_2's opening, up to the grid plane
    # x = 3.49688, on every row).
    mesh, log = cases[ranks]
    slack = first_layer_max(log)
    for server in ("Default_Rack_1_fan_server_1", "Default_Rack_2_fan_server_1", "Default_Rack_2_fan_server_2"):
        for side in ("outlet_p0", "outlet_wall"):
            (_, tri), = solids_matching(f"{server}_{side}")
            surface = mesh["boundary"][f"{server}_{side}"]
            worst = distance_with_vertical_slack(surface.points, tri, slack).max()
            assert worst <= SNAP, (server, side, worst)


@pytest.mark.parametrize("ranks", [1, 4, "fine"])
def test_rack_frames_lie_on_the_exported_frame(cases, ranks):
    mesh, log = cases[ranks]
    slack = first_layer_max(log)
    for rack in ("Default_Rack_1", "Default_Rack_2"):
        (_, tri), = solids_matching(f"{rack}_frame")
        surface = mesh["boundary"][f"{rack}_frame"]
        worst = distance_with_vertical_slack(surface.points, tri, slack).max()
        assert worst <= SNAP, (rack, worst)


def test_no_face_in_the_floor_plane_is_named_after_another_patch(cases):
    # A leaf re-admitted beside a rotated rack's foot took the frame's name
    # for its floor face, and the frame kept it because its foot (at y = 0
    # too) was within reach: 13 Default_Rack_1_frame faces on the floor.
    boundary = cases[1][0]["boundary"]
    for name in boundary.keys():
        if name == "Room_Floor":
            continue
        on_floor = [c for c in boundary[name].cell if np.all(np.abs(np.asarray(c.points)[:, 1]) < 1e-9)]
        assert not on_floor, (name, len(on_floor))


def test_serial_and_parallel_meshes_have_the_same_cell_count(cases):
    assert cases[1][0]["internalMesh"].n_cells == cases[4][0]["internalMesh"].n_cells


@pytest.mark.parametrize("ranks", [1, 4, "fine", ROUTING_RANKS])
def test_mesh_passes_quality_checks(cases, ranks):
    _, log = cases[ranks]
    assert "Mesh OK." in log
    assert not re.search(r"Failed \d+ mesh checks", log)


# ---- every baffle the export declares: both sides, one sheet, one way round --
#
# A perforated panel's faceZone is its file, and one zone can hold several
# solids (6U holds the rotated rack's inlet AND outlet panel); a rack
# partition's zone is its own solid (baffle_<solid>.stl, split out of
# merged_partition_1U.stl by the runner).
def baffle_zones():
    """{zone: [(solid, triangles), ...]} for every baffle of the fixture."""
    zones = {}
    for path in sorted((FIXTURE / "triSurface").glob("merged_perforatedBlankingPanel_*.stl")):
        zones[path.stem] = solids_matching_in(path)
    for path in sorted((FIXTURE / "triSurface").glob("baffle_*.stl")):
        for name, tri in solids_matching_in(path):
            zones[name] = [(name, tri)]
    return zones


def solids_matching_in(path):
    out = []
    for name, body in re.findall(r"^solid (\S+)\n(.*?)^endsolid", path.read_text(), re.M | re.S):
        rows = re.findall(r"vertex\s+([^\n]+)", body)
        out.append((name, np.array([[float(v) for v in r.split()] for r in rows]).reshape(-1, 3, 3)))
    return out


FLOW_THROUGH = "merged_perforatedBlankingPanel_"


def face_geometry(surface):
    """Vertex centre and area vector (by the face's own winding) of every
    face of a patch, in patch order."""
    points = np.asarray(surface.points, float)
    centres, areas = [], []
    for face in surface.irregular_faces:
        p = points[list(face)]
        centres.append(p.mean(0))
        areas.append(0.5 * np.sum(np.cross(p, np.roll(p, -1, axis=0)), axis=0))
    return np.array(centres), np.array(areas)


def winding_normals(tri):
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    return n / np.linalg.norm(n, axis=1)[:, None]


def faces_by_solid(surface, solids):
    """Index of the zone solid each face of the patch lies on (nearest one)."""
    centres, _ = face_geometry(surface)
    tri = np.concatenate([t for _, t in solids])
    owner = np.concatenate([[k] * len(t) for k, (_, t) in enumerate(solids)])
    return owner[np.argmin(triangle_distances(centres, tri), axis=1)]


def test_every_baffle_of_the_export_is_checked(cases):
    boundary = cases[1][0]["boundary"]
    masters = {name[:-len("_master")] for name in boundary.keys() if name.endswith("_master")}
    assert masters == set(baffle_zones()), sorted(masters ^ set(baffle_zones()))


@pytest.mark.parametrize("ranks", [1, 4, "fine", ROUTING_RANKS])
def test_every_baffle_side_faces_one_way(cases, ranks):
    # The master side of a baffle is the cell the exported normal points
    # into: every master face points AGAINST the solid's normal, every slave
    # face along it (the orientation the cut pass gives a sheet's own caps).
    # The mesher used to take the master side from whichever cell happened to
    # own the zone face, and faces an earlier pass had already cut in the
    # sheet's plane (a partition's free-edge split, a server face's feature
    # split) kept their arbitrary orientation: whole rows of Rack_1's 6U
    # outlet (U18 and U23: 44 of 188 faces) and of Rack_2's 11U / 13U panels
    # were master the other way round, and a preview that draws a master
    # single-sided showed them as holes (2026-09-23).
    mesh, _ = cases[ranks]
    wrong = {}
    for zone, solids in baffle_zones().items():
        tri = np.concatenate([t for _, t in solids])
        normals = winding_normals(tri)
        owner = np.concatenate([[name] * len(t) for name, t in solids])
        for side, sign in (("master", -1), ("slave", 1)):
            centres, areas = face_geometry(mesh["boundary"][f"{zone}_{side}"])
            k = np.argmin(triangle_distances(centres, tri), axis=1)
            along = np.einsum("ij,ij->i", areas, normals[k])
            bad = sign * along <= 0
            for name in np.unique(owner[k][bad]):
                sel = bad & (owner[k] == name)
                wrong[(zone, side, name)] = (int(sel.sum()), int((owner[k] == name).sum()),
                                             round(float(np.linalg.norm(areas[sel], axis=1).sum()), 6))
    assert not wrong, wrong


@pytest.mark.parametrize("ranks", [1, 4, "fine", ROUTING_RANKS])
def test_flow_through_baffle_sides_pair_face_by_face(cases, ranks):
    # Every master face of a cyclic pair has exactly one slave partner: the
    # same polygon in the same place, facing the other way. (Matched by
    # position: the reader appends a rank's processorCyclic faces to the
    # patch, so patch order is k-for-k only within a rank.) An extra guard,
    # not the regression for the master/slave patchwork: the pre-fix meshes
    # pass it, because a turned face still has its partner in place.
    mesh, _ = cases[ranks]
    for zone in baffle_zones():
        if not zone.startswith(FLOW_THROUGH):
            continue
        cm, am = face_geometry(mesh["boundary"][f"{zone}_master"])
        cs, as_ = face_geometry(mesh["boundary"][f"{zone}_slave"])
        assert len(cm) == len(cs), (zone, len(cm), len(cs))
        om = np.lexsort(np.round(cm, 7).T[::-1])
        os_ = np.lexsort(np.round(cs, 7).T[::-1])
        assert np.max(np.linalg.norm(cm[om] - cs[os_], axis=1)) < 1e-9, zone
        assert np.max(np.linalg.norm(am[om] + as_[os_], axis=1)) < 1e-12, zone


@pytest.mark.parametrize("ranks", [1, 4, "fine"])
def test_every_baffle_side_tiles_its_own_sheet(cases, ranks):
    # Per solid of every baffle zone and per side: every face lies in the
    # solid's own plane (up to the carve's snap and the first-layer floor's
    # rigid move of a horizontal sheet), the faces are ONE connected piece
    # (no floating sheet), they cover the exported rectangle, and the two
    # sides tile the same area -- a wall pair's two sides may merge their
    # slivers differently and so need not match face for face (Rack_1's
    # u17 partition: 294 master / 306 slave faces, the same 0.288 m2 each).
    # An extra guard, like the pairing check: the pre-fix meshes pass it.
    mesh, log = cases[ranks]
    slack = first_layer_max(log)
    for zone, solids in baffle_zones().items():
        area = {}
        for side in ("master", "slave"):
            surface = mesh["boundary"][f"{zone}_{side}"]
            which = faces_by_solid(surface, solids)
            for k, (name, tri) in enumerate(solids):
                idx = np.where(which == k)[0]
                assert len(idx), (zone, side, name)
                sub = surface.extract_cells(idx).extract_surface(algorithm="dataset_surface")
                normal = winding_normals(tri)[0]
                _, areas = face_geometry(sub)
                tilt = np.abs(areas @ normal) / np.linalg.norm(areas, axis=1)
                assert tilt.min() > np.cos(np.radians(.5)), (zone, side, name, tilt.min())
                worst = distance_with_vertical_slack(sub.points, tri, slack).max()
                assert worst <= SNAP, (zone, side, name, worst)
                pieces = sub.clean(tolerance=1e-9, absolute=True).connectivity(extraction_mode="all")
                assert len(np.unique(pieces["RegionId"])) == 1, (zone, side, name)
                assert_rectangle_covered(sub, name, max_shift=slack, snap=SNAP)
                area[(side, name)] = float(np.linalg.norm(areas, axis=1).sum())
        for name, _ in solids:
            assert abs(area[("master", name)] - area[("slave", name)]) < 1e-9, (zone, name, area)


def processor_cyclic_patches(mesh_dir):
    """[(my rank, neighbour rank, refer patch, nFaces)] of every non-empty
    processorCyclic patch of the per-rank meshes."""
    out = []
    for path in sorted(Path(mesh_dir).glob("processor*/constant/polyMesh/boundary")):
        text = path.read_text(errors="ignore")
        for name, body in re.findall(r"^\s*(procBoundary\d+to\d+through\S+)\s*\{(.*?)\}", text, re.M | re.S):
            if "processorCyclic" not in body:
                continue
            n = int(re.search(r"nFaces\s+(\d+)", body).group(1))
            me = int(re.search(r"myProcNo\s+(\d+)", body).group(1))
            nbr = int(re.search(r"neighbProcNo\s+(\d+)", body).group(1))
            refer = re.search(r"referPatch\s+(\S+);", body).group(1)
            if n:
                out.append((me, nbr, refer, n))
    return out


def test_a_rank_boundary_through_a_panel_follows_the_sheet(cases):
    # A cyclic baffle face whose two cells sit on different ranks becomes a
    # processorCyclic pair, one side per rank. Which rank refers to the
    # master used to be "the lower one", right only where the lower rank's
    # face already pointed the master's way; now it follows the sheet
    # (baffleFaceSide), whatever the rank numbering. With ROUTING_RANKS the
    # mesh has a crossing of each kind, so reverting the rule leaves the
    # precondition below failing and a master face of Rack_1's 6U inlet
    # pointing the slave's way (test_every_baffle_side_faces_one_way).
    cases[ROUTING_RANKS]
    patches = processor_cyclic_patches(CASE_DIRS[ROUTING_RANKS])
    assert patches, "no panel crosses a rank boundary: pick another ROUTING_RANKS"
    upper_masters = [p for p in patches if p[0] > p[1] and p[2].endswith("_master")]
    lower_masters = [p for p in patches if p[0] < p[1] and p[2].endswith("_master")]
    assert upper_masters, ("no crossing where the higher rank holds the master: "
                           "pick another ROUTING_RANKS", patches)
    assert lower_masters, patches
    # each side of a pair is referred to from exactly one of the two ranks
    for me, nbr, refer, n in patches:
        other = refer[:-len("_master")] + "_slave" if refer.endswith("_master") \
            else refer[:-len("_slave")] + "_master"
        assert (nbr, me, other, n) in patches, (me, nbr, refer, n)
