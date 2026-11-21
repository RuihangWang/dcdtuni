"""Bounded CPU1/CPU2 replay of the same project-derived NV mock DAE input.

No parameter tuning, GPU, NSCC, UI or core edits. A 55 s internal deadline leaves
reporting margin under the caller's 60 s wall cap. The source fixture stays intact.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import inspect
import json
import os
from pathlib import Path
import signal
import sys
import time
import traceback


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parents=Path(__file__).resolve().parents
    parser.add_argument('--repo',type=Path,default=parents[2] if len(parents)>2 else Path.cwd())
    parser.add_argument('--fixture',type=Path)
    parser.add_argument('--expected-equation-sha',required=True)
    parser.add_argument('--expected-newton-sha',required=True)
    parser.add_argument('--output-prefix',type=Path,default=Path(__file__).with_name('nv-vectorized-cpu-2026-09-26'))
    args=parser.parse_args()
    root=args.repo.resolve();fixture=args.fixture or root/'docs/benchmarks/fixtures/nv-pek02-mock-v3.liquidrun'
    for name in ('OMP_NUM_THREADS','MKL_NUM_THREADS','OPENBLAS_NUM_THREADS','NUMEXPR_NUM_THREADS'):
        os.environ[name]='1'
    os.environ['CUDA_VISIBLE_DEVICES']=''
    allowed=set(os.sched_getaffinity(0)) & {0,1}
    if allowed!={0,1}:
        raise RuntimeError(f'CPU0/1 were requested but are not both available: {sorted(allowed)}; no substitute CPUs selected')
    os.sched_setaffinity(0,{0})
    os.nice(max(0,10-os.getpriority(os.PRIO_PROCESS,0)))
    sys.path.insert(0,str(root/'src'))
    started=time.perf_counter()
    evidence={'status':'running','scope':'synthetic inventory mock numerical probe; not NV calibration or a general hardware benchmark',
        'total_wall_cap_s':60,'internal_deadline_s':55,'pid':os.getpid(),'nice':os.getpriority(os.PRIO_PROCESS,0),
        'fixture_path':str(fixture),'cuda_validation':'not_run','gradient_validation':'not_run',
        'cases':[],'stages':{},'thread_comparison':None}
    args.output_prefix.parent.mkdir(parents=True,exist_ok=True)
    output=args.output_prefix.with_suffix('.json')
    def persist():
        evidence['elapsed_s']=time.perf_counter()-started
        output.write_text(json.dumps(evidence,indent=2,ensure_ascii=False,allow_nan=False)+'\n')
    def deadline(_signal,_frame):raise TimeoutError('55-second internal wall deadline exhausted')
    signal.signal(signal.SIGALRM,deadline);signal.setitimer(signal.ITIMER_REAL,55)
    def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    def stage(name,fn):
        evidence['current_stage']=name;persist();print('START '+name,flush=True)
        begin=time.perf_counter()
        try:return fn()
        finally:
            evidence['stages'][name]=time.perf_counter()-begin
            persist();print('END '+name+f" {evidence['stages'][name]:.4f}s",flush=True)
    def error_record(error):return {'type':type(error).__name__,'message':str(error),'traceback':traceback.format_exc()}
    try:
        import torch
        torch.set_num_threads(1);torch.set_num_interop_threads(1)
        from backend.liquidcooling.liquidrun_dae_schema import load_dae_input
        from backend.liquidcooling.dae_experiment import DAEExperiment
        from backend.liquidcooling.solver.equation_graph import EquationGraph
        from backend.liquidcooling.solver.dae_newton import solve_steady
        eq_path=root/'src/backend/liquidcooling/solver/equation_graph.py'
        newton_path=root/'src/backend/liquidcooling/solver/dae_newton.py'
        if Path(inspect.getfile(EquationGraph)).resolve()!=eq_path or Path(inspect.getfile(solve_steady)).resolve()!=newton_path:
            raise RuntimeError('Imports resolve outside the selected worktree')
        if digest(eq_path)!=args.expected_equation_sha or digest(newton_path)!=args.expected_newton_sha:
            raise RuntimeError('Core hashes do not match the reviewed vectorized/device-aware implementation; refusing stale evidence')
        evidence['source_sha256']={str(path.relative_to(root)):digest(path) for path in [eq_path,newton_path,root/'src/backend/liquidcooling/dae_experiment.py',root/'src/backend/liquidcooling/liquidrun_dae_schema.py',root/'src/backend/liquidcooling/graphcompiling/compiler.py']}
        evidence['script_sha256']=digest(__file__);evidence['fixture_sha256']=digest(fixture)
        evidence['torch']={'version':str(torch.__version__),'cpu_capability':torch.backends.cpu.get_cpu_capability(),'interop_threads':torch.get_num_interop_threads(),'build_config':torch.__config__.show()}
        evidence['host']={'machine':os.uname().machine,'load_average_at_start':os.getloadavg()}
        raw=json.loads(fixture.read_text())
        if (len(raw['racks']),len(raw['nodes']),len(raw['edges']))!=(34,316,348):raise ValueError('Requires the saved NV mock fixture without reduction')
        evidence['primary_circuits']=raw['primary_circuits']
        evidence['source_project']=raw['source_project']
        evidence['manifest_execution']=raw['execution']
        evidence['runtime_thread_override']='The immutable input declares max_threads=1. Only this authorized benchmark overrides PyTorch/CPU affinity to two threads for its second run; physical data and algorithm stay unchanged.'
        evidence['forcing']={'runtime_heat_multiplier':1.2,'applies_from_seq':1,'dt_s':raw['time']['dt_s'],'maximum_transient_steps':3,
            'saved_events':raw.get('events',[]),'policy':'Runtime load step at the next accepted step; stop before the saved t=5 s load event, so heat is not multiplied twice. No input file is rewritten.'}
        if any(event['time_s']<=3*raw['time']['dt_s'] for event in raw.get('events',[])):
            raise ValueError('Saved events overlap the short runtime step probe; do not silently change the input schedule')
        states={}
        def primary_reference(frame):
            # This fixture explicitly assigns equal 17-rack zones, all rack
            # loads equal, one HX per zone and no pipe heat. This is a separate
            # first-law reference, not a solver residual or fitted target.
            heat_per_zone=frame['diagnostics']['heat_input_W']/2
            expected=[c['inlet_K']+heat_per_zone/(c['flow_kg_s']*c['cp_J_kgK']) for c in raw['primary_circuits']]
            error=max(abs(a-b) for a,b in zip(frame['primary_return_K'],expected))
            if error>1e-6:raise RuntimeError(f'Independent steady primary-temperature heat balance failed: {error} K')
            return {'expected_return_K':expected,'max_error_K':error}
        def compact(frame):
            diagnostics=frame['diagnostics']
            if abs(diagnostics['energy_balance_W'])>1e-7*(1+abs(diagnostics['heat_input_W'])):
                raise RuntimeError('Accepted frame failed independent global-energy tolerance')
            if diagnostics['mass_residual_kg_s']>1e-7:raise RuntimeError('Accepted frame failed mass-balance tolerance')
            return {key:frame[key] for key in ['kind','seq','heat_multiplier','primary_return_K','secondary_supply_K','diagnostics','newton'] if key in frame} | {
                'time_s':frame.get('time_s'),'evaluated_at_s':frame.get('evaluated_at_s'),
                'solid_min_K':min(frame['solid_temperature_K']),'solid_max_K':max(frame['solid_temperature_K']),
                'flow_min_kg_s':min(frame['edge_flow_kg_s']),'flow_max_kg_s':max(frame['edge_flow_kg_s'])}
        for threads in (1,2):
            record={'threads_requested':threads,'status':'running','samples':[],'equilibrium':None}
            evidence['cases'].append(record)
            try:
                os.sched_setaffinity(0,{0} if threads==1 else {0,1})
                os.environ['OMP_NUM_THREADS']=str(threads);os.environ['MKL_NUM_THREADS']=str(threads)
                torch.set_num_threads(threads)
                record.update(affinity=sorted(os.sched_getaffinity(0)),torch_threads=torch.get_num_threads(),parallel_info=torch.__config__.parallel_info())
                definition=stage(f'cpu{threads}/load_and_compile',lambda:load_dae_input(raw))
                record['solver_options']=asdict(definition.newton_options)
                eq=definition.equation_graph
                record['graph']={'state_count':eq.size,'parameter_count':eq.parameter_count,'nodes':eq.graph.N_nodes,'edges':eq.graph.N_edges,'pipe_cells':eq.graph.N_cells,'cold_plates':eq.graph.N_cp,'heat_exchangers':eq.graph.N_hx}
                experiment=stage(f'cpu{threads}/steady_initialization',lambda:DAEExperiment(definition))
                record['initializer']=experiment.initializer
                initial=compact(experiment.frames[0])
                initial['independent_primary_heat_balance']=primary_reference(experiment.frames[0])
                record['samples'].append(initial)
                states[(threads,'steady')]=experiment.tensor_state.detach().clone()
                record['device']=str(states[(threads,'steady')].device);record['dtype']=str(states[(threads,'steady')].dtype)
                if record['device']!='cpu' or record['dtype']!='torch.float64':raise RuntimeError('Unexpected device or dtype')
                experiment.set_heat_multiplier(1.2)
                for step in range(1,(3 if threads==1 else 1)+1):
                    frame=stage(f'cpu{threads}/step_{step}',experiment.step)
                    record['samples'].append(compact(frame))
                    if step==1:states[(threads,'step_1')]=experiment.tensor_state.detach().clone()
                sequence=experiment.seq;before=experiment.tensor_state.detach().clone()
                equilibrium=stage(f'cpu{threads}/independent_equilibrium',lambda:experiment.equilibrium(1.2))
                record['equilibrium']=compact(equilibrium)
                record['equilibrium']['independent_primary_heat_balance']=primary_reference(equilibrium)
                record['equilibrium_did_not_mutate_transient']=experiment.seq==sequence and torch.equal(experiment.tensor_state.detach(),before)
                if not record['equilibrium_did_not_mutate_transient']:raise RuntimeError('Equilibrium query mutated the transient experiment')
                # Small warmed residual batch, measured on the same baseline state
                # and physical theta for both thread settings. No extra nonlinear solves.
                theta=definition.parameters(0);y=states[(threads,'steady')];ydot=torch.zeros_like(y);zero=y.new_zeros(())
                with torch.no_grad():
                    for _ in range(3):eq.residual(zero,y,ydot,theta)
                    begin=time.perf_counter()
                    for _ in range(20):eq.residual(zero,y,ydot,theta)
                    record['residual_batch']={'repeats':20,'elapsed_s':time.perf_counter()-begin}
                record['status']='passed';persist()
            except TimeoutError:raise
            except Exception as error:
                record['status']='failed';record['error']=error_record(error);persist()
                print(traceback.format_exc(),flush=True)
        if all(c['status']=='passed' for c in evidence['cases']):
            comparison={}
            for name in ('steady','step_1'):
                a,b=states[(1,name)],states[(2,name)]
                scaled=(a-b).abs()/eq.state_scale
                comparison[name]={'max_abs_difference':float((a-b).abs().max()),'max_state_scaled_difference':float(scaled.max()),'within_1e_8_scaled':bool((scaled<=1e-8).all())}
            comparison['steady_time_cpu1_over_cpu2']=evidence['stages']['cpu1/steady_initialization']/evidence['stages']['cpu2/steady_initialization']
            comparison['step1_time_cpu1_over_cpu2']=evidence['stages']['cpu1/step_1']/evidence['stages']['cpu2/step_1']
            comparison['timing_scope']='one measured solve per setting; no statistical speedup claim; GPU not run'
            evidence['thread_comparison']=comparison
            evidence['status']='passed' if all(comparison[n]['within_1e_8_scaled'] for n in ('steady','step_1')) else 'failed_thread_mismatch'
        else:evidence['status']='failed'
    except BaseException as error:
        evidence['status']='timeout' if isinstance(error,TimeoutError) else 'failed'
        evidence['error']=error_record(error)
        for record in evidence['cases']:
            if record['status']=='running':record['status']='incomplete'
        print(traceback.format_exc(),flush=True)
    finally:
        signal.setitimer(signal.ITIMER_REAL,0)
        persist()
        lines=['# NV mock vectorized DAE CPU probe','',f"Status: **{evidence['status']}**. Elapsed {evidence['elapsed_s']:.3f} s; 60 s caller cap, 55 s internal deadline.",'',
            'Same immutable 316-node / 348-edge input, float64 PyTorch algorithm; one then two threads on CPU 0/1, nice 10. No CUDA or NSCC execution. Full hashes, build/SIMD capability and effective thread settings are in the JSON.','',
            'The heat multiplier is applied at the next step and the run stops before the saved 5 s event. All topology, physical coefficients and primary-circuit assumptions remain unchanged. Independent equilibrium is recorded separately, never inserted as a transient sample.','']
        for c in evidence['cases']:
            lines.append(f"- CPU {c['threads_requested']} thread(s): {c['status']}; affinity {c.get('affinity')}; accepted transient steps {max(0,len(c['samples'])-1)}.")
            for sample in c['samples']:
                d=sample['diagnostics'];lines.append(f"  - t={sample['time_s']} s: primary return {sample['primary_return_K']} K; secondary supply {sample['secondary_supply_K']} K; mass residual {d['mass_residual_kg_s']:.6g} kg/s; energy balance {d['energy_balance_W']:.6g} W.")
            if c.get('error'):lines.append(f"  - Failure: {c['error']['type']}: {c['error']['message']}")
        if evidence.get('error'):lines+=['',f"Stopped at {evidence.get('current_stage')}: {evidence['error']['type']} — {evidence['error']['message']}."]
        lines+=['','Single-run timing is reported without assuming two threads are faster. SIMD capability is a library/CPU report, not a hardware instruction trace. Unexecuted stages, gradients, CUDA and NV field validation remain unverified.']
        args.output_prefix.with_suffix('.md').write_text('\n'.join(lines)+'\n')
        print(json.dumps({'status':evidence['status'],'elapsed_s':evidence['elapsed_s'],'report':str(output)}),flush=True)
    return 0 if evidence['status']=='passed' else 1


if __name__=='__main__':raise SystemExit(main())
