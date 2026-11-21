from pathlib import Path
import json
O=Path(__file__).resolve().parents[1]
p=O/'tests/native_runtime.py'
g={'__file__':str(p),'__name__':'root_counterexample'}
exec('\n'.join(p.read_text().splitlines()[:51]),g)
rows=[]
for sync_api in (False,True):
 s,a,state,setting,calls,run=g['fixture']();n=[0]
 before=g['r'].read_view(s,[])
 def clock():
  n[0]+=1
  if n[0]==2:
   view=g['copy'].deepcopy(before);view['camera']['position'][0]+=2;view['camera']['target'][0]+=2
   g['r'].apply_view(s,view)
   if sync_api:a._sync_viewport_api(g['UsdGeom'].Camera(s.GetPrimAtPath(g['r'].CAMERA)),(1600,638),force_update=True)
  return 10.+.001*n[0]
 reply=run(g['p'].ReadbackController(True,clock));after=g['r'].read_view(s,[])
 rows.append({'case':'camera-pose-change-after-sampler-'+('api-synced' if sync_api else 'api-stale'),'replyOK':reply['ok'],'error':reply.get('error'),'capturedCamera':reply.get('view',{}).get('camera'),'currentCamera':after['camera'],'cameraChanged':before['camera']!=after['camera'],'staleCameraAccepted':reply['ok'] and reply['view']['camera']!=after['camera'],'identityPathHeld':str(a.camera_path)==g['r'].CAMERA,'sceneRevisionHeld':state['sceneRevision']==1,'sourceHashHeld':state['projectHash']==g['H'],'scope':'Controlled callback + actual CPU USD + literal installed API; no live native/pixel proof'})
print(json.dumps(rows,indent=2))
(O/'root-counterexample-candidate.json').write_text(json.dumps(rows,indent=2))
assert all(r['cameraChanged'] and not r['replyOK'] and not r['staleCameraAccepted'] for r in rows)
