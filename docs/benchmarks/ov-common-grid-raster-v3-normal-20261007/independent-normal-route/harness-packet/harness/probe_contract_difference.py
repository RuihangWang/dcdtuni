from pathlib import Path
import asyncio,copy,hashlib,importlib.util,json,math,sys,types,weakref
from pxr import Gf,Sdf,Usd,UsdGeom,Tf,CameraUtil
T=Path(__file__).resolve().parents[1]
D=T/'candidate/src/frontend/omniverse/cfd_demo/kit/extensions/dc.cfd_bridge/dc/cfd_bridge'
pkg=types.ModuleType('controlled_bridge');pkg.__path__=[str(D)];sys.modules[pkg.__name__]=pkg
from controlled_bridge import render_scene as r,grid_raster as g,grid_viewport as p
fixture=json.loads((T/'harness/canonical-grid.json').read_text());grid=fixture['view'];three=json.loads((T/'harness/actual-three-projections.json').read_text())
checks=[]
def check(name,fn):
 fn();checks.append(name)
def cam(aspect=1992/796):return{'position':[0,20,0],'target':[0,0,0],'up':[0,0,-1],'fov':45,'near':.1,'far':400,'aspect':aspect}
def stage():
 s=Usd.Stage.CreateInMemory();UsdGeom.Xform.Define(s,r.ROOT);r.apply_view(s,{'camera':cam(),'backgroundGrid':grid,'selectedUids':[],'timeSeconds':None});return s
# Actual installed complete API module. Only native settings/USD-context/capture
# carriers are controlled. No API method, projection/conformance or sampler stub.
settings=types.SimpleNamespace(get=lambda _:None,subscribe_to_node_change_events=lambda *_:None,unsubscribe_to_change_events=lambda *_:None)
carb=types.ModuleType('carb');carb.settings=types.SimpleNamespace(get_settings=lambda:settings,SubscriptionId=object);sys.modules['carb']=carb
omni=types.ModuleType('omni');omni.usd=types.ModuleType('omni.usd');contexts={};omni.usd.get_context=lambda name:contexts[name];sys.modules['omni']=omni;sys.modules['omni.usd']=omni.usd
for name in ['literal_viewport','literal_viewport.impl']:
 mod=types.ModuleType(name);mod.__path__=[str(T/'harness')];sys.modules[name]=mod
util=types.ModuleType('literal_viewport.impl.utility');util._report_error=lambda *_:None;sys.modules[util.__name__]=util
capture=types.ModuleType('literal_viewport.capture');capture.Capture=object;sys.modules[capture.__name__]=capture
spec=importlib.util.spec_from_file_location('literal_viewport.api',T/'harness/ViewportAPI.py');api_module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=api_module;spec.loader.exec_module(api_module)
assert hashlib.sha256((T/'harness/ViewportAPI.py').read_bytes()).hexdigest()=='0f2ed3f0b60162aca96ab7d0bac983f4735a88e786acfd6c77ce0c348d01a22c'
def api(s,texture=(1600,900),canvas=(1600,638),scaled=None):
 name=s.GetRootLayer().identifier;contexts[name]=types.SimpleNamespace(get_stage=lambda:s)
 a=api_module.ViewportAPI(name,'CONTROLLED_NATIVE_TEXTURE',None)
 a._ViewportAPI__viewport_texture=types.SimpleNamespace(resolution=scaled or texture,full_resolution=texture,camera_path=r.CAMERA)
 # This literal native producer entry is called only in the test harness to
 # deliver a controlled frame. Production sampling uses public getters only.
 a._sync_viewport_api(UsdGeom.Camera(s.GetPrimAtPath(r.CAMERA)),canvas,force_update=True)
 return a
class App:
 def get_size(self):return types.SimpleNamespace(x=1600,y=638)
 def get_position(self):return types.SimpleNamespace(x=0,y=0)
 def get_dpi_scale(self):return 1
 def get_ui_scale(self):return 1
class Outer:
 frame=types.SimpleNamespace(screen_position_x=0,screen_position_y=0,computed_content_width=1600,computed_content_height=638)
 viewport_widget=types.SimpleNamespace(name='')
class ControlledOwnedLayout:
 # Explicit native controlled carrier fixture, never browser or runtime proof.
 def __init__(self,m=None):self.m=m or Gf.Matrix4d(1)
 def capture_current(self,*_):return{'imageNdcToOutputNdc':[float(self.m[i][j])for i in range(4)for j in range(4)]}

def public_actual():
 s=stage();a=api(s);sample=p.sample_grid_viewport(weakref.proxy(a),s,r.CAMERA,viewport_window=Outer(),app_window=App())
 assert sample['phase']=='pending' and sample['reason']=='stream-layout-unresolved';o=sample['observations'];assert o['layoutProof']=='unresolved' and o['resolution']==[1600,900] and o['appSizePx']==[1600,638]
 assert any(not z['insideTexture'] for z in o['ndcToTextureSamples'])
 assert o['imageWorldToNdc']!=o['worldToNdc'];assert len(o['transform'])==16 and sample['projection']is None
 current=copy.deepcopy(o);assert p.sample_grid_viewport(a,s,r.CAMERA,output_layout={'fake':'wire claim'})['projection']is None
 # Same width camera in a16:9 texture shrinks when displayed in a wide canvas.
 local=Gf.Vec4d(1,0,0,1);ui=local*Gf.Matrix4d(*o['worldToNdc']);image=local*Gf.Matrix4d(*o['imageWorldToNdc']);ratio=(ui[0]/ui[3])/(image[0]/image[3]);assert abs(ratio-(1600/900)/(1600/638))<1e-12
 (T/'logs/controlled-width-ratio.json').write_text(json.dumps({'scope':'actual installed API with controlled texture/canvas; not actual runtime root cause','ratio':ratio,'predicted780px':780*ratio,'observations':current},indent=2))
 a._sync_viewport_api(UsdGeom.Camera(s.GetPrimAtPath(r.CAMERA)),(1600,638),force_update=True)
 assert p.sample_grid_viewport(a,s,r.CAMERA,output_layout=ControlledOwnedLayout())['phase']=='ready'
