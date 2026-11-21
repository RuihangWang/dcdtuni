import argparse,ast,asyncio,contextlib,copy,dataclasses,hashlib,io,json,os,sys,types
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
ROOT=Path(__file__).resolve().parent
REL=Path('src/frontend/omniverse/cfd_demo/kit/extensions/dc.cfd_bridge/dc/cfd_bridge')
B=Path('/mnt/lv/home/litianyu/mycode/dcdtuni').resolve()
a=argparse.ArgumentParser();a.add_argument('--output',required=True);out=Path(a.parse_args().output).resolve()
if not out.is_relative_to(B) or out.exists():raise SystemExit('output must be fresh inside repo')
out.mkdir(parents=True);controls=[];raw=[]
def passed(name):controls.append({'name':name,'passed':True})
class Strip(ast.NodeTransformer):
 def visit_If(self,n):
  if isinstance(n.test,ast.Name) and n.test.id=='_NATIVE_GRID_PENDING_DIAGNOSTICS':return None
  return self.generic_visit(n)
 def visit_Assign(self,n):
  if any(isinstance(t,ast.Tuple) and any(isinstance(x,ast.Name) and x.id=='diagnostic_frames' for x in t.elts) for t in n.targets):return None
  return self.generic_visit(n)
 def visit_ImportFrom(self,n):
  if n.module=='grid_viewport':n.names=[x for x in n.names if not x.name.startswith('_native_grid_pending') and x.name!='_NATIVE_GRID_PENDING_DIAGNOSTICS']
  return n
for filename in ('grid_viewport.py','grid_raster.py'):
 before=ast.parse((ROOT/'baseline'/REL/filename).read_text());after=ast.parse((ROOT/'candidate'/REL/filename).read_text())
 if filename=='grid_viewport.py':
  old={n.name:n for n in before.body if isinstance(n,(ast.FunctionDef,ast.ClassDef))};new={n.name:n for n in after.body if isinstance(n,(ast.FunctionDef,ast.ClassDef))}
  for name,node in old.items():assert ast.dump(node,include_attributes=False)==ast.dump(Strip().visit(copy.deepcopy(new[name])),include_attributes=False),name
 else:
  oc=next(n for n in before.body if isinstance(n,ast.ClassDef) and n.name=='GridRasterCoordinator');nc=next(n for n in after.body if isinstance(n,ast.ClassDef) and n.name==oc.name)
  assert ast.dump(oc,include_attributes=False)==ast.dump(Strip().visit(copy.deepcopy(nc)),include_attributes=False)
 passed('all pre-existing '+filename+' functions/classes AST-identical after removing diagnostic-only storage/calls')
identity=[1.,0.,0.,0.,0.,1.,0.,0.,0.,0.,1.,0.,0.,0.,0.,1.]
class Matrix:
 def __init__(self,*args):self.v=list(args) if args else identity[:]
 def __getitem__(self,i):return self.v[4*i:4*i+4]
 def __mul__(self,b):return Matrix(*[sum(self.v[i*4+k]*b.v[k*4+j] for k in range(4)) for i in range(4) for j in range(4)])
 def GetInverse(self):return Matrix(*[1/self.v[i] if i in (0,5,10,15) else 0. for i in range(16)])
class Time:
 def IsDefault(self):return False
 def GetValue(self):return 0.
 def __eq__(self,o):return isinstance(o,Time)
def load(tree,name,enabled):
 env={k:os.environ.get(k) for k in ('DC_CFD_NATIVE_GRID_PENDING_DIAGNOSTICS','DC_CFD_NATIVE_GRID_COHORT_DIAGNOSTICS','DC_CFD_NATIVE_GRID_DIAGNOSTICS')}
 for k in env:os.environ.pop(k,None)
 if enabled:os.environ['DC_CFD_NATIVE_GRID_PENDING_DIAGNOSTICS']='grid-pending-v1'
 pkg=types.ModuleType(name);pkg.__path__=[];sys.modules[name]=pkg
 m=types.ModuleType(name+'.grid_viewport');m.__package__=name;sys.modules[m.__name__]=m
 try:exec(compile((ROOT/tree/REL/'grid_viewport.py').read_bytes(),str(ROOT/tree/REL/'grid_viewport.py'),'exec'),m.__dict__)
 finally:
  for k,v in env.items():
   if v is None:os.environ.pop(k,None)
   else:os.environ[k]=v
 rs=types.ModuleType(name+'.render_scene');rs.validate_view=lambda view:None;rs._background_grid_intent_json=lambda grid:json.dumps(grid,sort_keys=True);sys.modules[rs.__name__]=rs
 ro=types.ModuleType(name+'.render_output_observations');ro._same_public_api=lambda a,b:a is b;sys.modules[ro.__name__]=ro
 return m,ro,rs

