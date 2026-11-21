"""Actual schema/BC resolver compatibility and immutable RAM input ownership."""
import hashlib
import json
from dataclasses import FrozenInstanceError
from pathlib import Path
import pytest
from backend.aircooling.runner import cfdrun_reader
try:
    from backend.aircooling.runner.cfdrun_snapshot import CfdrunSnapshot
except ModuleNotFoundError as exc:
    if exc.name != 'backend.aircooling.runner.cfdrun_snapshot':
        raise
    CfdrunSnapshot = None
FIXTURE = Path(__file__).parent / 'fixtures/cfdrun_v2_full/Base_leakage.cfdrun'

def snapshot_type():
    assert CfdrunSnapshot is not None, 'immutable RAM cfdrun snapshot capability is missing'
    return CfdrunSnapshot

def setup_case(tmp_path):
    case = tmp_path / 'case'
    path = case / 'model/input.cfdrun'
    path.parent.mkdir(parents=True)
    path.write_bytes(FIXTURE.read_bytes())
    return case, path

def dump(resolved):
    return {key: spec.model_dump(mode='json') for key, spec in resolved.bridge_registry.items()}

def test_original_file_resolver_control(tmp_path):
    case, path = setup_case(tmp_path)
    resolved = cfdrun_reader.resolve_run(case, cfdrun_path=path, resolve_stl=False)
    assert len(dump(resolved)) == 52

def test_ram_resolver_matches_full_original_registry_without_artifact(tmp_path):
    cls = snapshot_type()
    case, path = setup_case(tmp_path)
    original = cfdrun_reader.resolve_run(case, cfdrun_path=path, resolve_stl=False)
    payload = path.read_bytes()
    snapshot = cls(payload, case, path, expected_sha256=hashlib.sha256(payload).hexdigest())
    path.unlink()
    actual = cfdrun_reader.resolve_run(case, resolve_stl=False, input_snapshot=snapshot)
    assert dump(actual) == dump(original)
    for field in ('mesh_config', 'simulation_config', 'geometry_solids', 'probes',
                  'surface_monitors', 'fv_options_inputs', 'source_terms', 'transient', 'artifacts'):
        assert getattr(actual, field) == getattr(original, field)

def test_owned_bytes_and_fresh_json_trees_preserve_extensions(tmp_path):
    cls = snapshot_type()
    case, path = setup_case(tmp_path)
    raw = json.loads(path.read_bytes())
    raw['opaqueEngineeringExtension'] = {'uid': 'owned-source', 'values': [-0.0, 4.5]}
    external = bytearray(json.dumps(raw).encode())
    owned = bytes(external)
    snapshot = cls(external, case, path)
    external[:] = b'changed external bytes'
    parsed = snapshot.parse()
    parsed['opaqueEngineeringExtension']['values'][1] = 1000
    assert snapshot.read_bytes() == owned
    assert snapshot.sha256 == hashlib.sha256(owned).hexdigest()
    assert snapshot.parse()['opaqueEngineeringExtension']['values'] == [-0.0, 4.5]
    with pytest.raises(FrozenInstanceError):
        snapshot.payload = b'changed'

def test_changed_disk_artifact_cannot_change_ram_materialization(tmp_path):
    cls = snapshot_type()
    case, path = setup_case(tmp_path)
    snapshot = cls(path.read_bytes(), case, path)
    expected = dump(cfdrun_reader.resolve_run(case, cfdrun_path=path, resolve_stl=False))
    path.write_bytes(b'not a valid manifest anymore')
    assert dump(cfdrun_reader.resolve_run(case, input_snapshot=snapshot, resolve_stl=False)) == expected
    with pytest.raises(ValueError):
        cfdrun_reader.resolve_run(case, cfdrun_path=path, resolve_stl=False)

