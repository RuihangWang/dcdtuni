"""Liquid-cooling flow quantities and directions at one result sample.

The flow counterpart of liquid_colour.py: the same rules as the Three.js
side (src/frontend/react/src/liquid/liquidFlow.ts), pinned to the same
numbers by the shared fixture src/frontend/shared/testdata/
liquid_flow_expected.json. Pure module (no omni/pxr).

Signs. ``edge_flow_kg_s`` is positive from ``edge_from`` to ``edge_to``.
Everything shown follows the ACTUAL flow: an edge's upstream node is
``from`` when the flow is >= 0 (the solver's own convention) and ``to``
otherwise, and ``reversed`` marks flow against the modelled direction.
Stagnant edges (``edge_stagnant``) carry no flow, no arrow and no
upstream; the solver keeps a ~1e-31 residue on them, which is not shown.

Cells. A pipe edge's cells run from ``from`` to ``to`` along its
waypoints (graphcompiling/compiler.py). A cell's arrow is the unit
difference of its neighbouring cells (one-sided at the ends; from-node to
to-node for a one-cell edge) times sign(flow).

Part flow scalars (|flow| in kg/s), joined exactly like the temperature
rules of liquid_colour.py:
  pipe            cell-weighted mean over the binding's edges
                  (edges without cells: mean over those edges)
  supply/return   SUM over the rack's ``*_branch_in`` / ``*_branch_out``
  manifold        edges: parallel branches add up to the manifold flow
  tee             throughput of node ``<pipe>_tee_<id>`` (what flows in)
  flow valve      binding ``<pipe>/<valve id>`` edges (wins over the tee)
  CDU             throughput of node ``<cdu>_supply``
  pump / hx       each pump / heat-exchanger edge of a CDU binding
  server          largest cold-plate flow, joined by cp edge id
Volume flow is |flow| / rho in L/min; velocity is |flow| / (rho * pi D^2/4)
for pipes and valves (edges with ``edge_D_m``), else None.

A ``pipe_segment`` binding that lists no edges owns the edge named after its
uid: a pipe without tees compiles to ONE edge whose id is the pipe uid, and
results compiled before 2026-10-22 left that binding empty (the compiler
matched ``<uid>_`` prefixes only).

``flow_arrows`` lays the flow arrows out on the same data (see its docstring);
both renderers draw them, the Three.js viewport and Kit.
"""
from __future__ import annotations

import math

NETWORK = 'network'
SERVERS = 'servers'


