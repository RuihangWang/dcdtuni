from pathlib import Path
import os,sys,subprocess,time,json,hashlib
O=Path(__file__).resolve().parent
label=sys.argv[1]; target=sys.argv[2]; cmd=sys.argv[3:]
assert target in ('before','after')
env=dict(os.environ)
env.update(TMPDIR=str(O/'tmp'),TMP=str(O/'tmp'),TEMP=str(O/'tmp'),CUDA_VISIBLE_DEVICES='',OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',PYTHONDONTWRITEBYTECODE='1',NODE_COMPILE_CACHE=str(O/'tmp/node-compile-cache'),npm_config_cache=str(O/'tmp/npm-cache'),OV_PRIVATE_TREE=str(O/target))
start=time.monotonic()
p=subprocess.run(cmd,cwd=O,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
raw=p.stdout
f=O/'logs'/(label+'.stdout.txt');f.write_bytes(raw)
receipt={'command':cmd,'target':target,'returncode':p.returncode,'elapsedSeconds':time.monotonic()-start,'stdout':{'path':str(f),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()},'scope':'Controlled CPU-only; no Kit, services, converter or business run'}
(O/'logs'/(label+'.json')).write_text(json.dumps(receipt,indent=2)+'\n')
print(json.dumps(receipt))
print(raw.decode(errors='replace')[-4500:])
sys.exit(p.returncode)
