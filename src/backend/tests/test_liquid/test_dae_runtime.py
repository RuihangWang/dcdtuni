"""DAE run lifecycle at the user-facing boundary, including persisted provenance."""
import copy
import json
import pytest
from backend.liquidcooling.run_manager import LiquidRunManager,RunInputError
from backend.tests.test_liquid.test_liquidrun_dae_schema import manifest


def case():
    raw=manifest()
    raw['events']=[]
    raw['primary_circuits'][0].update(flow_kg_s=1.,inlet_K=290.)
    return raw


def test_dae_live_heat_next_step_and_equilibrium_query_persist_actual_solved_fields(tmp_path):
    manager=LiquidRunManager();created=manager.create(case(),case_dir=tmp_path)
    run_id=created['run_id'];initial=copy.deepcopy(created['frame'])
    assert created['metadata']['solver']=='differentiable_newton_dae'
    queued=manager.set_inputs(run_id,heat_multiplier=2.)
    assert queued['frame']==initial
    changed=manager.operate(run_id,'step')
    assert changed['frame']['heat_multiplier']==2.
    assert changed['frame']['primary_return_K'][0]>initial['primary_return_K'][0]
    query=manager.equilibrium(run_id,heat_multiplier=2.)
    assert query['kind']=='equilibrium' and 'time_s' not in query
    assert manager.get(run_id)['frame']==changed['frame']
    result=manager.result(run_id)
    assert len(result['time_s'])==2 and len(result['primary_return_K'][0])==2
    assert abs(result['diagnostics'][-1]['energy_balance_W'])<1e-6
    saved=tmp_path/'Liquid'/'log'/f'run-{run_id}'
    assert json.loads((saved/'input.liquidrun').read_text())['schemaVersion']==3
    assert json.loads((saved/'checkpoint.json').read_text())['applied_inputs'][0]['seq']==1
    assert json.loads((saved/'result.json').read_text())['primary_return_K']==result['primary_return_K']


def test_dae_checkpoint_replay_matches_uninterrupted_history_and_pending_command(tmp_path):
    manager=LiquidRunManager();run_id=manager.create(case(),case_dir=tmp_path/'a')['run_id']
    manager.set_inputs(run_id,heat_multiplier=1.2);manager.operate(run_id,'step')
    manager.set_inputs(run_id,heat_multiplier=.8)
    checkpoint=manager.checkpoint(run_id)
    resumed=LiquidRunManager();created=resumed.create(case(),case_dir=tmp_path/'b',checkpoint=checkpoint)
    copy_id=created['run_id']
    # A resume is a new run that continues the checkpointed one.
    assert copy_id!=run_id and created['metadata']['resumed_from']==run_id
    expected=manager.operate(run_id,'step')['frame']
    actual=resumed.operate(copy_id,'step')['frame']
    assert {**actual,'run_id':None}=={**expected,'run_id':None}
    assert resumed.result(copy_id)['time_s']==manager.result(run_id)['time_s']
    broken=copy.deepcopy(checkpoint);broken['config_hash']='bad'
    with pytest.raises(RunInputError,match='hash'):
        LiquidRunManager().create(case(),case_dir=tmp_path/'c',checkpoint=broken)


def test_direct_steady_and_invalid_inputs_have_no_fake_time_trace(tmp_path):
    raw=case();raw['analysis']={'mode':'steady','initialization':'steady'}
    raw['time']['N_t']=1;raw['edges'][-1]['params']['rpm_ratio']=1.
    manager=LiquidRunManager();created=manager.create(raw,case_dir=tmp_path)
    assert created['status']=='completed'
    assert manager.result(created['run_id'])['time_s']==[0.]
    assert abs(created['frame']['diagnostics']['energy_balance_W'])<1e-6
    with pytest.raises((RunInputError,ValueError),match='finite|nonnegative|scalar|number'):
        manager.set_inputs(created['run_id'],heat_multiplier=float('nan'))


def test_standalone_dae_file_uses_the_same_solver_and_keeps_primary_results(tmp_path):
    from backend.liquidcooling.runner import run_liquid_solve
    raw=case();path=tmp_path/'input.liquidrun';path.write_text(json.dumps(raw))
    result=run_liquid_solve(path,case_dir=tmp_path)
    assert result.converged
    assert result.primary_return_K.shape==(1,raw['time']['N_t'])
    replay=tmp_path/'roundtrip.json';result.save_json(replay)
    assert json.loads(replay.read_text())['primary_return_K']==result.primary_return_K.tolist()
    files=list((tmp_path/'log').glob('run-*/result.json'))
    assert len(files)==1
    saved=json.loads(files[0].read_text())
    assert saved['solver']=='differentiable_newton_dae'
    assert len(saved['primary_return_K'][0])==raw['time']['N_t']
    assert max(abs(d['energy_balance_W']) for d in saved['diagnostics'])<1e-6