def sampler(tree,enabled,scenario):
 m,ro,rs=load(tree,'case_'+tree+'_'+scenario+'_'+str(enabled),enabled);calls=[]
 pxr=types.ModuleType('pxr');pxr.Gf=types.SimpleNamespace(Matrix4d=Matrix);sys.modules['pxr']=pxr
 class Api:
  def __getattribute__(self,n):
   if n in ('time','id','usd_context_name','render_product_path','resolution','full_resolution','world_to_ndc','view','transform','projection','stage','camera_path'):calls.append('api.'+n)
   return object.__getattribute__(self,n)
  time=Time();id='Viewport0';usd_context_name='';render_product_path='/Render/Product';resolution=[1920,1080];full_resolution=[1920,1080];world_to_ndc=Matrix();view=Matrix();transform=Matrix();projection=Matrix();camera_path='/World/RenderCamera'
  def map_ndc_to_texture(self,p):calls.append('api.map');return [(p[0]+1)/2,(p[1]+1)/2],self
 stage=types.SimpleNamespace(GetPrimAtPath=lambda path:(calls.append('stage.GetPrimAtPath'),path)[1]);api=Api();api.stage=stage;window=types.SimpleNamespace(viewport_widget=types.SimpleNamespace(viewport_api=api))
 class Camera:
  def __init__(self,p):self.transforms=0;self.cameras=0;calls.append('Camera')
  def __bool__(self):return True
  def ComputeLocalToWorldTransform(self,t):
   calls.append('camera.transform');self.transforms+=1;v=identity[:]
   if scenario=='first-view' and self.transforms==1 or scenario=='final-view' and self.transforms==2:v[0]=2.
   return Matrix(*v)
  def GetCamera(self,t):
   calls.append('camera.GetCamera');self.cameras+=1;v=identity[:]
   if scenario in ('first-projection','diagnostic-call-error') and self.cameras==1 or scenario=='final-projection' and self.cameras==2:v[0]=2.
   return types.SimpleNamespace(frustum=types.SimpleNamespace(ComputeProjectionMatrix=lambda:Matrix(*v)))
 pxr.UsdGeom=types.SimpleNamespace(Camera=Camera);pxr.CameraUtil=types.SimpleNamespace(MatchHorizontally=1,ConformWindow=lambda *a:calls.append('CameraUtil.ConformWindow'))
 count={'frame':0,'view':0,'source':0}
 def frame(a):
  calls.append('frame');count['frame']+=1
  if scenario=='frame-error':raise RuntimeError('controlled native getter failure')
  return {'frameNumber':100+(1 if scenario=='frame-advance' and count['frame']==2 else 0),'resolution':[1920,1080],'view':identity[:],'projection':identity[:]}
 ro._frame=frame
 def source():
  calls.append('source');count['source']+=1;return {'projectUid':'cpu','revision':2 if scenario=='source-change' and count['source']==2 else 1}
 def view():
  calls.append('view');count['view']+=1;return {'backgroundGrid':{'visible':True},'camera':{'fov':46 if scenario=='view-change' and count['view']==2 else 45},'timeSeconds':None}
 def pair():calls.append('pair');return (None,window) if scenario=='owner' else (api,window)
 if scenario=='diagnostic-call-error' and hasattr(m,'_native_grid_pending_diagnostic'):m._native_grid_pending_diagnostic=lambda *args,**kw:(_ for _ in ()).throw(RuntimeError('helper'))
 if scenario=='sink-error':m.print=lambda *args:(_ for _ in ()).throw(RuntimeError('sink'))
 stream=io.StringIO()
 with contextlib.redirect_stdout(stream):result=m.sample_native_grid_viewport(None if scenario=='unavailable' else api,stage,'/World/RenderCamera',source,view,pair,window)
 raw.extend(stream.getvalue().splitlines());reply={k:v for k,v in result.items() if k!='nativeObservation'}
 if result.get('nativeObservation') is not None:
  ob=result['nativeObservation'];reply['observation']={k:getattr(ob,k) for k in ('owner','context','view_token','intent','view_revision','projection','resolution','full_resolution','frame_number')}
 rows=[json.loads(line.split('diagnostic ',1)[1]) for line in stream.getvalue().splitlines()]
 return reply,calls,rows
