"""Independent conservation audit of a solved DAE .liquidrun (numpy only).

Fixed from the 2026-10-21 study scripts (_tmp/understand_liquid_correct/
verify_steady.py, verify_transient.py). Nothing here calls EquationGraph's
residual, diagnostics or compiled parameters: every law is re-derived from the
RAW manifest (after parameter-pack resolution, a data step) and applied to the
solved fields that DAESimulation reports.

Laws re-implemented (the declared product physics, 2026-10-21):
  pipe   Darcy, laminar/Swamee-Jain blend at Re=2300 (log argument capped at
         0.5), K = bends*K_per_bend + K_additional + 0.9 per tee end, + rho*g*dh(route)
  plate  dp_a*m|m| + dp_b*m + rho*g*dh(nodes); NTU outlet with the solid
  HX     dp_a*m|m| + dp_b*m + rho*g*dh(nodes); duty by hxModel
  valve  ISA Kv law + rho*g*dh(nodes); closed: m = 0
  pump   -rho*g*(N^2*H0 - a*Q^2) + rho*g*dh(nodes); flow-held: m = set
"""
from __future__ import annotations

import copy
import math

import numpy as np

from backend.liquidcooling.parameters import resolve_parameter_pack

TEE_K = .9


def _sample(value, index=0):
    return float(value[index] if isinstance(value, list) else value)


def counterflow_eps(ntu, cr):
    if abs(1 - cr) < 1e-12:
        return ntu / (1 + ntu)
    x = math.exp(-ntu * (1 - cr))
    return (1 - x) / (1 - cr * x)


