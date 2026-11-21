from pathlib import Path
import json,copy,sys,time,numpy as np
from scipy.optimize import linprog,minimize
I=Path("/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/integration")
sys.path.insert(0,str(I/"tools"))
import audit_air_cone_small_union_independent as U
here=Path(__file__).parent
original=json.loads((here/"complete-star-input.json").read_text())
top=json.loads((here/"star-topology-report.json").read_text())
seam=json.loads((here/"next-seam-subcavity-observation.json").read_text())
oldcore=set(original["children"]);P={int(k):np.array(v)for k,v in original["points"].items()}
old=U.WrittenStencil(original,original["originalApex"])
axis=seam["axis"];plane=seam["plane"];separatorID=2251326
groups=[];planes=[];bounds=[];cheby=[]
for k,name in enumerate(["left","right"]):
 rows=[]
 for f in seam["subshellOriginalFaces"][name]:
  r=original["faces"][str(f)];ids=r["pointIDs"]if r["owner"]in oldcore else [r["pointIDs"][0]]+r["pointIDs"][:0:-1]
  sf,fc=old.face(f);sf=sf*(1 if r["owner"]in oldcore else -1)
  rows.append(dict(face=f,ids=ids,Sf=sf.tolist(),FC=fc.tolist(),outside=r["neighbour"]if r["owner"]in oldcore else r["owner"],patch=r["patch"],side=k,separator=False))
 ids=seam["closingInternalQuadOutwardRings"][name];xyz=np.array([P[v]for v in ids])
 sf=np.cross(xyz[1]-xyz[0],xyz[2]-xyz[0]);fc=xyz.mean(0)
 rows.append(dict(face=separatorID,ids=ids,Sf=sf.tolist(),FC=fc.tolist(),outside=None,patch=None,side=k,separator=True))
 pp=np.array([[*(np.array(r["Sf"])/np.linalg.norm(r["Sf"])),np.array(r["Sf"])@r["FC"]/np.linalg.norm(r["Sf"])]for r in rows])
 nodes=sorted(set(v for r in rows for v in r["ids"]));xyz=np.array([P[v]for v in nodes]);lo=xyz.min(0);span=xyz.max(0)-lo
 lp=linprog([0,0,0,-1],A_ub=np.c_[pp[:,:3],np.ones(len(pp))],b_ub=pp[:,3],bounds=[(None,None)]*3+[(0,None)],method="highs")
 assert lp.success and lp.x[3]>3e-6,(name,lp.message)
 groups.append(rows);planes.append(pp);bounds.append((lo,span));cheby.append(lp.x[:3])
