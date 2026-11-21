#!/usr/bin/env python3
"""Reproduce the frozen static and candidate Fraction audits into a fresh owned scratch."""
import argparse,hashlib,json,os,re,uuid
from pathlib import Path
HERE=Path(__file__).resolve().parent
AIR_TMP=Path('/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/air/.codex-tmp')
parser=argparse.ArgumentParser()
parser.add_argument('--check-only',action='store_true')
parser.add_argument('--run-id',default=None)
args=parser.parse_args()
manifest=json.loads((HERE/'reproducer-manifest.json').read_bytes())
for group in ['sourceScripts','inputFiles','reportFiles']:
 for row in manifest[group]:
  path=(HERE/row['name']) if group=='sourceScripts' else Path(row['path'])
  digest=hashlib.sha256(path.read_bytes()).hexdigest()
  if digest!=row['sha256']:raise RuntimeError('Frozen hash mismatch: '+str(path))
if args.check_only:
 print(json.dumps(dict(frozenHashesVerified=True,sourceFiles=len(manifest['sourceScripts']),inputFiles=len(manifest['inputFiles']),reportFiles=len(manifest['reportFiles']))))
 raise SystemExit(0)
run_id=args.run_id or uuid.uuid4().hex
if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',run_id):raise ValueError('Unsafe run ID')
requested=set(range(8,16))
if not requested.issubset(os.sched_getaffinity(0)):raise RuntimeError('CPUs 8-15 are unavailable')
os.sched_setaffinity(0,requested)
run_dir=AIR_TMP/'two-lobe-candidate-independent-20261006'/'reproductions'/run_id
run_dir.mkdir(parents=True,exist_ok=False)
tmp=run_dir/'tmp';tmp.mkdir()
os.environ['CUDA_VISIBLE_DEVICES']=''
os.environ['OPENBLAS_NUM_THREADS']='1'
os.environ['OMP_NUM_THREADS']='1'
os.environ['TMPDIR']=str(tmp)
static_relative=str(run_dir.relative_to(AIR_TMP)/'static')
candidate_relative=str(run_dir.relative_to(AIR_TMP)/'candidate')
for name in manifest['executionOrder']:
 path=HERE/name
 source=path.read_text()
 # Only artifact paths change. The actual frozen arithmetic source remains immutable.
 source=source.replace("'two-lobe-separator-independent-20261006'",repr(static_relative))
 source=source.replace("'two-lobe-candidate-independent-20261006'",repr(candidate_relative))
 namespace={'__name__':'__main__','__file__':str(path)}
 exec(compile(source,str(path),'exec'),namespace)
static=json.loads((run_dir/'static'/'separator-topology-geometry-audit.json').read_bytes())
embedded=json.loads((run_dir/'static'/'fixed-surface-embeddedness-certificate.json').read_bytes())
candidate=json.loads((run_dir/'candidate'/'candidate-exact-geometry-certificate.json').read_bytes())
if not all(static['checks'].values()):raise RuntimeError('Static topology/conservation check failed')
if not all(embedded[k]['embeddedByExactConvexFacePairIntersections'] for k in ['globalShell','leftSubshell','rightSubshell']):raise RuntimeError('Fixed surface embeddedness check failed')
if not all(candidate['checks'].values()):raise RuntimeError('Candidate geometry check failed')
proofs=candidate['pairSeparationCertificates']
if len(proofs)!=780 or candidate['pairSeparationSummary']['unresolvedPairs']:raise RuntimeError('Pair separation incomplete')
summary=dict(scope='Frozen exact sources reproduced; original reports and input candidates untouched.',runDirectory=str(run_dir),sourceManifestSha256=hashlib.sha256((HERE/'reproducer-manifest.json').read_bytes()).hexdigest(),allStaticChecks=True,allEmbeddednessChecks=True,allCandidateChecks=True,pairSeparationSummary=candidate['pairSeparationSummary'],cpuAffinity=sorted(os.sched_getaffinity(0)),cudaVisibleDevices=os.environ['CUDA_VISIBLE_DEVICES'],tmpDirectory=str(tmp))
(run_dir/'reproduction-summary.json').open('x').write(json.dumps(summary,indent=2)+'\n')
print(json.dumps(summary,indent=2))
