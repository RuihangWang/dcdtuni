// PRIVATE candidate: closed dependency partition only, never permission by itself.
#ifndef dcCartFiniteClosedComponents_H
#define dcCartFiniteClosedComponents_H
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <functional>
#include <map>
#include <set>
#include <string>
#include <vector>

namespace dcCartFiniteClosedComponents
{
using Id=std::int64_t;
using XYZ=std::array<double,3>;
inline bool finiteXYZ(const XYZ& p)
{ for(double x:p)if(!std::isfinite(x))return false;return true; }
inline bool sameXYZ(const XYZ& a,const XYZ& b)
{ return std::memcmp(a.data(),b.data(),3*sizeof(double))==0; }
inline bool sortedUnique(const std::vector<Id>& ids)
{
    for(std::size_t i=0;i<ids.size();++i)
        if(ids[i]<0||(i&&ids[i-1]>=ids[i]))return false;
    return true;
}
struct Footprint
{
    Id moving=-1;
    std::vector<Id> points,coreCells,outsideCells,faces,dependencyCells;
    // ORIGINAL earliest compact coordinates. Equal-coordinate aliases join
    // even when two labels differ; a changed accepted coordinate cannot split
    // an originally overlapping dependency component.
    std::vector<XYZ> earliestCoordinates;
    bool protectedPoint=false,coupledCore=false,zone=false,tetBinding=true;
};
struct Counts
{
    std::size_t core=0,outside=0,faces=0,points=0;
};
struct Partition
{
    bool valid=false;
    std::string refusal;
    std::vector<std::vector<Id>> components;
    std::vector<Counts> counts;
};
inline Partition partition
(
    const std::vector<Footprint>& footprints,
    const std::vector<std::vector<Id>>& atomicModes,
    const std::function<bool()>& available,
    std::size_t componentCap=256
)
{
    Partition result;
    auto refuse=[&](const char* reason)
    { result.refusal=reason;result.components.clear();result.counts.clear();return result; };
    // 512 census nodes plus the existing eight R modes of at most three
    // points. This is a bound, not a new census, budget or source authority.
    if(footprints.size()>536||!componentCap||componentCap>256)
        return refuse("metadata-cap");
    std::vector<Id> ids;
    std::map<Id,std::size_t> index;
    for(const Footprint& f:footprints)
    {
        if(f.moving<0||!index.emplace(f.moving,index.size()).second
         ||f.points.empty()||f.points.size()>8192||!sortedUnique(f.points)
         ||f.coreCells.empty()||f.coreCells.size()>128||!sortedUnique(f.coreCells)
         ||f.outsideCells.size()>256||!sortedUnique(f.outsideCells)
         ||f.faces.empty()||f.faces.size()>2048||!sortedUnique(f.faces)
         ||f.dependencyCells.empty()||!sortedUnique(f.dependencyCells)
         ||f.earliestCoordinates.size()!=f.points.size()
         ||!std::binary_search(f.points.begin(),f.points.end(),f.moving))
            return refuse("identity");
        for(Id c:f.coreCells)
            if(std::binary_search(f.outsideCells.begin(),f.outsideCells.end(),c)
             ||!std::binary_search(f.dependencyCells.begin(),f.dependencyCells.end(),c))
                return refuse("cell-binding");
        for(Id c:f.outsideCells)
            if(!std::binary_search(f.dependencyCells.begin(),f.dependencyCells.end(),c))
                return refuse("cell-binding");
        if(f.protectedPoint||f.zone||f.coupledCore||!f.tetBinding)
            return refuse("protected-or-unbound");
        ids.push_back(f.moving);
    }
    std::vector<std::size_t> parent(ids.size());
    for(std::size_t i=0;i<parent.size();++i)parent[i]=i;
    auto root=[&](std::size_t p)
    { while(parent[p]!=p)p=parent[p];return p; };
    auto join=[&](std::size_t p,std::size_t q)
    {
        p=root(p);q=root(q);
        if(p!=q)parent[std::max(p,q)]=std::min(p,q);
    };
    std::map<Id,std::size_t> pointOwner,cellOwner;
    std::map<Id,XYZ> pointCoordinates;
    std::map<XYZ,std::size_t> aliasOwner;
    std::size_t visits=0;
    auto withinBudget=[&]()
    { return (++visits&255)!=0||(available&&available()); };
    if(!available||!available())return refuse("budget");
    for(std::size_t i=0;i<footprints.size();++i)
    {
        const Footprint& f=footprints[i];
        for(std::size_t j=0;j<f.points.size();++j)
        {
            if(!withinBudget())return refuse("budget");
            const Id p=f.points[j];const XYZ& coordinate=f.earliestCoordinates[j];
            if(!finiteXYZ(coordinate))return refuse("point-coordinate");
            auto value=pointCoordinates.emplace(p,coordinate);
            if(!value.second&&!sameXYZ(value.first->second,coordinate))
                return refuse("earliest-point-binding");
            auto owner=pointOwner.emplace(p,i);
            if(!owner.second)join(i,owner.first->second);
            auto alias=aliasOwner.emplace(coordinate,i);
            if(!alias.second)join(i,alias.first->second);
        }
        for(Id c:f.dependencyCells)
        {
            if(!withinBudget())return refuse("budget");
            auto owner=cellOwner.emplace(c,i);
            if(!owner.second)join(i,owner.first->second);
        }
    }
    for(const auto& mode:atomicModes)
    {
        if(mode.empty()||mode.size()>3)return refuse("atomic-mode");
        std::set<Id> seen;std::size_t first=0;bool hasFirst=false;
        for(Id p:mode)
        {
            auto found=index.find(p);
            if(found==index.end()||!seen.insert(p).second)return refuse("atomic-mode");
            if(!hasFirst){first=found->second;hasFirst=true;}
            else join(first,found->second);
        }
    }
    std::map<std::size_t,std::vector<Id>> groups;
    for(std::size_t i=0;i<ids.size();++i)groups[root(i)].push_back(ids[i]);
    if(groups.size()>componentCap)return refuse("component-cap");
    for(auto& group:groups)
    {
        std::sort(group.second.begin(),group.second.end());
        result.components.push_back(std::move(group.second));
    }
    std::sort(result.components.begin(),result.components.end());
    for(const auto& component:result.components)
    {
        std::set<Id> core,outside,faces,points;
        for(Id id:component)
        {
            const Footprint& f=footprints[index.at(id)];
            for(Id c:f.coreCells){if(!withinBudget())return refuse("budget");core.insert(c);}
            for(Id c:f.outsideCells){if(!withinBudget())return refuse("budget");outside.insert(c);}
            for(Id face:f.faces){if(!withinBudget())return refuse("budget");faces.insert(face);}
            for(Id p:f.points){if(!withinBudget())return refuse("budget");points.insert(p);}
        }
        for(Id c:core)outside.erase(c);
        Counts count;count.core=core.size();count.outside=outside.size();
        count.faces=faces.size();count.points=points.size();
        if(count.core>512||count.outside>512||count.faces>4096||count.points>8192)
            return refuse("merged-stencil-cap");
        result.counts.push_back(count);
    }
    if(!available())return refuse("budget");
    result.valid=true;return result;
}
struct FaceIdentity
{
    Id id=-1,owner=-1,neighbour=-1;
    std::vector<Id> ring;
};
inline bool sameFaceIdentity(const FaceIdentity& a,const FaceIdentity& b)
{
    return a.id>=0&&a.owner>=0&&a.id==b.id&&a.owner==b.owner
        &&a.neighbour==b.neighbour&&a.ring==b.ring;
}
// Deterministic full global accepted-role records. Any duplicate, even with
// equal bytes, refuses: disjoint closed components must not share an ID.
struct RoleRecords
{
    std::map<Id,double> cells;
    std::map<Id,std::array<double,3>> faces;
    bool append
    (
        const std::vector<Id>& cellIds,const std::vector<double>& determinant,
        const std::vector<Id>& faceIds,
        const std::vector<std::array<double,3>>& metrics
    )
    {
        if(cellIds.size()!=determinant.size()||faceIds.size()!=metrics.size())return false;
        for(std::size_t i=0;i<cellIds.size();++i)
            if(cellIds[i]<0||!std::isfinite(determinant[i])
             ||!cells.emplace(cellIds[i],determinant[i]).second)return false;
        for(std::size_t i=0;i<faceIds.size();++i)
        {
            if(faceIds[i]<0)return false;
            for(double value:metrics[i])if(!std::isfinite(value))return false;
            if(!faces.emplace(faceIds[i],metrics[i]).second)return false;
        }
        return true;
    }
    void ordered
    (
        std::vector<Id>& cellIds,std::vector<double>& determinant,
        std::vector<Id>& faceIds,std::vector<std::array<double,3>>& metrics
    ) const
    {
        cellIds.clear();determinant.clear();faceIds.clear();metrics.clear();
        for(const auto& row:cells){cellIds.push_back(row.first);determinant.push_back(row.second);}
        for(const auto& row:faces){faceIds.push_back(row.first);metrics.push_back(row.second);}
    }
};
// Aggregate every record, with the unchanged production hard-role predicate
// injected by the adapter. Per-component success cannot hide a global loss.
template<class Preserves>
inline bool globalQualityLineage
(
    const RoleRecords& before,const RoleRecords& after,
    const std::map<Id,double>& beforeN,const std::map<Id,double>& afterN,
    double ceiling,const Preserves& preserves
)
{
    if(!std::isfinite(ceiling)||before.cells.size()!=after.cells.size()
     ||before.faces.size()!=after.faces.size()||beforeN.size()!=afterN.size()
     ||beforeN.size()!=before.faces.size())return false;
    std::vector<double> oldD,newD;
    std::array<std::vector<double>,3> oldQ,newQ;
    for(const auto& row:before.cells)
    {
        const auto found=after.cells.find(row.first);
        if(found==after.cells.end())return false;
        oldD.push_back(row.second);newD.push_back(found->second);
    }
    for(const auto& row:before.faces)
    {
        const auto found=after.faces.find(row.first);
        if(found==after.faces.end())return false;
        for(unsigned k=0;k<3;++k)
        {oldQ[k].push_back(row.second[k]);newQ[k].push_back(found->second[k]);}
        if(!beforeN.count(row.first)||!afterN.count(row.first))return false;
    }
    if(!preserves(oldD,newD,.001,false))return false;
    for(unsigned k=0;k<3;++k)
        if(!preserves(oldQ[k],newQ[k],k==0?.05:k==1?.01:4.,k==2))return false;
    std::size_t oldBadN=0,newBadN=0;
    for(const auto& row:beforeN)
    {
        const auto found=afterN.find(row.first);
        if(found==afterN.end()||!std::isfinite(row.second)
         ||!std::isfinite(found->second)||found->second>ceiling)return false;
        oldBadN+=row.second>70;newBadN+=found->second>70;
    }
    return newBadN<=oldBadN;
}
struct CoreRecord
{
    double oldVolume=0,newVolume=0;
    XYZ oldCentre{{0,0,0}},newCentre{{0,0,0}};
};
struct ConservationRecords
{
    std::map<Id,CoreRecord> core;
    bool append(Id cell,const CoreRecord& value)
    {
        return cell>=0&&std::isfinite(value.oldVolume)&&value.oldVolume>0
            &&std::isfinite(value.newVolume)&&value.newVolume>0
            &&finiteXYZ(value.oldCentre)&&finiteXYZ(value.newCentre)
            &&core.emplace(cell,value).second;
    }
};
} // namespace dcCartFiniteClosedComponents
#endif
