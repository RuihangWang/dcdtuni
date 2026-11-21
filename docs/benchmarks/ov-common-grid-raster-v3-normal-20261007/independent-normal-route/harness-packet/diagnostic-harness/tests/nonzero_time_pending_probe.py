from pathlib import Path
import json
D=Path(__file__).resolve().parents[1];q=D/'tests/native_runtime.py';g={'__file__':str(q),'__name__':'time_fixture'};exec(q.read_text().split('def positive():',1)[0],g)
s,a,state,settings,calls,run=g['fixture']();c=g['UsdGeom'].Camera(s.GetPrimAtPath(g['r'].CAMERA));op=c.GetOrderedXformOps()[0]
a._ViewportAPI__time=g['Usd'].TimeCode(10)
original=op.Get();op.Set(original,g['Usd'].TimeCode(0));op.Set(original,g['Usd'].TimeCode(10))
a._sync_viewport_api(c,(1600,638),force_update=True)
# Begin with a legally pending API whose delivered pose is already behind time10.
op.Set(original*g['Gf'].Matrix4d().SetTranslate(g['Gf'].Vec3d(1,0,0)),g['Usd'].TimeCode(10))
initial=g['grid'].sample_grid_viewport(a,s,g['r'].CAMERA);before=g['r'].read_view(s,[]);n=[0]
def clock():
 n[0]+=1
 if n[0]==2:op.Set(original*g['Gf'].Matrix4d().SetTranslate(g['Gf'].Vec3d(2,0,0)),g['Usd'].TimeCode(10))
 return 10.+.001*n[0]
reply=run(g['p'].ReadbackController(True,clock));final=g['grid'].sample_grid_viewport(a,s,g['r'].CAMERA)
out={'initialReason':initial['reason'],'finalReason':final['reason'],'publicObservationsHeld':initial==final,'normalReadViewHeld':before==g['r'].read_view(s,[]),'apiTime':str(a.time),'clockCalls':n[0],'reply':reply}
print(json.dumps(out,indent=2));(D/'nonzero-time-pending-red.json').write_text(json.dumps(out,indent=2)+'\n')
assert out['publicObservationsHeld'] and out['normalReadViewHeld'] and reply['ok']
