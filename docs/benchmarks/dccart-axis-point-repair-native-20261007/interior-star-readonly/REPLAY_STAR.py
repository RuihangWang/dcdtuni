from pathlib import Path
import argparse,json,re,hashlib,numpy as np
p=argparse.ArgumentParser();p.add_argument('--mesh',required=True);p.add_argument('--point',required=True,type=int);p.add_argument('--out',required=True);a=p.parse_args();M=Path(a.mesh).resolve();O=Path(a.out).resolve();B=Path('/mnt/lv/home/litianyu/mycode/dcdtuni').resolve();assert M.is_relative_to(B) and O.is_relative_to(B)
bind={}
def raw(name,cls):
 d=(M/name).read_bytes();bind[name]={'bytes':len(d),'sha256':hashlib.sha256(d).hexdigest()};headEnd=d.index(b'}')+1;head=d[:headEnd];assert b'format      binary;' in head and b'LSB;label=32;scalar=64' in head and re.search(rb'class\s+'+cls+rb';',head);return d,headEnd
def array(d,start,typ,width):
 m=re.search(rb'\n(\d+)\s*\(',d[start:]);assert m;n=int(m[1]);begin=start+m.end();end=begin+n*width;assert d[end:end+1]==b')';return np.frombuffer(d[begin:end],dtype=typ).copy(),end+1
pd,h=raw('points',b'vectorField');flat,_=array(pd,h,'<f8',24);points=flat.reshape((-1,3));assert np.isfinite(points).all() and 0<=a.point<len(points)
fd,h=raw('faces',b'faceCompactList');offsets,end=array(fd,h,'<i4',4);labels,end=array(fd,end,'<i4',4);assert offsets[0]==0 and offsets[-1]==len(labels) and np.all(np.diff(offsets)>=3) and labels.min()>=0 and labels.max()<len(points);faces=[labels[offsets[i]:offsets[i+1]].tolist() for i in range(len(offsets)-1)]
od,h=raw('owner',b'labelList');owner,_=array(od,h,'<i4',4);nd,h=raw('neighbour',b'labelList');neigh,_=array(nd,h,'<i4',4);assert len(owner)==len(faces) and len(neigh)<=len(owner) and owner.min()>=0 and neigh.min()>=0;ncells=int(max(owner.max(),neigh.max())+1);cf=[[] for _ in range(ncells)]
for f,c in enumerate(owner):cf[int(c)].append(f)
for f,c in enumerate(neigh):cf[int(c)].append(f)
# Actual declared binary parser above is bound to ROOT's reviewed parser; arithmetic below is descriptive CPU geometry, not a new quality oracle.
def geometry(poly):
 pp=points[poly]
 if len(poly)==3:return .5*np.cross(pp[1]-pp[0],pp[2]-pp[0]),(pp[0]+pp[1]+pp[2])/3
 fc=pp.mean(axis=0);norm=np.cross(np.roll(pp,-1,axis=0)-pp,fc-pp);weights=np.linalg.norm(norm,axis=1);assert weights.sum()>0;return .5*norm.sum(axis=0),(1/3)*((weights[:,None]*(pp+np.roll(pp,-1,axis=0)+fc)).sum(axis=0))/weights.sum()
sf=np.empty((len(faces),3));fc=np.empty_like(sf)
for i,poly in enumerate(faces):sf[i],fc[i]=geometry(poly)
centres=[];volumes=[];Ds=[]
for c,rows in enumerate(cf):
 estimate=fc[rows].mean(axis=0);v3=0.;moment=np.zeros(3)
 for f in rows:
  ar=sf[f] if owner[f]==c else -sf[f];v=float(ar@(fc[f]-estimate));v3+=v;moment+=v*(.75*fc[f]+.25*estimate)
 assert np.isfinite(v3) and v3>0;centres.append(moment/v3);volumes.append(v3/3)
 selected=[f for f in rows if f<len(neigh)]
 if selected:
  av=float(np.linalg.norm(sf[selected],axis=1).sum()/len(selected));tensor=(sf[selected]/av).T@(sf[selected]/av);Ds.append(abs(float(np.linalg.det(tensor)))/8)
 else:Ds.append(None)
centres=np.asarray(centres);volumes=np.asarray(volumes)
pf=[f for f,poly in enumerate(faces) if a.point in poly];core=sorted({int(owner[f]) for f in pf}|{int(neigh[f]) for f in pf if f<len(neigh)});corefaces=sorted({f for c in core for f in cf[c]});outside=sorted(({int(owner[f]) for f in corefaces}|{int(neigh[f]) for f in corefaces if f<len(neigh)})-set(core))
boundaryTouch=[f for f in range(len(neigh),len(faces)) if a.point in faces[f]];boundaryAll=[{'face':f,'containsTarget':a.point in faces[f],'owner':int(owner[f]),'pointIDs':faces[f]} for f in range(len(neigh),len(faces))]
# Bounded actual ASCII patch list inside binary Foam header.
bd=(M/'boundary').read_bytes();bind['boundary']={'bytes':len(bd),'sha256':hashlib.sha256(bd).hexdigest()};bt=re.sub(r'/\*.*?\*/','',bd.decode(),flags=re.S);bt=re.sub(r'//[^\n]*','',bt);m=re.search(r'\n\s*(\d+)\s*\(\s*(.*?)\s*\)\s*$',bt,re.S);assert m;patches=[];pos=0
for q in re.finditer(r'\s*("[^"\n]+"|[^\s{}()]+)\s*\{([^{}]*)\}',m[2],re.S):
 assert not m[2][pos:q.start()].strip();pos=q.end();vals={}
 for k in ['startFace','nFaces']:
  z=re.findall(r'\b'+k+r'\s+(\d+)\s*;',q[2]);assert len(z)==1;vals[k]=int(z[0])
 typ=re.findall(r'\btype\s+([^;\s]+)\s*;',q[2]);assert len(typ)==1;patches.append({'name':q[1].strip('"'),'type':typ[0],**vals})
