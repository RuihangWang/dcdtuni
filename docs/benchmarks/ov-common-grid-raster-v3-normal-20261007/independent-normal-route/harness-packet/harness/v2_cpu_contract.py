"""Source-bound actual CPU USD/installed Three carrier counterexamples.
All future ready layouts here are controlled fixtures; no runtime/pixel proof.
"""
from pathlib import Path
import asyncio,copy,hashlib,json,math,runpy,sys,types
from pxr import Gf,Sdf,Usd,UsdGeom,UsdShade,Tf
T=Path(__file__).resolve().parents[1]
base=runpy.run_path(str(T/'harness/cpu_usd_grid.py'))
g,r,p,grid,three,view,stage,api,Layout=[base[k]for k in ('g','r','p','grid','three','view','stage','api','ControlledOwnedLayout')]
checks=list(base['checks']);results=[]
async def carrier_repairs():
 for kind in ('stroke-hidden','stroke-transform','color-interpolation'):
  s=stage();a=api(s,texture=(1600,638),canvas=(1600,638));frame=[0];ctx={'projectUid':'CONTROLLED_PROJECT','projectContentSha256':'a'*64,'revision':1};calls=[0]
  def compute(*args):calls[0]+=1;return g.calculate_ribbons(*args)
  c=g.GridRasterCoordinator(lambda _:dict(ctx),lambda _:p.sample_grid_viewport(a,s,r.CAMERA,output_layout=Layout()),lambda:frame[0],compute)
  try:
   c.offer(s,view());frame[0]+=1;c.notify();await c._task
   assert c.receipt=={'phase':'rendered','viewRevision':1}
   contract=c.carrier_contract(s);key=contract.key;expected=contract.expected;good=s.GetRootLayer().ExportToString()
   mesh=UsdGeom.Mesh(s.GetPrimAtPath(r.BACKGROUND_GRID_ROOT+'/Stroke'))
   if kind=='stroke-hidden':UsdGeom.Imageable(mesh).MakeInvisible()
   elif kind=='stroke-transform':UsdGeom.Xformable(mesh).AddTranslateOp().Set(Gf.Vec3d(0,.5,0))
   else:UsdGeom.PrimvarsAPI(mesh).GetPrimvar('displayColor').SetInterpolation('constant')
   wrong=s.GetRootLayer().ExportToString();assert wrong!=good
   assert not g.carrier_matches(s,key,contract) and contract.expected==expected
   c.offer(s,view(2));frame[0]+=1;c.notify();await c._task
   assert c.receipt=={'phase':'rendered','viewRevision':2} and calls[0]==2
   assert s.GetRootLayer().ExportToString()==good and g.carrier_matches(s,key,c.carrier_contract(s))
   (T/'logs'/f'{kind}-v2-repaired.usda').write_text(s.GetRootLayer().ExportToString())
   results.append({'kind':kind,'matchesWrong':False,'immutableExpectedHeld':True,'calls':calls[0],'restoredLiteralUSD':True,'receipt':dict(c.receipt)})
  finally:c.close()
asyncio.run(carrier_repairs());checks.append('original3 actual Stroke visibility/local transform/interpolation changes rejected then real coordinator repairs byte-exact')

