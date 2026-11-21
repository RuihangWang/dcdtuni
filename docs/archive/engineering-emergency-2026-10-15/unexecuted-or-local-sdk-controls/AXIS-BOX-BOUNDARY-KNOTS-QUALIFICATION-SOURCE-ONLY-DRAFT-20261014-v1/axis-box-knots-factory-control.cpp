#include "dcCartAxisBoxBoundaryKnotsProposal.H"
#include "dcCartRectilinearCompoundProposal.H"
#include <chrono>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <map>
#include <stdexcept>
using Foam::label;using Foam::scalar;using Foam::point;
namespace B=Foam::dcCartAxisBoxBoundaryKnotsProposal;
namespace R=Foam::dcCartRectilinearCompoundProposal;
struct Mesh
{
 std::vector<point> points;std::vector<std::vector<label>> faces;
 std::vector<label> owner,neighbour,patch,zone,cellRank;
 std::vector<unsigned char> faceAlive,cellAlive;label nCells=0;
};
struct Input
{
 Mesh raw;label rank=-1,parent=-1;std::vector<label> cf,pointIds;
 std::vector<std::vector<B::Plane>> arms;
};
static void need(bool x,const char* why){if(!x)throw std::runtime_error(why);}
static scalar value(std::uint64_t b){scalar x;std::memcpy(&x,&b,8);return x;}
static void sizeMesh(Input& d,std::size_t np,std::size_t nf,label nc)
{
 need(np>0&&np<10000000&&nf>0&&nf<10000000&&nc>0&&nc<10000000,"sparse-array-bound");
 d.raw.points.resize(np,point::zero);d.raw.faces.resize(nf);
 d.raw.owner.resize(nf,-1);d.raw.neighbour.resize(nf,-1);
 d.raw.patch.resize(nf,-1);d.raw.zone.resize(nf,-1);d.raw.faceAlive.resize(nf,0);
 d.raw.nCells=nc;d.raw.cellAlive.resize(nc,0);d.raw.cellRank.resize(nc,-1);
 need(d.parent>=0&&d.parent<nc&&d.rank>=0,"captured-cell-domain");
 d.raw.cellAlive[d.parent]=1;d.raw.cellRank[d.parent]=d.rank;
}
static std::vector<B::Plane> planes(std::istream& in,std::size_t count)
{
 need(count<=64,"planes-bound");std::vector<B::Plane> out;
 for(std::size_t i=0;i<count;++i){label a;std::uint64_t b;in>>a>>b;
  need(bool(in)&&a>=0&&a<3&&std::isfinite(value(b)),"plane");out.emplace_back(a,value(b));}
 return out;
}
static Input readBox(const char* file)
{
 std::ifstream in(file);std::string magic;Input d;std::size_t nrp,nrf,np,nf,ni,ne;label nc;
 in>>magic>>d.rank>>d.parent>>nrp>>nrf>>nc>>np>>nf>>ni>>ne;
 need(bool(in)&&magic=="DCTWIN_CAPTURED_AXIS_BOX_KNOTS_V1"&&np>=8&&np<=512&&nf>=6&&nf<=64&&ni>0&&ni<=64&&ne>0&&ne<=64,"box-contract");
 sizeMesh(d,nrp,nrf,nc);std::set<label> points;
 for(std::size_t i=0;i<np;++i){label id;std::uint64_t b[3];in>>id>>b[0]>>b[1]>>b[2];
  need(bool(in)&&id>=0&&std::size_t(id)<nrp&&points.insert(id).second,"point-identity");
  point p(value(b[0]),value(b[1]),value(b[2]));need(B::S::finite(p),"point-finite");d.raw.points[id]=p;d.pointIds.push_back(id);}
 std::set<label> faces,used;std::map<label,label> masks;
 for(std::size_t i=0;i<nf;++i){label f,source,o,n,p,z,mask;std::size_t count;in>>f>>source>>o>>n>>p>>z>>mask>>count;
  need(bool(in)&&f>=0&&std::size_t(f)<nrf&&source==f&&o>=0&&o<nc&&n>=-1&&n<nc&&o!=n&&(o==d.parent)!=(n==d.parent)&&p>=-1&&z>=-1&&mask>=0&&mask<=1&&count>=3&&count<=64&&faces.insert(f).second,"face-identity");
  std::vector<label> ring;for(std::size_t k=0;k<count;++k){label id;in>>id;need(bool(in)&&points.count(id),"face-point");ring.push_back(id);used.insert(id);}
  d.raw.faces[f]=ring;d.raw.owner[f]=o;d.raw.neighbour[f]=n;d.raw.patch[f]=p;d.raw.zone[f]=z;d.raw.faceAlive[f]=1;d.cf.push_back(f);
  if(p>=0){auto at=masks.emplace(p,mask);need(at.second||at.first->second==mask,"patch-mask");}
 }
 need(used==points,"complete-point-closure");d.arms.push_back(planes(in,ni));d.arms.push_back(planes(in,ne));
 std::string extra;need(!(in>>extra),"trailing-box-input");return d;
}
// Old L format is unchanged. Preserve literal native point/face/endpoint IDs;
// only absent array entries are unavailable sparse control placeholders.
static Input readOldL(const char* file)
{
 struct F{label id,source,o,n,p,z;std::vector<label> ring;};
 std::ifstream in(file);std::string magic;Input d;std::size_t np,nf,na;d.rank=0;
 in>>magic>>d.parent>>np>>nf>>na;need(bool(in)&&magic=="DCTWIN_CAPTURED_RECTILINEAR_COMPOUND_V1"&&np>=8&&np<=512&&nf>=6&&nf<=64&&na>4&&na<=16,"L-contract");
 std::map<label,point> pts;label maxp=-1,maxf=-1,maxc=d.parent;
 for(std::size_t i=0;i<np;++i){label id;std::uint64_t b[3];in>>id>>b[0]>>b[1]>>b[2];point p(value(b[0]),value(b[1]),value(b[2]));
  need(bool(in)&&id>=0&&B::S::finite(p)&&pts.emplace(id,p).second,"L-point");maxp=std::max(maxp,id);d.pointIds.push_back(id);}
 std::vector<F> fs;
 for(std::size_t i=0;i<nf;++i){F f;label mask;std::size_t count;in>>f.id>>f.source>>f.o>>f.n>>f.p>>f.z>>mask>>count;
  need(bool(in)&&f.id>=0&&f.source>=-1&&f.o>=0&&f.n>=-1&&f.o!=f.n&&(f.o==d.parent)!=(f.n==d.parent)&&count>=3&&count<=64&&mask>=0&&mask<=1,"L-face");
  for(std::size_t k=0;k<count;++k){label id;in>>id;need(bool(in)&&pts.count(id),"L-face-point");f.ring.push_back(id);}
  maxf=std::max(maxf,f.id);maxc=std::max(maxc,std::max(f.o,f.n));fs.push_back(f);}
 need(maxp<std::numeric_limits<label>::max()&&maxf<std::numeric_limits<label>::max()&&maxc<std::numeric_limits<label>::max(),"L-array-bound");
 sizeMesh(d,std::size_t(maxp)+1,std::size_t(maxf)+1,maxc+1);
 for(const auto& q:pts)d.raw.points[q.first]=q.second;
 for(const auto& f:fs){need(!d.raw.faceAlive[f.id],"L-duplicate-face");d.raw.faces[f.id]=f.ring;d.raw.owner[f.id]=f.o;d.raw.neighbour[f.id]=f.n;d.raw.patch[f.id]=f.p;d.raw.zone[f.id]=f.z;d.raw.faceAlive[f.id]=1;d.cf.push_back(f.id);}
 for(std::size_t i=0;i<na;++i){std::size_t n;in>>n;need(bool(in)&&n>0&&n<=64,"L-plane-count");d.arms.push_back(planes(in,n));}
 std::string extra;need(!(in>>extra),"trailing-L-input");return d;
}
static std::vector<B::Plane> canonical(std::vector<B::Plane> a){std::sort(a.begin(),a.end());a.erase(std::unique(a.begin(),a.end()),a.end());return a;}
static void pout(const std::vector<B::Plane>& p){std::cout<<'[';for(std::size_t i=0;i<p.size();++i){if(i)std::cout<<',';std::cout<<'['<<p[i].first<<','<<B::bits(p[i].second)<<']';}std::cout<<']';}
int main(int argc,char**argv)
{
 static_assert(sizeof(scalar)==8&&sizeof(label)==4,"actual DP/int32 ABI");
 try
 {
  need(argc==5,"box-input box-sha old-L-input old-L-sha");
  const auto until=std::chrono::steady_clock::now()+std::chrono::seconds(20);
  const std::function<bool()> work=[&](){return std::chrono::steady_clock::now()<until;};
  std::cout<<std::boolalpha;
  Input box=readBox(argv[1]);B::Proposal b;R::Proposal old;
  std::string why,oldWhy;const bool oldPass=R::qualify(box.raw,box.parent,box.cf,box.rank,work,old,&oldWhy);
  const bool good=B::qualify(box.raw,box.parent,box.cf,box.rank,work,b,&why);
  const std::string qualifyWhy=why;std::vector<B::Plane> out;B::Plane middle{-1,0};
  const bool factory=good&&B::factoryPlanes(b,box.arms[0],work,out,&middle,&why);
  const bool expected=factory&&out==canonical(box.arms[1])&&middle.first==-1;
  std::cout<<"{\"record\":\"actualBoxKnotsFactory\",\"inputSHAExternalBinding\":"<<std::quoted(argv[2])
   <<",\"rawRoot\":"<<box.parent<<",\"rank\":"<<box.rank<<",\"sparseOriginalNativeIdentityArrays\":true"
   <<",\"oldRectilinearQualified\":"<<oldPass<<",\"oldRectilinearReason\":"<<std::quoted(oldWhy)
   <<",\"newBoxQualified\":"<<good<<",\"qualifyReason\":"<<std::quoted(qualifyWhy)
   <<",\"factoryCalled\":"<<good<<",\"factoryReturned\":"<<factory<<",\"factoryReason\":"<<std::quoted(why)
   <<",\"literalPlaneSetMatchesRegistered\":"<<expected<<",\"factoryPlanes\":";pout(out);
  std::cout<<",\"midpointAxis\":"<<middle.first<<",\"boundaryTiles\":[";
  for(std::size_t i=0;i<b.boundaryTiles.size();++i){const auto&t=b.boundaryTiles[i];if(i)std::cout<<',';std::cout<<"{\"axis\":"<<t.axis<<",\"side\":"<<t.side<<",\"u\":"<<t.u<<",\"v\":"<<t.v<<",\"originalNativeFace\":"<<t.native<<'}';}
  std::cout<<"],\"oldKnots\":[";
  for(std::size_t i=0;i<b.oldKnots.size();++i){const auto&k=b.oldKnots[i];if(i)std::cout<<',';std::cout<<"{\"axis\":"<<k.plane.first<<",\"bits\":"<<B::bits(k.plane.second)<<",\"oldNativeNodes\":[";for(std::size_t j=0;j<k.oldNodes.size();++j){if(j)std::cout<<',';std::cout<<k.oldNodes[j];}std::cout<<"],\"incidentNativeFaces\":[";for(std::size_t j=0;j<k.nativeFaces.size();++j){if(j)std::cout<<',';std::cout<<k.nativeFaces[j];}std::cout<<"]}";}
  std::cout<<"],\"SourceAuthority\":false,\"peerAuthority\":false,\"fullStockAuthority\":false,\"meshAccepted\":false}\n";
  B::Proposal negative;std::string missingWhy,duplicateWhy,reversedWhy;
  auto missing=box.cf;missing.pop_back();
  const bool missingPass=B::qualify(box.raw,box.parent,missing,box.rank,work,negative,&missingWhy);
  auto duplicate=box.cf;duplicate.push_back(box.cf.front());
  const bool duplicatePass=B::qualify(box.raw,box.parent,duplicate,box.rank,work,negative,&duplicateWhy);
  auto& ring=box.raw.faces[box.cf.front()];std::reverse(ring.begin(),ring.end());
  const bool reversedPass=B::qualify(box.raw,box.parent,box.cf,box.rank,work,negative,&reversedWhy);
  std::reverse(ring.begin(),ring.end());
  std::vector<B::Plane> unused;std::string emptyWhy,outsideWhy;
  const bool emptyPass=good&&B::factoryPlanes(b,{},work,unused,nullptr,&emptyWhy);
  const bool outsidePass=good&&B::factoryPlanes(b,{{0,b.lo[0]}},work,unused,nullptr,&outsideWhy);
  std::cout<<"{\"record\":\"registeredNegativeControls\",\"missingFaceAccepted\":"<<missingPass<<",\"missingFaceReason\":"<<std::quoted(missingWhy)
   <<",\"duplicateFaceAccepted\":"<<duplicatePass<<",\"duplicateFaceReason\":"<<std::quoted(duplicateWhy)
   <<",\"reversedFaceAccepted\":"<<reversedPass<<",\"reversedFaceReason\":"<<std::quoted(reversedWhy)
   <<",\"emptyActualIncomingAccepted\":"<<emptyPass<<",\"emptyReason\":"<<std::quoted(emptyWhy)
   <<",\"nonInteriorIncomingAccepted\":"<<outsidePass<<",\"nonInteriorReason\":"<<std::quoted(outsideWhy)
   <<",\"theseAreIsolatedGeometryMutationsNotActualProductionFailures\":true,\"SourceAuthority\":false,\"meshAccepted\":false}\n";
  Input l=readOldL(argv[3]);R::Proposal lp;B::Proposal notBox;std::string lw,bw;std::vector<R::Plane> lo;R::Plane lm{-1,0};
  const bool lq=R::qualify(l.raw,l.parent,l.cf,l.rank,work,lp,&lw);
  const bool lf=lq&&R::factoryPlanes(lp,l.arms[0],work,lo,&lm,&lw);
  const bool le=lf&&lo==canonical(l.arms[4]);
  const bool lb=B::qualify(l.raw,l.parent,l.cf,l.rank,work,notBox,&bw);
  std::cout<<"{\"record\":\"originalLBranchHeld\",\"inputSHAExternalBinding\":"<<std::quoted(argv[4])<<",\"rawRoot\":"<<l.parent
   <<",\"oldQualified\":"<<lq<<",\"oldFactory\":"<<lf<<",\"oldReason\":"<<std::quoted(lw)<<",\"oldPlanesMatchPreviouslyRegisteredArm4\":"<<le
   <<",\"oldPlanes\":";pout(lo);std::cout<<",\"newBoxFamilyQualified\":"<<lb<<",\"newBoxReason\":"<<std::quoted(bw)
   <<",\"SourceAuthority\":false,\"peerAuthority\":false,\"fullStockAuthority\":false,\"meshAccepted\":false}\n";
  const bool pass=!oldPass&&oldWhy=="rectilinear-no-internal-support"&&good&&expected&&!missingPass&&!duplicatePass&&!reversedPass&&!emptyPass&&!outsidePass&&lq&&lf&&le&&!lb&&work();
  std::cout<<"{\"record\":\"summary\",\"allRegisteredLocalCriteria\":"<<pass<<",\"clockAvailable\":"<<work()
   <<",\"QNotCalled\":true,\"CompoundNotCalled\":true,\"Source16NotCalled\":true,\"nativePermission\":false,\"meshAccepted\":false}\n";
  return pass?0:2;
 }
 catch(const std::exception& e){std::cerr<<"UNKNOWN local input/API refusal: "<<e.what()<<'\n';return 2;}
}