(rowsL,rowsR)=groups;outer=rowsL+rowsR
newIDs=list(range(833839,833839+len(outer)))
newPoints=[591824,591825]
def construct(aps):
 out=copy.deepcopy(original);aps=np.array(aps).reshape(2,3)
 for p,ap in zip(newPoints,aps):out["points"][str(p)]=list(map(float,ap))
 for c in oldcore:del out["cells"][str(c)]
 for f,r in list(out["faces"].items()):
  if r["owner"]in oldcore and r["neighbour"]in oldcore:del out["faces"][f]
 bySep=[];edgeGroups=[]
 for k,rows in enumerate(groups):
  edges={}
  for j,r in enumerate(rows):
   c=newIDs[sum(len(x)for x in groups[:k])+j];f=r["face"]
   out["cells"][str(c)]={"faces":[f],"sourceParent":-1,"originCells":sorted(oldcore),"lobe":k,"baseFace":f}
   if r["separator"]:bySep.append(c)
   else:
    fr=out["faces"][str(f)]
    if fr["owner"]in oldcore:fr["owner"]=c
    else:fr["neighbour"]=c
   for a,b in zip(r["ids"],r["ids"][1:]+r["ids"][:1]):edges.setdefault(tuple(sorted((a,b))),[]).append((c,[b,a,newPoints[k]]))
  edgeGroups.append(edges)
 out["faces"][str(separatorID)]={"pointIDs":seam["closingInternalQuadOutwardRings"]["left"],"owner":bySep[0],"neighbour":bySep[1],"patch":None,"sourceBase":-1,"sourceZone":-1,"nativeFace":-1,"separator":True}
 nf=separatorID+1;newfaces=[separatorID]
 for k,edges in enumerate(edgeGroups):
  for edge,rs in sorted(edges.items()):
   assert len(rs)==2,(k,edge,rs)
   (a,ring),(b,rr)=rs;assert set(ring)==set(rr)
   if a>b:a,b=b,a;ring=[ring[0]]+ring[:0:-1]
   out["faces"][str(nf)]={"pointIDs":ring,"owner":a,"neighbour":b,"patch":None,"sourceBase":-1,"sourceZone":-1,"nativeFace":-1,"lobe":k}
   for c in (a,b):out["cells"][str(c)]["faces"].append(nf)
   newfaces.append(nf);nf+=1
 out["children"]=newIDs;out["apexWrittenPoint"]=newPoints[0];out["originalApex"]=aps[0].tolist();out["lobeApexWrittenPoints"]=newPoints
 out["firstHaloMetricFaces"]=sorted((set(original["firstHaloMetricFaces"])&set(map(int,out["faces"])))|set(newfaces));out,_=U.canonicalize_internal_roles(out)
 for c,r in out["cells"].items():assert {int(f)for f,fr in out["faces"].items()if int(c)in [fr["owner"],fr["neighbour"]]}==set(r["faces"])
 return out,newfaces
def full(aps):
 out,newfaces=construct(aps);st=U.WrittenStencil(out,out["originalApex"])
 cs=[st.cell_detail(c)for c in out["children"]];fs={f:st.face_detail(f)for f in out["firstHaloMetricFaces"]}
 D=[r["D"]for r in cs];W=[r.get("weight",1)for r in fs.values()];R=[r.get("ratio",1)for r in fs.values()];S=[r["skew"]for r in fs.values()];N=[r.get("nonOrthogonalityDegrees",0)for r in fs.values()]
 q=dict(points=np.array(aps).tolist(),Dmin=min(D),Wmin=min(W),Rmin=min(R),Smax=max(S),Nmax=max(N),counts=dict(D=sum(v<.001 for v in D),W=sum(v<.05 for v in W),R=sum(v<.01 for v in R),S=sum(v>4 for v in S),N=sum(v>70 for v in N)),stockTriggers=sum(r["faceCentreConcavityPairTriggers"]for r in cs),minPyramid=min(r["minCentroidPyramidVolume"]for r in cs))
 return out,q,dict(cells=cs,faces=fs,newFaces=newfaces)
# Generic local star fast geometry: all bases planar, each cone is .75FC+.25AP.
bs=np.array([r["Sf"]for r in outer]);bc=np.array([r["FC"]for r in outer]);sides=np.array([r["side"]for r in outer])
baseSelected=np.array([r["separator"]or r["outside"]is not None for r in outer])
edgeData=[];inc=[[]for r in outer]
for k,rows in enumerate(groups):
 edges={};offset=sum(len(x)for x in groups[:k])
 for j,r in enumerate(rows):
  for a,b in zip(r["ids"],r["ids"][1:]+r["ids"][:1]):edges.setdefault(tuple(sorted((a,b))),[]).append((offset+j,b,a))
 for edge,rs in sorted(edges.items()):
  assert len(rs)==2;ei=len(edgeData);a,b=rs[0][1:];edgeData.append((k,rs[0][0],rs[1][0],P[a],P[b]))
  inc[rs[0][0]].append((ei,1));inc[rs[1][0]].append((ei,-1))
