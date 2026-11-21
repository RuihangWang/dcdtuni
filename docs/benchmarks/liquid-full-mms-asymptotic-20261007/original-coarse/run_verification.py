from pathlib import Path
import subprocess,os,json,time,hashlib
T=Path(__file__).resolve().parent
jobs=[['before','default'],['after','default'],['after','contract-only']]
rows=[]
for args in jobs:
 name='-'.join(args);cmd=[os.sys.executable,str(T/'backend_tests.py'),*args]
 start=time.monotonic()
 with (T/f'results/verification-{name}.stdout').open('w') as out,(T/f'results/verification-{name}.stderr').open('w') as err:
  r=subprocess.run(cmd,cwd=T,env=os.environ.copy(),stdout=out,stderr=err)
 rows.append(dict(command=cmd,returncode=r.returncode,elapsed_s=time.monotonic()-start,driver_sha256=hashlib.sha256((T/'backend_tests.py').read_bytes()).hexdigest(),env={k:os.environ.get(k) for k in ['CUDA_VISIBLE_DEVICES','OMP_NUM_THREADS','MKL_NUM_THREADS','OPENBLAS_NUM_THREADS','TMP','TMPDIR','TEMP','PYTHONPYCACHEPREFIX','XDG_CACHE_HOME']},affinity=sorted(os.sched_getaffinity(0))))
 (T/'results/verification-commands.json').write_text(json.dumps(rows,indent=2))
 print(name,r.returncode,rows[-1]['elapsed_s'],flush=True)
 if r.returncode:break
if all(r['returncode']==0 for r in rows) and len(rows)==3:
 a=(T/'results/default-before.json').read_bytes();b=(T/'results/default-after.json').read_bytes()
 result={'frontend_steady_bytes_equal':(T/'results/before-baseline.liquidrun').read_bytes()==(T/'results/after-baseline.liquidrun').read_bytes(),'frontend_transient_bytes_equal':(T/'results/before-transient-base.liquidrun').read_bytes()==(T/'results/after-transient-base.liquidrun').read_bytes(),'backend_full_frames_checkpoint_layout_theta_state_bytes_equal':a==b,'before_sha256':hashlib.sha256(a).hexdigest(),'after_sha256':hashlib.sha256(b).hexdigest()}
 (T/'results/default-parity.json').write_text(json.dumps(result,indent=2));assert all(result[k] for k in list(result)[:3]);print(result,flush=True)
