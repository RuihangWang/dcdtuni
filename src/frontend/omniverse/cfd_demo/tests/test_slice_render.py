"""Surface-slice contract: pure validation/colour/cache math on CPU, plus real
OpenUSD authoring of the slice mesh and the draggable preview disc."""
from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "kit/extensions/dc.cfd_bridge"))
from dc.cfd_bridge import slice_render as sr  # noqa: E402
from dc.cfd_bridge import parsers  # noqa: E402


def msg(**kw):
    return json.dumps(kw)


describe_pure = True


def test_normalize_plane_rejects_zero_and_non_finite():
    with pytest.raises(ValueError):
        sr.normalize_plane([0, 0, 0], [0, 0, 0])
    with pytest.raises(ValueError):
        sr.normalize_plane([0, 0, 0], [float('nan'), 0, 1])
    with pytest.raises(ValueError):
        sr.normalize_plane([0, 0], [0, 1, 0])
    origin, normal = sr.normalize_plane([1, 2, 3], [0, 2, 0])
    assert normal == [0.0, 1.0, 0.0]
    assert origin == [1.0, 2.0, 3.0]


def test_parse_set_slice_rejects_silently_defaultable_garbage():
    with pytest.raises(ValueError):
        parsers.parse_set_slice(msg(origin=[0, 0, 0], normal=[0, 0, 0]))
    with pytest.raises(ValueError):
        parsers.parse_set_slice(msg(radius=0))
    with pytest.raises(ValueError):
        parsers.parse_set_slice(msg(radius=float('inf')))
    with pytest.raises(ValueError):
        parsers.parse_set_slice(msg(field='pressure'))
    with pytest.raises(ValueError):
        parsers.parse_set_slice(msg(domain=[5, 1]))
    parsed = parsers.parse_set_slice(msg(origin=[1, 2, 3], normal=[0, 0, 4],
                                          radius=2.5, field='Umag', domain=[280, 320]))
    assert parsed['normal'] == [0.0, 0.0, 1.0]
    assert parsed['domain'] == [280.0, 320.0]
    assert parsed['visible'] is True


def test_parse_set_slice_defaults_are_a_real_plane():
    parsed = parsers.parse_set_slice(msg())
    assert parsed['origin'] == [0.0, 0.0, 0.0]
    assert parsed['normal'] == [0.0, 1.0, 0.0]
    assert parsed['field'] == 'T'
    assert parsed['domain'] is None


def test_cache_key_stable_under_noise_and_sensitive_to_intent():
    base = sr.cache_key('ds', [0, 0, 0], [0, 1, 0], 1.0, 'T', None)
    # Solver-scale jitter must not bust the cache…
    assert sr.cache_key('ds', [0, 0, 1e-9], [0, 1, 0], 1.0, 'T', None) == base
    # …but anything the user can perceive must.
    assert sr.cache_key('ds', [0.01, 0, 0], [0, 1, 0], 1.0, 'T', None) != base
    assert sr.cache_key('ds', [0, 0, 0], [0, 1, 0], 1.5, 'T', None) != base
    assert sr.cache_key('ds', [0, 0, 0], [1, 0, 0], 1.0, 'T', None) != base
    assert sr.cache_key('ds', [0, 0, 0], [0, 1, 0], 1.0, 'Umag', None) != base
    assert sr.cache_key('ds', [0, 0, 0], [0, 1, 0], 1.0, 'T', [280, 320]) != base
    assert sr.cache_key('other', [0, 0, 0], [0, 1, 0], 1.0, 'T', None) != base


def test_scalar_to_rgb_clamps_and_interpolates():
    assert sr.scalar_to_rgb(-1) == sr.scalar_to_rgb(0)
    assert sr.scalar_to_rgb(2) == sr.scalar_to_rgb(1)
    assert sr.scalar_to_rgb(float('nan')) == (0.5, 0.5, 0.5)
    lo = sr.scalar_to_rgb(0.0)
    hi = sr.scalar_to_rgb(1.0)
    mid = sr.scalar_to_rgb(0.5)
    assert lo != hi and mid not in (lo, hi)
    assert all(0.0 <= c <= 1.0 for c in mid)


