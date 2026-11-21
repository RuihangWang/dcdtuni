from pathlib import Path
import json,hashlib,sys
F=Path(__file__).resolve().parent
ns={'__name__':'readonly_plan_validation','__file__':str(F/'RUN.py')}
exec(compile((F/'RUN.py').read_text(),str(F/'RUN.py'),'exec'),ns)
called=[]
def no_child(*args,**kwargs):called.append(True);raise AssertionError('Preparation cannot launch child')
ns['subprocess'].Popen=no_child
manifest=json.loads((F/'MANIFEST.json').read_text())
assert all(ns['info'](F/p)==v for p,v in manifest['files'].items())
root=Path(ns['plan']['caseRoot']);inputs=json.loads((F/'input-bindings.json').read_text())
for kind in ['AXIS_SHARED']:
 actual={str(p.relative_to(root/kind)):ns['info'](p) for p in (root/kind).rglob('*') if p.is_file()}
 assert actual=={p:{k:v[k] for k in ['sha256','bytes']} for p,v in inputs.items()}
 assert not(root/kind/'Mesh/constant/polyMesh').exists()
assert not ns['T'].exists()
a=ns['env_for']('AXIS_REFERENCE');b=ns['env_for']('AXIS_SHARED');delta={k:[a.get(k),b.get(k)] for k in set(a)|set(b) if a.get(k)!=b.get(k)}
assert set(delta)==set(ns['plan']['sharedOnlyDelta']) and all(v==[None,'1'] for v in delta.values())
import os
saved=dict(os.environ)
try:
 os.environ.update(DCCART_SHARED_POINT_REPAIR='0',DCCART_CONE_QUALITY_KERNEL_CANDIDATES='0',DCCART_CONE_KERNEL_TEST='1',DCCART_UNREVIEWED_INHERITED='1',CART_UNREVIEWED='1')
 ea=ns['env_for']('AXIS_REFERENCE');eb=ns['env_for']('AXIS_SHARED')
 assert not any(k in ea or k in eb for k in ['DCCART_CONE_QUALITY_KERNEL_CANDIDATES','DCCART_CONE_KERNEL_TEST','DCCART_UNREVIEWED_INHERITED','CART_UNREVIEWED'])
 assert 'DCCART_SHARED_POINT_REPAIR' not in ea and eb['DCCART_SHARED_POINT_REPAIR']=='1'
 assert {k:[ea.get(k),eb.get(k)] for k in set(ea)|set(eb) if ea.get(k)!=eb.get(k)}=={'DCCART_SHARED_POINT_REPAIR':[None,'1']}
finally:
 os.environ.clear();os.environ.update(saved)
old=sys.argv
try:
 for authorized in [False,True]:
  sys.argv=['RUN.py','--approved-plan-manifest-sha','0'*64]+(['--execute-authorized'] if authorized else [])
  try:ns['main']();raise AssertionError('Invalid permission/manifest accepted')
  except SystemExit as e:assert not authorized and 'PREPARED ONLY' in str(e)
  except AssertionError as e:assert authorized and 'ROOT must approve exact immutable plan manifest' in str(e)
finally:sys.argv=old
assert not called and not ns['T'].exists()
g=ns['guards']()
print(json.dumps({'manifestSHA':ns['info'](F/'MANIFEST.json')['sha256'],'payloadCount':len(manifest['files']),'nativeExecutions':0,'subprocessCalls':0,'fresh12InputsSingleCase':True,'authorizationAndWrongSHARefused':True,'singleSharedFlagDelta':delta,'guard':g},indent=2))
