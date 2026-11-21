"""Actual accepted mesh-only display; no field/run/time authority is manufactured.

The same MeshPreviewLoader supplies original polygon edges to both carriers.
Region/facet inventory proves final boundary provenance, not OO face semantics.
"""
from __future__ import annotations
import hashlib,json,time,uuid
from pathlib import Path
from types import SimpleNamespace
from backend.aircooling.runner.cfdrun_snapshot import CfdrunSnapshot
from backend.aircooling.runner.air_mesh_quality_receipt import verify_air_mesh_quality_receipt
from backend.aircooling.runner.openfoam_patch_area import mesh_identity_sha256
from backend.aircooling.runner.source_region_catalogue import canonical_sha,source_region_mesh_input_sha,source_region_mesh_input_matches,verify_source_region_stage_files,verify_native_source_region_ranges
from backend.aircooling.runner.stlgen_runner import STLGenRunner
from backend.aircooling.runner.postprocess import MeshPreviewLoader
from backend.aircooling.runner.numeric_product_receipts import canonical_bytes
if __package__:
 from .verified_render_source import _read_owned_json,_boundary_provenance,_sha
 from .canonical_render_packet import _keys,_integer,_number,_spans
 from .canonical_render_grant import _publish_owned_bytes
 from .ov_result_dataset import _grant_module
else:
 from verified_render_source import _read_owned_json,_boundary_provenance,_sha
 from canonical_render_packet import _keys,_integer,_number,_spans
 from canonical_render_grant import _publish_owned_bytes
 from ov_result_dataset import _grant_module


def _manifest_for_quality(case,quality):
 # Literal content SHA selects the runner's checked manifest; no filename or
 # patch-name heuristic confers identity. Original bytes remain read-only.
 directory=case/'model'
 if directory.is_symlink():raise ValueError('mesh manifest directory is linked')
 matches=[]
 for path in sorted(directory.glob('*.cfdrun')):
  p,data,raw=_read_owned_json(case,path,'checked mesh manifest')
  if _sha(data)==quality['cfdrunSha256']:matches.append((p,data,raw))
 if not matches:raise ValueError('strict mesh receipt has no current literal manifest')
 return matches[0]