def test_execution_threads_are_configured_at_host_boundary_and_cannot_change_an_active_run(tmp_path):
    import torch
    from backend.liquidcooling.run_manager import RunConflictError
    original=torch.get_num_threads()
    try:
        manager=LiquidRunManager();first=manager.create(case(),case_dir=tmp_path/'one')
        second=case();second['execution']['max_threads']=2
        with pytest.raises(RunConflictError,match='thread budget'):
            manager.create(second,case_dir=tmp_path/'two')
        manager.operate(first['run_id'],'cancel')
        created=manager.create(second,case_dir=tmp_path/'two')
        assert created['metadata']['execution']['actual_threads']==2
        manager.operate(created['run_id'],'step')
        assert torch.get_num_threads()==2
    finally:torch.set_num_threads(original)


def test_accepted_input_prefix_replays_exact_fields_and_excludes_queued_future_power(tmp_path):
    from backend.liquidcooling.dae_runtime import DAESimulation
    raw=manifest();original=copy.deepcopy(raw)
    manager=LiquidRunManager();created=manager.create(raw,case_dir=tmp_path/'original')
    run_id=created['run_id']
    manager.set_inputs(run_id,heat_multiplier=1.2);manager.operate(run_id,'step')
    manager.set_inputs(run_id,heat_multiplier=.8);manager.operate(run_id,'step')
    manager.set_inputs(run_id,heat_multiplier=3.)  # queued, not an accepted input
    executed=manager.executed_input(run_id)
    assert executed['time']['N_t']==3 and executed['events']==[]
    cp=next(e for e in executed['edges'] if e['type']=='cold_plate')
    assert cp['params']['Q_W']==[500.,600.,400.]
    assert raw==original
    replay=DAESimulation(executed)
    while not replay.done:replay.step()
    observed=manager.result(run_id);actual=replay.result()
    for key in ('node_pressure_Pa','edge_flow_kg_s','node_temperature_K','chip_temp_K','cell_temp_K','primary_return_K','secondary_supply_K'):
        assert actual[key]==observed[key],key
    folder=tmp_path/'original'/'Liquid'/'log'/f'run-{run_id}'
    assert json.loads((folder/'executed.liquidrun').read_text())==executed
    provenance=json.loads((folder/'executed-inputs.json').read_text())
    assert provenance['original_config_hash']==created['config_hash']
    assert provenance['accepted_sample_count']==3 and provenance['original_sample_count']==4
    assert provenance['complete_original_horizon'] is False
    assert json.loads((folder/'input.liquidrun').read_text())['events']==raw['events']
    # Complete the original horizon. The authored Q=900 W event must still be
    # multiplied by the actually accepted .8 input, rather than the queued 3.
    manager.set_inputs(run_id,heat_multiplier=.8);manager.operate(run_id,'step')
    completed=manager.executed_input(run_id)
    assert next(e for e in completed['edges'] if e['type']=='cold_plate')['params']['Q_W']==[500.,600.,400.,720.]
    replay=DAESimulation(completed)
    while not replay.done:replay.step()
    for key in ('node_temperature_K','chip_temp_K','primary_return_K','edge_flow_kg_s'):
        assert replay.result()[key]==manager.result(run_id)[key]


def test_actual_input_file_runs_through_standalone_entry_without_a_checkpoint(tmp_path):
    from backend.liquidcooling.runner import run_liquid_solve
    manager=LiquidRunManager();run_id=manager.create(case(),case_dir=tmp_path/'live')['run_id']
    manager.set_inputs(run_id,heat_multiplier=1.2)
    while manager.get(run_id)['status']!='completed':manager.operate(run_id,'step')
    path=tmp_path/'live'/'Liquid'/'log'/f'run-{run_id}'/'executed.liquidrun'
    result=run_liquid_solve(path,case_dir=tmp_path/'batch')
    assert result.primary_return_K.tolist()==manager.result(run_id)['primary_return_K']
    assert result.chip_temp_K.tolist()==manager.result(run_id)['chip_temp_K']