class Manifest:
    """Resolved raw manifest arrays, in the solver's node/edge id order."""

    def __init__(self, raw, node_ids, edge_ids):
        res = resolve_parameter_pack(copy.deepcopy(raw))
        nodes = {n['id']: n for n in res['nodes']}
        edges = {e['id']: e for e in res['edges']}
        assert set(nodes) == set(node_ids) and set(edges) == set(edge_ids)
        self.nodes = [nodes[i] for i in node_ids]
        self.edges = [edges[i] for i in edge_ids]
        self.nid = {n: i for i, n in enumerate(node_ids)}
        fluid = res['fluid']
        self.rho, self.cp, self.mu = fluid['rho_kg_m3'], fluid['cp_J_kgK'], fluid['mu_Pa_s']
        g = np.asarray(res['gravity'], float)
        self.g = float(np.linalg.norm(g))
        self.g_unit = g / self.g if self.g > 0 else g
        self.fr = np.array([self.nid[e['from']] for e in self.edges])
        self.to = np.array([self.nid[e['to']] for e in self.edges])
        self.type = np.array([e['type'] for e in self.edges])
        pos = np.array([n['position_m'] for n in self.nodes], float)
        self.height = -(pos @ self.g_unit)
        self.is_bc = np.array([n['type'] == 'pressure_bc' for n in self.nodes])
        degree = np.zeros(len(self.nodes), int)
        np.add.at(degree, self.fr, 1)
        np.add.at(degree, self.to, 1)
        self.degree = degree
        self.primary = {c['edge_id']: c for c in res.get('primary_circuits', [])}
        self.dh = np.zeros(len(self.edges))
        self.pipe = {}
        # Tee legs per pipe: a pipe touching a tee node is one leg; a pipe
        # reached from a tee through a chain of valves across degree-2 nodes
        # is that branch's leg (tee law v2).
        legs = np.zeros(len(self.edges), int)
        incident = [[] for _ in self.nodes]
        for j in range(len(self.edges)):
            incident[self.fr[j]].append(j)
            incident[self.to[j]].append(j)
        for tee in np.flatnonzero(degree >= 3):
            for leg in incident[tee]:
                edge, node = leg, tee
                while self.type[edge] == 'valve':
                    nxt = self.to[edge] if self.fr[edge] == node else self.fr[edge]
                    if degree[nxt] != 2:
                        break
                    node = nxt
                    edge = next(e for e in incident[nxt] if e != edge)
                if self.type[edge] == 'pipe':
                    legs[edge] += 1
        self._legs = legs
        for j, e in enumerate(self.edges):
            if e['type'] == 'pipe':
                p = e['params']
                w = np.asarray(p['waypoints_m'], float)
                seg = w[1:] - w[:-1]
                bends = 0
                for a, b in zip(seg[:-1], seg[1:]):
                    na, nb = np.linalg.norm(a), np.linalg.norm(b)
                    if na > 1e-12 and nb > 1e-12 and np.clip(a @ b / (na * nb), -1, 1) < .95:
                        bends += 1
                K = (bends * float(p.get('K_minor_per_bend', .5)) + float(p.get('K_minor_additional', 0) or 0)
                     + TEE_K * self._tee_legs(j))
                D = float(p['D_m'])
                self.pipe[j] = {'D': D, 'L': float(np.linalg.norm(seg, axis=1).sum()), 'K': K,
                                'rough': float(p.get('roughness_m', 1.5e-6)), 'n': int(p['n_cells'])}
                self.dh[j] = float(-(seg @ self.g_unit).sum())
            else:
                self.dh[j] = self.height[self.to[j]] - self.height[self.fr[j]]

    def _tee_legs(self, j):
        return int(self._legs[j])

    # ------------------------------------------------------------- laws ---
    def pipe_loss(self, j, m):
        p = self.pipe[j]
        A = math.pi * p['D'] ** 2 / 4
        m_abs = math.sqrt(m * m + 1e-16)
        Re = m_abs * p['D'] / (A * self.mu)
        f_lam = 64 / (Re + 1e-8)
        arg = min(p['rough'] / p['D'] / 3.7 + 5.74 / (Re + 1e-8) ** .9, .5)
        f_turb = .25 / (math.log10(arg) + 1e-10) ** 2
        s = 1 / (1 + math.exp(-(Re - 2300) / 200))
        f = (1 - s) * f_lam + s * f_turb
        return (f * p['L'] / p['D'] + p['K']) / (2 * self.rho * A * A) * m * m_abs

    def law(self, j, m, sample=0, frame=None):
        """(kind, value): 'dp' = modelled P_from - P_to, or 'flow' = required m.

        Actuators (pump speed, valve opening) come from the frame's own
        accepted controls when given: online commands override the manifest.
        """
        e, t = self.edges[j], self.type[j]
        p = e['params']
        frame = frame or {}
        hydro = self.rho * self.g * self.dh[j]
        m_abs = math.sqrt(m * m + 1e-16)
        if t == 'pipe':
            return 'dp', self.pipe_loss(j, m) + hydro
        if t in ('cold_plate', 'heat_exchanger'):
            a = float(p.get('dp_a', 1e8 if t == 'cold_plate' else 5e6))
            b = float(p.get('dp_b', 1e5 if t == 'cold_plate' else 1e4))
            return 'dp', a * m * m_abs + b * m + hydro
        if t == 'valve':
            op = frame.get('valve_openings', {}).get(e['id'], _sample(p.get('opening', 1.), sample))
            if op <= 0:
                return 'flow', 0.
            kv_max = float(p.get('Kv_max', 2.5))
            kv = {'linear': kv_max * op, 'equal_percentage': kv_max * 50 ** (op - 1),
                  'quick_opening': kv_max * math.sqrt(op)}[p.get('characteristic', 'linear')]
            R = 1e5 * (self.rho / 1000) * (3600 / (self.rho * kv)) ** 2
            return 'dp', R * m * m_abs + hydro
        if t == 'pump':
            if (p.get('mode') or 'curve') == 'flow':
                return 'flow', _sample(p['flow_set_kg_s'], sample)
            N = frame.get('pump_rpm_ratios', {}).get(e['id'], _sample(p.get('rpm_ratio', 1.), sample))
            q = m / self.rho
            return 'dp', -self.rho * self.g * (N * N * float(p['H0_m']) - float(p['a_coef']) * q * q) + hydro
        raise AssertionError(t)


def elevation_loop_closure(man):
    n_e, n_n = len(man.edges), len(man.nodes)
    B = np.zeros((n_e, n_n))
    B[np.arange(n_e), man.to] = 1
    B[np.arange(n_e), man.fr] = -1
    h, *_ = np.linalg.lstsq(B, man.dh, rcond=None)
    return float(np.abs(B @ h - man.dh).max())


