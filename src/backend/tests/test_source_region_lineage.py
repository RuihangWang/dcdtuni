"""Controlled original-emitter region inventory; no physical OO-face claim."""
import copy
import hashlib
import pytest
from backend.tests.test_cfdrun_reader import _minimal_manifest, _write_ascii_stl_rect
from backend.aircooling.runner.cfdrun_schema import GeometryExport
from backend.aircooling.runner.stl_geometry_index import verify_exported_stl_hashes

def source(tmp_path):
    tri = tmp_path/'original';tri.mkdir()
    path = tri/'group.stl';_write_ascii_stl_rect(path,'actual_region',.4,.2)
    raw = _minimal_manifest()
    raw['geometryExport']['stlFiles'] = {'stl_000':{'path':'group.stl','objectPath':None,
        'kind':'grouped_ascii','contentHash':hashlib.sha256(path.read_bytes()).hexdigest()}}
    raw['geometryExport']['sourceRegions'] = {'stl_000:actual_region':{
        'stlFileId':'stl_000','solidName':'actual_region','sourceEntityUid':'real_entity',
        'sourceRegionUid':'project/real_entity/stl-region/actual_region',
        'sourceFacetCount':2,'sourceRegionSha256':hashlib.sha256(path.read_bytes()).hexdigest(),
        'operation':'modelBoundary'}}
    return raw,tri,path

def test_accepts_literal_facet_inventory_without_claiming_declared_physical_face(tmp_path):
    raw,tri,path=source(tmp_path)
    assert verify_exported_stl_hashes(raw,tri)['filenames']==['group.stl']
    assert GeometryExport.model_validate(raw['geometryExport']).sourceSurfaces=={}
    binding=GeometryExport.model_validate(raw['geometryExport']).sourceRegions['stl_000:actual_region']
    assert binding.sourceFacetCount==2
    assert 'sourceFaceUid' not in binding.model_dump()

@pytest.mark.parametrize('change',['facet_count','region_digest','solid_name','exact_bytes'])
def test_rejects_changed_facet_inventory_even_if_full_file_hash_was_redeclared(tmp_path,change):
    raw,tri,path=source(tmp_path);row=raw['geometryExport']['sourceRegions']['stl_000:actual_region']
    if change=='facet_count':row['sourceFacetCount']=1
    elif change=='region_digest':row['sourceRegionSha256']='b'*64
    elif change=='solid_name':row['solidName']='missing_region'
    else:
        path.write_bytes(path.read_bytes().replace(b'facet normal',b'facet  normal'))
        raw['geometryExport']['stlFiles']['stl_000']['contentHash']=hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError,match='literal region/facet identity|invalid entity/region/STL identity'):verify_exported_stl_hashes(raw,tri)

@pytest.mark.parametrize('change',['duplicate','mismatched_end','zero_area'])
def test_rejects_ambiguous_or_invalid_original_region_geometry(tmp_path,change):
    raw,tri,path=source(tmp_path)
    if change=='duplicate':path.write_bytes(path.read_bytes()*2)
    elif change=='mismatched_end':path.write_bytes(path.read_bytes().replace(b'endsolid actual_region',b'endsolid another_region'))
    else:_write_ascii_stl_rect(path,'actual_region',0,.2)
    raw['geometryExport']['stlFiles']['stl_000']['contentHash']=hashlib.sha256(path.read_bytes()).hexdigest()
    if change=='zero_area':raw['geometryExport']['sourceRegions']['stl_000:actual_region']['sourceRegionSha256']=hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError,match='malformed|duplicated|degenerate'):verify_exported_stl_hashes(raw,tri)

@pytest.mark.parametrize('extra',['sourceFaceUid','physicalFaceUids','sourceFacetUids'])
def test_schema_keeps_region_inventory_distinct_from_physical_faces(tmp_path,extra):
    raw,tri,path=source(tmp_path)
    raw['geometryExport']['sourceRegions']['stl_000:actual_region'][extra]='invented'
    with pytest.raises(ValueError,match='Extra inputs'):GeometryExport.model_validate(raw['geometryExport'])

@pytest.mark.parametrize('count',[True,0,-1,2.5])
def test_literal_facet_count_is_a_positive_integer(tmp_path,count):
    raw,tri,path=source(tmp_path);raw['geometryExport']['sourceRegions']['stl_000:actual_region']['sourceFacetCount']=count
    with pytest.raises(ValueError):GeometryExport.model_validate(raw['geometryExport'])


