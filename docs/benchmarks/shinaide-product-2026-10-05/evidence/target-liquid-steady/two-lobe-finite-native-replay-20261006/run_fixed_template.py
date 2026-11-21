from pathlib import Path
import json,hashlib,itertools,time,os,subprocess,sys,re,importlib.util
import numpy as np
here=Path(__file__).parent
frozen=here.parent/"two-lobe-separator-star-template-20261006"
air=here.parents[1]
src=air/"src/backend/aircooling/third_parties/dcCartMesh/src/mesh/dcCartMesh/dcCartCutCells/dcCartFinitePointRepair.H"
start=time.monotonic();deadline=start+90
def remaining():
    r=deadline-time.monotonic()
    if r<=0:raise TimeoutError("90-second diagnostic deadline")
    return r
bindings={}
def read(f):
    b=f.read_bytes();bindings[str(f)]={"bytes":len(b),"sha256":hashlib.sha256(b).hexdigest()}
    return b
# A fixed topology copy contains no successful apex coordinates. Ignore both
# stored candidate apices; all fresh seeds are solved below.
template=json.loads(read(frozen/"candidate-chebyshev.json"))
aps=template["lobeApexWrittenPoints"]
rawlines=read(frozen/"typed/installed-kernel-raw.jsonl").decode().splitlines()
installed={r["face"]:r for x in rawlines for r in ([*json.loads(x)["capturedMetrics"]] if json.loads(x)["kind"]=="chebyshev" else [])}
fixedpoints={int(k):v for k,v in template["points"].items()if int(k) not in aps}
bases=[];seeds=[];seedreports=[]
for lobe in [0,1]:
    cells=sorted((int(k),v)for k,v in template["cells"].items()if v.get("lobe")==lobe)
    planes=[];baseids=[]
    for c,row in cells:
        f=row["baseFace"];fr=template["faces"][str(f)];sgn=1 if fr["owner"]==c else -1
        sf=sgn*np.asarray(installed[f]["Sf"]);fc=np.asarray(installed[f]["FC"])
        n=sf/np.linalg.norm(sf);planes.append([*n,float(n@fc)]);baseids.append(f)
    planes=np.asarray(planes)
    # Preserve all constraints; bounded enumeration can include duplicate planes.
    A=np.c_[planes[:,:3],np.ones(len(planes))];b=planes[:,3]
    feasible=[];singular=0;examined=0
    for ix in itertools.combinations(range(len(planes)),4):
        if examined&255==0:remaining()
        examined+=1
        try:q=np.linalg.solve(A[list(ix)],b[list(ix)])
        except np.linalg.LinAlgError:singular+=1;continue
        if np.all(np.isfinite(q)) and q[3]>3e-6 and np.min(b-A@q)>=-2e-12:
            feasible.append((q,ix))
    if not feasible:raise RuntimeError("no finite geometry seed")
    # Numerical radius comparisons are only proposal generation; the installed
    # full geometry/quality guard below is authoritative.
    bestR=max(q[3]for q,ix in feasible)
    candidates=[(q,ix)for q,ix in feasible if q[3]>=bestR-2e-12]
    q,ix=min(candidates,key=lambda z:tuple(z[0][:3]))
    seeds.append(q[:3].tolist());bases.append(planes.tolist())
    seedreports.append({"lobe":lobe,"bases":baseids,"planeEquations":planes.tolist(),
      "examined4PlaneBases":examined,"singularBases":singular,"feasibleBases":len(feasible),
      "maxRadiusCandidates":len(candidates),"selection":"max-radius then lexicographic world coordinates (proposal only)",
      "chosenActiveBaseOrdinals":list(ix),"seed":q[:3].tolist(),"radius":float(q[3]),
      "minimumActualPlaneMargin":float(np.min(b-planes[:,:3]@q[:3]))})
(here/"finite-seed-report.json").write_text(json.dumps(seedreports,indent=2)+"\n")
# New points use the finite-derived seeds, not either stored pair.
pointids=sorted(map(int,template["points"]))
for ap,xyz in zip(aps,seeds):template["points"][str(ap)]=xyz
(here/"finite-seed-template.json").write_text(json.dumps(template,indent=2)+"\n")
H=read(frozen/"typed/actual_fixture.H").decode()
for key in ["candidatePoints","alternatePoints"]:
    H=re.sub(r"static const std::vector<Foam::point> "+key+r" = \{.*?\};\n","",H,flags=re.S)
