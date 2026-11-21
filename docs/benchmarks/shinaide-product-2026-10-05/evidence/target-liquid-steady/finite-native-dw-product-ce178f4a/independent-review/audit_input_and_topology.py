import json,pathlib,hashlib,time,datetime,re,copy
B=pathlib.Path('/mnt/lv/home/litianyu/mycode/dcdtuni')
T=B/'.codex-tmp/goal-20261005/integration'
E=T/'docs/benchmarks/shinaide-product-2026-10-05/evidence/target-liquid-steady/finite-native-product-495546f2'
P=T/'.codex-tmp/finite-native-dw-product-20261005'; O=P/'independent-review'
D=T/'.codex-tmp/cases/Shinaide_PointRepairR_CUA'
OLD=T/'.codex-tmp/cases/Shinaide_MeshAbort_CUA'
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for c in iter(lambda:f.read(8*1024*1024),b''):h.update(c)
 return h.hexdigest()
def ident(p):return dict(path=str(p),bytes=p.stat().st_size,sha256=sha(p))
def write(name,obj): (O/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n')
def difference(a,b,path=''):
 if type(a)!=type(b):return [dict(path=path,old=a,new=b)]
 if isinstance(a,dict):
  out=[]
  for k in sorted(a.keys()|b.keys()):
   q=path+'/'+k.replace('~','~0').replace('/','~1')
   if k not in a:out.append(dict(path=q,oldAbsent=True,new=b[k]))
   elif k not in b:out.append(dict(path=q,old=a[k],newAbsent=True))
   else:out+=difference(a[k],b[k],q)
  return out
 if isinstance(a,list):
  if len(a)!=len(b):return [dict(path=path,old=a,new=b)]
  return [c for i,(x,y) in enumerate(zip(a,b)) for c in difference(x,y,path+'/'+str(i))]
 return [] if a==b else [dict(path=path,old=a,new=b)]
started=time.monotonic()
manifest=json.loads((E/'full-case-raw-manifest.json').read_text())
assert sha(E/'full-case-raw-manifest.json')=='ae413d4816b1067fc6a468112541c5f95a95f999daa8280aa572c4a9cc39d82e'
source=OLD/'model/Shinaide_liquid.cfdrun';raw=source.read_bytes()
old_id=ident(source); receipt=json.loads((E/'cfdrun-original-vs-fresh-react-export.json').read_text())
assert old_id['sha256']==receipt['oldSHA256']
doc=json.loads(raw)
for c in receipt['semanticChanges']:
 node=doc; keys=c['path'].split('/')[1:]
 for key in keys[:-1]:node=node[key]
 assert node[keys[-1]]==c['old'];node[keys[-1]]=c['new']
reconstructed=json.dumps(doc,ensure_ascii=False,indent=2).encode()
assert hashlib.sha256(reconstructed).hexdigest()==receipt['newSHA256']==manifest['model/Shinaide_liquid.cfdrun']['sha256']
(O/'old495-cfdrun-exact-reconstructed.json').write_bytes(reconstructed)
new_cfdrun=D/'model/Shinaide_liquid.cfdrun'; new_id=ident(new_cfdrun)
binding_before=json.loads((P/'business-input-binding-before-ui.json').read_text())
binding_after=json.loads((P/'business-input-binding-after-ui.json').read_text())
assert new_id['sha256']==binding_after['model/Shinaide_liquid.cfdrun']['sha256']=='7232a458a0e2e13b8935e829daa34ec98afbb978d17c65bc4d6e04026f4cb04a'
changes=difference(doc,json.loads(new_cfdrun.read_bytes()))
allowed={'/runId','/sourceProject/exportedAt'}
assert {c['path'] for c in changes}<=allowed,changes
inputs=[]
for rel,item in binding_after.items():
 measured=ident(D/rel)
 assert measured['bytes']==item['bytes'] and measured['sha256']==item['sha256'],rel
 assert rel in binding_before
 inputs.append(dict(relativePath=rel,before=binding_before[rel],after=item,measured=measured,bytesIdentical=binding_before[rel]==item))
assert len(inputs)==15 and sum(not x['bytesIdentical'] for x in inputs)==1
write('cfdrun-and-business-input-delta.json',dict(
 old495Reconstruction=dict(source=old_id,operations=ident(E/'cfdrun-original-vs-fresh-react-export.json'),method='apply the eight recorded metadata replacements; preserve dict order and serialize indent=2/no final newline; SHA equals original archived 495 byte SHA',reconstructed=ident(O/'old495-cfdrun-exact-reconstructed.json')),
 current=new_id,semanticChanges=changes,onlyRunMetadataChanged=True,physicsAndMeshAndBoundaryAndGraphJSONExact=True,businessInputs=inputs,
 scope='The old 495 cfdrun is reconstructed to the exact recorded SHA, not treated as a newly extracted archive member. Actual disk new cfdrun and all 15 root before/after bindings are independently verified.'))
fields=['faces','owner','neighbour','boundary','cellZones','faceZones','pointZones']
rows=[]
for rank in range(8):
 for field in fields:
  rel=f'Mesh/processor{rank}/constant/polyMesh/{field}'
  actual=ident(D/rel); expected=manifest[rel]
  rows.append(dict(rank=rank,field=field,relativePath=rel,old495=expected,new=actual,byteIdentical=actual['bytes']==expected['bytes'] and actual['sha256']==expected['sha256']))
assert len(rows)==56 and all(x['byteIdentical'] for x in rows)
write('topology-identity-before-label-comparison.json',dict(
 recordedUtc=datetime.datetime.now(datetime.timezone.utc).isoformat(),old495Manifest=ident(E/'full-case-raw-manifest.json'),rows=rows,matched56of56=True,
 scope='Actual current ce178f4a disk topology compared first against frozen old 495 manifest; labels may be compared only after these 56 byte identities pass. Points are geometry, excluded from topology equality.',elapsedSeconds=time.monotonic()-started))
# Check availability of exact 495 warning-set bytes from the immutable 7785 case before any archive read.
available=[]
for rank in range(8):
 for kind in ['underdeterminedCells','lowWeightFaces','nonOrthoFaces','edgeFaces','multiRegionPoints','region0','region1']:
  rel=f'Mesh/processor{rank}/constant/polyMesh/sets/{kind}'
  if rel not in manifest:continue
  q=OLD/rel
  measured=ident(q)
  available.append(dict(rank=rank,set=kind,relativePath=rel,source=measured,old495=manifest[rel],matchesExact495=measured['sha256']==manifest[rel]['sha256'] and measured['bytes']==manifest[rel]['bytes']))
write('old495-set-byte-availability.json',dict(rows=available,allExact=all(x['matchesExact495'] for x in available)))
print(json.dumps(dict(topologyMatched=len(rows),topologyBytes=sum(x['new']['bytes'] for x in rows),changes=changes,businessInputs=len(inputs),setByteMismatch=[dict(rank=x['rank'],set=x['set']) for x in available if not x['matchesExact495']],elapsedSeconds=time.monotonic()-started),indent=2))
