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

 View(int variant):primitiveMesh() {
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
  const auto& chosenPoints=variant==0?actualPoints:(variant==1?candidatePoints:alternatePoints);
  ps.setSize(chosenPoints.size());for(label i=0;i<ps.size();++i)ps[i]=chosenPoints[i];
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
int main(){std::cout<<std::setprecision(17);View before(0),cheby(1),best(2);before.print("old");cheby.print("chebyshev");best.print("maxmin");}
