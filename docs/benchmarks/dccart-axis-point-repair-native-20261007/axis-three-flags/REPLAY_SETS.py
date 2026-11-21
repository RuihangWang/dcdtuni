from pathlib import Path
import json,re,hashlib
F=Path(__file__).resolve().parent
sets={};core={};meshNames=['points','faces','owner','neighbour','boundary','cellZones','faceZones','pointZones']
def info(p):return {'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size}
for kind in ['BASE','AXIS']:
 M=F/'cases'/kind/'Mesh/constant/polyMesh';cout=(F/'runtime/logs'/f'{kind}.checkMesh.stdout').read_text();s=json.loads((F/'runtime/logs'/f'{kind}.checkMesh.status.json').read_text());assert s['returncode']==0 and not s['timedOut'] and re.search(r'^End\s*$',cout,re.M)
 actual={}
 for p in (M/'sets').iterdir():
  d=p.read_bytes();h=d.index(b'}')+1;cls=re.search(rb'class\s+(\w+)\s*;',d[:h]).group(1);assert cls in [b'cellSet',b'faceSet',b'pointSet'];m=re.search(rb'\n\s*(\d+)\s*\((.*?)\)',d[h:],re.S);assert m;v=list(map(int,m[2].split()));assert len(v)==int(m[1]) and len(set(v))==len(v) and all(x>=0 for x in v);actual[p.name]=sorted(v)
 sets[kind]={n:actual.get(n,[]) for n in ['underdeterminedCells','nonOrthoFaces','lowWeightFaces','lowVolRatioFaces','skewFaces']};core[kind]={n:info(M/n) for n in meshNames}
changes={n:{'removed':sorted(set(sets['BASE'][n])-set(sets['AXIS'][n])),'added':sorted(set(sets['AXIS'][n])-set(sets['BASE'][n]))} for n in sets['BASE']}
assert all(not c['added'] for c in changes.values())
assert all(core['BASE'][n]==core['AXIS'][n] for n in meshNames if n!='points')
expected=json.loads((F/'analysis/stock-set-replay.json').read_text());assert sets==expected['sets'] and changes==expected['changes']
print(json.dumps({'scope':'PURE OFFLINE no native/checker/source mutation','sets':sets,'changes':changes,'topologyAndZonesHeld':True,'remainingStrictRED':True,'nativeWrittenNotEquated':True},indent=2))
