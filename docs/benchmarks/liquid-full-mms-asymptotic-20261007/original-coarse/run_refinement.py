from pathlib import Path
import subprocess,os,time,json
T=Path(__file__).resolve().parent
jobs=[('sensitivity-v2',[os.sys.executable,str(T/'sensitivity.py')]),('fine-newton',[os.sys.executable,str(T/'fine_newton.py'),'after'])]
rows=[]
for name,cmd in jobs:
 start=time.monotonic()
 with (T/f'results/{name}.stdout').open('w') as out,(T/f'results/{name}.stderr').open('w') as err:r=subprocess.run(cmd,cwd=T,env=os.environ.copy(),stdout=out,stderr=err)
 rows.append(dict(name=name,command=cmd,returncode=r.returncode,elapsed_s=time.monotonic()-start,affinity=sorted(os.sched_getaffinity(0)),env={k:os.environ.get(k) for k in ['CUDA_VISIBLE_DEVICES','OMP_NUM_THREADS','MKL_NUM_THREADS','OPENBLAS_NUM_THREADS','TMP','TMPDIR','TEMP','PYTHONPYCACHEPREFIX','XDG_CACHE_HOME']}))
 (T/'results/refinement-commands.json').write_text(json.dumps(rows,indent=2));print(name,r.returncode,rows[-1]['elapsed_s'],flush=True)
 if r.returncode:break
