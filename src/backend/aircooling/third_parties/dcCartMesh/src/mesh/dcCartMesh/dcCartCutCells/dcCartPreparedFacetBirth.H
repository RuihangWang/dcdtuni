#ifndef dcCartPreparedFacetBirth_H
#define dcCartPreparedFacetBirth_H
#include <algorithm>
#include <array>
#include <cstdint>
#include <cstring>
#include <map>
#include <set>
#include <vector>
#include <type_traits>
#include <utility>
namespace dcCartPreparedFacetBirth
{
// This metadata lives only in one extracted, pre-cone carve part.
// A record is published only with a successful, closed FullStar Install.
template<class Label> struct Record
{
    Label point=-1,seedOrigin=-1;
    bool originalFaceBirth=false;
    std::array<std::uint64_t,3> storedBits{{0,0,0}};
    std::vector<Label> originCells,originalFaces,currentFaces,currentCells;
};
template<class Label> struct State
{
    Label extractionEpoch=0,originalPoints=0,originalCells=0;
    std::uint64_t generation=0;
    const char* lineageFailure=nullptr;
    std::size_t points=0,faces=0,cells=0;
    std::vector<Label> cellOrigins;
    std::map<Label,Record<Label>> records;
    std::map<Label,unsigned char> flatFootprintReasons;
};
// Diagnostic only: the unchanged flattened-witness loop records its actual
// return branch. These tags never grant completeness.
inline const char* flatFootprintReason(unsigned char code)
{
    return code==0?"nonempty":code==1?"emptyAncestors":
        code==2?"missingAncestorFootprint":code==3?"rootCap":"unobserved";
}
// Common append for actual native split parents and typed Install children.
// No coordinate, rank/leaf name, or approximate ancestry identifies the parent.
// Failure revokes lineage authority; native pre-split geometry remains unchanged.
template<class Mesh>
bool appendCellOrigin(Mesh& m,int parent,int child,const char** why=nullptr)
{
    auto& b=m.preparedBirth;
    if(b.extractionEpoch<=0)return true;
    auto fail=[&](const char* reason)
    {b.lineageFailure=reason;b.cellOrigins.clear();if(why)*why=reason;return false;};
    if(b.lineageFailure)return fail(b.lineageFailure);
    if(child<0||child!=m.nCells-1||b.cellOrigins.size()!=std::size_t(child))
        return fail("birth-cell-lineage-shape");
    if(parent<0||parent>=child||std::size_t(parent)>=b.cellOrigins.size())
        return fail("birth-cell-lineage-parent");
    const auto root=b.cellOrigins[parent];
    if(root<0||root>=b.originalCells||std::size_t(root)>=b.cellOrigins.size()
      ||b.cellOrigins[root]!=root)return fail("birth-cell-lineage-root");
    b.cellOrigins.push_back(root);return true;
}
// Native clip mutations are not a typed Install publication. Revocation also
// covers a same-size face/owner edit. A disposable trial's whole-mesh restore
// restores its certificate; no coordinate comparison reauthorizes it.
template<class Mesh> void invalidateCurrentIncidence(Mesh& m)
{
    if(m.preparedBirth.extractionEpoch>0&&!m.preparedBirth.records.empty())
        m.preparedBirth.generation=0;
}
template<class Point> inline std::array<std::uint64_t,3> bits(const Point& p)
{
    std::array<std::uint64_t,3> result;
    for(unsigned d=0;d<3;++d)
    {
        const double x=double(p[d]);static_assert(sizeof(x)==sizeof(result[d]),"binary64 birth bits");
        std::memcpy(&result[d],&x,sizeof(x));
    }
    return result;
}
// Caller prerequisites: the actual complete old FullStar and every original
// face identity/permission have passed; all edge insertions outside that star
// have been refused; final closed/V-M/material checks have passed.
// We additionally enumerate ALL published faces, so the sparse incidence cache
// includes every occurrence of every committed born point, including old faces.
// No source coordinate, nearest face, bbox, or material guess creates a record.
template<class Mesh,class Original,class Replacements,class FaceAllowed,class Tick>
bool seal(Mesh& staged,const Mesh& before,int seed,const Original& original,
          const Replacements& replacements,FaceAllowed faceAllowed,Tick tick,
          const char** why=nullptr)
{
    using StateType=typename std::decay<decltype(staged.preparedBirth)>::type;
    using Label=typename std::decay<decltype(staged.preparedBirth.cellOrigins)>::type::value_type;
    auto fail=[&](const char* reason){if(why)*why=reason;return false;};
    const auto& base=before.preparedBirth;
    if(base.lineageFailure||staged.preparedBirth.lineageFailure)
        return fail(base.lineageFailure?base.lineageFailure:staged.preparedBirth.lineageFailure);
    if(base.extractionEpoch<=0||base.originalPoints<0||base.originalCells<0
      ||std::size_t(base.originalPoints)>before.points.size()
      ||base.originalCells>before.nCells||base.cellOrigins.size()!=std::size_t(before.nCells)
      ||before.preparedCompleteCells.size()!=std::size_t(before.nCells)
      ||staged.preparedBirth.cellOrigins.size()!=std::size_t(staged.nCells)
      ||staged.preparedCompleteCells.size()!=std::size_t(staged.nCells)
      ||seed<0||seed>=before.nCells||!before.cellAlive[seed]||!before.preparedCompleteCells[seed])
        return fail("birth-input-shape");
    StateType next=staged.preparedBirth;
    if(next.generation==~std::uint64_t(0))return fail("birth-generation-overflow");
    ++next.generation;
    const Label seedOrigin=base.cellOrigins[seed];
    auto originValid=[&](Label root)
    {
        return root>=0&&root<base.originalCells&&std::size_t(root)<base.cellOrigins.size()
          &&base.cellOrigins[root]==root&&before.preparedCompleteCells[root];
    };
    if(!originValid(seedOrigin))return fail("birth-incomplete-seed-origin");
    std::map<Label,Label> faceParent;
    for(const auto& item:replacements)
    {
        tick();const Label old=Label(item.first);
        if(!original.count(old))return fail("birth-unbound-replacement-parent");
        for(auto f:item.second)
        {
            tick();
            if(f<0||std::size_t(f)>=staged.faces.size()||!faceParent.emplace(f,old).second)
                return fail("birth-duplicate-replacement-parent");
        }
    }
    for(std::size_t p=before.points.size();p<staged.points.size();++p)
    {
        tick();Record<Label> row;row.point=Label(p);row.seedOrigin=seedOrigin;
        row.storedBits=bits(staged.points[p]);
        if(!next.records.emplace(Label(p),std::move(row)).second)return fail("birth-point-redefinition");
    }
    for(auto& item:next.records)
    {
        tick();auto& row=item.second;
        if(row.point!=item.first||row.point<base.originalPoints||std::size_t(row.point)>=staged.points.size()
          ||row.storedBits!=bits(staged.points[row.point]))return fail("birth-stale-point");
        row.currentFaces.clear();row.currentCells.clear();
    }
    std::map<Label,std::set<Label>> currentOrigins,newOldFaces;
    for(std::size_t f=0;f<staged.faces.size();++f)
    {
        tick();if(!staged.faceAlive[f])continue;
        for(auto p:staged.faces[f])
        {
            const auto found=next.records.find(p);if(found==next.records.end())continue;
            tick();auto& row=found->second;row.currentFaces.push_back(Label(f));
            for(auto c:{staged.owner[f],staged.neighbour[f]})
            {
                if(c==-1)continue;
                if(c<0||c>=staged.nCells||!staged.cellAlive[c]||!staged.preparedCompleteCells[c])
                    return fail("birth-missing-current-cell");
                const Label root=next.cellOrigins[c];
                if(!originValid(root))return fail("birth-incomplete-cell-origin");
                row.currentCells.push_back(c);currentOrigins[p].insert(root);
            }
            if(std::size_t(p)<before.points.size())continue;
            Label parent=-1;
            if(f<before.faces.size())
            {
                parent=Label(f);
                if(!original.count(parent)||!faceAllowed(parent))
                    return fail("birth-original-incidence-outside-star");
            }
            else
            {
                const auto replacement=faceParent.find(Label(f));
                if(replacement!=faceParent.end())parent=replacement->second;
            }
            if(parent>=0)newOldFaces[p].insert(parent);
        }
    }
    std::vector<Label> retiredRecords;
    for(auto& item:next.records)
    {
        tick();auto& row=item.second;
        auto unique=[](std::vector<Label>& v)
        {std::sort(v.begin(),v.end());v.erase(std::unique(v.begin(),v.end()),v.end());};
        unique(row.currentFaces);unique(row.currentCells);
        if(row.currentFaces.empty())
        {
            if(std::size_t(row.point)>=before.points.size())return fail("birth-no-current-incidence");
            // Full alive-face scan proves this OLD point has retired. Revoke
            // only its certificate; a later unsealed use cannot reuse it.
            retiredRecords.push_back(row.point);continue;
        }
        if(row.currentCells.empty())return fail("birth-no-current-cell-incidence");
        const auto& roots=currentOrigins.at(row.point);
        if(std::size_t(row.point)>=before.points.size())
        {
            const auto& parents=newOldFaces[row.point];std::set<Label> allowed;
            row.originalFaceBirth=!parents.empty();
            row.originalFaces.assign(parents.begin(),parents.end());
            if(parents.empty())allowed.insert(seedOrigin);
            for(Label f:parents)
            {
                const auto old=original.find(f);
                if(old==original.end()||!faceAllowed(f))return fail("birth-unbound-original-face");
                for(auto c:{old->second.owner,old->second.neighbour})
                {
                    if(c==-1)continue;
                    if(c<0||c>=before.nCells||!before.cellAlive[c]||!before.preparedCompleteCells[c])
                        return fail("birth-incomplete-original-face-incidence");
                    const Label root=base.cellOrigins[c];
                    if(!originValid(root))return fail("birth-incomplete-original-face-origin");
                    allowed.insert(root);
                }
            }
            for(Label root:roots)if(!allowed.count(root))return fail("birth-foreign-original-incidence");
            if(!row.originalFaceBirth&&(roots.size()!=1||!roots.count(seedOrigin)))
                return fail("birth-internal-outside-seed-lineage");
            row.originCells.assign(roots.begin(),roots.end());
        }
        else for(Label root:roots)
            if(!std::binary_search(row.originCells.begin(),row.originCells.end(),root))
                return fail("birth-existing-point-foreign-lineage");
    }
    for(Label p:retiredRecords){next.records.erase(p);next.flatFootprintReasons.erase(p);}
    next.points=staged.points.size();next.faces=staged.faces.size();next.cells=std::size_t(staged.nCells);
    staged.preparedBirth=std::move(next);return true;
}
// The cache is complete because seal enumerated every actual alive face and
// published atomically with this exact shape. Formal clip snapshots restore
// that whole state before a later capture; other extraction resets this state.
template<class Mesh,class OriginComplete,class Available>
bool complete(const Mesh& m,int point,int epoch,int originalPoints,int originalCells,
              OriginComplete originComplete,Available available,const char** why=nullptr)
{
    auto fail=[&](const char* reason){if(why)*why=reason;return false;};
    if(!available())return fail("expired");
    const auto& b=m.preparedBirth;
    if(b.lineageFailure)return fail(b.lineageFailure);
    const auto found=b.records.find(point);
    if(found==b.records.end())return fail("empty/noBirth");
    const auto& r=found->second;
    if(b.extractionEpoch!=epoch||b.originalPoints!=originalPoints||b.originalCells!=originalCells
      ||b.generation==0||b.points!=m.points.size()||b.faces!=m.faces.size()||b.cells!=std::size_t(m.nCells)
      ||b.cellOrigins.size()!=std::size_t(m.nCells))return fail("staleBirthScopeOrShape");
    if(point<originalPoints||point<0||std::size_t(point)>=m.points.size()||r.point!=point
      ||r.storedBits!=bits(m.points[point])||m.preparedExactPoints.size()!=m.points.size()
      ||!m.preparedExactPoints[point])return fail("staleBirthPointOrPin");
    if(r.originCells.empty()||r.currentFaces.empty()||r.currentCells.empty())
        return fail("emptyBirthSupport");
    for(auto root:r.originCells)
    {
        if(!available())return fail("expired");
        if(root<0||root>=originalCells||b.cellOrigins[root]!=root||!originComplete(root))
            return fail("missingOriginalCell");
    }
    std::set<int> cells;
    for(auto f:r.currentFaces)
    {
        if(!available())return fail("expired");
        if(f<0||std::size_t(f)>=m.faces.size()||!m.faceAlive[f]
          ||std::find(m.faces[f].begin(),m.faces[f].end(),point)==m.faces[f].end())
            return fail("missingCurrentFace");
        for(auto c:{m.owner[f],m.neighbour[f]})
        {
            if(c==-1)continue;
            if(c<0||c>=m.nCells||!m.cellAlive[c]||m.preparedCompleteCells.size()!=std::size_t(m.nCells)
              ||!m.preparedCompleteCells[c]
              ||!std::binary_search(r.originCells.begin(),r.originCells.end(),b.cellOrigins[c]))
                return fail("missingCurrentCell");
            cells.insert(c);
        }
    }
    if(std::vector<int>(cells.begin(),cells.end())!=r.currentCells)return fail("changedCurrentIncidence");
    return available()?true:fail("expired");
}
}
#endif