edgeSides=np.array([r[0]for r in edgeData]);adj=np.array([[r[1],r[2]]for r in edgeData]);ea=np.array([r[3]for r in edgeData]);eb=np.array([r[4]for r in edgeData])
sepIndices=[j for j,r in enumerate(outer)if r["separator"]]
extidx=[j for j,r in enumerate(outer)if r["outside"]is not None];outCC=np.array([old.cell(outer[j]["outside"])[2]for j in extidx]);outV=np.array([old.cell(outer[j]["outside"])[3]for j in extidx])
physidx=[j for j,r in enumerate(outer)if r["patch"]is not None]
def met(sf,fc,pts,co,vo,cn,vn):
 delta=cn-co;cpf=fc-co;sn=np.linalg.norm(sf,axis=1);dn=np.linalg.norm(delta,axis=1)
 da=np.abs(np.einsum("ij,ij->i",sf,cpf));db=np.abs(np.einsum("ij,ij->i",sf,cn-fc))
 w=np.minimum(da,db)/(da+db+1e-300);r=np.minimum(vo,vn)/(np.maximum(vo,vn)+1e-300)
 sv=cpf-(np.einsum("ij,ij->i",sf,cpf)/(np.einsum("ij,ij->i",sf,delta)+1e-150))[:,None]*delta
 hh=sv/(np.linalg.norm(sv,axis=1)+1e-150)[:,None]
 proj=np.array([max(abs((pp-ff)@h))for pp,ff,h in zip(pts,fc,hh)])
 skew=np.linalg.norm(sv,axis=1)/np.maximum(.2*dn+1e-150,proj)
 n=np.degrees(np.arccos(np.clip(np.einsum("ij,ij->i",sf,delta)/(sn*dn+1e-150),-1,1)))
 return w,r,skew,n
def values(aps):
 aps=np.array(aps).reshape(2,3);cc=.75*bc+.25*aps[sides];vol=-np.einsum("ij,ij->i",bs,aps[sides]-bc)/3
 sf=np.cross(eb-ea,aps[edgeSides]-ea)/2;fc=(ea+eb+aps[edgeSides])/3
 ds=[]
 for j,entries in enumerate(inc):
  ss=[sf[e]*sign for e,sign in entries]
  if baseSelected[j]:ss.append(bs[j])
  ss=np.array(ss);avg=np.linalg.norm(ss,axis=1).mean();ds.append(np.linalg.det((ss/avg).T@(ss/avg))/8)
 pts=[np.array([a,b,p])for a,b,p in zip(ea,eb,aps[edgeSides])]
 w,r,sk,n=met(sf,fc,pts,cc[adj[:,0]],vol[adj[:,0]],cc[adj[:,1]],vol[adj[:,1]])
 si,sj=sepIndices;sw,sr,ss,sn=met(bs[[si]],bc[[si]],[np.array([P[v]for v in outer[si]["ids"]])],cc[[si]],vol[[si]],cc[[sj]],vol[[sj]])
 xx=np.array(extidx);ew,er,es,en=met(bs[xx],bc[xx],[np.array([P[v]for v in outer[j]["ids"]])for j in extidx],cc[xx],vol[xx],outCC,outV)
 # Boundary skew original formula, only8 physical bases
 ps=[]
 for j in physidx:
  co=cc[j];cpf=bc[j]-co;normal=bs[j]/np.linalg.norm(bs[j]);sv=cpf-(normal@cpf)*normal;hh=sv/(np.linalg.norm(sv)+1e-150)
  proj=max(abs((np.array([P[v]for v in outer[j]["ids"]])-bc[j])@hh));ps.append(np.linalg.norm(sv)/max(.4*np.linalg.norm(cpf)+1e-150,proj))
 return np.array(ds),np.r_[w,sw,ew],np.r_[r,sr,er],np.r_[sk,ss,es,ps],np.r_[n,sn,en],vol