def test_actual_mesh_stage_file_rebucket_keeps_literal_region_identity(tmp_path):
    from backend.aircooling.runner.source_region_catalogue import build_source_region_stage_catalogue,render_source_regions_dict
    raw,tri,path=source(tmp_path);path.rename(tri/'rebucketed.stl')
    stage=build_source_region_stage_catalogue(raw['geometryExport'],tri,{'rebucketed.stl':['actual_region']})
    row=stage['regions'][0]
    assert row['meshStageStlFile']=='rebucketed.stl' and row['originalStlFile']=='group.stl'
    assert row['unresolvedReason'] is None
    block=render_source_regions_dict(stage)
    assert 'dcdtuniSourceRegions' in block and 'sourceEntityUid "real_entity"' in block
    assert 'sourceFaceUid' not in block and 'sourceObjectUid' not in block

@pytest.mark.parametrize('change',['transformed','ambiguous','missing'])
def test_mesh_stage_name_alone_cannot_prove_a_region_source(tmp_path,change):
    from backend.aircooling.runner.source_region_catalogue import build_source_region_stage_catalogue,render_source_regions_dict
    raw,tri,path=source(tmp_path);selected={'group.stl':['actual_region']}
    if change=='transformed':path.write_bytes(path.read_bytes().replace(b'facet normal',b'facet  normal'))
    elif change=='ambiguous':(tri/'other.stl').write_bytes(path.read_bytes());selected['other.stl']=['actual_region']
    else:selected={'group.stl':['other_region']}
    stage=build_source_region_stage_catalogue(raw['geometryExport'],tri,selected)
    assert stage['regions'][0]['meshStageStlFile'] is None
    assert stage['regions'][0]['unresolvedReason'] is not None
    block=render_source_regions_dict(stage)
    assert 'dcdtuniSourceRegions' in block and 'sourceEntityUid' not in block

def native_fixture(tmp_path,rank_count=1,compound=False):
    import json
    raw,tri,path=source(tmp_path)
    from backend.aircooling.runner.source_region_catalogue import build_source_region_stage_catalogue
    stage=build_source_region_stage_catalogue(raw['geometryExport'],tri)
    if compound:
        another=copy.deepcopy(stage['regions'][0]);another['sourceKey']='stl_000:other_region';another['solidName']='other_region';another['sourceRegionUid']='project/real_entity/stl-region/other_region'
        stage['regions'].append(another)
    root=tmp_path/'mesh';root.mkdir();files=[]
    if rank_count>1:
        (root/'system').mkdir();(root/'system/decomposeParDict').write_text(f'numberOfSubdomains {rank_count};')
    for rank in range(rank_count):
        constant=(root/f'processor{rank}'/'constant') if rank_count>1 else root/'constant'
        poly=constant/'polyMesh';poly.mkdir(parents=True)
        for name in ['points','faces','owner','neighbour']:(poly/name).write_text('controlled complete fixture bytes; not accepted native physics')
        (poly/'boundary').write_text('2\n(\nwall_master\n{\ntype wall;\nnFaces 2;\nstartFace 4;\n}\nwall_slave\n{\ntype wall;\nnFaces 2;\nstartFace 6;\n}\n)\n')
        rows=[]
        for local,original in enumerate(stage['regions']):
            rows.append({name:original[name]for name in ['sourceEntityUid','sourceRegionUid','sourceRegionSha256','sourceFacetCount','solidName','operation']})
            rows[-1].update({'sourceFacetUidPrefix':original['sourceRegionUid']+'/facet/',
                'stlFile':'group.stl','nativeSurfaceIndex':0,'nativeLocalRegionIndex':local,'unresolvedReason':None,
                'patchRanges':[{'patchIndex':0,'patchName':'wall_master','startFace':4,'nFaces':2},
                               {'patchIndex':1,'patchName':'wall_slave','startFace':6,'nFaces':2}]})
        data={'format':'dcdtuni.native-source-regions/v1','rank':rank,'rankCount':rank_count,
              'bindingGranularity':'patch-region-facet-inventory','regions':sorted(rows,key=lambda r:r['sourceRegionUid'])}
        output=constant/'dcCartSourceRegions.json';output.write_text(json.dumps(data));files.append(output)
    return root,stage,files

@pytest.mark.parametrize('rank_count',[1,2])
def test_each_native_rank_joins_real_boundary_ranges_and_retains_compound_region_inventory(tmp_path,rank_count):
    from backend.aircooling.runner.source_region_catalogue import verify_native_source_region_ranges
    root,stage,files=native_fixture(tmp_path,rank_count,compound=True)
    result=verify_native_source_region_ranges(root,stage)
    assert len(result)==rank_count
    for capture in result:
        assert len(capture['regions'])==2
        assert all('sourceFaceUid'not in r for r in capture['regions'])
        assert all(len(r['patchRanges'])==2 for r in capture['regions'])

