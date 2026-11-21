#ifndef dcCartStrictLocalInertEdgeCleanup_H
#define dcCartStrictLocalInertEdgeCleanup_H
#include "dcCartOriginalNodePositiveEarFacets.H"
#include "dcCartPreparedFacetBirth.H"
#include <map>
#include <set>
#include <memory>
#include <fstream>
#include <sstream>
#include <iomanip>
#include <algorithm>
namespace Foam { namespace dcCartStrictLocalInertEdgeCleanup {
namespace E=dcCartExactBinary64Geometry;
namespace I=dcCartOriginalNodePositiveEarFacets;
namespace G=dcCartSourcePlanarConeGroups;
using Ring=std::vector<label>;
using Bits=std::array<std::uint64_t,3>;
inline Bits bits(const point& p) {
 Bits b{};for(unsigned k=0;k<3;++k)std::memcpy(&b[k],&p[k],8);return b;
}
struct Role {
 bool known=false,physical=false;
 label originalRegion=-1;
 std::string entity,region,sha,operation;
};
inline bool sameRole(const Role& a,const Role& b) {
 return a.known&&b.known&&a.entity==b.entity&&a.region==b.region
   &&a.sha==b.sha&&a.operation==b.operation;
}
struct Row {
 label face=-1,owner=-1,neighbour=-1,patch=-1,zone=-1;
 Ring before,after;Role role;
 std::vector<Bits> beforeBits;
};
struct Node {
 label point=-1,first=-1,second=-1;
 Bits pointBits{},firstBits{},secondBits{};
 std::vector<label> faces;
};
class FaceProof {
 Row row_;
 explicit FaceProof(Row r):row_(std::move(r)){}
 friend struct Builder;
public:
 const Row& row()const noexcept{return row_;}
 template<class Mesh,class Birth>
 bool continuation(const Mesh& m,label f,const Birth& creator)const {
  const Row& r=row_;
  if(f!=r.face||f<0||std::size_t(f)>=m.faces.size()||!m.faceAlive[f]
    ||m.owner[f]!=r.owner||m.neighbour[f]!=r.neighbour
    ||m.patch[f]!=r.patch||m.zone[f]!=r.zone||m.faces[f]!=r.after
    ||!r.role.known||!r.role.physical||r.zone<0
    ||creator.zone!=r.zone||creator.region!=r.role.originalRegion
    ||creator.orderedPointBits!=r.beforeBits||r.before.size()!=r.beforeBits.size())return false;
  for(std::size_t j=0;j<r.before.size();++j)
   if(r.before[j]<0||std::size_t(r.before[j])>=m.points.size()
     ||bits(m.points[r.before[j]])!=r.beforeBits[j])return false;
  return true;
 }
};
using Proofs=std::map<label,std::shared_ptr<const FaceProof>>;
struct Result {
 bool ready=false,published=false;
 std::string reason="not-prepared";
 std::size_t inspected=0,localCandidates=0,foreignOrCoupled=0,
   branchOrCorner=0,unknownRole=0,exactRefused=0;
 std::vector<Node> nodes;
 Proofs proofs;
};
struct Builder {
 template<class Mesh,class Coupled,class SourceRole>
 static Result prepare(const Mesh& m,label rank,Coupled coupled,SourceRole sourceRole,
   const std::function<bool()>& available) {
  Result out;
  const auto work=[&](){return !available||available();};
  const auto fail=[&](const char* why){out.ready=false;out.reason=why;return out;};
  try {
   if(!work()||m.nCells<0||m.cellAlive.size()!=std::size_t(m.nCells)
    ||m.cellRank.size()!=std::size_t(m.nCells)||m.owner.size()!=m.faces.size()
    ||m.neighbour.size()!=m.faces.size()||m.patch.size()!=m.faces.size()
    ||m.zone.size()!=m.faces.size()||m.faceAlive.size()!=m.faces.size())
      return fail("inert-shape-or-clock");
   std::vector<std::vector<label>> star(m.points.size()),cf(m.nCells);
   std::vector<Role> roles(m.faces.size());std::vector<unsigned char> safe(m.faces.size(),0);
   for(label f=0;f<label(m.faces.size());++f) {
    if(!work())return fail("inert-clock");
    if(!m.faceAlive[f])continue;
    const label o=m.owner[f],n=m.neighbour[f],p=m.patch[f];
    if(o<0||o>=m.nCells||!m.cellAlive[o]||n<-1||n>=m.nCells
      ||(n>=0&&(!m.cellAlive[n]||n==o)))return fail("inert-invalid-live-incidence");
    const auto& ring=m.faces[f];
    if(ring.size()<3||ring.size()>128)return fail("inert-ring-bound");
    std::set<label> unique;
    for(label q:ring) {
     if(q<0||std::size_t(q)>=m.points.size()||!unique.insert(q).second)
       return fail("inert-incomplete-point-star");
     star[q].push_back(f);
    }
    cf[o].push_back(f);if(n>=0)cf[n].push_back(f);
    const bool local=m.cellRank[o]==rank&&(n<0||m.cellRank[n]==rank);
    if(!local||coupled(p))continue;
    Role role;
    if(!sourceRole(f,role)||!role.known)continue;
    roles[f]=std::move(role);safe[f]=1;
   }
   std::vector<unsigned char> selected(m.points.size(),0);
   std::vector<std::pair<label,label>> segment(m.points.size(),{-1,-1});
   for(label q=0;q<label(star.size());++q) {
    if(!work())return fail("inert-clock");
    if(star[q].empty())continue;++out.inspected;
    // Existing exact-point pins are kept. Positions are never changed.
    if(!m.preparedExactPoints.empty()
      &&(m.preparedExactPoints.size()!=m.points.size()||m.preparedExactPoints[q]))continue;
    bool good=true,have=false,foreign=false,roleUnknown=false;Role physicalRole;
    std::pair<label,label> endpoints{-1,-1};
    E::Context exact(work,262144);
    const auto at=[&](label p){return E::vector({{m.points[p].x(),m.points[p].y(),m.points[p].z()}},exact);};
    for(label f:star[q]) {
     if(!safe[f]) {
      const label o=m.owner[f],n=m.neighbour[f];
      foreign=foreign||coupled(m.patch[f])||m.cellRank[o]!=rank||(n>=0&&m.cellRank[n]!=rank);
      roleUnknown=true;good=false;break;
     }
     const auto& ring=m.faces[f];
     if(ring.size()<4||ring.size()>64){good=false;break;}
     const auto found=std::find(ring.begin(),ring.end(),q);
     const std::size_t j=std::size_t(found-ring.begin());
     const label a=ring[(j+ring.size()-1)%ring.size()],b=ring[(j+1)%ring.size()];
     const std::pair<label,label> edge=std::minmax(a,b);
     if(a==b||a==q||b==q||(have&&edge!=endpoints)){good=false;break;}
     endpoints=edge;have=true;
     const auto left=E::difference(at(a),at(q),exact),right=E::difference(at(b),at(q),exact);
     const auto cross=E::cross(left,right,exact);
     if(E::sign(cross[0])||E::sign(cross[1])||E::sign(cross[2])
       ||E::sign(E::dot(left,right,exact))>=0){good=false;break;}
     if(roles[f].physical) {
      if(physicalRole.known&&!sameRole(physicalRole,roles[f])){good=false;roleUnknown=true;break;}
      physicalRole=roles[f];
     }
    }
    if(!good||!have||!exact.good()) {
     if(foreign)++out.foreignOrCoupled;
     else if(roleUnknown)++out.unknownRole;
     else ++out.branchOrCorner;
     continue;
    }
    selected[q]=1;segment[q]=endpoints;++out.localCandidates;
   }
   std::set<label> affectedCells;
   for(label f=0;f<label(m.faces.size());++f) {
    if(!work())return fail("inert-clock");
    if(!m.faceAlive[f])continue;bool change=false;
    for(label q:m.faces[f])change=change||selected[q];
    if(!change)continue;
    Row r;r.face=f;r.owner=m.owner[f];r.neighbour=m.neighbour[f];
    r.patch=m.patch[f];r.zone=m.zone[f];r.role=roles[f];r.before=m.faces[f];
    for(label q:r.before) {r.beforeBits.push_back(bits(m.points[q]));if(!selected[q])r.after.push_back(q);}
    if(r.after.size()<3)return fail("inert-simultaneous-ring-collapse");
    std::set<label> ids(r.after.begin(),r.after.end());
    if(ids.size()!=r.after.size())return fail("inert-simultaneous-ring-repeat");
    E::Context exact(work,524288);std::vector<G::Point> pp;G::Ring before,after;
    for(label q:r.before){before.push_back(pp.size());const auto& p=m.points[q];pp.push_back({{p.x(),p.y(),p.z()}});}
    for(label q:r.after)after.push_back(std::find(r.before.begin(),r.before.end(),q)-r.before.begin());
    E::Vector normal,origin;
    if(!G::Detail::plane(before,pp,normal,origin,exact)
      ||!G::Detail::aligned(after,pp,normal,origin,exact))return fail("inert-nonplanar-or-nonconvex-ring");
    const auto coord=[&](label q)->const point&{return m.points[q];};
    if(!I::same(I::integrals(r.before,coord,exact),I::integrals(r.after,coord,exact),exact)
      ||!exact.good())return fail("inert-exact-sixteen-or-clock");
    // The after ring is an order-preserving subsequence. Every omitted
    // node was proved strictly internal to its same actual edge in ALL rings.
    affectedCells.insert(r.owner);if(r.neighbour>=0)affectedCells.insert(r.neighbour);
    out.proofs.emplace(f,std::shared_ptr<const FaceProof>(new FaceProof(std::move(r))));
   }
   for(label c:affectedCells) {
    std::map<std::pair<label,label>,std::pair<label,label>> edges;
    for(label f:cf[c]) {
     if(!work())return fail("inert-clock");
     const auto p=out.proofs.find(f);const auto& ring=p==out.proofs.end()?m.faces[f]:p->second->row().after;
     for(std::size_t j=0;j<ring.size();++j) {
      label a=ring[j],b=ring[(j+1)%ring.size()];if(m.owner[f]!=c)std::swap(a,b);
      auto& e=edges[std::minmax(a,b)];++e.first;e.second+=a<b?1:-1;
     }
    }
    for(const auto& e:edges)if(e.second.first!=2||e.second.second!=0)return fail("inert-complete-cell-edge-closure");
   }
   for(label q=0;q<label(selected.size());++q)if(selected[q]) {
    Node n;n.point=q;n.first=segment[q].first;n.second=segment[q].second;
    n.pointBits=bits(m.points[q]);n.firstBits=bits(m.points[n.first]);n.secondBits=bits(m.points[n.second]);n.faces=star[q];
    out.nodes.push_back(std::move(n));
   }
   if(!work())return fail("inert-clock");
   out.ready=true;out.reason=out.nodes.empty()?"inert-no-change":"inert-ready";return out;
  }catch(...){return fail("inert-unknown-or-allocation");}
 }
};
template<class Mesh,class Coupled,class SourceRole>
Result prepare(const Mesh& m,label rank,Coupled coupled,SourceRole role,
 const std::function<bool()>& available) {
 return Builder::prepare(m,rank,coupled,role,available);
}
template<class Mesh>
bool commit(Mesh& m,Result& p,const std::function<bool()>& available) {
 if(!p.ready||p.published||(!m.inertEdgeFaceProofs.empty()))return false;
 try {
  if(available&&!available())return false;
  std::map<label,Ring> swaps;
  for(const auto& item:p.proofs) {
   const auto& r=item.second->row();const label f=r.face;
   if(f<0||std::size_t(f)>=m.faces.size()||!m.faceAlive[f]
     ||m.owner[f]!=r.owner||m.neighbour[f]!=r.neighbour||m.patch[f]!=r.patch
     ||m.zone[f]!=r.zone||m.faces[f]!=r.before)return false;
   for(std::size_t j=0;j<r.before.size();++j)
    if(bits(m.points[r.before[j]])!=r.beforeBits[j])return false;
   swaps.emplace(f,r.after);
  }
  Proofs held=p.proofs; // Allocate every receipt/edit before any mutation.
  if(available&&!available())return false;
  for(auto& item:swaps)m.faces[item.first].swap(item.second);
  m.inertEdgeFaceProofs.swap(held);
  if(!swaps.empty())dcCartPreparedFacetBirth::invalidateCurrentIncidence(m);
  p.published=true;return true;
 }catch(...){return false;}
}
template<class Mesh,class Birth>
auto continuation(const Mesh& m,label f,const Birth& birth,int)
 ->decltype(m.inertEdgeFaceProofs.find(f),bool()) {
 const auto p=m.inertEdgeFaceProofs.find(f);
 return p!=m.inertEdgeFaceProofs.end()&&p->second&&p->second->continuation(m,f,birth);
}
template<class Mesh,class Birth>
bool continuation(const Mesh&,label,const Birth&,long){return false;}
inline void quoted(std::ostream& out,const std::string& s) {
 out<<'"';for(unsigned char c:s) {if(c=='"'||c=='\\')out<<'\\'<<char(c);else if(c>=32)out<<char(c);else out<<"\\u00"<<std::hex<<std::setw(2)<<std::setfill('0')<<unsigned(c)<<std::dec;}out<<'"';
}
inline bool writeReceipt(const Result& p,label rank,const std::string& path) {
 try {
  std::ostringstream o;o<<"{\"schema\":\"strict-local-inert-edge-cleanup/v1\",\"rank\":"<<rank
   <<",\"producerOnly\":true,\"sourceAuthority\":false,\"meshAccepted\":false,\"published\":"<<(p.published?"true":"false")
   <<",\"reason\":";quoted(o,p.reason);o<<",\"points\":[";
  bool comma=false;
  for(const auto& n:p.nodes) {
   if(comma)o<<',';comma=true;o<<"{\"point\":"<<n.point<<",\"endpoints\":["<<n.first<<','<<n.second<<"],\"pointBits\":[";
   for(unsigned k=0;k<3;++k){if(k)o<<',';o<<'"'<<std::hex<<n.pointBits[k]<<std::dec<<'"';}
   o<<"],\"incidentFaces\":[";for(std::size_t j=0;j<n.faces.size();++j){if(j)o<<',';o<<n.faces[j];}o<<"]}";
  }
  o<<"],\"faces\":[";comma=false;
  for(const auto& item:p.proofs) {
   const auto& r=item.second->row();if(comma)o<<',';comma=true;
   o<<"{\"face\":"<<r.face<<",\"owner\":"<<r.owner<<",\"neighbour\":"<<r.neighbour<<",\"patch\":"<<r.patch<<",\"zone\":"<<r.zone<<",\"sourceEntityUid\":";quoted(o,r.role.entity);
   o<<",\"sourceRegionUid\":";quoted(o,r.role.region);o<<",\"before\":[";
   for(std::size_t j=0;j<r.before.size();++j){if(j)o<<',';o<<r.before[j];}o<<"],\"after\":[";
   for(std::size_t j=0;j<r.after.size();++j){if(j)o<<',';o<<r.after[j];}o<<"]}";
  }
  o<<"],\"complete\":true}\n";const auto bytes=o.str();
  if(bytes.size()>16*1024*1024)return false;
  std::ofstream f(path,std::ios::binary);f.write(bytes.data(),bytes.size());return bool(f);
 }catch(...){return false;}
}
}}
#endif
