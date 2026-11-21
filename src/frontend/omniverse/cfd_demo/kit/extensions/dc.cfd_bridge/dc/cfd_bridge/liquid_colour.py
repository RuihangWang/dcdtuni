"""Liquid-cooling result colours for the OV display (Stage 4).

Kit reads a server-granted DAE ``result.json`` itself and computes every
colour here, so the browser only ever names a frame. The temperature rules
are the Three.js ones (liquidTempColor.ts) extended to every liquid part, and
the colour table is the same ParaView coolwarm (ppColormaps.ts); a shared
fixture (src/frontend/shared/testdata) pins both implementations to the same
numbers.

Part rules (one temperature per target at a time sample):
  pipe            mean cell temperature over the pipe binding's edges
  supply manifold mean cell temperature over the rack's ``*_branch_in`` edges
  return manifold mean cell temperature over the rack's ``*_branch_out`` edges
  tee junction    node ``<pipe>_tee_<id>`` temperature
  flow valve      binding ``<pipe>/<valve id>`` edge temperature (wins over the tee node)
  CDU             secondary supply node ``<cdu>_supply`` temperature
  server          max chip temperature over its cold plates, joined by edge id

Network parts share one colour domain; the chip domain spans every chip
temperature (servers show their hottest); each defaults to the current
sample's min..max like Three.js. Pure module (no omni/pxr).

A ``pipe_segment`` binding that lists no edges owns the edge named after its
uid (liquid_flow.binding_edges: older results left an untee'd pipe unbound).

Display quantities (``quantity_frame``; liquidDisplay.ts is the Three.js
twin, liquid_display_expected.json the shared contract). The network can be
coloured by one of:
  temperature  K      the part rules above; cells: cell_temp_K
  flow         L/min  liquid_flow part flows (manifold = sum of its
                      branches, tee/CDU = node throughput); cells: their
                      edge's |flow| / rho
  velocity     m/s    liquid_flow part velocities, a manifold taking its
                      header's; tees and the CDU have none (left unpainted);
                      cells: their edge's velocity
  pressure     kPa    absolute, hydrostatic included: tee, junction and CDU =
                      their node; pipe/manifold/valve = mean over their cells,
                      a cell interpolating its edge's end pressures at
                      (k + 0.5) / n; an edge without cells = mean of its ends
A ``pipe_junction`` model (a ring-feed tee) is a ``junction`` part: its
binding's node (temperature, pressure, and for flow what passes through it).
Chips always show chip temperature. The display domain of a quantity spans
its parts AND its cells (Three.js paints cells along the pipes); Kit is sent
that domain by the browser, so both viewports share one scale.
"""
from __future__ import annotations

import math

NETWORK = 'network'
CHIPS = 'chips'
REQUIRED_KEYS = ('time_s', 'object_bindings', 'cell_edge_ids', 'cell_temp_K', 'node_ids',
                 'node_temperature_K', 'edge_ids', 'edge_outlet_temp_K', 'cp_details',
                 'chip_temp_K', 'rack_summaries')


def _js_round(x: float) -> int:
    """Math.round for the non-negative values of the colour table."""
    return int(math.floor(x + 0.5))


def coolwarm_lut():
    """The 256-entry ParaView coolwarm table of ppColormaps.colormapData."""
    out = []
    for i in range(256):
        t = i / 255
        if t < 0.5:
            s = t * 2
            rgb = (59 + s * (255 - 59), 76 + s * (255 - 76), 192 + s * (255 - 192))
        else:
            s = (t - 0.5) * 2
            rgb = (255 - s * (255 - 180), 255 - s * (255 - 4), 255 - s * (255 - 38))
        out.append(tuple(_js_round(c) for c in rgb))
    return out


_LUT = coolwarm_lut()


def _jet_lut():
    out = []
    for i in range(256):
        t = i / 255
        rgb = (min(1.0, max(0.0, 1.5 - abs(4 * t - 3))), min(1.0, max(0.0, 1.5 - abs(4 * t - 2))),
               min(1.0, max(0.0, 1.5 - abs(4 * t - 1))))
        out.append(tuple(_js_round(c * 255) for c in rgb))
    return out


