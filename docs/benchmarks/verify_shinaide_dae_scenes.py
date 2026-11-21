"""Validate actual browser DAE frames and their portable CPU OpenUSD exports."""
import hashlib,importlib.util,json,os,signal,sys,time
from pathlib import Path
root=Path(__file__).resolve().parents[2]
for key in ('OMP_NUM_THREADS','MKL_NUM_THREADS','OPENBLAS_NUM_THREADS','PXR_WORK_THREAD_LIMIT'):os.environ[key]='1'
os.environ['CUDA_VISIBLE_DEVICES']=''
if hasattr(os,'sched_getaffinity'):
    os.sched_setaffinity(0,{min(os.sched_getaffinity(0))});os.nice(max(0,10-os.getpriority(os.PRIO_PROCESS,0)))

def deadline(*_):raise TimeoutError('60-second CPU USD verification budget exhausted')
signal.signal(signal.SIGALRM,deadline);signal.alarm(60)
started=time.perf_counter()
import numpy as np
from pxr import Usd,UsdGeom,Sdf
module_path=root/'src/frontend/omniverse/cfd_demo/kit/extensions/dc.cfd_bridge/dc/cfd_bridge/render_scene.py'
spec=importlib.util.spec_from_file_location('verified_render_scene',module_path)
render=importlib.util.module_from_spec(spec);spec.loader.exec_module(render)
folder=root/'case/Shinaide_Liquid/Render/scenes'
frames={}
for file in sorted(folder.glob('*.renderscene.json'),key=lambda p:p.stat().st_mtime_ns):
    data=json.loads(file.read_text());render.validate_snapshot(data)
    if data['view']['timeSeconds'] in (0,600):frames[data['view']['timeSeconds']]=(file,data)
if set(frames)!={0,600}:raise ValueError('requires actual saved browser frames at 0 and 600 seconds')
a,b=frames[0][1],frames[600][1]
left={p['id']:p for p in a['primitives']};right={p['id']:p for p in b['primitives']}
if left.keys()!=right.keys():raise ValueError('time selection changed primitive identities')
geometry=('kind','positions','indices','normals','uvs','matrix','modelUid','equipmentUid','visible')
if any(any(left[key].get(field)!=right[key].get(field) for field in geometry) for key in left):
    raise ValueError('temperature time selection changed physical geometry or ownership')
if a['textures']!=b['textures']:raise ValueError('time selection changed source texture data')
changed=[key for key in left if left[key]['material']!=right[key]['material']]
if not changed:raise ValueError('actual temperature overlay did not change any material')
output=folder.parent/'verified-20260926';output.mkdir(parents=True,exist_ok=True)
reports=[]
for time_value,(file,data) in sorted(frames.items()):
    stage=Usd.Stage.CreateInMemory()
    receipt=render.author_scene(stage,data,output/'textures',asset_prefix='textures')
    path=output/f'Shinaide_dae_t{time_value}s.usda';stage.GetRootLayer().Export(str(path))
    reopened=Usd.Stage.Open(str(path))
    if UsdGeom.GetStageUpAxis(reopened)!='Y' or UsdGeom.GetStageMetersPerUnit(reopened)!=1:
        raise ValueError('export changed coordinate units/up axis')
    scene_root=reopened.GetPrimAtPath(render.ROOT)
    if scene_root.GetCustomDataByKey('dcdtuni:projectUid')!=data['project']['uid'] or scene_root.GetCustomDataByKey('dcdtuni:timeSeconds')!=time_value:
        raise ValueError('USD lost source project/time identity')
    max_position_error=0.;references=0
    for source in data['primitives']:
        suffix='p_'+hashlib.sha256(source['id'].encode()).hexdigest()[:24]
        prim=reopened.GetPrimAtPath(render.ROOT+'/Geometry/'+suffix)
        if prim.GetCustomDataByKey('dcdtuni:modelUid')!=source['modelUid'] or prim.GetCustomDataByKey('dcdtuni:primitiveId')!=source['id']:
            raise ValueError('USD lost primitive/object identity')
        points=np.asarray(prim.GetAttribute('points').Get(),dtype=float)
        expected=np.asarray(source['positions'],dtype=float).reshape(-1,3)
        if source['kind']!='triangles':expected=expected[source['indices']]
        if not np.allclose(points,expected,atol=1e-6,rtol=3e-6):raise ValueError('USD coordinates differ from browser scene')
        if points.size:max_position_error=max(max_position_error,float(np.abs(points-expected).max()))
        matrix=np.asarray(UsdGeom.Xformable(prim).GetLocalTransformation(),dtype=float)
        if not np.allclose(matrix,np.asarray(source['matrix']).reshape(4,4),atol=1e-12,rtol=1e-12):
            raise ValueError('USD transform differs from browser transform')
        shader=reopened.GetPrimAtPath(render.ROOT+'/Looks/'+suffix+'/Surface')
        color_attribute='inputs:emissiveColor' if source['material']['unlit'] else 'inputs:diffuseColor'
        if not np.allclose(shader.GetAttribute(color_attribute).Get(),source['material']['color'],atol=1e-7,rtol=1e-6):
            raise ValueError('USD lost temperature/physical material color')
    for prim in reopened.Traverse():
        for attr in prim.GetAttributes():
            value=attr.Get()
            if isinstance(value,Sdf.AssetPath):
                references+=1
                if Path(value.path).is_absolute() or not (output/value.path).is_file():
                    raise ValueError('USD contains an absolute or unresolved texture asset')
    reports.append({'time_seconds':time_value,'snapshot':str(file.relative_to(root)),
        'snapshot_sha256':hashlib.sha256(file.read_bytes()).hexdigest(),
        'usd':str(path.relative_to(root)),'usd_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
        'usd_bytes':path.stat().st_size,'primitives':receipt['primitives'],'models':len(receipt['uidPaths']),
        'textures':len(data['textures']),'resolved_relative_texture_references':references,
        'max_abs_position_error_m':max_position_error})
report={'status':'passed','scope':'actual browser DAE time-selected colored scene and CPU OpenUSD; no Kit/RTX/live OV/GPU verification',
    'project':a['project'],'geometry_and_UIDs_identical_between_frames':True,'texture_data_identical_between_frames':True,
    'changed_material_primitives':len(changed),'changed_model_uids':sorted({left[key]['modelUid'] for key in changed}),
    'frames':reports,'texture_files':len(list((output/'textures').glob('*.png'))),
    'source_sha256':hashlib.sha256(module_path.read_bytes()).hexdigest(),'walltime_seconds':time.perf_counter()-started,
    'cpu_affinity':sorted(os.sched_getaffinity(0)) if hasattr(os,'sched_getaffinity') else 'not available',
    'cuda_visible_devices':os.environ['CUDA_VISIBLE_DEVICES'],'live_omniverse_validation':'not_run'}
(root/'docs/benchmarks/shinaide-dae-scene-export-2026-09-26.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report),flush=True)
