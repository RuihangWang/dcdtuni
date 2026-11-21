from pathlib import Path
import asyncio,copy,hashlib,importlib.util,json,math,sys,types,weakref
from pxr import Gf,Sdf,Usd,UsdGeom,Tf,CameraUtil
T=Path(__file__).resolve().parents[1]
mode=sys.argv[1];candidate=T/'candidate/src/frontend/omniverse/cfd_demo/kit/extensions/dc.cfd_bridge/dc/cfd_bridge'
assert mode in ('before','candidate')
D=T/((('before/' if mode=='before' else 'candidate/')+'src/frontend/omniverse/cfd_demo/kit/extensions/dc.cfd_bridge/dc/cfd_bridge'))
pkg=types.ModuleType('controlled_bridge');pkg.__path__=[str(D),str(candidate)];sys.modules[pkg.__name__]=pkg
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

from controlled_bridge import output_layout as o
s=stage();a=api(s);before=s.GetRootLayer().ExportToString()
foreign=p.sample_grid_viewport(weakref.proxy(a),s,r.CAMERA,viewport_window=Outer(),app_window=App(),output_layout=ControlledOwnedLayout())
assert s.GetRootLayer().ExportToString()==before
# Same assertion both before/candidate: a foreign duck cannot authorize output.
if sys.argv[2]=='duck':
 assert foreign['phase']=='pending' and foreign['reason']=='stream-layout-unresolved' and foreign['projection']is None,foreign
 print(json.dumps({'case':'foreign-duck','scope':'literal API / actual CPU USD; controlled native inputs, not live stream','phase':foreign['phase']}));raise SystemExit(0)
assert sys.argv[2]=='late-owner'
# This controlled ready sample bypasses the sampler solely to isolate the
# coordinator's ownership check. It is never a production/encoded-layout proof.
fixture_stage=stage(); aa=api(fixture_stage,texture=(1600,638),canvas=(1600,638))
# Build image projection with the same literal public camera/texture math; no
# output is accepted from candidate production (capture_current remains None).
from pxr import CameraUtil
camera=UsdGeom.Camera(fixture_stage.GetPrimAtPath(r.CAMERA)).GetCamera(aa.time);CameraUtil.ConformWindow(camera,CameraUtil.MatchHorizontally,1600/638)
projection=[float((aa.view*camera.frustum.ComputeProjectionMatrix())[i][j])for i in range(4)for j in range(4)]
grid_view=json.loads((T/'harness/canonical-grid.json').read_text())['view']
def view():
 return{'camera':cam(),'backgroundGrid':copy.deepcopy(grid_view),'selectedUids':[],'timeSeconds':None,'backgroundGridRaster':{'metricVersion':2,
 'line':{'owner':'CONTROLLED_GL','drawingBuffer':[1992,796],'css':[1992,796],'pixelRatio':1,'aliasedRange':[1,1],'effectiveWidthPx':1,'coordinateFrame':'scene-world','worldToClip':three['top']['worldToClip']},
 'output':{'owner':'CONTROLLED_MEDIA','revision':1,'decoded':[1600,638],'css':[1992,796],'layoutCss':[1992,796],'objectFit':'contain'},'binding':{'projectUid':'CONTROLLED_PROJECT','projectContentSha256':'a'*64,'sceneRevision':1,'viewRevision':1}}}
async def late():
 import threading
 stage_=fixture_stage;frame=[0];ctx={'projectUid':'CONTROLLED_PROJECT','projectContentSha256':'a'*64,'revision':1}; owner=[o.LayoutLease(object(),1,'same-numeric-label')];entered=threading.Event();release=threading.Event();calls=[0]
 def sample(_):return{'phase':'ready','projection':projection[:],'observations':{'imageNdcToOutputNdc':[float(Gf.Matrix4d(1)[i][j])for i in range(4)for j in range(4)]},'layoutLease':owner[0]}
 def calc(*args):
  calls[0]+=1;entered.set();assert release.wait(4),'private CPU carrier timeout';return g.calculate_ribbons(*args)
 kwargs={} if mode=='before' else {'layout_lease':lambda:owner[0]}
 coordinator=g.GridRasterCoordinator(lambda _:dict(ctx),sample,lambda:frame[0],calc,**kwargs)
 try:
  coordinator.offer(stage_,view());await coordinator._task;assert coordinator.receipt['phase']=='pending';frame[0]+=1;coordinator.notify()
  for _ in range(400):
   if entered.is_set():break
   await asyncio.sleep(.005)
  assert entered.is_set(),'worker never entered';raw=stage_.GetRootLayer().ExportToString();owner[0]=o.LayoutLease(object(),1,'same-numeric-label');release.set();await asyncio.wait_for(coordinator._task,4)
  # Same semantic assertion both versions; all camera/layout numbers are equal
  # but a different local native owner referent must prevent the delayed write.
  assert stage_.GetRootLayer().ExportToString()==raw and not stage_.GetPrimAtPath(r.BACKGROUND_GRID_ROOT+'/Stroke'),coordinator.receipt
  assert coordinator.carrier_contract(stage_)is None;assert calls[0]==1
  print(json.dumps({'case':'late-owner','phase':coordinator.receipt,'calculateCalls':calls[0],'sourceUnchanged':True,'scope':'actual CPU USD/product math with controlled executor and ready sample; no native/media proof'}))
 finally:release.set();coordinator.close()
asyncio.run(late())
