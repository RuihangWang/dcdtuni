from pathlib import Path
import subprocess,os,time,json,hashlib
T=Path(__file__).resolve().parent
p=T/'vitest.config.ts';s=p.read_text().replace("include:['manufactured.test.ts']","include:['fine.test.ts']");(T/'vitest.fine.config.ts').write_text(s)
jobs=[('generate-fine',[os.sys.executable,str(T/'generate_fine.py')]),('compile-fine',['node',str(T/'node_modules/vitest/vitest.mjs'),'run','--config',str(T/'vitest.fine.config.ts')]),('sensitivity',[os.sys.executable,str(T/'sensitivity.py')]),('fine-newton',[os.sys.executable,str(T/'fine_newton.py'),'after'])]
rows=[]
for name,cmd in jobs:
 env=os.environ.copy();env['MMS_VARIANT']='after';start=time.monotonic()
 with (T/f'results/{name}.stdout').open('w') as out,(T/f'results/{name}.stderr').open('w') as err:r=subprocess.run(cmd,cwd=T,env=env,stdout=out,stderr=err)
 rows.append(dict(name=name,command=cmd,returncode=r.returncode,elapsed_s=time.monotonic()-start,env={k:env.get(k) for k in ['CUDA_VISIBLE_DEVICES','OMP_NUM_THREADS','MKL_NUM_THREADS','OPENBLAS_NUM_THREADS','TMP','TMPDIR','TEMP','PYTHONPYCACHEPREFIX','XDG_CACHE_HOME']},affinity=sorted(os.sched_getaffinity(0))))
 (T/'results/final-commands.json').write_text(json.dumps(rows,indent=2));print(name,r.returncode,rows[-1]['elapsed_s'],flush=True)
 if r.returncode:break
