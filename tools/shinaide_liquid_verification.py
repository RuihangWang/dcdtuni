"""Independent full-topology thermal MMS, physical closed form and DAE AD/FD.

Use a React-exported Shinaide .liquidrun v4. All verification copies and outputs
stay inside this repository. The manufactured pipe sources are verification
forcing, not a claim that the original physical case contains distributed heat.
The reference equations are assembled from balances without calling production
residuals; production JVP is used only for a one-way audit afterwards.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import time

import numpy as np
from scipy.linalg import expm
import torch

from backend.liquidcooling.liquidrun_dae_schema import load_dae_input
from backend.liquidcooling.dae_experiment import DAEExperiment

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def at(value, index=0):
    return value[index] if isinstance(value, list) else value


def scalar_copy(raw, duration=120., dt=10.):
    """Hold the input's initial values for a verification copy, preserve graph."""
    out = copy.deepcopy(raw)
    out['time'].update(dt_s=dt, N_t=round(duration / dt) + 1)
    out['events'] = []
    out['analysis'] = {'mode': 'transient', 'initialization': 'steady'}
    out['execution'].update(device='cpu', dtype='float64', max_threads=2)
    if out['schemaVersion'] == 4:
        out['transient'].update(durationS=duration, start={'kind': 'steadyConverged'})
    for edge in out['edges']:
        for key in ('Q_W', 'rpm_ratio', 'opening', 'flow_set_kg_s', 'dT_set_K', 'T_set_K'):
            if key in edge['params']:
                edge['params'][key] = at(edge['params'][key])
        edge['params'].pop('heat_source_W_per_m', None)
    for node in out['nodes']:
        if node.get('bc'):
            node['bc']['P_Pa'] = at(node['bc']['P_Pa'])
    for circuit in out['primary_circuits']:
        for key in ('inlet_K', 'flow_kg_s'):
            circuit[key] = at(circuit[key])
    return out


def run(raw):
    definition = load_dae_input(raw)
    experiment = DAEExperiment(definition)
    states = [experiment.tensor_state.detach().cpu().numpy()]
    max_res = max_balance = max_balance_relative = 0.
    while not experiment.done:
        frame = experiment.step()
        max_res = max(max_res, frame['newton']['scaled_residual_inf'])
        max_balance = max(max_balance, abs(frame['diagnostics']['energy_balance_W']))
        max_balance_relative = max(max_balance_relative, abs(frame['diagnostics']['energy_balance_W']) / (1 + abs(frame['diagnostics']['heat_input_W'])))
        states.append(experiment.tensor_state.detach().cpu().numpy())
    return definition, experiment, np.asarray(states), max_res, max_balance, max_balance_relative


