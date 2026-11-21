from pathlib import Path
import sys, importlib.util, json, hashlib, re, math
from collections import Counter
import numpy as np
A=Path(__file__).resolve().parents[2]
E=Path(__file__).resolve().parent
sys.path[:0]=[str(A/'src'),str(A/'tools'),str(E/'readers')]
import air_cone_trace_probe as T
def load(name,path):
 spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
G=load('remaining_geometry',E/'readers/audit_air_mesh_geometry.py')
Q=load('remaining_quality',E/'readers/audit_air_cone_remaining_quality.py')
M=Path('/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/integration/.codex-tmp/cases/Shinaide_MeshAbort_CUA/Mesh')
def ids(path):
 raw=path.read_bytes();hit=re.search(rb'\n\s*(\d+)\s*\(\s*([0-9\s]*)\)',raw)
 if not hit:raise ValueError(path)
 values=np.fromstring(hit[2].decode(),sep=' ',dtype=np.int64)
 if len(values)!=int(hit[1]):raise ValueError(path)
 return values
def nonorth(sf,cc,nc):
 d=nc-cc
 return float(np.degrees(np.arccos(np.clip(np.dot(d,sf)/(np.linalg.norm(d)*np.linalg.norm(sf)+1e-150),-1,1))))
out={'format':'7785-exact-written-remaining-classification/v1','mesh':str(M),'scriptSHA256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'readers':json.loads((E/'reader-identity.json').read_text()),'scope':'Read-only actual 7785 written labels, ancestry/base trace and stock formulas; classifications do not authorize a native mesh. No coordinates/connectivity/case files are changed.','ranks':[]}
for rank in range(8):
 t=T.read_trace(M,rank);g=G.Geometry(M/f'processor{rank}/constant/polyMesh')
 sets={k:ids(g.poly_dir/'sets'/name) for k,name in [('D','underdeterminedCells'),('W','lowWeightFaces'),('R','lowVolRatioFaces'),('N','nonOrthoFaces')]}
 def cell_summary(c):
  fs,sf,fc,cc,v,vs,lo,hi=g.cell(int(c));bases=sorted(set(int(t['faceBase'][f]) for f in fs if t['faceBase'][f]>=0))
  arity=sorted(len(g.ids(int(f))) for f in fs)
  shape='tetrahedron' if len(fs)==4 and len(vs)==4 and arity==[3]*4 else 'quad-pyramid' if len(fs)==5 and len(vs)==5 and arity==[3,3,3,3,4] else 'other'
  selected=[a for f,a in zip(fs,sf) if f<len(g.neighbour) or (g.patch(int(f)) or {}).get('type') in ['cyclic','processor','processorCyclic']]
  dv=Q.determinant(selected)[0]
  return {'cell':int(c),'nativeCell':int(t['cellNative'][c]),'parent':int(t['cellParent'][c]),'shape':shape,'faces':list(map(int,fs)),'bases':bases,'sourceBaseArities':[int(t['offsets'][b+1]-t['offsets'][b]) for b in bases],'selectedFaceCount':len(selected),'determinant':dv,'volume':v,'CC':cc.tolist(),'bbox':[lo.tolist(),hi.tolist()]}
 counts=Counter();rows=[];parent_counts=np.bincount(t['cellParent'])
 for c in sets['D']:
  row=cell_summary(int(c));row['parentWrittenChildren']=int(parent_counts[row['parent']])
  if len(row['bases'])==1:
   bf=next(int(f) for f in row['faces'] if int(t['faceBase'][f])==row['bases'][0])
   row['baseWrittenFace']=bf;row['basePatch']=g.patch(bf);row['baseVertexIDs']=g.ids(bf).tolist()
   fs,*_,vs,_lo,_hi=g.cell(int(c))
   ap=set(map(int,vs))-set(row['baseVertexIDs'])
   if len(ap)==1:
    ap=next(iter(ap));row['apexWrittenPoint']=ap;row['apexNativePoint']=int(t['pointNative'][ap]);row['apex']=g.points[ap].tolist()
  counts[(row['shape'],row['selectedFaceCount'],tuple(row['sourceBaseArities']))]+=1
  rows.append(row)
 # Source-face CSR once, avoids scanning the full source mesh per parent.
 alive=np.flatnonzero(t['faceAlive']!=0)
 ol=np.asarray(t['owner'][alive],dtype=np.int64);nl=np.asarray(t['neighbour'][alive],dtype=np.int64)
 labels=np.r_[ol,nl[nl>=0]];sfids=np.r_[alive,alive[nl>=0]]
 order=np.argsort(labels,kind='stable');labels=labels[order];sfids=sfids[order]
 source_kind={}
 for start,count,name,kind in g.patches:
  bases=np.asarray(t['faceBase'][start:start+count]);bases=bases[bases>=0]
  for pid in np.unique(t['patch'][bases]):
   key=int(pid);coupled=kind in ['cyclic','processor','processorCyclic']
   if key in source_kind and source_kind[key]!=coupled:raise ValueError('source patch role alias')
   source_kind[key]=coupled
 parents=[]
 for parent in sorted(set(r['parent'] for r in rows)):
  fs=sfids[np.searchsorted(labels,parent,'left'):np.searchsorted(labels,parent,'right')]
  ps=[]
  for f in fs:
   q=t['vertices'][t['offsets'][f]:t['offsets'][f+1]].tolist()
   ps.append(q if t['owner'][f]==parent else q[::-1])
  sg=G.Geometry.from_arrays(t['points'],ps)
  _,ss,sc,scc,sv,svid,slo,shi=sg.cell(0)
  span=shi-slo;aspect=float(span.max()/span.min())
  axis=[]
  for a,fc,f in zip(ss,sc,fs):
   d=int(np.argmax(np.abs(a)));mag=np.linalg.norm(a);pts=sg.points[sg.ids(len(axis))]
   axis.append(bool(all(abs(a[j])<=1e-12*mag for j in range(3) if j!=d) and np.max(abs(pts[:,d]-fc[d]))<=1e-10*max(np.linalg.norm(pts-fc,axis=1).max(),1e-6)))
  bodybox=all(axis) and abs(sv-float(np.prod(span)))<=1e-10*sv
  selected=[a for f,a in zip(fs,ss) if t['patch'][f]<0 or source_kind.get(int(t['patch'][f]),False)]
  parents.append({'parent':parent,'badDchildren':sum(r['parent']==parent for r in rows),'writtenChildren':int(parent_counts[parent]),'sourceFaceCount':len(fs),'sourceVertexCount':len(svid),'sourceVolume':sv,'sourceBBox':[slo.tolist(),shi.tolist()],'sourceAspect':aspect,'sourceAxisBoxWithinDeclaredAuditTolerance':bodybox,'sourceSelectedDeterminant':Q.determinant(selected)[0]})
 faces=[]
 for f in sorted(set(map(int,np.r_[sets['W'],sets['R'],sets['N']]))):
  own=int(g.owner[f]);nei=int(g.neighbour[f]) if f<len(g.neighbour) else None
  oc=cell_summary(own);nc=cell_summary(nei) if nei is not None else None
  sf,fc,*_=g.face(f)
  metrics=Q.face_metrics(sf,fc,np.asarray(oc['CC']),oc['volume'],g.points[g.ids(f)],np.asarray(nc['CC']) if nc else None,nc['volume'] if nc else None)
  if nc:metrics['nonorthogonalityDegrees']=nonorth(sf,np.asarray(oc['CC']),np.asarray(nc['CC']))
  role='boundary' if nc is None else 'cross-parent' if oc['parent']!=nc['parent'] else 'same-parent/same-base' if oc['bases']==nc['bases'] and len(oc['bases'])==1 else 'same-parent/different-base'
  faces.append({'face':f,'nativeFace':int(t['faceNative'][f]),'sourceBase':int(t['faceBase'][f]),'patch':g.patch(f),'roles':role,'failedFlags':[k for k in ['W','R','N'] if f in sets[k]],'owner':oc,'neighbour':nc,'metrics':metrics,'vertexIDs':g.ids(f).tolist(),'points':g.points[g.ids(f)].tolist()})
 rankout={'rank':rank,'declaredSetCounts':{k:len(v) for k,v in sets.items()},'DshapeAndSelectedMask':[{'shape':k[0],'selected':k[1],'sourceBaseArities':list(k[2]),'count':v} for k,v in sorted(counts.items())],'Dcells':rows,'Dparents':parents,'failedFaces':faces}
 out['ranks'].append(rankout)
 (E/'classification-partial.json').write_text(json.dumps(out,indent=2))
 print(json.dumps({'rank':rank,'sets':rankout['declaredSetCounts'],'Dshapes':rankout['DshapeAndSelectedMask'],'parents':len(parents),'sourceBoxes':sum(p['sourceAxisBoxWithinDeclaredAuditTolerance'] for p in parents)}),flush=True)
out['totals']={k:sum(r['declaredSetCounts'][k] for r in out['ranks']) for k in ['D','W','R','N']}
out['faceRoleTotals']={flag:dict(Counter(f['roles'] for r in out['ranks'] for f in r['failedFaces'] if flag in f['failedFlags'])) for flag in ['W','R','N']}
out['DshapeTotals']=dict(Counter(c['shape'] for r in out['ranks'] for c in r['Dcells']))
out['DparentGeometryTotals']=dict(Counter('thin-axis-box' if p['sourceAxisBoxWithinDeclaredAuditTolerance'] and p['sourceAspect']>4 else 'axis-box-aspect<=4' if p['sourceAxisBoxWithinDeclaredAuditTolerance'] else 'other' for r in out['ranks'] for p in r['Dparents']))
out['DchildSourceGeometryTotals']=dict(Counter('thin-axis-box' if next(p for p in r['Dparents'] if p['parent']==c['parent'])['sourceAxisBoxWithinDeclaredAuditTolerance'] and next(p for p in r['Dparents'] if p['parent']==c['parent'])['sourceAspect']>4 else 'axis-box-aspect<=4' if next(p for p in r['Dparents'] if p['parent']==c['parent'])['sourceAxisBoxWithinDeclaredAuditTolerance'] else 'other' for r in out['ranks'] for c in r['Dcells']))
(E/'classification.json').write_text(json.dumps(out,indent=2))
print(json.dumps({k:out[k] for k in ['totals','faceRoleTotals','DshapeTotals','DparentGeometryTotals','DchildSourceGeometryTotals']}),flush=True)