H+="static const std::vector<Foam::point> candidatePoints = {"+",".join("Foam::point("+",".join(format(v,'.17g')for v in template["points"][str(i)])+")"for i in pointids)+"};\n"
newcore=sorted(template["children"])
H+="static const std::set<Foam::label> newCore = {"+",".join(map(str,newcore))+"};\n"
H+="static const Foam::label movingCompact[2] = {"+",".join(str(pointids.index(i))for i in aps)+"};\n"
H+="static const std::vector<Foam::label> lobeCellIDs[2] = {"+",".join("{"+",".join(k for k,v in template["cells"].items()if v.get("lobe")==l)+"}"for l in [0,1])+"};\n"
(here/"actual_fixture.H").write_text(H)
kernelbytes=read(src);(here/"dcCartFinitePointRepair.H").write_bytes(kernelbytes)
cpp=read(frozen/"typed/installed_kernel.cpp").decode()
cpp=cpp.replace('View(int variant):primitiveMesh()', 'View(int variant,const point* apOverride=nullptr):primitiveMesh()')
cpp=cpp.replace('const auto& chosenPoints=variant==0?actualPoints:(variant==1?candidatePoints:alternatePoints);','const auto& chosenPoints=variant==0?actualPoints:candidatePoints;')
cpp=cpp.replace('fs.setSize(rr.size());own.setSize(rr.size());nei.setSize(internal.size());selected.resize(rr.size());',
'if(variant!=0 && apOverride){ps[movingCompact[0]]=apOverride[0];ps[movingCompact[1]]=apOverride[1];}\n  fs.setSize(rr.size());own.setSize(rr.size());nei.setSize(internal.size());selected.resize(rr.size());')
cpp=cpp[:cpp.index('int main()')]
cpp+='\n#define DCCART_FINITE_REPAIR_KERNEL_ONLY\n#include "dcCartFinitePointRepair.H"\n#include <fstream>\n#include <chrono>\n'
cpp+=r"""
namespace K=dcCartFiniteRepairKernel;
struct Metrics {std::vector<double>d,w,r,s,n;std::vector<label>di,fi;double minV=GREAT,minPyramid=GREAT,maxClosure=0,minTet=GREAT;bool valid=true,geometry=true;};
Metrics metrics(View&v,bool geometry){
 Metrics o;auto dets=primitiveMeshTools::cellDeterminant(v,Vector<label>::one,v.sf,v.selected);
 for(label c=0;c<v.cc.size();++c)if(v.complete.count(v.cellIDs[c])){
  const scalar d=dets()[c];o.di.push_back(v.cellIDs[c]);o.d.push_back(d);
  o.valid &= std::isfinite(d)&&std::isfinite(v.vol[c])&&v.vol[c]>0;
  o.minV=std::min(o.minV,v.vol[c]);
  if(geometry){vector cl(Zero);scalar area=0;
   for(label f:v.cells()[c]){scalar sg=v.own[f]==c?1:-1;cl+=sg*v.sf[f];area+=mag(v.sf[f]);o.minPyramid=std::min(o.minPyramid,sg*(v.sf[f]&(v.fc[f]-v.cc[c]))/3);}
   o.maxClosure=std::max(o.maxClosure,mag(cl)/(area+VSMALL));
  }
 }
 for(label f=0;f<v.fs.size();++f){
  if(std::find(capturedFaces.begin(),capturedFaces.end(),v.faceIDs[f])==capturedFaces.end())continue;
  label a=v.own[f],b=f<v.nei.size()?v.nei[f]:-1;
  double w=1,r=1,n=0,sk=0;
  if(b>=0){const scalar da=mag(v.sf[f]&(v.fc[f]-v.cc[a])),db=mag(v.sf[f]&(v.cc[b]-v.fc[f]));
   w=min(da,db)/(da+db+VSMALL);r=min(v.vol[a],v.vol[b])/(max(v.vol[a],v.vol[b])+VSMALL);
   const vector delta=v.cc[b]-v.cc[a];scalar co=(delta&v.sf[f])/(mag(delta)*mag(v.sf[f])+ROOTVSMALL);
   n=Foam::acos(max(scalar(-1),min(scalar(1),co)))*180/constant::mathematical::pi;
   sk=primitiveMeshTools::faceSkewness(v.fs,v.ps,v.fc,v.sf,f,v.cc[a],v.cc[b]);
  }else sk=primitiveMeshTools::boundaryFaceSkewness(v.fs,v.ps,v.fc,v.sf,f,v.cc[a]);
  o.fi.push_back(v.faceIDs[f]);o.w.push_back(w);o.r.push_back(r);o.s.push_back(sk);o.n.push_back(n);
  o.valid&=std::isfinite(w)&&std::isfinite(r)&&std::isfinite(sk)&&std::isfinite(n);
  if(geometry){scalar best=-GREAT;
   for(label base=0;base<v.fs[f].size();++base){scalar qm=GREAT;
    for(label j=1;j<v.fs[f].size()-1;++j){const point&p=v.ps[v.fs[f][base]],&q=v.ps[v.fs[f][(base+j)%v.fs[f].size()]],&rr=v.ps[v.fs[f][(base+j+1)%v.fs[f].size()]];
     qm=min(qm,-tetPointRef(p,q,rr,v.cc[a]).quality());if(b>=0)qm=min(qm,tetPointRef(p,q,rr,v.cc[b]).quality());}
    best=max(best,qm);}
   o.minTet=std::min(o.minTet,best);
  }
 }
 if(geometry){labelHashSet bad;v.concave(bad);for(label c=0;c<v.cc.size();++c)if(v.complete.count(v.cellIDs[c])&&bad.found(c))o.geometry=false;
 o.geometry &= o.minPyramid>0 && o.maxClosure<=1e-10 && o.minTet>Foam::sqr(SMALL);}
 return o;
}
unsigned badCount(const std::vector<double>&v,double t,bool upper=false){unsigned n=0;for(double x:v)n+=upper?x>t:x<t;return n;}
void summary(const char*tag,const Metrics&m){std::cout<<"{\"kind\":\""<<tag<<"\",\"D\":"<<badCount(m.d,.001)<<",\"W\":"<<badCount(m.w,.05)<<",\"R\":"<<badCount(m.r,.01)<<",\"S\":"<<badCount(m.s,4,true)<<",\"N\":"<<badCount(m.n,70,true)<<",\"Dmin\":"<<*std::min_element(m.d.begin(),m.d.end())<<",\"Wmin\":"<<*std::min_element(m.w.begin(),m.w.end())<<",\"Rmin\":"<<*std::min_element(m.r.begin(),m.r.end())<<",\"Smax\":"<<*std::max_element(m.s.begin(),m.s.end())<<",\"Nmax\":"<<*std::max_element(m.n.begin(),m.n.end())<<",\"geometry\":"<<(m.geometry?"true":"false")<<"}\n";}
int main(int argc,char**argv){
 std::cout<<std::setprecision(17);
 const double limit=argc>1?std::stod(argv[1]):60;auto start=std::chrono::steady_clock::now();
 auto available=[&](){return std::chrono::duration<double>(std::chrono::steady_clock::now()-start).count()<limit;};
 View before(0),seed(1);auto old=metrics(before,true),initial=metrics(seed,true);summary("old",old);summary("finite-seed",initial);seed.print("finite-seed-full");
 unsigned counts[2]={0,0};for(unsigned i=0;i<initial.d.size();++i)for(int l=0;l<2;++l)if(std::find(lobeCellIDs[l].begin(),lobeCellIDs[l].end(),initial.di[i])!=lobeCellIDs[l].end())counts[l]+=initial.d[i]<.001;
 int chosen=counts[0]>0&&counts[1]==0?0:counts[1]>0&&counts[0]==0?1:-1;
 std::cout<<"{\"kind\":\"selector\",\"badDByLobe\":["<<counts[0]<<","<<counts[1]<<"],\"chosenLobe\":"<<chosen<<"}\n";
 if(chosen<0){std::cout<<"{\"kind\":\"refusal\",\"reason\":\"sole-failing-lobe-grammar\"}\n";return 0;}
 const point ap[2]={seed.ps[movingCompact[0]],seed.ps[movingCompact[1]]};
 vector lo(GREAT,GREAT,GREAT),hi(-GREAT,-GREAT,-GREAT);
 for(const point&p:candidatePoints)for(direction k=0;k<3;++k){lo[k]=min(lo[k],p[k]);hi[k]=max(hi[k],p[k]);}
 const scalar scale=max(hi.x()-lo.x(),max(hi.y()-lo.y(),hi.z()-lo.z()));
 const double nCeiling=*std::max_element(old.n.begin(),old.n.end());
 auto evaluate=[&](const K::Row&q){
  K::Evaluation e;if(q.size()!=3||!K::finite(q))return e;
  point a[2]={ap[0],ap[1]};for(direction k=0;k<3;++k)a[chosen][k]+=scale*q[k];
  View v(1,a);auto m=metrics(v,false);if(!m.valid)return e;
  e.objective=0;e.hardDW=badCount(m.d,.001)+badCount(m.w,.05);
  for(unsigned i=0;i<m.d.size();++i){if(newCore.count(m.di[i]))e.objective+=std::exp(-m.d[i]/.001);e.constraints.push_back((m.d[i]-.001)/.001);}
  for(unsigned i=0;i<m.w.size();++i){
   e.objective+=.25*std::exp(-m.w[i]/.05)+.1*std::exp(-m.r[i]/.01);
   e.constraints.push_back((m.w[i]-.05)/.05);e.constraints.push_back((m.r[i]-.01)/.01);
   e.constraints.push_back((4-m.s[i])/4);e.constraints.push_back((nCeiling-m.n[i])/70);
  }
  e.valid=std::isfinite(e.objective)&&K::finite(e.constraints);return e;
 };
 auto search=K::propose(3,evaluate,available,K::ObjectiveKind::DWeightCount);
 std::cout<<"{\"kind\":\"search\",\"requests\":"<<search.requests<<",\"iterations\":"<<search.iterations<<",\"projectionUpdates\":"<<search.projectionUpdates<<",\"improved\":"<<(search.improved?"true":"false")<<",\"bestHardImproved\":"<<(search.bestHardImproved?"true":"false")<<",\"requestCap\":"<<(search.stoppedByRequestCap?"true":"false")<<",\"timeBudget\":"<<(search.stoppedByTimeBudget?"true":"false")<<",\"parameters\":[";
 for(unsigned i=0;i<search.parameters.size();++i){if(i)std::cout<<',';std::cout<<search.parameters[i];}std::cout<<"]}\n";
 bool passed=false;
 for(int checkpoint=0;checkpoint<2&&!passed;++checkpoint){
  if(checkpoint && !search.bestHardImproved)break;const auto&q=checkpoint?search.bestHardParameters:search.parameters;
  for(double fraction:{1.,.95,.5,.25}){
   if(!available()){std::cout<<"{\"kind\":\"refusal\",\"reason\":\"final-budget\"}\n";return 0;}
   point a[2]={ap[0],ap[1]};for(direction k=0;k<3;++k)a[chosen][k]+=fraction*scale*q[k];
   View v(1,a);auto m=metrics(v,true);
   bool hard=m.valid&&m.geometry&&badCount(m.d,.001)==0&&badCount(m.w,.05)==0&&badCount(m.r,.01)==0&&badCount(m.s,4,true)==0&&badCount(m.n,70,true)<=badCount(old.n,70,true)&&*std::max_element(m.n.begin(),m.n.end())<=nCeiling;
   std::cout<<"{\"kind\":\"final-trial\",\"fraction\":"<<fraction<<",\"checkpoint\":"<<checkpoint<<",\"hardPass\":"<<(hard?"true":"false")<<",\"apices\":[["<<a[0].x()<<','<<a[0].y()<<','<<a[0].z()<<"],["<<a[1].x()<<','<<a[1].y()<<','<<a[1].z()<<"]]}\n";
   summary("final-trial-summary",m);
   if(hard){v.print("finite-qualified-full");passed=true;break;}
  }
 }
 std::cout<<"{\"kind\":\"result\",\"qualifiedInstalledHard\":"<<(passed?"true":"false")<<",\"kernelSeconds\":"<<std::chrono::duration<double>(std::chrono::steady_clock::now()-start).count()<<"}\n";
}
"""
(here/"installed_finite_replay.cpp").write_text(cpp)
(here/"input-bindings.json").write_text(json.dumps(bindings,indent=2)+"\n")
cmd=json.loads(read(frozen/"typed/build-command.json"))
cmd=[str(here/"installed_finite_replay.cpp")if x.endswith("/typed/installed_kernel.cpp")else str(here/"installed-finite-replay")if x.endswith("/typed/installed-kernel")else x for x in cmd]
(here/"build-command.json").write_text(json.dumps(cmd,indent=2)+"\n")
env=dict(os.environ,CUDA_VISIBLE_DEVICES="",OMP_NUM_THREADS="1",OPENBLAS_NUM_THREADS="1",MKL_NUM_THREADS="1",TMPDIR=str(here/"tmp"),WM_PROJECT_DIR="/mnt/lv/home/litianyu/miniconda3/envs/dcdtuni")
t=time.monotonic();r=subprocess.run(cmd,cwd=here,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=remaining())
(here/"build.log").write_bytes(r.stdout)
timing={"totalDeadlineSeconds":90,"seedAndGenerationSeconds":t-start,"buildSeconds":time.monotonic()-t,"buildReturncode":r.returncode}
if r.returncode:
    timing["actualSeconds"]=time.monotonic()-start;(here/"timing.json").write_text(json.dumps(timing,indent=2)+"\n")
    print("build first-red",r.returncode);sys.exit(r.returncode)
cmd=["taskset","-c","8-15",str(here/"installed-finite-replay"),str(max(0.,remaining()-1))]
(here/"run-command.json").write_text(json.dumps(cmd,indent=2)+"\n")
t=time.monotonic();r=subprocess.run(cmd,cwd=here,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=remaining())
(here/"installed-finite-replay.jsonl").write_bytes(r.stdout);(here/"run.stderr").write_bytes(r.stderr)
timing.update(runSeconds=time.monotonic()-t,runReturncode=r.returncode,actualSeconds=time.monotonic()-start)
(here/"timing.json").write_text(json.dumps(timing,indent=2)+"\n")
print(json.dumps(timing));print("\n".join(x for x in r.stdout.decode().splitlines()if "full" not in x))
