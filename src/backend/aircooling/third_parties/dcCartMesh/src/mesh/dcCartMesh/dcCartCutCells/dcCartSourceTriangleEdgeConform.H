#ifndef dcCartSourceTriangleEdgeConform_H
#define dcCartSourceTriangleEdgeConform_H
// Geometric staging only. Never material/source/stock/distance permission.
#include "point.H"
#include "dcCartPreparedFacetPartition.H"
#include <array>
#include <vector>
#include <map>
#include <set>
#include <string>
#include <functional>
#include <algorithm>
#include <cstring>
#include <cmath>
#include <limits>
#include <new>
namespace Foam { namespace dcCartSourceTriangleEdgeConform {
namespace D=::dcCartPreparedFacetPartition;
using Rational=D::Rational;
using ExactPoint=std::array<Rational,3>;
struct Source {
 label native=-1,owner=-1,neighbour=-1,patch=-1,zone=-1;
 std::array<label,3> ring{{-1,-1,-1}};
 std::string sourceKey;
};
struct EdgeBirth {
 label source=-1,first=-1,second=-1,point=-1,ordinal=-1,axis=-1;
 scalar value=0;
 Foam::point roundedPosition=Foam::point::zero;
 std::string sourceKey;
};
struct Result {
 std::vector<std::array<label,3>> triangles;
 std::vector<label> outline;
 bool idealExactConserved=false,storedSurfaceExactlyConserved=false;
 Rational storedSixVolumeDelta;
 std::array<Rational,3> storedTwentyFourMomentDelta;
 std::array<Rational,3> storedDoubleAreaDelta;
 scalar relativeVectorAreaError=0,relativeScalarAreaError=0;
 // Caller must still apply its complete-shell true V/M, physical source,
 // active-point 3um, actual endpoint stock and fresh publication gates.
};
inline bool work(const std::function<bool()>& available)
 {return !available||available();}
inline bool finite(const point& p)
 {return std::isfinite(p.x())&&std::isfinite(p.y())&&std::isfinite(p.z());}
inline bool bits(const point& a,const point& b)
 {return std::memcmp(&a[0],&b[0],3*sizeof(scalar))==0;}
inline ExactPoint exact(const point& p)
 {return {{D::detail::number(p.x()),D::detail::number(p.y()),D::detail::number(p.z())}};}
inline ExactPoint subtract(const ExactPoint& a,const ExactPoint& b)
 {return {{a[0]-b[0],a[1]-b[1],a[2]-b[2]}};}
inline ExactPoint cross(const ExactPoint& a,const ExactPoint& b)
 {return {{a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]}};}
inline Rational dot(const ExactPoint& a,const ExactPoint& b)
 {return a[0]*b[0]+a[1]*b[1]+a[2]*b[2];}
struct Surface {
 ExactPoint doubleArea;Rational sixVolume;ExactPoint twentyFourMoment;
};
inline Surface surface(const std::array<ExactPoint,3>& q) {
 Surface s;s.doubleArea=cross(subtract(q[1],q[0]),subtract(q[2],q[0]));
 s.sixVolume=dot(q[0],cross(q[1],q[2]));
 for(label k=0;k<3;++k)s.twentyFourMoment[k]=s.sixVolume*(q[0][k]+q[1][k]+q[2][k]);
 return s;
}
inline void add(Surface& a,const Surface& b) {
 a.sixVolume+=b.sixVolume;
 for(label k=0;k<3;++k){a.doubleArea[k]+=b.doubleArea[k];a.twentyFourMoment[k]+=b.twentyFourMoment[k];}
}
// Every birth is a declared original-native-edge exact Rational crossing,
// rounded identically to Shared::prepare. No new labels are allocated here.
// One original edge per source is supported; multiple-edge requests refuse.
template<class Coordinates>
bool prepare(const Source& source,label originalPointCount,label pointCount,
 const Coordinates& at,const std::vector<EdgeBirth>& births,
 const std::function<bool()>& available,Result& out,std::string* why=nullptr) {
 out=Result();auto refuse=[&](const char* r){if(why)*why=r;out=Result();return false;};
 try {
  if(!work(available))return refuse("clock");
  if(sizeof(scalar)!=sizeof(double)||!std::numeric_limits<scalar>::is_iec559
    ||source.native<0||source.owner<0||source.neighbour!=-1||source.patch<0
    ||source.sourceKey.empty()||originalPointCount<3||pointCount<originalPointCount
    ||births.size()>8)return refuse("source-triangle-input");
  std::set<label> original;
  std::array<ExactPoint,3> q;
  for(label i=0;i<3;++i) {
   const label id=source.ring[i];if(id<0||id>=originalPointCount||!original.insert(id).second||!finite(at(id)))return refuse("original-node-domain");
   q[i]=exact(at(id));
  }
  const Surface before=surface(q);if(dot(before.doubleArea,before.doubleArea)<=0)return refuse("degenerate-source");
  label selectedEdge=-1;std::map<Rational,const EdgeBirth*> ordered;
  std::map<label,ExactPoint> ideal;
  for(label i=0;i<3;++i)ideal.emplace(source.ring[i],q[i]);
  std::set<label> newIds,ordinals;
  for(const auto& birth:births) {
   if(!work(available))return refuse("clock");
   if(birth.source!=source.native||birth.sourceKey!=source.sourceKey||birth.first<0
     ||birth.second<=birth.first||birth.first>=originalPointCount||birth.second>=originalPointCount
     ||birth.ordinal<0||birth.ordinal>=pointCount-originalPointCount
     ||birth.point!=originalPointCount+birth.ordinal||birth.axis<0||birth.axis>2
     ||!std::isfinite(birth.value)||!finite(birth.roundedPosition)
     ||!newIds.insert(birth.point).second||!ordinals.insert(birth.ordinal).second)
     return refuse("birth-metadata");
   label edge=-1;
   for(label i=0;i<3;++i)if(std::pair<label,label>(std::minmax(source.ring[i],source.ring[(i+1)%3]))==std::make_pair(birth.first,birth.second))edge=i;
   if(edge<0)return refuse("birth-not-source-edge");
   if(selectedEdge>=0&&selectedEdge!=edge)return refuse("multiple-source-edges-unsupported");
   selectedEdge=edge;
   const point& a=at(birth.first);const point& b=at(birth.second);
   const Rational da=D::detail::number(a[birth.axis]),db=D::detail::number(b[birth.axis]);
   if(da==db)return refuse("birth-axis-not-crossed");
   const Rational t=(D::detail::number(birth.value)-da)/(db-da);
   if(!(t>0&&t<1))return refuse("birth-not-strict-crossing");
   const ExactPoint ea=exact(a),eb=exact(b);ExactPoint ep;point stored;
   for(label k=0;k<3;++k) {
    ep[k]=ea[k]+t*(eb[k]-ea[k]);
    stored[k]=k==birth.axis?birth.value:D::detail::rounded(ep[k]);
   }
   if(!finite(at(birth.point))||!bits(stored,birth.roundedPosition)||!bits(stored,at(birth.point)))return refuse("birth-rounded-position");
   const Rational forward=source.ring[edge]==birth.first?t:Rational(1)-t;
   if(!ordered.emplace(forward,&birth).second)return refuse("duplicate-edge-parameter");
   ideal.emplace(birth.point,std::move(ep));
  }
  Result result;
  for(label i=0;i<3;++i) {
   result.outline.push_back(source.ring[i]);
   if(i==selectedEdge)for(const auto& node:ordered)result.outline.push_back(node.second->point);
  }
  if(selectedEdge<0)result.triangles.push_back(source.ring);
  else {
   label previous=source.ring[selectedEdge];const label hub=source.ring[(selectedEdge+2)%3];
   for(const auto& node:ordered){result.triangles.push_back({{previous,node.second->point,hub}});previous=node.second->point;}
   result.triangles.push_back({{previous,source.ring[(selectedEdge+1)%3],hub}});
  }
  Surface idealSum,actualSum;std::map<std::pair<label,label>,label> directed;
  std::set<label> complete;scalar triangleArea=0;
  for(const auto& tri:result.triangles) {
   if(!work(available))return refuse("clock");
   std::array<ExactPoint,3> iq,aq;
   for(label i=0;i<3;++i){iq[i]=ideal.at(tri[i]);aq[i]=exact(at(tri[i]));complete.insert(tri[i]);++directed[{tri[i],tri[(i+1)%3]}];}
   const Surface ip=surface(iq),ap=surface(aq);
   if(dot(ip.doubleArea,before.doubleArea)<=0||dot(ap.doubleArea,before.doubleArea)<=0)return refuse("nonpositive-source-piece");
   add(idealSum,ip);add(actualSum,ap);
   triangleArea+=mag((at(tri[1])-at(tri[0]))^(at(tri[2])-at(tri[0])));
  }
  std::map<std::pair<label,label>,label> expected;
  for(std::size_t i=0;i<result.outline.size();++i)++expected[{result.outline[i],result.outline[(i+1)%result.outline.size()]}];
  for(auto& edge:directed) {
   const auto reverse=directed.find({edge.first.second,edge.first.first});
   if(reverse!=directed.end()&&edge.second&&reverse->second){const label n=std::min(edge.second,reverse->second);edge.second-=n;reverse->second-=n;}
  }
  for(auto it=directed.begin();it!=directed.end();)if(!it->second)it=directed.erase(it);else++it;
  std::set<label> requested(source.ring.begin(),source.ring.end());requested.insert(newIds.begin(),newIds.end());
  if(directed!=expected||complete!=requested)return refuse("source-outline-or-node-cohort");
  if(idealSum.doubleArea!=before.doubleArea||idealSum.sixVolume!=before.sixVolume
    ||idealSum.twentyFourMoment!=before.twentyFourMoment)return refuse("ideal-source-conservation");
  result.idealExactConserved=true;result.storedSurfaceExactlyConserved=true;
  result.storedSixVolumeDelta=actualSum.sixVolume-before.sixVolume;
  if(result.storedSixVolumeDelta!=0)result.storedSurfaceExactlyConserved=false;
  vector delta=vector::zero;
  for(label k=0;k<3;++k) {
   result.storedDoubleAreaDelta[k]=actualSum.doubleArea[k]-before.doubleArea[k];
   result.storedTwentyFourMomentDelta[k]=actualSum.twentyFourMoment[k]-before.twentyFourMoment[k];
   delta[k]=D::detail::rounded(result.storedDoubleAreaDelta[k]);
   if(result.storedDoubleAreaDelta[k]!=0||result.storedTwentyFourMomentDelta[k]!=0)result.storedSurfaceExactlyConserved=false;
  }
  const scalar originalArea=mag((at(source.ring[1])-at(source.ring[0]))^(at(source.ring[2])-at(source.ring[0])));
  if(!std::isfinite(originalArea)||originalArea<=0||!std::isfinite(triangleArea))return refuse("source-area-nonfinite");
  result.relativeVectorAreaError=mag(delta)/originalArea;
  result.relativeScalarAreaError=std::abs(triangleArea-originalArea)/originalArea;
  // Existing Shared/canonical source-conservation budget. Full-shell true V/M
  // must use the stored residuals and the caller's unchanged complete shell gate.
  if(!std::isfinite(result.relativeVectorAreaError)||!std::isfinite(result.relativeScalarAreaError)
    ||result.relativeVectorAreaError>1e-10||result.relativeScalarAreaError>1e-10)return refuse("stored-source-area");
  if(!work(available))return refuse("clock");out=std::move(result);return true;
 }catch(const std::bad_alloc&){return refuse("allocation");}
  catch(const std::exception&){return refuse("source-exact-unknown");}
}
} }
#endif