def _finite(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _at(row, s):
    value = row[s] if isinstance(row, list) and s < len(row) else (None if isinstance(row, list) else row)
    return float(value) if _finite(value) else None


def _mean(values):
    total = 0.0
    for v in values:
        total += v
    return total / len(values)


def _sum(values):
    total = 0.0
    for v in values:
        total += v
    return total


def _range(values):
    return [min(values), max(values)] if values else None


def _lpm(mass, rho):
    return mass / rho * 60000


def _velocity(mass, rho, diameter):
    if diameter is None:
        return None
    return mass / (rho * (math.pi * diameter * diameter / 4))


def _list(data, key):
    value = data.get(key)
    return value if isinstance(value, list) else None


def binding_edges(key, binding, known_edges) -> list:
    """The edges a binding owns; see the module docstring for the pipe rule."""
    edges = [str(e) for e in (binding.get('edge_ids') or [])]
    if not edges and binding.get('kind') == 'pipe_segment' and str(key) in known_edges:
        edges = [str(key)]
    return edges


def flow_frame(data: dict, sample: int = -1) -> dict:
    """Flow of every edge, cell and liquid part of ``data`` at ``sample``."""
    times = _list(data, 'time_s') or []
    if not times:
        raise ValueError('liquid result has no time samples')
    flows = _list(data, 'edge_flow_kg_s')
    edge_ids = _list(data, 'edge_ids')
    if flows is None or edge_ids is None:
        raise ValueError('liquid result lacks edge_flow_kg_s/edge_ids')
    fluid = data.get('fluid') if isinstance(data.get('fluid'), dict) else {}
    rho = fluid.get('rho_kg_m3')
    if not _finite(rho) or rho <= 0:
        raise ValueError('liquid result lacks a positive fluid rho_kg_m3')
    rho = float(rho)
    n = len(times)
    s = n - 1 if sample < 0 else int(sample)
    if not 0 <= s < n:
        raise ValueError(f'sample {sample} outside 0..{n - 1}')

    sources = _list(data, 'edge_from')
    sinks = _list(data, 'edge_to')
    types = _list(data, 'edge_types')
    diameters = _list(data, 'edge_D_m')
    stagnant_flags = _list(data, 'edge_stagnant')
    node_ids = [str(v) for v in (_list(data, 'node_ids') or [])]
    node_pos = dict(zip(node_ids, _list(data, 'node_positions_m') or []))
    topology = (sources is not None and sinks is not None
                and len(sources) == len(edge_ids) == len(sinks))

    edges = []
    by_id = {}
    for i, raw_id in enumerate(edge_ids):
        m = _at(flows[i], s) if i < len(flows) else None
        if m is None:
            continue
        edge_id = str(raw_id)
        stagnant = bool(stagnant_flags[i]) if stagnant_flags is not None and i < len(stagnant_flags) else False
        diameter = diameters[i] if diameters is not None and i < len(diameters) else None
        diameter = float(diameter) if _finite(diameter) and diameter > 0 else None
        mass = 0.0 if stagnant else abs(m)
        src = str(sources[i]) if topology else None
        dst = str(sinks[i]) if topology else None
        if stagnant or not topology:
            upstream = downstream = None
        else:
            upstream, downstream = (src, dst) if m >= 0 else (dst, src)
        entry = {
            'edgeId': edge_id,
            'type': str(types[i]) if types is not None and i < len(types) else None,
            'from': src, 'to': dst, 'flowKgS': m, 'massFlowKgS': mass,
            'volumeFlowLpm': _lpm(mass, rho), 'velocityMS': _velocity(mass, rho, diameter),
            'diameterM': diameter, 'upstream': upstream, 'downstream': downstream,
            'reversed': (not stagnant) and m < 0, 'stagnant': stagnant,
        }
        edges.append(entry)
        by_id[edge_id] = entry

    cell_edges = [str(e) for e in (_list(data, 'cell_edge_ids') or [])]
    positions = _list(data, 'cell_positions') or []
    cells_by_edge = {}
    rank = []  # a cell's position among its edge's cells (from -> to)
    for c, e in enumerate(cell_edges):
        own = cells_by_edge.setdefault(e, [])
        rank.append(len(own))
        own.append(c)
    cells = []
    for c, e in enumerate(cell_edges):
        own = cells_by_edge[e]
        k = rank[c]
        p = positions[c] if c < len(positions) else None
        info = by_id.get(e)
        if len(own) >= 2:
            a = positions[own[k - 1]] if k > 0 else p
            b = positions[own[k + 1]] if k < len(own) - 1 else p
        elif info is not None and info['from'] in node_pos and info['to'] in node_pos:
            a, b = node_pos[info['from']], node_pos[info['to']]
        else:
            a = b = None
        direction = [0.0, 0.0, 0.0]
        if info is not None and not info['stagnant'] and a is not None and b is not None:
            m = info['flowKgS']
            sign = 1.0 if m > 0 else (-1.0 if m < 0 else 0.0)
            tx, ty, tz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
            norm = math.sqrt(tx * tx + ty * ty + tz * tz)
            if norm > 0 and sign != 0:
                direction = [tx / norm * sign, ty / norm * sign, tz / norm * sign]
        mass = info['massFlowKgS'] if info is not None else 0.0
        cells.append({
            'edgeId': e, 'position': list(p) if isinstance(p, list) else None,
            'direction': direction, 'massFlowKgS': mass, 'volumeFlowLpm': _lpm(mass, rho),
            'velocityMS': info['velocityMS'] if info is not None else None,
            'stagnant': bool(info['stagnant']) if info is not None else False,
        })

    def edge_stats(edge_list):
        """Cell-weighted mean |flow| and velocity; edges without cells: edge mean."""
        masses, speeds = [], []
        for e in edge_list:
            info = by_id.get(str(e))
            if info is None:
                continue
            for _ in cells_by_edge.get(str(e), ()):
                masses.append(info['massFlowKgS'])
                if info['velocityMS'] is not None:
                    speeds.append(info['velocityMS'])
        if not masses:
            for e in edge_list:
                info = by_id.get(str(e))
                if info is not None:
                    masses.append(info['massFlowKgS'])
                    if info['velocityMS'] is not None:
                        speeds.append(info['velocityMS'])
        if not masses:
            return None, None
        return _mean(masses), (_mean(speeds) if speeds else None)

    def throughput(node):
        if not topology or node not in node_ids:
            return None
        total = 0.0
        for info in edges:
            if info['stagnant']:
                continue
            m = info['flowKgS']
            if (info['to'] == node and m > 0) or (info['from'] == node and m < 0):
                total += abs(m)
        return total

    targets = []

    def add(kind, model, equip, part, group, mass, velocity=None):
        if mass is not None:
            targets.append({'kind': kind, 'modelUid': model, 'equipmentUid': equip, 'liquidPart': part,
                            'group': group, 'massFlowKgS': mass, 'volumeFlowLpm': _lpm(mass, rho),
                            'velocityMS': velocity})

    bindings = data.get('object_bindings') if isinstance(data.get('object_bindings'), dict) else {}
    fittings = {}
    known_edges = set(str(e) for e in edge_ids)
    for key, binding in bindings.items():
        kind = binding.get('kind')
        edge_list = binding_edges(key, binding, known_edges)
        if kind == 'pipe_segment':
            add('pipe', key, '', '', NETWORK, *edge_stats(edge_list))
            prefix = f'{key}_tee_'
            for node in node_ids:
                if node.startswith(prefix):
                    fittings.setdefault((key, node[len(prefix):]), ('node', node))
        elif kind == 'valve' and '/' in key:
            pipe, vid = key.split('/', 1)
            fittings[(pipe, vid)] = ('edges', edge_list)
        elif kind == 'rack':
            for suffix, part in (('_branch_in', 'supply-manifold'), ('_branch_out', 'return-manifold')):
                branch = [by_id[e]['massFlowKgS'] for e in edge_list if e.endswith(suffix) and e in by_id]
                add('manifold', key, '', part, NETWORK, _sum(branch) if branch else None)
        elif kind == 'cdu':
            add('cdu', key, '', '', NETWORK, throughput(f'{key}_supply'))
            for e in edge_list:
                info = by_id.get(e)
                if info is not None and info['type'] in ('pump', 'heat_exchanger'):
                    add('pump' if info['type'] == 'pump' else 'hx', key, e, '', NETWORK,
                        info['massFlowKgS'], info['velocityMS'])
    for (pipe, vid), (how, ref) in sorted(fittings.items()):
        if how == 'edges':
            add('valve', pipe, vid, '', NETWORK, *edge_stats(ref))
        else:
            add('tee', pipe, vid, '', NETWORK, throughput(ref))
    for rack in data.get('rack_summaries') or []:
        for server in rack.get('servers') or []:
            plates = [by_id[str(e)]['massFlowKgS'] for e in (server.get('cp_edge_ids') or []) if str(e) in by_id]
            add('server', str(rack.get('rack_id')), str(server.get('server_id')), '', SERVERS,
                max(plates) if plates else None)

    domains = {}
    for group in (NETWORK, SERVERS):
        found = _range([t['massFlowKgS'] for t in targets if t['group'] == group])
        if found:
            domains[group] = found
    moving = [c for c in cells if not c['stagnant'] and c['edgeId'] in by_id]
    found = _range([c['massFlowKgS'] for c in moving])
    if found:
        domains['cells'] = found
    found = _range([c['velocityMS'] for c in moving if c['velocityMS'] is not None])
    if found:
        domains['velocity'] = found

    observable = _list(data, 'node_observable') or []
    unobservable = [node_ids[i] for i, row in enumerate(observable)
                    if i < len(node_ids) and isinstance(row, list) and s < len(row) and row[s] is False]
    return {
        'sample': s, 'timeSeconds': float(times[s]), 'rhoKgM3': rho, 'domains': domains,
        'cells': cells, 'edges': edges, 'targets': targets,
        'stagnantEdges': [e['edgeId'] for e in edges if e['stagnant']],
        'unobservableNodes': unobservable,
    }


def global_domains(data: dict) -> dict:
    """Min..max of each flow scale over EVERY sample: a fixed domain, so an
    animation shows the change over time instead of re-normalising."""
    out = {}
    for s in range(len(_list(data, 'time_s') or [])):
        for group, (lo, hi) in flow_frame(data, s)['domains'].items():
            current = out.get(group)
            out[group] = [lo, hi] if current is None else [min(current[0], lo), max(current[1], hi)]
    return out


def node_throughput(frame: dict, node: str):
    """|flow| entering ``node`` along the actual flow (what passes through it),
    from a flow_frame; None without topology or for an unknown node."""
    edges = frame['edges']
    if not edges or edges[0]['from'] is None:
        return None
    total, seen = 0.0, False
    for info in edges:
        if info['from'] == node or info['to'] == node:
            seen = True
        if info['stagnant']:
            continue
        m = info['flowKgS']
        if (info['to'] == node and m > 0) or (info['from'] == node and m < 0):
            total += abs(m)
    return total if seen else None


BRANCH_SUFFIXES = (('_branch_in', 'supply'), ('_branch_out', 'return'))


def _header_side(edge_id: str):
    if edge_id.endswith('_branch_in') or edge_id.endswith('_branch_out'):
        return None
    if '_mf_supply' in edge_id:
        return 'supply'
    if '_mf_return' in edge_id:
        return 'return'
    return None


def flow_arrows(data: dict, sample: int = -1, frame=None) -> dict:
    """Where the flow arrows go at ``sample``, and what each one carries.

    Every moving pipe cell gets an arrow at its position along its cell
    direction (flow_frame), carrying its edge's |flow| and velocity, except:

    * Server branches (``*_branch_in`` / ``*_branch_out`` of a rack). The
      compiled graph routes every branch from the top of the manifold down its
      axis to the server's height, so their cells lie ON the header, pointing
      against it and on top of each other. They are listed in ``branches``
      instead (rack, server, side, signed flow, and ``tap``: the server's
      node on the manifold axis) for the renderer to draw on the stub it
      shows between manifold and server.
    * Manifold headers (rack edges named ``*_mf_supply*`` / ``*_mf_return*``)
      are drawn as the distributed manifold they represent: a header cell
      carries what is still to be delivered (supply: the branch flows of the
      taps downstream of it along the flow) or already collected (return: the
      taps upstream of it). A header cell with nothing left to carry (above
      the top server of a bottom-fed manifold) gets no arrow.

    ``domains`` gives the mass-flow and velocity ranges over arrows and
    branches (sizes and colours on a common scale).
    """
    fr = frame if frame is not None else flow_frame(data, sample)
    s = fr['sample']
    rho = fr['rhoKgM3']
    by_id = {e['edgeId']: e for e in fr['edges']}
    node_ids = [str(v) for v in (_list(data, 'node_ids') or [])]
    node_pos = dict(zip(node_ids, _list(data, 'node_positions_m') or []))
    bindings = data.get('object_bindings') if isinstance(data.get('object_bindings'), dict) else {}

    branch_edges = set()
    for binding in bindings.values():
        if binding.get('kind') in ('rack', 'server'):
            for e in binding.get('edge_ids') or []:
                if any(str(e).endswith(suffix) for suffix, _side in BRANCH_SUFFIXES):
                    branch_edges.add(str(e))

    branches = []
    for key, binding in bindings.items():
        if binding.get('kind') != 'server' or not binding.get('parent_uid'):
            continue
        rack = str(binding['parent_uid'])
        server = key.split('/', 1)[1] if '/' in key else key
        for e in binding.get('edge_ids') or []:
            e = str(e)
            side = next((sd for suffix, sd in BRANCH_SUFFIXES if e.endswith(suffix)), None)
            info = by_id.get(e)
            if side is None or info is None:
                continue
            node = info['to'] if side == 'supply' else info['from']
            tap = node_pos.get(node) if node is not None else None
            branches.append({
                'rackUid': rack, 'serverUid': server, 'side': side, 'edgeId': e,
                'flowKgS': 0.0 if info['stagnant'] else info['flowKgS'],
                'massFlowKgS': info['massFlowKgS'], 'volumeFlowLpm': info['volumeFlowLpm'],
                'velocityMS': info['velocityMS'], 'reversed': info['reversed'], 'stagnant': info['stagnant'],
                'tap': list(tap) if isinstance(tap, list) else None,
            })
    taps = {}
    for b in branches:
        if b['tap'] is not None:
            taps.setdefault((b['rackUid'], b['side']), []).append(b)
    header_rack = {}
    for key, binding in bindings.items():
        if binding.get('kind') == 'rack':
            for e in binding.get('edge_ids') or []:
                header_rack[str(e)] = str(key)

    arrows = []
    for c, cell in enumerate(fr['cells']):
        e = cell['edgeId']
        if e in branch_edges or cell['stagnant'] or e not in by_id:
            continue
        d = cell['direction']
        p = cell['position']
        if p is None or (d[0] == 0 and d[1] == 0 and d[2] == 0):
            continue
        side = _header_side(e) if e in header_rack else None
        own_taps = taps.get((header_rack.get(e), side)) if side is not None else None
        if own_taps:
            total = 0.0
            for b in own_taps:
                t = b['tap']
                along = (t[0] - p[0]) * d[0] + (t[1] - p[1]) * d[1] + (t[2] - p[2]) * d[2]
                if (side == 'supply' and along > 0) or (side == 'return' and along < 0):
                    total += b['flowKgS']
            if total == 0:
                continue
            mass = abs(total)
            direction = list(d) if total > 0 else [-d[0], -d[1], -d[2]]
            arrows.append({
                'edgeId': e, 'cell': c, 'kind': 'header', 'position': list(p), 'direction': direction,
                'massFlowKgS': mass, 'volumeFlowLpm': _lpm(mass, rho),
                'velocityMS': _velocity(mass, rho, by_id[e]['diameterM']),
            })
        else:
            arrows.append({
                'edgeId': e, 'cell': c, 'kind': 'cell', 'position': list(p), 'direction': list(d),
                'massFlowKgS': cell['massFlowKgS'], 'volumeFlowLpm': cell['volumeFlowLpm'],
                'velocityMS': cell['velocityMS'],
            })

    domains = {}
    moving = [b for b in branches if not b['stagnant']]
    found = _range([a['massFlowKgS'] for a in arrows] + [b['massFlowKgS'] for b in moving])
    if found:
        domains['mass'] = found
    found = _range([a['velocityMS'] for a in arrows if a['velocityMS'] is not None]
                   + [b['velocityMS'] for b in moving if b['velocityMS'] is not None])
    if found:
        domains['velocity'] = found
    return {'sample': s, 'timeSeconds': fr['timeSeconds'], 'arrows': arrows, 'branches': branches,
            'domains': domains}


def arrow_global_domains(data: dict) -> dict:
    """flow_arrows domains over EVERY sample (arrow sizes do not re-normalise)."""
    out = {}
    for s in range(len(_list(data, 'time_s') or [])):
        for key, (lo, hi) in flow_arrows(data, s)['domains'].items():
            current = out.get(key)
            out[key] = [lo, hi] if current is None else [min(current[0], lo), max(current[1], hi)]
    return out


if __name__ == '__main__':
    import json
    import sys
    if len(sys.argv) != 3:
        sys.exit('usage: liquid_flow.py FIXTURE.json EXPECTED.json')
    with open(sys.argv[1]) as f:
        fixture = json.load(f)
    samples = list(range(len(fixture['time_s'])))
    expected = {
        'note': 'cross-implementation contract for liquid flow (liquid_flow.py <-> liquidFlow.ts); '
                'regenerate only on a deliberate rule change',
        'samples': samples,
        'frames': {str(s): flow_frame(fixture, s) for s in samples},
        'globalDomains': global_domains(fixture),
    }
    with open(sys.argv[2], 'w') as f:
        json.dump(expected, f)
        f.write('\n')
