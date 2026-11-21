from pathlib import Path
import os, json, hashlib, subprocess, shutil, time, re
from datetime import datetime, timezone
B=Path('/mnt/lv/home/litianyu/mycode/dcdtuni')
T=B/'.codex-tmp/goal-20261005/integration'
O=T/'.codex-tmp/domain-object-foundation-build-20261006'
D=T/'src/frontend/react/dist'
F=T/'.codex-tmp/domain-object-foundation-20261006'
PIDS={'itLauncher':140357,'itBackend':140390,'mainLauncher':3598106,'mainSupervisor':3598127,'mainBackend':3598160,'kit':3598531}
PORTS={8301,8390,8391,8396,8397}
protected=['case/Alibaba-hongchou/Alibaba_hangchou.cfdproj','case/Alibaba-sige/Alibaba-sige.cfdproj','case/GTC/GTC_2025.cfdproj','case/GTC2026/GTC_2026.cfdproj','case/GTC_dataset/template/GTC_template.cfdproj','case/GTC2026/.dcdtuni-case.lock']
def now(): return datetime.now(timezone.utc).isoformat()
def fileid(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for x in iter(lambda:f.read(1024*1024),b''): h.update(x)
 return {'bytes':p.stat().st_size,'sha256':h.hexdigest()}
def write(name,obj): (O/name).write_text(json.dumps(obj,indent=2,ensure_ascii=False)+'\n')
def head(p): return subprocess.check_output(['git','rev-parse','HEAD'],cwd=p,text=True).strip()
def inventory(root):
 rows=[]
 for parent,dirs,files in os.walk(root,followlinks=False):
  for name in sorted(dirs+files):
   p=Path(parent)/name
   if p.is_symlink(): rows.append({'path':str(p.relative_to(root)),'symlink':os.readlink(p)})
   elif p.is_file(): rows.append({'path':str(p.relative_to(root)),**fileid(p)})
 return sorted(rows,key=lambda r:r['path'])
def pidinfo(pid):
 p=Path('/proc')/str(pid)
 try:
  stat=p.joinpath('stat').read_text().rsplit(')',1)[1].split()
  status=p.joinpath('status').read_text()
  vals=dict(line.split(':',1) for line in status.splitlines() if ':' in line)
  flags={}
  for v in p.joinpath('environ').read_bytes().split(b'\0'):
   k,sep,value=v.partition(b'=')
   if sep and k.startswith(b'DCCART_'): flags[k.decode()]=value.decode(errors='replace')
  return {'pid':pid,'ppid':int(stat[1]),'startTicks':int(stat[19]),'uid':vals['Uid'].strip().split(),'cpuAllowed':vals['Cpus_allowed_list'].strip(),'cwd':os.readlink(p/'cwd'),'exe':os.readlink(p/'exe'),'nativeFlags':flags}
 except (OSError,ValueError,KeyError) as e: return {'pid':pid,'unavailable':type(e).__name__}
def listeners():
 owners={}
 for name,pid in PIDS.items():
  fd=Path('/proc')/str(pid)/'fd'
  try:
   for path in fd.iterdir():
    try: link=os.readlink(path)
    except OSError: continue
    m=re.fullmatch(r'socket:\[(\d+)\]',link)
    if m: owners.setdefault(m.group(1),[]).append({'role':name,'pid':pid})
  except OSError: pass
 rows=[]
 for net in ('tcp','tcp6'):
  for line in (Path('/proc/net')/net).read_text().splitlines()[1:]:
   x=line.split(); port=int(x[1].split(':')[1],16)
   if port in PORTS and x[3]=='0A':
    rows.append({'network':net,'localAddress':x[1],'port':port,'inode':x[9],'knownOwners':owners.get(x[9],[])})
 return sorted(rows,key=lambda r:(r['port'],r['network'],r['inode']))
expected=json.loads((F/'source-identity.json').read_text())['files']
def capture():
 sources=[{'path':r['path'],**fileid(T/r['path'])} for r in expected]
 return {'utc':now(),'heads':{'main':head(B),'integration':head(T)},'sourceFiles':sources,
  'protectedFiles':[{'path':r,**fileid(B/r)} for r in protected],
  'protectedTrackedFlags':subprocess.check_output(['git','ls-files','-v','--',*protected],cwd=B,text=True).splitlines(),
  'lockIgnored':subprocess.run(['git','check-ignore','--',protected[-1]],cwd=B,text=True,capture_output=True).stdout.splitlines(),
  'processes':{k:pidinfo(v) for k,v in PIDS.items()},'listeners':listeners()}
before=capture()
assert all(r['sha256']==e['sha256'] and r['bytes']==e['bytes'] for r,e in zip(before['sourceFiles'],expected)), 'Frozen source mismatch'
assert D.is_dir() and not D.is_symlink() and D.resolve().is_relative_to(T), 'Unexpected dist target'
write('before.json',before)
old=inventory(D); write('dist-before.json',{'root':str(D),'files':old})
backup=O/'dist-before'
shutil.copytree(D,backup,symlinks=True)
assert inventory(backup)==old,'Full backup mismatch'
write('backup-verification.json',{'verified':True,'root':str(backup),'files':len(old),'bytes':sum(x.get('bytes',0) for x in old),'utc':now()})
(O/'tmp').mkdir(); (O/'npm-cache').mkdir()
env=os.environ.copy(); env.update({'TMPDIR':str(O/'tmp'),'TMP':str(O/'tmp'),'TEMP':str(O/'tmp'),'npm_config_cache':str(O/'npm-cache'),'CUDA_VISIBLE_DEVICES':'','OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1'})
start=time.monotonic(); startutc=now()
with (O/'build.log').open('wb') as log:
 rc=subprocess.run(['/usr/bin/npm','run','build'],cwd=T/'src/frontend/react',env=env,stdout=log,stderr=subprocess.STDOUT).returncode
elapsed=time.monotonic()-start
write('build-result.json',{'command':['/usr/bin/npm','run','build'],'cwd':str(T/'src/frontend/react'),'cpuAllowed':os.sched_getaffinity(0).__repr__(),'cudaVisibleDevices':'','tmpdir':str(O/'tmp'),'startUtc':startutc,'endUtc':now(),'elapsedSeconds':elapsed,'returncode':rc,**fileid(O/'build.log')})
write('dist-built.json',{'root':str(D),'files':inventory(D)})
newindex_before=fileid(D/'index.html') if (D/'index.html').exists() else None
restored=[]; same=[]; collisions=[]; excluded=[]
for row in old:
 path=row['path']; p=Path(path)
 ishash=(p.parts[0]=='assets' and re.search(r'-[A-Za-z0-9_-]{8,}\.[^.]+$',p.name) is not None)
 isproof=(p.name.startswith('proof-') and p.suffix.lower() in ('.jpg','.jpeg'))
 if not (ishash or isproof): excluded.append(path); continue
 source=backup/path; target=D/path
 if 'symlink' in row:
  collisions.append({'path':path,'reason':'eligible symlink requires manual review'}); continue
 category='proofJPEG' if isproof else 'oldHashedAsset'
 if target.exists():
  actual=fileid(target)
  if actual=={'bytes':row['bytes'],'sha256':row['sha256']}: same.append({'path':path,'category':category,**actual})
  else: collisions.append({'path':path,'category':category,'before':row,'built':actual})
 else:
  target.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(source,target)
  actual=fileid(target); assert actual=={'bytes':row['bytes'],'sha256':row['sha256']}
  restored.append({'path':path,'category':category,**actual})
assert newindex_before==(fileid(D/'index.html') if (D/'index.html').exists() else None),'New index altered'
write('asset-restoration.json',{'restored':restored,'sameNameSameBytes':same,'collisions':collisions,'excludedOldFiles':excluded,'oldIndexRestored':False})
after=capture(); write('after.json',after)
write('dist-after.json',{'root':str(D),'files':inventory(D)})
entry=[]
if (D/'index.html').exists():
 index=(D/'index.html').read_text()
 for rel in sorted(set(re.findall(r'(?:src|href)="([^"]+)"',index))):
  rel=rel.split('?',1)[0].lstrip('./')
  if rel.startswith('/'): rel=rel[1:]
  p=D/rel
  if p.is_file(): entry.append({'path':rel,**fileid(p)})
receipt={'scope':'Normal integration production build only; no service, browser, Git/index or MAIN mutation',
 'returncode':rc,'elapsedSeconds':elapsed,'source11Unchanged':before['sourceFiles']==after['sourceFiles'],
 'protected6Unchanged':before['protectedFiles']==after['protectedFiles'],'protectedFlagsUnchanged':before['protectedTrackedFlags']==after['protectedTrackedFlags'] and before['lockIgnored']==after['lockIgnored'],
 'sixProcessIdentitiesUnchanged':before['processes']==after['processes'],'fiveListenersUnchanged':before['listeners']==after['listeners'],
 'headsUnchanged':before['heads']==after['heads'],'newIndex':fileid(D/'index.html') if (D/'index.html').exists() else None,'entryFiles':entry,
 'oldDistBackup':str(backup),'oldDistFiles':len(old),'oldDistBytes':sum(x.get('bytes',0) for x in old),'restoredFiles':len(restored),'restoredProofJPEGs':sum(r['category']=='proofJPEG' for r in restored),'sameNameSameBytes':len(same),'collisions':len(collisions),
 'previousTypecheckNotRepeated':True,'buildScriptIncludesTscB':True}
write('receipt.json',receipt)
(O/'README.md').write_text('# Domain foundation normal integration build\n\nNormal npm run build; package script tsc -b then Vite. Standalone typecheck and test suite were not repeated. Full pre-build dist copied and byte-verified in dist-before/. New index retained. Missing prior hash assets and proof JPEGs restored byte-exactly; same-name assets compared before any restoration. No browser, service, Git/index or MAIN action.\n\nLarge backup files are an owned preservation tree, inventoried by dist-before.json and backup-verification.json; they are not proposed publication payloads. Stage 1 remains a plain-JSON domain foundation, not renderer/physics/SimReady acceptance.\n')
payload=[p for p in O.iterdir() if p.is_file() and p.name!='manifest.json']
write('manifest.json',{'schema':'dcdtuni.owned-build-proof/v1','createdUtc':now(),'files':[{'path':p.name,**fileid(p)} for p in sorted(payload)],'backupInventory':'dist-before.json','backupRoot':'dist-before/','sourceFreezeManifest':fileid(F/'manifest.json'),'scope':'integration build and exact missing-asset restoration only'})
print(json.dumps(receipt,ensure_ascii=False))
print('MANIFEST',fileid(O/'manifest.json'))
if rc or collisions: raise SystemExit(1)
