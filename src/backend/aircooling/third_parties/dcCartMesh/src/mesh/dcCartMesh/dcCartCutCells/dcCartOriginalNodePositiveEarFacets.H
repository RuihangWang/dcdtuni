#ifndef dcCartOriginalNodePositiveEarFacets_H
#define dcCartOriginalNodePositiveEarFacets_H
#include "point.H"
#include "dcCartSourcePlanarConeGroups.H"
#include <array>
#include <map>
#include <set>
#include <vector>
#include <functional>
#include <limits>
#include <cmath>
namespace Foam { namespace dcCartOriginalNodePositiveEarFacets {
namespace E=dcCartExactBinary64Geometry;
namespace G=dcCartSourcePlanarConeGroups;
using Ring=std::vector<label>;
struct Integrals {std::array<E::Dyadic,12> surface;E::Dyadic sixV;std::array<E::Dyadic,3> twentyFourM;};
inline bool same(const Integrals& a,const Integrals& b,E::Context& c)
{
 for(unsigned k=0;k<12;++k)if(E::sign(E::subtract(a.surface[k],b.surface[k],c)))return false;
 if(E::sign(E::subtract(a.sixV,b.sixV,c)))return false;
 for(unsigned k=0;k<3;++k)if(E::sign(E::subtract(a.twentyFourM[k],b.twentyFourM[k],c)))return false;
 return c.good();
}
inline void add(Integrals& a,const Integrals& b,E::Context& c)
{
 for(unsigned k=0;k<12;++k)a.surface[k]=E::add(a.surface[k],b.surface[k],c);
 a.sixV=E::add(a.sixV,b.sixV,c);
 for(unsigned k=0;k<3;++k)a.twentyFourM[k]=E::add(a.twentyFourM[k],b.twentyFourM[k],c);
}
template<class Coordinate>
Integrals integrals(const Ring& r,Coordinate at,E::Context& c)
{
 auto exact=[&](label id){const point& p=at(id);return E::vector({{p.x(),p.y(),p.z()}},c);};
 Integrals out;const auto a=exact(r.front());
 for(std::size_t j=1;j+1<r.size();++j)
 {
  const auto b=exact(r[j]),d=exact(r[j+1]);const auto n=E::cross(E::difference(b,a,c),E::difference(d,a,c),c);
  const auto v=E::dot(a,E::cross(b,d,c),c);out.sixV=E::add(out.sixV,v,c);
  for(unsigned k=0;k<3;++k)
  {
   const auto sum=E::add(E::add(a[k],b[k],c),d[k],c);
   out.surface[k]=E::add(out.surface[k],n[k],c);
   out.twentyFourM[k]=E::add(out.twentyFourM[k],E::multiply(v,sum,c),c);
   for(unsigned axis=0;axis<3;++axis)out.surface[3+3*axis+k]=E::add(out.surface[3+3*axis+k],E::multiply(n[axis],sum,c),c);
  }
 }
 return out;
}
// Proposal eligibility only: this conservative floating screen never grants
// planarity, convexity, a zero turn, quality, source or publication authority.
// All actual straight-turn/plane/convex predicates below remain exact.
template<class Coordinate>
bool possibleStraightTurn(const Ring& r,Coordinate at,const std::function<bool()>& available)
{
 const scalar e=512*std::numeric_limits<scalar>::epsilon();
 const scalar tiny=128*std::numeric_limits<scalar>::denorm_min();
 for(std::size_t j=0;j<r.size();++j)
 {
  if(available&&!available())return true; // Exact path reports the same deadline.
  const point& x=at(r[j]);const vector a=at(r[(j+r.size()-1)%r.size()])-x,b=at(r[(j+1)%r.size()])-x;
  bool near=true;scalar dot=0,bound=0;
  for(label k=0;k<3;++k)
  {
   const label u=(k+1)%3,v=(k+2)%3;const scalar first=a[u]*b[v],second=a[v]*b[u];
   const scalar cross=first-second,error=e*(std::abs(first)+std::abs(second))+tiny;
   const scalar product=a[k]*b[k];dot+=product;bound+=std::abs(product);
   if(!std::isfinite(cross)||!std::isfinite(error)||!std::isfinite(product))return true;
   if(std::abs(cross)>error)near=false;
  }
  if(!std::isfinite(dot)||!std::isfinite(bound))return true;
  if(near&&dot<=e*bound+tiny)return true;
 }
 return false;
}
// Atomic source-fragment representation only, all existing points retained.
// metric is the calling production ring metric, not a substituted FC formula.
// Caller must consume every piece in complete shells, remeasure true CC/V and
// check every actual endpoint with original Stock/source/peer/quality gates.
template<class Coordinate,class Metric>
bool partition(const Ring& input,Coordinate at,Metric metric,
 const std::function<bool()>& available,std::vector<Ring>& out,
 bool& changed,std::string* why=nullptr,bool keepWeakConvexRing=false)
{
 out.clear();changed=false;
 const auto work=[&](){return !available||available();};
 const auto fail=[&](const char* reason){if(why)*why=reason;return false;};
 if(!work())return fail("positive-ears-clock");
 if(input.size()<3||input.size()>64)return fail("positive-ears-ring");
 try
 {
  if(input.size()==3||!possibleStraightTurn(input,at,available))
  {if(!work())return fail("positive-ears-clock");out.push_back(input);return true;}
  std::set<label> unique;std::vector<G::Point> localPoints;G::Ring localRing;
  for(label id:input)
  {
   if(!work())return fail("positive-ears-clock");const point& p=at(id);
   if(id<0||!unique.insert(id).second||!std::isfinite(p.x())||!std::isfinite(p.y())||!std::isfinite(p.z()))return fail("positive-ears-point");
   localRing.push_back(localPoints.size());localPoints.push_back({{p.x(),p.y(),p.z()}});
  }
  E::Context context(work);E::Vector exactNormal,origin;
  const bool eligible=G::Detail::plane(localRing,localPoints,exactNormal,origin,context);
  if(!context.good())return fail("positive-ears-exact-or-clock");
  if(!eligible){out.push_back(input);return true;}
  // Exact whole-source clip representation: retain every original outline node.
  // This chooses a ring representation; Source and full Stock gates remain with the caller.
  if(keepWeakConvexRing)
  {if(!work())return fail("positive-ears-clock");out.push_back(input);return true;}
  bool collinear=false;std::vector<label> straightIds;
  for(std::size_t j=0;j<localPoints.size();++j)
  {
   const auto x=E::vector(localPoints[j],context);
   const auto a=E::difference(E::vector(localPoints[(j+localPoints.size()-1)%localPoints.size()],context),x,context),b=E::difference(E::vector(localPoints[(j+1)%localPoints.size()],context),x,context);
   const auto cross=E::cross(a,b,context);
   if(!E::sign(cross[0])&&!E::sign(cross[1])&&!E::sign(cross[2])&&E::sign(E::dot(a,b,context))<0)
   {collinear=true;straightIds.push_back(input[j]);}
  }
  if(!context.good())return fail("positive-ears-exact-or-clock");
  if(!collinear){out.push_back(input);return true;}
  auto less=[&](label a,label b){const point& x=at(a);const point& y=at(b);for(label k=0;k<3;++k){if(x[k]<y[k])return true;if(x[k]>y[k])return false;}return false;};
  Ring poly=input;label bestStart=0,bestDirection=1;bool haveBest=false;
  for(label direction:{label(1),label(-1)})for(label start=0;start<label(input.size());++start)
  {
   if(!work())return fail("positive-ears-clock");bool better=false;
   if(haveBest)for(label j=0;j<label(input.size());++j){const label a=input[(start+direction*j+label(input.size())*2)%input.size()],b=input[(bestStart+bestDirection*j+label(input.size())*2)%input.size()];if(less(a,b)){better=true;break;}if(less(b,a))break;}
   if(!haveBest||better){bestStart=start;bestDirection=direction;haveBest=true;}
  }
  for(label j=0;j<label(input.size());++j)poly[j]=input[(bestStart+bestDirection*j+label(input.size())*2)%input.size()];
  auto finite=[](const point& p){return std::isfinite(p.x())&&std::isfinite(p.y())&&std::isfinite(p.z());};
  auto writable=[&](const Ring& ring){vector n=vector::zero;for(std::size_t j=0;j<ring.size();++j){const point& a=at(ring[j]);const point& b=at(ring[(j+1)%ring.size()]);n.x()+=(a.y()-b.y())*(a.z()+b.z());n.y()+=(a.z()-b.z())*(a.x()+b.x());n.z()+=(a.x()-b.x())*(a.y()+b.y());}n*=.5;return finite(n)&&mag(n)>1e-12;};
  vector av,original;point fc,originalFc;metric(poly,av,fc);metric(input,original,originalFc);const scalar amount=mag(av);
  if(!finite(av)||!finite(fc)||!finite(original)||!finite(originalFc)||!std::isfinite(amount)||amount<=VSMALL)return fail("positive-ears-metric");
  const vector normal=av/amount;std::vector<Ring> pieces;
  // A different birth chord can avoid a sliver which the first fan's
  // adjacent unions cannot remove. Scores only order proposals; the full
  // exact source/outline and unchanged production metric gates still follow.
  bool chordPartition=false;
  if(straightIds.size()==1||straightIds.size()==2)
  {
   std::vector<std::size_t> straightPositions;
   for(std::size_t j=0;j<poly.size();++j)
    if(std::find(straightIds.begin(),straightIds.end(),poly[j])!=straightIds.end())
     straightPositions.push_back(j);
   if(straightPositions.size()!=straightIds.size())return fail("positive-ears-chord-node");
   const std::size_t first=straightPositions.front();
   struct Chord {std::vector<Ring> rings;scalar minimum;std::size_t second;};
   std::vector<Chord> candidates;
   for(std::size_t second=0;second<poly.size();++second)
   {
    if(!work())return fail("positive-ears-clock");
    if(straightPositions.size()==2&&second!=straightPositions[1])continue;
    if(first==second||(first+1)%poly.size()==second||(second+1)%poly.size()==first)continue;
    std::vector<Ring> two(2);
    for(std::size_t j=first;;j=(j+1)%poly.size())
    {two[0].push_back(poly[j]);if(j==second)break;}
    for(std::size_t j=second;;j=(j+1)%poly.size())
    {two[1].push_back(poly[j]);if(j==first)break;}
    if(bestDirection<0)for(auto& ring:two)std::reverse(ring.begin()+1,ring.end());
    scalar minimum=std::numeric_limits<scalar>::max(),sumArea=0;
    vector sumSf=vector::zero;point areaMoment=point::zero;bool usable=true;
    for(const auto& ring:two)
    {
     vector sf;point centre;metric(ring,sf,centre);const scalar a=mag(sf);
     if(!finite(sf)||!finite(centre)||!std::isfinite(a)||a<=VSMALL
        ||!std::isfinite(sf&original)||(sf&original)<=0||!writable(ring))
     {usable=false;break;}
     minimum=std::min(minimum,a);sumSf+=sf;sumArea+=a;areaMoment+=a*centre;
    }
    // Repeat the original final metric gate before selecting a proposal,
    // so a rejected chord cannot hide a later eligible chord or old fallback.
    const scalar oldArea=mag(original),scale=std::max(std::sqrt(oldArea),scalar(1e-6));
    if(!usable||!finite(sumSf)||!finite(areaMoment)||!std::isfinite(sumArea)
       ||oldArea<=VSMALL||mag(sumSf-original)/oldArea>1e-10
       ||mag(sumArea-oldArea)/oldArea>1e-10
       ||mag(areaMoment/std::max(sumArea,scalar(VSMALL))-originalFc)/scale>1e-10)continue;
    candidates.push_back({std::move(two),minimum,second});
   }
   std::sort(candidates.begin(),candidates.end(),[](const Chord& a,const Chord& b)
   {return a.minimum!=b.minimum?a.minimum>b.minimum:a.second<b.second;});
   if(!candidates.empty())
   {
    std::map<label,G::Label> localIndex;
    for(std::size_t j=0;j<input.size();++j)localIndex.emplace(input[j],G::Label(j));
    const G::Ring expectedRing(input.begin(),input.end());
    const auto sourceIntegral=integrals(input,at,context);
    if(!context.good())return fail("positive-ears-exact-or-clock");
    for(const auto& candidate:candidates)
    {
     if(!work())return fail("positive-ears-clock");
     std::vector<G::Ring> local,global;std::set<label> covered;
     for(const auto& ring:candidate.rings)
     {
      global.emplace_back(ring.begin(),ring.end());local.emplace_back();
      for(label id:ring){covered.insert(id);local.back().push_back(localIndex.at(id));}
     }
     G::Ring outer;
     if(covered!=unique||!G::Detail::outline(global,outer,context)
        ||!G::Detail::sameCycle(outer,expectedRing))
     {
      if(!context.good())return fail("positive-ears-exact-or-clock");
      continue;
     }
     bool positive=true;
     for(const auto& ring:local)
     {
      for(std::size_t j=0;j<ring.size();++j)
      {
       if(!context.take())return fail("positive-ears-exact-or-clock");
       const auto a=E::vector(localPoints[ring[(j+ring.size()-1)%ring.size()]],context);
       const auto b=E::vector(localPoints[ring[j]],context);
       const auto c=E::vector(localPoints[ring[(j+1)%ring.size()]],context);
       if(E::sign(E::dot(E::cross(E::difference(b,a,context),
            E::difference(c,b,context),context),exactNormal,context))<=0)
       {positive=false;break;}
      }
      if(!positive)break;
     }
     if(!context.good())return fail("positive-ears-exact-or-clock");
     if(!positive)continue;
     for(const auto& ring:local)
      if(!G::Detail::aligned(ring,localPoints,exactNormal,origin,context))
      {positive=false;break;}
     if(!context.good())return fail("positive-ears-exact-or-clock");
     if(!positive)continue;
     Integrals total;
     for(const auto& ring:candidate.rings)add(total,integrals(ring,at,context),context);
     if(!same(sourceIntegral,total,context))
     {
      if(!context.good())return fail("positive-ears-exact-or-clock");
      continue;
     }
     pieces=candidate.rings;chordPartition=true;break;
    }
   }
  }
  if(!chordPartition)
  {
  for(std::size_t k=0;k<poly.size();++k)
  {
   if(!work())return fail("positive-ears-clock");std::vector<Ring> trial;bool positive=true;
   for(std::size_t j=1;j+1<poly.size();++j){const label a=poly[k],b=poly[(k+j)%poly.size()],c=poly[(k+j+1)%poly.size()];const scalar area=(((at(b)-at(a))^(at(c)-at(a)))&normal)*.5;if(!std::isfinite(area)||area<=1e-15*amount||!writable({a,b,c})){positive=false;break;}trial.push_back({a,b,c});}
   if(positive){pieces=std::move(trial);break;}
  }
  if(pieces.empty())
  {
   Ring remaining=poly;std::vector<Ring> trial;
   while(remaining.size()>3)
   {
    if(!work())return fail("positive-ears-clock");bool clipped=false;
    for(std::size_t i=0;i<remaining.size();++i)
    {
     if(!work())return fail("positive-ears-clock");const label a=remaining[(i+remaining.size()-1)%remaining.size()],b=remaining[i],c=remaining[(i+1)%remaining.size()];const scalar area=(((at(b)-at(a))^(at(c)-at(a)))&normal)*.5;
     if(!std::isfinite(area)||area<=1e-15*amount||!writable({a,b,c}))continue;
     Ring next=remaining;next.erase(next.begin()+i);vector sf;point centre;metric(next,sf,centre);
     if(!finite(sf)||!finite(centre)||!writable(next)||!std::isfinite(sf&normal)||(sf&normal)<=1e-15*amount)continue;
     trial.push_back({a,b,c});remaining=std::move(next);clipped=true;break;
    }
    if(!clipped){trial.clear();break;}
   }
   if(remaining.size()==3&&!trial.empty()){const scalar area=(((at(remaining[1])-at(remaining[0]))^(at(remaining[2])-at(remaining[0])))&normal)*.5;if(std::isfinite(area)&&area>1e-15*amount&&writable(remaining)){trial.push_back(remaining);pieces=std::move(trial);}}
  }
  if(pieces.empty()){out.push_back(input);return true;}
  if((av&original)<0)for(auto& piece:pieces)std::reverse(piece.begin()+1,piece.end());
  // Proposal-only coarsening of the actual positive triangle group.
  // Adjacency and immutable piece IDs avoid all-pairs/repeated exact work.
  // Every accepted union retains all vertices and a strictly positive turn;
  // the original full Source/metric validation below remains mandatory.
  if(pieces.size()>62)return fail("positive-ears-merge-piece-cap");
  std::map<label,G::Label> localIndex;
  for(std::size_t j=0;j<input.size();++j)localIndex.emplace(input[j],G::Label(j));
  std::vector<unsigned> pieceIdentity;std::vector<scalar> pieceArea;
  unsigned nextIdentity=0;
  for(const auto& piece:pieces)
  {
   if(!work())return fail("positive-ears-clock");
   if(piece.size()!=3)return fail("positive-ears-merge-triangle-input");
   vector sf;point centre;metric(piece,sf,centre);const scalar a=mag(sf);
   if(!finite(sf)||!finite(centre)||!std::isfinite(a)||a<=VSMALL)return fail("positive-ears-merge-metric");
   pieceIdentity.push_back(nextIdentity++);pieceArea.push_back(a);
  }
  std::set<std::pair<unsigned,unsigned>> rejected;
  struct Merge {std::size_t a,b;Ring ring;scalar minimum,small,area;unsigned first,second;};
  while(pieces.size()>1)
  {
   if(!work())return fail("positive-ears-clock");
   std::map<std::pair<label,label>,std::vector<std::pair<std::size_t,bool>>> adjacency;
   for(std::size_t i=0;i<pieces.size();++i)for(std::size_t j=0;j<pieces[i].size();++j)
   {
    if(!work())return fail("positive-ears-clock");
    const label a=pieces[i][j],b=pieces[i][(j+1)%pieces[i].size()];
    adjacency[std::minmax(a,b)].push_back({i,a<b});
   }
   std::vector<Merge> candidates;std::set<std::pair<std::size_t,std::size_t>> considered;
   for(const auto& edge:adjacency)
   {
    if(!work())return fail("positive-ears-clock");
    const auto& uses=edge.second;
    if(uses.size()>2)return fail("positive-ears-merge-incidence");
    if(uses.size()!=2)continue;
    if(uses[0].first==uses[1].first||uses[0].second==uses[1].second)return fail("positive-ears-merge-incidence");
    const auto pair=std::minmax(uses[0].first,uses[1].first);
    if(!considered.insert(pair).second)continue;
    const auto ids=std::minmax(pieceIdentity[pair.first],pieceIdentity[pair.second]);
    if(rejected.count(ids))continue;
    G::Ring outer;std::vector<G::Ring> two;
    two.emplace_back(pieces[pair.first].begin(),pieces[pair.first].end());
    two.emplace_back(pieces[pair.second].begin(),pieces[pair.second].end());
    if(!G::Detail::outline(two,outer,context))
    {
     if(!context.good())return fail("positive-ears-exact-or-clock");
     rejected.insert(ids);continue;
    }
    std::set<label> joined(pieces[pair.first].begin(),pieces[pair.first].end());
    joined.insert(pieces[pair.second].begin(),pieces[pair.second].end());
    const std::set<label> boundary(outer.begin(),outer.end());
    if(outer.size()<3||outer.size()>64||boundary!=joined)
    {rejected.insert(ids);continue;}
    Ring ring(outer.begin(),outer.end());vector sf;point centre;metric(ring,sf,centre);
    const scalar a=mag(sf);
    if(!finite(sf)||!finite(centre)||!std::isfinite(a)||a<=VSMALL
       ||!std::isfinite(sf&original)||(sf&original)<=0||!writable(ring))
    {rejected.insert(ids);continue;}
    scalar smallest=a;
    for(std::size_t j=0;j<pieces.size();++j)
     if(j!=pair.first&&j!=pair.second)smallest=std::min(smallest,pieceArea[j]);
    candidates.push_back({pair.first,pair.second,std::move(ring),smallest,
      std::min(pieceArea[pair.first],pieceArea[pair.second]),a,ids.first,ids.second});
   }
   // These floating scores order proposals only; none is a quality permission.
   std::sort(candidates.begin(),candidates.end(),[](const Merge& a,const Merge& b)
   {
    if(a.minimum!=b.minimum)return a.minimum>b.minimum;
    if(a.small!=b.small)return a.small<b.small;
    if(a.area!=b.area)return a.area>b.area;
    return a.first!=b.first?a.first<b.first:a.second<b.second;
   });
   bool merged=false;
   for(const auto& candidate:candidates)
   {
    if(!work())return fail("positive-ears-clock");
    G::Ring local;for(label id:candidate.ring)local.push_back(localIndex.at(id));
    bool positive=G::Detail::aligned(local,localPoints,exactNormal,origin,context);
    if(positive)for(std::size_t j=0;j<local.size();++j)
    {
     if(!context.take())return fail("positive-ears-exact-or-clock");
     const auto a=E::vector(localPoints[local[(j+local.size()-1)%local.size()]],context);
     const auto b=E::vector(localPoints[local[j]],context);
     const auto c=E::vector(localPoints[local[(j+1)%local.size()]],context);
     if(E::sign(E::dot(E::cross(E::difference(b,a,context),
          E::difference(c,b,context),context),exactNormal,context))<=0)
     {positive=false;break;}
    }
    if(!context.good())return fail("positive-ears-exact-or-clock");
    if(!positive){rejected.insert({candidate.first,candidate.second});continue;}
    Integrals pairIntegral;
    add(pairIntegral,integrals(pieces[candidate.a],at,context),context);
    add(pairIntegral,integrals(pieces[candidate.b],at,context),context);
    if(!same(pairIntegral,integrals(candidate.ring,at,context),context))
    {
     if(!context.good())return fail("positive-ears-exact-or-clock");
     rejected.insert({candidate.first,candidate.second});continue;
    }
    pieces[candidate.a]=candidate.ring;pieceArea[candidate.a]=candidate.area;
    pieceIdentity[candidate.a]=nextIdentity++;
    pieces.erase(pieces.begin()+candidate.b);pieceArea.erase(pieceArea.begin()+candidate.b);
    pieceIdentity.erase(pieceIdentity.begin()+candidate.b);merged=true;break;
   }
   if(!merged)break; // Merge-maximal for this triangulation, not global minimum.
  }
  }
  vector sumSf=vector::zero;point areaMoment=point::zero;scalar sumArea=0;
  Integrals total;const auto originalIntegral=integrals(input,at,context);
  std::set<label> covered;std::map<std::pair<label,label>,label> boundary,expected;
  for(const auto& piece:pieces)
  {
   if(!work())return fail("positive-ears-clock");vector sf;point centre;metric(piece,sf,centre);const scalar area=mag(sf);
   if(!finite(sf)||!finite(centre)||!std::isfinite(area)||area<=VSMALL||!std::isfinite(sf&original)||(sf&original)<=0||!writable(piece))return fail("positive-ears-piece");
   sumSf+=sf;sumArea+=area;areaMoment+=area*centre;add(total,integrals(piece,at,context),context);
   for(std::size_t j=0;j<piece.size();++j){const label a=piece[j],b=piece[(j+1)%piece.size()];covered.insert(a);boundary[std::minmax(a,b)]+=a<b?1:-1;}
  }
  for(std::size_t j=0;j<input.size();++j){const label a=input[j],b=input[(j+1)%input.size()];expected[std::minmax(a,b)]+=a<b?1:-1;}
  for(auto it=boundary.begin();it!=boundary.end();)if(!it->second)it=boundary.erase(it);else++it;
  if(covered!=unique||boundary!=expected)return fail("positive-ears-nodes-outline");
  if(!context.good()||!same(originalIntegral,total,context))return fail("positive-ears-exact12-V-M");
  const scalar oldArea=mag(original),scale=std::max(std::sqrt(oldArea),scalar(1e-6));
  if(!finite(sumSf)||!finite(areaMoment)||!std::isfinite(sumArea)||oldArea<=VSMALL||mag(sumSf-original)/oldArea>1e-10||mag(sumArea-oldArea)/oldArea>1e-10||mag(areaMoment/std::max(sumArea,scalar(VSMALL))-originalFc)/scale>1e-10)return fail("positive-ears-area-moment");
  if(!work())return fail("positive-ears-clock");out=std::move(pieces);changed=true;return true;
 }
 catch(...){return fail("positive-ears-bounded-exception");}
}
} }
#endif
