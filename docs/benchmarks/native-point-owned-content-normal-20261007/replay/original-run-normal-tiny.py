
from pathlib import Path
import json,sys,os,time,datetime,subprocess,hashlib
B=Path('/mnt/lv/home/litianyu/mycode/dcdtuni');V=B/'.codex-tmp/physical-point-v3-root-normal-20261007';F=B/'.codex-tmp/physical-point-v3-root-fresh-20261007';D=Path(__file__).resolve().parent;N=B/'.codex-tmp/goal-20261005/air/src/backend/aircooling/third_parties/dcCartMesh';H=N/'src/mesh/dcCartMesh/dcCartCutCells';PL=N/'platforms/linux64GccDPInt32Opt/lib';CONDA=Path('/mnt/lv/home/litianyu/miniconda3/envs/dcdtuni')
sys.path.insert(0,str(V));import normal_rebuild_worker as w
inputs=json.loads((D/'INPUT-byte-exact-manifest.json').read_text());expected_pair={x['path']:x['sha256'] for x in json.loads((V/'NORMAL-worker-build-after.json').read_text())['pair']}
def bindings():return {x['copy']:w.sha(Path(x['copy'])) for x in inputs}
def record(name,cmd,env,timeout=180):
 before=w.controls(expected_pair);assert before['allHeld'];binp=bindings();assert all(binp[x['copy']]==x['sha256'] for x in inputs)
 actual=os.environ.copy();actual.update(env);actual.update({'CUDA_VISIBLE_DEVICES':'','OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1','MKL_NUM_THREADS':'1','TMPDIR':str(D/'tmp'),'TMP':str(D/'tmp'),'TEMP':str(D/'tmp'),'XDG_CACHE_HOME':str(D/'cache'),'PYTHONDONTWRITEBYTECODE':'1'})
 t=time.monotonic();utc=datetime.datetime.now(datetime.timezone.utc).isoformat()
 with (D/'logs'/f'{name}.stdout').open('xb') as out,(D/'logs'/f'{name}.stderr').open('xb') as err:
  try:rc=subprocess.run(cmd,cwd=D,env=actual,stdout=out,stderr=err,timeout=timeout).returncode
  except subprocess.TimeoutExpired:rc=-999
 after=w.controls(expected_pair);v={'command':cmd,'cwd':str(D),'env':{k:actual[k] for k in set(env)|{'CUDA_VISIBLE_DEVICES','OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','TMPDIR','TMP','TEMP','XDG_CACHE_HOME','PYTHONDONTWRITEBYTECODE'}},'startedUTC':utc,'seconds':time.monotonic()-t,'rc':rc,'affinity':sorted(os.sched_getaffinity(0)),'timeout':timeout,'source39':after['source39'],'normalGuards':after['held'],'normalHeld':after['allHeld'],'inputSourcesHeld':bindings()==binp,'expectedPair':expected_pair}
 (D/'logs'/f'{name}.json').write_text(json.dumps(v,indent=2)+'\n');print(name,'rc',rc,'seconds',round(v['seconds'],3),'normalHeld',after['allHeld'],'inputSourcesHeld',v['inputSourcesHeld'],flush=True)
 if name.endswith('-run'):
  for l in (D/'logs'/f'{name}.stdout').read_text(errors='replace').splitlines():
   if l.startswith('{'):print(l,flush=True)
 if rc:print((D/'logs'/f'{name}.stderr').read_text(errors='replace')[-4000:],flush=True)
 assert rc==0 and after['allHeld'] and v['inputSourcesHeld'];return v

def transform(x):return x.replace(str(F/'after'),str(H)).replace(str(F),str(D))
def lib_reference(x):return x.replace(str(PL),str(D/'reference-lib'))
def main():
 pre=w.controls(expected_pair);assert pre['allHeld'];(D/'NORMAL-tiny-before.json').write_text(json.dumps(pre,indent=2)+'\n')
 envbase=json.loads((F/'logs/final-normal-driver-link.json').read_text())['env'];envbase={k:transform(v) for k,v in envbase.items()};envref={k:lib_reference(v) for k,v in envbase.items()}
 # The normal executable is linked directly to the rebuilt normal library, with no private C object.
 a=json.loads((F/'logs/final-normal-driver-link.json').read_text());normal=[transform(x) for x in a['command']];normal.remove(str(D/'tmp/final4-normal-C.o'));normal[normal.index('-o')+1]=str(D/'tmp/normal-driver')
 record('normal-driver-link',normal,envbase)
 # Preserve the original reference C and compile flags. Only copied-path and library bindings change.
 a=json.loads((F/'logs/final-reference-C-compile.json').read_text());reference_c=[transform(x) for x in a['command']];record('reference-C-compile',reference_c,envref)
 a=json.loads((F/'logs/final-reference-driver-link.json').read_text());reference=[lib_reference(transform(x)) for x in a['command']];reference[reference.index('-o')+1]=str(D/'tmp/reference-driver');record('reference-driver-link',reference,envref)
 for target,label in [(N/'platforms/linux64GccDPInt32Opt/bin/dcCartMesh','normal-APP'),(PL/'libdcCartMesh.so','normal-LIB'),(D/'tmp/normal-driver','normal-driver'),(D/'tmp/reference-driver','reference-driver')]:
  env=envref if label=='reference-driver' else envbase
  record(label+'-readelf',['readelf','-d',str(target)],env,30);record(label+'-ldd',['ldd',str(target)],env,30)
 for which,mode in [('reference','OFF'),('normal','OFF'),('normal','ON')]:
  a=json.loads((F/'logs'/f'final-{which}-{mode}-run.json').read_text());env={k:transform(v) for k,v in a['env'].items()};env= {k:lib_reference(v) for k,v in env.items()} if which=='reference' else env
  env['CONTROL_OUTPUT']=str(D/'logs'/f'{which}-{mode}-actual-fields.bin');cmd=[str(D/'tmp'/f'{which}-driver'),str(D/'controlled-case')];record(which+'-'+mode+'-run',cmd,env,35)
 p=D/'logs/reference-OFF-actual-fields.bin';q=D/'logs/normal-OFF-actual-fields.bin';baseline=F/'logs/final-reference-OFF-actual-fields.bin';cmp={'referenceSHA':w.sha(p),'normalOFFSHA':w.sha(q),'previousReferenceSHA':w.sha(baseline),'referenceBytes':p.stat().st_size,'normalOFFBytes':q.stat().st_size,'byteExact':p.read_bytes()==q.read_bytes(),'referenceOldByteExact':p.read_bytes()==baseline.read_bytes(),'normalActualRebuiltLibrary':True,'privateNormalCObjectUsed':False}
 (D/'NORMAL-OFF-field-comparison.json').write_text(json.dumps(cmp,indent=2)+'\n');print('OFF-COMPARISON',json.dumps(cmp),flush=True)
 after=w.controls(expected_pair);(D/'NORMAL-tiny-after.json').write_text(json.dumps(after,indent=2)+'\n');assert after['allHeld'];assert cmp['byteExact'] and cmp['referenceOldByteExact'] and cmp['normalOFFBytes']==6594 and cmp['normalOFFSHA']=='46e3d9735734e6ede20e10582e18f37fef017bf0d91c1cea1c30a7c4950458e3'
if __name__=='__main__':main()
