from pathlib import Path
import json
D=Path(__file__).resolve().parents[1];q=D/'tests/native_runtime.py';g={'__file__':str(q),'__name__':'time_fixture'};exec(q.read_text().split('def positive():',1)[0],g)
rows=[]
for mutate,sync in ((True,False),(True,True),(False,False)):
 s,a,state,settings,calls,run=g['fixture']();c=g['UsdGeom'].Camera(s.GetPrimAtPath(g['r'].CAMERA));op=c.GetOrderedXformOps()[0]
 a._ViewportAPI__time=g['Usd'].TimeCode(10)
 original=op.Get();op.Set(original,g['Usd'].TimeCode(0));op.Set(original,g['Usd'].TimeCode(10));a._sync_viewport_api(c,(1600,638),force_update=True)
 op.Set(original*g['Gf'].Matrix4d().SetTranslate(g['Gf'].Vec3d(1,0,0)),g['Usd'].TimeCode(10))
 camBefore=g['p']._camera_identity(s,a);matrixBefore=list(c.ComputeLocalToWorldTransform(a.time));initial=g['grid'].sample_grid_viewport(a,s,g['r'].CAMERA);before=g['r'].read_view(s,[]);n=[0]
 identity=(a.stage,str(a.camera_path),a.time,state['sceneRevision'],state['projectHash'],state['displayGeneration'])
 sourceBefore=s.GetRootLayer().ExportToString()
 def clock():
  n[0]+=1
  if n[0]==2 and mutate:
   op.Set(original*g['Gf'].Matrix4d().SetTranslate(g['Gf'].Vec3d(2,0,0)),g['Usd'].TimeCode(10))
   if sync:a._sync_viewport_api(c,(1600,638),force_update=True)
  return 10.+.001*n[0]
 reply=run(g['p'].ReadbackController(True,clock));final=g['grid'].sample_grid_viewport(a,s,g['r'].CAMERA)
 assert identity==(a.stage,str(a.camera_path),a.time,state['sceneRevision'],state['projectHash'],state['displayGeneration'])and n[0]==2
 assert before==g['r'].read_view(s,[])
 if mutate:print('identity before',camBefore,'after',g['p']._camera_identity(s,a),'matrix before',matrixBefore,'after',list(c.ComputeLocalToWorldTransform(a.time)),'reply',reply['ok'],reply.get('error'))
 else:assert reply['ok']and s.GetRootLayer().ExportToString()==sourceBefore
 rows.append({'mutatedTime10':mutate,'apiSynced':sync,'initialReason':initial['reason'],'finalReason':final['reason'],'publicObservationsHeld':initial==final,'normalReadViewHeld':True,'apiTime':str(a.time),'clockCalls':n[0],'reply':reply})
print(json.dumps(rows,indent=2));(D/'nonzero-time-pending-results.json').write_text(json.dumps(rows,indent=2)+'\n')
