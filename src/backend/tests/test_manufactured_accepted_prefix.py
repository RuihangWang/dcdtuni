"""Real React MMS input must survive accepted-prefix export and runtime API."""
import io,json,copy
from pathlib import Path
import pytest
from backend.liquidcooling.dae_runtime import DAESimulation
from backend.liquidcooling.accepted_run_input import build_accepted_manifest
from backend.liquidcooling.liquidrun_dae_schema import load_dae_input
from backend.liquidcooling.manufactured_benchmark import canonical_digest
from backend.liquidcooling.run_manager import LiquidRunManager

INPUT=Path(__file__).parent/'fixtures/manufactured_ring_actual_20261006.liquidrun'

def raw():return json.loads(INPUT.read_text())

@pytest.mark.parametrize('count',[1,2,17])
def test_manufactured_accepted_prefix_has_exact_unchanged_source_samples(count):
    original=raw();before=copy.deepcopy(original)
    sim=DAESimulation(original)
    prefix=build_accepted_manifest(sim.definition,[1.0]*count)
    benchmark=prefix['manufactured_benchmark'];full=original['manufactured_benchmark']
    assert prefix['time']['N_t']==count
    assert benchmark['times_s']==full['times_s'][:count]
    for name,field in full['sources'].items():
        assert benchmark['sources'][name]['ids']==field['ids']
        assert benchmark['sources'][name]['values']==[row[:count]for row in field['values']]
    for key in ['initial_state','initial_temperature_rate','flow_signs','graph_layout_sha256','source_reference']:
        assert benchmark[key]==full[key]
    body=copy.deepcopy(benchmark);digest=body.pop('source_sha256')
    assert canonical_digest(body)==digest
    if count==17:assert digest==full['source_sha256']
    else:assert digest!=full['source_sha256']
    admitted=load_dae_input(prefix)
    assert admitted.sample_count==count
    assert original==before
    assert sim.checkpoint()['liquidrun']==before

class Handler:
    def __init__(self,body):
        blob=json.dumps(body).encode();self.headers={'Content-Length':str(len(blob))}
        self.rfile=io.BytesIO(blob);self.wfile=io.BytesIO();self.status=None
    def send_response(self,status):self.status=status
    def send_header(self,*_):pass
    def end_headers(self):pass
    @property
    def response(self):return json.loads(self.wfile.getvalue())

def test_real_liquid_create_step_prefix_and_checkpoint_api(tmp_path,monkeypatch):
    import sys
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]/'server'))
    import liquid_runtime_api as api
    manager=LiquidRunManager();monkeypatch.setattr(api,'_manager',lambda:manager)
    original=raw();h=Handler({'liquidrun':original,'project_revision':5})
    api.handle_post(h,'/api/liquid/runs',case_dir=tmp_path)
    assert h.status==201,h.response
    rid=h.response['run_id'];assert h.response['status']=='ready'
    first=manager.executed_input(rid);assert first['time']['N_t']==1
    assert first['manufactured_benchmark']['times_s']==[0.0]
    h=Handler({});api.handle_post(h,f'/api/liquid/runs/{rid}/step',case_dir=tmp_path)
    assert h.status==200,h.response
    prefix=manager.executed_input(rid);assert prefix['time']['N_t']==2
    assert prefix['manufactured_benchmark']['times_s']==[0.0,0.5]
    persisted=tmp_path/'Liquid/log'/('run-'+rid)
    assert json.loads((persisted/'input.liquidrun').read_text())==original
    assert json.loads((persisted/'executed.liquidrun').read_text())==prefix
    receipt=json.loads((persisted/'executed-inputs.json').read_text())
    assert receipt['accepted_sample_count']==2 and receipt['original_sample_count']==17
    assert receipt['accepted_time_s']==0.5 and not receipt['complete_original_horizon']
    checkpoint=manager.checkpoint(rid)
    replay=DAESimulation.from_checkpoint(original,checkpoint)
    expected=manager.frames(rid)['frames'];actual=replay.frames
    assert len(expected)==len(actual)==2
    assert expected==actual
    short=DAESimulation(prefix)
    short.step()
    for a,b in zip(short.frames,expected):
        for key in ['node_pressure_Pa','node_temperature_K','edge_flow_kg_s','chip_temp_K','cell_temperature_K','primary_return_K','secondary_supply_K']:
            assert a[key]==b[key]
