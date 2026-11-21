"""Update only recognized mesh preset leaves in current generated case inputs."""
from pathlib import Path
import copy,hashlib,json,os,stat,subprocess,tarfile,tempfile,time
repo=Path('/mnt/lv/home/litianyu/mycode/dcdtuni').resolve()
work=repo/'.codex-tmp/goal-20261005/preserve'
work.mkdir(parents=True,exist_ok=True)
presets={'meshBaseSize':.25,'meshEngine':'dcCart','meshTimeLimitSeconds':300,
 'parallelMesh':True,'meshProcess':8,'weldTolerance':.0012,
 'firstLayerMinFraction':.3,'firstLayerMax':.005,'firstLayerSealGap':-1,'rackCellMinSize':.0175}
tracked=set(subprocess.check_output(['git','ls-files','-z'],cwd=repo).decode().split('\0'))
files=sorted(p for scope in ['case/GTC_dataset','case/Slab_dataset']
 for p in (repo/scope).rglob('*.cfdproj') if str(p.relative_to(repo)) not in tracked)
assert len(files)==1502,len(files)
for p in files: assert p.resolve().is_relative_to(repo) and not p.is_symlink(),str(p)
backup=work/'generated-case-inputs-before-presets.tar.gz'
assert not backup.exists(),'refuse replacing original backup'
rows=[]
started=time.monotonic()
with tarfile.open(backup,'w:gz',compresslevel=1) as archive:
 for p in files:
  raw=p.read_bytes();d=json.loads(raw)
  if int(d.get('schemaVersion',2))<3: location='cfdRoom';cfg=d.get('cfdRoom')
  else: location='runDefaults/meshConfig';cfg=d.get('runDefaults',{}).get('meshConfig')
  assert isinstance(cfg,dict),(str(p),location)
  rows.append({'path':str(p.relative_to(repo)),'schemaVersion':d.get('schemaVersion'),
   'presetLocation':location,'beforeSha256':hashlib.sha256(raw).hexdigest()})
  archive.add(p,arcname=str(p.relative_to(repo)),recursive=False)
print('Original input backup complete:',len(rows),'files',round(time.monotonic()-started,2),'s',flush=True)
def without_preset(d,location):
 clone=copy.deepcopy(d)
 cfg=clone['cfdRoom'] if location=='cfdRoom' else clone['runDefaults']['meshConfig']
 for key in presets:cfg.pop(key,None)
 return json.dumps(clone,sort_keys=True,separators=(',',':'),ensure_ascii=False)
for i,row in enumerate(rows):
 p=repo/row['path'];raw=p.read_bytes()
 assert hashlib.sha256(raw).hexdigest()==row['beforeSha256'],'input changed after backup: '+row['path']
 d=json.loads(raw);invariant=without_preset(d,row['presetLocation'])
 cfg=d['cfdRoom'] if row['presetLocation']=='cfdRoom' else d['runDefaults']['meshConfig']
 changed=[k for k,v in presets.items() if k not in cfg or type(cfg[k])!=type(v) or cfg[k]!=v]
 cfg.update(presets)
 assert without_preset(d,row['presetLocation'])==invariant
 output=(json.dumps(d,ensure_ascii=False,indent=2)+'\n').encode()
 fd,temp=tempfile.mkstemp(prefix='.mesh-preset-',dir=str(p.parent))
 try:
  with os.fdopen(fd,'wb') as stream:stream.write(output)
  os.chmod(temp,stat.S_IMODE(p.stat().st_mode))
  os.replace(temp,p)
 finally:
  if os.path.exists(temp):os.unlink(temp)
 check=json.loads(p.read_bytes())
 assert without_preset(check,row['presetLocation'])==invariant
 row.update(afterSha256=hashlib.sha256(output).hexdigest(),changedPresetKeys=changed,
  nonPresetSemanticSha256=hashlib.sha256(invariant.encode()).hexdigest(),noOtherLeavesChanged=True)
 if (i+1)%500==0:print('Presets migrated:',i+1,'/',len(rows),flush=True)
result={'scope':'Ignored current generated top-level GTC/Slab case inputs only; tracked templates are handled by Git. No model/cfdrun/log/sampling record modified.',
 'repo':str(repo),'sourceSha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
 'backup':str(backup.relative_to(repo)),'preset':presets,'count':len(rows),
 'allNonPresetSemanticsPreserved':True,'caseAcceptance':'No mesh or solve execution. Preset deadline does not prove timing.',
 'wallSeconds':time.monotonic()-started,'files':rows}
(work/'generated-case-input-preset-migration.json').write_text(json.dumps(result,indent=2)+'\n')
print('Complete:',len(rows),'current generated inputs; unchanged geometry and physics;',round(result['wallSeconds'],2),'s',flush=True)