def steady_audit(raw, sim, frame):
    """Return a dict of measured violations of every law on a steady frame."""
    man = Manifest(raw, sim.metadata['node_ids'], sim.metadata['edge_ids'])
    P = np.asarray(frame['node_pressure_Pa'], float)
    m = np.asarray(frame['edge_flow_kg_s'], float)
    Tn = np.asarray(frame['node_temperature_K'], float)
    Tc = np.asarray(frame['cell_temperature_K'], float)
    Ts = np.asarray(frame['chip_temp_K'], float)
    Tout_solver = np.asarray(frame['edge_outlet_temp_K'], float)
    multiplier = frame.get('heat_multiplier', 1.)
    out = {}
    net = np.zeros(len(man.nodes))
    np.add.at(net, man.fr, m)
    np.add.at(net, man.to, -m)
    out['mass_kg_s'] = float(np.abs(net[~man.is_bc]).max())
    momentum, against = [], []
    for j in range(len(man.edges)):
        kind, value = man.law(j, m[j], frame.get('seq', 0), frame)
        drop = P[man.fr[j]] - P[man.to[j]]
        if kind == 'flow':
            momentum.append(abs(m[j] - value))
            continue
        momentum.append(abs(drop - value) / (1. + 1e-3 * max(abs(drop), abs(value))))
        if man.type[j] != 'pump' and abs(m[j]) > 1e-9:
            drive = drop - man.rho * man.g * man.dh[j]
            if np.sign(drive) != np.sign(m[j]):
                against.append(man.edges[j]['id'])
    out['momentum_scaled'] = float(max(momentum))
    out['flow_against_drive'] = against
    pumps = [j for j in range(len(man.edges)) if man.type[j] == 'pump'
             and (man.edges[j]['params'].get('mode') or 'curve') == 'curve']
    out['pump_reverse_or_no_rise'] = [man.edges[j]['id'] for j in pumps
                                      if not (m[j] > 0 and P[man.to[j]] - P[man.fr[j]] + man.rho * man.g * man.dh[j] > 0)]
    # ---- thermal ---------------------------------------------------------
    up = np.where(m >= 0, man.fr, man.to)
    down = np.where(m >= 0, man.to, man.fr)
    stagnant = np.asarray(frame.get('edge_stagnant', [False] * len(m)), bool)
    active = ~stagnant
    Tout = Tn[up].copy()
    offset, pipe_dev = 0, 0.
    for j in range(len(man.edges)):
        if man.type[j] != 'pipe':
            continue
        n = man.pipe[j]['n']
        cells = Tc[offset:offset + n]
        Tout[j] = cells[-1] if m[j] >= 0 else cells[0]
        source = man.edges[j]['params'].get('heat_source_W_per_m', 0) or 0
        if active[j] and abs(m[j]) > 1e-9 and not source:
            pipe_dev = max(pipe_dev, float(np.abs(cells - Tn[up[j]]).max()))
        offset += n
    assert offset == Tc.size
    out['pipe_adiabatic_K'] = pipe_dev
    cps = [j for j in range(len(man.edges)) if man.type[j] == 'cold_plate']
    plate_err, q_total, second_law = 0., 0., []
    for k, j in enumerate(cps):
        p = man.edges[j]['params']
        C = abs(m[j]) * man.cp if active[j] else 0.
        eps = 1 - math.exp(-float(p.get('UA_W_per_K', 150.)) / C) if C > 0 else 0.
        Tout[j] = Tn[up[j]] + eps * (Ts[k] - Tn[up[j]])
        q = (C * _sample(p['dT_set_K']) * multiplier if p.get('heat_mode') == 'temperature_rise'
             else _sample(p.get('Q_W', 350.)) * multiplier)
        q_total += q
        if C > 0:
            plate_err = max(plate_err, abs(C * (Tout[j] - Tn[up[j]]) - q))
            if q > 0 and not (Ts[k] > Tout[j] >= Tn[up[j]] - 1e-9):
                second_law.append(man.edges[j]['id'])
    out['plate_heat_W'] = plate_err
    hx = [j for j in range(len(man.edges)) if man.type[j] == 'heat_exchanger']
    q_sec, q_pri, hx_model_err = 0., 0., 0.
    for k, j in enumerate(hx):
        p = man.edges[j]['params']
        circuit = man.primary[man.edges[j]['id']]
        t_in, t_out = Tn[up[j]], frame['secondary_supply_K'][k]
        index = frame.get('seq', 0)
        t_pi = frame.get('primary_inlet_K', {}).get(man.edges[j]['id'], _sample(circuit['inlet_K'], index))
        t_pr = frame['primary_return_K'][k]
        c_s, c_p = abs(m[j]) * man.cp, _sample(circuit['flow_kg_s'], index) * circuit['cp_J_kgK']
        Tout[j] = t_out
        qs, qp = c_s * (t_in - t_out), c_p * (t_pr - t_pi)
        q_sec += qs
        q_pri += qp
        if (p.get('mode') or 'physics') == 'setpoint':
            hx_model_err = max(hx_model_err, abs(t_out - _sample(p['T_set_K'])) * c_s)
        elif (p.get('hxModel') or 'mixed-volumes') == 'counterflow-ntu':
            c_min, c_max = min(c_s, c_p), max(c_s, c_p)
            duty = counterflow_eps(float(p.get('UA_W_per_K', 5000.)) / c_min, c_min / c_max) * c_min * (t_in - t_pi) if c_min > 0 else 0.
            hx_model_err = max(hx_model_err, abs(qs - duty))
            if qs > 0 and not (t_out >= t_pi - 1e-9 and t_in >= t_pr - 1e-9):
                second_law.append(man.edges[j]['id'])
        else:
            hx_model_err = max(hx_model_err, abs(qs - float(p.get('UA_W_per_K', 5000.)) * (t_out - t_pr)))
            if qs > 0 and not (t_in >= t_out >= t_pr >= t_pi - 1e-9):
                second_law.append(man.edges[j]['id'])
    out['hx_model_W'] = hx_model_err
    out['second_law_violations'] = second_law
    inflow = np.zeros(len(man.nodes))
    enthalpy = np.zeros(len(man.nodes))
    w = np.where(active, np.abs(m), 0.)
    np.add.at(inflow, down, w)
    np.add.at(enthalpy, down, w * Tout)
    mixing = np.where(inflow > 0, (inflow * Tn - enthalpy) * man.cp, 0.)
    out['node_mixing_W'] = float(np.abs(mixing).max())
    out['edge_outlet_vs_solver_K'] = float(np.abs(Tout - Tout_solver)[active].max())
    out['heat_total_W'] = q_total
    out['energy_plates_vs_hx_secondary_W'] = abs(q_total - q_sec)
    out['energy_hx_secondary_vs_primary_W'] = abs(q_sec - q_pri)
    out['elevation_closure_m'] = elevation_loop_closure(man)
    return out


