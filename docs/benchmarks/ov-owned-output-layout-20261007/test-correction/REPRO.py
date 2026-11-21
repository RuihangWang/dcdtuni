#!/usr/bin/env python3
"""Fresh, by-value CPU cohorts. Never imports normal source or invokes real Kit."""
import argparse,hashlib,json,pathlib,re,shutil,subprocess,sys
F=pathlib.Path(__file__).resolve().parent
B=pathlib.Path('/mnt/lv/home/litianyu/mycode/dcdtuni').resolve()
p=argparse.ArgumentParser();p.add_argument('--owner',required=True);p.add_argument('--candidate-only',action='store_true');a=p.parse_args()
O=pathlib.Path(a.owner).resolve()
assert O.is_relative_to(B) and O!=B and not O.is_relative_to(F) and not O.exists(), 'fresh B-owned directory required'
m=json.loads((F/'MANIFEST.json').read_text())
for r in m['files']:
 q=F/r['path'];assert q.is_file() and q.stat().st_size==r['bytes'] and hashlib.sha256(q.read_bytes()).hexdigest()==r['sha256'],r['path']
O.mkdir()
for cohort in ('baseline','current','candidate'):
 for q in (F/cohort/'src').rglob('*'):
  if q.is_file():
   out=O/cohort/q.relative_to(F/cohort);out.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(q,out)
shutil.copyfile(F/'RUN_TESTS.py',O/'RUN_TESTS.py')
steps=('candidate',) if a.candidate_only else ('baseline','current','candidate')
results=[]
for cohort in steps:
 call=subprocess.run([sys.executable,str(O/'RUN_TESTS.py'),cohort],cwd=O)
 assert call.returncode==0,'runner failed'
 status=json.loads((O/'logs'/(cohort+'.status.json')).read_text())
 expected=0 if cohort=='candidate' else 1
 assert status['returncode']==expected,(cohort,status)
 text=(O/'logs'/(cohort+'.stdout')).read_text()
 if cohort!='candidate':
  failures=re.findall(r'^FAILED (.+)$',text,re.M)
  assert len(failures)==10 and sum('test_display_frame_order' in x for x in failures)==9 and sum('test_render_command_routing' in x for x in failures)==1 and not any('test_ov_startup_lifecycle' in x for x in failures),failures
 else:assert '89 passed, 12 subtests passed' in text
 results.append({'cohort':cohort,'returncode':status['returncode'],'elapsedSeconds':status['elapsedSeconds'],'lastLine':text.splitlines()[-1]})
(O/'REPRO-results.json').write_text(json.dumps({'results':results,'fixtureScope':'controlled CPU lifecycle; no numeric/scientific/display acceptance'},indent=2)+'\n')
print(json.dumps(results,indent=2))
