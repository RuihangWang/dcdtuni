#include "dcCartAxisBoxBoundaryKnotsProposal.H"
#include "dcCartRawUnionStockMetrics.H"
#include "dcCartSharedAxisSeamVolumePartition.H"
#include "dcCartSharedAxisColumnCompound.H"
#include "dcCartRetainedPlanarBaseStockTets.H"
#include "dcCartOriginalNodePositiveEarFacets.H"
#include <chrono>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <set>
#include <sstream>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>
using Foam::label;using Foam::scalar;using Foam::point;using Foam::vector;
namespace Q=Foam::dcCartRawUnionStockMetrics;
namespace S=Foam::dcCartSharedAxisSeamVolumePartition;
namespace Tets=Foam::dcCartRetainedPlanarBaseStockTets;
namespace I=Foam::dcCartOriginalNodePositiveEarFacets;
namespace C=Foam::dcCartSharedAxisColumnCompound;
namespace F=Foam::dcCartFiniteTensorCutScope;
namespace G=dcCartSourcePlanarConeGroups;
namespace B=Foam::dcCartAxisBoxBoundaryKnotsProposal;
static std::chrono::steady_clock::time_point deadline;
static bool work(){return std::chrono::steady_clock::now()<deadline;}
static void need(bool ok,const char* s){if(!ok)throw std::runtime_error(s);}
static scalar value(std::uint64_t n){scalar x;std::memcpy(&x,&n,8);return x;}
static std::uint64_t bits(scalar x){std::uint64_t n;std::memcpy(&n,&x,8);return n;}
static bool healthy(const Q::Cell& q){return q.finite&&q.strictGeometry&&std::isfinite(q.determinant)&&q.determinant>=.001&&std::isfinite(q.aspect)&&q.aspect<=1000;}
static void number(scalar x){if(std::isfinite(x))std::cout<<x;else std::cout<<"null";}
static void qout(const Q::Cell& q){
 std::cout<<"{\"finite\":"<<q.finite<<",\"strictGeometry\":"<<q.strictGeometry<<",\"healthyCDAR\":"<<healthy(q)<<",\"D\":";number(q.determinant);
 std::cout<<",\"DBits\":"<<bits(q.determinant)<<",\"AR\":";number(q.aspect);std::cout<<",\"ARBits\":"<<bits(q.aspect)<<",\"VBits\":"<<bits(q.volume)<<",\"CCBits\":["<<bits(q.centre.x())<<','<<bits(q.centre.y())<<','<<bits(q.centre.z())<<"],\"reason\":";
 if(q.reason)std::cout<<std::quoted(q.reason);else std::cout<<"null";std::cout<<'}';
}
struct Mesh{std::vector<point> points;std::vector<std::vector<label>> faces;std::vector<label> owner,neighbour,patch,zone,cellRank;std::vector<unsigned char> faceAlive,cellAlive;label nCells=1;};
struct Measurement{Q::Cell plain,repaired;S::Geometry geometry;bool s=false,tetsKnown=false,tets=false;};
static Measurement measure(label c,const std::vector<Q::Face>& shell,const Mesh& mesh,
 const Q::CoupledTypePatch& coupled,const point& reference){
 S::Plan empty;empty.ready=true;
 const Q::FaceOF metric=[&](const std::vector<label>& r,vector& a,point& p){S::metric(mesh,empty,r,a,p);};
 Measurement m;m.plain=Q::measure(c,shell,mesh.points,metric,coupled,work,true,false);
 m.repaired=Q::measure(c,shell,mesh.points,metric,coupled,work,true,true);
 std::vector<S::Face> faces;for(const auto& f:shell)faces.push_back({f.id,f.ring,f.owner==c});
 m.s=S::geometry(mesh,empty,faces,reference,work,m.geometry);
 m.tetsKnown=m.plain.centres.size()==shell.size()&&Q::finitePoint(m.plain.centre)&&std::isfinite(m.plain.volume)&&m.plain.volume>0&&work();m.tets=m.tetsKnown;
 if(m.tetsKnown)for(std::size_t j=0;j<shell.size();++j){
  std::vector<point> polygon;for(label p:shell[j].ring)polygon.push_back(mesh.points.at(p));
  if(shell[j].owner!=c)std::reverse(polygon.begin(),polygon.end());
  m.tets=Tets::accept(polygon,m.plain.centres[j],m.plain.centre,nullptr,work,nullptr)&&m.tets;
 }
 m.tetsKnown=m.tetsKnown&&work();return m;
}
static void mout(const Measurement& m){
 std::cout<<"{\"plainStrictQ\":";qout(m.plain);std::cout<<",\"repairedStrictQ\":";qout(m.repaired);
 std::cout<<",\"SGeometryReturned\":"<<m.s<<",\"motherSideTetsKnown\":"<<m.tetsKnown<<",\"motherSideTetsPassed\":";
 if(m.tetsKnown)std::cout<<m.tets;else std::cout<<"null";
 std::cout<<",\"physicalVM\":";
 if(m.s){std::cout<<'[';for(int k=0;k<4;++k){if(k)std::cout<<',';std::ostringstream text;text<<std::setprecision(std::numeric_limits<long double>::max_digits10)<<m.geometry.physical[k];std::cout<<std::quoted(text.str());}std::cout<<']';}else std::cout<<"null";
 std::cout<<'}';
}
struct Input
{
 Mesh raw;label rank=-1,parent=-1;std::vector<label> cf,pointIds;
 std::vector<std::vector<B::Plane>> arms;
};
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
static std::vector<B::Plane> canonical(std::vector<B::Plane> a){std::sort(a.begin(),a.end());a.erase(std::unique(a.begin(),a.end()),a.end());return a;}
static void pout(const std::vector<B::Plane>& p){std::cout<<'[';for(std::size_t i=0;i<p.size();++i){if(i)std::cout<<',';std::cout<<'['<<p[i].first<<','<<B::bits(p[i].second)<<']';}std::cout<<']';}
struct Source{label native=-1,origin=-1,owner=-1,neighbour=-1,patch=-1,zone=-1;I::Ring normalized;};
int main(int argc,char**argv){static_assert(sizeof(scalar)==8&&sizeof(label)==4,"actual DP/int32 ABI");try{
 need(argc==3,"usage actual-box-input.txt inputSHA-root-bound-externally");
 deadline=std::chrono::steady_clock::now()+std::chrono::seconds(20);
 std::cout<<std::boolalpha<<std::setprecision(17);
 const Input captured=readBox(argv[1]);const label actualParent=captured.parent;
 B::Proposal proposal;std::string factoryWhy;std::vector<B::Plane> requested;B::Plane midpoint{-1,0};
 const bool qualified=B::qualify(captured.raw,actualParent,captured.cf,captured.rank,work,proposal,&factoryWhy);
 const bool generated=qualified&&B::factoryPlanes(proposal,captured.arms[0],work,requested,&midpoint,&factoryWhy);
 const bool exact=generated&&requested==canonical(captured.arms[1])&&midpoint.first==-1;
 std::cout<<"{\"record\":\"actualBoxKnotsFactoryBinding\",\"inputSHAExternalBinding\":"<<std::quoted(argv[2])
  <<",\"actualParent\":"<<actualParent<<",\"qualified\":"<<qualified<<",\"factoryReturned\":"<<generated
  <<",\"factoryReason\":"<<std::quoted(factoryWhy)<<",\"planesMatchLiteralActualIncomingAndOldKnots\":"<<exact<<",\"factoryPlanes\":";
 pout(requested);std::cout<<",\"sixSideTileCount\":"<<proposal.boundaryTiles.size()<<",\"SourceAuthority\":false,\"peerKnown\":false,\"fullStockKnown\":false,\"meshAccepted\":false}\n";
 need(exact,"factory-or-registered-literal-plane-set");
 // Explicit isolated geometry frame. All seven actual full-CF polygons and
 // original patch/zone roles are retained. Original external endpoints are
 // normalized to outside=-1 so uncaptured neighbouring full CF is never faked.
 // The factory above used the literal native raw IDs and original tuples.
 Mesh raw;raw.cellRank={0};raw.cellAlive={1};raw.nCells=1;
 std::map<label,label> pointMap;for(label id:captured.pointIds){need(pointMap.emplace(id,raw.points.size()).second,"dense-point-map");raw.points.push_back(captured.raw.points.at(id));}
 const std::size_t np=raw.points.size(),nf=captured.cf.size();
 std::vector<Source> sources;std::vector<Q::Face> rawShell;
 std::map<label,bool> patchTypes;
 std::cout<<"{\"record\":\"actualSourceToIsolatedGeometryFrame\",\"privateParent\":0,\"externalEndpointsNormalizedToOutside\":true,\"noNeighbourCFInvented\":true,\"patchZoneAndQSelectedRoleHeld\":true,\"sources\":[";
 for(std::size_t j=0;j<nf;++j){const label f=captured.cf[j];Source s;s.native=f;s.origin=f;s.owner=captured.raw.owner[f];s.neighbour=captured.raw.neighbour[f];s.patch=captured.raw.patch[f];s.zone=captured.raw.zone[f];
  for(label id:captured.raw.faces[f])s.normalized.push_back(pointMap.at(id));
  if(s.owner!=actualParent)std::reverse(s.normalized.begin(),s.normalized.end());
  raw.faces.push_back(s.normalized);raw.owner.push_back(0);raw.neighbour.push_back(-1);
  raw.patch.push_back(s.patch);raw.zone.push_back(s.zone);raw.faceAlive.push_back(1);
  rawShell.emplace_back(j,0,-1,s.patch,s.zone,s.normalized);sources.push_back(s);
  if(s.patch>=0)patchTypes.emplace(s.patch,false); // actual input mask validation below
  if(j)std::cout<<',';std::cout<<"{\"privateRawFace\":"<<j<<",\"actualNativeSource\":"<<f<<",\"actualOwner\":"<<s.owner<<",\"actualNeighbour\":"<<s.neighbour<<",\"patch\":"<<s.patch<<",\"zone\":"<<s.zone<<'}';
 }
 std::cout<<"],\"SourceAuthority\":false,\"currentCohortKnown\":false,\"outsideFutureStockKnown\":false,\"meshAccepted\":false}\n";
 // readBox retains native tuple/PZ but not coupled permission. This input is
 // externally bound to actual patch0=patch and patch1=wall, both noncoupled.
 // Validate the masks literally from the same input before measuring any Q.
 {
  std::ifstream maskInput(argv[1]);std::string magic;label r,p,nc;std::size_t nrp,nrf,npt,nft,ni,ne;
  maskInput>>magic>>r>>p>>nrp>>nrf>>nc>>npt>>nft>>ni>>ne;
  for(std::size_t i=0;i<npt;++i){label id;std::uint64_t x,y,z;maskInput>>id>>x>>y>>z;}
  for(std::size_t i=0;i<nft;++i){label f,src,o,n,patch,zone,mask;std::size_t count;maskInput>>f>>src>>o>>n>>patch>>zone>>mask>>count;need(bool(maskInput)&&mask==0,"actual-noncoupled-mask-contract");for(std::size_t j=0;j<count;++j){label id;maskInput>>id;}}
  need(bool(maskInput),"mask-input-incomplete");
 }
 std::vector<std::vector<C::Plane>> arms{captured.arms[0],requested};
 bool allFactoryArm=false,allPrepared=true;
 const Q::CoupledTypePatch coupled=[&](label p){if(p<0)return false;const auto f=patchTypes.find(p);need(f!=patchTypes.end(),"uncaptured-mask");return f->second;};
 point reference=point::zero;for(const auto&p:raw.points)reference+=p;reference/=raw.points.size();
 const auto baseline=measure(0,rawShell,raw,coupled,reference);S::PointIndex index;need(S::pointIndex(raw,work,index),"actual-point-index");
 std::vector<std::vector<label>> cf(1);for(label f=0;f<label(nf);++f)cf[0].push_back(f);
 std::cout<<"{\"record\":\"rawRectilinearBaseline\",\"inputSHAExternalBinding\":"<<std::quoted(argv[2])<<",\"actualParent\":"<<actualParent<<",\"privateParent\":0,\"points\":"<<np<<",\"faces\":"<<nf<<",\"baseline\":";mout(baseline);
 std::cout<<",\"SourceAuthority\":false,\"peerKnown\":false,\"outsideKnown\":false,\"nativePermission\":false}\n";
 for(std::size_t arm=0;arm<arms.size();++arm){
  need(work(),"global-clock");S::Plan plan;std::string why;C::RootPlanes roots{{0,arms[arm]}},completed;C::SourceSeamCache cache;F::SurfaceHalo halo;
  const bool prepared=C::prepare(raw,cf,{0},arms[arm],0,index,[](label){return false;},[](label c){return c==0;},work,plan,&why,nullptr,nullptr,&roots,&completed,&cache,true,F::CutScope::FiniteTensor,&halo,[](label){return false;});
  std::cout<<"{\"record\":\"rectilinearCompoundArm\",\"arm\":"<<arm<<",\"requestedPlanes\":[";for(std::size_t j=0;j<arms[arm].size();++j){if(j)std::cout<<',';std::cout<<'['<<arms[arm][j].first<<','<<bits(arms[arm][j].second)<<']';}
  std::cout<<"],\"prepared\":"<<prepared<<",\"reason\":"<<std::quoted(why)<<",\"preserveConvexSourceRings\":true,\"isolatedCutCoupledNormalized\":true,\"QActualCoupledSelectionHeld\":true";
  if(!prepared){allPrepared=false;std::cout<<",\"state\":\"UNPREPARED_NO_CHILD_ACCEPTANCE\",\"SourceAuthority\":false,\"meshAccepted\":false}\n";continue;}
  need(plan.ready&&plan.cells.size()==1&&plan.cells[0].parent==0,"plan-parent-frame");
  Mesh staged;staged.points=raw.points;for(const auto& b:plan.births)staged.points.push_back(b.position);
  std::set<label> children;for(std::size_t k=0;k<S::partCount(plan.cells[0]);++k)need(children.insert(S::partCell(plan.cells[0],k)).second,"duplicate-child");
  need(children.size()>1&&children.size()<=64,"actual-child-cap");
  std::map<label,std::vector<Q::Face>> shells;std::map<label,std::vector<I::Ring>> groups;std::set<label> emitted;
  for(const auto& r:plan.rows){need(children.count(r.owner)&&r.neighbour>=-1&&(r.neighbour<0||children.count(r.neighbour)),"row-child-domain");for(label p:r.ring){need(p>=0&&std::size_t(p)<staged.points.size(),"row-point-domain");emitted.insert(p);}Q::Face f(r.native,r.owner,r.neighbour,r.patch,r.zone,r.ring);shells[r.owner].push_back(f);if(r.neighbour>=0)shells[r.neighbour].push_back(f);
   if(r.source>=0){need(std::size_t(r.source)<sources.size()&&r.patch==sources[r.source].patch&&r.zone==sources[r.source].zone&&r.neighbour==-1,"original-source-metadata");groups[r.source].push_back(r.ring);}else need(r.source==-1&&r.patch==-1&&r.zone==-1&&r.neighbour>=0,"actual-cap-domain");
  }
  bool nodes=emitted.size()==staged.points.size();for(label p=0;p<label(staged.points.size());++p)nodes=nodes&&emitted.count(p);
  std::cout<<",\"actualChildren\":"<<children.size()<<",\"actualBirths\":"<<plan.births.size()<<",\"allInputAndBirthNodesRetained\":"<<nodes<<",\"sourceFaceChecks\":[";
  bool source16=true,boundaries=true;
  for(std::size_t f=0;f<sources.size();++f){I::E::Context exact(work);I::Integrals total;std::vector<G::Ring> pieces;
   const auto found=groups.find(f);if(found!=groups.end())for(const auto& r:found->second){pieces.emplace_back(r.begin(),r.end());I::add(total,I::integrals(r,[&](label id)->const point&{return staged.points[id];},exact),exact);}
   const bool equal=found!=groups.end()&&I::same(I::integrals(sources[f].normalized,[&](label id)->const point&{return raw.points[id];},exact),total,exact)&&exact.good();
   G::Ring expected;const auto& original=sources[f].normalized;
   for(std::size_t j=0;j<original.size();++j){const label a=original[j],b=original[(j+1)%original.size()];expected.push_back(a);int axis=-1;for(int k=0;k<3;++k)if(raw.points[a][k]!=raw.points[b][k])axis=k;need(axis>=0,"original-edge-axis");std::vector<label> add;
    for(label p=0;p<label(staged.points.size());++p){bool same=true;for(int k=0;k<3;++k)if(k!=axis)same=same&&staged.points[p][k]==raw.points[a][k];const scalar x=staged.points[p][axis];if(same&&x>std::min(raw.points[a][axis],raw.points[b][axis])&&x<std::max(raw.points[a][axis],raw.points[b][axis]))add.push_back(p);}
    std::sort(add.begin(),add.end(),[&](label p,label q){return raw.points[a][axis]<raw.points[b][axis]?staged.points[p][axis]<staged.points[q][axis]:staged.points[p][axis]>staged.points[q][axis];});expected.insert(expected.end(),add.begin(),add.end());
   }
   G::Ring outer;const bool bound=!pieces.empty()&&G::Detail::outline(pieces,outer,exact)&&G::Detail::sameCycle(expected,outer)&&exact.good();source16=source16&&equal;boundaries=boundaries&&bound;
   if(f)std::cout<<',';std::cout<<"{\"privateRawFace\":"<<f<<",\"actualNativeFace\":"<<sources[f].native<<",\"actualOriginSource\":"<<sources[f].origin<<",\"geometric16Equal\":"<<equal<<",\"directedRefinedBoundaryHeld\":"<<bound<<",\"pieces\":"<<(found==groups.end()?0:found->second.size())<<'}';
  }
  std::cout<<"],\"childChecks\":[";bool allQ=true,allS=true,allTets=true;std::array<long double,4> sum{{0,0,0,0}};scalar volume=0;bool first=true;
  for(label c:children){need(work(),"global-clock");need(shells.count(c)&&shells[c].size()>=4&&shells[c].size()<=64,"complete-child-shell");const auto m=measure(c,shells[c],staged,coupled,reference);allQ=allQ&&healthy(m.plain)&&healthy(m.repaired);allS=allS&&m.s;allTets=allTets&&m.tetsKnown&&m.tets;if(m.s){volume+=m.geometry.volume;for(int k=0;k<4;++k)sum[k]+=m.geometry.physical[k];}
   if(!first)std::cout<<',';first=false;std::cout<<"{\"privateChild\":"<<c<<",\"faces\":"<<shells[c].size()<<",\"metrics\":";mout(m);std::cout<<'}';
  }
  bool vm=baseline.s&&allS;const scalar span=std::max(Foam::mag(baseline.geometry.hi-baseline.geometry.lo),scalar(1e-6));
  if(vm){vm=std::abs(volume-baseline.geometry.volume)/std::max(baseline.geometry.volume,scalar(Foam::VSMALL))<=1e-10;for(int k=0;k<4;++k)vm=vm&&std::abs(sum[k]-baseline.geometry.physical[k])/std::max(baseline.geometry.physical[0]*(k?span:1),static_cast<long double>(Foam::VSMALL))<=1e-10L;}
  const bool conjunction=source16&&boundaries&&nodes&&allQ&&allS&&allTets&&vm;
  if(arm==1)allFactoryArm=conjunction;
  std::cout<<"],\"allGeometric16Equal\":"<<source16<<",\"allDirectedBoundaryHeld\":"<<boundaries<<",\"allStrictQPass\":"<<allQ<<",\"allSGeometryReturned\":"<<allS<<",\"allMotherTetsPass\":"<<allTets<<",\"originalRawPhysicalVM1e10Held\":"<<vm<<",\"SourceAuthority\":false,\"currentCohortVMKnown\":false,\"outsideFutureStockKnown\":false,\"remotePeerKnown\":false,\"nativePermission\":false,\"meshAccepted\":false}\n";
 }
 std::cout<<"{\"record\":\"summary\",\"arms\":"<<arms.size()<<",\"bothArmsPrepared\":"<<allPrepared<<",\"newFactoryArmAllLocalConjuncts\":"<<allFactoryArm<<",\"clockAvailable\":"<<work()<<",\"SourceAuthority\":false,\"meshAccepted\":false}\n";return allFactoryArm&&work()?0:2;
 }catch(const std::exception&e){std::cerr<<"UNKNOWN input/API refusal: "<<e.what()<<'\n';return 2;}
}
