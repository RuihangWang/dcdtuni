#ifndef dcCartRawQualityClusterUnion_H
#define dcCartRawQualityClusterUnion_H

#include "dcCartRawUnionStockMetrics.H"
#include "dcCartConformingConeSides.H"
#include "dcCartBoundedEarPartition.H"
#include "dcCartPreparedFacetBirth.H"
#include "dcCartPointRepairSourceContract.H"
#include "dcCartWeightUnionSourceContract.H"
#include "../dcCartSurfaces/dcCartPreparedFacetPartition.H"
#include <algorithm>
#include <array>
#include <cstring>
#include <cstdlib>
#include <deque>
#include <exception>
#include <map>
#include <unordered_map>
#include <set>
#include <string>
#include <utility>
#include <vector>
#include <fcntl.h>
#include <unistd.h>
#include <chrono>
#include <cerrno>
#include <sstream>
#include <iomanip>
#include <cstdint>

namespace Foam { namespace dcCartRawQualityClusterUnion {
namespace M=dcCartRawUnionStockMetrics;
namespace X=dcCartPreparedFacetPartition::detail;
using R=dcCartPreparedFacetPartition::Rational;
using Q=dcCartPreparedFacetPartition::ExactPoint;
using Ring=std::vector<label>;

// The exact operand of the existing availability predicate, for observation only.
struct NativeWorkObservation
{
    bool evaluated=false,remainingKnown=false,available=false;
    scalar remaining=0;
};
// Diagnostic values never participate in geometry, permissions or publication.
struct NativeDebtObservation
{
    bool attempted=false,accepted=false,eligible=false,coneInherited=false;
    const char* reason="not-called";
    label parent=-1,face=-1;
    std::size_t parents=0,faces=0;
    bool failedFaceKnown=false,nativeFaceKnown=false,nativeFaceAlive=false;
    label faceOwner=-1,faceNeighbour=-1,facePatch=-1,faceZone=-1;
    label nativeFaceOwner=-1,nativeFaceNeighbour=-1;
    NativeWorkObservation failedFaceWork;
};
struct Report
{
    std::size_t tested=0,accepted=0,members=0;
    std::size_t nativeDebtCalls=0,nativeDebtAccepted=0;
    NativeWorkObservation lastAvailableObservation;
    struct NativeRefusal {const char* reason=nullptr;std::size_t count=0;};
    std::array<NativeRefusal,64> nativeDebtRefused{};
    std::size_t nNativeDebtRefused=0,nativeDebtRefusalOverflow=0;
    void nativeDebtRefusal(const char* reason) noexcept
    {
        for(std::size_t i=0;i<nNativeDebtRefused;++i)
            if(!std::strcmp(nativeDebtRefused[i].reason,reason)){++nativeDebtRefused[i].count;return;}
        if(nNativeDebtRefused<nativeDebtRefused.size())nativeDebtRefused[nNativeDebtRefused++]={reason,1};
        else ++nativeDebtRefusalOverflow;
    }
    std::map<std::string,std::size_t> refused;
    struct Failure {label seed=-1;std::size_t members=0;const char* reason=nullptr;};
    std::array<Failure,8> first;
    std::size_t nFirst=0,currentMembers=0;
    label currentSeed=-1;
};
struct Row
{
    M::Face face;
    // Constituent native handles, not invented STL triangle identities.
    std::vector<label> originals;
};
struct Moment
{
    R volume;
    Q first{{R(0),R(0),R(0)}};
};
struct Plan
{
    label host=-1;
    std::vector<label> members,retired;
    std::vector<Row> rows;
    std::set<label> affected;
    Moment exactHost;
    const char* reason="unprepared";
};
inline Q exact(const point& p)
{return X::exact({{p.x(),p.y(),p.z()}});}
inline bool same(const Moment& a,const Moment& b)
{return a.volume==b.volume&&a.first==b.first;}
inline void add(Moment& a,const Moment& b)
{a.volume+=b.volume;a.first=X::add(a.first,b.first);}
inline bool coupled(const wordList& types,label p)
{
    if(p<0)return false;
    if(p>=types.size())return true;
    const word& t=types[p];
    return t!="wall"&&t!="patch";
}

// Reuse the native writer's actual file/LOCAL-region inventory addresses.
// Baffle pair patches bind the WHOLE region catalogue of that real faceZone.
// The master/slave patch and zone remain separate grouping keys below.
template<class Surfaces>
std::vector<std::string> sourceKeys
(
    const Surfaces& surfaces,const dictionary& identity,
    const wordList& names,const wordList& types,
    const labelList& wallMasters,const labelList& wallSlaves,
    const labelList& cycMasters,const labelList& cycSlaves,
    label domainOffset,const M::Available& work
)
{
    std::vector<std::string> out(types.size());
    if(names.size()!=types.size())return out;
    const entry* cat=identity.findEntry("dcdtuniSourceRegions",keyType::LITERAL);
    if(!cat||!cat->isDict())return out;
    std::map<label,std::set<std::string>> rows;
    std::set<std::string> regionUids;
    try
    {
        for(const entry& file:cat->dict())
        {
            if(!M::workAvailable(work))return std::vector<std::string>(out.size());
            if(!file.isDict()||file.keyword().isPattern())return out;
            label si=-1;
            for(label i=0;i<label(surfaces.info().size());++i)
                if(surfaces.info()[i].stlFile==file.keyword())
                {if(si>=0)return out;si=i;}
            if(si<0)return out;
            const auto& surface=surfaces.info()[si];
            const wordList nativeNames(surfaces.sourceRegionNames(si));
            std::set<label> coveredRegions;
            std::vector<std::pair<label,std::string>> inventory;
            for(const entry& re:file.dict())
            {
                if(!M::workAvailable(work))return std::vector<std::string>(out.size());
                if(!re.isDict()||re.keyword().isPattern())return out;
                label ri=-1;
                forAll(nativeNames,i)if(nativeNames[i]==re.keyword())
                {if(ri>=0)return out;ri=i;}
                if(ri<0||!coveredRegions.insert(ri).second)return out;
                const dictionary& d=re.dict();
                const std::set<std::string> allowed={"sourceEntityUid","sourceRegionUid",
                    "sourceRegionSha256","sourceFacetCount","operation"};
                for(const entry& field:d)
                    if(field.isDict()||field.keyword().isPattern()
                      ||!allowed.count(field.keyword().c_str()))return out;
                auto text=[&](const word& key,string& value)
                {
                    const ITstream* t=dcCartPointRepairSourceContract::primitive(d,key);
                    if(!t||t->size()!=1||!((*t)[0].isString()||(*t)[0].isWord()))return false;
                    value=(*t)[0].isString()?(*t)[0].stringToken():string((*t)[0].wordToken());
                    return true;
                };
                string entity,region,sha,op;
                const ITstream* countRow=dcCartPointRepairSourceContract::primitive(d,"sourceFacetCount");
                if(!text("sourceEntityUid",entity)||!text("sourceRegionUid",region)
                  ||!text("sourceRegionSha256",sha)||!text("operation",op)
                  ||!countRow||countRow->size()!=1||!(*countRow)[0].isLabel())return out;
                const label count=(*countRow)[0].labelToken();
                const string chars("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_./:-");
                if(entity.empty()||region.empty()||entity.find_first_not_of(chars)!=string::npos
                  ||region.find_first_not_of(chars)!=string::npos||sha.size()!=64
                  ||sha.find_first_not_of("0123456789abcdef")!=string::npos
                  ||count<=0||count!=surfaces.sourceRegionFacetCount(si,ri)
                  ||!regionUids.insert(region.c_str()).second
                  ||!(op=="modelBoundary"||op=="doorSheet"||op=="cellZone"
                    ||op=="liquidTransportUnionBoundary"))return out;
                const std::string key=std::string(entity.c_str())+"\n"+region.c_str()+"\n"
                    +sha.c_str()+"\n"+op.c_str()+"\n"+std::to_string(count);
                inventory.push_back({ri,key});
            }
            if(surface.internalBaffle)
            {
                // Never pretend a multi-region baffle is one selected region.
                if(coveredRegions.size()!=std::size_t(nativeNames.size()))return out;
                const label zone=surfaces.faceZoneNames().find(surface.faceZone);
                if(zone<0)return out;
                for(const labelList* ps:{&wallMasters,&wallSlaves,&cycMasters,&cycSlaves})
                {
                    if(zone>=ps->size())return out;
                    const label p=(*ps)[zone];
                    if(p>=0&&p<types.size())for(const auto& x:inventory)rows[p].insert(x.second);
                }
            }
            else if(surface.nRegions>0)
                for(const auto& x:inventory)
                {
                    const label p=surface.regionOffset+x.first;
                    if(x.first<surface.nRegions&&p>=0&&p<surfaces.nPatches()
                      &&p<types.size())rows[p].insert(x.second);
                }
        }
        for(const auto& p:rows)
        {
            if(coupled(types,p.first))continue;
            std::string key;
            for(const auto& x:p.second)key+=x+"\n--\n";
            out[p.first]=std::move(key);
        }
        // These six addresses are created by the real native Grid builder.
        for(label d=0;d<6;++d)
        {
            const label p=domainOffset+d;
            if(p>=0&&p<types.size()&&!coupled(types,p))
                out[p]="native-grid-domain:"+std::string(names[p].c_str());
        }
    }
    catch(...) {return std::vector<std::string>(out.size());}
    return out;
}

// No point/coordinate equality can supply topology or source authority.
// Exact predicates below interpret the actual native binary64 point values.
struct Geometry
{
    const std::vector<point>& points;
    const M::Available& available;
    // Coordinates stay immutable; lookup order supplies no geometry authority.
    // Node references survive rehash. No cache iterator escapes at().
    std::unordered_map<label,Q> cache;
    struct Tetra
    {
        R volume;
        Q first{{R(0),R(0),R(0)}};
        bool ready=false;
    };
    struct RingValue
    {
        Q area{{R(0),R(0),R(0)}},primitive{{R(0),R(0),R(0)}};
        R bound;
        bool areaReady=false,planarReady=false,simplePlanarReady=false,primitiveReady=false,boundReady=false;
        std::vector<Tetra> tetra;
    };
    // Sparse call-local FIFO: mesh points never change during this repair.
    // Full ordered IDs retain orientation/start vertex; no cyclic key or welding.
    std::map<Ring,RingValue> ringCache;
    std::deque<Ring> ringOrder;
    std::size_t areaHits=0,areaMisses=0,planarHits=0,tetraHits=0,tetraMisses=0;
    RingValue* ringValue(const Ring& ring)
    {
        if(!M::workAvailable(available))throw X::Refusal("clock");
        if(ring.size()<3||ring.size()>64)return nullptr;
        auto it=ringCache.find(ring);
        if(it!=ringCache.end())return &it->second;
        // Storage bound only: eviction always falls back to the literal math.
        if(ringCache.size()>=8192)
        {ringCache.erase(ringOrder.front());ringOrder.pop_front();}
        it=ringCache.emplace(ring,RingValue{}).first;
        try {ringOrder.push_back(ring);}
        catch(...) {ringCache.erase(it);throw;}
        return &it->second;
    }
    explicit Geometry(const std::vector<point>& p,const M::Available& a):points(p),available(a){}
    const Q& at(label id)
    {
        if(!M::workAvailable(available))throw X::Refusal("clock");
        if(id<0||std::size_t(id)>=points.size())throw X::Refusal("point-id");
        auto it=cache.find(id);
        if(it==cache.end())it=cache.emplace(id,exact(points[id])).first;
        return it->second;
    }
    Q area(const Ring& ring)
    {
        RingValue* saved=ringValue(ring);
        if(saved&&saved->areaReady){++areaHits;return saved->area;}
        ++areaMisses;
        Q sum{{R(0),R(0),R(0)}};
        for(std::size_t j=0;j<ring.size();++j)
            sum=X::add(sum,X::cross(at(ring[j]),at(ring[(j+1)%ring.size()])));
        const Q value=X::mul(sum,R(1,2));
        if(saved){saved->area=value;saved->areaReady=true;}
        return value;
    }
    bool planar(const Ring& ring,Q& normal)
    {
        RingValue* saved=ringValue(ring);
        if(saved&&saved->planarReady)
        {++planarHits;normal=saved->area;return true;}
        if(!planarLiteral(ring,normal))return false;
        if(saved)saved->planarReady=true;
        return true;
    }
    bool planarLiteral(const Ring& ring,Q& normal)
    {
        if(ring.size()<3||ring.size()>64)return false;
        const std::set<label> unique(ring.begin(),ring.end());
        if(unique.size()!=ring.size())return false;
        normal=area(ring);if(!X::nonzero(normal))return false;
        for(label p:ring)if(X::side(normal,at(ring.front()),at(p))!=0)return false;
        bool positive=false;
        for(std::size_t j=0;j<ring.size();++j)
        {
            const Q edge=X::sub(at(ring[(j+1)%ring.size()]),at(ring[j]));
            if(!X::nonzero(edge))return false;
            for(label p:ring)
            {
                const R turn=X::dot(normal,X::cross(edge,X::sub(at(p),at(ring[j]))));
                if(turn<0)return false;
                positive=positive||turn>0;
            }
        }
        return positive;
    }
    // Moment reference for an original simple planar outline. Convexity remains
    // a separate native keep/Stock gate; this emits no faces or permissions.
    bool simplePlanar(const Ring& ring,Q& normal)
    {
        if(planar(ring,normal))return true; // preserve the original convex fast path
        RingValue* saved=ringValue(ring);
        if(saved&&saved->simplePlanarReady){normal=saved->area;return true;}
        if(ring.size()<3||ring.size()>64)return false;
        normal=area(ring);if(!X::nonzero(normal))return false;
        for(label p:ring)if(X::side(normal,at(ring.front()),at(p))!=0)return false;
        unsigned drop=0;while(drop<3&&normal[drop]==0)++drop;if(drop==3)return false;
        const auto pieces=dcCartBoundedEarPartition::build(ring,points.size(),
            [&](label p){const point& x=points.at(p);return std::array<double,3>{{x.x(),x.y(),x.z()}};},
            drop,[&](const dcCartBoundedEarPartition::Triangle& t)
            {
                // Exact positive reference triangles and the ear module's full
                // directed-edge certificate prove a simple original footprint.
                return X::dot(normal,X::cross(X::sub(at(t[1]),at(t[0])),X::sub(at(t[2]),at(t[0]))))>0;
            },available);
        if(!pieces.complete||!M::workAvailable(available))return false;
        if(saved)saved->simplePlanarReady=true;
        return true;
    }
    Q primitiveNormal(const Ring& ring,const Q& areaValue)
    {
        RingValue* saved=ringValue(ring);
        if(saved&&saved->primitiveReady)return saved->primitive;
        const Q value=X::primitivePositiveNormal(areaValue);
        if(saved){saved->primitive=value;saved->primitiveReady=true;}
        return value;
    }
    R planeBound(const Ring& ring,const Q& normal)
    {
        RingValue* saved=ringValue(ring);
        if(saved&&saved->boundReady&&saved->primitive==normal)return saved->bound;
        const R value=X::dot(normal,at(ring.front()));
        if(saved&&saved->primitiveReady&&saved->primitive==normal)
        {saved->bound=value;saved->boundReady=true;}
        return value;
    }
    bool segment(label p,label a,label b)
    {return X::onSegment(at(p),at(a),at(b));}
    bool moments(const std::vector<M::Face>& shell,label c,Moment& out)
    {
        std::map<std::pair<label,label>,std::size_t> edges;
        Moment value;
        for(const auto& f:shell)
        {
            Ring ring=f.ring;if(f.owner!=c)std::reverse(ring.begin(),ring.end());
            Q n;if(!simplePlanar(ring,n))return false;
            for(std::size_t j=0;j<ring.size();++j)++edges[{ring[j],ring[(j+1)%ring.size()]}];
            RingValue* saved=ringValue(ring);
            if(saved&&saved->tetra.empty())saved->tetra.resize(ring.size()-2);
            for(std::size_t j=1;j+1<ring.size();++j)
            {
                const Q a=at(ring[0]),b=at(ring[j]),d=at(ring[j+1]);
                Tetra* term=saved?&saved->tetra[j-1]:nullptr;
                if(term&&term->ready)++tetraHits;else ++tetraMisses;
                const R v=term&&term->ready?term->volume:X::dot(a,X::cross(b,d))/6;
                value.volume+=v;
                const Q first=term&&term->ready?term->first:
                    X::mul(X::add(X::add(a,b),d),v/4);
                value.first=X::add(value.first,first);
                // Preserve shell/triangle ordered accumulation, not per-ring sums.
                if(term&&!term->ready)
                {term->volume=v;term->first=first;term->ready=true;}
            }
        }
        for(const auto& e:edges)
        {
            const auto r=edges.find({e.first.second,e.first.first});
            if(e.second!=1||r==edges.end()||r->second!=1)return false;
        }
        if(value.volume<=0)return false;
        out=std::move(value);return true;
    }
    bool outline(const std::vector<Ring>& rings,Ring& out)
    {
        // Atomize only this bounded same-plane group. Endpoints remain actual IDs.
        std::set<label> vertices;Q originalArea{{R(0),R(0),R(0)}};
        for(const Ring& r:rings)
        {vertices.insert(r.begin(),r.end());originalArea=X::add(originalArea,area(r));}
        if(vertices.size()>512)return false;
        std::map<std::pair<label,label>,std::size_t> count;
        for(const Ring& r:rings)for(std::size_t j=0;j<r.size();++j)
        {
            label a=r[j],b=r[(j+1)%r.size()];
            if(a==b||at(a)==at(b))return false;
            unsigned axis=0;while(axis<3&&at(a)[axis]==at(b)[axis])++axis;
            if(axis==3)return false;
            std::vector<std::pair<R,label>> hits;
            for(label p:vertices)if(segment(p,a,b))
                hits.push_back({(at(p)[axis]-at(a)[axis])/(at(b)[axis]-at(a)[axis]),p});
            std::sort(hits.begin(),hits.end());
            for(std::size_t k=1;k<hits.size();++k)
            {
                if(hits[k-1].first==hits[k].first)return false; // no coordinate weld
                ++count[{hits[k-1].second,hits[k].second}];
            }
        }
        std::map<label,label> next;std::map<label,label> previous;
        for(const auto& e:count)
        {
            const auto reverse=count.find({e.first.second,e.first.first});
            if(e.second!=1||(reverse!=count.end()&&reverse->second!=1))return false;
            if(reverse!=count.end())continue;
            if(!next.emplace(e.first.first,e.first.second).second
              ||!previous.emplace(e.first.second,e.first.first).second)return false;
        }
        if(next.size()<3||next.size()>64||next.size()!=previous.size())return false;
        Ring cycle;label p=next.begin()->first;
        do
        {
            if(cycle.size()>=next.size()||!next.count(p))return false;
            cycle.push_back(p);p=next.at(p);
        }while(p!=cycle.front());
        if(cycle.size()!=next.size()||area(cycle)!=originalArea)return false;
        // Convex outline with collinear OUTER boundary points retained.
        const Q n=area(cycle);
        for(std::size_t j=0;j<cycle.size();++j)
            for(label q:cycle)
                if(X::dot(n,X::cross(X::sub(at(cycle[(j+1)%cycle.size()]),at(cycle[j])),
                                    X::sub(at(q),at(cycle[j]))))<0)return false;
        out=std::move(cycle);return true;
    }
};

// Pure staging: no mesh write, no lineage invalidation, no source grant.
template<class Mesh>
bool stage
(
    const Mesh& m,const std::vector<std::vector<label>>& cf,
    const std::vector<label>& members,label host,const wordList& types,
    const std::vector<std::string>& sources,label rank,
    Geometry& geometry,const std::map<label,Moment>& currentMoments,Plan& out,
    bool nativeFacetOriginalRings=false
)
{
    Plan p;p.host=host;p.members=members;
    auto fail=[&](const char* why){out.reason=why;return false;};
    if(members.empty()||members.size()>9||std::find(members.begin(),members.end(),host)==members.end())return fail("cluster-cap");
    std::set<label> selected(members.begin(),members.end()),all;
    if(selected.size()!=members.size())return fail("duplicate-member");
    for(label c:members)
    {
        if(c<0||c>=m.nCells||!m.cellAlive[c]||m.cellRank[c]!=rank||cf[c].size()<4||cf[c].size()>64)return fail("cell-scope");
        for(label f:cf[c])all.insert(f);
    }
    struct Group
    {label other,patch,zone;std::string source;Q normal;R bound;std::vector<label> ids;std::vector<Ring> rings;};
    std::vector<Group> groups;
    for(label f:all)
    {
        if(!M::workAvailable(geometry.available))return fail("clock");
        if(f<0||std::size_t(f)>=m.faces.size()||!m.faceAlive[f])return fail("face-state");
        const label own=m.owner[f],nei=m.neighbour[f],patch=m.patch[f],zone=m.zone[f];
        const bool a=selected.count(own),b=nei>=0&&selected.count(nei);
        if(!a&&!b)return fail("face-incidence");
        if(a&&b)
        {if(patch!=-1||zone!=-1)return fail("protected-separator");p.retired.push_back(f);continue;}
        const label other=a?nei:own;
        if(other>=0&&(other>=m.nCells||!m.cellAlive[other]||m.cellRank[other]!=rank))return fail("outside-rank");
        if(other<0&&(patch<0||patch>=types.size()||coupled(types,patch)))return fail("coupled-boundary");
        if(other>=0&&patch!=-1)return fail("internal-patch");
        Ring ring=m.faces[f];if(!a)std::reverse(ring.begin(),ring.end());
        Q area;if(!geometry.planar(ring,area))return fail("exact-plane");
        const Q n=geometry.primitiveNormal(ring,area);
        const R d=geometry.planeBound(ring,n);
        const std::string source=other<0&&std::size_t(patch)<sources.size()?sources[patch]:std::string();
        std::size_t g=groups.size();
        for(std::size_t k=0;k<groups.size();++k)
        {
            const auto& x=groups[k];
            // Unknown physical source keeps each literal ring separate.
            if(x.other==other&&x.patch==patch&&x.zone==zone&&x.source==source
              &&x.normal==n&&x.bound==d&&((other>=0&&zone<0)||(other<0&&!source.empty()))){g=k;break;}
        }
        if(g==groups.size())groups.push_back({other,patch,zone,source,n,d,{},{}});
        groups[g].ids.push_back(f);groups[g].rings.push_back(std::move(ring));
        if(other>=0)p.affected.insert(other);
    }
    for(const auto& g:groups)
    {
        Ring ring;
        if(g.rings.size()==1)ring=g.rings.front();
        else if(!geometry.outline(g.rings,ring))
        {
            if(!M::workAvailable(geometry.available))return fail("clock");
            // Keep the existing cone proposal's fast outline rejection. Only
            // native W/R repair needs to retain a disconnected outer group.
            if(!nativeFacetOriginalRings)return fail("outline");
            // A same-plane role group need not be one convex boundary cycle.
            // Preserve each actual host-oriented native outer ring/ID instead.
            // Exact closed V/M and all downstream source/Stock gates still run.
            for(std::size_t k=0;k<g.rings.size();++k)
                p.rows.push_back({M::Face(g.ids[k],host,g.other,g.patch,g.zone,g.rings[k]),{g.ids[k]}});
            continue;
        }
        const label keep=*std::min_element(g.ids.begin(),g.ids.end());
        p.rows.push_back({M::Face(keep,host,g.other,g.patch,g.zone,ring),g.ids});
        for(label f:g.ids)if(f!=keep)p.retired.push_back(f);
    }
    p.affected.insert(host);for(label c:members)p.affected.insert(c);
    std::sort(p.rows.begin(),p.rows.end(),[](const Row& a,const Row& b){return a.face.id<b.face.id;});
    std::sort(p.retired.begin(),p.retired.end());p.retired.erase(std::unique(p.retired.begin(),p.retired.end()),p.retired.end());
    std::vector<M::Face> after;for(const auto& row:p.rows)after.push_back(row.face);
    Moment expected;
    for(label c:members)
    {
        const auto before=currentMoments.find(c);
        if(before==currentMoments.end())return fail("original-closed-moment");
        add(expected,before->second);
    }
    Moment actual;
    if(!geometry.moments(after,host,actual)||!same(actual,expected))return fail("exact-volume-first-moment");
    p.exactHost=std::move(actual);
    p.reason=nullptr;out=std::move(p);return true;
}

inline bool exactCentroidMargin(Geometry& geometry,const std::vector<M::Face>& faces,label c,const Moment& value)
{
    const Q centre=X::mul(value.first,R(1)/value.volume);
    for(const auto& f:faces)
    {
        Q n=geometry.area(f.ring);if(f.owner!=c)n=X::mul(n,R(-1));
        const R h=X::dot(n,X::sub(geometry.at(f.ring.front()),centre));
        if(h<=0||h*h<=R(9,1000000000000LL)*X::dot(n,n))return false;
    }
    return true;
}
inline bool bits(scalar a,scalar b)
{return std::isfinite(a)&&std::isfinite(b)&&std::memcmp(&a,&b,sizeof(scalar))==0;}
inline bool bits(const point& a,const point& b)
{return bits(a.x(),b.x())&&bits(a.y(),b.y())&&bits(a.z(),b.z());}

// Physical outside shells are exactly unchanged (verified below); their face
// partition may be restored. Check affected numeric/stock roles independently,
// without forcing an unrelated old failing interface to heal first.
inline bool outsideGeometry
(
    const M::Cell& before,const M::Cell& after,
    const std::map<label,std::vector<label>>& originals
)
{
    if(!before.finite||!after.finite)return false;
    std::map<label,std::size_t> oldIndex;
    for(std::size_t j=0;j<before.faceIds.size();++j)oldIndex[before.faceIds[j]]=j;
    auto oldFaces=[&](label f)->std::vector<label>
    {const auto it=originals.find(f);return it==originals.end()?std::vector<label>{f}:it->second;};
    for(std::size_t j=0;j<after.faceIds.size();++j)
    {
        const scalar ap=(after.areas[j]&(after.centres[j]-after.centre))/3;
        if(!std::isfinite(ap))return false;
        bool oldPass=false;scalar oldWorst=GREAT;
        for(label f:oldFaces(after.faceIds[j]))
        {
            const auto it=oldIndex.find(f);if(it==oldIndex.end())return false;
            const std::size_t k=it->second;
            const scalar bp=(before.areas[k]&(before.centres[k]-before.centre))/3;
            if(!std::isfinite(bp))return false;
            oldPass=oldPass||bp>1e-15*before.volume;
            oldWorst=min(oldWorst,bp/max(mag(before.areas[k]),VSMALL));
        }
        if(oldPass&&ap<=1e-15*after.volume)return false;
        if(!oldPass&&ap/max(mag(after.areas[j]),VSMALL)<oldWorst)return false;
        for(std::size_t k=0;k<after.faceIds.size();++k)if(j!=k)
        {
            vector d=after.centres[k]-after.centres[j];
            d/=max(mag(d),VSMALL);
            const scalar ac=(after.areas[j]/mag(after.areas[j]))&d;
            if(!std::isfinite(ac))return false;
            bool passing=false;scalar worst=-GREAT;
            for(label a:oldFaces(after.faceIds[j]))for(label b:oldFaces(after.faceIds[k]))
            {
                const std::size_t oi=oldIndex.at(a),ok=oldIndex.at(b);
                vector od=before.centres[ok]-before.centres[oi];
                od/=max(mag(od),VSMALL);
                const scalar bc=(before.areas[oi]/mag(before.areas[oi]))&od;
                if(!std::isfinite(bc))return false;
                passing=passing||bc<=-1e-6;worst=max(worst,bc);
            }
            if(passing ? ac>-1e-6 : ac>worst)return false;
        }
    }
    return true;
}

// Bounded immutable observation of actual native-facet proposal failures.
// Output is data only. This function returns no geometry or source authority.
template<class Mesh>
void captureNativeFacetFailure
(
    const Mesh& m,const std::vector<std::vector<label>>& cf,
    const wordList& types,const std::vector<std::string>& sources,
    const std::map<label,M::Cell>& current,const std::map<label,Moment>& moments,
    label rank,unsigned record,label seed,label seedFace,const M::FacetQuality& seedQuality,
    const std::vector<label>& members,const char* stageName,const char* reason,
    const Plan* future,const M::Cell* host,const M::Available& available,
    const NativeDebtObservation* nativeObservation=nullptr
) noexcept
{
    try
    {
        if(record>=8||!M::workAvailable(available))return;
        bool complete=true;const char* state="complete";
        std::set<label> cells(members.begin(),members.end()),faces,points,patches;
        auto bounded=[&](bool ok)
        {if(!ok){complete=false;state="bounds-or-reference";}return ok;};
        for(label c:members)
        {
            if(!M::workAvailable(available)){complete=false;state="clock";break;}
            if(!bounded(c>=0&&std::size_t(c)<cf.size()))break;
            for(label f:cf[c])
            {
                if(!bounded(f>=0&&std::size_t(f)<m.faces.size()))break;
                cells.insert(m.owner[f]);if(m.neighbour[f]>=0)cells.insert(m.neighbour[f]);
            }
        }
        if(future)cells.insert(future->affected.begin(),future->affected.end());
        bounded(cells.size()<=64&&types.size()==sources.size());
        if(complete)for(label c:cells)
        {
            if(!M::workAvailable(available)){complete=false;state="clock";break;}
            if(!bounded(c>=0&&std::size_t(c)<cf.size()&&std::size_t(c)<m.cellAlive.size()
                &&std::size_t(c)<m.cellRank.size()&&cf[c].size()<=64))break;
            faces.insert(cf[c].begin(),cf[c].end());
            if(!bounded(faces.size()<=1024))break;
        }
        // cf may retain old associations. Certify only its complete LIVE shell
        // against actual current owner/neighbour incidence, without a mesh dump.
        std::map<label,std::set<label>> liveShell;
        if(complete)
        {
            if(!bounded(m.faceAlive.size()==m.faces.size()&&m.owner.size()==m.faces.size()
                &&m.neighbour.size()==m.faces.size()))complete=false;
            for(label c:cells)liveShell.emplace(c,std::set<label>{});
        }
        if(complete)for(std::size_t f=0;f<m.faces.size();++f)
        {
            if((f&1023)==0&&!M::workAvailable(available)){complete=false;state="clock";break;}
            if(!m.faceAlive[f])continue;
            for(label c:std::array<label,2>{{m.owner[f],m.neighbour[f]}})
            {
                const auto at=liveShell.find(c);if(at==liveShell.end())continue;
                at->second.insert(label(f));if(!bounded(at->second.size()<=64))break;
            }
            if(!complete)break;
        }
        if(complete)for(label c:cells)
        {
            std::set<label> supplied;
            for(label f:cf[c])
            {
                if(!bounded(f>=0&&std::size_t(f)<m.faces.size()))break;
                if(!m.faceAlive[f])continue;
                if((m.owner[f]!=c&&m.neighbour[f]!=c)||!supplied.insert(f).second)
                {complete=false;state="invalid-current-cf";break;}
            }
            if(!complete)break;
            if(supplied!=liveShell.at(c)){complete=false;state="incomplete-current-cf";break;}
        }
        auto ringPoints=[&](const Ring& ring)->bool
        {
            if(!bounded(ring.size()>=3&&ring.size()<=64))return false;
            for(label point:ring)
            {
                if(!M::workAvailable(available)){complete=false;state="clock";return false;}
                if(!bounded(point>=0&&std::size_t(point)<m.points.size()
                    &&M::finitePoint(m.points[point])))return false;
                points.insert(point);if(!bounded(points.size()<=2048))return false;
            }
            return true;
        };
        if(complete)for(label f:faces)
        {
            if(!bounded(f>=0&&std::size_t(f)<m.faces.size())||!ringPoints(m.faces[f]))break;
            if(m.patch[f]>=0)patches.insert(m.patch[f]);
        }
        if(complete&&future)for(const auto& row:future->rows)
        {
            if(!ringPoints(row.face.ring))break;
            if(row.face.patch>=0)patches.insert(row.face.patch);
        }
        if(complete)for(label patch:patches)
            if(!bounded(patch>=0&&patch<types.size()&&sources[patch].size()<=1024))break;
        std::ostringstream out;out<<std::boolalpha;
        auto text=[&](const std::string& value)
        {
            out<<'"';for(unsigned char c:value)
            {if(c=='"'||c=='\\')out<<'\\'<<char(c);else if(c<32)out<<"\\u"<<std::hex<<std::setw(4)<<std::setfill('0')<<unsigned(c)<<std::dec;else out<<char(c);}out<<'"';
        };
        auto scalarBits=[&](scalar value)
        {
            std::uint64_t bits=0;static_assert(sizeof(value)==sizeof(bits),"binary64 observation");
            std::memcpy(&bits,&value,sizeof(bits));out<<'"'<<std::hex<<std::setw(16)<<std::setfill('0')<<bits<<std::dec<<'"';
        };
        auto pointBits=[&](const point& point)
        {out<<'[';for(label k=0;k<3;++k){if(k)out<<',';scalarBits(point[k]);}out<<']';};
        auto ids=[&](const auto& values)
        {out<<'[';bool comma=false;for(label value:values){if(comma)out<<',';comma=true;out<<value;}out<<']';};
        auto metric=[&](const M::Cell& cell)
        {
            out<<"{\"finite\":"<<cell.finite<<",\"strictGeometry\":"<<cell.strictGeometry<<",\"centreBits\":";pointBits(cell.centre);
            out<<",\"volumeBits\":";scalarBits(cell.volume);out<<",\"determinantBits\":";scalarBits(cell.determinant);
            out<<",\"aspectBits\":";scalarBits(cell.aspect);out<<",\"reason\":";text(cell.reason?cell.reason:"unknown");
            out<<",\"blockingFace\":"<<cell.blockingFace<<",\"blockingPoint\":"<<cell.blockingPoint
                <<",\"blockingPair\":["<<cell.blockingPair.face<<','<<cell.blockingPair.otherFace<<']'<<'}';
        };
        auto exactMoment=[&](const Moment& moment)
        {
            out<<"{\"volume\":\""<<moment.volume<<"\",\"first\":[";
            for(label k=0;k<3;++k){if(k)out<<',';out<<'"'<<moment.first[k]<<'"';}out<<"]}";
        };
        auto face=[&](const M::Face& f)
        {out<<"{\"native\":"<<f.id<<",\"faceAlive\":"<<bool(m.faceAlive[f.id])<<",\"owner\":"<<f.owner<<",\"neighbour\":"<<f.neighbour<<",\"patch\":"<<f.patch<<",\"zone\":"<<f.zone<<",\"ring\":";ids(f.ring);out<<'}';};
        out<<"{\"schema\":\"actual-native-facet-failure/v1\",\"observationOnly\":true,\"sourcePermission\":false,\"publicationPermission\":false,\"role\":\"NativeFacetRepair\",\"rank\":"<<rank<<",\"record\":"<<record
            <<",\"seed\":"<<seed<<",\"seedNativeFace\":"<<seedFace<<",\"selectedMembers\":";ids(members);
        out<<",\"stage\":";text(stageName?stageName:"unknown");out<<",\"reason\":";text(reason?reason:"unknown");
        if(nativeObservation)
        {
            out<<",\"nativePredictor\":{\"attempted\":"<<(nativeObservation->attempted?"true":"false")
                <<",\"accepted\":"<<(nativeObservation->accepted?"true":"false")
                <<",\"eligible\":"<<(nativeObservation->eligible?"true":"false")
                <<",\"coneInherited\":"<<(nativeObservation->coneInherited?"true":"false")<<",\"reason\":";
            text(nativeObservation->reason);
            out<<",\"parent\":"<<nativeObservation->parent<<",\"face\":"<<nativeObservation->face
                <<",\"parents\":"<<nativeObservation->parents<<",\"faces\":"<<nativeObservation->faces;
            if(nativeObservation->failedFaceKnown)
            {
                // Metadata is from this exact rejected view. It is not a far full shell.
                const label patch=nativeObservation->facePatch;
                const bool typeKnown=patch>=0&&patch<types.size();
                const bool sourceKnown=patch>=0&&std::size_t(patch)<sources.size();
                out<<",\"failedFaceMetadata\":{\"metadataOnly\":true,\"view\":\"prospective-shell\""
                    <<",\"owner\":"<<nativeObservation->faceOwner<<",\"neighbour\":"<<nativeObservation->faceNeighbour
                    <<",\"patch\":"<<patch<<",\"zone\":"<<nativeObservation->faceZone
                    <<",\"nativeFaceKnown\":"<<(nativeObservation->nativeFaceKnown?"true":"false")
                    <<",\"nativeFaceAlive\":"<<(nativeObservation->nativeFaceAlive?"true":"false")
                    <<",\"nativeOwner\":"<<nativeObservation->nativeFaceOwner
                    <<",\"nativeNeighbour\":"<<nativeObservation->nativeFaceNeighbour
                    <<",\"typeKnown\":"<<(typeKnown?"true":"false")<<",\"type\":";
                text(typeKnown?types[patch].c_str():"unknown");
                out<<",\"declaredSourceKeyKnown\":"<<(sourceKnown?"true":"false")<<",\"declaredSourceKey\":";
                text(sourceKnown?sources[patch]:std::string("unknown"));
                const auto& work=nativeObservation->failedFaceWork;
                out<<",\"workEvaluated\":"<<(work.evaluated?"true":"false")
                    <<",\"workAvailable\":"<<(work.available?"true":"false")
                    <<",\"remainingKnown\":"<<(work.remainingKnown?"true":"false")<<",\"remainingBits\":";
                if(work.remainingKnown)scalarBits(work.remaining);else out<<"null";
                out<<'}';
            }
            out<<'}';
        }
        out<<",\"captureComplete\":"<<(complete?"true":"false")<<",\"captureState\":";text(state);
        out<<",\"seedQualityNumeric\":"<<seedQuality.numeric<<",\"seedWeightBits\":";scalarBits(seedQuality.v[1]);
        out<<",\"seedVolRatioBits\":";scalarBits(seedQuality.v[2]);
        if(complete)
        {
            out<<",\"nativePoints\":[";bool comma=false;
            for(label point:points){if(comma)out<<',';comma=true;out<<"{\"native\":"<<point<<",\"bits\":";pointBits(m.points[point]);out<<'}';}
            out<<"],\"nativeFaces\":[";comma=false;
            for(label f:faces){if(comma)out<<',';comma=true;face(M::Face(f,m.owner[f],m.neighbour[f],m.patch[f],m.zone[f],m.faces[f]));}
            out<<"],\"nativeCells\":[";comma=false;
            for(label c:cells)
            {
                if(comma)out<<',';comma=true;out<<"{\"native\":"<<c<<",\"alive\":"<<unsigned(m.cellAlive[c])<<",\"rank\":"<<m.cellRank[c]<<",\"faces\":";ids(cf[c]);out<<",\"currentShellFaces\":";ids(liveShell.at(c));
                const auto found=current.find(c);out<<",\"metricCached\":"<<(found!=current.end()?"true":"false");
                if(found!=current.end()){out<<",\"metric\":";metric(found->second);}
                const auto value=moments.find(c);out<<",\"exactMomentCached\":"<<(value!=moments.end()?"true":"false");
                if(value!=moments.end()){out<<",\"exactMoment\":";exactMoment(value->second);}out<<'}';
            }
            out<<"],\"patchMetadata\":[";comma=false;
            for(label patch:patches){if(comma)out<<',';comma=true;out<<"{\"patch\":"<<patch<<",\"type\":";text(types[patch].c_str());out<<",\"declaredSourceKey\":";text(sources[patch]);out<<'}';}
            out<<"],\"futurePlanAvailable\":"<<(future&&!future->reason?"true":"false");
            if(future&&!future->reason)
            {
                out<<",\"futureHost\":"<<future->host<<",\"futureRetired\":";ids(future->retired);out<<",\"futureExactMoment\":";exactMoment(future->exactHost);
                out<<",\"futureRows\":[";comma=false;for(const auto& row:future->rows){if(comma)out<<',';comma=true;out<<"{\"face\":";face(row.face);out<<",\"originals\":";ids(row.originals);out<<'}';}out<<']';
            }
            if(host){out<<",\"measuredFutureHost\":";metric(*host);}
        }
        out<<"}\n";std::string payload=out.str();
        if(payload.size()>1024*1024)return;
        if(!M::workAvailable(available))return;
        const auto stamp=std::chrono::duration_cast<std::chrono::nanoseconds>
            (std::chrono::steady_clock::now().time_since_epoch()).count();
        const std::string path="raw-native-facet-failure-rank"+std::to_string(rank)+"-record"+std::to_string(record)
            +"-pid"+std::to_string(::getpid())+"-ns"+std::to_string(stamp)+".json";
        // Never overwrite evidence, even if the caller reuses its Mesh cwd.
        const int fd=::open(path.c_str(),O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC,0600);
        if(fd<0)return;
        std::size_t written=0;
        while(written<payload.size())
        {
            const ssize_t n=::write(fd,payload.data()+written,payload.size()-written);
            if(n<0&&errno==EINTR)continue;
            if(n<=0)break;written+=std::size_t(n);
        }
        ::close(fd);
    }
    catch(...){} // Observation cannot change any admission or publication decision.
}

// Prospective normal native representation, not source or publication permission.
// A missing shared classifier or any uncertain endpoint retains the old predictor.
struct NativeFacePlan
{
    bool known=false,split=true,retained=false;
    vector area=vector::zero;point centre=point::zero;
};
using NativeFacePlanner=std::function<NativeFacePlan(const M::Face&)>;
using NativeLocalFaceOF=std::function<void(const std::vector<point>&,vector&,point&)>;
using NativeCurrentShell=std::function<bool(label,std::vector<M::Face>&)>;

template<class Mesh>
bool unsplitNativeDebt
(
    const Mesh& m,const std::map<label,std::vector<M::Face>>& shells,label host,
    const wordList& types,Geometry& geometry,const M::FaceOF& faceOF,
    const M::Available& available,const NativeFacePlanner& planner,M::ConeDebt& out,
    const NativeLocalFaceOF& localFaceOF=NativeLocalFaceOF(),
    const NativeCurrentShell& currentShell=NativeCurrentShell(),
    NativeDebtObservation* observation=nullptr,
    const NativeWorkObservation* availableObservation=nullptr
)
{
    if(observation){const bool eligible=observation->eligible;*observation=NativeDebtObservation{};observation->eligible=eligible;observation->attempted=true;observation->reason="entry-planner-clock-shell";observation->parent=host;}
    if(!planner||!M::workAvailable(available)||!shells.count(host))return false;
    struct Body {bool marked=false;point apex=point::zero,rawCentre=point::zero;std::vector<NativeFacePlan> faces;};
    std::map<label,Body> bodies;
    std::map<label,std::vector<M::Face>> view;
    std::size_t viewFaces=0;
    auto observe=[&](const char* reason,label parent=-1,label face=-1)
    {
        if(observation){observation->reason=reason;observation->parent=parent;observation->face=face;
            observation->parents=view.size();observation->faces=viewFaces;}
    };
    constexpr std::size_t maxParents=64,maxFaces=4096;
    auto volumeCentre=[&](const std::vector<vector>& av,const std::vector<point>& fc,point& centre,label parent,label face)
    {
        observe("volume-centre-shape",parent,face);
        if(av.empty()||av.size()!=fc.size())return false;
        point estimate(point::zero);for(const point& p:fc)estimate+=p;estimate/=fc.size();
        scalar volume3=0;point moment(point::zero);
        for(std::size_t j=0;j<fc.size();++j)
        {
            observe("volume-centre-clock-or-finite",parent,face);
            if(!M::workAvailable(available)||!M::finitePoint(av[j])||!M::finitePoint(fc[j]))return false;
            const scalar v=av[j]&(fc[j]-estimate);volume3+=v;moment+=v*(.75*fc[j]+.25*estimate);
        }
        observe("volume-centre-volume-or-moment",parent,face);
        if(!std::isfinite(volume3)||volume3<=VSMALL||!M::finitePoint(moment))return false;
        centre=moment/max(volume3,VSMALL);observe("volume-centre-finite",parent,face);return M::finitePoint(centre);
    };
    auto classifyBody=[&](label c)->Body*
    {
        observe("classify-clock",c);
        if(!M::workAvailable(available))return nullptr;
        const auto saved=bodies.find(c);if(saved!=bodies.end())return &saved->second;
        observe("parent-shape-rank-or-bound",c);
        if(c<0||c>=m.nCells||!m.cellAlive[c]||m.cellRank[c]!=m.cellRank[host]||view.size()>=maxParents)return nullptr;
        std::vector<M::Face> full;
        const auto staged=shells.find(c);
        if(staged!=shells.end())full=staged->second;
        else {observe("current-shell-unavailable",c);if(!currentShell||!currentShell(c,full))return nullptr;}
        observe("shell-shape-clock-or-bound",c);
        if(!M::workAvailable(available)||full.size()<4||full.size()>64||viewFaces+full.size()>maxFaces)return nullptr;
        viewFaces+=full.size();const auto& faces=view.emplace(c,std::move(full)).first->second;Body body;
        std::vector<vector> areas,rawAreas;std::vector<point> centres,rawCentres;
        for(const auto& face:faces)
        {
            // Same one clock predicate, then incidence, then coupled short circuit.
            // Copy the evaluated operand before any later observer clock call.
            auto failedFace=[&](const char* why)
            {
                observe(why,c,face.id);
                if(observation)
                {
                    observation->failedFaceKnown=true;
                    observation->faceOwner=face.owner;observation->faceNeighbour=face.neighbour;
                    observation->facePatch=face.patch;observation->faceZone=face.zone;
                    if(availableObservation)observation->failedFaceWork=*availableObservation;
                    if(face.id>=0&&std::size_t(face.id)<m.faceAlive.size()
                        &&std::size_t(face.id)<m.owner.size()&&std::size_t(face.id)<m.neighbour.size())
                    {
                        observation->nativeFaceKnown=true;observation->nativeFaceAlive=m.faceAlive[face.id];
                        observation->nativeFaceOwner=m.owner[face.id];observation->nativeFaceNeighbour=m.neighbour[face.id];
                    }
                }
            };
            if(!M::workAvailable(available)){failedFace("face-clock");return nullptr;}
            if(face.owner!=c&&face.neighbour!=c){failedFace("face-incidence");return nullptr;}
            if(coupled(types,face.patch)){failedFace("face-coupled");return nullptr;}
            observe("exact-planar-convex-outline",c,face.id);
            Q exactArea;const bool exactConvex=geometry.planar(face.ring,exactArea);
            if(!exactConvex&&!geometry.simplePlanar(face.ring,exactArea))return nullptr;
            observe("native-classifier-known-finite-area",c,face.id);
            const NativeFacePlan native=planner(face);
            if(!native.known||!M::finitePoint(native.area)||!M::finitePoint(native.centre)||mag(native.area)<=VSMALL)return nullptr;
            // A nonconvex original face is supported only through its actual
            // production split plan, never as an unmarked retained base.
            observe("simple-concave-real-split-required",c,face.id);
            if(!exactConvex&&(!native.split||native.retained))return nullptr;
            body.marked=body.marked||native.split;body.faces.push_back(native);
            areas.push_back(face.owner==c?native.area:-native.area);centres.push_back(native.centre);
            vector area;point centre;faceOF(face.ring,area,centre);
            rawAreas.push_back(face.owner==c?area:-area);rawCentres.push_back(centre);
        }
        // Full literal closure is required, including unchanged outside faces.
        observe("full-closed-positive-exact-moment",c);
        Moment moment;if(!geometry.moments(faces,c,moment)||moment.volume<=0)return nullptr;
        if(!volumeCentre(areas,centres,body.apex,c,-1)||!volumeCentre(rawAreas,rawCentres,body.rawCentre,c,-1))return nullptr;
        // Literal production initial planConeParent mark condition on this view.
        for(std::size_t f=0;f<faces.size();++f)
        {
            const vector normal=areas[f]/max(mag(areas[f]),VSMALL);
            for(std::size_t other=0;other<faces.size();++other)if(other!=f)
            {
                observe("initial-mark-clock",c);
                if(!M::workAvailable(available))return nullptr;
                const vector d=centres[other]-centres[f];
                if((d&normal)/max(mag(d),VSMALL)>-1e-6)body.marked=true;
            }
        }
        // The real kernel search can change a marked apex at this margin.
        // Strict full-shell interiority proves the initial apex stays fixed.
        if(body.marked)for(std::size_t f=0;f<faces.size();++f)
        {
            observe("apex-margin-clock",c,faces[f].id);
            if(!M::workAvailable(available))return nullptr;
            const scalar margin=(areas[f]&(centres[f]-body.apex))/max(mag(areas[f]),VSMALL);
            observe("marked-apex-production-3um",c,faces[f].id);
            if(!std::isfinite(margin)||margin<=3e-6)return nullptr;
        }
        return &bodies.emplace(c,std::move(body)).first->second;
    };
    Body* hostBody=classifyBody(host);if(!hostBody)return false;
    observe("host-initial-marked",host);if(hostBody->marked)return false;
    const auto& hostFaces=view.at(host);
    const auto metric=M::measure(host,hostFaces,m.points,faceOF,
        [&](label patch){return coupled(types,patch);},available,true);
    observe("host-full-stock-D-AR-C",host);
    if(!metric.finite||!metric.strictGeometry||!M::newHost(metric))return false;
    auto endpointCentre=[&](const M::Face& face,label parent,point& centre)->bool
    {
        Body* body=classifyBody(parent);if(!body)return false;
        const auto& faces=view.at(parent);
        const auto base=std::find_if(faces.begin(),faces.end(),[&](const M::Face& f){return f.id==face.id;});
        observe("endpoint-base-identity",parent,face.id);
        if(base==faces.end()||base->owner!=face.owner||base->neighbour!=face.neighbour
          ||base->patch!=face.patch||base->zone!=face.zone||base->ring!=face.ring)return false;
        if(!body->marked){centre=body->rawCentre;observe("unmarked-endpoint-clock",parent,face.id);return M::workAvailable(available);}
        observe("marked-endpoint-local-OF-or-point-bound",parent,face.id);
        if(!localFaceOF||m.points.size()>=std::size_t(std::numeric_limits<label>::max()))return false;
        const auto& ring=face.ring;std::vector<label> adjacent(ring.size(),-1);
        std::vector<bool> collinear(ring.size(),false);
        for(std::size_t j=0;j<ring.size();++j)
        {
            observe("marked-endpoint-clock",parent,face.id);
            if(!M::workAvailable(available))return false;
            const label a=ring[j],b=ring[(j+1)%ring.size()];label uses=0;
            for(const auto& other:faces)if(other.id!=face.id)
                for(std::size_t k=0;k<other.ring.size();++k)
                {
                    const label u=other.ring[k],v=other.ring[(k+1)%other.ring.size()];
                    if(std::minmax(a,b)!=std::minmax(u,v))continue;
                    const bool same=(a==u&&b==v);
                    observe("marked-side-directed-edge",parent,face.id);
                    if(same==((face.owner==parent)==(other.owner==parent)))return false;
                    ++uses;adjacent[j]=other.id;
                }
            observe("marked-side-one-adjacent",parent,face.id);
            if(uses!=1)return false;
            const Q left=X::sub(geometry.at(ring[(j+ring.size()-1)%ring.size()]),geometry.at(a));
            const Q right=X::sub(geometry.at(b),geometry.at(a));
            collinear[j]=!X::nonzero(X::cross(left,right))&&X::dot(left,right)<0;
        }
        // Actual native adjacent face identity, never same-plane role grouping.
        for(std::size_t j=0;j<ring.size();++j)if(collinear[j])
        {
            const label before=adjacent[(j+ring.size()-1)%ring.size()],after=adjacent[j];
            observe("collinear-same-native-adjacent",parent,face.id);
            if(before!=after)return false;
            const auto side=std::find_if(faces.begin(),faces.end(),[&](const M::Face& f){return f.id==before;});
            observe("collinear-side-present",parent,face.id);
            if(side==faces.end())return false;
            observe("collinear-side-known-unsplit",parent,side->id);
            const auto native=planner(*side);if(!native.known||native.split)return false;
        }
        const label ap=m.points.size();std::vector<dcCartConformingConeSides::Side> sides;sides.reserve(ring.size());
        for(std::size_t j=0;j<ring.size();++j)
        {
            label a=ring[j],b=ring[(j+1)%ring.size()];if(face.owner!=parent)std::swap(a,b);
            sides.push_back({face.id,adjacent[j],{b,a,ap}});
        }
        const auto plan=dcCartConformingConeSides::build(sides,[&](label p)
        {const point& q=p==ap?body->apex:m.points.at(p);return std::array<double,3>{{q.x(),q.y(),q.z()}};},available);
        observe("production-conforming-side-plan",parent,face.id);
        if(!plan.complete)return false;
        std::vector<vector> av;std::vector<point> fc;vector area;point centreFace;
        faceOF(ring,area,centreFace);av.push_back(face.owner==parent?area:-area);fc.push_back(centreFace);
        for(const auto& side:plan.sides)
        {
            observe("marked-endpoint-clock",parent,face.id);
            if(!M::workAvailable(available))return false;
            std::vector<point> points;points.reserve(side.ring.size());
            for(label p:side.ring)points.push_back(p==ap?body->apex:m.points.at(p));
            localFaceOF(points,area,centreFace);av.push_back(side.owner==face.id?area:-area);fc.push_back(centreFace);
        }
        // Literal installed-centre child C gate, before the dual-end stock tets.
        for(std::size_t j=0;j<fc.size();++j)for(std::size_t k=0;k<fc.size();++k)if(j!=k)
        {
            observe("marked-endpoint-clock",parent,face.id);
            if(!M::workAvailable(available))return false;
            vector normal=av[j]/max(mag(av[j]),VSMALL);
            vector direction=fc[k]-fc[j];direction/=max(mag(direction),VSMALL);
            const scalar cosine=normal&direction;
            observe("marked-child-face-centre-concavity",parent,face.id);
            if(!std::isfinite(cosine)||cosine>-1e-6)return false;
        }
        return volumeCentre(av,fc,centre,parent,face.id);
    };
    // Finite retained-incidence closure of this actual prospective view. If
    // every retained base passes with fixed initial endpoints, the production
    // resolver has no split/mark event to propagate. Ordinary unsplit faces
    // need no extra neighbour traversal. No source/permission is inferred here.
    std::deque<label> queue{host};std::set<label> scanned;
    std::map<label,const M::Face*> retained;
    while(!queue.empty())
    {
        observe("retained-closure-clock");
        if(!M::workAvailable(available))return false;
        const label parent=queue.front();queue.pop_front();
        if(!scanned.insert(parent).second)continue;
        Body* body=classifyBody(parent);if(!body)return false;
        const auto& faces=view.at(parent);
        for(std::size_t f=0;f<faces.size();++f)if(body->faces[f].retained)
        {
            const auto& face=faces[f];const auto other=retained.find(face.id);
            if(other!=retained.end())
            {
                const auto& saved=*other->second;
                observe("retained-incidence-identity",parent,face.id);
                if(saved.owner!=face.owner||saved.neighbour!=face.neighbour
                  ||saved.patch!=face.patch||saved.zone!=face.zone||saved.ring!=face.ring)return false;
            }
            else retained.emplace(face.id,&face);
            for(label end:{face.owner,face.neighbour})if(end>=0&&!scanned.count(end))
            {
                if(!classifyBody(end))return false;
                queue.push_back(end);
            }
        }
    }
    for(const auto& entry:retained)
    {
        observe("retained-closure-clock");
        if(!M::workAvailable(available))return false;
        const auto& face=*entry.second;point own,nei;
        if(!endpointCentre(face,face.owner,own)||(face.neighbour>=0&&!endpointCentre(face,face.neighbour,nei)))return false;
        vector area;point centre;faceOF(face.ring,area,centre);
        std::vector<point> points;points.reserve(face.ring.size());for(label p:face.ring)points.push_back(m.points[p]);
        observe("retained-double-end-stock",face.owner,face.id);
        if(!dcCartRetainedPlanarBaseStockTets::accept(points,centre,own,face.neighbour>=0?&nei:nullptr,available))return false;
    }
    observe("final-clock",host);
    if(!M::workAvailable(available))return false;
    M::ConeDebt actual;actual.complete=true;actual.cones=1;
    actual.minD=metric.determinant;actual.maxAspect=metric.aspect;
    actual.badD=M::passes(metric.determinant,0)?0:1;
    actual.badAspect=std::isfinite(metric.aspect)&&metric.aspect<=1000?0:1;
    out=std::move(actual);observe("pass",host);if(observation)observation->accepted=true;return true;
}

template<class Mesh>
void repair
(
    Mesh& m,const wordList& types,const std::vector<std::string>& sources,
    label rank,const M::FaceOF& faceOF,const M::Available& available,
    bool homogeneousFluid,Report& report,
    const NativeFacePlanner& nativeFacePlanner=NativeFacePlanner(),
    const NativeLocalFaceOF& nativeLocalFaceOF=NativeLocalFaceOF()
)
{
    auto refusal=[&](const char* why)
    {
        const char* reason=why?why:"unknown";++report.refused[reason];
        if(report.nFirst<report.first.size())
        {
            auto& f=report.first[report.nFirst++];
            f.seed=report.currentSeed;f.members=report.currentMembers;f.reason=reason;
        }
    };
    if(!homogeneousFluid){refusal("global-fluid-source");return;}
    if(!M::workAvailable(available)){refusal("source-clock");return;}
    const std::size_t nc=m.nCells,nf=m.faces.size();
    if(m.nCells<0||m.cellAlive.size()!=nc||m.cellRank.size()!=nc
      ||m.cellLevel.size()!=nc||m.cellLeaf.size()!=nc||m.cellPiece.size()!=nc
      ||m.cellOrigAlive.size()!=nc||m.cellRefVol.size()!=nc||m.cellRefCentre.size()!=nc
      ||m.owner.size()!=nf||m.neighbour.size()!=nf||m.patch.size()!=nf
      ||m.zone.size()!=nf||m.faceAlive.size()!=nf||sources.size()!=std::size_t(types.size()))
    {refusal("native-shape");return;}
    std::vector<std::vector<label>> cf(nc);
    for(std::size_t i=0;i<nf;++i)
    {
        if((i&1023)==0&&!M::workAvailable(available)){refusal("source-clock");return;}
        if(!m.faceAlive[i])continue;
        const label own=m.owner[i],nei=m.neighbour[i];
        if(own<0||std::size_t(own)>=nc||!m.cellAlive[own]||nei<-1
          ||(nei>=0&&(std::size_t(nei)>=nc||!m.cellAlive[nei]||own==nei)))
        {refusal("native-incidence");return;}
        cf[own].push_back(label(i));if(nei>=0)cf[nei].push_back(label(i));
    }
    // Literal production selectedSf predicate, distinct from scope protection.
    auto coupledPatch=[&](label p)
    {
        if(p<0||p>=types.size())return false;
        const std::string t(types[p].c_str());
        return t.rfind("cyclic",0)==0||t.rfind("processor",0)==0
            ||t.rfind("mapped",0)==0||t.find("AMI")!=std::string::npos;
    };
    std::map<Ring,std::pair<vector,point>> faceMetrics;
    const M::FaceOF cachedFaceOF=[&](const Ring& ring,vector& sf,point& fc)
    {
        auto it=faceMetrics.find(ring);
        if(it==faceMetrics.end())
        {
            vector area;point centre;faceOF(ring,area,centre);
            it=faceMetrics.emplace(ring,std::make_pair(area,centre)).first;
        }
        sf=it->second.first;fc=it->second.second;
    };
    auto fullShell=[&](label c)->std::vector<M::Face>
    {
        std::vector<M::Face> out;out.reserve(cf[c].size());
        for(label f:cf[c])out.emplace_back(f,m.owner[f],m.neighbour[f],m.patch[f],m.zone[f],m.faces[f]);
        return out;
    };
    std::map<label,M::Cell> current;
    auto currentCell=[&](label c)->const M::Cell&
    {
        auto it=current.find(c);
        if(it==current.end())it=current.emplace(c,M::measure(c,fullShell(c),m.points,cachedFaceOF,coupledPatch,available,false)).first;
        return it->second;
    };
    auto quality=[&](const M::Face& f,const std::map<label,M::Cell>& next)->M::FacetQuality
    {
        auto at=[&](label c)->const M::Cell&
        {const auto it=next.find(c);return it==next.end()?currentCell(c):it->second;};
        const auto& own=at(f.owner);const M::Cell* nei=f.neighbour>=0?&at(f.neighbour):nullptr;
        return M::facet(f,m.points,cachedFaceOF,own,nei,available);
    };
    const std::map<label,M::Cell> unchanged;
    Geometry geometry(m.points,available);
    std::map<label,Moment> currentMoments;
    auto originalMoment=[&](label c,Moment& value)
    {
        auto it=currentMoments.find(c);
        if(it==currentMoments.end())
        {
            Moment actual;if(!geometry.moments(fullShell(c),c,actual))return false;
            it=currentMoments.emplace(c,std::move(actual)).first;
        }
        value=it->second;return true;
    };
    const bool vertexFan=std::getenv("DCCART_CONES_PREFER_VERTEX_FAN")!=nullptr;
    auto coneDebt=[&](const M::Cell& body,const std::vector<M::Face>& shell)->M::ConeDebt
    {
        M::ConeDebt out;
        if(!body.finite||shell.size()!=body.areas.size()
          ||shell.size()!=body.centres.size()||shell.size()!=body.faceIds.size())return out;
        for(std::size_t fi=0;fi<shell.size();++fi)
        {
            if(!M::workAvailable(available))return M::ConeDebt{};
            const auto& face=shell[fi];
            if(body.faceIds[fi]!=face.id)return M::ConeDebt{};
            Ring ring=face.ring;
            if(face.owner!=body.id)std::reverse(ring.begin(),ring.end());
            const vector area=body.areas[fi],normal=area/mag(area);
            const bool selectedBase=face.patch<0||coupledPatch(face.patch);
            auto debt=[&](const point& a,const point& b,const point& d)->bool
            {
                const auto q=M::triangularConeQuality(a,b,d,body.centre,selectedBase,available);
                if(!q.available)return false;
                M::addConeDebt(out,q);return true;
            };
            if(ring.size()==3)
            {if(!debt(m.points[ring[0]],m.points[ring[1]],m.points[ring[2]]))return M::ConeDebt{};continue;}
            bool positiveFan=false;
            if(vertexFan)for(std::size_t k=0;k<ring.size();++k)
            {
                bool positive=true;
                for(std::size_t j=1;j+1<ring.size();++j)
                {
                    const point& a=m.points[ring[k]];
                    const point& b=m.points[ring[(k+j)%ring.size()]];
                    const point& d=m.points[ring[(k+j+1)%ring.size()]];
                    const vector av=((b-a)^(d-a))*.5;
                    if(!M::finitePoint(av)||mag(av)<=1e-12
                        ||(av&normal)<=1e-15*mag(area)){positive=false;break;}
                }
                if(!positive)continue;
                positiveFan=true;
                for(std::size_t j=1;j+1<ring.size();++j)
                    if(!debt(m.points[ring[k]],m.points[ring[(k+j)%ring.size()]],
                        m.points[ring[(k+j+1)%ring.size()]]))return M::ConeDebt{};
                break; // Same first positive original-vertex fan recipe.
            }
            if(!positiveFan)for(std::size_t j=0;j<ring.size();++j)
                if(!debt(m.points[ring[j]],m.points[ring[(j+1)%ring.size()]],
                    body.centres[fi]))return M::ConeDebt{};
        }
        out.complete=true;return out;
    };
    std::map<label,M::ConeDebt> currentConeDebt;
    auto futureCell=[&](label c)->M::ConeDebt
    {
        const auto found=currentConeDebt.find(c);
        if(found!=currentConeDebt.end())return found->second;
        const auto value=coneDebt(currentCell(c),fullShell(c));
        if(value.complete)currentConeDebt.emplace(c,value);
        return value;
    };
    auto hasDebt=[&](label c)->bool
    {
        if(c<0||std::size_t(c)>=nc||!m.cellAlive[c]||m.cellRank[c]!=rank
          ||cf[c].size()<4||cf[c].size()>64)return false;
        for(label f:cf[c])if(m.neighbour[f]<0&&coupled(types,m.patch[f]))return false;
        // Existing sparse boundary/non-six-face scope remains literal.
        bool boundary=false;
        for(label f:cf[c])boundary=boundary||m.neighbour[f]<0;
        if(!boundary&&cf[c].size()==6)return false;
        // Raw stock D/AR alone is not the debt this cone repair is addressing.
        // No raw tessellation/strict-C failure seeds work.
        const auto value=futureCell(c);
        return value.complete&&value.bad();
    };
    enum class ProposalRole : unsigned char {FutureCone,NativeFacetRepair};
    // Facet seeds use live normal m geometry only. They grant no source or
    // publication authority; exact stage and every existing quality gate remain.
    auto localFacetCell=[&](label c)->bool
    {
        if(c<0||std::size_t(c)>=nc||!m.cellAlive[c]||m.cellRank[c]!=rank
          ||cf[c].size()<4||cf[c].size()>64)return false;
        for(label f:cf[c])if(m.neighbour[f]<0&&coupled(types,m.patch[f]))return false;
        return true;
    };
    auto rawFacetPeer=[&](label c,label& seedFace,M::FacetQuality& seedQuality)->label
    {
        if(!localFacetCell(c))return -1;
        for(label f:cf[c])
        {
            if(!M::workAvailable(available))return -1;
            if(!m.faceAlive[f]||m.patch[f]!=-1||m.zone[f]!=-1||m.neighbour[f]<0)continue;
            const label other=m.owner[f]==c?m.neighbour[f]:m.owner[f];
            if(!localFacetCell(other))continue;
            const M::Face face(f,m.owner[f],m.neighbour[f],m.patch[f],m.zone[f],m.faces[f]);
            const auto value=quality(face,unchanged);
            if(value.numeric&&(!M::passes(value.v[1],1)||!M::passes(value.v[2],2)))
            {seedFace=f;seedQuality=value;return other;}
        }
        return -1;
    };
    // The original cone family runs first, in its literal native-slot order.
    // Remaining clock may propose real local W/R interfaces in a second pass.
    std::size_t scannedSeeds=0,debtSeeds=0;
    const bool traceNativeFacet=std::getenv("DCCART_CONE_EVIDENCE")!=nullptr;
    unsigned nativeFacetCaptures=0;
    for(unsigned family=0;family<2&&M::workAvailable(available);++family)
    for(label seed=0;seed<m.nCells;++seed)
    {
        if(!M::workAvailable(available)){refusal("source-clock");break;}
        ++scannedSeeds;
        const ProposalRole role=family==0?ProposalRole::FutureCone:ProposalRole::NativeFacetRepair;
        const bool coneSeed=hasDebt(seed);label facetPeer=-1,facetSeedFace=-1;M::FacetQuality facetSeedQuality;
        if(family==0){if(!coneSeed)continue;}
        else{if(coneSeed||(facetPeer=rawFacetPeer(seed,facetSeedFace,facetSeedQuality))<0)continue;}
        ++debtSeeds;
        report.currentSeed=seed;
        std::vector<label> members{seed};std::set<label> tried;
        bool nativeAlternativePending=role==ProposalRole::NativeFacetRepair;
        NativeDebtObservation nativeObservation;
        auto captureFailure=[&](const char* stageName,const char* why,const Plan* plan=nullptr,const M::Cell* host=nullptr) noexcept
        {
            if(!traceNativeFacet||role!=ProposalRole::NativeFacetRepair||nativeFacetCaptures>=8)return;
            const unsigned record=nativeFacetCaptures++;
            captureNativeFacetFailure(m,cf,types,sources,current,currentMoments,rank,record,
                seed,facetSeedFace,facetSeedQuality,members,stageName,why,plan,host,available,&nativeObservation);
        };
        auto grow=[&](label preferred=-1)
        {
            if(!M::workAvailable(available)||members.size()>=9)return false;
            // One alternative only after the actual initial WR pair refused.
            // A fresh ordinary seed neighbour uses the existing interface-area
            // order; no UID, saved observer or new time allowance chooses it.
            if(nativeAlternativePending&&members.size()==2&&members[1]==facetPeer)
            {
                nativeAlternativePending=false;
                std::map<label,scalar> alternatives;
                for(label f:cf[seed])
                {
                    if(!M::workAvailable(available))return false;
                    if(!m.faceAlive[f]||m.patch[f]!=-1||m.zone[f]!=-1||m.neighbour[f]<0)continue;
                    const label other=m.owner[f]==seed?m.neighbour[f]:m.owner[f];
                    if(other==facetPeer||tried.count(other)||!localFacetCell(other))continue;
                    vector a;point fc;cachedFaceOF(m.faces[f],a,fc);
                    if(M::finitePoint(a)&&mag(a)>0)alternatives[other]+=mag(a);
                }
                label other=-1;scalar area=-1;
                for(const auto& candidate:alternatives)if(candidate.second>area)
                {other=candidate.first;area=candidate.second;}
                if(other>=0)
                {
                    members.resize(1);tried.erase(facetPeer);
                    tried.insert(other);members.push_back(other);return true;
                }
            }

            const std::set<label> selected(members.begin(),members.end());
            std::map<label,scalar> candidates;
            for(label c:members)for(label f:cf[c])
            {
                if(m.patch[f]!=-1||m.zone[f]!=-1||m.neighbour[f]<0)continue;
                const label other=m.owner[f]==c?m.neighbour[f]:m.owner[f];
                if(other<0||other>=m.nCells||!m.cellAlive[other]||m.cellRank[other]!=rank
                  ||selected.count(other)||tried.count(other)||cf[other].size()<4||cf[other].size()>64)continue;
                vector a;point fc;cachedFaceOF(m.faces[f],a,fc);
                if(M::finitePoint(a)&&mag(a)>0)candidates[other]+=mag(a);
            }
            label best=-1;scalar area=-1;
            // Only prefer an already eligible ordinary neighbour from the actual
            // blocking staged face. Otherwise retain the literal largest-area rule.
            if(preferred>=0&&candidates.count(preferred))best=preferred;
            else for(const auto& x:candidates)if(x.second>area){best=x.first;area=x.second;}
            if(best<0)return false;tried.insert(best);members.push_back(best);return true;
        };
        // A W/R proposal starts from the actual failing interface's two ends.
        // grow still verifies ordinary local adjacency and the original cap.
        if(family==1&&!grow(facetPeer))continue;
        // The existing native merge bounds at eight absorbed cells are retained.
        while(members.size()<=9&&M::workAvailable(available))
        {
            nativeObservation=NativeDebtObservation{};
            report.currentMembers=members.size();++report.tested;Plan plan;
            bool ready=false;
            try
            {
                bool allMoments=true;Moment value;
                for(label c:members)allMoments=originalMoment(c,value)&&allMoments;
                ready=allMoments&&stage(m,cf,members,seed,types,sources,rank,geometry,currentMoments,plan,
                    role==ProposalRole::NativeFacetRepair);
                if(!allMoments)plan.reason="original-closed-moment";
            }
            catch(const std::exception&){captureFailure("stage-exception","exact-or-allocation");refusal("exact-or-allocation");break;}
            if(!ready)
            {
                captureFailure("exact-source-stage",plan.reason,&plan);
                refusal(plan.reason);
                // Adding neighbours cannot repair this unchanged member shell.
                if(plan.reason&&(!std::strcmp(plan.reason,"original-closed-moment")
                  ||!std::strcmp(plan.reason,"clock")))break;
                // Unsupported intermediate outlines may become legal when
                // their actual neighbour is included. No rejected draft publishes.
                if(!grow())break;
                continue;
            }
            std::set<label> selected(members.begin(),members.end());
            std::map<label,Row*> byId;std::map<label,label> destination;
            std::map<label,std::vector<label>> provenance;
            for(auto& row:plan.rows)
            {
                byId[row.face.id]=&row;provenance[row.face.id]=row.originals;
                for(label f:row.originals)destination[f]=row.face.id;
            }
            std::set<label> retired(plan.retired.begin(),plan.retired.end());
            std::map<label,std::vector<label>> nextCf;
            std::map<label,std::vector<M::Face>> shells;
            for(label c:plan.affected)if(c==seed||!selected.count(c))
            {
                std::set<label> faces;
                if(c!=seed)for(label f:cf[c])
                    if(!retired.count(f)&&!destination.count(f))faces.insert(f);
                for(const auto& row:plan.rows)
                    if(row.face.owner==c||row.face.neighbour==c)faces.insert(row.face.id);
                auto& ids=nextCf[c];ids.assign(faces.begin(),faces.end());
                auto& shell=shells[c];shell.reserve(ids.size());
                for(label f:ids)
                {
                    const auto it=byId.find(f);
                    if(it==byId.end())shell.emplace_back(f,m.owner[f],m.neighbour[f],m.patch[f],m.zone[f],m.faces[f]);
                    else shell.push_back(it->second->face);
                }
            }
            std::map<label,M::Cell> next;
            const M::PartitionOnly partitionOnly=[&](const M::Face& a,const M::Face& b)
            {
                // Only rows from this complete staged union may use this raw
                // partition proof. Endpoints/metadata remain distinct/literal.
                if(!byId.count(a.id)||!byId.count(b.id))return false;
                const bool plain=a.neighbour>=0&&b.neighbour>=0
                    &&a.patch==-1&&b.patch==-1&&a.zone==-1&&b.zone==-1;
                const bool physical=a.neighbour<0&&b.neighbour<0
                    &&a.patch==b.patch&&a.zone==b.zone&&a.patch>=0
                    &&std::size_t(a.patch)<sources.size()&&!sources[a.patch].empty()
                    &&!coupled(types,a.patch);
                if(!plain&&!physical)return false;
                const Q aa=geometry.area(a.ring),bb=geometry.area(b.ring);
                if(X::dot(aa,bb)<=0)return false;
                const Q n=geometry.primitiveNormal(a.ring,aa);
                return n==geometry.primitiveNormal(b.ring,bb)
                    &&geometry.planeBound(a.ring,n)==geometry.planeBound(b.ring,n);
            };
            const auto host=M::measure(seed,shells.at(seed),m.points,cachedFaceOF,coupledPatch,
                available,true,true,partitionOnly);
            bool accept=M::newHost(host);const char* why=accept?nullptr:(host.reason?host.reason:"host-D");
            if(!accept)
            {
                // A refused host cannot publish. Avoid exact outside moments and
                // stock-role work for this doomed draft; successful drafts still
                // execute every unchanged source, V/M, outside and facet gate.
                label preferred=-1;
                if(host.blockingFace>=0)
                {
                    const auto blocking=byId.find(host.blockingFace);
                    if(blocking!=byId.end())preferred=blocking->second->face.neighbour;
                }
                captureFailure("host-stock",why,&plan,&host);
                refusal(why);
                if(!M::workAvailable(available)||members.size()==9||!grow(preferred))break;
                continue;
            }
            // The old cone role still requires strict triangular improvement.
            // Native facet proposals must preserve that complete cone quality;
            // their separate full affected W/R improvement is checked below.
            M::ConeDebt beforeCones;beforeCones.complete=true;
            for(label c:members)M::addConeDebt(beforeCones,futureCell(c));
            auto afterCones=coneDebt(host,shells.at(seed));
            const bool nativeFallbackRequired=role==ProposalRole::NativeFacetRepair
                &&!M::inheritsConeDebt(beforeCones,afterCones,false);
            nativeObservation.eligible=nativeFallbackRequired;
            if(nativeFallbackRequired)
            {
                M::ConeDebt actual;++report.nativeDebtCalls;
                if(unsplitNativeDebt(m,shells,seed,types,geometry,cachedFaceOF,available,
                    nativeFacePlanner,actual,nativeLocalFaceOF,[&](label c,std::vector<M::Face>& out)
                    {
                        if(!M::workAvailable(available)||c<0||c>=m.nCells||!m.cellAlive[c]
                          ||(selected.count(c)&&c!=seed)||cf[c].size()<4||cf[c].size()>64)return false;
                        // Changed endpoints are all in plan.affected and use the
                        // staged shells above; only an untouched current shell
                        // may come from this real live incidence callback.
                        if(plan.affected.count(c))return false;
                        out.clear();out.reserve(cf[c].size());
                        for(label f:cf[c])
                        {
                            if(!M::workAvailable(available)||!m.faceAlive[f])return false;
                            out.emplace_back(f,m.owner[f],m.neighbour[f],m.patch[f],m.zone[f],m.faces[f]);
                        }
                        return M::workAvailable(available);
                    },&nativeObservation,&report.lastAvailableObservation))
                {afterCones=std::move(actual);++report.nativeDebtAccepted;}
                else report.nativeDebtRefusal(nativeObservation.reason);
            }
            const bool coneInherited=M::inheritsConeDebt(beforeCones,afterCones,role==ProposalRole::FutureCone);
            nativeObservation.coneInherited=coneInherited;
            if(!coneInherited)
            {
                captureFailure("future-cone-inheritance","future-cone-no-improvement",&plan,&host);
                refusal("future-cone-no-improvement");
                if(!M::workAvailable(available)||members.size()==9||!grow())break;
                continue;
            }
            if(accept)
            {
                // Same sorted staged rows already passed full closure/exact V/M.
                const Moment& trueHost=plan.exactHost;
                if(!exactCentroidMargin(geometry,shells.at(seed),seed,trueHost))
                {if(accept)why="true-centroid-3um";accept=false;}
            }
            next.emplace(seed,host);
            for(label c:members)
            {
                const auto& before=currentCell(c);
                if(!before.finite||!M::inheritsNumeric(before.determinant,host.determinant,0)
                  ||!M::inheritsAspect(before.aspect,host.aspect))
                {if(accept)why="host-inherited-D-AR";accept=false;}
            }
            // Preserve the existing numeric OF CC/V conservation gate as well
            // as the independent exact physical integral above. OF CC is not
            // substituted for the physical first moment.
            scalar oldVolume=0,span=1e-6;vector oldOffset(vector::zero);
            const point reference=currentCell(seed).centre;
            for(label c:members)
            {
                const auto& before=currentCell(c);
                oldVolume+=before.volume;
                oldOffset+=before.volume*(before.centre-reference);
            }
            for(const auto& row:plan.rows)for(label p:row.face.ring)
                span=max(span,mag(m.points[p]-reference));
            const scalar vr=mag(host.volume-oldVolume)/max(oldVolume,VSMALL);
            const scalar mr=mag(host.volume*(host.centre-reference)-oldOffset)/max(oldVolume*span,VSMALL);
            if(!std::isfinite(vr)||!std::isfinite(mr)||vr>1e-10||mr>1e-10)
            {if(accept)why="literal-OF-volume-centre-moment";accept=false;}
            for(label c:plan.affected)if(!selected.count(c))
            {
                const auto after=M::measure(c,shells.at(c),m.points,cachedFaceOF,coupledPatch,available,false);
                const auto& before=currentCell(c);
                Moment oldTrue,newTrue;
                if(!originalMoment(c,oldTrue)
                  ||!geometry.moments(shells.at(c),c,newTrue)||!same(oldTrue,newTrue)
                  ||!outsideGeometry(before,after,provenance)
                  ||!M::inheritsNumeric(before.determinant,after.determinant,0)
                  ||!M::inheritsAspect(before.aspect,after.aspect)
                  ||!M::inheritsConeDebt(futureCell(c),coneDebt(after,shells.at(c)),false))
                {if(accept)why="outside-inheritance";accept=false;}
                next.emplace(c,after);
            }
            std::set<label> checkFaces;
            for(label c:plan.affected)for(label f:cf[c])if(!retired.count(f)||destination.count(f))checkFaces.insert(f);
            for(label f:checkFaces)
            {
                const M::Face old(f,m.owner[f],m.neighbour[f],m.patch[f],m.zone[f],m.faces[f]);
                const label dst=destination.count(f)?destination.at(f):f;
                const auto it=byId.find(dst);
                const M::Face after=it==byId.end()?M::Face(dst,m.owner[dst],m.neighbour[dst],m.patch[dst],m.zone[dst],m.faces[dst]):it->second->face;
                const auto& bo=currentCell(old.owner);
                const M::Cell* bn=old.neighbour>=0?&currentCell(old.neighbour):nullptr;
                auto at=[&](label c)->const M::Cell&
                {const auto k=next.find(c);return k==next.end()?currentCell(c):k->second;};
                const auto& ao=at(after.owner);const M::Cell* an=after.neighbour>=0?&at(after.neighbour):nullptr;
                const bool changed=dst!=f||after.ring!=old.ring||after.owner==seed||after.neighbour==seed
                    ||!bits(bo.centre,ao.centre)||!bits(bo.volume,ao.volume)
                    ||bool(bn)!=bool(an)||(bn&&an&&(!bits(bn->centre,an->centre)||!bits(bn->volume,an->volume)));
                if(coupled(types,old.patch))
                {
                    if(changed||after.ring!=old.ring||after.patch!=old.patch||after.zone!=old.zone)
                    {if(accept)why="changed-coupled";accept=false;}
                    continue;
                }
                const auto bq=quality(old,unchanged),aq=quality(after,next);
                if(!M::inheritsFacet(bq,aq,changed)){if(accept)why="facet-stock-or-role";accept=false;}
            }
            if(accept&&role==ProposalRole::NativeFacetRepair)
            {
                // Complete before/after interface debt, including legally
                // retired selected/selected faces and every outside-cell face.
                // The unchanged full source, exact V/M and Stock gates above
                // must already pass; retirement alone grants no authority.
                std::array<std::size_t,2> oldBad{{0,0}},newBad{{0,0}};
                std::array<scalar,2> oldDebt{{0,0}},newDebt{{0,0}};
                auto addFacetDebt=[&](const M::FacetQuality& q,
                    std::array<std::size_t,2>& bad,std::array<scalar,2>& debt)->bool
                {
                    if(!q.numeric)return false;
                    for(std::size_t k=0;k<2;++k)if(!M::passes(q.v[k+1],k+1))
                    {
                        ++bad[k];const scalar threshold=M::thresholds()[k+1];
                        debt[k]+=(threshold-q.v[k+1])/threshold;
                        if(!std::isfinite(debt[k]))return false;
                    }
                    return true;
                };
                bool complete=true;std::set<label> allOld,newIds;
                for(label c:plan.affected)for(label f:cf[c])allOld.insert(f);
                for(label f:allOld)
                {
                    if(!M::workAvailable(available)){complete=false;break;}
                    const M::Face old(f,m.owner[f],m.neighbour[f],m.patch[f],m.zone[f],m.faces[f]);
                    // No physical-boundary surrogate for an unknown real peer.
                    if(coupled(types,old.patch)
                      ||!addFacetDebt(quality(old,unchanged),oldBad,oldDebt))
                    {complete=false;break;}
                    if(retired.count(f)&&!destination.count(f))
                    {
                        if(old.patch!=-1||old.zone!=-1||old.neighbour<0
                          ||!selected.count(old.owner)||!selected.count(old.neighbour))
                        {complete=false;break;}
                        continue;
                    }
                    const label dst=destination.count(f)?destination.at(f):f;
                    if(!newIds.insert(dst).second)continue;
                    const auto row=byId.find(dst);
                    const M::Face after=row==byId.end()
                        ?M::Face(dst,m.owner[dst],m.neighbour[dst],m.patch[dst],m.zone[dst],m.faces[dst])
                        :row->second->face;
                    if(coupled(types,after.patch)
                      ||!addFacetDebt(quality(after,next),newBad,newDebt))
                    {complete=false;break;}
                }
                bool improved=false;
                for(std::size_t k=0;k<2;++k)
                {
                    complete=complete&&newBad[k]<=oldBad[k]&&newDebt[k]<=oldDebt[k];
                    improved=improved||newBad[k]<oldBad[k]||newDebt[k]<oldDebt[k];
                }
                if(!complete||!improved)
                {why=complete?"native-facet-no-improvement":"native-facet-incomplete-or-regression";accept=false;}
            }
            if(accept&&M::workAvailable(available))
            {
                // Every allocation and callback above belongs to the disposable plan.
                // Publish contains only native-vector swaps and scalar/bit writes.
                for(auto& row:plan.rows)
                {
                    const label f=row.face.id;m.faces[f].swap(row.face.ring);
                    m.owner[f]=row.face.owner;m.neighbour[f]=row.face.neighbour;
                    // patch/zone keep their literal old role; no cross-tag transfer.
                }
                for(label f:plan.retired)m.faceAlive[f]=false;
                for(label c:members)if(c!=seed)m.cellAlive[c]=false;
                for(auto& c:nextCf)cf[c.first].swap(c.second);
                for(label c:members)if(c!=seed)cf[c].clear();
                dcCartPreparedFacetBirth::invalidateCurrentIncidence(m);
                for(label c:plan.affected)
                {
                    if(std::size_t(c)<m.preparedCompleteCells.size())m.preparedCompleteCells[c]=false;
                    current.erase(c);currentMoments.erase(c);currentConeDebt.erase(c);
                }
                ++report.accepted;report.members+=members.size();break;
            }
            captureFailure("full-source-vm-outside-stock-facet",why?why:"source-clock",&plan,&host);
            refusal(why?why:"source-clock");
            if(!M::workAvailable(available)||members.size()==9)break;
            if(!grow())break;
        }
    }
    Info<<"Raw union exact-ring cache entries/area-hit/miss/planar-hit/tetra-hit/miss "
        <<geometry.ringCache.size()<<"/"<<geometry.areaHits<<"/"<<geometry.areaMisses
        <<"/"<<geometry.planarHits<<"/"<<geometry.tetraHits<<"/"<<geometry.tetraMisses
        <<" scanned/debt seeds "<<scannedSeeds<<"/"<<debtSeeds<<nl;
    return;
}

} }
#endif
