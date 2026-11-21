#ifndef dcCartExactCutPlaneBacktracks_H
#define dcCartExactCutPlaneBacktracks_H
#include "dcCartPreparedFacetPartition.H"
#include "point.H"
#include "label.H"
#include <array>
#include <vector>
#include <set>
#include <functional>
#include <algorithm>
#include <string>
#include <cmath>
namespace Foam { namespace dcCartExactCutPlaneBacktracks {
namespace D=::dcCartPreparedFacetPartition;
using Exact=D::Rational;
using Integral=std::array<Exact,12>;
struct Result {std::vector<label> positiveRemoved,negativeRemoved;};
struct ClockRefusal {};
inline bool available(const std::function<bool()>& work) {return !work||work();}
template<class At>
Integral integral(const std::vector<label>& ring,const At& at,const point& reference,
 const std::function<bool()>& work=std::function<bool()>()) {
 Integral out{};
 for(std::size_t i=0;i<ring.size();++i){if(!available(work))throw ClockRefusal();const point& p=at(ring[i]);const point& q=at(ring[(i+1)%ring.size()]);std::array<Exact,3> a,b;
  for(label k=0;k<3;++k){a[k]=D::detail::number(p[k])-D::detail::number(reference[k]);b[k]=D::detail::number(q[k])-D::detail::number(reference[k]);}
  const std::array<Exact,3> n{{a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]}};
  for(label k=0;k<3;++k){out[k]+=n[k]/2;for(label j=0;j<3;++j)out[3+3*k+j]+=n[k]*(a[j]+b[j])/6;}
 }return out;
}
// Caller provides complete current point cohorts. This does not give ownership,
// original-source identity, processor, material, or geometry permission.
// Ordinary in-order collinear vertices are retained. No new point is created.
template<class At,class Cohort>
bool normalize(const std::vector<label>& original,label axis,scalar cut,
 const At& at,const Cohort& completeCohort,const std::function<bool()>& work,
 std::vector<label>& positive,std::vector<label>& negative,Result& result,
 std::string* why=nullptr) {
 result=Result();if(why)why->clear();const auto fail=[&](const char* reason){if(why)*why=reason;return false;};
 if(axis<0||axis>2||!std::isfinite(cut)||original.size()<3||original.size()>64
    ||positive.size()<3||positive.size()>64||negative.size()<3||negative.size()>64)return fail("cut-backtrack-input");
 try {
  if(!available(work))return fail("cut-backtrack-clock");
  const std::set<label> originalNodes(original.begin(),original.end());
  if(originalNodes.size()!=original.size())return fail("cut-backtrack-original-label-alias");
  auto unionHeld=[&](const std::vector<label>& p,const std::vector<label>& n){std::set<label> nodes(p.begin(),p.end());nodes.insert(n.begin(),n.end());for(label old:originalNodes)if(!nodes.count(old))return false;return true;};
  if(!unionHeld(positive,negative))return fail("cut-backtrack-before-node-union");
  // Cheap exact comparisons exclude ordinary in-order chains. Rational area
  // and moment work is needed only for a possible on-plane reversal.
  const auto possible=[&](const std::vector<label>& ring){for(std::size_t i=0;i<ring.size();++i){const point& a=at(ring[(i+ring.size()-1)%ring.size()]);const point& b=at(ring[i]);const point& c=at(ring[(i+1)%ring.size()]);if(a[axis]!=cut||b[axis]!=cut||c[axis]!=cut)continue;for(label k=0;k<3;++k)if((b[k]>a[k]&&c[k]<b[k])||(b[k]<a[k]&&c[k]>b[k]))return true;}return false;};
  if(!possible(positive)&&!possible(negative))return available(work)?true:fail("cut-backtrack-clock");
  std::vector<label> p=positive,n=negative;Result observed;
  const point reference=at(original.front());const Integral pBefore=integral(p,at,reference,work),nBefore=integral(n,at,reference,work);
  const auto oneSide=[&](std::vector<label>& ring,const std::vector<label>& other,std::vector<label>& removed)->bool {
   for(std::size_t step=0;step<64;++step){if(!available(work))return fail("cut-backtrack-clock");bool changed=false;
    for(std::size_t i=0;i<ring.size();++i){if(!available(work))return fail("cut-backtrack-clock");const label a=ring[(i+ring.size()-1)%ring.size()],b=ring[i],c=ring[(i+1)%ring.size()];
     if(a==b||b==c||a==c)continue;const point& pa=at(a);const point& pb=at(b);const point& pc=at(c);
     // Equality refers to the actual stored cut plane; near points are retained.
     if(pa[axis]!=cut||pb[axis]!=cut||pc[axis]!=cut)continue;
     std::array<Exact,3> ab,bc;Exact product=0;
     for(label k=0;k<3;++k){ab[k]=D::detail::number(pb[k])-D::detail::number(pa[k]);bc[k]=D::detail::number(pc[k])-D::detail::number(pb[k]);product+=ab[k]*bc[k];}
     if(ab[1]*bc[2]-ab[2]*bc[1]!=0||ab[2]*bc[0]-ab[0]*bc[2]!=0||ab[0]*bc[1]-ab[1]*bc[0]!=0||product>=0)continue;
     // Exact reversal alone is insufficient: the SAME label must be retained
     // in the other actual fragment and its complete active source cohort.
     if(std::find(other.begin(),other.end(),b)==other.end())return fail("cut-backtrack-opposite-node-union");
     if(!completeCohort(b))return fail(available(work)?"cut-backtrack-source-cohort":"cut-backtrack-clock");
     if(ring.size()<=3)return fail("cut-backtrack-degenerate-fragment");
     removed.push_back(b);ring.erase(ring.begin()+i);changed=true;break;
    }
    if(!changed)return true;
   }return fail("cut-backtrack-step-bound");
  };
  if(!oneSide(p,n,observed.positiveRemoved)||!oneSide(n,p,observed.negativeRemoved))return false;
  if(!unionHeld(p,n))return fail("cut-backtrack-after-node-union");
  if(integral(p,at,reference,work)!=pBefore||integral(n,at,reference,work)!=nBefore)return fail("cut-backtrack-exact-area-moment");
  if(!available(work))return fail("cut-backtrack-clock");
  positive=std::move(p);negative=std::move(n);result=std::move(observed);return true;
 }catch(const ClockRefusal&){return fail("cut-backtrack-clock");}
 catch(...){return fail("cut-backtrack-exact-arithmetic");}
}
} }
#endif
