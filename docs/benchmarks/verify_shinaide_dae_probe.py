"""Bounded CPU numerical probe of the actual Shinaide equation graph.

This is numerical evidence, not a schema/runtime/UI integration or a calibrated
NV model. The immutable exported fixture supplies the topology and initial heat
loads; the probe explicitly replaces its event schedule by a 1 -> 1.2 heat step.
"""
from __future__ import annotations

import argparse
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture',type=Path,default=Path('/tmp/dcdtuni-shinaide-v2.liquidrun'))
    parser.add_argument('--output-prefix',type=Path,default=Path(__file__).with_name('shinaide-dae-probe-2026-09-26'))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0,str(root/'src'))
    for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):
        os.environ[key]='1'
    os.environ['CUDA_VISIBLE_DEVICES']=''
    os.sched_setaffinity(0,{min(os.sched_getaffinity(0))})
    os.nice(max(0,10-os.getpriority(os.PRIO_PROCESS,0)))
    started = time.perf_counter()
    evidence = {'status':'running','scope':'numerical probe only; no calibrated NV primary circuit or schema/runtime/UI integration',
        'cpu_affinity':sorted(os.sched_getaffinity(0)),'nice':os.getpriority(os.PRIO_PROCESS,0),
        'pid':os.getpid(),'budget_seconds':90,'walltime_seconds':{},'accepted_states':[],
        'fixture_path':str(args.fixture.resolve()),'script_path':str(Path(__file__).resolve())}
    output = args.output_prefix.with_suffix('.json')
    output.parent.mkdir(parents=True,exist_ok=True)

    def persist():
        evidence['total_walltime_seconds']=time.perf_counter()-started
        output.write_text(json.dumps(evidence,ensure_ascii=False,indent=2,allow_nan=False)+'\n')

    def timeout(_sig,_frame):
        raise TimeoutError('90-second total numerical probe deadline exhausted')
    signal.signal(signal.SIGALRM,timeout)
    signal.setitimer(signal.ITIMER_REAL,90)
    print(json.dumps({'pid':evidence['pid'],'affinity':evidence['cpu_affinity'],'budget_seconds':90}),flush=True)

    def stage(name,function):
        evidence['current_stage']=name
        persist()
        begin=time.perf_counter()
        print('START '+name,flush=True)
        result=function()
        evidence['walltime_seconds'][name]=time.perf_counter()-begin
        print('DONE '+name+f" {evidence['walltime_seconds'][name]:.4f}s",flush=True)
        persist()
        return result

    def sha(path):
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
        evidence['torch']={'version':torch.__version__,'threads':torch.get_num_threads(),'interop_threads':torch.get_num_interop_threads()}
        evidence['git_head']=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()
        evidence['fixture_sha256']=sha(args.fixture)
        evidence['script_sha256']=sha(__file__)
        evidence['source_sha256']={name:sha(root/name) for name in (
            'src/backend/liquidcooling/liquidrun_schema.py','src/backend/liquidcooling/graphcompiling/compiler.py',
            'src/backend/liquidcooling/solver/equation_graph.py','src/backend/liquidcooling/solver/dae_newton.py',
            'src/backend/liquidcooling/solver/differentiable_hydraulic.py')}
        raw=json.loads(args.fixture.read_text())
        config=stage('schema_parse',lambda:parse_liquidrun(raw))
        graph=stage('compile_graph',lambda:compile_graph(config,device='cpu'))
        if (graph.N_nodes,graph.N_edges)!=(187,242):
            raise ValueError('requires the actual 187-node / 242-edge Shinaide fixture')
        vector=lambda value:torch.full((graph.N_hx,),float(value),dtype=torch.float64)
        primary=PrimaryCircuit(flow_kg_s=vector(1),inlet_K=vector(293.15),
            cp_J_kgK=vector(graph.fluid_cp),rho_kg_m3=vector(graph.fluid_rho),
            primary_volume_m3=vector(.02),secondary_volume_m3=vector(.02),
            source='mock:explicit DAE probe values; not NV measurements, vendor curves, or calibration')
        evidence['primary_circuit']={key:(value.tolist() if isinstance(value,torch.Tensor) else value)
            for key,value in asdict(primary).items()}
        eq=stage('equation_graph',lambda:EquationGraph(graph,primary))
        evidence['graph']={'nodes':graph.N_nodes,'edges':graph.N_edges,'pipe_cells':graph.N_cells,
            'cold_plates':graph.N_cp,'heat_exchangers':graph.N_hx,'dae_variables':eq.size,
            'differential_variables':int(eq.differential_mask.sum()),
            'neutral_storage_variables':int(eq.steady_neutral_storage_mask.sum())}
        evidence['solver_options']=asdict(NewtonOptions())
        baseline=graph.cp_params.Q_W[:,0].detach().clone()
        theta=eq.parameters(time_index=0,heat_W=baseline)
        evidence['forcing']={'baseline_total_heat_W':float(baseline.sum()),'step_multiplier':1.2,
            'dt_s':5,'step_times_s':[5,10,15],
            'original_fixture_event_count':len(raw['events']),
            'event_policy':'use initial fixture heat at time_index=0; replace original schedules with explicit 1 -> 1.2 heat step for this probe only'}
        guess=eq.initial_guess(theta)
        P,m=stage('hydraulic_warm_start_only',lambda:hydraulic_step(time_slice(graph,0)))
        guess[eq.state_layout['pressure_free']]=P[~graph.bc_mask]
        guess[eq.state_layout['flow']]=m
        reference=guess.detach().clone()

        def sample_state(y,parameters,when,mode,report,previous=None,dt=None):
            values=eq.unpack(y,parameters)
            differential=torch.zeros_like(y) if previous is None else torch.where(eq.differential_mask,(y-previous)/dt,torch.zeros_like(y))
            residual=eq.residual(y.new_tensor(when),y,differential,parameters)
            params={name:parameters[sl] for name,sl in eq.parameter_layout.items()}
            rates={name:differential[sl] for name,sl in eq.state_layout.items()}
            storage=(params['fluid_rho']*params['fluid_cp']*params['cell_volume']*rates['water_temperature']).sum()
            storage=storage+(params['cp_Csolid']*rates['solid_temperature']).sum()
            storage=storage+(params['fluid_rho']*params['fluid_cp']*params['secondary_volume']*rates['hx_secondary_temperature']).sum()
            storage=storage+(params['primary_rho']*params['primary_cp']*params['primary_volume']*rates['primary_return_temperature']).sum()
            source=params['heat_W'].sum()+params['cell_heat_W'].sum()
            rejected=(params['primary_flow_kg_s']*params['primary_cp']*(values['primary_return_temperature']-params['primary_inlet_K'])).sum()
            balance=source-storage-rejected
            def maximum(v):return float(v.detach().abs().max()) if v.numel() else 0.
            all_finite=bool(torch.isfinite(y).all() and torch.isfinite(residual).all())
            result={'mode':mode,'time_s':when,'newton':asdict(report),'all_finite':all_finite,
                'primary_return_K':values['primary_return_temperature'].detach().tolist(),
                'secondary_supply_K':values['hx_secondary_temperature'].detach().tolist(),
                'max_solid_K':float(values['solid_temperature'].detach().max()),
                'mass_residual_inf_kg_s':maximum(residual[eq.state_layout['pressure_free']]),
                'momentum_residual_inf_Pa':maximum(residual[eq.state_layout['flow']]),
                'original_scaled_residual_inf':maximum(residual/eq.problem.residual_scale),
                'heat_source_W':float(source.detach()),'storage_rate_W':float(storage.detach()),
                'primary_heat_rejection_W':float(rejected.detach()),'global_energy_balance_W':float(balance.detach()),
                'observable_node_temperatures':int(values['node_temperature_observable'].sum()),
                'conserved_blind_storage_max_change_K':maximum((y-reference)[eq.steady_neutral_storage_mask])}
            if not all_finite:
                raise RuntimeError('accepted DAE state contains nonfinite physics')
            if float(balance.detach().abs()) > 1e-8*max(1.,abs(float(source.detach()))):
                raise RuntimeError(f'accepted DAE energy balance failed: {float(balance.detach())} W')
            evidence['accepted_states'].append(result)
            persist()

        steady,report=stage('unified_steady',lambda:solve_steady(eq.problem,guess,theta,
            conserved=(eq.steady_neutral_storage_mask,reference)))
        sample_state(steady,theta,0.,'steady',report)
        theta_step=eq.parameters(time_index=0,heat_W=1.2*baseline)
        previous=steady
        for index in range(1,4):
            when=5.*index
            current,report=stage(f'backward_euler_step_{index}',lambda:backward_euler_step(
                eq.problem,previous,theta_step,time=when,dt=5.))
            sample_state(current,theta_step,when,'transient',report,previous,5.)
            previous=current
        evidence['status']='passed'
    except BaseException as error:
        evidence['status']='timeout' if isinstance(error,TimeoutError) else 'failed'
        evidence['error']={'type':type(error).__name__,'message':str(error),'traceback':traceback.format_exc()}
        print(traceback.format_exc(),flush=True)
    finally:
        signal.setitimer(signal.ITIMER_REAL,0)
        persist()
        lines=['# Shinaide 新 DAE 单核数值 probe（2026-09-26）','',
            f"状态：**{evidence['status']}**；总耗时 {evidence['total_walltime_seconds']:.3f} s；90 s 总预算。",'',
            '仅数值 probe：不是 NV 校准、独立物性确认，也不代表 schema/runtime/UI/mock 平台已接入。','',
            'CPU 单核 affinity、PyTorch/BLAS 单线程、nice 10、CUDA 禁用；不涉及 NSCC。','',
            f'输入：`{args.fixture}`，SHA-256 与关键实现 SHA-256 见同名 JSON。','',
            '一次侧 mock：每 HX 流量 1 kg/s、入口 293.15 K；cp/rho 取图中介质；一次/二次混合容积各 0.02 m³。所有值明确非 NV 标定。','',
            '水力旧解仅用于 pressure/flow 初猜；最终稳态与三个 5 s 瞬态步均由同一连续 F 的 Newton 求根完成。稳态保留盲端初始储能，求解后仍检验原始 F。', '',
            '本 probe 使用初始热负载；稳态后施加 1.2 倍热负载，替代该派生输入原有事件，不修改原工程或输入文件。','',
            '复现脚本：[verify_shinaide_dae_probe.py](verify_shinaide_dae_probe.py)。在 dcdtuni 环境执行：','',
            '```sh',f'python docs/benchmarks/verify_shinaide_dae_probe.py --fixture {args.fixture}','```','',
            '已接受状态：','']
        for record in evidence['accepted_states']:
            lines += [f"- {record['mode']} t={record['time_s']:g} s：一次回水 {record['primary_return_K']} K；二次供水 {record['secondary_supply_K']} K；质量残差 {record['mass_residual_inf_kg_s']:.3g} kg/s；能量残差 {record['global_energy_balance_W']:.3g} W。"]
        lines += ['','阶段耗时：','']
        lines += [f'- {key}: {value:.4f} s。' for key,value in evidence['walltime_seconds'].items()]
        if 'error' in evidence:
            lines += ['',f"停止于 {evidence.get('current_stage')}：{evidence['error']['type']} — {evidence['error']['message']}。未完成阶段不计为通过。"]
        args.output_prefix.with_suffix('.md').write_text('\n'.join(lines)+'\n')
        print(json.dumps({'status':evidence['status'],'elapsed_s':evidence['total_walltime_seconds'],'evidence':str(output)}),flush=True)
    return 0 if evidence['status']=='passed' else 1


if __name__=='__main__':
    raise SystemExit(main())