def test_ram_mode_keeps_real_stl_validation_enabled(tmp_path):
    cls = snapshot_type()
    case, path = setup_case(tmp_path)
    snapshot = cls(path.read_bytes(), case, path)
    path.unlink()
    with pytest.raises(RuntimeError, match=r'(?i)(stl|file|solid|surface)'):
        cfdrun_reader.resolve_run(case, input_snapshot=snapshot)

def test_foreign_case_artifact_and_sha_are_refused(tmp_path):
    cls = snapshot_type()
    case, path = setup_case(tmp_path)
    snapshot = cls(path.read_bytes(), case, path)
    with pytest.raises(ValueError, match='case'):
        cfdrun_reader.resolve_run(tmp_path / 'foreign', input_snapshot=snapshot, resolve_stl=False)
    with pytest.raises(ValueError, match='artifact'):
        cfdrun_reader.resolve_run(case, cfdrun_path=case/'model/other.cfdrun',
                                  input_snapshot=snapshot, resolve_stl=False)
    with pytest.raises(ValueError, match='SHA'):
        cls(path.read_bytes(), case, path, expected_sha256='0'*64)
    with pytest.raises(ValueError, match='SHA'):
        cls(path.read_bytes(), case, path, expected_sha256='BAD')
    with pytest.raises(ValueError, match='artifact'):
        cls(path.read_bytes(), case, tmp_path/'outside.cfdrun')

@pytest.mark.parametrize('version', [0, 1, True, 2.0, 4294967301])
def test_invalid_or_future_schema_numbers_refuse(tmp_path, version):
    cls = snapshot_type()
    payload = json.dumps({'schemaVersion': version}).encode()
    with pytest.raises(ValueError, match='schemaVersion'):
        cls(payload, tmp_path, tmp_path/'model/input.cfdrun')

@pytest.mark.parametrize('version', [2, 3, 4])
def test_supported_schema_identity_kept_in_owned_payload(tmp_path, version):
    cls = snapshot_type()
    payload = json.dumps({'schemaVersion': version, 'extension': {'v': -0.0}}).encode()
    snapshot = cls(memoryview(payload), tmp_path, tmp_path/'model/input.cfdrun')
    assert snapshot.read_bytes() == payload
    assert snapshot.parse()['schemaVersion'] == version

def test_nonfinite_and_nonobject_json_refuse(tmp_path):
    cls = snapshot_type()
    for payload in (b'[]', b'null', b'{"schemaVersion":2,"v":NaN}', b'{"schemaVersion":2,"v":Infinity}'):
        with pytest.raises(ValueError):
            cls(payload, tmp_path, tmp_path/'model/input.cfdrun')


def test_multibyte_view_capacity_refuses_before_copy_or_json(tmp_path):
    cls = snapshot_type()
    from backend.aircooling.runner.cfdrun_snapshot import MAX_CFDRUN_SNAPSHOT_BYTES
    backing = bytearray(MAX_CFDRUN_SNAPSHOT_BYTES + 4)
    view = memoryview(backing).cast('I')
    assert len(view) < MAX_CFDRUN_SNAPSHOT_BYTES < view.nbytes
    with pytest.raises(ValueError, match='capacity'):
        cls(view, tmp_path, tmp_path/'model/input.cfdrun')


def test_actual_owned_capacity_is_rechecked_after_custom_copy(tmp_path, monkeypatch):
    from backend.aircooling.runner import cfdrun_snapshot as module
    monkeypatch.setattr(module, 'MAX_CFDRUN_SNAPSHOT_BYTES', 64)
    expanded = json.dumps({'schemaVersion': 2, 'extension': 'x' * 80}).encode()
    class ExpandingBytes(bytearray):
        def __bytes__(self):
            return expanded
    external = ExpandingBytes(b'{}')
    assert len(external) == 2 < 64 < len(bytes(external))
    with pytest.raises(ValueError, match='capacity'):
        snapshot_type()(external, tmp_path, tmp_path/'model/input.cfdrun')
