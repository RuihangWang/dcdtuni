from pathlib import Path
import os,subprocess,time,json,sys
R=Path(__file__).resolve().parent;env=os.environ.copy();env.update(PYTHONDONTWRITEBYTECODE='1',CUDA_VISIBLE_DEVICES='',OMP_NUM_THREADS='2',OPENBLAS_NUM_THREADS='2',MKL_NUM_THREADS='2',TMPDIR=str(R/'tmp'),TMP=str(R/'tmp'),TEMP=str(R/'tmp'),XDG_CACHE_HOME=str(R/'cache'),VITE_CACHE_DIR=str(R/'cache/vite'))
jobs=[('react-compile',['/usr/bin/node',str(R/'node_modules/vitest/vitest.mjs'),'run','--config',str(R/'vitest.config.ts')]),('react-types',['/usr/bin/node',str(R/'node_modules/typescript/bin/tsc'),'--noEmit','--project',str(R/'source/src/frontend/react/tsconfig.json')])];rows=[]
if 'types-only' in sys.argv:
 jobs=[j for j in jobs if j[0]=='react-types'];rows=json.loads((R/'results/commands.json').read_text())
for name,command in jobs:
 t=time.perf_counter()
 with (R/f'results/{name}.stdout').open('w') as out,(R/f'results/{name}.stderr').open('w') as err:r=subprocess.run(command,cwd=R,env=env,stdout=out,stderr=err)
 rows.append({'name':name,'command':command,'cwd':str(R),'rc':r.returncode,'elapsed_s':time.perf_counter()-t,'affinity':sorted(os.sched_getaffinity(0)),'env':{k:env[k] for k in ('CUDA_VISIBLE_DEVICES','OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','TMPDIR','TMP','TEMP','XDG_CACHE_HOME')}});(R/'results/commands.json').write_text(json.dumps(rows,indent=2));print(name,r.returncode,rows[-1]['elapsed_s'],flush=True)
 if r.returncode:
  print((R/f'results/{name}.stdout').read_text()[-3500:]);print((R/f'results/{name}.stderr').read_text()[-3500:]);sys.exit(r.returncode)
