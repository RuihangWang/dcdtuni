"""Actual runner reuse transitions on controlled native ranges, no physics claim."""
import copy,hashlib,json
import pytest
from backend.tests.test_source_region_lineage import region_receipt_runner as _region_receipt_runner

def region_receipt_runner(tmp_path):
    runner,files=_region_receipt_runner(tmp_path)
    replace_manifest(runner,lambda raw:raw.setdefault("meshConfig",{}).update(meshEngine="dcCart"))
    return runner,files

def replace_manifest(runner, change):
    raw=json.loads(runner.cfdrun_path.read_text());change(raw)
    runner.cfdrun_path.write_text(json.dumps(raw))
    runner._verified_source_cfdrun_sha256=hashlib.sha256(runner.cfdrun_path.read_bytes()).hexdigest()
    return raw

def test_fresh_solve_run_metadata_reuses_proven_source_geometry_without_replacing_original_mesh_receipt(tmp_path):
    runner,_=region_receipt_runner(tmp_path);runner._record_source_region_mesh_source()
    path=runner.case_dir/'source-region-mesh-source.json';original=path.read_bytes()
    replace_manifest(runner,lambda raw:raw.update(runId='fresh_execution_run',simulationConfig={'endTime':200}))
    runner._verify_source_region_mesh_source()
    assert path.read_bytes()==original
    reuse=json.loads((runner.case_dir/'source-region-mesh-reuse.json').read_text())
    assert reuse['executionCfdrunSha256']==runner._verified_source_cfdrun_sha256
    assert reuse['meshSourceReceiptSha256']==hashlib.sha256(original).hexdigest()
    assert reuse['meshCfdrunSha256']==json.loads(original)['cfdrunSha256']

def test_regions_only_preflight_rejects_intervening_native_mesh_before_solve_setup(tmp_path):
    runner,_=region_receipt_runner(tmp_path);runner._record_source_region_mesh_source()
    (runner.mesh_dir/'constant/polyMesh/faces').write_text('changed before load and solve setup')
    with pytest.raises((RuntimeError,ValueError)):
        runner._verify_source_stl_snapshot_before_solve()

@pytest.mark.parametrize('part',['geometryExport','meshConfig','meshGeometry','boundaryBindings','porousBindings','sourceTerms','geometryTopology','project'])
def test_fresh_manifest_cannot_reuse_after_mesh_source_or_role_change(tmp_path,part):
    runner,_=region_receipt_runner(tmp_path);runner._record_source_region_mesh_source()
    def change(raw):
        raw['runId']='fresh_execution_run'
        if part=='project':raw['sourceProject']['uid']='another_project'
        else:raw.setdefault(part,{})['intervening_semantic_change']=True
    replace_manifest(runner,change)
    with pytest.raises((RuntimeError,ValueError)):
        runner._verify_source_region_mesh_source()
    assert not (runner.case_dir/'source-region-mesh-reuse.json').exists()

def test_operating_values_reuse_the_mesh(tmp_path):
    """The ACU / rack operating values (`inputs`) and binding parameters do not build the mesh (v4 identity)."""
    runner,_=region_receipt_runner(tmp_path);runner._record_source_region_mesh_source()
    def change(raw):
        raw['runId']='fresh_execution_run'
        raw.setdefault('inputs',{})['acus']={'m_1':{'supplyAirVolumeFlowRateM3s':2.5}}
        for binding in (raw.get('boundaryBindings') or {}).values():
            binding.setdefault('parameters',{})['flowRateM3s']=2.5
    replace_manifest(runner,change)
    runner._verify_source_region_mesh_source()
    assert (runner.case_dir/'source-region-mesh-reuse.json').exists()

def test_legacy_missing_mesh_identity_is_never_inferred_for_a_fresh_run(tmp_path):
    runner,_=region_receipt_runner(tmp_path);runner._record_source_region_mesh_source()
    path=runner.case_dir/'source-region-mesh-source.json';old=json.loads(path.read_text())
    old['format']='dcdtuni.source-region-mesh-source/v1';old.pop('meshInputIdentitySha256',None);old.pop('triSurfaceSha256',None)
    path.write_text(json.dumps(old));runner._verify_source_region_mesh_source()
    replace_manifest(runner,lambda raw:raw.update(runId='fresh_execution_run'))
    with pytest.raises(RuntimeError):runner._verify_source_region_mesh_source()

@pytest.mark.parametrize('change',['original_receipt','stage','native','manifest'])
def test_preflight_and_final_checks_reject_late_mutation_without_overwriting_original_mesh_proof(tmp_path,monkeypatch,change):
    runner,native=region_receipt_runner(tmp_path);runner._record_source_region_mesh_source()
    original=(runner.case_dir/'source-region-mesh-source.json').read_bytes()
    runner._verify_source_stl_snapshot_before_solve()
    replace_manifest(runner,lambda raw:raw.update(runId='fresh_execution_run'))
    original_check=runner._checked_source_region_mesh_receipt
    calls=0
    def checked(raw,pinned):
        nonlocal calls
        result=original_check(raw,pinned);calls+=1
        if calls==1:
            if change=='original_receipt':(runner.case_dir/'source-region-mesh-source.json').write_bytes(original+b' ')
            elif change=='stage':next(runner.tri_surface_dir.glob('*.stl')).write_bytes(b'late rewritten stage')
            elif change=='native':native[0].write_text('{}')
            else:runner.cfdrun_path.write_text('{}')
        return result
    monkeypatch.setattr(runner,'_checked_source_region_mesh_receipt',checked)
    with pytest.raises((RuntimeError,ValueError,KeyError)):
        runner._verify_source_region_mesh_source()

@pytest.mark.parametrize('which',['source-region-mesh-source.json','source-region-mesh-reuse.json'])
def test_owned_receipt_paths_cannot_follow_symlinks(tmp_path,which):
    runner,_=region_receipt_runner(tmp_path)
    runner._record_source_region_mesh_source();target=tmp_path/'unrelated.json';target.write_text('keep')
    path=runner.case_dir/which
    if path.exists():path.unlink()
    path.symlink_to(target)
    with pytest.raises(RuntimeError):runner._verify_source_region_mesh_source()
    assert target.read_text()=='keep'
