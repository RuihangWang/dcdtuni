"""Focused ownership regression and optional reproducible CPU profiling probe."""
import json
import os
import time
from pathlib import Path

import pytest
import torch

from backend.liquidcooling.runtime import LiquidSimulation
from backend.liquidcooling.schema import LiquidrunConfig


def config(samples=121):
    raw=json.loads(Path(__file__).with_name('cdu_loop.liquidrun').read_text())
    raw['time'].update(N_t=samples,dt_s=1.)
    raw['nodes'][0]['bc'].pop('T_K')
    raw['nodes'][-1]={'id':'return','type':'junction','position_m':[2,0,0]}
    raw['edges'].append({'id':'pump','type':'pump','from':'return','to':'supply','params':{'H0_m':10.,'a_coef':500000.,'rpm_ratio':[1. if i<40 or i>=80 else .65 for i in range(samples)]}})
    return LiquidrunConfig.model_validate(raw)


def test_step_control_slice_does_not_modify_other_steps_or_static_inputs():
    sim=LiquidSimulation(config())
    original=sim._graph.pump_params.rpm_ratio.clone()
    changed=sim._step_graph(4,{('pump','rpm_ratio'):.31})
    assert changed.pump_params.rpm_ratio[0,0].item()==pytest.approx(.31)
    assert torch.equal(sim._graph.pump_params.rpm_ratio,original)
    other=sim._step_graph(5,{})
    assert other.pump_params.rpm_ratio[0,0].item()==pytest.approx(1.)
    assert sim._graph.N_t==121 and changed.N_t==1


@pytest.mark.skipif(not os.getenv('LIQUID_BENCHMARK_OUT'),reason='opt-in timing probe')
def test_profile_transient_runtime():
    import backend.liquidcooling.runtime as module
    # Warm imports/BLAS before timing; no wall-clock pass/fail threshold.
    warm=LiquidSimulation(config(3));warm.step()
    data={'threads':torch.get_num_threads(),'samples':121,'repeats':[]}
    for _ in range(3):
        sim=LiquidSimulation(config())
        stages={'graph_slice':0.,'hydraulic':0.,'thermal':0.,'frame':0.}
        def wrapped(name,fn):
            def call(*a,**kw):
                start=time.perf_counter()
                try:return fn(*a,**kw)
                finally:stages[name]+=time.perf_counter()-start
            return call
        sim._step_graph=wrapped('graph_slice',sim._step_graph)
        sim._solve_hydraulic=wrapped('hydraulic',sim._solve_hydraulic)
        sim._frame=wrapped('frame',sim._frame)
        thermal=module.thermal_step;module.thermal_step=wrapped('thermal',thermal)
        start=time.perf_counter()
        try:
            while not sim.done:sim.step()
        finally:module.thermal_step=thermal
        stages['total']=time.perf_counter()-start
        stages['final_chip_K']=sim.latest_frame['chip_temp_K']
        assert sim.latest_frame['seq']==120
        data['repeats'].append(stages)
    Path(os.environ['LIQUID_BENCHMARK_OUT']).write_text(json.dumps(data,indent=2))
    print(json.dumps(data))
