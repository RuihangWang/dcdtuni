"""Pure geometry helpers for the draggable in-viewport gizmos (ROI box, seed sphere).

Why this module exists
----------------------
Both the ROI box and the streamline seed sphere are dragged with Omniverse's transform
gizmo. A drag authors an XFORM on the prim; it does NOT rewrite the prim's authored
points. But the voxeliser reads the ROI box's LOCAL bound (its authored points) and
ignores the xform stack, and ``_place_seed_prim`` used to overwrite the sphere's xform
with the last value the UI sent. So a drag was invisible to the render (ROI) or undone
on the next Apply (seed sphere).

The fix is to BAKE a dragged xform back into world-space points, and to detect that a
drag happened at all. That is pure arithmetic, so it lives here and is unit-tested
without Kit or USD.

USD convention: ``Gf.Matrix4d`` is row-major with the ROW-VECTOR convention
(``v' = v * M``), so the translation lives in row 3. Inputs may be a ``Gf.Matrix4d`` or
plain nested sequences -- both index as ``m[row][col]``.
"""

# A source extent below this is treated as degenerate (avoids divide-by-zero when a
# box is flat on an axis); a target extent is never allowed below this either.
_EPS_SIZE = 1e-9
_MIN_TARGET_SIZE = 1e-3
# Gizmo drags move things by centimetres at least; this only rejects float noise.
_EPS_EQ = 1e-6


def aabb_of(points):
    """Axis-aligned bounds of ``points`` as ``([minx,miny,minz], [maxx,maxy,maxz])``.
    Returns ``None`` for an empty sequence."""
    pts = [p for p in points]
    if not pts:
        return None
    mn = [min(float(p[i]) for p in pts) for i in range(3)]
    mx = [max(float(p[i]) for p in pts) for i in range(3)]
    return mn, mx


def transform_points(points, m):
    """Apply a row-vector 4x4 matrix to each point (``v' = v * M``).

    This turns a gizmo-dragged prim's LOCAL points into the WORLD points the user
    actually sees, which is what the voxeliser needs baked in."""
    if m is None:
        return [tuple(float(c) for c in p) for p in points]
    out = []
    for p in points:
        x, y, z = float(p[0]), float(p[1]), float(p[2])
        out.append((
            x * m[0][0] + y * m[1][0] + z * m[2][0] + m[3][0],
            x * m[0][1] + y * m[1][1] + z * m[2][1] + m[3][1],
            x * m[0][2] + y * m[1][2] + z * m[2][2] + m[3][2],
        ))
    return out


def remap_points_to_aabb(points, mn, mx):
    """Linearly remap ``points`` so their AABB becomes exactly ``[mn, mx]``.

    Self-correcting: it reads the points' CURRENT bounds each time, so calling it
    repeatedly against any prior state still lands on the target. That is why the ROI
    box is resized by re-authoring points rather than by an xform -- the voxeliser only
    ever looks at the points."""
    pts = [p for p in points]
    if not pts:
        return []
    cmin, cmax = aabb_of(pts)
    csize = [max(cmax[i] - cmin[i], _EPS_SIZE) for i in range(3)]
    tsize = [max(float(mx[i]) - float(mn[i]), _MIN_TARGET_SIZE) for i in range(3)]
    return [
        tuple(float(mn[i]) + (float(p[i]) - cmin[i]) / csize[i] * tsize[i]
              for i in range(3))
        for p in pts
    ]


def is_identity(m, eps=_EPS_EQ):
    """True when ``m`` is the identity (within float noise), i.e. the prim has NOT been
    dragged. ``None`` -- no xform authored at all -- counts as identity."""
    if m is None:
        return True
    for r in range(4):
        for c in range(4):
            expect = 1.0 if r == c else 0.0
            if abs(float(m[r][c]) - expect) > eps:
                return False
    return True


def moved(commanded, live, eps=_EPS_EQ):
    """True when the prim's ``live`` value differs from what the backend last
    ``commanded`` -- i.e. the user dragged it in the viewport, so the live value wins.

    A missing ``commanded`` (nothing recorded yet) means we cannot claim ownership of
    the prim's position, so the live value is treated as authoritative."""
    if commanded is None or live is None:
        return True
    if len(commanded) != len(live):
        return True
    return any(abs(float(a) - float(b)) > eps for a, b in zip(commanded, live))