def strong_layer_cases():
 for kind in ('visibility','transform'):
  s=stage();a=api(s,texture=(1600,638),canvas=(1600,638));sample=p.sample_grid_viewport(a,s,r.CAMERA,output_layout=Layout());points,colors=g.canonical_grid(grid);meta=view()['backgroundGridRaster'];data=g.calculate_ribbons(grid,points,colors,sample['projection'],meta['output']['decoded'],meta)
  contract=g.author_ribbons(s,grid,data,'EXPECTED');root_before=s.GetRootLayer().ExportToString()
  with Usd.EditContext(s,s.GetSessionLayer()):
   mesh=UsdGeom.Mesh(s.GetPrimAtPath(r.BACKGROUND_GRID_ROOT+'/Stroke'))
   if kind=='visibility':UsdGeom.Imageable(mesh).MakeInvisible()
   else:UsdGeom.Xformable(mesh).AddTranslateOp().Set(Gf.Vec3d(0,.5,0))
  assert s.GetRootLayer().ExportToString()==root_before # Root bytes do not detect this.
  foreign=s.GetSessionLayer().ExportToString();assert not g.carrier_matches(s,'EXPECTED',contract)
  with Usd.EditContext(s,s.GetSessionLayer()):
   try:g.author_ribbons(s,grid,data,'NEXT')
   except g.GridRefused:pass
   else:raise AssertionError('stronger session opinion falsely accepted as rendered')
  assert s.GetSessionLayer().ExportToString()==foreign and s.GetRootLayer().ExportToString()==root_before
  s.GetSessionLayer().Clear();next_contract=g.author_ribbons(s,grid,data,'NEXT');assert g.carrier_matches(s,'NEXT',next_contract)
  results.append({'kind':'session-'+kind,'rootBytesHeldDuringWrongComposedState':True,'rejectedCommit':True,'foreignSessionHeld':True,'afterExternalClearMatches':True})
 # Parent inherited visibility is also an actual rendered value, not owned tags.
 s=stage();a=api(s,texture=(1600,638),canvas=(1600,638));points,colors=g.canonical_grid(grid);meta=view()['backgroundGridRaster'];data=g.calculate_ribbons(grid,points,colors,p.sample_grid_viewport(a,s,r.CAMERA,output_layout=Layout())['projection'],meta['output']['decoded'],meta);contract=g.author_ribbons(s,grid,data,'EXPECTED')
 with Usd.EditContext(s,s.GetSessionLayer()):UsdGeom.Xform.Define(s,'/World').MakeInvisible()
 assert not g.carrier_matches(s,'EXPECTED',contract)
 # No whole-stage traversal or unrelated data read for bounded matcher.
 unrelated=UsdGeom.Xform.Define(s,'/World/UnrelatedControlledModel');unrelated.GetPrim().SetCustomDataByKey('controlled:large','x'*100000)
 assert not g.carrier_matches(s,'EXPECTED',contract)
strong_layer_cases();checks.append('actual stronger session visibility/transform and inherited visibility refuse composed commit, rollback owned root, preserve foreign layer')

async def strong_layer_coordinator():
 s=stage();a=api(s,texture=(1600,638),canvas=(1600,638));frame=[0];ctx={'projectUid':'CONTROLLED_PROJECT','projectContentSha256':'a'*64,'revision':1};calls=[0]
 def compute(*args):calls[0]+=1;return g.calculate_ribbons(*args)
 c=g.GridRasterCoordinator(lambda _:dict(ctx),lambda _:p.sample_grid_viewport(a,s,r.CAMERA,output_layout=Layout()),lambda:frame[0],compute)
 try:
  c.offer(s,view());frame[0]+=1;c.notify();await c._task;assert c.receipt['phase']=='rendered';root_before=s.GetRootLayer().ExportToString();contract=c.carrier_contract(s)
  with Usd.EditContext(s,s.GetSessionLayer()):UsdGeom.Mesh(s.GetPrimAtPath(r.BACKGROUND_GRID_ROOT+'/Stroke')).MakeInvisible()
  foreign=s.GetSessionLayer().ExportToString();assert not g.carrier_matches(s,contract.key,contract)
  c.offer(s,view(2));frame[0]+=1;c.notify();await c._task
  assert c.receipt=={'phase':'rejected','reason':'grid-lowering-rejected','viewRevision':2} and calls[0]==2
  assert s.GetRootLayer().ExportToString()==root_before and s.GetSessionLayer().ExportToString()==foreign
  s.GetSessionLayer().Clear();c.offer(s,view(3));frame[0]+=1;c.notify();await c._task;assert c.receipt=={'phase':'rendered','viewRevision':3} and g.carrier_matches(s,contract.key,c.carrier_contract(s))
 finally:c.close()
asyncio.run(strong_layer_coordinator());checks.append('actual coordinator cannot ACK rendered after foreign composed session override; later external clear recovers safely')

