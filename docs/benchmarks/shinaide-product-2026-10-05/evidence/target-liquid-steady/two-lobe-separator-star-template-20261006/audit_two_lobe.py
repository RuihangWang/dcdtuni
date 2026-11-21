from pathlib import Path
from fractions import Fraction as F
from collections import Counter
import json,struct,hashlib,math
here=Path(__file__).parent
def vector(p):return list(map(F,p))
def sub(a,b):return [x-y for x,y in zip(a,b)]
def cross(a,b):return [a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]]
def dot(a,b):return sum(x*y for x,y in zip(a,b))
def vm(doc,cell):
 p={int(k):vector(v)for k,v in doc['points'].items()};v=F(0);m=[F(0)]*3
 for f in doc['cells'][str(cell)]['faces']:
  row=doc['faces'][str(f)];r=row['pointIDs']if row['owner']==cell else [row['pointIDs'][0]]+row['pointIDs'][:0:-1]
  a=p[r[0]]
  for j in range(1,len(r)-1):
   b,c=p[r[j]],p[r[j+1]];dv=dot(a,cross(b,c))/6;v+=dv
   for q in range(3):m[q]+=dv*(a[q]+b[q]+c[q])/4
 return v,m
def summed(doc,cells):
 vals=[vm(doc,c)for c in cells]
 return sum(v[0]for v in vals),[sum(v[1][j]for v in vals)for j in range(3)]
def outward(row,core):
 ids=row['pointIDs']
 assert (row['owner']in core)!=(row['neighbour']in core)
 return ids if row['owner']in core else [ids[0]]+ids[:0:-1]
def ulpdiff(a,b):
 def key(x):
  q=struct.unpack('q',struct.pack('d',x))[0]
  return q if q>=0 else -(q+(1<<63))
 return abs(key(a)-key(b))

