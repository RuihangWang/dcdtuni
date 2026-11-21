#ifndef dcCartPreparedFacetContext_H
#define dcCartPreparedFacetContext_H
#include "../dcCartSurfaces/dcCartPreparedFacetPartition.H"
#include <functional>
#include <map>
#include <set>
#include <vector>
#include <utility>
namespace dcCartPreparedFacetContext
{
using Label=dcCartPreparedFacetPartition::Id;
struct CaptureLimits
{
    std::size_t maxCells=128,maxFaces=4096,maxPoints=8192,maxSteps=500000;
};
struct CaptureFailure
{
    const char* reason=nullptr;Label id=-1;std::size_t observed=0,limit=0;
};
struct FullStar
{
    Label seed=-1;
    std::map<Label,dcCartPreparedFacetPartition::OriginalCell> cells;
    std::set<Label> footprintPoints;
    bool complete=false;
};
// Capture before clipPass changes ANY original face. Native extraction proof
// and actual original-ID point incidence are both mandatory; bbox is no authority.
template<class Mesh,class CompletePointStar>
inline bool capture(const Mesh& m,const std::vector<std::vector<int>>& cf,
                    int seed,const std::vector<signed char>& sides,
                    const std::function<bool()>& available,
                    CompletePointStar&& completePointStar,FullStar& out,
                    std::size_t maxCells=128,std::size_t maxFaces=4096,
                    std::size_t maxPoints=8192,std::size_t maxSteps=500000,
                    CaptureFailure* failure=nullptr)
{
    out=FullStar();
    if(failure)*failure=CaptureFailure();
    auto reject=[&](const char* reason,Label id=-1,std::size_t observed=0,std::size_t limit=0)
    {
        if(failure&&!failure->reason)*failure={reason,id,observed,limit};
        return false;
    };
    if(!available||!available()||seed<0||seed>=m.nCells
       ||m.preparedCompleteCells.size()!=std::size_t(m.nCells)
       ||cf.size()!=std::size_t(m.nCells)||!m.cellAlive[seed]
       ||!m.preparedCompleteCells[seed])return reject("invalid-or-incomplete-seed",seed);
    FullStar staged;staged.seed=seed;std::size_t steps=0;
    auto step=[&]()
    {
        if(++steps>maxSteps)return reject("step-capacity",seed,steps,maxSteps);
        if(!available())return reject("original-clock-expired",seed);
        return true;
    };
    std::set<int> targetPoints,cells{seed};
    // Target membership only: no cross-rank point identity or material role.
    std::vector<unsigned char> targetPoint(m.points.size(),0);
    for(int f:cf[seed])
    {
        if(!step()||f<0||std::size_t(f)>=m.faces.size()||!m.faceAlive[f])return reject("invalid-seed-face",f);
        for(int p:m.faces[f])
        {
            if(!step()||p<0||std::size_t(p)>=m.points.size()
              ||std::size_t(p)>=sides.size())return reject("invalid-seed-point",p);
            targetPoints.insert(p);targetPoint[std::size_t(p)]=1;
            if(targetPoints.size()>maxPoints)return reject("seed-point-capacity",p,targetPoints.size(),maxPoints);
        }
    }
    if(targetPoints.empty())return reject("empty-seed-points",seed);
    for(int p:targetPoints)
        if(!step()||!completePointStar(p))return reject("incomplete-native-point-star",p);
    // Full local incidence, including edge/point-only neighbours. The supplied
    // original-ID proof additionally rejects stars outside this extracted part.
    for(std::size_t f=0;f<m.faces.size();++f)
    {
        if(!step())return false;
        if(!m.faceAlive[f])continue;
        bool touches=false;
        for(int p:m.faces[f])
            if(p>=0&&std::size_t(p)<targetPoint.size()&&targetPoint[std::size_t(p)])
            {touches=true;break;}
        if(!touches)continue;
        for(int c:{m.owner[f],m.neighbour[f]})
        {
            if(c==-1)continue;
            if(c<0||c>=m.nCells||!m.cellAlive[c]
               ||!m.preparedCompleteCells[c])return reject("incomplete-incident-cell",c);
            cells.insert(c);
            if(cells.size()>maxCells)return reject("cell-capacity",c,cells.size(),maxCells);
        }
    }
    std::size_t nFaces=0;
    for(int c:cells)
    {
        dcCartPreparedFacetPartition::OriginalCell cell;cell.id=c;
        std::map<std::pair<int,int>,std::pair<unsigned,int>> edges;
        for(int f:cf[c])
        {
            if(!step())return false;
            if(++nFaces>maxFaces)return reject("face-capacity",f,nFaces,maxFaces);
            if(f<0||std::size_t(f)>=m.faces.size()
               ||!m.faceAlive[f]||m.owner[f]<0||m.neighbour[f]<-1
               ||m.owner[f]==m.neighbour[f]||m.zone[f]<-1
               ||(m.owner[f]!=c&&m.neighbour[f]!=c))return reject("invalid-star-face",f);
            dcCartPreparedFacetPartition::OriginalFace face;
            face.id=f;face.owner=m.owner[f];face.neighbour=m.neighbour[f];
            face.patch=m.patch[f];face.zone=m.zone[f];
            std::set<int> ids;
            for(int p:m.faces[f])
            {
                if(!step()||p<0||std::size_t(p)>=m.points.size()
                   ||std::size_t(p)>=sides.size()||!ids.insert(p).second)return reject("invalid-or-repeated-star-point",p);
                const auto& x=m.points[p];
                dcCartPreparedFacetPartition::Point pt{{x[0],x[1],x[2]}};
                if(!dcCartPreparedFacetPartition::detail::finite(pt))return reject("nonfinite-star-point",p);
                face.pointIds.push_back(p);face.points.push_back(pt);
                cell.pointSides.emplace(p,int(sides[p]));staged.footprintPoints.insert(p);
                if(staged.footprintPoints.size()>maxPoints)
                    return reject("star-point-capacity",p,staged.footprintPoints.size(),maxPoints);
            }
            if(face.pointIds.size()<3)return reject("short-star-face",f);
            const bool own=m.owner[f]==c;
            for(std::size_t j=0;j<face.pointIds.size();++j)
            {
                int a=face.pointIds[j],b=face.pointIds[(j+1)%face.pointIds.size()];
                if(!own)std::swap(a,b);
                if(a==b)return reject("repeated-star-edge",f);
                auto& e=edges[std::minmax(a,b)];++e.first;e.second+=(a<b?1:-1);
            }
            cell.faces.push_back(std::move(face));
        }
        if(cell.faces.empty())return reject("empty-star-cell",c);
        for(const auto& e:edges)if(e.second.first!=2||e.second.second!=0)
            return reject("unclosed-star-cell",c);
        staged.cells.emplace(c,std::move(cell));
    }
    if(!available())return reject("original-clock-expired",seed);
    staged.complete=true;out=std::move(staged);return true;
}
// Capacity affects only bounded storage/work. It never grants geometry,
// source identity, point-star completeness or quality permission.
template<class Mesh,class CompletePointStar>
inline bool capture(const Mesh& m,const std::vector<std::vector<int>>& cf,
                    int seed,const std::vector<signed char>& sides,
                    const std::function<bool()>& available,
                    CompletePointStar&& completePointStar,FullStar& out,
                    const CaptureLimits& limits,CaptureFailure* failure=nullptr)
{
    return capture(m,cf,seed,sides,available,
        std::forward<CompletePointStar>(completePointStar),out,
        limits.maxCells,limits.maxFaces,limits.maxPoints,limits.maxSteps,failure);
}
}
#endif
