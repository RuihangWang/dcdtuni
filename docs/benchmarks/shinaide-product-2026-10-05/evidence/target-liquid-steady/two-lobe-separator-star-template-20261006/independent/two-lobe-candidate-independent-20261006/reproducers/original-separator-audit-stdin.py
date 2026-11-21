import json,math,hashlib,collections
from pathlib import Path
from fractions import Fraction as F
root=Path('/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/air/.codex-tmp');base=root/'complete-two-point-star-template-20261006'
contents={n:(base/n).read_bytes() for n in ['complete-star-input.json','star-topology-report.json','next-seam-subcavity-observation.json']};a=json.loads(contents['complete-star-input.json']);t=json.loads(contents['star-topology-report.json']);o=json.loads(contents['next-seam-subcavity-observation.json'])
P={int(p):tuple(F.from_float(x) for x in xyz) for p,xyz in a['points'].items()};core=set(a['children']);plane=F.from_float(o['plane']);axis=o['axis'];outside=t['vertexStarExteriorFaces'];sepL=[26616,26656,61112,61101];sepR=[26616,61101,61112,26656]
sub=lambda u,v:tuple(x-y for x,y in zip(u,v));dot=lambda u,v:sum((x*y for x,y in zip(u,v)),F(0));cross=lambda u,v:(u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0]);rat=lambda x:dict(numerator=str(x.numerator),denominator=str(x.denominator),approx=float(x))
refs=collections.defaultdict(list)
for c in core:
 for f in a['cells'][str(c)]['faces']:refs[f].append(c)
actualOuter=sorted(f for f,cs in refs.items() if len(cs)==1);actualInner=sorted(f for f,cs in refs.items() if len(cs)==2)
classification={k:[] for k in ['left','right','on','cross']}
for fid in actualOuter:
 xs=[P[p][axis] for p in a['faces'][str(fid)]['pointIDs']];lo=min(xs);hi=max(xs)
 key='on' if lo==hi==plane else 'left' if hi<=plane else 'right' if lo>=plane else 'cross';classification[key].append(fid)
def outer_ring(fid):
 f=a['faces'][str(fid)];ring=list(f['pointIDs'])
 if f['owner'] not in core:ring=[ring[0]]+list(reversed(ring[1:]))
 return ring
leftIds=classification['left']+classification['on'];rightIds=classification['right'];left=[(f,outer_ring(f)) for f in leftIds];right=[(f,outer_ring(f)) for f in rightIds];globalRings=[(f,outer_ring(f)) for f in actualOuter]
def edges_of(rings):
 e=collections.defaultdict(lambda:[0,0]);verts=set();faceAdj=collections.defaultdict(set);users=collections.defaultdict(list);links=collections.defaultdict(lambda:collections.defaultdict(set))
 for fid,ring in rings:
  verts.update(ring)
  for i,(u,v) in enumerate(zip(ring,ring[1:]+ring[:1])):
   key=tuple(sorted([u,v]));e[key][0]+=1;e[key][1]+=1 if u<v else -1;users[key].append(fid);links[u][ring[i-1]].add(v);links[u][v].add(ring[i-1])
 for fs in users.values():
  for u in fs:
   faceAdj[u].update(v for v in fs if v!=u)
 def connected(g):
  if not g:return True
  todo=[next(iter(g))];seen=set()
  while todo:
   u=todo.pop()
   if u in seen:continue
   seen.add(u);todo.extend(g.get(u,set())-seen)
  return len(seen)==len(g)
 return dict(edgeData=e,vertices=verts,faceConnected=connected(faceAdj),vertexLinksSingleCycles=all(all(len(ns)==2 for ns in link.values()) and connected(link) for link in links.values()))
def closure(rings):
 g=edges_of(rings);bad=[dict(edge=list(e),uses=nb,balance=bal) for e,(nb,bal) in g['edgeData'].items() if nb!=2 or bal]
 return dict(faces=len(rings),vertices=len(g['vertices']),edges=len(g['edgeData']),Euler=len(g['vertices'])-len(g['edgeData'])+len(rings),directedClosed=not bad,faceConnected=g['faceConnected'],vertexLinksSingleCycles=g['vertexLinksSingleCycles'],edgeIssues=bad)
def integral(rings):
 V=F(0);M=[F(0)]*3
 for fid,ring in rings:
  x=P[ring[0]]
  for j in range(1,len(ring)-1):
   y=P[ring[j]];z=P[ring[j+1]];v=dot(x,cross(y,z))/6;V+=v
   for k in range(3):M[k]+=v*(x[k]+y[k]+z[k])/4
 return V,tuple(M)