def assemble(definition, state):
    """Independent node elimination and water/solid/two-fluid heat balances.

    Read topology/property VALUES, not production residual coefficients. This
    implementation handles every pipe, every cold plate and every exchanger.
    """
    eq = definition.equation_graph
    layout = eq.state_layout
    theta = definition.parameters(0).detach().cpu().numpy()
    p = {name: theta[sl] for name, sl in eq.parameter_layout.items()}
    ids = {kind: eq._ids[kind].cpu().numpy() for kind in ('pipe', 'cp', 'hx', 'pump', 'valve')}
    if bool(eq._hx_setpoint.any()) or bool(eq._cp_rise.any()):
        raise ValueError('full thermal MMS currently verifies physical HXs and power cold plates')
    if bool(eq._bc_T_mask.any()):
        raise ValueError('uniform-shift MMS needs a closed loop without fixed temperature nodes')
    flow = state[layout['flow']]
    actual = np.where(eq._thermal_active.cpu().numpy(), flow, 0.)
    weight = np.abs(actual)
    fr, to = eq.graph.edge_from.cpu().numpy(), eq.graph.edge_to.cpu().numpy()
    up, down = np.where(actual >= 0, fr, to), np.where(actual >= 0, to, fr)
    nw, ns, nh = eq.graph.N_cells, eq.graph.N_cp, eq.graph.N_hx
    nx = nw + ns + 2 * nh
    sw, ss, sh, sp = slice(0, nw), slice(nw, nw + ns), slice(nw + ns, nw + ns + nh), slice(nw + ns + nh, nx)
    dynamic = np.concatenate([np.arange(layout[k].start, layout[k].stop) for k in
                              ('water_temperature', 'solid_temperature', 'hx_secondary_temperature', 'primary_return_temperature')])
    cp_local = {int(edge): i for i, edge in enumerate(ids['cp'])}
    hx_local = {int(edge): i for i, edge in enumerate(ids['hx'])}
    first, last, owner = {}, {}, np.empty(nw, dtype=int)
    spans = eq.graph.pipe_indices
    for edge, begin, end in zip(spans.edge_ids.tolist(), spans.cell_start.tolist(), spans.cell_end.tolist()):
        first[edge], last[edge] = begin, end - 1
        owner[begin:end] = edge
    cp = float(p['fluid_cp'][0])
    rho = float(p['fluid_rho'][0])
    epsilon = np.array([1 - np.exp(-p['cp_UA'][i] / (weight[e] * cp)) if weight[e] > 0 else 0.
                        for i, e in enumerate(ids['cp'])])
    nn = eq.graph.N_nodes
    N, R, q = np.zeros((nn, nn)), np.zeros((nn, nx)), np.zeros(nn)
    for node in range(nn):
        incoming = [e for e in range(len(flow)) if down[e] == node and weight[e] > 0]
        total = sum(weight[e] for e in incoming)
        if total == 0:
            N[node, node], q[node] = 1., p['node_gauge_temperature'][node]
            continue
        N[node, node] = total
        for edge in incoming:
            w = weight[edge]
            if edge in first:
                R[node, last[edge] if actual[edge] >= 0 else first[edge]] += w
            elif edge in cp_local:
                local = cp_local[edge]
                N[node, up[edge]] -= w * (1 - epsilon[local])
                R[node, ss.start + local] += w * epsilon[local]
            elif edge in hx_local:
                R[node, sh.start + hx_local[edge]] += w
            else:
                N[node, up[edge]] -= w
    S = np.linalg.solve(N, R)
    g = np.linalg.solve(N, q)
    cw = rho * cp * p['cell_volume']
    cs = p['cp_Csolid']
    ch = rho * cp * p['secondary_volume']
    cpri = p['primary_rho'] * p['primary_cp'] * p['primary_volume']
    C = np.concatenate([cw, cs, ch, cpri])
    A, b = np.zeros((nx, nx)), np.zeros(nx)
    for cell, edge in enumerate(owner):
        wc = weight[edge] * cp
        A[cell, cell] -= wc / cw[cell]
        if (actual[edge] >= 0 and cell == first[edge]) or (actual[edge] < 0 and cell == last[edge]):
            A[cell] += wc / cw[cell] * S[up[edge]]
            b[cell] += wc / cw[cell] * g[up[edge]]
        else:
            A[cell, cell - 1 if actual[edge] >= 0 else cell + 1] += wc / cw[cell]
        b[cell] += p['cell_heat_W'][cell] / cw[cell]
    for local, edge in enumerate(ids['cp']):
        index = ss.start + local
        G = weight[edge] * cp * epsilon[local]
        A[index, index] -= G / cs[local]
        A[index] += G / cs[local] * S[up[edge]]
        b[index] += G / cs[local] * g[up[edge]] + p['heat_W'][local] / cs[local]
    for local, edge in enumerate(ids['hx']):
        isec, ipri = sh.start + local, sp.start + local
        wc, ua = weight[edge] * cp, p['hx_UA'][local]
        mc = p['primary_flow_kg_s'][local] * p['primary_cp'][local]
        A[isec, isec] -= (wc + ua) / ch[local]
        A[isec, ipri] += ua / ch[local]
        A[isec] += wc / ch[local] * S[up[edge]]
        b[isec] += wc / ch[local] * g[up[edge]]
        A[ipri, isec] += ua / cpri[local]
        A[ipri, ipri] -= (ua + mc) / cpri[local]
        b[ipri] += mc / cpri[local] * p['primary_inlet_K'][local]
    return dict(A=A, b=b, S=S, g=g, C=C, p=p, ids=ids, owner=owner, first=first, last=last,
                cw=cw, cs=cs, ch=ch, cpri=cpri, up=up, down=down, weight=weight,
                actual=actual, dynamic=dynamic, sw=sw, ss=ss, sh=sh, sp=sp, layout=layout,
                x0=state[dynamic], flow=flow)


