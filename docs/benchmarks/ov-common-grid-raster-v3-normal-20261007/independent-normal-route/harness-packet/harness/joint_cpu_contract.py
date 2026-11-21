"""Actual CPU USD/public API + controlled event carriers; no native/pixel acceptance."""
from pathlib import Path
import asyncio,copy,importlib.util,json,runpy,sys,threading,time
from pxr import UsdGeom
T=Path(__file__).resolve().parents[1]
reference='--reference' in sys.argv
base=runpy.run_path(str(T/'harness/cpu_usd_grid.py'))
g,r,p,view,stage,api,Layout=[base[k]for k in ('g','r','p','view','stage','api','ControlledOwnedLayout')]
if reference:
 q=T/'before-correction/src/frontend/omniverse/cfd_demo/kit/extensions/dc.cfd_bridge/dc/cfd_bridge/grid_raster.py'
 spec=importlib.util.spec_from_file_location('controlled_bridge.grid_reference',q);g=importlib.util.module_from_spec(spec);sys.modules[spec.name]=g;spec.loader.exec_module(g)
rows=[]
async def retirement():
 for kind in ('invalid-current','foreign-owner','foreign-project','foreign-hash','foreign-scene','stale-view','backwards-output','bool-view','missing-view','missing-owner','missing-binding'):
  s=stage();a=api(s,texture=(1600,638),canvas=(1600,638));frame=[0];ctx={'projectUid':'CONTROLLED_PROJECT','projectContentSha256':'a'*64,'revision':1}
  c=g.GridRasterCoordinator(lambda _:dict(ctx),lambda _:p.sample_grid_viewport(a,s,r.CAMERA,output_layout=Layout()),lambda:frame[0])
  try:
   c.offer(s,view());frame[0]+=1;c.notify();await c._task;assert c.receipt['phase']=='rendered'
   old=c.carrier_contract(s);literal=s.GetRootLayer().ExportToString();held_points=tuple(UsdGeom.Mesh(s.GetPrimAtPath(r.BACKGROUND_GRID_ROOT+'/Stroke')).GetPointsAttr().Get());serial=c._serial;wrong=view(2);ras=wrong['backgroundGridRaster'];ras['output']['css'][0]=0
   if kind=='foreign-owner':ras['output']['owner']='FOREIGN'
   if kind=='foreign-project':ras['binding']['projectUid']='FOREIGN'
   if kind=='foreign-hash':ras['binding']['projectContentSha256']='b'*64
   if kind=='foreign-scene':ras['binding']['sceneRevision']=2
   if kind=='stale-view':ras['binding']['viewRevision']=1
   if kind=='backwards-output':ras['output']['revision']=0
   if kind=='bool-view':ras['binding']['viewRevision']=True
   if kind=='missing-view':del ras['binding']['viewRevision']
   if kind=='missing-owner':del ras['output']['owner']
   if kind=='missing-binding':del ras['binding']
   try:c.offer(s,wrong)
   except (ValueError,TypeError,g.GridRefused):pass
   else:raise AssertionError('invalid presentation was accepted')
   retired=c.carrier_contract(s)is None and c.carrier_contract(s,wrong)is None
   assert s.GetRootLayer().ExportToString()==literal
   if kind=='invalid-current':
    assert retired is (not reference)
    if not reference:
     assert c._serial==serial+1 and c._request is None and c.receipt=={'phase':'rejected','reason':'grid-lowering-rejected','viewRevision':2}
     assert c._owner=='CONTROLLED_MEDIA' and c._last_view==2 and c._output_revision==1
     recovered=view(3);recovered['backgroundGridRaster']['output']['revision']=2;c.offer(s,recovered);frame[0]+=1;c.notify();await c._task;assert c.receipt=={'phase':'rendered','viewRevision':3} and c.carrier_contract(s)is not None
   else:assert not retired and c._serial==serial and c.carrier_contract(s)is old
   if kind!='invalid-current' or reference:assert s.GetRootLayer().ExportToString()==literal
   else:assert tuple(UsdGeom.Mesh(s.GetPrimAtPath(r.BACKGROUND_GRID_ROOT+'/Stroke')).GetPointsAttr().Get())==held_points
   rows.append({'case':kind,'retired':retired,'refused':True,'foreignAndStaleCannotWithdraw':kind=='invalid-current' or c._serial==serial,'actualUSDHeldUntilValidRecovery':True})
  finally:c.close()
asyncio.run(retirement())