leftclosed=left+[('separator',sepL)];rightclosed=right+[('separator',sepR)];L=integral(leftclosed);R=integral(rightclosed);G=integral(globalRings);cells=[]
for c in sorted(core):
 rings=[]
 for f in a['cells'][str(c)]['faces']:
  row=a['faces'][str(f)];ring=list(row['pointIDs'])
  if row['owner']!=c:ring=[ring[0]]+list(reversed(ring[1:]))
  rings.append((f,ring))
 cells.append(integral(rings))
CV=sum((v for v,m in cells),F(0));CM=tuple(sum((m[k] for v,m in cells),F(0)) for k in range(3));openL=edges_of(left)['edgeData'];openR=edges_of(right)['edgeData'];boundaryL={e:bal for e,(n,bal) in openL.items() if bal};boundaryR={e:bal for e,(n,bal) in openR.items() if bal}
separatorPoints=[P[p] for p in sepL];n=cross(sub(separatorPoints[1],separatorPoints[0]),sub(separatorPoints[2],separatorPoints[0]));nonplanar=[p for p in sepL if dot(n,sub(P[p],separatorPoints[0]))]
ys=sorted(set(P[p][1] for p in sepL));zs=sorted(set(P[p][2] for p in sepL));rectangle=axis==0 and len(ys)==len(zs)==2 and all(P[p][0]==plane for p in sepL) and {(P[p][1],P[p][2]) for p in sepL}=={(y,z) for y in ys for z in zs}
originalExactFaces=[int(f) for f,row in a['faces'].items() if len(row['pointIDs'])==4 and set(row['pointIDs'])==set(sepL)]
planeHits=[];interiorHits=[]
for fid in actualOuter:
 ring=a['faces'][str(fid)]['pointIDs'];on=[p for p in ring if P[p][axis]==plane]
 if not on:continue
 entry=dict(face=fid,onPlaneNodes=on,coplanarFace=len(on)==len(ring));planeHits.append(entry)
 for p in on:
  if ys[0]<P[p][1]<ys[1] and zs[0]<P[p][2]<zs[1]:interiorHits.append(dict(face=fid,node=p))
 for u,v in zip(ring,ring[1:]+ring[:1]):
  if P[u][axis]==P[v][axis]==plane:
   y=(P[u][1]+P[v][1])/2;z=(P[u][2]+P[v][2])/2
   if ys[0]<y<ys[1] and zs[0]<z<zs[1]:interiorHits.append(dict(face=fid,edge=[u,v],kind='edge-interior-hit'))
 if len(on)==len(ring):
  y=sum((P[p][1] for p in ring),F(0))/len(ring);z=sum((P[p][2] for p in ring),F(0))/len(ring)
  if ys[0]<y<ys[1] and zs[0]<z<zs[1]:interiorHits.append(dict(face=fid,kind='coplanar-face-interior-hit'))
roles=[]
for fid in actualOuter:
 row=a['faces'][str(fid)];endpoints=[c for c in [row['owner'],row['neighbour']] if c is not None];other=[c for c in endpoints if c not in core]
 roles.append(dict(face=fid,lobe='left' if fid in leftIds else 'right',orderedPointIDs=row['pointIDs'],owner=row['owner'],neighbour=row['neighbour'],actualOutsideCellIDs=other,patch=row['patch'],sourceBase=row['sourceBase'],sourceZone=row['sourceZone'],nativeFace=row['nativeFace']))