def audit(definition, state, reference):
    eq, r = definition.equation_graph, reference
    theta = definition.parameters(0)
    x0 = r['x0']
    node_error = np.max(np.abs(r['S'] @ x0 + r['g'] - state[r['layout']['node_temperature']]))
    equilibrium_error = np.max(np.abs(r['A'] @ x0 + r['b']))
    gen = np.random.default_rng(20261005)
    jvp_errors = []
    y = torch.tensor(state, dtype=torch.float64)
    for _ in range(3):
        v = gen.normal(size=len(x0))
        full = np.zeros(len(state))
        full[r['dynamic']] = v
        full[r['layout']['node_temperature']] = r['S'] @ v
        _, tangent = torch.func.jvp(lambda yy: eq.residual(yy.new_tensor(0.), yy, torch.zeros_like(yy), theta),
                                   (y,), (torch.tensor(full, dtype=torch.float64),))
        expected = -r['C'] * (r['A'] @ v)
        actual = tangent.detach().numpy()[r['dynamic']]
        jvp_errors.append(float(np.max(np.abs(actual - expected)) / max(1., np.max(np.abs(expected)))))
    net = np.zeros(eq.graph.N_nodes)
    np.add.at(net, eq.graph.edge_from.numpy(), r['flow'])
    np.add.at(net, eq.graph.edge_to.numpy(), -r['flow'])
    return dict(node_consistency_max_K=float(node_error), equilibrium_rhs_max_K_s=float(equilibrium_error),
                assembly_jvp_relative_errors=jvp_errors, independent_mass_balance_kg_s=float(np.max(np.abs(net))))


def wave(t, duration, amplitude=.2):
    w = math.pi / duration
    z = w * np.asarray(t)
    f = amplitude * np.sin(z) ** 3
    df = 3 * amplitude * w * np.sin(z) ** 2 * np.cos(z)
    ddf = 3 * amplitude * w ** 2 * (2 * np.sin(z) * np.cos(z) ** 2 - np.sin(z) ** 3)
    return f, df, ddf


