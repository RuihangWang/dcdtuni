"""Strict v3 DAE input contract; no numerical time integration in these tests."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest
import torch

from backend.tests.test_liquid.test_liquidrun_v2 import manifest as v2_manifest


def api():
    name='backend.liquidcooling.liquidrun_dae_schema'
    assert importlib.util.find_spec(name), 'DAE runs require a distinct strict v3 manifest'
    return __import__(name,fromlist=['LiquidrunDAEV3'])


def manifest():
    raw=v2_manifest()
    raw.update(schemaVersion=3,version='3.0',equation_system='incompressible_thermal_dae',
               analysis={'mode':'transient','initialization':'steady'},
               newton={'max_iterations':40,'atol':1e-10,'rtol':1e-10,'max_backtracks':18},
               controllers=[],primary_circuits=[{
                   'edge_id':'cdu_hx','flow_kg_s':[1.,.9,.8,.7],'inlet_K':[290.,291.,292.,293.],
                   'cp_J_kgK':4000.,'rho_kg_m3':1000.,'primary_volume_m3':.02,'secondary_volume_m3':.02,
                   'source':'mock','source_reference':'independent schema test; not site data',
               }])
    raw.pop('solver')
    for edge in raw['edges']:
        if edge['type']=='heat_exchanger':edge['params'].pop('T_secondary_K')
    return raw


def test_self_contained_v3_loader_preserves_public_manifest_and_parameter_gradients():
    raw=manifest();before=copy.deepcopy(raw)
    loaded=api().load_dae_input(raw)
    assert loaded.mode=='transient' and loaded.initialization=='steady'
    assert loaded.dt==1 and loaded.sample_count==4
    assert loaded.newton_options.max_iterations==40
    saved=loaded.manifest.model_dump(by_alias=True,mode='json',exclude_none=True)
    assert saved['schemaVersion']==3 and 'solver' not in saved
    assert 'T_secondary_K' not in saved['edges'][1]['params']
    assert raw==before
    alpha=torch.tensor(1.2,dtype=torch.float64,requires_grad=True)
    theta=loaded.parameters(3,heat_multiplier=alpha)
    layout=loaded.equation_graph.parameter_layout
    assert theta[layout['primary_inlet_K']].tolist()==[293.]
    assert theta[layout['primary_flow_kg_s']].tolist()==[.7]
    assert theta[layout['heat_W']].tolist()==pytest.approx([1080.])  # t=3 Q=900 W event, then x1.2
    derivative=torch.autograd.grad(theta[layout['heat_W']].sum(),alpha)[0]
    assert derivative.item()==900.
    assert api().load_dae_input(saved).manifest.model_dump()==loaded.manifest.model_dump()


@pytest.mark.parametrize('mutate,message',[
    (lambda d:d.update(unknown=True),'unknown'),
    (lambda d:d.update(version='2.0'),'version'),
    (lambda d:d.update(solver={'optimizer':'adam'}),'solver'),
    (lambda d:d['newton'].update(lr=.01),'lr'),
    (lambda d:d['newton'].update(atol=0.),'atol'),
    (lambda d:d['units'].update(temperature='C'),'temperature'),
    (lambda d:d['primary_circuits'][0].update(edge_id='pump'),'heat.exchanger|HX'),
    (lambda d:d['primary_circuits'].clear(),'every|missing|cover'),
    (lambda d:d['primary_circuits'].append(copy.deepcopy(d['primary_circuits'][0])),'unique|duplicate'),
    (lambda d:d['primary_circuits'][0].update(flow_kg_s=[1.,2.]),'N_t'),
    (lambda d:d['primary_circuits'][0].update(inlet_K=[290.,291.]),'N_t'),
    (lambda d:d['primary_circuits'][0].update(flow_kg_s=True),'flow_kg_s'),
    (lambda d:d['primary_circuits'][0].update(primary_volume_m3=0.),'primary_volume_m3'),
    (lambda d:d['primary_circuits'][0].update(source_reference=''),'source_reference'),
    (lambda d:d['edges'][1]['params'].update(T_secondary_K=290.),'T_secondary_K'),
    (lambda d:d['edges'][0].update(to='absent'),'node'),
    (lambda d:d['events'][0].update(time_s=2.5),'grid'),
    (lambda d:d['analysis'].update(mode='steady'),'N_t|steady'),
    # A sampled, slew-limited v2 PID is refused by the DAE's PI contract.
    (lambda d:d['controllers'].append(v2_manifest()['controllers'][0]),'actuator slew limit'),
])
def test_invalid_v3_inputs_are_rejected_before_graph_allocation(mutate,message):
    raw=manifest();mutate(raw)
    with pytest.raises(ValueError,match=message):api().LiquidrunDAEV3.model_validate(raw)


def test_v3_profile_resolution_preserves_sources_but_rejects_legacy_reservoir_parameters():
    raw=manifest()
    import backend.liquidcooling
    pack=json.loads((Path(backend.liquidcooling.__file__).parent/'data/mock-demo-v1.json').read_text())
    hx=pack['profiles']['hx-demo']
    hx['params'].pop('T_secondary_K');hx['units'].pop('T_secondary_K')
    raw['parameter_pack']=pack
    raw['edges'][1].update(params={},parameter_ref='hx-demo')
    loaded=api().load_dae_input(raw)
    assert loaded.equation_graph.graph.hx_params.UA.tolist()==[2000.]
    assert loaded.manifest.parameter_pack.source=='mock'
    raw['parameter_pack']['profiles']['hx-demo']['params']['T_secondary_K']=290.
    raw['parameter_pack']['profiles']['hx-demo']['units']['T_secondary_K']='K'
    with pytest.raises(ValueError,match='T_secondary_K'):api().LiquidrunDAEV3.model_validate(raw)


def test_grid_aligned_decimal_event_uses_its_integer_sample_not_floor_roundoff():
    raw=manifest()
    raw['time']['dt_s']=.1
    raw['events'][0]['time_s']=.3  # .3/.1 is just below 3 in binary floating point
    loaded=api().load_dae_input(raw)
    heat=loaded.equation_graph.parameter_layout['heat_W']
    assert loaded.parameters(2)[heat].tolist()==[500.]
    assert loaded.parameters(3)[heat].tolist()==[900.]


def test_single_sample_steady_manifest_and_v1_v2_loader_boundaries():
    raw=manifest()
    raw['analysis']={'mode':'steady','initialization':'steady'}
    raw['time']['N_t']=1;raw['events']=[]
    raw['edges'][-1]['params']['rpm_ratio']=1.
    raw['primary_circuits'][0].update(flow_kg_s=1.,inlet_K=290.)
    assert api().load_dae_input(raw).sample_count==1
    old=v2_manifest()
    with pytest.raises(ValueError):api().load_dae_input(old)
    from backend.liquidcooling.liquidrun_schema import parse_liquidrun
    assert parse_liquidrun(old).version=='2.0'
    with pytest.raises(ValueError):parse_liquidrun(raw)


def test_json_schema_is_generated_from_v3_and_has_no_adam_or_reservoir_contract():
    module=api()
    path=Path(module.__file__).with_name('liquidrun_dae.schema.json')
    schema=json.loads(path.read_text())
    assert schema==module.dae_json_schema()
    assert schema['additionalProperties'] is False
    assert schema['properties']['schemaVersion']['const']==3
    assert 'solver' not in schema['properties']
    assert 'T_secondary_K' not in schema['$defs']['TwoVolumeHXParams']['properties']


def test_fixed_valve_admission_rejects_known_closure_before_loading_but_allows_positive_adjustment():
    raw=manifest()
    raw['edges'].append({'id':'adjustable','type':'valve','from':'supply','to':'return',
        'params':{'D_m':.025,'Kv_max':1.5,'opening':[1.,.8,.7,.5],'characteristic':'linear'}})
    loaded=api().load_dae_input(raw)
    opening=loaded.equation_graph.parameter_layout['valve_opening']
    assert loaded.parameters(3)[opening].tolist()==[.5]
    scheduled=copy.deepcopy(raw)
    scheduled['edges'][-1]['params']['opening'][2]=0.
    with pytest.raises(ValueError,match='fixed valve active set'):
        api().LiquidrunDAEV3.model_validate(scheduled)
    event=copy.deepcopy(raw)
    event['events'].append({'time_s':3.,'target':'adjustable','field':'opening','value':0.})
    with pytest.raises(ValueError,match='fixed valve active set'):
        api().LiquidrunDAEV3.model_validate(event)


def test_derived_real_shinaide_fixture_loads_without_legacy_reservoir_or_solver_settings():
    import backend.liquidcooling
    root=Path(backend.liquidcooling.__file__).resolve().parents[3]
    path=root/'docs/benchmarks/fixtures/shinaide-dae-v3-mock.liquidrun'
    raw=json.loads(path.read_text())
    loaded=api().load_dae_input(raw)
    assert (loaded.equation_graph.graph.N_nodes,loaded.equation_graph.graph.N_edges)==(187,242)
    assert len(loaded.manifest.racks)==14
    assert loaded.sample_count==11 and len(loaded.manifest.events)==56
    assert len(loaded.manifest.primary_circuits)==1
    assert loaded.manifest.primary_circuits[0].source=='mock'
    assert 'T_secondary_K' not in json.dumps(raw)
    assert 'solver' not in raw
    heat=loaded.equation_graph.parameter_layout['heat_W']
    assert loaded.parameters(0)[heat].sum().item()==21000.
    assert loaded.parameters(5)[heat].sum().item()==29400.


def test_execution_policy_is_distinct_from_legacy_cpu_only_validation():
    raw=manifest()
    raw['execution'].update(device='cuda:1',dtype='float64',max_threads=4)
    # Validation only: this test must not initialize or allocate on a GPU.
    parsed=api().LiquidrunDAEV3.model_validate(raw)
    assert parsed.execution.device=='cuda:1' and parsed.execution.max_threads==4
    for key,value in [('device','unknown'),('max_threads',0),('dtype','float16')]:
        bad=copy.deepcopy(raw);bad['execution'][key]=value
        with pytest.raises(ValueError):api().LiquidrunDAEV3.model_validate(bad)


def test_cpu_float32_loader_moves_all_float_fields_but_not_topology_indices():
    raw=manifest();raw['execution'].update(dtype='float32',max_threads=2)
    loaded=api().load_dae_input(raw)
    graph=loaded.equation_graph.graph
    assert graph.bc_P.dtype==graph.cp_params.Q_W.dtype==torch.float32
    assert graph.edge_from.dtype==torch.int64 and graph.bc_mask.dtype==torch.bool
    assert loaded.parameters(0).dtype==loaded.equation_graph.state_scale.dtype==torch.float32
