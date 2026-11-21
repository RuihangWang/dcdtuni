#ifndef dcCartRectangularSourceClosurePlanes_H
#define dcCartRectangularSourceClosurePlanes_H
#include "dcCartSharedAxisSeamVolumePartition.H"
#include "dcCartExactBinary64Geometry.H"
#include <map>
#include <set>
#include <tuple>
namespace Foam { namespace dcCartRectangularSourceClosurePlanes {
namespace S=dcCartSharedAxisSeamVolumePartition;
namespace E=::dcCartExactBinary64Geometry;
using Plane=std::pair<label,scalar>;
struct Entry { bool eligible=false; std::vector<Plane> planes; };
struct Cache { const void* raw=nullptr; std::size_t points=0,faces=0,cells=0; std::map<label,Entry> entries; };
// Proposal screen only. It neither repairs geometry nor supplies a role,
// current-prefix identity, quality measurement or publication permission.
template<class Raw>
bool rectangular(const Raw& m,label root,const std::vector<label>& cf,
 const std::function<bool()>& available)
{
 auto work=[&](){return S::clock(available);};
 if(!work()||root<0||root>=m.nCells||!m.cellAlive[root]||cf.size()<6||cf.size()>64)return false;
 point lo(GREAT,GREAT,GREAT),hi(-GREAT,-GREAT,-GREAT);
 std::set<label> faces,points;std::map<std::tuple<scalar,scalar,scalar>,label> coordinates;
 for(label f:cf) {
  if(!work()||f<0||std::size_t(f)>=m.faces.size()||!m.faceAlive[f]
    ||(m.owner[f]!=root&&m.neighbour[f]!=root)||!faces.insert(f).second
    ||m.faces[f].size()<3||m.faces[f].size()>64)return false;
  std::set<label> unique;
  for(label p:m.faces[f]) {
   if(!work()||p<0||std::size_t(p)>=m.points.size()||!unique.insert(p).second||!S::finite(m.points[p]))return false;
   const point& q=m.points[p];lo=min(lo,q);hi=max(hi,q);points.insert(p);
   const auto inserted=coordinates.emplace(std::make_tuple(q.x(),q.y(),q.z()),p);
   if(!inserted.second&&inserted.first->second!=p)return false;
  }
 }
 if(points.size()>256||!(lo.x()<hi.x()&&lo.y()<hi.y()&&lo.z()<hi.z()))return false;
 E::Context exact(work);std::array<E::Dyadic,6> area;
 std::map<std::pair<label,label>,std::pair<label,label>> edges;std::array<bool,6> support{};
 for(label f:cf) {
  const auto& ring=m.faces[f];label axis=-1,side=-1;
  for(label d=0;d<3;++d) {
   bool same=true;for(label p:ring)same=same&&m.points[p][d]==m.points[ring[0]][d];
   if(same) {if(axis>=0)return false;axis=d;}
  }
  if(axis<0)return false;
  const scalar position=m.points[ring[0]][axis];side=position==lo[axis]?0:position==hi[axis]?1:-1;
  if(side<0)return false;support[2*axis+side]=true;
  E::Dyadic signedArea;const bool forward=m.owner[f]==root;
  for(std::size_t j=0;j<ring.size();++j) {
   if(!work())return false;label a=ring[j],b=ring[(j+1)%ring.size()];
   label varying=0;for(label d=0;d<3;++d)varying+=m.points[a][d]!=m.points[b][d];
   if(varying!=1)return false; // only genuine rectilinear source boundaries
   if(!forward)std::swap(a,b);auto& e=edges[std::minmax(a,b)];++e.first;e.second+=a<b?1:-1;
   const point& x=m.points[a];const point& y=m.points[b];
   const auto cross=E::cross(E::vector(E::Point{{x.x(),x.y(),x.z()}},exact),
     E::vector(E::Point{{y.x(),y.y(),y.z()}},exact),exact);
   signedArea=E::add(signedArea,cross[axis],exact);
  }
  if(!exact.good()||E::sign(signedArea)!=(side?1:-1))return false;
  area[2*axis+side]=E::add(area[2*axis+side],signedArea,exact);
 }
 for(const auto& e:edges)if(e.second.first!=2||e.second.second!=0)return false;
 for(label d=0;d<3;++d) {
  const label a=(d+1)%3,b=(d+2)%3;
  auto target=E::multiply(E::subtract(E::number(hi[a],exact),E::number(lo[a],exact),exact),
    E::subtract(E::number(hi[b],exact),E::number(lo[b],exact),exact),exact);
  target=E::multiply(target,E::number(2,exact),exact);
  for(label side=0;side<2;++side) {
   const auto expected=side?target:E::multiply(target,E::number(-1,exact),exact);
   if(!support[2*d+side]||E::sign(E::subtract(area[2*d+side],expected,exact))!=0)return false;
  }
 }
 return exact.good()&&work();
}
template<class Raw,class Roots>
bool augment(const Raw& raw,const std::vector<std::vector<label>>& cf,const Roots& reached,
 Roots& requested,Cache& cache,const std::function<bool()>& available,label& added)
{
 added=0;auto work=[&](){return S::clock(available);};
 if(!work()||cf.size()<std::size_t(raw.nCells))return false;
 if(cache.raw!=static_cast<const void*>(&raw)||cache.points!=raw.points.size()
   ||cache.faces!=raw.faces.size()||cache.cells!=std::size_t(raw.nCells)) {
  cache.entries.clear();cache.raw=static_cast<const void*>(&raw);cache.points=raw.points.size();
  cache.faces=raw.faces.size();cache.cells=std::size_t(raw.nCells);
 }
 for(const auto& entry:reached) {
  if(!work())return false;const label root=entry.first;
  if(root<0||root>=raw.nCells)return false;
  auto found=cache.entries.find(root);
  if(found==cache.entries.end()) {
   Entry proposal;proposal.eligible=rectangular(raw,root,cf[root],available);
   if(!work())return false;
   if(proposal.eligible) {
    proposal.planes=S::seams(raw,root,cf[root],available);
    if(!work())return false;
    if(proposal.planes.size()>8)proposal=Entry(); // bounded suggestion, never a relaxed gate
   }
   found=cache.entries.emplace(root,std::move(proposal)).first;
  }
  if(!found->second.eligible)continue;
  for(const Plane& plane:found->second.planes) {
   if(!work())return false;
   if(std::find(entry.second.begin(),entry.second.end(),plane)!=entry.second.end())continue;
   auto& next=requested[root];if(std::find(next.begin(),next.end(),plane)!=next.end())continue;
   next.push_back(plane);std::sort(next.begin(),next.end());++added;
  }
 }
 return work();
}
} }
#endif
