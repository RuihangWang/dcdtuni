#ifndef dcCartPrefixSourceRingRestoration_H
#define dcCartPrefixSourceRingRestoration_H
#include "dcCartSharedAxisSeamVolumePartition.H"
#include "dcCartSourcePlanarConeGroups.H"
#include <array>
#include <cmath>
#include <functional>
#include <utility>
#include <map>
#include <set>
#include <vector>
#include <algorithm>
#include <cstring>
#include <limits>
#include <string>

namespace Foam { namespace dcCartPrefixSourceRingRestoration
{
namespace S=dcCartSharedAxisSeamVolumePartition;
namespace E=dcCartExactBinary64Geometry;
using Ring=std::vector<label>;
// Geometry bookkeeping, not an origin/material or writer certificate.
struct Index
{
    std::vector<std::vector<label>> bySource;
    std::vector<std::size_t> pointReferences;
    std::size_t faceCount=0,pointCount=0;label cellCount=0;
};
struct Result
{
    S::Plan plan; // All staged native point/face/child IDs refer to current append slots.
    std::vector<S::Row> removed; // Complete old rows, including reused source slots.
    std::vector<label> retiredFaces,affectedCells,releasedPrefixPoints;
    std::map<label,label> faceMap,pointMap,cellMap;
    std::size_t initialPoints=0,initialFaces=0,expectedPoints=0,expectedFaces=0;
    label initialCells=0,expectedCells=0;
    std::map<label,std::vector<label>> sourceGroups;
    // Explicit proof payload for unchanged real prefix IDs/order, not authority.
    std::map<label,std::vector<label>> unchangedCurrentCohorts;
    std::map<label,Ring> sourceOutlines;
    std::vector<std::pair<label,std::size_t>> pointReferenceAfter;
    bool indexPrepared=false;
};
inline bool work(const std::function<bool()>& available)
{try{return !available||available();}catch(...){return false;}}
inline bool samePointBits(const point& a,const point& b)
{
    for(label k=0;k<3;++k)
    {const scalar x=a[k],y=b[k];if(std::memcmp(&x,&y,sizeof(scalar)))return false;}
    return true;
}
template<class Raw,class Mesh>
bool index(const Raw& raw,const Mesh& m,const std::vector<label>& source,
 const std::function<bool()>& available,Index& out,std::string* why=nullptr)
{
    out=Index();auto fail=[&](const char* s){if(why)*why=s;return false;};
    if(source.size()!=m.faces.size()||raw.faces.size()>std::size_t(std::numeric_limits<label>::max())
       ||m.faces.size()>std::size_t(std::numeric_limits<label>::max())
       ||m.points.size()>std::size_t(std::numeric_limits<label>::max()))return fail("index-shape");
    Index x;x.bySource.resize(raw.faces.size());x.pointReferences.assign(m.points.size(),0);
    x.faceCount=m.faces.size();x.pointCount=m.points.size();x.cellCount=m.nCells;
    for(label f=0;f<label(m.faces.size());++f)
    {
        if(!work(available))return fail("clock");if(!m.faceAlive[f])continue;
        if(source[f]>=0)
        {
            if(std::size_t(source[f])>=raw.faces.size())return fail("index-source");
            x.bySource[source[f]].push_back(f);
        }
        for(label p:m.faces[f])
        {
            if(p<0||std::size_t(p)>=m.points.size()
               ||x.pointReferences[p]==std::numeric_limits<std::size_t>::max())return fail("index-point");
            ++x.pointReferences[p];
        }
    }
    if(!work(available))return fail("clock");out=std::move(x);return true;
}
namespace Detail
{
struct Integrals {E::Vector area;E::Dyadic sixV;std::array<E::Dyadic,3> twentyFourM;};
inline bool equal(const E::Dyadic& a,const E::Dyadic& b,E::Context& c)
{return E::sign(E::subtract(a,b,c))==0&&c.good();}
inline bool same(const Integrals& a,const Integrals& b,E::Context& c)
{
    if(!equal(a.sixV,b.sixV,c))return false;
    for(unsigned k=0;k<3;++k)
        if(!equal(a.area[k],b.area[k],c)||!equal(a.twentyFourM[k],b.twentyFourM[k],c))return false;
    return c.good();
}
inline void add(Integrals& a,const Integrals& b,E::Context& c)
{
    a.sixV=E::add(a.sixV,b.sixV,c);
    for(unsigned k=0;k<3;++k)
    {a.area[k]=E::add(a.area[k],b.area[k],c);a.twentyFourM[k]=E::add(a.twentyFourM[k],b.twentyFourM[k],c);}
}
template<class Mesh>
E::Vector exact(const Mesh& m,label id,E::Context& c)
{const point& p=m.points.at(id);return E::vector({{p.x(),p.y(),p.z()}},c);}
template<class Mesh>
Integrals integrals(const Mesh& m,const Ring& r,E::Context& c)
{
    Integrals out;const auto a=exact(m,r[0],c);
    for(std::size_t j=0;j<r.size();++j)
    {
        const auto b=exact(m,r[j],c),d=exact(m,r[(j+1)%r.size()],c);
        const auto n=E::cross(b,d,c);
        for(unsigned k=0;k<3;++k)out.area[k]=E::add(out.area[k],n[k],c);
        if(j>0&&j+1<r.size())
        {
            const auto v=E::dot(a,n,c);out.sixV=E::add(out.sixV,v,c);
            for(unsigned k=0;k<3;++k)
            {
                const auto sum=E::add(E::add(a[k],b[k],c),d[k],c);
                out.twentyFourM[k]=E::add(out.twentyFourM[k],E::multiply(v,sum,c),c);
            }
        }
    }
    return out;
}
template<class Mesh>
bool onSegment(const Mesh& m,label a,label b,label p,E::Context& c)
{
    const point& x=m.points[a];const point& y=m.points[b];const point& z=m.points[p];
    for(label k=0;k<3;++k)if(z[k]<std::min(x[k],y[k])||z[k]>std::max(x[k],y[k]))return false;
    const auto u=exact(m,a,c),v=E::difference(exact(m,b,c),u,c),w=E::difference(exact(m,p,c),u,c);
    const auto cross=E::cross(v,w,c);
    return c.good()&&!E::sign(cross[0])&&!E::sign(cross[1])&&!E::sign(cross[2]);
}
template<class Mesh>
bool refinedCycle(const Mesh& m,const Ring& original,const Ring& outer,E::Context& c)
{
    if(original.size()<3||original.size()>64||outer.size()<original.size()||outer.size()>64)return false;
    const auto first=std::find(outer.begin(),outer.end(),original[0]);if(first==outer.end())return false;
    std::size_t pos=first-outer.begin(),used=0;
    for(std::size_t j=0;j<original.size();++j)
    {
        const label a=original[j],b=original[(j+1)%original.size()];
        if(outer[pos]!=a)return false;label axis=-1;
        for(label k=0;k<3;++k)if(m.points[a][k]!=m.points[b][k]){axis=k;break;}
        if(axis<0)return false;
        scalar previous=m.points[a][axis];const bool increasing=m.points[a][axis]<m.points[b][axis];
        do
        {
            pos=(pos+1)%outer.size();if(++used>outer.size())return false;
            const label p=outer[pos];if(!onSegment(m,a,b,p,c))return false;
            const scalar next=m.points[p][axis];
            if(increasing?next<=previous:next>=previous)return false;previous=next;
            if(p==b)break;
        }while(true);
    }
    return used==outer.size()&&outer[pos]==original[0]&&c.good();
}
template<class Raw,class Mesh>
bool coverage(const Raw& raw,const Mesh& m,label source,const std::vector<label>& faces,
 const std::function<bool()>& available,Ring& certifiedOuter,std::string* why)
{
    auto fail=[&](const char* s){if(why)*why=s;return false;};
    if(faces.empty()||faces.size()>64||raw.faces[source].size()<3||raw.faces[source].size()>64)
        return fail("source-capacity");
    E::Context c([&](){return work(available);});
    std::vector<Ring> pieces;pieces.reserve(faces.size());
    std::set<label> rawIds;
    for(label p:raw.faces[source])
    {
        if(!c.take()||p<0||std::size_t(p)>=raw.points.size()||std::size_t(p)>=m.points.size()
           ||!rawIds.insert(p).second||!samePointBits(raw.points[p],m.points[p]))return fail("raw-point-bits");
    }
    const auto original=integrals(raw,raw.faces[source],c);const auto normal=original.area;
    if(!c.good()||(!E::sign(normal[0])&&!E::sign(normal[1])&&!E::sign(normal[2])))return fail("raw-area");
    const auto origin=exact(raw,raw.faces[source][0],c);
    for(label p:raw.faces[source])
        if(E::sign(E::dot(normal,E::difference(exact(raw,p,c),origin,c),c)))return fail("raw-nonplanar");
    Integrals total;
    for(label f:faces)
    {
        if(!c.take()||f<0||std::size_t(f)>=m.faces.size()||!m.faceAlive[f]
           ||m.faces[f].size()<3||m.faces[f].size()>64)return fail("piece-shape");
        if(m.owner[f]!=raw.owner[source]||m.neighbour[f]!=raw.neighbour[source]
           ||m.patch[f]!=raw.patch[source]||m.zone[f]!=raw.zone[source])return fail("piece-metadata");
        std::set<label> unique;const Ring& r=m.faces[f];
        for(label p:r)
        {
            if(p<0||std::size_t(p)>=m.points.size()||!unique.insert(p).second)return fail("piece-point");
            if(E::sign(E::dot(normal,E::difference(exact(m,p,c),origin,c),c)))return fail("piece-plane");
        }
        const auto sum=integrals(m,r,c);
        if(!c.good()||E::sign(E::dot(sum.area,normal,c))<=0)return fail("piece-orientation");
        // Each prefix piece is convex, so positive boundary currents have nonnegative winding.
        for(std::size_t j=0;j<r.size();++j)
        {
            const auto a=exact(m,r[j],c),edge=E::difference(exact(m,r[(j+1)%r.size()],c),a,c);
            for(label p:r)
                if(E::sign(E::dot(E::cross(edge,E::difference(exact(m,p,c),a,c),c),normal,c))<0)
                    return fail("piece-nonconvex");
        }
        add(total,sum,c);pieces.push_back(r);
    }
    Ring outer;
    if(!dcCartSourcePlanarConeGroups::Detail::outline(pieces,outer,c)
       ||!refinedCycle(m,raw.faces[source],outer,c))return fail("source-boundary");
    if(!c.good()||!same(original,total,c))return fail("source-area-moment");
    if(!c.good())return false;certifiedOuter=std::move(outer);return true;
}
// An inherited source-edge segment is identified by raw endpoints/Birth edge provenance,
// rather than assuming its rounded new endpoint is exactly on the old straight segment.
template<class Raw>
const point& rawPoint(const Raw& raw,const S::Plan& plan,label p)
{return p<label(raw.points.size())?raw.points.at(p):plan.births.at(std::size_t(p)-raw.points.size()).position;}
template<class Raw>
bool supports(const Raw& raw,const S::Plan& plan,label p,label a,label b)
{
    if(p==a||p==b)return true;if(p<label(raw.points.size()))return false;
    const std::size_t i=std::size_t(p)-raw.points.size();if(i>=plan.births.size())return false;
    const auto& birth=plan.births[i];if(std::minmax(birth.first,birth.second)==std::minmax(a,b))return true;
    // A compound cut can bisect an earlier staged source-edge segment.
    // Every leaf of its creation-ordered provenance must be the same real
    // original edge; a new cap edge cannot impersonate a physical outline.
    std::vector<label> pending{birth.first,birth.second};std::set<label> visited;
    while(!pending.empty())
    {
        const label id=pending.back();pending.pop_back();
        if(id==a||id==b)continue;
        if(id<0||id<label(raw.points.size()))return false;
        if(!visited.insert(id).second)continue;
        const std::size_t at=std::size_t(id)-raw.points.size();
        if(at>=i)return false;
        const auto& inherited=plan.births[at];
        if(inherited.first>=id||inherited.second>=id)return false;
        pending.push_back(inherited.first);pending.push_back(inherited.second);
    }
    return true;
}
template<class Raw,class Mesh>
bool insertSourceNodes(const Raw& raw,const Mesh& m,const S::Plan& plan,label source,
 const Ring& outer,const Ring& rawRing,Ring& currentRing,E::Context& c)
{
    Ring result;
    for(std::size_t j=0;j<rawRing.size();++j)
    {
        const label u=rawRing[j],v=rawRing[(j+1)%rawRing.size()];
        const label mappedU=currentRing[j],mappedV=currentRing[(j+1)%currentRing.size()];
        result.push_back(mappedU);std::vector<label> inserted;label edgeA=-1,edgeB=-1;
        for(std::size_t k=0;k<raw.faces[source].size();++k)
        {
            if(!c.take())return false;
            const label a=raw.faces[source][k],b=raw.faces[source][(k+1)%raw.faces[source].size()];
            if(supports(raw,plan,u,a,b)&&supports(raw,plan,v,a,b))
            {if(edgeA>=0)return false;edgeA=a;edgeB=b;}
        }
        if(edgeA<0)continue;
        const point& x=rawPoint(raw,plan,u);const point& y=rawPoint(raw,plan,v);label axis=-1;
        if(raw.points[edgeA][plan.axis]!=raw.points[edgeB][plan.axis])axis=plan.axis;
        else for(label k=0;k<3;++k)if(raw.points[edgeA][k]!=raw.points[edgeB][k]){axis=k;break;}
        if(axis<0||x[axis]==y[axis])return false;
        for(label p:outer)
        {
            if(!c.take())return false;
            if(p==mappedU||p==mappedV)continue;
            if(!onSegment(m,edgeA,edgeB,p,c))continue;
            const scalar t=m.points[p][axis];if(t<std::min(x[axis],y[axis])||t>std::max(x[axis],y[axis]))continue;
            if(t==x[axis]||t==y[axis])return false; // no identity weld of distinct nodes
            inserted.push_back(p);
        }
        std::sort(inserted.begin(),inserted.end(),[&](label a,label b)
        {return x[axis]<y[axis]?m.points[a][axis]<m.points[b][axis]:m.points[a][axis]>m.points[b][axis];});
        for(std::size_t k=1;k<inserted.size();++k)
            if(m.points[inserted[k-1]][axis]==m.points[inserted[k]][axis])return false;
        for(label p:inserted)result.push_back(p);
        if(result.size()>64||!c.good())return false;
    }
    std::set<label> ids(result.begin(),result.end());if(ids.size()!=result.size()||result.size()>64)return false;
    currentRing=std::move(result);return c.good();
}
template<class Mesh>
bool onStagedSegment(const Mesh& m,const S::Plan& plan,label a,label b,label p,E::Context& c)
{
    const point& x=S::coordinate(m,plan,a);const point& y=S::coordinate(m,plan,b);const point& z=m.points[p];
    for(label k=0;k<3;++k)if(z[k]<std::min(x[k],y[k])||z[k]>std::max(x[k],y[k]))return false;
    const auto u=E::vector({{x.x(),x.y(),x.z()}},c),v=E::vector({{y.x(),y.y(),y.z()}},c);
    const auto n=E::cross(E::difference(v,u,c),E::difference(exact(m,p,c),u,c),c);
    return c.good()&&!E::sign(n[0])&&!E::sign(n[1])&&!E::sign(n[2]);
}
template<class Mesh>
bool refineStagedEdgeNodes(const Mesh& m,const S::Plan& plan,const std::set<label>& candidates,
 Ring& ring,E::Context& c)
{
    Ring result;
    for(std::size_t j=0;j<ring.size();++j)
    {
        const label a=ring[j],b=ring[(j+1)%ring.size()];result.push_back(a);
        const point& x=S::coordinate(m,plan,a);const point& y=S::coordinate(m,plan,b);label axis=-1;
        for(label k=0;k<3;++k)if(x[k]!=y[k]){axis=k;break;}if(axis<0)return false;
        std::vector<label> add;
        for(label p:candidates)
        {
            if(!c.take())return false;
            if(p!=a&&p!=b&&onStagedSegment(m,plan,a,b,p,c))
            {
                const scalar t=m.points[p][axis];if(t==x[axis]||t==y[axis])return false;
                add.push_back(p);
            }
        }
        std::sort(add.begin(),add.end(),[&](label a,label b)
        {return x[axis]<y[axis]?m.points[a][axis]<m.points[b][axis]:m.points[a][axis]>m.points[b][axis];});
        for(std::size_t k=1;k<add.size();++k)
            if(m.points[add[k-1]][axis]==m.points[add[k]][axis])return false;
        result.insert(result.end(),add.begin(),add.end());if(result.size()>64||!c.good())return false;
    }
    std::set<label> unique(result.begin(),result.end());if(unique.size()!=result.size())return false;
    ring=std::move(result);return c.good();
}
// A finite cut can insert genuine shared-edge births and re-express a face
// as a complete positive piece group without splitting its endpoints. The
// record is a preimage; every actual piece is independently revalidated.
template<class Raw,class Mesh>
bool finiteHaloSource(const Raw& raw,const Mesh& m,const std::vector<label>& source,
 const Index& idx,const S::Plan& plan,const std::vector<const S::Row*>& rows,
 const std::function<bool(label)>* allowed,const std::function<bool()>& available)
{
    namespace F=dcCartFiniteTensorCutScope;
    namespace I=dcCartOriginalNodePositiveEarFacets;
    using Rational=S::D::Rational;
    try
    {
        if(rows.empty()||rows.size()>F::ringCap||!rows.front())return false;
        const label f=rows.front()->source;
        if(plan.cutScope!=F::CutScope::FiniteTensor||!allowed||!(*allowed)
            ||!work(available)||!(*allowed)(f)||f<0||std::size_t(f)>=raw.faces.size()
            ||std::size_t(f)>=idx.bySource.size()||!raw.faceAlive[f])return false;
        const auto found=plan.surfaceRefinements.find(f);
        if(found==plan.surfaceRefinements.end()||!found->second)return false;
        const auto& proof=*found->second;
        if(proof.source()!=f||proof.owner()!=raw.owner[f]||proof.neighbour()!=raw.neighbour[f]
            ||proof.patch()!=raw.patch[f]||proof.zone()!=raw.zone[f]
            ||proof.before()!=raw.faces[f]||proof.pieces().size()!=rows.size()
            ||proof.before().size()<3||proof.before().size()>F::ringCap
            ||proof.after().size()<proof.before().size()||proof.after().size()>F::ringCap)
            return false;
        const auto at=[&](label id)->const point&{return rawPoint(raw,plan,id);};
        std::set<label> oldIds,allIds;
        for(label id:proof.before())
        {
            if(!work(available)||id<0||std::size_t(id)>=raw.points.size()
                ||std::size_t(id)>=m.points.size()||!oldIds.insert(id).second
                ||!samePointBits(raw.points[id],m.points[id]))return false;
        }
        for(label id:proof.after())
            if(!work(available)||id<0||std::size_t(id)>=raw.points.size()+plan.births.size()
                ||!allIds.insert(id).second
                ||(std::size_t(id)<raw.points.size()&&!oldIds.count(id)))return false;
        allIds.insert(oldIds.begin(),oldIds.end());
        if(proof.pointBits().size()!=allIds.size())return false;
        for(label id:allIds)
        {
            const auto bit=proof.pointBits().find(id);
            if(!work(available)||bit==proof.pointBits().end()
                ||bit->second!=F::pointBits(at(id)))return false;
        }
        E::Context exactWork([&](){return work(available);});
        // Bind the complete raw-frame row group. A private producer snapshot
        // cannot stand in for native identity, tuple, coverage or geometry.
        std::map<label,Ring> actualPieces;std::set<label> piecePoints;
        std::vector<dcCartSourcePlanarConeGroups::Ring> directedPieces;
        I::Integrals proposed;
        for(const auto* row:rows)
        {
            if(!exactWork.take()||!row||row->source!=f
                ||row->owner!=raw.owner[f]||row->neighbour!=raw.neighbour[f]
                ||row->patch!=raw.patch[f]||row->zone!=raw.zone[f]
                ||row->native<0||(row->native!=f&&std::size_t(row->native)<raw.faces.size())
                ||row->ring.size()<3||row->ring.size()>F::ringCap
                ||!actualPieces.emplace(row->native,row->ring).second)return false;
            std::set<label> unique;
            for(label id:row->ring)
            {
                if(!exactWork.take()||!allIds.count(id)||!unique.insert(id).second)return false;
                piecePoints.insert(id);
            }
            directedPieces.emplace_back(row->ring.begin(),row->ring.end());
            I::add(proposed,I::integrals(row->ring,at,exactWork),exactWork);
        }
        if(!actualPieces.count(f)||actualPieces!=proof.pieces()||piecePoints!=allIds)return false;
        namespace G=dcCartSourcePlanarConeGroups;
        G::Ring whole(proof.after().begin(),proof.after().end()),outer;
        if(!G::Detail::outline(directedPieces,outer,exactWork)
            ||!G::Detail::sameCycle(whole,outer))return false;
        if(rows.size()>1)
        {
            // Local indexing keeps the exact independent plane test bounded
            // by this outline, irrespective of global native point labels.
            std::map<label,G::Label> local;std::vector<G::Point> coordinates;
            for(label id:proof.after())
            {
                if(!exactWork.take())return false;const point& x=at(id);
                local.emplace(id,G::Label(coordinates.size()));coordinates.push_back({{x.x(),x.y(),x.z()}});
            }
            G::Ring outline;for(label id:proof.after())outline.push_back(local.at(id));
            E::Vector normal,origin;
            if(!G::Detail::plane(outline,coordinates,normal,origin,exactWork))return false;
            for(const auto& piece:directedPieces)
            {
                G::Ring ids;for(label id:piece)ids.push_back(local.at(id));
                if(!G::Detail::aligned(ids,coordinates,normal,origin,exactWork))return false;
            }
        }
        const auto original=I::integrals(proof.before(),at,exactWork);
        if(!I::same(original,proposed,exactWork))return false;
        I::Integrals current;
        const auto& fragments=idx.bySource[f];std::set<label> uniqueFaces;
        if(fragments.empty()||fragments.size()>64)return false;
        for(label q:fragments)
        {
            if(!exactWork.take()||q<0||std::size_t(q)>=m.faces.size()
                ||std::size_t(q)>=source.size()||source[q]!=f||!m.faceAlive[q]
                ||!uniqueFaces.insert(q).second||m.owner[q]!=raw.owner[f]
                ||m.neighbour[q]!=raw.neighbour[f]||m.patch[q]!=raw.patch[f]
                ||m.zone[q]!=raw.zone[f]||m.faces[q].size()<3||m.faces[q].size()>64)
                return false;
            std::set<label> points;
            for(label id:m.faces[q])
                if(id<0||std::size_t(id)>=m.points.size()||!points.insert(id).second)return false;
            I::add(current,I::integrals(m.faces[q],[&](label id)->const point&
                {return m.points[id];},exactWork),exactWork);
        }
        if(!I::same(original,current,exactWork))return false;
        const auto first=std::find(proof.after().begin(),proof.after().end(),proof.before()[0]);
        if(first==proof.after().end())return false;
        std::size_t position=first-proof.after().begin(),used=0;
        for(std::size_t edge=0;edge<proof.before().size();++edge)
        {
            if(!exactWork.take())return false;
            const label a=proof.before()[edge],b=proof.before()[(edge+1)%proof.before().size()];
            if(proof.after()[position]!=a)return false;
            label orderAxis=-1;
            for(label k=0;k<3;++k)if(at(a)[k]!=at(b)[k]){orderAxis=k;break;}
            if(orderAxis<0)return false;
            std::map<label,Rational> parameters;parameters.emplace(a,Rational(0));parameters.emplace(b,Rational(1));
            std::function<bool(label,Rational&)> parameter;
            parameter=[&](label id,Rational& result)->bool
            {
                if(!exactWork.take())return false;
                const auto cached=parameters.find(id);
                if(cached!=parameters.end()){result=cached->second;return true;}
                if(id<0||std::size_t(id)<raw.points.size())return false;
                const std::size_t j=std::size_t(id)-raw.points.size();
                if(j>=plan.births.size()||j>=plan.birthPlanes.size())return false;
                const auto& birth=plan.births[j];const auto& plane=plan.birthPlanes[j];
                if(birth.first<0||birth.second<0||birth.first>=id||birth.second>=id
                    ||birth.first==birth.second||plane.first<0||plane.first>2
                    ||!std::isfinite(plane.second)||!S::finite(birth.position))return false;
                Rational u,v;if(!parameter(birth.first,u)||!parameter(birth.second,v)||u==v)return false;
                const point& x=at(birth.first);const point& y=at(birth.second);
                const auto denominator=S::D::detail::number(y[plane.first])-S::D::detail::number(x[plane.first]);
                if(denominator==0)return false;
                const Rational t=(S::D::detail::number(plane.second)-S::D::detail::number(x[plane.first]))/denominator;
                if(!(t>0&&t<1))return false;
                point expected(point::zero);
                for(label k=0;k<3;++k)
                    expected[k]=k==plane.first?plane.second:S::D::detail::rounded(S::D::detail::number(x[k])
                        +t*(S::D::detail::number(y[k])-S::D::detail::number(x[k])));
                if(!samePointBits(expected,birth.position))return false;
                result=u+t*(v-u);if(!(result>0&&result<1))return false;
                parameters.emplace(id,result);return true;
            };
            Rational previous(0);scalar previousCoordinate=at(a)[orderAxis];
            const bool increasing=at(a)[orderAxis]<at(b)[orderAxis];
            do
            {
                position=(position+1)%proof.after().size();if(++used>proof.after().size())return false;
                const label id=proof.after()[position];Rational next;
                if(!parameter(id,next)||next<=previous||next>1)return false;
                const scalar value=at(id)[orderAxis];
                if(increasing?value<=previousCoordinate:value>=previousCoordinate)return false;
                previous=next;previousCoordinate=value;
                if(id==b)break;
            }while(true);
        }
        if(used!=proof.after().size()||proof.after()[position]!=proof.before()[0])return false;
        return I::same(original,I::integrals(proof.after(),at,exactWork),exactWork)&&exactWork.good();
    }
    catch(...){return false;}
}
}
template<class Raw,class Mesh>
bool stage(const Raw& raw,const Mesh& m,const std::vector<label>& source,const Index& idx,
 const S::Plan& plan,const std::function<bool()>& available,Result& out,std::string* why=nullptr,
 const std::function<bool(label)>* haloSourceAllowed=nullptr,
 const S::PointIndex* surfaceRawIndex=nullptr)
{
    out=Result();auto fail=[&](const char* s){if(why)*why=s;return false;};
    const std::size_t bound=std::numeric_limits<label>::max();
    const label additionalCells=S::addedCells(plan);
    const bool surfaceOnly=plan.cutScope==dcCartFiniteTensorCutScope::CutScope::FiniteTensor
        &&!plan.surfaceEdgeBirths.empty()&&!plan.surfaceRefinements.empty()
        &&!plan.births.empty()&&!plan.rows.empty();
    if(!plan.ready||(plan.cells.empty()&&!surfaceOnly)||plan.axis<0||plan.axis>2||!std::isfinite(plan.coordinate)
       ||source.size()!=m.faces.size()
       ||idx.faceCount!=m.faces.size()||idx.pointCount!=m.points.size()||idx.cellCount!=m.nCells
       ||idx.bySource.size()!=raw.faces.size()||idx.pointReferences.size()!=m.points.size()
       ||raw.nCells<0||m.nCells<raw.nCells||raw.points.size()>m.points.size()||raw.faces.size()>m.faces.size()
       ||plan.births.size()>bound||plan.rows.size()>bound
       ||m.points.size()>bound-plan.births.size()||m.faces.size()>bound-plan.rows.size()
       ||additionalCells<0||additionalCells>std::numeric_limits<label>::max()-m.nCells)return fail("stage-shape");
    Result r;r.plan=plan;r.plan.ready=false;
    std::set<label> selected,sources,affected,retired,reused,finiteHaloSources;
    std::map<label,std::vector<const S::Row*>> finiteSourceRows;
    label nextRawChild=raw.nCells,nextCurrentChild=m.nCells;
    for(std::size_t j=0;j<plan.cells.size();++j)
    {
        if(!work(available))return fail("clock");const auto& c=plan.cells[j];
        if(c.parent<0||c.parent>=raw.nCells||c.parent>=m.nCells||!raw.cellAlive[c.parent]
           ||!m.cellAlive[c.parent]||!selected.insert(c.parent).second
           ||raw.cellRank[c.parent]!=m.cellRank[c.parent])return fail("parent-state");
        r.cellMap.emplace(c.parent,c.parent);affected.insert(c.parent);
        if(c.parts.empty())
        {
            if(c.child!=nextRawChild++)return fail("raw-child");
            r.cellMap.emplace(c.child,nextCurrentChild);
            r.plan.cells[j].child=nextCurrentChild;affected.insert(nextCurrentChild++);
        }
        else
        {
            if(c.parts.front().cell!=c.parent)return fail("raw-parts");
            r.plan.cells[j].parts.front().cell=c.parent;
            for(std::size_t k=1;k<c.parts.size();++k)
            {
                if(c.parts[k].cell!=nextRawChild++)return fail("raw-parts");
                r.cellMap.emplace(c.parts[k].cell,nextCurrentChild);
                r.plan.cells[j].parts[k].cell=nextCurrentChild;
                affected.insert(nextCurrentChild++);
            }
            r.plan.cells[j].child=r.plan.cells[j].parts[1].cell;
        }
    }
    if(plan.cutScope==dcCartFiniteTensorCutScope::CutScope::FiniteTensor)
    {
        const std::set<label> declared(plan.volumeRoots.begin(),plan.volumeRoots.end());
        if(declared!=selected||declared.size()!=plan.volumeRoots.size()
            ||declared.size()>dcCartFiniteTensorCutScope::volumeParentCap
            ||plan.birthPlanes.size()!=plan.births.size()
            ||plan.births.size()>dcCartFiniteTensorCutScope::pointCap
            ||plan.surfaceRefinements.size()>dcCartFiniteTensorCutScope::surfaceFaceCap)
            return fail("finite-halo-plan");
        label nextFiniteFace=raw.faces.size();
        for(const auto& row:plan.rows)
        {
            if(!work(available))return fail("clock");
            if(row.native>=label(raw.faces.size())&&row.native!=nextFiniteFace++)
                return fail("finite-halo-append-identity");
            if(row.source>=0)finiteSourceRows[row.source].push_back(&row);
        }
    }
    if(!plan.surfaceEdgeBirths.empty())
    {
        namespace F=dcCartFiniteTensorCutScope;
        if(plan.cutScope!=F::CutScope::FiniteTensor||!surfaceRawIndex
            ||surfaceRawIndex->initialPoints!=label(raw.points.size())
            ||surfaceRawIndex->faces.size()!=raw.points.size()
            ||plan.surfaceEdgeBirths.size()>F::surfaceFaceCap
            ||(plan.cells.empty()&&plan.surfaceWholeParents.empty()))return fail("surface-edge-stage-input");
        for(const auto& intent:plan.surfaceEdgeBirths)
        {
            if(!work(available)||!haloSourceAllowed||!(*haloSourceAllowed)
                ||!(*haloSourceAllowed)(intent.source))return fail("surface-edge-stage-source");
            F::SurfaceEdgeBirth resolved;
            const auto first=std::min(intent.firstBits,intent.secondBits);
            const auto second=std::max(intent.firstBits,intent.secondBits);
            if(!S::resolveSurfaceEdgeBirth(raw,intent.source,first,second,intent.birthBits,
                intent.plane,available,resolved)||resolved.first!=intent.first||resolved.second!=intent.second
                ||resolved.firstBits!=intent.firstBits||resolved.secondBits!=intent.secondBits)
                return fail("surface-edge-stage-preimage");
            label birthId=-1;
            for(std::size_t j=0;j<plan.births.size();++j)
            {
                if(!work(available))return fail("clock");const auto& birth=plan.births[j];
                if(birth.first==intent.first&&birth.second==intent.second
                    &&plan.birthPlanes[j]==intent.plane&&F::pointBits(birth.position)==intent.birthBits)
                {
                    if(birthId>=0)return fail("surface-edge-stage-duplicate-birth");
                    birthId=raw.points.size()+j;
                }
            }
            if(birthId<0)return fail("surface-edge-stage-birth");
            const S::Edge edge{intent.first,intent.second};bool sourceSeen=false;
            for(label f:surfaceRawIndex->faces[intent.first])
            {
                if(!work(available)||f<0||std::size_t(f)>=raw.faces.size()||!raw.faceAlive[f])
                    return fail("surface-edge-stage-star");
                bool contains=false;
                for(std::size_t j=0;j<raw.faces[f].size();++j)
                    contains=contains||S::Edge(std::minmax(raw.faces[f][j],
                        raw.faces[f][(j+1)%raw.faces[f].size()]))==edge;
                if(!contains)continue;if(f==intent.source)sourceSeen=true;
                const auto group=finiteSourceRows.find(f);
                if(group==finiteSourceRows.end()||group->second.empty())return fail("surface-edge-stage-missing-ring");
                bool foundBirth=false;
                for(const auto* row:group->second)
                    foundBirth=foundBirth||std::find(row->ring.begin(),row->ring.end(),birthId)!=row->ring.end();
                if(!foundBirth)return fail("surface-edge-stage-missing-birth");
                for(label c:{raw.owner[f],raw.neighbour[f]})if(c>=0&&!selected.count(c)
                    &&!plan.surfaceWholeParents.count(c))return fail("surface-edge-stage-missing-whole");
            }
            if(!sourceSeen)return fail("surface-edge-stage-missing-source");
        }
        for(label c:plan.surfaceWholeParents)
            if(!work(available)||c<0||c>=raw.nCells||c>=m.nCells
                ||!raw.cellAlive[c]||!m.cellAlive[c]||raw.cellRank[c]!=m.cellRank[c]
                ||selected.count(c))return fail("surface-edge-stage-whole-state");
    }
    for(std::size_t j=0;j<plan.births.size();++j)
    {
        if(!work(available))return fail("clock");const auto& b=plan.births[j];
        auto inherited=[&](label rawId,label& current)->bool
        {
            if(rawId<0||std::size_t(rawId)>=raw.points.size()+j)return false;
            if(std::size_t(rawId)<raw.points.size())
            {
                if(!samePointBits(raw.points[rawId],m.points[rawId]))return false;
                current=rawId;return true;
            }
            const auto found=r.pointMap.find(rawId);
            if(found==r.pointMap.end())return false;current=found->second;return true;
        };
        label first,second;
        if(!inherited(b.first,first)||!inherited(b.second,second)||!S::finite(b.position))
            return fail("birth-state");
        r.plan.births[j].first=first;r.plan.births[j].second=second;
        r.pointMap.emplace(label(raw.points.size()+j),label(m.points.size()+j));
    }
    for(const auto& row:plan.rows)if(row.source>=0)
    {
        const label f=row.source;
        if(std::size_t(f)>=raw.faces.size()||!raw.faceAlive[f]||row.patch!=raw.patch[f]
           ||row.zone!=raw.zone[f]||(!selected.count(raw.owner[f])&&!selected.count(raw.neighbour[f])))
        {
            if(plan.cutScope!=dcCartFiniteTensorCutScope::CutScope::FiniteTensor)
                return fail("raw-source"); // Original propagating return and short circuit.
            // Validate all actual rows for this Source exactly once, including
            // every appended piece; repeated rows cannot bypass tuple checks.
            if(!finiteHaloSources.count(f))
            {
                const auto group=finiteSourceRows.find(f);
                if(group==finiteSourceRows.end()||!Detail::finiteHaloSource(raw,m,source,idx,plan,
                    group->second,haloSourceAllowed,available))return fail("raw-source-finite-halo");
                finiteHaloSources.insert(f);
            }
        }
        sources.insert(f);
    }
    for(label f:sources)
    {
        if(!work(available))return fail("clock");const auto& fragments=idx.bySource[f];
        for(label q:fragments)if(q<0||std::size_t(q)>=source.size()||source[q]!=f)return fail("source-index");
        Ring outer;if(!Detail::coverage(raw,m,f,fragments,available,outer,why))return false;
        r.sourceOutlines.emplace(f,std::move(outer));
        for(label q:fragments)
        {
            if(!retired.insert(q).second)return fail("duplicate-piece");
            S::Row old;old.native=q;old.source=f;old.owner=m.owner[q];old.neighbour=m.neighbour[q];
            old.patch=m.patch[q];old.zone=m.zone[q];old.ring=m.faces[q];
            affected.insert(old.owner);if(old.neighbour>=0)affected.insert(old.neighbour);
            r.removed.push_back(std::move(old));
        }
    }
    label nextFace=m.faces.size();std::set<label> nativeIds;std::set<label> emittedPoints;
    auto mapPoint=[&](label p,label& v)
    {
        if(p>=0&&std::size_t(p)<raw.points.size()){v=p;return true;}
        const auto i=r.pointMap.find(p);if(i==r.pointMap.end())return false;v=i->second;return true;
    };
    auto mapCell=[&](label c,label& v)
    {
        if(c<0){v=c;return c==-1;}
        const auto i=r.cellMap.find(c);if(i!=r.cellMap.end()){v=i->second;return true;}
        if(c<raw.nCells&&c<m.nCells&&raw.cellAlive[c]&&m.cellAlive[c]){v=c;return true;}
        return false;
    };
    for(std::size_t j=0;j<r.plan.rows.size();++j)
    {
        auto& row=r.plan.rows[j];const auto& rawRow=plan.rows[j];
        if(!work(available))return fail("clock");const label rawId=row.native;
        if(row.source>=0&&rawId==row.source)
        {if(!reused.insert(row.source).second)return fail("duplicate-reuse");row.native=row.source;}
        else
        {if(rawId<label(raw.faces.size()))return fail("row-native");row.native=nextFace++;}
        if(!r.faceMap.emplace(rawId,row.native).second||!nativeIds.insert(row.native).second)return fail("row-duplicate");
        if(row.source<0&&(row.source!=-1||row.patch!=-1||row.zone!=-1))return fail("cap-metadata");
        label owner,neighbour;if(!mapCell(row.owner,owner)||!mapCell(row.neighbour,neighbour))return fail("row-endpoint");
        row.owner=owner;row.neighbour=neighbour;if(owner<0||owner==neighbour)return fail("row-endpoint");
        affected.insert(owner);if(neighbour>=0)affected.insert(neighbour);
        if(row.source>=0)r.sourceGroups[row.source].push_back(row.native);
        std::set<label> unique;
        for(label& p:row.ring)
        {label mapped;if(!mapPoint(p,mapped)||!unique.insert(mapped).second)return fail("row-point");p=mapped;}
        if(row.ring.size()<3||row.ring.size()>64)return fail("row-ring");
        if(row.source>=0)
        {
            E::Context c([&](){return work(available);});
            if(!Detail::insertSourceNodes(raw,m,plan,row.source,r.sourceOutlines.at(row.source),rawRow.ring,row.ring,c))
                return fail("source-edge-refinement");
        }
    }
    if(reused!=sources)return fail("missing-source-row");
    // A retained source-boundary node must also appear on an adjacent cap edge.
    // Pre-index local endpoint shells once; no rows x all-sources x all-cells search.
    std::map<label,std::set<label>> outlinePointsByParent;
    std::map<label,std::vector<label>> sourceFacesByParent;
    for(const auto& group:r.sourceOutlines)
    {
        if(!work(available))return fail("clock");const label f=group.first;
        for(label parent:{raw.owner[f],raw.neighbour[f]})if(parent>=0)
        {
            outlinePointsByParent[parent].insert(group.second.begin(),group.second.end());
            sourceFacesByParent[parent].push_back(f); // ascending physical-source order
        }
    }
    std::map<label,label> rawParentByCurrentCell;
    for(const auto& cell:r.plan.cells)
    {
        for(std::size_t k=0;k<S::partCount(cell);++k)
            rawParentByCurrentCell.emplace(S::partCell(cell,k),cell.parent);
    }
    auto originalEndpoint=[&](label cell)
    {
        if(cell<0)return label(-1);const auto i=rawParentByCurrentCell.find(cell);
        if(i!=rawParentByCurrentCell.end())return i->second;
        return cell<raw.nCells?cell:label(-1);
    };
    std::map<label,std::vector<std::size_t>> rowsByEndpoint;
    for(std::size_t j=0;j<r.plan.rows.size();++j)
    {
        if(!work(available))return fail("clock");auto& row=r.plan.rows[j];
        std::set<label> candidates;
        for(label cell:{row.owner,row.neighbour})
        {
            const label parent=originalEndpoint(cell);if(parent<0)continue;
            const auto i=outlinePointsByParent.find(parent);
            if(i!=outlinePointsByParent.end())candidates.insert(i->second.begin(),i->second.end());
        }
        E::Context c([&](){return work(available);});
        if(!Detail::refineStagedEdgeNodes(m,r.plan,candidates,row.ring,c))return fail("shared-edge-refinement");
        emittedPoints.insert(row.ring.begin(),row.ring.end());
        rowsByEndpoint[row.owner].push_back(j);
        if(row.neighbour>=0)rowsByEndpoint[row.neighbour].push_back(j);
    }
    // A later edge refinement must also retain all sixteen Source integrals.
    for(label f:finiteHaloSources)
    {
        namespace I=dcCartOriginalNodePositiveEarFacets;
        E::Context c([&](){return work(available);});I::Integrals total;
        for(const auto& row:r.plan.rows)if(row.source==f)
            I::add(total,I::integrals(row.ring,[&](label id)->const point&
                {return S::coordinate(m,r.plan,id);},c),c);
        if(!I::same(I::integrals(raw.faces[f],[&](label id)->const point&
            {return raw.points[id];},c),total,c))return fail("finite-halo-source16");
    }
    for(auto& cell:r.plan.cells)
    {
        cell.positive.clear();cell.negative.clear();cell.cap.clear();
        point reference(point::zero);std::size_t count=0;
        const auto sf=sourceFacesByParent.find(cell.parent);
        if(sf==sourceFacesByParent.end())return fail("parent-source-shell");
        for(label f:sf->second)for(label p:raw.faces[f]){reference+=raw.points[p];++count;}
        if(!count)return fail("parent-source-shell");reference/=scalar(count);
        if(!cell.parts.empty())
        {
            scalar volume=0;std::array<long double,4> physical{{0,0,0,0}};
            for(auto& part:cell.parts)
            {
                part.shell.clear();
                for(std::size_t j:rowsByEndpoint[part.cell])
                {
                    const auto& row=r.plan.rows[j];
                    part.shell.push_back({row.native,row.ring,row.owner==part.cell});
                }
                S::Geometry measured;
                if(!S::geometry(m,r.plan,part.shell,reference,available,measured))
                    return fail("refined-part-geometry");
                volume+=measured.volume;
                for(label k=0;k<4;++k)physical[k]+=measured.physical[k];
                part.geometry=std::move(measured);
            }
            const scalar span=max(mag(cell.before.hi-cell.before.lo),scalar(1e-6));
            if(mag(volume-cell.before.volume)/max(cell.before.volume,VSMALL)>1e-10)
                return fail("refined-volume");
            for(label k=0;k<4;++k)
                if(std::abs(physical[k]-cell.before.physical[k])
                    /std::max(cell.before.physical[0]*(k?span:1),static_cast<long double>(VSMALL))>1e-10L)
                    return fail("refined-physical-moment");
            cell.a=cell.parts[0].geometry;cell.b=cell.parts[1].geometry;
            continue;
        }
        for(std::size_t j:rowsByEndpoint[cell.parent])
        {
            const auto& row=r.plan.rows[j];
            cell.positive.push_back({row.native,row.ring,row.owner==cell.parent});
            if(row.source<0&&row.owner==cell.parent&&row.neighbour==cell.child)cell.cap=row.ring;
        }
        for(std::size_t j:rowsByEndpoint[cell.child])
        {
            const auto& row=r.plan.rows[j];
            cell.negative.push_back({row.native,row.ring,row.owner==cell.child});
        }
        S::Geometry a,b;
        if(cell.cap.empty()||!S::geometry(m,r.plan,cell.positive,reference,available,a)
           ||!S::geometry(m,r.plan,cell.negative,reference,available,b))return fail("refined-part-geometry");
        const scalar span=max(mag(cell.before.hi-cell.before.lo),scalar(1e-6));
        if(mag(a.volume+b.volume-cell.before.volume)/max(cell.before.volume,VSMALL)>1e-10)return fail("refined-volume");
        for(label k=0;k<4;++k)
        {
            const long double scale=cell.before.physical[0]*(k?span:1);
            if(std::abs(a.physical[k]+b.physical[k]-cell.before.physical[k])
               /std::max(scale,static_cast<long double>(VSMALL))>1e-10L)return fail("refined-physical-moment");
        }
        cell.a=std::move(a);cell.b=std::move(b);
    }
    std::map<label,std::size_t> removedUses;
    for(const auto& row:r.removed)for(label p:row.ring)++removedUses[p];
    for(const auto& use:removedUses)
        if(std::size_t(use.first)>=raw.points.size()&&idx.pointReferences[use.first]==use.second
           &&!emittedPoints.count(use.first))r.releasedPrefixPoints.push_back(use.first);
    for(label q:retired)if(!nativeIds.count(q))r.retiredFaces.push_back(q);
    r.affectedCells.assign(affected.begin(),affected.end());
    r.initialPoints=m.points.size();r.initialFaces=m.faces.size();r.initialCells=m.nCells;
    r.expectedPoints=m.points.size()+plan.births.size();
    r.expectedFaces=nextFace;r.expectedCells=m.nCells+additionalCells;r.plan.ready=true;
    if(!work(available))return fail("clock");out=std::move(r);return true;
}
// Explicit source-face restage: no fabricated Cell/Part, and no cell append.
// currentParents is a real raw-parent -> current-native-cell mapping. This is
// geometry bookkeeping only: caller still proves full before/after VM, Stock,
// source authority, generation freshness and processor agreement before publish.
namespace Detail
{
// An unchanged single original source has a stronger identity witness than
// a general prefix tessellation. Exact simplicity is still required; this is
// source bookkeeping, not permission to retain a concave measured cell.
template<class Raw>
bool simpleFaceRestageOriginal(const Raw& raw,const Ring& ring,
 const E::Vector& normal,E::Context& c)
{
    if(ring.size()<3||ring.size()>64)return false;
    auto turn=[&](label a,label b,label p)
    {
        const auto x=exact(raw,a,c);
        return E::sign(E::dot(E::cross(E::difference(exact(raw,b,c),x,c),
            E::difference(exact(raw,p,c),x,c),c),normal,c));
    };
    for(std::size_t i=0;i<ring.size();++i)
    {
        if(!c.take())return false;
        const label a=ring[i],b=ring[(i+1)%ring.size()];
        const auto edge=E::difference(exact(raw,b,c),exact(raw,a,c),c);
        if(!c.good()||(!E::sign(edge[0])&&!E::sign(edge[1])&&!E::sign(edge[2])))return false;
        const auto at=exact(raw,a,c);
        const auto previous=E::difference(exact(raw,ring[(i+ring.size()-1)%ring.size()],c),at,c);
        const auto next=E::difference(exact(raw,b,c),at,c);
        const auto cross=E::cross(previous,next,c);
        if(!E::sign(cross[0])&&!E::sign(cross[1])&&!E::sign(cross[2])
           &&E::sign(E::dot(previous,next,c))>=0)return false; // adjacent retrace
        for(std::size_t j=i+1;j<ring.size();++j)
        {
            if(j==(i+1)%ring.size()||i==(j+1)%ring.size())continue;
            if(!c.take())return false;
            const label u=ring[j],v=ring[(j+1)%ring.size()];
            const int abU=turn(a,b,u),abV=turn(a,b,v),uvA=turn(u,v,a),uvB=turn(u,v,b);
            if(!c.good()||(abU*abV<0&&uvA*uvB<0)
               ||(!abU&&onSegment(raw,a,b,u,c))||(!abV&&onSegment(raw,a,b,v,c))
               ||(!uvA&&onSegment(raw,u,v,a,c))||(!uvB&&onSegment(raw,u,v,b,c)))return false;
        }
    }
    return c.good();
}
inline bool sameDirectedFaceRestageCycle(const Ring& original,const Ring& current)
{
    if(original.size()!=current.size()||original.empty())return false;
    const auto start=std::find(current.begin(),current.end(),original.front());
    if(start==current.end())return false;const std::size_t offset=start-current.begin();
    for(std::size_t j=0;j<original.size();++j)
        if(original[j]!=current[(offset+j)%current.size()])return false;
    return true;
}
template<class Raw,class Mesh>
bool coverageFaceRestage(const Raw& raw,const Mesh& m,label source,const std::vector<label>& faces,
 label mappedOwner,label mappedNeighbour,
 const std::function<bool()>& available,Ring& certifiedOuter,std::string* why)
{
    auto fail=[&](const char* s){if(why)*why=s;return false;};
    if(faces.empty()||faces.size()>64||raw.faces[source].size()<3||raw.faces[source].size()>64)
        return fail("source-capacity");
    E::Context c([&](){return work(available);});
    std::vector<Ring> pieces;pieces.reserve(faces.size());
    std::set<label> rawIds;
    for(label p:raw.faces[source])
    {
        if(!c.take()||p<0||std::size_t(p)>=raw.points.size()||std::size_t(p)>=m.points.size()
           ||!rawIds.insert(p).second||!samePointBits(raw.points[p],m.points[p]))return fail("raw-point-bits");
    }
    const auto original=integrals(raw,raw.faces[source],c);const auto normal=original.area;
    if(!c.good()||(!E::sign(normal[0])&&!E::sign(normal[1])&&!E::sign(normal[2])))return fail("raw-area");
    const auto origin=exact(raw,raw.faces[source][0],c);
    for(label p:raw.faces[source])
        if(E::sign(E::dot(normal,E::difference(exact(raw,p,c),origin,c),c)))return fail("raw-nonplanar");
    Integrals total;
    for(label f:faces)
    {
        if(!c.take()||f<0||std::size_t(f)>=m.faces.size()||!m.faceAlive[f]
           ||m.faces[f].size()<3||m.faces[f].size()>64)return fail("piece-shape");
        if(m.owner[f]!=mappedOwner||m.neighbour[f]!=mappedNeighbour
           ||m.patch[f]!=raw.patch[source]||m.zone[f]!=raw.zone[source])return fail("piece-metadata");
        std::set<label> unique;const Ring& r=m.faces[f];
        if(faces.size()==1&&sameDirectedFaceRestageCycle(raw.faces[source],r))
        {
            // Original node IDs/bits, exact nonzero planar area and mapped
            // endpoints/patch/zone were checked above. No refined/aliased ring
            // or multi-piece prefix receives this original-identity witness.
            if(!simpleFaceRestageOriginal(raw,raw.faces[source],normal,c))
                return fail("original-source-not-simple");
            if(!c.good())return fail("clock");certifiedOuter=r;return true;
        }
        for(label p:r)
        {
            if(p<0||std::size_t(p)>=m.points.size()||!unique.insert(p).second)return fail("piece-point");
            if(E::sign(E::dot(normal,E::difference(exact(m,p,c),origin,c),c)))return fail("piece-plane");
        }
        const auto sum=integrals(m,r,c);
        if(!c.good()||E::sign(E::dot(sum.area,normal,c))<=0)return fail("piece-orientation");
        // Each prefix piece is convex, so positive boundary currents have nonnegative winding.
        for(std::size_t j=0;j<r.size();++j)
        {
            const auto a=exact(m,r[j],c),edge=E::difference(exact(m,r[(j+1)%r.size()],c),a,c);
            for(label p:r)
                if(E::sign(E::dot(E::cross(edge,E::difference(exact(m,p,c),a,c),c),normal,c))<0)
                    return fail("piece-nonconvex");
        }
        add(total,sum,c);pieces.push_back(r);
    }
    Ring outer;
    if(!dcCartSourcePlanarConeGroups::Detail::outline(pieces,outer,c)
       ||!refinedCycle(m,raw.faces[source],outer,c))return fail("source-boundary");
    if(!c.good()||!same(original,total,c))return fail("source-area-moment");
    if(!c.good())return false;certifiedOuter=std::move(outer);return true;
}
template<class Raw,class Mesh>
bool insertFaceRestageSourceNodes(const Raw& raw,const Mesh& m,const S::Plan& plan,label source,
 const Ring& outer,const Ring& rawRing,Ring& currentRing,E::Context& c)
{
    Ring result;
    for(std::size_t j=0;j<rawRing.size();++j)
    {
        const label u=rawRing[j],v=rawRing[(j+1)%rawRing.size()];
        const label mappedU=currentRing[j],mappedV=currentRing[(j+1)%currentRing.size()];
        result.push_back(mappedU);std::vector<label> inserted;label edgeA=-1,edgeB=-1;
        for(std::size_t k=0;k<raw.faces[source].size();++k)
        {
            if(!c.take())return false;
            const label a=raw.faces[source][k],b=raw.faces[source][(k+1)%raw.faces[source].size()];
            if(supports(raw,plan,u,a,b)&&supports(raw,plan,v,a,b))
            {if(edgeA>=0)return false;edgeA=a;edgeB=b;}
        }
        if(edgeA<0)continue;
        const point& x=rawPoint(raw,plan,u);const point& y=rawPoint(raw,plan,v);label axis=-1;
        for(label k=0;k<3;++k)if(raw.points[edgeA][k]!=raw.points[edgeB][k]){axis=k;break;}
        if(axis<0||x[axis]==y[axis])return false;
        for(label p:outer)
        {
            if(!c.take())return false;
            if(p==mappedU||p==mappedV)continue;
            if(!onSegment(m,edgeA,edgeB,p,c))continue;
            const scalar t=m.points[p][axis];if(t<std::min(x[axis],y[axis])||t>std::max(x[axis],y[axis]))continue;
            if(t==x[axis]||t==y[axis])return false; // no identity weld of distinct nodes
            inserted.push_back(p);
        }
        std::sort(inserted.begin(),inserted.end(),[&](label a,label b)
        {return x[axis]<y[axis]?m.points[a][axis]<m.points[b][axis]:m.points[a][axis]>m.points[b][axis];});
        for(std::size_t k=1;k<inserted.size();++k)
            if(m.points[inserted[k-1]][axis]==m.points[inserted[k]][axis])return false;
        for(label p:inserted)result.push_back(p);
        if(result.size()>64||!c.good())return false;
    }
    std::set<label> ids(result.begin(),result.end());if(ids.size()!=result.size()||result.size()>64)return false;
    currentRing=std::move(result);return c.good();
}
}
namespace Detail
{
template<class Raw,class Mesh,class Coverage>
bool stageFaceRestageWithCoverage(const Raw& raw,const Mesh& m,const std::vector<label>& source,
 const Index& idx,const S::Plan& plan,const std::set<label>& completeRawParents,
 const std::map<label,label>& currentParents,
 const std::map<label,std::vector<label>>& orderedSourceGroups,
 const S::PointIndex& rawPointIndex,const std::function<bool()>& available,
 Result& out,std::string* why,
 const std::set<label>* unchangedCurrentSources,const Coverage& coverage)
{
    out=Result();auto fail=[&](const char* s){if(why)*why=s;return false;};
    try
    {
        const std::size_t bound=std::numeric_limits<label>::max();
        if(!plan.ready||!plan.cells.empty()||plan.rows.empty()||completeRawParents.empty()
           ||source.size()!=m.faces.size()||idx.faceCount!=m.faces.size()
           ||idx.pointCount!=m.points.size()||idx.cellCount!=m.nCells
           ||idx.bySource.size()!=raw.faces.size()||idx.pointReferences.size()!=m.points.size()
           ||raw.nCells<0||m.nCells<0||raw.cellAlive.size()!=std::size_t(raw.nCells)
           ||raw.cellRank.size()!=std::size_t(raw.nCells)||m.cellAlive.size()!=std::size_t(m.nCells)
           ||m.cellRank.size()!=std::size_t(m.nCells)||rawPointIndex.faces.size()!=raw.points.size()
           ||raw.points.size()>m.points.size()||raw.faces.size()>m.faces.size()
           ||raw.owner.size()!=raw.faces.size()||raw.neighbour.size()!=raw.faces.size()
           ||raw.patch.size()!=raw.faces.size()||raw.zone.size()!=raw.faces.size()
           ||raw.faceAlive.size()!=raw.faces.size()||m.owner.size()!=m.faces.size()
           ||m.neighbour.size()!=m.faces.size()||m.patch.size()!=m.faces.size()
           ||m.zone.size()!=m.faces.size()||m.faceAlive.size()!=m.faces.size()
           ||plan.births.size()>bound||plan.rows.size()>bound
           ||m.points.size()>bound-plan.births.size()||m.faces.size()>bound-plan.rows.size())
            return fail("restage-shape");
        Result r;r.plan=plan;r.plan.ready=false;
        std::set<label> selected,sources,affected,retired,reused,currentIds;
        std::map<label,label> rawParentByCurrentCell;
        for(const auto& mapping:currentParents)
        {
            if(!work(available))return fail("clock");const label p=mapping.first,q=mapping.second;
            if(!completeRawParents.count(p)||p<0||p>=raw.nCells||q<0||q>=m.nCells
               ||!raw.cellAlive[p]||!m.cellAlive[q]||raw.cellRank[p]!=m.cellRank[q]
               ||!currentIds.insert(q).second)return fail("restage-parent-state");
            selected.insert(p);affected.insert(q);r.cellMap.emplace(p,q);
            rawParentByCurrentCell.emplace(q,p);
        }
        if(selected!=completeRawParents)return fail("restage-parent-map");
        std::map<label,std::vector<label>> actualGroups;std::set<label> rawNativeIds,requiredParents;
        label nextRawFace=label(raw.faces.size());
        for(const auto& row:plan.rows)
        {
            if(!work(available))return fail("clock");const label f=row.source;
            if(f<0||std::size_t(f)>=raw.faces.size()||!raw.faceAlive[f]
               ||raw.owner[f]<0||raw.owner[f]>=raw.nCells||raw.neighbour[f]<-1
               ||raw.neighbour[f]>=raw.nCells||row.owner!=raw.owner[f]
               ||row.neighbour!=raw.neighbour[f]||row.patch!=raw.patch[f]||row.zone!=raw.zone[f]
               ||!selected.count(row.owner)||(row.neighbour>=0&&!selected.count(row.neighbour)))
                return fail("restage-source-role");
            if(!rawNativeIds.insert(row.native).second)return fail("restage-duplicate-row");
            const bool unchanged=unchangedCurrentSources&&unchangedCurrentSources->count(f);
            if(unchanged)
            {
                // Preserve the current native identity/ring/order literally.
                // Source coverage below remains mandatory for this full cohort.
                if(row.native<0||std::size_t(row.native)>=m.faces.size()
                    ||!m.faceAlive[row.native]||source[row.native]!=f
                    ||currentParents.at(row.owner)!=row.owner
                    ||(row.neighbour>=0&&currentParents.at(row.neighbour)!=row.neighbour)
                    ||row.owner!=m.owner[row.native]||row.neighbour!=m.neighbour[row.native]
                    ||row.patch!=m.patch[row.native]||row.zone!=m.zone[row.native]
                    ||row.ring!=m.faces[row.native])return fail("restage-unchanged-row");
            }
            else if(row.native!=f&&row.native!=nextRawFace++)return fail("restage-native-order");
            actualGroups[f].push_back(row.native);sources.insert(f);requiredParents.insert(row.owner);
            if(row.neighbour>=0)requiredParents.insert(row.neighbour);
        }
        if(actualGroups!=orderedSourceGroups||requiredParents!=selected)return fail("restage-source-groups");
        if(unchangedCurrentSources)for(label f:*unchangedCurrentSources)
        {
            const auto group=actualGroups.find(f);
            if(f<0||std::size_t(f)>=idx.bySource.size()||group==actualGroups.end()
                ||group->second!=idx.bySource[f])return fail("restage-unchanged-cohort");
            r.unchangedCurrentCohorts.emplace(f,group->second);
        }
        for(const auto& group:actualGroups)
            if(group.second.empty()||(!(unchangedCurrentSources&&unchangedCurrentSources->count(group.first))
                &&group.second.front()!=group.first))return fail("restage-source-reuse");
        // Every birth must be on a real original edge; recursive/cap-edge
        // births are deliberately unsupported by this first face-only entry.
        for(std::size_t j=0;j<plan.births.size();++j)
        {
            if(!work(available))return fail("clock");const auto& b=plan.births[j];
            if(b.first<0||b.first>=b.second||std::size_t(b.second)>=raw.points.size()
               ||!S::finite(b.position)||!samePointBits(raw.points[b.first],m.points[b.first])
               ||!samePointBits(raw.points[b.second],m.points[b.second]))return fail("restage-birth-state");
            bool found=false;
            for(label f:rawPointIndex.faces.at(b.first))
            {
                if(!work(available))return fail("clock");
                if(f<0||std::size_t(f)>=raw.faces.size()||!raw.faceAlive[f])return fail("restage-edge-star-index");
                const auto& ring=raw.faces[f];bool incident=false;
                for(std::size_t k=0;k<ring.size();++k)
                    if(std::minmax(ring[k],ring[(k+1)%ring.size()])==std::minmax(b.first,b.second))
                        {incident=true;break;}
                if(!incident)continue;found=true;
                if(!sources.count(f)||!selected.count(raw.owner[f])
                   ||(raw.neighbour[f]>=0&&!selected.count(raw.neighbour[f])))return fail("restage-incomplete-edge-star");
            }
            if(!found)return fail("restage-edge-star-empty");
            r.pointMap.emplace(label(raw.points.size()+j),label(m.points.size()+j));
            r.plan.births[j].first=b.first;r.plan.births[j].second=b.second;
        }
        for(label f:sources)
        {
            if(!work(available))return fail("clock");const auto& fragments=idx.bySource[f];
            for(label q:fragments)if(q<0||std::size_t(q)>=source.size()||source[q]!=f)return fail("source-index");
            const label own=currentParents.at(raw.owner[f]);
            const label nei=raw.neighbour[f]<0?-1:currentParents.at(raw.neighbour[f]);
            Ring outer;if(!coverage(raw,m,f,fragments,own,nei,available,outer,why))return false;
            r.sourceOutlines.emplace(f,std::move(outer));
            for(label q:fragments)
            {
                if(!retired.insert(q).second)return fail("duplicate-piece");
                S::Row old;old.native=q;old.source=f;old.owner=m.owner[q];old.neighbour=m.neighbour[q];
                old.patch=m.patch[q];old.zone=m.zone[q];old.ring=m.faces[q];r.removed.push_back(std::move(old));
            }
        }
        label nextFace=label(m.faces.size());std::set<label> nativeIds,emittedPoints;
        auto mapPoint=[&](label p,label& v)
        {
            if(p>=0&&std::size_t(p)<raw.points.size()){v=p;return true;}
            const auto i=r.pointMap.find(p);if(i==r.pointMap.end())return false;v=i->second;return true;
        };
        for(std::size_t j=0;j<r.plan.rows.size();++j)
        {
            auto& row=r.plan.rows[j];const auto& rawRow=plan.rows[j];
            if(!work(available))return fail("clock");const label rawId=row.native;
            const bool unchanged=unchangedCurrentSources&&unchangedCurrentSources->count(row.source);
            if(unchanged)
            {
                // Reusing a current cohort is not a source-slot remint.
                reused.insert(row.source);
            }
            else if(rawId==row.source)
            {if(!reused.insert(row.source).second)return fail("duplicate-reuse");row.native=row.source;}
            else row.native=nextFace++;
            if(!r.faceMap.emplace(rawId,row.native).second||!nativeIds.insert(row.native).second)return fail("row-duplicate");
            row.owner=currentParents.at(row.owner);
            row.neighbour=row.neighbour<0?-1:currentParents.at(row.neighbour);
            if(row.owner==row.neighbour)return fail("row-endpoint");
            r.sourceGroups[row.source].push_back(row.native);std::set<label> unique;
            for(label& p:row.ring)
            {
                label mapped=p;
                if((unchanged?(p<0||std::size_t(p)>=m.points.size()):!mapPoint(p,mapped))
                    ||!unique.insert(mapped).second)return fail("row-point");p=mapped;
            }
            if(row.ring.size()<3||row.ring.size()>64)return fail("row-ring");
            E::Context c([&](){return work(available);});
            if(!unchanged&&!Detail::insertFaceRestageSourceNodes(raw,m,plan,row.source,r.sourceOutlines.at(row.source),rawRow.ring,row.ring,c))
                return fail("source-edge-refinement");
        }
        if(reused!=sources)return fail("missing-source-row");
        std::map<label,std::set<label>> outlinePointsByParent;
        for(const auto& group:r.sourceOutlines)
        {
            if(!work(available))return fail("clock");const label f=group.first;
            for(label parent:{raw.owner[f],raw.neighbour[f]})if(parent>=0)
                outlinePointsByParent[parent].insert(group.second.begin(),group.second.end());
        }
        std::map<label,std::set<label>> emittedBySource;
        for(auto& row:r.plan.rows)
        {
            if(!work(available))return fail("clock");std::set<label> candidates;
            for(label current:{row.owner,row.neighbour})if(current>=0)
            {
                const label parent=rawParentByCurrentCell.at(current);
                const auto i=outlinePointsByParent.find(parent);
                if(i!=outlinePointsByParent.end())candidates.insert(i->second.begin(),i->second.end());
            }
            E::Context c([&](){return work(available);});
            if(!(unchangedCurrentSources&&unchangedCurrentSources->count(row.source))
                &&!Detail::refineStagedEdgeNodes(m,r.plan,candidates,row.ring,c))return fail("shared-edge-refinement");
            emittedPoints.insert(row.ring.begin(),row.ring.end());
            emittedBySource[row.source].insert(row.ring.begin(),row.ring.end());
        }
        for(label f:sources)
        {
            for(label p:raw.faces[f])if(!emittedBySource[f].count(p))return fail("restage-original-node-cohort");
            for(label p:r.sourceOutlines.at(f))if(!emittedBySource[f].count(p))return fail("restage-prefix-node-cohort");
        }
        std::map<label,std::size_t> removedUses;
        for(const auto& row:r.removed)for(label p:row.ring)++removedUses[p];
        for(const auto& use:removedUses)
            if(std::size_t(use.first)>=raw.points.size()&&idx.pointReferences[use.first]==use.second
               &&!emittedPoints.count(use.first))r.releasedPrefixPoints.push_back(use.first);
        for(label q:retired)if(!nativeIds.count(q))r.retiredFaces.push_back(q);
        for(const auto& group:r.unchangedCurrentCohorts)
        {
            if(r.sourceGroups.at(group.first)!=group.second)return fail("restage-unchanged-order");
            for(label native:group.second)
            {
                const auto row=std::find_if(r.plan.rows.begin(),r.plan.rows.end(),
                    [&](const S::Row& value){return value.native==native;});
                if(row==r.plan.rows.end()||row->source!=source[native]
                    ||row->owner!=m.owner[native]||row->neighbour!=m.neighbour[native]
                    ||row->patch!=m.patch[native]||row->zone!=m.zone[native]
                    ||row->ring!=m.faces[native]
                    ||std::find(r.retiredFaces.begin(),r.retiredFaces.end(),native)!=r.retiredFaces.end())
                    return fail("restage-unchanged-publication");
            }
        }
        r.affectedCells.assign(affected.begin(),affected.end());
        r.initialPoints=m.points.size();r.initialFaces=m.faces.size();r.initialCells=m.nCells;
        r.expectedPoints=m.points.size()+plan.births.size();r.expectedFaces=nextFace;
        r.expectedCells=m.nCells;r.plan.ready=true;
        if(!work(available))return fail("clock");out=std::move(r);return true;
    }
    catch(...){return fail("restage-exception");}
}
}
template<class Raw,class Mesh>
bool stageFaceRestage(const Raw& raw,const Mesh& m,const std::vector<label>& source,
 const Index& idx,const S::Plan& plan,const std::set<label>& completeRawParents,
 const std::map<label,label>& currentParents,
 const std::map<label,std::vector<label>>& orderedSourceGroups,
 const S::PointIndex& rawPointIndex,const std::function<bool()>& available,
 Result& out,std::string* why=nullptr,
 const std::set<label>* unchangedCurrentSources=nullptr)
{
    // The legacy entry retains its literal exact-plane/coverage policy.
    const auto coverage=[&](const Raw& original,const Mesh& current,label f,
        const std::vector<label>& fragments,label own,label nei,
        const std::function<bool()>& work,Ring& outer,std::string* reason)
    {return Detail::coverageFaceRestage(original,current,f,fragments,own,nei,work,outer,reason);};
    return Detail::stageFaceRestageWithCoverage(raw,m,source,idx,plan,completeRawParents,
        currentParents,orderedSourceGroups,rawPointIndex,available,out,why,
        unchangedCurrentSources,coverage);
}

// Reserve/cache only: call before Lineage/Birth/mesh publication. Logical Index entries stay unchanged.
inline bool prepareIndexCommit(Index& idx,Result& r,const std::function<bool()>& available,
 std::string* why=nullptr)
{
    auto fail=[&](const char* s){if(why)*why=s;return false;};r.indexPrepared=false;
    if(!r.plan.ready||idx.faceCount!=r.initialFaces||idx.pointCount!=r.initialPoints
       ||idx.cellCount!=r.initialCells||idx.pointReferences.size()!=r.initialPoints)
        return fail("index-prepare-shape");
    std::map<label,std::size_t> removed,added;
    for(const auto& old:r.removed)for(label p:old.ring)++removed[p];
    for(const auto& row:r.plan.rows)for(label p:row.ring)++added[p];
    std::set<label> touched;for(const auto& p:removed)touched.insert(p.first);
    for(const auto& p:added)touched.insert(p.first);
    std::vector<std::pair<label,std::size_t>> after;after.reserve(touched.size());
    for(label p:touched)
    {
        if(!work(available))return fail("clock");
        if(p<0||std::size_t(p)>=r.expectedPoints)return fail("index-point");
        const std::size_t before=std::size_t(p)<idx.pointReferences.size()?idx.pointReferences[p]:0;
        const std::size_t subtract=removed[p],plus=added[p];
        if(subtract>before||plus>std::numeric_limits<std::size_t>::max()-(before-subtract))return fail("index-reference");
        after.emplace_back(p,before-subtract+plus);
    }
    for(const auto& group:r.sourceGroups)
        if(group.first<0||std::size_t(group.first)>=idx.bySource.size())return fail("index-source");
    for(const auto& group:r.unchangedCurrentCohorts)
    {
        if(!work(available))return fail("clock");
        const auto staged=r.sourceGroups.find(group.first);
        if(group.first<0||std::size_t(group.first)>=idx.bySource.size()
            ||idx.bySource[group.first]!=group.second||staged==r.sourceGroups.end()
            ||staged->second!=group.second)return fail("index-unchanged-cohort");
    }
    if(r.expectedPoints>idx.pointReferences.max_size())return fail("index-capacity");
    if(r.expectedPoints>idx.pointReferences.capacity())
    {
        const std::size_t old=idx.pointReferences.capacity(),extra=old/2;
        const std::size_t maximum=idx.pointReferences.max_size();
        const std::size_t grown=extra>maximum-old?maximum:old+extra;
        idx.pointReferences.reserve(std::max(r.expectedPoints,grown));
    }
    if(!work(available))return fail("clock");r.pointReferenceAfter=std::move(after);r.indexPrepared=true;return true;
}
// Called only AFTER the caller's atomic publish. Validate first, then no allocation/callback writes.
template<class Mesh>
bool advanceIndex(const Mesh& m,const std::vector<label>& source,Result& r,
 Index& idx,std::string* why=nullptr)
{
    auto fail=[&](const char* s){if(why)*why=s;return false;};
    if(!r.plan.ready||!r.indexPrepared||source.size()!=m.faces.size()||m.faces.size()!=r.expectedFaces
       ||m.points.size()!=r.expectedPoints||m.nCells!=r.expectedCells
       ||idx.faceCount!=r.initialFaces||idx.pointCount!=r.initialPoints||idx.cellCount!=r.initialCells
       ||idx.pointReferences.capacity()<r.expectedPoints)return fail("published-shape");
    for(const auto& row:r.plan.rows)
    {
        const label f=row.native;
        if(f<0||std::size_t(f)>=m.faces.size()||!m.faceAlive[f]||source[f]!=row.source
           ||m.owner[f]!=row.owner||m.neighbour[f]!=row.neighbour||m.patch[f]!=row.patch
           ||m.zone[f]!=row.zone||m.faces[f]!=row.ring)return fail("published-row");
    }
    for(label f:r.retiredFaces)if(m.faceAlive[f])return fail("published-retire");
    for(const auto& group:r.unchangedCurrentCohorts)
    {
        const auto staged=r.sourceGroups.find(group.first);
        if(group.first<0||std::size_t(group.first)>=idx.bySource.size()
            ||idx.bySource[group.first]!=group.second||staged==r.sourceGroups.end()
            ||staged->second!=group.second)return fail("published-unchanged-cohort");
    }
    idx.pointReferences.resize(r.expectedPoints,0); // capacity prepared before publication
    for(const auto& p:r.pointReferenceAfter)idx.pointReferences[p.first]=p.second;
    for(auto& group:r.sourceGroups)idx.bySource[group.first].swap(group.second);
    idx.faceCount=m.faces.size();idx.pointCount=m.points.size();idx.cellCount=m.nCells;
    r.indexPrepared=false;return true;
}
} }
#endif
