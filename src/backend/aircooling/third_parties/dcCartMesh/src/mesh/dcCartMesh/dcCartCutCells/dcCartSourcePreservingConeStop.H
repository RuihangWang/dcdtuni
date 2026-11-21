#ifndef dcCartSourcePreservingConeStop_H
#define dcCartSourcePreservingConeStop_H
#include "point.H"
#include "dcCartSharedAxisSeamVolumePartition.H"
#include "dcCartSourceTriangleEdgeConform.H"
#include <set>
#include <map>
#include <limits>
namespace Foam { namespace dcCartSourcePreservingConeStop {
namespace S=dcCartSharedAxisSeamVolumePartition;
namespace T=dcCartSourceTriangleEdgeConform;
using Plane=std::pair<label,scalar>;
struct Prepared {
 S::Plan plan; // Face-restage only: no invented Cell/child lineage.
 std::map<label,std::vector<label>> sourceGroups;
 std::map<label,std::vector<S::Row>> shells;
 std::map<label,S::Geometry> before,after;
 std::set<label> affected;
 std::map<S::Edge,label> edgePoints;
 std::size_t originalPoints=0,originalFaces=0;
 label failedSource=-1,failedParent=-1;
};
template<class Mesh>
bool guardedBirth(const Mesh& raw,const S::PointIndex& index,S::Plan& plan,
 label first,label second,const Plane& cut,label& pointId,
 const std::function<bool()>& available,std::string*why=nullptr) {
 auto fail=[&](const char*s){if(why)*why=s;return false;};
 if(!S::clock(available))return fail("clock");
 if(first<0||second<0||first==second||std::size_t(first)>=raw.points.size()
  ||std::size_t(second)>=raw.points.size()||cut.first<0||cut.first>2||!std::isfinite(cut.second))return fail("edge-input");
 const auto edge=std::minmax(first,second);const point& a=raw.points[edge.first];const point& b=raw.points[edge.second];
 const auto num=[](scalar x){return S::D::detail::number(x);};
 const T::Rational den=num(b[cut.first])-num(a[cut.first]);if(den==0)return fail("edge-parallel");
 const T::Rational t=(num(cut.second)-num(a[cut.first]))/den;
 if(t<=0||t>=1)return fail("edge-not-strict-crossing");
 point candidate;
 for(label k=0;k<3;++k)candidate[k]=k==cut.first?cut.second:S::D::detail::rounded(num(a[k])+t*(num(b[k])-num(a[k])));
 if(!S::finite(candidate))return fail("edge-nonfinite");
 S::GridKey key;if(!S::gridKey(candidate,key))return fail("grid-range");
 for(int x=-1;x<=1;++x)for(int y=-1;y<=1;++y)for(int z=-1;z<=1;++z) {
  if(!S::clock(available))return fail("clock");
  const auto found=index.grid.find({key[0]+x,key[1]+y,key[2]+z});
  if(found!=index.grid.end())for(label p:found->second)
   if(magSqr(raw.points.at(p)-candidate)<=9e-12)return fail("active-point-3um");
 }
 for(const auto& old:plan.births) {
  if(!S::clock(available))return fail("clock");
  if(magSqr(old.position-candidate)<=9e-12)return fail("staged-point-3um");
 }
 if(raw.points.size()+plan.births.size()>=std::size_t(std::numeric_limits<label>::max()))return fail("point-overflow");
 pointId=label(raw.points.size()+plan.births.size());plan.births.push_back({edge.first,edge.second,candidate});return true;
}
template<class Mesh,class Coupled>
bool prepare(const Mesh& raw,const std::vector<std::vector<label>>& cf,
 const std::map<label,Plane>& requests,const S::PointIndex& index,Coupled coupled,
 const std::function<bool()>& available,Prepared& out,std::string*why=nullptr) {
 out=Prepared();Prepared draft;draft.originalPoints=raw.points.size();draft.originalFaces=raw.faces.size();
 label diagnosticSource=-1,diagnosticParent=-1;
 auto fail=[&](const char*s){if(why)*why=s;out.failedSource=diagnosticSource;out.failedParent=diagnosticParent;return false;};
 auto work=[&](){return S::clock(available);};
 if(requests.empty()||cf.size()!=std::size_t(raw.nCells)||index.faces.size()!=raw.points.size())return fail("input");
 std::map<S::Edge,Plane> crossing;
 for(const auto& request:requests) {
  if(!work())return fail("clock");const label f=request.first;diagnosticSource=f;const Plane cut=request.second;
  if(f<0||std::size_t(f)>=raw.faces.size()||!raw.faceAlive[f]||raw.owner[f]<0
   ||raw.neighbour[f]<0||raw.owner[f]>=raw.nCells||raw.neighbour[f]>=raw.nCells
   ||!raw.cellAlive[raw.owner[f]]||!raw.cellAlive[raw.neighbour[f]]||raw.patch[f]>=0
   ||cut.first<0||cut.first>2||!std::isfinite(cut.second))return fail("request-not-real-internal");
  const auto& ring=raw.faces[f];if(ring.size()<3||ring.size()>64)return fail("source-ring");
  bool pos=false,neg=false;
  for(label p:ring) {
   if(p<0||std::size_t(p)>=raw.points.size()||!S::finite(raw.points[p]))return fail("source-point");
   pos=pos||raw.points[p][cut.first]>cut.second;neg=neg||raw.points[p][cut.first]<cut.second;
  }
  if(!pos||!neg)return fail("request-not-mixed");
  for(std::size_t j=0;j<ring.size();++j) {
   label a=ring[j],b=ring[(j+1)%ring.size()];scalar da=raw.points[a][cut.first]-cut.second,db=raw.points[b][cut.first]-cut.second;
   if((da<0&&db>0)||(da>0&&db<0)) {
    const S::Edge e=std::minmax(a,b);auto found=crossing.find(e);
    if(found!=crossing.end()&&found->second!=cut)return fail("conflicting-edge-plane");crossing[e]=cut;
   }
  }
 }
 std::set<label> affectedFaces;
 for(const auto& request:requests) {affectedFaces.insert(request.first);draft.affected.insert(raw.owner[request.first]);draft.affected.insert(raw.neighbour[request.first]);}
 for(const auto& entry:crossing) {
  label p=-1;if(!guardedBirth(raw,index,draft.plan,entry.first.first,entry.first.second,entry.second,p,available,why))return false;
  draft.edgePoints.emplace(entry.first,p);bool foundEdge=false;
  for(label f:index.faces.at(entry.first.first)) {
   if(!work())return fail("clock");if(f<0||std::size_t(f)>=raw.faces.size()||!raw.faceAlive[f])return fail("point-star-face");
   const auto& ring=raw.faces[f];bool contains=false;
   for(std::size_t j=0;j<ring.size();++j)if(S::Edge(std::minmax(ring[j],ring[(j+1)%ring.size()]))==entry.first){contains=true;break;}
   if(!contains)continue;foundEdge=true;
   if(raw.owner[f]<0||raw.owner[f]>=raw.nCells||!raw.cellAlive[raw.owner[f]]
    ||raw.neighbour[f]>=raw.nCells||(raw.neighbour[f]>=0&&!raw.cellAlive[raw.neighbour[f]]))return fail("point-star-cell");
   if(coupled(raw.patch[f]))return fail("point-star-needs-real-processor-peer");
   affectedFaces.insert(f);draft.affected.insert(raw.owner[f]);if(raw.neighbour[f]>=0)draft.affected.insert(raw.neighbour[f]);
  }
  if(!foundEdge)return fail("edge-star-empty");
 }
 label nextFace=label(raw.faces.size());
 auto at=[&](label p)->const point&{return S::coordinate(raw,draft.plan,p);};
 for(label f:affectedFaces) {
  if(!work())return fail("clock");diagnosticSource=f;const auto& original=raw.faces[f];std::vector<label> noded;
  for(std::size_t j=0;j<original.size();++j) {
   const label a=original[j],b=original[(j+1)%original.size()];noded.push_back(a);
   const auto p=draft.edgePoints.find(std::minmax(a,b));if(p!=draft.edgePoints.end())noded.push_back(p->second);
  }
  if(noded.size()<3||noded.size()>64)return fail("noded-source-size");
  std::vector<std::vector<label>> rings;
  const auto request=requests.find(f);
  if(request!=requests.end()) {
   std::vector<label> positive,negative;const auto cut=request->second;
   for(label p:noded){const scalar d=at(p)[cut.first]-cut.second;if(d>=0)positive.push_back(p);if(d<=0)negative.push_back(p);}
   rings.push_back(std::move(positive));rings.push_back(std::move(negative));
  } else if(raw.patch[f]>=0) {
   if(original.size()!=3)return fail("boundary-source-not-triangle");
   T::Source source;source.native=f;source.owner=raw.owner[f];source.neighbour=raw.neighbour[f];
   source.patch=raw.patch[f];source.zone=raw.zone[f];source.sourceKey=std::to_string(f);
   std::copy(original.begin(),original.end(),source.ring.begin());std::vector<T::EdgeBirth> births;
   for(std::size_t j=0;j<original.size();++j) {
    const S::Edge e=std::minmax(original[j],original[(j+1)%original.size()]);const auto p=draft.edgePoints.find(e);if(p==draft.edgePoints.end())continue;
    T::EdgeBirth birth;birth.source=f;birth.first=e.first;birth.second=e.second;birth.point=p->second;
    birth.ordinal=p->second-label(raw.points.size());birth.axis=crossing.at(e).first;birth.value=crossing.at(e).second;
    birth.roundedPosition=at(birth.point);birth.sourceKey=source.sourceKey;births.push_back(std::move(birth));
   }
   T::Result result;std::string cause;
   if(!T::prepare(source,label(raw.points.size()),label(raw.points.size()+draft.plan.births.size()),at,births,available,result,&cause)) {
    if(why)*why="triangle-edge-conform:"+cause;return false;
   }
   if(!result.idealExactConserved)return fail("triangle-ideal-source-integral");
   for(const auto& triangle:result.triangles)rings.emplace_back(triangle.begin(),triangle.end());
  } else rings.push_back(noded);
  std::map<std::pair<label,label>,unsigned> edges;
  std::set<label> covered;vector sourceSF;point sourceFC;S::Plan none;S::metric(raw,none,original,sourceSF,sourceFC);
  vector sumSF=vector::zero;point sumMoment=point::zero;scalar sumArea=0;
  for(const auto& ring:rings) {
   if(!work()||ring.size()<3||ring.size()>64||std::set<label>(ring.begin(),ring.end()).size()!=ring.size())return fail("piece-ring");
   vector sf;point fc;S::metric(raw,draft.plan,ring,sf,fc);
   if(!S::finite(sf)||!S::finite(fc)||mag(sf)<=1e-12||(sf&sourceSF)<=0)return fail("piece-positive-area");
   sumSF+=sf;sumArea+=mag(sf);sumMoment+=mag(sf)*fc;
   for(std::size_t j=0;j<ring.size();++j){++edges[{ring[j],ring[(j+1)%ring.size()]}];covered.insert(ring[j]);}
  }
  for(label p:noded)if(!covered.count(p))return fail("source-node-cohort");
  for(auto& entry:edges){auto reverse=edges.find({entry.first.second,entry.first.first});if(reverse!=edges.end()){unsigned n=std::min(entry.second,reverse->second);entry.second-=n;reverse->second-=n;}}
  std::map<std::pair<label,label>,unsigned> outline;
  for(std::size_t j=0;j<noded.size();++j)++outline[{noded[j],noded[(j+1)%noded.size()]}];
  for(const auto& entry:edges)if(entry.second!=outline[entry.first])return fail("source-outline");
  for(const auto& entry:outline)if(edges[entry.first]!=entry.second)return fail("source-outline");
  const scalar area=mag(sourceSF),scale=std::max(scalar(1),mag(sourceFC));
  if(mag(sumSF-sourceSF)>1e-10*area||std::abs(sumArea-area)>1e-10*area
   ||mag(sumMoment-area*sourceFC)>1e-10*area*scale)return fail("source-area-moment");
  auto& group=draft.sourceGroups[f];
  for(std::size_t k=0;k<rings.size();++k) {
   if(k&&nextFace==std::numeric_limits<label>::max())return fail("face-overflow");
   const label native=k?nextFace++:f;group.push_back(native);
   draft.plan.rows.push_back({native,f,raw.owner[f],raw.neighbour[f],raw.patch[f],raw.zone[f],std::move(rings[k])});
  }
 }
 std::map<label,const S::Row*> byNative;for(const auto& row:draft.plan.rows)byNative.emplace(row.native,&row);
 diagnosticSource=-1;
 for(label c:draft.affected) {
  diagnosticParent=c;
  if(!work()||c<0||c>=raw.nCells||!raw.cellAlive[c])return fail("affected-cell");
  std::vector<S::Face> oldShell,newShell;
  for(label f:cf[c]) {
   if(!work()||f<0||std::size_t(f)>=raw.faces.size()||!raw.faceAlive[f]
    ||(raw.owner[f]!=c&&raw.neighbour[f]!=c))return fail("affected-inventory");
   oldShell.push_back({f,raw.faces[f],raw.owner[f]==c});
   const auto group=draft.sourceGroups.find(f);
   if(group==draft.sourceGroups.end())draft.shells[c].push_back({f,f,raw.owner[f],raw.neighbour[f],raw.patch[f],raw.zone[f],raw.faces[f]});
   else for(label native:group->second)draft.shells[c].push_back(*byNative.at(native));
  }
  for(const auto& row:draft.shells[c])newShell.push_back({row.native,row.ring,row.owner==c});
  S::Geometry oldGeometry,newGeometry;S::Plan none;const point reference=raw.points[oldShell.front().ring.front()];
  if(!S::geometry(raw,none,oldShell,reference,available,oldGeometry))return fail("original-full-shell-geometry");
  if(!S::geometry(raw,draft.plan,newShell,reference,available,newGeometry))return fail("restaged-full-shell-geometry");
  const scalar span=std::max(mag(oldGeometry.hi-oldGeometry.lo),scalar(1e-6));
  if(std::abs(newGeometry.volume-oldGeometry.volume)>1e-10*std::max(oldGeometry.volume,scalar(VSMALL)))return fail("OF-volume-conservation");
  for(unsigned k=0;k<4;++k)if(std::abs(newGeometry.physical[k]-oldGeometry.physical[k])/std::max(oldGeometry.physical[0]*(k?span:1),static_cast<long double>(VSMALL))>1e-10L)return fail("physical-V-M-conservation");
  draft.before.emplace(c,oldGeometry);draft.after.emplace(c,newGeometry);
 }
 if(!work())return fail("clock");draft.plan.ready=true;out=std::move(draft);return true;
}
} }
#endif
