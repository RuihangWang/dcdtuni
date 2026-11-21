from pathlib import Path
import ast,datetime,fcntl,hashlib,json,os,sys,requests
ROOT=Path(__file__).resolve().parents[2]
CASE=ROOT/'.codex-tmp/cases/Shinaide_MeshAbort_CUA'
OUT=Path(__file__).resolve().parent

def processes():
 found=[]
 for p in Path('/proc').iterdir():
  if not p.name.isdigit(): continue
  try:
   cmd=(p/'cmdline').read_bytes().replace(b'\0',b' ').decode(errors='replace').strip()
   if 'dcCartMesh' not in cmd or ((p/'cwd').resolve() != CASE/'Mesh' and str(CASE/'Mesh') not in cmd): continue
   fields=(p/'stat').read_text().rsplit(')',1)[1].split()
   found.append({'pid':int(p.name),'ppid':int(fields[1]),'state':fields[0],
                 'session':int(fields[3]),'startTicks':int(fields[19]),'cmdline':cmd})
  except (FileNotFoundError,ProcessLookupError,PermissionError): pass
 return found

def session():
 source=ROOT.parent/'air/.codex-tmp/api_case.py'
 credentials=None
 for node in ast.walk(ast.parse(source.read_text())):
  if isinstance(node,ast.Dict):
   try: value=ast.literal_eval(node)
   except (ValueError,TypeError): continue
   if isinstance(value,dict) and set(value)=={'username','password'}:
    credentials=value;break
 if credentials is None: raise RuntimeError('existing authentication helper has no literal login')
 s=requests.Session()
 response=s.post('http://127.0.0.1:8396/api/auth/login',json=credentials,timeout=10)
 response.raise_for_status()
 if not response.json().get('ok'): raise RuntimeError('normal login failed')
 return s

mode=sys.argv[1]
now=datetime.datetime.now(datetime.timezone.utc).isoformat()
if mode=='before':
 result={'utc':now,'nativeProcesses':processes()}
 (OUT/'processes-before-stop.json').write_text(json.dumps(result,indent=2)+'\n')
 print(json.dumps(result))
elif mode=='after':
 job=sys.argv[2];s=session()
 status=s.get('http://127.0.0.1:8396/api/job-status',params={'job_id':job},timeout=10)
 status.raise_for_status()
 original=json.loads((OUT/'processes-before-stop.json').read_text())['nativeProcesses']
 previous=[]
 for q in original:
  p=Path('/proc')/str(q['pid'])
  try:
   f=(p/'stat').read_text().rsplit(')',1)[1].split()
   alive=int(f[19])==q['startTicks']
  except (FileNotFoundError,ProcessLookupError): alive=False
  previous.append({'pid':q['pid'],'sameProcessStillExists':alive})
 with (CASE/'.dcdtuni-case.lock').open('a+b') as f:
  try:
   fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB);lockFree=True
   fcntl.flock(f,fcntl.LOCK_UN)
  except BlockingIOError:lockFree=False
 stream=s.get('http://127.0.0.1:8396/api/log-stream',params={'job_id':job},timeout=20)
 stream.raise_for_status();log=stream.content
 (OUT/(job+'-terminal.sse')).write_bytes(log)
 result={'utc':now,'jobId':job,'status':status.json(),'previousProcesses':previous,
         'remainingNativeProcesses':processes(),'caseLockFree':lockFree,
         'terminalSha256':hashlib.sha256(log).hexdigest(),
         'ignoredShutdownTraceback':b'Exception ignored in:' in log,
         'ordinaryTraceback':b'Traceback (most recent call last)' in log,
         'projectSha256':hashlib.sha256((CASE/'Shinaide_liquid.cfdproj').read_bytes()).hexdigest()}
 (OUT/'terminal-verification.json').write_text(json.dumps(result,indent=2)+'\n')
 print(json.dumps(result))
else:raise SystemExit('before or after <jobId>')
