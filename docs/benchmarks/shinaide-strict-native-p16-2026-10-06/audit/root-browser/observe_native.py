from pathlib import Path
import os,json,time,hashlib,re
from datetime import datetime,timezone
O=Path(__file__).parent
CASE='/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/integration/.codex-tmp/cases/Shinaide_LegacyJournalP16_CUA/Mesh'
def fid(p):
 r=Path(p).read_bytes();return {'bytes':len(r),'sha256':hashlib.sha256(r).hexdigest()}
def observe():
 rows=[]
 for p in Path('/proc').iterdir():
  if not p.name.isdecimal():continue
  try:
   if p.stat().st_uid!=1002:continue
   cwd=os.readlink(p/'cwd');argv=[x.decode(errors='replace') for x in (p/'cmdline').read_bytes().split(b'\0') if x]
   if cwd!=CASE:continue
   exe=os.readlink(p/'exe');base=Path(exe).name
   if base not in ['dcCartMesh','mpiexec.hydra','hydra_pmi_proxy','bash']:continue
   if any(re.search(r'password|secret|token',arg,re.I) for arg in argv):raise RuntimeError('Refusing unexpected sensitive argv')
   st=(p/'stat').read_text().rsplit(')',1)[1].split();status=(p/'status').read_text().splitlines()
   flags={}
   for e in (p/'environ').read_bytes().split(b'\0'):
    k,sep,v=e.partition(b'=')
    if sep and k.startswith(b'DCCART_'):flags[k.decode()]=v.decode()
   paths=sorted(set(l.split()[-1] for l in (p/'maps').read_text().splitlines() if 'libdcCartMesh.so' in l))
   maps=[{'path':v,**fid(v)} for v in paths]
   rows.append({'pid':int(p.name),'ppid':int(st[1]),'startTicks':int(st[19]),'cwd':cwd,'exe':exe,'exeIdentity':fid(p/'exe'),'argv':argv,'cpuAllowed':[l for l in status if l.startswith('Cpus_allowed_list:')],'uid':[l for l in status if l.startswith('Uid:')],'nativeFlags':flags,'mappedNativeLibs':maps})
  except (FileNotFoundError,ProcessLookupError,PermissionError):continue
 return rows
print('READ_ONLY_OBSERVER_READY',flush=True)
start=time.monotonic();first=None
while time.monotonic()-start<240:
 rows=observe();ranks=[r for r in rows if Path(r['exe']).name=='dcCartMesh']
 if rows and first is None:
  first={'utc':datetime.now(timezone.utc).isoformat(),'processes':rows};(O/'first-native-observation.json').write_text(json.dumps(first,indent=2)+'\n')
 if len(ranks)==8 and all(r['mappedNativeLibs'] for r in ranks):
  result={'utc':datetime.now(timezone.utc).isoformat(),'elapsedSeconds':time.monotonic()-start,'caseCwd':CASE,'processCount':len(rows),'nativeRankCount':len(ranks),'processes':rows,'scope':'read-only actual process snapshot; not job completion/mesh acceptance'}
  (O/'actual-native-observation.json').write_text(json.dumps(result,indent=2)+'\n');print('CAPTURED '+json.dumps({'processCount':len(rows),'ranks':len(ranks),'newLibrarySha':sorted(set(m['sha256'] for r in ranks for m in r['mappedNativeLibs']))}),flush=True);break
 time.sleep(.5)
else:
 (O/'observer-timeout.json').write_text(json.dumps({'seconds':time.monotonic()-start,'caseCwd':CASE,'firstSeen':first is not None},indent=2)+'\n');print('OBSERVER_TIMEOUT',flush=True)
