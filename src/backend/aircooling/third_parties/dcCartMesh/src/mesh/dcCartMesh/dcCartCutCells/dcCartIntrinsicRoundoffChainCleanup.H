#ifndef dcCartIntrinsicRoundoffChainCleanup_H
#define dcCartIntrinsicRoundoffChainCleanup_H
#include "point.H"
#include "dcCartExactBinary64Geometry.H"
#include <utility>
#include <algorithm>
#include <array>
#include <cmath>
#include <functional>
#include <limits>
#include <map>
#include <set>
#include <string>
#include <vector>
namespace Foam { namespace dcCartIntrinsicRoundoffChainCleanup {
// New geometry producer candidate only. No coordinate, cell, Source or permission changes.
// Complete point-star provenance is an input inventory, never a Source/Stock certificate.
struct Row {
 label native=-1,owner=-1,neighbour=-1,patch=-1,zone=-1;
 std::vector<label> ring;
 std::string boundaryType,sourceRegion,sourceSha,operation;
 bool sourceRoleKnown=false;
};
struct Chain {
 std::array<label,3> faces{{-1,-1,-1}};
 label first=-1,last=-1;
 std::vector<label> removed;
 scalar maxChordDistance=0,roundoffBound=0;
};
struct Result {
 bool ready=false;
 std::map<label,std::vector<label>> rings;
 std::vector<Chain> chains;
 std::set<label> removed;
 const char* reason=nullptr;
};
inline bool finite(const point& p) {
 return std::isfinite(p[0])&&std::isfinite(p[1])&&std::isfinite(p[2]);
}
inline scalar componentULP(scalar x) {
 if(!std::isfinite(x))return std::numeric_limits<scalar>::infinity();
 return std::max(std::abs(std::nextafter(x,std::numeric_limits<scalar>::infinity())-x),
                 std::abs(x-std::nextafter(x,-std::numeric_limits<scalar>::infinity())));
}
template<class Points>
bool prepare(const Points& points,const std::vector<Row>& rows,
 const std::set<label>& completeStarPoints,const std::set<label>& protectedPoints,
 const std::function<bool()>& available,Result& output) {
 output=Result();
 const auto work=[&](){return !available||available();};
 const auto no=[&](const char* why){output.reason=why;return false;};
 try {
  if(rows.empty()||rows.size()>64||completeStarPoints.empty()||completeStarPoints.size()>512)
   return no("chain-input-cap");
  std::map<label,const Row*> face;
  std::map<label,std::vector<label>> stars;
  std::set<label> referenced;
  for(const auto& row:rows) {
   if(!work())return no("chain-clock");
   if(row.native<0||row.owner<0||row.neighbour<-1||row.owner==row.neighbour
    ||row.ring.size()<3||row.ring.size()>64||!face.emplace(row.native,&row).second)
    return no("chain-row-shape");
   std::set<label> unique;
   for(label id:row.ring) {
    if(id<0||std::size_t(id)>=points.size()||!finite(points[id])||!unique.insert(id).second)
     return no("chain-point-or-ring");
    referenced.insert(id);
    if(completeStarPoints.count(id))stars[id].push_back(row.native);
   }
  }
  if(referenced.size()>512)return no("chain-point-cap");
  struct Vertex {std::array<label,3> star;std::array<label,2> adjacent;};
  std::map<label,Vertex> eligible;
  for(label id:completeStarPoints) {
   if(!work())return no("chain-clock");
   if(protectedPoints.count(id))continue;
   const auto found=stars.find(id);
   if(found==stars.end())return no("chain-complete-star-missing");
   auto incident=found->second;
   std::sort(incident.begin(),incident.end());
   if(incident.size()!=3)continue; // branch/corner is preserved
   const Row* internal=nullptr;std::vector<const Row*> physical;
   bool supported=true;
   for(label f:incident) {
    const Row& r=*face.at(f);
    if(r.zone!=-1){supported=false;break;}
    if(r.neighbour>=0) {
     if(r.patch!=-1||internal){supported=false;break;}
     internal=&r;
    } else {
     if(r.patch<0||r.boundaryType!="wall"||!r.sourceRoleKnown
      ||r.sourceRegion.empty()||r.sourceSha.size()!=64) {supported=false;break;}
     physical.push_back(&r);
    }
   }
   if(!supported||!internal||physical.size()!=2)continue;
   const auto& a=*physical[0];const auto& z=*physical[1];
   if(a.owner==z.owner||std::set<label>{a.owner,z.owner}!=std::set<label>{internal->owner,internal->neighbour}
    ||a.patch!=z.patch||a.sourceRegion!=z.sourceRegion||a.sourceSha!=z.sourceSha
    ||a.operation!=z.operation)continue; // true protected Source/BC seam
   std::array<label,2> common{{-1,-1}};bool first=true;
   for(label f:incident) {
    const auto& ring=face.at(f)->ring;const auto at=std::find(ring.begin(),ring.end(),id);
    if(at==ring.end()){supported=false;break;}
    const auto k=std::size_t(std::distance(ring.begin(),at));
    std::array<label,2> near{{ring[(k+ring.size()-1)%ring.size()],ring[(k+1)%ring.size()]}};
    std::sort(near.begin(),near.end());
    if(first){common=near;first=false;}else if(common!=near){supported=false;break;}
   }
   if(!supported||common[0]==common[1])continue;
   eligible.emplace(id,Vertex{{{incident[0],incident[1],incident[2]}},common});
  }
  namespace E=::dcCartExactBinary64Geometry;
  E::Context exact(work,262144);
  const auto exactPoint=[&](label id){const point& p=points[id];return E::vector(E::Point{{p[0],p[1],p[2]}},exact);};
  Result draft;std::set<label> visited;
  for(const auto& vertex:eligible) {
   if(!work())return no("chain-clock");
   if(visited.count(vertex.first))continue;
   const auto signature=vertex.second.star;
   std::set<label> component;std::vector<label> todo{vertex.first};
   while(!todo.empty()) {
    if(!work())return no("chain-clock");
    label id=todo.back();todo.pop_back();
    if(!component.insert(id).second)continue;
    for(label q:eligible.at(id).adjacent) {
     const auto next=eligible.find(q);
     if(next!=eligible.end()&&next->second.star==signature)todo.push_back(q);
    }
   }
   visited.insert(component.begin(),component.end());
   std::set<label> ends;
   for(label id:component)for(label q:eligible.at(id).adjacent)if(!component.count(q))ends.insert(q);
   if(ends.size()!=2)continue; // closed loop/branch has no safe chord
   Chain chain;chain.faces=signature;chain.first=*ends.begin();chain.last=*ends.rbegin();
   label previous=chain.first,current=-1;
   for(label id:component) {
    const auto& near=eligible.at(id).adjacent;
    if(near[0]==previous||near[1]==previous){if(current>=0){current=-2;break;}current=id;}
   }
   if(current<0)continue;
   std::set<label> ordered;
   while(component.count(current)) {
    if(!work())return no("chain-clock");
    if(!ordered.insert(current).second)break;
    chain.removed.push_back(current);
    const auto near=eligible.at(current).adjacent;
    const label next=near[0]==previous?near[1]:(near[1]==previous?near[0]:-1);
    previous=current;current=next;
   }
   if(current!=chain.last||ordered!=component)continue;
   const point& start=points[chain.first];const point& end=points[chain.last];
   std::array<long double,3> delta{};
   long double length2=0;
   for(unsigned k=0;k<3;++k){delta[k]=static_cast<long double>(end[k])-start[k];length2+=delta[k]*delta[k];}
   if(!(length2>0)||!std::isfinite(length2))continue;
   scalar ulp=0;
   for(label id:ends)for(unsigned k=0;k<3;++k)ulp=std::max(ulp,componentULP(points[id][k]));
   for(label id:chain.removed)for(unsigned k=0;k<3;++k)ulp=std::max(ulp,componentULP(points[id][k]));
   const long double bound=8*static_cast<long double>(ulp);
   long double lastT=0,maxDistance=0;bool accepted=std::isfinite(bound)&&bound>0;
   const auto eStart=exactPoint(chain.first),eDelta=E::difference(exactPoint(chain.last),eStart,exact);
   const auto eLength2=E::dot(eDelta,eDelta,exact);
   const auto eBound=E::number(scalar(8)*ulp,exact);
   const auto eLimit=E::multiply(E::multiply(eBound,eBound,exact),eLength2,exact);
   auto ePrevious=E::number(0,exact);
   if(!exact.good())return no("chain-exact-cap-or-clock");
   for(label id:chain.removed) {
    if(!work())return no("chain-clock");
    std::array<long double,3> offset{};long double along=0;
    for(unsigned k=0;k<3;++k){offset[k]=static_cast<long double>(points[id][k])-start[k];along+=offset[k]*delta[k];}
    const long double t=along/length2;long double distance2=0;
    for(unsigned k=0;k<3;++k){const auto residual=offset[k]-t*delta[k];distance2+=residual*residual;}
    const long double distance=std::sqrt(distance2);
    const auto eOffset=E::difference(exactPoint(id),eStart,exact);
    const auto eAlong=E::dot(eOffset,eDelta,exact);
    const auto eCross=E::cross(eOffset,eDelta,exact);
    const auto eDistanceTimesLength2=E::dot(eCross,eCross,exact);
    const bool inside=E::sign(E::subtract(eAlong,ePrevious,exact))>0
      &&E::sign(E::subtract(eLength2,eAlong,exact))>0;
    const bool bounded=E::sign(E::subtract(eDistanceTimesLength2,eLimit,exact))<=0;
    if(!exact.good())return no("chain-exact-cap-or-clock");
    if(!std::isfinite(t)||!std::isfinite(distance)||!inside||!bounded){accepted=false;break;}
    ePrevious=eAlong;
    lastT=t;maxDistance=std::max(maxDistance,distance);
   }
   if(!accepted)continue;
   chain.maxChordDistance=static_cast<scalar>(maxDistance);chain.roundoffBound=static_cast<scalar>(bound);
   for(label id:chain.removed)if(!draft.removed.insert(id).second)return no("chain-overlap");
   draft.chains.push_back(std::move(chain));
  }
  if(draft.removed.empty())return no("chain-no-roundoff-internal-chain");
  for(const auto& item:face) {
   if(!work())return no("chain-clock");
   std::vector<label> ring;
   for(label id:item.second->ring)if(!draft.removed.count(id))ring.push_back(id);
   if(ring.size()<3||std::set<label>(ring.begin(),ring.end()).size()!=ring.size())
    return no("chain-result-face-shape");
   draft.rings.emplace(item.first,std::move(ring));
  }
  if(!exact.good())return no("chain-exact-cap-or-clock");
  draft.ready=work();if(!draft.ready)return no("chain-clock");
  output=std::move(draft);return true;
 } catch(...) {return no("chain-unknown-or-allocation");}
}
} }
#endif
