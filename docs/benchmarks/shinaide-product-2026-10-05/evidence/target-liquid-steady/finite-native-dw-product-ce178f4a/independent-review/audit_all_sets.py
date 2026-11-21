import pathlib,json,hashlib,re,datetime
B=pathlib.Path('/mnt/lv/home/litianyu/mycode/dcdtuni');T=B/'.codex-tmp/goal-20261005/integration'
P=T/'.codex-tmp/finite-native-dw-product-20261005';O=P/'independent-review'
E=T/'docs/benchmarks/shinaide-product-2026-10-05/evidence/target-liquid-steady/finite-native-product-495546f2'
D=T/'.codex-tmp/cases/Shinaide_PointRepairR_CUA'; OLD=T/'.codex-tmp/cases/Shinaide_MeshAbort_CUA'
def digest(b):return hashlib.sha256(b).hexdigest()
def write(n,d):(O/n).write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
def parse(b,include=True):
 m=re.search(rb'\n([0-9]+)\s*\(\s*',b);assert m,'labelSet count not found'
 if int(m[1])==0:return [] if include else 0
 end=b.index(b')',m.end());vals=b[m.end():end].split()
 assert len(vals)==int(m[1]),dict(count=int(m[1]),parsed=len(vals))
 if not include:return int(m[1])
 numbers=list(map(int,vals));assert len(set(numbers))==len(numbers)
 return numbers
topo=json.loads((O/'topology-identity-before-label-comparison.json').read_text());assert topo['matched56of56'] and len(topo['rows'])==56
manifest=json.loads((E/'full-case-raw-manifest.json').read_text())
rel='Mesh/processor1/constant/polyMesh/sets/nonOrthoFaces'
old=(OLD/rel).read_bytes();m=re.search(rb'\n([0-9]+)\n\(\n',old);end=old.index(b'\n)',m.end())
lines=old[m.end():end].split(b'\n')
prefix=old[:m.start()]+b'\n254\n(\n';suffix=old[end:]
matches=[]
for i,line in enumerate(lines):
 b=prefix+b'\n'.join(lines[:i]+lines[i+1:])+suffix
 if digest(b)==manifest[rel]['sha256']:matches.append((int(line),b))
assert len(matches)==1,'Could not reconstruct exact old495 warning-set bytes by one deletion'
removed_from7785,b=matches[0];assert len(b)==manifest[rel]['bytes']
(O/'old495-rank1-nonOrthoFaces-exact-reconstructed').write_bytes(b)
write('old495-warning-reconstruction.json',dict(method='Immutable 7785 rank1 warning set has 255 labels. Enumerate each single-label deletion with count 254; exactly one candidate matches old495 full manifest bytes and SHA. This recreates exact old495 bytes, not a broad arbitrary label substitution.',sourcePath=str(OLD/rel),sourceSHA256=digest(old),removed7785Label=removed_from7785,reconstructedSHA256=digest(b),reconstructedBytes=len(b),old495ManifestExpected=manifest[rel]))
rows=[]
oldsetrels=sorted(k for k in manifest if '/constant/polyMesh/sets/' in k)
newsetrels=sorted(str(f.relative_to(D)) for r in range(8) for f in (D/f'Mesh/processor{r}/constant/polyMesh/sets').iterdir() if f.is_file())
assert oldsetrels==newsetrels,dict(missing=sorted(set(oldsetrels)-set(newsetrels)),new=sorted(set(newsetrels)-set(oldsetrels)))
for rel in oldsetrels:
 old=(O/'old495-rank1-nonOrthoFaces-exact-reconstructed').read_bytes() if rel.endswith('processor1/constant/polyMesh/sets/nonOrthoFaces') else (OLD/rel).read_bytes()
 assert digest(old)==manifest[rel]['sha256'] and len(old)==manifest[rel]['bytes'],rel
 new=(D/rel).read_bytes();parts=rel.split('/');rank=int(parts[1][9:]);kind=parts[-1]
 row=dict(rank=rank,set=kind,relativePath=rel,old495SHA256=digest(old),newSHA256=digest(new),byteIdentical=old==new,oldCount=parse(old,False),newCount=parse(new,False))
 if old!=new or kind in ['underdeterminedCells','lowWeightFaces','nonOrthoFaces']:
  a=set(parse(old));c=set(parse(new))
  row.update(removedLabels=sorted(a-c),newLabels=sorted(c-a),labelSetIdentical=a==c)
  if kind in ['underdeterminedCells','lowWeightFaces','nonOrthoFaces']:row.update(oldLabels=sorted(a),newFinalLabels=sorted(c))
 else:row.update(removedLabels=[],newLabels=[],labelSetIdentical=True)
 rows.append(row)
summary={}
for kind in sorted({x['set'] for x in rows}):
 xs=[x for x in rows if x['set']==kind]
 summary[kind]=dict(oldCount=sum(x['oldCount'] for x in xs),newCount=sum(x['newCount'] for x in xs),removed=[dict(rank=x['rank'],label=v) for x in xs for v in x['removedLabels']],new=[dict(rank=x['rank'],label=v) for x in xs for v in x['newLabels']],allLabelSetsIdentical=all(x['labelSetIdentical'] for x in xs))
assert summary['underdeterminedCells']['oldCount']==909 and summary['underdeterminedCells']['newCount']==907
assert len(summary['underdeterminedCells']['removed'])==2 and not summary['underdeterminedCells']['new']
assert summary['lowWeightFaces']['oldCount']==summary['lowWeightFaces']['newCount']==100 and summary['lowWeightFaces']['allLabelSetsIdentical']
log=(P/'job-log-stream.txt').read_text()
oldlog=(E/'full-job-log-stream.txt').read_text()
key_words=['volume ratio','volumeRatio','determinant','interpolation weight','Failed ','severely non-orthogonal','non-orthogonality','skewness','literal two-cell','finite point repair','finite point proposal','Finite point']
def relevant(text):return [dict(line=i+1,text=s[:1800]) for i,s in enumerate(text.splitlines()) if any(k in s for k in key_words)]
status=json.loads((P/'job-terminal-status.json').read_text())
write('all-written-set-label-delta.json',dict(guard=dict(topologyIdentityFile='topology-identity-before-label-comparison.json',matched56of56=True),old495SetsCount=len(oldsetrels),newSetsCount=len(newsetrels),rows=rows,summary=summary,ratioScope='Neither old495 nor ce178f4a has a low-volume-ratio failure set. R0 is independently supported by actual stock log passing volume-ratio check and terminal Failed2 D/W. Set absence alone is not treated as proof.',skewScope='No skew failure set in either mesh; actual stock logs report S0.',scope='All 56 actual stored sets compared against exact old495 bytes, including edge/multi-region/region labels and N warning labels. D/W failure labels compared only after all 56 topology fields match.'))
write('actual-log-and-terminal-binding.json',dict(status=status,newLog=dict(path=str(P/'job-log-stream.txt'),bytes=(P/'job-log-stream.txt').stat().st_size,sha256=digest((P/'job-log-stream.txt').read_bytes()),lines=relevant(log)),old495Log=dict(path=str(E/'full-job-log-stream.txt'),bytes=(E/'full-job-log-stream.txt').stat().st_size,sha256=digest((E/'full-job-log-stream.txt').read_bytes()),lines=relevant(oldlog)),scope='Readonly extraction from real UI raw logs; count/terminal evidence, not a new checker run.'))
print(json.dumps(summary,indent=2))
print('log keylines',json.dumps(relevant(log),indent=2)[:6500])
