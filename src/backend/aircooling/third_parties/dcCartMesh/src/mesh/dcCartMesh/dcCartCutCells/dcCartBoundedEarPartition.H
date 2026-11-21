#ifndef dcCartBoundedEarPartition_H
#define dcCartBoundedEarPartition_H
#include "dcCartExactBinary64Geometry.H"
#include <array>
#include <vector>
#include <set>
#include <map>
#include <functional>
namespace dcCartBoundedEarPartition
{
using Label=int;
using Triangle=std::array<Label,3>;
struct Plan {bool complete=false;std::vector<Triangle> triangles;};
namespace detail
{
namespace E=dcCartExactBinary64Geometry;
struct Q { E::Dyadic u,v; };
inline E::Dyadic orient(const Q& a,const Q& b,const Q& d,E::Context& c)
{
 return E::subtract(E::multiply(E::subtract(b.u,a.u,c),E::subtract(d.v,a.v,c),c),
                    E::multiply(E::subtract(b.v,a.v,c),E::subtract(d.u,a.u,c),c),c);
}
inline bool between(const E::Dyadic& p,const E::Dyadic& a,const E::Dyadic& b,E::Context& c)
{return E::sign(E::multiply(E::subtract(p,a,c),E::subtract(p,b,c),c))<=0;}
inline bool on(const Q& p,const Q& a,const Q& b,E::Context& c)
{return E::sign(orient(a,b,p,c))==0&&between(p.u,a.u,b.u,c)&&between(p.v,a.v,b.v,c);}
inline bool crossing(const Q& a,const Q& b,const Q& d,const Q& e,E::Context& c)
{
 int x=E::sign(orient(a,b,d,c)),y=E::sign(orient(a,b,e,c));
 int z=E::sign(orient(d,e,a,c)),w=E::sign(orient(d,e,b,c));
 return (x*y<0&&z*w<0)||on(d,a,b,c)||on(e,a,b,c)||on(a,d,e,c)||on(b,d,e,c);
}
}
// Exact predicates in the caller's original dominant coordinate projection.
// Original 3D points are never changed. Native preflight planarity and every
// existing A/C, Newell, positive volume, topology and quality gate remain needed.
// triangleAllowed must be the real read-only native writable/positive criterion.
template<class PointAt,class TriangleAllowed>
Plan build(const std::vector<Label>& ring,std::size_t pointCount,PointAt pointAt,
           unsigned dropAxis,TriangleAllowed triangleAllowed,
           const std::function<bool()>& available)
{
 namespace E=dcCartExactBinary64Geometry;
 using namespace detail;
 Plan result;
 if(ring.size()<3||ring.size()>64||dropAxis>2)return result;
 E::Context c(available,262144);
 unsigned axes[2],naxis=0;for(unsigned k=0;k<3;++k)if(k!=dropAxis)axes[naxis++]=k;
 std::vector<Q> q;std::set<Label> ids;
 std::set<std::array<std::uint64_t,2>> projected;
 for(Label id:ring)
 {
  if(!c.take()||id<0||std::size_t(id)>=pointCount||!ids.insert(id).second)return result;
  const auto p=pointAt(id);std::array<std::uint64_t,3> bytes{};
  for(unsigned k=0;k<3;++k)
  {
   std::memcpy(&bytes[k],&p[k],8);
   if(((bytes[k]>>52)&2047)==2047)return result;
   if((bytes[k]&0x7fffffffffffffffULL)==0)bytes[k]=0;
  }
  const std::array<std::uint64_t,2> key{{bytes[axes[0]],bytes[axes[1]]}};
  if(!projected.insert(key).second)return result;
  q.push_back(Q{E::number(p[axes[0]],c),E::number(p[axes[1]],c)});
 }
 E::Dyadic shoelace;
 for(std::size_t j=0;j<q.size();++j)
 {
  const Q& a=q[j];const Q& b=q[(j+1)%q.size()];
  shoelace=E::add(shoelace,E::subtract(E::multiply(a.u,b.v,c),E::multiply(a.v,b.u,c),c),c);
 }
 const int sign=E::sign(shoelace);if(!c.good()||sign==0)return result;
 for(std::size_t i=0;i<q.size();++i)for(std::size_t j=i+1;j<q.size();++j)
 {
  if(!c.take())return Plan();
  if((i+1)%q.size()==j||(j+1)%q.size()==i)continue;
  if(crossing(q[i],q[(i+1)%q.size()],q[j],q[(j+1)%q.size()],c))return Plan();
 }
 if(!c.good())return Plan();
 std::set<std::uint64_t> failed;std::vector<Triangle> staged;
 auto allowed=[&](unsigned a,unsigned b,unsigned d)
 {
  if(!c.take())return false;
  bool ok=triangleAllowed(Triangle{{ring[a],ring[b],ring[d]}});
  if(!c.take())return false;return ok;
 };
 std::function<bool(const std::vector<unsigned>&)> search;
 search=[&](const std::vector<unsigned>& active)->bool
 {
  if(!c.take())return false;
  std::uint64_t mask=0;for(unsigned i:active)mask|=(std::uint64_t(1)<<i);
  if(failed.count(mask))return false;
  if(failed.size()>=4096){c.fail(E::Refusal::capacity);return false;}
  if(active.size()==3)
  {
   const unsigned a=active[0],b=active[1],d=active[2];
   if(E::sign(orient(q[a],q[b],q[d],c))*sign<=0||!allowed(a,b,d))return false;
   staged.push_back(Triangle{{ring[a],ring[b],ring[d]}});return c.good();
  }
  for(std::size_t j=0;j<active.size();++j)
  {
   if(!c.take())return false;
   unsigned a=active[(j+active.size()-1)%active.size()],b=active[j],d=active[(j+1)%active.size()];
   if(E::sign(orient(q[a],q[b],q[d],c))*sign<=0)continue;
   bool blocked=false;
   for(unsigned p:active)
   {
    if(!c.take())return false;
    if(p==a||p==b||p==d)continue;
    if(E::sign(orient(q[a],q[b],q[p],c))*sign>=0
      &&E::sign(orient(q[b],q[d],q[p],c))*sign>=0
      &&E::sign(orient(q[d],q[a],q[p],c))*sign>=0){blocked=true;break;}
   }
   if(blocked||!allowed(a,b,d))continue;
   std::vector<unsigned> next=active;next.erase(next.begin()+j);
   const std::size_t saved=staged.size();staged.push_back(Triangle{{ring[a],ring[b],ring[d]}});
   if(search(next))return true;
   staged.resize(saved);if(!c.good())return false;
  }
  failed.insert(mask);return false;
 };
 std::vector<unsigned> active;for(unsigned j=0;j<ring.size();++j)active.push_back(j);
 if(!search(active)||!c.good()||staged.size()!=ring.size()-2)return Plan();
 // Certificate of original directed-outline coverage and two uses per interior
 // diagonal. Positive exact projected triangles cannot hide skipped edge nodes.
 using Edge=std::pair<Label,Label>;
 std::map<Edge,std::vector<Edge>> uses;
 for(const Triangle& t:staged)for(unsigned k=0;k<3;++k)
 {
  if(!c.take())return Plan();
  Label a=t[k],b=t[(k+1)%3];uses[std::minmax(a,b)].emplace_back(a,b);
 }
 std::map<Edge,Edge> boundary;
 for(std::size_t j=0;j<ring.size();++j)
 {Label a=ring[j],b=ring[(j+1)%ring.size()];boundary.emplace(std::minmax(a,b),Edge{a,b});}
 for(const auto& entry:uses)
 {
  if(!c.take())return Plan();
  auto b=boundary.find(entry.first);const auto& u=entry.second;
  if(b!=boundary.end()){if(u.size()!=1||u[0]!=b->second)return Plan();boundary.erase(b);}
  else if(u.size()!=2||u[0].first!=u[1].second||u[0].second!=u[1].first)return Plan();
 }
 if(!boundary.empty()||!c.good())return Plan();
 result.complete=true;result.triangles=std::move(staged);return result;
}
}
#endif