expected={'ready':None,'unavailable':'native-viewport-unavailable','owner':'native-viewport-owner-not-current','first-view':'native-camera-frame-not-current','first-projection':'native-projection-frame-not-current','final-view':'native-camera-changed-during-sample','final-projection':'native-camera-changed-during-sample','frame-advance':'native-observation-changed-during-sample','source-change':'native-observation-changed-during-sample','view-change':'native-observation-changed-during-sample','frame-error':'native-public-viewport-observation-unavailable','sink-error':None,'diagnostic-call-error':'native-projection-frame-not-current'}
for scenario,reason in expected.items():
 baseline,calls0,_=sampler('baseline',False,scenario)
 for enabled in (False,True):
  reply,calls,rows=sampler('candidate',enabled,scenario)
  assert reply==baseline and calls==calls0,(scenario,enabled)
  assert reply['reason']==reason and reply['phase']==('ready' if reason is None else 'pending')
  if not enabled or reason is None or scenario=='diagnostic-call-error':assert rows==[]
  else:
   assert len(rows)==1 and rows[0]['reason']==reason and rows[0]['gridPixelVerified'] is False
   if scenario=='frame-advance':assert rows[0]['frames']['first']['frameNumber']==100 and rows[0]['frames']['final']['frameNumber']==101 and 'frame.frameNumber' in rows[0]['changedSampleFields']
 passed('actual sampler '+scenario+': exact baseline return/getter/callback order ON/OFF')
# Call the actual helper after the independent older cohort budget is exhausted.
m,ro,rs=load('candidate','helper_controls',True);m._NATIVE_GRID_COHORT_SEEN.update(range(8));stream=io.StringIO()
with contextlib.redirect_stdout(stream):
 for n in range(20):m._native_grid_pending_diagnostic('sampler','first-camera-projection','pending','native-projection-frame-not-current')
assert len(stream.getvalue().splitlines())==1 and len(m._NATIVE_GRID_PENDING_SEEN)==1
passed('pending observer dedups after old cohort cap is exhausted')
with contextlib.redirect_stdout(stream):
 for n in range(12):m._native_grid_pending_diagnostic('lowering','lower-'+str(n),'pending','native-grid-view-not-current')
assert len(m._NATIVE_GRID_PENDING_SEEN)==8 and len(stream.getvalue().splitlines())==8
raw.extend(stream.getvalue().splitlines());passed('independent module lifetime cap exactly8')
m,ro,rs=load('candidate','request_binding',True);token=m._NATIVE_GRID_COHORT_REQUEST.set({'requestSerial':19,'viewToken':'{"camera":{}}','contextToken':'{"projectUid":"cpu"}'})
stream=io.StringIO()
with contextlib.redirect_stdout(stream):m._native_grid_pending_diagnostic('sampler','first-camera-projection','pending','native-projection-frame-not-current')
m._NATIVE_GRID_COHORT_REQUEST.reset(token);row=json.loads(stream.getvalue().split('diagnostic ',1)[1]);assert row['requestSerial']==19 and row['requestContextTokenSHA256']==hashlib.sha256(b'{"projectUid":"cpu"}').hexdigest();raw.extend(stream.getvalue().splitlines());passed('reuses already-existing request scope values; no request/source callback')
m,ro,rs=load('candidate','invalid_helper',True);stream=io.StringIO()
with contextlib.redirect_stdout(stream):
 m._native_grid_pending_diagnostic('sampler','first-camera-projection','pending','untrusted payload')
 m._native_grid_pending_diagnostic('sampler','first-camera-projection','pending','native-projection-frame-not-current',{'first':{'bad':float('nan')}})
 m._native_grid_pending_diagnostic('sampler','first-camera-projection','pending','native-projection-frame-not-current',{'first':{'bad':'x'*5000}})