def _viridis_lut():
    stops = [(0.0, 68, 1, 84), (0.25, 59, 82, 139), (0.5, 33, 145, 140), (0.75, 94, 201, 98), (1.0, 253, 231, 37)]
    out = []
    for i in range(256):
        t = i / 255
        s0, s1 = stops[0], stops[1]
        for j in range(1, len(stops)):
            if t <= stops[j][0]:
                s0, s1 = stops[j - 1], stops[j]
                break
        f = (t - s0[0]) / ((s1[0] - s0[0]) or 1)
        out.append(tuple(_js_round(s0[k] + f * (s1[k] - s0[k])) for k in (1, 2, 3)))
    return out


_LUTS = {'coolwarm': _LUT, 'jet': _jet_lut(), 'viridis': _viridis_lut()}


def colormap_lut(name: str):
    """The 256-entry table of a ppColormaps.colormapData preset."""
    if name not in _LUTS:
        raise ValueError(f'unknown colour map {name!r}')
    return _LUTS[name]


def value_to_rgb8(value: float, lo: float, hi: float, colormap: str = 'coolwarm'):
    """tempToColor for any scalar: clamp, floor(t*255) into the table."""
    t = max(0.0, min(1.0, (value - lo) / (hi - lo + 1e-6)))
    return colormap_lut(colormap)[int(math.floor(t * 255))]


def temp_to_rgb8(T: float, lo: float, hi: float):
    """liquidTempColor.tempToColor: clamp, floor(t*255) into the table."""
    return value_to_rgb8(T, lo, hi, 'coolwarm')


QUANTITIES = ('temperature', 'flow', 'velocity', 'pressure')
UNITS = {'temperature': 'K', 'flow': 'L/min', 'velocity': 'm/s', 'pressure': 'kPa'}
DEFAULT_COLORMAPS = {'temperature': 'coolwarm', 'flow': 'viridis', 'velocity': 'viridis', 'pressure': 'viridis'}


def parse_display(display):
    """The browser's display choice of a setLiquidFrame: None (temperature on
    coolwarm, the original command) or {quantity, colormap?, arrows?} (the
    arrows are liquid_arrows.parse_arrow_options's)."""
    if display is None:
        return 'temperature', None
    if not isinstance(display, dict) or not set(display) <= {'quantity', 'colormap', 'arrows'} or 'quantity' not in display:
        raise ValueError('display must be null or {quantity, colormap?, arrows?}')
    quantity = display['quantity']
    if quantity not in QUANTITIES:
        raise ValueError(f'unknown liquid display quantity {quantity!r}')
    colormap = display.get('colormap')
    if colormap is not None and (not isinstance(colormap, str) or colormap not in _LUTS):
        raise ValueError(f'unknown colour map {colormap!r}')
    return quantity, colormap


def _flow_module():
    """liquid_flow, as a package sibling inside Kit, or by file next to this one
    (the server loads this module on its own to check grants)."""
    try:
        from . import liquid_flow
        return liquid_flow
    except ImportError:
        import importlib.util
        import os
        import sys
        name = '_dcdtuni_liquid_flow'
        if name in sys.modules:
            return sys.modules[name]
        spec = importlib.util.spec_from_file_location(name, os.path.join(os.path.dirname(__file__), 'liquid_flow.py'))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sys.modules[name] = module
        return module


