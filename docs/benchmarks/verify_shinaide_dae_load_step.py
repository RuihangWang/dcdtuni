"""Single-core, 90-second-budget Shinaide DAE 600 s load-step measurement.

Immutable topology and coefficients; no capacity, UA or flow tuning. A declared
mock primary circuit matches the earlier short probe. Every accepted step is
saved to CSV and JSON. This does not integrate DAE configuration with the UI.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import traceback


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture',type=Path,default=Path('/tmp/dcdtuni-shinaide-v2.liquidrun'))
    parser.add_argument('--output-prefix',type=Path,default=Path(__file__).with_name('shinaide-dae-load-step-2026-09-26'))
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[2]
    sys.path.insert(0,str(root/'src'))
    for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):
        os.environ[key]='1'
    os.environ['CUDA_VISIBLE_DEVICES']=''
    os.sched_setaffinity(0,{min(os.sched_getaffinity(0))})
    os.nice(max(0,10-os.getpriority(os.PRIO_PROCESS,0)))
    started=time.perf_counter()
    result={'status':'running','budget_seconds':90,'requested_horizon_s':600,'completed_horizon_s':0,
        'dt_s':5,'cpu_affinity':sorted(os.sched_getaffinity(0)),'nice':os.getpriority(os.PRIO_PROCESS,0),
        'pid':os.getpid(),'fixture_path':str(args.fixture.resolve()),'script_path':str(Path(__file__).resolve()),
        'scope':'numerical experiment with explicit mock primary circuit; not NV calibration or schema/runtime/UI integration',
        'stages':{},'samples':[]}
    args.output_prefix.parent.mkdir(parents=True,exist_ok=True)
    csv_path=args.output_prefix.with_suffix('.csv')
    json_path=args.output_prefix.with_suffix('.json')

    def persist():
        result['total_walltime_seconds']=time.perf_counter()-started
        json_path.write_text(json.dumps(result,indent=2,ensure_ascii=False,allow_nan=False)+'\n')

    def timeout(_signal,_frame):
        raise TimeoutError('90-second total wall-clock deadline exhausted')
    signal.signal(signal.SIGALRM,timeout)
    signal.setitimer(signal.ITIMER_REAL,90)
    print(json.dumps({'pid':result['pid'],'cpu_affinity':result['cpu_affinity'],'deadline_s':90}),flush=True)

    def stage(name,function):
        result['current_stage']=name
        persist()
        begin=time.perf_counter()
        output=function()
        result['stages'][name]=time.perf_counter()-begin
        print(f"DONE {name} {result['stages'][name]:.4f} s",flush=True)
        persist()
        return output

    def digest(path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    try:
        import torch
        torch.set_num_threads(1);torch.set_num_interop_threads(1)
        from backend.liquidcooling.liquidrun_schema import parse_liquidrun
        from backend.liquidcooling.graphcompiling.compiler import compile_graph
        from backend.liquidcooling.graphcompiling.time_slice import time_slice
        from backend.liquidcooling.solver.differentiable_hydraulic import hydraulic_step
        from backend.liquidcooling.solver.equation_graph import EquationGraph,PrimaryCircuit
        from backend.liquidcooling.solver.dae_newton import solve_steady,backward_euler_step,NewtonOptions
        result['torch']={'version':torch.__version__,'threads':torch.get_num_threads(),'interop_threads':torch.get_num_interop_threads()}
        result['git_head']=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()
        result['fixture_sha256']=digest(args.fixture)
        result['script_sha256']=digest(__file__)
        result['source_sha256']={name:digest(root/name) for name in (
            'src/backend/liquidcooling/liquidrun_schema.py','src/backend/liquidcooling/graphcompiling/compiler.py',
            'src/backend/liquidcooling/solver/equation_graph.py','src/backend/liquidcooling/solver/dae_newton.py',
            'src/backend/liquidcooling/solver/differentiable_hydraulic.py')}
        raw=json.loads(args.fixture.read_text())
        graph=stage('parse_compile',lambda:compile_graph(parse_liquidrun(raw),device='cpu'))
        if (graph.N_nodes,graph.N_edges,graph.N_hx)!=(187,242,1):
            raise ValueError('this measurement requires the actual 187-node/242-edge/one-HX Shinaide graph')
        vector=lambda value:torch.full((graph.N_hx,),float(value),dtype=torch.float64)
        primary=PrimaryCircuit(vector(1),vector(293.15),vector(graph.fluid_cp),vector(graph.fluid_rho),
                               vector(.02),vector(.02),
                               'mock:unchanged short-probe values; not NV measurements, vendor data or calibration')
        result['primary_circuit']={key:(value.tolist() if isinstance(value,torch.Tensor) else value) for key,value in asdict(primary).items()}
        eq=stage('equation_graph',lambda:EquationGraph(graph,primary))
        result['graph']={'nodes':graph.N_nodes,'edges':graph.N_edges,'pipe_cells':graph.N_cells,
            'cold_plates':graph.N_cp,'state_count':eq.size,'differential_count':int(eq.differential_mask.sum()),
            'neutral_storage_count':int(eq.steady_neutral_storage_mask.sum())}
        result['solver_options']=asdict(NewtonOptions())
        baseline_heat=graph.cp_params.Q_W[:,0].detach().clone()
        theta0=eq.parameters(time_index=0,heat_W=baseline_heat)
        theta1=eq.parameters(time_index=0,heat_W=1.2*baseline_heat)
        result['forcing']={'baseline_total_Qit_W':float(baseline_heat.sum()),'load_factor_after_t0':1.2,
            'original_event_count':len(raw['events']),
            'policy':'explicit theta replaces original event schedule with fixed initial Qit then 1.2*Qit; all physical coefficients unchanged'}
        guess=eq.initial_guess(theta0)
        pressure,flow=stage('hydraulic_initial_guess',lambda:hydraulic_step(time_slice(graph,0)))
        guess[eq.state_layout['pressure_free']]=pressure[~graph.bc_mask]
        guess[eq.state_layout['flow']]=flow
        conserved=guess.detach().clone()
        steady,report=stage('unified_steady',lambda:solve_steady(eq.problem,guess,theta0,
            conserved=(eq.steady_neutral_storage_mask,conserved)))
        properties={name:theta0[sl] for name,sl in eq.parameter_layout.items()}
        capacity=torch.zeros(eq.size,dtype=torch.float64)
        capacity[eq.state_layout['water_temperature']]=properties['fluid_rho']*properties['fluid_cp']*properties['cell_volume']
        capacity[eq.state_layout['solid_temperature']]=properties['cp_Csolid']
        capacity[eq.state_layout['hx_secondary_temperature']]=properties['fluid_rho']*properties['fluid_cp']*properties['secondary_volume']
        capacity[eq.state_layout['primary_return_temperature']]=properties['primary_rho']*properties['primary_cp']*properties['primary_volume']
        fields=['time_s','mode','Qit_W','primary_inlet_K','primary_return_K','secondary_supply_K','secondary_return_K',
                'max_solid_K','stored_energy_change_J','storage_rate_W','primary_rejection_W','energy_balance_W',
                'mass_residual_inf_kg_s','momentum_residual_inf_Pa','scaled_residual_inf','newton_iterations',
                'newton_backtracks','step_walltime_s']
        with csv_path.open('w',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=fields)
            writer.writeheader();stream.flush()

            def save_sample(y,theta,time_s,mode,report,previous=None,step_walltime=0.):
                values=eq.unpack(y,theta)
                params={name:theta[sl] for name,sl in eq.parameter_layout.items()}
                ydot=torch.zeros_like(y) if previous is None else torch.where(eq.differential_mask,(y-previous)/5,torch.zeros_like(y))
                residual=eq.residual(y.new_tensor(time_s),y,ydot,theta)
                hx_edges=graph.hx_indices.edge_ids
                upstream=torch.where(values['flow'][hx_edges]>=0,graph.edge_from[hx_edges],graph.edge_to[hx_edges])
                source=params['heat_W'].sum()+params['cell_heat_W'].sum()
                storage=(capacity*ydot).sum()
                rejected=(params['primary_flow_kg_s']*params['primary_cp']*(values['primary_return_temperature']-params['primary_inlet_K'])).sum()
                balance=source-storage-rejected
                if not bool(torch.isfinite(y).all() and torch.isfinite(residual).all()):
                    raise RuntimeError('nonfinite accepted DAE state')
                record={'time_s':time_s,'mode':mode,'Qit_W':float(source),
                    'primary_inlet_K':float(params['primary_inlet_K'][0]),
                    'primary_return_K':float(values['primary_return_temperature'][0]),
                    'secondary_supply_K':float(values['hx_secondary_temperature'][0]),
                    'secondary_return_K':float(values['node_temperature'][upstream][0]),
                    'max_solid_K':float(values['solid_temperature'].max()),
                    'stored_energy_change_J':float((capacity*(y-steady)).sum()),
                    'storage_rate_W':float(storage),'primary_rejection_W':float(rejected),'energy_balance_W':float(balance),
                    'mass_residual_inf_kg_s':float(residual[eq.state_layout['pressure_free']].abs().max()),
                    'momentum_residual_inf_Pa':float(residual[eq.state_layout['flow']].abs().max()),
                    'scaled_residual_inf':float((residual/eq.problem.residual_scale).abs().max()),
                    'newton_iterations':report.iterations,'newton_backtracks':report.backtracks,'step_walltime_s':step_walltime}
                writer.writerow(record);stream.flush()
                result['samples'].append(record)
                result['completed_horizon_s']=time_s
                persist()
                if abs(record['energy_balance_W']) > 1e-8*max(1,abs(record['Qit_W'])):
                    raise RuntimeError(f'global energy balance is not satisfied at t={time_s}: {record["energy_balance_W"]} W')

            save_sample(steady,theta0,0.,'steady',report)
            previous=steady
            result['current_stage']='load_step_trajectory'
            for index in range(1,121):
                t=5.*index
                result['attempted_step_time_s']=t
                begin=time.perf_counter()
                current,report=backward_euler_step(eq.problem,previous,theta1,time=t,dt=5.)
                elapsed=time.perf_counter()-begin
                save_sample(current,theta1,t,'transient',report,previous,elapsed)
                previous=current
                if index%12==0:
                    print(json.dumps({'completed_time_s':t,'walltime_s':time.perf_counter()-started,
                                      'primary_return_K':result['samples'][-1]['primary_return_K']}),flush=True)
        result['trajectory_status']='passed'
        result['response']={
            'primary_return_change_K':result['samples'][-1]['primary_return_K']-result['samples'][0]['primary_return_K'],
            'secondary_supply_change_K':result['samples'][-1]['secondary_supply_K']-result['samples'][0]['secondary_supply_K'],
            'secondary_return_change_K':result['samples'][-1]['secondary_return_K']-result['samples'][0]['secondary_return_K'],
            'final_storage_rate_W':result['samples'][-1]['storage_rate_W'],
            'max_abs_energy_balance_W':max(abs(sample['energy_balance_W']) for sample in result['samples']),
            'first_primary_change_over_0_001_K_s':next((sample['time_s'] for sample in result['samples']
                if abs(sample['primary_return_K']-result['samples'][0]['primary_return_K'])>1e-3),None)}
        persist()
        remaining=90-(time.perf_counter()-started)
        if remaining>=12:
            alpha=torch.tensor(1.,dtype=torch.float64,requires_grad=True)
            differentiable_theta=eq.parameters(heat_W=baseline_heat*alpha)
            state,gradient_report=stage('steady_gradient_forward',lambda:solve_steady(eq.problem,steady,differentiable_theta,
                conserved=(eq.steady_neutral_storage_mask,conserved)))
            objective=eq.unpack(state)['primary_return_temperature'][0]
            actual=stage('steady_implicit_backward',lambda:torch.autograd.grad(objective,alpha)[0])
            expected=float(baseline_heat.sum()/(primary.flow_kg_s[0]*primary.cp_J_kgK[0]))
            absolute=abs(float(actual)-expected)
            passed=absolute<=1e-9+1e-7*abs(expected)
            result['steady_primary_return_gradient']={'status':'passed' if passed else 'failed',
                'input':'alpha, baseline entire-graph Qit multiplier','autograd_K_per_alpha':float(actual),
                'analytic_K_per_alpha':expected,'absolute_error':absolute,'relative_error':absolute/abs(expected),
                'newton':asdict(gradient_report)}
            if not passed:
                raise AssertionError('steady primary-return implicit gradient differs from analytic whole-loop heat balance')
        else:
            result['steady_primary_return_gradient']={'status':'not_run','reason':f'only {remaining:.3f} s remains of the hard budget'}
        result['status']='passed'
    except BaseException as error:
        result['status']='timeout' if isinstance(error,TimeoutError) else 'failed'
        result['error']={'type':type(error).__name__,'message':str(error),'traceback':traceback.format_exc()}
        print(traceback.format_exc(),flush=True)
    finally:
        signal.setitimer(signal.ITIMER_REAL,0)
        persist()
        lines=['# Shinaide DAE 600 s 功率阶跃（2026-09-26）','',
            f"状态：**{result['status']}**；实际完成 **{result['completed_horizon_s']:g} / 600 s**；墙钟 {result['total_walltime_seconds']:.3f} s，硬预算 90 s。",'',
            '仅数值验证，非 NV 现场标定，非 schema/UI/mock 平台交付。CPU 单核 affinity、PyTorch/BLAS 单线程、nice 10、CUDA 禁用；未使用 NSCC。','',
            '一次侧保持短 probe 的明确 mock 值：1 kg/s、入口 293.15 K、两侧容积各 0.02 m³；cp/rho 来自原图。没有修改容量、流量或 UA 以加快响应。','',
            '从原图初始 21 kW 热负载稳态开始，t=0 后施加 1.2 倍功率，dt=5 s。原导出文件的事件由此次显式 theta 阶跃替代，输入文件与原工程只读。','',
            f'输入：`{args.fixture}`；逐步数据：[CSV]({csv_path.name})、[JSON]({json_path.name})。','',
            '复现：[verify_shinaide_dae_load_step.py](verify_shinaide_dae_load_step.py)。','',
            '```sh',f'python docs/benchmarks/verify_shinaide_dae_load_step.py --fixture {args.fixture}','```','']
        if result['samples']:
            first,last=result['samples'][0],result['samples'][-1]
            lines += [f"一次回水：{first['primary_return_K']:.9f} → {last['primary_return_K']:.9f} K，变化 {last['primary_return_K']-first['primary_return_K']:.9g} K。",'',
                f"二次供水：{first['secondary_supply_K']:.9f} → {last['secondary_supply_K']:.9f} K；二次回水：{first['secondary_return_K']:.9f} → {last['secondary_return_K']:.9f} K。",'',
                f"末时刻输入功率 {last['Qit_W']:.6f} W，储能率 {last['storage_rate_W']:.6f} W，一次侧带走 {last['primary_rejection_W']:.6f} W。",'',
                f"最大绝对能量残差 {max(abs(sample['energy_balance_W']) for sample in result['samples']):.4g} W；最大质量残差 {max(sample['mass_residual_inf_kg_s'] for sample in result['samples']):.4g} kg/s。",'']
        if 'steady_primary_return_gradient' in result:
            g=result['steady_primary_return_gradient']
            lines += [f"稳态 primaryReturn 对总热负载 scale 的梯度检查：{g['status']}。",'']
            if g['status'] in ('passed','failed'):
                lines += [f"隐式反传 {g['autograd_K_per_alpha']:.12g} K/alpha；独立解析值 {g['analytic_K_per_alpha']:.12g}；相对误差 {g['relative_error']:.3g}。",'']
        if 'error' in result:
            lines += [f"停止于 {result.get('current_stage')}：{result['error']['type']} — {result['error']['message']}。未完成时段不计为通过。",'']
        lines += ['数值温度变化不等于传感器可检测性；该结论仍依赖未校准的阻力、热容与换热参数。']
        args.output_prefix.with_suffix('.md').write_text('\n'.join(lines)+'\n')
        print(json.dumps({'status':result['status'],'completed_time_s':result['completed_horizon_s'],
                          'walltime_s':result['total_walltime_seconds'],'evidence':str(json_path)}),flush=True)
    return 0 if result['status']=='passed' else 1


if __name__=='__main__':
    raise SystemExit(main())
