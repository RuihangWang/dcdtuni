"""Surface-slice rendering for the OV display bridge: plane-cut a VTK dataset,
colour the cut by a scalar field, author it as an in-memory USD mesh, and keep
a draggable preview disc gizmo beside it.

Display-only by product ruling: nothing here writes results back to any
.cfdproj/.cfdrun/.liquidrun, and the slice prim lives under /World/CAE like the
volume and streamlines it sits beside.

The cutter runs on vtkmodules (bundled with Kit; also present in the dcdtuni
conda env, so the pure parts are CPU-testable there). USD authoring uses lazy
pxr imports exactly like render_scene.py: this module must stay importable
without pxr for the schema-side tests.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

SLICE_MESH_PATH = '/World/CAE/SliceMesh'
SLICE_DISC_PATH = '/World/CAE/SliceDisc'

# A quantisation for cache keys and drag arbitration: coarser than solver
# noise, far finer than anything a user can perceive or drag.
QUANTUM = 1e-4

# Five-stop thermal ramp (blue -> cyan -> green -> yellow -> red), evaluated
# in Python so slice colours are deterministic and unit-testable without MDL.
_RAMP = [
    (0.00, (0.10, 0.25, 0.85)),
    (0.25, (0.10, 0.75, 0.85)),
    (0.50, (0.20, 0.80, 0.30)),
    (0.75, (0.95, 0.85, 0.15)),
    (1.00, (0.90, 0.15, 0.10)),
]


def scalar_to_rgb(t: float):
    """Map a normalised scalar in [0,1] to an sRGB-ish 0..1 triple."""
    if not math.isfinite(t):
        return (0.5, 0.5, 0.5)
    t = min(1.0, max(0.0, t))
    for (t1, c1), (t2, c2) in zip(_RAMP, _RAMP[1:]):
        if t <= t2:
            f = 0.0 if t2 == t1 else (t - t1) / (t2 - t1)
            return tuple(c1[i] + (c2[i] - c1[i]) * f for i in range(3))
    return _RAMP[-1][1]


def normalize_plane(origin, normal):
    """Finite-check and unit-normalise the cut plane; ValueError on garbage."""
    o = [float(v) for v in origin]
    n = [float(v) for v in normal]
    if len(o) != 3 or len(n) != 3 or not all(math.isfinite(v) for v in o + n):
        raise ValueError('slice plane needs three finite origin and normal components')
    length = math.sqrt(sum(v * v for v in n))
    if length < 1e-9:
        raise ValueError('slice normal must be non-zero')
    return o, [v / length for v in n]


def cache_key(dataset_id: str, origin, normal, radius: float, field: str, domain) -> str:
    """Identity of one slice render: same inputs -> same key -> cache hit."""
    q = lambda v: round(v / QUANTUM)
    payload = {
        'dataset': dataset_id,
        'origin': [q(v) for v in origin],
        'normal': [q(v) for v in normal],
        'radius': q(float(radius)),
        'field': field,
        'domain': None if domain is None else [q(float(v)) for v in domain],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()[:32]


def read_grid(vtu_path: str):
    """Load the cut source grid. .vtm is converted by the scene layer before it
    reaches here; only native VTK readers are used."""
    from vtkmodules.vtkIOXML import (vtkXMLPolyDataReader, vtkXMLStructuredGridReader,
                                     vtkXMLUnstructuredGridReader)
    suffix = Path(vtu_path).suffix.lower()
    if suffix == '.vts':
        reader = vtkXMLStructuredGridReader()
    elif suffix == '.vtp':
        reader = vtkXMLPolyDataReader()
    elif suffix == '.vtk':
        # Legacy VTK: the generic reader returns whichever dataset type the file holds.
        from vtkmodules.vtkIOLegacy import vtkGenericDataObjectReader
        reader = vtkGenericDataObjectReader()
    else:
        reader = vtkXMLUnstructuredGridReader()
    reader.SetFileName(vtu_path)
    reader.Update()
    return reader.GetOutput()


def cut_plane(grid, origin, normal):
    """VTK-cut the grid with the plane; return
    (flat points, triangle indices, per-point scalars or None, cut polydata)."""
    from vtkmodules.vtkFiltersCore import vtkCutter, vtkTriangleFilter
    from vtkmodules.vtkCommonDataModel import vtkPlane
    plane = vtkPlane()
    plane.SetOrigin(*origin)
    plane.SetNormal(*normal)
    cutter = vtkCutter()
    cutter.SetCutFunction(plane)
    cutter.SetInputData(grid)
    cutter.Update()
    triangles = vtkTriangleFilter()
    triangles.SetInputConnection(cutter.GetOutputPort())
    # A 2D cell (a boundary patch merged in with the internal mesh) cuts to a
    # LINE; passed through, its 2-index cell would shear the flat triangle
    # stream. Only whole triangles belong on a slice surface.
    triangles.PassLinesOff()
    triangles.PassVertsOff()
    triangles.Update()
    poly = triangles.GetOutput()
    n_pts = poly.GetNumberOfPoints()
    points = []
    for i in range(n_pts):
        points.extend(poly.GetPoint(i))
    tris = []
    from vtkmodules.util.numpy_support import vtk_to_numpy
    for i in range(poly.GetNumberOfCells()):
        cell = poly.GetCell(i)
        if cell.GetNumberOfPoints() == 3:
            tris.extend(cell.GetPointId(j) for j in range(3))
    scalars = None
    active = poly.GetPointData().GetScalars()
    if active is not None:
        scalars = vtk_to_numpy(active).tolist()
    return points, tris, scalars, poly


def point_scalar_grid(grid, field):
    """Cell data -> point data, with ``field`` made the active point scalar.

    Returns ``(point_grid, found)``. ``field`` 'U' and 'Umag' both mean the
    speed |U|; when the file carries only the vec3 U (foamToEnsight and
    foamToVTK write no magnitude) the magnitude is derived here, so a |U|
    slice never degrades to the grey no-data colour."""
    from vtkmodules.vtkFiltersCore import vtkCellDataToPointData
    converter = vtkCellDataToPointData()
    converter.SetInputData(grid)
    converter.Update()
    point_grid = converter.GetOutput()
    pd = point_grid.GetPointData()
    name = 'T' if field == 'T' else 'Umag'
    if name == 'Umag' and not pd.HasArray('Umag') and pd.HasArray('U'):
        import numpy as np
        from vtkmodules.util.numpy_support import numpy_to_vtk, vtk_to_numpy
        u = vtk_to_numpy(pd.GetArray('U')).reshape(-1, 3).astype(np.float64)
        mag = numpy_to_vtk(np.linalg.norm(u, axis=1), deep=1)
        mag.SetName('Umag')
        pd.AddArray(mag)
    if pd.HasArray(name):
        pd.SetActiveScalars(name)
        return point_grid, True
    return point_grid, False


def slice_geometry(grid, origin, normal, radius, field, domain):
    """The heavy, USD-free half of a slice rebuild: point-convert, cut, clip to
    the preview disc, colour. Safe to run off Kit's main loop (the caller
    authors the returned arrays on the stage). ``summary`` is what the ack
    reports; ``found`` is False when the dataset has no such field."""
    point_grid, found = point_scalar_grid(grid, field)
    points, tris, scalars, _poly = cut_plane(point_grid, origin, normal)
    if tris:
        tris = clip_to_radius(points, tris, origin, normal, radius)
    colors = colorize(scalars, len(points) // 3, domain) if tris else []
    finite = [v for v in (scalars or []) if math.isfinite(v)]
    return {
        'points': points, 'tris': tris, 'colors': colors,
        'summary': {'tris': len(tris) // 3, 'field': field, 'found': bool(found and scalars),
                    'min': min(finite) if finite else None,
                    'max': max(finite) if finite else None},
    }


def volume_display_domain(field_stats, field, explicit=None):
    """Colour domain for the OV display volume: ``(lo, hi, source, degenerate)``.

    An explicit [lo, hi] wins. Otherwise THIS dataset's p1..p99 working band
    (the rule the slice and the reference volume share); a degenerate band — a
    uniform field such as a manufactured display fixture — is widened by 1 %
    (at least 0.05) so the transfer function stays well-defined, and says so.
    Returns ``None`` when the dataset published no stats for the field (the
    caller then keeps its pinned default)."""
    if explicit is not None:
        lo, hi = float(explicit[0]), float(explicit[1])
        if not (math.isfinite(lo) and math.isfinite(hi) and lo < hi):
            raise ValueError('volume domain must be two finite ascending numbers')
        return lo, hi, 'explicit', False
    key = 'T' if field == 'T' else 'Umag'
    st = (field_stats or {}).get(key)
    if not st:
        return None
    lo, hi = float(st['p1']), float(st['p99'])
    if not (math.isfinite(lo) and math.isfinite(hi)):
        return None
    degenerate = not (hi - lo > 1e-9 * max(1.0, abs(lo), abs(hi)))
    if degenerate:
        pad = max(abs(lo) * 0.01, 0.05)
        lo, hi = lo - pad, hi + pad
    return lo, hi, 'dataset p1..p99', degenerate


def streamline_display_domain(field_stats, field):
    """Colour domain for OV streamlines: ``(lo, hi, source)`` or ``None``.

    The volume's p1..p99 band is counted per cell, so on a graded mesh the many
    small cold-aisle cells set its top; streamlines exist to follow air through
    the racks into the hot aisle and all saturate there (measured 2026-09-30:
    every T streamline at the colour map's top end). Keep p1 at the cold end,
    extend the top to the dataset maximum; a uniform field is widened exactly
    like the volume's."""
    chosen = volume_display_domain(field_stats, field)
    if chosen is None:
        return None
    lo, hi, _source, degenerate = chosen
    if degenerate:
        return lo, hi, 'dataset uniform (widened)'
    st = (field_stats or {}).get('T' if field == 'T' else 'Umag') or {}
    top = st.get('max')
    if isinstance(top, (int, float)) and math.isfinite(top) and top > hi:
        return lo, float(top), 'dataset p1..max'
    return lo, hi, 'dataset p1..p99'


def clip_to_radius(points, tris, origin, normal, radius):
    """Keep only triangles whose centroid lies within `radius` of the plane
    origin, measured IN the plane. The preview disc and the rendered slice
    share this bound, so what the circle promises is what the cut shows."""
    if radius is None or radius <= 0:
        return tris
    n = normal
    kept = []
    for t in range(0, len(tris), 3):
        a, b, c = tris[t:t + 3]
        cx = sum(points[3 * i] for i in (a, b, c)) / 3.0 - origin[0]
        cy = sum(points[3 * i + 1] for i in (a, b, c)) / 3.0 - origin[1]
        cz = sum(points[3 * i + 2] for i in (a, b, c)) / 3.0 - origin[2]
        along = cx * n[0] + cy * n[1] + cz * n[2]
        px, py, pz = cx - along * n[0], cy - along * n[1], cz - along * n[2]
        if math.sqrt(px * px + py * py + pz * pz) <= radius:
            kept.extend((a, b, c))
    return kept


def colorize(scalars, n_points, domain):
    """Per-vertex RGB from the cut's scalar array. domain None = data p1..p99
    (the same working-range rule the volume uses), else explicit [lo, hi]."""
    if not scalars:
        return [0.6, 0.6, 0.6] * n_points
    finite = [v for v in scalars if math.isfinite(v)]
    if not finite:
        return [0.6, 0.6, 0.6] * len(scalars)
    if domain is None:
        ordered = sorted(finite)
        # Nearest-rank percentiles on the index scale (n-1), so a 900 K
        # outlier in 100 samples cannot stretch the working range.
        lo = ordered[min(len(ordered) - 1, int(round(0.01 * (len(ordered) - 1))))]
        hi = ordered[min(len(ordered) - 1, int(round(0.99 * (len(ordered) - 1))))]
    else:
        lo, hi = float(domain[0]), float(domain[1])
    span = (hi - lo) or 1.0
    out = []
    for v in scalars:
        rgb = scalar_to_rgb((v - lo) / span)
        out.extend(rgb)
    return out


def author_slice_mesh(stage, points, tris, colors):
    """Author (replace) the slice mesh prim. Lazy pxr, like render_scene."""
    from pxr import Gf, Sdf, UsdGeom, Vt
    # Define, not DefinePrim: a typeless prim carrying mesh attributes is not a
    # Mesh and the renderer draws nothing (Define also re-types an existing prim).
    mesh = UsdGeom.Mesh.Define(stage, SLICE_MESH_PATH)
    n_pts = len(points) // 3
    mesh.CreatePointsAttr(Vt.Vec3fArray([Gf.Vec3f(*points[3 * i:3 * i + 3]) for i in range(n_pts)]))
    mesh.CreateFaceVertexCountsAttr([3] * (len(tris) // 3))
    mesh.CreateFaceVertexIndicesAttr(tris)
    colors_attr = UsdGeom.PrimvarsAPI(mesh).CreatePrimvar(
        'displayColor', Sdf.ValueTypeNames.Color3fArray, 'vertex')
    colors_attr.Set(Vt.Vec3fArray([Gf.Vec3f(*colors[3 * i:3 * i + 3]) for i in range(n_pts)]))
    mesh.CreateDoubleSidedAttr(True)
    if n_pts:
        xs, ys, zs = points[0::3], points[1::3], points[2::3]
        mesh.CreateExtentAttr().Set(Vt.Vec3fArray([Gf.Vec3f(min(xs), min(ys), min(zs)),
                                                   Gf.Vec3f(max(xs), max(ys), max(zs))]))
    return mesh


def author_slice_disc(stage, origin, normal, radius):
    """The draggable preview ring: a flat annulus in the cut plane. Selection
    attaches Kit's transform gizmo, and a viewport drag translates it — the
    scene layer adopts that translation as the new slice origin (the seed
    sphere's arbitration pattern)."""
    from pxr import Gf, Sdf, UsdGeom, Vt
    mesh = UsdGeom.Mesh.Define(stage, SLICE_DISC_PATH)
    # Build the annulus in local XZ (normal = +Y) and orient by Xform, so the
    # live translation the drag arbitration reads is the whole transform.
    inner, outer = radius * 0.86, radius
    segments = 48
    pts = []
    idx = []
    for i in range(segments):
        a = 2 * math.pi * i / segments
        c, s = math.cos(a), math.sin(a)
        pts.append((inner * c, 0.0, inner * s))
        pts.append((outer * c, 0.0, outer * s))
    for i in range(segments):
        j = (i + 1) % segments
        base_i, base_j = 2 * i, 2 * j
        idx.extend((base_i, base_i + 1, base_j))
        idx.extend((base_i + 1, base_j + 1, base_j))
    mesh.CreatePointsAttr(Vt.Vec3fArray([Gf.Vec3f(*p) for p in pts]))
    mesh.CreateFaceVertexCountsAttr([3] * (len(idx) // 3))
    mesh.CreateFaceVertexIndicesAttr(idx)
    mesh.CreateDisplayColorAttr(Vt.Vec3fArray([Gf.Vec3f(0.95, 0.75, 0.1)]))
    mesh.CreateDoubleSidedAttr(True)
    # Orient local +Y onto the plane normal, then translate to the origin. One
    # transform op keeps the drag arbitration trivial: the live translation IS
    # the slice centre the user dragged to.
    quat = Gf.Rotation(Gf.Vec3d(0, 1, 0), Gf.Vec3d(*normal)).GetQuat()
    xform = UsdGeom.Xformable(mesh.GetPrim())
    # This pxr build has no RemoveXformOp: drop the ops as plain properties
    # after clearing the order, so re-authoring never stacks transforms.
    xform.ClearXformOpOrder()
    for prop in list(mesh.GetPrim().GetProperties()):
        if prop.GetName().startswith('xformOp:'):
            mesh.GetPrim().RemoveProperty(prop.GetName())
    orient = xform.AddTransformOp()
    m = Gf.Matrix4d()
    m.SetRotate(quat)
    m.SetTranslate(Gf.Vec3d(*origin))
    orient.Set(m)
    return mesh


def disc_origin_from_xform(stage):
    """Read the disc's live translation back out (drag arbitration)."""
    from pxr import UsdGeom
    prim = stage.GetPrimAtPath(SLICE_DISC_PATH)
    if not prim or not prim.IsValid():
        return None
    m = UsdGeom.Xformable(prim).GetLocalTransformation()
    return [m[3][0], m[3][1], m[3][2]]
