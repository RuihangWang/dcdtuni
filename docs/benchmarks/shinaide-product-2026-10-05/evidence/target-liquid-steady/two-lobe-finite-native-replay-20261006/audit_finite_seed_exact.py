import json,math,hashlib,collections,itertools,functools,time
from pathlib import Path
from fractions import Fraction as F
start=time.monotonic();root=Path('/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/air/.codex-tmp');base=Path(__file__).parent;origbase=root/'complete-two-point-star-template-20261006';staticbase=root/'two-lobe-separator-independent-20261006'
contents={n:(Path('/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/air/.codex-tmp/two-lobe-separator-star-template-20261006')/n if n=='eligibility.json' else base/n).read_bytes() for n in ['finite-seed-template.json','eligibility.json','finite-seed-report.json']};c=json.loads(contents['finite-seed-template.json']);elig=json.loads(contents['eligibility.json']);search=json.loads(contents['finite-seed-report.json']);orig=json.loads((origbase/'complete-star-input.json').read_bytes());static=json.loads((staticbase/'separator-topology-geometry-audit.json').read_bytes());embedding=json.loads((staticbase/'fixed-surface-embeddedness-certificate.json').read_bytes())
P={int(p):tuple(F.from_float(x) for x in xyz) for p,xyz in c['points'].items()};oldcore=set(orig['children']);core=set(c['children']);ap=c['lobeApexWrittenPoints'];sep=elig['separatorID'];plane=F.from_float(elig['plane'])
sub=lambda u,v:tuple(x-y for x,y in zip(u,v));dot=lambda u,v:sum((x*y for x,y in zip(u,v)),F(0));cross=lambda u,v:(u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0]);rat=lambda x:dict(numerator=str(x.numerator),denominator=str(x.denominator),approx=float(x))
def primitive(v):
 den=math.lcm(*(x.denominator if isinstance(x,F) else 1 for x in v));ii=[int(x*den) for x in v];g=functools.reduce(math.gcd,(abs(x) for x in ii))
 if not g:return None
 ii=tuple(x//g for x in ii)
 if next(x for x in ii if x)<0:ii=tuple(-x for x in ii)
 return ii

def normal(ring):
 xyz=[P[p] for p in ring]
 for k in range(1,len(xyz)-1):
  n=cross(sub(xyz[k],xyz[0]),sub(xyz[k+1],xyz[0]))
  if dot(n,n):return n
 return None

def ring_out(fid,cell):
 row=c['faces'][str(fid)];ring=list(row['pointIDs'])
 return ring if row['owner']==cell else [ring[0]]+list(reversed(ring[1:]))
def integral(rings):
 V=F(0);M=[F(0)]*3
 for ring in rings:
  x=P[ring[0]]
  for k in range(1,len(ring)-1):
   y=P[ring[k]];z=P[ring[k+1]];v=dot(x,cross(y,z))/6;V+=v
   for i in range(3):M[i]+=v*(x[i]+y[i]+z[i])/4
 return V,tuple(M)
refs=collections.defaultdict(list)
for cell in core:
 for f in c['cells'][str(cell)]['faces']:refs[f].append(cell)
outer=sorted(f for f,cs in refs.items() if len(cs)==1);interior=sorted(f for f,cs in refs.items() if len(cs)==2);ownershipIssues=[]
for f,cs in refs.items():
 row=c['faces'][str(f)];expected=[v for v in [row['owner'],row['neighbour']] if v in core]
 if sorted(expected)!=sorted(cs):ownershipIssues.append(dict(face=f,refs=cs,endpoints=expected))
outerPreservation=[]
for f in static['all38LiteralExteriorRoles']:
 fid=f['face'];old=orig['faces'][str(fid)];new=c['faces'][str(fid)];oldRing=old['pointIDs'] if old['owner'] in oldcore else [old['pointIDs'][0]]+list(reversed(old['pointIDs'][1:]));newRing=new['pointIDs'] if new['owner'] in core else [new['pointIDs'][0]]+list(reversed(new['pointIDs'][1:]));outsideOld=[v for v in [old['owner'],old['neighbour']] if v is not None and v not in oldcore];outsideNew=[v for v in [new['owner'],new['neighbour']] if v is not None and v not in core]
 outerPreservation.append(dict(face=fid,outwardOrderedRingPreserved=oldRing==newRing,coordinatesPreserved=all(c['points'][str(p)]==orig['points'][str(p)] for p in old['pointIDs']),outsideCellIDsPreserved=outsideOld==outsideNew,rolePreserved=all(old[k]==new[k] for k in ['patch','sourceBase','sourceZone','nativeFace'])))
lobeRows={0:[],1:[]};cellRows=[];vertexIssues=[];shapeIssues=[];closureIssues=[];cellData={};totV=F(0);totM=[F(0)]*3;bylobe={0:[F(0),[F(0)]*3],1:[F(0),[F(0)]*3]}
for cell in sorted(core):
 row=c['cells'][str(cell)];lobe=row['lobe'];basefid=row['baseFace'];baseRing=ring_out(basefid,cell);baseSet=set(baseRing);apex=ap[lobe];rings=[ring_out(f,cell) for f in row['faces']];verts=sorted(set(p for ring in rings for p in ring));expectedSideSets={frozenset([u,v,apex]) for u,v in zip(baseRing,baseRing[1:]+baseRing[:1])};actualSideSets={frozenset(c['faces'][str(f)]['pointIDs']) for f in row['faces'] if f!=basefid}
 if set(verts)!=baseSet|{apex} or len(row['faces'])!=len(baseRing)+1 or expectedSideSets!=actualSideSets:shapeIssues.append(cell)
 n=normal(baseRing)
 if n is None:raise ValueError(('zero-base',cell))
 signed=dot(n,sub(P[apex],P[baseRing[0]]));lobeRows[lobe].append(dict(cell=cell,baseFace=basefid,orderedRing=baseRing,apex=apex,signedPlaneValue=rat(signed),strictlyVisible=signed<0,approximatePositiveHeight=float(-signed)/math.sqrt(float(dot(n,n)))))
 planeRows=[];edgeCount=collections.defaultdict(lambda:[0,0]);norms=[];edgeDirs=set()
 for f,ring in zip(row['faces'],rings):
  nn=normal(ring)
  if nn is None:vertexIssues.append(dict(cell=cell,face=f,issue='zero-area'));continue
  offset=dot(nn,P[ring[0]]);residual=[dot(nn,P[p])-offset for p in ring];test={p:dot(nn,P[p])-offset for p in verts};bad={str(p):rat(v) for p,v in test.items() if v>0};strictNonface=all(v<0 for p,v in test.items() if p not in ring)
  turns=[dot(nn,cross(sub(P[ring[i]],P[ring[i-1]]),sub(P[ring[(i+1)%len(ring)]],P[ring[i]]))) for i in range(len(ring))]
  if any(residual) or bad or not strictNonface or not all(v>0 for v in turns):vertexIssues.append(dict(cell=cell,face=f,issue='planarity-or-convexity',outwardVertexViolations=bad,strictNonface=strictNonface,strictFaceConvex=all(v>0 for v in turns)))
  planeRows.append(dict(face=f,orderedOutwardRing=ring,normal=[rat(x) for x in nn],offset=rat(offset),exactPlanar=not any(residual),allConeVerticesInward=not bad,allNonFaceVerticesStrictlyInward=strictNonface))
  norms.append((f,primitive(nn)))
  for u,v in zip(ring,ring[1:]+ring[:1]):
   key=tuple(sorted([u,v]));edgeCount[key][0]+=1;edgeCount[key][1]+=1 if u<v else -1;edgeDirs.add(primitive(sub(P[v],P[u])))
 if any(nb!=2 or bal!=0 for nb,bal in edgeCount.values()):closureIssues.append(cell)
 V,M=integral(rings);fanV=F(0);fanM=[F(0)]*3
 for j in range(1,len(baseRing)-1):
  aa,bb,cc=P[baseRing[0]],P[baseRing[j]],P[baseRing[j+1]];v=dot(sub(aa,P[apex]),cross(sub(bb,P[apex]),sub(cc,P[apex])))/6;fanV+=v
  for k in range(3):fanM[k]+=v*(P[apex][k]+aa[k]+bb[k]+cc[k])/4
 if V<=0 or fanV!=V or tuple(fanM)!=M:shapeIssues.append(cell)
 totV+=V
 for k in range(3):totM[k]+=M[k];bylobe[lobe][1][k]+=M[k]
 bylobe[lobe][0]+=V;cellRows.append(dict(cell=cell,lobe=lobe,baseFace=basefid,apex=apex,vertices=verts,coneType='tet' if len(baseRing)==3 else 'quad-base pyramid',volume=rat(V),firstMoment=[rat(x) for x in M],baseFanMatchesClosedCell=True,planes=planeRows));cellData[cell]=dict(vertices=[P[p] for p in verts],normalAxes=norms,edgeAxes=list(edgeDirs))
# Exact SAT face normals first, then nonzero edge-cross axes only for unresolved pairs.
proofs=[];unresolved=[];faceSeparated=0;edgeSeparated=0
for ca,cb in itertools.combinations(sorted(core),2):
 A=cellData[ca];B=cellData[cb];seen=set();found=None
 def try_axis(axis,kind,face=None):
  if axis is None or axis in seen:return None
  seen.add(axis);aa=[dot(axis,p) for p in A['vertices']];bb=[dot(axis,p) for p in B['vertices']];alo,ahi=min(aa),max(aa);blo,bhi=min(bb),max(bb)
  if ahi<=blo:return dict(cells=[ca,cb],kind=kind,face=face,axis=list(axis),lowerCone=ca,upperCone=cb,maxLower=rat(ahi),minUpper=rat(blo),gap=rat(blo-ahi))
  if bhi<=alo:return dict(cells=[ca,cb],kind=kind,face=face,axis=list(axis),lowerCone=cb,upperCone=ca,maxLower=rat(bhi),minUpper=rat(alo),gap=rat(alo-bhi))
  return None
 for owner,data in [(ca,A),(cb,B)]:
  for face,axis in data['normalAxes']:
   found=try_axis(axis,'face-plane',face)
   if found:break
  if found:break
 if found:faceSeparated+=1
 else:
  for ea in A['edgeAxes']:
   for eb in B['edgeAxes']:
    found=try_axis(primitive(cross(ea,eb)),'edge-cross-SAT')
    if found:break
   if found:break
  if found:edgeSeparated+=1
 if found:proofs.append(found)
 else:unresolved.append([ca,cb])
oldGlobalV=F(int(static['globalExact']['volume']['numerator']),int(static['globalExact']['volume']['denominator']));oldGlobalM=tuple(F(int(x['numerator']),int(x['denominator'])) for x in static['globalExact']['firstMoment']);lobeConservation=[]
for lobe,name in [(0,'left'),(1,'right')]:
 ref=static['subshells'][name];v=F(int(ref['FractionVolume']['numerator']),int(ref['FractionVolume']['denominator']));m=tuple(F(int(x['numerator']),int(x['denominator'])) for x in ref['FractionFirstMoment']);lobeConservation.append(dict(lobe=lobe,name=name,volumeMatches=bylobe[lobe][0]==v,firstMomentMatches=tuple(bylobe[lobe][1])==m,volume=rat(bylobe[lobe][0]),firstMoment=[rat(x) for x in bylobe[lobe][1]]))
checks=dict(core40=len(core)==40,coneCounts28And12=[len(lobeRows[x]) for x in [0,1]]==[28,12],apexIDsMatchEligibility=ap==elig['newPointIDs'],apexCoordinatesMatchFinitePlaneSeeds=[c['points'][str(p)] for p in ap]==[x['seed']for x in search],oppositeCorrectSides=P[ap[0]][0]<plane<P[ap[1]][0],separatorRingAndGeometryCorrect=set(c['faces'][str(sep)]['pointIDs'])==set(static['separator']['originalOrderedRingLeft']) and all(P[p][0]==plane for p in c['faces'][str(sep)]['pointIDs']),separatorIsInternal=c['faces'][str(sep)]['owner'] in core and c['faces'][str(sep)]['neighbour'] in core and c['faces'][str(sep)]['patch'] is None,allAPStrictlyVisible=all(v['strictlyVisible'] for rows in lobeRows.values() for v in rows),allConeVertexHalfspacesConvex=not vertexIssues,allConesPositiveClosed=not closureIssues and all(x['volume']['approx']>0 for x in cellRows),oneConePerLobeBaseAndCorrectSides=not shapeIssues,all780PairsInteriorDisjoint=len(proofs)==780 and not unresolved,ownershipMatchesCellRefs=not ownershipIssues,actualOuterExactlyOriginal38=outer==sorted(f['face'] for f in static['all38LiteralExteriorRoles']),all38OuterCoordinatesRingsOutsideAndRolesPreserved=all(all(v for k,v in x.items() if k!='face') for x in outerPreservation),oldTargetsUnusedAllCore=all(p not in [28142,28144] for cell in cellRows for p in cell['vertices']),oldTargetStoredCoordinatesPreserved=all(c['points'][str(p)]==orig['points'][str(p)] for p in [28142,28144]),exactTotalVolume=totV==oldGlobalV,exactTotalFirstMoment=tuple(totM)==oldGlobalM,exactBothLobeVolumeAndFirstMoment=all(x['volumeMatches'] and x['firstMomentMatches'] for x in lobeConservation),staticSubshellEmbeddingCertificatesPass=all(embedding[k]['embeddedByExactConvexFacePairIntersections'] for k in ['globalShell','leftSubshell','rightSubshell']))
report=dict(scope='Independent exact Fraction audit of fixed candidate APs and40 cones. Pure geometric certificate only; no native/business/API/source/index operation or candidate optimization/rebuild.',fixedInputSha256={n:hashlib.sha256(v).hexdigest() for n,v in contents.items()},referenceInputSha256=hashlib.sha256((origbase/'complete-star-input.json').read_bytes()).hexdigest(),staticSeparatorCertificateSha256=hashlib.sha256((staticbase/'separator-topology-geometry-audit.json').read_bytes()).hexdigest(),checks=checks,apexPoints=[dict(id=p,lobe=i,coordinates=c['points'][str(p)],xGapFromSeparator=rat(plane-P[p][0] if i==0 else P[p][0]-plane)) for i,p in enumerate(ap)],strictVisibilityByLobe=lobeRows,minimumPositiveVisibilityHeightApprox=min(v['approximatePositiveHeight'] for rows in lobeRows.values() for v in rows),cellHalfspaceCertificates=cellRows,pairSeparationSummary=dict(totalPairs=len(proofs)+len(unresolved),facePlaneSeparated=faceSeparated,edgeCrossSATSeparated=edgeSeparated,unresolvedPairs=unresolved),pairSeparationCertificates=proofs,outerPreservation=outerPreservation,lobeExactConservation=lobeConservation,totalExact=dict(volume=rat(totV),firstMoment=[rat(x) for x in totM]),failures=dict(coneShape=shapeIssues,coneVertexHalfspaces=vertexIssues,coneClosure=closureIssues,ownership=ownershipIssues,pairSAT=unresolved),coverageProof='The exact embedded static28/12 subshells partition the original38face domain. Each new AP is strictly inward of every own-lobe base plane; each actual candidate cone is a closed positive-volume convex polyhedron of that AP and its literal base. Every pair has an exact separating plane. The actual candidate outer surface is exactly the original38 oriented rings and roles, while all internal faces cancel by ownership. Thus these actual40 cones cover the original bounded two-lobe domain without overlapping interiors; exact V and M1 agree independently by each lobe and globally.',limits=['This audit proves geometry for the fixed binary coordinates; it does not certify stock/native floating arithmetic or final writer installation.','Typed fullhalo D/W/R/S/N/tet/concavity and every passing-role test are separate evidence, not recomputed here.','SourceZone/sourceBase physical-role preservation is not material or homogeneous-fluid/source authority.','P13 payload hash/binding, actual native labeling/aliases/point0, mesh install and final writer certificate remain outside this task.'],seconds=time.monotonic()-start)
out=base/'exact-geometry';out.mkdir(exist_ok=False);(out/'candidate-exact-geometry-certificate.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
print(json.dumps(dict(reportPath=str(out/'candidate-exact-geometry-certificate.json'),checks=checks,apexPoints=report['apexPoints'],pairSeparationSummary=report['pairSeparationSummary'],minimumPositiveVisibilityHeightApprox=report['minimumPositiveVisibilityHeightApprox'],minimumConeVolumeApprox=min(x['volume']['approx'] for x in cellRows),lobeVolumes=[dict(lobe=x['lobe'],volume=x['volume']) for x in lobeConservation],fixedInputSha256=report['fixedInputSha256'],seconds=report['seconds']),indent=2))