def stronger_ancestor_binding():
 for purpose in (UsdShade.Tokens.allPurpose,UsdShade.Tokens.preview,UsdShade.Tokens.full):
  s=stage();a=api(s,texture=(1600,638),canvas=(1600,638));meta=view()['backgroundGridRaster'];points,colors=g.canonical_grid(grid);data=g.calculate_ribbons(grid,points,colors,p.sample_grid_viewport(a,s,r.CAMERA,output_layout=Layout())['projection'],meta['output']['decoded'],meta);contract=g.author_ribbons(s,grid,data,'EXPECTED');mesh=s.GetPrimAtPath(r.BACKGROUND_GRID_ROOT+'/Stroke');local=mesh.GetRelationship('material:binding').GetTargets()
  other=UsdShade.Material.Define(s,'/World/CONTROLLED_other_material');UsdShade.MaterialBindingAPI.Apply(s.GetPrimAtPath(r.ROOT)).Bind(other,materialPurpose=purpose,bindingStrength=UsdShade.Tokens.strongerThanDescendants)
  assert mesh.GetRelationship('material:binding').GetTargets()==local and UsdShade.MaterialBindingAPI(mesh).ComputeBoundMaterial(materialPurpose=purpose)[0].GetPath()==other.GetPath()
  before=s.GetRootLayer().ExportToString();assert not g.carrier_matches(s,'EXPECTED',contract)
  try:g.author_ribbons(s,grid,data,'NEXT')
  except g.GridRefused:pass
  else:raise AssertionError('ancestor stronger binding falsely accepted as intended material')
  assert s.GetRootLayer().ExportToString()==before
stronger_ancestor_binding();checks.append('actual stronger ancestor material binding resolved per owned Gprim rejects despite byte-held local relationship, foreign root opinions preserved')



def world_frame():
 plane=Gf.Matrix4d(*grid['matrix'])
 for name,case in three.items():
  m=plane*Gf.Matrix4d(*case['worldToClip'])
  for sample in case['samples']:
   local=sample['local'];clip=Gf.Vec4d(*local,1)*m;ndc=[clip[i]/clip[3]for i in range(3)];assert max(abs(a-b)for a,b in zip(ndc,sample['ndc']))<1e-12
   world=Gf.Vec4d(*local,1)*plane;assert max(abs(world[i]-sample['world'][i])for i in range(3))<1e-12
world_frame();checks.append('actual installed Three project(local→same canonical grid.matrix→world) equals Gf row-vector clip, top/isometric')

# Independent width measurement projects actual authored polygon vertices and
# compares perpendicular CSS coverage, not an implementation-computed width.
def project(point,plane,matrix,size):
 v=Gf.Vec4d(*point,1)*plane*Gf.Matrix4d(*matrix);return(v[0]/v[3]*size[0]/2,v[1]/v[3]*size[1]/2)
def expected_css_width(j,meta,points):
 plane=Gf.Matrix4d(*grid['matrix']);line=meta['line'];a,b=[project(points[k],plane,line['worldToClip'],line['drawingBuffer'])for k in (j,j+1)];tx,ty=b[0]-a[0],b[1]-a[1];length=math.hypot(tx,ty);nx,ny=-ty/length,tx/length
 # Project the two actual GL-normal strip boundaries to CSS and measure along
 # the CSS-line perpendicular normal. This derivation is independent of lower.
 sx,sy=[a/b for a,b in zip(line['css'],line['drawingBuffer'])];ctx,cty=tx*sx,ty*sy;cl=math.hypot(ctx,cty);cnx,cny=-cty/cl,ctx/cl
 return abs(nx*sx*cnx+ny*sy*cny)*line['effectiveWidthPx']