check('actual full public API weakproxy/map/letterbox + honest unresolved layout',public_actual)
def stale_api():
 s=stage();a=api(s);newcam=cam(1600/590);r.apply_view(s,{'camera':newcam,'selectedUids':[],'timeSeconds':None})
 assert p.sample_grid_viewport(a,s,r.CAMERA,output_layout=ControlledOwnedLayout())['reason']=='viewport-projection-frame-not-current'
 wrong=stage();assert p.sample_grid_viewport(a,wrong,r.CAMERA)['reason']=='viewport-scene-not-current'
 assert p.sample_grid_viewport(a,s,'/World/WrongCamera')['reason']=='viewport-scene-not-current'
 a._sync_viewport_api(UsdGeom.Camera(s.GetPrimAtPath(r.CAMERA)),(1600,590),force_update=True)
 assert p.sample_grid_viewport(a,s,r.CAMERA,output_layout=ControlledOwnedLayout())['phase']=='ready'
 a._ViewportAPI__viewport_texture.resolution=(800,450);assert p.sample_grid_viewport(a,s,r.CAMERA)['observations']['resolution']==[800,450]
check('stale same-pose aspect / stage / camera path and scaled texture',stale_api)
def view(revision=1,owner='CONTROLLED_MEDIA',time=None):
 return{'camera':cam(),'backgroundGrid':copy.deepcopy(grid),'selectedUids':[],'timeSeconds':time,'backgroundGridRaster':{'metricVersion':2,
 'line':{'owner':'CONTROLLED_GL','drawingBuffer':[1992,796],'css':[1992,796],'pixelRatio':1,'aliasedRange':[1,1],'effectiveWidthPx':1,'coordinateFrame':'scene-world','worldToClip':three['top']['worldToClip']},
 'output':{'owner':owner,'revision':1,'decoded':[1600,638],'css':[1992,796],'layoutCss':[1992,796],'objectFit':'contain'},'binding':{'projectUid':'CONTROLLED_PROJECT','projectContentSha256':'a'*64,'sceneRevision':1,'viewRevision':revision}}}

def presentation(width=1):
 v=view()
 v['backgroundGridRaster']['line']['effectiveWidthPx']=width
 return {k:v['backgroundGridRaster'][k]for k in ('metricVersion','line','output')}

def canonical_and_lower():
 s=stage();a=api(s,texture=(1600,638),canvas=(1600,638));sample=p.sample_grid_viewport(a,s,r.CAMERA,output_layout=ControlledOwnedLayout());assert sample['phase']=='ready'
 points,colors=g.canonical_grid(grid)
 assert [x for point in points for x in point]==fixture['positions'];assert [x for color in colors for x in color]==fixture['colors']
 before=copy.deepcopy((points,colors,grid));data=g.calculate_ribbons(grid,points,colors,sample['projection'],[1600,638],presentation())
 assert data['points'];assert all(pt[1]==0 for pt in data['points']);assert(points,colors,grid)==before
 contract=g.author_ribbons(s,grid,data,'CONTROLLED_READY_KEY');assert g.carrier_matches(s,'CONTROLLED_READY_KEY',contract)
 raw=s.GetRootLayer().ExportToString();notices=[];sub=Tf.Notice.Register(Usd.Notice.ObjectsChanged,lambda n,s:notices.append(n),s)
 for _ in range(12):assert g.carrier_matches(s,'CONTROLLED_READY_KEY',contract)
 assert not notices and s.GetRootLayer().ExportToString()==raw;sub.Revoke()
 mesh=UsdGeom.Mesh(s.GetPrimAtPath(r.BACKGROUND_GRID_ROOT+'/Stroke'));ps=list(mesh.GetPointsAttr().Get());ps[0]=Gf.Vec3f(9,0,9);mesh.GetPointsAttr().Set(ps);assert not g.carrier_matches(s,'CONTROLLED_READY_KEY',contract)
 for dims in [[1600,0],[True,638],[1601,638],[1600,901]]:
  try:g.calculate_ribbons(grid,points,colors,sample['projection'],dims,presentation())
  except g.GridRefused:pass
  else:raise AssertionError('invalid raster accepted')
 for width in [True,0,float('nan'),9]:
  try:g.calculate_ribbons(grid,points,colors,sample['projection'],[1600,638],presentation(width))
  except g.GridRefused:pass
  else:raise AssertionError('invalid width accepted')

s=stage();a=api(s,texture=(1600,638),canvas=(1600,638));sample=p.sample_grid_viewport(a,s,r.CAMERA,output_layout=ControlledOwnedLayout());points,colors=g.canonical_grid(grid);data=g.calculate_ribbons(grid,points,colors,sample['projection'],[1600,638],presentation())
original=g.carrier_matches

def diagnostic(stage,key,contract):
 actual,_=g._composed_contract(stage,contract.paths)
 for expected,observed in zip(contract.expected,actual):
  if expected!=observed:
   for i,(x,y)in enumerate(zip(expected,observed)):
    if x!=y:print('controlledDiff',expected[0],i,repr(x)[:600],repr(y)[:600])
 return original(stage,key,contract)
g.carrier_matches=diagnostic
try:g.author_ribbons(s,grid,data,'CONTROLLED_KEY')
except g.GridRefused:pass