async def late_worker():
 s=stage();a=api(s,texture=(1600,638),canvas=(1600,638));frame=[0];ctx={'projectUid':'CONTROLLED_PROJECT','projectContentSha256':'a'*64,'revision':1};calls=[0];entered=threading.Event();release=threading.Event()
 def compute(*args):
  calls[0]+=1
  if calls[0]==2:
   entered.set();assert release.wait(5),'controlled worker release missing'
  return g.calculate_ribbons(*args)
 c=g.GridRasterCoordinator(lambda _:dict(ctx),lambda _:p.sample_grid_viewport(a,s,r.CAMERA,output_layout=Layout()),lambda:frame[0],compute)
 try:
  c.offer(s,view());frame[0]+=1;c.notify();await c._task;literal=s.GetRootLayer().ExportToString()
  second=view(2);second['backgroundGrid']['opacity']=.5;c.offer(s,second);frame[0]+=1;c.notify()
  for _ in range(1000):
   if entered.is_set():break
   await asyncio.sleep(.001)
  assert entered.is_set()
  wrong=view(3);wrong['backgroundGridRaster']['output']['css'][0]=0
  try:c.validate_binding(s,wrong)
  except ValueError:pass
  else:raise AssertionError('invalid observation passed pre-apply validation')
  release.set();await c._task
  submitted=s.GetRootLayer().ExportToString()!=literal
  assert submitted is reference
  if not reference:
   assert c._request is None and c.carrier_contract(s)is None and c.receipt['phase']=='rejected'
   recovered=view(4);recovered['backgroundGridRaster']['output']['revision']=2;c.offer(s,recovered);frame[0]+=1;c.notify();await c._task;assert c.receipt['phase']=='rendered' and c.carrier_contract(s)is not None
  rows.append({'case':'pending-worker-invalid-new-view-final-commit','oldWorkerSubmitted':submitted,'recoveredAfterValidObservation':not reference,'actualUSD':True})
 finally:release.set();c.close()
asyncio.run(late_worker())

# Separate real diagnostic route: literal installed API + actual extension/readback.
Q=T/'diagnostic-harness';script=Q/'tests/native_runtime.py';env={'__file__':str(script),'__name__':'joint_diagnostic_fixture'}
exec(script.read_text().split('def positive():',1)[0],env)
r,diag=env['r'],env['p']
if reference:
 q=T/'source-before/src/frontend/omniverse/cfd_demo/kit/extensions/dc.cfd_bridge/dc/cfd_bridge/display_diagnostics.py';spec=importlib.util.spec_from_file_location('controlled_bridge.diag_reference',q);diag=importlib.util.module_from_spec(spec);sys.modules[spec.name]=diag;spec.loader.exec_module(diag)
s,a,state,settings,calls,run=env['fixture']();v=view();r.apply_view(s,v);stored=r.read_view(s,[]);r.validate_view(stored);literal=s.GetRootLayer().ExportToString()
# run uses p from fixture env, so point only that test-owned fixture to our source.
env['p']=diag
reply=run(diag.ReadbackController(True));assert reply['ok'] is (not reference)
if not reference:
 assert reply['view']==stored and reply['diagnostics']['pixelVerified']is False and reply['diagnostics']['viewport']['layoutProof']=='unresolved'
 (T/'logs/joint-actual-native-reply.json').write_text(json.dumps(reply,indent=2)+'\n')
 assert s.GetRootLayer().ExportToString()==literal
rows.append({'case':'common-view-to-bounded-native-diagnostic','replyOK':reply['ok'],'error':reply.get('error'),'sourceHeld':s.GetRootLayer().ExportToString()==literal})
if not reference:
 for kind in ('wrong-hash','wrong-scene','zero-css','v1-metric','wire-layout'):
  s,a,state,settings,calls,run=env['fixture']();wrong=view();ras=wrong['backgroundGridRaster']
  if kind=='wrong-hash':ras['binding']['projectContentSha256']='b'*64
  if kind=='wrong-scene':ras['binding']['sceneRevision']=2
  if kind=='zero-css':ras['output']['css'][0]=0
  if kind=='v1-metric':ras['metricVersion']=1
  if kind=='wire-layout':ras['output']['textureToEncodedMapping']='owned-layout'
  # Capture a rejected raw stored input without pretending it passed apply_view.
  s.GetPrimAtPath(r.ROOT).SetCustomDataByKey('dcdtuni:viewJson',json.dumps(wrong));literal=s.GetRootLayer().ExportToString();reply=run(diag.ReadbackController(True));assert not reply['ok']and s.GetRootLayer().ExportToString()==literal
  rows.append({'case':'diagnostic-'+kind,'rejected':True,'sourceHeld':True})
 # The raster is compared before/after too: same scene and camera cannot conceal a transition.
 s,a,state,settings,calls,run=env['fixture']();r.apply_view(s,view());n=[0]
 def clock():
  n[0]+=1
  if n[0]==2:
   newer=r.read_view(s,[]);newer['backgroundGridRaster']['output']['revision']+=1;newer['backgroundGridRaster']['output']['decoded']=[960,382];s.GetPrimAtPath(r.ROOT).SetCustomDataByKey('dcdtuni:viewJson',json.dumps(newer))
  return 10.+n[0]*.001
 reply=run(diag.ReadbackController(True,clock));assert not reply['ok']and reply['error']=='bounded-display:source-context-changed';rows.append({'case':'diagnostic-same-context-raster-changed-between-reads','rejected':True})
print(json.dumps({'reference':reference,'rows':rows,'scope':'Controlled events + actual CPU USD/installed API; no media/layout/GPU/pixel acceptance'},indent=2))
(T/'logs'/('joint-reference-results.json'if reference else'joint-corrected-results.json')).write_text(json.dumps({'reference':reference,'rows':rows},indent=2)+'\n')
