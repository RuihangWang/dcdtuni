import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path

p=argparse.ArgumentParser();p.add_argument('--output',required=True);p.add_argument('--cpu',type=int,default=38)
a=p.parse_args();F=Path(__file__).resolve().parent
B=Path('/mnt/lv/home/litianyu/mycode/dcdtuni');out=Path(a.output).resolve()
assert out.is_relative_to((B/'.codex-tmp').resolve()) and not out.exists(), 'new project-owned output required'
manifest=json.loads((F/'MANIFEST.json').read_text())
for e in manifest['files']:
    raw=(F/e['path']).read_bytes()
    assert len(raw)==e['bytes'] and hashlib.sha256(raw).hexdigest()==e['sha256'],e['path']
out.mkdir(parents=True)
env=os.environ.copy();env.update(PYTHONDONTWRITEBYTECODE='1',CUDA_VISIBLE_DEVICES='',OMP_NUM_THREADS='1')
records=[]
for name,args,expected in [('baseline-observation-RED',['--baseline-red'],1),('controlled-candidate',[],0)]:
    r=subprocess.run(['taskset','-c',str(a.cpu),'/mnt/lv/home/litianyu/miniconda3/envs/dcdtuni/bin/python',
        str(F/'tests/test_source_selection_trace.py'),*args],cwd=F,env=env,capture_output=True,timeout=120)
    (out/(name+'.stdout')).write_bytes(r.stdout);(out/(name+'.stderr')).write_bytes(r.stderr)
    records.append({'name':name,'actualReturnCode':r.returncode,'expectedReturnCode':expected,
        'stdoutSha256':hashlib.sha256(r.stdout).hexdigest(),'stderrSha256':hashlib.sha256(r.stderr).hexdigest()})
    assert r.returncode==expected,(name,r.returncode)
(out/'status.json').write_text(json.dumps(records,indent=2)+'\n')
print(json.dumps({'FULLpayloadsVerified':len(manifest['files']),'results':records}))
