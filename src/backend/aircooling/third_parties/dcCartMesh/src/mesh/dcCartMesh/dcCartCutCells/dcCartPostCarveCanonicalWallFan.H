#ifndef dcCartPostCarveCanonicalWallFan_H
#define dcCartPostCarveCanonicalWallFan_H
#include "dcCartPointRepairSourceContract.H"
#include "point.H"
#include "wordList.H"
#include "labelList.H"
#include "dcCartExactBinary64Geometry.H"
#include "dcCartPreparedFacetBirth.H"
#include <array>
#include <vector>
#include <map>
#include <set>
#include <functional>
#include <limits>
#include <cmath>
#include <cstring>
#include <cstdint>
#include <algorithm>
#include <utility>
#include <stdexcept>
#include <type_traits>

namespace Foam { namespace dcCartPostCarveCanonicalWallFan
{
// Representation repair before the current post-carve authority snapshot.
// A new actual cut-face handle is NOT an STL triangle ID or a D birth certificate.
struct Role
{
    bool known=false;
    std::string entity,region,sha,operation;
};
inline bool sameRole(const Role& a,const Role& b) noexcept
{return a.known&&b.known&&a.entity==b.entity&&a.region==b.region&&a.sha==b.sha&&a.operation==b.operation;}
// Construction provenance only, not Source or geometric permission. Native
// labels can be remapped by extraction; the complete ordered world-ring bits
// must remain literal before a role is attached to the current source face.
struct BaffleRegionBirth
{
    label region=-1,zone=-1;
    std::vector<std::array<std::uint64_t,3>> orderedPointBits;
};
struct Record
{
    label original=-1,parent=-1,patch=-1,zone=-1;
    std::vector<label> actualFaces;
    Role role;
    // Value-owned construction origin, never source/material/peer authority.
    std::vector<label> originalRing;
    std::vector<std::array<std::uint64_t,3>> originalPointBits;
    label hub=-1,extractionEpoch=0;
    point storedFC=point::zero;
    std::array<std::uint64_t,3> hubBits{{0,0,0}};
    std::uint64_t generation=0;
    std::size_t beforePoints=0,beforeFaces=0;
};
// Actual carrier geometry and every distinct original face are separate domains.
// This value-owned shape record grants no Source/material/MPI permission.
struct CompositeConstituent
{
    label native=-1,source=-1,owner=-1,neighbour=-1,patch=-1,zone=-1;
    std::vector<label> ring;
};
struct CompositeRecord
{
    label carrier=-1,parent=-1,child=-1,patch=-1,zone=-1;
    Role role; // actual positive APP BC producer binding, not P/Z inference
    std::vector<CompositeConstituent> constituents;
    std::vector<label> outline;
    std::map<label,std::array<std::uint64_t,3>> pointBits;
};
// Optional diagnostic payload, independent of geometry/authority Record storage.
// Counts and preparedBirth fields are observed values, not a new generation certificate.
struct InventoryRecord
{
    label hub=-1;
    std::array<std::uint64_t,3> hubBits{{0,0,0}};
    std::vector<label> originalRing;
    std::vector<std::array<std::uint64_t,3>> originalPointBits;
};
struct Inventory
{
    bool requested=false,complete=false,overflow=false,error=false,clock=false;
    std::size_t beforePoints=0,beforeFaces=0,beforeCells=0;
    label extractionEpoch=0;
    std::uint64_t generationBefore=0;
    std::vector<InventoryRecord> records;
};
struct Result
{
    bool completed=false,published=false;
    label candidates=0,parents=0,refused=0,newPoints=0,newFaces=0;
    scalar maximumFoamCentreChange=0;
    const char* reason="unchanged";
    std::vector<Record> records;
    Inventory inventory;
};
inline bool available(const std::function<bool()>& work)
{return !work||work();}
inline bool finite(const point& p)
{return std::isfinite(p.x())&&std::isfinite(p.y())&&std::isfinite(p.z());}
inline std::array<std::uint64_t,3> bits(const point& p)
{
    std::array<std::uint64_t,3> out;
    for(unsigned d=0;d<3;++d){const double x=p[d];std::memcpy(&out[d],&x,8);}
    return out;
}
inline bool text(const dictionary& d,const word& key,std::string& out)
{
    const ITstream* s=dcCartPointRepairSourceContract::primitive(d,key);
    if(!s||s->size()!=1)return false;
    const token& t=(*s)[0];
    if(t.isString())out=t.stringToken().c_str();
    else if(t.isWord())out=t.wordToken().c_str();
    else return false;
    return true;
}
inline bool uid(const std::string& s)
{
    return !s.empty()&&s.find_first_not_of
        ("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_./:-")==std::string::npos;
}
template<class Surfaces>
std::vector<Role> roles(const Surfaces& surfaces,const dictionary& identity,
    const wordList& patchTypes,const labelList& wallMasters,const labelList& wallSlaves,
    const labelList& cycMasters,const labelList& cycSlaves,const std::function<bool()>& work,
    bool declaredWallPairBC=false)
{
    std::vector<Role> out(patchTypes.size());
    std::vector<bool> protectedPatch(patchTypes.size(),false),seen(patchTypes.size(),false);
    for(const labelList* list:{&wallMasters,&wallSlaves,&cycMasters,&cycSlaves})
        for(label p:*list)if(p>=0&&std::size_t(p)<out.size())protectedPatch[p]=true;
    const entry* catalogue=identity.findEntry("dcdtuniSourceRegions",keyType::LITERAL);
    if(!catalogue||!catalogue->isDict())return out;
    std::set<std::string> regionUids;
    for(const entry& file:catalogue->dict())
    {
        if(!available(work))return std::vector<Role>(out.size());
        if(!file.isDict()||file.keyword().isPattern())continue;
        label si=-1;
        for(label i=0;i<label(surfaces.info().size());++i)
            if(std::string(surfaces.info()[i].stlFile.c_str())==std::string(file.keyword().c_str()))
            {if(si>=0){si=-2;break;}si=i;}
        if(si<0)continue;
        const auto& surface=surfaces.info()[si];
        const bool ordinary=surface.carves&&!surface.internalBaffle&&!surface.wallBaffle&&surface.faceZone.empty();
        const bool paired=declaredWallPairBC&&(surface.internalBaffle||surface.wallBaffle)&&!surface.faceZone.empty();
        if(!ordinary&&!paired)continue;
        const wordList names(surfaces.sourceRegionNames(si));
        for(const entry& re:file.dict())
        {
            if(!available(work))return std::vector<Role>(out.size());
            if(!re.isDict()||re.keyword().isPattern())continue;
            label ri=-1;
            forAll(names,i)if(names[i]==re.keyword()){if(ri>=0){ri=-2;break;}ri=i;}
            if(ri<0||ri>=surface.nRegions)continue;
            const label p=surface.regionOffset+ri;
            if(p<0||p>=surfaces.nPatches()||std::size_t(p)>=out.size()||patchTypes[p]!="wall"||(protectedPatch[p]&&!paired))continue;
            if(seen[p]){out[p]=Role();continue;}
            seen[p]=true;
            const dictionary& d=re.dict();Role r;bool fields=true;
            const std::set<std::string> keys={"sourceEntityUid","sourceRegionUid",
                "sourceRegionSha256","sourceFacetCount","operation"};
            for(const entry& e:d)if(e.isDict()||e.keyword().isPattern()
                ||!keys.count(e.keyword().c_str()))fields=false;
            const ITstream* count=dcCartPointRepairSourceContract::primitive(d,"sourceFacetCount");
            if(!fields||!text(d,"sourceEntityUid",r.entity)||!text(d,"sourceRegionUid",r.region)
                ||!text(d,"sourceRegionSha256",r.sha)||!text(d,"operation",r.operation)
                ||!uid(r.entity)||!uid(r.region)||r.sha.size()!=64
                ||r.sha.find_first_not_of("0123456789abcdef")!=std::string::npos
                ||!count||count->size()!=1||!(*count)[0].isLabel()
                ||(*count)[0].labelToken()<=0
                ||(*count)[0].labelToken()!=surfaces.sourceRegionFacetCount(si,ri)
                ||!(r.operation=="modelBoundary"||r.operation=="liquidTransportUnionBoundary"))
                continue;
            if(!regionUids.insert(r.region).second)
            {
                for(Role& old:out)if(old.region==r.region)old=Role();
                continue;
            }
            r.known=true;out[p]=std::move(r);
        }
    }
    return out;
}
// Only real APP wall-pair slots can receive a scalar bridge. A multi-region
// zone stays unknown here: its actual native-face lineage is resolved below.
// Original roles() metadata validation and its default plain scope are held.
template<class Surfaces>
void bridgeDeclaredWallPairRoles(const Surfaces& surfaces,
    const std::vector<Role>& originalRegions,const wordList& patchTypes,
    const std::vector<std::array<label,2>>& pairs,std::vector<Role>& out)
{
    if(out.size()!=std::size_t(patchTypes.size()))return;
    for(std::size_t z=0;z<pairs.size();++z)
    {
        const auto pair=pairs[z];const label a=pair[0],b=pair[1];
        if(a<0||b<0||a==b||std::size_t(a)>=out.size()||std::size_t(b)>=out.size()
            ||patchTypes[a]!="wall"||patchTypes[b]!="wall"
            ||z>=std::size_t(surfaces.faceZoneNames().size()))continue;
        label selected=-1;bool ambiguous=false;
        for(const auto& si:surfaces.info())
        {
            if(!(si.internalBaffle||si.wallBaffle)||si.faceZone.empty()
                ||si.faceZone!=surfaces.faceZoneNames()[z])continue;
            if(si.nRegions!=1||si.regionOffset<0
                ||std::size_t(si.regionOffset)>=originalRegions.size())
            {ambiguous=true;continue;}
            if(selected>=0){ambiguous=true;continue;}
            selected=si.regionOffset;
        }
        if(ambiguous||selected<0||!originalRegions[selected].known)continue;
        const Role& role=originalRegions[selected];
        if((out[a].known&&!sameRole(out[a],role))||(out[b].known&&!sameRole(out[b],role)))
        {out[a]=Role();out[b]=Role();continue;}
        out[a]=role;out[b]=role;
    }
}
template<class Mesh>
bool baffleRegionBirthCurrent(const Mesh& m,label face)
{
    const auto found=m.physicalBaffleRegionBirths.find(face);
    if(found==m.physicalBaffleRegionBirths.end()||found->second.region<0||face<0
        ||std::size_t(face)>=m.faces.size()||std::size_t(face)>=m.zone.size()
        ||std::size_t(face)>=m.faceAlive.size()||!m.faceAlive[face]
        ||m.zone[face]!=found->second.zone)return false;
    const auto& ring=m.faces[face];const auto& expected=found->second.orderedPointBits;
    if(ring.size()<3||ring.size()>128||ring.size()!=expected.size())return false;
    for(std::size_t i=0;i<ring.size();++i)
        if(ring[i]<0||std::size_t(ring[i])>=m.points.size()
            ||bits(m.points[ring[i]])!=expected[i])return false;
    return true;
}
template<class Mesh>
void bindBaffleRegionBirth(Mesh& m,label face,label region)
{
    if(face<0||std::size_t(face)>=m.faces.size()||std::size_t(face)>=m.zone.size())return;
    auto found=m.physicalBaffleRegionBirths.find(face);
    if(region<0||m.zone[face]<0||m.faces[face].size()<3||m.faces[face].size()>128)
    {BaffleRegionBirth rejected;rejected.region=-2;rejected.zone=m.zone[face];
     m.physicalBaffleRegionBirths[face]=std::move(rejected);return;}
    if(found!=m.physicalBaffleRegionBirths.end()
        &&(found->second.region==-2||(found->second.region>=0&&found->second.region!=region)))
    {found->second.region=-2;found->second.orderedPointBits.clear();return;}
    BaffleRegionBirth born;born.region=region;born.zone=m.zone[face];
    born.orderedPointBits.reserve(m.faces[face].size());
    for(label p:m.faces[face])
    {if(p<0||std::size_t(p)>=m.points.size()||!finite(m.points[p]))return;
     born.orderedPointBits.push_back(bits(m.points[p]));}
    m.physicalBaffleRegionBirths[face]=std::move(born);
}
// Per-native-source face qualification is independent of the single patch role.
// A paired lookup needs the actual APP pair and an actual captured region birth;
// a unique-region bridge is the only allowed fallback for that declared pair.
// A frozen source rejection (creator unknown/conflict/stale) takes precedence;
// later mutable native rings are never used to refresh original source identity.
template<class Mesh>
bool compositeRoleForSourceFace(const Mesh& m,label source,label patch,label zone,Role& role)
{
    role=Role();
    if(source<0||patch<0||std::size_t(patch)>=m.physicalCompositeBCRoles.size()
        ||m.physicalCompositeSourceBCRejected.count(source))return false;
    if(zone>=0)
    {
        if(std::size_t(zone)>=m.physicalWallPatchPairs.size())return false;
        const auto pair=m.physicalWallPatchPairs[zone];
        if(pair[0]<0||pair[1]<0||pair[0]==pair[1]||(patch!=pair[0]&&patch!=pair[1]))return false;
        const auto found=m.physicalCompositeSourceBCRoles.find(source);
        if(found!=m.physicalCompositeSourceBCRoles.end()&&found->second.known)
        {
            if(m.physicalCompositeBCRoles[patch].known
                &&!sameRole(found->second,m.physicalCompositeBCRoles[patch]))return false;
            role=found->second;return true;
        }
    }
    if(!m.physicalCompositeBCRoles[patch].known)return false;
    role=m.physicalCompositeBCRoles[patch];return true;
}
struct Staged
{
    std::vector<point> points;
};
template<class Mesh>
const point& coordinate(const Mesh& m,const Staged& s,label id)
{
    if(id<0)throw std::out_of_range("canonical-wall-fan point");
    if(std::size_t(id)<m.points.size())return m.points[id];
    return s.points.at(std::size_t(id)-m.points.size());
}
template<class Mesh>
void metric(const Mesh& m,const Staged& s,const std::vector<label>& ring,vector& area,point& centre)
{
    // Literal current faceAreaCentreOF arithmetic; freezes the same stored FC.
    area=vector::zero;centre=point::zero;const label k=ring.size();
    if(k==3)
    {
        const point& a=coordinate(m,s,ring[0]);const point& b=coordinate(m,s,ring[1]);
        const point& c=coordinate(m,s,ring[2]);centre=(a+b+c)/3.0;area=.5*((b-a)^(c-a));return;
    }
    point fc=point::zero;for(label id:ring)fc+=coordinate(m,s,id);fc/=scalar(k);
    vector sumN=vector::zero,sumAc=vector::zero;scalar sumA=0;
    for(label i=0;i<k;++i)
    {
        const point& p=coordinate(m,s,ring[i]);const point& q=coordinate(m,s,ring[(i+1)%k]);
        const vector n=(q-p)^(fc-p);const scalar a=mag(n);
        sumN+=n;sumA+=a;sumAc+=a*(p+q+fc);
    }
    if(sumA<ROOTVSMALL){centre=fc;return;}
    centre=(1.0/3.0)*sumAc/sumA;area=.5*sumN;
}
inline dcCartExactBinary64Geometry::Point exactPoint(const point& p)
{return {{double(p.x()),double(p.y()),double(p.z())}};}
template<class Mesh>
bool nonplanar(const Mesh& m,const std::vector<label>& ring,const std::function<bool()>& work)
{
    namespace E=dcCartExactBinary64Geometry;
    // Exact coordinate-constant planes are a cheap, complete rejection for axis planes.
    for(unsigned d=0;d<3;++d)
    {
        bool same=true;for(label id:ring)if(m.points[id][d]!=m.points[ring[0]][d]){same=false;break;}
        if(same)return false;
    }
    E::Context c([&](){return available(work);},131072);
    const auto a=E::vector(exactPoint(m.points[ring[0]]),c);
    E::Vector n;bool found=false;
    for(std::size_t j=1;j+1<ring.size();++j)
    {
        n=E::cross(E::difference(E::vector(exactPoint(m.points[ring[j]]),c),a,c),
            E::difference(E::vector(exactPoint(m.points[ring[j+1]]),c),a,c),c);
        if(!c.good())return false;
        for(unsigned d=0;d<3;++d)if(E::sign(n[d]))found=true;
        if(found)break;
    }
    if(!found)return false;
    for(label id:ring)
    {
        const auto side=E::dot(n,E::difference(E::vector(exactPoint(m.points[id]),c),a,c),c);
        if(!c.good())return false;
        if(E::sign(side))return true;
    }
    return false;
}
template<class Mesh>
bool simpleStar(const Mesh& m,const std::vector<label>& ring,const point& fc,
    const vector& area,const std::function<bool()>& work)
{
    namespace E=dcCartExactBinary64Geometry;
    E::Context c([&](){return available(work);},131072);
    const auto n=E::vector(exactPoint(area),c),centre=E::vector(exactPoint(fc),c);
    const auto n2=E::dot(n,n,c);if(!c.good()||E::sign(n2)<=0)return false;
    std::vector<E::Vector> q;q.reserve(ring.size());
    for(label id:ring)q.push_back(E::vector(exactPoint(m.points[id]),c));
    auto orientation=[&](std::size_t a,std::size_t b,std::size_t p)
    {return E::dot(E::cross(E::difference(q[b],q[a],c),E::difference(q[p],q[a],c),c),n,c);};
    auto between=[&](std::size_t a,std::size_t b,std::size_t p)
    {
        const auto u=E::difference(q[a],q[p],c),v=E::difference(q[b],q[p],c);
        return E::sign(E::subtract(E::multiply(E::dot(u,v,c),n2,c),
            E::multiply(E::dot(u,n,c),E::dot(v,n,c),c),c))<=0;
    };
    for(std::size_t i=0;i<q.size();++i)
    {
        const auto fan=E::cross(E::difference(q[(i+1)%q.size()],q[i],c),
            E::difference(centre,q[i],c),c);
        const auto wedge=E::dot(fan,n,c);
        if(!c.good()||E::sign(wedge)<=0)return false;
    }
    for(std::size_t i=0;i<q.size();++i)for(std::size_t j=i+1;j<q.size();++j)
    {
        if(!c.take())return false;
        const auto in=(i+1)%q.size(),jn=(j+1)%q.size();
        if(in==j||jn==i)continue;
        const int a=E::sign(orientation(i,in,j)),b=E::sign(orientation(i,in,jn));
        const int d=E::sign(orientation(j,jn,i)),e=E::sign(orientation(j,jn,in));
        bool crossed=a*b<0&&d*e<0;
        if(a==0)crossed=crossed||between(i,in,j);
        if(b==0)crossed=crossed||between(i,in,jn);
        if(d==0)crossed=crossed||between(j,jn,i);
        if(e==0)crossed=crossed||between(j,jn,in);
        if(!c.good()||crossed)return false;
    }
    return c.take();
}
static_assert(sizeof(scalar)==8&&std::numeric_limits<scalar>::is_iec559,
    "canonical physical points require the current binary64 native scalar");
static_assert(std::is_nothrow_default_constructible<point>::value,
    "publication requires the current fixed-scalar point default construction");
static_assert(std::is_nothrow_move_constructible<Result>::value,
    "publication receipt must return without an allocating copy");
static_assert(std::is_nothrow_move_assignable<std::vector<label>>::value,
    "canonical fan publication requires a nonthrowing default-allocator ring move");
static_assert(std::is_nothrow_default_constructible<std::vector<label>>::value,
    "canonical fan publication requires a nonthrowing empty footprint row");
using Key=std::array<long long,3>;
inline bool key(const point& p,Key& k)
{
    for(unsigned d=0;d<3;++d)
    {
        const scalar bucket=std::floor(p[d]/scalar(3e-6));
        const long double x=bucket;
        if(!std::isfinite(x)||x<static_cast<long double>(std::numeric_limits<long long>::min())+2
            ||x>static_cast<long double>(std::numeric_limits<long long>::max())-2)return false;
        k[d]=static_cast<long long>(x);
    }
    return true;
}
template<class Mesh>
label centrePoint(const Mesh& m,Staged& s,const point& fc,
    const std::map<Key,std::vector<label>>& index,
    std::map<Key,std::vector<label>>& additions,const std::function<bool()>& work)
{
    Key k;if(!finite(fc)||!key(fc,k))return -1;
    label found=-1;
    for(int x=-1;x<=1;++x)for(int y=-1;y<=1;++y)for(int z=-1;z<=1;++z)
    {
        if(!available(work))return -1;
        const Key neighbour{{k[0]+x,k[1]+y,k[2]+z}};
        for(const auto* table:{&index,static_cast<const std::map<Key,std::vector<label>>*>(&additions)})
        {
            const auto it=table->find(neighbour);if(it==table->end())continue;
            for(label id:it->second)
            {
                if(!available(work))return -1;
                const point& p=coordinate(m,s,id);const scalar d=magSqr(p-fc);
                if(d<=9e-12)
                {
                    if(bits(p)!=bits(fc))return -1;
                    if(found<0||id<found)found=id;
                }
            }
        }
    }
    if(found>=0)return found;
    if(m.points.size()+s.points.size()>=std::size_t(std::numeric_limits<label>::max()))return -1;
    const label id=m.points.size()+s.points.size();s.points.push_back(fc);
    additions[k].push_back(id);return id;
}
struct Shell
{
    std::array<long double,4> physical{{0,0,0,0}};
    point foamCentre=point::zero,lo=point(GREAT,GREAT,GREAT),hi=point(-GREAT,-GREAT,-GREAT);
};
template<class Mesh>
bool measure(const Mesh& m,const Staged& s,const std::vector<std::vector<label>>& rings,
    const point& reference,const std::function<bool()>& work,Shell& out)
{
    if(rings.size()<4)return false;
    std::map<std::pair<label,label>,label> edges;
    std::vector<vector> areas;std::vector<point> centres;
    vector closure=vector::zero;scalar areaScale=0;point estimate=point::zero;
    auto tetra=[&](const point& a,const point& b,const point& c)
    {
        std::array<long double,3> x,y,z;
        for(unsigned d=0;d<3;++d)
        {x[d]=static_cast<long double>(a[d])-reference[d];
         y[d]=static_cast<long double>(b[d])-reference[d];
         z[d]=static_cast<long double>(c[d])-reference[d];}
        const long double v=(x[0]*(y[1]*z[2]-y[2]*z[1])
            +x[1]*(y[2]*z[0]-y[0]*z[2])+x[2]*(y[0]*z[1]-y[1]*z[0]))/6;
        out.physical[0]+=v;
        for(unsigned d=0;d<3;++d)out.physical[d+1]+=v*(x[d]+y[d]+z[d])/4;
    };
    for(const auto& ring:rings)
    {
        if(!available(work)||ring.size()<3||ring.size()>64)return false;
        std::set<label> unique;
        for(label id:ring)
        {
            const point& p=coordinate(m,s,id);
            if(!finite(p)||!unique.insert(id).second)return false;
            out.lo=min(out.lo,p);out.hi=max(out.hi,p);
        }
        vector a;point fc;metric(m,s,ring,a,fc);
        if(!finite(a)||!finite(fc)||mag(a)<=1e-12)return false;
        areas.push_back(a);centres.push_back(fc);closure+=a;areaScale+=mag(a);estimate+=fc;
        for(std::size_t j=0;j<ring.size();++j)
        {
            if(!available(work))return false;
            ++edges[{ring[j],ring[(j+1)%ring.size()]}];
        }
        // The canonical physical triangle is its THREE stored vertices.
        // Only a polygon uses its frozen actual OF FC as the canonical fan.
        if(ring.size()==3)tetra(coordinate(m,s,ring[0]),coordinate(m,s,ring[1]),coordinate(m,s,ring[2]));
        else for(std::size_t j=0;j<ring.size();++j)
            tetra(coordinate(m,s,ring[j]),coordinate(m,s,ring[(j+1)%ring.size()]),fc);
    }
    for(const auto& e:edges)
    {
        if(!available(work)||e.second!=1)return false;
        const auto r=edges.find({e.first.second,e.first.first});
        if(r==edges.end()||r->second!=1)return false;
    }
    if(!std::isfinite(out.physical[0])||out.physical[0]<=0
        ||mag(closure)>1e-10*std::max(areaScale,scalar(VSMALL)))return false;
    for(unsigned d=1;d<4;++d)if(!std::isfinite(out.physical[d]))return false;
    // The current OF centre is a separate diagnostic, not an additive physical moment.
    estimate/=scalar(rings.size());point mc=point::zero;scalar v3=0;
    for(std::size_t i=0;i<rings.size();++i)
    {
        const scalar v=areas[i]&(centres[i]-estimate);v3+=v;
        mc+=v*(.75*centres[i]+.25*estimate);
    }
    if(!std::isfinite(v3)||v3<=0)return false;
    out.foamCentre=mc/v3;
    return finite(out.foamCentre)&&available(work);
}
struct Fan
{
    label face=-1,parent=-1,pointId=-1;
    std::vector<std::vector<label>> triangles;
    Role role;
};
template<class Mesh>
Result apply(Mesh& m,const std::vector<Role>& sourceRoles,label rank,
    const std::function<bool()>& work,bool captureInventory=false)
{
    Result result;Staged staged;std::vector<Fan> approved;std::size_t added=0;
    if(captureInventory)
    {
        auto& observed=result.inventory;observed.requested=true;
        observed.beforePoints=m.points.size();observed.beforeFaces=m.faces.size();
        observed.beforeCells=m.nCells;observed.extractionEpoch=m.preparedBirth.extractionEpoch;
        observed.generationBefore=m.preparedBirth.generation;
    }
    try
    {
        const std::size_t nf=m.faces.size(),np=m.points.size();
        if(m.owner.size()!=nf||m.neighbour.size()!=nf||m.patch.size()!=nf
            ||m.zone.size()!=nf||m.faceAlive.size()!=nf
            ||m.cellAlive.size()!=std::size_t(m.nCells)||m.cellRank.size()!=std::size_t(m.nCells)
            ||(!m.preparedExactPoints.empty()&&m.preparedExactPoints.size()!=np)
            ||(!m.preparedPointFootprints.empty()&&m.preparedPointFootprints.size()!=np))
        {result.reason="shape";return result;}
        // This call must precede all immutable current-face authority/writer snapshots.
        if(m.writerBaseline||m.weightUnionState||m.completeCavityContext||m.completeCavityState)
        {result.reason="already-snapshotted";return result;}
        std::map<label,std::vector<label>> candidates;
        for(label f=0;f<label(nf);++f)
        {
            if(!available(work)){result.reason="whole-clock";return result;}
            const label parent=m.owner[f],p=m.patch[f];const auto& ring=m.faces[f];
            if(!m.faceAlive[f]||m.neighbour[f]!=-1||m.zone[f]!=-1
                ||parent<0||parent>=m.nCells||!m.cellAlive[parent]||m.cellRank[parent]!=rank
                ||p<0||std::size_t(p)>=sourceRoles.size()||!sourceRoles[p].known
                ||ring.size()<=3||ring.size()>64)continue;
            bool valid=true;std::set<label> unique;
            for(label id:ring)if(id<0||std::size_t(id)>=np||!finite(m.points[id])
                ||!unique.insert(id).second){valid=false;break;}
            if(valid&&nonplanar(m,ring,work))
            {candidates[parent].push_back(f);++result.candidates;}
        }
        if(candidates.empty()){result.completed=true;return result;}
        std::map<label,std::vector<label>> cf;
        for(const auto& c:candidates)cf.emplace(c.first,std::vector<label>());
        for(label f=0;f<label(nf);++f)
        {
            if(!available(work)){result.reason="whole-clock";return result;}
            if(!m.faceAlive[f])continue;
            for(label parent:{m.owner[f],m.neighbour[f]})
            {const auto it=cf.find(parent);if(it!=cf.end())it->second.push_back(f);}
        }
        std::map<Key,std::vector<label>> pointIndex;
        for(label id=0;id<label(np);++id)
        {
            if(!available(work)){result.reason="whole-clock";return result;}
            Key k;if(!finite(m.points[id])||!key(m.points[id],k))
            {result.reason="point-index-domain";return result;}
            pointIndex[k].push_back(id);
        }
        for(const auto& group:candidates)
        {
            if(!available(work)){result.reason="whole-clock";return result;}
            const label parent=group.first;const auto& oldCF=cf.at(parent);
            Staged local=staged;
            // Only this owner's proposed FCs are mutable; no full-point-index copy.
            std::map<Key,std::vector<label>> localAdditions;
            std::vector<Fan> proposal;bool good=true;
            for(label f:group.second)
            {
                const auto& ring=m.faces[f];vector a;point fc;metric(m,local,ring,a,fc);
                if(!finite(a)||!finite(fc)||mag(a)<=1e-12||!simpleStar(m,ring,fc,a,work))
                {good=false;break;}
                const label id=centrePoint(m,local,fc,pointIndex,localAdditions,work);
                if(id<0){good=false;break;}
                Fan fan;fan.face=f;fan.parent=parent;fan.pointId=id;fan.role=sourceRoles[m.patch[f]];
                vector sum=vector::zero;scalar physicalArea=0,triArea=0;
                for(std::size_t j=0;j<ring.size();++j)
                {
                    if(!available(work)){good=false;break;}
                    fan.triangles.push_back({ring[j],ring[(j+1)%ring.size()],id});
                    vector ta;point tc;metric(m,local,fan.triangles.back(),ta,tc);
                    const vector original=.5*((m.points[ring[(j+1)%ring.size()]]-m.points[ring[j]])^(fc-m.points[ring[j]]));
                    if(!finite(ta)||mag(ta)<=1e-12||(ta&a)<=0){good=false;break;}
                    sum+=ta;triArea+=mag(ta);physicalArea+=mag(original);
                }
                if(!good||mag(sum-a)>1e-10*mag(a)
                    ||std::abs(triArea-physicalArea)>1e-10*std::max(physicalArea,scalar(VSMALL)))
                {good=false;break;}
                proposal.push_back(std::move(fan));
            }
            std::vector<std::vector<label>> before,after;
            if(good)for(label f:oldCF)
            {
                auto ring=m.faces[f];if(m.neighbour[f]==parent)std::reverse(ring.begin(),ring.end());
                before.push_back(ring);const Fan* replacement=nullptr;
                for(const Fan& fan:proposal)if(fan.face==f){replacement=&fan;break;}
                if(replacement)for(const auto& t:replacement->triangles)after.push_back(t);
                else after.push_back(std::move(ring));
            }
            if(good)
            {
                const point reference=coordinate(m,local,before[0][0]);Shell a,b;
                good=measure(m,local,before,reference,work,a)&&measure(m,local,after,reference,work,b);
                if(good)
                {
                    const long double scale=std::max(static_cast<long double>(mag(a.hi-a.lo)),1e-30L);
                    good=std::abs(a.physical[0]-b.physical[0])<=1e-10L*a.physical[0];
                    for(unsigned d=1;d<4;++d)good=good
                        &&std::abs(a.physical[d]-b.physical[d])<=1e-10L*a.physical[0]*scale;
                    if(good)result.maximumFoamCentreChange=std::max
                        (result.maximumFoamCentreChange,mag(a.foamCentre-b.foamCentre));
                }
            }
            if(!good){++result.refused;continue;}
            for(const auto& e:localAdditions)
            {
                if(!available(work)){result.reason="whole-clock";return result;}
                auto& bucket=pointIndex[e.first];bucket.insert(bucket.end(),e.second.begin(),e.second.end());
            }
            staged.points.swap(local.points);
            for(Fan& fan:proposal)approved.push_back(std::move(fan));
            ++result.parents;
        }
        if(!available(work)){result.reason="whole-clock";return result;}
        if(approved.empty()){result.completed=true;result.reason="parent-veto";return result;}
        for(const Fan& fan:approved)added+=fan.triangles.size()-1;
        if(nf+added>=std::size_t(std::numeric_limits<label>::max()))
        {result.reason="label-capacity";return result;}
        // Preallocate all result metadata and native arrays before the first logical mesh edit.
        result.records.reserve(approved.size());label next=nf;
        for(const Fan& fan:approved)
        {
            Record r;r.original=fan.face;r.parent=fan.parent;r.patch=m.patch[fan.face];
            r.zone=m.zone[fan.face];r.role=fan.role;r.actualFaces.reserve(fan.triangles.size());
            r.originalRing=m.faces[fan.face];r.originalPointBits.reserve(r.originalRing.size());
            for(label p:r.originalRing)
            {
                if(!available(work)){result.reason="origin-record-clock";return result;}
                r.originalPointBits.push_back(bits(m.points[p]));
            }
            r.hub=fan.pointId;r.storedFC=coordinate(m,staged,r.hub);r.hubBits=bits(r.storedFC);
            r.beforePoints=np;r.beforeFaces=nf;
            r.actualFaces.push_back(fan.face);
            for(std::size_t j=1;j<fan.triangles.size();++j)r.actualFaces.push_back(next++);
            result.records.push_back(std::move(r));
        }
        // Capture the actual original rings while still live, before publication.
        // An observation allocation/callback failure cannot change the preparation decision.
        if(captureInventory)
        {
            auto& observed=result.inventory;
            if(approved.size()>4096)observed.overflow=true;
            else try
            {
                observed.records.reserve(approved.size());
                for(const Fan& fan:approved)
                {
                    if(!available(work)){observed.clock=true;break;}
                    InventoryRecord record;record.hub=fan.pointId;
                    record.hubBits=bits(coordinate(m,staged,fan.pointId));
                    record.originalRing=m.faces[fan.face];
                    record.originalPointBits.reserve(record.originalRing.size());
                    for(label id:record.originalRing)
                    {
                        if(!available(work)){observed.clock=true;break;}
                        record.originalPointBits.push_back(bits(m.points[id]));
                    }
                    if(observed.clock)break;
                    observed.records.push_back(std::move(record));
                }
                observed.complete=!observed.clock&&observed.records.size()==approved.size();
            }
            catch(...){observed.records.clear();observed.complete=false;observed.error=true;}
        }
        m.points.reserve(np+staged.points.size());m.faces.reserve(nf+added);
        m.owner.reserve(nf+added);m.neighbour.reserve(nf+added);m.patch.reserve(nf+added);
        m.zone.reserve(nf+added);m.faceAlive.reserve(nf+added);
        if(!m.preparedExactPoints.empty())m.preparedExactPoints.reserve(np+staged.points.size());
        if(!m.preparedPointFootprints.empty())m.preparedPointFootprints.reserve(np+staged.points.size());
        if(!available(work)){result.reason="whole-clock";return result;}
    }
    catch(const std::exception&)
    {result.records.clear();result.inventory.records.clear();result.inventory.complete=false;result.reason="prepublication-resource-or-geometry";return result;}
    // This separate publication block is intentionally OUTSIDE the preparation
    // catch: an unexpected publication exception must not be labelled unchanged.
    // Default-allocator ring move and point default construction are noexcept.
    // Point publication assigns only the three noexcept scalar component accesses.
    // All native capacities are already reserved. No callback or allocating
    // construction remains, including empty std::vector<label> footprint rows.
    for(const point& p:staged.points)
        {
            m.points.emplace_back();
            m.points.back().x()=p.x();m.points.back().y()=p.y();m.points.back().z()=p.z();
            if(!m.preparedExactPoints.empty())m.preparedExactPoints.push_back(0);
            if(!m.preparedPointFootprints.empty())m.preparedPointFootprints.emplace_back();
        }
        for(Fan& fan:approved)
        {
            const label p=m.patch[fan.face],z=m.zone[fan.face];
            m.faces[fan.face]=std::move(fan.triangles[0]);
            for(std::size_t j=1;j<fan.triangles.size();++j)
            {
                m.faces.push_back(std::move(fan.triangles[j]));
                m.owner.push_back(fan.parent);m.neighbour.push_back(-1);
                m.patch.push_back(p);m.zone.push_back(z);m.faceAlive.push_back(true);
            }
        }
        dcCartPreparedFacetBirth::invalidateCurrentIncidence(m);
        // Bind construction metadata to the state that actually exists after
        // publication/revocation. These numbers do not restore birth authority.
        for(Record& record:result.records)
        {
            record.extractionEpoch=m.preparedBirth.extractionEpoch;
            record.generation=m.preparedBirth.generation;
        }
        result.newPoints=staged.points.size();result.newFaces=added;
        result.completed=true;result.published=true;result.reason="canonical-surface-preserved";
    return result;
}
}}
#endif
