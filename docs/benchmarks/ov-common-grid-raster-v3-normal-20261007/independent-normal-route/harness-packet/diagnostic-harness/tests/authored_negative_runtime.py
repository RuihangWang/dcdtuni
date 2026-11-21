from pathlib import Path
import copy,json
D=Path(__file__).resolve().parents[1];q=D/'tests/native_runtime.py';g={'__file__':str(q),'__name__':'authored_fixture'};exec(q.read_text().split('def positive():',1)[0],g)
rows=[]
for name in ('missing-camera','unknown-projection','focal-nan','aperture-inf','clipping-nan'):
 s,a,state,settings,calls,run=g['fixture']();c=g['UsdGeom'].Camera(s.GetPrimAtPath(g['r'].CAMERA))
 if name=='missing-camera':s.RemovePrim(g['r'].CAMERA)
 elif name=='unknown-projection':c.GetProjectionAttr().Set('unknown')
 elif name=='focal-nan':c.GetFocalLengthAttr().Set(float('nan'))
 elif name=='aperture-inf':c.GetHorizontalApertureAttr().Set(float('inf'))
 elif name=='clipping-nan':c.GetClippingRangeAttr().Set(g['Gf'].Vec2f(float('nan'),400.))
 reply=run();assert not reply['ok'] and reply['error']=='bounded-display:invalid-public-value',reply
 rows.append({'case':name,'reply':reply,'calls':calls})
# Internal identity is not serialized or substituted for the stored normal view.
s,a,state,settings,calls,run=g['fixture']();c=g['UsdGeom'].Camera(s.GetPrimAtPath(g['r'].CAMERA));c.GetFocalLengthAttr().Set(c.GetFocalLengthAttr().Get()*1.1)
before=s.GetRootLayer().ExportToString();reply=run();assert reply['ok'];assert reply['view']==g['r'].read_view(s,[]);assert s.GetRootLayer().ExportToString()==before
assert set(reply['view']['camera'])=={'position','target','up','fov','aspect','near','far'}
rows.append({'case':'no-output-fallback-no-source-write','replyOK':True,'sourceHeld':True})
print(json.dumps(rows,indent=2));(D/'authored-negative-results.json').write_text(json.dumps(rows,indent=2)+'\n')