def manufactured(raw, reference, duration, dt):
    out = scalar_copy(raw, duration, dt)
    r = reference
    times = np.arange(out['time']['N_t']) * dt
    f, df, ddf = wave(times, duration)
    edges = {e['id']: e for e in out['edges']}
    graph = load_dae_input(out).equation_graph.graph
    # Different amplitudes on each water cell/plate make pipe advection,
    # node mixing and cold-plate NTU terms change throughout the trajectory.
    # These sources are hand-derived from control-volume balances, not A@x
    # or a call to the production residual.
    amplitudes = np.random.default_rng(20261005).uniform(.5, 1.5, len(r['x0']))
    amplitudes[r['sp']] = 0.  # primary amplitudes follow the HX balance below
    node_amplitude = r['S'] @ amplitudes
    cp = r['p']['fluid_cp'][0]
    for edge, begin in r['first'].items():
        end = r['last'][edge] + 1
        indices = np.arange(begin, end)
        local = amplitudes[indices]
        if r['actual'][edge] >= 0:
            upstream = np.r_[node_amplitude[r['up'][edge]], local[:-1]]
        else:
            upstream = np.r_[local[1:], node_amplitude[r['up'][edge]]]
        q = r['cw'][indices, None] * local[:, None] * df[None, :] - (
            r['weight'][edge] * cp * (upstream - local)[:, None] * f[None, :])
        ds = graph.cell_ds[begin:end].numpy()
        edges[graph.edge_str_ids[edge]]['params']['heat_source_W_per_m'] = (q / ds[:, None]).tolist()
    for local, edge in enumerate(r['ids']['cp']):
        weight_cp = r['weight'][edge] * cp
        effectiveness = 1 - np.exp(-r['p']['cp_UA'][local] / weight_cp) if weight_cp > 0 else 0.
        conductance = weight_cp * effectiveness
        solid_amp = amplitudes[r['ss'].start + local]
        q = r['p']['heat_W'][local] + r['cs'][local] * solid_amp * df + (
            conductance * (solid_amp - node_amplitude[r['up'][edge]]) * f)
        edges[graph.edge_str_ids[edge]]['params']['Q_W'] = q.tolist()
    secondary_amp = amplitudes[r['sh']]
    hx_up_amp = node_amplitude[r['up'][r['ids']['hx']]]
    hx_advection = r['weight'][r['ids']['hx']] * cp
    primary_f_amp = secondary_amp - hx_advection / r['p']['hx_UA'] * (hx_up_amp - secondary_amp)
    primary_df_amp = r['ch'] / r['p']['hx_UA'] * secondary_amp
    primary_shift = primary_f_amp[:, None] * f + primary_df_amp[:, None] * df
    primary_rate = primary_f_amp[:, None] * df + primary_df_amp[:, None] * ddf
    exchange_shift = hx_advection[:, None] * (hx_up_amp - secondary_amp)[:, None] * f - (
        r['ch'][:, None] * secondary_amp[:, None] * df)
    shift_inlet = primary_shift + (r['cpri'][:, None] * primary_rate - exchange_shift) / (
        r['p']['primary_flow_kg_s'] * r['p']['primary_cp'])[:, None]
    order = {graph.edge_str_ids[e]: i for i, e in enumerate(r['ids']['hx'])}
    for circuit in out['primary_circuits']:
        i = order[circuit['edge_id']]
        circuit['inlet_K'] = (r['p']['primary_inlet_K'][i] + shift_inlet[i]).tolist()
    exact = r['x0'][None, :] + amplitudes[None, :] * f[:, None]
    exact[:, r['sp']] = r['x0'][r['sp']][None, :] + primary_shift.T
    derivative = amplitudes[None, :] * df[:, None]
    derivative[:, r['sp']] = primary_rate.T
    return out, exact, derivative, node_amplitude


def full_mms(raw, reference, output, duration=120.):
    runs = []
    for dt in (10., 5., 2.5):
        mms_input, exact, derivative, node_amplitude = manufactured(raw, reference, duration, dt)
        path = output / f'manufactured-dt{dt:g}.liquidrun'
        path.write_text(json.dumps(mms_input, indent=2) + '\n')
        start = time.perf_counter()
        definition, experiment, states, residual, balance, relative_balance = run(mms_input)
        eq = definition.equation_graph
        continuous_defect = 0.
        # Independence anchor: exact states come from per-cell manufactured balances,
        # not A/JVP. Every continuous row must vanish before integration.
        for i in range(len(states)):
            target = states[0].copy()
            target[reference['dynamic']] = exact[i]
            target[reference['layout']['node_temperature']] = states[0, reference['layout']['node_temperature']] + node_amplitude * wave(i * dt, duration)[0]
            rate = np.zeros(eq.size)
            rate[reference['dynamic']] = derivative[i]
            res = eq.residual(torch.tensor(i * dt), torch.tensor(target), torch.tensor(rate), definition.parameters(i))
            continuous_defect = max(continuous_defect, float((res / eq.residual_scale).abs().max()))
        error = np.abs(states[:, reference['dynamic']] - exact)
        runs.append(dict(dt_s=dt, frames=len(states), max_temperature_error_K=float(error.max()),
                         max_solid_error_K=float(error[:, reference['ss']].max()),
                         max_continuous_scaled_defect=continuous_defect,
                         max_newton_scaled_residual=residual, max_energy_balance_W=balance,
                         max_relative_energy_balance=relative_balance,
                         wall_seconds=time.perf_counter() - start, input_sha256=digest(path)))
        print('MMS ' + json.dumps(runs[-1]), flush=True)
    orders = [math.log(a['max_temperature_error_K'] / b['max_temperature_error_K'], 2) for a, b in zip(runs, runs[1:])]
    return dict(runs=runs, observed_time_orders=orders)


