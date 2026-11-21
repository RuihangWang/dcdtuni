"""PRIVATE native-viewport visibility geometry proposal, not encoded parity.

The caller must separately sample/close actual current viewport ownership and
projection fences. This pure helper supplies no sampler, commit permission,
source/UID authority, encoded frame binding, or gridRaster success receipt.
"""
from __future__ import annotations
import bisect
import math
from .grid_raster import GridRefused, _area, _clip_h, _clip_poly


def _camera_above(m):
    """True when the camera of grid-plane-to-clip ``m`` (row vectors) is above
    the plane (local y > 0). Its centre solves [x, y, z, 1] * m = (0, 0, *, 0);
    an orthographic camera has no centre and is taken to look down."""
    rows = [[m[0][j], m[1][j], m[2][j]] for j in (0, 1, 3)]
    rhs = [-m[3][j] for j in (0, 1, 3)]
    def det(a):
        return (a[0][0]*(a[1][1]*a[2][2]-a[1][2]*a[2][1]) - a[0][1]*(a[1][0]*a[2][2]-a[1][2]*a[2][0])
                + a[0][2]*(a[1][0]*a[2][1]-a[1][1]*a[2][0]))
    d = det(rows)
    if abs(d) < 1e-14:
        return True
    with_y = [[row[0], value, row[2]] for row, value in zip(rows, rhs)]
    return det(with_y) / d > 0