def test_clip_to_radius_keeps_only_the_disc():
    # Four triangles: two inside radius 1 of the origin (in-plane), two outside.
    points = [
        0.0, 0.0, 0.0,   0.5, 0.0, 0.0,   0.0, 0.0, 0.5,      # in
        0.2, 0.0, 0.2,   0.4, 0.0, 0.1,   0.1, 0.0, 0.4,      # in
        5.0, 0.0, 5.0,   5.5, 0.0, 5.0,   5.0, 0.0, 5.5,      # out
        -4.0, 0.0, 0.0,  -3.5, 0.0, 0.0,  -4.0, 0.0, 0.5,    # out
    ]
    tris = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]
    kept = sr.clip_to_radius(points, tris, [0, 0, 0], [0, 1, 0], 1.0)
    assert kept == [0, 1, 2, 3, 4, 5]
    assert sr.clip_to_radius(points, tris, [0, 0, 0], [0, 1, 0], 0) == tris  # radius 0 = no clip


def test_colorize_uses_p1_p99_working_range_like_the_volume():
    scalars = [300.0] + [301.0 + i for i in range(98)] + [900.0]  # outliers both ends
    auto = sr.colorize(scalars, len(scalars), None)
    explicit = sr.colorize(scalars, len(scalars), [301.0, 398.0])
    assert len(auto) == len(scalars) * 3
    # The automatic domain must ignore the 900 K outlier: an explicit domain
    # equal to p1..p99 reproduces it exactly.
    for a, b in zip(auto, explicit):
        assert a == pytest.approx(b, abs=1e-9)
    assert sr.colorize([], 4, None) == [0.6, 0.6, 0.6] * 4


def test_author_slice_mesh_and_disc_roundtrip_through_usd():
    Usd = pytest.importorskip('pxr.Usd')
    from pxr import UsdGeom
    stage = Usd.Stage.CreateInMemory()
    points = [0, 0, 0, 1, 0, 0, 0, 0, 1, 1, 0, 1]
    tris = [0, 1, 2, 1, 3, 2]
    colors = [1, 0, 0, 0, 1, 0, 0, 0, 1, 1, 1, 0]
    mesh = sr.author_slice_mesh(stage, points, tris, colors)
    # A typeless prim carrying mesh attributes is invisible to the renderer:
    # the prims must BE meshes (measured in Kit: the slice acked 4832 tris
    # and nothing was drawn).
    assert stage.GetPrimAtPath(sr.SLICE_MESH_PATH).GetTypeName() == 'Mesh'
    assert mesh.GetPointsAttr().Get() is not None
    assert list(mesh.GetFaceVertexCountsAttr().Get()) == [3, 3]
    prim = stage.GetPrimAtPath(sr.SLICE_MESH_PATH)
    colors_primvar = UsdGeom.Primvar(mesh.GetPrim().GetAttribute('primvars:displayColor'))
    assert colors_primvar.GetInterpolation() == 'vertex'
    disc = sr.author_slice_disc(stage, [0, 1, 0], [0, 1, 0], 2.0)
    dprim = stage.GetPrimAtPath(sr.SLICE_DISC_PATH)
    assert dprim.IsValid() and dprim.GetTypeName() == 'Mesh'
    # Re-authoring over an existing typeless prim must also yield a Mesh.
    stage2 = Usd.Stage.CreateInMemory()
    stage2.DefinePrim(sr.SLICE_MESH_PATH)
    sr.author_slice_mesh(stage2, points, tris, colors)
    assert stage2.GetPrimAtPath(sr.SLICE_MESH_PATH).GetTypeName() == 'Mesh'
    # The live translation the drag arbitration reads is the slice origin.
    origin = sr.disc_origin_from_xform(stage)
    assert [round(v, 6) for v in origin] == [0.0, 1.0, 0.0]
    # Re-authoring moves the disc without duplicating prims.
    sr.author_slice_disc(stage, [3, 1, 0], [0, 1, 0], 2.0)
    assert [round(v, 6) for v in sr.disc_origin_from_xform(stage)] == [3.0, 1.0, 0.0]
    assert mesh.GetPrim().IsValid() and disc.GetPrim().IsValid()


