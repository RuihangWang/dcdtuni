from pathlib import Path
import argparse,hashlib,json,re,struct,numpy as np
from scipy.spatial import cKDTree
p=argparse.ArgumentParser();p.add_argument('--mesh',required=True);p.add_argument('--out',required=True);a=p.parse_args();M=Path(a.mesh).resolve();O=Path(a.out).resolve()
B=Path('/mnt/lv/home/litianyu/mycode/dcdtuni');assert M.is_relative_to(B) and O.is_relative_to(B)
bind={}
def raw(name,cls):
 d=(M/name).read_bytes();bind[name]={'bytes':len(d),'sha256':hashlib.sha256(d).hexdigest()};headEnd=d.index(b'}')+1;head=d[:headEnd]
 assert b'format      binary;' in head and b'LSB;label=32;scalar=64' in head and re.search(rb'class\s+'+cls+rb';',head)
 return d,headEnd
def array(d,start,typ,width):
 m=re.search(rb'\n(\d+)\s*\(',d[start:]);assert m
 n=int(m[1]);begin=start+m.end();end=begin+n*width;assert d[end:end+1]==b')'
 values=np.frombuffer(d[begin:end],dtype=typ).copy();return values,end+1
pd,h=raw('points',b'vectorField');flat,_=array(pd,h,'<f8',8*3);points=flat.reshape((-1,3));assert np.isfinite(points).all()
fd,h=raw('faces',b'faceCompactList');offsets,end=array(fd,h,'<i4',4);labels,end=array(fd,end,'<i4',4);assert offsets[0]==0 and offsets[-1]==len(labels) and np.all(np.diff(offsets)>=3) and labels.min()>=0 and labels.max()<len(points)
faces=[labels[offsets[i]:offsets[i+1]].tolist() for i in range(len(offsets)-1)]
od,h=raw('owner',b'labelList');owner,_=array(od,h,'<i4',4);nd,h=raw('neighbour',b'labelList');neigh,_=array(nd,h,'<i4',4);assert len(owner)==len(faces) and len(neigh)<=len(owner) and owner.min()>=0 and neigh.min()>=0
ncells=int(max(owner.max(),neigh.max())+1);cellFaces=[[] for _ in range(ncells)]
for i,c in enumerate(owner):cellFaces[int(c)].append(i)
for i,c in enumerate(neigh):cellFaces[int(c)].append(i)
sd,h=raw('sets/underdeterminedCells',b'cellSet');m=re.search(rb'\n(\d+)\s*\((.*?)\)',sd[h:],re.S);assert m;bad=sorted(map(int,m[2].split()));assert len(bad)==int(m[1]) and len(set(bad))==len(bad) and all(0<=x<ncells for x in bad)
near=sorted(cKDTree(points).query_pairs(1e-7));cells=[]
for c in bad:
 fids=cellFaces[c];pids=sorted(set(v for f in fids for v in faces[f]));edges=sorted(set(tuple(sorted((face[i],face[(i+1)%len(face)]))) for f in fids for face in [faces[f]] for i in range(len(face))));pp=points[pids];lens=[float(np.linalg.norm(points[u]-points[v])) for u,v in edges]
 cells.append({'writtenCellID':c,'faces':fids,'pointIDs':pids,'points_m':pp.tolist(),'bbox_m':{'lo':pp.min(axis=0).tolist(),'hi':pp.max(axis=0).tolist()},'faceCount':len(fids),'pointCount':len(pids),'edgeCount':len(edges),'minEdge_m':min(lens),'maxEdge_m':max(lens)})
result={'scope':'Pure offline written-mesh geometry diagnosis; no native/checker invocation, repair, source change or acceptance. Written IDs are not captured native/pre-decomposition IDs.','inputBindings':bind,'counts':{'points':len(points),'faces':len(faces),'internalFaces':len(neigh),'cells':ncells,'stockUnderdeterminedCells':len(bad)},'nearPairDistance_m':1e-7,'nearPairs':near,'cells':cells,'interpretation':'Zero near pairs at this distance does not prove overall mesh quality. Eight stock-bad five-face/five-point pyramids have a near-corner internal apex and a long base. Cone-apex placement is a hypothesis for investigation, not a proven cause. Physical coordinates/IDs must not become case-specific repair selectors.'}
O.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({'counts':result['counts'],'nearPairs':len(near),'fiveFaceFivePointBadCells':[x['writtenCellID'] for x in cells if x['faceCount']==5 and x['pointCount']==5]},indent=2))