def verify_mesh_preview_source(*,case_dir,mesh_snapshot_key,current_snapshot):
 case=Path(case_dir).resolve(strict=True);mesh=case/'Mesh'
 if not isinstance(mesh_snapshot_key,str)or len(mesh_snapshot_key)!=64 or any(c not in '0123456789abcdef'for c in mesh_snapshot_key):raise ValueError('invalid mesh snapshot key')
 if current_snapshot()!=mesh_snapshot_key:raise ValueError('mesh preview selection changed before verification')
 qp,qb,qr=_read_owned_json(case,case/'mesh-quality-receipt.json','strict mesh quality receipt')
 mp,mb,manifest=_manifest_for_quality(case,qr)
 snapshot=CfdrunSnapshot(mb,case_dir=case,logical_path=mp,expected_sha256=qr['cfdrunSha256'])
 # Identity, not verdict: a mesh that failed the strict check is the one most worth looking at (owner 2026-10-22).
 quality=verify_air_mesh_quality_receipt(qp,mesh,mp,input_snapshot=snapshot)
 source_regions=(manifest.get('geometryExport')or{}).get('sourceRegions')
 if not source_regions:raise ValueError('checked mesh manifest lacks original emitter region inventory')
 sp,sb,source=_read_owned_json(case,case/'source-region-mesh-source.json','native region source receipt')
 expected={'format','cfdrunSha256','sourceRegionsSha256','stageCatalogueSha256','meshSha256','stageCatalogue','nativeCatalogues','meshInputIdentitySha256','triSurfaceSha256'}
 if set(source)!=expected or source['format']!='dcdtuni.source-region-mesh-source/v2':raise ValueError('mesh-only source requires actual source-region v2 receipt')
 tri=mesh/'constant/triSurface';tri_sha=STLGenRunner._stl_snapshot_sha256(SimpleNamespace(tri_surface_dir=tri))
 stage=source['stageCatalogue']
 if (not isinstance(stage,dict)or set(stage)!={'format','files','regions'}or stage.get('format')!='dcdtuni.source-region-stage/v1' or
     source['sourceRegionsSha256']!=canonical_sha(source_regions)or source['stageCatalogueSha256']!=canonical_sha(stage)or
     not source_region_mesh_input_matches(source['meshInputIdentitySha256'],manifest)or source['triSurfaceSha256']!=tri_sha or source['meshSha256']!=quality['meshSha256']):
  raise ValueError('original emitter inventory differs from checked mesh inputs/STL bytes')
 rows=stage['regions'];seen=set()
 if not isinstance(rows,list)or len(rows)!=len(source_regions):raise ValueError('stage region set differs from original emitter')
 for row in rows:
  key=row.get('sourceKey')if isinstance(row,dict)else None
  if key not in source_regions or key in seen or any(row.get(k)!=v for k,v in source_regions[key].items()):raise ValueError('stage source region identity differs from actual emitted manifest')
  seen.add(key)
 verify_source_region_stage_files(stage,tri);native=verify_native_source_region_ranges(mesh,stage)
 if native!=source['nativeCatalogues']:raise ValueError('native final region table changed')
 project=manifest.get('sourceProject')or{};uid=project.get('uid');content=project.get('contentHash')
 if not isinstance(uid,str)or not uid or not isinstance(content,str)or len(content)!=64 or any(c not in '0123456789abcdef'for c in content):raise ValueError('checked mesh manifest lacks actual OO project identity')
 # A fresh instance prevents cached old bytes from inheriting a new mesh proof.
 loader=MeshPreviewLoader(mesh);binary,patches=loader.load()
 payload_sha=_sha(binary);metadata_sha=_sha(canonical_bytes(patches))
 descriptor={'schema':'dcdtuni.verified-mesh-preview-source/v2','meshSnapshotKey':mesh_snapshot_key,
  'meshPayloadSha256':payload_sha,'meshMetadataSha256':metadata_sha,
  'source':{'projectUid':uid,'projectContentSha256':content,'cfdrunSha256':snapshot.sha256,
   'meshReceiptSha256':_sha(canonical_bytes({'qualitySha256':_sha(qb),'sourceSha256':_sha(sb),'meshSha256':quality['meshSha256']})),
   'meshQuality':'accepted','run':None},'boundaryProvenanceByPatch':_boundary_provenance(mesh,native)}
 # Validate all hidden/visible spans against original final boundary inventory.
 product=SimpleNamespace(payload=binary,metadata_json=canonical_bytes(patches))
 _spans(product,descriptor)
 verify_source_region_stage_files(stage,tri)
 if (current_snapshot()!=mesh_snapshot_key or mesh_identity_sha256(mesh)!=quality['meshSha256']or
     verify_native_source_region_ranges(mesh,stage)!=native or
     STLGenRunner._stl_snapshot_sha256(SimpleNamespace(tri_surface_dir=tri))!=tri_sha or
     any(p.is_symlink()or p.read_bytes()!=data for p,data in [(qp,qb),(mp,mb),(sp,sb)])):
  raise ValueError('mesh/source/strict quality changed while preparing display')
 return descriptor,product


