import sys;sys.dont_write_bytecode=True
from pathlib import Path
import subprocess,os,signal,time,json
W=Path(__file__).resolve().parent;D=W.parent.parent;T=W/'reviewtree';node=D/'.codex-tmp/bootstrap-toolchain/node-v24.12.0-linux-x64/bin';python=D/'.codex-tmp/envs/dcdtuni/bin/python'
label=sys.argv[1];mode=sys.argv[2];args=sys.argv[3:];out=W/'evidence-v2'/label;env=os.environ.copy();env.update(PATH=str(node)+':'+env['PATH'],CUDA_VISIBLE_DEVICES='',OMP_NUM_THREADS='2',OPENBLAS_NUM_THREADS='2',MKL_NUM_THREADS='2',TMPDIR=str(W/'tmp-v2'),npm_config_cache=str(W/'npm-cache-v2'),PYTHONDONTWRITEBYTECODE='1')
Path(env['TMPDIR']).mkdir(exist_ok=True);Path(env['npm_config_cache']).mkdir(exist_ok=True)
command=['taskset','-c','38-39',str(python),'-B','-m','pytest','-p','no:cacheprovider',*args] if mode=='native' else ['taskset','-c','38-39',str(node/'npm'),*args]
cwd=T if mode=='native' else T/'src/frontend/react';start=time.monotonic()
with out.open('wb') as log:
 proc=subprocess.Popen(command,cwd=cwd,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
 try:rc=proc.wait(timeout=240)
 except subprocess.TimeoutExpired:
  os.killpg(proc.pid,signal.SIGTERM)
  try:rc=proc.wait(timeout=5)
  except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);rc=proc.wait()
  rc=124
meta={'argv':command,'rc':rc,'elapsed':time.monotonic()-start,'cwd':str(cwd),'cpu':'38-39','CUDA_VISIBLE_DEVICES':'','TMPDIR':env['TMPDIR'],'npmCache':env['npm_config_cache']};out.with_suffix(out.suffix+'.json').write_text(json.dumps(meta,indent=2)+'\n');print(json.dumps(meta));print(out.read_text()[-22000:])