def transient_energy_audit(raw, sim, frames):
    """Backward-Euler discrete energy balance per accepted step [W]."""
    man = Manifest(raw, sim.metadata['node_ids'], sim.metadata['edge_ids'])
    volumes = []
    for j in range(len(man.edges)):
        if man.type[j] == 'pipe':
            p = man.pipe[j]
            volumes += [math.pi * p['D'] ** 2 / 4 * p['L'] / p['n']] * p['n']
    volumes = np.asarray(volumes)
    cps = [j for j in range(len(man.edges)) if man.type[j] == 'cold_plate']
    hx = [j for j in range(len(man.edges)) if man.type[j] == 'heat_exchanger']
    c_solid = np.asarray([float(man.edges[j]['params'].get('C_solid_J_per_K', 50.)) for j in cps])
    circuits = [man.primary[man.edges[j]['id']] for j in hx]
    c_sec = np.asarray([man.rho * man.cp * c['secondary_volume_m3'] for c in circuits])
    c_pri = np.asarray([c['rho_kg_m3'] * c['cp_J_kgK'] * c['primary_volume_m3'] for c in circuits])

    def energy(f):
        return float((man.rho * man.cp * volumes * np.asarray(f['cell_temperature_K'])).sum()
                     + (c_solid * np.asarray(f['chip_temp_K'])).sum()
                     + (c_sec * np.asarray(f['secondary_supply_K'])).sum()
                     + (c_pri * np.asarray(f['primary_return_K'])).sum())
    rows = []
    for n in range(1, len(frames)):
        f0, f1 = frames[n - 1], frames[n]
        dt = f1['time_s'] - f0['time_s']
        index = f1['seq']
        m = np.asarray(f1['edge_flow_kg_s'])
        q = 0.
        for j in cps:
            p = man.edges[j]['params']
            q += (abs(m[j]) * man.cp * _sample(p['dT_set_K'], index) if p.get('heat_mode') == 'temperature_rise'
                  else _sample(p.get('Q_W', 350.), index)) * f1['heat_multiplier']
        rejection = sum(_sample(c['flow_kg_s'], index) * c['cp_J_kgK']
                        * (f1['primary_return_K'][k] - f1['primary_inlet_K'][man.edges[j]['id']])
                        for k, (j, c) in enumerate(zip(hx, circuits)))
        storage = (energy(f1) - energy(f0)) / dt
        rows.append({'t': f1['time_s'], 'heat_W': q, 'rejection_W': rejection, 'storage_W': storage,
                     'balance_W': q - rejection - storage})
    return rows
