"""Read-only accepted native frame and honest final boundary provenance.

No browser field establishes acceptance. Region/facet inventory is distinct
from declared OO faces; an unbound actual boundary remains meshBoundaryOnly.
Historical missing receipts, transformed geometry and reconstructed layouts
without the same strict quality/source proof fail closed.
"""
from __future__ import annotations
import hashlib,json
from types import SimpleNamespace
from backend.aircooling.runner.stlgen_runner import STLGenRunner
from pathlib import Path
from backend.aircooling.runner.cfdrun_snapshot import CfdrunSnapshot
from backend.aircooling.runner.air_run_execution_receipt import verify_air_run_execution_receipt,FILENAME
from backend.aircooling.runner.air_run_result_receipt import build_air_run_result_receipt,_inside
from backend.aircooling.runner.air_mesh_quality_receipt import verify_air_mesh_quality_receipt
from backend.aircooling.runner.openfoam_patch_area import mesh_identity_sha256
from backend.aircooling.runner.source_region_catalogue import canonical_sha,source_region_mesh_input_sha,source_region_mesh_input_matches,verify_native_source_region_ranges,verify_source_region_stage_files
from backend.aircooling.runner.mesh_layout import mesh_directories
from backend.aircooling.runner.postprocess import MeshPreviewLoader,_physical_patch_name
from backend.aircooling.runner.native_numeric_input import capture_native_inputs,native_field_units
from backend.aircooling.runner.numeric_product_receipts import canonical_bytes,require_session

# Base604's native region source receipt is 94 MB.
MAX_RECEIPT_BYTES=256*1024*1024

def _read_owned_json(root,path,label):
    path=_inside(root,path,label=label)
    before=path.stat()
    if not path.is_file() or path.is_symlink() or not 0<before.st_size<=MAX_RECEIPT_BYTES:
        raise ValueError(label+' is absent/linked or exceeds capacity')
    data=path.read_bytes();after=path.stat()
    if (before.st_size,before.st_mtime_ns,before.st_ino)!=(after.st_size,after.st_mtime_ns,after.st_ino):
        raise ValueError(label+' changed while read')
    raw=json.loads(data,parse_constant=lambda v:(_ for _ in ()).throw(ValueError('nonfinite receipt')))
    if not isinstance(raw,dict):raise ValueError(label+' must be an object')
    return path,data,raw

def _sha(data):return hashlib.sha256(data).hexdigest()

def _boundary_provenance(mesh,native):
    """Actual final rank/index/range, optionally its full original inventory.

    Names match the ordinary PP physical-patch assembly; they do not assign
    entity, region or domain ownership. Processor-cyclic referPatch is a real
    native dictionary reference, never a string-prefix/name ownership guess.
    """
    result={}
    for rank,poly in enumerate(mesh_directories(mesh)):
        rows=native[rank]['regions'];boundary_sha=native[rank]['boundarySha256']
        # The rows naming each patch index, in row order (a row once): scanning
        # every row per patch was 39 s of Base604's 4,336 patches.
        by_index={}
        for row in rows:
            for patch_index in dict.fromkeys(span['patchIndex'] for span in row['patchRanges']):
                by_index.setdefault(patch_index,[]).append(row)
        for index,patch in enumerate(MeshPreviewLoader._parse_boundary(poly/'boundary')):
            name=_physical_patch_name(patch)
            if name is None or patch['nFaces']==0:continue
            inventory=[{key:row[key] for key in ['sourceEntityUid','sourceRegionUid','sourceRegionSha256','sourceFacetCount','sourceFacetUidPrefix']}
                       for row in by_index.get(index,())]
            inventory.sort(key=lambda r:r['sourceRegionUid'])
            result.setdefault(name,[]).append({
                'bindingKind':'regionFacetInventory' if inventory else 'meshBoundaryOnly',
                'rank':rank,'patchIndex':index,'nativePatchName':patch['name'],
                'startFace':patch['startFace'],'nFaces':patch['nFaces'],
                'boundarySha256':boundary_sha,'sourceRegions':inventory,
            })
    return {name:{'schema':'dcdtuni.native-boundary-provenance/v1','patchRanges':rows}
            for name,rows in sorted(result.items())}

