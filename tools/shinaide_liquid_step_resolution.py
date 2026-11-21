"""Resolve the Shinaide load-step peak with sub-second full-graph DAE steps.

The original pre-step trajectory is a stationary equilibrium. Starting this
verification window at original t=50 s therefore preserves its full state.
This supplements the complete 0..600 s runs; the window is 50..90 s.
"""
import argparse
import copy
import json
import math
import os
from pathlib import Path
import time

import numpy as np
import torch

from shinaide_liquid_verification import (
    ROOT, scalar_copy, load_dae_input, DAEExperiment, assemble,
    physical_closed_form, digest,
)


def window(raw, dt, start=50., duration=40.):
    out = copy.deepcopy(raw)
    old_dt, old_count = raw['time']['dt_s'], raw['time']['N_t']
    count = round(duration / dt) + 1
    indices = [min(math.floor((start + i * dt) / old_dt + 1e-10), old_count - 1) for i in range(count)]
    out['time'].update(dt_s=dt, N_t=count)
    out['transient']['durationS'] = duration
    out['execution'].update(device='cpu', dtype='float64', max_threads=2)
    def crop(value):
        return [value[i] for i in indices] if isinstance(value, list) else value
    for edge in out['edges']:
        for name in ('Q_W','rpm_ratio','opening','T_set_K','flow_set_kg_s','dT_set_K'):
            if name in edge['params']:
                edge['params'][name] = crop(edge['params'][name])
    for node in out['nodes']:
        if node.get('bc'):
            node['bc']['P_Pa'] = crop(node['bc']['P_Pa'])
    for circuit in out['primary_circuits']:
        for name in ('inlet_K','flow_kg_s'):
            circuit[name] = crop(circuit[name])
    # Only a stationary prefix makes re-initializing this window equivalent.
    for edge in raw['edges']:
        for name in ('Q_W','rpm_ratio','opening'):
            value = edge['params'].get(name)
            if isinstance(value,list) and len(set(value[:math.floor(start/old_dt)+1])) > 1:
                raise ValueError('the pre-window trajectory is not stationary')
    for circuit in raw['primary_circuits']:
        for name in ('inlet_K','flow_kg_s'):
            value = circuit[name]
            if isinstance(value,list) and len(set(value[:math.floor(start/old_dt)+1])) > 1:
                raise ValueError('the pre-window primary trajectory is not stationary')
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--fixture',type=Path,required=True)
    ap.add_argument('--output-dir',type=Path,required=True)
    args = ap.parse_args()
    out = args.output_dir.resolve()
    if not out.is_relative_to(ROOT):
        ap.error('outputs must stay inside dcdtuni')
    out.mkdir(parents=True,exist_ok=True)
    torch.set_num_threads(2);torch.set_num_interop_threads(1)
    raw = json.loads(args.fixture.read_text())
    base = scalar_copy(raw)
    definition = load_dae_input(base)
    experiment = DAEExperiment(definition)
    reference = assemble(definition,experiment.tensor_state.detach().numpy())
    r = reference
    cp_adv = r['weight'][r['ids']['cp']] * r['p']['fluid_cp'][0]
    conductance = cp_adv * (-np.expm1(-r['p']['cp_UA']/cp_adv))
    tau = r['cs']/conductance
    report = dict(status='running',date='2026-10-05',input_sha256=digest(args.fixture),
                  tool_sha256=digest(__file__),reference_tool_sha256=digest(Path(__file__).with_name('shinaide_liquid_verification.py')),
                  execution=dict(device='cpu',dtype='float64',threads=2,affinity=sorted(os.sched_getaffinity(0))),
                  original_time_window_s=[50,90],assumption='prefix 0..50s is stationary with the original initial boundary values',
                  cp_time_constant_s=dict(minimum=float(tau.min()),maximum=float(tau.max())))
    start=time.perf_counter()
    runs=[]
    try:
        for dt in (2.5,1.25,.625,.3125):
            inp = window(raw,dt)
            inp_path = out/f'physical-step-window-dt{dt:g}.liquidrun'
            inp_path.write_text(json.dumps(inp,indent=2)+'\n')
            result=physical_closed_form(inp,reference)
            result['input_sha256']=digest(inp_path)
            result['original_peak_time_s']=50+np.argmax(result['errors_K_by_sample'])*dt
            runs.append(result)
            print('STEP_RESOLUTION '+json.dumps({k:v for k,v in result.items() if k!='errors_K_by_sample'}),flush=True)
        orders=[math.log(a['max_all_thermal_error_K']/b['max_all_thermal_error_K'],2) for a,b in zip(runs,runs[1:])]
        report.update(runs=runs,observed_peak_error_orders=orders,
                      gates=dict(monotonic_refinement=all(a['max_all_thermal_error_K']>b['max_all_thermal_error_K'] for a,b in zip(runs,runs[1:])),
                                 finest_order_in_first_order_band=.85<orders[-1]<1.15,
                                 independent_initial_match=max(x['initial_match_max_K'] for x in runs)<1e-8,
                                 nonlinear_residual=max(x['max_newton_scaled_residual'] for x in runs)<1e-8,
                                 energy_balance=max(x['max_relative_energy_balance'] for x in runs)<1e-9))
        report['status']='passed' if all(report['gates'].values()) else 'gates_not_met'
    except Exception as exc:
        import traceback
        report.update(status='failed',error=str(exc),traceback=traceback.format_exc())
    report['wall_seconds']=time.perf_counter()-start
    (out/'receipt.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    print(json.dumps(report,indent=2,allow_nan=False),flush=True)
    return 0 if report['status']=='passed' else 1


if __name__=='__main__':
    raise SystemExit(main())