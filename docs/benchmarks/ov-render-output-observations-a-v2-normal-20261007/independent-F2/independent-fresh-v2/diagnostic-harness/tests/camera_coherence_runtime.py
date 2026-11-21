from pathlib import Path
import argparse,copy,importlib.util,json,time
D=Path(__file__).resolve().parents[1]
args=argparse.ArgumentParser();args.add_argument('--reference',action='store_true');args.add_argument('--expect-red',action='store_true');a=args.parse_args()
q=D/'tests/native_runtime.py';g={'__file__':str(q),'__name__':'coherence_fixture'}
exec(q.read_text().split('def positive():',1)[0],g)
if a.reference:
 q=D/'v1-reference/after/src/frontend/omniverse/cfd_demo/kit/extensions/dc.cfd_bridge/dc/cfd_bridge/display_diagnostics.py'
 spec=importlib.util.spec_from_file_location('reference_display_diagnostics',q);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);g['p']=m
rows=[]
Gf,UsdGeom,r,grid=g['Gf'],g['UsdGeom'],g['r'],g['grid']
def mutate(name,s,api,state,settings,sync):
 cam=UsdGeom.Camera(s.GetPrimAtPath(r.CAMERA))
 if name=='pose':
  v=copy.deepcopy(r.read_view(s,[]));v['camera']['position'][0]+=2;v['camera']['target'][0]+=2;r.apply_view(s,v)
 elif name=='transform':
  old=cam.GetOrderedXformOps()[0].Get();cam.GetOrderedXformOps()[0].Set(old*Gf.Matrix4d().SetTranslate(Gf.Vec3d(2,0,0)))
 elif name=='focal':cam.GetFocalLengthAttr().Set(cam.GetFocalLengthAttr().Get()*1.25)
 elif name=='horizontal-aperture':cam.GetHorizontalApertureAttr().Set(cam.GetHorizontalApertureAttr().Get()*1.25)
 elif name=='vertical-aperture':cam.GetVerticalApertureAttr().Set(cam.GetVerticalApertureAttr().Get()*1.25)
 elif name=='horizontal-offset':cam.GetHorizontalApertureOffsetAttr().Set(2.)
 elif name=='vertical-offset':cam.GetVerticalApertureOffsetAttr().Set(2.)
 elif name=='clipping':cam.GetClippingRangeAttr().Set(Gf.Vec2f(.5,350))
 elif name=='projection':cam.GetProjectionAttr().Set('orthographic')
 elif name=='public-world-matrix':api._ViewportAPI__world_to_ndc=Gf.Matrix4d(2)
 elif name=='public-view-matrix':api._ViewportAPI__view=Gf.Matrix4d(2)
 elif name=='public-transform':api._ViewportAPI__view=Gf.Matrix4d().SetTranslate(Gf.Vec3d(2,0,0));api._ViewportAPI__world_to_ndc=None
 elif name=='public-projection':api._ViewportAPI__projection=Gf.Matrix4d(2);api._ViewportAPI__world_to_ndc=None
 elif name=='texture-mapping':api._ViewportAPI__aspect_ratios=(1.1,1.2)
 elif name=='resolution':api._ViewportAPI__viewport_texture.resolution=(1280,720)
 elif name=='full-resolution':api._ViewportAPI__viewport_texture.full_resolution=(1280,720)
 elif name=='renderer-bool':settings.values[next(iter(settings.values))]=True
 elif name=='renderer-mode':settings.values['/rtx/rendermode']='PathTracing'
 elif name=='view-selection':
  v=copy.deepcopy(r.read_view(s,[]));v['selectedUids']=['other'];s.GetPrimAtPath(r.ROOT).SetCustomDataByKey('dcdtuni:viewJson',json.dumps(v))
 elif name=='view-time':
  v=copy.deepcopy(r.read_view(s,[]));v['timeSeconds']=2.;s.GetPrimAtPath(r.ROOT).SetCustomDataByKey('dcdtuni:viewJson',json.dumps(v))
 else:raise AssertionError(name)
 if sync:api._sync_viewport_api(cam,(1600,638),force_update=True)
names=['pose','transform','focal','horizontal-aperture','vertical-aperture','horizontal-offset','vertical-offset','clipping','projection']
cases=[(n,sync,lag)for n in names for sync in (False,True)for lag in (False,True)]
cases += [(n,False,False)for n in ['public-world-matrix','public-view-matrix','public-transform','public-projection','texture-mapping','resolution','full-resolution','renderer-bool','renderer-mode','view-time']]
for name,sync,lag in cases:
 s,api,state,settings,calls,run=g['fixture']();n=[0]
 if lag:
  cam=UsdGeom.Camera(s.GetPrimAtPath(r.CAMERA));cam.GetFocalLengthAttr().Set(cam.GetFocalLengthAttr().Get()*1.1)
 initial=grid.sample_grid_viewport(api,s,r.CAMERA)
 identity=(api.stage,str(api.camera_path),api.time,state['sceneRevision'],state['projectHash'],state['displayGeneration'])
 before=r.read_view(s,[])
 def clock():
  n[0]+=1
  if n[0]==2:mutate(name,s,api,state,settings,sync)
  return 10.+.001*n[0]
 start=time.monotonic();reply=run(g['p'].ReadbackController(True,clock));elapsed=time.monotonic()-start
 current=r.read_view(s,[])
 row={'case':name,'apiSynced':sync,'initiallyLagging':lag,'initialSamplerReason':initial['reason'],'replyOK':reply['ok'],'error':reply.get('error'),'sameIdentity':identity==(api.stage,str(api.camera_path),api.time,state['sceneRevision'],state['projectHash'],state['displayGeneration']),'cameraChanged':before['camera']!=current['camera'],'clockCalls':n[0],'producerCalls':calls,'elapsed':elapsed}
 assert row['sameIdentity'] and n[0]==2
 rows.append(row)
# Actual unchanged positive with already-lagging API remains honest pending, never restored.
for lag in (False,True):
 s,api,state,settings,calls,run=g['fixture']()
 if lag:
  cam=UsdGeom.Camera(s.GetPrimAtPath(r.CAMERA));cam.GetFocalLengthAttr().Set(cam.GetFocalLengthAttr().Get()*1.1)
 before=s.GetRootLayer().ExportToString();values=copy.deepcopy(settings.values);start=time.monotonic();reply=run();elapsed=time.monotonic()-start
 assert reply['ok'] and reply['diagnostics']['viewport']['layoutProof']=='unresolved'
 assert s.GetRootLayer().ExportToString()==before and values==settings.values
 rows.append({'case':'unchanged-positive','initiallyLagging':lag,'replyOK':True,'viewportReason':reply['diagnostics']['viewport']['reason'],'producerCalls':calls,'elapsed':elapsed})
out={'reference':a.reference,'negativeCount':len(cases),'staleAcceptedCount':sum(r['replyOK']for r in rows[:-2]),'rows':rows,'scope':'Controlled readonly producer/clock adversary + actual CPUUSD/literal public ViewportAPI. No live Kit/GPU/browser/layout/pixel proof.'}
print(json.dumps(out,indent=2));(D/('coherence-reference.json'if a.reference else 'coherence-candidate.json')).write_text(json.dumps(out,indent=2)+'\n')
if a.expect_red:assert out['staleAcceptedCount']>0
else:assert out['staleAcceptedCount']==0,out['staleAcceptedCount']
