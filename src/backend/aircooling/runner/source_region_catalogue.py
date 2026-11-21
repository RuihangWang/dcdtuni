"""Independent original STL region/facet inventory and native range provenance.

This binds an entire literal emitted region to native written patch ranges.
It never creates a declared OO physical face or per-cut-face triangle ancestry.
Unknown/rewritten regions stay explicitly unresolved; their physics is unchanged.
"""
from __future__ import annotations
import hashlib
import json
import re
from pathlib import Path
from collections import defaultdict
from backend.aircooling.runner.cfdrun_schema import GeometryExport

_FOAM_NAME=re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
_UID=re.compile(r"^[A-Za-z0-9_./:-]+$")
_REGION=re.compile(r"(?m)^solid[ \t]+([^\r\n]+)\r?\n([\s\S]*?)^endsolid[ \t]+([^\r\n]+)[ \t]*\r?\n?")

def canonical_sha(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()

def source_region_mesh_input_sha(raw):
    """Pin actual mesh geometry and roles, independently of execution metadata.

    This intentionally includes conservative OO-derived physics/role inputs;
    changing them requires a fresh mesh. Only run names/timestamps, output and
    solver/time settings may vary between mesh generation and solve-only.
    Missing fields stay distinct from explicit empty fields. No older receipt
    can acquire this identity by inference.

    v3 (2026-10-20) leaves out how the mesher was run -- its rank count,
    parallel switch and time target (_MESH_EXECUTION_FIELDS) -- which do not
    change the mesh (dcCart is rank-invariant, and the mesh bytes are pinned
    by the receipt's meshSha256): Base604 meshed on 16 ranks was refused for
    solve-only because the reloaded editor exported meshProcess 8.

    v4 (2026-10-28) also leaves out the operating values -- airflows, powers,
    temperatures, porosity coefficients, sensor probes (_without_operating_values):
    none of them builds the mesh, and with them in, changing an ACU airflow on a
    dcCart mesh was refused for solve-only. Patch names, roles and types stay in,
    and the STL bytes are pinned by the receipt's triSurface hash.

    v5 (2026-10-29) also leaves out the editor's view switches kept in the mesh
    settings -- the locationInMesh marker and the background-block editing modes
    (_MESH_VIEW_FIELDS): showing the marker made a dcCart mesh look stale.

    v6 (2026-10-30) also leaves out perforation values -- a rack door's or a
    baffle's open area, hole size and shape (_without_perforation_values):
    they set the porous jump, not the mesh, and with them in, opening
    Alibaba_hangchou's doors from 64 to 99 % was refused for solve-only.
    Whether the enabled rack doors' perforations differ stays in: it decides
    one merged door patch or one per door.
    """
    return _mesh_input_sha(raw,'v6')

_MESH_INPUT_FIELDS=['geometryExport','meshConfig','meshGeometry','boundaryBindings',
                    'porousBindings','sourceTerms','inputs','geometryTopology']
_MESH_EXECUTION_FIELDS=('meshProcess','parallelMesh','meshTimeLimitSeconds')
#: Editor view state saved with the mesh settings; it draws, it does not mesh.
_MESH_VIEW_FIELDS=('locInMeshVisible','blockMeshEditActive','blockMeshMoveMode')

def _without_mesh_execution(inputs):
    import copy
    inputs=copy.deepcopy(inputs)
    for config in (inputs.get('meshConfig'),(inputs.get('meshGeometry') or {}).get('meshConfig')):
        if isinstance(config,dict):
            for key in _MESH_EXECUTION_FIELDS: config.pop(key,None)
    return inputs

def _without_mesh_view(inputs):
    import copy
    inputs=copy.deepcopy(inputs)
    for config in (inputs.get('meshConfig'),(inputs.get('meshGeometry') or {}).get('meshConfig')):
        if isinstance(config,dict):
            for key in _MESH_VIEW_FIELDS: config.pop(key,None)
    return inputs

#: What a physics block keeps in v4: what decides a patch's type or a baffle's kind, not its values.
_STRUCTURAL_PHYSICS=('role','type','bcType','baffleType','kind')

def _without_operating_values(inputs):
    import copy
    inputs=copy.deepcopy(inputs)
    inputs.pop('inputs',None)
    for key in ('boundaryBindings','porousBindings'):
        for binding in (inputs.get(key) or {}).values():
            if isinstance(binding,dict): binding.pop('parameters',None)
    def strip(node):
        if isinstance(node,dict):
            for key in list(node):
                if key in ('physics','patchPhysics') and isinstance(node[key],dict):
                    node[key]={k:v for k,v in node[key].items() if k in _STRUCTURAL_PHYSICS} if key=='physics' else {
                        name:({k:v for k,v in p.items() if k in _STRUCTURAL_PHYSICS} if isinstance(p,dict) else p)
                        for name,p in node[key].items()}
                else: strip(node[key])
        elif isinstance(node,list):
            for item in node: strip(item)
    geometry=inputs.get('meshGeometry')
    if isinstance(geometry,dict):
        geometry.pop('sensorPoints',None)
        strip(geometry)
    return inputs

_PERFORATION_KEYS=('perforated','perforation')

def _without_perforation_values(inputs):
    import copy
    inputs=copy.deepcopy(inputs)
    geometry=inputs.get('meshGeometry')
    if not isinstance(geometry,dict): return inputs
    # the runner's door split (_split_internal_face_solids): enabled doors whose
    # perforations differ each get their own patch, identical ones share one
    doors=set()
    for rack in (geometry.get('racks') or {}).values():
        for side in ('inlet','outlet'):
            door=rack.get(f'{side}Door') if isinstance(rack,dict) else None
            if isinstance(door,dict) and door.get('enabled'):
                doors.add(json.dumps(door.get('perforated') or door.get('perforation'),sort_keys=True))
    def strip(node):
        if isinstance(node,dict):
            for key in list(node):
                if key in _PERFORATION_KEYS and isinstance(node[key],dict): node[key]='perforated'
                else: strip(node[key])
        elif isinstance(node,list):
            for item in node: strip(item)
    strip(geometry)
    geometry['rackDoorPerforationsDiffer']=len(doors)>1
    return inputs

def _mesh_input_sha(raw,version):
    project=raw.get('sourceProject')
    if not isinstance(project,dict) or not isinstance(project.get('uid'),str) or not project['uid']:
        raise ValueError('source-region mesh input lacks its real OO project UID')
    inputs={key:raw[key] for key in _MESH_INPUT_FIELDS if key in raw}
    if version=='v6': inputs=_without_perforation_values(_without_mesh_view(_without_operating_values(_without_mesh_execution(_without_handed_heat(inputs)))))
    elif version=='v5': inputs=_without_mesh_view(_without_operating_values(_without_mesh_execution(_without_handed_heat(inputs))))
    elif version=='v4': inputs=_without_operating_values(_without_mesh_execution(_without_handed_heat(inputs)))
    elif version=='v3': inputs=_without_mesh_execution(_without_handed_heat(inputs))
    elif version=='v2': inputs=_without_handed_heat(inputs)
    return canonical_sha({'format':f'dcdtuni.source-region-mesh-input/{version}',
        'sourceProject':{'uid':project['uid'],'schemaVersion':project.get('schemaVersion')},
        'meshInputs':inputs})

def source_region_mesh_input_matches(recorded,raw):
    """A receipt's identity against this manifest: v2, or the stricter v1 a
    receipt written before v2 carries (every field, handed heat included)."""
    # In turn, stopping at the first match: each one deep-copies the manifest.
    return isinstance(recorded,str) and any(
        recorded==_mesh_input_sha(raw,version) for version in ('v6','v5','v4','v3','v2','v1'))

# What a liquid result's heat handoff rewrites in the air manifest it is
# solved with (hybrid_air_heat_handoff): the heat values, and the STL file and
# measured area it indexes for each heated solid. None of it builds the mesh,
# and the STL bytes stay pinned by the receipt's own triSurface hash.
_HANDED_HEAT_PARAMETERS=('powerW','temperatureC','heatFluxWm2')

def _without_handed_heat(inputs):
    import copy
    inputs=copy.deepcopy(inputs)
    for binding in (inputs.get('boundaryBindings') or {}).values():
        parameters=binding.get('parameters') if isinstance(binding,dict) else None
        if isinstance(parameters,dict):
            for key in _HANDED_HEAT_PARAMETERS: parameters.pop(key,None)
    for rack in ((inputs.get('meshGeometry') or {}).get('racks') or {}).values():
        for server in (rack.get('servers') or []) if isinstance(rack,dict) else []:
            physics=server.get('physics') if isinstance(server,dict) else None
            if isinstance(physics,dict): physics.pop('power',None)
    for solid in ((inputs.get('geometryExport') or {}).get('solids') or {}).values():
        if isinstance(solid,dict):
            solid.pop('stlFileId',None); solid.pop('areaM2',None)
    return inputs

def build_source_region_stage_catalogue(geometry_export,tri_surface_dir,mesh_stl_solids=None):
    """Join only unique literal emitted bytes to the actual mesh-stage inventory.

    Names select candidates. Exact original region SHA and facet count decide
    the join; transformed or aggregated bytes do not inherit an invented UID.
    """
    geometry=GeometryExport.model_validate(geometry_export);directory=Path(tri_surface_dir)
    selected=set(mesh_stl_solids) if mesh_stl_solids is not None else {row.path for row in geometry.stlFiles.values()}
    by_solid=defaultdict(list);files={}
    for filename in sorted(selected):
        if not isinstance(filename,str) or not _FOAM_NAME.fullmatch(filename) or not filename.endswith('.stl'):
            raise ValueError('unsafe mesh-stage STL filename')
        path=directory/filename
        if not path.is_file() or path.is_symlink():raise ValueError('mesh-stage source STL is missing or symlinked')
        data=path.read_bytes();files[filename]=hashlib.sha256(data).hexdigest()
        for match in _REGION.finditer(data.decode('utf-8')):
            solid=match[1].strip()
            if solid!=match[3].strip():raise ValueError('mesh-stage source region delimiters differ')
            if mesh_stl_solids is not None and solid not in mesh_stl_solids[filename]:continue
            by_solid[solid].append((filename,hashlib.sha256(match[0].encode()).hexdigest(),
                len(re.findall(r'\bfacet[ \t]+normal\b',match[2]))))
    rows=[]
    for key,binding in sorted(geometry.sourceRegions.items()):
        for field in ['sourceEntityUid','sourceRegionUid']:
            if not _UID.fullmatch(getattr(binding,field)):raise ValueError('unsafe source region UID')
        if not _FOAM_NAME.fullmatch(binding.solidName):raise ValueError('unsafe source region name')
        candidates=by_solid.get(binding.solidName,[])
        matched=None;reason=None
        if len(candidates)!=1:reason='missing-or-ambiguous-mesh-stage-region'
        elif candidates[0][1:]!=(binding.sourceRegionSha256,binding.sourceFacetCount):reason='mesh-stage-region-bytes-were-transformed'
        else:matched=candidates[0][0]
        rows.append({'sourceKey':key,**binding.model_dump(),
            'originalStlFile':geometry.stlFiles[binding.stlFileId].path,
            'meshStageStlFile':matched,'unresolvedReason':reason})
    return {'format':'dcdtuni.source-region-stage/v1','files':files,'regions':rows}

def verify_source_region_stage_files(catalogue, tri_surface_dir):
    directory=Path(tri_surface_dir)
    for filename,digest in catalogue.get('files',{}).items():
        if not isinstance(filename,str) or not _FOAM_NAME.fullmatch(filename):raise ValueError('unsafe mesh-stage catalogue file')
        path=directory/filename
        if not path.is_file() or path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
            raise ValueError('source-region mesh-stage bytes changed after catalogue capture')


def render_source_regions_dict(catalogue):
    by_file=defaultdict(list)
    for row in catalogue['regions']:
        if row['unresolvedReason'] is None:by_file[row['meshStageStlFile']].append(row)
    if not catalogue['regions']:return ''
    lines=['dcdtuniSourceRegions','{']
    for filename in sorted(by_file):
        lines.extend([f'    {filename}','    {'])
        for row in sorted(by_file[filename],key=lambda r:r['solidName']):
            lines.extend([f'        {row["solidName"]}','        {',
                f'            sourceEntityUid "{row["sourceEntityUid"]}";',
                f'            sourceRegionUid "{row["sourceRegionUid"]}";',
                f'            sourceRegionSha256 "{row["sourceRegionSha256"]}";',
                f'            sourceFacetCount {row["sourceFacetCount"]};',
                f'            operation {row["operation"]};','        }'])
        lines.append('    }')
    return '\n'.join(lines+['}',''])

def verify_native_source_region_ranges(mesh_dir,stage_catalogue):
    """Check every rank's native producer rows against its exact final boundary.

    The native file's surface/local-region address came from the actual mesher,
    not inferred here from patch names. A full mesh hash is captured by caller.
    """
    from backend.aircooling.runner.mesh_layout import mesh_directories
    from backend.aircooling.runner.postprocess import MeshPreviewLoader
    roots=mesh_directories(Path(mesh_dir))
    if not roots:raise ValueError('source regions require a complete written mesh')
    expected={r['sourceRegionUid']:r for r in stage_catalogue['regions'] if r['unresolvedReason'] is None}
    captures=[]
    for rank,poly in enumerate(roots):
        path=poly.parent/'dcCartSourceRegions.json'
        if not path.is_file() or path.is_symlink():raise ValueError('native source-region catalogue is absent or symlinked')
        data=path.read_bytes();native=json.loads(data)
        if set(native)!={'format','rank','rankCount','bindingGranularity','regions'} or type(native.get('rank'))is not int or type(native.get('rankCount'))is not int:
            raise ValueError('native source-region envelope has invalid or extra fields')
        if (native.get('format')!='dcdtuni.native-source-regions/v1' or
            native.get('rank')!=rank or native.get('rankCount')!=len(roots) or
            native.get('bindingGranularity')!='patch-region-facet-inventory'):
            raise ValueError('native source-region rank/format identity differs')
        patches=MeshPreviewLoader._parse_boundary(poly/'boundary');seen=set();rows=[]
        if not isinstance(native.get('regions'),list):raise ValueError('native source-region rows are not a list')
        for row in native['regions']:
            if not isinstance(row,dict) or set(row)!={'sourceEntityUid','sourceRegionUid','sourceRegionSha256','sourceFacetCount','sourceFacetUidPrefix','stlFile','solidName','operation','nativeSurfaceIndex','nativeLocalRegionIndex','unresolvedReason','patchRanges'}:
                raise ValueError('native source-region row has invalid or extra fields')
            if type(row.get('sourceFacetCount'))is not int:raise ValueError('native source facet count is not an integer')
            uid=row.get('sourceRegionUid');original=expected.get(uid)
            if original is None or uid in seen:raise ValueError('native source region is unknown or duplicated')
            seen.add(uid)
            for name in ['sourceEntityUid','sourceRegionSha256','sourceFacetCount','solidName','operation']:
                if row.get(name)!=original[name]:raise ValueError('native source-region identity/facet inventory differs')
            if row.get('stlFile')!=original['meshStageStlFile'] or row.get('sourceFacetUidPrefix')!=uid+'/facet/':
                raise ValueError('native source region points to a different stage/facet namespace')
            for name in ['nativeSurfaceIndex','nativeLocalRegionIndex']:
                if type(row.get(name))is not int or row[name]<0:raise ValueError('invalid native STL region address')
            ranges=row.get('patchRanges');reason=row.get('unresolvedReason')
            if not isinstance(ranges,list) or (reason is not None and (not isinstance(reason,str) or not reason or ranges)):
                raise ValueError('native unresolved range contract is invalid')
            if reason is None and not ranges:raise ValueError('native resolved region has no final patch ranges')
            patch_ids=[]
            for span in ranges:
                if not isinstance(span,dict) or set(span)!={'patchIndex','patchName','startFace','nFaces'}:raise ValueError('native patch range has invalid or extra fields')
                index=span.get('patchIndex')
                if type(index)is not int or index<0 or index>=len(patches):raise ValueError('native patch address outside final table')
                patch=patches[index]
                if (span.get('patchName')!=patch['name'] or span.get('startFace')!=patch['startFace'] or
                    span.get('nFaces')!=patch['nFaces'] or type(span.get('startFace'))is not int or type(span.get('nFaces'))is not int or span['startFace']<0 or span['nFaces']<0):
                    raise ValueError('native range differs from the final written boundary')
                if index in patch_ids:raise ValueError('duplicate native patch range')
                patch_ids.append(index)
            if patch_ids!=sorted(patch_ids):raise ValueError('native patch ranges are not canonical')
            rows.append(row)
        if seen!=set(expected):raise ValueError('native source-region set is incomplete')
        if [r['sourceRegionUid'] for r in rows]!=sorted(expected):raise ValueError('native source-region rows are not canonical')
        if path.read_bytes()!=data:raise ValueError('native source-region catalogue changed during verification')
        captures.append({'rank':rank,'nativeCatalogueSha256':hashlib.sha256(data).hexdigest(),
            'boundarySha256':hashlib.sha256((poly/'boundary').read_bytes()).hexdigest(),'regions':rows})
    return captures
