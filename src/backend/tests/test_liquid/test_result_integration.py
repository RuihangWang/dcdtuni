import os
from types import SimpleNamespace

import numpy as np
import torch

from backend.liquidcooling.graphcompiling.compiled_graph import RackMeta
from backend.liquidcooling.postprocess.summaries import build_rack_summaries
from backend.tests.test_liquid.test_liquid_runtime_api import http_server  # shared real HTTP fixture
from backend.tests.test_liquid.test_runtime import runtime_config
from backend.tests.test_liquid.test_liquidrun_v2 import manifest
from backend.liquidcooling.schema import LiquidrunConfig
from backend.liquidcooling.runtime import LiquidSimulation
import json


def test_runtime_artifacts_are_readable_and_listed_by_existing_postprocess_api(http_server):
    cfg = runtime_config(samples=3).model_dump(by_alias=True)
    code, run = http_server.request('POST','/api/liquid/runs',{'liquidrun':cfg})
    assert code == 201
    run_id = run['run_id']
    http_server.request('POST',f'/api/liquid/runs/{run_id}/step',{})
    code, result = http_server.request('GET','/api/liquid/results?run=run-'+run_id)
    assert code == 200 and result['run_id'] == run_id
    assert len(result['time_s']) == 2
    code, status = http_server.request('GET','/api/scan-case-status')
    assert code == 200
    assert any(r['name']=='run-'+run_id for r in status['liquidRuns'])
    assert status['liquidReady'] is True


def test_latest_result_uses_file_time_and_keeps_legacy_files(http_server):
    root = http_server.module.CASE_DIR/'Liquid'/'log'
    older=root/'run-zzzz';newer=root/'run-aaaa';legacy=root/'2025-01-01_00-00-00'
    for folder in (older,newer,legacy):folder.mkdir(parents=True)
    (older/'result.json').write_text('{"run_id":"old"}')
    (newer/'result.json').write_text('{"run_id":"new"}')
    (legacy/'liquid_result.json').write_text('{"run_id":"legacy"}')
    os.utime(older/'result.json',(100,100));os.utime(newer/'result.json',(300,300));os.utime(legacy/'liquid_result.json',(200,200))
    assert http_server.request('GET','/api/liquid/results')[1]['run_id']=='new'
    assert http_server.request('GET','/api/liquid/results?run='+str(legacy))[1]['run_id']=='legacy'
    assert http_server.request('GET','/api/liquid/results?run=../../elsewhere')[0]==404


def test_series_plates_have_one_branch_flow_and_actual_manifold_temperatures():
    g=SimpleNamespace(N_t=99,T_fluid_init=293.15,fluid_cp=4182.,
      node_ids=['rack_mf_supply','middle','rack_mf_return'],edge_str_ids=['a','b'],
      edge_from=torch.tensor([0,1]),edge_to=torch.tensor([1,2]),
      cp_indices=SimpleNamespace(edge_ids=torch.tensor([0,1])),
      rack_meta=[RackMeta('rack',['server'],[['a','b']])])
    summaries=build_rack_summaries(g,{'m':np.array([[.1,-.2],[.1,-.2]])},
      np.array([[310.,322.],[315.,320.]]),np.array([[350.,351.],[360.,352.]]),
      np.array([[300.,322.],[310.,320.],[315.,300.]]))
    rack=summaries[0];server=rack.servers[0]
    np.testing.assert_allclose(server.flow_kg_s,[.1,.2])
    np.testing.assert_allclose(rack.total_flow_kg_s,[.1,.2])
    np.testing.assert_allclose(rack.return_temp_K,[315.,300.])
    np.testing.assert_allclose(server.heat_removed_W,[.1*4182*15,.2*4182*22])
    np.testing.assert_allclose(server.max_chip_temp_K,[360.,352.])
    assert len(rack.total_flow_kg_s)==2


def test_direct_runtime_validates_v2_and_uses_embedded_controller():
    raw=manifest()
    sim=LiquidSimulation(LiquidrunConfig.model_validate(raw))
    assert raw['controllers'][0]['id'] in sim.step()['controls']
    raw['units']['temperature']='C'
    import pytest
    with pytest.raises(ValueError):
        LiquidSimulation(LiquidrunConfig.model_validate(raw))


def test_batch_v2_uses_the_same_transient_control_path(tmp_path):
    from backend.liquidcooling.runner import run_liquid_solve
    raw=manifest()
    path=tmp_path/'model'/'test.liquidrun';path.parent.mkdir()
    path.write_text(json.dumps(raw))
    reference=LiquidSimulation(LiquidrunConfig.model_validate(raw),raw['controllers'])
    while not reference.done:reference.step()
    result=run_liquid_solve(path,case_dir=tmp_path,device='cpu')
    np.testing.assert_allclose(result.edge_flow_kg_s,reference.result()['edge_flow_kg_s'])
    artifacts=list((tmp_path/'log').glob('run-*/result.json'))
    assert len(artifacts)==1
    saved=json.loads(artifacts[0].read_text())
    assert saved['controls'] and saved['object_bindings']==raw['object_bindings']


def test_cell_result_identity_uses_pipe_owner_not_an_unrelated_global_edge():
    config=runtime_config(samples=2)
    expected={edge.id for edge in config.edges if edge.type=='pipe'}
    result=LiquidSimulation(config).result()
    assert result['cell_temp_K']
    assert set(result['cell_edge_ids'])==expected