def build_mesh_preview_packet(intent,descriptor,product):
 _keys(intent,['schema','revision','source','meshSnapshotKey','meshPayloadSha256','hidden','opacity'],'common mesh preview intent')
 if intent['schema']!='dcdtuni.common-mesh-preview-intent/v1':raise ValueError('unsupported common mesh preview intent')
 revision=_integer(intent['revision'],'mesh layer revision',1)
 for key in ['source','meshSnapshotKey','meshPayloadSha256']:
  if intent[key]!=descriptor[key]:raise ValueError('common mesh preview differs from current actual source/bytes')
 opacity=_number(intent['opacity'],'mesh opacity',low=0,high=1)
 hidden=intent['hidden'];names=set(descriptor['boundaryProvenanceByPatch'])
 if not isinstance(hidden,list)or any(not isinstance(v,str)for v in hidden)or hidden!=sorted(set(hidden))or not set(hidden)<=names:raise ValueError('hidden mesh patch list differs from actual boundary')
 spans,positions,triangles,edges,npoints=_spans(product,descriptor);layers=[]
 for kind,color in [('meshFaces',[0.1844749944900301,0.31854677812509186,0.4969329950515914]),('meshEdges',[0.005605391621829107,0.015996293361446288,0.057805430183792694])]:
  for patch,p0,t0,e0 in spans:
   name=patch['name'];count=patch['nTriangles']if kind=='meshFaces'else patch['nEdges']
   if name in hidden or not count:continue
   indices=triangles[t0*3:(t0+count)*3]if kind=='meshFaces'else edges[e0*2:(e0+count)*2]
   layers.append({'kind':kind,'layerId':kind+'/'+name,'visible':True,'meshPatchName':name,
    'boundaryProvenance':descriptor['boundaryProvenanceByPatch'][name],
    'positions':positions[p0*3:(p0+patch['nPoints'])*3].tolist(),
    'indices':(indices.astype('int64')-p0).tolist(),'opacity':opacity if kind=='meshFaces'else .35,'color':color})
 return {'schema':'dcdtuni.render-result-layers/v4','revision':revision,'source':descriptor['source'],
  'coordinateSystem':{'upAxis':'Y','metersPerUnit':1,'handedness':'right'},'timeSeconds':None,'layers':layers,
  'meshPreview':{'meshSnapshotKey':descriptor['meshSnapshotKey'],'meshPayloadSha256':descriptor['meshPayloadSha256'],'meshMetadataSha256':descriptor['meshMetadataSha256']}}


def issue_mesh_preview_grant(*,case_dir,mesh_snapshot_key,current_snapshot,request,key,now=None):
 _keys(request,['schema','scene','displayGeneration','intent'],'canonical mesh display request')
 if request['schema']!='dcdtuni.canonical-mesh-preview-request/v1':raise ValueError('unsupported mesh display request')
 scene=_keys(request['scene'],['projectUid','projectContentSha256','revision'],'acknowledged mesh scene');revision=_integer(scene['revision'],'scene revision');generation=_integer(request['displayGeneration'],'common display generation')
 descriptor,product=verify_mesh_preview_source(case_dir=case_dir,mesh_snapshot_key=mesh_snapshot_key,current_snapshot=current_snapshot)
 if scene['projectUid']!=descriptor['source']['projectUid']or scene['projectContentSha256']!=descriptor['source']['projectContentSha256']:raise ValueError('acknowledged scene differs from checked mesh project')
 packet=build_mesh_preview_packet(request['intent'],descriptor,product);payload=canonical_bytes(packet);digest=_sha(payload)
 if len(payload)>256*1024*1024:raise ValueError('mesh edge display exceeds artifact capacity')
 case=Path(case_dir).resolve();directory=case/'.ov-display/canonical-mesh'
 current=case
 for component in ['.ov-display','canonical-mesh']:
  current=current/component
  if current.is_symlink():raise ValueError('mesh display directory is linked')
  current.mkdir(exist_ok=True)
 target=_publish_owned_bytes(directory,digest+'.json',payload)
 after,again=verify_mesh_preview_source(case_dir=case_dir,mesh_snapshot_key=mesh_snapshot_key,current_snapshot=current_snapshot)
 if after!=descriptor or canonical_bytes(build_mesh_preview_packet(request['intent'],after,again))!=payload or target.is_symlink()or target.read_bytes()!=payload:raise ValueError('mesh source/display bytes changed before signature')
 stamp=time.time()if now is None else now
 grant=_grant_module().sign({'kind':'rendererMesh','path':str(target),'packetSha256':digest,
  'meshPayloadSha256':descriptor['meshPayloadSha256'],'meshMetadataSha256':descriptor['meshMetadataSha256'],'meshSnapshotKey':mesh_snapshot_key,
  'timeSeconds':None,'sceneRevision':revision,'displayGeneration':generation,'source':descriptor['source'],
  'issuedAt':stamp,'expiresAt':stamp+600,'nonce':uuid.uuid4().hex},key)
 return {'grant':grant,'packetReceipt':{'sha256':digest,'revision':packet['revision'],'source':packet['source'],'timeSeconds':None,'meshPreview':packet['meshPreview'],'sceneRevision':revision,'displayGeneration':generation}}
