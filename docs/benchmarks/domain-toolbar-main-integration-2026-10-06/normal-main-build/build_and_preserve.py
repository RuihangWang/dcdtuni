from pathlib import Path
import os,json,hashlib,subprocess,shutil,time,re,stat
from datetime import datetime,timezone
B=Path('/mnt/lv/home/litianyu/mycode/dcdtuni');I=B/'.codex-tmp/goal-20261005/integration';O=B/'.codex-tmp/toolbar-domain-normal-main-build-20261006';D=B/'src/frontend/react/dist'
MAIN_HEAD='237d66bb49d9c5bad1b4c26b4ff4a011f1d2db09';IT_HEAD='16557402de55870a62f31467968121fbc6590134'
PIDS={'itLauncher':745627,'itBackend':745658,'mainLauncher':3598106,'mainSupervisor':3598127,'mainBackend':3598160,'kit':3598531};PORTS={8301,8390,8391,8396,8397}
GO={'MAIN_HEAD':MAIN_HEAD,'IT_HEAD':IT_HEAD,'PIDS':PIDS,'CPU':[28,29,30,31],'authorization':'ROOT explicit BUILD GO after MAIN dependency closure149PASS and formalITrestart; one npmrunbuild; no source/Git mutation/browser/service/native/business'}
ENV_GIT=dict(os.environ,GIT_OPTIONAL_LOCKS='0')
def now():return datetime.now(timezone.utc).isoformat()
def fid(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return {'bytes':p.stat().st_size,'sha256':h.hexdigest()}
def write(name,obj):
 with (O/name).open('x') as f:f.write(json.dumps(obj,ensure_ascii=False,indent=2)+'\n')
def git(root,args):return subprocess.check_output(['git','-C',str(root),*args],env=ENV_GIT,text=True)
def head(root):return git(root,['rev-parse','HEAD']).strip()
def inv(root):
 rows=[]
 for dr,ds,fs in os.walk(root,followlinks=False):
  for n in sorted(ds+fs):
   p=Path(dr)/n
   if p.is_symlink():rows.append({'path':str(p.relative_to(root)),'symlink':os.readlink(p)})
   elif p.is_file():rows.append({'path':str(p.relative_to(root)),**fid(p),'mode':oct(stat.S_IMODE(p.stat().st_mode))})
 return sorted(rows,key=lambda r:r['path'])
def pidinfo(pid):
 p=Path('/proc')/str(pid);s=p.joinpath('stat').read_text().rsplit(')',1)[1].split();vals=dict(l.split(':',1) for l in p.joinpath('status').read_text().splitlines() if ':' in l)
 argv=[a.decode(errors='replace') for a in p.joinpath('cmdline').read_bytes().split(b'\0') if a]
 for a in argv:
  if re.search(r'(^|--)(password|secret|access-token|auth-token)(=|$)',a,re.I):raise RuntimeError('unexpected sensitive argv; refuse')
 flags={};visible={}
 for item in p.joinpath('environ').read_bytes().split(b'\0'):
  k,sep,v=item.partition(b'=')
  if not sep:continue
  if k.startswith(b'DCCART_') or k in (b'CART_DBG6',b'S10_CENSUS',b'A4_CENSUS',b'DBG_K1ALL'):flags[k.decode()]=v.decode(errors='replace')
  if k==b'CUDA_VISIBLE_DEVICES':visible[k.decode()]=v.decode(errors='replace')
 return {'pid':pid,'ppid':int(s[1]),'startTicks':int(s[19]),'uid':vals['Uid'].strip().split(),'cpuAllowed':vals['Cpus_allowed_list'].strip(),'cwd':os.readlink(p/'cwd'),'exe':os.readlink(p/'exe'),'argv':argv,'nativeFlags':flags,'cudaVisibility':visible}
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
 return sorted(rows,key=lambda r:(r['port'],r['network'],r['inode']))
main_expected=json.loads((O/'dependency-closure-v2/main144-inputs-after.json').read_text());it_expected=json.loads((O/'dependency-closure-v2/it140-inputs-after.json').read_text());control=json.loads((O/'prepare-reference__next-normal-build-controls.json').read_text());protected=[v['path'] for v in control['protectedMainUserFiles']]
def capture():
 return {'utc':now(),'heads':{'main':head(B),'integration':head(I)},'indexStagedNames':{'main':git(B,['diff','--cached','--name-only']),'integration':git(I,['diff','--cached','--name-only'])},'indexFiles':{'main':fid(B/'.git/index'),'integration':fid(Path((I/'.git').read_text().strip().split(': ',1)[1])/'index')},'main144':[{'path':v['path'],**fid(B/v['path'])} for v in main_expected],'it140':[{'path':v['path'],**fid(I/v['path'])} for v in it_expected],'protected6':[{'path':p,**fid(B/p)} for p in protected],'protectedTrackedFlags':git(B,['ls-files','-v','--',*protected]).splitlines(),'lockIgnored':subprocess.run(['git','-C',str(B),'check-ignore','--',protected[-1]],env=ENV_GIT,text=True,capture_output=True).stdout.splitlines(),'processes':{r:pidinfo(p) for r,p in PIDS.items()},'listeners':listeners()}
handoff=json.loads((O/'committed-handoff-baseline.json').read_text());main_expected=[({'path':handoff['path'],'bytes':handoff['bytes'],'sha256':handoff['sha256']} if v['path']==handoff['path'] else v) for v in main_expected]
write('build-go.json',GO);before=capture();write('build-before.json',before)
assert before['heads']=={'main':MAIN_HEAD,'integration':IT_HEAD},'HEAD mismatch';assert all(not v for v in before['indexStagedNames'].values()),'Index must be empty'
assert before['main144']==main_expected and before['it140']==it_expected,'Frozen input source mismatch'
assert all(a['sha256']==e['sha256'] and a['bytes']==e['bytes'] for a,e in zip(before['protected6'],control['protectedMainUserFiles'])),'Protected6 mismatch'
assert before['protectedTrackedFlags']==control['protectedTrackedFlags'],'Protected flags mismatch'
assert before['processes']['itLauncher']['startTicks']==129560431 and before['processes']['itBackend']['startTicks']==129560442,'IT startTicks mismatch'
assert len(before['listeners'])==5 and {v['port'] for v in before['listeners']}==PORTS,'Listener census mismatch'
assert set(os.sched_getaffinity(0))=={28,29,30,31};assert D.is_dir() and not D.is_symlink() and D.resolve().is_relative_to(B)
old=inv(D);write('dist-before.json',{'root':str(D),'files':old});backup=O/'dist-before';shutil.copytree(D,backup,symlinks=True);assert inv(backup)==old
write('backup-verification.json',{'verified':True,'files':len(old),'bytes':sum(x.get('bytes',0) for x in old),'root':str(backup),'utc':now()})
for n in ['tmp','npm-cache','npm-logs','xdg-cache']:(O/n).mkdir()
env=os.environ.copy();env.update({'TMPDIR':str(O/'tmp'),'TMP':str(O/'tmp'),'TEMP':str(O/'tmp'),'npm_config_cache':str(O/'npm-cache'),'npm_config_logs_dir':str(O/'npm-logs'),'XDG_CACHE_HOME':str(O/'xdg-cache'),'CUDA_VISIBLE_DEVICES':'','OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1'})
command=['/usr/bin/npm','run','build'];write('build-command.json',{'argv':command,'cwd':str(B/'src/frontend/react'),'affinity':sorted(os.sched_getaffinity(0)),'environmentOverrides':{k:env[k] for k in ['TMPDIR','TMP','TEMP','npm_config_cache','npm_config_logs_dir','XDG_CACHE_HOME','CUDA_VISIBLE_DEVICES','OPENBLAS_NUM_THREADS','OMP_NUM_THREADS']},'startedUTC':now(),'scripts':json.loads((B/'src/frontend/react/package.json').read_text())['scripts']})
print('BACKUP_VERIFIED_MAIN_BUILD_START '+str(O),flush=True);start=time.monotonic();startutc=now()
with (O/'build.stdout.log').open('xb') as out,(O/'build.stderr.log').open('xb') as err:rc=subprocess.run(command,cwd=B/'src/frontend/react',env=env,stdout=out,stderr=err).returncode
elapsed=time.monotonic()-start;write('build-result.json',{'returncode':rc,'elapsedSeconds':elapsed,'startedUTC':startutc,'finishedUTC':now(),'stdout':fid(O/'build.stdout.log'),'stderr':fid(O/'build.stderr.log')});write('dist-built.json',{'root':str(D),'files':inv(D)})
entry_before=fid(D/'index.html') if (D/'index.html').is_file() else None;restored=[];same=[];collisions=[];excluded=[]
for row in old:
 rel=row['path'];p=Path(rel);hashed=p.parts[0]=='assets' and re.search(r'-[A-Za-z0-9_-]{8,}\.[^.]+$',p.name) is not None;proof=p.parts[0]=='proof'
 if not(hashed or proof):excluded.append(rel);continue
 source=backup/rel;target=D/rel;cat='proof' if proof else 'oldHashedAsset'
 if 'symlink' in row:collisions.append({'path':rel,'reason':'eligible symlink refused'});continue
 expected={'bytes':row['bytes'],'sha256':row['sha256']}
 if target.exists():
  actual=fid(target)
  if actual==expected:same.append({'path':rel,'category':cat,**actual})
  else:collisions.append({'path':rel,'category':cat,'old':row,'built':actual})
 else:
  target.parent.mkdir(parents=True,exist_ok=True)
  with source.open('rb') as f,target.open('xb') as t:shutil.copyfileobj(f,t)
  shutil.copymode(source,target);actual=fid(target);assert actual==expected;restored.append({'path':rel,'category':cat,**actual})
assert entry_before==(fid(D/'index.html') if (D/'index.html').is_file() else None),'New index altered'
write('asset-restoration.json',{'restored':restored,'sameNameSameBytes':same,'collisions':collisions,'excludedOldFiles':excluded,'oldIndexRestored':False})
after=capture();write('build-after.json',after);write('dist-after.json',{'root':str(D),'files':inv(D)});entries=[]
if (D/'index.html').is_file():
 for rel in sorted(set(re.findall(r'(?:src|href)="([^"]+)"',(D/'index.html').read_text()))):
  q=D/rel.split('?',1)[0].lstrip('./')
  if q.is_file():entries.append({'path':str(q.relative_to(D)),**fid(q)})
receipt={'scope':'one normal MAIN frontend build plus complete old-dist backup/exact old hashed-asset and allproof append; no browser/API/Kit/service/native/Git mutation','returncode':rc,'elapsedSeconds':elapsed,'main144Unchanged':before['main144']==after['main144'],'it140Unchanged':before['it140']==after['it140'],'protectedMain6Unchanged':before['protected6']==after['protected6'],'protectedFlagsUnchanged':before['protectedTrackedFlags']==after['protectedTrackedFlags'] and before['lockIgnored']==after['lockIgnored'],'sixProcessesUnchanged':before['processes']==after['processes'],'fiveListenersUnchanged':before['listeners']==after['listeners'],'headsUnchanged':before['heads']==after['heads'],'indexesUnchangedAndEmpty':before['indexFiles']==after['indexFiles'] and all(not v for v in before['indexStagedNames'].values()) and all(not v for v in after['indexStagedNames'].values()),'distBackupVerified':inv(backup)==old,'distBackupFiles':len(old),'distBackupBytes':sum(x.get('bytes',0) for x in old),'newEntry':fid(D/'index.html') if (D/'index.html').is_file() else None,'entryFiles':entries,'restoredFiles':len(restored),'restoredProofFiles':sum(v['category']=='proof' for v in restored),'sameNameSameBytes':len(same),'collisions':len(collisions),'runtimeUiGpuAcceptance':'not run; ROOT normal UI remains separate','mainOnlyDistBuilt':True,'extraTestsRun':False}
write('receipt.json',receipt);print(json.dumps(receipt,ensure_ascii=False),flush=True)
if rc or collisions or not all(receipt[k] for k in ['main144Unchanged','it140Unchanged','protectedMain6Unchanged','protectedFlagsUnchanged','sixProcessesUnchanged','fiveListenersUnchanged','headsUnchanged','indexesUnchangedAndEmpty','distBackupVerified']):raise SystemExit(1)
