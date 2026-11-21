
from pathlib import Path
import os,json,hashlib,subprocess,shutil,time,re
from datetime import datetime,timezone
B=Path('/mnt/lv/home/litianyu/mycode/dcdtuni');T=B/'.codex-tmp/goal-20261005/integration'
O=T/'.codex-tmp/domain-box-cdu-material-normal-it-build-20261006';D=T/'src/frontend/react/dist'
C=T/'docs/benchmarks/domain-box-cdu-material-2026-10-06/next-normal-build-controls.json'
PIDS={'itLauncher':553430,'itBackend':553461,'mainLauncher':3598106,'mainSupervisor':3598127,'mainBackend':3598160,'kit':3598531}
PORTS={8301,8390,8391,8396,8397}
def now():return datetime.now(timezone.utc).isoformat()
def fid(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return {'bytes':p.stat().st_size,'sha256':h.hexdigest()}
def write(name,value):
 with (O/name).open('x') as f:f.write(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
def inv(root):
 rows=[]
 for dr,ds,fs in os.walk(root,followlinks=False):
  for n in sorted(ds+fs):
   p=Path(dr)/n
   if p.is_symlink():rows.append({'path':str(p.relative_to(root)),'symlink':os.readlink(p)})
   elif p.is_file():rows.append({'path':str(p.relative_to(root)),**fid(p)})
 return sorted(rows,key=lambda r:r['path'])
def head(p):return subprocess.check_output(['git','-C',str(p),'rev-parse','HEAD'],text=True).strip()
def pidinfo(pid):
 p=Path('/proc')/str(pid);st=p.joinpath('stat').read_text().rsplit(')',1)[1].split()
 vals=dict(l.split(':',1) for l in p.joinpath('status').read_text().splitlines() if ':' in l)
 argv=[b.decode(errors='replace') for b in p.joinpath('cmdline').read_bytes().split(b'\0') if b]
 for a in argv:
  if re.search(r'(^|--)(password|secret|access-token|auth-token)(=|$)',a,re.I):raise RuntimeError('unexpected sensitive argv; refuse recording')
 flags={}
 for item in p.joinpath('environ').read_bytes().split(b'\0'):
  k,sep,v=item.partition(b'=')
  if sep and k.startswith(b'DCCART_'):flags[k.decode()]=v.decode(errors='replace')
 return {'pid':pid,'ppid':int(st[1]),'startTicks':int(st[19]),'uid':vals['Uid'].strip().split(),'cpuAllowed':vals['Cpus_allowed_list'].strip(),'cwd':os.readlink(p/'cwd'),'exe':os.readlink(p/'exe'),'argv':argv,'nativeFlags':flags}
def listeners():
 owners={}
 for role,pid in PIDS.items():
  for p in (Path('/proc')/str(pid)/'fd').iterdir():
   try:v=os.readlink(p)
   except OSError:continue
   m=re.fullmatch(r'socket:\[(\d+)\]',v)
   if m:owners.setdefault(m.group(1),[]).append({'role':role,'pid':pid})
 rows=[]
 for net in ['tcp','tcp6']:
  for l in (Path('/proc/net')/net).read_text().splitlines()[1:]:
   x=l.split();port=int(x[1].split(':')[1],16)
   if port in PORTS and x[3]=='0A':rows.append({'network':net,'localAddress':x[1],'port':port,'inode':x[9],'knownOwners':owners.get(x[9],[])})
 return sorted(rows,key=lambda x:(x['port'],x['network'],x['inode']))
control=json.loads(C.read_text());protected=[x['path'] for x in control['protectedMainUserFiles']]
def capture():
 return {'utc':now(),'heads':{'main':head(B),'integration':head(T)},
  'indexedNames':subprocess.check_output(['git','-C',str(T),'diff','--cached','--name-only'],text=True),
  'source20':[{'group':r['group'],'path':r['path'],**fid(T/r['path'])} for r in control['controls']],
  'protected6':[{'path':p,**fid(B/p)} for p in protected],
  'protectedTrackedFlags':subprocess.check_output(['git','-C',str(B),'ls-files','-v','--']+protected,text=True).splitlines(),
  'lockIgnored':subprocess.run(['git','-C',str(B),'check-ignore','--',protected[-1]],text=True,capture_output=True).stdout.splitlines(),
  'processes':{r:pidinfo(p) for r,p in PIDS.items()},'listeners':listeners()}
O.mkdir(parents=True,exist_ok=False)
write('frozen-next-build-controls-copy.json',control)
before=capture();write('before.json',before)
assert before['indexedNames']=='','Index not empty'
for a,e in zip(before['source20'],control['controls']):assert a['sha256']==e['sha256'] and a['bytes']==e['bytes'],'Source control mismatch'
for a,e in zip(before['protected6'],control['protectedMainUserFiles']):assert a['sha256']==e['sha256'] and a['bytes']==e['bytes'],'Protected input mismatch'
assert before['protectedTrackedFlags']==control['protectedTrackedFlags'],'Protected flags changed'
assert D.is_dir() and not D.is_symlink() and D.resolve().is_relative_to(T),'Unexpected dist target'
old=inv(D);write('dist-before.json',{'root':str(D),'files':old})
backup=O/'dist-before';shutil.copytree(D,backup,symlinks=True)
assert inv(backup)==old
write('backup-verification.json',{'verified':True,'root':str(backup),'files':len(old),'bytes':sum(x.get('bytes',0) for x in old),'utc':now()})
(O/'tmp').mkdir();(O/'npm-cache').mkdir()
env=os.environ.copy();env.update({'TMPDIR':str(O/'tmp'),'TMP':str(O/'tmp'),'TEMP':str(O/'tmp'),'npm_config_cache':str(O/'npm-cache'),'CUDA_VISIBLE_DEVICES':'','OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1'})
command=['/usr/bin/npm','run','build']
write('build-command.json',{'argv':command,'cwd':str(T/'src/frontend/react'),'affinity':sorted(os.sched_getaffinity(0)),'environmentOverrides':{k:env[k] for k in ['TMPDIR','TMP','TEMP','npm_config_cache','CUDA_VISIBLE_DEVICES','OPENBLAS_NUM_THREADS','OMP_NUM_THREADS']},'startedUTC':now(),'scripts':json.loads((T/'src/frontend/react/package.json').read_text())['scripts']})
start=time.monotonic();startutc=now()
print('READY_BACKUP_VERIFIED_BUILD_START '+str(O),flush=True)
with (O/'build.stdout.log').open('xb') as out, (O/'build.stderr.log').open('xb') as err:
 rc=subprocess.run(command,cwd=T/'src/frontend/react',env=env,stdout=out,stderr=err).returncode
elapsed=time.monotonic()-start
write('build-result.json',{'returncode':rc,'elapsedSeconds':elapsed,'startedUTC':startutc,'finishedUTC':now(),'stdout':fid(O/'build.stdout.log'),'stderr':fid(O/'build.stderr.log')})
built=inv(D);write('dist-built.json',{'root':str(D),'files':built})
entrybefore=fid(D/'index.html') if (D/'index.html').exists() else None
restored=[];same=[];collisions=[];excluded=[]
for row in old:
 rel=row['path'];p=Path(rel)
 hashed=p.parts[0]=='assets' and re.search(r'-[A-Za-z0-9_-]{8,}\.[^.]+$',p.name) is not None
 proof=p.parts[0]=='proof'
 if not(hashed or proof):excluded.append(rel);continue
 s=backup/rel;t=D/rel;category='proof' if proof else 'oldHashedAsset'
 if 'symlink' in row:
  collisions.append({'path':rel,'reason':'eligible symlink refused'});continue
 if t.exists():
  got=fid(t)
  if got=={'bytes':row['bytes'],'sha256':row['sha256']}:same.append({'path':rel,'category':category,**got})
  else:collisions.append({'path':rel,'category':category,'old':row,'built':got})
 else:
  t.parent.mkdir(parents=True,exist_ok=True)
  with s.open('rb') as source,t.open('xb') as target:shutil.copyfileobj(source,target)
  got=fid(t);assert got=={'bytes':row['bytes'],'sha256':row['sha256']}
  restored.append({'path':rel,'category':category,**got})
assert entrybefore==(fid(D/'index.html') if (D/'index.html').exists() else None),'New index altered'
write('asset-restoration.json',{'restored':restored,'sameNameSameBytes':same,'collisions':collisions,'excludedOldFiles':excluded,'oldIndexRestored':False})
after=capture();write('after.json',after);write('dist-after.json',{'root':str(D),'files':inv(D)})
entries=[]
if (D/'index.html').is_file():
 for rel in sorted(set(re.findall(r'(?:src|href)="([^"]+)"',(D/'index.html').read_text()))):
  r=rel.split('?',1)[0].lstrip('./');q=D/r
  if q.is_file():entries.append({'path':r,**fid(q)})
receipt={'scope':'Single normal IT material production build, preserve missing old hashed assets and all proof files; no browser/API/service/physics/Git mutation.',
 'returncode':rc,'elapsedSeconds':elapsed,'source20Unchanged':before['source20']==after['source20'],'protectedMain6Unchanged':before['protected6']==after['protected6'],
 'protectedFlagsUnchanged':before['protectedTrackedFlags']==after['protectedTrackedFlags'] and before['lockIgnored']==after['lockIgnored'],
 'sixProcessIdentitiesAndArgvUnchanged':before['processes']==after['processes'],'fiveListenersUnchanged':before['listeners']==after['listeners'],
 'headsUnchanged':before['heads']==after['heads'],'indexedNamesUnchangedAndEmpty':before['indexedNames']==after['indexedNames']=='',
 'distBackup':{'root':str(backup),'files':len(old),'bytes':sum(x.get('bytes',0) for x in old),'verified':inv(backup)==old},
 'newEntry':fid(D/'index.html') if (D/'index.html').exists() else None,'entryFiles':entries,
 'restoredFiles':len(restored),'restoredProofFiles':sum(x['category']=='proof' for x in restored),'sameNameSameBytes':len(same),'collisions':len(collisions),
 'runtimeGpuAcceptance':'pending ROOT normal UI; build does not establish raster/GPU acceptance','mainDistTouched':False,'extraTestsRun':False}
write('receipt.json',receipt)
(O/'README.md').write_text('# BOX/CDU material normal IT build\n\nOne normal npm run build (tsc -b and Vite) with frozen source8+CP6+BOX3+original3goldens, Main six protected inputs/flags, six service PID/startticks/argv and five listener identities checked before/after. Complete old dist copied and hashed independently. Missing old hashed assets and proof files restored exactly; new index retained, collisions refused. No Main dist, service/browser/API/physics/Git or HANDOFF action. This is build evidence only; real UI/GPU/runtime remains pending ROOT.\n')
files=[{'path':p.name,**fid(p)} for p in sorted(O.iterdir()) if p.is_file() and p.name!='manifest.json']
write('manifest.json',{'format':'normal material IT build evidence/v1','files':files,'backupInventory':'dist-before.json','backupRoot':'dist-before/','controlManifestSHA256':fid(C),'scope':'single production frontend build and missing-asset preservation only'})
print(json.dumps(receipt,ensure_ascii=False),flush=True);print('MANIFEST '+json.dumps(fid(O/'manifest.json')),flush=True)
if rc or collisions or not all(receipt[k] for k in ['source20Unchanged','protectedMain6Unchanged','protectedFlagsUnchanged','sixProcessIdentitiesAndArgvUnchanged','fiveListenersUnchanged','headsUnchanged','indexedNamesUnchangedAndEmpty']):raise SystemExit(1)