def main():
 start=time.monotonic()
 eligibility=dict(scope="One exact existing-axis two-lobe/star template only. Closure is not a convexity proof; visibility and actual typed geometry are separate.",axis=axis,plane=plane,outerCount=[len(x)-1 for x in groups],coneCount=[len(x)for x in groups],planes=[x.tolist()for x in planes],bounds=[dict(lo=x.tolist(),span=y.tolist())for x,y in bounds],chebyshevPoints=[x.tolist()for x in cheby],radii=[float(min(pp[:,3]-pp[:,:3]@ap))for pp,ap in zip(planes,cheby)],separatorID=separatorID,newPointIDs=newPoints,newCoreIDs=newIDs)
 (here/"eligibility.json").write_text(json.dumps(eligibility,indent=2,allow_nan=False)+"\n")
 d,q,detail=full(cheby);(here/"candidate-chebyshev.json").write_text(json.dumps(d,indent=2,allow_nan=False)+"\n");(here/"chebyshev-profile.json").write_text(json.dumps(dict(summary=q,**detail),indent=2,allow_nan=False)+"\n")
 v=values(cheby);assert max(abs(np.array([r["D"]for r in detail["cells"]])-v[0]))<1e-12,"fast initial full-formula Dbinding"
 los=np.array([x for x,y in bounds]);spans=np.array([y for x,y in bounds]);q0=((np.array(cheby)-los)/spans).ravel()
 hist=[];cache={};best=None
 class Stop(Exception):pass
 def evaluate(q):
  nonlocal best
  key=tuple(map(float,q))
  if key in cache:return cache[key]
  if len(hist)>=192 or time.monotonic()-start>=65:raise Stop
  aps=los+np.array(q).reshape(2,3)*spans;dv,w,r,s,n,vol=values(aps)
  margin=np.r_[*[pp[:,3]-pp[:,:3]@ap-3e-6 for pp,ap in zip(planes,aps)]]
  feasible=bool(min(w)>=.05 and min(r)>=.01 and max(s)<=4 and max(n)<=86.9077 and min(vol)>0 and min(margin)>=0)
  score=float(min(np.r_[dv/.001,w/.05,r/.01,4/np.maximum(s,1e-20),86.9077/np.maximum(n,1e-20)]))
  row=dict(points=aps.tolist(),q=list(q),Dmin=float(min(dv)),Wmin=float(min(w)),Rmin=float(min(r)),Smax=float(max(s)),Nmax=float(max(n)),minV=float(min(vol)),score=score,nonDFeasible=feasible,counts=dict(D=int(sum(dv<.001)),W=int(sum(w<.05)),R=int(sum(r<.01)),S=int(sum(s>4)),N=int(sum(n>70))))
  hist.append(row);cache[key]=(dv,w,r,s,n,vol,margin,row)
  if best is None or(feasible and(not best["nonDFeasible"]or row["Dmin"]>best["Dmin"]))or(not feasible and not best["nonDFeasible"]and score>best["score"]):best=row
  return cache[key]
 def con(x):
  dv,w,r,s,n,vol,margin,row=evaluate(x[:6]);return np.r_[dv/.001-x[6],w/.05-1,r/.01-1,4-s,86.9077-n,margin/max(np.linalg.norm(spans),1e-20)]
 initial=evaluate(q0)
 try:
  res=minimize(lambda x:-x[6],np.r_[q0,initial[0].min()/.001],method="SLSQP",bounds=[(0,1)]*6+[(0,20)],constraints={"type":"ineq","fun":con},options={"maxiter":32,"ftol":1e-10})
  stop=dict(success=bool(res.success),message=str(res.message),parameters=res.x.tolist())
 except Stop:stop=dict(success=False,message="bounded192requests/65seconds")
 d,q,detail=full(best["points"]);(here/"candidate-best.json").write_text(json.dumps(d,indent=2,allow_nan=False)+"\n");(here/"best-profile.json").write_text(json.dumps(dict(summary=q,**detail),indent=2,allow_nan=False)+"\n")
 result=dict(scope="One geometry-derived two-subkernel seed and one bounded joint6D finite maxmin selection; pure proposal, not actual installed acceptance.",initial=hist[0],best=best,fullPureBestSummary=q,stop=stop,evaluations=len(hist),seconds=time.monotonic()-start,history=hist)
 (here/"finite-two-lobe-search.json").write_text(json.dumps(result,indent=2,allow_nan=False)+"\n")
 print(json.dumps({k:result[k]for k in ["initial","best","fullPureBestSummary","stop","evaluations","seconds"]}))
if __name__=="__main__":main()
