from pathlib import Path
D=Path(__file__).resolve().parents[1];q=D/'tests/native_runtime.py';g={'__file__':str(q),'__name__':'probe'};exec(q.read_text().split('def positive():',1)[0],g)
s,a,st,settings,calls,run=g['fixture']();c=g['UsdGeom'].Camera(s.GetPrimAtPath(g['r'].CAMERA));v=c.GetCamera(a.time)
for name in ('projection','focalLength','horizontalAperture','verticalAperture','horizontalApertureOffset','verticalApertureOffset','clippingRange'): print(name,repr(getattr(v,name)),type(getattr(v,name)),str(getattr(v,name)))
print('enums',g['Gf'].Camera.Perspective,g['Gf'].Camera.Orthographic)
print('result',run())
