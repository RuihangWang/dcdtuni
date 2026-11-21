"""Actual product DAE execution observation; CPU controls never start a GPU."""
import copy
import pytest
import torch
from backend.liquidcooling.dae_runtime import DAESimulation
from backend.liquidcooling.liquidrun_dae_schema import load_dae_input
from backend.tests.test_liquid.test_liquidrun_dae_schema import manifest

@pytest.mark.parametrize('dtype',['float64'])
def test_product_runtime_reports_actual_tensor_placement_and_preserves_requested_input(dtype):
    raw=manifest();raw['events']=[]
    raw['execution']={'backend':'pytorch','device':'cpu','dtype':dtype,'max_threads':2}
    if dtype=='float32':raw['newton'].update(atol=1e-5,rtol=1e-5)
    original=copy.deepcopy(raw);threads=torch.get_num_threads()
    try:
        sim=DAESimulation(raw)
        obs=sim.metadata['execution']
        assert obs=={**raw['execution'],'actual_device':'cpu','actual_dtype':dtype,'actual_threads':2}
        assert sim.experiment.tensor_state.device.type=='cpu'
        assert sim.experiment.tensor_state.dtype==getattr(torch,dtype)
        sim.step()
        assert sim.metadata['execution']==obs
        assert raw==original
        assert sim.executed_manifest()['execution']==raw['execution']
    finally:torch.set_num_threads(threads)

def test_actual_observation_does_not_copy_state_or_treat_requested_device_as_proof(monkeypatch):
    raw=manifest();threads=torch.get_num_threads()
    try:
        sim=DAESimulation(raw)
        actual=sim.experiment._state
        # CPU spy trap: metadata must read tensor attributes directly, without copying/converting.
        monkeypatch.setattr(type(sim.experiment),'tensor_state',property(lambda self:(_ for _ in ()).throw(AssertionError('unexpected clone'))))
        sim._liquidrun['execution']['device']='cuda'
        obs=sim.metadata['execution']
        assert obs['device']=='cuda' and obs['actual_device']==str(actual.device)=='cpu'
        assert obs['actual_dtype']==str(actual.dtype).removeprefix('torch.')
    finally:torch.set_num_threads(threads)

def test_unavailable_cuda_is_rejected_instead_of_implicitly_using_cpu(monkeypatch):
    # Allocation boundary rejection sentinel, not a claim of GPU hardware validation.
    raw=manifest();raw['execution']={'backend':'pytorch','device':'cuda:0','dtype':'float64','max_threads':2}
    from backend.liquidcooling.graphcompiling.compiled_graph import CompiledGraph
    original=CompiledGraph.to
    seen=[]
    def refuse(self,device,*args,**kwargs):
        seen.append(str(device))
        if torch.device(device).type=='cuda':raise RuntimeError('controlled unavailable CUDA device')
        return original(self,device,*args,**kwargs)
    monkeypatch.setattr(CompiledGraph,'to',refuse)
    with pytest.raises(RuntimeError,match='controlled unavailable CUDA'):
        load_dae_input(raw)
    assert seen==['cpu','cuda:0']  # Graph parsing precedes the requested CUDA allocation; no later CPU fallback.

def test_float32_loader_preserves_explicit_dtype_without_claiming_product_convergence():
    raw=manifest();raw['execution']={'backend':'pytorch','device':'cpu','dtype':'float32','max_threads':2}
    loaded=load_dae_input(raw)
    assert loaded.equation_graph.graph.bc_P.dtype==torch.float32
    assert loaded.equation_graph.graph.bc_P.device.type=='cpu'
    assert loaded.manifest.execution.dtype=='float32'
