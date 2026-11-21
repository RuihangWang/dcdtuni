from pathlib import Path
import json,copy,hashlib
F=Path('/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/ov-readonly-display-diagnostic-v2-private-20261007/FROZEN-v2')
D=Path(__file__).resolve().parent
q=F/'tests/native_runtime.py';g={'__file__':str(q),'__name__':'independent_tail_fixture'}
exec(q.read_text().split('def positive():',1)[0],g)
p,r,grid,Usd,Gf,UsdGeom=g['p'],g['r'],g['grid'],g['Usd'],g['Gf'],g['UsdGeom']
rows=[]
for kind in ('unchanged','tail-roof-pose','tail-roof-time10-transform','tail-roof-renderer','tail-sampler-public-projection'):
 s,a,state,settings,calls,run=g['fixture']();cam=UsdGeom.Camera(s.GetPrimAtPath(r.CAMERA))
 if kind=='tail-roof-time10-transform':
  a._ViewportAPI__time=Usd.TimeCode(10);op=cam.GetOrderedXformOps()[0];original=op.Get()
  op.Set(original,Usd.TimeCode(0));op.Set(original,Usd.TimeCode(10));a._sync_viewport_api(cam,(1600,638),time=Usd.TimeCode(10),force_update=True)
 api_identity=(a.stage,str(a.camera_path),a.time)
 context_identity=(state['stage'],state['sceneRevision'],state['projectHash'],state['displayGeneration'],tuple(state['uidPaths'][g['UID']]))
 initial_camera=p._camera_identity(s,a);initial_view=r.read_view(s,[]);initial_renderer=p._settings(settings,a);initial_sample=p._lower_sampler(grid.sample_grid_viewport(a,s,r.CAMERA,viewport_window=g['Outer'](),app_window=g['App']()))
 before=s.GetRootLayer().ExportToString();n=[0];sample_count=[0];clock_n=[0];mutated=[False]
 def clock():clock_n[0]+=1;return 10.+.001*clock_n[0]
 actual_get=Usd.Attribute.Get
 def controlled_actual_get(attr,*args,**kw):
  if str(attr.GetPath())==g['mat']+'/Surface.inputs:opacity':
   n[0]+=1
   if n[0]==2 and kind.startswith('tail-roof-'):
    mutated[0]=True
    if kind=='tail-roof-pose':
     v=copy.deepcopy(initial_view);v['camera']['position'][0]+=2;v['camera']['target'][0]+=2;r.apply_view(s,v)
    elif kind=='tail-roof-time10-transform':
     cam.GetOrderedXformOps()[0].Set(original*Gf.Matrix4d().SetTranslate(Gf.Vec3d(2,0,0)),Usd.TimeCode(10))
    elif kind=='tail-roof-renderer':settings.values[next(iter(p.SETTINGS.values()))]=True
  return actual_get(attr,*args,**kw)
 def controlled_sample(*args,**kw):
  sample_count[0]+=1;out=grid.sample_grid_viewport(*args,**kw)
  if sample_count[0]==2 and kind=='tail-sampler-public-projection':
   a._ViewportAPI__projection=Gf.Matrix4d(2);a._ViewportAPI__world_to_ndc=None;mutated[0]=True
  return out
 Usd.Attribute.Get=controlled_actual_get
 try:reply=run(p.ReadbackController(True,clock),sampler=controlled_sample)
 finally:Usd.Attribute.Get=actual_get
 final_camera=p._camera_identity(s,a);final_view=r.read_view(s,[]);final_renderer=p._settings(settings,a);final_sample=p._lower_sampler(grid.sample_grid_viewport(a,s,r.CAMERA,viewport_window=g['Outer'](),app_window=g['App']()))
 after=s.GetRootLayer().ExportToString()
 if kind.startswith('tail-roof-') or kind=='unchanged':assert n[0]==2
 assert clock_n[0]==2 and sample_count[0]==2
 row={'case':kind,'controlledMutation':mutated[0],'reply':reply,'clockCalls':clock_n[0],'samplerCalls':sample_count[0],'roofOpacityReads':n[0],'apiPathStageTimeHeld':api_identity==(a.stage,str(a.camera_path),a.time),'contextHeld':context_identity==(state['stage'],state['sceneRevision'],state['projectHash'],state['displayGeneration'],tuple(state['uidPaths'][g['UID']])),'actualCameraIdentityChanged':initial_camera!=final_camera,'actualNormalViewChanged':initial_view!=final_view,'actualRendererChanged':initial_renderer!=final_renderer,'actualPublicLoweredSampleChanged':initial_sample!=final_sample,'replyViewIsInitial':reply.get('view')==initial_view,'replyRendererIsInitial':reply.get('diagnostics',{}).get('renderer')==initial_renderer,'replySamplerIsInitial':reply.get('diagnostics',{}).get('viewport')==initial_sample,'USDByteExactHeld':before==after}
 if kind=='unchanged':assert reply['ok'] and before==after and not mutated[0]
 else:assert mutated[0] and reply['ok'],row
 for suffix,text in (('before',before),('after',after)):(D/f'{kind}-{suffix}.usda').write_text(text)
 rows.append(row)
result={'scope':'Actual CPU USD + frozen literal ViewportAPI/product controller with test-owned getter callback mutations at the final roof/sampler boundary. These callbacks model a state change at a getter boundary; they do not prove the owned normal synchronous readonly producers mutate or that a real Kit frame changed. No browser/GPU/layout/pixel/scientific acceptance.','rows':rows,'staleAcceptedCount':sum(r['reply']['ok']for r in rows[1:])}
(D/'TAIL-COUNTEREXAMPLES.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({'scope':result['scope'],'staleAcceptedCount':result['staleAcceptedCount'],'rows':[{k:v for k,v in x.items()if k!='reply'}for x in rows]},indent=2))
