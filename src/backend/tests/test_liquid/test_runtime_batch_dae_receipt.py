"""Standalone DAE runs must export the accepted-input identity used by heat handoff."""
import json

from backend.liquidcooling.hybrid_heat_audit import audit_hybrid_heat
from backend.liquidcooling.runtime_batch import run_dae_manifest
from backend.tests.test_liquid.test_hybrid_heat_audit import coupled_case


def test_standalone_dae_batch_writes_complete_accepted_input_receipt(tmp_path):
    original = coupled_case()
    run_dae_manifest(original, tmp_path)
    folders = list((tmp_path / 'log').glob('run-*'))
    assert len(folders) == 1
    folder = folders[0]
    executed = json.loads((folder / 'executed.liquidrun').read_text())
    result = json.loads((folder / 'result.json').read_text())
    receipt = json.loads((folder / 'executed-inputs.json').read_text())
    assert receipt['accepted_sample_count'] == original['time']['N_t']
    assert receipt['complete_original_horizon'] is True
    report = audit_hybrid_heat(executed, result, 'project-stable-uid',
                               original_liquidrun=original, accepted_provenance=receipt)
    assert report['identity_verified'] and report['handoff_ready']
