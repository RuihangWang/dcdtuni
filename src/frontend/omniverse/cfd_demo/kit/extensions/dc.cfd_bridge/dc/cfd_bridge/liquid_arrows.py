"""Liquid flow arrows in the OV viewport (Kit's twin of shared/js/liquid_flow_anim.js).

Kit lays the arrows out itself from the verified result, with the rule both
viewports share (liquid_flow.flow_arrows): one arrow per moving pipe cell and
per manifold header cell, pointing along the actual flow. The browser only
sends display options and the scales of its legends (setLiquidFrame
display.arrows), so sizes and colours match the Three.js view.

Where Kit differs from the Three.js view, and why:
  * every arrow rides its edge's own polyline (from-node, its cells, to-node):
    Kit does not have the editor's tube paths, and the result cells lie on the
    rendered centreline (C1 zero-training check), so only corners are cut;
  * an arrow on a horizontal run rides on the pipe's crown (lifted by the pipe
    radius) rather than on the camera-facing side; on a vertical run (a
    manifold header) it sits on the axis as a collar wider than the pipe;
  * server branches are not drawn: they run on the hoses the editor draws
    between manifold and server, which the scene snapshot does not identify.

Sizes: radius 0.007 + 0.021 * sqrt(n) m, n = the arrow's place in the mass-flow
scale, length 2.8 * radius. Colours: the velocity scale in the chosen colour
map, quantised to BINS prototype colours (one instancer, one prototype per
colour, coloured by displayColor), or one plain colour. Pure Python except the USD helpers (pxr).
"""
from __future__ import annotations

import math

from . import liquid_colour as lc
from . import liquid_flow as lf

ARROW_PATH = '/World/LiquidFlowArrows'
BINS = 64
RADIUS_M = (0.007, 0.028)
LENGTH = 2.8
FALLBACK_SPEED = 0.05
DEFAULT_PIPE_RADIUS = 0.0125
PLAIN_RGB = (0xf2 / 255, 0xf2 / 255, 0xf2 / 255)
_KEYS = {'density', 'speedScale', 'animate', 'colour', 'mass', 'velocity', 'colormap'}


def _domain(value, name):
    if value is None:
        return None
    if (not isinstance(value, list) or len(value) != 2 or not all(isinstance(v, (int, float)) and math.isfinite(v)
                                                                   for v in value) or value[0] > value[1]):
        raise ValueError(f'arrows.{name} must be null or [lo, hi]')
    return [float(value[0]), float(value[1])]


def parse_arrow_options(raw):
    """display.arrows of setLiquidFrame: None (no arrows) or the options."""
    if raw is None:
        return None
    if not isinstance(raw, dict) or set(raw) != _KEYS:
        raise ValueError('display.arrows must be null or {' + ', '.join(sorted(_KEYS)) + '}')
    density = raw['density']
    if type(density) is not int or not 1 <= density <= 4:
        raise ValueError('arrows.density must be 1..4')
    speed = raw['speedScale']
    if not isinstance(speed, (int, float)) or not math.isfinite(speed) or speed < 0:
        raise ValueError('arrows.speedScale must be a non-negative number')
    if raw['colour'] not in ('velocity', 'plain'):
        raise ValueError('arrows.colour must be velocity or plain')
    lc.colormap_lut(raw['colormap'])
    return {'density': density, 'speedScale': float(speed), 'animate': bool(raw['animate']),
            'colour': raw['colour'], 'mass': _domain(raw['mass'], 'mass'),
            'velocity': _domain(raw['velocity'], 'velocity'), 'colormap': raw['colormap']}


def _cum(points):
    out = [0.0]
    for a, b in zip(points, points[1:]):
        out.append(out[-1] + math.dist(a, b))
    return out


def _sample(points, cum, s):
    """Point and unit tangent at arc length s (clamped)."""
    n = len(points)
    if n == 1:
        return list(points[0]), [0.0, 1.0, 0.0]
    s = min(max(s, 0.0), cum[-1])
    i = 1
    while i < n - 1 and cum[i] < s:
        i += 1
    while i < n - 1 and cum[i] - cum[i - 1] <= 0:
        i += 1
    a, b = points[i - 1], points[i]
    length = cum[i] - cum[i - 1]
    f = (s - cum[i - 1]) / length if length > 0 else 0.0
    p = [a[k] + (b[k] - a[k]) * f for k in range(3)]
    t = [(b[k] - a[k]) / length for k in range(3)] if length > 0 else [0.0, 1.0, 0.0]
    return p, t