old=json.loads((here/"complete-star-input.json").read_text());top=json.loads((here/"star-topology-report.json").read_text());seam=json.loads((here/"next-seam-subcavity-observation.json").read_text())
raw=[json.loads(l)for l in(here/"typed/installed-kernel-raw.jsonl").read_text().splitlines()if l.startswith('{"kind"')]
def exactrow(x):return {"numerator":str(x.numerator),"denominator":str(x.denominator),"approx":float(x)}
def certify(new):
 oc=set(old["children"]);nc=set(new["children"])
 assert not nc&{v for r in old["faces"].values()for v in [r["owner"],r["neighbour"]]},"new-label-collision"
 for p,xyz in old["points"].items():assert struct.pack("ddd",*xyz)==struct.pack("ddd",*new["points"][p]),"original-point-coordinate"
 assert set(new["points"])-set(old["points"])=={"591824","591825"},"new-point-identity"
 assert all(not {28142,28144}&set(r["pointIDs"])for r in new["faces"].values()),"old-interior-points-still-used"
 for c,r in new["cells"].items():assert set(r["faces"])=={int(f)for f,rr in new["faces"].items()if int(c)in[rr["owner"],rr["neighbour"]]},"incomplete-cell-incidence"
 separator=new["faces"]["2251326"];assert separator["pointIDs"]==seam["closingInternalQuadOutwardRings"]["left"],"separator-ring"
 assert separator["patch"]is None and separator["sourceZone"]==-1 and separator["sourceBase"]==-1,"separator-role"
 assert new["cells"][str(separator["owner"])]["lobe"]==0 and new["cells"][str(separator["neighbour"])]["lobe"]==1,"separator-endpoint"
 kernel=[];shell=[]
 for f in top["vertexStarExteriorFaces"]:
  a,b=old["faces"][str(f)],new["faces"][str(f)]
  for k in ["patch","sourceZone","sourceBase","nativeFace"]:assert a[k]==b[k],"outer-role"
  ids=outward(a,oc);assert ids==outward(b,nc),"outer-ring-or-point0"
  ao=a["neighbour"]if a["owner"]in oc else a["owner"];bo=b["neighbour"]if b["owner"]in nc else b["owner"];assert ao==bo,"outside-endpoint"
  shell.append(dict(face=f,coreOutwardRing=ids,actualOutside=ao,patch=a["patch"],sourceZone=a["sourceZone"],sourceBase=a["sourceBase"],nativeFace=a["nativeFace"]))
 for k,name in enumerate(["left","right"]):
  p=vector(new["points"][str(591824+k)]);assert (p[0]<F(seam["plane"])if k==0 else p[0]>F(seam["plane"])),"apex-wrong-lobe"
  for f in seam["subshellOriginalFaces"][name]+[2251326]:
   rr=new["faces"][str(f)]
   if f==2251326:ids=seam["closingInternalQuadOutwardRings"][name]
   else:ids=outward(rr,nc)
   ps=[vector(new["points"][str(i)])for i in ids];n=cross(sub(ps[1],ps[0]),sub(ps[2],ps[0]))
   assert dot(n,n)>0 and all(dot(n,sub(q,ps[0]))==0 for q in ps),"nonplanar-or-zero-base"
   side=dot(n,sub(p,ps[0]));assert side<0,"strict-subkernel"
   kernel.append(dict(lobe=k,face=f,side=exactrow(side)))
 for c in nc:
  rr=new["cells"][str(c)];ed=Counter();vertices=set()
  for f in rr["faces"]:
   fr=new["faces"][str(f)];ids=fr["pointIDs"]if fr["owner"]==c else [fr["pointIDs"][0]]+fr["pointIDs"][:0:-1];vertices.update(ids)
   for a,b in zip(ids,ids[1:]+ids[:1]):ed[a,b]+=1
  assert all(ed[a,b]==ed[b,a]==1 for a,b in ed),"directed-cell-edge-closure"
  assert vm(new,c)[0]>0,"nonpositive-cell-volume"
  for f in rr["faces"]:
   fr=new["faces"][str(f)];ids=fr["pointIDs"]if fr["owner"]==c else [fr["pointIDs"][0]]+fr["pointIDs"][:0:-1];pp=[vector(new["points"][str(i)])for i in ids];n=cross(sub(pp[1],pp[0]),sub(pp[2],pp[0]))
   assert all(dot(n,sub(vector(new["points"][str(i)]),pp[0]))<=0 for i in vertices),"nonconvex-cone-halfspace"
 assert summed(old,oc)==summed(new,nc),"cavity-V-M1"
 for c in old["outsideCells"]:assert vm(old,c)==vm(new,c),"outside-Fraction-V-M1"
 subs={}
 for k,name in enumerate(["left","right"]):
  v,m=summed(new,[c for c in nc if new["cells"][str(c)]["lobe"]==k]);q=seam["subshellVolumes"][name]
  assert v==F(int(q["numerator"]),int(q["denominator"])),"subcavity-V"
  subs[name]=dict(V=exactrow(v),M1=[exactrow(x)for x in m])
 contained={}
 for k in range(2):
  ap=vector(new["points"][str(591824+k)]);rows=[]
  for c in oc:
   sides=[]
   for f in old["cells"][str(c)]["faces"]:
    fr=old["faces"][str(f)];ids=fr["pointIDs"]if fr["owner"]==c else [fr["pointIDs"][0]]+fr["pointIDs"][:0:-1];pp=[vector(old["points"][str(i)])for i in ids]
    sides.append(dot(cross(sub(pp[1],pp[0]),sub(pp[2],pp[0])),sub(ap,pp[0])))
   if all(s<=0 for s in sides):rows.append(c)
  assert rows,"apex-not-in-original-covered-cavity";contained[str(k)]=rows
 return dict(literal38OuterOrderedRingsPoint0RolesAndOutside=shell,strictFractionSubkernel=kernel,subcavityVolumesAndMoments=subs,apexContainedInOriginalCells=contained,all40NewConesConvexHalfspacePositiveAndClosed=True,originalCoordinatesExact=True,FractionWholeVolumeAndFirstMomentExact=True,all69OutsideFractionShapesExact=True,separatorOppositeExactPlaneAndRoles=True)
