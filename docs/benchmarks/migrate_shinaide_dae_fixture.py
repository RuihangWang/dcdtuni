"""Explicit verification-only v2 -> v3 mock conversion, never an editor migration.

The original cfdproj and v2 input are read-only. The output is a derived schema
fixture, not a replacement project. Removed reservoir values and source hashes
are recorded beside it. No solver is executed.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,default=Path('/tmp/dcdtuni-shinaide-v2.liquidrun'))
    parser.add_argument('--output',type=Path,default=Path(__file__).parent/'fixtures/shinaide-dae-v3-mock.liquidrun')
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[2]
    sys.path.insert(0,str(root/'src'))
    for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):
        os.environ[key]='1'
    os.environ['CUDA_VISIBLE_DEVICES']=''
    if hasattr(os,'sched_getaffinity'):
        os.sched_setaffinity(0,{min(os.sched_getaffinity(0))})
    if hasattr(os,'getpriority'):
        os.nice(max(0,10-os.getpriority(os.PRIO_PROCESS,0)))
    import torch
    torch.set_num_threads(1);torch.set_num_interop_threads(1)
    from backend.liquidcooling.liquidrun_schema import parse_liquidrun
    from backend.liquidcooling.liquidrun_dae_schema import load_dae_input
    original=json.loads(args.input.read_text())
    parse_liquidrun(original)
    if original['version']!='2.0' or original.get('controllers'):
        raise ValueError('this explicit fixture migration accepts v2 with no PID controllers only')
    raw=deepcopy(original)
    removed={'solver':raw.pop('solver',None),'parameter_provenance':raw.pop('parameter_provenance',None),'reservoir_parameters':[]}
    raw.update(schemaVersion=3,version='3.0',equation_system='incompressible_thermal_dae',
        source='validation-migration:react-dynamic-to-dae-v3',
        analysis={'mode':'transient','initialization':'steady'},
        newton={'max_iterations':40,'atol':1e-10,'rtol':1e-10,'max_backtracks':18})
    for edge in raw['edges']:
        if edge['type']=='heat_exchanger' and 'T_secondary_K' in edge['params']:
            removed['reservoir_parameters'].append({'edge_id':edge['id'],'T_secondary_K':edge['params'].pop('T_secondary_K')})
    pack=raw.get('parameter_pack')
    if pack is not None:
        if pack['source']!='mock':
            raise ValueError('verification conversion must not reinterpret vendor/calibrated HX UA as a mixed-volume model')
        pack['revision']+='-dae-v3-validation'
        pack['description']+=' Derived verification pack: two-volume HX interpretation is a mock assumption, not a counterflow performance equivalence.'
        for profile_id,profile in pack['profiles'].items():
            if profile['type']=='heat_exchanger':
                value=profile['params'].pop('T_secondary_K',None)
                unit=profile['units'].pop('T_secondary_K',None)
                if value is not None:
                    removed['reservoir_parameters'].append({'profile':profile_id,'T_secondary_K':value,'unit':unit})
    raw['primary_circuits']=[{
        'edge_id':edge['id'],'flow_kg_s':1.,'inlet_K':293.15,
        'cp_J_kgK':raw['fluid']['cp_J_kgK'],'rho_kg_m3':raw['fluid']['rho_kg_m3'],
        'primary_volume_m3':.02,'secondary_volume_m3':.02,'source':'mock',
        'source_reference':'Explicit input-contract validation choices, matching the bounded DAE probes; not NV telemetry, vendor data or calibration. The old reservoir field is not treated as a primary circuit.',
    } for edge in raw['edges'] if edge['type']=='heat_exchanger']
    loaded=load_dae_input(raw)
    graph=loaded.equation_graph.graph
    if (graph.N_nodes,graph.N_edges)!=(187,242):
        raise ValueError('input is not the observed Shinaide graph')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(loaded.manifest.model_dump(by_alias=True,mode='json',exclude_none=True),indent=2,ensure_ascii=False)+'\n')
    report={'scope':'derived schema validation fixture only, not the editable engineering source',
        'input_path':str(args.input.resolve()),'input_sha256':hashlib.sha256(args.input.read_bytes()).hexdigest(),
        'output_path':str(args.output.resolve()),'output_sha256':hashlib.sha256(args.output.read_bytes()).hexdigest(),
        'removed_legacy_definitions':removed,'source_project':raw['source_project'],
        'validation':{'nodes':graph.N_nodes,'edges':graph.N_edges,'racks':len(loaded.manifest.racks),
            'heat_exchangers':graph.N_hx,'samples':loaded.sample_count,'dt_s':loaded.dt,
            'initialization':loaded.initialization,'mode':loaded.mode,
            'theta_sample_0_count':loaded.parameters(0).numel(),'solver_executed':False}}
    args.output.with_suffix('.migration.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps(report['validation']),flush=True)


if __name__=='__main__':main()