def arrow_spec(data: dict, sample: int, options: dict) -> dict:
    """Tracks, colour bins and arrows (track, s, sMin, sMax, dir, radius,
    length, lift, bin, speed) at ``sample`` for ``options``."""
    layout = lf.flow_arrows(data, sample)
    edge_ids = [str(e) for e in data.get('edge_ids') or []]
    edge_index = {e: i for i, e in enumerate(edge_ids)}
    diameters = data.get('edge_D_m') or []
    node_ids = [str(n) for n in data.get('node_ids') or []]
    node_pos = dict(zip(node_ids, data.get('node_positions_m') or []))
    sources, sinks = data.get('edge_from') or [], data.get('edge_to') or []
    cells_of = {}
    for c, e in enumerate(data.get('cell_edge_ids') or []):
        cells_of.setdefault(str(e), []).append(c)
    positions = data.get('cell_positions') or []

    if options['colour'] == 'velocity' and options['velocity'] is not None:
        lut = lc.colormap_lut(options['colormap'])
        bins = [[c / 255 for c in lut[min(255, int((k + 0.5) / BINS * 256))]] for k in range(BINS)]
    else:
        bins = [list(PLAIN_RGB)]

    def bin_of(velocity):
        if len(bins) == 1 or velocity is None:
            return 0
        lo, hi = options['velocity']
        t = max(0.0, min(1.0, (velocity - lo) / (hi - lo + 1e-6)))
        return min(BINS - 1, int(t * BINS))

    tracks, track_of, stretch = [], {}, {}

    def place(edge):
        cells = cells_of.get(edge, [])
        i = edge_index.get(edge)
        start = node_pos.get(str(sources[i])) if i is not None and i < len(sources) else None
        end = node_pos.get(str(sinks[i])) if i is not None and i < len(sinks) else None
        points = ([list(start)] if start else []) + [list(positions[c]) for c in cells] + ([list(end)] if end else [])
        cum = _cum(points)
        offset = 1 if start else 0
        ss = [cum[k + offset] for k in range(len(cells))]
        track_of[edge] = len(tracks)
        tracks.append({'points': points, 'cum': cum})
        for k, c in enumerate(cells):
            lo = (ss[k - 1] + ss[k]) / 2 if k > 0 else 0.0
            hi = (ss[k] + ss[k + 1]) / 2 if k < len(ss) - 1 else cum[-1]
            lo, hi = min(lo, ss[k]), max(hi, ss[k])
            if hi - lo <= 1e-9:
                lo, hi = max(0.0, ss[k] - 0.05), min(cum[-1], ss[k] + 0.05)
            stretch[c] = (ss[k], lo, hi)

    mass = options['mass']
    arrows = []
    for a in layout['arrows']:
        e = a['edgeId']
        if e not in track_of:
            place(e)
        track = tracks[track_of[e]]
        s, lo, hi = stretch[a['cell']]
        _p, tangent = _sample(track['points'], track['cum'], s)
        direction = 1 if sum(tangent[k] * a['direction'][k] for k in range(3)) >= 0 else -1
        n = 1.0 if mass is None or mass[1] <= mass[0] else max(0.0, min(1.0, (a['massFlowKgS'] - mass[0]) / (mass[1] - mass[0])))
        radius = RADIUS_M[0] + (RADIUS_M[1] - RADIUS_M[0]) * math.sqrt(n)
        i = edge_index.get(e)
        d = diameters[i] if i is not None and i < len(diameters) else None
        pipe_radius = d / 2 if isinstance(d, (int, float)) and d > 0 else DEFAULT_PIPE_RADIUS
        vertical = abs(a['direction'][1]) >= 0.7
        span = hi - lo
        for j in range(options['density']):
            arrows.append({
                'edgeId': e, 'track': track_of[e], 's': s, 'sMin': lo, 'sMax': hi, 'dir': direction,
                'offset': ((s - lo) if direction > 0 else (hi - s)) / span + j / options['density'],
                # A collar on a vertical header; on the crown of a horizontal run.
                'radius': max(radius, 1.5 * pipe_radius) if vertical else radius,
                'length': (max(radius, 1.5 * pipe_radius) if vertical else radius) * LENGTH,
                'lift': 0.0 if vertical else pipe_radius + 0.6 * radius,
                'bin': bin_of(a['velocityMS']), 'speed': a['velocityMS'] if a['velocityMS'] is not None else FALLBACK_SPEED,
            })
    return {'sample': layout['sample'], 'tracks': tracks, 'bins': bins, 'arrows': arrows,
            'animate': options['animate'], 'speedScale': options['speedScale'],
            'skippedBranches': len([b for b in layout['branches'] if not b['stagnant']])}


