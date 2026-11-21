"""Every air solve entry must enforce the post-mesh hybrid heat gate."""
import json
from copy import deepcopy

import pytest

from backend.aircooling.runner.stlgen_runner import STLGenRunner, requires_hybrid_air_gate


def _stub_runner(monkeypatch):
    runner = object.__new__(STLGenRunner)
    events = []
    for name in ('_preflight_case_path', '_load', '_preflight_floor_openings',
                 '_verify_source_stl_snapshot_before_solve',
                 '_verify_hybrid_source_stls',
                 'validate_geometry', '_preflight_partition_wall_thickness',
                 '_prepare_runtime_metadata', '_generate_mesh_dicts',
                 '_run_serial_mesh_pipeline', '_setup_complete_case_in_mesh',
                 '_record_hybrid_mesh_source', '_record_source_surface_mesh_source',
                 '_verify_source_surface_mesh_source',
                 '_record_air_mesh_quality', '_require_air_mesh_quality',
                 '_run_solve_from_mesh',
                 '_apply_hybrid_air_gate'):
        monkeypatch.setattr(runner, name, lambda *args, _name=name, **kwargs: events.append(_name),
                            raising=False)
    runner._data = {'runId':'hybrid_test', 'meshConfig': {'meshEngine': 'snappy', 'parallelMesh': False}}
    monkeypatch.setattr(runner, '_read_cfdrun_manifest', lambda: runner._data, raising=False)
    return runner, events


def test_solve_only_normalizes_mesh_then_gates_then_regenerates_fields(monkeypatch):
    runner, events = _stub_runner(monkeypatch)
    runner.run_solve_only()
    setup = [i for i,name in enumerate(events) if name=='_setup_complete_case_in_mesh']
    assert len(setup)==2
    assert setup[0] < events.index('_apply_hybrid_air_gate') < setup[1]
    assert setup[1] < events.index('_run_solve_from_mesh')


def test_default_mesh_then_solve_gates_the_new_mesh(monkeypatch):
    runner, events = _stub_runner(monkeypatch)
    runner.run_simulation()
    assert events.index('_verify_hybrid_source_stls') < events.index('_load')
    setup = [i for i,name in enumerate(events) if name=='_setup_complete_case_in_mesh']
    assert len(setup)==2
    assert events.index('_run_serial_mesh_pipeline') < events.index('_apply_hybrid_air_gate')
    assert setup[0] < events.index('_apply_hybrid_air_gate') < setup[1]


@pytest.mark.parametrize("entry", ["run_solve_only", "run_simulation"])
def test_root_hybrid_contract_normalizes_mesh_before_heat_gate(monkeypatch, entry):
    runner, events = _stub_runner(monkeypatch)
    runner._data = {'meshConfig': {'meshEngine': 'snappy', 'parallelMesh': False}}
    root = {'runId': 'hybrid_fixture', 'inputs': {'liquid': {'enabled': True}}}
    monkeypatch.setattr(runner, '_read_cfdrun_manifest', lambda: root, raising=False)

    getattr(runner, entry)()

    setup = [i for i, name in enumerate(events) if name == '_setup_complete_case_in_mesh']
    assert len(setup) == 2
    assert setup[0] < events.index('_apply_hybrid_air_gate') < setup[1]


@pytest.mark.parametrize("entry", ["run_solve_only", "run_simulation"])
def test_hybrid_strict_quality_rejects_before_legacy_heat_gate(monkeypatch, entry):
    runner, events = _stub_runner(monkeypatch)
    monkeypatch.setattr(runner, '_record_air_mesh_quality',
                        lambda: events.append('strict_quality'), raising=False)
    def reject_quality():
        events.append('quality_rejected')
        raise RuntimeError('strict mesh quality rejected')
    monkeypatch.setattr(runner, '_require_air_mesh_quality', reject_quality, raising=False)

    with pytest.raises(RuntimeError, match='strict mesh quality rejected'):
        getattr(runner, entry)()
    assert events.index('strict_quality') < events.index('quality_rejected')
    assert '_apply_hybrid_air_gate' not in events


def test_liquid_server_flux_needs_gate_even_if_run_id_and_sidecar_are_removed():
    raw={'runId':'ordinary_edited','inputs':{'liquid':{'enabled':True}},
         'boundaryBindings':{'bb_server':{
             'objectPath':'constructions.racks.r1.servers.s1',
             'bcType':'nonfan_heat_flux'}}}
    assert requires_hybrid_air_gate(raw) is True
    raw['inputs']['liquid']['enabled']=False
    assert requires_hybrid_air_gate(raw) is False


def test_solve_only_requires_mesh_source_identity_receipt(tmp_path):
    runner=object.__new__(STLGenRunner)
    runner.case_dir=tmp_path
    runner.mesh_dir=tmp_path/'Mesh'
    model=tmp_path/'model';model.mkdir()
    runner.cfdrun_path=model/'hybrid.cfdrun'
    runner.cfdrun_path.write_text(json.dumps({
        'runId':'hybrid_unit','sourceProject':{'uid':'u','contentHash':'a'*64},
        'geometryExport':{'stlFiles':{}},
    }))
    (tmp_path/'heat-ledger.json').write_text('{}')
    with pytest.raises(RuntimeError,match='mesh.source|mesh source|hybrid-mesh-source'):
        runner._apply_hybrid_air_gate()


def test_mesh_source_receipt_binds_source_surface_uids_not_only_stl_bytes():
    raw={'runId':'hybrid_test','sourceProject':{'contentHash':'a'*64},
         'geometryExport':{'stlFiles':{'stl_000':{'contentHash':'b'*64}},
                           'sourceSurfaces':{'stl_000:wall':{'stlFileId':'stl_000',
                             'solidName':'wall','sourceObjectUid':'m_22',
                             'sourceFaceUid':'project/m_22/source-sheet',
                             'representation':'zeroThicknessSheet'}}}}
    first=STLGenRunner._hybrid_source_fields(raw)
    changed=deepcopy(raw)
    changed['geometryExport']['sourceSurfaces']['stl_000:wall']['sourceObjectUid']='m_23'
    assert STLGenRunner._hybrid_source_fields(changed)!=first
    assert 'source_surfaces_sha256' in first