def _network_parts(data):
    """(kind, modelUid, equipmentUid, liquidPart, edges-or-node) of every
    coloured network part, in the order of the temperature targets."""
    edge_ids = set(str(e) for e in (data.get('edge_ids') or []))
    node_ids = [str(n) for n in (data.get('node_ids') or [])]
    lf = _flow_module()
    out, fittings = [], {}
    for key, b in (data.get('object_bindings') or {}).items():
        kind = b.get('kind')
        edges = lf.binding_edges(key, b, edge_ids)
        if kind == 'pipe_segment':
            out.append(('pipe', key, '', '', edges))
            prefix = f'{key}_tee_'
            for node in node_ids:
                if node.startswith(prefix):
                    fittings.setdefault((key, node[len(prefix):]), ('tee', node))
        elif kind == 'valve' and '/' in key:
            pipe, vid = key.split('/', 1)
            fittings[(pipe, vid)] = ('valve', edges)
        elif kind == 'rack':
            sup = [e for e in edges if e.endswith('_branch_in')]
            ret = [e for e in edges if e.endswith('_branch_out')]
            if sup:
                out.append(('manifold', key, '', 'supply-manifold', sup))
            if ret:
                out.append(('manifold', key, '', 'return-manifold', ret))
        elif kind == 'cdu':
            out.append(('cdu', key, '', '', f'{key}_supply'))
        elif kind == 'pipe_junction' and b.get('node_ids'):
            out.append(('junction', key, '', '', str(b['node_ids'][0])))
    for (pipe, vid), (kind, ref) in sorted(fittings.items()):
        out.append((kind, pipe, vid, '', ref))
    return out


def _cell_pressures_kpa(data, s):
    """Each cell's pressure, interpolated between its edge's end nodes."""
    cell_edges = [str(e) for e in (data.get('cell_edge_ids') or [])]
    edge_ids = [str(e) for e in (data.get('edge_ids') or [])]
    sources, sinks = data.get('edge_from'), data.get('edge_to')
    node_index = {str(n): i for i, n in enumerate(data.get('node_ids') or [])}
    rows = data.get('node_pressure_Pa') or []
    if not isinstance(sources, list) or not isinstance(sinks, list) or len(sources) != len(edge_ids):
        return [None] * len(cell_edges)
    edge_index = {e: i for i, e in enumerate(edge_ids)}

    def node_pa(node):
        i = node_index.get(str(node))
        return None if i is None or i >= len(rows) else LiquidResult._at(rows[i], s)

    counts, rank = {}, []
    for e in cell_edges:
        rank.append(counts.get(e, 0))
        counts[e] = counts.get(e, 0) + 1
    out = []
    for c, e in enumerate(cell_edges):
        i = edge_index.get(e)
        pa = node_pa(sources[i]) if i is not None else None
        pb = node_pa(sinks[i]) if i is not None else None
        if pa is None or pb is None:
            out.append(None)
        else:
            out.append((pa + (pb - pa) * (rank[c] + 0.5) / counts[e]) / 1000)
    return out