def physical_closed_form(raw, reference):
    # The original React-exported trajectory is sampled and held per BE step.
    # Exact continuous interpolation of keyframe ramps is a different input.
    definition, experiment, states, residual, balance, relative_balance = run(raw)
    r, eq = reference, definition.equation_graph
    initial_delta = float(np.max(np.abs(states[0, r['dynamic']] - r['x0'])))
    x = states[0, r['dynamic']].copy()
    errors = [0.]
    component_errors = {name:0. for name in ('water','solid','secondary','primary')}
    cache = {}
    for i in range(1, len(states)):
        theta = definition.parameters(i).detach().numpy()
        p = {name: theta[sl] for name, sl in eq.parameter_layout.items()}
        # Constant flows / UA / capacities are needed for a fixed linear A.
        for name in ('pump_rpm_ratio', 'valve_opening', 'primary_flow_kg_s'):
            if not np.array_equal(p[name], r['p'][name]):
                raise ValueError(f'physical closed-form input has varying {name}; use fixed hydraulics')
        if np.max(np.abs(states[i, r['layout']['flow']] - r['flow'])) > 1e-9:
            raise ValueError('flow changed; a constant thermal matrix is invalid')
        b = r['b'].copy()
        b[r['sw']] += (p['cell_heat_W'] - r['p']['cell_heat_W']) / r['cw']
        b[r['ss']] += (p['heat_W'] - r['p']['heat_W']) / r['cs']
        b[r['sp']] += r['p']['primary_flow_kg_s'] * r['p']['primary_cp'] / r['cpri'] * (p['primary_inlet_K'] - r['p']['primary_inlet_K'])
        key = b.tobytes()
        if key not in cache:
            aug = np.zeros((len(x) + 1, len(x) + 1))
            aug[:-1, :-1], aug[:-1, -1] = r['A'], b
            cache[key] = expm(aug * definition.dt)
        x = (cache[key] @ np.r_[x, 1.])[:-1]
        difference = np.abs(states[i, r['dynamic']] - x)
        errors.append(float(np.max(difference)))
        for name, key in zip(component_errors, ('sw','ss','sh','sp')):
            component_errors[name] = max(component_errors[name], float(difference[r[key]].max()))
    return dict(frames=len(states), duration_s=(len(states) - 1) * definition.dt,
                dt_s=definition.dt, initial_match_max_K=initial_delta,
                max_all_thermal_error_K=max(errors), final_all_thermal_error_K=errors[-1],
                max_newton_scaled_residual=residual, max_energy_balance_W=balance,
                         max_relative_energy_balance=relative_balance,
                exact_exponential_segments=len(cache), errors_K_by_sample=errors,
                max_component_errors_K=component_errors)