def run():
 bc={r["id"]:r for r in raw[0]["cells"]};bf={r["face"]:r for r in raw[0]["capturedMetrics"]};reports={}
 for j,(tag,filename)in enumerate([("chebyshev","candidate-chebyshev.json"),("maxmin","candidate-best.json")],1):
  new=json.loads((here/filename).read_text());shape=certify(new)
  cc={r["id"]:r for r in raw[j]["cells"]};ff={r["face"]:r for r in raw[j]["capturedMetrics"]};metrics={}
  for k,t,up in [("D",.001,False),("weight",.05,False),("ratio",.01,False),("skew",4,True),("N",70,True)]:
   a,b=(bc,cc)if k=="D"else(bf,ff);bad=lambda x:not math.isfinite(x)or(x>t if up else x<t)
   metrics[k]=dict(threshold=t,oldBad=[c for c,r in a.items()if bad(r[k])],newBad=[c for c,r in b.items()if bad(r[k])],oldMin=min(r[k]for r in a.values()),newMin=min(r[k]for r in b.values()),oldMax=max(r[k]for r in a.values()),newMax=max(r[k]for r in b.values()),retainedPassingRoleRegressions=[c for c in set(a)&set(b)if not bad(a[c][k])and bad(b[c][k])])
  passing=[c for c in old["children"]if bc[c]["D"]>=.001]
  inherited=[dict(cell=c,D=cc[c]["D"],baseFace=new["cells"][str(c)]["baseFace"],replacedPassingOriginCount=len(passing))for c in new["children"]if cc[c]["D"]<.001]
  changes=[]
  for c in old["outsideCells"]:
   a,b=bc[c],cc[c];fields=[]
   for name,x,y in [("V",a["V"],b["V"])]+[(f"CC{k}",a["CC"][k],b["CC"][k])for k in range(3)]:
    if struct.pack("d",x)!=struct.pack("d",y):fields.append(dict(field=name,old=x,new=y,ULP=ulpdiff(x,y)))
   if fields:changes.append(dict(cell=c,deltas=fields))
  assert not any(metrics[k]["retainedPassingRoleRegressions"]for k in["D","weight","ratio","skew"])
  candidatepass=not inherited and not any(metrics[k]["newBad"]for k in["D","weight","ratio","skew"])and not raw[j]["stockConcaveAny"]
  r=dict(shape=shape,metrics=metrics,coreCounts=[32,40],completeCounts=[len(bc),len(cc)],capturedMetricFaces=[len(bf),len(ff)],inheritedPassingDRefusals=inherited,allReplacedPassingOriginCount=len(passing),newCoreDmin=min(cc[c]["D"]for c in new["children"]),actualStockConcavityAnyCompleteCell=[raw[0]["stockConcaveAny"],raw[j]["stockConcaveAny"]],minPyramidV=min(x["minPyramidV"]for x in cc.values()),maxRelativeClosure=max(x["relativeClosure"]for x in cc.values()),minCentreFanTetQuality=min(x["minCentreFanTetQuality"]for x in ff.values()),minSharedAnchorTetQuality=min(x["bestSharedBaseTetQuality"]for x in ff.values()),ordinaryOutsideMomentChanges=changes,maxOrdinaryOutsideULP=max([0]+[v["ULP"]for x in changes for v in x["deltas"]]),qualifiedLocalInstalledHardCandidate=candidatepass,productionAuthorized=False)
  assert r["minPyramidV"]>0 and r["maxRelativeClosure"]<1e-10 and r["minCentreFanTetQuality"]>1e-30 and r["minSharedAnchorTetQuality"]>1e-30
  reports[tag]=r
 assert not reports["chebyshev"]["qualifiedLocalInstalledHardCandidate"]and reports["maxmin"]["qualifiedLocalInstalledHardCandidate"]
 out=dict(scope="One approved exact-existing-axis/same4point separator two-lobe template. Installed DP/Int32 OpenFOAM geometry fullhalo. Local hard candidate only; not P13/writer/source-authorized/native-product acceptance.",baselineJob="P12 52d0e89c",inputSha256=hashlib.sha256((here/"complete-star-input.json").read_bytes()).hexdigest(),reports=reports,all8PhysicalLiteralFacetsPreserved=True,coupledFacesStored=[],unknownNormalSourceMaterialAuthorityNotInferred=True,noP13OrFinalWriterAliasOrProductClockClaim=True)
 (here/"installed-two-lobe-audit.json").write_text(json.dumps(out,indent=2,allow_nan=False)+"\n")
 print(json.dumps({tag:dict(counts={k:[len(v["oldBad"]),len(v["newBad"])]for k,v in r["metrics"].items()},coreDmin=r["newCoreDmin"],qualifiedLocal=r["qualifiedLocalInstalledHardCandidate"],outsideDPChanges=len(r["ordinaryOutsideMomentChanges"]),maxULP=r["maxOrdinaryOutsideULP"],tet=r["minSharedAnchorTetQuality"])for tag,r in reports.items()}))
if __name__=="__main__":run()
