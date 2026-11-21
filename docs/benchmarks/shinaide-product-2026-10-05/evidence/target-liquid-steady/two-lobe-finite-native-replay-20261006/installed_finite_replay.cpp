#include "primitiveMesh.H"
#include "primitiveMeshTools.H"
#include "tetPointRef.H"
#include "bitSet.H"
#include "mathematicalConstants.H"
#include <vector>
#include <set>
#include <map>
#include <algorithm>
#include <iostream>
#include <iomanip>
using namespace Foam;
struct Raw {label id,own,nei;std::vector<label> ring;bool selected;};
#include "actual_fixture.H"
struct View : primitiveMesh {
 pointField ps;faceList fs;labelList own,nei;std::vector<label> cellIDs,faceIDs;
 bitSet selected;vectorField sf,fc,cc;scalarField vol;std::set<label> complete;

 View(int variant,const point* apOverride=nullptr):primitiveMesh() {
  const bool merged=variant!=0;
  cellIDs=merged?candidateCells:actualCells;
  complete.insert(cellIDs.begin(),cellIDs.end());
  for(const auto&r:(merged?candidateFaces:actualFaces)){cellIDs.push_back(r.own);if(r.nei>=0)cellIDs.push_back(r.nei);}
  std::sort(cellIDs.begin(),cellIDs.end());cellIDs.erase(std::unique(cellIDs.begin(),cellIDs.end()),cellIDs.end());
  std::map<label,label> cm;for(label i=0;i<label(cellIDs.size());++i)cm[cellIDs[i]]=i;
  std::vector<Raw> internal,boundary;
  for(auto r:(merged?candidateFaces:actualFaces)) {
   
   
   const bool a=cm.count(r.own),b=cm.count(r.nei);if(!a&&!b)continue;
   if(a&&b) {if(r.own>r.nei) {std::swap(r.own,r.nei);std::reverse(r.ring.begin()+1,r.ring.end());}internal.push_back(r);}
   else {if(!a){r.own=r.nei;std::reverse(r.ring.begin()+1,r.ring.end());}r.nei=-1;boundary.push_back(r);}
  }
  auto order=[](const Raw&a,const Raw&b){return a.id<b.id;};
  std::sort(internal.begin(),internal.end(),order);std::sort(boundary.begin(),boundary.end(),order);
  auto rr=internal;rr.insert(rr.end(),boundary.begin(),boundary.end());
  const auto& chosenPoints=variant==0?actualPoints:candidatePoints;
  ps.setSize(chosenPoints.size());for(label i=0;i<ps.size();++i)ps[i]=chosenPoints[i];
  if(variant!=0 && apOverride){ps[movingCompact[0]]=apOverride[0];ps[movingCompact[1]]=apOverride[1];}
  fs.setSize(rr.size());own.setSize(rr.size());nei.setSize(internal.size());selected.resize(rr.size());
  for(label i=0;i<label(rr.size());++i){const Raw&r=rr[i];faceIDs.push_back(r.id);
   fs[i].setSize(r.ring.size());for(label j=0;j<fs[i].size();++j)fs[i][j]=r.ring[j];
   own[i]=cm.at(r.own);if(i<nei.size())nei[i]=cm.at(r.nei);if(r.selected)selected.set(i);
  }
  reset(ps.size(),nei.size(),fs.size(),cellIDs.size());
  primitiveMeshTools::makeFaceCentresAndAreas(fs,ps,fc,sf);
  cc.setSize(cellIDs.size());vol.setSize(cellIDs.size());
  primitiveMeshTools::makeCellCentresAndVols(*this,fc,sf,cc,vol);
 }
 const pointField& points()const override{return ps;}
 const pointField& oldPoints()const override{return ps;}
 const faceList& faces()const override{return fs;}
 const labelList& faceOwner()const override{return own;}
 const labelList& faceNeighbour()const override{return nei;}
 bool concave(labelHashSet& s)const{return primitiveMesh::checkConcaveCells(sf,fc,false,&s);}
 void print(const char* tag) {
  auto dt=primitiveMeshTools::cellDeterminant(*this,Vector<label>::one,sf,selected);
  labelHashSet bad;const bool rawFailure=concave(bad);bool failure=false;for(label i=0;i<label(cellIDs.size());++i)if(complete.count(cellIDs[i])&&bad.found(i))failure=true;
  label ki=label(std::find(cellIDs.begin(),cellIDs.end(),keepCell)-cellIDs.begin());
  std::cout<<"{\"kind\":\""<<tag<<"\",\"stockConcaveAny\":"<<(failure?"true":"false")
   <<",\"keepCellStockConcave\":"<<(bad.found(ki)?"true":"false")<<",\"keepD\":";
  if(ki<dt().size())std::cout<<dt()[ki];else std::cout<<"null";
  std::cout<<",\"keepV\":";
  if(ki<vol.size())std::cout<<vol[ki];else std::cout<<"null";
  std::cout<<",\"cells\":[";
  bool firstCell=true;for(label i=0;i<cc.size();++i){if(!complete.count(cellIDs[i]))continue;
   vector closure=vector::zero;scalar areaSum=0,minPyramid=GREAT;
   for(const label f:cells()[i]){const scalar sign=(own[f]==i?1:-1);closure+=sign*sf[f];areaSum+=mag(sf[f]);minPyramid=min(minPyramid,sign*(sf[f]&(fc[f]-cc[i]))/3);}
   if(!firstCell)std::cout<<',';firstCell=false;std::cout<<"{\"id\":"<<cellIDs[i]<<",\"D\":"<<dt()[i]
   <<",\"V\":"<<vol[i]<<",\"CC\":["<<cc[i].x()<<','<<cc[i].y()<<','<<cc[i].z()<<"],\"relativeClosure\":"<<mag(closure)/(areaSum+VSMALL)<<",\"minPyramidV\":"<<minPyramid<<",\"concave\":"<<(bad.found(i)?"true":"false")<<'}';}
  std::cout<<"],\"capturedMetrics\":[";
  bool first=true;
  for(label f=0;f<fs.size();++f) {
   if(std::find(capturedFaces.begin(),capturedFaces.end(),faceIDs[f])==capturedFaces.end())continue;
   if(!first)std::cout<<',';first=false;
   const label a=own[f];const bool internal=f<nei.size();
   const label b=internal?nei[f]:-1;
   scalar w=1,r=1,n=0,skew=0;
   if(internal){
    const scalar da=mag(sf[f]&(fc[f]-cc[a])),db=mag(sf[f]&(cc[b]-fc[f]));
    w=min(da,db)/(da+db+VSMALL);r=min(vol[a],vol[b])/(max(vol[a],vol[b])+VSMALL);
    const vector delta=cc[b]-cc[a];
    const scalar cosine=(delta&sf[f])/(mag(delta)*mag(sf[f])+ROOTVSMALL);
    n=Foam::acos(Foam::max(scalar(-1),Foam::min(scalar(1),cosine)))*180.0/constant::mathematical::pi;
    skew=primitiveMeshTools::faceSkewness(fs,ps,fc,sf,f,cc[a],cc[b]);
   } else { if(selected.test(f)) {std::cerr<<"incomplete captured endpoint\n";std::exit(2);}
    skew=primitiveMeshTools::boundaryFaceSkewness(fs,ps,fc,sf,f,cc[a]); }
   scalar tq=GREAT;
   for(label j=0;j<fs[f].size();++j){
    const point&p=ps[fs[f][j]],&q=ps[fs[f][(j+1)%fs[f].size()]];
    tq=min(tq,-tetPointRef(p,q,fc[f],cc[a]).quality());
    if(internal)tq=min(tq,tetPointRef(p,q,fc[f],cc[b]).quality());
   }
   scalar bestAnchor=-GREAT;
   for(label base=0;base<fs[f].size();++base){scalar qmin=GREAT;
    for(label j=1;j<fs[f].size()-1;++j){const point&p=ps[fs[f][base]],&q=ps[fs[f][(base+j)%fs[f].size()]],&r=ps[fs[f][(base+j+1)%fs[f].size()]];
     qmin=min(qmin,-tetPointRef(p,q,r,cc[a]).quality());if(internal)qmin=min(qmin,tetPointRef(p,q,r,cc[b]).quality());}
    bestAnchor=max(bestAnchor,qmin);
   }
   std::cout<<"{\"face\":"<<faceIDs[f]<<",\"weight\":"<<w<<",\"ratio\":"<<r<<",\"skew\":"<<skew<<",\"N\":"<<n<<",\"Sf\":["<<sf[f].x()<<','<<sf[f].y()<<','<<sf[f].z()<<"],\"FC\":["<<fc[f].x()<<','<<fc[f].y()<<','<<fc[f].z()<<"],\"bestSharedBaseTetQuality\":"<<bestAnchor<<",\"minCentreFanTetQuality\":"<<tq<<'}';
  }
  std::cout<<"]}\n";
 }
};

#define DCCART_FINITE_REPAIR_KERNEL_ONLY
#include "dcCartFinitePointRepair.H"
#include <fstream>
#include <chrono>

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
