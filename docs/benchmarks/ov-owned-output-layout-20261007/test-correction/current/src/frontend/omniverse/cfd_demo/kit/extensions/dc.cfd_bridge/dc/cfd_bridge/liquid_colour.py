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


def temp_to_rgb8(T: float, lo: float, hi: float):
    """liquidTempColor.tempToColor: clamp, floor(t*255) into the table."""
    t = max(0.0, min(1.0, (T - lo) / (hi - lo + 1e-6)))
    return _LUT[int(math.floor(t * 255))]


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

    def frame(self, sample: int, domains=None):
        """Temperatures and 8-bit colours of every target at ``sample``.

        ``domains`` may pin {'network': [lo, hi], 'chips': [lo, hi]}; a group
        without a pin uses this sample's min..max (the Three.js rule)."""
        s = self.sample_index(sample)
        temps = []
        for kind, model, equip, part, group, rule in self.targets:
            T = rule(s)
            if T is not None:
                temps.append((kind, model, equip, part, group, T))
        out = {'sample': s, 'timeSeconds': self.times[s], 'groups': {}, 'targets': []}
        all_chips = [v for v in (self._at(row, s) for row in self.data['chip_temp_K']) if v is not None]
        for group in (NETWORK, CHIPS):
            # Chips scale over every chip (the Three.js rule), servers show their hottest.
            vals = all_chips if group == CHIPS else [t[5] for t in temps if t[4] == group]
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
            out['groups'][group] = {'domain': [lo, hi], 'source': source, 'count': len(vals)}
        for kind, model, equip, part, group, T in temps:
            dom = out['groups'].get(group, {}).get('domain')
            rgb = temp_to_rgb8(T, dom[0], dom[1]) if dom else (128, 128, 128)
            out['targets'].append({'kind': kind, 'modelUid': model, 'equipmentUid': equip,
                                   'liquidPart': part, 'group': group, 'T': T, 'rgb': list(rgb)})
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