def native_grid_strips(grid, points, colors, world_to_native_image, native_resolution, width_native_px=1.0):
    """Lower canonical lines to their plane, measured in native image pixels.

    native_resolution is the public viewport texture's own observed resolution.
    It MUST NOT be populated from video dimensions or described as decoded size.
    The transformation is actual native world-to-image NDC, not UI/framebuffer
    or video NDC. No CSS, DPI, crop, contain, or encoder values are manufactured.
    """
    from pxr import Gf
    if (len(native_resolution) != 2 or
            any(type(v) is not int or not 1 <= v <= 4096 for v in native_resolution)):
        raise GridRefused('native image resolution unavailable')
    if type(width_native_px) not in (int,float) or not math.isfinite(width_native_px) or not 0 < width_native_px <= 8:
        raise GridRefused('native pixel width invalid')
    if len(world_to_native_image) != 16 or any(type(v) not in (int,float) or not math.isfinite(v) for v in world_to_native_image):
        raise GridRefused('native image projection unavailable')
    if (len(grid['matrix']) != 16 or any(type(v) not in (int,float) or not math.isfinite(v) for v in grid['matrix'])
            or type(grid['divisions']) is not int or not 4 <= grid['divisions'] <= 4000
            or len(points) != len(colors) or len(points) != 4*(grid['divisions']+1)):
        raise GridRefused('canonical line inventory invalid')
    if any(len(p) != 3 or any(not math.isfinite(v) for v in p) for p in points):
        raise GridRefused('canonical points invalid')
    if any(len(p) != 3 or any(not math.isfinite(v) or not 0 <= v <= 1 for v in p) for p in colors):
        raise GridRefused('canonical colors invalid')
    empty = {'points':[], 'colors':[], 'counts':[], 'sourceLines':[],
             'widthDomain':'native-viewport-image-pixel', 'widthNativePx':float(width_native_px),
             'encodedFrameProof':'unresolved', 'gridPixelVerified':False}
    if not grid['visible']:
        return empty
    m = Gf.Matrix4d(*grid['matrix']) * Gf.Matrix4d(*world_to_native_image)
    if abs(m.GetDeterminant()) <= 1e-14:
        raise GridRefused('native projection degenerate')
    half = max(abs(p[0]) for p in points)
    def clip(xz):
        return Gf.Vec4d(xz[0],0,xz[1],1)*m
    def ndc(xz):
        p = clip(xz)
        if p[3] <= 1e-9:
            raise GridRefused('grid point behind native camera')
        return p[0]/p[3],p[1]/p[3]
    poly = [(-half,-half),(half,-half),(half,half),(-half,half)]
    for i in range(7):
        poly = _clip_poly(poly,lambda p,i=i:_clip_h(clip(p))[i])
    if len(poly) < 3:
        return empty
    domain = [ndc(p) for p in poly]
    if abs(_area(domain)) < 1e-12:
        raise GridRefused('edge-on native grid has no finite area')
    if _area(domain) < 0:
        domain.reverse()
    def unproject(u,v):
        a,b,c,d = m[0][0]-u*m[0][3],m[2][0]-u*m[2][3],m[0][1]-v*m[0][3],m[2][1]-v*m[2][3]
        e,f = u*m[3][3]-m[3][0],v*m[3][3]-m[3][1]
        det = a*d-b*c
        if abs(det) < 1e-14*max(abs(a*d),abs(b*c),1):
            raise GridRefused('native horizon inverse unbounded')
        x,z = (e*d-b*f)/det,(a*f-e*c)/det
        if not all(map(math.isfinite,(x,z))) or max(abs(x),abs(z)) > half+1e-6:
            raise GridRefused('strip escaped canonical extent')
        if min(_clip_h(clip((x,z)))) < -1e-7:
            raise GridRefused('strip escaped native frustum')
        return x,0.,z
    W,H = native_resolution
    # Each strip faces the camera: RTX emits an opaque UsdPreviewSurface from
    # its front side only, and a strip seen from behind rendered black
    # (2026-10-23 Kit probe: emission 0.5 shows 206 from the front, 1 from
    # behind). The camera's side of the plane is the sign of its centre's
    # height, the point every clip x, y and w vanishes at.
    above = _camera_above(m)
    k = [p[2] for p in points[::4]]
    xlo,xhi = min(p[0] for p in poly),max(p[0] for p in poly)
    zlo,zhi = min(p[1] for p in poly),max(p[1] for p in poly)
    hiz = range(max(0,bisect.bisect_left(k,zlo-1e-7)-1),min(len(k),bisect.bisect_right(k,zhi+1e-7)+1))
    hix = range(max(0,bisect.bisect_left(k,xlo-1e-7)-1),min(len(k),bisect.bisect_right(k,xhi+1e-7)+1))
    vertices,vertex_colors,counts,source_lines = [],[],[],[]
    for j in sorted([4*i for i in hiz]+[4*i+2 for i in hix]):
        a,b = (points[j][0],points[j][2]),(points[j+1][0],points[j+1][2])
        ca,cb,low,high = clip(a),clip(b),0.,1.
        for fa,fb in zip(_clip_h(ca),_clip_h(cb)):
            if fa < 0 and fb < 0:
                low,high = 1,0
                break
            if (fa < 0) != (fb < 0):
                t = fa/(fa-fb)
                if fa < 0: low = max(low,t)
                else: high = min(high,t)
        if low >= high:
            continue
        aa = tuple(x+(y-x)*low for x,y in zip(a,b))
        bb = tuple(x+(y-x)*high for x,y in zip(a,b))
        u,v = ndc(aa),ndc(bb)
        dx,dy = (v[0]-u[0])*W/2,(v[1]-u[1])*H/2
        length = math.hypot(dx,dy)
        if length < 1e-8:
            continue
        nx,ny = -dy/length*width_native_px/W,dx/length*width_native_px/H
        ribbon = [(u[0]+nx,u[1]+ny),(v[0]+nx,v[1]+ny),(v[0]-nx,v[1]-ny),(u[0]-nx,u[1]-ny)]
        for e0,e1 in zip(domain,domain[1:]+domain[:1]):
            ribbon = _clip_poly(ribbon,lambda p,e0=e0,e1=e1:(e1[0]-e0[0])*(p[1]-e0[1])-(e1[1]-e0[1])*(p[0]-e0[0]))
        if len(ribbon) < 3 or abs(_area(ribbon)) < 1e-16:
            continue
        strip = [unproject(*p) for p in ribbon]
        up = sum((a[2]-b[2])*(a[0]+b[0]) for a,b in zip(strip,strip[1:]+strip[:1])) > 0
        if up != above:
            strip.reverse()
        vertices.extend(strip)
        vertex_colors.extend([colors[j]]*len(ribbon))
        counts.append(len(ribbon)); source_lines.append(j//2)
    return dict(empty,points=vertices,colors=vertex_colors,counts=counts,sourceLines=source_lines)