def verify_render_source(*,cpp,case_dir,session):
    """Called inside the CPP revision lock; all disk inputs are re-read/pinned.

    This requires the runner's archived exact execution manifest and CURRENT
    case mesh quality/source receipt. A later remesh invalidates an older run
    instead of borrowing current provenance. Immutable receipt archiving may
    extend that explicit path later; no fallback or self-asserted acceptance.
    """
    identity=require_session(session)
    if cpp._identity()!=identity or cpp.multi_block is None:
        raise ValueError('Common postprocess frame changed before source verification')
    case=Path(case_dir).resolve(strict=True)
    run=_inside(case,Path(cpp.run_dir),label='selected render run')
    if run.parent!=case/'log' or _sha(str(run).encode())!=identity['runId']:
        raise ValueError('Common postprocess run is outside the selected case')
    native_before=capture_native_inputs(run,identity['timeValue'])
    if getattr(cpp,'_native_input_snapshot',None)!=native_before:
        raise ValueError('Actual native bytes differ from the postprocess read')
    execution_path,execution_bytes,execution=_read_owned_json(run,run/FILENAME,'execution receipt')
    result=execution.get('result')
    if not isinstance(result,dict):raise ValueError('Execution receipt has no native result')
    relative=result.get('cfdrunRelativePath')
    if not isinstance(relative,str) or len(Path(relative).parts)!=2 or Path(relative).parts[0]!='model' or Path(relative).suffix!='.cfdrun':
        raise ValueError('Executed logical manifest is not in case/model')
    archive,archive_bytes,manifest=_read_owned_json(run,run/relative,'archived executed manifest')
    logical=case/relative
    snapshot=CfdrunSnapshot(archive_bytes,case_dir=case,logical_path=logical,expected_sha256=result.get('cfdrunSha256'))
    verify_air_run_execution_receipt(execution,case_dir=case,cfdrun_path=logical,run_dir=run,input_snapshot=snapshot)
    # That check rebuilt the receipt of its frame from the current bytes and found it equal to
    # `result`: for that frame it IS the selected receipt (the closing byte checks still run).
    selected=(result if result.get('timeValue')==identity['timeValue'] else
        build_air_run_result_receipt(case_dir=case,cfdrun_path=logical,run_dir=run,time_value=identity['timeValue'],input_snapshot=snapshot))
    mesh=case/'Mesh'
    quality_path,quality_bytes,_=_read_owned_json(case,case/'mesh-quality-receipt.json','strict mesh quality receipt')
    # Identity, not verdict: a run solved on a mesh that failed the strict check still renders (owner 2026-10-22).
    quality=verify_air_mesh_quality_receipt(quality_path,mesh,logical,input_snapshot=snapshot)
    if selected['meshSha256']!=quality['meshSha256']:
        raise ValueError('Selected run mesh differs from the checked mesh')
    source_project=manifest.get('sourceProject') or {};geometry=manifest.get('geometryExport') or {}
    source_regions=geometry.get('sourceRegions')
    if not source_regions:raise ValueError('Executed export lacks original emitter region/facet catalogue')
    source_path,source_bytes,source=_read_owned_json(case,case/'source-region-mesh-source.json','native region source receipt')
    version=source.get('format')
    source_keys={'format','cfdrunSha256','sourceRegionsSha256','stageCatalogueSha256','meshSha256','stageCatalogue','nativeCatalogues'}
    if version=='dcdtuni.source-region-mesh-source/v2':source_keys|={'meshInputIdentitySha256','triSurfaceSha256'}
    if set(source)!=source_keys:raise ValueError('Native source receipt has unexpected fields')
    pinned_paths=[(archive,archive_bytes),(execution_path,execution_bytes),(quality_path,quality_bytes),(source_path,source_bytes)]
    tri=mesh/'constant/triSurface'
    tri_before=STLGenRunner._stl_snapshot_sha256(SimpleNamespace(tri_surface_dir=tri))
    if version=='dcdtuni.source-region-mesh-source/v1':
        mesh_identity_ok=source['cfdrunSha256']==snapshot.sha256
    elif version=='dcdtuni.source-region-mesh-source/v2':
        mesh_identity_ok=(source_region_mesh_input_matches(source['meshInputIdentitySha256'],manifest) and
                          source['triSurfaceSha256']==tri_before)
        if source['cfdrunSha256']!=snapshot.sha256:
            reuse_path,reuse_bytes,reuse=_read_owned_json(case,case/'source-region-mesh-reuse.json','executed native mesh reuse receipt')
            pinned_paths.append((reuse_path,reuse_bytes))
            expected={'format':'dcdtuni.source-region-mesh-reuse/v1',
                'executionCfdrunSha256':snapshot.sha256,'meshCfdrunSha256':source['cfdrunSha256'],
                'meshSourceReceiptSha256':_sha(source_bytes),'meshInputIdentitySha256':source['meshInputIdentitySha256'],
                'sourceRegionsSha256':source['sourceRegionsSha256'],'stageCatalogueSha256':source['stageCatalogueSha256'],
                'meshSha256':source['meshSha256'],'triSurfaceSha256':tri_before}
            if reuse!=expected:raise ValueError('Mesh reuse receipt differs from actual executed source/native mesh')
    else:mesh_identity_ok=False
    stage=source['stageCatalogue']
    if not isinstance(stage,dict) or set(stage)!={'format','files','regions'} or stage.get('format')!='dcdtuni.source-region-stage/v1':
        raise ValueError('Original source stage catalogue is unavailable')
    if (not mesh_identity_ok
        or source['sourceRegionsSha256']!=canonical_sha(source_regions)
        or source['stageCatalogueSha256']!=canonical_sha(stage)
        or source['meshSha256']!=quality['meshSha256']):
        raise ValueError('Source receipt differs from executed export/strict mesh')
    # Every captured stage row must be the ORIGINAL executed binding, even if
    # its later mesh-stage geometry explicitly remained unresolved.
    original={key:value for key,value in source_regions.items()}
    rows=stage['regions']
    if not isinstance(rows,list) or len(rows)!=len(original):raise ValueError('Stage source binding set differs')
    seen=set()
    for row in rows:
        key=row.get('sourceKey') if isinstance(row,dict) else None
        if key not in original or key in seen or any(row.get(k)!=v for k,v in original[key].items()):
            raise ValueError('Stage source binding differs from exact executed export')
        seen.add(key)
    verify_source_region_stage_files(stage,mesh/'constant/triSurface')
    native=verify_native_source_region_ranges(mesh,stage)
    if native!=source['nativeCatalogues']:raise ValueError('Native final region catalogue changed')
    provenance=_boundary_provenance(mesh,native)
    # Preserve old OO declared-face meaning; this path publishes INDEPENDENT
    # native provenance, never region/facet IDs inside sourceFaceUids.
    uid=source_project.get('uid')
    if not isinstance(uid,str)or not uid:raise ValueError('Executed export has no project UID')
    convergence_path,convergence_bytes,convergence=_read_owned_json(run,run/'convergence.json','independent run convergence receipt')
    if convergence.get('verdict')not in {'CONVERGED','NOT_CONVERGED','INCONCLUSIVE','TRANSIENT_COMPLETED','TRANSIENT_INCOMPLETE'}:
        raise ValueError('Actual convergence receipt has no recognized independent verdict')
    run_convergence={'verdict':convergence['verdict'],'receiptSha256':_sha(convergence_bytes)}
    pinned_paths.append((convergence_path,convergence_bytes))
    public_source={
        'projectUid':uid,'projectContentSha256':selected['projectContentHash'],
        'cfdrunSha256':snapshot.sha256,
        'meshReceiptSha256':_sha(canonical_bytes({'qualitySha256':_sha(quality_bytes),'sourceSha256':_sha(source_bytes),'reuseSha256':_sha(reuse_bytes)if version=='dcdtuni.source-region-mesh-source/v2'and source['cfdrunSha256']!=snapshot.sha256 else None,'meshSha256':quality['meshSha256']})),
        'meshQuality':'accepted',
        'run':{'uid':selected['runId'],'receiptSha256':_sha(canonical_bytes({'executionSha256':_sha(execution_bytes),'selected':selected,'convergenceSha256':_sha(convergence_bytes)}))},
    }
    native_after=capture_native_inputs(run,identity['timeValue'])
    verify_source_region_stage_files(stage,mesh/'constant/triSurface')
    if (native_after!=native_before or cpp._identity()!=identity or
        mesh_identity_sha256(mesh)!=quality['meshSha256'] or
        STLGenRunner._stl_snapshot_sha256(SimpleNamespace(tri_surface_dir=tri))!=tri_before or
        any(path.is_symlink()or path.read_bytes()!=data for path,data in pinned_paths)):
        raise ValueError('Source/quality/execution/native bytes changed during verification')
    return {'schema':'dcdtuni.verified-air-result-source/v2','postprocess':identity,'source':public_source,
        'boundaryProvenanceByPatch':provenance,
        'fieldUnits':native_field_units(native_before),
        'nativeInputSha256':native_before['sha256'],'runConvergence':run_convergence}