assert len(patches)==int(m[1]) and not m[2][pos:].strip();branges=[f for row in patches for f in range(row['startFace'],row['startFace']+row['nFaces'])];assert sorted(branges)==list(range(len(neigh),len(faces))) and len(set(branges))==len(branges)
sets={}
for q in (M/'sets').iterdir():
 d=q.read_bytes();bind['sets/'+q.name]={'bytes':len(d),'sha256':hashlib.sha256(d).hexdigest()};h=d.index(b'}')+1;m=re.search(rb'\n\s*(\d+)\s*\((.*?)\)',d[h:],re.S);assert m;v=sorted(map(int,m[2].split()));assert len(v)==int(m[1]);sets[q.name]=v
cells=[]
for c in core:
 rows=cf[c];pids=sorted({v for f in rows for v in faces[f]});edges=sorted({tuple(sorted((poly[i],poly[(i+1)%len(poly)]))) for f in rows for poly in [faces[f]] for i in range(len(poly))});closure=sum((sf[f] if owner[f]==c else -sf[f]) for f in rows);area=sum(float(np.linalg.norm(sf[f])) for f in rows)
 cells.append({'writtenCellID':c,'pointIDs':pids,'faceIDs':rows,'pointCount':len(pids),'faceCount':len(rows),'volume_m3_CPU':float(volumes[c]),'centre_m_CPU':centres[c].tolist(),'D_CPU_not_bitwise_stock':Ds[c],'stockDfailed':c in sets.get('underdeterminedCells',[]),'closureRelative_CPU':float(np.linalg.norm(closure)/area),'minEdge_m':min(float(np.linalg.norm(points[x]-points[y])) for x,y in edges),'maxEdge_m':max(float(np.linalg.norm(points[x]-points[y])) for x,y in edges)})
facerecs=[]
for f in corefaces:
 rec={'writtenFaceID':f,'owner':int(owner[f]),'neighbour':int(neigh[f]) if f<len(neigh) else None,'pointIDs':faces[f],'containsTarget':a.point in faces[f],'boundary':f>=len(neigh),'area_m2_CPU':float(np.linalg.norm(sf[f])),'centre_m_CPU':fc[f].tolist(),'areaVector_CPU':sf[f].tolist(),'stockFailedMembership':{k:f in ids for k,ids in sets.items() if k!='underdeterminedCells'}}
 if f<len(neigh):
  d=centres[neigh[f]]-centres[owner[f]];den=float(np.linalg.norm(sf[f])*np.linalg.norm(d));rec['N_degree_CPU']=float(np.degrees(np.arccos(np.clip(float(sf[f]@d)/den,-1,1)))) if den else None;x=abs(float(sf[f]@(fc[f]-centres[owner[f]])));y=abs(float(sf[f]@(centres[neigh[f]]-fc[f])));rec['W_CPU']=min(x,y)/(x+y) if x+y else None;rec['R_CPU']=float(min(volumes[owner[f]],volumes[neigh[f]])/max(volumes[owner[f]],volumes[neigh[f]]))
 facerecs.append(rec)
zonecounts={}
for name in ['cellZones','faceZones','pointZones']:
 d=(M/name).read_bytes();bind[name]={'bytes':len(d),'sha256':hashlib.sha256(d).hexdigest()};h=d.index(b'}')+1;m=re.search(rb'\n\s*(\d+)\s*\(',d[h:]);assert m;zonecounts[name]=int(m[1])
result={'scope':'PURE OFFLINE final-written generic point-star/boundary census and descriptive geometry, not native selector or mesh repair','inputBindings':bind,'targetWrittenPointID':a.point,'target_m':points[a.point].tolist(),'counts':{'points':len(points),'faces':len(faces),'internalFaces':len(neigh),'boundaryFaces':len(boundaryAll),'cells':ncells},'pointFaces':pf,'pointStarCells':core,'pointStarOutsideHalo':outside,'coreAllFaces':corefaces,'finalBoundaryTouchingTarget':boundaryTouch,'finalTopologyInterior':not boundaryTouch,'allBoundaryCensus':boundaryAll,'actualPatches':patches,'allPatchRangesExactlyCoverFinalBoundary':True,'zoneCounts':zonecounts,'allIncidentFacesTriangle':all(len(faces[f])==3 for f in pf),'stockMembership':{k:sorted(set(v)&set(core if k=='underdeterminedCells' else corefaces)) for k,v in sets.items()},'cells':cells,'coreFaces':facerecs,'sourcePointUIDParentOrNativeBinding':'unresolved: final topology observation does not identify creator/native/fan provenance','arithmeticLimits':'CPU NumPy follows source face/V/M1/D formulas descriptively; native stock raw sets remain authority; no bitwise installed field replay or optimizer result'}
O.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({'writtenPoint':a.point,'interior':result['finalTopologyInterior'],'boundaryCount':len(boundaryAll),'boundaryTouches':boundaryTouch,'starCells':len(core),'pointFaces':len(pf),'allIncidentTriangle':result['allIncidentFacesTriangle'],'zones':zonecounts,'stockMembership':result['stockMembership'],'D_CPU_range':[min(Ds[c] for c in core),max(Ds[c] for c in core)],'starVolumesRange':[min(volumes[c] for c in core),max(volumes[c] for c in core)]},indent=2))
