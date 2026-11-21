"""Derived hybrid RAM identity preserves original source and replay namespace."""
import json
from dataclasses import FrozenInstanceError
import pytest
from backend.aircooling.runner import cfdrun_snapshot as module

def parent(tmp_path):
    payload=json.dumps({'schemaVersion':4,'runId':'hybrid_unit',
        'sourceProject':{'contentHash':'a'*64},'boundaryBindings':{}}).encode()
    return module.CfdrunSnapshot(payload,tmp_path,tmp_path/'model/input.cfdrun')

def child_type():
    assert hasattr(module,'HybridCfdrunSnapshot'),'derived hybrid snapshot capability missing'
    return module.HybridCfdrunSnapshot

def child(source,raw=None,path=None,mesh='b'*64):
    raw=raw if raw is not None else {**source.parse(),'boundaryBindings':{'adjusted':{'area':2.0}}}
    return child_type()(json.dumps(raw).encode(),source.case_dir,
        path or source.case_dir/f'hybrid-mesh-{mesh[:16]}.cfdrun',
        parent_input=source,mesh_sha256=mesh)

def test_child_owns_adjusted_bytes_and_preserves_parent_source(tmp_path):
    source=parent(tmp_path);original=source.payload;raw=source.parse()
    raw['boundaryBindings']={'adjusted':{'area':2.0}}
    result=child(source,raw);raw['boundaryBindings']['adjusted']['area']=99
    assert result.parse()['boundaryBindings']['adjusted']['area']==2.0
    assert source.payload==original and result.parent_sha256==source.sha256
    assert result.sha256!=source.sha256
    result.require_context(tmp_path,result.logical_path)
    assert not result.logical_path.exists()
    with pytest.raises(FrozenInstanceError):result.mesh_sha256='c'*64

@pytest.mark.parametrize('change',['runId','sourceProject','schemaVersion'])
def test_child_refuses_different_source_run_or_schema(tmp_path,change):
    source=parent(tmp_path);raw=source.parse();raw[change]={'contentHash':'c'*64} if change=='sourceProject' else (3 if change=='schemaVersion' else 'other')
    with pytest.raises(ValueError,match='parent'):
        child(source,raw)

def test_child_refuses_wrong_case_replay_mesh_and_derived_parent(tmp_path):
    source=parent(tmp_path)
    with pytest.raises(ValueError,match='hybrid'):
        child(source,path=tmp_path/'model/arbitrary.cfdrun')
    with pytest.raises(ValueError,match='hybrid'):
        child(source,mesh='wrong')
    with pytest.raises(ValueError,match='hybrid|case'):
        child(source,path=tmp_path/'other/hybrid-mesh-bbbbbbbbbbbbbbbb.cfdrun')
    nested=child(source)
    with pytest.raises(ValueError,match='parent'):
        child(nested)

def test_original_source_constructor_does_not_gain_case_root_permission(tmp_path):
    source=parent(tmp_path)
    with pytest.raises(ValueError,match='artifact'):
        module.CfdrunSnapshot(source.payload,tmp_path,tmp_path/'hybrid-mesh-bbbbbbbbbbbbbbbb.cfdrun')


@pytest.mark.parametrize('original,replacement',[(1,True),(1,1.0),(-0.0,0.0)])
def test_child_preserves_source_project_extension_numeric_types_and_zero(tmp_path,original,replacement):
    raw=parent(tmp_path).parse();raw['sourceProject']['extension']={'samples':[original]}
    source=module.CfdrunSnapshot(json.dumps(raw).encode(),tmp_path,tmp_path/'model/input.cfdrun')
    adjusted=source.parse();adjusted['sourceProject']['extension']['samples'][0]=replacement
    with pytest.raises(ValueError,match='parent'):
        child(source,adjusted)