def quantity_frame(data: dict, sample: int, quantity: str) -> dict:
    """The value of every network part and every pipe cell in ``quantity``."""
    if quantity not in QUANTITIES:
        raise ValueError(f'unknown liquid display quantity {quantity!r}')
    lf = _flow_module()
    times = data.get('time_s') or []
    n = len(times)
    s = n - 1 if sample < 0 else int(sample)
    if not 0 <= s < n:
        raise ValueError(f'sample {sample} outside 0..{n - 1}')
    cell_edges = [str(e) for e in (data.get('cell_edge_ids') or [])]
    cells_by_edge = {}
    for c, e in enumerate(cell_edges):
        cells_by_edge.setdefault(e, []).append(c)
    parts = _network_parts(data)
    values = []
    if quantity == 'temperature':
        result = LiquidResult(data)
        cells = [LiquidResult._at(row, s) for row in data['cell_temp_K']]
        for kind, model, equip, side, ref in parts:
            if kind in ('cdu', 'tee', 'junction'):
                values.append(result.node_temp(ref, s))
            else:
                values.append(result.edge_mean(ref, s))
    elif quantity in ('flow', 'velocity'):
        fr = lf.flow_frame(data, s)
        key = 'volumeFlowLpm' if quantity == 'flow' else 'velocityMS'
        cells = [c[key] for c in fr['cells']]
        targets = {(t['kind'], t['modelUid'], t['equipmentUid'], t['liquidPart']): t for t in fr['targets']}
        edges = {e['edgeId']: e for e in fr['edges']}
        rack_edges = {k: lf.binding_edges(k, b, edges) for k, b in (data.get('object_bindings') or {}).items()
                      if b.get('kind') == 'rack'}
        for kind, model, equip, side, ref in parts:
            if kind == 'junction':
                through = lf.node_throughput(fr, ref) if quantity == 'flow' else None
                values.append(None if through is None else through / fr['rhoKgM3'] * 60000)
                continue
            if quantity == 'velocity' and kind == 'manifold':
                marker = '_mf_supply' if side == 'supply-manifold' else '_mf_return'
                speeds = [edges[e]['velocityMS'] for e in rack_edges.get(model, [])
                          if marker in e and not e.endswith('_branch_in') and not e.endswith('_branch_out')
                          and e in edges and edges[e]['velocityMS'] is not None]
                values.append(sum(speeds) / len(speeds) if speeds else None)
                continue
            t = targets.get((kind, model, equip, side))
            values.append(None if t is None else t[key])
    else:
        cells = _cell_pressures_kpa(data, s)
        edge_ids = [str(e) for e in (data.get('edge_ids') or [])]
        edge_index = {e: i for i, e in enumerate(edge_ids)}
        sources, sinks = data.get('edge_from') or [], data.get('edge_to') or []
        node_index = {str(nd): i for i, nd in enumerate(data.get('node_ids') or [])}
        rows = data.get('node_pressure_Pa') or []

        def node_kpa(node):
            i = node_index.get(str(node))
            v = None if i is None or i >= len(rows) else LiquidResult._at(rows[i], s)
            return None if v is None else v / 1000

        def edge_kpa(edge_list):
            found = [cells[c] for e in edge_list for c in cells_by_edge.get(e, ()) if cells[c] is not None]
            if not found:
                for e in edge_list:
                    i = edge_index.get(e)
                    if i is None or i >= len(sources) or i >= len(sinks):
                        continue
                    a, b = node_kpa(sources[i]), node_kpa(sinks[i])
                    if a is not None and b is not None:
                        found.append((a + b) / 2)
            return sum(found) / len(found) if found else None

        for kind, model, equip, side, ref in parts:
            values.append(node_kpa(ref) if kind in ('cdu', 'tee', 'junction') else edge_kpa(ref))
    out_parts = [{'kind': k, 'modelUid': m, 'equipmentUid': q, 'liquidPart': sd, 'value': v}
                 for (k, m, q, sd, _r), v in zip(parts, values) if v is not None]
    pool = [p['value'] for p in out_parts] + [v for v in cells if v is not None]
    return {'sample': s, 'timeSeconds': float(times[s]), 'quantity': quantity, 'unit': UNITS[quantity],
            'parts': out_parts, 'cells': cells, 'domain': [min(pool), max(pool)] if pool else None}


def quantity_global_domain(data: dict, quantity: str):
    """quantity_frame domains over EVERY sample."""
    out = None
    for s in range(len(data.get('time_s') or [])):
        d = quantity_frame(data, s, quantity)['domain']
        if d is not None:
            out = list(d) if out is None else [min(out[0], d[0]), max(out[1], d[1])]
    return out


def missing_keys(data) -> list:
    return [k for k in REQUIRED_KEYS if not isinstance(data, dict) or k not in data]


