"""Execute a standalone v2 manifest through the same PyTorch runtime as the UI.

The JSON adapter serializes results; optimization uses solver.rollout's tensor
API over these same hydraulic/thermal kernels, without a JSON gradient boundary.
"""
import json
from pathlib import Path
import torch

from backend.liquidcooling.runtime import LiquidSimulation, _canonical_hash
from backend.liquidcooling.postprocess.results import LiquidResult


def run_transient_manifest(config, controllers, case_dir: Path, callback=None):
    # v2 declares one CPU thread; no CUDA initialization or alternate solver.
    torch.set_num_threads(1)
    simulation=LiquidSimulation(config,controllers)
    return _save_and_run(simulation, case_dir, callback)


def run_dae_manifest(raw, case_dir: Path, callback=None, *, inputs=None):
    """March a v3/v4 DAE manifest to its horizon and save it under ``case_dir/log/run-<id>/``.

    ``inputs`` (``heat_multiplier``, ``pump_rpm_ratios``, ``valve_openings``,
    ``primary_inlet_K``) is one command queued with ``DAESimulation.set_inputs``
    before the first step -- what the server's ``inputs`` endpoint does -- so it
    takes effect from sample 1 and is recorded in ``applied_inputs`` and the
    executed manifest.
    """
    from backend.liquidcooling.dae_runtime import DAESimulation
    simulation=DAESimulation(raw)
    if inputs:
        simulation.set_inputs(**inputs)
    return _save_and_run(simulation, case_dir, callback)


def _save_and_run(simulation, case_dir, callback):
    folder=Path(case_dir)/'log'/f'run-{simulation.run_id}'
    folder.mkdir(parents=True,exist_ok=False)
    resolved=simulation.checkpoint()['liquidrun']
    if not resolved.get('parameter_provenance'):
        resolved.pop('parameter_provenance',None)
    (folder/'input.liquidrun').write_text(json.dumps(resolved,indent=2,allow_nan=False))
    if callback:
        callback({'stage':'validated','projectName':resolved['projectName']})
        callback({'stage':'compiled','N_nodes':len(resolved['nodes']),'N_edges':len(resolved['edges']),'N_t':resolved['time']['N_t']})
    while not simulation.done:
        frame=simulation.step()
        if callback:
            callback({'stage':'runtime','time_s':frame['time_s'],'seq':frame['seq'],
                      'diagnostics':frame['diagnostics'],'controls':frame['controls']})
    raw=simulation.result()
    path=folder/'result.json'
    path.write_text(json.dumps(raw,indent=2,allow_nan=False))
    (folder/'checkpoint.json').write_text(json.dumps(simulation.checkpoint(),indent=2,allow_nan=False))
    if hasattr(simulation,'executed_manifest'):
        executed=simulation.executed_manifest()
        (folder/'executed.liquidrun').write_text(json.dumps(executed,indent=2,allow_nan=False))
        (folder/'executed-inputs.json').write_text(json.dumps({
            'format':'liquid-accepted-inputs/v1',
            'run_id':simulation.run_id,
            'original_config_hash':simulation.config_hash,
            'executed_config_hash':_canonical_hash(executed,[]),
            'accepted_sample_count':executed['time']['N_t'],
            'original_sample_count':simulation.metadata['total_sample_count'],
            'accepted_time_s':simulation.latest_frame['time_s'],
            'complete_original_horizon':simulation.done,
            'input_file':'executed.liquidrun',
            'scope':'accepted inputs only; pending commands and independent equilibrium are excluded',
        },indent=2,allow_nan=False))
    if callback:
        callback({'stage':'done','converged':raw['converged'],
                  'final_loss':raw['final_loss'],'epochs':raw['epochs']})
    return LiquidResult.load_json(path)