def measure_css(data,meta,matrix):
 plane=Gf.Matrix4d(*grid['matrix']);output=meta['output'];W,H=output['decoded'];fit=min(output['layoutCss'][0]/W,output['layoutCss'][1]/H);sx=fit*output['css'][0]/output['layoutCss'][0];sy=fit*output['css'][1]/output['layoutCss'][1];points,_=g.canonical_grid(grid);cursor=0;rows=[]
 for count,source in zip(data['counts'],data['sourceLines']):
  polygon=data['points'][cursor:cursor+count];cursor+=count;j=source*2
  # Only polygons with both strip edges closest to center; corner triangles are excluded.
  if count<4 or max(abs(x)for x in points[j] if x!=0)<1:continue
  a,b=[project(points[k],plane,matrix,[W,H])for k in (j,j+1)];tx,ty=(b[0]-a[0])*sx,(b[1]-a[1])*sy;length=math.hypot(tx,ty);nx,ny=-ty/length,tx/length
  values=[sum(z*n for z,n in zip((v[0]*sx,v[1]*sy),(nx,ny)))for v in [project(q,plane,matrix,[W,H])for q in polygon]]
  actual=max(values)-min(values);target=expected_css_width(j,meta,points)
  # The midpoint of canonical line's varying coordinate is at0; constant
  # coordinate comes from min coordinate magnitudes, not source name guesses.
  constant=min(abs(points[j][0]),abs(points[j][2]))
  rows.append((constant,source,actual,target))
 return sorted(rows)[:8]

def authored_measure(stage_,data,meta,matrix,key,writer=g):
 writer.author_ribbons(stage_,grid,data,key)
 mesh=UsdGeom.Mesh(stage_.GetPrimAtPath(r.BACKGROUND_GRID_ROOT+'/Stroke'))
 actual={**data,'points':[tuple(float(v)for v in q)for q in mesh.GetPointsAttr().Get()], 'counts':list(mesh.GetFaceVertexCountsAttr().Get())}
 assert actual['counts']==data['counts'] and list(mesh.GetFaceVertexIndicesAttr().Get())==list(range(len(actual['points'])))
 plane=UsdGeom.Xformable(stage_.GetPrimAtPath(r.BACKGROUND_GRID_ROOT)).ComputeLocalToWorldTransform(Usd.TimeCode.Default());assert plane==Gf.Matrix4d(*grid['matrix'])
 output=meta['output'];fit=min(a/b for a,b in zip(output['layoutCss'],output['decoded']));scale=[fit*a/b for a,b in zip(output['css'],output['layoutCss'])]
 error=0
 for q,a in zip(data['points'],actual['points']):
  u,v=project(q,plane,matrix,output['decoded']),project(a,plane,matrix,output['decoded']);error=max(error,math.hypot((u[0]-v[0])*scale[0],(u[1]-v[1])*scale[1]))
 # Perpendicular width is max(dot(n,p))-min(dot(n,p)); each support value
 # moves at most |delta p|, hence actual f32 width error <=2*max vertex error.
 # Keep the original strict nominal-math tests separate and unchanged.
 return measure_css(actual,meta,matrix),2*error

