#!/usr/bin/env python3
"""CPU-only v2 incremental split API replay; creates only a fresh private copy."""
import sys;sys.dont_write_bytecode=True
import argparse,hashlib,json,os,signal,subprocess,time
from pathlib import Path
B=Path('/mnt/lv/home/litianyu/mycode/dcdtuni').resolve()
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def owned(p):
 p=p.resolve();p.relative_to(B);return p
def main():
 parser=argparse.ArgumentParser(description=__doc__)
 for key in ('source','node-bin','node-modules','output'):parser.add_argument('--'+key,required=True,type=Path)
 parser.add_argument('--cpu',default='38-39')
 parser.add_argument('--source-manifest',type=Path,help='Actual copied-source REPLAY.json with sourceClosure, or SOURCE_CLOSURE.json with files')
 parser.add_argument('--base-freeze',type=Path,help='Immutable v2 target freeze; defaults to the hash-bound reference')
 args=parser.parse_args()
 source,node,modules,out=map(owned,(args.source,args.node_bin,args.node_modules,args.output));pack=Path(__file__).resolve().parent
 base=json.loads((pack/'BASE_FREEZE.json').read_text());F=owned(args.base_freeze or Path(base['path']))
 assert sha(F/'MANIFEST.json')==base['manifestSha256'],'immutable v2 freeze identity mismatch'
 assert not out.exists(),'output must not exist; no existing path is removed'
 assert args.cpu and all(c in '0123456789,-' for c in args.cpu),'bad CPU list'
 assert (modules/'vitest/vitest.mjs').is_file() and (modules/'typescript/bin/tsc').is_file()
 targets=json.loads((F/'TARGETS.json').read_text())['targets'];sourceInputs=[]
 for row in targets:
  for stage in ('before','after'):assert sha(F/stage/row['path'])==row[stage+'Sha256'],row['path']
  p=source/row['path'];assert p.is_file() and not p.is_symlink()
  digest=sha(p);assert digest in {row['beforeSha256'],row['afterSha256']},row['path']+' needs v1 before or v2 after bytes'
  sourceInputs.append({'path':row['path'],'sha256':digest})
 requirements=json.loads((F/'SOURCE_REQUIREMENTS.json').read_text())
 for row in requirements:assert sha(source/row['path'])==row['afterSha256'],row['path']+' needs the actual fitting v1 dependency'
 # Prefer the actual copied source manifest, not a historical static list.
 catalogue_path=owned(args.source_manifest) if args.source_manifest else None
 if catalogue_path is None and (source.parent/'REPLAY.json').is_file():
  catalogue_path=owned(source.parent/'REPLAY.json')
 if catalogue_path is not None:
  receipt=json.loads(catalogue_path.read_text());catalogue=receipt.get('sourceClosure',receipt.get('files'))
  assert isinstance(catalogue,list) and catalogue,'source manifest needs actual sourceClosure/files'
  catalogue_binding={'method':'actual-source-manifest','path':str(catalogue_path),'sha256':sha(catalogue_path)}
 else:
  # A copied tree need not have .git. Only source subtrees are walked, and
  # runtime/generated/cache directories and every symlink are excluded.
  banned={'node_modules','dist','.vite','.codex-tmp','.git','__pycache__','.pytest_cache','Ref_code','_build','_deps','_repo','_out'}
  catalogue=[]
  for prefix in ('src/frontend','src/backend','tools','case/LiquidSingle'):
   root=source/prefix
   if not root.is_dir():continue
   for current,dirs,files in os.walk(root,followlinks=False):
    current=Path(current);dirs[:]=[name for name in dirs if name not in banned and not (current/name).is_symlink()]
    for name in files:
     p=current/name
     if p.is_symlink() or name.endswith(('.pyc','.tsbuildinfo')):continue
     if p.is_file():catalogue.append({'path':str(p.relative_to(source))})
  catalogue_binding={'method':'bounded-actual-source-subtrees','prefixes':['src/frontend','src/backend','tools','case/LiquidSingle'],'excludedDirectories':sorted(banned)}
 names={row['path'] for row in catalogue}|{row['path'] for row in requirements}|{row['path'] for row in targets}
 for rel in names:
  assert any(rel.startswith(prefix+'/') for prefix in ('src/frontend','src/backend','tools','case/LiquidSingle')),'source catalogue path outside approved subtrees'
  assert not set(Path(rel).parts)&{'node_modules','dist','.vite','.codex-tmp','.git','__pycache__','.pytest_cache','Ref_code','_build','_deps','_repo','_out'},'generated/cache catalogue path rejected'
 out.mkdir(parents=True);tree=out/'reviewtree';tree.mkdir();closure=[]
 for rel in sorted(names):
  assert not Path(rel).is_absolute() and '..' not in Path(rel).parts,'non-relative source path rejected'
  p=source/rel;assert p.is_file() and not p.is_symlink(),rel+' missing or source symlink'
  p.resolve().relative_to(source);dest=tree/rel;dest.parent.mkdir(parents=True,exist_ok=True);data=p.read_bytes();dest.write_bytes(data);dest.chmod(p.stat().st_mode&0o777);closure.append({'path':rel,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()})
 (tree/'src/frontend/react/node_modules').symlink_to(modules,target_is_directory=True)
 tmp=out/'tmp';cache=out/'npm-cache';tmp.mkdir();cache.mkdir()
 env=os.environ.copy();env.update(PATH=str(node)+':'+env['PATH'],CUDA_VISIBLE_DEVICES='',OMP_NUM_THREADS='2',MKL_NUM_THREADS='2',OPENBLAS_NUM_THREADS='2',TMPDIR=str(tmp),npm_config_cache=str(cache),PYTHONDONTWRITEBYTECODE='1')
 versions={name:subprocess.check_output([str(node/name),'--version'],text=True,env=env).strip() for name in ('node','npm')};results=[]
 def overlay(stage):
  for row in targets:(tree/row['path']).write_bytes((F/stage/row['path']).read_bytes())
 def run(label,args):
  cmd=['taskset','-c',args_cpu,*args];log=out/(label+'.log');start=time.monotonic()
  with log.open('wb') as stream:
   proc=subprocess.Popen(cmd,cwd=tree/'src/frontend/react',env=env,stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
   try:rc=proc.wait(timeout=120)
   except subprocess.TimeoutExpired:
    os.killpg(proc.pid,signal.SIGTERM)
    try:proc.wait(timeout=5)
    except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);proc.wait()
    rc=124
  row={'label':label,'argv':cmd,'rc':rc,'elapsedSeconds':time.monotonic()-start,'log':str(log),'bytes':log.stat().st_size,'sha256':sha(log)};results.append(row);print(json.dumps(row),flush=True);return rc
 args_cpu=args.cpu;vitest=[str(node/'node'),'node_modules/vitest/vitest.mjs','run'];workers=['--maxWorkers=2','--minWorkers=1']
 overlay('before');(tree/'src/frontend/react/test/authoredTBranch.test.ts').write_bytes((F/'baseline-tests-fixed-fixture.ts').read_bytes())
 report=out/'baseline-vitest.json';baseline=run('baseline-red',vitest+['test/authoredTBranch.test.ts','-t','^split semantics',*workers,'--reporter=json','--outputFile='+str(report)])
 summary=json.loads(report.read_text()) if report.is_file() else {};expected=baseline==1 and summary.get('numFailedTests')==10 and summary.get('numPassedTests')==1
 overlay('after');code=0
 code|=run('after-api-20',vitest+['test/authoredTBranch.test.ts','-t','^(split semantics|real shared upgrade|both real creation|rejects invalid authored)',*workers])
 code|=run('after-i18n-9',vitest+['test/i18n.test.ts',*workers])
 code|=run('after-typecheck',[str(node/'npm'),'exec','--','tsc','-b','--pretty','false'])
 (out/'REPLAY.json').write_text(json.dumps({'source':str(source),'sourceInputs':sourceInputs,'sourceClosure':closure,'sourceCatalogueBinding':catalogue_binding,'versions':versions,'modules':str(modules),'packageLockSha256':sha(source/'src/frontend/react/package-lock.json'),'targets':targets,'cpu':args.cpu,'CUDA_VISIBLE_DEVICES':'','TMPDIR':str(tmp),'baselineExpected10Failures1Pass':expected,'baselineSummary':summary,'results':results,'limitations':['4 incremental paths applied after fitting v1; no product source mutation','Selected tests leave 4 export tests unselected by design, without changing their definitions','No full build rerun, whole case export/graph, CFD, GPU, service, or browser acceptance']},indent=2)+'\n')
 return code or (0 if expected else 1)
if __name__=='__main__':raise SystemExit(main())
