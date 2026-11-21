#!/usr/bin/env python3
"""Independent CPU-only exact-overlay replay; no install or product service."""
import sys;sys.dont_write_bytecode=True
import argparse,hashlib,json,os,signal,subprocess,time
from pathlib import Path
B=Path('/mnt/lv/home/litianyu/mycode/dcdtuni').resolve()
FE=['authoredTBranch.test.ts','PipeJunctionInspector.test.tsx','liquidFactories.test.ts','legacy/liquidGlobals.test.ts','liquidTransportSolid.test.ts','projectFiles.test.ts','pipeInspector.test.ts','i18n.test.ts']
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def owned(path):
 value=path.resolve();value.relative_to(B);return value
def main():
 parser=argparse.ArgumentParser(description=__doc__)
 for key in ('source','node-bin','node-modules','output'):parser.add_argument('--'+key,required=True,type=Path)
 parser.add_argument('--cpu',default='38-39')
 args=parser.parse_args();source,node,modules,output=map(owned,(args.source,args.node_bin,args.node_modules,args.output));frozen=Path(__file__).resolve().parent
 assert subprocess.check_output(['git','-C',str(source),'rev-parse','--show-toplevel'],text=True).strip()==str(source),'source must be a real repository root'
 assert not output.exists(),'output must be fresh; no deletion/overwrite is performed'
 for binary in ('node','npm'):assert (node/binary).is_file(),binary
 assert (modules/'vitest/vitest.mjs').is_file() and (modules/'typescript/bin/tsc').is_file(),'use actual installed repo-owned modules'
 assert args.cpu and all(c in '0123456789,-' for c in args.cpu),'invalid CPU list'
 targets=json.loads((frozen/'TARGETS.json').read_text())['targets']
 for row in targets:
  p=source/row['path'];a=frozen/'after'/row['path'];assert sha(a)==row['afterSha256']
  assert (p.is_file() and sha(p)==row['beforeSha256']) if row['beforeExists'] else not p.exists(),row['path']+' does not match the frozen before guard'
 output.mkdir(parents=True);tree=output/'reviewtree';tree.mkdir();closure=[]
 names=subprocess.check_output(['git','-C',str(source),'ls-files','-z','src/frontend','src/backend','tools','case/LiquidSingle'],text=True).split('\0')
 for rel in names:
  if not rel or rel.startswith('src/frontend/omniverse/Ref_code/'):continue
  p=source/rel
  if not p.is_file():continue
  assert not p.is_symlink(),rel+' source symlink rejected'
  p.resolve().relative_to(source);dest=tree/rel;dest.parent.mkdir(parents=True,exist_ok=True);data=p.read_bytes();dest.write_bytes(data);dest.chmod(p.stat().st_mode&0o777);closure.append({'path':rel,'bytes':len(data),'sha256':sha(p)})
 for row in targets:
  p=tree/row['path'];p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes((frozen/'after'/row['path']).read_bytes());p.chmod(row['afterMode'])
 (tree/'src/frontend/react/node_modules').symlink_to(modules,target_is_directory=True)
 tmp=output/'tmp';cache=output/'npm-cache';tmp.mkdir();cache.mkdir();env=os.environ.copy();env.update(PATH=str(node)+':'+env['PATH'],CUDA_VISIBLE_DEVICES='',OMP_NUM_THREADS='2',OPENBLAS_NUM_THREADS='2',MKL_NUM_THREADS='2',TMPDIR=str(tmp),npm_config_cache=str(cache),PYTHONDONTWRITEBYTECODE='1')
 versions={name:subprocess.check_output([str(node/name),'--version'],text=True,env=env).strip() for name in ('node','npm')};results=[]
 def run(label,argv):
  start=time.monotonic();log=output/(label+'.log');cmd=['taskset','-c',args.cpu,*argv]
  with log.open('wb') as stream:
   process=subprocess.Popen(cmd,cwd=tree/'src/frontend/react',env=env,stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
   try:rc=process.wait(timeout=240)
   except subprocess.TimeoutExpired:
    os.killpg(process.pid,signal.SIGTERM)
    try:process.wait(timeout=5)
    except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait()
    rc=124
  receipt={'name':label,'argv':cmd,'rc':rc,'elapsedSeconds':time.monotonic()-start,'log':str(log),'bytes':log.stat().st_size,'sha256':sha(log)};results.append(receipt);print(json.dumps(receipt),flush=True);return rc
 code=run('fitting-103', [str(node/'node'),'node_modules/vitest/vitest.mjs','run',*['test/'+f for f in FE],'--maxWorkers=2','--minWorkers=2'])
 code |= run('production-build',[str(node/'npm'),'run','build'])
 (output/'REPLAY.json').write_text(json.dumps({'source':str(source),'sourceHead':subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip(),'sourceClosure':closure,'candidateTargets':targets,'nodeModules':str(modules),'packageLockSha256':sha(source/'src/frontend/react/package-lock.json'),'versions':versions,'cpu':args.cpu,'CUDA_VISIBLE_DEVICES':'','TMPDIR':str(tmp),'npmCache':str(cache),'results':results,'limitations':['CPU/source tests only; no browser/CFD/checkMesh/numerical MMS acceptance','Original real-project optional transport test is skipped without explicitly supplied own fixture; no skips were added','Existing AuthOverlay asynchronous act warning is retained in original logs, not a clean browser console claim']},indent=2)+'\n')
 return code
if __name__=='__main__':raise SystemExit(main())