def presentation_measurements():
 s=stage();a=api(s,texture=(1600,638),canvas=(1600,638));matrix=p.sample_grid_viewport(a,s,r.CAMERA,output_layout=Layout())['projection'];points,colors=g.canonical_grid(grid)
 scenarios=[('root-example',[2490,995],[1992,796],[1992,796],[1992,796]),('reference-anisotropy',[2490,995],[1992,597],[1992,796],[1992,796]),('output-anisotropy',[2490,995],[1992,796],[2390.4,636.8],[1992,796]),('contain-letterbox',[2490,995],[1992,796],[1992,1200],[1992,1200])]
 for name,db,css,ocss,layout in scenarios:
  meta=copy.deepcopy(view()['backgroundGridRaster']);meta['line'].update(drawingBuffer=db,css=css);meta['output'].update(css=ocss,layoutCss=layout)
  data=g.calculate_ribbons(grid,points,colors,matrix,[1600,638],meta);rows=measure_css(data,meta,matrix);assert len(rows)>=2 and all(abs(actual-target)<1e-8 for _,_,actual,target in rows),(name,rows)
  authored,bound=authored_measure(s,data,meta,matrix,'CSS_'+name);assert authored and all(abs(actual-target)<=bound+1e-8 for _,_,actual,target in authored)
  results.append({'kind':name,'nominalWidths':[{'source':source,'actualCss':actual,'referenceCss':target}for _,source,actual,target in rows],'actualUSDf32Widths':[{'source':source,'actualCss':actual,'referenceCss':target}for _,source,actual,target in authored],'propagatedF32WidthBoundCss':bound})
 # Diagonal reference/output projected tangents: actual installed API pose.
 case=three['iso'];s=stage();r.apply_view(s,{'camera':case['camera'],'selectedUids':[],'timeSeconds':None});a=api(s,texture=(1600,638),canvas=(1600,638));matrix=p.sample_grid_viewport(a,s,r.CAMERA,output_layout=Layout())['projection'];meta=copy.deepcopy(view()['backgroundGridRaster']);meta['line'].update(worldToClip=case['worldToClip'],drawingBuffer=[2490,995],css=[1992,597]);meta['output'].update(css=[2390.4,636.8])
 data=g.calculate_ribbons(grid,points,colors,matrix,[1600,638],meta);rows=measure_css(data,meta,matrix);assert rows and all(abs(actual-target)<2e-8 for _,_,actual,target in rows),(rows)
 authored,bound=authored_measure(s,data,meta,matrix,'CSS_iso');assert authored and all(abs(actual-target)<=bound+2e-8 for _,_,actual,target in authored)
 results.append({'kind':'isometric-diagonal-anisotropy','nominalWidths':[{'source':source,'actualCss':actual,'referenceCss':target}for _,source,actual,target in rows],'actualUSDf32Widths':[{'source':source,'actualCss':actual,'referenceCss':target}for _,source,actual,target in authored],'propagatedF32WidthBoundCss':bound})
 # Literal frozen v1 lower demonstrates old ratio on the same actual inputs.
 F=T/'v1-reference/candidate/src/frontend/omniverse/cfd_demo/kit/extensions/dc.cfd_bridge/dc/cfd_bridge'
 pkg=types.ModuleType('frozen_v1_width');pkg.__path__=[str(F)];sys.modules[pkg.__name__]=pkg
 from frozen_v1_width import grid_raster as old
 s=stage();a=api(s,texture=(1600,638),canvas=(1600,638));matrix=p.sample_grid_viewport(a,s,r.CAMERA,output_layout=Layout())['projection'];meta=copy.deepcopy(view()['backgroundGridRaster']);meta['line'].update(drawingBuffer=[2490,995],css=[1992,796]);before=old.calculate_ribbons(grid,points,colors,matrix,[1600,638],1);rows=measure_css(before,meta,matrix)
 assert rows and all(abs(actual/target-1.55625)<1e-8 for _,_,actual,target in rows),rows
 authored,bound=authored_measure(s,before,meta,matrix,'OLD_CSS',old);assert authored and all(abs(actual-target*1.55625)<=bound+1e-8 and actual/target>1.55 for _,_,actual,target in authored)
 results.append({'kind':'actual-v1-presentation-RED','nominalRatio':rows[0][2]/rows[0][3],'actualUSDf32Ratio':authored[0][2]/authored[0][3],'expectedRatio':1,'propagatedF32WidthBoundCss':bound,'scoped':'controlled actual CPUUSD/installed camera math; no runtime layout/pixels'})
presentation_measurements();checks.append('actual authored polygon perpendicular CSS width, root v1 1.55625 RED→v2 parity; anisotropy, diagonal tangents, contain letterbox')