def test_cut_plane_on_a_synthetic_volume_cell():
    pytest.importorskip('vtkmodules')
    from vtkmodules.vtkCommonDataModel import vtkUnstructuredGrid
    from vtkmodules.vtkCommonCore import vtkPoints, vtkIdList, vtkFloatArray
    # One hexahedron spanning y=0..2: cutting at y=1 must yield a surface
    # (two triangles), not lines — surface cells would degenerate to polylines.
    pts = vtkPoints()
    for y in (0.0, 2.0):
        for x, z in ((0, 0), (2, 0), (0, 2), (2, 2)):
            pts.InsertNextPoint(x, y, z)
    grid = vtkUnstructuredGrid()
    grid.SetPoints(pts)
    ids = vtkIdList()
    for i in range(8):
        ids.InsertNextId(i)
    grid.InsertNextCell(12, ids)  # VTK_HEXAHEDRON
    scalars = vtkFloatArray()
    scalars.SetName('T')
    scalars.InsertNextValue(300.0)
    grid.GetCellData().SetScalars(scalars)
    points, tris, _scalars, _poly = sr.cut_plane(grid, [0, 1, 0], [0, 1, 0])
    assert len(tris) == 6  # one quad cut -> two triangles
    assert all(abs(points[i * 3 + 1] - 1.0) < 1e-6 for i in range(len(points) // 3))
    # Clipping to a disc smaller than the cut removes everything; a large one keeps it.
    assert sr.clip_to_radius(points, tris, [1, 1, 1], [0, 1, 0], 0.1) == []
    assert len(sr.clip_to_radius(points, tris, [1, 1, 1], [0, 1, 0], 10.0)) == 6


def _hex_plus_boundary_quad():
    """One hexahedron (y=0..2) plus a coplanar 2D boundary quad at y=1 — the
    shape a foamToEnsight/foamToVTK merge hands the cutter when boundary
    patches ride along with the internal mesh."""
    from vtkmodules.vtkCommonDataModel import vtkUnstructuredGrid
    from vtkmodules.vtkCommonCore import vtkPoints, vtkIdList, vtkFloatArray
    pts = vtkPoints()
    for y in (0.0, 2.0):
        for x, z in ((0, 0), (2, 0), (0, 2), (2, 2)):
            pts.InsertNextPoint(x, y, z)
    for x, z in ((0, 0), (2, 0), (2, 2), (0, 2)):
        pts.InsertNextPoint(x + 5.0, 0.5, z)  # quad face straddled by nothing
    grid = vtkUnstructuredGrid()
    grid.SetPoints(pts)
    hexa = vtkIdList()
    for i in range(8):
        hexa.InsertNextId(i)
    grid.InsertNextCell(12, hexa)  # VTK_HEXAHEDRON
    quad = vtkIdList()
    for i in range(8, 12):
        quad.InsertNextId(i)
    grid.InsertNextCell(9, quad)  # VTK_QUAD
    t = vtkFloatArray()
    t.SetName('T')
    u = vtkFloatArray()
    u.SetName('U')
    u.SetNumberOfComponents(3)
    for tv, uv in ((300.0, (3.0, 4.0, 0.0)), (310.0, (0.0, 0.0, 1.0))):
        t.InsertNextValue(tv)
        u.InsertNextTuple3(*uv)
    grid.GetCellData().AddArray(t)
    grid.GetCellData().AddArray(u)
    return grid


def test_cut_plane_emits_only_whole_triangles_on_mixed_cells():
    """A 2D face cut by the plane yields a LINE; if the cutter output passes
    lines through, their 2-index cells corrupt the flat triangle stream."""
    pytest.importorskip('vtkmodules')
    grid = _hex_plus_boundary_quad()
    # Cut at y=0.5: through the hex AND exactly through the boundary quad row.
    points, tris, _s, _p = sr.cut_plane(grid, [0, 0.5, 0], [1e-3, 1, 0])
    n_pts = len(points) // 3
    assert tris and len(tris) % 3 == 0
    assert max(tris) < n_pts


def test_point_scalar_grid_derives_umag_from_u_when_absent():
    """foamToEnsight/foamToVTK write U but no magnitude; the |U| slice must not
    silently fall back to the grey no-data colour."""
    pytest.importorskip('vtkmodules')
    from vtkmodules.util.numpy_support import vtk_to_numpy
    grid = _hex_plus_boundary_quad()
    pg, found = sr.point_scalar_grid(grid, 'Umag')
    assert found
    active = pg.GetPointData().GetScalars()
    assert active is not None and active.GetName() == 'Umag'
    mags = vtk_to_numpy(active)
    # Hex points only see the hex cell: |(3,4,0)| = 5.
    assert mags[0] == pytest.approx(5.0)
    pg_t, found_t = sr.point_scalar_grid(grid, 'T')
    assert found_t and pg_t.GetPointData().GetScalars().GetName() == 'T'
    assert sr.point_scalar_grid(grid, 'U')[0].GetPointData().GetScalars().GetName() == 'Umag'


def test_slice_geometry_is_pure_and_summarised():
    """The executor-side half of a slice rebuild (read-free: grid in, arrays
    out) reports the triangle count the ack carries."""
    pytest.importorskip('vtkmodules')
    grid = _hex_plus_boundary_quad()
    out = sr.slice_geometry(grid, [1, 1, 1], [0, 1, 0], 10.0, 'T', None)
    assert out['tris'] and len(out['tris']) % 3 == 0
    assert len(out['colors']) == len(out['points'])
    assert out['summary']['tris'] == len(out['tris']) // 3
    assert out['summary']['field'] == 'T'
    assert out['summary']['found'] is True
    empty = sr.slice_geometry(grid, [0, 50, 0], [0, 1, 0], 10.0, 'T', None)
    assert empty['summary']['tris'] == 0


def test_volume_display_domain_prefers_explicit_then_dataset_band():
    stats = {'T': {'min': 290.0, 'max': 320.0, 'p1': 292.0, 'p99': 310.0},
             'Umag': {'min': 0.0, 'max': 5.0, 'p1': 0.01, 'p99': 1.5}}
    assert sr.volume_display_domain(stats, 'T', [280, 300]) == (280.0, 300.0, 'explicit', False)
    assert sr.volume_display_domain(stats, 'T') == (292.0, 310.0, 'dataset p1..p99', False)
    assert sr.volume_display_domain(stats, 'U')[:2] == (0.01, 1.5)
    assert sr.volume_display_domain(None, 'T') is None
    assert sr.volume_display_domain({'T': stats['T']}, 'U') is None
    with pytest.raises(ValueError):
        sr.volume_display_domain(stats, 'T', [300, 300])


def test_volume_display_domain_widens_a_uniform_fixture_and_says_so():
    """SyntheticAirDisplay is uniform (T=303 K): p1 == p99 would hand IndeX a
    zero-width transfer function."""
    uniform = {'T': {'min': 303.0, 'max': 303.0, 'p1': 303.0, 'p99': 303.0},
               'Umag': {'min': 0.1, 'max': 0.1, 'p1': 0.1, 'p99': 0.1}}
    lo, hi, source, degenerate = sr.volume_display_domain(uniform, 'T')
    assert degenerate and lo < 303.0 < hi and source == 'dataset p1..p99'
    lo, hi, _s, degenerate = sr.volume_display_domain(uniform, 'U')
    assert degenerate and lo == pytest.approx(0.05) and hi == pytest.approx(0.15)


def test_parse_set_volume_display_rejects_garbage():
    assert parsers.parse_set_volume_display(msg(field='off', domain=None)) == {'field': 'off', 'domain': None}
    assert parsers.parse_set_volume_display(msg(field='U', domain=[0, 2])) == {'field': 'U', 'domain': [0.0, 2.0]}
    for bad in (msg(field='pressure', domain=None), msg(field='T', domain=[2, 1]),
                msg(field='T', domain=[1]), msg(field='T', domain=[0, float('inf')])):
        with pytest.raises(ValueError):
            parsers.parse_set_volume_display(bad)


@pytest.mark.parametrize('suffix', ['.vtu', '.vtp'])
def test_read_grid_reads_what_the_bridge_writes(tmp_path, suffix):
    """read_grid is the disk path the slice and the dataset-bounds ack take;
    a wrong reader import only shows up when a real file is read."""
    pytest.importorskip('vtkmodules')
    grid = _hex_plus_boundary_quad()
    path = tmp_path / f'g{suffix}'
    if suffix == '.vtu':
        from vtkmodules.vtkIOXML import vtkXMLUnstructuredGridWriter as W
        data = grid
    else:
        from vtkmodules.vtkFiltersGeometry import vtkGeometryFilter
        gf = vtkGeometryFilter()
        gf.SetInputData(grid)
        gf.Update()
        from vtkmodules.vtkIOXML import vtkXMLPolyDataWriter as W
        data = gf.GetOutput()
    w = W()
    w.SetFileName(str(path))
    w.SetInputData(data)
    w.Write()
    out = sr.read_grid(str(path))
    assert out.GetNumberOfCells() > 0
    assert out.GetBounds()[0] <= out.GetBounds()[1]


def test_streamline_domain_keeps_the_cold_end_and_reaches_the_hottest_air():
    """Streamlines carry air into the hot aisle: a per-cell p99 top saturated
    every T streamline (graded mesh, many small cold cells)."""
    stats = {'T': {'min': 287.0, 'max': 306.0, 'p1': 288.15, 'p99': 295.31},
             'Umag': {'min': 0.0, 'max': 5.11, 'p1': 0.035, 'p99': 5.11}}
    assert sr.streamline_display_domain(stats, 'T') == (288.15, 306.0, 'dataset p1..max')
    assert sr.streamline_display_domain(stats, 'U') == (0.035, 5.11, 'dataset p1..p99')
    uniform = {'T': {'min': 303.0, 'max': 303.0, 'p1': 303.0, 'p99': 303.0}}
    lo, hi, source = sr.streamline_display_domain(uniform, 'T')
    assert lo < 303.0 < hi and source == 'dataset uniform (widened)'
    assert sr.streamline_display_domain(None, 'T') is None


def test_parse_set_surface_display_matches_the_volume_contract():
    assert parsers.parse_set_surface_display(msg(field='off', domain=None)) == {
        'field': 'off', 'domain': None, 'palette': 'coolwarm', 'hidden': []}
    assert parsers.parse_set_surface_display(msg(field='T', domain=[292, 300], palette='jet', hidden=['Room_Wall'])) == {
        'field': 'T', 'domain': [292.0, 300.0], 'palette': 'jet', 'hidden': ['Room_Wall']}
    for bad in (msg(field='p_rgh', domain=None), msg(field='T', domain=[3, 1]),
                msg(field='T', domain=None, palette='rainbow'), msg(field='T', domain=None, hidden='Room_Wall')):
        with pytest.raises(ValueError):
            parsers.parse_set_surface_display(bad)
