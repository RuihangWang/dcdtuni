"""Accepted online controls become a strict, replayable v3 input prefix."""
from __future__ import annotations

import copy
import importlib.util

import pytest
import torch

from backend.liquidcooling.liquidrun_dae_schema import LiquidrunDAEV3, load_dae_input
from backend.tests.test_liquid.test_liquidrun_dae_schema import manifest


def api():
    name='backend.liquidcooling.accepted_run_input'
    assert importlib.util.find_spec(name), 'accepted controls require a self-contained input exporter'
    return __import__(name,fromlist=['build_accepted_manifest'])


def scheduled_input():
    raw=manifest()
    raw['analysis']['initialization']='consistent'
    raw['edges'][-1]['params']['rpm_ratio']=[1.,.9,.8,.7]
    raw['edges'].append({'id':'branch_valve','type':'valve','from':'supply','to':'return',
        'params':{'D_m':.025,'Kv_max':1.5,'characteristic':'linear','opening':[1.,.9,.8,.7]}})
    raw['events']=[
        {'time_s':1.,'target':'cold_plate_1','field':'Q_W','value':900.},
        {'time_s':2.,'target':'pump','field':'rpm_ratio','value':.6},
        {'time_s':2.,'target':'branch_valve','field':'opening','value':.5},
        {'time_s':3.,'target':'cold_plate_1','field':'Q_W','value':1200.},
    ]
    return raw


def test_plan_events_and_accepted_heat_multipliers_materialize_without_mutating_inputs():
    raw=scheduled_input();original=copy.deepcopy(raw)
    loaded=load_dae_input(raw)
    manifest_before=loaded.manifest.model_dump(mode='json',by_alias=True)
    graph_before=loaded.equation_graph.graph.cp_params.Q_W.clone()
    scales=[1.,1.2,.8,1.5]
    exported=api().build_accepted_manifest(loaded,scales)
    LiquidrunDAEV3.model_validate(exported)
    edges={edge['id']:edge for edge in exported['edges']}
    assert edges['cold_plate_1']['params']['Q_W']==[500.,1080.,720.,1800.]
    assert edges['pump']['params']['rpm_ratio']==[1.,.9,.6,.6]
    assert edges['branch_valve']['params']['opening']==[1.,.9,.5,.5]
    assert exported['primary_circuits']==raw['primary_circuits']
    assert exported['events']==[]
    assert exported['source'].endswith(':accepted-trace')
    for key in ('source_project','nodes','racks','initial_conditions','object_bindings','analysis'):
        assert exported[key]==raw[key]
    restored=load_dae_input(exported)
    for index,scale in enumerate(scales):
        torch.testing.assert_close(restored.parameters(index),loaded.parameters(index,heat_multiplier=scale),rtol=0,atol=0)
    assert raw==original
    assert loaded.manifest.model_dump(mode='json',by_alias=True)==manifest_before
    torch.testing.assert_close(loaded.equation_graph.graph.cp_params.Q_W,graph_before,rtol=0,atol=0)
    assert scales==[1.,1.2,.8,1.5]


def test_partial_prefix_excludes_future_plan_and_any_unsupplied_pending_input():
    loaded=load_dae_input(scheduled_input())
    exported=api().build_accepted_manifest(loaded,[1.,1.2])
    assert exported['time']['N_t']==2
    assert exported['analysis']=={'mode':'transient','initialization':'consistent'}
    assert exported['events']==[]
    assert exported['primary_circuits'][0]['flow_kg_s']==[1.,.9]
    assert exported['primary_circuits'][0]['inlet_K']==[290.,291.]
    edges={edge['id']:edge for edge in exported['edges']}
    assert edges['cold_plate_1']['params']['Q_W']==[500.,1080.]
    assert edges['pump']['params']['rpm_ratio']==[1.,.9]
    assert edges['branch_valve']['params']['opening']==[1.,.9]
    assert len(load_dae_input(exported).parameters(1))==len(loaded.parameters(1))
    with pytest.raises(ValueError,match='index'):
        load_dae_input(exported).parameters(2)


def test_profile_pipe_heat_rows_are_cut_in_time_while_pack_and_per_cell_scalars_stay_intact():
    raw=scheduled_input()
    source=[[10.,20.,30.,40.],50.,[60.,70.,80.,90.]]
    raw['parameter_pack']={'schema_version':'1.0','id':'pipe-source','revision':'r1','source':'mock',
        'description':'Independent mixed spatial/temporal pipe heat source test',
        'profiles':{'heated-pipe':{'type':'pipe','description':'Spatial cell constants and temporal heat sequences','params':{'heat_source_W_per_m':source},
            'units':{'heat_source_W_per_m':'W/m'}}}}
    pipe=next(edge for edge in raw['edges'] if edge['type']=='pipe')
    pipe['parameter_ref']='heated-pipe'
    loaded=load_dae_input(raw)
    exported=api().build_accepted_manifest(loaded,[1.,0.])
    pipe_output=next(edge for edge in exported['edges'] if edge['type']=='pipe')
    assert pipe_output['params']['heat_source_W_per_m']==[[10.,20.],50.,[60.,70.]]
    assert pipe_output['parameter_ref']=='heated-pipe'
    assert exported['parameter_pack']==raw['parameter_pack']
    replay=load_dae_input(exported)
    torch.testing.assert_close(replay.equation_graph.graph.cell_heat_W,
        loaded.equation_graph.graph.cell_heat_W[:,:2],rtol=0,atol=0)
    # A vector is spatial (one constant per cell), not one value per time.
    raw['parameter_pack']['profiles']['heated-pipe']['params']['heat_source_W_per_m']=[10.,50.,60.]
    exported=api().build_accepted_manifest(load_dae_input(raw),[1.])
    pipe_output=next(edge for edge in exported['edges'] if edge['type']=='pipe')
    assert pipe_output['params']['heat_source_W_per_m']==[10.,50.,60.]


@pytest.mark.parametrize('scales',[[],[1.]*5,[.9],[True],[1.,True],[1.,-1.],[1.,float('nan')],[1.,float('inf')],['1'],None,(1.,)])
def test_invalid_accepted_trace_is_rejected(scales):
    loaded=load_dae_input(scheduled_input())
    with pytest.raises((TypeError,ValueError),match='heat_multipliers|first|prefix|finite|nonnegative|sample|list'):
        api().build_accepted_manifest(loaded,scales)


def test_single_sample_steady_input_retains_steady_mode_and_initial_state():
    raw=manifest()
    raw['analysis']={'mode':'steady','initialization':'steady'}
    raw['time']['N_t']=1;raw['events']=[]
    raw['edges'][-1]['params']['rpm_ratio']=1.
    raw['primary_circuits'][0].update(flow_kg_s=1.,inlet_K=290.)
    exported=api().build_accepted_manifest(load_dae_input(raw),[1.])
    assert exported['time']['N_t']==1
    assert exported['analysis']==raw['analysis']
    assert exported['initial_conditions']==raw['initial_conditions']
    LiquidrunDAEV3.model_validate(exported)
