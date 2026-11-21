from pathlib import Path
import argparse,gzip,hashlib,json,os,signal,subprocess,time
p=argparse.ArgumentParser()
p.add_argument('--repo',required=True);p.add_argument('--python',required=True);p.add_argument('--gpu',action='store_true')
p.add_argument('--cuda-visible',default='GPU-dd97ad37-4a36-19df-d789-0ee503ba4d93')
a=p.parse_args();B=Path(__file__).resolve().parent;R=Path(a.repo).resolve()
assert (R/'src/backend/liquidcooling/dae_runtime.py').is_file()
binding=json.loads((B/'original-keyerror/source-binding.json').read_text())
for e in binding['files']:
 raw=(R/e['path']).read_bytes();assert len(raw)==e['bytes'] and hashlib.sha256(raw).hexdigest()==e['sha256'],e['path']
O=R/'.codex-tmp/fullcase-parity-gradient-replay'/str(time.time_ns());O.mkdir(parents=True)
input_bytes=gzip.decompress((B/'input/checkpoint.json.gz').read_bytes())
checkpoint=O/'checkpoint.json';checkpoint.write_bytes(input_bytes)
source=(B/'probe.py').read_text();plan=json.loads((B/'plan.json').read_text())
assert hashlib.sha256(source.encode()).hexdigest()==plan['probe_sha256']
# Only adapt filesystem and the physical GPU selector; solver calls/equations stay exact.
old="I=R.parents[3]";assert source.count(old)==1;source=source.replace(old,"I=Path(sys.argv[7])")
old="source=I/'.codex-tmp/dae-controls-root-replay-20261007/gui-response-fullcase/checkpoint.json'"
assert source.count(old)==1;source=source.replace(old,"source=Path(sys.argv[8])")
old="'GPU-dd97ad37-4a36-19df-d789-0ee503ba4d93'";assert source.count(old)==1
source=source.replace(old,'sys.argv[9]')
probe=O/'probe.py';probe.write_text(source)
receipts=[]
for label,device,multiplier,gradient,steps in plan['cohorts']:
 if device.startswith('cuda') and not a.gpu:continue
 C=O/label;C.mkdir();(C/'tmp').mkdir();(C/'xdg').mkdir()
 env=os.environ.copy();env.update(CUDA_VISIBLE_DEVICES=a.cuda_visible if device.startswith('cuda') else '',PYTHONPATH=str(R/'src')+':'+str(R/'src/backend'),PYTHONDONTWRITEBYTECODE='1',TMPDIR=str(C/'tmp'),XDG_CACHE_HOME=str(C/'xdg'))
 for key in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS']:env[key]='2'
 argv=['taskset','-c','26-27',str(Path(a.python).resolve()),'-B',str(probe),str(C),label,device,str(multiplier),str(gradient),str(steps),str(R),str(checkpoint),a.cuda_visible]
 start=time.monotonic()
 with (C/'stdout.log').open('wb')as out,(C/'stderr.log').open('wb')as err:
  child=subprocess.Popen(argv,cwd=R,env=env,stdout=out,stderr=err,start_new_session=True)
  (C/'process.json').write_text(json.dumps({'pid':child.pid,'argv':argv,'cuda_visible':env['CUDA_VISIBLE_DEVICES']}))
  try:rc=child.wait(timeout=300)
  except subprocess.TimeoutExpired:
   os.killpg(child.pid,signal.SIGTERM)
   try:child.wait(timeout=5)
   except subprocess.TimeoutExpired:os.killpg(child.pid,signal.SIGKILL);child.wait()
   rc=124
 status={'rc':rc,'wall_s':time.monotonic()-start,'label':label}
 (C/'status.json').write_text(json.dumps(status));receipts.append(status);print(json.dumps(status),flush=True)
 if rc:break
(O/'receipts.json').write_text(json.dumps({'cohorts':receipts,'gpu_requested':a.gpu,'all_eight_executed':len(receipts)==8,'probe_adjustments':'only explicit checkout, checkpoint and physical GPU selector'},indent=2))
raise SystemExit(receipts[-1]['rc'])