@pytest.mark.parametrize('change',['range','owner','region_set','duplicate','extra_face','rank','bool_range','surface_address'])
def test_native_range_or_source_identity_mutation_is_rejected(tmp_path,change):
    import json
    from backend.aircooling.runner.source_region_catalogue import verify_native_source_region_ranges
    root,stage,files=native_fixture(tmp_path);path=files[0];data=json.loads(path.read_text());row=data['regions'][0]
    if change=='range':row['patchRanges'][0]['startFace']=5
    elif change=='owner':row['sourceEntityUid']='other_entity'
    elif change=='region_set':data['regions']=[]
    elif change=='duplicate':data['regions'].append(copy.deepcopy(row))
    elif change=='extra_face':row['sourceFaceUid']='invented_body_face'
    elif change=='rank':data['rank']=True
    elif change=='bool_range':row['patchRanges'][0]['nFaces']=True
    else:row['nativeSurfaceIndex']=-1
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):verify_native_source_region_ranges(root,stage)

def test_native_unresolved_region_keeps_empty_ranges_without_a_fake_face(tmp_path):
    import json
    from backend.aircooling.runner.source_region_catalogue import verify_native_source_region_ranges
    root,stage,files=native_fixture(tmp_path);path=files[0];data=json.loads(path.read_text())
    data['regions'][0]['unresolvedReason']='refinement-volume-has-no-boundary-range';data['regions'][0]['patchRanges']=[];path.write_text(json.dumps(data))
    result=verify_native_source_region_ranges(root,stage)
    assert result[0]['regions'][0]['patchRanges']==[]
    assert result[0]['regions'][0]['unresolvedReason'] is not None


def test_later_stage_mutation_cannot_reuse_prior_literal_region_proof(tmp_path):
    from backend.aircooling.runner.source_region_catalogue import build_source_region_stage_catalogue,verify_source_region_stage_files
    raw,tri,path=source(tmp_path);stage=build_source_region_stage_catalogue(raw['geometryExport'],tri)
    verify_source_region_stage_files(stage,tri);path.write_bytes(path.read_bytes()+b'\n')
    with pytest.raises(ValueError,match='changed after catalogue'):verify_source_region_stage_files(stage,tri)


def region_receipt_runner(tmp_path):
    """Controlled native-range fixture, never an accepted physics receipt."""
    import hashlib,json
    from backend.aircooling.runner.stlgen_runner import STLGenRunner
    root,stage,files=native_fixture(tmp_path)
    raw,tri,original=source(tmp_path/'original')
    runner=object.__new__(STLGenRunner);runner._data={"meshConfig":{"meshEngine":"dcCart"}};runner.case_dir=root;runner.mesh_dir=root
    runner.tri_surface_dir=tri;runner.cfdrun_path=root/'source.cfdrun'
    runner.cfdrun_path.write_text(json.dumps(raw));runner._source_stls_verified=True
    runner._verified_source_cfdrun_sha256=hashlib.sha256(runner.cfdrun_path.read_bytes()).hexdigest()
    runner._source_region_stage_catalogue=stage
    # native_fixture builds the same literal geometry and canonical stage bytes.
    return runner,files

@pytest.mark.parametrize('mutation',['missing','source','mesh','native','stage','receipt'])
def test_real_runner_range_receipt_reuse_denies_mutation(tmp_path,mutation):
    import json
    runner,files=region_receipt_runner(tmp_path)
    runner._record_source_region_mesh_source();runner._verify_source_region_mesh_source()
    path=runner.case_dir/'source-region-mesh-source.json'
    if mutation=='missing':path.unlink()
    elif mutation=='source':
        raw=json.loads(runner.cfdrun_path.read_text());raw['sourceProject']['uid']='other';runner.cfdrun_path.write_text(json.dumps(raw))
    elif mutation=='mesh':(runner.mesh_dir/'constant/polyMesh/faces').write_text('changed')
    elif mutation=='native':
        raw=json.loads(files[0].read_text());raw['regions'][0]['sourceEntityUid']='other';files[0].write_text(json.dumps(raw))
    elif mutation=='stage':next(runner.tri_surface_dir.glob('*.stl')).write_text('changed')
    else:
        raw=json.loads(path.read_text());raw['meshSha256']='0'*64;path.write_text(json.dumps(raw))
    with pytest.raises((RuntimeError,ValueError)):
        runner._verify_source_region_mesh_source()


@pytest.mark.parametrize('engine',['snappy'])
def test_other_existing_engines_do_not_invent_native_range_receipts(tmp_path,engine):
    runner,files=region_receipt_runner(tmp_path);runner._data={'meshConfig':{'meshEngine':engine}}
    runner._source_region_stage_catalogue=None
    runner._record_source_region_mesh_source();runner._verify_source_region_mesh_source()
    assert not (runner.case_dir/'source-region-mesh-source.json').exists()