def regrid_physical(raw, dt):
    """Refine a sampled-hold product trajectory; no geometry/parameter edits."""
    out = copy.deepcopy(raw)
    original_dt, original_count = raw['time']['dt_s'], raw['time']['N_t']
    duration = (original_count - 1) * original_dt
    count = round(duration / dt) + 1
    out['time'].update(dt_s=dt, N_t=count)
    indices = [min(int(math.floor(i * dt / original_dt + 1e-10)), original_count - 1) for i in range(count)]
    def resample(value):
        return [value[i] for i in indices] if isinstance(value, list) else value
    for edge in out['edges']:
        for name in ('Q_W','rpm_ratio','opening','T_set_K','flow_set_kg_s','dT_set_K'):
            if name in edge['params']:
                edge['params'][name] = resample(edge['params'][name])
        if isinstance(edge['params'].get('heat_source_W_per_m'), list):
            edge['params']['heat_source_W_per_m'] = [resample(row) for row in edge['params']['heat_source_W_per_m']]
    for node in out['nodes']:
        if node.get('bc'):
            node['bc']['P_Pa'] = resample(node['bc']['P_Pa'])
    for circuit in out['primary_circuits']:
        for name in ('inlet_K','flow_kg_s'):
            circuit[name] = resample(circuit[name])
    return out


