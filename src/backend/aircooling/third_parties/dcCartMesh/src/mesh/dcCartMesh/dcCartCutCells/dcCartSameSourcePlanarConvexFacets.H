#ifndef dcCartSameSourcePlanarConvexFacets_H
#define dcCartSameSourcePlanarConvexFacets_H
#include "point.H"
#include "dcCartPrefixSourceRingRestoration.H"

namespace Foam { namespace dcCartSameSourcePlanarConvexFacets {
namespace S=dcCartSharedAxisSeamVolumePartition;
namespace R=dcCartPrefixSourceRingRestoration;
namespace E=dcCartExactBinary64Geometry;
using Ring=std::vector<label>;
struct Result {std::vector<Ring> facets;bool exactSourceConserved=false;};

template<class At>
bool prepare(const S::Row& source,std::size_t pointCount,At at,
 const std::function<bool()>& available,Result& out,std::string* why=nullptr) {
 out=Result();auto work=[&](){return R::work(available);};
 auto fail=[&](const char* s){if(why)*why=s;return false;};
 const auto& original=source.ring;
 if(!work()||source.source<0||source.native<0||source.owner<0||source.neighbour<-1
  ||original.size()<3||original.size()>64)return fail("facet-input");
 std::set<label> ids;std::vector<point> points;points.reserve(original.size());
 for(label p:original) {
  if(!work()||p<0||std::size_t(p)>=pointCount||!ids.insert(p).second||!S::finite(at(p)))return fail("facet-node");
  for(const point& x:points)if(x==at(p))return fail("facet-coordinate-alias");
  points.push_back(at(p));
 }
 E::Context ec(work);std::map<label,E::Vector> exact;
 for(label p:original){const point& x=at(p);exact.emplace(p,E::vector({{x.x(),x.y(),x.z()}},ec));}
 E::Vector normal;const auto origin=exact.at(original.front());bool plane=false;
 for(std::size_t i=1;i+1<original.size();++i) {
  normal=E::cross(E::difference(exact.at(original[i]),origin,ec),E::difference(exact.at(original[i+1]),origin,ec),ec);
  if(E::sign(normal[0])||E::sign(normal[1])||E::sign(normal[2])){plane=true;break;}
 }
 if(!ec.good()||!plane)return fail("facet-plane");
 for(label p:original)if(E::sign(E::dot(normal,E::difference(exact.at(p),origin,ec),ec)))return fail("facet-not-exact-planar");
 if(!ec.good())return fail("facet-exact-or-clock");
 unsigned drop=0;while(drop<3&&!E::sign(normal[drop]))++drop;
 if(drop==3)return fail("facet-plane");const unsigned u=(drop+1)%3,v=(drop+2)%3;
 auto orient=[&](label a,label b,label c) {
  const auto ab=E::difference(exact.at(b),exact.at(a),ec),ac=E::difference(exact.at(c),exact.at(a),ec);
  return E::sign(E::subtract(E::multiply(ab[u],ac[v],ec),E::multiply(ab[v],ac[u],ec),ec));
 };
 auto within=[&](label a,label b,label p) {
  const point& x=at(a);const point& y=at(b);const point& z=at(p);
  return z[u]>=std::min(x[u],y[u])&&z[u]<=std::max(x[u],y[u])
   &&z[v]>=std::min(x[v],y[v])&&z[v]<=std::max(x[v],y[v]);
 };
 // A simple source polygon is necessary before any ear is a source partition.
 for(std::size_t i=0;i<original.size();++i)for(std::size_t j=i+1;j<original.size();++j) {
  if(!work())return fail("facet-clock");
  if(j==i+1||(i==0&&j+1==original.size()))continue;
  label a=original[i],b=original[(i+1)%original.size()],c=original[j],d=original[(j+1)%original.size()];
  const int abC=orient(a,b,c),abD=orient(a,b,d),cdA=orient(c,d,a),cdB=orient(c,d,b);
  if((abC*abD<0&&cdA*cdB<0)||(!abC&&within(a,b,c))||(!abD&&within(a,b,d))
   ||(!cdA&&within(c,d,a))||(!cdB&&within(c,d,b)))return fail("facet-not-simple");
 }
 auto lessPoint=[&](label a,label b) {
  const point& x=at(a);const point& y=at(b);
  for(unsigned k=0;k<3;++k){if(x[k]<y[k])return true;if(x[k]>y[k])return false;}return false;
 };
 // Coordinate canonical cycle is independent of rank-local point numbers and
 // input rotation/direction. It does not move/alias any source node.
 Ring poly;bool have=false;
 for(label dir:{label(1),label(-1)})for(label start=0;start<label(original.size());++start) {
  if(!work())return fail("facet-clock");Ring candidate;
  for(label k=0;k<label(original.size());++k)candidate.push_back(original[(start+dir*k+label(original.size())*2)%original.size()]);
  bool smaller=!have;
  if(have)for(std::size_t k=0;k<candidate.size();++k) {
   if(lessPoint(candidate[k],poly[k])){smaller=true;break;}
   if(lessPoint(poly[k],candidate[k]))break;
  }
  if(smaller){poly=std::move(candidate);have=true;}
 }
 auto signedArea=[&](const Ring& r) {
  E::Dyadic a;for(std::size_t j=0;j<r.size();++j) {
   const auto& x=exact.at(r[j]);const auto& y=exact.at(r[(j+1)%r.size()]);
   a=E::add(a,E::subtract(E::multiply(x[u],y[v],ec),E::multiply(x[v],y[u],ec),ec),ec);
  }return a;
 };
 const int sign=E::sign(signedArea(poly));if(!ec.good()||!sign)return fail("facet-zero-area");
 auto convex=[&](const Ring& r) {
  if(r.size()<3||r.size()>64||E::sign(signedArea(r))!=sign)return false;
  for(std::size_t i=0;i<r.size();++i)for(label p:r)
   if(orient(r[i],r[(i+1)%r.size()],p)*sign<0)return false;
  return ec.good();
 };
 auto numericPositive=[&](const Ring& r) {
  vector area=vector::zero;const point& a=at(r.front());
  for(std::size_t i=1;i+1<r.size();++i)area+=(at(r[i])-a)^(at(r[i+1])-a);
  return S::finite(area)&&mag(area)*.5>1e-12&&ec.good();
 };
 Ring remaining=poly;std::vector<Ring> pieces;
 while(remaining.size()>3) {
  if(!work())return fail("facet-clock");bool cut=false;
  for(std::size_t i=0;i<remaining.size();++i) {
   const label a=remaining[(i+remaining.size()-1)%remaining.size()],b=remaining[i],c=remaining[(i+1)%remaining.size()];
   if(orient(a,b,c)*sign<=0||!numericPositive({a,b,c}))continue;bool empty=true;
   for(label p:remaining)if(p!=a&&p!=b&&p!=c) {
    if(orient(a,b,p)*sign>=0&&orient(b,c,p)*sign>=0&&orient(c,a,p)*sign>=0){empty=false;break;}
   }
   if(!ec.good())return fail("facet-exact-or-clock");
   if(!empty)continue;
   pieces.push_back({a,b,c});remaining.erase(remaining.begin()+i);cut=true;break;
  }
  if(!cut)return fail("facet-no-positive-ear");
 }
 if(!convex(remaining)||!numericPositive(remaining))return fail("facet-final-ear");
 pieces.push_back(std::move(remaining));
 // Deterministic adjacent-ear convex coalescence. Only this one actual source
 // role is present; no unrelated neighbours, patches, or sources can be merged.
 bool merged=true;
 while(merged) {
  merged=false;
  for(std::size_t i=0;i<pieces.size()&&!merged;++i)for(std::size_t j=i+1;j<pieces.size()&&!merged;++j) {
   if(!work())return fail("facet-clock");std::map<std::pair<label,label>,unsigned> edges;
   for(const Ring* r:{&pieces[i],&pieces[j]})for(std::size_t k=0;k<r->size();++k)++edges[{(*r)[k],(*r)[(k+1)%r->size()]}];
   bool shared=false;for(auto& e:edges){auto rev=edges.find({e.first.second,e.first.first});
    if(rev!=edges.end()){unsigned n=std::min(e.second,rev->second);e.second-=n;rev->second-=n;shared=shared||n;}}
   if(!shared)continue;std::map<label,label> next;std::set<label> incoming;bool single=true;std::size_t count=0;
   for(const auto& e:edges)if(e.second){if(e.second!=1||!next.emplace(e.first.first,e.first.second).second||!incoming.insert(e.first.second).second)single=false;++count;}
   if(!single||count<3||count>64||next.size()!=incoming.size())continue;
   label start=next.begin()->first;for(const auto& e:next)if(lessPoint(e.first,start))start=e.first;
   Ring ring;label p=start;std::set<label> visited;
   do{if(!visited.insert(p).second||!next.count(p)){single=false;break;}ring.push_back(p);p=next.at(p);}while(p!=start&&ring.size()<=count);
   if(!single||p!=start||ring.size()!=count||!convex(ring)||!numericPositive(ring))continue;
   pieces[i]=std::move(ring);pieces.erase(pieces.begin()+j);merged=true;
  }
 }
 if(!ec.good()||!work())return fail("facet-exact-or-clock");
 const int originalSign=E::sign(signedArea(original));
 if(originalSign!=sign)for(Ring& r:pieces)std::reverse(r.begin()+1,r.end());
 // Prefix exact integrals accept a .points.at() accessor.
 struct Access {At getter;const point& at(std::size_t p)const{return getter(label(p));}};
 struct View {Access points;} view{{at}};
 R::Detail::Integrals total;const auto before=R::Detail::integrals(view,original,ec);
 std::map<std::pair<label,label>,unsigned> edges,outline;std::set<label> used;
 for(const Ring& r:pieces) {
  if(!work()||!numericPositive(r))return fail("facet-piece");
  R::Detail::add(total,R::Detail::integrals(view,r,ec),ec);
  for(std::size_t i=0;i<r.size();++i){++edges[{r[i],r[(i+1)%r.size()]}];used.insert(r[i]);}
 }
 for(auto& e:edges){auto rev=edges.find({e.first.second,e.first.first});
  if(rev!=edges.end()){unsigned n=std::min(e.second,rev->second);e.second-=n;rev->second-=n;}}
 for(std::size_t i=0;i<original.size();++i)++outline[{original[i],original[(i+1)%original.size()]}];
 for(const auto& e:edges)if(e.second!=outline[e.first])return fail("facet-outline");
 for(const auto& e:outline)if(edges[e.first]!=e.second)return fail("facet-outline");
 if(used!=ids)return fail("facet-source-node-cohort");
 if(!ec.good()||!R::Detail::same(before,total,ec)||!work())return fail("facet-exact-source-integral");
 Result result;result.facets=std::move(pieces);result.exactSourceConserved=true;out=std::move(result);return true;
}
} }
#endif
