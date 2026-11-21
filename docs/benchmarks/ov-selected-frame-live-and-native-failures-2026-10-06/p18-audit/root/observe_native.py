from pathlib import Path
import json,os,time,hashlib,datetime
I=Path('/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/integration')
T=I/'.codex-tmp/p18-root-live-preflight-20261006';CASE=I/'.codex-tmp/cases/Shinaide_LegacyJournalScopeP18_CUA'
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
started=time.monotonic();last=[]
while time.monotonic()-started<300:
 rows=[]
 for d in Path('/proc').iterdir():
  if not d.name.isdigit():continue
  try:
   if (d/'comm').read_text().strip()!='dcCartMesh':continue
   status=(d/'status').read_text();uid=next(l for l in status.splitlines() if l.startswith('Uid:')).split()[1:]
   if uid!=['1002']*4:continue
   cwd=str((d/'cwd').resolve())
   if not cwd.startswith(str(CASE)+'/'):continue
   argv=(d/'cmdline').read_bytes().split(b'\0');env={}
   for line in (d/'environ').read_bytes().split(b'\0'):
    if b'=' not in line:continue
    key,val=line.split(b'=',1);key=key.decode(errors='replace')
    if key.startswith('DCCART_') or key in ['CUDA_VISIBLE_DEVICES','OMP_NUM_THREADS','PMI_RANK','PMIX_RANK','OMPI_COMM_WORLD_RANK']:env[key]=val.decode(errors='replace')
   exe=(d/'exe').resolve();maps=(d/'maps').read_text();libs=sorted(set(line.split(maxsplit=5)[5] for line in maps.splitlines() if 'libdcCartMesh.so' in line and len(line.split(maxsplit=5))==6))
   if not libs:continue
   stat=(d/'stat').read_text();fields=stat[stat.rindex(')')+2:].split()
   rows.append({'pid':int(d.name),'ppid':int(fields[1]),'startTicks':fields[19],'uid':uid,'cpu':next(l.split(':',1)[1].strip() for l in status.splitlines() if l.startswith('Cpus_allowed_list:')),'cwd':cwd,'argv':[a.decode(errors='replace') for a in argv if a],'exe':str(exe),'exeSha256':digest(exe),'selectedEnv':env,'loadedLibrary':[{'path':name,'sha256':digest(Path(name))} for name in libs]})
  except (OSError,StopIteration,ValueError):continue
 if rows:
  last=rows;(T/'latest-native-observation.json').write_text(json.dumps({'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'processes':rows,'scope':'Actual native ranks only; not full process-tree census'},indent=2))
 if len(rows)>=8:
  (T/'actual-native-observation.json').write_text((T/'latest-native-observation.json').read_text());print('CAPTURED',len(rows),'native ranks only',flush=True);break
 time.sleep(.5)
else:
 (T/'observer-timeout.json').write_text(json.dumps({'captured':len(last),'elapsed':time.monotonic()-started}));print('TIMEOUT',len(last),flush=True)