def _quat_from_y(t):
    """(w, x, y, z) rotating +Y onto the unit vector t."""
    x, y, z = t
    if y < -0.999999:
        return (0.0, 1.0, 0.0, 0.0)
    w = math.sqrt((1.0 + y) / 2.0)
    # axis = y_hat x t = (z, 0, -x); half-angle sine folded into the division.
    return (w, z / (2 * w), 0.0, -x / (2 * w))


class ArrowMotion:
    """Where every arrow is at a time (seconds), along its own stretch."""

    def __init__(self, spec):
        self.spec = spec

    def place(self, seconds):
        positions, orientations, scales = [], [], []
        spec = self.spec
        t = seconds if spec['animate'] else 0.0
        for a in spec['arrows']:
            track = spec['tracks'][a['track']]
            span = a['sMax'] - a['sMin']
            u = (a['offset'] + (t * a['speed'] * spec['speedScale'] / span if spec['animate'] else 0.0)) % 1.0
            s = a['sMax'] - u * span if a['dir'] < 0 else a['sMin'] + u * span
            p, tangent = _sample(track['points'], track['cum'], s)
            if a['dir'] < 0:
                tangent = [-v for v in tangent]
            if a['lift'] > 0:
                p = [p[0], p[1] + a['lift'], p[2]]
            positions.append(p)
            orientations.append(_quat_from_y(tangent))
            scales.append([a['radius'], a['length'], a['radius']])
        return positions, orientations, scales


def author_arrows(stage, spec, placed, path=ARROW_PATH):
    """A PointInstancer of unit cones, one prototype per colour bin coloured by
    its displayColor alone. No material: RTX compiles a material per bound
    Look, and 64 of them stalled Kit's main loop for ~22 s on the first frame
    (measured 2026-10-22), long enough for the WebRTC client to drop the
    stream; displayColor needs no compile."""
    from pxr import Gf, UsdGeom, Vt
    remove_arrows(stage, path)
    if True:  # prims are defined through the Usd API (not inside an Sdf.ChangeBlock)
        inst = UsdGeom.PointInstancer.Define(stage, path)
        inst.GetPrim().SetCustomDataByKey('dcdtuni:liquidFlow', True)
        protos = UsdGeom.Scope.Define(stage, path + '/Prototypes')
        targets = []
        for k, rgb in enumerate(spec['bins']):
            cone = UsdGeom.Cone.Define(stage, f'{protos.GetPath()}/Arrow{k}')
            cone.CreateAxisAttr(UsdGeom.Tokens.y)
            cone.CreateHeightAttr(1.0)
            cone.CreateRadiusAttr(1.0)
            cone.CreateDisplayColorAttr(Vt.Vec3fArray([Gf.Vec3f(*rgb)]))
            targets.append(cone.GetPath())
        inst.CreatePrototypesRel().SetTargets(targets)
        inst.CreateProtoIndicesAttr(Vt.IntArray([a['bin'] for a in spec['arrows']]))
        _write(inst, placed, scales=True)
    return path


def _write(inst, placed, scales=False):
    from pxr import Gf, Vt
    positions, orientations, sizes = placed
    inst.GetPositionsAttr().Set(Vt.Vec3fArray([Gf.Vec3f(*p) for p in positions])) if inst.GetPositionsAttr() \
        else inst.CreatePositionsAttr(Vt.Vec3fArray([Gf.Vec3f(*p) for p in positions]))
    quats = Vt.QuathArray([Gf.Quath(q[0], Gf.Vec3h(q[1], q[2], q[3])) for q in orientations])
    inst.GetOrientationsAttr().Set(quats) if inst.GetOrientationsAttr() else inst.CreateOrientationsAttr(quats)
    if scales:
        inst.CreateScalesAttr(Vt.Vec3fArray([Gf.Vec3f(*s) for s in sizes]))


def update_arrows(stage, path, placed):
    """Move the arrows (positions and orientations only)."""
    from pxr import UsdGeom
    prim = stage.GetPrimAtPath(path)
    if prim:
        _write(UsdGeom.PointInstancer(prim), placed)


def remove_arrows(stage, path=ARROW_PATH):
    if stage.GetPrimAtPath(path):
        stage.RemovePrim(path)