async def observation_guards():
 s=stage();a=api(s,texture=(1600,638),canvas=(1600,638));frame=[0];ctx={'projectUid':'CONTROLLED_PROJECT','projectContentSha256':'a'*64,'revision':1};calls=[0]
 def compute(*args):calls[0]+=1;return g.calculate_ribbons(*args)
 c=g.GridRasterCoordinator(lambda _:dict(ctx),lambda _:p.sample_grid_viewport(a,s,r.CAMERA,output_layout=Layout()),lambda:frame[0],compute)
 try:
  first=view();c.offer(s,first);frame[0]+=1;c.notify();await c._task;assert c.receipt['phase']=='rendered';before=s.GetRootLayer().ExportToString()
  for mutation in [lambda v:v['backgroundGridRaster']['output'].update(revision=0),lambda v:v['backgroundGridRaster']['output'].update(css=[1992,590]),lambda v:v['backgroundGridRaster']['output'].update(decoded=[960,382]),lambda v:v['backgroundGridRaster']['line'].update(coordinateFrame='other-units')]:
   wrong=view(2);mutation(wrong)
   try:c.offer(s,wrong)
   except (g.GridRefused,ValueError):pass
   else:raise AssertionError('backward/equal changed output/frame accepted')
   assert s.GetRootLayer().ExportToString()==before and calls[0]==1
  resized=view(3);resized['backgroundGridRaster']['output'].update(revision=2,css=[1992,590],layoutCss=[1992,590]);c.offer(s,resized);frame[0]+=1;c.notify();await c._task;assert c.receipt['phase']=='rendered' and calls[0]==2
  # Null unavailable observation cannot reuse derived no-op permission.
  missing=view(4);del missing['backgroundGridRaster'];assert c.carrier_contract(s,missing)is None;r.apply_view(s,missing,c.carrier_contract(s,missing));c.offer(s,missing);assert c.carrier_contract(s)is None;assert c.receipt=={'phase':'pending','reason':'raster-unobserved'};assert not s.GetPrimAtPath(r.BACKGROUND_GRID_ROOT+'/Stroke')
 finally:c.close()
 # Old v1 contract is explicitly incompatible, not guessed into v2.
 old=view();del old['backgroundGridRaster']['metricVersion'];old['backgroundGridRaster']['line'].pop('worldToClip');old['backgroundGridRaster']['line'].pop('coordinateFrame')
 try:r.validate_view(old)
 except ValueError:pass
 else:raise AssertionError('old unobserved presentation inferred as v2')
asyncio.run(observation_guards());checks.append('actual coordinator output monotonic/equal-payload guard, host-only output change relower, unavailable and v1 refusal')

def bounded_noop_probe():
 import time,statistics
 s=stage();a=api(s,texture=(1600,638),canvas=(1600,638));meta=view()['backgroundGridRaster'];points,colors=g.canonical_grid(grid);data=g.calculate_ribbons(grid,points,colors,p.sample_grid_viewport(a,s,r.CAMERA,output_layout=Layout())['projection'],meta['output']['decoded'],meta);contract=g.author_ribbons(s,grid,data,'EXPECTED');before=s.GetRootLayer().ExportToString();events=[];registration=Tf.Notice.Register(Usd.Notice.ObjectsChanged,lambda *_:events.append(1),s);elapsed=[]
 try:
  for _ in range(16):
   start=time.perf_counter();assert g.carrier_matches(s,'EXPECTED',contract);elapsed.append(time.perf_counter()-start)
 finally:registration.Revoke()
 assert not events and s.GetRootLayer().ExportToString()==before
 results.append({'kind':'bounded-composed-noop-probe','sameOwnedUSDByteHeld':True,'USDChangeNotices':len(events),'trials':16,'medianSeconds':statistics.median(elapsed),'maxSeconds':max(elapsed),'ownedPrims':len(contract.paths),'authoredVertices':len(data['points']),'scope':'one controlled view only; not live Kit drag/pipeline performance'})
bounded_noop_probe();checks.append('read-only bounded expected carrier compares actual composed USD with0 mutation notices, local-only timing')
(T/'logs/v2-cpu-contract-receipt.json').write_text(json.dumps({'scope':'CONTROLLED actual installed Three+ViewportAPI/CPUUSD/product code; no production Kit/layout/GPU/pixels','checks':checks,'count':len(checks),'results':results,'browserAccepted':False},indent=2)+'\n');print(json.dumps({'count':len(checks),'checks':checks,'original3repairs':results[:3]},indent=2))