class LiquidResult:
    """Validated view over a differentiable-DAE result with per-part rules."""

    def __init__(self, data: dict):
        missing = missing_keys(data)
        if missing:
            raise ValueError(f'liquid result lacks {", ".join(missing)} (only DAE results with object bindings colour)')
        self.data = data
        self.times = [float(t) for t in data['time_s']]
        if not self.times:
            raise ValueError('liquid result has no time samples')
        self.cells_by_edge = {}
        for i, edge in enumerate(data['cell_edge_ids']):
            self.cells_by_edge.setdefault(str(edge), []).append(i)
        self.node_index = {str(n): i for i, n in enumerate(data['node_ids'])}
        self.edge_index = {str(e): i for i, e in enumerate(data['edge_ids'])}
        self.cp_index = {str(c.get('edge_id')): i for i, c in enumerate(data['cp_details'])}
        self.targets = self._targets()

    @property
    def samples(self) -> int:
        return len(self.times)

    def sample_index(self, sample: int) -> int:
        n = self.samples
        s = n - 1 if sample < 0 else int(sample)
        if not 0 <= s < n:
            raise ValueError(f'sample {sample} outside 0..{n - 1}')
        return s

    @staticmethod
    def _at(row, s):
        if isinstance(row, list):
            v = row[s] if s < len(row) else None
        else:
            v = row
        return float(v) if isinstance(v, (int, float)) and math.isfinite(v) else None

    def edge_mean(self, edges, s):
        values = []
        for e in edges:
            for c in self.cells_by_edge.get(str(e), ()):
                v = self._at(self.data['cell_temp_K'][c], s)
                if v is not None:
                    values.append(v)
        if not values:  # edges without cells (pump/hx/control valve): outlet temperature
            for e in edges:
                i = self.edge_index.get(str(e))
                v = None if i is None else self._at(self.data['edge_outlet_temp_K'][i], s)
                if v is not None:
                    values.append(v)
        return sum(values) / len(values) if values else None

    def node_temp(self, node, s):
        i = self.node_index.get(str(node))
        return None if i is None else self._at(self.data['node_temperature_K'][i], s)

    def chip_max(self, cp_edges, s):
        values = []
        for e in cp_edges:
            i = self.cp_index.get(str(e))
            v = None if i is None else self._at(self.data['chip_temp_K'][i], s)
            if v is not None:
                values.append(v)
        return max(values) if values else None

    def _targets(self):
        """[(kind, modelUid, equipmentUid, liquidPart, group, rule)] — rule(s) -> K."""
        out = []
        bindings = self.data['object_bindings']
        valve_ids = {}
        for key, b in bindings.items():
            kind = b.get('kind')
            edges = list(b.get('edge_ids') or [])
            if not edges and kind == 'pipe_segment' and str(key) in self.edge_index:
                edges = [str(key)]  # an untee'd pipe's one edge is named after it
            if kind == 'pipe_segment':
                out.append(('pipe', key, '', '', NETWORK, lambda s, e=edges: self.edge_mean(e, s)))
                prefix = f'{key}_tee_'
                for node in self.node_index:
                    if node.startswith(prefix):
                        valve_ids.setdefault((key, node[len(prefix):]), ('node', node))
            elif kind == 'valve' and '/' in key:
                pipe, vid = key.split('/', 1)
                valve_ids[(pipe, vid)] = ('edges', edges)  # a real valve edge beats its tee node
            elif kind == 'rack':
                sup = [e for e in edges if str(e).endswith('_branch_in')]
                ret = [e for e in edges if str(e).endswith('_branch_out')]
                if sup:
                    out.append(('manifold', key, '', 'supply-manifold', NETWORK, lambda s, e=sup: self.edge_mean(e, s)))
                if ret:
                    out.append(('manifold', key, '', 'return-manifold', NETWORK, lambda s, e=ret: self.edge_mean(e, s)))
            elif kind == 'cdu':
                out.append(('cdu', key, '', '', NETWORK, lambda s, n=f'{key}_supply': self.node_temp(n, s)))
            elif kind == 'pipe_junction' and b.get('node_ids'):
                out.append(('junction', key, '', '', NETWORK, lambda s, n=str(b['node_ids'][0]): self.node_temp(n, s)))
        for (pipe, vid), (how, ref) in sorted(valve_ids.items()):
            kind = 'valve' if how == 'edges' else 'tee'
            rule = (lambda s, e=ref: self.edge_mean(e, s)) if how == 'edges' else (lambda s, n=ref: self.node_temp(n, s))
            out.append((kind, pipe, vid, '', NETWORK, rule))
        for rack in self.data['rack_summaries']:
            for srv in rack.get('servers') or []:
                cps = list(srv.get('cp_edge_ids') or [])
                out.append(('server', str(rack.get('rack_id')), str(srv.get('server_id')), '', CHIPS,
                            lambda s, c=cps: self.chip_max(c, s)))
        return out

    def frame(self, sample: int, domains=None, quantity: str = 'temperature', colormap=None):
        """Values and 8-bit colours of every target at ``sample``.

        ``domains`` may pin {'network': [lo, hi], 'chips': [lo, hi]}; a group
        without a pin uses this sample's min..max (the Three.js rule; for a
        quantity other than temperature, the quantity_frame domain). The
        network shows ``quantity`` in ``colormap`` (default: coolwarm for
        temperature, viridis otherwise); chips always show temperature."""
        if quantity not in QUANTITIES:
            raise ValueError(f'unknown liquid display quantity {quantity!r}')
        cmap = colormap or DEFAULT_COLORMAPS[quantity]
        colormap_lut(cmap)
        s = self.sample_index(sample)
        temps = []
        network_domain = None
        if quantity == 'temperature':
            for kind, model, equip, part, group, rule in self.targets:
                T = rule(s)
                if T is not None:
                    temps.append((kind, model, equip, part, group, T))
        else:
            q = quantity_frame(self.data, s, quantity)
            network_domain = q['domain']
            temps = [(p['kind'], p['modelUid'], p['equipmentUid'], p['liquidPart'], NETWORK, p['value'])
                     for p in q['parts']]
            for kind, model, equip, part, group, rule in self.targets:
                if group == CHIPS:
                    T = rule(s)
                    if T is not None:
                        temps.append((kind, model, equip, part, group, T))
        out = {'sample': s, 'timeSeconds': self.times[s], 'quantity': quantity, 'colormap': cmap,
               'groups': {}, 'targets': []}
        all_chips = [v for v in (self._at(row, s) for row in self.data['chip_temp_K']) if v is not None]
        for group in (NETWORK, CHIPS):
            # Chips scale over every chip (the Three.js rule), servers show their hottest.
            vals = all_chips if group == CHIPS else [t[5] for t in temps if t[4] == group]
            count = len(vals)
            if group == NETWORK and network_domain is not None:
                vals = list(network_domain)  # parts AND cells (quantity_frame)
            pinned = (domains or {}).get(group)
            if pinned is not None:
                lo, hi = float(pinned[0]), float(pinned[1])
                if not (math.isfinite(lo) and math.isfinite(hi) and lo < hi):
                    raise ValueError(f'{group} domain must be finite and ascending')
                source = 'explicit'
            elif vals:
                lo, hi, source = min(vals), max(vals), 'frame min..max'
            else:
                continue
            out['groups'][group] = {'domain': [lo, hi], 'source': source, 'count': count}
        for kind, model, equip, part, group, value in temps:
            dom = out['groups'].get(group, {}).get('domain')
            table = cmap if group == NETWORK else 'coolwarm'
            rgb = value_to_rgb8(value, dom[0], dom[1], table) if dom else (128, 128, 128)
            entry = {'kind': kind, 'modelUid': model, 'equipmentUid': equip,
                     'liquidPart': part, 'group': group, 'value': value, 'rgb': list(rgb)}
            if group == CHIPS or quantity == 'temperature':
                entry['T'] = value
            out['targets'].append(entry)
        return out


def bind_result(granted: dict, raw: bytes, scene_project_uid, acked_project_hash):
    """The Kit-side binding of a granted result, pure so it is testable:
    the file must still hash to the grant (a newer step or an edit means
    "request a new grant"), and it must belong to the project the OV scene
    shows. Returns (LiquidResult, projectContentMatches)."""
    import hashlib
    import json
    if hashlib.sha256(raw).hexdigest() != granted['resultSha256']:
        raise ValueError('result changed since the grant was issued (a newer step landed); request a new grant')
    if scene_project_uid != granted['projectUid']:
        raise ValueError(f'liquid result belongs to project {granted["projectUid"]!r}, '
                         f'the OV scene shows {scene_project_uid!r}')
    result = LiquidResult(json.loads(raw))
    return result, granted['projectContentSha256'] == acked_project_hash
