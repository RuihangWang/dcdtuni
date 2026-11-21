from pathlib import Path
import asyncio,copy,hashlib,json,sys,types
from pxr import Gf,Sdf,Usd,UsdGeom
T=Path(__file__).resolve().parents[1];F=T/'v1-reference';V=T/'logs'
D=F/'candidate/src/frontend/omniverse/cfd_demo/kit/extensions/dc.cfd_bridge/dc/cfd_bridge'
pkg=types.ModuleType('independently_reviewed_grid');pkg.__path__=[str(D)];sys.modules[pkg.__name__]=pkg
from independently_reviewed_grid import grid_raster as g,render_scene as r
fixture=json.loads((T/'harness/canonical-grid.json').read_text());grid=fixture['view']
ctx={'projectUid':'INDEPENDENT_PROJECT','projectContentSha256':'a'*64,'revision':1};frame=[0]
view={'camera':{'position':[0,20,0],'target':[0,0,0],'up':[0,0,-1],'fov':45,'near':.1,'far':400,'aspect':1600/638},'backgroundGrid':copy.deepcopy(grid),'selectedUids':[],'timeSeconds':None,'backgroundGridRaster':{'line':{'owner':'INDEPENDENT_GL','drawingBuffer':[2490,995],'css':[1992,796],'pixelRatio':1.25,'aliasedRange':[1,1],'effectiveWidthPx':1},'output':{'owner':'INDEPENDENT_MEDIA','revision':1,'decoded':[1600,638]},'binding':{'projectUid':ctx['projectUid'],'projectContentSha256':ctx['projectContentSha256'],'sceneRevision':1,'viewRevision':1}}}
results=[]
async def test(kind):
 s=Usd.Stage.CreateInMemory();UsdGeom.Xform.Define(s,r.ROOT);r.apply_view(s,view)
 camera=UsdGeom.Camera(s.GetPrimAtPath(r.CAMERA));gf=camera.GetCamera(Usd.TimeCode.Default());matrix=camera.ComputeLocalToWorldTransform(Usd.TimeCode.Default()).GetInverse()*gf.frustum.ComputeProjectionMatrix()
 # Deliberately controlled future ready layout/projection; not a runtime public sampler or pixel proof.
 projection=[float(matrix[i][j]) for i in range(4) for j in range(4)]
 sample=lambda _s:{'phase':'ready','reason':None,'projection':projection,'observations':{'layoutProof':'CONTROLLED_TEST_ONLY','imageNdcToOutputNdc':[float(Gf.Matrix4d(1)[i][j])for i in range(4)for j in range(4)]}}
 calls=[0]
 def calc(*args):calls[0]+=1;return g.calculate_ribbons(*args)
 c=g.GridRasterCoordinator(lambda _s:dict(ctx),sample,lambda:frame[0],calc)
 try:
  first=copy.deepcopy(view);c.offer(s,first);frame[0]+=1;c.notify();await c._task
  assert c.receipt=={'phase':'rendered','viewRevision':1} and calls[0]==1
  root=s.GetPrimAtPath(r.BACKGROUND_GRID_ROOT);key=root.GetCustomDataByKey('dcdtuni:rasterCarrierKey');assert g.carrier_matches(s,key)
  mesh=UsdGeom.Mesh(s.GetPrimAtPath(r.BACKGROUND_GRID_ROOT+'/Stroke'))
  if kind=='stroke-hidden':UsdGeom.Imageable(mesh).MakeInvisible();assert mesh.ComputeVisibility()==UsdGeom.Tokens.invisible
  elif kind=='stroke-transform':UsdGeom.Xformable(mesh).AddTranslateOp().Set(Gf.Vec3d(0,.5,0));assert mesh.ComputeLocalToWorldTransform(Usd.TimeCode.Default())!=UsdGeom.Xformable(root).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
  elif kind=='color-interpolation':UsdGeom.PrimvarsAPI(mesh).GetPrimvar('displayColor').SetInterpolation('constant');assert UsdGeom.PrimvarsAPI(mesh).GetPrimvar('displayColor').GetInterpolation()=='constant'
  elif kind=='session-hidden':
   with Usd.EditContext(s,s.GetSessionLayer()):UsdGeom.Imageable(mesh).MakeInvisible()
   assert mesh.ComputeVisibility()==UsdGeom.Tokens.invisible
  else:raise AssertionError(kind)
  wrong_hash=hashlib.sha256(s.GetRootLayer().ExportToString().encode()).hexdigest();matches=g.carrier_matches(s,key)
  newer=copy.deepcopy(first);newer['backgroundGridRaster']['binding']['viewRevision']=2;c.offer(s,newer);frame[0]+=1;c.notify();await c._task
  current_hash=hashlib.sha256(s.GetRootLayer().ExportToString().encode()).hexdigest()
  row={'kind':kind,'actualCPUsdMutation':True,'carrierMatchesAfterMutation':matches,'receipt':dict(c.receipt),'calculateCalls':calls[0],'wrongUSDKeptByteExact':current_hash==wrong_hash,'scopedExpected':'invalid actual carrier must not be accepted as rendered no-op','counterexample':matches and c.receipt['phase']=='rendered' and calls[0]==1 and current_hash==wrong_hash}
  results.append(row);print(json.dumps(row),flush=True)
 finally:c.close()
async def main():
 for kind in ('stroke-hidden','stroke-transform','color-interpolation','session-hidden'):await test(kind)
asyncio.run(main());assert all(x['counterexample'] for x in results)
(V/'counterexamples.json').write_text(json.dumps({'scope':'Actual CPU USD authored carrier mutation and real product coordinator, controlled future ready layout; no current native layout/Kit/GPU/pixel/browser acceptance','results':results},indent=2)+'\n')
