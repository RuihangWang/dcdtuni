import sys;sys.dont_write_bytecode=True
from pathlib import Path
import subprocess,os,signal,time,json
W=Path(__file__).resolve().parent;D=W.parent.parent;T=W/'reviewtree';node=D/'.codex-tmp/bootstrap-toolchain/node-v24.12.0-linux-x64/bin'
label=sys.argv[1];args=sys.argv[2:];out=W/'evidence'/label;env=os.environ.copy();env.update(PATH=str(node)+':'+env['PATH'],CUDA_VISIBLE_DEVICES='',OMP_NUM_THREADS='2',OPENBLAS_NUM_THREADS='2',MKL_NUM_THREADS='2',TMPDIR=str(W/'tmp'),npm_config_cache=str(W/'npm-cache'))
Path(env['TMPDIR']).mkdir(exist_ok=True);Path(env['npm_config_cache']).mkdir(exist_ok=True)
command=['taskset','-c','38-39',str(node/'npm'),*args];start=time.monotonic()
with out.open('wb') as log:
 proc=subprocess.Popen(command,cwd=T/'src/frontend/react',env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
 try:rc=proc.wait(timeout=240)
 except subprocess.TimeoutExpired:
  os.killpg(proc.pid,signal.SIGTERM)
  try:rc=proc.wait(timeout=5)
  except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);rc=proc.wait()
metadata={'argv':command,'rc':rc,'elapsed':time.monotonic()-start,'cpu':'38-39','CUDA_VISIBLE_DEVICES':'','cwd':str(T/'src/frontend/react'),'TMPDIR':env['TMPDIR'],'npmCache':env['npm_config_cache']}
out.with_suffix(out.suffix+'.json').write_text(json.dumps(metadata,indent=2)+'\n');print(json.dumps(metadata));print(out.read_text()[-22000:]);sys.exit(0)