def gradient_check(raw):
    shortened = scalar_copy(raw, duration=30., dt=10.)
    def trajectory(alpha, beta):
        definition = load_dae_input(shortened)
        original = definition.parameters
        sl = definition.equation_graph.parameter_layout['pump_rpm_ratio']
        def parameters(index, heat_multiplier=1.):
            theta = original(index, heat_multiplier=heat_multiplier)
            if index:
                theta = torch.cat([theta[:sl.start], theta[sl] * beta, theta[sl.stop:]])
            return theta
        definition.parameters = parameters
        experiment = DAEExperiment(definition, record_frames=False)
        experiment.set_heat_multiplier(alpha)
        while not experiment.done:
            experiment.step()
        return experiment.tensor_series('solid_temperature')[:, -1].mean()
    alpha = torch.tensor(1.2, dtype=torch.float64, requires_grad=True)
    beta = torch.tensor(.9, dtype=torch.float64, requires_grad=True)
    objective = trajectory(alpha, beta)
    derivatives = torch.autograd.grad(objective, (alpha, beta))
    h = 1e-4
    fd = [(trajectory(1.2 + h, .9) - trajectory(1.2 - h, .9)) / (2 * h),
          (trajectory(1.2, .9 + h) - trajectory(1.2, .9 - h)) / (2 * h)]
    directions = {}
    for name, ad, centered in zip(('heat_multiplier', 'pump_rpm_multiplier'), derivatives, fd):
        a, b = float(ad), float(centered)
        rel = abs(a - b) / max(abs(a), abs(b), 1e-10)
        directions[name] = dict(autograd=a, centered_difference=b, relative_error=rel,
                               passed=math.isfinite(a) and abs(a) > 1e-8 and rel < 1e-4)
    return dict(objective='mean of all cold-plate solid temperatures at 30 s',
                objective_K=float(objective), epsilon=h, input_schema=shortened['schemaVersion'],
                directions=directions, scope='fixed topology and flow/valve regime; JSON compiler is discrete')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--fixture', type=Path, required=True)
    ap.add_argument('--output-dir', type=Path, required=True)
    args = ap.parse_args()
    output = args.output_dir.resolve()
    if not output.is_relative_to(ROOT):
        ap.error('output must stay inside dcdtuni')
    output.mkdir(parents=True, exist_ok=True)
    torch.set_num_threads(2)
    torch.set_num_interop_threads(1)
    start = time.perf_counter()
    raw = json.loads(args.fixture.read_text())
    if raw.get('schemaVersion') != 4:
        ap.error('final verification requires a React-exported v4 input')
    raw['execution'].update(device='cpu', dtype='float64', max_threads=2)
    report = dict(status='running', date='2026-10-05', input=str(args.fixture.resolve()),
                  input_sha256=digest(args.fixture), git_head=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                  source_sha256={rel:digest(ROOT / rel) for rel in ['tools/shinaide_liquid_verification.py',
                      'src/backend/liquidcooling/solver/equation_graph.py', 'src/backend/liquidcooling/solver/dae_newton.py',
                      'src/backend/liquidcooling/liquidrun_dae_schema.py']},
                  execution=dict(device='cpu', dtype='float64', threads=2, affinity=sorted(os.sched_getaffinity(0)),
                                 cuda_visible_devices=os.environ.get('CUDA_VISIBLE_DEVICES')))
    try:
        base = scalar_copy(raw)
        definition = load_dae_input(base)
        experiment = DAEExperiment(definition)
        state = experiment.tensor_state.detach().numpy()
        reference = assemble(definition, state)
        eq = definition.equation_graph
        report['graph'] = dict(nodes=eq.graph.N_nodes, edges=eq.graph.N_edges, cold_plates=eq.graph.N_cp,
                               exchangers=eq.graph.N_hx, water_cells=eq.graph.N_cells,
                               dae_unknowns=eq.size, thermal_states=len(reference['x0']))
        report['independent_assembly'] = audit(definition, state, reference)
        print('ASSEMBLY ' + json.dumps(report['independent_assembly']), flush=True)
        report['manufactured_solution'] = full_mms(raw, reference, output)
        print('PHYSICAL_RUN_START', flush=True)
        report['physical_closed_form'] = physical_closed_form(raw, reference)
        physical_runs = [report['physical_closed_form']]
        for dt in (5., 2.5):
            refined = regrid_physical(raw, dt)
            physical_runs.append(physical_closed_form(refined, reference))
            print('PHYSICAL_REFINEMENT ' + json.dumps({k:v for k,v in physical_runs[-1].items() if k != 'errors_K_by_sample'}), flush=True)
        report['physical_time_refinement'] = dict(runs=physical_runs, observed_orders=[
            math.log(a['max_all_thermal_error_K'] / b['max_all_thermal_error_K'], 2)
            for a,b in zip(physical_runs,physical_runs[1:])])
        print('GRADIENT_START', flush=True)
        report['gradient'] = gradient_check(raw)
        a, m, physical = report['independent_assembly'], report['manufactured_solution'], report['physical_closed_form']
        report['gates'] = dict(node_consistency=a['node_consistency_max_K'] < 1e-8,
            steady_equivalence=a['equilibrium_rhs_max_K_s'] < 1e-8,
            physical_initial_match=physical['initial_match_max_K'] < 1e-8,
            assembly_jvp=max(a['assembly_jvp_relative_errors']) < 1e-8,
            mass_conservation=a['independent_mass_balance_kg_s'] < 1e-8,
            manufactured_continuous_defect=max(x['max_continuous_scaled_defect'] for x in m['runs']) < 1e-8,
            backward_euler_order=all(.85 < order < 1.15 for order in m['observed_time_orders']),
            newton_residual=max([x['max_newton_scaled_residual'] for x in m['runs']] + [physical['max_newton_scaled_residual']]) < 1e-8,
            energy_balance=max([x['max_relative_energy_balance'] for x in m['runs']] + [x['max_relative_energy_balance'] for x in physical_runs]) < 1e-9,
            physical_refinement_reduces_error=all(a['max_all_thermal_error_K'] > b['max_all_thermal_error_K'] for a,b in zip(physical_runs,physical_runs[1:])),
            gradient=all(d['passed'] for d in report['gradient']['directions'].values()))
        report['status'] = 'passed' if all(report['gates'].values()) else 'gates_not_met'
    except Exception as error:
        import traceback
        report.update(status='failed', error_type=type(error).__name__, error=str(error), traceback=traceback.format_exc())
    report['wall_seconds'] = time.perf_counter() - start
    (output / 'receipt.json').write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
    print(json.dumps(report, indent=2, allow_nan=False), flush=True)
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())