assert not stream.getvalue() and not m._NATIVE_GRID_PENDING_SEEN;passed('unknown/nonfinite/oversize rows contained without consuming cap')
m,ro,rs=load('candidate','sink_helper',True);m.print=lambda *args:(_ for _ in ()).throw(RuntimeError('sink'))
m._native_grid_pending_diagnostic('sampler','first-camera-projection','pending','native-projection-frame-not-current');assert len(m._NATIVE_GRID_PENDING_SEEN)==1;passed('sink failure reserves cap and is contained')
# Exact source asynchronous lowering method: three receipt paths, no native SDK.
async def lower(tree,enabled,scenario):
 m,ro,rs=load(tree,'lower_'+tree+'_'+scenario+'_'+str(enabled),enabled)
 scope={'__name__':m.__package__+'.lower','__package__':m.__package__,'json':json,'asyncio':asyncio,'replace':dataclasses.replace}
 src=ast.parse((ROOT/tree/REL/'grid_raster.py').read_text());node=next(f for c in src.body if isinstance(c,ast.ClassDef) and c.name=='GridRasterCoordinator' for f in c.body if isinstance(f,ast.AsyncFunctionDef) and f.name=='_lower_native')
 exec(compile(ast.Module(body=[copy.deepcopy(node)],type_ignores=[]),'actual-lower-native','exec'),scope)
 @dataclasses.dataclass(frozen=True)
 class Contract:context:tuple=();intent:str=''
 events=[];scope['native_carrier_matches']=lambda *a:(events.append('carrier_matches'),scenario=='encoded')[1];scope['canonical_grid']=lambda *a:([],[]);scope['author_ribbons']=lambda *a,**k:(events.append('author'),Contract())[1]
 ob=m.NativeGridObservation(object(),object(),('Viewport0','','/Render/Product',('time',0.)),'context','view','intent',None,tuple(identity),(1920,1080),(1920,1080),100)
 class Obj:
  _last_view=1;_canonical=None;receipt={};_contract=None;_contract_layout_lease=None
  def __init__(self):self.n=0;self._pool=ThreadPoolExecutor(max_workers=1);self.native_calculate=lambda *a:None
  def _capture_native(self,r):
   events.append('capture');self.n+=1
   if scenario=='initial-pending' or scenario=='final-pending' and self.n==2:return {'phase':'pending','reason':'native-projection-frame-not-current'}
   return {'phase':'ready','nativeObservation':ob}
  def _current(self,r):events.append('current');return True
  def carrier_contract(self,s):events.append('contract');return None
 obj=Obj();request={'serial':31,'stage':object(),'view':{'backgroundGrid':{'visible':True}},'context':{'projectUid':'cpu'}};stream=io.StringIO()
 try:
  with contextlib.redirect_stdout(stream):await scope['_lower_native'](obj,request,'stream-layout-unresolved')
 finally:obj._pool.shutdown()
 raw.extend(stream.getvalue().splitlines());return obj.receipt,events,stream.getvalue().splitlines()
for scenario in ('initial-pending','final-pending','encoded'):
 baseline,events,_=asyncio.run(lower('baseline',False,scenario))
 for enabled in (False,True):
  receipt,current,rows=asyncio.run(lower('candidate',enabled,scenario));assert receipt==baseline and current==events
  assert len(rows)==(1 if enabled else 0)
  if scenario=='encoded':assert receipt=={'phase':'unsupported','reason':'stream-layout-unresolved','viewRevision':1,'gridPixelVerified':False}
 passed('actual async _lower_native '+scenario+': exact baseline receipt/call order ON/OFF')
report={'scope':'pure CPU exact production Python source with controlled pxr/native getter fixtures; no Kit/GPU/live frame/UI authority','allPassed':True,'count':len(controls),'controls':controls,'python':sys.executable}
(out/'controls.json').write_text(json.dumps(report,indent=2)+'\n');(out/'raw-rows.jsonl').write_text('\n'.join(raw)+'\n');print(json.dumps(report,sort_keys=True))