physical=[r for r in roles if r['neighbour'] is None];coupled=[r for r in physical if any(x in r['patch']['type'].lower() for x in ['cyclic','processor','ami','mapped','coupled'])]
checks=dict(actualOuterMatchesReport=actualOuter==outside,coreCount32=len(core)==32,classificationMatchesObservation=classification==o['classification'],noOuterCrossing=not classification['cross'],original38PartitionOnce=len(leftIds)+len(rightIds)==38 and set(leftIds).isdisjoint(rightIds),onFacesBothAssignedLeft=classification['on']==[140941,140966],separatorExactRectangle=rectangle,separatorExactPlanar=not nonplanar,leftSeparatorNormalPositiveX=n[0]>0 and n[1]==n[2]==0,rightSeparatorReverse=sepR==[sepL[0]]+list(reversed(sepL[1:])),openEdgeChainsReverse=boundaryL=={k:-v for k,v in boundaryR.items()},leftClosure=closure(leftclosed)['directedClosed'],rightClosure=closure(rightclosed)['directedClosed'],leftBoundaryWithinLeftHalfspace=all(P[p][axis]<=plane for _,ring in leftclosed for p in ring),rightBoundaryWithinRightHalfspace=all(P[p][axis]>=plane for _,ring in rightclosed for p in ring),separatorInteriorNoOriginalOuterHit=not interiorHits,exactVolumeSum=L[0]+R[0]==G[0]==CV,exactFirstMomentSum=tuple(L[1][k]+R[1][k] for k in range(3))==G[1]==CM,observationFractionVolumesMatch=all(q[0]==F(int(o['subshellVolumes'][name]['numerator']),int(o['subshellVolumes'][name]['denominator'])) for name,q in [('left',L),('right',R)]),allEightPhysicalSourceZone3Literal=len(physical)==8 and all(r['sourceZone']==3 and r['patch']==dict(name='merged_wall_baffle_master',type='wall') for r in physical),noCoupled=not coupled)
report=dict(scope='Independent static separator, two closed subshells, exact V/M1 and literal role audit only. No AP, candidate rebuild, optimization, native/API/source/index operation.',inputPaths={n:str(base/n) for n in contents},inputSha256={n:hashlib.sha256(v).hexdigest() for n,v in contents.items()},checks=checks,classification=classification,separator=dict(axis=axis,plane=rat(plane),originalOrderedRingLeft=sepL,originalOrderedRingRight=sepR,exactExistingNodeCoordinates={str(p):a['points'][str(p)] for p in sepL},rectangleArea=rat((ys[1]-ys[0])*(zs[1]-zs[0])),normalLeft=[rat(x) for x in n],existingStoredFacesWithExactlyThisFourNodeRing=originalExactFaces,nativeFaceClaim='Existing support plane and four original coordinates are verified. No pre-existing native separator-face identity is asserted when this list is empty.',newInternalRole='The shared closure is internal with opposite outward rings; no physical/source/material role is invented.'),subshells=dict(left=dict(originalOuterFaces=leftIds,closure=closure(leftclosed),FractionVolume=rat(L[0]),FractionFirstMoment=[rat(x) for x in L[1]]),right=dict(originalOuterFaces=rightIds,closure=closure(rightclosed),FractionVolume=rat(R[0]),FractionFirstMoment=[rat(x) for x in R[1]])),globalExact=dict(volume=rat(G[0]),firstMoment=[rat(x) for x in G[1]],matches32CellSignedIntegral=True),directedOpenBoundaryLeft=[dict(edge=list(k),balance=v) for k,v in sorted(boundaryL.items())],directedOpenBoundaryRight=[dict(edge=list(k),balance=v) for k,v in sorted(boundaryR.items())],originalOuterPlaneHits=planeHits,separatorInteriorOuterHits=interiorHits,all38LiteralExteriorRoles=roles,physicalSourceZone3Faces=physical,coverageBeforeAP=['The left and right oriented surface chains are individually closed and their sum is exactly the original 38-face outer chain because the shared quad cancels.','Both subshells are connected orientable manifold surface chains with spherical Euler characteristic if their vertex-link/closure checks hold; this audit does not independently certify full global surface self-intersection absence.','All left boundary facets lie in x<=plane and all right facets in x>=plane. Their off-seam winding supports are therefore disjoint. Oriented boundary addition gives pointwise winding-number coverage of the original oriented domain before choosing an AP.','For an embedded frozen original shell interpreted as a valid bounded mesh domain, this winding partition gives geometric two-lobe coverage/nonoverlap away from the seam. V and M1 conservation are additional exact checks, not standalone overlap proofs.'],requirementsAfterAP=['AP_left must be strictly within every inward face halfspace of its 28-face closed subshell, AP_right within its 12-face closed subshell, including opposite sides of the separator.','With embedded subshells and convex literal bases, strict visibility supplies nonzero cone heights and a cone partition; actual conforming side-ring matching, all metric/role guards and complete halo checks remain necessary.','Strict visibility alone does not prove D/W/R/S/nonorthogonality/tet quality, source material authority, actual writer/native topology or final installed certification.','The original38 face ordered rings/coordinates/outside-cell IDs and eight physical/source roles must remain unchanged; sourceZone is provenance only, not material authorization.'])
out=root/'two-lobe-separator-independent-20261006';out.mkdir(exist_ok=False);(out/'separator-topology-geometry-audit.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
print(json.dumps(dict(reportPath=str(out/'separator-topology-geometry-audit.json'),checks=checks,classificationCounts={k:len(v) for k,v in classification.items()},existingExactSeparatorFaces=originalExactFaces,leftClosure=closure(leftclosed),rightClosure=closure(rightclosed),volumes=dict(left=rat(L[0]),right=rat(R[0]),globalVolume=rat(G[0])),physicalFacesByLobe={name:[r['face'] for r in physical if r['lobe']==name] for name in ['left','right']},separatorInteriorOuterHits=interiorHits,inputSha256=report['inputSha256']),indent=2))
